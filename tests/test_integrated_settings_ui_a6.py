"""A6 integrated Settings: one draft across Overview, Groups, Mapping and A5 actions."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from literature_monitor.application.settings import load_settings
from literature_monitor.web.app import create_app
from test_access_mapping_a4 import prepared, stamp
from test_full_markdown_import_a5 import candidate, preview
from test_access_service_schema import LEGACY, monitor
from test_web_run_settings import SETTINGS_NODE_DOM, SettingsDOM, browser_settings_submission


def node_program(page: str, script: str, instructions: str, **other_pages) -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node runtime required for Settings executable DOM")
    prefix = "const PAGE=" + json.dumps(SettingsDOM(page).root)
    for name, html in other_pages.items():
        prefix += ", " + name + "=" + json.dumps(SettingsDOM(html).root)
    prefix += ", SOURCE=" + json.dumps(script) + ";\n"
    result = subprocess.run([node, "-e", prefix + SETTINGS_NODE_DOM + instructions],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_initial_overview_is_read_only_and_has_one_form(tmp_path):
    config, path, service, spare = prepared(tmp_path)
    before = stamp(path), stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as browser:
        page = browser.get("/settings").text
        css = browser.get("/static/app.css").text
    root = SettingsDOM(page).root
    assert len([item for item in root["children"] if isinstance(item, dict)]) > 0
    assert 'id="settings-overview"' in page
    assert 'data-settings-view="groups"' in page and 'data-settings-view="mapping"' in page
    assert re.search(r'<section[^>]+id="settings-journal-groups"[^>]+hidden', page)
    assert re.search(r'<section[^>]+id="settings-access-mapping"[^>]+hidden', page)
    for label in ("Journals", "Groups", "Ungrouped Journals", "Publishers",
                  "Access Services", "Unassigned Publishers"):
        assert label in page
    assert service.id in page and spare.id in page
    assert 'data-settings-view-button="overview"' in page
    assert page.count('<form id="settings-form"') == 1
    assert page.split('<form id="settings-form"', 1)[1].split('</form>', 1)[0].count('type="submit"') == 1
    assert '/settings/import/preview' in page and '/settings/reload' in page
    assert 'data-export-markdown' in page
    assert '[data-settings-view][hidden]' in css
    assert '@media (max-width: 760px)' in css
    assert 'grid-template-columns: repeat(2, minmax(0, 1fr))' in css
    assert "role=\"status\"" in page and 'aria-pressed="true"' in page
    assert (stamp(path), stamp(config)) == before


NODE_EDIT_TWO_VIEWS = r"""
const editor = document.getElementById("settings-editor");
const click = selector => handlers.click({target: editor.querySelector(selector)});
const field = name => editor.querySelector('[name="' + name + '"]');
const overview = editor.querySelector("[data-settings-overview]");
const groups = editor.querySelector('[data-settings-view="groups"]');
const mapping = editor.querySelector('[data-settings-view="mapping"]');
assert.equal(overview.hidden, false);
assert.equal(groups.hidden, true);
assert.equal(mapping.hidden, true);
const oldJournalRevision = field("journal_revision_digest").value;
const oldMonitorRevision = field("monitor_revision_digest").value;
field("name").value = "A6 unsaved monitor";
handlers.input({target: field("name")});
field("institution_name").value = "University X";
handlers.input({target: field("institution_name")});
click('[data-settings-view-button="groups"]');
assert.equal(groups.hidden, false);
assert.equal(mapping.hidden, true);
assert.equal(overview.hidden, true);
const journal = editor.querySelectorAll(".journal-row").find(row =>
  row.querySelector('[name="journal_name"]').value === "Biometrics");
const originalJournalGroup = journal.querySelector('[name="journal_group"]').value;
journal.querySelector('[name="journal_group"]').value = "Other";
handlers.change({target: journal.querySelector('[name="journal_group"]')});
assert.equal(journal.querySelector('[name="journal_group"]').value, "Other");
click('[data-settings-view-button="mapping"]');
assert.equal(mapping.hidden, false);
const publisher = editor.querySelectorAll("[data-publisher-row]").find(row =>
  row.querySelector('[name="publisher_id"]').value.endsWith("/P1"));
