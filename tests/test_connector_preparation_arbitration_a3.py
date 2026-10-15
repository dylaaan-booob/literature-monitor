"""A3 real Connector runtime idle callback and separate browser grant checks.

Node executes the shipped derivative source with a dormant Zotero bootstrap;
no Chrome, Zotero, network or user Workspace is contacted.
"""

from __future__ import annotations

import subprocess
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "connector/overlay/literature-monitor-runtime.js"


def test_real_connector_reports_browser_idle_only_after_cleanup():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for Connector runtime callback contract")
    program = r"""
const fs=require("node:fs"),vm=require("node:vm"),assert=require("node:assert/strict");
const source=fs.readFileSync(process.argv[1],"utf8");
const context={
 self: {}, Zotero: {
   initDeferred: {promise: new Promise(()=>{})}, logError() {},
 }, setTimeout,clearTimeout,URL,AbortController,fetch(){throw new Error("network forbidden");},
};
vm.runInNewContext(source,context);
const runtime=context.self.LiteratureMonitorConnectorRuntime;
assert.ok(runtime);
const posted=[];
runtime._postJSON=async (path,payload)=>{
  posted.push({path,payload,active:runtime._activeTask});
  return {ok:true,body:{accepted:true}};
};
let completeReturn;
runtime._deliverResult=async()=>true;
runtime._returnToApp=()=>new Promise(resolve=>{completeReturn=resolve;});
const task={
 requestID:"req-unique", invocationID:"inv-unique",doiURL:"https://doi.org/10.1000/test",
 access:{reason:"unknown_service"}, challengedDocuments:new Set(),
 documentID:null, saveTriggered:false,translatorCancel:null,
 observationTimer:null,accessTimer:null,dispatchAccepted:false,
 pipelineEnded:false,resultAccepted:null,terminalOutcome:null,
};
(async()=>{
 runtime._activeTask=task;
 runtime._finishTask(task,"FAILED");
 await new Promise(setImmediate);
 assert.equal(posted.length,0,"idle receipt before browser cleanup");
 assert.equal(runtime._activeTask,task);
 completeReturn();
 await new Promise(setImmediate);
 assert.equal(runtime._activeTask,null);
 assert.equal(posted.length,1);
 assert.equal(posted[0].path,"/api/connector/import-browser-idle");
 assert.equal(posted[0].active,null);
 assert.deepEqual(Object.keys(posted[0].payload).sort(),["doi_url","invocation_id","request_id"]);
 assert.equal(posted[0].payload.request_id,"req-unique");
 assert.equal(posted[0].payload.invocation_id,"inv-unique");
 assert.equal(posted[0].payload.doi_url,"https://doi.org/10.1000/test");
 await runtime._reportImportBrowserIdle(task);
 assert.equal(posted.length,2);
 assert.equal(posted[1].active,null);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", program, str(RUNTIME)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def _run_connector_vm(program: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for real Connector runtime tests")
    result = subprocess.run(
        [node, "-e", _VM_SETUP + program, str(RUNTIME)],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


_VM_SETUP = r"""
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const context = {
  self: {}, Zotero: {initDeferred: {promise: new Promise(()=>{})}, logError() {}},
  browser: {runtime: {id: "test-connector"}},
  setTimeout, clearTimeout, URL, AbortController,
  fetch(){throw new Error("Real network forbidden");},
};
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), context);
const runtime = context.self.LiteratureMonitorConnectorRuntime;
const tick = async () => {await new Promise(setImmediate); await new Promise(setImmediate);};
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve=yes; reject=no;});
  return {promise, resolve, reject};
}
function makeTask() {
  return {
    requestID:"req-unique", invocationID:"inv-unique", doiURL:"https://doi.org/10.1000/example",
    tabID:17, access:{reason:"unknown_service"}, challengedDocuments:new Set(),
    documentID:"doc", saveDocumentID:"doc", saveTranslatorID:"translator",
    saveTriggered:true, dispatchRequested:true, dispatchAccepted:true,
    sessionID:"session", senderFrame:0, senderDocument:"doc",
    expiresAt:Date.now()+60000, parentConfirmed:false,
    translatorCancel:null, observationTimer:null, accessTimer:null,
    pipelineEnded:false, pipelineComplete:false, resultAccepted:null, terminalOutcome:null,
  };
}
function pipelineMessage(task, type="literature-monitor-pipeline-complete") {
  return {
    type, sessionID:task.sessionID, stage:"native_save",
    context:{
      documentID:task.saveDocumentID, translatorID:task.saveTranslatorID,
      requestID:task.requestID, invocationID:task.invocationID,
      doiURL:task.doiURL, expiresAt:task.expiresAt,
    },
  };
}
const sender={id:"test-connector",frameId:0,documentId:"doc",tab:{id:17}};
"""


@pytest.mark.parametrize("settlement_order", ["receipt_first", "cleanup_first"])
@pytest.mark.parametrize("late_event", ["literature-monitor-pipeline-complete",
                                         "literature-monitor-save-failed"])
def test_terminal_then_late_pipeline_serializes_both_cleanups(settlement_order, late_event):
    """Reproduce overlapping cleanup: neither the first nor second completion releases early."""
    program = r"""
