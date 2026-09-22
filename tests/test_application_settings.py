from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

import literature_monitor.application.monitor as monitor_module
import literature_monitor.application.settings as settings_module
import literature_monitor.config as config_module
from literature_monitor.application.settings import (
    ContentRevision,
    SettingsIssueSource,
    SettingsSaveOutcome,
    SettingsValidationOutcome,
    load_settings,
    save_settings,
    validate_settings,
)
from literature_monitor.config import (
    JournalConfig,
    LogLevel,
    load_config,
    parse_journal_whitelist,
)
from literature_monitor.date_range import DateRangeSpec
from literature_monitor.search import SearchBackendError


JOURNAL_DOCUMENT = """\
# Venues

Intro text that is not owned by Settings.

## Journals

| Journal | ISSN/EISSN |
|---|---|
| Biometrics | 0006-341X |
| Annals of Applied Statistics | 1932-6157 / 1941-7330 |

## Conferences

| Abbreviation | Full Name |
|---|---|
| TESTCONF | Test Conference |
"""


def write_valid_settings_files(
    tmp_path: Path,
    *,
    monitor_name: str = "Statistical Monitor",
    keyword_expression: str = "causal",
    output_dir: str = "./workspace",
    journal_name: str = "venues.md",
    journal_contents: str = JOURNAL_DOCUMENT,
) -> tuple[Path, Path]:
    journal_path = tmp_path / journal_name
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.write_text(journal_contents, encoding="utf-8")
    config_path = tmp_path / "monitor.yaml"
    config_path.write_text(
        f"name: {monitor_name}\n"
        f"venue_whitelist: {journal_name}\n"
        f"keyword_expression: {keyword_expression}\n"
        f"output_dir: {output_dir}\n"
        "window_days: 14\n"
        "log_level: INFO\n",
        encoding="utf-8",
    )
    return config_path, journal_path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def valid_journals() -> tuple[JournalConfig, ...]:
    return (
        JournalConfig(name="Biometrics", issn=("0006-341X",)),
        JournalConfig(
            name="Annals of Applied Statistics",
            issn=("1932-6157", "1941-7330"),
        ),
    )


def test_load_valid_settings_returns_complete_structured_draft(tmp_path: Path) -> None:
    config_path, journal_path = write_valid_settings_files(tmp_path)

    result = load_settings(config_path)

    assert result.issues == ()
    assert result.config_path == config_path.resolve()
    assert result.journal_path == journal_path.resolve()
    assert result.draft.name == "Statistical Monitor"
    assert result.draft.keyword_expression == "causal"
    assert result.draft.journals == valid_journals()
    assert result.draft.date_spec == DateRangeSpec(window_days=14)
    assert result.draft.output_dir == Path("workspace")
    assert result.draft.log_level is LogLevel.INFO
    assert result.venue_whitelist == Path("venues.md")
    assert not hasattr(result.draft, "venue_whitelist")
    assert result.draft.monitor_revision == ContentRevision(
        exists=True,
        digest=sha256(config_path),
    )
    assert result.draft.journal_revision == ContentRevision(
        exists=True,
        digest=sha256(journal_path),
    )


def test_exact_revisions_change_for_comments_whitespace_and_newlines(tmp_path: Path) -> None:
    config_path, journal_path = write_valid_settings_files(tmp_path)
    first = load_settings(config_path)

    config_path.write_bytes(config_path.read_bytes() + b"# comment only\n")
    journal_path.write_bytes(journal_path.read_bytes().replace(b"\n", b"\r\n"))
    second = load_settings(config_path)

    assert second.draft.monitor_revision != first.draft.monitor_revision
    assert second.draft.journal_revision != first.draft.journal_revision
    assert second.draft.monitor_revision.digest == sha256(config_path)
    assert second.draft.journal_revision.digest == sha256(journal_path)


def test_missing_monitor_is_recoverable_and_uses_defaults(tmp_path: Path) -> None:
    journal_path = tmp_path / "list.md"
    journal_path.write_text(JOURNAL_DOCUMENT, encoding="utf-8")
    config_path = tmp_path / "missing-monitor.yaml"

    result = load_settings(config_path)

    assert result.draft.name == "missing-monitor"
    assert result.draft.keyword_expression == ""
    assert result.draft.output_dir == Path("workspace")
    assert result.draft.date_spec == DateRangeSpec(window_days=14)
    assert result.draft.log_level is LogLevel.INFO
    assert result.draft.journals == valid_journals()
    assert not result.draft.monitor_revision.exists
    assert any(
        issue.source is SettingsIssueSource.MONITOR_CONFIG
        and "missing" in issue.message
        for issue in result.issues
    )


