"""A4: signed Settings deletion authorization and executable Mapping DOM."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from literature_monitor.application.settings import (
    SettingsSaveOutcome, load_settings, plan_settings_deletions, save_settings,
)
from literature_monitor.config import (
    AccessService, JournalConfig, PublisherConfig, render_settings_list_text,
)
from literature_monitor.web.app import create_app
from test_access_service_schema import A, B, P1, P2, current_mapping_document
from test_web_run_settings import SETTINGS_NODE_DOM, SettingsDOM, browser_settings_submission


def stamp(path: Path):
    stat = path.stat()
    return path.read_bytes(), stat.st_ino, stat.st_mtime_ns


def test_service_deletion_preview_lists_only_publishers_actually_unassigned(tmp_path):
    config, path, service, spare = prepared(tmp_path)
    before = load_settings(config).draft
    moved = before.publishers[0].model_copy(update={"access_service_id": spare.id})
    candidate = replace(before, access_services=(spare,), publishers=(moved, before.publishers[1]))
    plan = plan_settings_deletions(before, candidate)
    assert [item.id for item in plan.services] == [service.id]
    assert plan.service_members == ((service.id, ()),)
    assert not plan.publishers
    assert save_settings(config, candidate).outcome is SettingsSaveOutcome.INVALID_DRAFT


def prepared(tmp_path):
    config, path, journal, publisher, service = current_mapping_document(tmp_path)
    spare = AccessService.create("Unused")
    journals = (journal, JournalConfig(name="Annals", issn_l=B, publisher_id=P2, group="Other"))
    publishers = (publisher, PublisherConfig(name="Publisher Two", publisher_id=P2,
                                            publisher_url="https://second.example/manual"))
    path.write_text(render_settings_list_text(
        path.read_text(), journals, publishers, access_services=(service, spare), path=path,
    ), encoding="utf-8")
    return config, path, service, spare


def submit(client, data):
    return client.post("/settings/save", data=data)


def confirm_submission(page, data):
    proof = re.search(r'name="settings_deletion_proof" value="([^"]+)"', page)
    assert proof, page
    result = dict(data)
    result["settings_deletion_proof"] = [proof.group(1)]
    result["confirm_settings_deletions"] = ["yes"]
    return result


def test_service_crud_and_mapping_changed_file_only(tmp_path):
    config, path, old, spare = prepared(tmp_path)
    monitor_before, list_before = stamp(config), stamp(path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        data = browser_settings_submission(client.get("/settings").text)
        # A new Service is stable even when its name/URL subsequently change.
        new = AccessService.create("Library", "https://library.example")
        data["service_id"].append(new.id)
        data["service_name"].append(new.name)
        data["service_access_url"].append(new.access_url)
        data["publisher_access_service_id"] = [new.id, new.id]
        data["journal_group"] = ["A4 Group", "Other"]
        saved = submit(client, data)
        assert saved.headers.get("HX-Trigger") == "settingsSaved", saved.text
        state = load_settings(config).draft
        assert [p.access_service_id for p in state.publishers] == [new.id, new.id]
        assert [j.group for j in state.journals] == ["A4 Group", "Other"]
        assert state.access_services[-1].id == new.id
        assert state.publishers[0].publisher_url
        assert stamp(config) == monitor_before

        after = browser_settings_submission(saved.text)
        after["service_name"][-1] = "Renamed Library"
        after["service_access_url"][-1] = "https://library.example/new"
        changed = submit(client, after)
        assert changed.headers.get("HX-Trigger") == "settingsSaved", changed.text
        assert load_settings(config).draft.access_services[-1].id == new.id
        assert load_settings(config).draft.publishers[0].access_service_id == new.id
        assert stamp(config) == monitor_before

        noop = stamp(path)
        response = submit(client, browser_settings_submission(changed.text))
        assert response.headers.get("HX-Trigger") == "settingsSaved", response.text
        assert stamp(path) == noop
    assert stamp(path) != list_before


@pytest.mark.parametrize("invalid", ["duplicate", "blank", "unsafe"])
def test_service_validation_rejects_bad_draft_without_writing(tmp_path, invalid):
    config, path, service, spare = prepared(tmp_path)
    before = stamp(config), stamp(path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        data = browser_settings_submission(client.get("/settings").text)
        if invalid == "duplicate":
            data["service_name"][1] = "  " + service.name.upper() + "  "
        elif invalid == "blank":
            data["service_name"][0] = ""
        else:
            data["service_access_url"][0] = "http://127.0.0.1/private"
        result = submit(client, data)
        assert "HX-Trigger" not in result.headers
        assert "Settings draft is invalid" in result.text or "Settings issues" in result.text
    assert (stamp(config), stamp(path)) == before


@pytest.mark.parametrize("kind", ["empty", "assigned", "forged", "stale", "changed_draft"])
def test_service_deletion_requires_signed_current_confirmation(tmp_path, kind):
    config, path, service, spare = prepared(tmp_path)
    before = stamp(config), stamp(path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        data = browser_settings_submission(client.get("/settings").text)
        removing = spare if kind == "empty" else service
        idx = data["service_id"].index(removing.id)
        for key in ("service_id", "service_name", "service_access_url"):
            data[key].pop(idx)
        if kind != "empty":
            data["publisher_access_service_id"][0] = ""
        preview = submit(client, data)
        assert "data-settings-deletion-preview" in preview.text, preview.text
        assert removing.id in preview.text
        assert (stamp(config), stamp(path)) == before
        if kind == "forged":
            signed = dict(data, settings_deletion_proof=["0" * 64], confirm_settings_deletions=["yes"])
            rejected = submit(client, signed)
            assert "HX-Trigger" not in rejected.headers
            assert (stamp(config), stamp(path)) == before
            return
        confirmed = confirm_submission(preview.text, data)
        if kind == "stale":
            path.write_bytes(path.read_bytes() + b"\n# external change\n")
            stale = stamp(path)
            rejected = submit(client, confirmed)
            assert "HX-Trigger" not in rejected.headers
            assert "files changed" in rejected.text or "revision" in rejected.text.lower()
            assert stamp(path) == stale and stamp(config) == before[0]
            return
        if kind == "changed_draft":
            confirmed["service_name"][0] = "Changed after Preview"
            rejected = submit(client, confirmed)
            assert "HX-Trigger" not in rejected.headers
            assert (stamp(config), stamp(path)) == before
            return
        saved = submit(client, confirmed)
        assert saved.headers.get("HX-Trigger") == "settingsSaved", saved.text
    stored = load_settings(config).draft
    assert removing.id not in {s.id for s in stored.access_services}
    if kind == "assigned":
        assert stored.publishers[0].access_service_id is None
    assert stamp(config) == before[0]


@pytest.mark.parametrize("confirm", [False, True])
def test_publisher_cascade_requires_signed_confirmation(tmp_path, confirm):
    config, path, service, spare = prepared(tmp_path)
    before = stamp(config), stamp(path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        data = browser_settings_submission(client.get("/settings").text)
        # Keep Annals and its Publisher; delete the sole Journal owned by P1.
        for key in ("journal_name", "journal_issns", "journal_group", "journal_pending",
                    "journal_publisher_id", "journal_order"):
            data[key] = data[key][1:]
        preview = submit(client, data)
        assert "data-settings-deletion-preview" in preview.text, preview.text
        assert P1 in preview.text and "Publisher URL" in preview.text
        assert service.id in preview.text
        assert (stamp(config), stamp(path)) == before
        if not confirm:
            assert save_settings(config, replace(load_settings(config).draft,
                journals=load_settings(config).draft.journals[1:])).outcome is SettingsSaveOutcome.INVALID_DRAFT
            return
        saved = submit(client, confirm_submission(preview.text, data))
        assert saved.headers.get("HX-Trigger") == "settingsSaved", saved.text
    state = load_settings(config).draft
    assert tuple(j.issn_l for j in state.journals) == (B,)
    assert tuple(p.publisher_id for p in state.publishers) == (P2,)
    assert {s.id for s in state.access_services} == {service.id, spare.id}
    assert stamp(config) == before[0]


def test_journal_remove_requires_local_cascade_confirmation_even_after_url_cleared(tmp_path):
    node = shutil.which("node")
    assert node
    config, path, service, spare = prepared(tmp_path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        page = client.get("/settings").text
        source = client.get("/static/app.js").text
    program = (
        "const PAGE=" + json.dumps(SettingsDOM(page).root)
        + ", SOURCE=" + json.dumps(source) + ";\n"
        + SETTINGS_NODE_DOM
        + r"""
