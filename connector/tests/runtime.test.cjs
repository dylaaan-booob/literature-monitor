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
const ACCESS_SOURCE = fs.readFileSync(path.join(ROOT, "overlay", "literature-monitor-access.js"), "utf8");
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

async function flush(turns = 128) {
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
	const committed = new FakeEvent();
	const beforeNavigate = new FakeEvent();
	const redirected = new FakeEvent();
	const requestStarted = new FakeEvent();
	const headers = new FakeEvent();
	const requestCompleted = new FakeEvent();
	const navigationError = new FakeEvent();
	const frames = new Map();
	const documents = new Map();
	const scriptCalls = [];
	const requests = [];
	const createdTabs = [];
	const removedTabs = [];
	const focusCalls = [];
	const windowQueries = [];
	const storageCalls = [];
	const saveCalls = [];
	const keepAliveCalls = [];
	const loggedErrors = [];
	const onlineQueue = [];
	const bridgeQueues = new Map();
	const commands = new Map();
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
		if (["/api/connector/result", "/api/connector/dispatch", "/api/connector/access",
			"/api/connector/native-save-settled"].includes(pathname)) {
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
		if (parsed.pathname === "/api/connector/claim" && !(resolved instanceof Error)) {
			const command = JSON.parse(await resolved.text()).command;
			if (command) commands.set(command.request_id, command);
		}
		if (resolved instanceof Error) {
			throw resolved;
		}
		return resolved;
	}

	const browser = {
		webNavigation: {onCommitted: committed, onBeforeNavigate: beforeNavigate, onErrorOccurred: navigationError,
			async getFrame({tabId}) { return frames.get(tabId) || null; }},
		webRequest: {onBeforeRedirect: redirected, onBeforeRequest: requestStarted,
			onHeadersReceived: headers, onCompleted: requestCompleted},
		scripting: {async executeScript(options) {
			scriptCalls.push(options);
			const frame = frames.get(options.target.tabId);
			assert.equal(options.world, "ISOLATED");
			assert.deepEqual(Array.from(options.target.documentIds), [frame?.documentId]);
			const doc = documents.get(frame?.documentId);
			if (!doc) throw new Error("No current content script document");
			// Execute the actual injected function against a per-document isolated world.
			const document = {
				readyState: doc.readyState || "complete", title: doc.title || "A paper",
				location: {href: frame.url},
				body: {childElementCount: 1, textContent: doc.body || "Article content"},
				querySelector(selector) { return doc.markers?.has(selector) ? {textContent: doc.errorText || ""} : null; },
				querySelectorAll() { return (doc.scripts || []).map(src => ({src})); },
			};
			const result = vm.runInNewContext(`(${options.func.toString()})()`, {
				document, Zotero: {PageSaving: {translators: doc.translators || []}}, URL,
			});
			return [{documentId: frame.documentId, frameId: 0, result}];
		}},
		runtime: {
			id: "lm-extension",
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
			async update(id, options) { focusCalls.push({windowID: id, ...options}); },
			async getAll(options) {
				windowQueries.push({...options});
				if (windowError) {
					throw windowError;
				}
				return windows;
			},
		},
		tabs: {
			async query({active} = {}) {
				return [...tabs.values()].filter(tab => !active || tab.active);
			},
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
			async update(tabID, options) {
				assert(tabs.has(tabID), "only the dedicated task tab may be navigated");
				const tab = tabs.get(tabID); Object.assign(tab, options);
				return tab;
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
		performance: {now: () => clock.now},
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
	vm.runInNewContext(ACCESS_SOURCE, context, {filename: "literature-monitor-access.js"});
	vm.runInNewContext(RUNTIME_SOURCE, context, {
		filename: "literature-monitor-runtime.js",
	});

	return {
		committed, beforeNavigate, redirected, requestStarted, headers, requestCompleted, navigationError,
		frames, documents, scriptCalls,
		browser,
		commands,
		clock,
		context,
		createdTabs,
		removedTabs,
		focusCalls,
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

function expectedResult(env, requestID, outcome) {
		const command = env.commands.get(requestID);
	return {
		request_id: requestID, invocation_id: command.invocation_id, doi_url: command.doi_url,
		tab_id: env.runtime._activeTask?.tabID ?? (env.createdTabs.at(-1)?.tab.incognito === false ? env.createdTabs.at(-1).tab.id : null),
		session_id: requestsFor(env, "/api/connector/dispatch").length ? "native-session" : null,
		outcome, pdf_outcome: "unverified",
	};
}

async function startRuntime(env) {
	env.init.resolve();
	await flush();
}

async function claim(env, command) {
	if (!env.runtime._started) await startRuntime(env);
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
	if (!env.frames.has(tab.id)) commit(env, tab, tab.url);
	await flush();
	tab.status = "complete";
	env.tabInfo.set(tab.id, {translators: [translator], instanceID: 0});
	env.documents.get(env.frames.get(tab.id).documentId).translators = [translator];
	env.tabUpdated.emit(tab.id, {status: "complete"}, tab);
	await flush();
	return tab;
}

function senderFor(tabID, frameId = 0, documentId = "document-1") {
	return {id: "lm-extension", tab: {id: tabID}, frameId, documentId};
}

function taskMessage(env, type, sessionID = "native-session") {
	return {type, context: {...env.saveCalls.at(-1).options.literatureMonitorContext}, sessionID};
}

async function finishParent(env, tabID) {
	assert.equal(await env.runtime._handleRuntimeMessage(
		taskMessage(env, "literature-monitor-before-parent-save"), senderFor(tabID),
	), true);
	await env.runtime._handleRuntimeMessage(
		taskMessage(env, "literature-monitor-parent-saved"), senderFor(tabID));
	await env.runtime._handleRuntimeMessage(
		taskMessage(env, "literature-monitor-pipeline-complete"), senderFor(tabID));
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
			invocation_id: "invocation-" + "unsupported-browser", doi_url: "https://doi.org/10.5555/unsupported",
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
		invocation_id: "invocation-" + "normal-window", doi_url: "https://doi.org/10.5555/normal",
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
	assert.deepEqual(bodiesFor(env, "/api/connector/result"), [expectedResult(env, "normal-window", "CONFIRMED")]);
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
			invocation_id: "invocation-" + "profile-boundary", doi_url: "https://doi.org/10.5555/private",
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
		assert.deepEqual(bodiesFor(env, "/api/connector/result"), [expectedResult(env, "profile-boundary", "FAILED")]);
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
		invocation_id: "invocation-" + "", doi_url: "https://doi.org/10.5555/example",
	});
	assert.equal(env.createdTabs.length, 0);
	assert.equal(requestsFor(env, "/api/connector/result").length, 0);
});

test("unsafe or malformed DOI command is ignored without navigation or fabricated result", async () => {
	const unsafeCommands = [
		{request_id: "r-http", invocation_id: "invocation-" + "r-http", doi_url: "http://doi.org/10.5555/x"},
		{request_id: "r-host", invocation_id: "invocation-" + "r-host", doi_url: "https://example.org/10.5555/x"},
		{request_id: "r-user", invocation_id: "invocation-" + "r-user", doi_url: "https://user@doi.org/10.5555/x"},
		{request_id: "r-port", invocation_id: "invocation-" + "r-port", doi_url: "https://doi.org:444/10.5555/x"},
		{request_id: "r-query", invocation_id: "invocation-" + "r-query", doi_url: "https://doi.org/10.5555/x?q=1"},
		{request_id: "r-fragment", invocation_id: "invocation-" + "r-fragment", doi_url: "https://doi.org/10.5555/x#frag"},
		{request_id: "r-root", invocation_id: "invocation-" + "r-root", doi_url: "https://doi.org/"},
		{request_id: "r-smuggle", invocation_id: "invocation-" + "r-smuggle", doi_url: "https://doi.org@evil.example/10.5555/x"},
		{
			request_id: "r-extra",
			invocation_id: "invocation-" + "r-extra", doi_url: "https://doi.org/10.5555/x",
			output_dir: "/tmp/not-authority",
		},
	];

	for (const command of unsafeCommands) {
		const env = makeEnvironment();
		await claim(env, command);
		assert.equal(env.createdTabs.length, 0, command.request_id);
		assert.deepEqual(bodiesFor(env, "/api/connector/result"), []);
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
		invocation_id: "invocation-" + "safe-1", doi_url: "https://doi.org/10.5555/SAFE%20DOI",
	});

	assert.equal(env.createdTabs.length, 1);
	assert.deepEqual(env.createdTabs[0].options, {
		url: "about:blank",
		windowId: 1,
	});
	assert.equal(env.createdTabs[0].tab.incognito, false);
	assert.equal(env.tabs.get(7).url, "https://publisher.example/already-open");
});

test("translator readiness filters exact task tab and triggers one single-item save", async () => {
	const env = makeEnvironment();
	await claim(env, {
		request_id: "translate-1",
		invocation_id: "invocation-" + "translate-1", doi_url: "https://doi.org/10.5555/translate",
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

	await makeSaveReady(env, {translatorID: "single-translator", itemType: "journalArticle"});

	assert.equal(env.saveCalls.length, 1);
	assert.equal(env.saveCalls[0].tab.id, taskTab.id);
	assert.equal(env.saveCalls[0].index, 0);
	assert.deepEqual(JSON.parse(JSON.stringify(env.saveCalls[0].options)), {
		fallbackOnFailure: false,
		literatureMonitorAutomatic: true,
		literatureMonitorContext: {requestID: "translate-1", invocationID: "invocation-translate-1", doiURL: "https://doi.org/10.5555/translate", expiresAt: env.saveCalls[0].options.literatureMonitorContext.expiresAt, documentID: "document-1", translatorID: "single-translator"},
	});

	env.tabUpdated.emit(taskTab.id, {status: "complete"}, taskTab);
	await flush();
	assert.equal(env.saveCalls.length, 1);
});

test("multiple translator is never auto-selected", async () => {
	const env = makeEnvironment();
	await claim(env, {
		request_id: "multiple-1",
		invocation_id: "invocation-" + "multiple-1", doi_url: "https://doi.org/10.5555/multiple",
	});
	await makeSaveReady(env, {
		translatorID: "multiple-translator",
		itemType: "multiple",
	});

	assert.equal(env.saveCalls.length, 0);
	assert.deepEqual(bodiesFor(env, "/api/connector/result"), [expectedResult(env, "multiple-1", "FAILED")]);
	assert.equal(env.createdTabs.length, 1);
});

test("no usable translator reaches a finite pre-trigger FAILED deadline", async () => {
	const env = makeEnvironment();
	await claim(env, {
		request_id: "no-translator",
		invocation_id: "invocation-" + "no-translator", doi_url: "https://doi.org/10.5555/no-translator",
	});

	await env.clock.advance(20000);

	assert.equal(env.saveCalls.length, 0);
	assert.deepEqual(bodiesFor(env, "/api/connector/result"), [expectedResult(env, "no-translator", "FAILED")]);
	assert.equal(env.createdTabs.length, 1);
});

test("Zotero is rechecked immediately before save and explicit unavailable prevents trigger", async () => {
	const env = makeEnvironment();
	await claim(env, {
		request_id: "offline-before-save",
		invocation_id: "invocation-" + "offline-before-save", doi_url: "https://doi.org/10.5555/offline",
	});
	env.onlineQueue.push(false);
	await makeSaveReady(env);

	assert.equal(env.saveCalls.length, 0);
	assert.deepEqual(bodiesFor(env, "/api/connector/result"), [expectedResult(env, "offline-before-save", "FAILED")]);
});

test("task signals from other tabs are ignored", async () => {
	const env = makeEnvironment();
	await claim(env, {
		request_id: "signal-filter",
		invocation_id: "invocation-" + "signal-filter", doi_url: "https://doi.org/10.5555/signal",
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
	assert.deepEqual(bodiesFor(env, "/api/connector/result"), [expectedResult(env, "signal-filter", "CONFIRMED")]);
});

test("top-level parent signal confirms while full save promise remains unresolved", async () => {
	const env = makeEnvironment();
	await claim(env, {
		request_id: "parent-before-attachments",
		invocation_id: "invocation-" + "parent-before-attachments", doi_url: "https://doi.org/10.5555/parent",
	});
	const tab = await makeSaveReady(env);
	assert.equal(env.saveCalls.length, 1);
	assert.equal(env.saveDeferred.settled, false);

	await finishParent(env, tab.id);

	assert.equal(env.saveDeferred.settled, false);
	assert.deepEqual(bodiesFor(env, "/api/connector/result"), [expectedResult(env, "parent-before-attachments", "CONFIRMED")]);

	env.saveDeferred.reject(new Error("simulated later attachment failure"));
	await flush();
	assert.equal(requestsFor(env, "/api/connector/result").length, 1);
});

test("explicit pre-parent save failure is FAILED", async () => {
	const env = makeEnvironment();
	await claim(env, {
		request_id: "pre-parent-failure",
		invocation_id: "invocation-" + "pre-parent-failure", doi_url: "https://doi.org/10.5555/fail",
	});
	const tab = await makeSaveReady(env);

	env.runtime._handleRuntimeMessage(
		taskMessage(env, "literature-monitor-save-failed"),
		senderFor(tab.id),
	);
	await flush();

	assert.deepEqual(bodiesFor(env, "/api/connector/result"), [{
		...expectedResult(env, "pre-parent-failure", "FAILED"),
		failure_stage: "translator",
	}]);
	assert.equal(env.createdTabs.length, 1);
});

test("save promise rejection after trigger is conservatively UNCONFIRMED", async () => {
	const env = makeEnvironment();
	await claim(env, {
		request_id: "save-reject",
		invocation_id: "invocation-" + "save-reject", doi_url: "https://doi.org/10.5555/reject",
	});
	await makeSaveReady(env);

	env.saveDeferred.reject(new Error("translation/save failed"));
	await flush();

	assert.deepEqual(bodiesFor(env, "/api/connector/result"), [expectedResult(env, "save-reject", "UNCONFIRMED")]);
});

test("post-trigger observation deadline is UNCONFIRMED, never FAILED", async () => {
	const env = makeEnvironment();
	await claim(env, {
		request_id: "observation-timeout",
		invocation_id: "invocation-" + "observation-timeout", doi_url: "https://doi.org/10.5555/timeout",
	});
	await makeSaveReady(env);
	assert.equal(env.saveCalls.length, 1);

	await env.clock.advance(75000);

	assert.deepEqual(bodiesFor(env, "/api/connector/result"), [expectedResult(env, "observation-timeout", "UNCONFIRMED")]);
	assert.equal(env.createdTabs.length, 1);
});

test("task tab close is FAILED pre-trigger and UNCONFIRMED post-trigger", async () => {
	{
		const env = makeEnvironment();
		await claim(env, {
			request_id: "close-before",
			invocation_id: "invocation-" + "close-before", doi_url: "https://doi.org/10.5555/close-before",
		});
		const tab = env.createdTabs[0].tab;
		env.runtime._handleTaskTabRemoved(tab.id);
		await flush();
		assert.deepEqual(bodiesFor(env, "/api/connector/result"), [expectedResult(env, "close-before", "FAILED")]);
	}
	{
		const env = makeEnvironment();
		await claim(env, {
			request_id: "close-after",
			invocation_id: "invocation-" + "close-after", doi_url: "https://doi.org/10.5555/close-after",
		});
		const tab = await makeSaveReady(env);
		env.runtime._handleTaskTabRemoved(tab.id);
		await flush();
		assert.deepEqual(bodiesFor(env, "/api/connector/result"), [expectedResult(env, "close-after", "UNCONFIRMED")]);
	}
});

test("terminal outcome is one-shot and never creates a second tab or save", async () => {
	const env = makeEnvironment();
	await claim(env, {
		request_id: "one-shot",
		invocation_id: "invocation-" + "one-shot", doi_url: "https://doi.org/10.5555/one-shot",
	});
	const tab = await makeSaveReady(env);
	await finishParent(env, tab.id);

	env.runtime._handleRuntimeMessage(
		taskMessage(env, "literature-monitor-save-failed"),
		senderFor(tab.id),
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
		invocation_id: "invocation-" + "result-retry", doi_url: "https://doi.org/10.5555/result-retry",
	});
	const tab = await makeSaveReady(env);
	await finishParent(env, tab.id);
	await env.clock.advance(500);

	const results = requestsFor(env, "/api/connector/result");
	assert.equal(results.length, 2);
	for (const request of results) {
		const body = JSON.parse(request.options.body);
		assert.deepEqual(Object.keys(body).sort(), ["doi_url", "invocation_id", "outcome", "pdf_outcome", "request_id", "session_id", "tab_id"]);
		assert.deepEqual(body, expectedResult(env, "result-retry", "CONFIRMED"));
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
		invocation_id: "invocation-" + "result-stale", doi_url: "https://doi.org/10.5555/result-stale",
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
		/\b(collection|groups?|tags?)\b/i.test(RUNTIME_SOURCE),
		false,
	);
	assert(RUNTIME_SOURCE.includes("INTERNAL_PIPELINE_COMPLETE"));
	assert(RUNTIME_SOURCE.includes("task.parentConfirmed ? OUTCOME_CONFIRMED : OUTCOME_UNCONFIRMED"));
});


for (const [field, value] of [
	["requestID", "previous-request"], ["invocationID", "previous-invocation"],
	["doiURL", "https://doi.org/10.5555/other"], ["expiresAt", 1],
]) {
	test(`wrong ${field} cannot obtain dispatch or confirm parent`, async () => {
		const env = makeEnvironment();
		await claim(env, {request_id: "attribution", invocation_id: "invocation-attribution", doi_url: "https://doi.org/10.5555/a2"});
		const tab = await makeSaveReady(env);
		const message = taskMessage(env, "literature-monitor-before-parent-save");
		message.context[field] = value;
		assert.equal(await env.runtime._handleRuntimeMessage(message, senderFor(tab.id)), false);
		assert.equal(requestsFor(env, "/api/connector/dispatch").length, 0);
		message.type = "literature-monitor-parent-saved";
		assert.equal(await env.runtime._handleRuntimeMessage(message, senderFor(tab.id)), false);
		assert.equal(requestsFor(env, "/api/connector/result").length, 0);
	});
}

test("dispatch is one-shot; other tabs, extensions, documents and sessions cannot confirm", async () => {
	const env = makeEnvironment();
	await claim(env, {request_id: "sender", invocation_id: "invocation-sender", doi_url: "https://doi.org/10.5555/sender"});
	const tab = await makeSaveReady(env);
	const dispatch = taskMessage(env, "literature-monitor-before-parent-save");
	assert.equal(await env.runtime._handleRuntimeMessage(dispatch, senderFor(tab.id + 1)), false);
	assert.equal(await env.runtime._handleRuntimeMessage(dispatch, {...senderFor(tab.id), id: "other-extension"}), false);
	assert.equal(await env.runtime._handleRuntimeMessage(dispatch, senderFor(tab.id)), true);
	assert.equal(await env.runtime._handleRuntimeMessage(dispatch, senderFor(tab.id)), false);
	assert.equal(requestsFor(env, "/api/connector/dispatch").length, 1);
	const accepted = taskMessage(env, "literature-monitor-parent-saved");
	for (const sender of [senderFor(tab.id + 1), senderFor(tab.id, 1), senderFor(tab.id, 0, "previous-document")]) {
		assert.equal(await env.runtime._handleRuntimeMessage(accepted, sender), false);
	}
	assert.equal(await env.runtime._handleRuntimeMessage({...accepted, sessionID: "old-session"}, senderFor(tab.id)), false);
	assert.equal(requestsFor(env, "/api/connector/result").length, 0);
	await env.runtime._handleRuntimeMessage(accepted, senderFor(tab.id));
	await flush();
	assert.equal(requestsFor(env, "/api/connector/result").length, 0);
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-pipeline-complete"), senderFor(tab.id));
	await flush();
	const body = bodiesFor(env, "/api/connector/result")[0];
	assert.deepEqual(body, {
		request_id: "sender", invocation_id: "invocation-sender", doi_url: "https://doi.org/10.5555/sender",
		tab_id: tab.id, session_id: "native-session", outcome: "CONFIRMED", pdf_outcome: "unverified",
	});
	await env.runtime._handleRuntimeMessage(accepted, senderFor(tab.id));
	assert.equal(requestsFor(env, "/api/connector/result").length, 1);
});

test("expired and restarted runtime rejects delayed dispatch and previous task callbacks", async () => {
	const env = makeEnvironment();
	await claim(env, {request_id: "expired", invocation_id: "invocation-expired", doi_url: "https://doi.org/10.5555/expired"});
	const tab = await makeSaveReady(env);
	const oldDispatch = taskMessage(env, "literature-monitor-before-parent-save");
	const oldAccepted = taskMessage(env, "literature-monitor-parent-saved");
	await env.clock.advance(75000);
	assert.equal(await env.runtime._handleRuntimeMessage(oldDispatch, senderFor(tab.id)), false);
	assert.equal(requestsFor(env, "/api/connector/dispatch").length, 0);
	assert.equal(env.saveCalls.length, 1);
	const restarted = makeEnvironment();
	assert.equal(await restarted.runtime._handleRuntimeMessage(oldDispatch, senderFor(tab.id)), false);
	await claim(env, {request_id: "new-task", invocation_id: "invocation-new-task", doi_url: "https://doi.org/10.5555/new"});
	const newTab = env.createdTabs.at(-1).tab;
	newTab.status = "complete";
	env.tabInfo.set(newTab.id, {translators: [{translatorID: "single", itemType: "journalArticle"}]});
	env.tabUpdated.emit(newTab.id);
	await flush();
	// Even tab-id reuse cannot make the old request's callback current.
	assert.equal(await env.runtime._handleRuntimeMessage(oldAccepted, senderFor(newTab.id)), false);
	assert.equal(requestsFor(env, "/api/connector/result").length, 1);
});

// Execute the shipped patch's native dispatch hook with the real runtime listener.
const patchedLines = HOOK_PATCH.split("\n").filter(line =>
	(line.startsWith("+") && !line.startsWith("+++")) || line.startsWith(" ")
).map(line => line.slice(1)).join("\n");
const hookStart = patchedLines.lastIndexOf("\t\tif (this._literatureMonitorAutomatic) {", patchedLines.indexOf("const attempt = this._literatureMonitorAttempt;"));
const hookEnd = patchedLines.indexOf("\t\t// Update UI for top-level items", hookStart);
assert(hookStart >= 0 && hookEnd > hookStart);
const nativeHook = new Function("Zotero", "browser", "performance", `return async function(items, payload) {
	${patchedLines.slice(hookStart, hookEnd)}
}`);
const failureHook = [...patchedLines.matchAll(/if \(literatureMonitorAttempt\) \{[\s\S]*?\n\t\t\t\}/g)].at(-1);
assert(failureHook);
const reportPreDispatch = new Function("literatureMonitorAttempt", "browser", failureHook[0]);

// Run the shipped itemSaver parent hook AND the unmodified upstream
// getSelectedCollection/saveAttachmentsToZotero continuation.
const nativeEnd = patchedLines.indexOf("\n\t\treturn items;", hookEnd);
assert(nativeEnd > hookEnd);
const fullNativeHook = new Function("Zotero", "browser", "performance", `return async function(items, payload, itemsDoneCallback, attachmentCallback) {
	${patchedLines.slice(hookStart, nativeEnd + "\n\t\treturn items;".length)}
}`);

test("201 empty body continues native collection and attachment saving before result, closes owned tab and refocuses app", async () => {
	const env = makeEnvironment();
	env.tabs.set(50, {id: 50, windowId: 1, url: "http://127.0.0.1:8000/?view=workspace", incognito: false, active: true});
	await claim(env, {request_id: "pdf-continuation", invocation_id: "pdf-continuation-id", doi_url: "https://doi.org/10.5555/native"});
	const tab = await makeSaveReady(env);
	const attempt = {context: taskMessage(env, "unused").context, dispatchRequested: false, deadline: 75000};
	const attachment = new Deferred();
	const calls = [];
	const saver = {
		_literatureMonitorAutomatic: true, _literatureMonitorAttempt: attempt, _sessionID: "native-session",
		async saveAttachmentsToZotero() { calls.push("attachments"); await attachment.promise; },
	};
	const Zotero = {
		debug() {}, Messaging: {sendMessage() {}},
		Connector: {async callMethod(options) {
			const method = typeof options === "string" ? options : options.method;
			calls.push(method);
			if (method === "saveItems") {
				assert.equal(options.literatureMonitorResponse, true);
				return {status: 201, body: ""};
			}
			if (method === "getSelectedCollection") return {filesEditable: true};
			throw new Error("unexpected native method");
		}},
	};
	const transport = {runtime: {sendMessage: message => env.runtime._handleRuntimeMessage(message, senderFor(tab.id))}};
	const saving = fullNativeHook(Zotero, transport, {now: () => 0}).call(saver,
		[{DOI: "10.5555/native", itemType: "journalArticle"}],
		{items: [{id: "parent"}]}, () => calls.push("parent-callback"), () => {});
	await flush();
	assert.deepEqual(calls, ["saveItems", "parent-callback", "getSelectedCollection", "attachments"]);
	assert.equal(env.runtime._activeTask.parentConfirmed, true);
	assert.equal(requestsFor(env, "/api/connector/result").length, 0);
	assert.equal(env.removedTabs.length, 0);
	attachment.resolve();
	await saving;
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-pipeline-complete"), senderFor(tab.id));
	await flush();
	assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "CONFIRMED");
	assert.deepEqual(env.removedTabs, [tab.id]);
	assert(env.focusCalls.some(call => call.windowID === 1 && call.focused === true));
	assert.equal(env.tabs.get(50).active, true);
});

test("native error retains owned task tab and returns focus to original app", async () => {
	const env = makeEnvironment();
	env.tabs.set(51, {id: 51, windowId: 1, url: "http://127.0.0.1:8000/", incognito: false, active: true});
	await claim(env, {request_id: "return-error", invocation_id: "return-error-id", doi_url: "https://doi.org/10.5555/native"});
	const tab = await makeSaveReady(env);
	assert.equal(await env.runtime._handleRuntimeMessage(
		taskMessage(env, "literature-monitor-before-parent-save"), senderFor(tab.id)), true);
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-save-failed"), senderFor(tab.id));
	await flush();
	assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "UNCONFIRMED");
	assert.equal(env.removedTabs.length, 0);
	assert(env.tabs.has(tab.id));
	assert(env.focusCalls.some(call => call.windowID === 1 && call.focused === true));
});

test("attachment failure after proven parent retains inspection tab without demoting parent", async () => {
	const env = makeEnvironment();
	await claim(env, {request_id: "attachment-error", invocation_id: "attachment-error-id", doi_url: "https://doi.org/10.5555/native"});
	const tab = await makeSaveReady(env);
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-before-parent-save"), senderFor(tab.id));
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-parent-saved"), senderFor(tab.id));
	assert.equal(bodiesFor(env, "/api/connector/result").length, 0);
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-save-failed"), senderFor(tab.id));
	await flush();
	assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "CONFIRMED");
	assert.equal(bodiesFor(env, "/api/connector/result")[0].pdf_outcome, "unverified");
	assert.deepEqual(env.removedTabs, []);
});

test("confirmed parent timeout retains browser task ownership until late native attachment completion", async () => {
	const env = makeEnvironment();
	await claim(env, {request_id: "slow-attachment", invocation_id: "slow-attachment-id", doi_url: "https://doi.org/10.5555/native"});
	const tab = await makeSaveReady(env);
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-before-parent-save"), senderFor(tab.id));
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-parent-saved"), senderFor(tab.id));
	await env.clock.advance(75000);
	assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "CONFIRMED");
	assert.equal(env.runtime._activeTask?.tabID, tab.id);
	assert.deepEqual(env.removedTabs, []);
	env.enqueue("/api/connector/claim", response(200, {command: {
		request_id: "next", invocation_id: "next-id", doi_url: "https://doi.org/10.5555/next",
	}}));
	await env.runtime._claimTick();
	assert.equal(env.createdTabs.length, 1);
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-pipeline-complete"), senderFor(tab.id));
	await flush();
	assert.deepEqual(env.removedTabs, [tab.id]);
	assert.equal(env.runtime._activeTask, null);
});