def test_malformed_monitor_yaml_is_recoverable(tmp_path: Path) -> None:
    (tmp_path / "list.md").write_text(JOURNAL_DOCUMENT, encoding="utf-8")
    config_path = tmp_path / "monitor.yaml"
    config_path.write_text("venue_whitelist: [\n", encoding="utf-8")

    result = load_settings(config_path)

    assert result.draft.keyword_expression == ""
    assert result.draft.journals == valid_journals()
    assert result.draft.monitor_revision.exists
    assert any("invalid YAML" in issue.message for issue in result.issues)


def test_missing_journal_file_is_recoverable_with_empty_journals(tmp_path: Path) -> None:
    config_path = tmp_path / "monitor.yaml"
    config_path.write_text(
        "keyword_expression: causal\nvenue_whitelist: missing.md\n",
        encoding="utf-8",
    )

    result = load_settings(config_path)

    assert result.draft.journals == ()
    assert not result.draft.journal_revision.exists
    assert any(
        issue.source is SettingsIssueSource.JOURNAL_DATA
        and "missing" in issue.message
        for issue in result.issues
    )


def test_malformed_journal_file_is_recoverable_with_issue(tmp_path: Path) -> None:
    config_path, journal_path = write_valid_settings_files(
        tmp_path,
        journal_contents="# Venues\n\n## Journals\n\nnot a table\n",
    )

    result = load_settings(config_path)

    assert result.draft.journals == ()
    assert result.draft.journal_revision.digest == sha256(journal_path)
    assert any(
        issue.source is SettingsIssueSource.JOURNAL_DATA
        and "Journals table" in issue.message
        for issue in result.issues
    )


def test_load_settings_performs_no_write_or_workspace_creation(tmp_path: Path) -> None:
    config_path, journal_path = write_valid_settings_files(tmp_path)
    config_before = config_path.read_bytes()
    journal_before = journal_path.read_bytes()
    entries_before = sorted(path.name for path in tmp_path.iterdir())

    load_settings(config_path)

    assert config_path.read_bytes() == config_before
    assert journal_path.read_bytes() == journal_before
    assert sorted(path.name for path in tmp_path.iterdir()) == entries_before
    assert not (tmp_path / "workspace").exists()


def test_valid_unsaved_draft_uses_shared_runtime_validation(tmp_path: Path) -> None:
    config_path, _ = write_valid_settings_files(tmp_path)
    opened = load_settings(config_path)
    draft = replace(
        opened.draft,
        keyword_expression='"causal inference"~0',
        date_spec=DateRangeSpec(window_days=7),
        output_dir=Path("edited-workspace"),
        log_level=LogLevel.DEBUG,
    )

    result = validate_settings(config_path, draft)

    assert result.outcome is SettingsValidationOutcome.VALID
    assert result.issues == ()
    assert result.config is not None
    assert result.config.output_dir == (tmp_path / "edited-workspace").resolve()
    assert result.config.log_level is LogLevel.DEBUG
    assert result.resolved_date_range is not None
    assert (
        result.resolved_date_range.to_date
        - result.resolved_date_range.from_date
    ).days == 6


def test_runtime_and_settings_reference_the_same_shared_preflight_helpers() -> None:
    assert monitor_module.validate_runtime_keyword is config_module.validate_runtime_keyword
    assert settings_module.validate_runtime_config is config_module.validate_runtime_config


@pytest.mark.parametrize(
    ("expression", "message"),
    (
        ("alpha AND", "column"),
        ('"---"~2', "at least two lexical tokens"),
    ),
)
def test_invalid_keyword_draft_is_structured_failure(
    expression: str,
    message: str,
    tmp_path: Path,
) -> None:
    config_path, _ = write_valid_settings_files(tmp_path)
    draft = replace(load_settings(config_path).draft, keyword_expression=expression)

    result = validate_settings(config_path, draft)

    assert result.outcome is SettingsValidationOutcome.INVALID
    assert result.issues[0].field == "keyword_expression"
    assert message in result.issues[0].message


