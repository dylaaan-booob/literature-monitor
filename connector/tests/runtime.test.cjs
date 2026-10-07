"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const RUNTIME_SOURCE = fs.readFileSync(
	path.join(ROOT, "overlay", "literature-monitor-runtime.js"),
	"utf8",
);
const HOOK_PATCH = fs.readFileSync(
	path.join(ROOT, "patches", "0002-literature-monitor-automatic-capture-hooks.patch"),
	"utf8",
);

class Deferred {
	constructor() {
		this.settled = false;
		this.promise = new Promise((resolve, reject) => {
			this.resolve = value => {
				this.settled = true;
				resolve(value);
			};
			this.reject = error => {
				this.settled = true;
				reject(error);
			};
		});
	}
}

class FakeEvent {
	constructor() {
		this.listeners = [];
	}

	addListener(listener) {
		if (!this.listeners.includes(listener)) {
			this.listeners.push(listener);
		}
	}

	removeListener(listener) {
		this.listeners = this.listeners.filter(candidate => candidate !== listener);
	}

	emit(...args) {
		for (const listener of [...this.listeners]) {
			listener(...args);
		}
	}
}

class FakeClock {
	constructor() {
		this.now = 0;
		this.nextID = 1;
		this.timers = new Map();
		this.intervalDelays = [];
	}

	setTimeout(fn, delay = 0) {
		const id = this.nextID++;
		this.timers.set(id, {
			fn,
			at: this.now + Number(delay),
			interval: 0,
		});
		return id;
	}

	clearTimeout(id) {
		this.timers.delete(id);
	}

	setInterval(fn, delay = 0) {
		const interval = Number(delay);
		const id = this.nextID++;
		this.intervalDelays.push(interval);
		this.timers.set(id, {
			fn,
			at: this.now + interval,
			interval,
		});
		return id;
	}

	clearInterval(id) {
		this.timers.delete(id);
	}

	async advance(milliseconds) {
		const target = this.now + milliseconds;
		let turns = 0;
		while (true) {
			let nextID = null;
			let nextTimer = null;
			for (const [id, timer] of this.timers) {
				if (timer.at <= target
						&& (!nextTimer || timer.at < nextTimer.at)) {
					nextID = id;
					nextTimer = timer;
				}
			}
			if (!nextTimer) {
				break;
			}
			if (++turns > 20000) {
				throw new Error("Fake clock runaway");
			}

			this.now = nextTimer.at;
			if (nextTimer.interval > 0 && this.timers.has(nextID)) {
				nextTimer.at += nextTimer.interval;
			}
			else {
				this.timers.delete(nextID);
			}
			nextTimer.fn();
			await flush();
		}
		this.now = target;
		await flush();
	}
}

async function flush(turns = 12) {
	for (let i = 0; i < turns; i++) {
		await Promise.resolve();
	}
}

function response(status = 200, body = {}) {
	const text = body === null
		? ""
		: typeof body === "string"
			? body
			: JSON.stringify(body);
	return {
		ok: status >= 200 && status < 300,
		status,
		async text() {
			return text;
		},
	};
}