const editor = document.getElementById("settings-editor");
const journals = () => editor.querySelectorAll(".journal-row");
const journal = journals().find(row => row.querySelector('[name="journal_publisher_id"]').value.endsWith("/P1"));
const publisher = editor.querySelectorAll("[data-publisher-row]").find(row =>
  row.querySelector('[name="publisher_id"]').value.endsWith("/P1"));
const panel = editor.querySelector("[data-journal-cascade-preview]");
const click = target => handlers.click({target});
const before = journals().length;
click(journal.querySelector("[data-remove-journal]"));
assert.equal(journals().length, before);
assert.equal(panel.hidden, false);
assert.ok(panel.querySelector("[data-journal-cascade-members]").textContent || panel.querySelectorAll("li").length);
click(panel.querySelector("[data-cancel-journal-remove]"));
assert.equal(journals().length, before);
const url = publisher.querySelector('[name="publisher_access_url"]');
url.value = ""; handlers.change({target: url});
const map = publisher.querySelector('[name="publisher_access_service_id"]');
map.value = ""; handlers.change({target: map});
click(journal.querySelector("[data-remove-journal]"));
assert.equal(panel.hidden, false);
assert.equal(journals().length, before);
assert.ok(panel.querySelectorAll("li")[0].textContent.includes("Saved URL"));
url.value = "https://changed.example"; handlers.change({target: url});
click(panel.querySelector("[data-confirm-journal-remove]"));
assert.equal(journals().length, before);
click(journal.querySelector("[data-remove-journal]"));
click(panel.querySelector("[data-confirm-journal-remove]"));
assert.equal(journals().length, before - 1);
assert.equal(editor.querySelectorAll("[data-service-row]").length, 2);
"""
    )
    result = subprocess.run([node, "-e", program], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_deletion_proof_rejects_changed_membership_and_partial_write_preserves_draft(tmp_path, monkeypatch):
    from literature_monitor.application import settings as settings_module

    config, path, service, spare = prepared(tmp_path)
    original = stamp(config), stamp(path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        draft = browser_settings_submission(client.get("/settings").text)
        for field in ("service_id", "service_name", "service_access_url"):
            draft[field].pop(0)
        draft["publisher_access_service_id"][0] = ""
        preview = submit(client, draft)
        confirmed = confirm_submission(preview.text, draft)
        # Signed for a specific Publisher membership, name, URL and both base revisions.
        confirmed["publisher_access_service_id"][1] = spare.id
        rejection = submit(client, confirmed)
        assert "HX-Trigger" not in rejection.headers
        assert stamp(path) == original[1]

        confirmed = confirm_submission(preview.text, draft)
        confirmed["name"] = ["Modified monitor field"]
        rejection = submit(client, confirmed)
        assert "HX-Trigger" not in rejection.headers
        assert stamp(path) == original[1]

        confirmed = confirm_submission(preview.text, draft)
        original_write = settings_module._write_snapshot_target
        def reject_list(target, contents, snapshot):
            if target == path:
                raise OSError("test write failure")
            return original_write(target, contents, snapshot)
        monkeypatch.setattr(settings_module, "_write_snapshot_target", reject_list)
        rejection = submit(client, confirmed)
        assert "HX-Trigger" not in rejection.headers
        retained = browser_settings_submission(rejection.text)
        assert retained["journal_revision_digest"] == draft["journal_revision_digest"]
        assert retained["monitor_revision_digest"] == draft["monitor_revision_digest"]
        assert retained["publisher_access_service_id"][0] == ""
        assert spare.id in retained["service_id"]
        assert service.id not in retained["service_id"]
        assert (stamp(config), stamp(path)) == original


def test_mapping_node_dom_interactions_and_htmx_draft(tmp_path):
    node = shutil.which("node")
    assert node
    config, path, first, spare = prepared(tmp_path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        page = client.get("/settings").text
        source = client.get("/static/app.js").text
        script = (
            "const PAGE=" + json.dumps(SettingsDOM(page).root)
            + ", SOURCE=" + json.dumps(source) + ";\n"
            + SETTINGS_NODE_DOM
            + r"""
