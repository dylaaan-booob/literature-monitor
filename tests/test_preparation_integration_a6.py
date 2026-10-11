"""A6 real Executor → Runtime → FastAPI → Coordinator → rendered Kept fragment.

Only Chrome DOM/events and transport are simulated; no Executor method is replaced.
"""
from __future__ import annotations

import json
from pathlib import Path
import select
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from literature_monitor.application.batch_import import BatchPhase
from literature_monitor.web.app import create_app
from literature_monitor.web.preparation_coordinator import EXECUTOR_CAPABILITY
from test_access_mapping_a4 import stamp
from preparation_helpers import prepared
from test_batch_import import paper, state
from test_web_run_settings import csrf_from_html


PROGRAM = r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const input = JSON.parse(process.argv[2]);
class Event {
    constructor() {this.listeners = new Set();}
    addListener(callback) {this.listeners.add(callback);}
    removeListener(callback) {this.listeners.delete(callback);}
    emit(...args) {for (const callback of [...this.listeners]) callback(...args);}
}
const nav = Object.fromEntries(["onCreatedNavigationTarget", "onBeforeNavigate", "onCommitted",
    "onCompleted", "onErrorOccurred"].map(name => [name, new Event()]));
const removed = new Event(), activated = new Event(), tabs = new Map(), frames = new Map();
const resourceURL = "https://ds.carsi.edu.cn/resource/resource.php";
const searches = [], visits = [], removals = [];
let schoolOnly = false, showing = "initial", portalCreations = 0, cardClicks = 0;
const searchInput = {value: ""};
function putTab(id, url, documentId) {
    tabs.set(id, {id, url, windowId: 1, incognito: false, status: "complete", title: "Research content"});
    frames.set(id, {frameId: 0, documentId, documentLifecycle: "active", url});
}
function card(name) {
    const index = input.names.indexOf(name), detailID = 10 + index * 10;
    return {
        href: "https://ds.carsi.edu.cn/resource/resourceDetail.php?id=" + index,
        getAttribute() {return "/resource/resourceDetail.php?id=" + index;},
        querySelector() {return {childNodes: [
            {nodeType: 3, textContent: name}, {nodeType: 1, textContent: "校购"},
        ]};},
        click() {
            cardClicks++;
            putTab(detailID, "https://ds.carsi.edu.cn/resource/resourceDetail.php?id=" + index,
                "detail-" + index);
            nav.onCreatedNavigationTarget.emit({sourceTabId: 3, sourceFrameId: 0, tabId: detailID});
        },
    };
}
const portalDoc = {
    querySelector(selector) {
        if (selector === "div.con.text.act #padInfo.list")
            return {querySelectorAll: () => [card(showing)]};
        if (selector === 'input[type="checkbox"]#onlyMySchool[name="onlyMySchool"]')
            return {get checked() {return schoolOnly;}, click() {schoolOnly = true;}};
        return null;
    },
    querySelectorAll(selector) {
        if (selector === "input#searchKey.searchInput") return [searchInput];
        if (selector === "button.Btn1.searBtn") return [{click() {
            searches.push(searchInput.value); showing = searchInput.value;
        }}];
        return [];
    },
};
function detailDoc(detailID) {
    return {querySelectorAll(selector) {
        if (selector !== ".moreBox a.earth_more") return [];
        return [{
            querySelector() {return {textContent: "访问资源"};},
            getAttribute() {return "javascript:gotoResource(42)";},
            click() {
                visits.push(detailID);
                const targetID = detailID + 1, documentId = "landing-" + detailID;
                putTab(targetID, "https://publisher.example.org/article/" + detailID, documentId);
                if (input.cancel) tabs.get(targetID).title = "Institution sign in";
                nav.onCreatedNavigationTarget.emit({sourceTabId: detailID, sourceFrameId: 0, tabId: targetID});
                nav.onBeforeNavigate.emit({tabId: targetID, frameId: 0});
                nav.onCommitted.emit({tabId: targetID, frameId: 0, documentId});
                // 上个 Service 的晚到完成事件不能代替当前 Service 的导航证据。
                if (detailID === 20)
                    nav.onCompleted.emit({tabId: 11, frameId: 0, documentId: "landing-10"});
                // Multiple external navigations need no final completion.
                nav.onBeforeNavigate.emit({tabId: targetID, frameId: 0});
                nav.onCommitted.emit({tabId: targetID, frameId: 0, documentId: "redirect-" + detailID});
            },
        }];
    }};
}
const browser = {
    runtime: {getManifest() {return {version: "0.6.4"};}},
    windows: {async getAll() {return [{id: 1, type: "normal", incognito: false}];}},
    webNavigation: {...nav, async getFrame({tabId}) {return frames.get(tabId);}},
    tabs: {
        onRemoved: removed, onActivated: activated,
        async create({url, windowId}) {
            assert.equal(windowId, 1); assert.equal(url, "about:blank");
            portalCreations++; putTab(3, url, "blank"); return tabs.get(3);
        },
        async update(id, {url}) {
            assert.equal(id, 3); assert.equal(url, resourceURL);
            putTab(id, url, "portal-doc"); return tabs.get(id);
        },
        async get(id) {if (!tabs.has(id)) throw Error("No such tab"); return tabs.get(id);},
        async remove(id) {
            assert(tabs.has(id)); removals.push(id); tabs.delete(id); frames.delete(id);
            removed.emit(id, {windowId: 1, isWindowClosing: false});
        },
    },
    scripting: {async executeScript({target, world, func, args}) {
        assert([3, 10, 20].includes(target.tabId), "no scripting on external pages");
        assert.equal(world, target.tabId !== 3 && args[0] === "visit" ? "MAIN" : "ISOLATED");
        assert.equal(target.documentIds[0], frames.get(target.tabId).documentId);
        const location = new URL(tabs.get(target.tabId).url);
        const result = vm.runInNewContext("(" + func.toString() + ")(...args)", {
            args, URL, location, document: target.tabId === 3 ? portalDoc : detailDoc(target.tabId),
        });
        return [{frameId: 0, documentId: target.documentIds[0], result}];
    }},
};
const context = {
    browser, URL, AbortController, setTimeout, clearTimeout, console: {info() {}},
    Zotero: {initDeferred: {promise: new Promise(() => {})}, logError() {},
        Connector: {async checkIsOnline() {return false;}}},
    fetch() {throw Error("Network forbidden");},
};
context.self = context;
for (const name of ["literature-monitor-carsi.js", "literature-monitor-runtime.js"])
    vm.runInNewContext(fs.readFileSync(process.argv[1] + "/" + name, "utf8"), context);