test("verified pipeline completion wins against an expired observation deadline", async () => {
	const env = makeEnvironment();
	await claim(env, {request_id: "expired-but-ended", invocation_id: "expired-but-ended-id", doi_url: "https://doi.org/10.5555/native"});
	const tab = await makeSaveReady(env);
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-before-parent-save"), senderFor(tab.id));
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-parent-saved"), senderFor(tab.id));
	const done = taskMessage(env, "literature-monitor-pipeline-complete");
	env.runtime._activeTask.expiresAt = Date.now() - 1;
	done.context.expiresAt = env.runtime._activeTask.expiresAt;
	await env.runtime._handleRuntimeMessage(done, senderFor(tab.id));
	await flush();
	assert.equal(requestsFor(env, "/api/connector/native-save-settled").length, 1);
	assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "CONFIRMED");
});

test("late upstream pipeline completion settles only the original granted invocation", async () => {
	const env = makeEnvironment();
	await claim(env, {request_id: "late-settlement", invocation_id: "late-settlement-id", doi_url: "https://doi.org/10.5555/native"});
	const tab = await makeSaveReady(env);
	assert.equal(await env.runtime._handleRuntimeMessage(
		taskMessage(env, "literature-monitor-before-parent-save"), senderFor(tab.id)), true);
	await env.clock.advance(75000);
	assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "UNCONFIRMED");
	assert.equal(requestsFor(env, "/api/connector/native-save-settled").length, 0);
	assert.equal(env.runtime._activeTask?.tabID, tab.id);
	const settled = taskMessage(env, "literature-monitor-pipeline-complete");
	for (const sender of [senderFor(tab.id + 1), senderFor(tab.id, 1), senderFor(tab.id, 0, "previous-document")]) {
		await env.runtime._handleRuntimeMessage(settled, sender);
	}
	await env.runtime._handleRuntimeMessage({...settled, sessionID: "wrong-session"}, senderFor(tab.id));
	assert.equal(requestsFor(env, "/api/connector/native-save-settled").length, 0);
	await env.runtime._handleRuntimeMessage(settled, senderFor(tab.id));
	await flush();
	assert.deepEqual(bodiesFor(env, "/api/connector/native-save-settled"), [{
		request_id: "late-settlement", invocation_id: "late-settlement-id", doi_url: "https://doi.org/10.5555/native",
		tab_id: tab.id, session_id: "native-session",
	}]);
	assert.equal(requestsFor(env, "/api/connector/result").length, 1);
	assert.equal(env.runtime._activeTask, null);
	await env.runtime._handleRuntimeMessage(settled, senderFor(tab.id));
	assert.equal(requestsFor(env, "/api/connector/native-save-settled").length, 1);
});

