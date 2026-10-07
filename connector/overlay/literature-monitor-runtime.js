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
	const TRANSLATOR_POLL_INTERVAL_MS = 250;
	const TRANSLATOR_DEADLINE_MS = 20000;
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
		if (keys.length !== 2
				|| keys[0] !== "doi_url"
				|| keys[1] !== "request_id") {
			return { requestID, command: null };
		}

		const doiURL = validateDOIURL(command.doi_url);
		if (!requestID || !doiURL) {
			return { requestID, command: null };
		}

		return {
			requestID,
			command: {
				requestID,
				doiURL,
			},
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
				this._handleRuntimeMessage(message, sender);
			};
			browser.runtime.onMessage.addListener(this._runtimeMessageListener);

			this._tabRemovedListener = tabID => {
				this._handleTaskTabRemoved(tabID);
			};
			browser.tabs.onRemoved.addListener(this._tabRemovedListener);

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
				if (!parsed.command) {
					if (parsed.requestID) {
						const task = {
							requestID: parsed.requestID,
							doiURL: null,
							tabID: null,
							saveTriggered: false,
							parentConfirmed: false,
							terminalOutcome: null,
							observationTimer: null,
							translatorCancel: null,
						};
						this._activeTask = task;
						this._finishTask(task, OUTCOME_FAILED);
					}
					return;
				}

				const task = {
					requestID: parsed.command.requestID,
					doiURL: parsed.command.doiURL,
					tabID: null,
					saveTriggered: false,
					parentConfirmed: false,
					terminalOutcome: null,
					observationTimer: null,
					translatorCancel: null,
				};
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
				const normalWindow = windows.find(window => window.type === "normal"
					&& window.incognito === false && Number.isInteger(window.id));
				if (!normalWindow) {
					throw new Error("No normal non-incognito Chrome window is available");
				}
				const tab = await browser.tabs.create({
					url: task.doiURL,
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
				if (task.terminalOutcome) {
					return;
				}
				task.tabID = tab.id;

				const readyTab = await this._waitForTranslator(task);
				if (task.terminalOutcome) {
					return;
				}

				if (!await this._zoteroReachable()) {
					this._finishTask(task, OUTCOME_FAILED);
					return;
				}

				let savePromise;
				try {
					savePromise = Zotero.Connector_Browser.saveWithTranslator(
						readyTab,
						0,
						{
							fallbackOnFailure: false,
							literatureMonitorAutomatic: true,
						},
					);
					task.saveTriggered = true;
				}
				catch (_) {
					this._finishTask(task, OUTCOME_FAILED);
					return;
				}

				Promise.resolve(savePromise).catch(() => {
					if (this._activeTask === task
							&& !task.parentConfirmed
							&& !task.terminalOutcome) {
						// The trigger crossed the content-script messaging boundary.
						// Without the explicit pre-parent failure hook, a rejected
						// promise is ambiguous rather than proof that no parent saved.
						this._finishTask(task, OUTCOME_UNCONFIRMED);
					}
				});

				task.observationTimer = setTimeout(() => {
					if (this._activeTask === task && !task.terminalOutcome) {
						this._finishTask(task, OUTCOME_UNCONFIRMED);
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

		_waitForTranslator(task) {
			return new Promise((resolve, reject) => {
				let finished = false;
				let checking = false;

				const cleanup = () => {
					clearInterval(pollTimer);
					clearTimeout(deadlineTimer);
					browser.tabs.onUpdated.removeListener(onUpdated);
					task.translatorCancel = null;
				};
				const finish = (fn, value) => {
					if (finished) {
						return;
					}
					finished = true;
					cleanup();
					fn(value);
				};
				const check = async () => {
					if (finished || checking || task.terminalOutcome) {
						return;
					}
					checking = true;
					try {
						const tab = await browser.tabs.get(task.tabID);
						if (tab.status !== "complete") {
							return;
						}
						const tabInfo = Zotero.Connector_Browser.getTabInfo(task.tabID);
						const translators = tabInfo && tabInfo.translators;
						if (!Array.isArray(translators) || !translators.length) {
							return;
						}

						const translator = translators[0];
						if (!translator
								|| typeof translator.translatorID !== "string"
								|| !translator.translatorID
								|| typeof translator.itemType !== "string"
								|| !translator.itemType) {
							return;
						}
						if (translator.itemType === "multiple") {
							finish(
								reject,
								new Error(
									"Automatic capture will not select from a multiple translator",
								),
							);
							return;
						}
						finish(resolve, tab);
					}
					catch (error) {
						if (!task.terminalOutcome) {
							finish(reject, error);
						}
					}
					finally {
						checking = false;
					}
				};
				const onUpdated = tabID => {
					if (tabID === task.tabID) {
						void check();
					}
				};

				browser.tabs.onUpdated.addListener(onUpdated);
				const pollTimer = setInterval(
					() => void check(),
					TRANSLATOR_POLL_INTERVAL_MS,
				);
				const deadlineTimer = setTimeout(
					() => finish(
						reject,
						new Error("Timed out waiting for a usable single-item translator"),
					),
					TRANSLATOR_DEADLINE_MS,
				);
				task.translatorCancel = () => finish(
					reject,
					new Error("Automatic capture ended before translator readiness"),
				);
				void check();
			});
		},

		_handleRuntimeMessage(message, sender) {
			if (!isPlainObject(message)) {
				return;
			}
			const task = this._activeTask;
			if (!task
					|| task.terminalOutcome
					|| !task.saveTriggered
					|| !sender
					|| !sender.tab
					|| sender.tab.id !== task.tabID) {
				return;
			}

			if (message.type === INTERNAL_PARENT_SAVED) {
				task.parentConfirmed = true;
				this._finishTask(task, OUTCOME_CONFIRMED);
			}
			else if (message.type === INTERNAL_SAVE_FAILED
					&& !task.parentConfirmed) {
				this._finishTask(task, OUTCOME_FAILED);
			}
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
				task.saveTriggered
					? OUTCOME_UNCONFIRMED
					: OUTCOME_FAILED,
			);
		},

		_finishTask(task, outcome) {
			if (task.terminalOutcome) {
				return;
			}
			task.terminalOutcome = outcome;
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
				.finally(() => {
					if (this._activeTask === task) {
						this._activeTask = null;
					}
				});
		},

		async _deliverResult(task) {
			const payload = {
				request_id: task.requestID,
				outcome: task.terminalOutcome,
			};

			for (let attempt = 0; attempt < RESULT_ATTEMPTS; attempt++) {
				try {
					const response = await this._postJSON(
						"/api/connector/result",
						payload,
					);
					if (response.status === 409 || response.ok) {
						return;
					}
				}
				catch (_) {
					// Retry only this already-terminal result; never repeat the save.
				}
				if (attempt + 1 < RESULT_ATTEMPTS) {
					await delay(RESULT_RETRY_DELAY_MS);
				}
			}
		},
	};

	global.LiteratureMonitorConnectorRuntime = Runtime;
	Runtime.install();
})(self);