const runtime = context.LiteratureMonitorConnectorRuntime;
runtime._prepInstance = input.instance_id;
runtime._postJSON = async (path, body) => {
    fs.writeSync(1, JSON.stringify({path, body}) + "\n");
    const byte = Buffer.alloc(1);
    let line = "";
    while (fs.readSync(0, byte, 0, 1, null) && byte[0] !== 10) line += byte.toString();
    const reply = JSON.parse(line);
    if (reply.lost) throw Error("Server accepted; acknowledgement lost");
    return reply;
};
// Bridge drives the real Executor and Runtime; no Preparation-specific timer or settlement.
let execution;
const run = runtime._runPreparation.bind(runtime);
runtime._runPreparation = active => (execution = run(active));
const tick = async () => {await new Promise(setImmediate); await new Promise(setImmediate);};
(async () => {
    await runtime._claimPreparation();
    const first = runtime._prepActive;
    assert(first);
    await execution; await tick();
    assert.equal(runtime._prepActive, null, "Result immediately releases the Connector Service");
    assert.deepEqual(visits, [10]);
    await runtime._claimPreparation();
    if (!input.cancel) {
        const second = runtime._prepActive;
        assert(second);
        assert.equal(second.command.task_id, first.command.task_id);
        assert.notEqual(second.command.request_id, first.command.request_id);
        assert.notEqual(second.command.service_id, first.command.service_id);
        await execution; await tick();
        assert.equal(runtime._prepActive, null);
        assert.deepEqual(searches, input.names);
        assert.deepEqual(visits, [10, 20]);
        assert.deepEqual(removals, [], "Preparation never closes any tab");
        assert.deepEqual([...tabs.keys()], [3, 10, 11, 20, 21]);
        assert.equal(runtime._prepContext.taskID, first.command.task_id);
    }
    else {
        assert.equal(runtime._prepActive, null, "Cancel prevents the second Service claim");
        assert.deepEqual(searches, [input.names[0]]);
        assert.deepEqual(removals, [], "Cancel preserves every Preparation tab");
        assert(tabs.has(11), "sign-in page remains available for the user");
        assert.deepEqual([...tabs.keys()], [3, 10, 11]);
    }
    assert.equal(portalCreations, 1);
    assert.equal(cardClicks, input.cancel ? 1 : 2);
    assert.equal(schoolOnly, true);
    assert(Object.values(nav).every(event => event.listeners.size === 0));
    fs.writeSync(1, JSON.stringify({done: true}) + "\n");
})().catch(error => {
    fs.writeSync(1, JSON.stringify({error: error.stack}) + "\n"); process.exitCode = 1;
});

