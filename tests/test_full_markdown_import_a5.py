"""SPEC §43.5 full-file Markdown Import/Export/Reload, isolated from user files."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from literature_monitor.application.full_markdown_import import (
    preview_full_markdown, confirm_full_markdown,
)
from literature_monitor.application.settings import load_settings
from literature_monitor.config import (
    AccessService, ConfigurationError, JournalConfig, PublisherConfig,
    parse_settings_list_text, render_settings_list_text,
)
from literature_monitor.safe_write import ContentChangedError, TextWriteCommittedError
from literature_monitor.web.app import create_app
from literature_monitor.openalex import SourceEvidence, SourceEvidenceResolution, SourceEvidenceStatus, PublisherMetadata
from test_access_mapping_a4 import prepared, stamp
from test_access_service_schema import A, B, P1, P2
from test_access_service_schema import LEGACY, monitor
from test_web_run_settings import browser_settings_submission, SettingsDOM, SETTINGS_NODE_DOM


def candidate(path: Path, *, suffix: str = "Imported", mapped: bool = True) -> str:
    current = path.read_text(encoding="utf-8")
    journals, publishers, services = parse_settings_list_text(current, path=path)
    new_journals = tuple(j.model_copy(update={"group": suffix}) if i == 0 else j
                         for i, j in enumerate(journals))
    new_publishers = tuple(p.model_copy(update={"publisher_url": "https://changed.example/manual"}) if i == 0 else p
                           for i, p in enumerate(publishers))
    if not mapped:
        new_publishers = tuple(p.model_copy(update={"access_service_id": None}) for p in new_publishers)
    new_services = tuple(s.model_copy(update={"name": suffix}) if i == 0 else s
                         for i, s in enumerate(services))
    document = render_settings_list_text(current, new_journals, new_publishers,
                                         access_services=new_services, path=path)
    return document + "\n## Notes From Import\n\nNew human content.\n"


def submission(client):
    return browser_settings_submission(client.get("/settings").text)


def preview(client, data, source: str, *, filename="import.md", encoding="utf-8"):
    data = dict(data)
    data.pop("markdown_file", None)
    return client.post("/settings/import/preview", data=data,
                       files={"markdown_file": (filename, source.encode(encoding), "text/markdown")})


def confirm(client, page):
    return client.post("/settings/import/confirm", data=browser_settings_submission(page.text))


def test_full_markdown_round_trip_and_unchanged_monitor(tmp_path):
    config, path, service, spare = prepared(tmp_path)
    saved_monitor, old_list = stamp(config), stamp(path)
    source = candidate(path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        data = submission(client)
        exported = client.post("/settings/export", data=data)
        assert exported.status_code == 200 and exported.content == old_list[0]
        assert "attachment" in exported.headers["Content-Disposition"]
        assert b"monitor.yaml" not in exported.content
        assert "## Access Services" in exported.text
        assert "## Conferences" in exported.text or "##" in exported.text

        response = preview(client, data, source)
        assert "data-markdown-preview" in response.text, response.text[:2000]
        assert service.id in response.text
        assert "Group" in response.text and "manual text" in response.text
        assert stamp(path) == old_list
        assert stamp(config) == saved_monitor

        saved = confirm(client, response)
        assert saved.headers.get("HX-Trigger") == "settingsSaved", saved.text[:2000]
        assert path.read_text() == source
        assert stamp(config) == saved_monitor
        assert load_settings(config).draft.journals[0].group == "Imported"
        assert load_settings(config).draft.access_services[0].id == service.id
        exported_again = client.post("/settings/export", data=submission(client))
        assert exported_again.content == source.encode()
        before_noop = stamp(path)
        no_op = preview(client, submission(client), source)
        assert confirm(client, no_op).headers.get("HX-Trigger") == "settingsSaved"
        assert stamp(path) == before_noop


@pytest.mark.parametrize("damage", ["incomplete", "legacy", "unsafe_url", "dangling_service",
                                   "wrong_journal_name", "wrong_publisher_name",
                                   "duplicate_service_id", "duplicate_publisher_id"])
def test_invalid_whole_file_is_blocked_without_write(tmp_path, damage):
    config, path, service, spare = prepared(tmp_path)
    current = candidate(path)
    if damage == "incomplete":
        source = "## Journals\n\n| Journal | ISSN-L | Publisher ID | Group |\n| --- | --- | --- | --- |\n"
    elif damage == "legacy":
        source = current.replace("| Publisher | OpenAlex ID | Publisher URL | Access Service ID |",
                                 "| Publisher | OpenAlex ID | Access URL |").replace(
                                     "## Access Services", "## Removed Services")
    elif damage == "unsafe_url":
        source = current.replace("https://changed.example/manual", "http://127.0.0.1/private")
    elif damage == "dangling_service":
        source = current.replace(service.id, "svc_" + "a" * 32, 1)
    elif damage == "wrong_journal_name":
        source = current.replace("| Biometrics |", "| Wrong Name |")
    elif damage == "wrong_publisher_name":
        source = current.replace("| Publisher One |", "| Wrong Publisher |")
    elif damage == "duplicate_service_id":
        original = f"| {service.id} | Imported |"
        source = current.replace(original, original + "\n" + original)
    else:
        source = current.replace(f"| {P2} |", f"| {P1} |", 1)
    previous = stamp(path), stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        result = preview(client, submission(client), source)
        assert "data-markdown-preview" not in result.text, (damage, result.text[-1000:])
        assert "Import Preview blocked" in result.text
    assert (stamp(path), stamp(config)) == previous


def test_whole_file_deletion_previews_publisher_cascade_and_preserves_services(tmp_path):
    config, path, service, spare = prepared(tmp_path)
    original = path.read_text()
    journals, publishers, services = parse_settings_list_text(original, path=path)
    imported = render_settings_list_text(original, journals[1:], publishers[1:],
                                         access_services=services, path=path)
    before = stamp(path), stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        plan = preview(client, submission(client), imported)
        assert "data-markdown-preview" in plan.text
        assert "Remove Journal" in plan.text
        assert "Publisher cascade / loss of manual metadata" in plan.text
        assert publishers[0].publisher_url in plan.text
        assert service.id in plan.text
        assert (stamp(path), stamp(config)) == before
        rejected_data = browser_settings_submission(plan.text)
        rejected_data.pop("confirm_full_import")
        rejected = client.post("/settings/import/confirm", data=rejected_data)
        assert "Import not written" in rejected.text
        assert (stamp(path), stamp(config)) == before
        saved = confirm(client, plan)
        assert saved.headers.get("HX-Trigger") == "settingsSaved", saved.text[:900]
        assert path.read_text() == imported
        assert {s.id for s in load_settings(config).draft.access_services} == {service.id, spare.id}
        assert stamp(config) == before[1]


def test_whole_file_service_deletion_lists_and_unassigns_member(tmp_path):
    config, path, service, spare = prepared(tmp_path)
    original = path.read_text()
    journals, publishers, services = parse_settings_list_text(original, path=path)
    unassigned = publishers[0].model_copy(update={"access_service_id": None})
    imported = render_settings_list_text(original, journals, (unassigned, *publishers[1:]),
                                         access_services=(spare,), path=path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        selected = preview(client, submission(client), imported)
        assert "Delete Access Service" in selected.text
        assert "Service deletion affects Publisher" in selected.text
        assert publishers[0].publisher_id in selected.text
        assert service.id in selected.text
        response = confirm(client, selected)
        assert response.headers.get("HX-Trigger") == "settingsSaved", response.text[:1400]
    state = load_settings(config).draft
    assert state.publishers[0].access_service_id is None
    assert state.access_services == (spare,)


@pytest.mark.parametrize("attack", ["tamper_source", "tamper_draft", "tamper_signature", "target_changed", "cancel", "missing_confirmation"])
def test_confirm_rejects_stale_forged_or_unconfirmed_import(tmp_path, attack):
    config, path, service, spare = prepared(tmp_path)
    source = candidate(path)
    original = stamp(path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        response = preview(client, submission(client), source)
        assert "data-markdown-preview" in response.text
        data = browser_settings_submission(response.text)
        if attack == "tamper_source":
            data["markdown_import_payload"] = [data["markdown_import_payload"][0] + "changed"]
        elif attack == "tamper_draft":
            data["service_name"][0] = "Edited After Preview"
        elif attack == "tamper_signature":
            data["markdown_import_proof"] = ["0" * 64]
        elif attack == "target_changed":
            path.write_text(path.read_text() + "\n# External editor", encoding="utf-8")
        elif attack == "cancel":
            assert stamp(path) == original
            return
        else:
            data.pop("confirm_full_import", None)
        rejected = client.post("/settings/import/confirm", data=data)
        assert "HX-Trigger" not in rejected.headers
        assert "Import not written" in rejected.text
    if attack == "target_changed":
        assert "External editor" in path.read_text()
    else:
        assert stamp(path) == original


def test_unsaved_draft_requires_explicit_discard_and_is_preserved_on_preview(tmp_path):
    config, path, service, spare = prepared(tmp_path)
    original = stamp(path), stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        data = submission(client)
        data["name"] = ["Draft Monitor"]
        data["journal_group"][0] = "Unsaved Group"
        data["publisher_access_url"][0] = "https://draft.example/url"
        data["service_name"][0] = "Unsaved Service"
        imported_source = candidate(path)
        previewed = preview(client, data, imported_source)
        assert "data-markdown-preview" in previewed.text
        fields = browser_settings_submission(previewed.text)
        assert fields["name"] == ["Draft Monitor"]
        assert "Unsaved Group" in fields["journal_group"]
        assert "https://draft.example/url" in fields["publisher_access_url"]
        assert "Unsaved Service" in fields["service_name"]
        assert fields["journal_revision_digest"] == data["journal_revision_digest"]
        rejected = confirm(client, previewed)
        assert "Unsaved Settings Draft" in rejected.text
        assert (stamp(path), stamp(config)) == original
        fields["import_discard"] = ["yes"]
        committed = client.post("/settings/import/confirm", data=fields)
        assert committed.headers.get("HX-Trigger") == "settingsSaved", committed.text[:1600]
        assert load_settings(config).draft.name != "Draft Monitor"
        assert stamp(config) == original[1]
    assert path.read_text() == imported_source


def test_empty_group_draft_is_not_silently_discarded(tmp_path):
    config, path, _, _ = prepared(tmp_path)
    original = stamp(path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        data = submission(client)
        data["settings_group"] = ["Statistics", "Other", "New empty Group"]
        selected = preview(client, data, candidate(path))
        assert "data-markdown-preview" in selected.text
        assert "Unsaved Settings Draft" in selected.text
        assert 'value="New empty Group"' in selected.text
        blocked = confirm(client, selected)
        assert "Unsaved Settings Draft" in blocked.text
        assert stamp(path) == original
        guarded_export = client.post("/settings/export", data=data)
        assert "data-export-guard" in guarded_export.text
        assert stamp(path) == original


def test_export_and_reload_explicit_guards(tmp_path):
    config, path, service, spare = prepared(tmp_path)
    old = stamp(path), stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        data = submission(client)
        data["keyword_expression"] = ["unsaved draft keyword"]
        export_guard = client.post("/settings/export", data=data)
        assert "data-export-guard" in export_guard.text
        assert "unsaved draft keyword" in export_guard.text
        assert (stamp(path), stamp(config)) == old
        exported = client.post("/settings/export", data=dict(data, export_discard=["yes"]))
        assert exported.content == old[0][0]
        assert "attachment" in exported.headers["Content-Disposition"]
        reload_guard = client.post("/settings/reload", data=data)
        assert "data-reload-guard" in reload_guard.text
        assert "unsaved draft keyword" in reload_guard.text
        wrong = client.post("/settings/reload", data=dict(data, reload_step=["confirm"], reload_word=["no"]))
        assert "data-reload-guard" in wrong.text
        restored = client.post("/settings/reload", data=dict(data, reload_step=["confirm"], reload_word=["RELOAD"]))
        assert restored.headers["HX-Trigger"] == "settingsSaved"
        assert "unsaved draft keyword" not in browser_settings_submission(restored.text)["keyword_expression"]
        assert (stamp(path), stamp(config)) == old


def test_application_identity_cas_and_source_size_boundary(tmp_path, monkeypatch):
    config, path, _, _ = prepared(tmp_path)
    source = candidate(path)
    first = preview_full_markdown(path, source, filename="changes.md")
    with pytest.raises(ConfigurationError):
        preview_full_markdown(path, source, filename="changes.csv")
    with pytest.raises(ConfigurationError):
        preview_full_markdown(path, "X" * (1024 * 1024 + 1), filename="changes.md")
    original = stamp(path)
    path.write_text(path.read_text() + "\n## External Editor\n\nNew content\n", encoding="utf-8")
    with pytest.raises(ContentChangedError):
        confirm_full_markdown(first)
    assert path.read_text().rstrip().endswith("New content")
    assert stamp(config) == stamp(config)
    assert stamp(path) != original


def test_monitor_revision_can_block_full_import_at_atomic_replace_boundary(tmp_path):
    config, path, _, _ = prepared(tmp_path)
    plan = preview_full_markdown(path, candidate(path), filename="import.md")
    before = stamp(path)
    def concurrent_monitor_edit():
        config.write_bytes(config.read_bytes() + b"\n# concurrent monitor edit\n")
        raise ContentChangedError("monitor.yaml changed")
    with pytest.raises(ContentChangedError, match="monitor.yaml changed"):
        confirm_full_markdown(plan, before_write=concurrent_monitor_edit)
    assert stamp(path) == before
    assert config.read_bytes().endswith(b"# concurrent monitor edit\n")


def test_legacy_access_upgrade_requires_a_new_signed_preview(tmp_path):
    config = monitor(tmp_path)
    path = tmp_path / "list.md"
    path.write_text(LEGACY, encoding="utf-8")
    original = stamp(path), stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        data = submission(client)
        assert "data-preview-access-upgrade" in client.get("/settings").text
        refused = preview(client, data, LEGACY)
        assert "Import Preview blocked" in refused.text
        first = client.post("/settings/upgrade/preview", data=data)
        assert "data-access-upgrade-plan" in first.text, first.text[:1800]
        assert (stamp(path), stamp(config)) == original
        signed = browser_settings_submission(first.text)
        signed["access_upgrade_proof"] = ["0" * 64]
        rejected = client.post("/settings/upgrade/confirm", data=signed)
        assert "HX-Trigger" not in rejected.headers
        assert stamp(path) == original[0]
        signed = browser_settings_submission(first.text)
        if "Discard Draft" in first.text:
            signed["upgrade_discard"] = ["yes"]
        upgraded = client.post("/settings/upgrade/confirm", data=signed)
        assert upgraded.headers.get("HX-Trigger") == "settingsSaved", upgraded.text[:2500]
        assert "## Access Services" in path.read_text()
        assert "## Conferences" in path.read_text()
        assert "Human free text" in path.read_text()
        assert stamp(config) == original[1]
        assert "data-preview-access-upgrade" not in client.get("/settings").text


def test_import_fails_on_non_utf8_and_wrong_upload_type(tmp_path):
    config, path, _, _ = prepared(tmp_path)
    original = stamp(path), stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        data = submission(client)
        for name, blob in (("config.md", b"\xff"), ("config.tsv", path.read_bytes()), ("config.md", b"")):
            form = dict(data)
            form.pop("markdown_file", None)
            invalid = client.post("/settings/import/preview", data=form,
                                  files={"markdown_file": (name, blob, "text/plain")})
            assert "Import Preview blocked" in invalid.text
    assert (stamp(path), stamp(config)) == original


def test_import_rejects_inode_swap_and_failed_write(tmp_path, monkeypatch):
    import literature_monitor.application.full_markdown_import as importer

    config, path, _, _ = prepared(tmp_path)
    plan = preview_full_markdown(path, candidate(path), filename="import.md")
    original_text = path.read_text()
    other = tmp_path / "replacement.md"
    other.write_text(original_text, encoding="utf-8")
    other.replace(path)
    with pytest.raises(ContentChangedError):
        confirm_full_markdown(plan)
    assert path.read_text() == original_text

    plan = preview_full_markdown(path, candidate(path), filename="import.md")
    def fail_before_replace(*args, **kwargs):
        raise OSError("simulated write failure")
    monkeypatch.setattr(importer, "replace_regular_text_at_identity", fail_before_replace)
    with pytest.raises(OSError, match="simulated write failure"):
        confirm_full_markdown(plan)
    assert path.read_text() == original_text


def test_import_reports_actual_write_when_directory_sync_fails(tmp_path, monkeypatch):
    import literature_monitor.application.full_markdown_import as importer

    config, path, _, _ = prepared(tmp_path)
    source = candidate(path)
    monitor_before = stamp(config)
    def simulated_late_failure(target, contents, **kwargs):
        target.write_text(contents, encoding="utf-8")
        raise TextWriteCommittedError("simulated directory fsync failure")
    monkeypatch.setattr(importer, "replace_regular_text_at_identity", simulated_late_failure)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        selected = preview(client, submission(client), source)
        saved = confirm(client, selected)
        assert saved.headers.get("HX-Trigger") == "settingsSaved"
        assert "directory synchronization failed" in saved.text
        assert load_settings(config).draft.journal_revision.digest in saved.text
    assert path.read_text() == source
    assert stamp(config) == monitor_before


def test_old_draft_import_endpoint_is_not_available(tmp_path):
    config, path, _, _ = prepared(tmp_path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        assert client.post("/settings/import/apply", data=submission(client)).status_code == 404


@pytest.mark.parametrize("verified", [False, True])
def test_new_journal_import_needs_verified_openalex_identity(tmp_path, monkeypatch, verified):
    import literature_monitor.application.full_markdown_import as importer

    config, path, _, _ = prepared(tmp_path)
    saved, publishers, services = parse_settings_list_text(path.read_text(), path=path)
    added = JournalConfig(name="Verified Journal", issn_l="1541-0420", publisher_id=P1, group="New")
    source = render_settings_list_text(path.read_text(), (*saved, added), publishers,
                                       access_services=services, path=path)
    plan = preview_full_markdown(path, source, filename="import.md")
    assert plan.new_journal_ids == (added.issn_l,)
    before = stamp(path)
    def source_lookup(client, identifiers):
        assert identifiers == [added.issn_l] or identifiers == (added.issn_l,)
        return (SourceEvidenceResolution(
            requested_issn=added.issn_l,
            evidence=SourceEvidence(source_id="https://openalex.org/S123", display_name=added.name if verified else "Wrong Name",
                                    aliases=(added.issn_l,), provider_issn_l=added.issn_l, publisher_id=P1),
            status=SourceEvidenceStatus.RESOLVED,
        ),)
    monkeypatch.setattr(importer, "resolve_source_identities", source_lookup)
    if verified:
        digest = confirm_full_markdown(plan, client=object())
        assert digest == load_settings(config).draft.journal_revision.digest
        assert added in load_settings(config).draft.journals
    else:
        with pytest.raises(ConfigurationError, match="unverified or conflicting"):
            confirm_full_markdown(plan, client=object())
        assert stamp(path) == before


@pytest.mark.parametrize("verified", [False, True])
def test_new_publisher_import_is_directly_verified_and_unassigned(tmp_path, monkeypatch, verified):
    import literature_monitor.application.full_markdown_import as importer

    config, path, _, _ = prepared(tmp_path)
    saved, publishers, services = parse_settings_list_text(path.read_text(), path=path)
    publisher_id = "https://openalex.org/P123"
    new_journal = JournalConfig(name="New Journal", issn_l="1541-0420", publisher_id=publisher_id)
    new_publisher = PublisherConfig(name="New Publisher", publisher_id=publisher_id,
                                     publisher_url="https://newpublisher.example", access_service_id=None)
    source = render_settings_list_text(path.read_text(), (*saved, new_journal), (*publishers, new_publisher),
                                       access_services=services, path=path)
    plan = preview_full_markdown(path, source, filename="import.md")
    monkeypatch.setattr(importer, "resolve_source_identities",
                        lambda client, ids: (SourceEvidenceResolution(
                            requested_issn=new_journal.issn_l,
                            evidence=SourceEvidence(source_id="https://openalex.org/S123",
                                                    display_name=new_journal.name, aliases=(new_journal.issn_l,),
                                                    provider_issn_l=new_journal.issn_l,
                                                    publisher_id=new_journal.publisher_id),
                            status=SourceEvidenceStatus.RESOLVED),))
    monkeypatch.setattr(importer, "resolve_publisher_metadata",
                        lambda client, ids: (PublisherMetadata(
                            publisher_id, "New Publisher" if verified else "Unexpected Publisher",
                            "https://newpublisher.example"),))
    previous = stamp(path)
    if verified:
        confirm_full_markdown(plan, client=object())
        assert load_settings(config).draft.publishers[-1] == new_publisher
    else:
        with pytest.raises(ConfigurationError, match="unverified canonical"):
            confirm_full_markdown(plan, client=object())
        assert stamp(path) == previous


def test_native_dom_markdown_upload_guard_and_cancel_preserves_other_edits(tmp_path):
    node = shutil.which("node")
    assert node
    config, path, _, _ = prepared(tmp_path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        page = client.get("/settings").text
        script = client.get("/static/app.js").text
    program = (
        "const PAGE=" + json.dumps(SettingsDOM(page).root)
        + ", SOURCE=" + json.dumps(script) + ";\n"
        + SETTINGS_NODE_DOM
        + r"""