const url = publisher.querySelector('[name="publisher_access_url"]');
url.value = "https://a6.example/manual";
handlers.input({target:url});
const second = editor.querySelectorAll("[data-service-row]")[1];
const serviceId = second.querySelector('[name="service_id"]').value;
const assign = publisher.querySelector('[name="publisher_access_service_id"]');
assign.value = serviceId;
handlers.change({target:assign});
assert.equal(publisher.closest("[data-mapping-container]").dataset.serviceTarget, serviceId);
click('[data-settings-view-button="groups"]');
assert.equal(journal.querySelector('[name="journal_group"]').value, "Other");
assert.equal(field("name").value, "A6 unsaved monitor");
assert.equal(field("institution_name").value, "University X");
click('[data-settings-view-button="overview"]');
const details = overview.querySelectorAll("details");
assert.ok(details.some(d => d.querySelector("summary").textContent.startsWith("Other")
  && d.querySelector("ul").querySelectorAll("li").some(li => li.textContent.includes("Biometrics"))),
  "Biometrics must appear in the Other Group");
assert.ok(details.some(d => d.querySelector("ul").querySelectorAll("li")
  .some(li => li.textContent.includes("a6.example/manual"))), "Publisher URL must appear in Overview");
const expandedGroup = details.find(d => d.querySelector("summary").textContent.startsWith("Other"));
expandedGroup.open = true;
field("keyword_expression").value = "causal AND integration";
handlers.input({target:field("keyword_expression")});
assert.equal(overview.querySelectorAll("details").find(d =>
  d.querySelector("summary").textContent.startsWith("Other")).open, true);
assert.equal(document.documentElement.dataset.settingsDirty, "true");
assert.equal(field("journal_revision_digest").value, oldJournalRevision);
assert.equal(field("monitor_revision_digest").value, oldMonitorRevision);
assert.equal(editor.querySelectorAll('form[data-settings-form]').length, 1);
const unload = {prevented: false, preventDefault() {this.prevented = true;}};
windowHandlers.beforeunload(unload);
assert.equal(unload.prevented, true);
const data = {};
for (const input of editor.querySelectorAll("input, select, textarea")) {
  const key = input.attrs.name;
  if (!key || input.closest("template") || (input.attrs.type === "checkbox" && !input.checked)) continue;
  (data[key] ||= []).push(input.value);
}
console.log(JSON.stringify(data));
"""


def test_switching_views_retains_all_fields_and_one_save(tmp_path):
    config, path, service, spare = prepared(tmp_path)
    before = stamp(path), stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as browser:
        page = browser.get("/settings").text
        script = browser.get("/static/app.js").text
        output = node_program(page, script, NODE_EDIT_TWO_VIEWS)
        data = json.loads(output.splitlines()[-1])
        assert data["name"] == ["A6 unsaved monitor"]
        assert data["institution_name"] == ["University X"]
        assert data["journal_group"] == ["Other", "Other"]
        assert "https://a6.example/manual" in data["publisher_access_url"]
        assert dict(zip(data["publisher_order"], data["publisher_access_service_id"]))["0"] == spare.id
        assert (stamp(path), stamp(config)) == before
        saved = browser.post("/settings/save", data=data)
        assert saved.headers.get("HX-Trigger") == "settingsSaved", saved.text[:1400]
        state = load_settings(config).draft
        assert state.name == "A6 unsaved monitor"
        assert state.keyword_expression == "causal AND integration"
        assert state.institution.name == "University X"
        assert state.journals[0].group == "Other"
        assert state.publishers[0].publisher_url == "https://a6.example/manual"
        assert state.publishers[0].access_service_id == spare.id
        assert stamp(path) != before[0] and stamp(config) != before[1]
        noop = stamp(path), stamp(config)
        response = browser.post("/settings/save", data=browser_settings_submission(saved.text))
        assert response.headers.get("HX-Trigger") == "settingsSaved"
        assert (stamp(path), stamp(config)) == noop


@pytest.mark.parametrize("change", ["group", "mapping", "noop", "conflict"])
def test_integrated_changed_file_only_and_conflict_preserves_attempt(tmp_path, change):
    config, path, service, spare = prepared(tmp_path)
    before = stamp(path), stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as browser:
        fields = browser_settings_submission(browser.get("/settings").text)
        if change == "group":
            fields["journal_group"][0] = "Other"
        elif change == "mapping":
            fields["publisher_access_url"][0] = "https://a6.example/new"
        elif change == "conflict":
            fields["name"] = ["Stale monitor"]
            path.write_text(path.read_text() + "\n## External\n\nEdit\n")
        result = browser.post("/settings/save", data=fields)
        if change == "conflict":
            assert "changed since this editor was opened" in result.text
            restored = browser_settings_submission(result.text)
            assert restored["name"] == ["Stale monitor"]
            assert restored["monitor_revision_digest"] == fields["monitor_revision_digest"]
            assert restored["journal_revision_digest"] == fields["journal_revision_digest"]
            assert stamp(config) == before[1]
            return
        assert result.headers.get("HX-Trigger") == "settingsSaved"
        assert stamp(config) == before[1]
        if change == "noop":
            assert stamp(path) == before[0]
        else:
            assert stamp(path) != before[0]


def test_keyboard_assign_and_view_invalidation_of_confirmation(tmp_path):
    config, path, service, spare = prepared(tmp_path)
    with TestClient(create_app(config), base_url="http://localhost") as browser:
        page = browser.get("/settings").text
        source = browser.get("/static/app.js").text
        plan = preview(browser, browser_settings_submission(page), candidate(path)).text
        assert 'data-markdown-preview' in plan
        instructions = r"""