'''


@pytest.mark.parametrize("cancel_after_result", [False, True], ids=["two-services", "cancel-after-manual-result"])
def test_serial_preparation_with_real_executor_bridge_and_kept(tmp_path, cancel_after_result):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for Executor/Runtime integration")
    config, path, first, second_service = prepared(tmp_path)
    config.write_text(config.read_text() + "\ninstitution:\n  idp_entity_id: urn:example:campus\n")
    _, paper_path = paper(tmp_path / "workspace", "a6-kept")
    before = stamp(config), stamp(path), stamp(paper_path)
    app = create_app(config)
    prep, capture = app.state.preparation_coordinator, app.state.capture_coordinator
    capture_before = capture.snapshot()
    instance = "a6-real-executor-connector"
    payload = {"instance_id": instance, "names": [first.name, second_service.name], "cancel": cancel_after_result}
    deliveries = {"result": 0}
    results = []
    overlay = Path(__file__).resolve().parents[1] / "connector/overlay"
    with TestClient(app, base_url="http://localhost") as client:
        token = csrf_from_html(client.get("/?view=kept").text)
        assert client.post("/api/connector/preparation/heartbeat", json={
            "instance_id": instance, "capability": EXECUTOR_CAPABILITY, "version": "0.6.4",
        }).json()["ready"]
        assert "data-preparation-state>Running</strong>" in client.post(
            "/preparation/start", data={"csrf_token": token}).text
        with subprocess.Popen(
            [node, "-e", PROGRAM, str(overlay), json.dumps(payload)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ) as process:
            try:
                while True:
                    # 人工登录页面保留真实 30 秒观察期限，不替换生产计时器。
                    assert select.select([process.stdout], [], [], 40)[0], "Runtime bridge request timed out"
                    line = process.stdout.readline()
                    assert line, process.stderr.read()
                    message = json.loads(line)
                    assert "error" not in message, message.get("error")
                    if message.get("done"):
                        break
                    response = client.post(message["path"], json=message["body"])
                    kind = message["path"].rsplit("/", 1)[-1]
                    reply = {"ok": response.is_success, "status": response.status_code, "body": response.json()}
                    if kind == "result":
                        deliveries["result"] += 1
                        assert response.status_code == 200, response.json()
                        assert response.json() == {"accepted": True}
                        body = message["body"]
                        assert body["outcome"] == "Opened", body
                        assert body["reason"] == "target_opened"
                        assert body["evidence"]["navigation_observed"] is True
                        assert "final_document_id" not in body["evidence"]
                        if not results or results[-1]["request_id"] != body["request_id"]:
                            results.append(body)
                        else:
                            assert body == results[-1], "retry must preserve original identity/proof"
                        assert len(prep.snapshot().results) == len(results)
                        page = client.get("/fragments/preparation").text
                        assert f'data-preparation-count>{len(results)}</span>' in page
                        assert f'data-preparation-stat="opened">{len(results)}' in page
                        assert "data-preparation-pending-result" not in page
                        if deliveries["result"] == 1 and cancel_after_result:
                            canceled = client.post("/preparation/cancel", data={
                                "csrf_token": token, "task_id": body["task_id"],
                                "generation": body["generation"],
                            }).text
                            assert 'data-preparation-state>Canceled</strong>' in canceled
                        # First receipt is accepted, but the HTTP response is lost.
                        # The identical retry cannot advance another Service.
                        if deliveries["result"] == 1:
                            reply = {"lost": True}
                    process.stdin.write(json.dumps(reply) + "\n")
                    process.stdin.flush()
                assert process.wait(timeout=5) == 0, process.stderr.read()
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
        expected = 1 if cancel_after_result else 2
        snapshot = prep.snapshot()
        assert len(snapshot.results) == expected
        assert deliveries == {"result": expected + 1}
        assert [result["service_id"] for result in results] == [first.id, second_service.id][:expected]
        completed = client.get("/?view=kept").text
        assert f'data-preparation-state>{"Canceled" if cancel_after_result else "Completed"}</strong>' in completed
        assert f'data-preparation-count>{expected}</span>' in completed
        assert f'data-preparation-stat="opened">{expected}' in completed
        assert "Import to Zotero" in completed and "a6-kept" in completed
        assert app.state.batch_import.snapshot().phase is BatchPhase.IDLE
        owner = capture.reserve_browser_task("import")
        assert owner is not None
        assert capture.reserve_browser_task("preparation") is None
        assert capture.release_browser_task("import", owner)
    assert state(paper_path).status.value == "kept"
    assert capture.snapshot().attempt == capture_before.attempt
    assert capture.snapshot().completion_pending == capture_before.completion_pending
    assert (stamp(config), stamp(path), stamp(paper_path)) == before
