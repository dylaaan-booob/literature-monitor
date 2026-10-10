"""A2: full Settings Draft round-trip and changed-file-only persistence."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import FormData

import literature_monitor.application.settings as settings_module
from literature_monitor.application.settings import (
    ContentRevision, SettingsIssueSource, SettingsSaveOutcome, load_settings, save_settings,
)
from literature_monitor.config import (
    AccessService, JournalConfig, PublisherConfig, load_config,
    parse_settings_list_text, render_settings_list_text,
)
from literature_monitor.web.app import create_app
from literature_monitor.web.settings_form import (
    settings_draft_from_form, settings_form_from_draft, settings_form_from_submission,
)
from test_access_service_schema import A, B, P1, P2, current_mapping_document
from test_web_run_settings import browser_settings_submission


def stamp(path: Path):
    info = path.stat()
    return path.read_bytes(), info.st_ino, info.st_mtime_ns


def start(tmp_path: Path):
    config, path, journal, publisher, service = current_mapping_document(tmp_path)
    return config, path, load_settings(config).draft


def missing_monitor(tmp_path: Path):
    config, path, _ = start(tmp_path)
    config.unlink()
    opened = load_settings(config)
    assert opened.draft.monitor_revision == ContentRevision(exists=False, digest=None)
    assert len(opened.issues) == 1
    assert opened.issues[0].source is SettingsIssueSource.MONITOR_CONFIG
    return config, path, replace(
        opened.draft, name="Recovered Monitor", keyword_expression="causal",
    )


def test_missing_monitor_creates_only_yaml_from_valid_canonical_draft(tmp_path: Path):
    config, path, draft = missing_monitor(tmp_path)
    before_list = stamp(path)

    saved = save_settings(config, draft)

    assert saved.outcome is SettingsSaveOutcome.SAVED, saved.issues
    assert (saved.journal_written, saved.monitor_written) == (False, True)
    assert saved.state.issues == ()
    assert saved.monitor_revision.exists and saved.monitor_revision.digest
    assert saved.journal_revision == draft.journal_revision
    assert stamp(path) == before_list
    loaded = load_config(config)
    assert loaded.name == "Recovered Monitor"
    assert loaded.keyword_expression == "causal"
    assert saved.state.draft.journals == draft.journals
    assert saved.state.draft.publishers == draft.publishers
    assert saved.state.draft.access_services == draft.access_services


def test_missing_monitor_requires_valid_explicit_draft_before_creation(tmp_path: Path):
    config, path, draft = missing_monitor(tmp_path)
    before_list = stamp(path)

    invalid = save_settings(config, replace(draft, keyword_expression=""))

    assert invalid.outcome is SettingsSaveOutcome.INVALID_DRAFT
    assert not invalid.journal_written and not invalid.monitor_written
    assert not config.exists()
    assert stamp(path) == before_list


def test_concurrent_monitor_creation_before_save_is_revision_conflict(tmp_path: Path):
    config, path, draft = missing_monitor(tmp_path)
    before_list = stamp(path)
    config.write_text("name: External Monitor\nkeyword_expression: outside\n", encoding="utf-8")
    external = stamp(config)

    result = save_settings(config, draft)

    assert result.outcome is SettingsSaveOutcome.REVISION_CONFLICT
    assert not result.journal_written and not result.monitor_written
    assert stamp(config) == external
    assert stamp(path) == before_list


def test_creation_race_at_exclusive_write_boundary_preserves_external_file(tmp_path: Path, monkeypatch):
    config, path, draft = missing_monitor(tmp_path)
    before_list = stamp(path)
    original_write = settings_module._write_snapshot_target
    def create_externally_before_save(target, contents, snapshot):
        if target == config:
            assert not snapshot.revision.exists
            config.write_text("name: External writer\n", encoding="utf-8")
        return original_write(target, contents, snapshot)
    monkeypatch.setattr(settings_module, "_write_snapshot_target", create_externally_before_save)

    result = save_settings(config, draft)

    assert result.outcome is SettingsSaveOutcome.REVISION_CONFLICT
    assert not result.journal_written and not result.monitor_written
    assert config.read_text() == "name: External writer\n"
    assert stamp(path) == before_list


def test_missing_monitor_creation_failure_preserves_canonical_list(tmp_path: Path, monkeypatch):
    config, path, draft = missing_monitor(tmp_path)
    before_list = stamp(path)
    original_write = settings_module._write_snapshot_target
    def fail_monitor_creation(target, contents, snapshot):
        if target == config:
            assert not snapshot.revision.exists
            raise OSError("injected monitor creation failure")
        return original_write(target, contents, snapshot)
    monkeypatch.setattr(settings_module, "_write_snapshot_target", fail_monitor_creation)

    result = save_settings(config, draft)

    assert result.outcome is SettingsSaveOutcome.WRITE_FAILED
    assert not result.journal_written and not result.monitor_written
    assert result.state.draft.monitor_revision == draft.monitor_revision
    assert not config.exists()
    assert stamp(path) == before_list


def test_corrupt_existing_monitor_is_not_replaced_by_recovered_defaults(tmp_path: Path):
    config, path, draft = missing_monitor(tmp_path)
    config.write_text("venue_whitelist: [\n", encoding="utf-8")
    opened = load_settings(config)
    assert opened.draft.monitor_revision.exists
    assert any(issue.source is SettingsIssueSource.MONITOR_CONFIG for issue in opened.issues)
    before_list, before_monitor = stamp(path), stamp(config)

    rejected = save_settings(config, replace(
        opened.draft, name="Repaired?", keyword_expression="causal",
    ))

    assert rejected.outcome is SettingsSaveOutcome.INVALID_DRAFT
    assert not rejected.journal_written and not rejected.monitor_written
    assert (stamp(path), stamp(config)) == (before_list, before_monitor)


@pytest.mark.parametrize("invalid_list", ["service_header", "service_reference", "legacy_publishers"])
def test_missing_monitor_cannot_bypass_strict_list_schema(tmp_path: Path, invalid_list: str):
    config, path, draft = missing_monitor(tmp_path)
    original = path.read_text()
    if invalid_list == "service_header":
        corrupt = original.replace("| Service ID | Service | Access URL |", "| Broken | Service | Access URL |")
    elif invalid_list == "service_reference":
        corrupt = original.replace(
            f"| {draft.access_services[0].id} | University Library |",
            f"| {AccessService.create('Unknown').id} | University Library |",
        )
    else:
        corrupt = original.replace(
            "| Publisher | OpenAlex ID | Publisher URL | Access Service ID |",
            "| Publisher | OpenAlex ID | Access URL |",
        )
    path.write_text(corrupt, encoding="utf-8")
    opened = load_settings(config)
    assert opened.issues
    attempted = replace(opened.draft, name="Do not persist", keyword_expression="causal")
    before_list = stamp(path)

    rejected = save_settings(config, attempted)

    assert rejected.outcome is SettingsSaveOutcome.INVALID_DRAFT
    assert not rejected.journal_written and not rejected.monitor_written
    assert stamp(path) == before_list
    assert not config.exists()


def test_web_can_create_missing_monitor_without_touching_saved_mapping(tmp_path: Path):
    config, path, draft = missing_monitor(tmp_path)
    before_list = stamp(path)

    with TestClient(create_app(config), base_url="http://localhost") as client:
        form = browser_settings_submission(client.get("/settings").text)
        assert form["monitor_revision_exists"] == ["0"]
        assert form["service_id"] == [draft.access_services[0].id]
        form["name"] = ["Created from Settings"]
        form["keyword_expression"] = ["causal"]
        response = client.post("/settings/save", data=form)
        assert response.headers.get("HX-Trigger") == "settingsSaved"

    assert load_config(config).name == "Created from Settings"
    assert load_settings(config).draft.access_services == draft.access_services
    assert stamp(path) == before_list


def test_web_stale_missing_monitor_draft_preserves_original_revision(tmp_path: Path):
    config, path, draft = missing_monitor(tmp_path)
    before_list = stamp(path)

    with TestClient(create_app(config), base_url="http://localhost") as client:
        form = browser_settings_submission(client.get("/settings").text)
        form["name"] = ["Attempted Recovery"]
        form["keyword_expression"] = ["causal"]
        config.write_text("name: External Writer\nkeyword_expression: outside\n", encoding="utf-8")
        before_external = stamp(config)
        response = client.post("/settings/save", data=form)
        assert "HX-Trigger" not in response.headers
        retained = browser_settings_submission(response.text)
        assert retained["name"] == ["Attempted Recovery"]
        assert retained["monitor_revision_exists"] == ["0"]
        assert retained["monitor_revision_digest"] == [""]

    assert stamp(config) == before_external
    assert stamp(path) == before_list


def form_data(values):
    entries = [
        ("name", values.name), ("keyword_expression", values.keyword_expression),
        ("output_dir", values.output_dir), ("log_level", values.log_level),
        ("from_date", values.from_date), ("to_date", values.to_date), ("window_days", values.window_days),
        ("monitor_revision_exists", values.monitor_revision_exists),
        ("monitor_revision_digest", values.monitor_revision_digest),
        ("journal_revision_exists", values.journal_revision_exists),
        ("journal_revision_digest", values.journal_revision_digest),
        ("institution_name", values.institution_name),
        ("institution_idp_entity_id", values.institution_idp_entity_id),
    ]
    for j in values.journals:
        entries.extend((("journal_name", j.name), ("journal_issns", j.issns), ("journal_group", j.group),
                        ("journal_publisher_id", j.publisher_id), ("journal_pending", "1" if j.pending else "0")))
    for p in values.publishers:
        entries.extend((("publisher_name", p.name), ("publisher_id", p.publisher_id),
                        ("publisher_access_url", p.publisher_url),
                        ("publisher_access_service_id", p.access_service_id)))
    for s in values.access_services:
        entries.extend((("service_id", s.id), ("service_name", s.name), ("service_access_url", s.access_url)))
    return FormData(entries)


def test_full_draft_form_roundtrip_preserves_relation_ids(tmp_path: Path):
    config, path, draft = start(tmp_path)
    original = stamp(path), stamp(config)
    values = settings_form_from_draft(draft)
    assert len(values.publishers) == len(values.access_services) == 1
    assert values.publishers[0].access_service_id == values.access_services[0].id
    submitted = settings_form_from_submission(form_data(values))
    rebuilt, issues = settings_draft_from_form(submitted)
    assert not issues and rebuilt == draft
    assert (stamp(path), stamp(config)) == original


@pytest.mark.parametrize("change", [
    "group", "publisher_url", "publisher_assignment", "service_name", "service_url", "monitor",
    "institution", "both", "noop", "service_creation", "unassigned",
])
def test_draft_change_only_writes_its_owner(tmp_path: Path, change: str, monkeypatch):
    config, path, draft = start(tmp_path)
    before = (stamp(path), stamp(config))
    existing_service = draft.access_services[0]
    if change == "group":
        draft = replace(draft, journals=(draft.journals[0].model_copy(update={"group": "Other"}),))
    elif change == "publisher_url":
        draft = replace(draft, publishers=(draft.publishers[0].model_copy(
            update={"publisher_url": "https://new-publisher.example.org"}),))
    elif change == "publisher_assignment":
        draft = replace(draft, publishers=(draft.publishers[0].model_copy(update={"access_service_id": None}),))
    elif change == "service_name":
        draft = replace(draft, access_services=(existing_service.model_copy(update={"name": "Changed Service"}),))
    elif change == "service_url":
        draft = replace(draft, access_services=(existing_service.model_copy(
            update={"access_url": "https://new-library.example.org"}),))
    elif change == "monitor":
        draft = replace(draft, keyword_expression="statistics")
    elif change == "institution":
        from literature_monitor.config import InstitutionConfig
        draft = replace(draft, institution=InstitutionConfig(name="University"))
    elif change == "both":
        draft = replace(draft, name="Updated Monitor", access_services=(
            existing_service.model_copy(update={"name": "New name"}),))
    elif change == "service_creation":
        draft = replace(draft, access_services=draft.access_services + (AccessService.create("Vacant Service"),))
    elif change == "unassigned":
        draft = replace(draft, publishers=(draft.publishers[0].model_copy(update={"access_service_id": None}),),
                        access_services=draft.access_services + (AccessService.create("Empty Service"),))
    writes = []
    original = settings_module._write_snapshot_target
    def write(path, contents, snapshot):
        writes.append(path)
        return original(path, contents, snapshot)
    monkeypatch.setattr(settings_module, "_write_snapshot_target", write)
    result = save_settings(config, draft)
    assert result.outcome is SettingsSaveOutcome.SAVED, result.issues
    md = change not in ("monitor", "institution", "noop")
    yaml = change in ("monitor", "institution", "both")
    assert (result.journal_written, result.monitor_written) == (md, yaml)
    assert writes == ([path] if md else []) + ([config] if yaml else [])
    if not md:
        assert stamp(path) == before[0]
    if not yaml:
        assert stamp(config) == before[1]
    journals, publishers, services = parse_settings_list_text(path.read_text(), path=path)
    assert journals == result.state.draft.journals
    assert publishers == result.state.draft.publishers
    assert services == result.state.draft.access_services
    assert services[0].id == existing_service.id


def test_two_publishers_one_service_without_group_cross_effect(tmp_path: Path):
    config, path, original = start(tmp_path)
    shared = original.access_services[0]
    journals = original.journals + (JournalConfig(name="Annals", issn_l=B, publisher_id=P2, group="Applied"),)
    publishers = original.publishers + (PublisherConfig(
        name="Publisher Two", publisher_id=P2, access_service_id=shared.id),)
    path.write_text(render_settings_list_text(path.read_text(), journals, publishers,
                                               access_services=(shared,), path=path))
    draft = load_settings(config).draft
    saved = save_settings(config, replace(draft, journals=(journals[0].model_copy(update={"group": "New Group"}), journals[1]),
                                           access_services=(shared.model_copy(update={"name": "University System"}),)))
    assert saved.outcome is SettingsSaveOutcome.SAVED
    assert all(p.access_service_id == shared.id for p in saved.state.draft.publishers)
    assert [j.group for j in saved.state.draft.journals] == ["New Group", "Applied"]


@pytest.mark.parametrize("kind", ["duplicate", "dangling", "unsafe", "forged_publisher", "drop_service", "cascade"])
def test_unsafe_or_unconfirmed_mapping_changes_make_zero_writes(tmp_path: Path, kind: str):
    config, path, draft = start(tmp_path)
    service = draft.access_services[0]
    if kind == "duplicate":
        draft = replace(draft, access_services=(service, service))
    elif kind == "dangling":
        draft = replace(draft, publishers=(draft.publishers[0].model_copy(
            update={"access_service_id": AccessService.create("Not in list").id}),))
    elif kind == "unsafe":
        draft = replace(draft, access_services=(service.model_copy(update={"access_url": "http://localhost"}),))
    elif kind == "forged_publisher":
        draft = replace(draft, publishers=(draft.publishers[0].model_copy(update={"name": "Forged"}),))
    elif kind == "drop_service":
        draft = replace(draft, access_services=())
    else:
        draft = replace(draft, journals=())
    before = (stamp(path), stamp(config))
    result = save_settings(config, draft)
    assert result.outcome is SettingsSaveOutcome.INVALID_DRAFT
    assert not result.journal_written and not result.monitor_written
    assert (stamp(path), stamp(config)) == before


@pytest.mark.parametrize("target", ["list", "monitor"])
def test_write_failure_reports_actual_partial_state(tmp_path: Path, monkeypatch, target: str):
    config, path, draft = start(tmp_path)
    draft = replace(draft, name="Edited", access_services=(
        draft.access_services[0].model_copy(update={"name": "New Service"}),))
    before = stamp(path), stamp(config)
    original = settings_module._write_snapshot_target
    writes = []
    def fail(p, contents, snapshot):
        writes.append(p)
        if p == (path if target == "list" else config):
            raise OSError("injected write failure")
        return original(p, contents, snapshot)
    monkeypatch.setattr(settings_module, "_write_snapshot_target", fail)
    result = save_settings(config, draft)
    assert result.outcome is (SettingsSaveOutcome.WRITE_FAILED if target == "list"
                              else SettingsSaveOutcome.PARTIAL_SAVE)
    assert result.journal_written == (target == "monitor")
    assert not result.monitor_written
    assert writes == ([path] if target == "list" else [path, config])
    if target == "list":
        assert (stamp(path), stamp(config)) == before
    else:
        assert stamp(config) == before[1]
        assert "New Service" in path.read_text()


def test_error_after_atomic_replace_reports_actual_writes(tmp_path: Path, monkeypatch):
    config, path, draft = start(tmp_path)
    draft = replace(draft, name="Edited", access_services=(
        draft.access_services[0].model_copy(update={"name": "New Service"}),))
    original = settings_module._write_snapshot_target
    def fail_after(p, contents, snapshot):
        original(p, contents, snapshot)
        raise OSError("post-commit synchronization failure")
    monkeypatch.setattr(settings_module, "_write_snapshot_target", fail_after)
    first = save_settings(config, draft)
    assert first.journal_written and not first.monitor_written
    assert first.outcome is SettingsSaveOutcome.PARTIAL_SAVE
    assert "New Service" in path.read_text()
    retry = replace(load_settings(config).draft, keyword_expression="statistics")
    second = save_settings(config, retry)
    assert second.monitor_written and not second.journal_written
    assert second.outcome is SettingsSaveOutcome.PARTIAL_SAVE
    assert load_settings(config).draft.keyword_expression == "statistics"


def test_web_htmx_preserves_service_draft_and_failed_attempt(tmp_path: Path):
    config, path, draft = start(tmp_path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        first = client.get("/settings")
        data = browser_settings_submission(first.text)
        assert data["service_id"] == [draft.access_services[0].id]
        assert data["service_name"] == ["University Library"]
        assert data["publisher_access_service_id"] == [draft.access_services[0].id]
        data["service_name"] = ["University North"]
        data["publisher_access_url"] = ["https://editor.example.org"]
        response = client.post("/settings/save", data=data)
        assert response.headers["HX-Trigger"] == "settingsSaved"
        assert load_settings(config).draft.access_services[0].name == "University North"
        assert load_settings(config).draft.publishers[0].publisher_url == "https://editor.example.org"
        data = browser_settings_submission(response.text)
        data["service_name"] = ["Still Draft"]
        data["service_access_url"] = ["http://127.0.0.1"]
        failed = client.post("/settings/save", data=data)
        assert "HX-Trigger" not in failed.headers
        preserved = browser_settings_submission(failed.text)
        assert preserved["service_name"] == ["Still Draft"]
        assert preserved["service_access_url"] == ["http://127.0.0.1"]
        assert preserved["journal_revision_digest"] == data["journal_revision_digest"]
    assert load_settings(config).draft.access_services[0].name == "University North"


def test_missing_service_membership_form_cannot_clear_relation(tmp_path: Path):
    config, path, draft = start(tmp_path)
    before = stamp(path), stamp(config)
    values = settings_form_from_draft(draft)
    data = [(key, value) for key, value in form_data(values).multi_items()
            if key != "publisher_access_service_id"]
    rebuilt, issues = settings_draft_from_form(settings_form_from_submission(FormData(data)))
    assert rebuilt is None and issues
    assert (stamp(path), stamp(config)) == before


def test_missing_service_rows_cannot_silently_delete_services_over_http(tmp_path: Path):
    config, path, draft = start(tmp_path)
    before = stamp(path), stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        data = browser_settings_submission(client.get("/settings").text)
        for field in ("service_id", "service_name", "service_access_url"):
            data.pop(field)
        rejected = client.post("/settings/save", data=data)
        assert "HX-Trigger" not in rejected.headers
        assert "Settings draft is invalid" in rejected.text or "Settings issues" in rejected.text
    assert (stamp(path), stamp(config)) == before


def test_import_preview_uses_current_unsaved_service_fields(tmp_path: Path):
    config, path, draft = start(tmp_path)
    before = stamp(path), stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        data = browser_settings_submission(client.get("/settings").text)
        data["service_name"] = ["Draft Library"]
        data["publisher_access_url"] = ["https://draft.example.org"]
        preview = client.post("/settings/import/preview", data=data)
        fields = browser_settings_submission(preview.text)
        assert fields["service_name"] == ["Draft Library"]
        assert fields["service_id"] == [draft.access_services[0].id]
        assert fields["publisher_access_url"] == ["https://draft.example.org"]
        assert fields["publisher_access_service_id"] == [draft.access_services[0].id]
    assert (stamp(path), stamp(config)) == before


def test_monitor_conflict_after_target_preparation_blocks_journal_write(tmp_path: Path, monkeypatch):
    config, path, draft = start(tmp_path)
    draft = replace(draft, access_services=(
        draft.access_services[0].model_copy(update={"name": "Edited Service"}),))
    journal_before = stamp(path)
    original_render = settings_module.render_settings_list_text
    def render(*args, **kwargs):
        target = original_render(*args, **kwargs)
        config.write_bytes(config.read_bytes() + b"# concurrent monitor edit\n")
        return target
    monkeypatch.setattr(settings_module, "render_settings_list_text", render)
    result = save_settings(config, draft)
    assert result.outcome is SettingsSaveOutcome.REVISION_CONFLICT
    assert not result.journal_written and not result.monitor_written
    assert stamp(path) == journal_before


def test_second_file_cas_conflict_reports_written_list(tmp_path: Path, monkeypatch):
    config, path, draft = start(tmp_path)
    draft = replace(draft, name="Edited Monitor", access_services=(
        draft.access_services[0].model_copy(update={"name": "Edited Service"}),))
    original = settings_module._write_snapshot_target
    def change_monitor_after_list(target, contents, snapshot):
        original(target, contents, snapshot)
        if target == path:
            config.write_bytes(config.read_bytes() + b"# concurrent monitor edit\n")
    monkeypatch.setattr(settings_module, "_write_snapshot_target", change_monitor_after_list)
    result = save_settings(config, draft)
    assert result.outcome is SettingsSaveOutcome.PARTIAL_SAVE
    assert result.journal_written and not result.monitor_written
    assert result.state.draft.access_services[0].name == "Edited Service"
    assert result.state.draft.name != "Edited Monitor"