def test_fts5_unavailable_is_structured_deterministic_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, _ = write_valid_settings_files(tmp_path)
    draft = load_settings(config_path).draft

    def unavailable(expression: object) -> None:
        raise SearchBackendError("SQLite FTS5 is unavailable")

    monkeypatch.setattr(config_module, "validate_search_expression", unavailable)

    result = validate_settings(config_path, draft)

    assert result.outcome is SettingsValidationOutcome.INVALID
    assert result.issues[0].field == "keyword_expression"
    assert "FTS5 is unavailable" in result.issues[0].message


@pytest.mark.parametrize(
    "date_spec",
    (
        DateRangeSpec(from_date=date(2026, 9, 1)),
        DateRangeSpec(to_date=date(2026, 9, 21)),
        DateRangeSpec(
            from_date=date(2026, 9, 1),
            to_date=date(2026, 9, 21),
            window_days=21,
        ),
        DateRangeSpec(window_days=0),
        DateRangeSpec(
            from_date=date(2026, 9, 22),
            to_date=date(2026, 9, 21),
        ),
    ),
)
def test_invalid_date_policy_forms_fail_draft_validation(
    date_spec: DateRangeSpec,
    tmp_path: Path,
) -> None:
    config_path, _ = write_valid_settings_files(tmp_path)
    draft = replace(load_settings(config_path).draft, date_spec=date_spec)

    result = validate_settings(config_path, draft)

    assert result.outcome is SettingsValidationOutcome.INVALID
    assert result.issues


def test_runtime_date_overflow_fails_draft_validation(tmp_path: Path) -> None:
    config_path, _ = write_valid_settings_files(tmp_path)
    draft = replace(
        load_settings(config_path).draft,
        date_spec=DateRangeSpec(from_date=date.max, window_days=2),
    )

    result = validate_settings(config_path, draft)

    assert result.outcome is SettingsValidationOutcome.INVALID
    assert result.issues[0].field == "window_days"
    assert "datetime.date bounds" in result.issues[0].message


@pytest.mark.parametrize(
    ("journals", "message"),
    (
        ((JournalConfig(name="Bad", issn=("1234-5678",)),), "checksum"),
        ((JournalConfig(name="Bad", issn=("not-an-issn",)),), "invalid ISSN"),
        (
            (
                JournalConfig(name="Biometrics", issn=("0006-341X",)),
                JournalConfig(name="biometrics", issn=("1932-6157",)),
            ),
            "duplicate Journal",
        ),
        (
            (
                JournalConfig(name="One", issn=("0006-341X",)),
                JournalConfig(name="Two", issn=("0006-341X",)),
            ),
            "duplicate ISSN",
        ),
        ((), "no entries"),
    ),
)
def test_journal_domain_failures_are_shared_validation_errors(
    journals: tuple[JournalConfig, ...],
    message: str,
    tmp_path: Path,
) -> None:
    config_path, _ = write_valid_settings_files(tmp_path)
    draft = replace(load_settings(config_path).draft, journals=journals)

    result = validate_settings(config_path, draft)

    assert result.outcome is SettingsValidationOutcome.INVALID
    assert result.issues[0].field == "journals"
    assert message in result.issues[0].message


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("name", ""),
        ("keyword_expression", ""),
        ("log_level", "TRACE"),
        ("output_dir", None),
    ),
)
def test_monitor_field_failures_match_runtime_rules(
    field: str,
    value: object,
    tmp_path: Path,
) -> None:
    config_path, _ = write_valid_settings_files(tmp_path)
    draft = replace(load_settings(config_path).draft, **{field: value})

    result = validate_settings(config_path, draft)

    assert result.outcome is SettingsValidationOutcome.INVALID
    assert result.issues


def test_validation_does_not_write_files_or_create_workspace(tmp_path: Path) -> None:
    config_path, journal_path = write_valid_settings_files(tmp_path)
    opened = load_settings(config_path)
    before = (config_path.read_bytes(), journal_path.read_bytes())

    result = validate_settings(
        config_path,
        replace(opened.draft, keyword_expression="causal AND inference"),
    )

    assert result.outcome is SettingsValidationOutcome.VALID
    assert (config_path.read_bytes(), journal_path.read_bytes()) == before
    assert not (tmp_path / "workspace").exists()


