"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(process.env.LM_COVER_SOURCE || path.join(__dirname, "../overlay/literature-monitor-covers.js"), "utf8");
function load(extra = {}) {
    const context = {URL, setTimeout, clearTimeout, setInterval, clearInterval, AbortController, Uint8Array,
        btoa: value => Buffer.from(value, "binary").toString("base64"),
        fetch: async () => new Response(new Uint8Array([1, 2, 3])), ...extra};
    context.self = context;
    vm.runInNewContext(source, context);
    return context;
}
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
class Event {
    constructor() {this.listeners = new Set();}
    addListener(listener) {this.listeners.add(listener);}
    removeListener(listener) {this.listeners.delete(listener);}
    emit(data) {for (const listener of this.listeners) listener(data);}
}

test("regional Google page: first DOM result maps to its original, without legacy selectors or preview loading", () => {
    // Cropped from the observed Google HK result: role=button -> img,
    // jsdata holds the result ID; a script stores thumbnail/original pairs.
    const thumbnail = "https://encrypted-tbn0.gstatic.com/images?q=tbn:first&s=10";
    const holder = {getAttribute(name) {return name === "jsdata" ? "XZxcdf;OVN4RzWdqDjdsM;page" : null;}};
    let link = null;
    const image = {src: thumbnail, getClientRects: () => [1], closest(selector) {
        if (selector === 'a[href]') return link;
        if (selector === '[role="button"]') return {};
        return holder;
    }};
    const second = {...image, src: "https://encrypted-tbn0.gstatic.com/images?q=second"};
    const record = '"OVN4RzWdqDjdsM",["https://encrypted-tbn0.gstatic.com/images?q\\u003dtbn:first\\u0026s\\u003d10",330,223],["https://projecteuclid.org/images/journals/cover_aos.jpg",330,223]';
    const document = {readyState: "interactive", scripts: [{textContent: record}],
        querySelector(selector) {
            assert.equal(selector, 'main, [role="main"]');
            return {querySelectorAll: () => [image, second]};
        }};
    const context = load({location: {origin: "https://www.google.com.hk", pathname: "/search", href: "https://www.google.com.hk/search?tbm=isch&q=normalized"}, document});
    const action = context.LiteratureMonitorCovers.imagePageAction;
    assert.equal(action()?.source_url, "https://projecteuclid.org/images/journals/cover_aos.jpg");
    // A later result with an original must never replace the first result.
    document.scripts = [{textContent: '"second-result",["https://encrypted-tbn0.gstatic.com/images?q=second",20,20],["https://example.org/second.jpg",400,400]'}];
    assert.equal(action().source_url, null);
    link = {href: "https://www.google.com.hk/imgres?imgurl=https%3A%2F%2Fexample.org%2Foriginal%3Fid%3D1"};
    assert.equal(action().source_url, "https://example.org/original?id=1");
    link.href = "https://www.google.com.hk/imgres?imgurl=" + encodeURIComponent(thumbnail);
    assert.equal(action().source_url, null);
    const page = context.LiteratureMonitorCovers.googlePage;
    for (const host of ["google.com", "www.google.com.hk", "www.google.co.uk", "images.google.de", "www.google.cat", "ipv4.google.com"])
        assert.equal(page(`https://${host}/search?tbm=isch&q=normalized&extra=1`), "search");
    assert.equal(page("https://www.google.com.hk/sorry/index"), "challenge");
    assert.equal(page("https://www.google.com.evil.example/search"), null);
});

