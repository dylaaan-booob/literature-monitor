"""A2 Pack Export, draft guard, saved Service Open and name-first Import Preview."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

import literature_monitor.web.app as web
from literature_monitor.application.ai_mapping_pack import (
    AI_MAPPING_PACK_FILENAME, MappingPackError, build_ai_mapping_pack, CarsiResource,
)
from literature_monitor.application.full_markdown_import import preview_full_markdown
from literature_monitor.config import (
    AccessService, parse_settings_list_text, render_settings_list_text,
)
from literature_monitor.web.app import create_app
from test_access_mapping_a4 import prepared, stamp
from test_full_markdown_import_a5 import preview
from test_web_run_settings import SETTINGS_NODE_DOM, SettingsDOM, browser_settings_submission


def _fake_pack(path):
    return build_ai_mapping_pack(
        path.read_bytes().decode("utf-8"),
        (CarsiResource("Springer Nature", "Springer Nature", "https://link.springer.com"),),
    )


def _patch_pack(monkeypatch, calls):
    def build(path):
        calls.append(path)
        return _fake_pack(path)
    monkeypatch.setattr(web, "generate_ai_mapping_pack", build)


def test_pack_visible_only_in_mapping_view_and_guidance(tmp_path):
    config, path, service, spare = prepared(tmp_path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        page = client.get("/settings").text
    mapping = page.split('id="settings-access-mapping"', 1)[1].split("</section>", 1)[0]
    assert 'data-export-mapping-pack' in mapping
    assert page.count('data-export-mapping-pack') == 1
    assert 'data-export-markdown' in page
    assert "complete saved list.md" in mapping
    assert "personal or sensitive" in mapping
    assert "Pack itself cannot be imported" in mapping
    assert "Import Markdown → Preview Changes → Confirm Import" in mapping
    assert "production" in mapping and "exact matching" in mapping
    assert "stable ID" in mapping and "Publisher associations" in mapping


def test_download_is_csrf_protected_and_pack_is_an_attachment(tmp_path, monkeypatch):
    config, path, service, spare = prepared(tmp_path)
    calls = []
    _patch_pack(monkeypatch, calls)
    before = stamp(path), stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        page = client.get("/settings")
        data = browser_settings_submission(page.text)
        assert client.post("/settings/export-ai-mapping-pack", data={}).status_code == 403
        assert not calls
        result = client.post("/settings/export-ai-mapping-pack", data=data)
        assert result.status_code == 200
        assert result.headers["cache-control"] == "no-store"
        assert result.headers["content-type"].startswith("text/markdown")
        assert f'filename="{AI_MAPPING_PACK_FILENAME}"' in result.headers["content-disposition"]
        assert result.text.splitlines()[0] == "<!-- literature-monitor-file-type: ai-mapping-pack/v1 -->"
        assert path.read_bytes() in result.content
        assert "CARSI official public resource directory" in result.text
        assert b"monitor.yaml" not in result.content
        assert calls == [path]
        ordinary = client.post("/settings/export", data=data)
        assert ordinary.content == before[0][0]
        assert 'filename="list.md"' in ordinary.headers["content-disposition"]
        assert calls == [path]
    assert (stamp(path), stamp(config)) == before


@pytest.mark.parametrize("kind,route", [
    ("pack", "/settings/export-ai-mapping-pack"), ("markdown", "/settings/export"),
])
def test_dirty_draft_guard_save_discard_cancel_are_distinct(tmp_path, monkeypatch, kind, route):
    config, path, service, spare = prepared(tmp_path)
    calls = []
    _patch_pack(monkeypatch, calls)
    before = stamp(path), stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        clean = browser_settings_submission(client.get("/settings").text)
        dirty = dict(clean)
        dirty["keyword_expression"] = ["unsaved_keyword"]
        dirty["service_name"] = ["Unsaved Service", *clean["service_name"][1:]]
        guard = client.post(route, data=dirty)
        assert guard.status_code == 200 and "data-export-guard" in guard.text
        assert f'data-export-kind="{kind}"' in guard.text
        assert "unsaved_keyword" in guard.text and "Unsaved Service" in guard.text
        assert ("AI Mapping Pack" in guard.text.split("data-export-guard", 1)[1]) is (kind == "pack")
        assert calls == []  # No public network before explicit discard.
        # Cancel is local-only; no request and no draft loss.
        assert 'data-cancel-markdown-guard' in guard.text
        assert (stamp(path), stamp(config)) == before
        exported = client.post(route, data=dict(dirty, export_discard=["yes"]))
        assert exported.headers["content-disposition"].endswith(
            f'filename="{AI_MAPPING_PACK_FILENAME if kind == "pack" else "list.md"}"'
        )
        assert len(calls) == (1 if kind == "pack" else 0)
        assert (stamp(path), stamp(config)) == before
        # Save remains the existing Settings Save; its result is never overwritten by Export.
        saved = client.post("/settings/save", data=dirty)
        assert saved.headers.get("HX-Trigger") == "settingsSaved"
        assert b"unsaved_keyword" in config.read_bytes()


def test_pack_error_retains_draft_and_never_writes(tmp_path, monkeypatch):
    config, path, service, spare = prepared(tmp_path)
    original = stamp(path), stamp(config)
    calls = []
    def fail(_):
        calls.append(True)
        raise MappingPackError("CARSI directory request timed out")
    monkeypatch.setattr(web, "generate_ai_mapping_pack", fail)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        dirty = browser_settings_submission(client.get("/settings").text)
        dirty["keyword_expression"] = ["private_unsaved_draft"]
        guard = client.post("/settings/export-ai-mapping-pack", data=dirty)
        assert "data-export-guard" in guard.text and calls == []
        response = client.post("/settings/export-ai-mapping-pack",
                               data=dict(dirty, export_discard=["yes"]))
        assert "AI Mapping Pack export blocked" in response.text
        assert "timed out" in response.text
        assert "private_unsaved_draft" in response.text
        assert "attachment" not in response.headers
        assert calls == [True]
    assert (stamp(path), stamp(config)) == original


def test_service_open_requires_valid_saved_url_and_is_not_publisher_open(tmp_path):
    config, path, service, spare = prepared(tmp_path)
    source = path.read_text()
    journals, publishers, services = parse_settings_list_text(source, path=path)
    first = services[0].model_copy(update={"access_url": "https://valid.example/service"})
    stored = render_settings_list_text(source, journals, publishers,
                                       access_services=(first, services[1]), path=path)
    path.write_text(stored, encoding="utf-8")
    before = stamp(path), stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        page = client.get("/settings").text
        assert page.count("data-service-open") == 1
        assert re.search(r'<a href="https://valid.example/service" data-service-open'
                         r'[^>]*target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer"', page)
        assert 'data-publisher-open' in page
        assert 'data-access-url="https://valid.example/service"' in page
        renamed = browser_settings_submission(page)
        renamed["service_name"] = ["Renamed but same identity", *renamed["service_name"][1:]]
        name_preview = client.post("/settings/import/preview", data=renamed)
        assert 'data-access-url="https://valid.example/service"' in name_preview.text
        assert service.id in name_preview.text
        assert 'Renamed but same identity' in name_preview.text
        assert 'data-publisher-open' in name_preview.text
        data = browser_settings_submission(page)
        data["service_access_url"][0] = "https://draft.example/not-saved"
        changed = client.post("/settings/import/preview", data=data)
        assert "data-service-open" not in changed.text
        assert "https://draft.example/not-saved" in changed.text
        # Draft URL is not a server-authorized Open destination.
        data["service_access_url"][0] = "http://127.0.0.1/private"
        invalid = client.post("/settings/import/preview", data=data)
        assert "data-service-open" not in invalid.text
    assert (stamp(path), stamp(config)) == before


def test_empty_and_invalid_disk_service_urls_never_create_open(tmp_path):
    config, path, service, spare = prepared(tmp_path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        base = client.get("/settings").text
        assert base.count("data-service-open") == 1  # The other fixture Service has a saved URL.
        data = browser_settings_submission(base)
        data["service_access_url"][1] = "https://draft-only.example"
        draft = client.post("/settings/import/preview", data=data)
        assert draft.text.count("data-service-open") == 1
        assert 'data-access-url="https://draft-only.example"' not in draft.text
    # Invalid disk URL cannot grant link authority.
    path.write_text(path.read_text().replace(service.access_url, "http://127.0.0.1/private"),
                    encoding="utf-8")
    with TestClient(create_app(config), base_url="http://localhost") as client:
        assert "data-service-open" not in client.get("/settings").text


@pytest.mark.parametrize("scenario", [
    "unassigned_assign", "service_move", "unassign", "rename", "delete", "new_service",
])
def test_full_import_preview_prefers_publisher_and_service_names(tmp_path, scenario):
    config, path, first, second = prepared(tmp_path)
    original = path.read_text()
    journals, publishers, services = parse_settings_list_text(original, path=path)
    a, b = publishers
    assert a.name == "Publisher One"
    if scenario == "unassigned_assign":
        new_publishers = (a, b.model_copy(update={"access_service_id": second.id}))
        new_services = services
        expected = f"Publisher default Service: Publisher Two ({b.publisher_id}): Unassigned → {second.name} ({second.id})"
    elif scenario == "service_move":
        new_publishers = (a.model_copy(update={"access_service_id": second.id}), b)
        new_services = services
        expected = f"Publisher default Service: Publisher One ({a.publisher_id}): {first.name} ({first.id}) → {second.name} ({second.id})"
    elif scenario == "unassign":
        new_publishers = (a.model_copy(update={"access_service_id": None}), b)
        new_services = services
        expected = f"Publisher default Service: Publisher One ({a.publisher_id}): {first.name} ({first.id}) → Unassigned"
    elif scenario == "rename":
        new_publishers = publishers
        new_services = (first.model_copy(update={"name": "Renamed Service"}), second)
        expected = f"Publisher default Service renamed: Publisher One ({a.publisher_id}): {first.name} ({first.id}) → Renamed Service ({first.id})"
    elif scenario == "delete":
        new_publishers = (a.model_copy(update={"access_service_id": None}), b)
        new_services = (second,)
        expected = f"Service deletion affects Publisher: Publisher One ({a.publisher_id}): {first.name} ({first.id}) → Unassigned"
    else:
        new_publishers = publishers
        added = AccessService.create("New CARSI Service", "https://new.example")
        new_services = (*services, added)
        expected = f"Add Access Service: New CARSI Service ({added.id})"
    source = render_settings_list_text(original, journals, new_publishers,
                                       access_services=new_services, path=path)
    previewed = preview_full_markdown(path, source, filename="ai-edited-list.md")
    assert expected in previewed.changes
    if scenario == "rename":
        assert any("Access Service edit:" in entry and "Renamed Service" in entry for entry in previewed.changes)
    if scenario == "delete":
        assert any("Publisher default Service:" in entry and "Unassigned" in entry for entry in previewed.changes)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        submitted = preview(client, browser_settings_submission(client.get("/settings").text), source)
        assert expected in submitted.text
        assert "data-markdown-preview" in submitted.text
    assert path.read_text() == original


def test_export_browser_dom_guards_view_and_download_routing(tmp_path):
    """Exercise the actual app.js event handler with the existing Node DOM harness."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable")
    config, path, service, spare = prepared(tmp_path)
    before = stamp(path), stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        page = client.get("/settings").text
        source = client.get("/static/app.js").text
        dirty = browser_settings_submission(page)
        dirty["keyword_expression"] = ["unsaved"]
        pack_guard = client.post("/settings/export-ai-mapping-pack", data=dirty).text
        markdown_guard = client.post("/settings/export", data=dirty).text
    assert 'data-export-kind="pack"' in pack_guard
    assert 'data-export-kind="markdown"' in markdown_guard
    harness = SETTINGS_NODE_DOM.replace(
        'fetch: () => assert.fail("Organization must not request the server"),',
        'fetch: mockFetch, FormData: MockFormData, DOMParser: MockDOMParser,',
    )
    assert harness != SETTINGS_NODE_DOM
    preamble = r"""
const requests = [], downloads = [];
let mode = "guard", kind = "pack";
class MockFormData {
  constructor(form) {
    this.values = form.querySelectorAll("input, select, textarea").filter(input =>
      input.attrs.name && !input.closest("template") && (input.attrs.type !== "checkbox" || input.checked)
    ).map(input => [input.attrs.name, input.value]);
  }
  [Symbol.iterator]() {return this.values[Symbol.iterator]();}
  set(name, value) {this.values = this.values.filter(([key]) => key !== name); this.values.push([name, value]);}
}
class MockDOMParser {
  parseFromString(source, _type) {
    const map = {packGuard: PACK_GUARD, markdownGuard: MARKDOWN_GUARD, saved: PAGE};
    return build(map[source]);
  }
}
function mockFetch(route, options) {
  if (route === "/settings") return Promise.resolve({ok: true, text: async () => "saved"});
  const discard = options.body.values.some(([key, value]) => key === "export_discard" && value === "yes");
  requests.push({route, discard});
  if (mode === "fail") return Promise.reject(new Error("offline"));
  if (mode === "edit-race") {
    document.getElementById("settings-editor").querySelector('[name="keyword_expression"]').value = "edited mid-request";
  }
  if ((!discard && mode !== "clean") || mode === "edit-race") return Promise.resolve({
    ok:true, headers: {get: () => null},
    text: async () => kind === "pack" ? "packGuard" : "markdownGuard"
  });
  return Promise.resolve({
    ok:true, headers: {get: () => 'attachment; filename="server-file.md"'},
    blob: async () => ({})
  });
}
"""
    program = r"""
(async () => {
const root = () => document.getElementById("settings-editor");
const button = s => root().querySelector(s);
const click = s => { assert.ok(button(s), s); handlers.click({target: button(s)}); };
const tick = async () => {await new Promise(setImmediate); await new Promise(setImmediate);};
Element.prototype.replaceWith = function(replacement) {
  const parent = this.parentNode, pos = parent.children.indexOf(this);
  parent.children[pos] = replacement; replacement.parentNode = parent; this.parentNode = null;
};
Element.prototype.click = function() {downloads.push(this.download);};
document.body.append = function(node) {return node;};
URL.createObjectURL = () => "blob:fake";
URL.revokeObjectURL = () => {};
const current = root().querySelector('[name="keyword_expression"]');
current.value = "unsaved"; handlers.input({target: current});
click('[data-settings-view-button="mapping"]');
assert.equal(root().querySelector('[data-settings-view="mapping"]').hidden, false);
click("[data-export-mapping-pack]"); await tick();
assert.equal(requests.at(-1).route, "/settings/export-ai-mapping-pack");
assert.equal(requests.at(-1).discard, false);
assert.equal(button('[data-export-guard]').dataset.exportKind, "pack");
assert.equal(button('[name="keyword_expression"]').value, "unsaved");
click("[data-cancel-markdown-guard]");
assert.equal(button("[data-export-guard]"), null);
assert.equal(button('[name="keyword_expression"]').value, "unsaved");
click("[data-export-mapping-pack]"); await tick();
click("[data-export-discard]"); await tick();
assert.equal(requests.at(-1).route, "/settings/export-ai-mapping-pack");
assert.equal(requests.at(-1).discard, true);
assert.equal(downloads.at(-1), "literature-monitor-ai-mapping-pack.md");
assert.equal(button('[data-settings-view="mapping"]').hidden, false);
assert.equal(button('[name="keyword_expression"]').value !== "unsaved", true);
assert.equal(document.documentElement.dataset.settingsDirty, "false");
kind = "markdown"; mode = "clean";
click("[data-export-markdown]"); await tick();
assert.equal(requests.at(-1).route, "/settings/export");
assert.equal(downloads.at(-1), "list.md");
const serviceLink = button("[data-service-open]");
const url = serviceLink.closest("[data-service-row]").querySelector('[name="service_access_url"]');
url.value = "https://unsaved.example";
handlers.input({target: url});
assert.equal(serviceLink.hidden, true);
assert.equal(document.documentElement.dataset.settingsDirty, "true");
url.value = serviceLink.dataset.accessUrl;
handlers.input({target:url});
assert.equal(serviceLink.hidden, false);
mode = "fail"; kind = "pack";
click("[data-export-mapping-pack]"); await tick();
assert.ok(button("[data-export-error]").textContent.includes("offline"));
assert.equal(button('[data-settings-view="mapping"]').hidden, false);
const field = button('[name="keyword_expression"]');
field.value = "before race"; handlers.input({target:field});
mode = "edit-race";
click("[data-export-mapping-pack]"); await tick();
assert.equal(button('[name="keyword_expression"]').value, "edited mid-request");
assert.equal(button("[data-export-guard]"), null);
assert.ok(button("[data-export-error]").textContent.includes("Settings changed"));
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    source_code = (
        "const PAGE=" + json.dumps(SettingsDOM(page).root)
        + ", PACK_GUARD=" + json.dumps(SettingsDOM(pack_guard).root)
        + ", MARKDOWN_GUARD=" + json.dumps(SettingsDOM(markdown_guard).root)
        + ", SOURCE=" + json.dumps(source) + ";\n" + preamble + harness + program
    )
    result = subprocess.run([node, "-e", source_code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (stamp(path), stamp(config)) == before


@pytest.mark.parametrize("kind", ["markdown", "pack"])
@pytest.mark.parametrize("stage", [
    "blob", "settings_get", "settings_text", "guard_text", "editor_replaced",
    "normal_discard", "normal_download",
])
def test_export_delayed_response_never_overwrites_new_draft(tmp_path, kind, stage):
    """Pause each awaited response; late UI work must respect the current editor."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable")
    config, path, _, _ = prepared(tmp_path)
    before = stamp(path), stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        page = client.get("/settings").text
        source = client.get("/static/app.js").text
        dirty = browser_settings_submission(page)
        dirty["keyword_expression"] = ["unsaved"]
        route = "/settings/export-ai-mapping-pack" if kind == "pack" else "/settings/export"
        guard = client.post(route, data=dirty).text
        assert f'data-export-kind="{kind}"' in guard
    harness = SETTINGS_NODE_DOM.replace(
        'fetch: () => assert.fail("Organization must not request the server"),',
        'fetch: mockFetch, FormData: MockFormData, DOMParser: MockDOMParser,',
    ).replace("let dom = build(PAGE)", "let dom = build(INITIAL)")
    assert harness != SETTINGS_NODE_DOM
    script = r"""
const calls = [], downloads = [];
let pendingResolve;
const pending = new Promise(resolve => {pendingResolve = resolve;});
class MockFormData {
  constructor(form) {
    this.values = form.querySelectorAll("input, select, textarea").filter(input =>
      input.attrs.name && !input.closest("template") && (input.attrs.type !== "checkbox" || input.checked)
    ).map(input => [input.attrs.name, input.value]);
  }
  [Symbol.iterator]() {return this.values[Symbol.iterator]();}
  set(name, value) {this.values = this.values.filter(([key]) => key !== name); this.values.push([name, value]);}
}
class MockDOMParser {
  parseFromString(value) {return build(value === "saved" ? PAGE : GUARD);}
}
function mockFetch(url, options) {
  calls.push(url);
  if (url === "/settings") {
    if (STAGE === "settings_get") return pending;
    return Promise.resolve({ok: true,
      text: () => STAGE === "settings_text" ? pending : Promise.resolve("saved")});
  }
  if (STAGE === "guard_text" || STAGE === "editor_replaced") {
    return Promise.resolve({ok: true, headers: {get: () => null},
      text: () => pending});
  }
  return Promise.resolve({ok: true,
    headers: {get: () => 'attachment; filename="response.md"'},
    blob: () => STAGE === "blob" ? pending : Promise.resolve({})});
}
"""
    program = r"""
(async () => {
const editor = () => document.getElementById("settings-editor");
const field = () => editor().querySelector('[name="keyword_expression"]');
const click = selector => {
  const target = editor().querySelector(selector);
  assert.ok(target, selector);
  handlers.click({target});
};
const tick = async () => {await new Promise(setImmediate); await new Promise(setImmediate);};
const edit = value => {field().value = value; handlers.input({target: field()});};
Element.prototype.replaceWith = function(replacement) {
  const parent = this.parentNode, index = parent.children.indexOf(this);
  parent.children[index] = replacement;
  replacement.parentNode = parent;
  this.parentNode = null;
};
Element.prototype.click = function() {downloads.push(this.download);};
document.body.append = () => {};
URL.createObjectURL = () => "blob:fake";
URL.revokeObjectURL = () => {};
click('[data-settings-view-button="mapping"]');
const original = editor();
const originalValue = field().value;
const discard = !["guard_text", "editor_replaced", "normal_download"].includes(STAGE);
if (!discard) {
  if (STAGE !== "normal_download") edit("original uncommitted draft");
  click(KIND === "pack" ? "[data-export-mapping-pack]" : "[data-export-markdown]");
} else {
  assert.equal(editor().querySelector("[data-export-guard]").dataset.exportKind, KIND);
  click("[data-export-discard]");
}
await tick();
assert.equal(calls[0], KIND === "pack" ? "/settings/export-ai-mapping-pack" : "/settings/export");
assert.equal(editor().querySelector('[data-settings-view="mapping"]').hidden, false);
const filename = KIND === "pack" ? "literature-monitor-ai-mapping-pack.md" : "list.md";
if (STAGE === "normal_discard") {
  assert.equal(downloads.at(-1), filename);
  assert.ok(calls.includes("/settings"));
  assert.notEqual(editor(), original);
  assert.equal(field().value !== "unsaved", true);
  assert.equal(document.documentElement.dataset.settingsDirty, "false");
  assert.equal(editor().querySelector('[data-settings-view="mapping"]').hidden, false);
  return;
}
if (STAGE === "normal_download") {
  assert.equal(downloads.at(-1), filename);
  assert.equal(editor(), original);
  assert.equal(field().value, originalValue);
  assert.equal(document.documentElement.dataset.settingsDirty, "false");
  return;
}
if (STAGE === "blob") assert.equal(downloads.length, 0);
if (STAGE === "settings_get" || STAGE === "settings_text") {
  assert.equal(downloads.at(-1), filename);
  assert.ok(calls.includes("/settings"));
}
if (STAGE === "editor_replaced") {
  dom = build(PAGE); // A different Settings action replaced the editor while awaiting response.text().
  click('[data-settings-view-button="mapping"]');
  edit("newer editor draft");
  assert.notEqual(editor(), original);
} else {
  edit("later unsaved edit");
  assert.equal(editor(), original);
}
const current = editor();
const changedValue = field().value;
const changedDirty = document.documentElement.dataset.settingsDirty;
pendingResolve(STAGE === "settings_get"
  ? {ok:true, text: async () => "saved"}
  : (STAGE === "settings_text" || STAGE === "guard_text" || STAGE === "editor_replaced"
    ? (STAGE === "settings_text" ? "saved" : "guard")
    : {}));
await tick();
assert.equal(editor(), current, "late asynchronous response replaced the user's active editor");
assert.equal(field().value, changedValue);
assert.equal(document.documentElement.dataset.settingsDirty, changedDirty);
assert.equal(editor().querySelector('[data-settings-view="mapping"]').hidden, false);
const notice = editor().querySelector("[data-export-error]");
assert.equal(notice.hidden, false);
assert.match(notice.textContent,
  STAGE === "editor_replaced" ? /Settings editor was replaced/ : /Settings changed during export/);
if (STAGE === "blob") assert.equal(downloads.length, 0);
else if (STAGE === "settings_get" || STAGE === "settings_text") {
  assert.equal(downloads.length, 1);
}
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    initial = guard if stage not in ("guard_text", "editor_replaced", "normal_download") else page
    executable = (
        "const INITIAL=" + json.dumps(SettingsDOM(initial).root)
        + ", PAGE=" + json.dumps(SettingsDOM(page).root)
        + ", GUARD=" + json.dumps(SettingsDOM(guard).root)
        + ", STAGE=" + json.dumps(stage)
        + ", KIND=" + json.dumps(kind)
        + ", SOURCE=" + json.dumps(source) + ";\n"
        + script + harness + program
    )
    result = subprocess.run([node, "-e", executable], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (stamp(path), stamp(config)) == before
