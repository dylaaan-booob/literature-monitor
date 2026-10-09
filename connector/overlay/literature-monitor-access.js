/*
 * Literature Monitor Access Service preparation (SPEC §42.6).
 * Copyright © 2026 Literature Monitor contributors
 * Part of the Zotero Connector derivative, GNU AGPL v3 or later.
 */
(function(global) {
	"use strict";
	const DOI_ORIGIN = "https:" + "//doi.org";
	const MAX_HOPS = 8;
	const WAIT_MS = 20000;
	// 当前没有经真实服务验证的规则；合成测试规则绝不进入生产表。
	const VERIFIED_RULES = Object.freeze([]);

	function publicOrigin(value) {
		try {
			const url = new URL(value);
			if (url.protocol !== "https:" || url.username || url.password || url.port
				|| !url.hostname.includes(".") || url.hostname.endsWith(".localhost")
				|| !/^[a-z0-9][a-z0-9.-]*[a-z0-9]$/.test(url.hostname) || /^[0-9.]+$/.test(url.hostname)
				|| url.hostname.split(".").some(label => !label || label.startsWith("-") || label.endsWith("-"))) return null;
			return url.origin;
		}
		catch (_) { return null; }
	}

	// 用路径指纹比较普通导航进展，不保留路径或敏感查询；query/hash 不算进展。
	// 指纹碰撞只会保守回退，不会给出保存资格。
	function progressKey(value) {
		const url = new URL(value);
		let hash = 2166136261;
		for (const char of url.pathname) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619) >>> 0;
		return `${url.origin}:${hash}`;
	}

	function trustedRule(rule) {
		if (!rule || rule.verified !== true || !["CARSI", "Shibboleth", "OpenAthens", "equivalent"].includes(rule.federation)
			|| rule.associationPolicy !== "preserve"
			|| !["sp", "selector", "destinations", "returnBehavior", "association"].every(
				key => typeof rule.evidence?.[key] === "string" && rule.evidence[key].trim())
			|| !Array.isArray(rule.landingOrigins) || !rule.landingOrigins.length
			|| !Array.isArray(rule.allowedOrigins) || !rule.allowedOrigins.length
			|| ![rule.serviceOrigin, ...rule.landingOrigins, ...rule.allowedOrigins].every(
				origin => publicOrigin(origin) === origin && origin !== DOI_ORIGIN)
			|| !rule.allowedOrigins.includes(rule.serviceOrigin)
			|| !rule.landingOrigins.every(origin => rule.allowedOrigins.includes(origin))) return false;
		if (rule.entryURL === null) return true; // 已验证 SP 但尚无已验证 federation 路线。
		try {
			const entry = new URL(rule.entryURL);
			return !entry.search && !entry.hash && rule.allowedOrigins.includes(publicOrigin(entry.href))
				&& typeof rule.returnPath === "string" && rule.returnPath.startsWith("/")
				&& !/[?#\\]/.test(rule.returnPath) && rule.returnPath !== entry.pathname;
		}
		catch (_) { return false; }
	}

	class AccessContext {
		constructor(id, rules = VERIFIED_RULES) {
			this.id = id;
			this.rules = rules.filter(trustedRule).map(rule => Object.freeze({
				...rule, landingOrigins: Object.freeze([...rule.landingOrigins]),
				allowedOrigins: Object.freeze([...rule.allowedOrigins]), evidence: Object.freeze({...rule.evidence}),
			}));
			this.services = new Map();
		}
	}

	class AccessJourney {
		constructor(context, doiURL, now = () => performance.now()) {
			this.context = context;
			this.doiURL = doiURL;
			this.now = now;
			this.deadline = now() + WAIT_MS;
			this.closed = false;
			this.hops = 0;
			this.documents = new Set();
			this.destinations = new Set();
			this.redirects = new Set();
			this.ordinaryDocuments = new Set();
			this.ordinaryRedirects = new Set();
			this.lastOrdinaryDocument = null;
			this.sameDocumentVisits = 0;
			this.landingOrigin = null;
			this.rule = null;
			this.stage = "landing";
			this.reason = "unknown_service";
			this.captureAllowed = true;
			this.navigation = null;
			this.anchored = false;
			this.routeIssued = false;
			this.resourceIssued = false;
			this.resourceRedirectOrigin = null;
		}

		fallback(reason) {
			this.reason = reason;
			this.captureAllowed = false;
			this.navigation = null;
			if (this.rule) this.context.services.set(this.rule.serviceOrigin, reason);
		}

		observe(details) {
			if (this.closed || !this.captureAllowed || details.frameId !== 0) return;
			if (this.now() >= this.deadline) return this.fallback("access_timeout");
			if (this.documents.has(details.documentId)) return;
			this.documents.add(details.documentId);
			if (++this.hops > MAX_HOPS) return this.fallback("hop_limit");
			const origin = publicOrigin(details.url);
			if (!origin) return this.fallback("unsafe_destination");
			// 只保留 origin 与已验证的公开路线阶段，不保留完整 URL/查询参数。
			if (origin === DOI_ORIGIN && details.url === this.doiURL) this.anchored = true;
			const redirected = details.transitionQualifiers?.some(q => q === "server_redirect" || q === "client_redirect");
			if (!this.anchored && !redirected) return;
			this.anchored = true;
			if (details.transitionQualifiers?.includes("from_address_bar")) return this.fallback("access_failed");
			if (details.transitionType === "form_submit") return this.fallback("manual_challenge");
			if (origin === DOI_ORIGIN) {
				if (this.stage === "resource" && details.url !== this.doiURL) return this.fallback("unsafe_destination");
				return;
			}
			if (!this.landingOrigin) {
				this.landingOrigin = origin;
				this.destinations.add(origin);
				const matches = this.context.rules.filter(rule => rule.landingOrigins.includes(origin));
				if (matches.length !== 1) return this.observeOrdinaryDocument(details.url);
				this.rule = matches[0];
				if (this.rule.entryURL === null) { this.reason = "no_verified_route"; return; }
				const prior = this.context.services.get(this.rule.serviceOrigin);
				if (prior) {
					this.reason = prior === "prepared" ? "prepared" : "service_deferred";
					this.captureAllowed = prior === "prepared";
					return;
				}
				this.context.services.set(this.rule.serviceOrigin, "preparing");
				this.stage = "federation";
				this.reason = "preparing";
				this.navigation = this.rule.entryURL;
				return;
			}
			if (this.rule && !this.rule.allowedOrigins.includes(origin)) return this.fallback("unsafe_destination");
			const path = new URL(details.url).pathname;
			if (this.stage === "federation" && details.url === this.rule.entryURL) return;
			if (this.stage === "federation" && this.routeIssued && origin === this.rule.serviceOrigin && path === this.rule.returnPath) {
				this.stage = "resource";
				this.navigation = this.doiURL;
				return;
			}
			if (this.stage === "resource" && this.resourceIssued && this.rule.landingOrigins.includes(origin)) {
				if (this.resourceRedirectOrigin && origin !== this.resourceRedirectOrigin) return this.fallback("unsafe_destination");
				this.stage = "complete";
				this.reason = "prepared";
				this.context.services.set(this.rule.serviceOrigin, "prepared");
				return;
			}
			if (!this.rule) return this.observeOrdinaryDocument(details.url);
			if (this.destinations.has(origin)) return this.fallback("redirect_loop");
			this.destinations.add(origin);
		}

		observeRedirect(source, destination) {
			if (this.closed || !this.captureAllowed) return;
			if (source === this.doiURL) this.anchored = true;
			if (!this.anchored) return;
			if (this.now() >= this.deadline) return this.fallback("access_timeout");
			if (++this.hops > MAX_HOPS) return this.fallback("hop_limit");
			const origin = publicOrigin(destination);
			if (!origin || (this.rule && origin !== DOI_ORIGIN && !this.rule.allowedOrigins.includes(origin))) {
				return this.fallback("unsafe_destination");
			}
			// 普通 DOI 允许有限同域进展；经验证联邦路径仍保留 Origin 历史和单次 return 例外。
			if (!this.rule) {
				const target = progressKey(destination);
				if (this.ordinaryRedirects.has(target)) return this.fallback("redirect_loop");
				this.ordinaryRedirects.add(target);
				this.redirects.add(origin);
				return;
			}
			const expectedReturn = this.rule && origin === this.rule.serviceOrigin
				&& new URL(destination).pathname === this.rule.returnPath && this.stage === "federation" && this.routeIssued;
			let expectedResource = false;
			if (this.stage === "resource" && publicOrigin(source) === DOI_ORIGIN) {
				if (source !== this.doiURL || !this.rule.landingOrigins.includes(origin)) return this.fallback("unsafe_destination");
				if (!this.resourceIssued || this.resourceRedirectOrigin) return this.fallback("redirect_loop");
				expectedResource = true;
			}
			// 可信 SP return 后，已主动发出的原 DOI 可解析回一个已允许的 landing origin。
			// 该阶段只消费一次，保留全部历史；其他重复目的地仍是循环。
			if (this.redirects.has(origin) && !expectedReturn && !expectedResource) return this.fallback("redirect_loop");
			if (expectedResource) this.resourceRedirectOrigin = origin;
			this.redirects.add(origin);
		}

		observeOrdinaryDocument(url) {
			const key = progressKey(url);
			if (key === this.lastOrdinaryDocument) {
				// 允许一次同 URL 的自然验证刷新；连续第三个无进展文档仍回退。
				// 总 hop 数和原任务期限同时保持有效。
				if (++this.sameDocumentVisits > 2) this.fallback("redirect_loop");
				return;
			}
			if (this.ordinaryDocuments.has(key)) return this.fallback("redirect_loop");
			this.ordinaryDocuments.add(key);
			this.lastOrdinaryDocument = key;
			this.sameDocumentVisits = 1;
		}

		takeNavigation() {
			if (this.closed || !this.captureAllowed || this.now() >= this.deadline) return null;
			const target = this.navigation;
			this.navigation = null;
			return target;
		}

		navigationIssued(target) {
			if (target === this.rule?.entryURL && this.stage === "federation") this.routeIssued = true;
			if (target === this.doiURL && this.stage === "resource") this.resourceIssued = true;
		}

		snapshot() {
			return {
				landing_origin: this.landingOrigin, service_origin: this.rule?.serviceOrigin ?? null,
				reason: this.reason,
				// 导航完成不能代替可靠的会话、认证或资源授权证据。
				idp_session: "unknown", sp_session: "unknown",
				authentication: "unknown", entitlement: "unknown",
			};
		}
	}

	global.LiteratureMonitorAccess = Object.freeze({AccessContext, AccessJourney, WAIT_MS, MAX_HOPS, publicOrigin});
})(self);