const editor = () => document.getElementById("settings-editor");
const click = selector => handlers.click({target: editor().querySelector(selector)});
click('[data-settings-view-button="groups"]');
const journal = editor().querySelector(".journal-row");
journal.querySelector("[data-select-journal]").checked = true;
handlers.change({target: journal.querySelector("[data-select-journal]")});
editor().querySelector("[data-batch-group]").value = "Other";
click("[data-assign-selected]");
assert.equal(journal.querySelector('[name="journal_group"]').value, "Other");
click('[data-settings-view-button="mapping"]');
const publisher = editor().querySelector("[data-publisher-row]");
publisher.querySelector("[data-select-publisher]").checked = true;
handlers.change({target: publisher.querySelector("[data-select-publisher]")});
editor().querySelector("[data-batch-service]").value = "";
click("[data-assign-publishers]");
assert.equal(publisher.querySelector('[name="publisher_access_service_id"]').value, "");
assert.equal(document.documentElement.dataset.settingsDirty, "true");
dom = build(PREVIEW);
bodyHandlers["htmx:afterSwap"]({detail:{target:editor()}});
assert.ok(editor().querySelector("[data-markdown-preview]"));
click('[data-settings-view-button="overview"]');
assert.equal(editor().querySelector("[data-markdown-preview]"), null);
assert.equal(editor().querySelector('[name="markdown_import_proof"]'), null);
assert.equal(editor().querySelector('[name="settings_deletion_proof"]'), null);
"""
        node_program(page, source, instructions, PREVIEW=plan)


def test_dirty_indicator_tracks_reverted_monitor_edits_and_navigation_warning(tmp_path):
    config, path, _, _ = prepared(tmp_path)
    with TestClient(create_app(config), base_url="http://localhost") as browser:
        page = browser.get("/settings").text
        source = browser.get("/static/app.js").text
    script = r"""
const editor = document.getElementById("settings-editor");
const input = editor.querySelector('[name="name"]');
const original = input.value;
input.value = "Temporarily edited";
handlers.input({target:input});
assert.equal(document.documentElement.dataset.settingsDirty, "true");
input.value = original;
handlers.input({target:input});
assert.equal(document.documentElement.dataset.settingsDirty, "false");
const event = {prevented:false, preventDefault() {this.prevented = true;}};
windowHandlers.beforeunload(event);
assert.equal(event.prevented, false);
assert.equal(editor.querySelector("[data-settings-overview]").hidden, false);
"""
    node_program(page, source, script)


def test_empty_group_and_service_are_readable_in_overview(tmp_path):
    config, path, service, spare = prepared(tmp_path)
    old = stamp(path), stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as browser:
        page = browser.get("/settings").text
        source = browser.get("/static/app.js").text
    program = r"""
const editor = document.getElementById("settings-editor");
const click = selector => handlers.click({target:editor.querySelector(selector)});
click('[data-settings-view-button="groups"]');
editor.querySelector("[data-new-group]").value = "Unused Draft Group";
click("[data-create-group]");
click('[data-settings-view-button="overview"]');
const summaries = editor.querySelector("[data-overview-content]").querySelectorAll("summary")
  .map(element => element.textContent);
