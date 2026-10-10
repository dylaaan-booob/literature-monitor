"""v0.6.4 A1: canonical Markdown schema and explicit single-file upgrade."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import re
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import literature_monitor.application.access_service_migration as upgrade_module
import literature_monitor.safe_write as safe_write
from literature_monitor.application.access_service_migration import (
    AccessSchemaUpgradeCommittedWarning,
    AccessSchemaUpgradeStateUncertain,
    confirm_access_schema_upgrade,
    preview_access_schema_upgrade,
)
from literature_monitor.application.settings import (
    SettingsIssueSource,
    SettingsSaveOutcome,
    SettingsValidationOutcome,
    load_settings,
    save_settings,
    validate_settings,
)
from literature_monitor.config import (
    AccessService,
    ConfigurationError,
    JournalConfig,
    PublisherConfig,
    detect_list_schema,
    load_config,
    parse_settings_list_text,
    render_settings_list_text,
    validate_access_mapping,
    validate_access_services,
)
from literature_monitor.safe_write import ContentChangedError
from literature_monitor.web.app import create_app
from test_web_run_settings import browser_settings_submission

P1 = "https://openalex.org/P1"
P2 = "https://openalex.org/P2"
A = "0006-341X"
B = "0090-5364"
LEGACY = (
    "# My venues\n\nManual introduction\n\n"
    "## Journals\n\n| Journal | ISSN-L | Publisher ID | Group |\n"
    "| --- | --- | --- | --- |\n"
    f"| Biometrics | {A} | {P1} | Biostatistics |\n"
    f"| Annals | {B} | {P2} | Methods |\n\n"
    "## Publishers\n\n| Publisher | OpenAlex ID | Access URL |\n"
    "| --- | --- | --- |\n"
    f"| Publisher One | {P1} | https://example.org/manual |\n"
    f"| Publisher Two | {P2} |  |\n\n"
    "## Conferences\n\n| Conference | Notes |\n| --- | --- |\n"
    "| ML | human content |\n\n## My Notes\n\nHuman free text.\n"
)


def document(tmp_path: Path, contents: str = LEGACY) -> Path:
    target = tmp_path / "list.md"
    target.write_text(contents, encoding="utf-8")
    return target


def monitor(tmp_path: Path) -> Path:
    config = tmp_path / "monitor.yaml"
    config.write_text(
        "name: Test Monitor\nvenue_whitelist: list.md\nkeyword_expression: causal\n"
        "output_dir: workspace\nwindow_days: 7\n", encoding="utf-8",
    )
    return config


def test_canonical_roundtrip_and_independent_group_service_mapping(tmp_path: Path):
    service = AccessService.create("University Access", "https://library.example.org")
    vacant = AccessService.create("Unused")
    journals = (
        JournalConfig(name="Biometrics", issn_l=A, publisher_id=P1, group="Statistics"),
        JournalConfig(name="Annals", issn_l=B, publisher_id=P2, group="Other"),
    )
    publishers = (
        PublisherConfig(name="Publisher One", publisher_id=P1,
                        publisher_url="https://example.org/manual", access_service_id=service.id),
        PublisherConfig(name="Publisher Two", publisher_id=P2, access_service_id=service.id),
    )
    original = LEGACY
    path = tmp_path / "list.md"
    rendered = render_settings_list_text(original, journals, publishers, access_services=(service, vacant), path=path)
    assert detect_list_schema(rendered, path=path) == "current"
    assert parse_settings_list_text(rendered, path=path) == (journals, publishers, (service, vacant))
    assert "## Conferences" + rendered.split("## Conferences")[1] == (
        "## Conferences" + original.split("## Conferences")[1]
    )
    assert render_settings_list_text(rendered, journals, publishers, access_services=(service, vacant), path=path) == rendered
    assert "Biostatistics" not in rendered.split("## Journals")[1].split("## Publishers")[0]
    assert service.id == service.model_copy(update={"name": "Renamed", "access_url": None}).id
    assert publishers[0].publisher_url == "https://example.org/manual"
    assert vacant.id not in {p.access_service_id for p in publishers}


def test_unassigned_and_empty_service(tmp_path: Path):
    path = tmp_path / "list.md"
    journal = JournalConfig(name="Biometrics", issn_l=A, publisher_id=P1)
    publisher = PublisherConfig(name="Publisher", publisher_id=P1)
    unused = AccessService.create("Unused")
    content = render_settings_list_text(None, (journal,), (publisher,), access_services=(unused,), path=path)
    assert parse_settings_list_text(content, path=path)[2] == (unused,)
    assert parse_settings_list_text(content, path=path)[1][0].access_service_id is None


@pytest.mark.parametrize("fault", ["duplicate_id", "duplicate_name", "dangling", "pipe", "control",
                                     "bad_publisher_url", "bad_service_url", "bad_id"])
def test_invalid_service_metadata_and_memberships_rejected(fault: str):
    service = AccessService.create("Main")
    publisher = PublisherConfig(publisher_id=P1, name="Publisher")
    if fault == "duplicate_id":
        services = (service, service)
    elif fault == "duplicate_name":
        services = (service, AccessService.create(" main "))
    elif fault == "pipe":
        services = (service.model_copy(update={"name": "Pipe | unsupported"}),)
    elif fault == "control":
        services = (service.model_copy(update={"name": "Bad\tname"}),)
    elif fault == "bad_service_url":
        services = (service.model_copy(update={"access_url": "http://127.0.0.1"}),)
    elif fault == "bad_id":
        services = (service.model_copy(update={"id": "Svc-name"}),)
    else:
        services = (service,)
    if fault == "dangling":
        publisher = publisher.model_copy(update={"access_service_id": AccessService.create("Absent").id})
    if fault == "bad_publisher_url":
        publisher = publisher.model_copy(update={"publisher_url": "http://localhost"})
    with pytest.raises((ConfigurationError, ValueError)):
        validate_access_mapping((publisher,), validate_access_services(services))


@pytest.mark.parametrize("fault", [
    "duplicate_services", "duplicate_publishers", "duplicate_journals", "mixed",
    "missing_services", "truncated", "wrong_separator", "wrong_header", "dangling",
])
def test_malformed_schema_rejected(tmp_path: Path, fault: str):
    service = AccessService.create("Access")
    journal = JournalConfig(name="Biometrics", issn_l=A, publisher_id=P1)
    publisher = PublisherConfig(name="Publisher", publisher_id=P1,
                                publisher_url="https://example.org", access_service_id=service.id)
    path = tmp_path / "list.md"
    content = render_settings_list_text(None, (journal,), (publisher,), access_services=(service,), path=path)
    if fault == "duplicate_services":
        content += "\n## Access Services\n\n| Service ID | Service | Access URL |\n|---|---|---|\n"
    elif fault == "duplicate_publishers":
        content += "\n## Publishers\n\n| Publisher | OpenAlex ID | Publisher URL | Access Service ID |\n|---|---|---|---|\n"
    elif fault == "duplicate_journals":
        content += "\n## Journals\n\n| Journal | ISSN-L | Publisher ID | Group |\n|---|---|---|---|\n"
    elif fault == "mixed":
        content = content.replace("Publisher URL | Access Service ID", "Access URL")
    elif fault == "missing_services":
        content = content.split("## Access Services")[0]
    elif fault == "truncated":
        content = content.replace("| --- | --- | --- |\n", "")
    elif fault == "wrong_separator":
        content = content.replace("| --- | --- | --- |\n", "| -- | --- | --- |\n")
    elif fault == "wrong_header":
        content = content.replace("| Service ID | Service | Access URL |", "| ID | Service | Access URL |")
    elif fault == "dangling":
        content = content.replace(f"| {service.id} | {service.name} |", f"| {AccessService.create('Other').id} | {service.name} |")
    with pytest.raises(ConfigurationError):
        detect_list_schema(content, path=path)


def test_preview_confirm_preserves_real_source_and_old_run(tmp_path: Path):
    path = document(tmp_path)
    config = monitor(tmp_path)
    original = path.read_bytes()
    old = load_config(config)
    assert len(old.journals) == 2 and [j.group for j in old.journals] == ["Biostatistics", "Methods"]
    preview = preview_access_schema_upgrade(path)
    assert preview.journal_count == 2 and preview.publisher_count == 2
    assert path.read_bytes() == original
    with pytest.raises(ConfigurationError):
        confirm_access_schema_upgrade(preview, confirmed=False)
    assert path.read_bytes() == original
    confirm_access_schema_upgrade(preview, confirmed=True)
    assert detect_list_schema(path.read_text(), path=path) == "current"
    journals, publishers, services = parse_settings_list_text(path.read_text(), path=path)
    assert len(journals) == 2 and len(publishers) == 2 and services == ()
    assert publishers[0].publisher_url == "https://example.org/manual"
    assert all(p.access_service_id is None for p in publishers)
    assert "## Conferences" + path.read_text().split("## Conferences")[1] == (
        "## Conferences" + LEGACY.split("## Conferences")[1]
    )
    assert load_config(config).journals == old.journals
    with pytest.raises(ConfigurationError):
        preview_access_schema_upgrade(path)


def test_stale_preview_and_corruption_are_atomic(tmp_path: Path):
    path = document(tmp_path)
    preview = preview_access_schema_upgrade(path)
    path.write_text(LEGACY + "\n# Concurrent user edit\n", encoding="utf-8")
    current = path.read_bytes()
    with pytest.raises(ContentChangedError):
        confirm_access_schema_upgrade(preview, confirmed=True)
    assert path.read_bytes() == current
    path.write_text(LEGACY.replace("## Publishers", "## Publishers\n\n## Publishers"), encoding="utf-8")
    current = path.read_bytes()
    with pytest.raises(ConfigurationError):
        preview_access_schema_upgrade(path)
    assert path.read_bytes() == current


def test_same_bytes_inode_replacement_and_symlink_substitution_are_rejected(tmp_path: Path):
    path = document(tmp_path)
    preview = preview_access_schema_upgrade(path)
    replacement = tmp_path / "replacement.md"
    replacement.write_bytes(path.read_bytes())
    replacement.replace(path)
    assert path.read_bytes() == LEGACY.encode()
    with pytest.raises(ContentChangedError, match="location changed"):
        confirm_access_schema_upgrade(preview, confirmed=True)
    assert path.read_bytes() == LEGACY.encode()

    preview = preview_access_schema_upgrade(path)
    actual = tmp_path / "moved.md"
    path.rename(actual)
    path.symlink_to(actual)
    with pytest.raises(ConfigurationError):
        confirm_access_schema_upgrade(preview, confirmed=True)
    assert path.is_symlink() and actual.read_bytes() == LEGACY.encode()


def test_symlink_and_write_failures_do_not_replace_original(tmp_path: Path, monkeypatch):
    path = document(tmp_path)
    preview = preview_access_schema_upgrade(path)
    original = path.read_bytes()
    import literature_monitor.application.access_service_migration as migration

    def denied(*args, **kwargs):
        raise OSError("injected disk failure")

    monkeypatch.setattr(migration, "replace_regular_text_at_identity", denied)
    with pytest.raises(OSError, match="injected disk failure"):
        confirm_access_schema_upgrade(preview, confirmed=True)
    assert path.read_bytes() == original
    link = tmp_path / "link.md"
    link.symlink_to(path)
    with pytest.raises(ConfigurationError):
        preview_access_schema_upgrade(link)


def fail_directory_fsync(monkeypatch, *, after_replace=None):
    """Leave file fsync intact; fail only after the real atomic rename of list.md."""
    real_fsync = safe_write.os.fsync

    def fail(fd):
        if stat.S_ISDIR(safe_write.os.fstat(fd).st_mode):
            if after_replace is not None:
                after_replace()
            raise OSError("injected directory fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(safe_write.os, "fsync", fail)


def test_post_replace_sync_error_reports_verified_upgrade_and_actual_revision(tmp_path: Path, monkeypatch):
    path = document(tmp_path)
    config = monitor(tmp_path)
    old_monitor = config.read_bytes()
    preview = preview_access_schema_upgrade(path)
    fail_directory_fsync(monkeypatch)

    with pytest.raises(AccessSchemaUpgradeCommittedWarning) as captured:
        confirm_access_schema_upgrade(preview, confirmed=True)

    assert path.read_bytes() == preview.target_contents.encode("utf-8")
    assert detect_list_schema(path.read_text(), path=path) == "current"
    assert captured.value.revision == hashlib.sha256(path.read_bytes()).hexdigest()
    assert load_settings(config).draft.journal_revision.digest == captured.value.revision
    assert config.read_bytes() == old_monitor


@pytest.mark.parametrize("failure", ["unreadable", "changed_bytes", "replaced_identity"])
def test_post_replace_sync_error_with_untrusted_readback_is_uncertain(tmp_path: Path, monkeypatch, failure: str):
    path = document(tmp_path)
    config = monitor(tmp_path)
    preview = preview_access_schema_upgrade(path)
    original_monitor = config.read_bytes()
    if failure == "unreadable":
        read = upgrade_module._read_snapshot
        calls = 0

        def fail_read(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected readback failure")
            return read(*args, **kwargs)

        monkeypatch.setattr(upgrade_module, "_read_snapshot", fail_read)
        after_replace = None
    elif failure == "changed_bytes":
        after_replace = lambda: path.write_bytes(preview.target_contents.encode() + b"\n# changed during sync\n")
    else:
        def after_replace():
            replacement = tmp_path / "different-inode.md"
            replacement.write_bytes(path.read_bytes())
            replacement.replace(path)
    fail_directory_fsync(monkeypatch, after_replace=after_replace)

    with pytest.raises(AccessSchemaUpgradeStateUncertain, match="unconfirmed"):
        confirm_access_schema_upgrade(preview, confirmed=True)
    assert config.read_bytes() == original_monitor


def test_web_upgrade_sync_warning_reloads_disk_schema_and_revision(tmp_path: Path, monkeypatch):
    path = document(tmp_path)
    config = monitor(tmp_path)
    monitor_before = config.read_bytes()
    with TestClient(create_app(config), base_url="http://localhost") as browser:
        data = browser_settings_submission(browser.get("/settings").text)
        preview_response = browser.post("/settings/upgrade/preview", data=data)
        assert "data-access-upgrade-plan" in preview_response.text
        planned = preview_access_schema_upgrade(path)
        fail_directory_fsync(monkeypatch)
        response = browser.post("/settings/upgrade/confirm", data=browser_settings_submission(preview_response.text))

    assert response.status_code == 200
    assert response.headers.get("HX-Trigger") == "settingsSaved"
    assert "Upgrade not written" not in response.text
    assert "directory synchronization failed" in response.text
    assert "Durable persistence is not confirmed" in response.text
    assert "Check storage health" in response.text
    assert path.read_bytes() == planned.target_contents.encode()
    assert config.read_bytes() == monitor_before
    state = load_settings(config)
    saved_revision = state.draft.journal_revision.digest
    assert saved_revision == hashlib.sha256(path.read_bytes()).hexdigest()
    assert saved_revision in response.text
    assert 'name="journal_revision_digest" value="' + saved_revision + '"' in response.text
    assert "data-preview-access-upgrade" not in response.text
    assert 'name="publisher_access_service_id"' in response.text


def test_web_upgrade_pre_replace_failure_keeps_original_and_reports_no_write(tmp_path: Path, monkeypatch):
    path = document(tmp_path)
    config = monitor(tmp_path)
    original = path.read_bytes(), config.read_bytes()
    with TestClient(create_app(config), base_url="http://localhost") as browser:
        page = browser.get("/settings")
        preview = browser.post("/settings/upgrade/preview", data=browser_settings_submission(page.text))
        assert "data-access-upgrade-plan" in preview.text

        def refuse_rename(*args, **kwargs):
            raise OSError("injected failure before atomic replacement")

        monkeypatch.setattr(safe_write.os, "replace", refuse_rename)
        response = browser.post("/settings/upgrade/confirm", data=browser_settings_submission(preview.text))

    assert "Upgrade not written" in response.text
    assert "directory synchronization failed" not in response.text
    assert response.headers.get("HX-Trigger") is None
    assert (path.read_bytes(), config.read_bytes()) == original


def test_web_upgrade_stale_confirmation_rejects_external_changes(tmp_path: Path):
    path = document(tmp_path)
    config = monitor(tmp_path)
    monitor_before = config.read_bytes()
    with TestClient(create_app(config), base_url="http://localhost") as browser:
        preview = browser.post(
            "/settings/upgrade/preview",
            data=browser_settings_submission(browser.get("/settings").text),
        )
        assert "data-access-upgrade-plan" in preview.text
        original_form = browser_settings_submission(preview.text)
        path.write_bytes(path.read_bytes() + b"\n# external manual change\n")
        after_external_change = path.read_bytes()
        response = browser.post("/settings/upgrade/confirm", data=original_form)

    assert "Upgrade not written" in response.text
    assert "Unsaved Settings Draft" in response.text or "stale" in response.text.lower()
    assert response.headers.get("HX-Trigger") is None
    assert path.read_bytes() == after_external_change
    assert config.read_bytes() == monitor_before


def test_web_upgrade_uncertain_sync_failure_does_not_claim_no_write(tmp_path: Path, monkeypatch):
    path = document(tmp_path)
    config = monitor(tmp_path)
    monitor_before = config.read_bytes()
    with TestClient(create_app(config), base_url="http://localhost") as browser:
        data = browser_settings_submission(browser.get("/settings").text)
        preview_response = browser.post("/settings/upgrade/preview", data=data)
        assert "data-access-upgrade-plan" in preview_response.text
        fail_directory_fsync(monkeypatch, after_replace=lambda: path.write_bytes(b"invalid post-write state"))
        response = browser.post("/settings/upgrade/confirm", data=browser_settings_submission(preview_response.text))

    assert "write state cannot be confirmed" in response.text
    assert "Upgrade not written" not in response.text
    assert "upgrade saved" not in response.text.lower()
    assert response.headers.get("HX-Trigger") is None
    assert config.read_bytes() == monitor_before


def test_old_settings_save_never_upgrades_and_mapping_safe_guard(tmp_path: Path):
    path = document(tmp_path)
    config = monitor(tmp_path)
    draft = load_settings(config).draft
    original = (path.read_bytes(), config.read_bytes())
    result = save_settings(config, replace(draft, keyword_expression="changed"))
    assert result.outcome is SettingsSaveOutcome.INVALID_DRAFT
    assert (path.read_bytes(), config.read_bytes()) == original

    preview = preview_access_schema_upgrade(path)
    confirm_access_schema_upgrade(preview, confirmed=True)
    service = AccessService.create("Mapped")
    journals, publishers, _ = parse_settings_list_text(path.read_text(), path=path)
    mapped = (publishers[0].model_copy(update={"access_service_id": service.id}), publishers[1])
    path.write_text(render_settings_list_text(path.read_text(), journals, mapped, access_services=(service,), path=path))
    original = (path.read_bytes(), config.read_bytes())
    draft = load_settings(config).draft
    assert draft.access_services == (service,)
    # A2 permits a monitor-only update with mapped services; list.md is untouched.
    result = save_settings(config, replace(draft, keyword_expression="changed"))
    assert result.outcome is SettingsSaveOutcome.SAVED
    assert not result.journal_written and result.monitor_written
    assert path.read_bytes() == original[0]
    assert config.read_bytes() != original[1]
    assert load_config(config).journals == tuple(journals)


def current_mapping_document(tmp_path: Path):
    service = AccessService.create("University Library", "https://library.example.org")
    journal = JournalConfig(name="Biometrics", issn_l=A, publisher_id=P1, group="Statistics")
    publisher = PublisherConfig(
        name="Publisher One", publisher_id=P1,
        publisher_url="https://publisher.example.org/manual", access_service_id=service.id,
    )
    config = monitor(tmp_path)
    path = document(tmp_path, render_settings_list_text(
        "# Venues\n\n## Conferences\n\nUser-maintained conference notes.\n",
        (journal,), (publisher,), access_services=(service,), path=tmp_path / "list.md",
    ))
    return config, path, journal, publisher, service


@pytest.mark.parametrize("damage,section,expected", [
    ("service_header", "access_services", (1, 1, 0)),
    ("service_url", "access_services", (1, 1, 0)),
    ("publisher_header", "publishers", (1, 0, 1)),
    ("journal_bad_row", "journals", (1, 1, 1)),
    ("service_missing", "access_services", (1, 1, 0)),
])
def test_settings_independent_sections_remain_visible_when_one_is_damaged(
    tmp_path: Path, damage: str, section: str, expected: tuple[int, int, int],
):
    config, path, journal, publisher, service = current_mapping_document(tmp_path)
    text = path.read_text()
    if damage == "service_header":
        text = text.replace("| Service ID | Service | Access URL |", "| ID | Service | Access URL |")
    elif damage == "service_url":
        text = text.replace("https://library.example.org", "http://localhost")
    elif damage == "publisher_header":
        text = text.replace("| Publisher | OpenAlex ID | Publisher URL | Access Service ID |",
                            "| Publisher | Wrong ID | Publisher URL | Access Service ID |")
    elif damage == "journal_bad_row":
        text = text.replace(f"| {journal.name} | {journal.issn_l} | {P1} | Statistics |",
                            f"| {journal.name} | {journal.issn_l} | {P1} | Statistics |\n"
                            "| Broken | not-an-issn |  |  |")
    else:
        text = re.sub(r"(?ms)^## Access Services\n.*?(?=^## |\Z)", "", text)
    path.write_text(text)
    before = (path.read_bytes(), config.read_bytes())

    state = load_settings(config)
    assert (len(state.draft.journals), len(state.draft.publishers), len(state.draft.access_services)) == expected
    assert len(state.issues) == 1
    assert state.issues[0].source is SettingsIssueSource.JOURNAL_DATA
    assert state.issues[0].field == section
    if damage != "publisher_header":
        assert state.draft.publishers == (publisher,)
    if damage != "journal_bad_row":
        assert state.draft.journals == (journal,)
    if damage in ("publisher_header", "journal_bad_row"):
        assert state.draft.access_services == (service,)

    with pytest.raises(ConfigurationError):
        parse_settings_list_text(text, path=path)
    result = save_settings(config, replace(state.draft, keyword_expression="statistics"))
    assert result.outcome is SettingsSaveOutcome.INVALID_DRAFT
    assert not result.journal_written and not result.monitor_written
    assert (path.read_bytes(), config.read_bytes()) == before


@pytest.mark.parametrize("damage", [
    "dangling_service", "missing_services", "invalid_service_header",
    "duplicate_service_id", "unsafe_service_url", "unsafe_publisher_url",
])
def test_run_ignores_optional_mapping_errors_while_settings_remains_strict(tmp_path: Path, damage: str):
    config, path, journal, publisher, service = current_mapping_document(tmp_path)
    text = path.read_text()
    if damage == "dangling_service":
        text = text.replace(f"| {service.id} | {service.name} |", f"| {AccessService.create('Other').id} | {service.name} |")
    elif damage == "missing_services":
        text = re.sub(r"(?ms)^## Access Services\n.*?(?=^## |\Z)", "", text)
    elif damage == "invalid_service_header":
        text = text.replace("| Service ID | Service | Access URL |", "| Invalid | Service | Access URL |")
    elif damage == "duplicate_service_id":
        text = text.replace(
            f"| {service.id} | {service.name} | {service.access_url} |",
            f"| {service.id} | {service.name} | {service.access_url} |\n"
            f"| {service.id} | Duplicate |  |",
        )
    elif damage == "unsafe_service_url":
        text = text.replace("https://library.example.org", "http://127.0.0.1")
    else:
        text = text.replace(publisher.publisher_url, "http://localhost")
    path.write_text(text)
    before = (config.read_bytes(), path.read_bytes())

    loaded = load_config(config)
    assert loaded.journals == (journal,)
    with pytest.raises(ConfigurationError):
        parse_settings_list_text(text, path=path)
    state = load_settings(config)
    assert state.issues
    assert state.draft.journals == (journal,)
    result = save_settings(config, state.draft)
    assert result.outcome is SettingsSaveOutcome.INVALID_DRAFT
    assert not result.monitor_written and not result.journal_written
    assert (config.read_bytes(), path.read_bytes()) == before


def test_corrupt_unassigned_service_cannot_pass_storage_save_guard(tmp_path: Path):
    config, path, journal, publisher, service = current_mapping_document(tmp_path)
    unassigned = publisher.model_copy(update={"access_service_id": None})
    text = render_settings_list_text(
        path.read_text(), (journal,), (unassigned,),
        access_services=(service,), path=path,
    ).replace("| Service ID | Service | Access URL |", "| Wrong ID | Service | Access URL |")
    path.write_text(text)
    before = (config.read_bytes(), path.read_bytes())
    state = load_settings(config)
    assert state.draft.journals == (journal,)
    assert state.draft.publishers == (unassigned,)
    assert state.draft.access_services == ()
    assert len(state.issues) == 1 and state.issues[0].field == "access_services"
    # Draft-only validation does not substitute for strict disk/schema checks.
    assert validate_settings(config, state.draft).outcome is SettingsValidationOutcome.VALID
    saved = save_settings(config, replace(state.draft, keyword_expression="statistics"))
    assert saved.outcome is SettingsSaveOutcome.INVALID_DRAFT
    assert not saved.journal_written and not saved.monitor_written
    assert (config.read_bytes(), path.read_bytes()) == before


@pytest.mark.parametrize("journal_error", ["checksum", "duplicate", "bad_publisher_id"])
def test_run_remains_strict_about_journal_identity(tmp_path: Path, journal_error: str):
    config, path, journal, publisher, service = current_mapping_document(tmp_path)
    text = path.read_text()
    row = f"| Biometrics | {A} | {P1} | Statistics |"
    if journal_error == "checksum":
        text = text.replace(row, row.replace(A, "0006-3411"))
    elif journal_error == "duplicate":
        text = text.replace(row, row + "\n" + row)
    else:
        text = text.replace(row, row.replace(P1, "not-a-publisher"))
    path.write_text(text)
    before = (path.read_bytes(), config.read_bytes())
    with pytest.raises(ConfigurationError):
        load_config(config)
    assert (path.read_bytes(), config.read_bytes()) == before
