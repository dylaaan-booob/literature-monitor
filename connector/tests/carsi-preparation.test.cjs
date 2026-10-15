"use strict";

const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "overlay", "literature-monitor-carsi.js"), "utf8");
const context = {URL, setTimeout, clearTimeout};
context.self = context;
vm.runInNewContext(source, context, {filename: "literature-monitor-carsi.js"});
const {RESOURCE_URL, officialPortal, exactNameMatches, VisitObservation, Executor, resourcePageAction, detailPageAction, verifiedExecutor} = context.LiteratureMonitorCARSI;

class Event {
    constructor() {this.listeners = new Set();}
    addListener(callback) {this.listeners.add(callback);}
    removeListener(callback) {this.listeners.delete(callback);}
    emit(...data) {for (const callback of [...this.listeners]) callback(...data);}
}
function navigation() {
    return {
        onCreatedNavigationTarget: new Event(), onBeforeNavigate: new Event(),
        onCommitted: new Event(), onCompleted: new Event(), onErrorOccurred: new Event(),
        onTabRemoved: new Event(),
    };
}
function setup(options = {}) {
    const nav = navigation();
    const state = {current: true};
    const observer = new VisitObservation(nav, {
        taskID: "task-1", generation: 10, requestID: "request-1", serviceID: "service-1", detailTabID: 3,
        current: () => state.current, maxWait: 25, completionQuietMs: 5,
        tabRemoved: nav.onTabRemoved, windowID: 1, ...options,
    });
    return {nav, state, observer};
}
function begin(nav, tabId = 3) {nav.onBeforeNavigate.emit({tabId, frameId: 0});}
function commit(nav, tabId = 3, documentId = "doc-final") {
    nav.onCommitted.emit({tabId, frameId: 0, documentId});
}
function complete(nav, tabId = 3, documentId = "doc-final") {
    nav.onCompleted.emit({tabId, frameId: 0, documentId});
}
function created(nav, tabId = 4, sourceTabId = 3, sourceFrameId = 0) {
    nav.onCreatedNavigationTarget.emit({tabId, sourceTabId, sourceFrameId});
}
function removed(nav, tabId = 4, windowId = 1) {
    nav.onTabRemoved.emit(tabId, {windowId, isWindowClosing: false});
}
function allDisposed(nav) {
    assert(Object.values(nav).every(event => event.listeners.size === 0));
}

function executorFixture({last = false, portalID = null, windowID = 1} = {}) {
    const nav = navigation(), tabs = new Map(), frames = new Map();
    const removedIDs = [];
    const browser = {
        windows: {async remove(id) {removedIDs.push("window:" + id);}},
        webNavigation: {...nav, async getFrame({tabId}) {return frames.get(tabId);}},
        tabs: {onRemoved: nav.onTabRemoved, onActivated: new Event(),
            async get(id) {
                if (!tabs.has(id)) throw Error("No such tab");
                return tabs.get(id);
            },
            async remove(id) {
                removedIDs.push(id);
                tabs.delete(id); frames.delete(id);
                removed(nav, id, windowID);
            }},
        scripting: {async executeScript() {throw Error("Unexpected script");}},
    };
    const context = {portalTabID: portalID, windowID};
    const command = {instance_id: "instance", task_id: "task", generation: 1,
        request_id: "request", service_id: "service", name: "Test Service", last_service: last};
    const executor = new Executor(browser, async () => ({ok: true, body: {accepted: true}}),
        command, context, () => true);
    return {nav, tabs, frames, browser, executor, context, removedIDs};
}

test("official resource and detail DOM adapter is present", () => {
    assert.equal(verifiedExecutor, true);
});

test("positive portal identity requires exact official URL and active top document", () => {
    const tab = {id: 3, windowId: 1, incognito: false, url: RESOURCE_URL};
    const frame = {frameId: 0, url: RESOURCE_URL, documentId: "doc-portal", documentLifecycle: "active"};
    assert.equal(officialPortal(tab, frame), true);
    for (const url of ["http://ds.carsi.edu.cn/wxds", "https://ds.carsi.edu.cn.evil.org/wxds",
        "https://ds.carsi.edu.cn/login", "https://ds.carsi.edu.cn/wxds?secret=x"] ) {
        assert.equal(officialPortal({...tab, url}, frame), false);
    }
    assert.equal(officialPortal({...tab, incognito: true}, frame), false);
    assert.equal(officialPortal(tab, {...frame, frameId: 1}), false);
    assert.equal(officialPortal(tab, {...frame, errorOccurred: true}), false);
    assert.equal(officialPortal(tab, {...frame, documentLifecycle: "cached"}), false);
    assert.equal(officialPortal(tab, {...frame, url: "https://idp.example.edu/login"}), false);
});