assert.ok(summaries.some(value => value.startsWith("Unused Draft Group · 0")));
assert.ok(summaries.some(value => value.startsWith("Unused · ") && value.endsWith(" · 0")));
assert.equal(document.documentElement.dataset.settingsDirty, "true");
assert.equal(editor.querySelector('[name="service_id"]').value.length > 0, true);
"""
    node_program(page, source, program)
    assert (stamp(path), stamp(config)) == old


def test_switching_views_invalidates_actual_signed_service_deletion(tmp_path):
    config, path, service, spare = prepared(tmp_path)
    before = stamp(path), stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as browser:
        page = browser.get("/settings").text
        script = browser.get("/static/app.js").text
        fields = browser_settings_submission(page)
        fields["service_id"] = [spare.id]
        fields["service_name"] = [spare.name]
        fields["service_access_url"] = [spare.access_url or ""]
        fields["publisher_access_service_id"][0] = ""
        response = browser.post("/settings/save", data=fields)
        assert 'data-settings-deletion-preview' in response.text
        assert (stamp(path), stamp(config)) == before
        js = r"""
const editor = document.getElementById("settings-editor");
assert.ok(editor.querySelector('[name="settings_deletion_proof"]'));
handlers.click({target:editor.querySelector('[data-settings-view-button="mapping"]')});
assert.equal(editor.querySelector('[name="settings_deletion_proof"]'), null);
assert.equal(editor.querySelector('[data-settings-deletion-preview]'), null);
assert.equal(editor.querySelector('[data-settings-view="mapping"]').hidden, false);
assert.equal(editor.querySelector('[name="service_id"]').value, SERVICE);
assert.equal(document.documentElement.dataset.settingsDirty, "false");
""".replace("SERVICE", json.dumps(spare.id))
        node_program(response.text, script, js)


def test_markdown_file_identity_survives_htmx_preview_in_mapping_view(tmp_path):
    config, path, _, _ = prepared(tmp_path)
    source_text = candidate(path)
    with TestClient(create_app(config), base_url="http://localhost") as browser:
        page = browser.get("/settings").text
        js = browser.get("/static/app.js").text
        response = preview(browser, browser_settings_submission(page), source_text)
        assert 'data-markdown-preview' in response.text
        program = r"""
