/*
 * Literature Monitor Journal cover search (SPEC §56).
 * AGPLv3-or-later, © 2026 Literature Monitor contributors.
 */
(function(global) {
    "use strict";
    const SEARCH_MS = 30000;
    const DOWNLOAD_MS = 20000;
    const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

    function searchURL(name) {
        return `https://www.google.com/search?udm=2&q=${encodeURIComponent(name)}`;
    }
    function googlePage(value) {
        try {
            const url = new URL(value);
            if (url.protocol !== "https:" || !/^(?:[a-z0-9-]+\.)*google\.(?:[a-z]{2,3}|(?:com|co)\.[a-z]{2})$/.test(url.hostname)) return null;
            if (url.pathname === "/search" || url.pathname === "/imgres") return "search";
            if (url.pathname === "/sorry" || url.pathname.startsWith("/sorry/")) return "challenge";
        } catch (_) { /* Not a Google task page. */ }
        return null;
    }

    function queryKey(value) {
        return (new URL(value).searchParams.get("q") || "").normalize("NFKC").trim().replace(/\s+/g, " ").toLowerCase();
    }

    // Runs in the selected document. The first DOM result determines the image;
    // embedded records provide its original URL, never a later result's URL.
    function imagePageAction() {
        if (document.readyState === "loading") return {ready: false};
        const main = document.querySelector('main, [role="main"]');
        if (!main) return {ready: false};
        const first = [...main.querySelectorAll("img")].find(image => {
            if (!image.getClientRects().length) return false;
            const link = image.closest("a[href]");
            return image.closest('[role="button"]') || (link && new URL(link.href, location.href).pathname === "/imgres");
        });
        if (!first) return {ready: false};
        const thumbnail = first.currentSrc || first.src;
        const holder = first.closest("[data-docid], [jsdata]");
        const resultID = holder?.getAttribute("data-docid") || holder?.getAttribute("jsdata")?.split(";")[1];
        const records = [];
        // Google encodes URL strings as JSON strings in thumbnail/original pairs.
        const pattern = /"([-\w]+)",\s*\["((?:[^"\\]|\\.)+)",\s*\d+,\s*\d+\],\s*\["((?:[^"\\]|\\.)+)",\s*\d+,\s*\d+\]/g;
        for (const script of document.scripts) {
            for (const match of script.textContent.matchAll(pattern)) {
                records.push({id: match[1], thumbnail: JSON.parse(`"${match[2]}"`), original: JSON.parse(`"${match[3]}"`)});
            }
        }
        const link = first.closest("a[href]");
        const record = resultID ? records.find(item => item.id === resultID) : records.find(item => item.thumbnail === thumbnail);
        const original = link && new URL(link.href, location.href).searchParams.get("imgurl") || record?.original;
        try {
            const url = new URL(original);
            if (!["http:", "https:"].includes(url.protocol) || url.username || url.password
                || url.href === thumbnail || /(^|\.)encrypted-tbn\d*\.(?:gstatic|google)\.com$/.test(url.hostname)) return {ready: true, source_url: null};
            return {ready: true, source_url: url.href};
        } catch (_) { return {ready: true, source_url: null}; }
    }

    async function runGroups(journals, search, save, live) {
        for (let offset = 0; offset < journals.length && live(); offset += 3) {
            const group = journals.slice(offset, offset + 3);
            const images = await Promise.all(group.map((journal, index) => search(journal, index).catch(() => null)));
            for (let index = 0; index < group.length && live(); index++) await save(group[index], images[index]);
        }
    }

    class Executor {
        constructor(browser, command, post) {
            this.browser = browser;
            this.command = command;
            this.post = post;
            this.cancelled = false;
            this.slots = [];
            this.downloadController = null;
        }
        live() { return !this.cancelled; }
        stop() {
            this.cancelled = true;
            this.downloadController?.abort();
        }
        async authorized() {
            const reply = await this.post("/api/connector/covers/authorize", {request_id: this.command.request_id});
            if (!reply.ok || reply.body?.accepted !== true) this.stop();
            return this.live();
        }
        async search(journal, index) {
            let active = true;
            let timer;
            const work = async () => {
                if (!this.live() || !active || !await this.authorized()) return null;
                let slot = this.slots[index];
                if (!slot) {
                    const tab = await this.browser.tabs.create({windowId: this.windowID, url: "about:blank", active: false});
                    slot = {id: tab.id, detached: false};
                    this.slots[index] = slot;
                }
                if (!this.live() || !active || slot.detached) return null;
                Object.assign(slot, {commandPending: true, loading: true, failed: false, documentId: null});
                await this.browser.tabs.update(slot.id, {url: searchURL(journal.name)});
                while (this.live() && active && !slot.detached && !slot.failed) {
                    const frame = await this.browser.webNavigation.getFrame({tabId: slot.id, frameId: 0});
                    if (!this.live() || !active) return null;
                    if (!slot.loading && frame?.documentId === slot.documentId
                        && frame.documentLifecycle === "active" && googlePage(frame.url) === "search") {
                        const records = await this.browser.scripting.executeScript({
                            target: {tabId: slot.id, documentIds: [frame.documentId]},
                            world: "ISOLATED", func: imagePageAction,
                        });
                        if (!this.live() || !active || slot.loading) return null;
                        const record = records.length === 1 && records[0];
                        if (record?.documentId !== slot.documentId || record.frameId !== 0) return null;
                        if (record.result?.ready) return record.result.source_url;
                    }
                    await wait(200);
                }
                return null;
            };
            try {
                return await Promise.race([work(), new Promise(resolve => {
                    timer = setTimeout(() => {active = false; resolve(null);}, SEARCH_MS);
                })]);
            } finally { active = false; clearTimeout(timer); }
        }
        async download(sourceURL) {
            if (!sourceURL || !this.live()) return null;
            const url = new URL(sourceURL);
            if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return null;
            const controller = new AbortController();
            this.downloadController = controller;
            const timer = setTimeout(() => controller.abort(), DOWNLOAD_MS);
            try {
                const response = await fetch(url.href, {credentials: "omit", redirect: "follow", signal: controller.signal});
                if (!response.ok) return null;
                const reader = response.body.getReader();
                const chunks = [];
                let size = 0;
                for (;;) {
                    const {done, value} = await reader.read();
                    if (done) break;
                    size += value.length;
                    if (size > MAX_IMAGE_BYTES || !this.live()) {controller.abort(); return null;}
                    chunks.push(value);
                }
                if (!size || !this.live()) return null;
                const bytes = new Uint8Array(size);
                let offset = 0;
                for (const chunk of chunks) {bytes.set(chunk, offset); offset += chunk.length;}
                let binary = "";
                for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
                return btoa(binary);
            } catch (_) { return null; }
            finally {
                clearTimeout(timer);
                controller.abort();
                this.downloadController = null;
            }
        }
        async run() {
            const identity = {request_id: this.command.request_id};
            const detach = slot => {slot.detached = true; this.stop();};
            const before = details => {
                if (details.frameId !== 0 || details.url === "about:blank") return;
                const slot = this.slots.find(item => item?.id === details.tabId);
                if (slot) slot.loading = true;
            };
            const committed = details => {
                if (details.frameId !== 0 || details.url === "about:blank") return;
                const slot = this.slots.find(item => item?.id === details.tabId);
                if (!slot || slot.detached) return;
                const qualifiers = details.transitionQualifiers || [];
                const user = qualifiers.includes("from_address_bar") || qualifiers.includes("forward_back")
                    || ["typed", "auto_bookmark", "form_submit", "link", "reload"].includes(details.transitionType)
                        && !qualifiers.some(value => value.endsWith("_redirect"));
                const redirected = qualifiers.some(value => value.endsWith("_redirect"));
                const page = googlePage(details.url);
                if (!page || qualifiers.includes("from_address_bar") || qualifiers.includes("forward_back")
                    || !slot.commandPending && (user || !redirected)) {detach(slot); return;}
                Object.assign(slot, {commandPending: false, loading: false, documentId: details.documentId,
                    url: details.url, query: queryKey(details.url), failed: page === "challenge"});
            };
            const history = details => {
                if (details.frameId !== 0) return;
                const slot = this.slots.find(item => item?.id === details.tabId);
                if (!slot || slot.detached) return;
                if (googlePage(details.url) !== "search" || queryKey(details.url) !== slot.query) detach(slot);
                else slot.url = details.url;
            };
            const removed = id => {
                const slot = this.slots.find(item => item?.id === id);
                if (slot) detach(slot);
            };
            let checking = false;
            const timer = setInterval(async () => {
                if (checking || !this.live()) return;
                checking = true;
                try {await this.authorized();} catch (_) {this.stop();}
                finally {checking = false;}
            }, 1000);
            this.browser.webNavigation.onBeforeNavigate.addListener(before);
            this.browser.webNavigation.onCommitted.addListener(committed);
            this.browser.webNavigation.onHistoryStateUpdated.addListener(history);
            this.browser.tabs.onRemoved.addListener(removed);
            try {
                if (!await this.authorized()) return;
                const windows = await this.browser.windows.getAll({windowTypes: ["normal"]});
                const normal = windows.find(window => window.type === "normal" && window.incognito === false);
                if (!normal) return;
                this.windowID = normal.id;
                await runGroups(this.command.journals,
                    (journal, index) => this.search(journal, index),
                    async (journal, sourceURL) => {
                        if (!await this.authorized()) return;
                        const image = await this.download(sourceURL);
                        if (!await this.authorized()) return;
                        const reply = await this.post("/api/connector/covers/result", {
                            ...identity, issn_l: journal.issn_l, source_url: sourceURL, image_base64: image,
                        }, 25000);
                        if (!reply.ok || reply.body?.accepted !== true) this.stop();
                    }, () => this.live());
            } catch (_) { this.stop(); }
            finally {
                this.stop();
                clearInterval(timer);
                this.browser.webNavigation.onBeforeNavigate.removeListener(before);
                this.browser.webNavigation.onCommitted.removeListener(committed);
                this.browser.webNavigation.onHistoryStateUpdated.removeListener(history);
                this.browser.tabs.onRemoved.removeListener(removed);
                for (const slot of this.slots) {
                    if (!slot || slot.detached || slot.loading) continue;
                    try {
                        const frame = await this.browser.webNavigation.getFrame({tabId: slot.id, frameId: 0});
                        if (frame?.documentId === slot.documentId && googlePage(frame.url)) await this.browser.tabs.remove(slot.id);
                    } catch (_) { /* The user may already have closed the task page. */ }
                }
                await this.post("/api/connector/covers/finish", identity);
            }
        }
    }
    global.LiteratureMonitorCovers = {Executor, imagePageAction, runGroups, searchURL, googlePage};
})(self);