test("resource identity is exact: zero, one, duplicates, case and whitespace", () => {
    const name = "Springer Nature";
    const matches = ["Springer", "springer nature", " Springer Nature", "Springer Nature", "Springer Nature"]
        .map(textContent => ({textContent}));
    assert.equal(exactNameMatches(matches.slice(0, 3), name).length, 0);
    assert.equal(exactNameMatches(matches.slice(0, 4), name).length, 1);
    assert.equal(exactNameMatches(matches, name).length, 2);
    assert.equal(exactNameMatches(matches, "").length, 0);
});

test("search accepts unique matches without list snapshots and waits for delayed results", async () => {
    const command = {instance_id: "instance", task_id: "task", generation: 1,
        request_id: "request", service_id: "service", name: "Target"};
    const executor = new Executor({}, async () => ({ok: true, body: {accepted: true}}),
        command, {portalTabID: 3}, () => true);
    // 缩短本用例的有界时钟，不改变生产接口或真实浏览器行为。
    const originalDate = context.Date;
    try {
        for (const [statuses, expected] of [
            [["found"], null],
            [["missing", "found"], null],
            [["missing"], "manual_action_required"],
            [["ambiguous"], "ambiguous_resource"],
        ]) {
            const actions = [];
            let polls = 0;
            executor.script = async (_tab, _detail, action) => {
                actions.push(action);
                if (action === "search") return {status: "submitted"};
                if (action === "scan") return {status: "ready"};
                return {status: statuses[Math.min(polls++, statuses.length - 1)]};
            };
            let now = 0;
            context.Date = {now: () => (now += 4000)};
            assert.equal(await executor.loadedTarget(), expected);
            assert.equal(actions.filter(action => action === "search").length, 1);
            assert(actions.includes("results"));
        }
    }
    finally {
        if (originalDate === undefined) delete context.Date;
        else context.Date = originalDate;
    }
});

test("Taylor & Francis uses a searchable suffix but extracts only the exact saved Service Detail URL", async () => {
    const name = "Taylor & Francis期刊数据库", query = "Francis期刊数据库";
    const input = {value: ""};
    let schoolEnabled = false, schoolClicks = 0, searches = 0, selected = 0, cards = [];
    const card = title => ({
        href: "https://ds.carsi.edu.cn/resource/resourceDetail.php?id=42",
        getAttribute: attribute => attribute === "href" ? "/resource/resourceDetail.php?id=42" : null,
        querySelector: selector => selector === ".flex .t1"
            ? {childNodes: [{nodeType: 3, textContent: title}]} : null,
        click() {selected++;},
    });
    const oldCard = card("Old Service"), target = card(name);
    cards = [oldCard];
    const school = {
        get checked() {return schoolEnabled;},
        click() {schoolClicks++; schoolEnabled = !schoolEnabled;},
    };
    const doc = {
        querySelector(selector) {
            if (selector === "div.con.text.act #padInfo.list")
                return {querySelectorAll: () => cards};
            if (selector.includes("#onlyMySchool")) return school;
            return null;
        },
        querySelectorAll(selector) {
            if (selector === "input#searchKey.searchInput") return [input];
            if (selector === "button.Btn1.searBtn") return [{click() {
                searches++;
                if (input.value === query) cards = [target];
            }}];
            return [];
        },
    };
    const browser = {
        scripting: {async executeScript({func, args, world}) {
            assert.equal(world, "ISOLATED");
            const result = vm.runInNewContext("(" + func.toString() + ")(...args)", {
                args, URL, document: doc,
                location: {origin: "https://ds.carsi.edu.cn", pathname: "/resource/resource.php"},
            });
            return [{frameId: 0, documentId: "portal-document", result}];
        }},
    };
    const command = {instance_id: "instance", task_id: "task", generation: 1,
        request_id: "request", service_id: "service", name};
    const executor = new Executor(browser, async () => ({ok: true, body: {accepted: true}}),
        command, {portalTabID: 3}, () => true);
    executor.inspect = async () => ({frame: {documentId: "portal-document"}});
    assert.equal(executor.searchQuery, query);
    assert.equal(new Executor({}, () => {}, {...command, name: "Oxford Journals"}, {},
        () => true).searchQuery, "Oxford Journals");
    const originalDate = context.Date;
    let clock = 0;
    context.Date = {now: () => (clock += 850)};
    try {
        assert.equal(await executor.loadedTarget(), null, "updated exact match is accepted");
    }
    finally {
        if (originalDate === undefined) delete context.Date;
        else context.Date = originalDate;
    }
    assert.equal(schoolEnabled, false);
    assert.equal(schoolClicks, 0, "an unchecked filter is never clicked");
    assert.equal(input.value, query, "search input must contain the suffix, not canonical identity");
    assert.equal(searches, 1, "one official search submission");
    const selection = await executor.script(3, false, "select");
    assert.equal(selection.status, "selected");
    assert.equal(selection.url, target.href);
    assert.equal(selected, 0, "the verified Detail URL is extracted without clicking");

    cards = [card(query)]; // A partial name is not the saved Service name.
    assert.equal((await executor.script(3, false, "select")).status, "missing");
    cards = [card(name), card(name)];
    assert.equal((await executor.script(3, false, "select")).status, "ambiguous");
    input.value = name;
    assert.equal((await executor.script(3, false, "select")).status, "unverified");
    assert.equal(selected, 0, "no Detail anchor is clicked");

    schoolEnabled = true;
    school.click = () => {schoolClicks++;}; // An ineffective native click.
    assert.equal(await executor.loadedTarget(), "unverified_official_page");
    assert.equal(searches, 1, "an unconfirmed filter state prevents search submission");
    schoolEnabled = undefined;
    assert.equal(await executor.loadedTarget(), "unverified_official_page");
    assert.equal(schoolClicks, 1, "an unknown state must not trigger another click");
});