test("groups search three at a time and download once at a time, in saved order; cancellation stops the rest", async () => {
    const {runGroups} = load().LiteratureMonitorCovers;
    const journals = Array.from({length: 7}, (_, index) => ({name: `Journal ${index}`, issn_l: index}));
    let searches = 0, maxSearches = 0, downloads = 0, maxDownloads = 0;
    const results = [];
    await runGroups(journals, async journal => {
        maxSearches = Math.max(maxSearches, ++searches);
        await sleep(5);
        searches--;
        if (journal.issn_l === 1) throw Error("Search failed");
        return `https://example.org/${journal.issn_l}.jpg`;
    }, async (journal, url) => {
        assert.equal(searches, 0);
        maxDownloads = Math.max(maxDownloads, ++downloads);
        await sleep(5);
        results.push([journal.issn_l, url]);
        downloads--;
    }, () => true);
    assert.equal(maxSearches, 3);
    assert.equal(maxDownloads, 1);
    assert.deepEqual(results.map(item => item[0]), [0, 1, 2, 3, 4, 5, 6]);
    assert.equal(results[1][1], null);
    let active = true, saved = 0;
    await runGroups(journals, async () => "https://example.org/image.jpg", async () => {
        saved++;
        active = false;
    }, () => active);
    assert.equal(saved, 1);
});

test("Executor reuses three task tabs, waits for serial saves, closes only its pages and runtime excludes other tasks", async () => {
    const navigation = new Event(), committed = new Event(), history = new Event(), removed = new Event();
    const tabs = new Map([[99, {id: 99, windowId: 1, url: "https://user.example", status: "complete"}]]);
    const closed = [], timeouts = [], saved = [];
    let nextTab = 1, creating = 0, created = 0;
    const browser = {
        windows: {async getAll() {return [{id: 1, type: "normal", incognito: false}];}},
        tabs: {
            onRemoved: removed,
            async create(options) {const id = nextTab++; created++; tabs.set(id, {id, ...options, status: "complete"}); return tabs.get(id);},
            async get(id) {return tabs.get(id);},
            async update(id, options) {
                navigation.emit({tabId: id, frameId: 0, url: options.url});
                const regional = options.url.replace("www.google.com", "www.google.com.hk").replace("udm=2", "tbm=isch");
                Object.assign(tabs.get(id), {url: regional, status: "complete"});
                committed.emit({tabId: id, frameId: 0, url: regional, documentId: `doc-${id}`,
                    transitionType: "typed", transitionQualifiers: []});
            },
            async remove(id) {closed.push(id); tabs.delete(id);},
        },
        webNavigation: {onBeforeNavigate: navigation, onCommitted: committed, onHistoryStateUpdated: history, async getFrame({tabId}) {
            return {documentId: `doc-${tabId}`, documentLifecycle: "active", url: tabs.get(tabId).url};
        }},
        scripting: {async executeScript({target}) {
            await sleep(1);
            return [{frameId: 0, documentId: target.documentIds[0], result: {ready: true, source_url: "https://example.org/original.jpg"}}];
        }},
    };
    let fetching = 0, maxFetching = 0;
    const context = load({browser, fetch: async (_url, options) => {
        assert.equal(options.credentials, "omit");
        assert.equal(options.redirect, "follow");
        maxFetching = Math.max(maxFetching, ++fetching);
        await sleep(2);
        fetching--;
        return new Response(new Uint8Array([1, 2, 3]));
    }});
    const command = {request_id: "batch-1", journals: Array.from({length: 4}, (_, i) => ({name: `Journal ${i}`, issn_l: `issn-${i}`}))};
    const executor = new context.LiteratureMonitorCovers.Executor(browser, command, async (route, payload, timeout) => {
        if (route.endsWith("/result")) {
            assert.equal(++creating, 1);
            timeouts.push(timeout);
            await sleep(2);
            assert.equal(payload.image_base64, "AQID");
            saved.push(payload.issn_l);
            creating--;
        }
        if (route.endsWith("/finish")) assert.deepEqual(closed.sort(), [1, 2, 3]);
        return {ok: true, body: {accepted: true}};
    });
    await executor.run();
    assert.equal(maxFetching, 1);
    assert.equal(created, 3);
    assert.deepEqual(saved, ["issn-0", "issn-1", "issn-2", "issn-3"]);
    assert(timeouts.every(timeout => timeout === 25000));
    assert(tabs.has(99));
    assert.equal(navigation.listeners.size, 0);
    assert.equal(removed.listeners.size, 0);
    assert.equal(committed.listeners.size, 0);
    assert.equal(history.listeners.size, 0);

    const runtimeSource = fs.readFileSync(path.join(__dirname, "../overlay/literature-monitor-runtime.js"), "utf8").replace("Runtime.install();", "");
    vm.runInNewContext(runtimeSource, context);
    const runtime = context.LiteratureMonitorConnectorRuntime;
    runtime._coverActive = executor;
    runtime._prepInstance = "instance";
    runtime._postJSON = () => assert.fail("An active cover batch must exclude other browser claims");
    await runtime._claimTick();
    await runtime._claimPreparation();
    await runtime._claimEnrichment();
});