function makeEnvironment({
	isChrome = true,
	isEdge = false,
	isManifestV3 = true,
	windows = [{id: 1, type: "normal", incognito: false}],
	createdIncognito,
	windowError = null,
} = {}) {
	const clock = new FakeClock();
	const init = new Deferred();
	const runtimeMessages = new FakeEvent();
	const tabUpdated = new FakeEvent();
	const tabRemoved = new FakeEvent();
	const requests = [];
	const createdTabs = [];
	const removedTabs = [];
	const windowQueries = [];
	const storageCalls = [];
	const saveCalls = [];
	const keepAliveCalls = [];
	const loggedErrors = [];
	const onlineQueue = [];
	const bridgeQueues = new Map();
	const tabs = new Map();
	const tabInfo = new Map();
	const saveDeferred = new Deferred();
	let nextTabID = 100;
	let defaultOnline = true;
	let saveImplementation = () => saveDeferred.promise;

	function enqueue(pathname, plan) {
		if (!bridgeQueues.has(pathname)) {
			bridgeQueues.set(pathname, []);
		}
		bridgeQueues.get(pathname).push(plan);
	}

	function defaultPlan(pathname) {
		if (pathname === "/api/connector/heartbeat") {
			return response(200, {readiness: "connected"});
		}
		if (pathname === "/api/connector/claim") {
			return response(200, {command: null});
		}
		if (pathname === "/api/connector/result") {
			return response(200, {accepted: true});
		}
		throw new Error(`Unexpected bridge path ${pathname}`);
	}

	async function fetchMock(url, options) {
		const parsed = new URL(url);
		requests.push({url, pathname: parsed.pathname, options});
		const queue = bridgeQueues.get(parsed.pathname);
		const plan = queue && queue.length
			? queue.shift()
			: defaultPlan(parsed.pathname);
		const resolved = await plan;
		if (resolved instanceof Error) {
			throw resolved;
		}
		return resolved;
	}

	const browser = {
		runtime: {
			onMessage: runtimeMessages,
			getManifest() {
				return {
					name: "Literature Monitor Connector",
					version: "5.0.999",
				};
			},
		},
		get storage() {
			storageCalls.push("storage");
			throw new Error("Automatic runtime must not access browser storage");
		},
		windows: {
			async getAll(options) {
				windowQueries.push({...options});
				if (windowError) {
					throw windowError;
				}
				return windows;
			},
		},
		tabs: {
			onUpdated: tabUpdated,
			onRemoved: tabRemoved,
			async create(options) {
				const windowID = options.windowId ?? windows[0]?.id;
				const window = windows.find(candidate => candidate.id === windowID);
				const tab = {
					id: nextTabID++,
					url: options.url,
					status: "loading",
					windowId: windowID,
					incognito: createdIncognito === undefined
						? window?.incognito
						: createdIncognito,
				};
				createdTabs.push({options: {...options}, tab});
				tabs.set(tab.id, tab);
				return tab;
			},
			async remove(tabID) {
				removedTabs.push(tabID);
				tabs.delete(tabID);
				tabRemoved.emit(tabID);
			},
			async update() {
				throw new Error("Existing user tabs must not be navigated");
			},
			async get(tabID) {
				const tab = tabs.get(tabID);
				if (!tab) {
					throw new Error("No such tab");
				}
				return tab;
			},
		},
	};

	const Zotero = {
		isChrome,
		isEdge,
		isManifestV3,
		initDeferred: {
			promise: init.promise,
		},
		logError(error) {
			loggedErrors.push(error);
		},
		Connector: {
			async checkIsOnline() {
				const value = onlineQueue.length
					? onlineQueue.shift()
					: defaultOnline;
				if (value instanceof Error) {
					throw value;
				}
				return value;
			},
		},
		Connector_Browser: {
			setKeepServiceWorkerAlive(value) {
				keepAliveCalls.push(value);
			},
			getTabInfo(tabID) {
				return tabInfo.get(tabID) || {};
			},
			saveWithTranslator(tab, index, options) {
				saveCalls.push({tab, index, options: {...options}});
				return saveImplementation(tab, index, options);
			},
		},
	};

	const context = {
		AbortController,
		URL,
		Promise,
		console,
		fetch: fetchMock,
		browser,
		Zotero,
		setTimeout: clock.setTimeout.bind(clock),
		clearTimeout: clock.clearTimeout.bind(clock),
		setInterval: clock.setInterval.bind(clock),
		clearInterval: clock.clearInterval.bind(clock),
	};
	context.self = context;
	vm.runInNewContext(RUNTIME_SOURCE, context, {
		filename: "literature-monitor-runtime.js",
	});

	return {
		browser,
		clock,
		context,
		createdTabs,
		removedTabs,
		windowQueries,
		storageCalls,
		enqueue,
		init,
		keepAliveCalls,
		loggedErrors,
		onlineQueue,
		requests,
		runtime: context.LiteratureMonitorConnectorRuntime,
		runtimeMessages,
		saveCalls,
		saveDeferred,
		setDefaultOnline(value) {
			defaultOnline = value;
		},
		setSaveImplementation(fn) {
			saveImplementation = fn;
		},
		tabInfo,
		tabRemoved,
		tabUpdated,
		tabs,
	};
}

function requestsFor(env, pathname) {
	return env.requests.filter(request => request.pathname === pathname);
}

function bodiesFor(env, pathname) {
	return requestsFor(env, pathname).map(request => JSON.parse(request.options.body));
}

async function startRuntime(env) {
	env.init.resolve();
	await flush();
}

async function claim(env, command) {
	env.enqueue(
		"/api/connector/claim",
		response(200, {command}),
	);
	await env.runtime._claimTick();
	await flush();
}