test("matching resource names never authorize external, script or non-detail URLs", () => {
    for (const raw of [
        "https://outside.example.org/resource/resourceDetail.php?id=1",
        "https://ds.carsi.edu.cn.evil.org/resource/resourceDetail.php",
        "javascript:gotoResource(1)", "http://ds.carsi.edu.cn/resource/resourceDetail.php",
        "/resource/gotoResource.php?id=1", "/resource/resourceDetail.php#spoof",
        "", {href: "/resource/resourceDetail.php?id=1",
            resolved: "https://outside.example.org/resource/resourceDetail.php?id=1"},
    ]) {
        let clicks = 0;
        const href = typeof raw === "string" ? raw : raw.href;
        const card = {
            href: typeof raw === "string" ? (href && new URL(href, RESOURCE_URL).href) : raw.resolved,
            getAttribute(name) {return name === "href" ? href : null;},
            querySelector(selector) {
                return selector === ".flex .t1"
                    ? {childNodes: [{nodeType: 3, textContent: "Target"}]} : null;
            },
            click() {clicks++;},
        };
        const document = {
            querySelector(selector) {
                if (selector === "div.con.text.act #padInfo.list")
                    return {querySelectorAll: () => [card]};
                if (selector.includes("#onlyMySchool"))
                    return {checked: false};
                return null;
            },
            querySelectorAll(selector) {
                if (selector === "input#searchKey.searchInput") return [{value: "Target"}];
                if (selector === "button.Btn1.searBtn") return [{click() {throw Error("unexpected search");}}];
                return [];
            },
        };
        const result = vm.runInNewContext("(" + resourcePageAction.toString() + ")('select', 'Target')", {
            URL, document, location: {origin: "https://ds.carsi.edu.cn",
                pathname: "/resource/resource.php", href: RESOURCE_URL},
        });
        assert.equal(result.status, "unverified", JSON.stringify(raw));
        assert.equal(clicks, 0, JSON.stringify(raw));
    }
});

test("Detail navigation refuses revoked authority, a changed Portal document and cancellation", async () => {
    for (const condition of ["authority", "document", "cancel"]) {
        const f = executorFixture({portalID: 3});
        const detailURL = "https://ds.carsi.edu.cn/resource/resourceDetail.php?id=42";
        f.tabs.set(3, {id: 3, windowId: 1, incognito: false, status: "complete", url: RESOURCE_URL});
        f.frames.set(3, {frameId: 0, documentId: "portal-document", documentLifecycle: "active", url: RESOURCE_URL});
        f.executor.command.login_url = "https://ds.carsi.edu.cn/Shibboleth.sso/Login";
        f.executor.portal = async () => true;
        f.executor.loadedTarget = async () => null;
        let selected = false, creations = 0, visitGrants = 0;
        f.browser.scripting.executeScript = async ({target, args}) => {
            assert.equal(args[0], "select");
            assert.equal(target.documentIds[0], "portal-document");
            selected = true;
            return [{frameId: 0, documentId: "portal-document", result: {status: "selected", url: detailURL}}];
        };
        f.executor.post = async url => {
            if (url.endsWith("/visit-authorize")) visitGrants++;
            if (selected) {
                if (condition === "authority") return {ok: true, body: {accepted: false}};
                if (condition === "document") f.frames.get(3).documentId = "user-navigated-document";
                if (condition === "cancel") f.executor.cancel();
            }
            return {ok: true, body: {accepted: true}};
        };
        f.browser.tabs.create = async () => {creations++; throw Error("Unexpected Detail creation");};
        const result = await f.executor.execute();
        assert.equal(selected, true, condition);
        assert.equal(result.outcome, "Needs attention", condition);
        assert.equal(creations, 0, condition);
        assert.equal(visitGrants, 0, condition);
        assert.deepEqual(f.removedIDs, [], condition);
        allDisposed(f.nav);
    }
});