test("native save error and confirmed parent alone never send pipeline settlement", async () => {
	const env = makeEnvironment();
	await claim(env, {request_id: "unsettled-native", invocation_id: "unsettled-native-id", doi_url: "https://doi.org/10.5555/native"});
	const tab = await makeSaveReady(env);
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-before-parent-save"), senderFor(tab.id));
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-parent-saved"), senderFor(tab.id));
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-save-failed"), senderFor(tab.id));
	await flush();
	assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "CONFIRMED");
	assert.equal(requestsFor(env, "/api/connector/native-save-settled").length, 0);
});

test("late native rejection releases browser task only, without a native-settlement receipt", async () => {
	const env = makeEnvironment();
	await claim(env, {request_id: "late-native-error", invocation_id: "late-native-error-id", doi_url: "https://doi.org/10.5555/native"});
	const tab = await makeSaveReady(env);
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-before-parent-save"), senderFor(tab.id));
	await env.clock.advance(75000);
	assert.equal(env.runtime._activeTask?.tabID, tab.id);
	const error = taskMessage(env, "literature-monitor-save-failed");
	error.stage = "native_save";
	delete error.sessionID; // Matches the pinned upstream onTranslate catch hook.
	await env.runtime._handleRuntimeMessage(error, senderFor(tab.id));
	await flush();
	assert.equal(requestsFor(env, "/api/connector/native-save-settled").length, 0);
	assert.equal(env.runtime._activeTask, null);
	assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "UNCONFIRMED");
});

test("lost settlement acknowledgment retries twice, never fabricates native completion", async () => {
	const env = makeEnvironment();
	env.enqueue("/api/connector/native-save-settled", response(500, {accepted: false}));
	env.enqueue("/api/connector/native-save-settled", response(500, {accepted: false}));
	await claim(env, {request_id: "lost-settlement", invocation_id: "lost-settlement-id", doi_url: "https://doi.org/10.5555/native"});
	const tab = await makeSaveReady(env);
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-before-parent-save"), senderFor(tab.id));
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-parent-saved"), senderFor(tab.id));
	const pending = env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-pipeline-complete"), senderFor(tab.id));
	await flush();
	await env.clock.advance(500);
	await pending;
	await flush();
	assert.equal(requestsFor(env, "/api/connector/native-save-settled").length, 2);
	assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "CONFIRMED");
});

test("manual authentication timeout retains task tab without stealing browser focus", async () => {
	const env = makeEnvironment();
	env.tabs.set(52, {id: 52, windowId: 1, url: "http://127.0.0.1:8000/", incognito: false, active: true});
	await claim(env, {request_id: "manual-login", invocation_id: "manual-login-id", doi_url: "https://doi.org/10.5555/native"});
	await flush();
	const task = env.runtime._activeTask;
	assert(task);
	task.access.reason = "preparing";
	env.runtime._finishTask(task, "FAILED");
	await flush();
	assert.deepEqual(env.removedTabs, []);
	assert.deepEqual(env.focusCalls, []);
});

test("native HTTP 201 empty body confirms parent without echoed items", async () => {
	const env = makeEnvironment();
	await claim(env, {request_id: "empty-201", invocation_id: "invocation-empty-201", doi_url: "https://doi.org/10.5555/native"});
	const tab = await makeSaveReady(env);
	const attempt = {context: taskMessage(env, "unused").context, dispatchRequested: false, deadline: 75000};
	const saver = {_literatureMonitorAutomatic: true, _literatureMonitorAttempt: attempt, _sessionID: "native-session"};
	const transport = {runtime: {sendMessage: message => env.runtime._handleRuntimeMessage(message, senderFor(tab.id))}};
	await nativeHook({Connector: {callMethod: async () => ({status: 201, body: ""})}}, transport,
		{now: () => 0}).call(saver, [{DOI: "10.5555/native", itemType: "journalArticle"}],
		{items: [{id: "parent"}]});
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-pipeline-complete"), senderFor(tab.id));
	await flush();
	assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "CONFIRMED");
});