async function makeSaveReady(env, translator = {
	translatorID: "translator-single",
	itemType: "journalArticle",
}) {
	assert.equal(env.createdTabs.length, 1);
	const tab = env.createdTabs[0].tab;
	tab.status = "complete";
	env.tabInfo.set(tab.id, {translators: [translator]});
	env.tabUpdated.emit(tab.id, {status: "complete"}, tab);
	await flush();
	return tab;
}

async function finishParent(env, tabID) {
	env.runtime._handleRuntimeMessage(
		{type: "literature-monitor-parent-saved"},
		{tab: {id: tabID}},
	);
	await flush();
}

const tests = [];
function test(name, fn) {
	tests.push([name, fn]);
}

test("runtime waits for upstream init and owns MV3 keep-alive exactly once", async () => {
	const env = makeEnvironment();
	assert.equal(env.keepAliveCalls.length, 0);
	assert.equal(env.requests.length, 0);

	await startRuntime(env);

	assert.deepEqual(env.keepAliveCalls, [true]);
	assert.equal(requestsFor(env, "/api/connector/heartbeat").length, 1);
	assert.equal(requestsFor(env, "/api/connector/claim").length, 1);
	assert(env.clock.intervalDelays.includes(5000));
	assert(env.clock.intervalDelays.includes(1000));

	env.runtime.start();
	assert.deepEqual(env.keepAliveCalls, [true]);
});

for (const [name, flags] of [
	["Edge", {isChrome: false, isEdge: true, isManifestV3: true}],
	["non-MV3 Chrome", {isChrome: true, isEdge: false, isManifestV3: false}],
	["missing Chrome flag", {isChrome: null}],
	["missing MV3 flag", {isManifestV3: null}],
]) {
	test(`${name} never starts LM automatic runtime or acquires keepalive`, async () => {
		const env = makeEnvironment(flags);
		env.enqueue("/api/connector/claim", response(200, {command: {
			request_id: "unsupported-browser",
			doi_url: "https://doi.org/10.5555/unsupported",
		}}));
		await startRuntime(env);
		env.runtime.start();
		await env.clock.advance(10000);
		env.runtimeMessages.emit({type: "literature-monitor-parent-saved"}, {tab: {id: 100}});
		assert.equal(env.keepAliveCalls.length, 0);
		assert.equal(env.runtime._started, false);
		assert.equal(env.runtime._activeTask, null);
		assert.equal(requestsFor(env, "/api/connector/heartbeat").length, 0);
		assert.equal(requestsFor(env, "/api/connector/claim").length, 0);
		assert.equal(env.requests.length, 0);
		assert.equal(env.runtimeMessages.listeners.length, 0);
		assert.equal(env.tabRemoved.listeners.length, 0);
		assert.equal(env.tabUpdated.listeners.length, 0);
		assert.equal(env.clock.timers.size, 0);
		assert.equal(env.windowQueries.length, 0);
		assert.equal(env.createdTabs.length, 0);
		assert.equal(env.saveCalls.length, 0);
		assert.equal(env.storageCalls.length, 0);
	});
}

test("Chrome uses one fresh non-incognito tab in a normal window despite a private window", async () => {
	const env = makeEnvironment({windows: [
		{id: 9, type: "normal", incognito: true},
		{id: 8, type: "popup", incognito: false},
		{id: 2, type: "normal", incognito: false},
	]});
	env.tabs.set(7, {id: 7, windowId: 2, incognito: false,
		url: "https://publisher.example/user", status: "complete"});
	const existing = {...env.tabs.get(7)};
	env.enqueue("/api/connector/claim", response(200, {command: {
		request_id: "normal-window",
		doi_url: "https://doi.org/10.5555/normal",
	}}));
	await startRuntime(env);
	assert.equal(requestsFor(env, "/api/connector/heartbeat").length, 1);
	assert.equal(requestsFor(env, "/api/connector/claim").length, 1);
	assert.deepEqual(env.keepAliveCalls, [true]);
	const tab = await makeSaveReady(env);
	assert.equal(tab.incognito, false);
	assert.equal(tab.windowId, 2);
	assert.notEqual(tab.id, 7);
	assert.deepEqual(env.tabs.get(7), existing);
	assert.deepEqual(JSON.parse(JSON.stringify(env.windowQueries)), [{windowTypes: ["normal"]}]);
	assert.equal(env.createdTabs.length, 1);
	assert.equal(env.saveCalls.length, 1);
	await finishParent(env, tab.id);
	assert.deepEqual(bodiesFor(env, "/api/connector/result"), [{
		request_id: "normal-window", outcome: "CONFIRMED",
	}]);
	assert.equal(env.storageCalls.length, 0);
});