def test_save_writes_journal_then_monitor_and_rereads_disk_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, journal_path = write_valid_settings_files(tmp_path)
    opened = load_settings(config_path)
    draft = replace(
        opened.draft,
        name="Edited Monitor",
        keyword_expression="statistics",
        journals=(
            JournalConfig(name="Biometrics", issn=("0006-341X",)),
            JournalConfig(name="Biostatistics", issn=("1465-4644",)),
        ),
        date_spec=DateRangeSpec(
            from_date=date(2026, 9, 1),
            to_date=date(2026, 9, 21),
        ),
        output_dir=Path("./edited-workspace"),
        log_level=LogLevel.DEBUG,
    )
    original_write = settings_module._write_snapshot_target
    order: list[Path] = []

    def record_write(
        path: Path,
        contents: str,
        snapshot: object,
    ) -> None:
        order.append(path)
        original_write(path, contents, snapshot)  # type: ignore[arg-type]

    monkeypatch.setattr(settings_module, "_write_snapshot_target", record_write)

    result = save_settings(config_path, draft)

    assert result.outcome is SettingsSaveOutcome.SAVED
    assert result.journal_written
    assert result.monitor_written
    assert order == [journal_path.resolve(), config_path.resolve()]
    assert result.state.issues == ()
    assert result.monitor_revision.digest == sha256(config_path)
    assert result.journal_revision.digest == sha256(journal_path)

    loaded = load_config(config_path)
    assert loaded.name == "Edited Monitor"
    assert loaded.keyword_expression == "statistics"
    assert loaded.output_dir == (tmp_path / "edited-workspace").resolve()
    assert loaded.log_level is LogLevel.DEBUG
    assert loaded.date_spec == DateRangeSpec(
        from_date=date(2026, 9, 1),
        to_date=date(2026, 9, 21),
    )
    assert [journal.name for journal in parse_journal_whitelist(journal_path)] == [
        "Biometrics",
        "Biostatistics",
    ]