for (const mode of ["accepted", "lost-response", "failed-response", "missing-native-response", "http-error", "non-201", "malformed-response", "wrong-doi", "permission-lost", "expired-reply"]) {
	test(`shipped parent hook: ${mode}; native save and no-effect evidence stay separate`, async () => {
		const env = makeEnvironment();
		await claim(env, {request_id: "native", invocation_id: "invocation-native", doi_url: "https://doi.org/10.5555/native"});
		const tab = await makeSaveReady(env);
		const attempt = {context: taskMessage(env, "unused").context, dispatchRequested: false, deadline: 75000};
		const saver = {_literatureMonitorAutomatic: true, _literatureMonitorAttempt: attempt, _sessionID: "native-session"};
		let nativeCalls = 0;
		let now = 0;
		const transport = {runtime: {sendMessage: async message => {
			const permitted = await env.runtime._handleRuntimeMessage(message, senderFor(tab.id));
			if (message.type === "literature-monitor-before-parent-save") {
				if (mode === "permission-lost") throw new Error("permission response lost");
				if (mode === "expired-reply") now = 75001;
			}
			return permitted;
		}}};
		const Zotero = {Connector: {callMethod: async () => {
			nativeCalls++;
			if (["lost-response", "failed-response"].includes(mode)) throw new Error(mode);
			if (mode === "missing-native-response") return undefined;
			if (mode === "non-201") return {status: 200, body: ""};
			return mode === "http-error" ? {status: 500, body: {error: "native failure"}}
				: mode === "malformed-response" ? {status: 201, body: {}}
				: {status: 201, body: ""};
		}}};
		let rejected = false;
		try {
			await nativeHook(Zotero, transport, {now: () => now}).call(saver,
				[{DOI: mode === "wrong-doi" ? "10.5555/other" : "https://doi.org/10.5555/NATIVE", itemType: "journalArticle"}],
				{items: [{id: "parent-1"}]});
		}
		catch (_) { rejected = true; reportPreDispatch(attempt, transport); }
		assert.equal(rejected, mode !== "accepted");
		if (!rejected) await env.runtime._handleRuntimeMessage(
			taskMessage(env, "literature-monitor-pipeline-complete"), senderFor(tab.id));
		await flush();
		if (!["accepted", "wrong-doi"].includes(mode)) await env.clock.advance(75000);
		assert.equal(nativeCalls, ["accepted", "lost-response", "failed-response", "missing-native-response", "http-error", "non-201", "malformed-response"].includes(mode) ? 1 : 0);
		const outcome = mode === "accepted" ? "CONFIRMED" : mode === "wrong-doi" ? "FAILED" : "UNCONFIRMED";
		assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, outcome);
		assert.equal(bodiesFor(env, "/api/connector/result")[0].pdf_outcome, "unverified");
	});
}

const sharedInvalidIdentity = {};
for (const [name, requestItem, accepted, confirmed, mutateID] of [
	["both IDs absent", {}, {items: [{}]}, false],
	["both IDs null", {id: null}, {items: [{id: null}]}, false],
	["both IDs empty", {id: ""}, {items: [{id: ""}]}, false],
	["both IDs whitespace", {id: " "}, {items: [{id: " "}]}, false],
	["both IDs zero", {id: 0}, {items: [{id: 0}]}, false],
	["both IDs false", {id: false}, {items: [{id: false}]}, false],
	["both IDs shared object", {id: sharedInvalidIdentity}, {items: [{id: sharedInvalidIdentity}]}, false],
	["both IDs boolean", {id: true}, {items: [{id: true}]}, false],
	["both IDs non-finite", {id: Infinity}, {items: [{id: Infinity}]}, false],
	["both IDs object", {id: {}}, {items: [{id: {}}]}, false],
	["201 empty body represented as null", {id: "a1B2c3D4"}, null, true],
	["response primitive", {id: "a1B2c3D4"}, true, false],
	["response array", {id: "a1B2c3D4"}, [{id: "a1B2c3D4"}], false],
	["response items missing", {id: "a1B2c3D4"}, {}, false],
	["response items malformed", {id: "a1B2c3D4"}, {items: {}}, false],
	["response item null", {id: "a1B2c3D4"}, {items: [null]}, false],
	["response item array", {id: "a1B2c3D4"}, {items: [[]]}, false],
	["response inherited items", {id: "a1B2c3D4"}, Object.create({items: [{id: "a1B2c3D4"}]}), false],
	["request ID mutated after dispatch", {id: "a1B2c3D4"}, {items: [{id: "z9Y8x7W6"}]}, false, "z9Y8x7W6"],
	["response ID absent", {id: "a1B2c3D4"}, {items: [{}]}, false],
	["response ID mismatched", {id: "a1B2c3D4"}, {items: [{id: "z9Y8x7W6"}]}, false],
	["numeric/string mismatch", {id: 1}, {items: [{id: "1"}]}, false],
	["inherited request ID", Object.create({id: "a1B2c3D4"}), {items: [{id: "a1B2c3D4"}]}, false],
	["inherited response ID", {id: "a1B2c3D4"}, {items: [Object.create({id: "a1B2c3D4"})]}, false],
	["native random-string ID", {id: "a1B2c3D4"}, {items: [{id: "a1B2c3D4"}]}, true],
	["native numeric ID", {id: 1}, {items: [{id: 1}]}, true],
]) {
	test(`native response identity: ${name}`, async () => {
		const env = makeEnvironment();
		await claim(env, {request_id: "identity", invocation_id: "invocation-identity", doi_url: "https://doi.org/10.5555/identity"});
		const tab = await makeSaveReady(env);
		const attempt = {context: taskMessage(env, "unused").context, dispatchRequested: false, deadline: 75000};
		const saver = {_literatureMonitorAutomatic: true, _literatureMonitorAttempt: attempt, _sessionID: "native-session"};
		let nativeCalls = 0;
		const messages = [];
		const transport = {runtime: {sendMessage: async message => {
			messages.push(message.type);
			return env.runtime._handleRuntimeMessage(message, senderFor(tab.id));
		}}};
		const Zotero = {Connector: {callMethod: async () => {
			nativeCalls++;
			if (mutateID !== undefined) requestItem.id = mutateID;
			return {status: 201, body: accepted};
		}}};
		let rejected = false;
		try {
			await nativeHook(Zotero, transport, {now: () => 0}).call(saver,
				[{DOI: "10.5555/identity", itemType: "journalArticle"}], {items: [requestItem]});
		}
		catch (_) { rejected = true; reportPreDispatch(attempt, transport); }
		await flush();
		assert.equal(rejected, !confirmed);
		if (confirmed) await env.runtime._handleRuntimeMessage(
			taskMessage(env, "literature-monitor-pipeline-complete"), senderFor(tab.id));
		assert.equal(messages.includes("literature-monitor-parent-saved"), confirmed);
		assert.equal(messages.includes("literature-monitor-save-failed"), !confirmed);
		if (!confirmed) {
			assert.equal(requestsFor(env, "/api/connector/result").length, 1);
		}
		const results = bodiesFor(env, "/api/connector/result");
		assert.equal(results.length, 1);
		assert.equal(results[0].outcome, confirmed ? "CONFIRMED" : "UNCONFIRMED");
		assert.equal(results[0].pdf_outcome, "unverified");
		await env.clock.advance(100000);
		assert.equal(nativeCalls, 1);
		assert.equal(env.saveCalls.length, 1);
		assert.equal(requestsFor(env, "/api/connector/dispatch").length, 1);
		assert.equal(requestsFor(env, "/api/connector/result").length, 1);
	});
}

for (const attachment of [
	{progress: 100}, {mimeType: "application/pdf", title: "PDF"},
	{url: "https://example.test/a.pdf", snapshot: false}, {snapshot: true, progress: 100},
]) {
	test(`attachment metadata/progress cannot verify PDF: ${JSON.stringify(attachment)}`, async () => {
		const env = makeEnvironment();
		await claim(env, {request_id: "pdf", invocation_id: "invocation-pdf", doi_url: "https://doi.org/10.5555/pdf"});
		const tab = await makeSaveReady(env);
		await env.runtime._handleRuntimeMessage({...taskMessage(env, "attachmentCallback"), ...attachment}, senderFor(tab.id));
		assert.equal(requestsFor(env, "/api/connector/result").length, 0);
		await finishParent(env, tab.id);
		assert.equal(bodiesFor(env, "/api/connector/result")[0].pdf_outcome, "unverified");
	});
}


test("PageSaving freezes invocation before await and attributes late native errors to the original task", async () => {
	const creation = patchedLines.match(/const literatureMonitorAttempt = options.literatureMonitorAutomatic \? \{[\s\S]*?\n\t\t\} : null;/);
	assert(creation);
	const createAttempt = new Function("options", "performance", "Date", `${creation[0]}; return literatureMonitorAttempt;`);
	const options = {literatureMonitorAutomatic: true, literatureMonitorContext: {
		requestID: "original", invocationID: "original-invocation", doiURL: "https://doi.org/10.5555/original", expiresAt: 76000,
	}};
	const attempt = createAttempt(options, {now: () => 100}, {now: () => 1000});
	options.literatureMonitorContext.requestID = "replacement";
	assert.equal(attempt.context.requestID, "original");
	assert(Object.isFrozen(attempt.context));
	const messages = [];
	const transport = {runtime: {sendMessage: async message => { messages.push(message); }}};
	const shared = {sessionDetails: {itemSaver: {_literatureMonitorParentSaveStarted: false}}};
	attempt.dispatchRequested = true;
	reportPreDispatch.call(shared, attempt, transport);
	await flush();
	assert.equal(messages.length, 1);
	assert.equal(messages[0].context.requestID, "original");
	assert.equal(messages[0].stage, "native_save");
});