test("failed Detail creation and cancellation during creation cannot dispatch Visit or close pages", async () => {
    for (const condition of ["failed", "cancel"]) {
        const f = executorFixture({portalID: 3});
        f.tabs.set(3, {id: 3, windowId: 1, incognito: false, status: "complete", url: RESOURCE_URL});
        f.frames.set(3, {frameId: 0, documentId: "portal-document", documentLifecycle: "active", url: RESOURCE_URL});
        f.executor.command.login_url = "https://ds.carsi.edu.cn/Shibboleth.sso/Login";
        f.executor.portal = async () => true;
        f.executor.loadedTarget = async () => null;
        const detailURL = "https://ds.carsi.edu.cn/resource/resourceDetail.php?id=42";
        let scripts = 0, visitGrants = 0, completeCreation, started;
        const creating = new Promise(resolve => {started = resolve;});
        f.executor.post = async url => {
            if (url.endsWith("/visit-authorize")) visitGrants++;
            return {ok: true, body: {accepted: true}};
        };
        f.browser.scripting.executeScript = async ({args}) => {
            scripts++;
            assert.equal(args[0], "select");
            return [{frameId: 0, documentId: "portal-document", result: {status: "selected", url: detailURL}}];
        };
        f.browser.tabs.create = async options => {
            assert.equal(options.windowId, 1);
            assert.equal(options.active, false);
            assert.equal(options.url, detailURL);
            started();
            if (condition === "failed") throw Error("Cannot create Detail tab");
            return new Promise(resolve => {completeCreation = resolve;});
        };
        const executing = f.executor.execute();
        if (condition === "failed") {
            await assert.rejects(executing, /Cannot create Detail tab/);
        } else {
            await creating;
            f.executor.cancel();
            const tab = {id: 4, windowId: 1, incognito: false, url: detailURL, active: false};
            f.tabs.set(tab.id, tab);
            completeCreation(tab);
            assert.equal((await executing).outcome, "Needs attention");
            assert(f.tabs.has(4), "the late-created page remains open");
        }
        assert.equal(scripts, 1, condition);
        assert.equal(visitGrants, 0, condition);
        assert.deepEqual(f.removedIDs, [], condition);
        allDisposed(f.nav);
    }
});

test("Visit dispatch checks fresh authorization, document and open observation", async () => {
    for (const [condition, reason] of [
        ["initial", "visit_initial_authorize_rejected"],
        ["document", "visit_document_unverified"],
        ["final", "visit_final_authorize_rejected"],
        ["closed", "visit_dispatch_closed"],
    ]) {
        let checks = 0, injections = 0;
        const executor = new Executor({scripting: {async executeScript() {injections++;}}},
            async () => {
                checks++;
                return {ok: checks !== (condition === "initial" ? 1 : 2),
                    status: checks === (condition === "initial" ? 1 : 2) ? 409 : 200,
                    body: {accepted: checks !== (condition === "initial" ? 1 : 2)}};
            },
            {instance_id: "instance", task_id: "task", generation: 1,
                request_id: "request", service_id: "service"},
            {windowID: 1}, () => true);
        const stages = [];
        executor.trace = stage => stages.push(stage);
        executor.inspect = async () => condition === "document" ? null
            : {frame: {documentId: "original-detail"}};
        executor.visitDispatchOpen = condition !== "closed";
        // The document/closed cases receive successful authorizations.
        if (["document", "closed"].includes(condition))
            executor.authorized = async stage => {
                if (stage) stages.push(stage + "_ok");
                return true;
            };
        assert.equal(await executor.script(4, true, "visit"), null, condition);
        assert.equal(injections, 0, condition);
        assert(stages.includes(reason), condition + ": " + stages.join(", "));
    }
});

