"""Kept-scoped access configuration, frozen queues and user-owned disclosure state."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from literature_monitor.application.ai_mapping_pack import CarsiResource, build_ai_mapping_pack
from literature_monitor.application.full_markdown_import import preview_full_markdown
from literature_monitor.application.settings import load_settings
from literature_monitor.config import AccessService, load_config, parse_settings_list_text, render_settings_list_text
from literature_monitor.web.app import create_app
from literature_monitor.web.preparation_coordinator import PreparationOutcome, PreparationPhase, read_saved_preparation_services
from preparation_helpers import prepared
from test_access_mapping_a4 import prepared as mapping_prepared, stamp
from test_batch_import import edit, paper
from test_full_markdown_import_a5 import preview, confirm
from test_preparation_coordinator_a3 import enabled, identity, INSTANCE
from test_web_run_settings import SettingsDOM, SETTINGS_NODE_DOM, browser_settings_submission, csrf_from_html


def save_mapping(path, draft, *, services=None, publishers=None):
    path.write_text(render_settings_list_text(path.read_text(), draft.journals,
        publishers if publishers is not None else draft.publishers,
        access_services=services if services is not None else draft.access_services, path=path))


def test_access_method_default_validation_old_markdown_and_explicit_preview(tmp_path):
    config, path, first, second = mapping_prepared(tmp_path)
    assert first.access_method == "carsi"
    with pytest.raises(ValidationError):
        AccessService(id=first.id, name=first.name, access_method="unassigned")
    draft = load_settings(config).draft
    manual = first.model_copy(update={"access_method": "manual"})
    save_mapping(path, draft, services=(manual, second))
    assert parse_settings_list_text(path.read_text(), path=path)[2][0] == manual
    # 旧三列输入仍默认为 CARSI；覆盖已有 Manual 必须在完整 Import Preview 明示。
    # Only rewrite the Access Services section, leaving Publisher/Journal headers intact.
    prefix, remaining = path.read_text().split("## Access Services", 1)
    section, following = remaining.split("\n## ", 1)
    section = section.replace(" | Access Method |", " |").replace("| --- | --- | --- | --- |", "| --- | --- | --- |")
    section = section.replace(" | manual |", " |").replace(" | carsi |", " |")
    old = prefix + "## Access Services" + section + "\n## " + following
    assert parse_settings_list_text(old, path=path)[2][0].access_method == "carsi"
    plan = preview_full_markdown(path, old, filename="list.md")
    assert any("Manual Login → CARSI" in change for change in plan.changes)
    assert load_settings(config).draft.access_services[0].access_method == "manual"
    pack = build_ai_mapping_pack(path.read_text(), (CarsiResource(first.name, "Vendor", "https://resource.example"),))
    assert path.read_text() in pack.markdown
    assert "never overwrite a user's manual classification" in pack.markdown


def test_settings_method_roundtrip_draft_export_and_confirmed_full_import(tmp_path):
    config, path, first, _ = mapping_prepared(tmp_path)
    monitor_before = stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        form = browser_settings_submission(client.get("/settings").text)
        assert form["service_access_method"] == ["carsi", "carsi"]
        original = stamp(path)
        invalid_method = {**form, "service_access_method": ["unassigned", "carsi"]}
        assert client.post("/settings/save", data=invalid_method).headers.get("HX-Trigger") != "settingsSaved"
        assert stamp(path) == original
        form["service_access_method"][0] = "manual"
        # An invalid unrelated field renders the entire unsaved Draft, including method.
        invalid = {**form, "window_days": ["invalid"]}
        rejected = client.post("/settings/save", data=invalid)
        assert browser_settings_submission(rejected.text)["service_access_method"][0] == "manual"
        assert stamp(path) == original
        saved = client.post("/settings/save", data=form)
        assert saved.headers.get("HX-Trigger") == "settingsSaved"
        current = load_settings(config).draft
        assert current.access_services[0].id == first.id
        assert current.access_services[0].access_method == "manual"
        data = browser_settings_submission(saved.text)
        assert client.post("/settings/export", data=data).content == path.read_bytes()
        incoming = render_settings_list_text(path.read_text(), current.journals, current.publishers,
            access_services=tuple(s.model_copy(update={"access_method": "carsi"}) for s in current.access_services), path=path)
        page = preview(client, data, incoming)
        assert "Manual Login → CARSI" in page.text
        assert load_settings(config).draft.access_services[0].access_method == "manual"
        done = confirm(client, page)
        assert done.headers.get("HX-Trigger") == "settingsSaved"
        assert load_settings(config).draft.access_services[0].access_method == "carsi"
    assert stamp(config) == monitor_before


def test_kept_filter_dedup_methods_and_freeze_until_next_explicit_prepare(tmp_path):
    config, path, first, second = prepared(tmp_path)
    draft = load_settings(config).draft
    manual = first.model_copy(update={"access_method": "manual"})
    unrelated = AccessService.create("Unrelated")
    save_mapping(path, draft, services=(unrelated, manual, second))
    workspace = load_config(config).output_dir
    assert workspace.is_relative_to(tmp_path)
    _, duplicate = paper(workspace, "older-kept", days=90)
    edit(duplicate, journal=draft.journals[0].name, journal_issns=[draft.journals[0].issn_l])
    originals = stamp(config), stamp(path), stamp(duplicate)
    assert read_saved_preparation_services(config) == (manual, second)
    capture, prep = enabled(config)
    started = prep.start(config)
    assert started.services == (second,) and started.manual_services == (manual,)
    cmd = prep.claim(INSTANCE)
    assert cmd.service_id == second.id and cmd.last_service
    assert prep.claim(INSTANCE) is None
    assert (stamp(config), stamp(path), stamp(duplicate)) == originals
    # 本次队列不随 Settings 或 Kept 编辑变化；下一次主动运行才读取新输入。
    save_mapping(path, draft, services=(first, second.model_copy(update={"access_method": "manual"})))
    edit(workspace / "Papers/preparation-kept-1.md", status="rejected")
    assert prep.snapshot().services == started.services
    assert prep.snapshot().manual_services == started.manual_services
    assert prep.result(**identity(cmd), outcome=PreparationOutcome.NEEDS_ATTENTION, reason="manual_action_required")
    assert prep.snapshot().phase is PreparationPhase.COMPLETED
    assert prep.snapshot().results[0].reason == "manual_action_required"
    owner = capture.reserve_browser_task("import")
    assert owner is not None
    assert capture.release_browser_task("import", owner)
    fresh = prep.start(config)
    assert fresh.services == (first,) and fresh.manual_services == ()
    assert fresh.task_id != started.task_id


def test_manual_and_unassigned_need_no_connector_or_browser_task(tmp_path):
    config, path, first, second = prepared(tmp_path)
    draft = load_settings(config).draft
    manual = first.model_copy(update={"access_method": "manual"})
    publishers = (draft.publishers[0], draft.publishers[1].model_copy(update={"access_service_id": None}))
    save_mapping(path, draft, services=(manual, second), publishers=publishers)
    before = stamp(config), stamp(path)
    app = create_app(config)
    capture, prep = app.state.capture_coordinator, app.state.preparation_coordinator
    owner = capture.reserve_browser_task("import")
    assert owner is not None
    started = prep.start(config)  # No heartbeat, no IdP and Import already owns the browser.
    assert started.phase is PreparationPhase.COMPLETED and started.task_id is None
    assert started.services == () and started.manual_services == (manual,)
    assert prep.claim(INSTANCE) is None
    assert capture.reserve_browser_task("preparation") is None
    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/?view=kept").text
        assert "No related CARSI Services" in page and "Manual Service Access" in page
        assert '<button type="submit" >Prepare CARSI Access</button>' in page
        assert 'data-preparation-connector' not in page
        assert 'Institution IdP is not configured' not in page
        assert first.access_url in page
        fragment = client.get("/fragments/preparation").text
        assert second.name not in fragment and 'data-preparation-stat="opened">0' in fragment
    assert capture.release_browser_task("import", owner)
    assert (stamp(config), stamp(path)) == before


def test_no_kept_papers_produce_no_services_or_task(tmp_path):
    config, _, _, _ = mapping_prepared(tmp_path)
    assert not (load_config(config).output_dir / "Papers").exists()
    app = create_app(config)
    assert read_saved_preparation_services(config) == ()
    snapshot = app.state.preparation_coordinator.start(config)
    assert snapshot.task_id is None and snapshot.services == snapshot.manual_services == ()
    assert app.state.capture_coordinator.reserve_browser_task("import") is not None


def test_manual_service_without_url_links_to_settings_without_carsi_task(tmp_path):
    config, path, first, second = prepared(tmp_path)
    draft = load_settings(config).draft
    save_mapping(path, draft, services=(first.model_copy(update={"access_method": "manual", "access_url": None}),
                                        second.model_copy(update={"access_method": "manual"})))
    app = create_app(config)
    with TestClient(app, base_url="http://localhost") as client:
        fragment = client.get("/fragments/preparation").text
        manual = fragment.split("<h2>Manual Service Access</h2>", 1)[1]
        assert first.name in manual and second.name in manual
        assert 'No saved Service Access URL — <a href="/settings">review Settings</a>' in manual
        assert 'data-disclosure-key="preparation-manual-services"' in manual
        assert 'data-preparation-count>0</span> of 0' in fragment
        token = csrf_from_html(client.get("/?view=kept").text)
        confirmed = client.post("/preparation/start", data={"csrf_token": token})
        assert confirmed.status_code == 200 and 'data-preparation-phase="completed"' in confirmed.text
        assert app.state.preparation_coordinator.snapshot().manual_services == load_settings(config).draft.access_services
    assert app.state.preparation_coordinator.snapshot().task_id is None


def test_executable_method_dirty_state_and_disclosures_survive_repeated_swaps(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for rendered DOM behavior")
    config, _, _, _ = mapping_prepared(tmp_path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        page = client.get("/settings").text
        source = client.get("/static/app.js").text
    program = SETTINGS_NODE_DOM + r'''
const editor = document.getElementById("settings-editor");
const method = editor.querySelector('[name="service_access_method"]');
assert.equal(method.value, "carsi");
method.value = "manual"; handlers.change({target: method});
assert.equal(document.documentElement.dataset.settingsDirty, "true");
method.value = "carsi"; handlers.change({target: method});
assert.equal(document.documentElement.dataset.settingsDirty, "false");
for (const key of ["preparation-manual-services", "preparation-results", "preparation-carsi-fallback", "import-results", "import-exclusions", "run-issues", "run-diagnostics"]) {
    let region = new Element("section", {id: "poll-region"});
    let panel = new Element("details", {"data-disclosure-key": key});
    region.append(panel); dom.append(region);
    for (const chosen of [true, false, true, false]) {
        panel.open = chosen;
        bodyHandlers["htmx:beforeSwap"]({detail: {target: region, xhr: {status: 200}}});
        const previous = region;
        region = new Element("section", {id: "poll-region"});
        previous.remove(); dom.append(region);
        panel = new Element("details", {"data-disclosure-key": key});
        panel.open = !chosen; region.append(panel);
        bodyHandlers["htmx:afterSwap"]({detail: {target: previous}});
        assert.equal(panel.open, chosen, key + " preserves both expanded and collapsed choices");
    }
    region.remove();
}
'''
    result = subprocess.run([node, "-e", "const PAGE=" + json.dumps(SettingsDOM(page).root) +
        ";const SOURCE=" + json.dumps(source) + ";" + program], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