test("upstream listener does not claim unrelated native messages", async () => {
	const env = makeEnvironment();
	await startRuntime(env);
	assert.equal(env.runtime._runtimeMessageListener(["progressWindow.done", [true]], senderFor(100)), undefined);
	assert.equal(env.runtime._runtimeMessageListener({type: "attachmentCallback", progress: 100}, senderFor(100)), undefined);
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

// Synthetic federation evidence: these .example rules are never shipped as enabled rules.
function accessRule(overrides = {}) {
	return {verified: true, federation: "OpenAthens", associationPolicy: "preserve",
		serviceOrigin: "https://sp.example", landingOrigins: ["https://sp.example", "https://proxy.example"],
		allowedOrigins: ["https://sp.example", "https://proxy.example", "https://idp.example"],
		entryURL: "https://sp.example/login", returnPath: "/institution-complete",
		evidence: {sp: "synthetic-SP", selector: "synthetic-IdP", destinations: "synthetic-hosts",
			returnBehavior: "synthetic-return", association: "synthetic-existing-association"}, ...overrides};
}
function journey(env, rules = [], context = null) {
	const ctx = context || new env.context.LiteratureMonitorAccess.AccessContext("batch", rules);
	return new env.context.LiteratureMonitorAccess.AccessJourney(ctx, "https://doi.org/10.5555/a");
}
function observed(access, url, documentId, extra = {}) {
	access.observe({frameId: 0, url, documentId, transitionQualifiers: ["server_redirect"], ...extra});
}
function commit(env, tab, url, extra = {}) {
	tab.url = url; tab.status = "loading";
	env.accessDocument = (env.accessDocument || 0) + 1;
	const documentId = extra.documentId || `document-${env.accessDocument}`;
	const requestId = extra.requestId || `request-${env.accessDocument}`;
	env.beforeNavigate.emit({tabId: tab.id, frameId: 0, url});
	env.requestStarted.emit({tabId: tab.id, type: "main_frame", requestId, url});
	const details = {tabId: tab.id, frameId: 0, url, documentId, transitionQualifiers: ["server_redirect"], ...extra};
	env.frames.set(tab.id, {documentId, url, documentLifecycle: "active", errorOccurred: false});
	env.documents.set(documentId, {translators: [], markers: new Set()});
	// Upstream clears its tab cache even for a same-URL reload.
	env.tabInfo.set(tab.id, {url, translators: null, instanceID: null});
	env.committed.emit(details);
	env.requestCompleted.emit({tabId: tab.id, type: "main_frame", requestId, url});
}

test("A6: actual DOI redirect identifies verified SP independently of bibliographic Publisher", () => {
	const env = makeEnvironment();
	const access = journey(env, [accessRule({entryURL: null})]);
	access.observeRedirect(access.doiURL, "https://proxy.example/resource?SAMLResponse=do-not-retain");
	observed(access, "https://proxy.example/resource?SAMLResponse=do-not-retain", "doc");
	assert.equal(access.snapshot().landing_origin, "https://proxy.example");
	assert.equal(access.snapshot().service_origin, "https://sp.example");
	assert.equal(access.snapshot().reason, "no_verified_route");
	assert.equal(access.takeNavigation(), null);
	assert(!JSON.stringify(access).includes("do-not-retain"));
	assert(!JSON.stringify(access).includes("SAMLResponse"));
});

test("A6: production has zero verified routes; unknown SP and resolver never manufacture identity", () => {
	const env = makeEnvironment();
	const access = journey(env);
	observed(access, access.doiURL, "resolver", {transitionQualifiers: []});
	assert.equal(access.snapshot().service_origin, null);
	observed(access, "https://unlisted.example/paper", "landing");
	assert.equal(access.snapshot().landing_origin, "https://unlisted.example");
	assert.equal(access.snapshot().service_origin, null);
	assert.equal(access.reason, "unknown_service");
	assert.equal(access.takeNavigation(), null);
	assert.equal(new env.context.LiteratureMonitorAccess.AccessContext("production").rules.length, 0);
});

for (const [name, changes] of [
	["unverified", {verified: false}], ["missing evidence", {evidence: {sp: "only"}}],
	["wildcard host", {allowedOrigins: ["https://*.example"]}],
	["association overwrite", {associationPolicy: "overwrite"}],
	["sensitive route query", {entryURL: "https://sp.example/login?SAMLResponse=secret"}],
	["unsafe entry", {entryURL: "https://evil.example/login"}],
	["unlisted return", {allowedOrigins: ["https://idp.example"]}],
	["unverified return path", {returnPath: "/return?unverified-selector"}],
]) test(`A6: ${name} cannot enable a federation rule`, () => {
	const env = makeEnvironment();
	const access = journey(env, [accessRule(changes)]);
	observed(access, "https://sp.example/paper", "doc");
	assert.equal(access.reason, "unknown_service");
	assert.equal(access.takeNavigation(), null);
});

test("A6: allowlist, repeated destinations and finite redirect hops stop orchestration", () => {
	const env = makeEnvironment();
	const access = journey(env, [accessRule()]);
	observed(access, "https://sp.example/paper", "a");
	const entry = access.takeNavigation(); access.navigationIssued(entry);
	observed(access, "https://evil.example/return", "b");
	assert.equal(access.reason, "unsafe_destination");
	assert.equal(access.captureAllowed, false);
	const loop = journey(env);
	loop.observeRedirect(loop.doiURL, "https://a.example/start");
	loop.observeRedirect("https://a.example/start", "https://b.example/relay");
	loop.observeRedirect("https://b.example/relay", "https://a.example/start?secret=not-retained");
	assert.equal(loop.reason, "redirect_loop");
	const hops = journey(env);
	for (let i = 0; i < 9; i++) hops.observeRedirect(hops.doiURL, `https://host${i}.example/redirect`);
	assert.equal(hops.reason, "hop_limit");
});

test("A6: one preparation per service/context; navigation does not establish any session or entitlement", () => {
	const env = makeEnvironment();
	const first = journey(env, [accessRule()]);
	observed(first, "https://sp.example/paper", "a");
	assert.equal(first.takeNavigation(), "https://sp.example/login");
	first.navigationIssued("https://sp.example/login");
	observed(first, "https://idp.example/auth", "b");
	observed(first, "https://sp.example/institution-complete", "c");
	assert.equal(first.takeNavigation(), first.doiURL);
	first.navigationIssued(first.doiURL);
	observed(first, "https://proxy.example/resource", "d");
	assert.equal(first.reason, "prepared");
	for (const name of ["idp_session", "sp_session", "authentication", "entitlement"]) assert.equal(first.snapshot()[name], "unknown");
	const later = journey(env, [], first.context);
	observed(later, "https://proxy.example/resource-two", "e");
	assert.equal(later.reason, "prepared");
	assert.equal(later.takeNavigation(), null);
	const newBatch = journey(env, [accessRule()]);
	observed(newBatch, "https://sp.example/paper", "f");
	assert.equal(newBatch.takeNavigation(), "https://sp.example/login");
});

test("A6: failed SP is deferred while unrelated services retain normal capture", () => {
	const env = makeEnvironment();
	const first = journey(env, [accessRule()]);
	observed(first, "https://sp.example/paper", "a");
	first.fallback("manual_challenge");
	const same = journey(env, [], first.context);
	observed(same, "https://proxy.example/paper", "b");
	assert.equal(same.reason, "service_deferred");
	assert.equal(same.takeNavigation(), null);
	assert.equal(same.captureAllowed, false);
	const unrelated = journey(env, [], first.context);
	observed(unrelated, "https://other.example/paper", "c");
	assert.equal(unrelated.captureAllowed, true);
});

function returningJourney(env, returnURL = "https://sp.example/institution-complete") {
	const access = journey(env, [accessRule()]);
	access.observeRedirect(access.doiURL, "https://sp.example/paper");
	observed(access, "https://sp.example/paper", "initial-landing");
	const entry = access.takeNavigation();
	assert.equal(entry, "https://sp.example/login");
	access.navigationIssued(entry);
	observed(access, entry, "federation-entry");
	observed(access, "https://idp.example/auth", "idp");
	observed(access, returnURL, "verified-return");
	return access;
}

test("A6 fix 1: expected DOI re-resolution combines redirects and commits within eight hops", () => {
	const env = makeEnvironment();
	const access = returningJourney(env);
	const resource = access.takeNavigation();
	assert.equal(resource, "https://doi.org/10.5555/a");
	access.navigationIssued(resource);
	observed(access, resource, "resource-doi", {transitionQualifiers: []});
	access.observeRedirect(resource, "https://sp.example/paper?SAMLResponse=not-retained");
	assert.equal(access.reason, "preparing");
	assert.equal(access.captureAllowed, true);
	observed(access, "https://sp.example/paper?SAMLResponse=not-retained", "final-landing");
	assert.equal(access.reason, "prepared");
	assert.equal(access.captureAllowed, true);
	assert.equal(access.hops, 8);
	assert.equal(access.takeNavigation(), null);
	assert.equal(access.context.services.get("https://sp.example"), "prepared");
	assert(!JSON.stringify(access).includes("SAMLResponse"));
	const next = journey(env, [], access.context);
	next.observeRedirect(next.doiURL, "https://sp.example/another-paper");
	observed(next, "https://sp.example/another-paper", "next-landing");
	assert.equal(next.reason, "prepared");
	assert.equal(next.takeNavigation(), null);
});

for (const [name, source, target, expected] of [
	["different DOI", "https://doi.org/10.5555/other", "https://sp.example/paper", "unsafe_destination"],
	["DOI with extra parameters", "https://doi.org/10.5555/a?token=secret", "https://sp.example/paper", "unsafe_destination"],
	["untrusted host", "https://doi.org/10.5555/a", "https://evil.example/paper", "unsafe_destination"],
	["IdP is not a landing", "https://doi.org/10.5555/a", "https://idp.example/auth", "unsafe_destination"],
	["same-origin loop", "https://sp.example/paper", "https://sp.example/again", "redirect_loop"],
	["other path cannot borrow return", "https://idp.example/auth", "https://sp.example/again", "redirect_loop"],
]) test(`A6 fix 1: ${name} cannot borrow expected resource return`, () => {
	const env = makeEnvironment(); const access = returningJourney(env);
	access.navigationIssued(access.takeNavigation());
	access.observeRedirect(source, target);
	assert.equal(access.reason, expected);
	assert.equal(access.captureAllowed, false);
	observed(access, "https://sp.example/paper", "late-landing");
	assert.equal(access.reason, expected);
	assert.equal(access.context.services.get("https://sp.example"), expected);
});

test("A6 fix 1: return must match verified SP path and actually issued original DOI", () => {
	const env = makeEnvironment();
	const wrongPath = returningJourney(env, "https://sp.example/not-verified");
	assert.equal(wrongPath.captureAllowed, false);
	assert.equal(wrongPath.takeNavigation(), null);
	const notIssued = returningJourney(env);
	assert.equal(notIssued.takeNavigation(), notIssued.doiURL);
	notIssued.observeRedirect(notIssued.doiURL, "https://sp.example/paper");
	assert.equal(notIssued.reason, "redirect_loop");
	assert.equal(notIssued.captureAllowed, false);
	const differentCommit = returningJourney(env);
	differentCommit.navigationIssued(differentCommit.takeNavigation());
	observed(differentCommit, "https://doi.org/10.5555/other", "wrong-doi");
	assert.equal(differentCommit.reason, "unsafe_destination");
});

test("A6 fix 1: resource redirect exception is consumed once and keeps other loop history", () => {
	const env = makeEnvironment();
	const repeated = returningJourney(env);
	repeated.navigationIssued(repeated.takeNavigation());
	repeated.observeRedirect(repeated.doiURL, "https://sp.example/paper");
	assert.equal(repeated.captureAllowed, true);
	repeated.observeRedirect(repeated.doiURL, "https://sp.example/paper");
	assert.equal(repeated.reason, "redirect_loop");
	const otherLoop = returningJourney(env);
	otherLoop.navigationIssued(otherLoop.takeNavigation());
	otherLoop.observeRedirect(otherLoop.doiURL, "https://sp.example/paper");
	otherLoop.observeRedirect("https://sp.example/paper", "https://idp.example/auth");
	otherLoop.observeRedirect("https://idp.example/auth", "https://idp.example/repeated");
	assert.equal(otherLoop.reason, "redirect_loop");
	assert.equal(otherLoop.captureAllowed, false);
	const mismatchedLanding = returningJourney(env);
	mismatchedLanding.navigationIssued(mismatchedLanding.takeNavigation());
	mismatchedLanding.observeRedirect(mismatchedLanding.doiURL, "https://sp.example/paper");
	observed(mismatchedLanding, "https://proxy.example/paper", "different-landing");
	assert.equal(mismatchedLanding.reason, "unsafe_destination");
});

test("A6 fix 1: extra legitimate redirect exceeds eight hops and falls back without retry", () => {
	const env = makeEnvironment(); const access = journey(env, [accessRule()]);
	access.observeRedirect(access.doiURL, "https://sp.example/paper");
	observed(access, "https://sp.example/paper", "landing");
	const entry = access.takeNavigation(); access.navigationIssued(entry);
	observed(access, entry, "entry");
	access.observeRedirect(entry, "https://idp.example/auth");
	observed(access, "https://idp.example/auth", "idp");
	observed(access, "https://sp.example/institution-complete", "return");
	const resource = access.takeNavigation(); access.navigationIssued(resource);
	observed(access, resource, "doi");
	access.observeRedirect(resource, "https://sp.example/paper");
	assert.equal(access.captureAllowed, true);
	observed(access, "https://sp.example/paper", "final");
	assert.equal(access.hops, 9);
	assert.equal(access.reason, "hop_limit");
	assert.equal(access.captureAllowed, false);
	assert.equal(access.takeNavigation(), null);
});

test("A6 fix 1: expired or closed resource journey cannot accept a late return", async () => {
	const env = makeEnvironment(); const expired = returningJourney(env);
	expired.navigationIssued(expired.takeNavigation());
	await env.clock.advance(20000);
	expired.observeRedirect(expired.doiURL, "https://sp.example/paper");
	observed(expired, "https://sp.example/paper", "late");
	assert.equal(expired.reason, "access_timeout");
	assert.equal(expired.captureAllowed, false);
	const closed = returningJourney(env);
	closed.navigationIssued(closed.takeNavigation()); closed.closed = true;
	const before = JSON.stringify(closed.snapshot());
	closed.observeRedirect(closed.doiURL, "https://sp.example/paper");
	observed(closed, "https://sp.example/paper", "closed-late");
	assert.equal(JSON.stringify(closed.snapshot()), before);
	assert.equal(closed.resourceRedirectOrigin, null);
	assert.equal(closed.takeNavigation(), null);
});

test("A6: runtime preserves OpenAthens associations and only navigates its own task tab", async () => {
	const env = makeEnvironment(); env.runtime.start(); await flush();
	Object.defineProperty(env.browser, "cookies", {get() { throw new Error("Cookie inspection forbidden"); }});
	const association = Object.freeze({"proxy.example": "existing-profile-association"});
	env.context.Zotero.Proxies = Object.freeze({hosts: association});
	env.runtime._accessContext = new env.context.LiteratureMonitorAccess.AccessContext("ctx", [accessRule()]);
	const navigations = [];
	env.browser.tabs.update = async (id, options) => {
		assert.equal(id, env.createdTabs[0].tab.id); navigations.push(options.url);
		const tab = env.tabs.get(id); Object.assign(tab, options, {status: "loading"}); return tab;
	};
	await claim(env, {request_id: "access-own-tab", invocation_id: "invocation-access", doi_url: "https://doi.org/10.5555/a", access_context: "ctx"});
	const tab = env.createdTabs[0].tab;
	env.redirected.emit({tabId: tab.id, type: "main_frame", url: "https://doi.org/10.5555/a", redirectUrl: "https://proxy.example/paper"});
	commit(env, tab, "https://proxy.example/paper?SAMLResponse=secret"); await flush();
	assert(!JSON.stringify(env.runtime._activeTask.access).includes("SAMLResponse"));
	assert.deepEqual(navigations, ["https://doi.org/10.5555/a", "https://sp.example/login"]);
	assert.equal(env.saveCalls.length, 0);
	commit(env, tab, "https://sp.example/login"); await flush();
	commit(env, tab, "https://idp.example/auth"); await flush();
	commit(env, tab, "https://sp.example/institution-complete"); await flush();
	assert.deepEqual(navigations, ["https://doi.org/10.5555/a", "https://sp.example/login", "https://doi.org/10.5555/a"]);
	commit(env, tab, "https://doi.org/10.5555/a"); await flush();
	env.redirected.emit({tabId: tab.id, type: "main_frame", url: "https://doi.org/10.5555/a", redirectUrl: "https://proxy.example/resource"});
	commit(env, tab, "https://proxy.example/resource"); await makeSaveReady(env);
	assert.equal(env.saveCalls.length, 1);
	assert.equal(env.context.Zotero.Proxies.hosts, association);
	const observation = bodiesFor(env, "/api/connector/access")[0].observation;
	assert.equal(observation.reason, "prepared");
	assert.equal(observation.entitlement, "unknown");
	assert(!JSON.stringify(env.requests).includes("SAMLResponse"));
	assert.deepEqual(env.storageCalls, []);
});

test("A6: timeout during deferred navigation prevents late update, translation, permission and restart revival", async () => {
	const env = makeEnvironment(); env.runtime.start(); await flush();
	env.runtime._accessContext = new env.context.LiteratureMonitorAccess.AccessContext("ctx", [accessRule()]);
	await claim(env, {request_id: "late-access", invocation_id: "invocation-late", doi_url: "https://doi.org/10.5555/a", access_context: "ctx"});
	const tab = env.createdTabs[0].tab;
	const gate = new Deferred(); env.browser.tabs.get = () => gate.promise;
	const updates = []; env.browser.tabs.update = async (...args) => updates.push(args);
	commit(env, tab, "https://sp.example/resource"); await flush();
	await env.clock.advance(20000);
	gate.resolve(tab); await flush();
	commit(env, tab, "https://sp.example/institution-complete"); await flush();
	assert.deepEqual(updates, []); assert.equal(env.saveCalls.length, 0);
	assert.equal(requestsFor(env, "/api/connector/dispatch").length, 0);
	assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "FAILED");
	const restarted = makeEnvironment(); restarted.runtime.start(); await flush();
	assert.equal(restarted.runtime._activeTask, null); assert.equal(restarted.runtime._accessContext, null);
	assert.equal(restarted.saveCalls.length, 0);
});

test("A6: challenge callback and post-trigger navigation cannot borrow old save authority", async () => {
	const env = makeEnvironment(); env.runtime.start(); await flush();
	await claim(env, {request_id: "challenge", invocation_id: "invocation-challenge", doi_url: "https://doi.org/10.5555/a"});
	const tab = env.createdTabs[0].tab;
	commit(env, tab, "https://idp.example/challenge", {transitionType: "form_submit"}); await flush();
	assert.equal(env.saveCalls.length, 0);
	assert.equal(bodiesFor(env, "/api/connector/access")[0].observation.reason, "manual_challenge");
	await claim(env, {request_id: "other-service", invocation_id: "invocation-other", doi_url: "https://doi.org/10.5555/b"});
	const next = env.createdTabs[1].tab;
	commit(env, next, next.url); await flush(); next.status = "complete";
	env.documents.get(env.frames.get(next.id).documentId).translators = [{translatorID: "single", itemType: "journalArticle"}];
	env.tabInfo.set(next.id, {translators: [{translatorID: "single", itemType: "journalArticle"}]});
	env.tabUpdated.emit(next.id, {}, next); await flush();
	assert.equal(env.saveCalls.length, 1);
	const oldMessage = taskMessage(env, "literature-monitor-before-parent-save");
	env.beforeNavigate.emit({tabId: next.id, frameId: 0, url: "https://idp.example/late"}); await flush();
	assert.equal(await env.runtime._handleRuntimeMessage(oldMessage, senderFor(next.id)), false);
	assert.equal(requestsFor(env, "/api/connector/dispatch").length, 0);
	assert.equal(bodiesFor(env, "/api/connector/result").at(-1).outcome, "UNCONFIRMED");
});

test("A6: navigation while readiness awaits bridge cannot dispatch the stale translator", async () => {
	const env = makeEnvironment(); env.runtime.start(); await flush();
	await claim(env, {request_id: "readiness-navigation", invocation_id: "readiness-invocation", doi_url: "https://doi.org/10.5555/a"});
	const tab = env.createdTabs[0].tab;
	const gate = new Deferred(); env.runtime._zoteroReachable = () => gate.promise;
	commit(env, tab, tab.url); await flush(); tab.status = "complete";
	env.documents.get(env.frames.get(tab.id).documentId).translators = [{translatorID: "single", itemType: "journalArticle"}];
	env.tabInfo.set(tab.id, {translators: [{translatorID: "single", itemType: "journalArticle"}]});
	env.tabUpdated.emit(tab.id, {}, tab); await flush();
	assert.equal(env.runtime._activeTask.translatorReady, true);
	env.beforeNavigate.emit({tabId: tab.id, frameId: 0, url: "https://idp.example/auth"}); await flush();
	gate.resolve(true); await flush();
	commit(env, tab, "https://sp.example/late-return"); await flush();
	assert.equal(env.saveCalls.length, 0);
	assert.equal(requestsFor(env, "/api/connector/dispatch").length, 0);
	assert.equal(bodiesFor(env, "/api/connector/result").length, 0);
	await makeSaveReady(env); await env.clock.advance(500);
	assert.equal(env.saveCalls.length, 1);
	assert.equal(env.saveCalls[0].options.literatureMonitorContext.documentID, env.frames.get(tab.id).documentId);
});

async function readinessTask() {
	const env = makeEnvironment(); await startRuntime(env);
	await claim(env, {request_id: "readiness", invocation_id: "readiness-invocation", doi_url: "https://doi.org/10.5555/readiness"});
	return env;
}
async function paperDocument(env, url = "https://publisher.example/article", extra = {}) {
	const tab = env.createdTabs[0].tab; commit(env, tab, url, extra);
	await makeSaveReady(env); return tab;
}
function challengeHeader(env, requestId = `request-${env.accessDocument}`) {
	env.headers.emit({tabId: env.createdTabs[0].tab.id, type: "main_frame", requestId,
		responseHeaders: [{name: "CF-Mitigated", value: "challenge"}]});
}
function assertNoSave(env) {
	assert.equal(env.saveCalls.length, 0); assert.equal(requestsFor(env, "/api/connector/dispatch").length, 0);
}
for (const kind of ["header", "DOM"]) test(`readiness: ${kind} challenge cannot use a single translator or consume dispatch`, async () => {
	const env = await readinessTask(); const tab = env.createdTabs[0].tab;
	commit(env, tab, "https://publisher.example/article");
	if (kind === "header") challengeHeader(env);
	else env.documents.get(env.frames.get(tab.id).documentId).markers.add("#challenge-form");
	await makeSaveReady(env); assertNoSave(env);
	await env.clock.advance(20000); assertNoSave(env);
	assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "FAILED");
	assert.equal(env.runtime._activeTask, null);
});