const SETTLEMENT_ORDER = """ + repr(settlement_order) + r""";
const LATE_EVENT = """ + repr(late_event) + r""";
const operations = [], idle = [], native = [];
let executing = 0, maximumConcurrent = 0;
const receipt = deferred();
runtime._deliverResult = async () => true;
runtime._reportNativeSaveSettled = async task => {
  native.push(task.requestID);
  await receipt.promise;
  return true;
};
runtime._returnToApp = (task, close) => {
  executing++;
  maximumConcurrent = Math.max(maximumConcurrent, executing);
  const done = deferred();
  operations.push({done, close});
  return done.promise.finally(() => {executing--;});
};
runtime._reportImportBrowserIdle = async task => {
  idle.push({task, active:runtime._activeTask, pending:executing});
  return true;
};
(async () => {
  const task = makeTask();
  runtime._activeTask = task;
  runtime._finishTask(task, "UNCONFIRMED");
  await tick();
  assert.equal(operations.length, 1, "first terminal cleanup starts");
  assert.equal(idle.length, 0);
  const callback = runtime._handleRuntimeMessage(pipelineMessage(task, LATE_EVENT), sender);
  await tick();
  assert.equal(operations.length, 1, "late callback must not overlap first cleanup");
  assert.equal(maximumConcurrent, 1);
  assert.equal(runtime._activeTask, task);
  assert.equal(idle.length, 0);

  if (SETTLEMENT_ORDER === "cleanup_first") {
    operations[0].done.resolve();
    await tick();
    // Failure has no native-settled request to wait for. Its second cleanup
    // may start once the first resolves, but never concurrently.
    assert.equal(operations.length,
      LATE_EVENT === "literature-monitor-pipeline-complete" ? 1 : 2);
    assert.equal(idle.length, 0, "first cleanup is insufficient");
    assert.equal(runtime._activeTask, task);
    receipt.resolve();
  } else {
    receipt.resolve();
    await tick();
    assert.equal(operations.length, 1, "second cleanup must wait for first");
    assert.equal(idle.length, 0);
    operations[0].done.resolve();
  }
  await callback;
  await tick();
  assert.equal(operations.length, 2, "late pipeline queues exactly one more cleanup");
  assert.equal(maximumConcurrent, 1, "two browser cleanups overlapped");
  assert.equal(idle.length, 0, "idle sent before last cleanup");
  assert.equal(runtime._activeTask, task);
  operations[1].done.resolve();
  await tick();
  assert.equal(executing, 0);
  assert.equal(maximumConcurrent, 1);
  assert.equal(idle.length, 1, "only one browser-idle receipt");
  assert.equal(idle[0].active, null);
  assert.equal(idle[0].pending, 0);
  assert.equal(runtime._activeTask, null);
  assert.equal(native.length, LATE_EVENT === "literature-monitor-pipeline-complete" ? 1 : 0);
  assert.equal(await runtime._handleRuntimeMessage(pipelineMessage(task,LATE_EVENT),sender), false);
  await tick();
  assert.equal(operations.length, 2);
  assert.equal(idle.length, 1);
})().catch(error => {console.error(error); process.exitCode=1;});
"""
    _run_connector_vm(program)


@pytest.mark.parametrize("scenario", [
    "confirmed", "native_failure", "timeout_unsettled",
    "cleanup_failure", "native_save_retry",
])
def test_import_terminal_safety_boundaries(scenario):
    program = r"""