test("two CARSI Services reuse the Portal and retain every official Detail and Visit tab", async () => {
    const nav = navigation();
    const tabs = new Map([
        [3, {id: 3, windowId: 1, incognito: false, status: "complete", url: RESOURCE_URL}],
    ]);
    const frames = new Map([
        [3, {frameId: 0, documentId: "portal-doc", documentLifecycle: "active", url: RESOURCE_URL}],
    ]);
    let onlyMySchool = true, schoolClicks = 0;
    let searchClicks = 0;
    let showing = "initial";
    const searchInput = {value: ""};
    let cardClicks = 0;
    const createdDetails = [];
    let detailTabID;
    let visitTabID;
    let visitClicks = 0;
    let externalScripts = 0;
    const fakeCard = (name, tag) => ({
        href: "https://ds.carsi.edu.cn/resource/resourceDetail.php?id=42",
        getAttribute(attribute) {
            return attribute === "href" ? "/resource/resourceDetail.php?id=42" : null;
        },
        querySelector(selector) {
            if (selector !== ".flex .t1") return null;
            return {childNodes: [
                {nodeType: 3, textContent: " " + name + " "},
                {nodeType: 1, textContent: tag},
            ]};
        },
        click() {
            cardClicks++;
            throw Error("Detail anchors must not be clicked");
        },
    });
    const sourceCard = fakeCard("Other Service", "免费");
    const targetCard = fakeCard("Sample Research", "校购");
    const nextCard = fakeCard("Second Research", "试用");
    const searchButton = {click() {
        searchClicks++;
        showing = searchInput.value;
    }};
    const school = {
        get checked() {return onlyMySchool;},
        click() {schoolClicks++; onlyMySchool = false;},
    };
    const resourceDoc = {
        querySelector(selector) {
            if (selector === "div.con.text.act #padInfo.list") {
                return {querySelectorAll: () => showing === "Sample Research" ? [targetCard]
                    : showing === "Second Research" ? [nextCard] : [sourceCard]};
            }
            if (selector === 'input[type="checkbox"]#onlyMySchool[name="onlyMySchool"]') return school;
            return null;
        },
        querySelectorAll(selector) {
            if (selector === "input#searchKey.searchInput") return [searchInput];
            if (selector === "button.Btn1.searBtn") return [searchButton];
            return [];
        },
    };
    const control = {
        querySelector(selector) {return selector === "span.txt" ? {textContent: "访问资源"} : null;},
        getAttribute() {return "javascript:gotoResource(42)";},
        click() {
            visitClicks++;
            visitTabID = detailTabID + 1;
            tabs.set(visitTabID, {id: visitTabID, windowId: 1, incognito: false, status: "loading",
                active: true, title: "Database", url: "https://publisher.example.org/article"});
            nav.onCreatedNavigationTarget.emit({sourceTabId: detailTabID, sourceFrameId: 0, tabId: visitTabID});
            nav.onBeforeNavigate.emit({tabId: visitTabID, frameId: 0});
            nav.onCommitted.emit({tabId: visitTabID, frameId: 0, documentId: "publisher-opening"});
        },
    };
    const detailDoc = {
        querySelectorAll(selector) {return selector === ".moreBox a.earth_more" ? [control] : [];},
    };
    const removedIDs = [];
    const activated = new Event();
    const browser = {
        webNavigation: {...nav, async getFrame({tabId}) {return frames.get(tabId);}},
        tabs: {
            onRemoved: nav.onTabRemoved,
            onActivated: activated,
            async create(options) {
                assert.equal(options.windowId, 1);
                assert.equal(options.active, false);
                assert.equal(options.url, targetCard.href);
                detailTabID = 4 + createdDetails.length * 2;
                const tab = {id: detailTabID, windowId: options.windowId, incognito: false,
                    active: options.active, status: "complete", url: options.url};
                tabs.set(tab.id, tab);
                frames.set(tab.id, {frameId: 0, documentId: "detail-doc-" + tab.id,
                    documentLifecycle: "active", url: tab.url});
                createdDetails.push({options, tab});
                return tab;
            },
            async get(tabId) {
                if (!tabs.has(tabId)) throw Error("No such tab");
                return tabs.get(tabId);
            },
            async remove(tabId) {
                assert(tabs.has(tabId));
                removedIDs.push(tabId);
                tabs.delete(tabId); frames.delete(tabId); removed(nav, tabId);
                if (tabId === 5) activated.emit({tabId: 4});
                if (tabId === 4) activated.emit({tabId: 3});
            },
        },
        scripting: {async executeScript({target, func, args, world}) {
            if (new URL(tabs.get(target.tabId).url).hostname !== "ds.carsi.edu.cn") {externalScripts++; throw Error("External script forbidden");}
            assert.equal(world, target.tabId === detailTabID && args[0] === "visit" ? "MAIN" : "ISOLATED");
            const tab = tabs.get(target.tabId);
            assert.equal(target.documentIds[0], frames.get(target.tabId).documentId);
            const url = new URL(tab.url);
            const document = target.tabId === 3 ? resourceDoc : detailDoc;
            const result = vm.runInNewContext("(" + func.toString() + ")(...args)", {
                args, document, URL,
                location: {origin: url.origin, pathname: url.pathname, href: url.href},
            });
            return [{frameId: 0, documentId: target.documentIds[0], result}];
        }},
    };
    const calls = [];
    const post = async (url, body) => {
        calls.push({url, body});
        if (url.endsWith("/visit-authorize")) return {ok: true, body: {nonce: "n".repeat(43)}};
        return {ok: true, body: {accepted: true}};
    };
    const command = {
        instance_id: "instance-aaaaaaaa", task_id: "task-aaaaaaaa",
        generation: 1, request_id: "request-aaaaaaaa",
        service_id: "svc_" + "a".repeat(32), name: "Sample Research",
        login_url: "https://ds.carsi.edu.cn/Shibboleth.sso/Login?entityID=urn%3Atest%3Aidp&target=https%3A%2F%2Fds.carsi.edu.cn%2Fwxds",
    };
    const context = {portalTabID: 3, windowID: 1};
    const executor = new Executor(browser, post, command, context, () => true);
    const stages = [];
    executor.trace = stage => stages.push(stage);
    searchInput.value = "Sample Research";
    assert.equal(showing, "initial", "typing does not submit the official search");
    const result = await executor.execute();
    assert.equal(result.outcome, "Opened");
    assert.equal(result.reason, "target_opened");
    assert.equal(result.evidence.nonce, "n".repeat(43));
    assert.equal(result.evidence.target_tab_id, 5);
    assert.equal(onlyMySchool, false);
    assert.equal(schoolClicks, 1);
    assert.equal(searchClicks, 1);
    assert.equal(searchInput.value, "Sample Research");
    assert.equal(cardClicks, 0);
    assert.equal(createdDetails.length, 1);
    assert.equal(result.evidence.source_tab_id, createdDetails[0].tab.id);
    assert.equal(visitClicks, 1);
    assert.equal(externalScripts, 0);
    assert.equal(calls.filter(call => call.url.endsWith("/visit-authorize")).length, 1);
    for (const stage of ["pre_visit_authorize_ok", "visit_grant_request",
        "visit_grant_accepted", "visit_observer_created", "navigation_observed",
        "visit_initial_authorize_ok", "visit_document_verified",
        "visit_final_authorize_ok", "visit_dispatch_started"]) {
        assert(stages.includes(stage), stage);
    }
    await executor.cleanup();
    executor.cancel();
    assert.deepEqual(removedIDs, []);
    assert.deepEqual([...tabs.keys()], [3, 4, 5]);
    allDisposed(nav);

    // The official page may restore its checked default between Services.
    onlyMySchool = true;
    const last = new Executor(browser, post, {
        ...command, request_id: "request-bbbbbbbb", service_id: "svc_" + "b".repeat(32),
        name: "Second Research", last_service: true,
    }, context, () => true);
    const lastResult = await last.execute();
    assert.equal(lastResult.outcome, "Opened");
    await last.cleanup();
    await last.cleanup();
    last.cancel();
    assert.deepEqual(removedIDs, []);
    assert.equal(context.portalTabID, 3);
    assert.deepEqual([...tabs.keys()], [3, 4, 5, 6, 7]);
    assert.equal(lastResult.evidence.target_tab_id, 7);
    allDisposed(nav);
    assert.equal(cardClicks, 0);
    assert.equal(createdDetails.length, 2);
    assert.equal(lastResult.evidence.source_tab_id, createdDetails[1].tab.id);
    assert.equal(visitClicks, 2);
    assert.equal(searchClicks, 2);
    assert.equal(onlyMySchool, false);
    assert.equal(schoolClicks, 2);
    assert.equal(searchInput.value, "Second Research");
    assert.equal(externalScripts, 0);
});


