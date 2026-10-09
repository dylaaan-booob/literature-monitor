/*
 * Literature Monitor Connector automatic capture runtime.
 *
 * This file is part of the Literature Monitor Connector derivative of
 * zotero/zotero-connectors and is distributed under the GNU Affero General
 * Public License, version 3 or (at your option) any later version.
 *
 * Copyright © 2026 Literature Monitor contributors
 */

(function(global) {
	"use strict";

	const BRIDGE_BASE = "http://127.0.0.1:8000";
	const HEARTBEAT_INTERVAL_MS = 5000;
	const CLAIM_INTERVAL_MS = 1000;
	const FETCH_TIMEOUT_MS = 3000;
	const TRANSLATOR_POLL_INTERVAL_MS = 500;
	const OBSERVATION_DEADLINE_MS = 75000;
	const RESULT_RETRY_DELAY_MS = 500;
	const RESULT_ATTEMPTS = 2;
	const MAX_RESPONSE_LENGTH = 8192;
	const MAX_REQUEST_ID_LENGTH = 128;

	const OUTCOME_CONFIRMED = "CONFIRMED";
	const OUTCOME_UNCONFIRMED = "UNCONFIRMED";
	const OUTCOME_FAILED = "FAILED";

	const INTERNAL_PARENT_SAVED = "literature-monitor-parent-saved";
	const INTERNAL_SAVE_FAILED = "literature-monitor-save-failed";
	const INTERNAL_PIPELINE_COMPLETE = "literature-monitor-pipeline-complete";

	function delay(ms) {
		return new Promise(resolve => setTimeout(resolve, ms));
	}

	function isPlainObject(value) {
		return !!value && typeof value === "object" && !Array.isArray(value);
	}

	function requestIDIsValid(value) {
		return typeof value === "string"
			&& value.length > 0
			&& value.length <= MAX_REQUEST_ID_LENGTH
			&& value.trim().length > 0;
	}

	function validateDOIURL(value) {
		if (typeof value !== "string" || !value) {
			return null;
		}

		let parsed;
		try {
			parsed = new URL(value);
		}
		catch (_) {
			return null;
		}

		if (parsed.protocol !== "https:"
				|| parsed.hostname !== "doi.org"
				|| parsed.host !== "doi.org"
				|| parsed.username
				|| parsed.password
				|| parsed.port
				|| parsed.search
				|| parsed.hash
				|| !parsed.pathname
				|| parsed.pathname === "/") {
			return null;
		}

		return parsed.href;
	}

	function parseCommand(command) {
		if (!isPlainObject(command)) {
			return { requestID: null, command: null };
		}

		const requestID = requestIDIsValid(command.request_id)
			? command.request_id
			: null;
		const keys = Object.keys(command).sort();
		const accessContext = command.access_context;
		if (accessContext !== undefined && !requestIDIsValid(accessContext)) return {requestID, command: null};
		const commandKeys = keys.filter(key => key !== "access_context");
		if (commandKeys.length !== 3
				|| commandKeys[0] !== "doi_url"
				|| commandKeys[1] !== "invocation_id"
				|| commandKeys[2] !== "request_id") {
			return { requestID, command: null };
		}

		const doiURL = validateDOIURL(command.doi_url);
		if (!requestID || !doiURL || !requestIDIsValid(command.invocation_id)) {
			return { requestID, command: null };
		}

		return {
			requestID,
			command: {
				requestID,
				doiURL,
				invocationID: command.invocation_id,
				accessContext: accessContext ?? requestID,
			},
		};
	}

	function inspectPage() {
		const title = document.title.trim();
		const waitingTitle = /^(?:just a moment|checking your browser)[\s.!…]*$/i.test(title);
		const challengeElement = ["#challenge-form", "#challenge-running", "#challenge-stage", "#challenge-error-text"]
			.some(selector => !!document.querySelector(selector));
		const orchestrate = [...document.querySelectorAll("script[src]")].some(script =>
			/\/cdn-cgi\/challenge-platform\/(?:h\/[a-z]\/)?orchestrate\//.test(new URL(script.src, document.location.href).pathname));
		const challenge = challengeElement || waitingTitle || orchestrate;
		const error = document.querySelector("#challenge-error-text");
		const translator = typeof Zotero !== "undefined" && Zotero.PageSaving?.translators?.[0];
		return {
			ready: document.readyState === "complete", challenge,
			manual: challenge && !!error?.textContent.trim(),
			content: !!title && !!document.body?.childElementCount && !!document.body.textContent.trim(),
			translatorID: translator?.translatorID, itemType: translator?.itemType,
		};
	}

	const Runtime = {
		_installed: false,
		_started: false,
		_keepAliveOwned: false,
		_heartbeatInFlight: false,
		_claimInFlight: false,
		_activeTask: null,
		_heartbeatTimer: null,
		_claimTimer: null,
		_runtimeMessageListener: null,
		_tabRemovedListener: null,
		_accessContext: null,

		install() {
			if (this._installed) {
				return;
			}
			this._installed = true;

			Promise.resolve(Zotero.initDeferred.promise)
				.then(() => this.start())
				.catch(error => Zotero.logError(error));
		},

		start() {
			if (Zotero.isChrome !== true
					|| Zotero.isManifestV3 !== true
					|| this._started) {
				return;
			}
			this._started = true;

			if (!this._keepAliveOwned) {
				Zotero.Connector_Browser.setKeepServiceWorkerAlive(true);
				this._keepAliveOwned = true;
			}

			this._runtimeMessageListener = (message, sender) => {
				// Unrelated upstream messages must remain owned by upstream listeners.
				if (!message || ![INTERNAL_PARENT_SAVED, INTERNAL_SAVE_FAILED, INTERNAL_PIPELINE_COMPLETE,
					"literature-monitor-before-parent-save"].includes(message.type)) return;
				return this._handleRuntimeMessage(message, sender);
			};
			browser.runtime.onMessage.addListener(this._runtimeMessageListener);

			this._tabRemovedListener = tabID => {
				this._handleTaskTabRemoved(tabID);
			};
			browser.tabs.onRemoved.addListener(this._tabRemovedListener);
			browser.webNavigation.onCommitted.addListener(details => void this._documentCommitted(details));
			browser.webNavigation.onBeforeNavigate.addListener(details => {
				const task = this._navigationTask(details);
				if (!task) return;
				this._invalidateDocument(task);
				task.navigationPending = true;
			});
			browser.webNavigation.onErrorOccurred.addListener(details => void this._navigationFailed(details));
			// Chrome 顶层响应没有 documentId，两个事件流也无先后保证。
			// 只接受最新 main_frame request，并排除请求前的文档；不保留 URL、Cookie 或正文。
			const filter = {urls: ["<all_urls>"], types: ["main_frame"]};
			browser.webRequest.onBeforeRequest.addListener(details => {
				const task = this._requestTask(details);
				if (!task) return;
				this._invalidateDocument(task);
				const challenge = task.response?.id === details.requestId && task.response.challenge;
				task.navigationPending = true;
				task.response = {
					id: details.requestId, challenge: !!challenge, complete: false, documentID: null,
					// 响应可能在旧文档仍活动时到达，必须拒绝把它赋给旧文档。
					priorDocument: browser.webNavigation.getFrame({tabId: task.tabID, frameId: 0})
						.then(frame => frame?.documentId ?? null).catch(() => undefined),
				};
			}, filter);
			browser.webRequest.onHeadersReceived.addListener(details => {
				const task = this._requestTask(details);
				if (!task || !details.responseHeaders?.some(header =>
					header.name.toLowerCase() === "cf-mitigated" && header.value?.trim().toLowerCase() === "challenge")) return;
				if (!task.response) {
					// An uncorrelatable response cannot establish document readiness.
					this._finishTask(task, task.saveTriggered ? OUTCOME_UNCONFIRMED : OUTCOME_FAILED);
					return;
				}
				if (task.response.id !== details.requestId) return;
				task.response.challenge = true;
				this._invalidateDocument(task);
			}, filter, ["responseHeaders"]);
			browser.webRequest.onCompleted.addListener(details => {
				const task = this._requestTask(details);
				if (task?.response?.id === details.requestId) task.response.complete = true;
			}, filter);
			browser.webRequest.onBeforeRedirect.addListener(details => {
				const task = this._requestTask(details);
				if (!task) return;
				if (task.saveTriggered) { this._finishTask(task, OUTCOME_UNCONFIRMED); return; }
				task.access.observeRedirect(details.url, details.redirectUrl);
				if (!task.access.captureAllowed) this._finishTask(task, OUTCOME_FAILED);
			}, filter);

			void this._heartbeatTick();
			void this._claimTick();
			this._heartbeatTimer = setInterval(
				() => void this._heartbeatTick(),
				HEARTBEAT_INTERVAL_MS,
			);
			this._claimTimer = setInterval(
				() => void this._claimTick(),
				CLAIM_INTERVAL_MS,
			);
		},

		_navigationTask(details) {
			const task = this._activeTask;
			return task && !task.terminalOutcome && details.tabId === task.tabID && details.frameId === 0 ? task : null;
		},

		_requestTask(details) {
			const task = this._activeTask;
			return task && !task.terminalOutcome && details.tabId === task.tabID && details.type === "main_frame" ? task : null;
		},

		_invalidateDocument(task) {
			task.generation++;
			task.translatorReady = false;
			if (task.saveTriggered) this._finishTask(task, OUTCOME_UNCONFIRMED);
		},

		async _navigationFailed(details) {
			const task = this._navigationTask(details);
			if (!task) return;
			if (task.saveTriggered) { this._finishTask(task, OUTCOME_UNCONFIRMED); return; }
			if (details.documentId) task.erroredDocuments.add(details.documentId);
			// 已在导航的文档没有保存资格；旧 provisional 错误不能取消新 commit 的核验。
			if (task.navigationPending) return;
			this._invalidateDocument(task);
			const generation = task.generation;
			task.navigationPending = true;
			if (!task.documentID || details.documentId === task.documentID) return;
			try {
				const frame = await browser.webNavigation.getFrame({tabId: task.tabID, frameId: 0});
				// 迟到错误只能恢复仍有效的已提交文档，不能认领另一文档或覆盖新导航。
				if (this._taskCanPrepare(task) && generation === task.generation
					&& frame?.documentId === task.documentID && !frame.errorOccurred
					&& frame.documentLifecycle === "active") task.navigationPending = false;
			}
			catch (_) { /* 保持失效，等待新 commit 或原 preparation deadline。 */ }
		},

		async _documentCommitted(details) {
			const task = this._navigationTask(details);
			if (!task) return;
			this._invalidateDocument(task);
			const generation = task.generation;
			try {
				const frame = await browser.webNavigation.getFrame({tabId: task.tabID, frameId: 0});
				if (!this._taskCanPrepare(task) || generation !== task.generation
					|| !details.documentId || frame?.documentId !== details.documentId
					|| task.erroredDocuments.has(frame.documentId)
					|| frame.errorOccurred || frame.documentLifecycle !== "active") return;
				task.documentID = frame.documentId;
				task.navigationPending = false;
				task.access.observe(details);
				if (!task.access.captureAllowed) this._finishTask(task, OUTCOME_FAILED);
				else void this._navigateAccess(task);
			}
			catch (_) {
				if (this._taskCanPrepare(task)) this._finishTask(task, OUTCOME_FAILED);
			}
		},

		async _postJSON(path, payload) {
			const controller = new AbortController();
			const timeout = setTimeout(
				() => controller.abort(),
				FETCH_TIMEOUT_MS,
			);
			try {
				const response = await fetch(BRIDGE_BASE + path, {
					method: "POST",
					headers: {
						"Content-Type": "application/json",
					},
					body: JSON.stringify(payload),
					credentials: "omit",
					signal: controller.signal,
				});
				const text = await response.text();
				if (text.length > MAX_RESPONSE_LENGTH) {
					throw new Error("Literature Monitor bridge response is too large");
				}

				let body = null;
				if (text) {
					body = JSON.parse(text);
				}
				return {
					ok: response.ok,
					status: response.status,
					body,
				};
			}
			finally {
				clearTimeout(timeout);
			}
		},

		async _zoteroReachable() {
			try {
				return await Zotero.Connector.checkIsOnline() === true;
			}
			catch (_) {
				return false;
			}
		},

		async _heartbeatTick() {
			if (this._heartbeatInFlight) {
				return;
			}
			this._heartbeatInFlight = true;
			try {
				const reachable = await this._zoteroReachable();
				const response = await this._postJSON(
					"/api/connector/heartbeat",
					{
						version: browser.runtime.getManifest().version,
						zotero_reachable: reachable,
					},
				);
				if (!response.ok) {
					throw new Error(
						`Literature Monitor heartbeat failed with status ${response.status}`,
					);
				}
			}
			catch (_) {
				// Bridge availability is intentionally not Connector/Zotero authority.
			}
			finally {
				this._heartbeatInFlight = false;
			}
		},

		async _claimTick() {
			if (this._claimInFlight || this._activeTask) {
				return;
			}
			this._claimInFlight = true;
			try {
				const response = await this._postJSON(
					"/api/connector/claim",
					{},
				);
				if (!response.ok || !isPlainObject(response.body)
						|| !Object.prototype.hasOwnProperty.call(response.body, "command")) {
					return;
				}
				if (response.body.command === null) {
					return;
				}

				const parsed = parseCommand(response.body.command);
				if (!parsed.command) return;

				const task = {
					requestID: parsed.command.requestID,
					doiURL: parsed.command.doiURL,
					invocationID: parsed.command.invocationID,
					sessionID: null,
					dispatchRequested: false,
					dispatchAccepted: false,
					tabID: null,
					saveTriggered: false,
					parentConfirmed: false,
					pipelineComplete: false,
					pipelineEnded: false,
					terminalOutcome: null,
					observationTimer: null,
					translatorCancel: null,
					translatorReady: false,
					generation: 0,
					navigationPending: true,
					documentID: null,
					response: null,
					challengedDocuments: new Set(),
					erroredDocuments: new Set(),
				};
				if (this._accessContext?.id !== parsed.command.accessContext) {
					this._accessContext = new LiteratureMonitorAccess.AccessContext(parsed.command.accessContext);
				}
				task.access = new LiteratureMonitorAccess.AccessJourney(this._accessContext, task.doiURL);
				task.accessTimer = setTimeout(() => {
					if (!task.terminalOutcome && !task.saveTriggered) {
						task.access.fallback("access_timeout");
						this._finishTask(task, OUTCOME_FAILED);
					}
				}, Math.max(0, task.access.deadline - task.access.now()));
				this._activeTask = task;
				void this._runTask(task);
			}
			catch (_) {
				// A failed claim request cannot manufacture a command or result.
			}
			finally {
				this._claimInFlight = false;
			}
		},

		async _runTask(task) {
			try {
				const windows = await browser.windows.getAll({windowTypes: ["normal"]});
				const activeTabs = await browser.tabs.query({active: true}).catch(() => []);
				const inNormalWindow = tab => this._isAppTab(tab)
					&& windows.some(window => window.id === tab.windowId
						&& window.type === "normal" && window.incognito === false);
				const appTab = activeTabs.find(tab => inNormalWindow(tab)
					&& windows.some(window => window.id === tab.windowId && window.focused === true))
					|| activeTabs.find(inNormalWindow);
				const normalWindow = windows.find(window => window.id === appTab?.windowId)
					|| windows.find(window => window.type === "normal"
						&& window.incognito === false && Number.isInteger(window.id));
				if (!normalWindow) {
					throw new Error("No normal non-incognito Chrome window is available");
				}
				if (!this._taskCanPrepare(task)) return;
				task.appTabID = appTab?.id ?? null;
				task.appWindowID = appTab?.windowId ?? null;
				const tab = await browser.tabs.create({
					url: "about:blank",
					windowId: normalWindow.id,
				});
				if (!tab || !Number.isInteger(tab.id)) {
					throw new Error("Dedicated task tab could not be created");
				}
				if (tab.incognito !== false) {
					// Only legal task tabs qualify for failed-task retention (§40.6).
					await browser.tabs.remove(tab.id);
					throw new Error("Dedicated task tab is not confirmed non-incognito");
				}
				if (!this._taskCanPrepare(task)) {
					return;
				}
				task.tabID = tab.id;
				task.windowID = normalWindow.id;
				// 先拥有空白任务标签页，再导航 DOI，避免 tabs.create 返回前漏掉 DOI 事件。
				task.access.anchored = true;
				await browser.tabs.update(task.tabID, {url: task.doiURL});

				let candidate;
				while (this._taskCanPrepare(task)) {
					candidate = await this._waitForTranslator(task);
					if (!this._candidateCurrent(task, candidate)) continue;
					task.translatorReady = true;
					const reachable = await this._zoteroReachable();
					if (!this._candidateCurrent(task, candidate)) continue;
					if (!reachable) { this._finishTask(task, OUTCOME_FAILED); return; }
					await this._reportAccess(task);
					if (!this._candidateCurrent(task, candidate)) continue;
					let final;
					try { final = await this._inspectDocument(task); }
					catch (error) {
						if (!this._candidateCurrent(task, candidate)) continue;
						throw error;
					}
					if (!final || !this._candidateCurrent(task, candidate)
						|| final.documentID !== candidate.documentID || final.translatorID !== candidate.translatorID) continue;
					candidate = final;
					break;
				}
				if (!candidate || !this._candidateCurrent(task, candidate)) return;
				clearTimeout(task.accessTimer);
				task.access.closed = true;
				task.saveDocumentID = candidate.documentID;
				task.saveGeneration = candidate.generation;
				task.saveTranslatorID = candidate.translatorID;
				let savePromise;
				task.expiresAt = Date.now() + OBSERVATION_DEADLINE_MS;
				task.saveTriggered = true;
				try {
					savePromise = Zotero.Connector_Browser.saveWithTranslator(
						candidate.tab,
						0,
						{
							fallbackOnFailure: false,
							literatureMonitorAutomatic: true,
							literatureMonitorContext: Object.freeze({
								requestID: task.requestID, invocationID: task.invocationID, doiURL: task.doiURL,
								expiresAt: task.expiresAt,
								documentID: candidate.documentID, translatorID: candidate.translatorID,
							}),
						},
					);
				}
				catch (_) {
					this._finishTask(task, OUTCOME_UNCONFIRMED);
					return;
				}

				Promise.resolve(savePromise).catch(() => {
					if (this._activeTask === task && !task.terminalOutcome) {
						// The trigger crossed the content-script messaging boundary.
						// Without the explicit pre-parent failure hook, a rejected
						// promise is ambiguous rather than proof that no parent saved.
						this._finishTask(task, task.parentConfirmed ? OUTCOME_CONFIRMED : OUTCOME_UNCONFIRMED);
					}
				});

				task.observationTimer = setTimeout(() => {
					if (this._activeTask === task && !task.terminalOutcome) {
						this._finishTask(task, task.parentConfirmed ? OUTCOME_CONFIRMED : OUTCOME_UNCONFIRMED);
					}
				}, OBSERVATION_DEADLINE_MS);
			}
			catch (_) {
				if (!task.terminalOutcome) {
					this._finishTask(
						task,
						task.saveTriggered ? OUTCOME_UNCONFIRMED : OUTCOME_FAILED,
					);
				}
			}
		},

		_taskCanPrepare(task) {
			return this._activeTask === task && !task.terminalOutcome && !task.access.closed
				&& task.access.captureAllowed && task.access.now() < task.access.deadline;
		},

		async _navigateAccess(task) {
			if (!this._taskCanPrepare(task)) return;
			const generation = task.generation;
			const target = task.access.takeNavigation();
			if (!target) return;
			try {
				const tab = await browser.tabs.get(task.tabID);
				if (!this._taskCanPrepare(task) || generation !== task.generation || tab.incognito !== false) return;
				task.access.navigationIssued(target);
				await browser.tabs.update(task.tabID, {url: target});
			}
			catch (_) {
				if (this._taskCanPrepare(task)) {
					task.access.fallback("access_failed");
					this._finishTask(task, OUTCOME_FAILED);
				}
			}
		},

		async _reportAccess(task) {
			if (task.accessReported || task.tabID === null) return;
			// 仅送一次不可变、安全摘要，不发送路径、redirect 参数或浏览器会话数据。
			task.accessReported = true;
			try {
				await this._postJSON("/api/connector/access", {
					request_id: task.requestID, invocation_id: task.invocationID,
					doi_url: task.doiURL, tab_id: task.tabID, observation: task.access.snapshot(),
				});
			}
			catch (_) { /* Observation delivery never grants saving authority. */ }
		},

		_candidateCurrent(task, candidate) {
			return this._taskCanPrepare(task) && candidate.generation === task.generation
				&& !task.navigationPending && candidate.documentID === task.documentID;
		},

		async _inspectDocument(task) {
			const generation = task.generation;
			if (!this._taskCanPrepare(task) || task.navigationPending || !task.response?.complete
				|| task.access.reason === "preparing") return null;
			const tab = await browser.tabs.get(task.tabID);
			if (!this._taskCanPrepare(task) || generation !== task.generation || tab.status !== "complete") return null;
			if (tab.incognito !== false || tab.windowId !== task.windowID) throw new Error("Task tab identity changed");
			const frame = await browser.webNavigation.getFrame({tabId: task.tabID, frameId: 0});
			if (!this._taskCanPrepare(task) || generation !== task.generation) return null;
			if (!frame?.documentId || frame.errorOccurred || frame.documentLifecycle !== "active") {
				throw new Error("Current top document identity is unavailable");
			}
			if (frame.documentId !== task.documentID || !LiteratureMonitorAccess.publicOrigin(frame.url)) return null;
			const response = task.response;
			const priorDocumentID = await response.priorDocument;
			if (!this._taskCanPrepare(task) || generation !== task.generation
				|| priorDocumentID === undefined || priorDocumentID === frame.documentId) return null;
			if (response.documentID && response.documentID !== frame.documentId) return null;
			response.documentID = frame.documentId;
			if (response.challenge) task.challengedDocuments.add(frame.documentId);
			const results = await browser.scripting.executeScript({
				target: {tabId: task.tabID, documentIds: [frame.documentId]},
				world: "ISOLATED",
				func: inspectPage,
			});
			if (!this._taskCanPrepare(task) || generation !== task.generation || task.navigationPending) return null;
			const result = results.length === 1 && results[0];
			if (!result || result.frameId !== 0 || result.documentId !== frame.documentId) return null;
			const current = await browser.webNavigation.getFrame({tabId: task.tabID, frameId: 0});
			if (!this._taskCanPrepare(task) || generation !== task.generation
				|| current?.documentId !== frame.documentId || current.errorOccurred || current.documentLifecycle !== "active") return null;
			const latestTab = await browser.tabs.get(task.tabID);
			if (!this._taskCanPrepare(task) || generation !== task.generation || task.navigationPending
				|| latestTab.status !== "complete") return null;
			if (latestTab.incognito !== false || latestTab.windowId !== task.windowID) throw new Error("Task tab identity changed");
			const page = result.result;
			if (!page || !page.ready) return null;
			if (page.manual) { task.access.fallback("manual_challenge"); throw new Error("Manual challenge required"); }
			if (page.challenge) task.challengedDocuments.add(frame.documentId);
			// 上游 onPageLoad 没有检测开始时间，且允许迟到回调。同文档内标记消失或
			// Translator 数组替换不能证明重新检测；只等待新文档，否则在统一期限回退。
			if (task.challengedDocuments.has(frame.documentId) || !page.content) return null;
			if (!requestIDIsValid(page.translatorID) || !requestIDIsValid(page.itemType)) return null;
			if (["multiple", "webpage", "attachment"].includes(page.itemType)) throw new Error("Not an automatic single-item document");
			return {tab: latestTab, documentID: frame.documentId, generation, translatorID: page.translatorID};
		},

		_waitForTranslator(task) {
			return new Promise((resolve, reject) => {
				let finished = false;
				let checking = false;
				const finish = (fn, value) => {
					if (finished) return;
					finished = true;
					clearInterval(pollTimer);
					browser.tabs.onUpdated.removeListener(onUpdated);
					task.translatorCancel = null;
					fn(value);
				};
				const check = async () => {
					if (finished || checking || !this._taskCanPrepare(task)) return;
					checking = true;
					const generation = task.generation;
					try {
						const candidate = await this._inspectDocument(task);
						if (!finished && candidate && this._candidateCurrent(task, candidate)) finish(resolve, candidate);
					}
					catch (error) {
						if (this._taskCanPrepare(task) && generation === task.generation) finish(reject, error);
					}
					finally { checking = false; }
				};
				const onUpdated = tabID => { if (tabID === task.tabID) void check(); };
				browser.tabs.onUpdated.addListener(onUpdated);
				const pollTimer = setInterval(() => void check(), TRANSLATOR_POLL_INTERVAL_MS);
				// AccessJourney 的单一期限同时约束验证、Translator 和保存前异步检查。
				task.translatorCancel = () => finish(reject, new Error("Automatic capture ended before readiness"));
				void check();
			});
		},

		async _saveDocumentCurrent(task, inspect = true) {
			if (this._activeTask !== task || task.terminalOutcome || task.generation !== task.saveGeneration
				|| task.navigationPending || Date.now() >= task.expiresAt) return false;
			const tab = await browser.tabs.get(task.tabID);
			const frame = await browser.webNavigation.getFrame({tabId: task.tabID, frameId: 0});
			if (frame?.documentId !== task.saveDocumentID || task.terminalOutcome) return false;
			let page;
			if (inspect) {
				const results = await browser.scripting.executeScript({
					target: {tabId: task.tabID, documentIds: [task.saveDocumentID]}, world: "ISOLATED", func: inspectPage,
				});
				page = results.length === 1 && results[0].frameId === 0
					&& results[0].documentId === task.saveDocumentID && results[0].result;
			}
			const current = await browser.webNavigation.getFrame({tabId: task.tabID, frameId: 0});
			const latestTab = await browser.tabs.get(task.tabID);
			return this._activeTask === task && !task.terminalOutcome && task.generation === task.saveGeneration
				&& !task.navigationPending && Date.now() < task.expiresAt
				&& tab.incognito === false && tab.windowId === task.windowID
				&& latestTab.incognito === false && latestTab.windowId === task.windowID && latestTab.status === "complete"
				&& current?.documentId === task.saveDocumentID && !current.errorOccurred && current.documentLifecycle === "active"
				&& (!inspect || (page?.ready && page.content && !page.challenge && page.translatorID === task.saveTranslatorID));
		},

		async _handleRuntimeMessage(message, sender) {
			if (!isPlainObject(message) || !isPlainObject(message.context)) return false;
			const task = this._activeTask;
			const context = message.context;
			if (!task || !task.saveTriggered
					|| !sender || sender.id !== browser.runtime.id || !Number.isInteger(sender.frameId)
					|| sender.frameId !== 0 || sender.documentId !== task.saveDocumentID
					|| context.documentID !== task.saveDocumentID || context.translatorID !== task.saveTranslatorID
					|| !sender.tab || sender.tab.id !== task.tabID
					|| context.requestID !== task.requestID
					|| context.invocationID !== task.invocationID
					|| context.doiURL !== task.doiURL || context.expiresAt !== task.expiresAt) return false;
			if (task.terminalOutcome) {
				// A timeout result is only an observation. Keep the original grant
				// identifiable until this exact native pipeline actually completes.
				if (task.dispatchAccepted && !task.pipelineEnded
						&& [INTERNAL_PIPELINE_COMPLETE, INTERNAL_SAVE_FAILED].includes(message.type)
						&& (message.type === INTERNAL_SAVE_FAILED && message.stage === "native_save"
							|| message.sessionID === task.sessionID)
						&& sender.frameId === task.senderFrame && sender.documentId === task.senderDocument) {
					// An upstream error ends browser task ownership, but supplies
					// no proof that the external native save has stopped.
					task.pipelineEnded = true;
					task.pipelineComplete = message.type === INTERNAL_PIPELINE_COMPLETE;
					if (task.pipelineComplete) await this._reportNativeSaveSettled(task);
					await this._returnToApp(task, task.resultAccepted === true && task.pipelineComplete);
					if (this._activeTask === task) this._activeTask = null;
				}
				return false;
			}
			if (Date.now() >= task.expiresAt
					&& !(task.dispatchAccepted && message.type === INTERNAL_PIPELINE_COMPLETE)) {
				// An expired dispatch cannot gain parent authority, but a verified
				// pipeline-end callback still proves the original grant has settled.
				this._finishTask(task, task.parentConfirmed ? OUTCOME_CONFIRMED : OUTCOME_UNCONFIRMED);
				return false;
			}

			if (message.type === "literature-monitor-before-parent-save") {
				if (task.dispatchRequested || !requestIDIsValid(message.sessionID)) return false;
				// Consume before awaiting: two frames or duplicate translations cannot both save.
				task.dispatchRequested = true;
				task.sessionID = message.sessionID;
				task.senderFrame = sender.frameId;
				task.senderDocument = sender.documentId;
				try {
					if (!await this._saveDocumentCurrent(task)) return false;
					const response = await this._postJSON("/api/connector/dispatch", this._invocationPayload(task));
					task.dispatchAccepted = response.ok && response.body?.accepted === true;
					return this._activeTask === task && !task.terminalOutcome
						&& task.dispatchAccepted && await this._saveDocumentCurrent(task);
				}
				catch (_) { return false; }
			}
			if (message.type === INTERNAL_PARENT_SAVED) {
				if (!task.dispatchAccepted || message.sessionID !== task.sessionID
						|| sender.frameId !== task.senderFrame
						|| sender.documentId !== task.senderDocument) return false;
				// 原生接受确认只核对保存归属；后续页面内容变化不能改写父条目完成标准。
				if (!await this._saveDocumentCurrent(task, false)) return false;
				task.parentConfirmed = true;
				// Keep the task owned until the upstream attachment pipeline settles.
				if (task.pipelineComplete) this._finishTask(task, OUTCOME_CONFIRMED);
			}
			else if (message.type === INTERNAL_PIPELINE_COMPLETE) {
				if (!task.dispatchAccepted || message.sessionID !== task.sessionID
						|| sender.frameId !== task.senderFrame
						|| sender.documentId !== task.senderDocument || task.pipelineEnded) return false;
				task.pipelineComplete = true;
				task.pipelineEnded = true;
				// The original upstream translateAndSave promise has resolved,
				// including downstream attachment work. This is the only native
				// completion proof that can retire the external Reset grant.
				await this._reportNativeSaveSettled(task);
				if (task.parentConfirmed) this._finishTask(task, OUTCOME_CONFIRMED);
			}
			else if (message.type === INTERNAL_SAVE_FAILED) {
				task.pipelineEnded = true;
				// Native rejection after a granted dispatch may still have side effects.
				// The source reports its phase; the coordinator verifies the actual grant.
				this._finishTask(task,
					task.parentConfirmed ? OUTCOME_CONFIRMED
						: task.dispatchRequested ? OUTCOME_UNCONFIRMED : OUTCOME_FAILED,
					task.parentConfirmed ? null : task.dispatchAccepted ? "native_save" :
						task.dispatchRequested ? "missing_confirmation" : "translator");
			}
			return false;
		},

		_invocationPayload(task) {
			return {
				request_id: task.requestID, invocation_id: task.invocationID,
				doi_url: task.doiURL, tab_id: task.tabID, session_id: task.sessionID,
			};
		},

		_handleTaskTabRemoved(tabID) {
			const task = this._activeTask;
			if (!task
					|| task.terminalOutcome
					|| task.tabID !== tabID) {
				return;
			}
			this._finishTask(
				task,
				task.parentConfirmed ? OUTCOME_CONFIRMED
					: task.saveTriggered ? OUTCOME_UNCONFIRMED : OUTCOME_FAILED,
			);
		},

		_finishTask(task, outcome, failureStage = null) {
			if (task.terminalOutcome) {
				return;
			}
			task.manualAccessPending = task.access.reason === "preparing"
				|| task.challengedDocuments.has(task.documentID);
			if (!task.saveTriggered && task.access.reason === "preparing") {
				task.access.fallback("access_failed");
			}
			task.terminalOutcome = outcome;
			task.failureStage = failureStage;
			clearTimeout(task.accessTimer);
			task.access.closed = true;
			const translatorCancel = task.translatorCancel;
			task.translatorCancel = null;
			if (translatorCancel) {
				translatorCancel();
			}
			if (task.observationTimer) {
				clearTimeout(task.observationTimer);
				task.observationTimer = null;
			}

			void this._deliverResult(task)
				.then(async acknowledged => {
					task.resultAccepted = acknowledged;
					await this._returnToApp(task, acknowledged && outcome === OUTCOME_CONFIRMED && task.pipelineComplete);
					if (this._activeTask === task
							&& (!task.dispatchAccepted || task.pipelineEnded
								|| task.failureStage === "native_save")) {
						this._activeTask = null;
					}
				});
		},

		_isAppTab(tab) {
			if (!tab || tab.incognito !== false || !Number.isInteger(tab.id)) return false;
			try { return new URL(tab.url).origin === BRIDGE_BASE; }
			catch (_) { return false; }
		},

		async _returnToApp(task, closeTaskTab) {
			if (closeTaskTab && Number.isInteger(task.tabID)) {
				try {
					const tab = await browser.tabs.get(task.tabID);
					const frame = await browser.webNavigation.getFrame({tabId: task.tabID, frameId: 0});
					if (tab.incognito === false && tab.windowId === task.windowID
							&& frame?.documentId === task.saveDocumentID) {
						await browser.tabs.remove(task.tabID);
					}
				}
				catch (_) { /* Task tab changed or was already closed. */ }
			}
			if (!Number.isInteger(task.appTabID) || task.manualAccessPending) return;
			try {
				const app = await browser.tabs.get(task.appTabID);
				if (!this._isAppTab(app) || app.windowId !== task.appWindowID) return;
				await browser.windows.update(task.appWindowID, {focused: true});
				await browser.tabs.update(task.appTabID, {active: true});
			}
			catch (_) { /* Original application tab may have been closed. */ }
		},

		async _reportNativeSaveSettled(task) {
			// A rejected/lost receipt never clears server custody by inference.
			for (let attempt = 0; attempt < RESULT_ATTEMPTS; attempt++) {
				try {
					const response = await this._postJSON(
						"/api/connector/native-save-settled", this._invocationPayload(task));
					if (response.ok && response.body?.accepted === true) return true;
					if (response.status === 409) return false;
				}
				catch (_) { /* Do not fabricate completion on transport loss. */ }
				if (attempt + 1 < RESULT_ATTEMPTS) await delay(RESULT_RETRY_DELAY_MS);
			}
			return false;
		},

		async _deliverResult(task) {
			await this._reportAccess(task);
			const payload = {
				...this._invocationPayload(task),
				outcome: task.terminalOutcome,
				// Native progress includes links/Snapshots; it is not actual PDF evidence.
				pdf_outcome: "unverified",
				...(task.failureStage ? {failure_stage: task.failureStage} : {}),
			};

			for (let attempt = 0; attempt < RESULT_ATTEMPTS; attempt++) {
				try {
					const response = await this._postJSON(
						"/api/connector/result",
						payload,
					);
					if (response.ok && response.body?.accepted === true) return true;
					if (response.status === 409) return false;
				}
				catch (_) {
					// Retry only this already-terminal result; never repeat the save.
				}
				if (attempt + 1 < RESULT_ATTEMPTS) {
					await delay(RESULT_RETRY_DELAY_MS);
				}
			}
			return false;
		},
	};

	global.LiteratureMonitorConnectorRuntime = Runtime;
	Runtime.install();
})(self);