test("Chrome downloads enforce stream size, timeout and cancellation without another request", async () => {
    let calls = 0;
    const oversize = load({fetch: async () => {
        calls++;
        return new Response(new Uint8Array(5 * 1024 * 1024 + 1));
    }});
    const large = new oversize.LiteratureMonitorCovers.Executor({}, {}, async () => {});
    assert.equal(await large.download("https://example.org/original"), null);
    assert.equal(calls, 1);
    let signal;
    const stalledFetch = async (_url, options) => {
        signal = options.signal;
        return new Response(new ReadableStream({start(controller) {
            signal.addEventListener("abort", () => controller.error(new Error("aborted")));
        }}));
    };
    const timed = load({fetch: stalledFetch, setTimeout: (fn, ms) => setTimeout(fn, Math.min(ms, 5))});
    assert.equal(await new timed.LiteratureMonitorCovers.Executor({}, {}, async () => {}).download("https://example.org/original"), null);
    assert.equal(signal.aborted, true);
    const context = load({fetch: stalledFetch});
    const executor = new context.LiteratureMonitorCovers.Executor({}, {}, async () => ({ok: false}));
    const pending = executor.download("https://example.org/original");
    await sleep(1);
    await executor.authorized();
    assert.equal(await pending, null);
    assert.equal(signal.aborted, true);
    assert.equal(executor.live(), false);
});

test("user navigation retains its tab and cancels an in-flight image before result submission", async () => {
    const before = new Event(), committed = new Event(), history = new Event(), removed = new Event();
    let url, downloadSignal, results = 0, closed = 0;
    const browser = {
        windows: {async getAll() {return [{id: 1, type: "normal", incognito: false}];}},
        tabs: {onRemoved: removed, async create() {return {id: 1};},
            async update(_id, options) {
                url = options.url;
                committed.emit({tabId: 1, frameId: 0, url, documentId: "owned", transitionQualifiers: []});
            }, async remove() {closed++;}},
        webNavigation: {onBeforeNavigate: before, onCommitted: committed, onHistoryStateUpdated: history,
            async getFrame() {return {url, documentId: "owned", documentLifecycle: "active"};}},
        scripting: {async executeScript() {return [{frameId: 0, documentId: "owned", result: {ready: true, source_url: "https://example.org/original"}}];}},
    };
    const context = load({fetch: async (_url, {signal}) => {
        downloadSignal = signal;
        return new Response(new ReadableStream({start(controller) {
            signal.addEventListener("abort", () => controller.error(new Error("aborted")));
            setTimeout(() => committed.emit({tabId: 1, frameId: 0, documentId: "user", url: "https://example.org/user", transitionType: "typed", transitionQualifiers: ["from_address_bar"]}), 1);
        }}));
    }});
    const executor = new context.LiteratureMonitorCovers.Executor(browser,
        {request_id: "cancel", journals: [{name: "Journal", issn_l: "first"}]},
        async route => {if (route.endsWith("/result")) results++; return {ok: true, body: {accepted: true}};});
    await executor.run();
    assert.equal(downloadSignal.aborted, true);
    assert.equal(results, 0);
    assert.equal(closed, 0);
});