const editor = () => document.getElementById("settings-editor");
const services = () => editor().querySelectorAll("[data-service-row]");
const publishers = () => editor().querySelectorAll("[data-publisher-row]");
const pid = row => row.querySelector('[name="publisher_id"]').value;
const assigned = row => row.querySelector('[name="publisher_access_service_id"]').value;
const click = button => handlers.click({target: button});
const group = editor().querySelector('[name="journal_group"]');
group.value = "Other"; handlers.change({target: group});
const before = publishers().map(row => [pid(row), row.querySelector('[name="publisher_access_url"]').value]);
const revisions = ["monitor_revision_digest", "journal_revision_digest"].map(name => editor().querySelector('[name="' + name + '"]').value);
const first = services()[0], spare = services()[1];
const secondPublisher = publishers()[1];
const assign = secondPublisher.querySelector('[name="publisher_access_service_id"]');
assign.value = first.querySelector('[name="service_id"]').value;
handlers.change({target: assign});
assert.equal(secondPublisher.closest("[data-mapping-container]"), first);
secondPublisher.querySelector("[data-select-publisher]").checked = true;
handlers.change({target: secondPublisher.querySelector("[data-select-publisher]")});
const other = publishers()[0];
other.querySelector("[data-select-publisher]").checked = true;
handlers.change({target: other.querySelector("[data-select-publisher]")});
editor().querySelector("[data-batch-service]").value = spare.querySelector('[name="service_id"]').value;
click(editor().querySelector("[data-assign-publishers]"));
assert.equal(publishers().every(row => assigned(row) === spare.querySelector('[name="service_id"]').value), true);
assert.equal(editor().querySelector("[data-publisher-selection]").textContent, "2 Publishers selected");
const transfer = {setData(type, value) {this[type] = value;}};
handlers.dragstart({target: other, dataTransfer: transfer});
const unassigned = editor().querySelector('[data-mapping-container][data-service-target=""]');
let prevented = false;
handlers.dragover({target: unassigned, preventDefault: () => {prevented = true;}, dataTransfer: transfer});
assert.ok(prevented);
handlers.drop({target: unassigned, preventDefault() {}, dataTransfer: transfer});
handlers.dragend({});
assert.equal(assigned(other), "");
assert.equal(other.closest("[data-mapping-container]"), unassigned);
click(editor().querySelector("[data-clear-publishers]"));
assert.ok(publishers().every(row => !row.querySelector("[data-select-publisher]").checked));
editor().querySelector("[data-new-service-name]").value = " New Service ";
editor().querySelector("[data-new-service-url]").value = "https://new.example";
click(editor().querySelector("[data-create-service]"));
const created = services().at(-1);
assert.match(created.querySelector('[name="service_id"]').value, /^svc_[0-9a-f]{32}$/);
editor().querySelector("[data-new-service-name]").value = "new service";
click(editor().querySelector("[data-create-service]"));
assert.equal(services().length, 3);
created.querySelector('[name="service_name"]').value = "Renamed";
click(created.querySelector("[data-edit-service]"));
assert.equal(created.querySelector('[name="service_id"]').value.length, 36);
click(first.querySelector("[data-delete-service]"));
let panel = first.querySelector("[data-service-delete-preview]");
assert.equal(panel.hidden, false);
click(panel.querySelector("[data-cancel-service-delete]"));
assert.equal(panel.hidden, true);
click(spare.querySelector("[data-delete-service]"));
panel = spare.querySelector("[data-service-delete-preview]");
assert.equal(panel.querySelectorAll("[data-service-delete-members] li").length, 1);
const current = publishers()[1].querySelector('[name="publisher_access_service_id"]');
current.value = ""; handlers.change({target: current});
click(panel.querySelector("[data-confirm-service-delete]"));
assert.ok(services().includes(spare));
click(spare.querySelector("[data-delete-service]"));
click(panel.querySelector("[data-confirm-service-delete]"));
assert.ok(!services().includes(spare));
assert.equal(group.value, "Other");
assert.equal(document.documentElement.dataset.settingsDirty, "true");
assert.deepEqual(publishers().map(row => [pid(row), row.querySelector('[name="publisher_access_url"]').value]).sort(),
                 before.sort());