def test_save_preserves_non_journals_content(tmp_path: Path) -> None:
    config_path, journal_path = write_valid_settings_files(tmp_path)
    opened = load_settings(config_path)
    before = journal_path.read_text(encoding="utf-8")
    prefix = before.split("## Journals", maxsplit=1)[0]
    conferences = "## Conferences" + before.split("## Conferences", maxsplit=1)[1]

    result = save_settings(
        config_path,
        replace(
            opened.draft,
            journals=(JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        ),
    )

    after = journal_path.read_text(encoding="utf-8")
    assert result.outcome is SettingsSaveOutcome.SAVED
    assert after.startswith(prefix)
    assert conferences in after
    assert "Annals of Applied Statistics" not in after


def test_open_then_save_preserves_relative_path_runtime_meaning(tmp_path: Path) -> None:
    config_dir = tmp_path / "research"
    config_dir.mkdir()
    config_path, _ = write_valid_settings_files(
        config_dir,
        output_dir="./workspace-relative",
    )
    opened = load_settings(config_path)

    result = save_settings(config_path, opened.draft)

    assert result.outcome is SettingsSaveOutcome.SAVED
    assert load_config(config_path).output_dir == (
        config_dir / "workspace-relative"
    ).resolve()


def test_monitor_revision_conflict_writes_neither_file(tmp_path: Path) -> None:
    config_path, journal_path = write_valid_settings_files(tmp_path)
    opened = load_settings(config_path)
    journal_before = journal_path.read_bytes()
    config_path.write_bytes(config_path.read_bytes() + b"# external edit\n")
    external_monitor = config_path.read_bytes()

    result = save_settings(config_path, opened.draft)

    assert result.outcome is SettingsSaveOutcome.REVISION_CONFLICT
    assert not result.journal_written
    assert not result.monitor_written
    assert journal_path.read_bytes() == journal_before
    assert config_path.read_bytes() == external_monitor


def test_journal_revision_conflict_writes_neither_file(tmp_path: Path) -> None:
    config_path, journal_path = write_valid_settings_files(tmp_path)
    opened = load_settings(config_path)
    config_before = config_path.read_bytes()
    journal_path.write_bytes(journal_path.read_bytes() + b"\n")
    external_journal = journal_path.read_bytes()

    result = save_settings(config_path, opened.draft)

    assert result.outcome is SettingsSaveOutcome.REVISION_CONFLICT
    assert not result.journal_written
    assert not result.monitor_written
    assert config_path.read_bytes() == config_before
    assert journal_path.read_bytes() == external_journal


def test_comments_only_external_monitor_edit_is_revision_conflict(tmp_path: Path) -> None:
    config_path, _ = write_valid_settings_files(tmp_path)
    opened = load_settings(config_path)
    config_path.write_bytes(config_path.read_bytes() + b"# harmless comment\n")

    result = save_settings(config_path, opened.draft)

    assert result.outcome is SettingsSaveOutcome.REVISION_CONFLICT


def test_journal_write_failure_never_writes_monitor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, journal_path = write_valid_settings_files(tmp_path)
    opened = load_settings(config_path)
    config_before = config_path.read_bytes()
    journal_before = journal_path.read_bytes()

    def fail_journal(
        path: Path,
        contents: str,
        snapshot: object,
    ) -> None:
        assert path == journal_path.resolve()
        raise OSError("journal write denied")

    monkeypatch.setattr(settings_module, "_write_snapshot_target", fail_journal)

    result = save_settings(config_path, opened.draft)

    assert result.outcome is SettingsSaveOutcome.WRITE_FAILED
    assert not result.journal_written
    assert not result.monitor_written
    assert config_path.read_bytes() == config_before
    assert journal_path.read_bytes() == journal_before


def test_monitor_write_failure_after_journal_success_is_partial_save_and_reread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, journal_path = write_valid_settings_files(tmp_path)
    opened = load_settings(config_path)
    config_before = config_path.read_bytes()
    draft = replace(
        opened.draft,
        journals=(JournalConfig(name="Biometrics", issn=("0006-341X",)),),
    )
    original_write = settings_module._write_snapshot_target

    def fail_monitor(
        path: Path,
        contents: str,
        snapshot: object,
    ) -> None:
        if path == config_path.resolve():
            raise OSError("monitor write denied")
        original_write(path, contents, snapshot)  # type: ignore[arg-type]

    monkeypatch.setattr(settings_module, "_write_snapshot_target", fail_monitor)

    result = save_settings(config_path, draft)

    assert result.outcome is SettingsSaveOutcome.PARTIAL_SAVE
    assert result.journal_written
    assert not result.monitor_written
    assert config_path.read_bytes() == config_before
    assert "Annals of Applied Statistics" not in journal_path.read_text(encoding="utf-8")
    assert result.journal_revision.digest == sha256(journal_path)
    assert result.monitor_revision.digest == sha256(config_path)
    assert [journal.name for journal in result.state.draft.journals] == ["Biometrics"]
    assert result.state.draft.name == "Statistical Monitor"


def test_missing_revision_differs_from_existing_empty_file(tmp_path: Path) -> None:
    config_path = tmp_path / "monitor.yaml"
    first = load_settings(config_path)
    assert first.draft.monitor_revision == ContentRevision(False, None)
    assert first.draft.journal_revision == ContentRevision(False, None)

    config_path.write_bytes(b"")
    (tmp_path / "list.md").write_bytes(b"")
    second = load_settings(config_path)

    assert second.draft.monitor_revision.exists
    assert second.draft.journal_revision.exists
    assert second.draft.monitor_revision != first.draft.monitor_revision
    assert second.draft.journal_revision != first.draft.journal_revision


@pytest.mark.parametrize("created_name", ("monitor.yaml", "list.md"))
def test_externally_created_previously_missing_file_conflicts_before_save(
    created_name: str,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "monitor.yaml"
    opened = load_settings(config_path)
    draft = replace(
        opened.draft,
        keyword_expression="causal",
        journals=(JournalConfig(name="Biometrics", issn=("0006-341X",)),),
    )
    created = tmp_path / created_name
    created.write_text("", encoding="utf-8")
    other = tmp_path / ("list.md" if created_name == "monitor.yaml" else "monitor.yaml")
    other_before = other.read_bytes() if other.exists() else None

    result = save_settings(config_path, draft)

    assert result.outcome is SettingsSaveOutcome.REVISION_CONFLICT
    assert not result.journal_written
    assert not result.monitor_written
    assert created.read_text(encoding="utf-8") == ""
    if other_before is None:
        assert not other.exists()
    else:
        assert other.read_bytes() == other_before


def test_missing_monitor_and_journal_can_be_created_after_valid_edit(tmp_path: Path) -> None:
    config_path = tmp_path / "monitor.yaml"
    opened = load_settings(config_path)
    draft = replace(
        opened.draft,
        name="Recovered",
        keyword_expression="causal",
        journals=(JournalConfig(name="Biometrics", issn=("0006-341X",)),),
    )

    result = save_settings(config_path, draft)

    assert result.outcome is SettingsSaveOutcome.SAVED
    assert result.journal_written
    assert result.monitor_written
    assert load_config(config_path).name == "Recovered"
    assert parse_journal_whitelist(tmp_path / "list.md") == draft.journals