for (const [name, config, expectedTabs] of [
	["private-only windows", {windows: [{id: 9, type: "normal", incognito: true}]}, 0],
	["no normal windows", {windows: [{id: 8, type: "popup", incognito: false}]}, 0],
	["unknown window privacy", {windows: [{id: 2, type: "normal", incognito: null}]}, 0],
	["window lookup failure", {windowError: new Error("Cannot inspect windows")}, 0],
	["unexpected incognito task tab", {createdIncognito: true}, 1],
	["unknown task-tab privacy", {createdIncognito: null}, 1],
]) {
	test(`${name} fails closed once without save, replacement tab, storage or stale revival`, async () => {
		const env = makeEnvironment(config);
		env.enqueue("/api/connector/claim", response(200, {command: {
			request_id: "profile-boundary",
			doi_url: "https://doi.org/10.5555/private",
		}}));
		await startRuntime(env);
		await flush();
		const tabID = env.createdTabs[0]?.tab.id ?? 100;
		if (env.createdTabs.length) {
			env.createdTabs[0].tab.status = "complete";
		}
		env.tabInfo.set(tabID, {translators: [{translatorID: "single", itemType: "journalArticle"}]});
		env.tabUpdated.emit(tabID, {status: "complete"}, {id: tabID});
		await flush(50);
		env.runtimeMessages.emit({type: "literature-monitor-parent-saved"}, {tab: {id: tabID}});
		env.runtimeMessages.emit({type: "literature-monitor-save-failed"}, {tab: {id: tabID}});
		env.tabRemoved.emit(tabID);
		await env.clock.advance(100000);
		assert.equal(env.saveCalls.length, 0);
		assert.equal(env.createdTabs.length, expectedTabs);
		assert.deepEqual(env.removedTabs, expectedTabs ? [tabID] : []);
		assert.equal(env.tabs.size, 0);
		assert.equal(env.runtime._activeTask, null);
		assert.equal(env.tabUpdated.listeners.length, 0);
		assert.equal(env.storageCalls.length, 0);
		assert.deepEqual(bodiesFor(env, "/api/connector/result"), [{
			request_id: "profile-boundary", outcome: "FAILED",
		}]);
		for (const request of env.requests) {
			assert.equal(new URL(request.url).origin, "http://127.0.0.1:8000");
			assert.equal(request.options.credentials, "omit");
			if (request.pathname === "/api/connector/claim") {
				assert.equal(request.options.body, "{}");
			}
		}
	});
}

test("heartbeat is immediate, uses manifest version, and repeats inside stale window", async () => {
	const env = makeEnvironment();
	await startRuntime(env);

	let heartbeats = requestsFor(env, "/api/connector/heartbeat");
	assert.equal(heartbeats.length, 1);
	assert.equal(heartbeats[0].url, "http://127.0.0.1:8000/api/connector/heartbeat");
	assert.equal(heartbeats[0].options.method, "POST");
	assert.equal(heartbeats[0].options.credentials, "omit");
	assert.equal(heartbeats[0].options.headers["Content-Type"], "application/json");
	assert.deepEqual(JSON.parse(heartbeats[0].options.body), {
		version: "5.0.999",
		zotero_reachable: true,
	});

	await env.clock.advance(5000);
	heartbeats = requestsFor(env, "/api/connector/heartbeat");
	assert.equal(heartbeats.length, 2);
});

test("heartbeat maps only explicit Zotero true to reachable", async () => {
	const env = makeEnvironment();
	env.onlineQueue.push(true, false, null, new Error("offline"));

	for (let i = 0; i < 4; i++) {
		await env.runtime._heartbeatTick();
	}
	assert.deepEqual(
		bodiesFor(env, "/api/connector/heartbeat").map(body => body.zotero_reachable),
		[true, false, false, false],
	);
});

test("claim is strict JSON empty object and only one request can be in flight", async () => {
	const env = makeEnvironment();
	const gate = new Deferred();
	env.enqueue("/api/connector/claim", gate.promise);

	const first = env.runtime._claimTick();
	const second = env.runtime._claimTick();
	await flush();

	const claims = requestsFor(env, "/api/connector/claim");
	assert.equal(claims.length, 1);
	assert.equal(claims[0].options.body, "{}");
	assert.equal(claims[0].options.headers["Content-Type"], "application/json");
	assert.equal(claims[0].options.credentials, "omit");

	gate.resolve(response(200, {command: null}));
	await Promise.all([first, second]);
	assert.equal(env.createdTabs.length, 0);
});