test("explicit browser scripting error produces Failed, not unconfirmed Opened", async () => {
    const {executor, nav} = executorFixture();
    executor.command.login_url = "https://ds.carsi.edu.cn/Shibboleth.sso/Login";
    executor.post = async path => path.endsWith("/visit-authorize")
        ? {ok: true, body: {nonce: "n".repeat(43)}}
        : {ok: true, body: {accepted: true}};
    executor.portal = async () => true;
    executor.loadedTarget = async () => null;
    executor.detailTab = async () => 4;
    executor.inspect = async () => ({frame: {documentId: "official-detail"}, tab: {id: 4}});
    executor.authorized = async () => true;
    const injected = executor.script.bind(executor);
    executor.script = (id, detail, action) => action === "check"
        ? Promise.resolve({status: "ready"}) : injected(id, detail, action);
    const result = await executor.execute();
    assert.equal(result.outcome, "Failed");
    assert.equal(result.reason, "connector_error");
    assert.equal(executor.visitBrowserError, true);
    allDisposed(nav);
});

test("old navigation before official Visit dispatch is not opening evidence", async () => {
    const {nav, observer} = setup({requireDispatchArm: true});
    const result = observer.run(async () => {
        created(nav, 4, 3); begin(nav, 4);
        observer.armed = true;
        created(nav, 5, 3); begin(nav, 5);
        return true;
    });
    const value = await result;
    assert.equal(value.reason, "opened");
    assert.equal(value.evidence.target_tab_id, 5);
    assert.equal(value.evidence.created_target_tab_id, 5);
    allDisposed(nav);
});

test("owned target opening completes without waiting for onCompleted or redirect quiescence", async () => {
    const {nav, observer} = setup();
    const result = observer.run(async () => {
        created(nav, 4, 3);
        begin(nav, 4);
        commit(nav, 4, "landing-one");
        for (let i = 0; i < 8; i++) {
            begin(nav, 4);
            commit(nav, 4, "redirect-" + i);
        }
        return true;
    });
    const value = await result;
    assert.equal(value.reason, "opened");
    assert.equal(value.evidence.target_tab_id, 4);
    assert.equal(value.evidence.navigation_observed, true);
    allDisposed(nav);
});