test("readiness: JSD, Turnstile and Cloudflare article text remain ordinary content", async () => {
	const env = await readinessTask(); const tab = env.createdTabs[0].tab;
	commit(env, tab, "https://publisher.example/article");
	Object.assign(env.documents.get(env.frames.get(tab.id).documentId), {
		body: "Research about Cloudflare challenge-form and Turnstile", title: "Cloudflare research article",
		scripts: ["https://publisher.example/cdn-cgi/challenge-platform/scripts/jsd/main.js", "https://challenges.cloudflare.com/turnstile/v0/api.js"],
		markers: new Set(['[id^="cf-chl-widget-"]', '.cf-turnstile']),
	});
	await makeSaveReady(env); assert.equal(env.saveCalls.length, 1);
	await finishParent(env, tab.id); assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "CONFIRMED");
});

for (const sameURL of [false, true]) test(`readiness: auto challenge reload ${sameURL ? "same URL" : "same origin new path"} binds only the new document`, async () => {
	const env = await readinessTask(); const tab = env.createdTabs[0].tab;
	const challengeURL = "https://publisher.example/challenge";
	commit(env, tab, challengeURL); const oldDoc = env.frames.get(tab.id).documentId;
	challengeHeader(env); await makeSaveReady(env, {translatorID: "challenge-translator", itemType: "journalArticle"}); assertNoSave(env);
	await paperDocument(env, sameURL ? challengeURL : "https://publisher.example/article");
	assert.equal(env.saveCalls.length, 1);
	const context = env.saveCalls[0].options.literatureMonitorContext;
	assert.notEqual(context.documentID, oldDoc); assert.equal(context.translatorID, "translator-single");
	const msg = taskMessage(env, "literature-monitor-before-parent-save");
	assert.equal(await env.runtime._handleRuntimeMessage(msg, senderFor(tab.id, 0, oldDoc)), false);
	assert.equal(requestsFor(env, "/api/connector/dispatch").length, 0);
	assert.equal(await env.runtime._handleRuntimeMessage(msg, senderFor(tab.id, 0, context.documentID)), true);
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-parent-saved"), senderFor(tab.id, 0, context.documentID));
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-pipeline-complete"), senderFor(tab.id, 0, context.documentID));
	await flush(); assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "CONFIRMED");
});