test("null command creates no task tab", async () => {
	const env = makeEnvironment();
	await env.runtime._claimTick();
	await flush();
	assert.equal(env.createdTabs.length, 0);
	assert.equal(requestsFor(env, "/api/connector/result").length, 0);
});

test("malformed command without a valid request id is ignored fail-closed", async () => {
	const env = makeEnvironment();
	await claim(env, {
		request_id: "",
		doi_url: "https://doi.org/10.5555/example",
	});
	assert.equal(env.createdTabs.length, 0);
	assert.equal(requestsFor(env, "/api/connector/result").length, 0);
});

test("unsafe or malformed DOI command with a valid request id reports FAILED without navigation", async () => {
	const unsafeCommands = [
		{request_id: "r-http", doi_url: "http://doi.org/10.5555/x"},
		{request_id: "r-host", doi_url: "https://example.org/10.5555/x"},
		{request_id: "r-user", doi_url: "https://user@doi.org/10.5555/x"},
		{request_id: "r-port", doi_url: "https://doi.org:444/10.5555/x"},
		{request_id: "r-query", doi_url: "https://doi.org/10.5555/x?q=1"},
		{request_id: "r-fragment", doi_url: "https://doi.org/10.5555/x#frag"},
		{request_id: "r-root", doi_url: "https://doi.org/"},
		{request_id: "r-smuggle", doi_url: "https://doi.org@evil.example/10.5555/x"},
		{
			request_id: "r-extra",
			doi_url: "https://doi.org/10.5555/x",
			output_dir: "/tmp/not-authority",
		},
	];

	for (const command of unsafeCommands) {
		const env = makeEnvironment();
		await claim(env, command);
		assert.equal(env.createdTabs.length, 0, command.request_id);
		assert.deepEqual(bodiesFor(env, "/api/connector/result"), [{
			request_id: command.request_id,
			outcome: "FAILED",
		}]);
	}
});

test("safe command creates exactly one fresh dedicated tab without reusing existing tabs", async () => {
	const env = makeEnvironment();
	env.tabs.set(7, {
		id: 7,
		url: "https://publisher.example/already-open",
		status: "complete",
		incognito: false,
		windowId: 1,
	});
	await claim(env, {
		request_id: "safe-1",
		doi_url: "https://doi.org/10.5555/SAFE%20DOI",
	});

	assert.equal(env.createdTabs.length, 1);
	assert.deepEqual(env.createdTabs[0].options, {
		url: "https://doi.org/10.5555/SAFE%20DOI",
		windowId: 1,
	});
	assert.equal(env.createdTabs[0].tab.incognito, false);
	assert.equal(env.tabs.get(7).url, "https://publisher.example/already-open");
});

test("translator readiness filters exact task tab and triggers one single-item save", async () => {
	const env = makeEnvironment();
	await claim(env, {
		request_id: "translate-1",
		doi_url: "https://doi.org/10.5555/translate",
	});
	const taskTab = env.createdTabs[0].tab;
	env.tabInfo.set(taskTab.id, {
		translators: [{
			translatorID: "single-translator",
			itemType: "journalArticle",
		}],
	});

	env.tabUpdated.emit(999, {status: "complete"}, {id: 999});
	await flush();
	assert.equal(env.saveCalls.length, 0);

	taskTab.status = "complete";
	env.tabUpdated.emit(taskTab.id, {status: "complete"}, taskTab);
	await flush();

	assert.equal(env.saveCalls.length, 1);
	assert.equal(env.saveCalls[0].tab.id, taskTab.id);
	assert.equal(env.saveCalls[0].index, 0);
	assert.deepEqual(env.saveCalls[0].options, {
		fallbackOnFailure: false,
		literatureMonitorAutomatic: true,
	});

	env.tabUpdated.emit(taskTab.id, {status: "complete"}, taskTab);
	await flush();
	assert.equal(env.saveCalls.length, 1);
});

test("multiple translator is never auto-selected", async () => {
	const env = makeEnvironment();
	await claim(env, {
		request_id: "multiple-1",
		doi_url: "https://doi.org/10.5555/multiple",
	});
	await makeSaveReady(env, {
		translatorID: "multiple-translator",
		itemType: "multiple",
	});

	assert.equal(env.saveCalls.length, 0);
	assert.deepEqual(bodiesFor(env, "/api/connector/result"), [{
		request_id: "multiple-1",
		outcome: "FAILED",
	}]);
	assert.equal(env.createdTabs.length, 1);
});

