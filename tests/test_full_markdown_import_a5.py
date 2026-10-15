"""SPEC §43.5 full-file Markdown Import/Export/Reload, isolated from user files."""
from __future__ import annotations

from contextlib import nullcontext
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
from settings_api_helpers import form as settings_form_data


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


def new_publisher_document(path: Path, service_id: str | None, *, renamed: bool = False) -> str:
    original = path.read_text()
    journals, publishers, services = parse_settings_list_text(original, path=path)
    publisher_id = "https://openalex.org/P123"
    journal = JournalConfig(name="New Journal", issn_l="1541-0420", publisher_id=publisher_id)
    publisher = PublisherConfig(name="New Publisher", publisher_id=publisher_id,
                                publisher_url="https://newpublisher.example", access_service_id=service_id)
    if renamed:
        services = tuple(s.model_copy(update={"name": "Renamed Service"}) if s.id == service_id else s
                         for s in services)
    return render_settings_list_text(original, (*journals, journal), (*publishers, publisher),
                                     access_services=services, path=path)


def submission(client):
    return settings_form_data(client.get("/api/ui-next/settings").json())


def preview(client, data, source: str, *, filename="import.md", encoding="utf-8"):
    data = dict(data)
    data.pop("markdown_file", None)
    return client.post("/settings/import/preview", data=data,
                       files={"markdown_file": (filename, source.encode(encoding), "text/markdown")})


@pytest.mark.parametrize("damage", ["incomplete", "legacy", "unsafe_url", "dangling_service",
                                   "wrong_journal_name", "wrong_publisher_name",
                                   "duplicate_service_id", "duplicate_service_name", "duplicate_publisher_id"])
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
    elif damage == "duplicate_service_name":
        source = current.replace(f"| {spare.id} | {spare.name} |", f"| {spare.id} |  IMPORTED  |")
    else:
        source = current.replace(f"| {P2} |", f"| {P1} |", 1)
    previous = stamp(path), stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        result = preview(client, submission(client), source)
        assert not result.json().get("import_plan"), (damage, result.json())
        assert "Import Preview blocked" in result.json()["message"]
    assert (stamp(path), stamp(config)) == previous


def test_empty_group_draft_is_not_silently_discarded(tmp_path):
    config, path, _, _ = prepared(tmp_path)
    original = stamp(path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        data = submission(client)
        data["settings_group"] = ["Statistics", "Other", "New empty Group"]
        selected = preview(client, data, candidate(path)).json()
        assert selected["import_plan"]
        assert selected["values"]["groups"][-1] == "New empty Group"
        assert selected["dirty"] is True
        confirm_data = dict(data, markdown_import_payload=selected["import_payload"],
                            markdown_import_filename=selected["import_filename"],
                            markdown_import_proof=selected["import_proof"],
                            confirm_full_import="yes")
        blocked = client.post("/settings/import/confirm", data=confirm_data).json()
        assert "Unsaved Settings Draft" in blocked["message"]
        assert stamp(path) == original
        guarded_export = client.post("/settings/export", data=data)
        assert guarded_export.json()["export_guard"]
        assert stamp(path) == original


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
            assert "Import Preview blocked" in invalid.json()["message"]
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


def test_old_draft_import_endpoint_is_not_available(tmp_path):
    config, path, _, _ = prepared(tmp_path)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        assert client.post("/settings/import/apply", data={}).status_code == 404


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


@pytest.mark.parametrize("damage", ["new_service", "removed_service", "invalid_id", "dangling_service"])
def test_new_publisher_rejects_unavailable_service_without_write(tmp_path, damage):
    config, path, _, spare = prepared(tmp_path)
    source = new_publisher_document(path, spare.id)
    if damage == "new_service":
        journals, publishers, services = parse_settings_list_text(source, path=path)
        added = AccessService.create("New Service", "https://newservice.example")
        publishers = (*publishers[:-1], publishers[-1].model_copy(update={"access_service_id": added.id}))
        source = render_settings_list_text(source, journals, publishers,
                                           access_services=(*services, added), path=path)
        # 完整关系本身有效，拒绝原因只能是该 Service 尚未保存。
        parse_settings_list_text(source, path=path)
        message = "must reference a saved Access Service ID"
    elif damage == "removed_service":
        source = "\n".join(line for line in source.split("\n") if not line.startswith(f"| {spare.id} |"))
        message = "unknown Access Service"
    else:
        invalid_id = "not-a-service-id" if damage == "invalid_id" else "svc_" + "a" * 32
        source = source.replace(f"| {spare.id} |", f"| {invalid_id} |", 1)
        message = "not-a-service-id" if damage == "invalid_id" else "Access Service"
    before = stamp(path), stamp(config)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        result = preview(client, submission(client), source)
        assert not result.json().get("import_plan")
        assert "Import Preview blocked" in result.json()["message"]
        assert message in result.json()["message"]
    assert (stamp(path), stamp(config)) == before