const SCENARIO = """ + repr(scenario) + r""";
const task = makeTask(), operations=[], idle=[], native=[];
runtime._activeTask = task;
runtime._deliverResult = async () => true;
runtime._reportImportBrowserIdle = async () => {idle.push(true); return true;};
runtime._reportNativeSaveSettled = async () => {native.push(true); return true;};
runtime._returnToApp = (task, close) => {
  const complete = deferred();
  operations.push({complete,close});
  return complete.promise;
};
(async () => {
  if (SCENARIO === "confirmed") {
    task.parentConfirmed = true;
    await runtime._handleRuntimeMessage(pipelineMessage(task), sender);
    assert.equal(task.terminalOutcome, "CONFIRMED");
    assert.deepEqual(native, [true]);
  } else if (SCENARIO === "native_failure") {
    await runtime._handleRuntimeMessage(
      pipelineMessage(task, "literature-monitor-save-failed"), sender);
    assert.equal(task.terminalOutcome, "UNCONFIRMED");
    assert.equal(task.failureStage, "native_save");
    assert.equal(native.length, 0, "failure is not pipeline-settled proof");
  } else {
    runtime._finishTask(task, "UNCONFIRMED",
      SCENARIO === "native_save_retry" ? "native_save" : null);
  }
  await tick();
  assert.equal(operations.length, 1);
  assert.equal(idle.length, 0);
  if (SCENARIO === "cleanup_failure") {
    operations[0].complete.reject(new Error("Cannot verify cleanup"));
  } else {
    operations[0].complete.resolve();
  }
  await tick();
  if (SCENARIO === "confirmed" || SCENARIO === "native_failure"
      || SCENARIO === "native_save_retry") {
    assert.equal(idle.length, 1);
    assert.equal(runtime._activeTask, null);
  } else {
    assert.equal(idle.length, 0, "unfinished or unverified browser operation released");
    assert.equal(runtime._activeTask, task);
  }
  assert.equal(native.length, SCENARIO === "confirmed" ? 1 : 0);
  if (SCENARIO === "confirmed") assert.equal(operations[0].close, true);
})().catch(error => {console.error(error); process.exitCode=1;});
"""
    _run_connector_vm(program)


def test_real_browser_cleanup_rejection_must_not_prove_idle():
    """An ambiguous Chrome API failure is not evidence the browser is idle."""
    program = r"""
const task = makeTask(), idle = [], ops = [];
task.pipelineEnded = true;
task.pipelineComplete = true;
task.windowID = 22;
context.browser.tabs = {
  get: async id => ({id,incognito:false,windowId:22,
                      url:id===33 ? "http://127.0.0.1:8000/kept" : "https://doi.org/10.1000/example"}),
  remove: async id => {
    ops.push("remove");
    throw new Error("browser removal unknown");
  },
  update: async () => {ops.push("activate");},
};
context.browser.webNavigation = {
  getFrame: async () => ({documentId:"doc"}),
};
runtime._activeTask = task;
runtime._deliverResult = async () => true;
runtime._reportImportBrowserIdle = async () => {idle.push(true);};
(async () => {
  runtime._finishTask(task,"CONFIRMED");
  await tick();
  assert.equal(idle.length, 0);
  assert.equal(runtime._activeTask, task);
  assert.equal(task.cleanupFailed, true);
  assert.equal(task.browserIdleReporting, undefined);
  assert.deepEqual(ops, ["remove"]);
})().catch(error => {console.error(error);process.exitCode=1;});
"""
    _run_connector_vm(program)