test("no usable translator reaches a finite pre-trigger FAILED deadline", async () => {
	const env = makeEnvironment();
	await claim(env, {
		request_id: "no-translator",
		doi_url: "https://doi.org/10.5555/no-translator",
	});

	await env.clock.advance(20000);

	assert.equal(env.saveCalls.length, 0);
	assert.deepEqual(bodiesFor(env, "/api/connector/result"), [{
		request_id: "no-translator",
		outcome: "FAILED",
	}]);
	assert.equal(env.createdTabs.length, 1);
});

test("Zotero is rechecked immediately before save and explicit unavailable prevents trigger", async () => {
	const env = makeEnvironment();
	env.onlineQueue.push(false);
	await claim(env, {
		request_id: "offline-before-save",
		doi_url: "https://doi.org/10.5555/offline",
	});
	await makeSaveReady(env);

	assert.equal(env.saveCalls.length, 0);
	assert.deepEqual(bodiesFor(env, "/api/connector/result"), [{
		request_id: "offline-before-save",
		outcome: "FAILED",
	}]);
});

test("task signals from other tabs are ignored", async () => {
	const env = makeEnvironment();
	await claim(env, {
		request_id: "signal-filter",
		doi_url: "https://doi.org/10.5555/signal",
	});
	const tab = await makeSaveReady(env);
	assert.equal(env.saveCalls.length, 1);

	env.runtime._handleRuntimeMessage(
		{type: "literature-monitor-parent-saved"},
		{tab: {id: tab.id + 1}},
	);
	await flush();
	assert.equal(requestsFor(env, "/api/connector/result").length, 0);

	await finishParent(env, tab.id);
	assert.deepEqual(bodiesFor(env, "/api/connector/result"), [{
		request_id: "signal-filter",
		outcome: "CONFIRMED",
	}]);
});

test("top-level parent signal confirms while full save promise remains unresolved", async () => {
	const env = makeEnvironment();
	await claim(env, {
		request_id: "parent-before-attachments",
		doi_url: "https://doi.org/10.5555/parent",
	});
	const tab = await makeSaveReady(env);
	assert.equal(env.saveCalls.length, 1);
	assert.equal(env.saveDeferred.settled, false);

	await finishParent(env, tab.id);

	assert.equal(env.saveDeferred.settled, false);
	assert.deepEqual(bodiesFor(env, "/api/connector/result"), [{
		request_id: "parent-before-attachments",
		outcome: "CONFIRMED",
	}]);

	env.saveDeferred.reject(new Error("simulated later attachment failure"));
	await flush();
	assert.equal(requestsFor(env, "/api/connector/result").length, 1);
});

test("explicit pre-parent save failure is FAILED", async () => {
	const env = makeEnvironment();
	await claim(env, {
		request_id: "pre-parent-failure",
		doi_url: "https://doi.org/10.5555/fail",
	});
	const tab = await makeSaveReady(env);

	env.runtime._handleRuntimeMessage(
		{type: "literature-monitor-save-failed"},
		{tab: {id: tab.id}},
	);
	await flush();

	assert.deepEqual(bodiesFor(env, "/api/connector/result"), [{
		request_id: "pre-parent-failure",
		outcome: "FAILED",
	}]);
	assert.equal(env.createdTabs.length, 1);
});

test("save promise rejection after trigger is conservatively UNCONFIRMED", async () => {
	const env = makeEnvironment();
	await claim(env, {
		request_id: "save-reject",
		doi_url: "https://doi.org/10.5555/reject",
	});
	await makeSaveReady(env);

	env.saveDeferred.reject(new Error("translation/save failed"));
	await flush();

	assert.deepEqual(bodiesFor(env, "/api/connector/result"), [{
		request_id: "save-reject",
		outcome: "UNCONFIRMED",
	}]);
});

test("post-trigger observation deadline is UNCONFIRMED, never FAILED", async () => {
	const env = makeEnvironment();
	await claim(env, {
		request_id: "observation-timeout",
		doi_url: "https://doi.org/10.5555/timeout",
	});
	await makeSaveReady(env);
	assert.equal(env.saveCalls.length, 1);

	await env.clock.advance(75000);

	assert.deepEqual(bodiesFor(env, "/api/connector/result"), [{
		request_id: "observation-timeout",
		outcome: "UNCONFIRMED",
	}]);
	assert.equal(env.createdTabs.length, 1);
});