assert.deepEqual(["monitor_revision_digest", "journal_revision_digest"].map(name => editor().querySelector('[name="' + name + '"]').value), revisions);
const values = {};
for (const item of editor().querySelectorAll("input, select")) {
  const key = item.attrs.name;
  if (key && !(item.attrs.type === "checkbox" && !item.checked)) (values[key] ||= []).push(item.value);
}
console.log(JSON.stringify(values));
"""
        )
        result = subprocess.run([node, "-e", script], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert len(data["publisher_id"]) == 2
        assert len(data["publisher_order"]) == 2
        assert "Other" in data["journal_group"]
        preview = submit(client, data)
        assert "data-settings-deletion-preview" in preview.text, preview.text
        submitted = browser_settings_submission(preview.text)
        assert submitted["monitor_revision_digest"] == data["monitor_revision_digest"]
        assert submitted["journal_revision_digest"] == data["journal_revision_digest"]
        assert sorted(submitted["publisher_access_url"]) == sorted(data["publisher_access_url"])
        assert submitted["journal_group"] == data["journal_group"] or set(submitted["journal_group"]) == set(data["journal_group"])
        assert len(submitted["service_id"]) == 2
        assert submitted["service_name"][-1] == "Renamed"
        # The rendered preview is a complete unsaved draft, rehydrated by the same code after HTMX.
        swap_program = (
            "const PAGE=" + json.dumps(SettingsDOM(page).root)
            + ", RESPONSE=" + json.dumps(SettingsDOM(preview.text).root)
            + ", SOURCE=" + json.dumps(source) + ";\n"
            + SETTINGS_NODE_DOM
            + r"""
const editor = () => document.getElementById("settings-editor");
bodyHandlers["htmx:beforeSwap"]({detail: {target: editor()}});
dom = build(RESPONSE);
bodyHandlers["htmx:afterSwap"]({detail: {target: editor()}});
assert.equal(editor().querySelectorAll("[data-publisher-row]").length, 2);
assert.equal(editor().querySelectorAll("[data-service-row]").length, 2);
assert.ok(editor().querySelectorAll("[data-publisher-row]").every(row =>
  row.closest("[data-mapping-container]").dataset.serviceTarget ===
  row.querySelector('[name="publisher_access_service_id"]').value));
assert.equal(editor().querySelector('[name="journal_revision_digest"]').value, REVISION);
""".replace("REVISION", json.dumps(data["journal_revision_digest"][0]))
        )
        swapped = subprocess.run([node, "-e", swap_program], text=True, capture_output=True)
        assert swapped.returncode == 0, swapped.stderr