(async () => {
const editor = document.getElementById("settings-editor");
const picker = editor.querySelector("[data-journal-import-file]");
const error = editor.querySelector("[data-import-file-error]");
const preview = editor.querySelector("[data-import-preview]");
const original = editor.querySelector('[name="journal_revision_digest"]').value;
const publisherUrl = editor.querySelector('[name="publisher_access_url"]');
publisherUrl.value = "https://draft.example/manual";
handlers.input({target: publisherUrl});
assert.equal(document.documentElement.dataset.settingsDirty, "true");
picker.files = [{name:"not-a-full-config.csv", size:10, arrayBuffer: async () => new Uint8Array([65]).buffer}];
handlers.change({target: picker});
await new Promise(setImmediate);
assert.ok(error.textContent.includes(".md"));
assert.equal(preview.disabled, true);
picker.files = [{name:"full.md", size:4, arrayBuffer: async () => new TextEncoder().encode("text").buffer}];
handlers.change({target: picker});
await new Promise(setImmediate);
assert.equal(preview.disabled, false);
assert.equal(error.hidden, true);
assert.equal(editor.querySelector('[name="journal_revision_digest"]').value, original);
assert.equal(editor.querySelector('[name="publisher_access_url"]').value, "https://draft.example/manual");
assert.equal(document.documentElement.dataset.settingsDirty, "true");
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    )
    result = subprocess.run([node, "-e", program], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_native_dom_rechecks_selected_file_before_confirm(tmp_path):
    node = shutil.which("node")
    assert node
    config, path, _, _ = prepared(tmp_path)
    source = candidate(path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        page = client.get("/settings").text
        script = client.get("/static/app.js").text
        draft = submission(client)
        draft["publisher_access_url"][0] = "https://draft.example/manual"
        previewed = preview(client, draft, source)
        assert "data-markdown-preview" in previewed.text
    program = (
        "const PAGE=" + json.dumps(SettingsDOM(page).root)
        + ", PREVIEW=" + json.dumps(SettingsDOM(previewed.text).root)
        + ", SOURCE=" + json.dumps(script)
        + ", IMPORTED=" + json.dumps(source) + ";\n"
        + SETTINGS_NODE_DOM
        + r"""
(async () => {
const editor = () => document.getElementById("settings-editor");
const picker = editor().querySelector("[data-journal-import-file]");
const file = {name:"full.md", size:IMPORTED.length,
  arrayBuffer:async () => new TextEncoder().encode(IMPORTED).buffer};
picker.files = [file];
handlers.change({target: picker});
await new Promise(setImmediate);
assert.equal(editor().querySelector("[data-import-preview]").disabled, false);
bodyHandlers["htmx:beforeSwap"]({detail:{target:editor()}});
dom = build(PREVIEW);
bodyHandlers["htmx:afterSwap"]({detail:{target:editor()}});
const button = editor().querySelector("[data-import-confirm-discard]");
assert.ok(button);
let issued = false, prevented = false;
bodyHandlers["htmx:confirm"]({detail:{elt:button,issueRequest(value) {issued = value;}},
  preventDefault() {prevented = true;}});
await new Promise(resolve => setTimeout(resolve, 60));
assert.equal(prevented, true);
assert.equal(issued, true);
file.arrayBuffer = async () => new TextEncoder().encode(IMPORTED + "\nChanged").buffer;
issued = false;
bodyHandlers["htmx:confirm"]({detail:{elt:button,issueRequest() {issued = true;}},
  preventDefault() {}});
await new Promise(resolve => setTimeout(resolve, 60));
assert.equal(issued, false);
const notice = editor().querySelector("[data-import-file-error]");
assert.equal(notice.hidden, false);
assert.ok(notice.textContent.includes("changed since Preview"));
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    )
    result = subprocess.run([node, "-e", program], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