test("task tab close is FAILED pre-trigger and UNCONFIRMED post-trigger", async () => {
	{
		const env = makeEnvironment();
		await claim(env, {
			request_id: "close-before",
			doi_url: "https://doi.org/10.5555/close-before",
		});
		const tab = env.createdTabs[0].tab;
		env.runtime._handleTaskTabRemoved(tab.id);
		await flush();
		assert.deepEqual(bodiesFor(env, "/api/connector/result"), [{
			request_id: "close-before",
			outcome: "FAILED",
		}]);
	}
	{
		const env = makeEnvironment();
		await claim(env, {
			request_id: "close-after",
			doi_url: "https://doi.org/10.5555/close-after",
		});
		const tab = await makeSaveReady(env);
		env.runtime._handleTaskTabRemoved(tab.id);
		await flush();
		assert.deepEqual(bodiesFor(env, "/api/connector/result"), [{
			request_id: "close-after",
			outcome: "UNCONFIRMED",
		}]);
	}
});

test("terminal outcome is one-shot and never creates a second tab or save", async () => {
	const env = makeEnvironment();
	await claim(env, {
		request_id: "one-shot",
		doi_url: "https://doi.org/10.5555/one-shot",
	});
	const tab = await makeSaveReady(env);
	await finishParent(env, tab.id);

	env.runtime._handleRuntimeMessage(
		{type: "literature-monitor-save-failed"},
		{tab: {id: tab.id}},
	);
	env.runtime._handleTaskTabRemoved(tab.id);
	await flush();

	assert.equal(env.createdTabs.length, 1);
	assert.equal(env.saveCalls.length, 1);
	assert.equal(requestsFor(env, "/api/connector/result").length, 1);
});

test("result body is minimal and delivery failure retries only the same terminal result", async () => {
	const env = makeEnvironment();
	env.enqueue("/api/connector/result", Promise.reject(new Error("bridge down")));
	env.enqueue("/api/connector/result", Promise.reject(new Error("bridge still down")));
	await claim(env, {
		request_id: "result-retry",
		doi_url: "https://doi.org/10.5555/result-retry",
	});
	const tab = await makeSaveReady(env);
	await finishParent(env, tab.id);
	await env.clock.advance(500);

	const results = requestsFor(env, "/api/connector/result");
	assert.equal(results.length, 2);
	for (const request of results) {
		const body = JSON.parse(request.options.body);
		assert.deepEqual(Object.keys(body).sort(), ["outcome", "request_id"]);
		assert.deepEqual(body, {
			request_id: "result-retry",
			outcome: "CONFIRMED",
		});
	}
	assert.equal(env.saveCalls.length, 1);
	assert.equal(env.createdTabs.length, 1);
});

test("stale 409 result stops delivery and never resaves", async () => {
	const env = makeEnvironment();
	env.enqueue(
		"/api/connector/result",
		response(409, {accepted: false}),
	);
	await claim(env, {
		request_id: "result-stale",
		doi_url: "https://doi.org/10.5555/result-stale",
	});
	const tab = await makeSaveReady(env);
	await finishParent(env, tab.id);

	assert.equal(requestsFor(env, "/api/connector/result").length, 1);
	assert.equal(env.saveCalls.length, 1);
});

test("runtime task state is process-local and contains no browser persistence or generic save fallback", async () => {
	for (const forbidden of [
		"localStorage",
		"sessionStorage",
		"indexedDB",
		"browser.storage",
		"saveAsWebpage",
		"Zotero Web API",
	]) {
		assert.equal(
			RUNTIME_SOURCE.includes(forbidden),
			false,
			`runtime must not contain ${forbidden}`,
		);
	}

	const env = makeEnvironment();
	await startRuntime(env);
	assert.equal(env.createdTabs.length, 0);
	assert.equal(env.saveCalls.length, 0);
});