test("same-tab navigation start plus official click is Opened without final document ID", async () => {
    const {nav, observer} = setup();
    const result = observer.run(async () => {begin(nav, 3); return true;});
    assert.equal((await result).evidence.target_tab_id, 3);
    allDisposed(nav);
});

test("unrelated target, invalid source frame and click failure cannot be Opened", async () => {
    const {nav, observer} = setup({maxWait: 20});
    const result = observer.run(async () => {
        created(nav, 4, 99);
        created(nav, 5, 3, 1);
        return false;
    });
    assert.equal((await result).evidence, null);
    allDisposed(nav);
});

test("Visit timeout cleans every listener and does not dispatch a late click", async () => {
    const {nav, observer} = setup({maxWait: 10});
    let release;
    const result = observer.run(() => new Promise(resolve => {release = resolve;}));
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal((await result).reason, "timeout");
    allDisposed(nav);
    release(true);
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(observer.done, true);
});

test("cancel revokes observation and late callbacks cannot act on new generation", async () => {
    const {nav, observer, state} = setup();
    let release;
    const pending = observer.run(() => new Promise(resolve => {release = resolve;}));
    await new Promise(resolve => setTimeout(resolve, 0));
    state.current = false;
    observer.cancel();
    assert.equal((await pending).reason, "revoked");
    release(true);
    created(nav, 4); begin(nav, 4);
    allDisposed(nav);
});