test("readiness: old response arriving after a newer request cannot block or authorize that document", async () => {
	const env = await readinessTask(); const tab = env.createdTabs[0].tab;
	commit(env, tab, "https://publisher.example/challenge"); const oldRequest = `request-${env.accessDocument}`;
	commit(env, tab, "https://publisher.example/article"); challengeHeader(env, oldRequest);
	await makeSaveReady(env); assert.equal(env.saveCalls.length, 1);
	assert.equal(env.saveCalls[0].options.literatureMonitorContext.documentID, env.frames.get(tab.id).documentId);
});

test("readiness: headers preceding navigation and late headers after commit both forbid save", async () => {
	for (const before of [true, false]) {
		const env = await readinessTask(); const tab = env.createdTabs[0].tab;
		env.requestStarted.emit({tabId: tab.id, type: "main_frame", requestId: "unordered"});
		if (before) challengeHeader(env, "unordered");
		commit(env, tab, "https://publisher.example/article", {requestId: "unordered"});
		if (!before) challengeHeader(env, "unordered");
		await makeSaveReady(env); assertNoSave(env);
	}
});

test("readiness: creation starts only a blank document; DOI navigation begins after task tab ownership", async () => {
	const env = makeEnvironment(); await startRuntime(env);
	const originalCreate = env.browser.tabs.create; const calls = [];
	env.browser.tabs.create = async options => {
		const tab = await originalCreate(options);
		env.beforeNavigate.emit({tabId: tab.id, frameId: 0, url: options.url});
		env.committed.emit({tabId: tab.id, frameId: 0, url: options.url, documentId: "initial-blank"});
		return tab;
	};
	env.browser.tabs.update = async (id, options) => {
		assert.equal(env.runtime._activeTask.tabID, id); calls.push(options.url);
		const tab = env.tabs.get(id); commit(env, tab, "https://publisher.example/article"); return tab;
	};
	await claim(env, {request_id: "early", invocation_id: "early-invocation", doi_url: "https://doi.org/10.5555/early"});
	assert.equal(env.createdTabs[0].options.url, "about:blank");
	assert.deepEqual(calls, ["https://doi.org/10.5555/early"]);
	await makeSaveReady(env); assert.equal(env.saveCalls.length, 1);
});

for (const boundary of ["DOM", "online", "access"]) test(`readiness: navigation during ${boundary} await discards the late candidate and recovers`, async () => {
	const env = await readinessTask(); const gate = new Deferred();
	let held = false;
	if (boundary === "DOM") {
		const execute = env.browser.scripting.executeScript;
		env.browser.scripting.executeScript = async options => {
			const result = await execute(options); if (!held) {held = true; await gate.promise;} return result;
		};
	}
	else if (boundary === "online") env.runtime._zoteroReachable = async () => {held = true; return gate.promise;};
	else env.enqueue("/api/connector/access", gate.promise);
	const tab = await paperDocument(env); const oldDoc = env.frames.get(tab.id).documentId;
	assertNoSave(env);
	commit(env, tab, "https://publisher.example/final");
	gate.resolve(boundary === "access" ? response(200, {accepted: true}) : true); await flush();
	await makeSaveReady(env); await env.clock.advance(500);
	assert.equal(env.saveCalls.length, 1);
	assert.notEqual(env.saveCalls[0].options.literatureMonitorContext.documentID, oldDoc);
});

test("readiness: same-document challenge disappearance cannot reuse cached or unversioned translators", async () => {
	const env = await readinessTask(); const tab = env.createdTabs[0].tab;
	commit(env, tab, "https://publisher.example/article"); const doc = env.documents.get(env.frames.get(tab.id).documentId);
	doc.markers.add("#challenge-form"); await makeSaveReady(env); assertNoSave(env);
	doc.markers.clear(); doc.translators = [{translatorID: "redetected-but-no-start-provenance", itemType: "journalArticle"}];
	await env.clock.advance(20000); assertNoSave(env);
	assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "FAILED");
});

test("readiness: iframe/stale background translators cannot authorize current top document", async () => {
	const env = await readinessTask(); const tab = env.createdTabs[0].tab;
	commit(env, tab, "https://publisher.example/article"); tab.status = "complete";
	env.tabInfo.set(tab.id, {instanceID: 555, translators: [{translatorID: "old-or-iframe", itemType: "journalArticle"}]});
	env.tabUpdated.emit(tab.id); await flush(); assertNoSave(env);
	await makeSaveReady(env); assert.equal(env.saveCalls.length, 1);
});

test("readiness: missing document identity fails closed; a current document alone still requires actual content", async () => {
	for (const missing of ["identity", "content"]) {
		const env = await readinessTask(); const tab = env.createdTabs[0].tab;
		commit(env, tab, "https://publisher.example/article");
		if (missing === "identity") env.frames.get(tab.id).documentId = undefined;
		else env.documents.get(env.frames.get(tab.id).documentId).body = " ";
		tab.status = "complete"; env.tabInfo.set(tab.id, {translators: [{translatorID: "single", itemType: "journalArticle"}]});
		if (missing !== "identity") env.documents.get(env.frames.get(tab.id).documentId).translators = env.tabInfo.get(tab.id).translators;
		env.tabUpdated.emit(tab.id); await env.clock.advance(20000); assertNoSave(env);
		assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "FAILED");
	}
});

test("readiness: consecutive/late commits and cancelled navigation without a new translator cannot revive old results", async () => {
	const env = await readinessTask(); const gate = new Deferred(); const execute = env.browser.scripting.executeScript;
	env.browser.scripting.executeScript = async options => {const result = await execute(options); await gate.promise; return result;};
	const tab = await paperDocument(env); const oldDoc = env.frames.get(tab.id).documentId;
	commit(env, tab, "https://publisher.example/second"); commit(env, tab, "https://publisher.example/third");
	env.committed.emit({tabId: tab.id, frameId: 0, documentId: oldDoc, url: "https://publisher.example/article"});
	env.navigationError.emit({tabId: tab.id, frameId: 0, error: "net::ERR_ABORTED"});
	gate.resolve(); await env.clock.advance(20000); assertNoSave(env);
	assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "FAILED");
});

for (const sameURL of [false, true]) test(`readiness: provisional error recovers with one save on a new ${sameURL ? "same-URL" : "different-URL"} document`, async () => {
	const env = await readinessTask(); const tab = env.createdTabs[0].tab;
	const gate = new Deferred(); const execute = env.browser.scripting.executeScript;
	env.browser.scripting.executeScript = async options => {const result = await execute(options); await gate.promise; return result;};
	commit(env, tab, "https://publisher.example/article");
	await makeSaveReady(env, {translatorID: "old-translator", itemType: "journalArticle"});
	const oldDoc = env.frames.get(tab.id).documentId;
	await env.clock.advance(19000);
	const url = sameURL ? tab.url : "https://publisher.example/final";
	env.beforeNavigate.emit({tabId: tab.id, frameId: 0, url, processId: -1});
	env.navigationError.emit({tabId: tab.id, frameId: 0, url, documentId: oldDoc, processId: -1, error: "net::ERR_ABORTED"});
	gate.resolve(); await flush(); assertNoSave(env);
	await paperDocument(env, url); await env.clock.advance(500);
	assert.equal(env.saveCalls.length, 1);
	const context = env.saveCalls[0].options.literatureMonitorContext;
	assert.notEqual(context.documentID, oldDoc);
	assert.equal(context.documentID, env.frames.get(tab.id).documentId);
	assert.equal(context.translatorID, "translator-single");
	const dispatch = taskMessage(env, "literature-monitor-before-parent-save");
	assert.equal(await env.runtime._handleRuntimeMessage(dispatch, senderFor(tab.id, 0, oldDoc)), false);
	assert.equal(await env.runtime._handleRuntimeMessage(dispatch, senderFor(tab.id, 0, context.documentID)), true);
	assert.equal(await env.runtime._handleRuntimeMessage(dispatch, senderFor(tab.id, 0, context.documentID)), false);
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-parent-saved"), senderFor(tab.id, 0, context.documentID));
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-pipeline-complete"), senderFor(tab.id, 0, context.documentID));
	await env.clock.advance(20000);
	assert.equal(env.saveCalls.length, 1);
	assert.equal(requestsFor(env, "/api/connector/dispatch").length, 1);
	assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "CONFIRMED");
});

for (const duringCommit of [false, true]) test(`readiness: late old navigation error ${duringCommit ? "during commit validation" : "after commit"} cannot terminate the current same-URL candidate`, async () => {
	const env = await readinessTask(); const tab = env.createdTabs[0].tab;
	commit(env, tab, "https://publisher.example/article"); await flush();
	const oldDoc = env.frames.get(tab.id).documentId;
	const gate = new Deferred(); env.runtime._zoteroReachable = () => gate.promise;
	commit(env, tab, tab.url);
	if (!duringCommit) await flush();
	env.navigationError.emit({tabId: tab.id, frameId: 0, url: tab.url, documentId: oldDoc, processId: -1, error: "net::ERR_ABORTED"});
	await makeSaveReady(env); gate.resolve(true); await flush(); await env.clock.advance(500);
	assert.equal(env.saveCalls.length, 1);
	assert.notEqual(env.saveCalls[0].options.literatureMonitorContext.documentID, oldDoc);
	assert.equal(env.saveCalls[0].options.literatureMonitorContext.documentID, env.frames.get(tab.id).documentId);
});

for (const kind of ["provisional", "current-document", "unavailable-frame"]) test(`readiness: unrecoverable ${kind} navigation error stops at the original deadline without saving old content`, async () => {
	const env = await readinessTask(); const gate = new Deferred(); env.runtime._zoteroReachable = () => gate.promise;
	const tab = await paperDocument(env); const documentId = env.frames.get(tab.id).documentId;
	await env.clock.advance(19000);
	if (kind === "provisional") env.beforeNavigate.emit({tabId: tab.id, frameId: 0, url: tab.url, processId: -1});
	if (kind === "unavailable-frame") env.browser.webNavigation.getFrame = async () => {throw new Error("frame unavailable");};
	env.navigationError.emit({tabId: tab.id, frameId: 0, url: tab.url,
		documentId: kind === "current-document" ? documentId : "failed-provisional", error: "net::ERR_CONNECTION_RESET"});
	gate.resolve(true); await flush(); await env.clock.advance(999); assertNoSave(env);
	assert.equal(bodiesFor(env, "/api/connector/result").length, 0);
	await env.clock.advance(1); assertNoSave(env);
	assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "FAILED");
	assert.equal(env.runtime._activeTask, null);
	await env.clock.advance(20000); assertNoSave(env);
});

test("readiness: a late error revalidation cannot clear a newer pending navigation", async () => {
	const env = await readinessTask(); const gate = new Deferred(); env.runtime._zoteroReachable = () => gate.promise;
	const tab = await paperDocument(env); const oldFrame = {...env.frames.get(tab.id)};
	const frameGate = new Deferred(); env.browser.webNavigation.getFrame = () => frameGate.promise;
	env.navigationError.emit({tabId: tab.id, frameId: 0, documentId: "old-provisional", error: "net::ERR_ABORTED"});
	env.beforeNavigate.emit({tabId: tab.id, frameId: 0, url: tab.url});
	frameGate.resolve(oldFrame); gate.resolve(true); await env.clock.advance(20000); assertNoSave(env);
	assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "FAILED");
});