test("runtime contains no collection routing, attachment readiness, or timer-based success path", async () => {
	assert.equal(
		/\b(collection|groups?|attachments?|snapshots?|pdf|tags?)\b/i.test(RUNTIME_SOURCE),
		false,
	);
	assert.equal(
		/setTimeout\([\s\S]{0,500}OUTCOME_CONFIRMED/.test(RUNTIME_SOURCE),
		false,
	);
});

test("lost saveItems response is UNCONFIRMED while pre-dispatch failure is FAILED", async () => {
	// Execute the shipped delta around the upstream request and catch, rather
	// than substituting a synthetic pre-parent message for a transport error.
	const dispatch = HOOK_PATCH.match(/((?:\+\t\t[^\n]*\n)*) \t\tawait Zotero\.Connector\.callMethod\("saveItems", payload\)\n((?:\+[^\n]*\n)*)/);
	assert(dispatch, "saveItems dispatch hook must remain reviewable");
	const added = HOOK_PATCH.split("\n")
		.filter(line => line.startsWith("+") && !line.startsWith("+++"))
		.map(line => line.slice(1)).join("\n");
	const failure = added.match(/if \(options\.literatureMonitorAutomatic\n[\s\S]*?\n\t\t\t\}/);
	assert(failure, "PageSaving catch hook must remain reviewable");
	const addedCode = text => text.split("\n")
		.filter(line => line.startsWith("+"))
		.map(line => line.slice(1)).join("\n");
	const save = new Function("Zotero", "browser", `return async function(payload) {
		${addedCode(dispatch[1])}
		await Zotero.Connector.callMethod("saveItems", payload);
		${addedCode(dispatch[2])}
	}`);
	const reportFailure = new Function("options", "browser", failure[0]);
	for (const postDispatch of [false, true]) {
		const env = makeEnvironment();
		await claim(env, {request_id: "lost-response", doi_url: "https://doi.org/10.5555/lost"});
		const tab = await makeSaveReady(env);
		const saver = {_literatureMonitorParentAccepted: false};
		const messages = [];
		const transport = {runtime: {sendMessage: async message => {
			messages.push(message.type);
			env.runtime._handleRuntimeMessage(message, {tab: {id: tab.id}});
		}}};
		if (postDispatch) {
			let received = 0;
			await assert.rejects(save({Connector: {callMethod: async () => {
				received++;
				throw new Error("Parent may have saved; response was lost");
			}}}, transport).call(saver, {}));
			assert.equal(received, 1);
		}
		reportFailure.call({sessionDetails: {itemSaver: saver}},
			{literatureMonitorAutomatic: true}, transport);
		await flush();
		if (postDispatch) {
			assert.deepEqual(messages, []);
			await env.clock.advance(75000);
			assert.deepEqual(bodiesFor(env, "/api/connector/result"), [{request_id: "lost-response", outcome: "UNCONFIRMED"}]);
		}
		else {
			assert.deepEqual(messages, ["literature-monitor-save-failed"]);
			assert.deepEqual(bodiesFor(env, "/api/connector/result"), [{request_id: "lost-response", outcome: "FAILED"}]);
		}
		assert.equal(env.saveCalls.length, 1);
		assert.equal(env.createdTabs.length, 1);
	}
});

test("upstream patch keeps automatic save local-only and confirms before attachment work", async () => {
	assert(HOOK_PATCH.includes('"literature-monitor-runtime.js"'));
	assert(HOOK_PATCH.includes("literatureMonitorAutomatic"));
	assert(HOOK_PATCH.includes("if (this._literatureMonitorAutomatic)"));
	assert(HOOK_PATCH.includes("return this._saveToServer"));
	assert(HOOK_PATCH.includes('"literature-monitor-save-failed"'));

	const saveItems = HOOK_PATCH.indexOf(
		'await Zotero.Connector.callMethod("saveItems", payload)',
	);
	const parentSignal = HOOK_PATCH.indexOf(
		'"literature-monitor-parent-saved"',
	);
	const selectedCollection = HOOK_PATCH.indexOf(
		'Zotero.Connector.callMethod("getSelectedCollection", {})',
	);
	const attachments = HOOK_PATCH.indexOf(
		"await this.saveAttachmentsToZotero(attachmentCallback)",
	);
	assert(saveItems >= 0);
	assert(parentSignal > saveItems);
	assert(selectedCollection > parentSignal);
	assert(attachments > selectedCollection);
});

(async () => {
	let failed = 0;
	for (const [name, fn] of tests) {
		try {
			await fn();
			process.stdout.write(`ok - ${name}\n`);
		}
		catch (error) {
			failed += 1;
			process.stderr.write(`not ok - ${name}\n`);
			process.stderr.write(`${error.stack || error}\n`);
		}
	}
	process.stdout.write(`\n${tests.length - failed}/${tests.length} tests passed\n`);
	if (failed) {
		process.exitCode = 1;
	}
})();