test("Preparation has no tab or window removal API calls", () => {
    assert(!/\b(?:tabs|windows)\s*\.\s*remove\s*\(/.test(source));
});

for (const scenario of ["Opened", "loading", "redirects", "active", "same-tab", "Needs attention", "Failed", "Cancel", "Timeout"]) {
    test("all Preparation pages remain open after " + scenario + "; local listeners and timers are released", async () => {
        const {executor, nav, tabs, removedIDs, context} = executorFixture({last: true, portalID: 3});
        tabs.set(3, {id: 3, windowId: 1, incognito: false, url: RESOURCE_URL});
        tabs.set(4, {id: 4, windowId: 1, incognito: false,
            url: "https://ds.carsi.edu.cn/resource/resourceDetail.php"});
        tabs.set(5, {id: 5, windowId: 1, incognito: false,
            status: scenario === "loading" ? "loading" : "complete", active: scenario === "active",
            url: "https://database.example/article"});
        tabs.set(6, {id: 6, windowId: 1, incognito: false, url: "https://idp.example/login"});
        const timers = new Map();
        const observer = new VisitObservation(nav, {detailTabID: 4, current: () => !executor.revoked,
            setTimer(callback, delay) {assert.equal(delay, 10000); timers.set(1, callback); return 1;},
            clearTimer(id) {timers.delete(id);}});
        executor.visitObserver = observer;
        const pending = observer.run(async () => {
            if (scenario === "Failed") throw Error("Official click failed");
            if (scenario === "Needs attention") return false;
            if (["Cancel", "Timeout"].includes(scenario)) return true;
            if (scenario === "same-tab") {
                tabs.get(4).url = "https://database.example/article";
                begin(nav, 4);
            } else created(nav, 5, 4);
            return true;
        });
        if (scenario === "Cancel") executor.cancel();
        if (scenario === "Timeout") timers.get(1)();
        const result = await pending;
        const reason = {"Failed": "visit_action_failed", "Needs attention": "visit_not_invoked",
            "Cancel": "revoked", "Timeout": "timeout"}[scenario] || "opened";
        assert.equal(result.reason, reason);
        if (scenario === "redirects") {
            for (let i = 0; i < 8; i++) {
                tabs.get(5).url = "https://database.example/redirect-" + i;
                begin(nav, 5); commit(nav, 5, "redirect-" + i);
            }
        }
        executor.cleanup(); executor.cancel(); executor.cleanup();
        assert.deepEqual(removedIDs, []);
        assert.deepEqual([...tabs.keys()], [3, 4, 5, 6]);
        assert.equal(context.portalTabID, 3);
        assert.equal(executor.visitObserver, null);
        assert.equal(timers.size, 0);
        allDisposed(nav);
    });
}

function consentPage(options = {}) {
    let selected = 0, submitted = 0;
    const location = {origin: "https://idp.example", href: "https://idp.example/idp/profile/SAML2/Redirect/SSO?execution=e1s2"};
    const form = {method: "post", target: "", getAttribute(name) {return name === "action" ? location.href : null;}};
    const control = type => ({tagName: "INPUT", type, form, isConnected: true,
        matches() {return false;}, getClientRects() {return [{}];}, getAttribute() {return null;}});
    const choice = {...control("radio"), checked: false, click() {selected++; this.checked = true;}};
    const proceed = {...control("submit"), click() {submitted++;}};
    if (options.disabled) choice.matches = () => true;
    if (options.hidden) choice.getClientRects = () => [];
    if (options.foreignForm) proceed.form = {...form};
    if (options.foreignAction) proceed.getAttribute = name => name === "formaction" ? "https://other.example/submit" : null;
    if (options.selectFailure) choice.click = () => {selected++;};
    if (options.submitFailure) proceed.click = () => {submitted++; throw Error("native click failed");};
    const document = {querySelectorAll(selector) {
        if (selector === "#_shib_idp_globalConsent") return options.missing ? [] : options.ambiguous ? [choice, choice] : [choice];
        return [proceed];
    }};
    const world = {URL, location, document, getComputedStyle: () => ({visibility: "visible"})};
    const run = action => vm.runInNewContext(`(${context.LiteratureMonitorCARSI.attributeConsentAction.toString()})("${action}", "https://idp.example")`, world);
    return {run, world, counts: () => ({selected, submitted})};
}

test("attribute consent uses native clicks once per document, across executor cleanup", () => {
    const page = consentPage();
    assert.equal(page.run("scan").status, "ready");
    assert.equal(page.run("submit").status, "submitted");
    assert.equal(page.run("submit").status, "attempted");
    assert.deepEqual(page.counts(), {selected: 1, submitted: 1});
});

for (const problem of ["missing", "ambiguous", "disabled", "hidden", "foreignForm", "foreignAction"]) {
    test(`attribute consent leaves ${problem} forms manual`, () => {
        const page = consentPage({[problem]: true});
        assert.equal(page.run("submit").status, "unverified");
        assert.deepEqual(page.counts(), {selected: 0, submitted: 0});
    });
}

test("attribute consent failed selection or click cannot cause a second submit", () => {
    const missing = consentPage({selectFailure: true});
    assert.equal(missing.run("submit").status, "not_selected");
    assert.equal(missing.run("submit").status, "attempted");
    assert.equal(missing.counts().submitted, 0);
    const failed = consentPage({submitFailure: true});
    assert.throws(() => failed.run("submit"));
    assert.equal(failed.run("submit").status, "attempted");
    assert.equal(failed.counts().submitted, 1);
});

function consentExecutor({enabled = true, foreign = false, urn = false, navigation = false} = {}) {
    const f = executorFixture({portalID: 3});
    const page = consentPage();
    const url = foreign ? "https://foreign.example/idp" : page.world.location.href;
    f.tabs.set(3, {id: 3, incognito: false, windowId: 1, status: "complete", url});
    // getFrame({frameId:0}) need not repeat frameId in its response.
    f.frames.set(3, {documentId: "idp-document", documentLifecycle: "active", url});
    const command = {...f.executor.command, automatic_attribute_release_consent: enabled,
        login_url: "https://ds.carsi.edu.cn/Shibboleth.sso/Login?entityID=" + encodeURIComponent(urn ? "urn:mace:example:idp" : "https://idp.example/idp/shibboleth")};
    const injections = [];
    f.browser.scripting = {async executeScript(details) {
        injections.push(details);
        const result = page.run(details.args[0]);
        if (navigation && details.args[0] === "submit") throw Error("navigation replaced document");
        return [{frameId: 0, documentId: "idp-document", result}];
    }};
    f.executor = new Executor(f.browser, async () => ({ok: true, body: {accepted: true}}), command, f.context, () => true);
    return {...f, page, injections};
}

test("consent executor binds the active top document and does not wait for redirect", async () => {
    const f = consentExecutor({navigation: true});
    assert.equal(await f.executor.consent(), false); // A lost receipt does not undo the native click.
    assert.equal(await f.executor.consent(), false);
    assert.deepEqual(f.page.counts(), {selected: 1, submitted: 1});
    assert(f.injections.every(i => i.world === "ISOLATED" && i.target.tabId === 3 && i.target.documentIds[0] === "idp-document"));
    f.executor.cancel();
    assert.equal(f.executor.consentDocuments.size, 0);
    assert.deepEqual(f.removedIDs, []);
});

for (const condition of ["disabled", "foreign", "urn", "cancel", "expired", "newDocument", "cancelDuringAuthorization"]) {
    test(`consent executor refuses ${condition} authority`, async () => {
        const f = consentExecutor({enabled: condition !== "disabled", foreign: condition === "foreign", urn: condition === "urn"});
        if (condition === "cancel") f.executor.cancel();
        if (condition === "expired") f.executor.post = async () => ({status: 409, ok: false, body: {accepted: false}});
        if (["newDocument", "cancelDuringAuthorization"].includes(condition)) {
            let authorizations = 0;
            f.executor.post = async () => {
                if (++authorizations === 2) {
                    if (condition === "cancelDuringAuthorization") f.executor.cancel();
                    else f.frames.set(3, {...f.frames.get(3), documentId: "replacement"});
                }
                return {ok: true, body: {accepted: true}};
            };
        }
        assert.equal(await f.executor.consent(), false);
        assert.deepEqual(f.page.counts(), {selected: 0, submitted: 0});
    });
}
