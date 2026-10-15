/*
 * Literature Monitor CARSI event attribution primitives (SPEC §44.6–44.7, §§61–62).
 * AGPLv3-or-later, © 2026 Literature Monitor contributors.
 *
 * Official resource-list and detail selectors supplied from normal Chrome DevTools.
 */
(function(global) {
	"use strict";

	const PORTAL_URL = "https://ds.carsi.edu.cn/wxds";
	const RESOURCE_URL = "https://ds.carsi.edu.cn/resource/resource.php";
	const PAGE_WAIT_MS = 15000;

	function officialURL(url, detail = false) {
		try {
			const parsed = new URL(url);
			return parsed.protocol === "https:" && parsed.host === "ds.carsi.edu.cn"
				&& !parsed.username && !parsed.password && (detail
					? parsed.pathname === "/resource/resourceDetail.php"
					: parsed.pathname === "/resource/resource.php");
		}
		catch (_) {return false;}
	}

	function officialPortal(tab, frame) {
		if (!tab || tab.incognito !== false || !Number.isInteger(tab.id)
				|| !Number.isInteger(tab.windowId) || !frame || frame.frameId !== 0
				|| frame.errorOccurred || frame.documentLifecycle !== "active"
				|| typeof frame.documentId !== "string" || !frame.documentId) return false;
		try {
			return officialURL(tab.url) && officialURL(frame.url);
		}
		catch (_) { return false; }
	}

	function exactNameMatches(nodes, name) {
		if (!Array.isArray(nodes) || typeof name !== "string" || !name.length) return [];
		return nodes.filter(node => node && typeof node.textContent === "string"
			&& node.textContent === name);
	}

	// Injected only into a verified official top document.
	function resourcePageAction(action, name, query = name) {
		if (location.origin !== "https://ds.carsi.edu.cn"
				|| location.pathname !== "/resource/resource.php") return {status: "unverified"};
		const list = document.querySelector("div.con.text.act #padInfo.list");
		const school = document.querySelector('input[type="checkbox"]#onlyMySchool[name="onlyMySchool"]');
		const inputs = [...document.querySelectorAll("input#searchKey.searchInput")];
		const buttons = [...document.querySelectorAll("button.Btn1.searBtn")];
		if (!list || !school || inputs.length !== 1 || buttons.length !== 1)
			return {status: "unverified"};
		if (typeof school.checked !== "boolean") return {status: "unverified"};
		if (school.checked) {
			if (action === "disable") {school.click(); return {status: "disabled"};}
			return {status: "school_filter_on"};
		}
		if (action === "scan") return {status: "ready"};
		const cards = [...list.querySelectorAll(".item a")];
		const titleText = anchor => {
			const title = anchor.querySelector(".flex .t1");
			return title ? [...title.childNodes].filter(node => node.nodeType === 3)
				.map(node => node.textContent).join("").trim() : "";
		};
		if (action === "search") {
			// 查询词只用于搜索；完整 Service Name 始终是匹配身份。
			inputs[0].value = query;
			buttons[0].click();
			return {status: "submitted"};
		}
		if (inputs[0].value !== query) return {status: "unverified"};
		const matches = cards.filter(anchor => titleText(anchor) === name);
		if (matches.length > 1) return {status: "ambiguous"};
		if (matches.length === 1) {
			// 资源名相同不能授权任意跳转。
			let target;
			try {
				const anchor = matches[0];
				if (!anchor.getAttribute("href")) return {status: "unverified"};
				target = new URL(anchor.href);
				if (target.origin !== "https://ds.carsi.edu.cn"
						|| target.pathname !== "/resource/resourceDetail.php"
						|| target.username || target.password || target.hash) return {status: "unverified"};
			}
			catch (_) {return {status: "unverified"};}
			return action === "select" ? {status: "selected", url: target.href} : {status: "found"};
		}
		return {status: "missing"};
	}

	function detailPageAction(action) {
		if (location.origin !== "https://ds.carsi.edu.cn"
				|| location.pathname !== "/resource/resourceDetail.php") return {status: "unverified"};
		const controls = [...document.querySelectorAll(".moreBox a.earth_more")]
			.filter(link => link.querySelector("span.txt")?.textContent.trim() === "访问资源"
				&& /^javascript:gotoResource\(.+\)\s*;?$/.test(link.getAttribute("href") || ""));
		if (controls.length !== 1) return {status: "unverified"};
		if (action === "visit") controls[0].click();
		return {status: action === "visit" ? "visited_click" : "ready"};
	}

	// Runs only in the extension's isolated world of the bound IdP document.
	function attributeConsentAction(action, origin) {
		if (location.origin !== origin) return {status: "unverified"};
		const marker = "__literatureMonitorAttributeConsentAttempted";
		if (globalThis[marker]) return {status: "attempted"};
		const controls = () => {
			const choices = [...document.querySelectorAll("#_shib_idp_globalConsent")];
			const buttons = [...document.querySelectorAll('input[name="_eventId_proceed"]')];
			if (choices.length !== 1 || buttons.length !== 1) return null;
			const choice = choices[0], proceed = buttons[0], form = choice.form;
			const usable = node => node.isConnected && !node.matches(":disabled")
				&& node.getClientRects().length > 0
				&& !["hidden", "collapse"].includes(getComputedStyle(node).visibility);
			if (choice.tagName !== "INPUT" || !["radio", "checkbox"].includes(choice.type)
					|| proceed.type !== "submit" || !usable(choice) || !usable(proceed)
					|| !form || form !== proceed.form) return null;
			const method = proceed.getAttribute("formmethod") || form.method;
			const target = proceed.getAttribute("formtarget") || form.target;
			if (method?.toLowerCase() !== "post" || (target && target !== "_self")) return null;
			try {
				const url = new URL(proceed.getAttribute("formaction") || form.getAttribute("action") || location.href, location.href);
				if (url.origin !== origin || url.username || url.password) return null;
			}
			catch (_) {return null;}
			return {choice, proceed};
		};
		const found = controls();
		if (!found) return {status: "unverified"};
		if (action === "scan") return {status: "ready"};
		if (action !== "submit") return {status: "unverified"};
		// Consume before either native click; navigation or a failed receipt cannot retry.
		globalThis[marker] = true;
		if (!found.choice.checked) found.choice.click();
		const current = controls();
		if (!current || current.choice !== found.choice || current.proceed !== found.proceed
				|| found.choice.checked !== true) return {status: "not_selected"};
		found.proceed.click();
		return {status: "submitted"};
	}

	class VisitObservation {
		constructor(webNavigation, {detailTabID, current,
				setTimer = (callback, delay) => globalThis.setTimeout(callback, delay),
				clearTimer = handle => globalThis.clearTimeout(handle),
				maxWait = 10000, requireDispatchArm = false, diagnose = () => {}}) {
			this.nav = webNavigation;
			this.detailTabID = detailTabID;
			this.current = current;
			this.setTimer = setTimer;
			this.clearTimer = clearTimer;
			this.maxWait = maxWait;
			this.diagnose = diagnose;
			this.done = false;
			this.armed = !requireDispatchArm;
			this.createdTabID = null;
			this.selectedTabID = null;
			this.clicked = false;
			this.handlers = [];
		}

		_finish(reason) {
			if (this.done) return;
			this.done = true;
			this.clearTimer(this.timer);
			for (const [event, handler] of this.handlers) event.removeListener(handler);
			this.handlers = [];
			const evidence = reason === "opened" ? {
				visit_action_observed: true,
				source_tab_id: this.detailTabID, target_tab_id: this.selectedTabID,
				created_target_tab_id: this.createdTabID, navigation_observed: true,
			} : null;
			this.resolve({reason, evidence});
		}

		_observe(tabID) {
			if (this.done || !this.armed || this.current() !== true) return;
			if (this.selectedTabID !== null && this.selectedTabID !== tabID) {
				this._finish("ambiguous_target");
				return;
			}
			this.selectedTabID = tabID;
			this.diagnose("navigation_observed");
			if (this.clicked) this._finish("opened");
		}

		cancel() {
			this._finish("revoked");
		}

		async run(click) {
			if (this.done || this.started || this.current() !== true
					|| !Number.isInteger(this.detailTabID)
					|| this.maxWait <= 0 || this.maxWait > 10000)
				return {reason: "invalid_operation", evidence: null};
			this.started = true;
			const onCreated = event => {
				if (this.done || !this.armed || event.sourceTabId !== this.detailTabID) return;
				if (event.sourceFrameId !== 0 || !Number.isInteger(event.tabId)
						|| event.tabId === this.detailTabID || this.createdTabID !== null
						|| this.selectedTabID === this.detailTabID) {
					this._finish("ambiguous_target");
					return;
				}
				this.createdTabID = event.tabId;
				this._observe(event.tabId);
			};
			const onNavigation = event => {
				if (event.frameId !== 0) return;
				if (event.tabId === this.detailTabID && this.createdTabID === null)
					this._observe(event.tabId);
				else if (event.tabId === this.createdTabID) this._observe(event.tabId);
			};
			const listeners = [
				[this.nav?.onCreatedNavigationTarget, onCreated],
				[this.nav?.onBeforeNavigate, onNavigation],
				[this.nav?.onCommitted, onNavigation],
			];
			if (listeners.some(([event]) => !event?.addListener || !event?.removeListener))
				return {reason: "listener_unavailable", evidence: null};
			const result = new Promise(resolve => {this.resolve = resolve;});
			try {
				for (const [event, handler] of listeners) {
					this.handlers.push([event, handler]);
					event.addListener(handler);
				}
				this.timer = this.setTimer(() => this._finish("timeout"), this.maxWait);
			} catch (_) {
				this._finish("listener_registration_failed");
				return result;
			}
			// 官方点击回执和首次可归属导航均必需；之后不等待外站重定向。
			Promise.resolve().then(click).then(clicked => {
				if (this.done) return;
				if (this.current() !== true) {this._finish("revoked"); return;}
				if (clicked !== true) {this._finish("visit_not_invoked"); return;}
				this.clicked = true;
				if (this.selectedTabID !== null) this._finish("opened");
			}, () => this._finish("visit_action_failed"));
			return result;
		}
	}

	class Executor {
		constructor(browserAPI, post, command, context, live) {
			this.browser = browserAPI;
			this.post = post;
			this.command = command;
			this.context = context;
			this.live = () => live() && !this.revoked;
			this.identity = {
				instance_id: command.instance_id, task_id: command.task_id,
				generation: command.generation, request_id: command.request_id,
				service_id: command.service_id,
			};
			this.detailDocumentID = null;
			// CARSI 搜索词不是 Service 身份；只对 & 名称取最后一段作为查询词。
			const name = typeof command.name === "string" ? command.name : "";
			const ampersand = name.lastIndexOf("&");
			this.searchQuery = ampersand === -1 ? name : name.slice(ampersand + 1).trim();
			this.visitObserver = null;
			this.visitClick = null;
			this.revoked = false;
			this.detailAttributionInvalid = false;
			this.visitBrowserError = false;
			this.waitingReported = false;
			this.traceCount = 0;
			this.consentDocuments = new Set();
			this.consentBusy = false;
			this.consentOrigin = null;
			if (command.automatic_attribute_release_consent === true) {
				try {
					const login = new URL(command.login_url);
					const entity = new URL(login.searchParams.get("entityID"));
					if (login.origin === "https://ds.carsi.edu.cn"
							&& login.pathname === "/Shibboleth.sso/Login"
							&& entity.protocol === "https:" && !entity.username && !entity.password)
						this.consentOrigin = entity.origin;
				}
				catch (_) { /* URN and unconfirmed origins remain manual. */ }
			}
		}

		trace(stage) {
			// 只记录固定阶段，不记录外部网址、凭证或导航历史。
			if (this.traceCount++ < 64) console.info("Literature Monitor CARSI", this.command.generation, stage);
		}

		async authorized(stage = null) {
			const finish = (reason, accepted = false) => {
				if (stage) this.trace(stage + "_" + reason);
				return accepted;
			};
			if (!this.live()) return finish("inactive");
			if (this.revoked) return finish("revoked");
			try {
				const answer = await this.post("/api/connector/preparation/authorize", this.identity);
				if (!this.live()) return finish("inactive");
				if (answer.status === 409 || answer.body?.accepted === false) {
					this.revoked = true;
					return finish("rejected");
				}
				if (!answer.ok) return finish([408, 504].includes(answer.status) ? "timeout" : "http_error");
				if (answer.body?.accepted !== true) return finish("invalid_response");
				return finish("ok", true);
			}
			catch (error) {
				return finish(["AbortError", "TimeoutError"].includes(error?.name)
					? "timeout" : "network_error");
			}
		}

		async reportAuthenticationWait(waiting) {
			if (this.waitingReported === waiting || !await this.authorized()) return;
			try {
				const result = await this.post("/api/connector/preparation/waiting", {
					...this.identity, waiting,
				});
				if (this.live() && result.ok && result.body?.accepted === true)
					this.waitingReported = waiting;
			}
			catch (_) { /* Progress is not browser-action or Visit authority. */ }
		}

		async inspect(tabID, detail = false) {
			if (!this.live()) return null;
			try {
				const tab = await this.browser.tabs.get(tabID);
				if (!this.live() || !tab || tab.incognito !== false
						|| tab.windowId !== this.context.windowID
						|| tab.status !== "complete" || !officialURL(tab.url, detail)) return null;
				const frame = await this.browser.webNavigation.getFrame({tabId: tabID, frameId: 0});
				if (!this.live() || !frame || frame.errorOccurred
						|| frame.documentLifecycle !== "active" || !frame.documentId
						|| !officialURL(frame.url, detail)) return null;
				return {tab, frame};
			}
			catch (_) {return null;}
		}

		async consentDocument() {
			if (!this.live() || this.revoked || !this.consentOrigin) return null;
			try {
				const tab = await this.browser.tabs.get(this.context.portalTabID);
				if (!this.live() || this.revoked || !tab || tab.incognito !== false
						|| tab.windowId !== this.context.windowID || tab.status !== "complete"
						|| new URL(tab.url).origin !== this.consentOrigin) return null;
				const frame = await this.browser.webNavigation.getFrame({tabId: tab.id, frameId: 0});
				if (!this.live() || this.revoked || !frame || frame.errorOccurred
						|| frame.documentLifecycle !== "active" || !frame.documentId
						|| new URL(frame.url).origin !== this.consentOrigin) return null;
				return {tab, frame};
			}
			catch (_) {return null;}
		}

		async consent() {
			if (!this.consentOrigin || this.consentBusy || !this.live() || this.revoked) return false;
			this.consentBusy = true;
			try {
				const before = await this.consentDocument();
				if (!before || this.consentDocuments.has(before.frame.documentId) || !await this.authorized()) return false;
				const inject = action => this.browser.scripting.executeScript({
					target: {tabId: before.tab.id, documentIds: [before.frame.documentId]},
					world: "ISOLATED", func: attributeConsentAction, args: [action, this.consentOrigin],
				});
				if (!this.live() || this.revoked) return false;
				const scanned = await inject("scan");
				if (scanned?.length !== 1 || scanned[0].frameId !== 0
						|| scanned[0].documentId !== before.frame.documentId
						|| scanned[0].result?.status !== "ready" || !await this.authorized()) return false;
				const current = await this.consentDocument();
				if (!current || current.frame.documentId !== before.frame.documentId || !this.live() || this.revoked) return false;
				this.consentDocuments.add(before.frame.documentId);
				const result = await inject("submit");
				// Do not inspect a redirect or infer authentication from this click.
				return result?.length === 1 && result[0].frameId === 0
					&& result[0].documentId === before.frame.documentId && result[0].result?.status === "submitted";
			}
			catch (_) {return false;}
			finally {this.consentBusy = false;}
		}

		async script(tabID, detail, action) {
			const dispatchVisit = detail && action === "visit";
			if (!await this.authorized(dispatchVisit ? "visit_initial_authorize" : null)) return null;
			const before = await this.inspect(tabID, detail);
			if (!before || !this.live()) {
				if (dispatchVisit) this.trace("visit_document_unverified");
				return null;
			}
			if (dispatchVisit) this.trace("visit_document_verified");
			// Tab/frame inspection crosses async browser APIs. Re-check this exact
			// Service grant after it completes, immediately before dispatching JS.
			if (!await this.authorized(dispatchVisit ? "visit_final_authorize" : null)
					|| !this.live() || this.revoked) return null;
			if (dispatchVisit && !this.visitDispatchOpen) {
				this.trace("visit_dispatch_closed");
				return null;
			}
			try {
				if (dispatchVisit) {
					// Ignore stale navigations before the verified official click dispatch.
					if (this.visitObserver) this.visitObserver.armed = true;
					this.trace("visit_dispatch_started");
				}
				const results = await this.browser.scripting.executeScript({
					target: {tabId: tabID, documentIds: [before.frame.documentId]},
					// MV3 ISOLATED CSP can reject clicks on javascript: anchors.
					// Only the verified official Detail Visit runs in the page's world.
					world: dispatchVisit ? "MAIN" : "ISOLATED",
					func: detail ? detailPageAction : resourcePageAction,
					args: detail ? [action] : [action, this.command.name, this.searchQuery],
				});
				if (!this.live() || results?.length !== 1 || results[0].frameId !== 0
						|| results[0].documentId !== before.frame.documentId) {
					if (dispatchVisit) this.trace("visit_script_receipt_unverified");
					return null;
				}
				// Visit is allowed to replace its own detail document. The click
				// receipt alone is not Opened; VisitObservation still requires
				// an attributable target opening.
				if (dispatchVisit) {
					if (results[0].result?.status === "visited_click") {
						this.trace("click_receipt_received");
					}
					return results[0].result;
				}
				const after = await this.inspect(tabID, detail);
				if (!after || after.frame.documentId !== before.frame.documentId
						|| !this.live()) return null;
				if (!detail && action === "select")
					return {...results[0].result, documentID: before.frame.documentId};
				return results[0].result;
			}
			catch (error) {
				if (dispatchVisit) this.visitBrowserError = true;
				// Chrome may reject an isolated javascript: click via CSP. Never
				// record raw errors, which could contain authentication URLs.
				if (dispatchVisit) {
					this.trace(/Content Security Policy|\bCSP\b|Refused to run a JavaScript URL/i
						.test(String(error?.message || "")) ? "visit_script_rejected_csp"
						: "visit_script_rejected");
				}
				return null;
			}
			finally {if (dispatchVisit) this.trace("visit_dispatch_finished");}
		}

		async portal() {
			let loginOpened = false;
			if (this.context.portalTabID === null) {
				if (!await this.authorized()) return false;
				let tab;
				try {
					const window = await this.browser.windows.create({
						url: "about:blank", type: "normal", incognito: false, focused: false,
					});
					if (!window || window.type !== "normal" || window.incognito !== false || !Number.isInteger(window.id)) return false;
					this.context.windowID = window.id;
					const tabs = window.tabs || await this.browser.tabs.query({windowId: window.id});
					tab = tabs.find(item => Number.isInteger(item.id) && item.incognito === false && item.windowId === window.id);
					if (tab && Number.isInteger(tab.id)
							&& tab.incognito === false && tab.windowId === window.id)
						this.context.portalTabID = tab.id;
				}
				catch (_) {
					// 无法取得新建 Tab ID 时，不能推断 create 从未生效。
					return false;
				}
				if (!tab || tab.incognito !== false || tab.windowId !== this.context.windowID
						|| !Number.isInteger(tab.id)) {
					return false;
				}
				if (!this.live()) return false;
				if (!await this.authorized() || !this.live() || this.revoked) return false;
				await this.browser.tabs.update(tab.id, {url: this.command.login_url});
				if (!this.live()) return false;
				loginOpened = true;
				await this.reportAuthenticationWait(true);
			}
			const until = Date.now() + 480000;
			while (this.live() && Date.now() < until) {
				if (!await this.authorized()) return false;
				if (await this.inspect(this.context.portalTabID)) {
					const state = await this.script(this.context.portalTabID, false, "scan");
					if (state && state.status !== "unverified") {
						await this.reportAuthenticationWait(false);
						return true;
					}
				}
				// After manual login /wxds is the documented return; navigate only
				// the already-owned tab to the verified official resource list.
				const tab = await this.browser.tabs.get(this.context.portalTabID).catch(() => null);
				if (!this.live() || !tab || tab.incognito !== false
						|| tab.windowId !== this.context.windowID) return false;
				if (tab.url === PORTAL_URL) {
					if (!await this.authorized() || !this.live() || this.revoked) return false;
					await this.browser.tabs.update(tab.id, {url: RESOURCE_URL});
				}
				await this.consent();
				// Report login waiting only after this task invoked the verified
				// CARSI IdP entry. An unexpected external URL is not IdP evidence.
				if (loginOpened) await this.reportAuthenticationWait(true);
				await new Promise(resolve => setTimeout(resolve, 1500));
			}
			return false;
		}

		async loadedTarget() {
			if (!this.searchQuery) return "manual_action_required";
			const tabID = this.context.portalTabID;
			const state = await this.script(tabID, false, "scan");
			if (state?.status === "school_filter_on") {
				if ((await this.script(tabID, false, "disable"))?.status !== "disabled")
					return "unverified_official_page";
				await new Promise(resolve => setTimeout(resolve, 700));
				if ((await this.script(tabID, false, "scan"))?.status !== "ready")
					return "unverified_official_page";
			}
			else if (state?.status !== "ready") return "unverified_official_page";
			if (!this.live()) return "manual_action_required";
			const submitted = await this.script(tabID, false, "search");
			if (submitted?.status !== "submitted") return "unverified_official_page";
			const until = Date.now() + PAGE_WAIT_MS;
			while (this.live() && Date.now() < until) {
				await new Promise(resolve => setTimeout(resolve, 350));
				const current = await this.script(tabID, false, "results");
				if (!current || !["found", "missing", "ambiguous"].includes(current.status))
					return "unverified_official_page";
				if (current.status === "found") return null;
				if (current.status === "ambiguous") return "ambiguous_resource";
				// 未知是否仍在异步加载时，不提前报告资源不存在。
			}
			// 页面没有可靠的查询完成标记；超时后的空结果仍只能交给用户确认。
			return "manual_action_required";
		}

		async detailTab() {
			const selected = await this.script(this.context.portalTabID, false, "select");
			if (selected?.status !== "selected" || !officialURL(selected.url, true)
					|| !await this.authorized()) return null;
			const portal = await this.inspect(this.context.portalTabID);
			if (!portal || portal.frame.documentId !== selected.documentID || !this.live()) return null;
			const tab = await this.browser.tabs.create({
				windowId: this.context.windowID, url: selected.url, active: false,
			});
			if (!this.live() || !Number.isInteger(tab?.id) || tab.incognito !== false
					|| tab.windowId !== this.context.windowID || tab.id === this.context.portalTabID) return null;
			return tab.id;
		}

		async preVisitFailure() {
			return {outcome: "Needs attention", reason: "manual_action_required"};
		}

		async execute() {
			if (!this.command.login_url) return {outcome: "Needs attention", reason: "manual_action_required"};
			if (!await this.portal()) {
				// An in-progress login or an unverified owned tab cannot be
				// declared browser-idle, even after a result or Cancel.
				return {outcome: "Needs attention", reason: "manual_action_required"};
			}
			const error = await this.loadedTarget();
			if (error) return {outcome: "Needs attention", reason: error};
			if (!await this.authorized()) return null;
			const detailID = await this.detailTab();
			if (!detailID || !this.live()) {
				return {outcome: "Needs attention", reason: "unverified_official_page"};
			}
			const deadline = Date.now() + 12000;
			let detail = null;
			while (this.live() && Date.now() < deadline) {
				detail = await this.inspect(detailID, true);
				if (detail) {
					if (this.detailDocumentID === null) this.trace("detail_verified");
					if (this.detailDocumentID !== null
							&& this.detailDocumentID !== detail.frame.documentId) {
						this.detailAttributionInvalid = true;
						return {outcome: "Needs attention", reason: "unverified_official_page"};
					}
					this.detailDocumentID = detail.frame.documentId;
					if ((await this.script(detailID, true, "check"))?.status === "ready") break;
				}
				detail = null;
				await new Promise(resolve => setTimeout(resolve, 400));
			}
			if (!detail || !this.live() || this.detailAttributionInvalid) {
				return {outcome: "Needs attention", reason: "unverified_official_page"};
			}
			this.trace("visit_control_ready");
			if (!await this.authorized("pre_visit_authorize") || this.detailAttributionInvalid)
				return this.preVisitFailure();
			let grant;
			this.trace("visit_grant_request");
			try {
				grant = await this.post("/api/connector/preparation/visit-authorize", {
					...this.identity, source_tab_id: detailID,
				});
			}
			catch (error) {
				this.trace(["AbortError", "TimeoutError"].includes(error?.name)
					? "visit_grant_timeout" : "visit_grant_network_error");
				return this.preVisitFailure();
			}
			if (!this.live()) return this.preVisitFailure();
			if (!grant?.ok) {
				this.trace(grant?.status === 409 ? "visit_grant_rejected"
					: [408, 504].includes(grant?.status) ? "visit_grant_timeout"
					: "visit_grant_http_error");
				return this.preVisitFailure();
			}
			if (typeof grant.body?.nonce !== "string"
					|| !/^[A-Za-z0-9_-]{32,128}$/.test(grant.body.nonce)) {
				this.trace("visit_grant_invalid_response");
				return this.preVisitFailure();
			}
			this.trace("visit_grant_accepted");
			const observer = new VisitObservation(this.browser.webNavigation, {
				detailTabID: detailID,
				current: () => this.live() && !this.detailAttributionInvalid,
				requireDispatchArm: true,
				diagnose: stage => this.trace(stage),
			});
			this.visitObserver = observer;
			this.trace("visit_observer_created");
			this.visitDispatchOpen = true;
			const observed = await observer.run(() => {
				// 观察结束不能代表已经发出的浏览器点击调用也已返回（SPEC §62）。
				this.visitClick = this.script(detailID, true, "visit");
				return this.visitClick.then(result => result?.status === "visited_click");
			});
			// Disable any injection still waiting for an authorization or frame callback.
			this.visitDispatchOpen = false;
			if (["invalid_operation", "listener_unavailable", "listener_registration_failed",
					"timer_registration_failed"].includes(observed.reason)) {
				return this.preVisitFailure();
			}
			if (!this.live() || this.detailAttributionInvalid)
				return {outcome: "Needs attention", reason: "navigation_incomplete"};
			if (!observed.evidence) {
				return {outcome: observed.reason === "visit_action_failed" || this.visitBrowserError
						? "Failed" : "Needs attention",
					reason: observed.reason === "visit_action_failed" || this.visitBrowserError ? "connector_error"
						: observed.reason === "timeout" ? "timeout" : "navigation_incomplete"};
			}
			return {outcome: "Opened", reason: "target_opened",
				evidence: {...observed.evidence, nonce: grant.body.nonce}};
		}

		cleanup() {
			// Preparation 保留所有标签页；这里只释放本 Service 的本地监听资源。
			this.consentDocuments.clear();
			this.visitObserver?.cancel();
			this.visitObserver = null;
		}

		cancel() {
			this.revoked = true;
			this.visitDispatchOpen = false;
			this.cleanup();
		}

	}

	global.LiteratureMonitorCARSI = Object.freeze({
		PORTAL_URL, RESOURCE_URL, officialPortal, exactNameMatches,
		resourcePageAction, detailPageAction, attributeConsentAction, VisitObservation, Executor,
		verifiedExecutor: true,
		verifiedConsent: true,
	});
})(self);