test("readiness: late error without document identity must revalidate the current committed frame", async () => {
	const env = await readinessTask(); const gate = new Deferred(); env.runtime._zoteroReachable = () => gate.promise;
	const tab = await paperDocument(env);
	env.navigationError.emit({tabId: tab.id, frameId: 0, error: "net::ERR_ABORTED"});
	gate.resolve(true); await flush(); await env.clock.advance(500);
	assert.equal(env.saveCalls.length, 1);
	assert.equal(env.saveCalls[0].options.literatureMonitorContext.documentID, env.frames.get(tab.id).documentId);
});

for (const frameError of [false, true]) test(`readiness: error during commit (${frameError ? "frame also errored" : "error event only"}) cannot authorize its translator but a healthy new commit can recover`, async () => {
	const env = await readinessTask(); const tab = env.createdTabs[0].tab;
	commit(env, tab, "https://publisher.example/article");
	env.frames.get(tab.id).errorOccurred = frameError;
	env.navigationError.emit({tabId: tab.id, frameId: 0, documentId: env.frames.get(tab.id).documentId, error: "net::ERR_CONNECTION_RESET"});
	await makeSaveReady(env); await env.clock.advance(19000); assertNoSave(env);
	await paperDocument(env); await env.clock.advance(500);
	assert.equal(env.saveCalls.length, 1);
	assert.equal(env.saveCalls[0].options.literatureMonitorContext.documentID, env.frames.get(tab.id).documentId);
});

test("readiness: navigation error after save remains UNCONFIRMED even if a new document is ready", async () => {
	const env = await readinessTask(); const tab = await paperDocument(env);
	assert.equal(env.saveCalls.length, 1);
	const dispatch = taskMessage(env, "literature-monitor-before-parent-save");
	env.navigationError.emit({tabId: tab.id, frameId: 0, documentId: "old-provisional", error: "net::ERR_ABORTED"});
	await paperDocument(env, "https://publisher.example/final"); await env.clock.advance(20000);
	assert.equal(env.saveCalls.length, 1);
	assert.equal(await env.runtime._handleRuntimeMessage(dispatch, senderFor(tab.id)), false);
	assert.equal(requestsFor(env, "/api/connector/dispatch").length, 0);
	assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "UNCONFIRMED");
});

test("readiness: navigation during native permission await prevents saveItems and any second invocation", async () => {
	const env = await readinessTask(); const tab = await paperDocument(env);
	const attempt = {context: taskMessage(env, "unused").context, dispatchRequested: false, deadline: 75000};
	const saver = {_literatureMonitorAutomatic: true, _literatureMonitorAttempt: attempt, _sessionID: "native-session"};
	const gate = new Deferred(); env.enqueue("/api/connector/dispatch", gate.promise);
	let nativeCalls = 0;
	const transport = {runtime: {sendMessage: m => env.runtime._handleRuntimeMessage(m, senderFor(tab.id, 0, attempt.context.documentID))}};
	const save = nativeHook({Connector: {callMethod: async () => {nativeCalls++; return {items: [{id: "parent"}]};}}}, transport, {now: () => 0}).bind(saver);
	const saving = save([{DOI: "10.5555/readiness", itemType: "journalArticle"}], {items: [{id: "parent"}]});
	const rejection = assert.rejects(saving, /no longer authorized/); await flush();
	assert.equal(requestsFor(env, "/api/connector/dispatch").length, 1);
	commit(env, tab, "https://publisher.example/other"); gate.resolve(response(200, {accepted: true})); await rejection; await flush();
	assert.equal(nativeCalls, 0); assert.equal(env.saveCalls.length, 1);
	assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "UNCONFIRMED");
	assert.equal(await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-before-parent-save"), senderFor(tab.id)), false);
});

test("readiness: ordinary same-origin paths and one same-URL reload are bounded progress", () => {
	const env = makeEnvironment(); const access = journey(env);
	access.observeRedirect(access.doiURL, "https://publisher.example/challenge");
	observed(access, "https://publisher.example/challenge", "challenge");
	access.observeRedirect("https://publisher.example/challenge", "https://publisher.example/article");
	observed(access, "https://publisher.example/article", "paper");
	observed(access, "https://publisher.example/article", "reload");
	assert.equal(access.captureAllowed, true);
	observed(access, "https://publisher.example/article", "no-progress");
	assert.equal(access.reason, "redirect_loop"); assert.equal(access.captureAllowed, false);
	assert(!JSON.stringify(access).includes("/challenge"));
});

test("readiness: a network request completing before navigation cannot reuse the still-active old document", async () => {
	const env = await readinessTask(); const tab = env.createdTabs[0].tab;
	commit(env, tab, "https://publisher.example/old"); await flush();
	const old = env.frames.get(tab.id).documentId;
	env.documents.get(old).translators = [{translatorID: "old", itemType: "journalArticle"}];
	tab.status = "complete";
	env.requestStarted.emit({tabId: tab.id, type: "main_frame", requestId: "new-request"});
	env.requestCompleted.emit({tabId: tab.id, type: "main_frame", requestId: "new-request"});
	// A late old commit is not enough to assign this new response to the old document.
	env.committed.emit({tabId: tab.id, frameId: 0, documentId: old, url: tab.url});
	env.tabUpdated.emit(tab.id); await env.clock.advance(500); assertNoSave(env);
	await paperDocument(env, "https://publisher.example/new"); assert.equal(env.saveCalls.length, 1);
});

test("readiness: final DOM injection rejected by a navigation recovers without another task deadline", async () => {
	const env = await readinessTask(); const execute = env.browser.scripting.executeScript; let count = 0;
	env.browser.scripting.executeScript = async options => {
		if (++count === 2) {commit(env, env.createdTabs[0].tab, "https://publisher.example/final"); throw new Error("Old document removed");}
		return execute(options);
	};
	await paperDocument(env); assertNoSave(env);
	await makeSaveReady(env); await env.clock.advance(500); assert.equal(env.saveCalls.length, 1);
});

test("readiness: same-document challenge arising during dispatch blocks native saveItems", async () => {
	const env = await readinessTask(); const tab = await paperDocument(env);
	const attempt = {context: taskMessage(env, "unused").context, dispatchRequested: false, deadline: 75000};
	const saver = {_literatureMonitorAutomatic: true, _literatureMonitorAttempt: attempt, _sessionID: "native-session"};
	const gate = new Deferred(); env.enqueue("/api/connector/dispatch", gate.promise); let calls = 0;
	const transport = {runtime: {sendMessage: m => env.runtime._handleRuntimeMessage(m, senderFor(tab.id, 0, attempt.context.documentID))}};
	const save = nativeHook({Connector: {callMethod: async () => {calls++;return {items: [{id: "parent"}]};}}},transport,{now: () => 0}).bind(saver);
	const saving = save([{DOI: "10.5555/readiness", itemType: "journalArticle"}], {items: [{id: "parent"}]});
	const rejected = assert.rejects(saving, /no longer authorized/); await flush();
	env.documents.get(attempt.context.documentID).markers.add("#challenge-form"); gate.resolve(response(200,{accepted:true}));
	await rejected; await env.clock.advance(75000);
	assert.equal(calls, 0); assert.equal(env.saveCalls.length, 1); assert.equal(requestsFor(env,"/api/connector/dispatch").length,1);
	assert.equal(bodiesFor(env,"/api/connector/result")[0].outcome,"UNCONFIRMED");
});

test("readiness: native parent confirmation survives unrelated same-document translator updates", async () => {
	const env = await readinessTask(); const tab = await paperDocument(env); const context = env.saveCalls[0].options.literatureMonitorContext;
	const sender = senderFor(tab.id,0,context.documentID);
	assert.equal(await env.runtime._handleRuntimeMessage(taskMessage(env,"literature-monitor-before-parent-save"), sender),true);
	env.documents.get(context.documentID).translators = [];
	await env.runtime._handleRuntimeMessage(taskMessage(env,"literature-monitor-parent-saved"), sender);
	await env.runtime._handleRuntimeMessage(taskMessage(env,"literature-monitor-pipeline-complete"), sender); await flush();
	assert.equal(bodiesFor(env,"/api/connector/result")[0].outcome,"CONFIRMED"); assert.equal(env.saveCalls.length,1);
});

test("readiness: automatic upstream save targets only the selected current document and translator", async () => {
	const start = patchedLines.indexOf("this.saveWithTranslator = function");
	const end = patchedLines.indexOf("\n\t\tlet tabInfo", start); assert(start >= 0 && end > start);
	const calls = [];
	const owner = {getTabInfo() {throw new Error("Automatic save must not read the unbound tab cache");}};
	new Function("browser", `${patchedLines.slice(start,end)}\n}`).call(owner,{tabs:{sendMessage: async (...args) => {calls.push(args);return true;}}});
	const context = {documentID:"current-document",translatorID:"current-translator"};
	await owner.saveWithTranslator({id:100},0,{literatureMonitorAutomatic:true,literatureMonitorContext:context});
	assert.deepEqual(calls[0],[100,["translate",[0,"current-translator",{literatureMonitorAutomatic:true,literatureMonitorContext:context}]],{documentId:"current-document"}]);
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

test("A4: delayed dispatch permission after terminal task cannot initiate native save", async () => {
	const env = makeEnvironment();
	await claim(env, {request_id: "old-task", invocation_id: "old-invocation", doi_url: "https://doi.org/10.5555/old"});
	const tab = await makeSaveReady(env);
	const attempt = {context: taskMessage(env, "unused").context, dispatchRequested: false, deadline: 75000};
	const saver = {_literatureMonitorAutomatic: true, _literatureMonitorAttempt: attempt, _sessionID: "native-session"};
	let resume;
	const held = new Promise(resolve => { resume = resolve; });
	let nativeCalls = 0;
	const transport = {runtime: {sendMessage: async message => {
		if (message.type === "literature-monitor-before-parent-save") await held;
		return env.runtime._handleRuntimeMessage(message, senderFor(tab.id));
	}}};
	const Zotero = {Connector: {callMethod: async () => { nativeCalls++; return {status: 201, body: ""}; }}};
	const saving = nativeHook(Zotero, transport, {now: () => 0}).call(saver,
		[{DOI: "10.5555/old", itemType: "journalArticle"}], {items: [{id: "parent"}]});
	const rejection = assert.rejects(saving, /no longer authorized/);
	await env.clock.advance(75000);
	resume();
	await rejection;
	assert.equal(nativeCalls, 0);
});

test("A4: native parent confirmation consumes permission; replay cannot save a second parent", async () => {
	const env = makeEnvironment();
	await claim(env, {request_id: "once", invocation_id: "once-invocation", doi_url: "https://doi.org/10.5555/once"});
	const tab = await makeSaveReady(env);
	const attempt = {context: taskMessage(env, "unused").context, dispatchRequested: false, deadline: 75000};
	const saver = {_literatureMonitorAutomatic: true, _literatureMonitorAttempt: attempt, _sessionID: "native-session"};
	let nativeCalls = 0;
	const Zotero = {Connector: {callMethod: async () => { nativeCalls++; return {status: 201, body: ""}; }}};
	const transport = {runtime: {sendMessage: message => env.runtime._handleRuntimeMessage(message, senderFor(tab.id))}};
	const save = nativeHook(Zotero, transport, {now: () => 0}).bind(saver);
	await save([{DOI: "10.5555/once", itemType: "journalArticle"}], {items: [{id: "parent"}]});
	await assert.rejects(save([{DOI: "10.5555/once", itemType: "journalArticle"}], {items: [{id: "parent"}]}), /already dispatched/);
	await env.runtime._handleRuntimeMessage(taskMessage(env, "literature-monitor-pipeline-complete"), senderFor(tab.id));
	assert.equal(nativeCalls, 1);
	await flush();
	assert.equal(bodiesFor(env, "/api/connector/result")[0].outcome, "CONFIRMED");
});