(async () => {
const editor = () => document.getElementById("settings-editor");
const click = selector => handlers.click({target:editor().querySelector(selector)});
click('[data-settings-view-button="mapping"]');
const selected = editor().querySelector("[data-journal-import-file]");
const file = {name:"current.md", size:SOURCE_FILE.length,
  arrayBuffer: async () => new TextEncoder().encode(SOURCE_FILE).buffer};
selected.files = [file];
handlers.change({target:selected});
await new Promise(setImmediate);
assert.equal(editor().querySelector("[data-import-preview]").disabled, false);
bodyHandlers["htmx:beforeSwap"]({detail:{target:editor()}});
dom = build(PREVIEW);
bodyHandlers["htmx:afterSwap"]({detail:{target:editor()}});
assert.equal(editor().querySelector('[data-settings-view="mapping"]').hidden, false);
const button = editor().querySelector("[data-import-confirm]");
let requested = false;
bodyHandlers["htmx:confirm"]({detail:{elt:button,issueRequest(skip) {requested = skip;}},
  preventDefault() {}});
await new Promise(resolve => setTimeout(resolve, 75));
assert.equal(requested, true);
click('[data-settings-view-button="groups"]');
assert.equal(editor().querySelector("[data-markdown-preview]"), null);
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
        node = shutil.which("node")
        script = (
            "const PAGE=" + json.dumps(SettingsDOM(page).root)
            + ", PREVIEW=" + json.dumps(SettingsDOM(response.text).root)
            + ", SOURCE=" + json.dumps(js)
            + ", SOURCE_FILE=" + json.dumps(source_text) + ";\n"
            + SETTINGS_NODE_DOM + program
        )
        executed = subprocess.run([node, "-e", script], capture_output=True, text=True)
        assert executed.returncode == 0, executed.stderr


def test_htmx_view_and_scroll_restored_without_extra_form_or_reads(tmp_path):
    config, path, service, spare = prepared(tmp_path)
    with TestClient(create_app(config), base_url="http://localhost") as browser:
        page = browser.get("/settings").text
        script = browser.get("/static/app.js").text
        form = browser_settings_submission(page)
        form["service_name"][0] = "Unsaved Library"
        response = browser.post("/settings/import/preview", data=form).text
    program = r"""
const editor = () => document.getElementById("settings-editor");
const click = selector => handlers.click({target: editor().querySelector(selector)});
click('[data-settings-view-button="mapping"]');
const viewport = editor().querySelector("[data-publisher-viewport]");
viewport.scrollTop = 105;
const journalRevision = editor().querySelector('[name="journal_revision_digest"]').value;
bodyHandlers["htmx:beforeSwap"]({detail:{target:editor()}});
dom = build(RESPONSE);
bodyHandlers["htmx:afterSwap"]({detail:{target:editor()}});
assert.equal(editor().querySelector('[data-settings-view="mapping"]').hidden, false);
assert.equal(editor().querySelector("[data-publisher-viewport]").scrollTop, 105);
assert.equal(editor().querySelector('[name="journal_revision_digest"]').value, journalRevision);
assert.equal(editor().querySelector('[name="service_name"]').value, "Unsaved Library");
assert.equal(editor().querySelectorAll('form[data-settings-form]').length, 1);
click('[data-settings-view-button="groups"]');
assert.equal(editor().querySelector('[name="service_name"]').value, "Unsaved Library");
"""
    node_program(page, script, program, RESPONSE=response)


def test_a5_file_actions_and_legacy_upgrade_remain_accessible_from_overview(tmp_path):
    config, path, _, _ = prepared(tmp_path)
    with TestClient(create_app(config), base_url="http://localhost") as browser:
        page = browser.get("/settings").text
        data = browser_settings_submission(page)
        data["keyword_expression"] = ["unsaved query"]
        exported = browser.post("/settings/export", data=data)
        reload_response = browser.post("/settings/reload", data=data)
        assert 'data-export-guard' in exported.text
        assert 'data-reload-guard' in reload_response.text
        assert 'data-settings-overview' in exported.text
        assert 'data-settings-overview' in reload_response.text
        assert 'value="unsaved query"' in exported.text
        assert 'value="unsaved query"' in reload_response.text
        assert 'data-settings-view-button="mapping"' in exported.text
        assert '/settings/import/preview' in page
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    legacy_config = monitor(legacy_dir)
    legacy_path = legacy_dir / "list.md"
    legacy_path.write_text(LEGACY, encoding="utf-8")
    original = stamp(legacy_path)
    with TestClient(create_app(legacy_config), base_url="http://localhost") as browser:
        page = browser.get("/settings").text
        js = browser.get("/static/app.js").text
        assert 'data-settings-view-button="overview"' in page
        assert 'data-preview-access-upgrade' in page
        node_program(page, js, r"""
const editor = document.getElementById("settings-editor");
const details = editor.querySelector("[data-overview-content]").querySelectorAll("details");
assert.ok(details.some(item => item.querySelector("ul").querySelectorAll("li")
  .some(li => li.textContent.includes("ISSN-L"))));
""")
        data = browser_settings_submission(page)
        upgrade = browser.post("/settings/upgrade/preview", data=data)
        assert 'data-access-upgrade-plan' in upgrade.text
        assert stamp(legacy_path) == original


def test_narrow_css_and_markup_support_non_drag_controls(tmp_path):
    config, path, _, _ = prepared(tmp_path)
    with TestClient(create_app(config), base_url="http://localhost") as browser:
        page = browser.get("/settings").text
        css = browser.get("/static/app.css").text
    assert 'data-batch-group' in page and 'data-assign-selected' in page
    assert 'data-batch-service' in page and 'data-assign-publishers' in page
    assert 'data-group-drop' in page and 'data-mapping-container' in page
    assert 'data-confirm-service-delete' in page and 'data-confirm-delete' in page
    assert 'data-journal-cascade-preview' in page
    assert re.search(r"\[data-settings-view\]\[hidden\][^{]*\{\s*display:\s*none !important", css)
    mobile = css.split("@media (max-width: 760px)", 1)[1]
    assert "grid-template-columns: 1fr" in mobile
    assert "[data-publisher-viewport]" in mobile
    assert "max-height: none" in mobile
    assert "overflow: visible" in mobile
    assert "minmax(0, 1fr)" in mobile
