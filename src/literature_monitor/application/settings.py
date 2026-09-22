"""Settings application boundary for monitor and journal configuration."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from literature_monitor.config import (
    ConfigurationError,
    JournalConfig,
    LoadedConfig,
    LogLevel,
    build_loaded_config,
    parse_journal_whitelist_text,
    parse_monitor_definition,
    render_journal_whitelist_text,
    resolve_config_path,
    validate_journal_storage,
    validate_runtime_config,
)
from literature_monitor.date_range import (
    DEFAULT_WINDOW_DAYS,
    DateRangeError,
    DateRangeSpec,
    ResolvedDateRange,
)
from literature_monitor.safe_write import (
    CompareReadError,
    ContentChangedError,
    create_text_exclusive,
    read_text_exact,
    replace_text_if_unchanged,
)
from literature_monitor.search import SearchBackendError, SearchExpressionError

__all__ = [
    "ContentRevision",
    "MonitorDraft",
    "SettingsIssueSource",
    "SettingsIssue",
    "SettingsLoadResult",
    "SettingsValidationOutcome",
    "SettingsValidationResult",
    "SettingsSaveOutcome",
    "SettingsSaveResult",
    "load_settings",
    "validate_settings",
    "save_settings",
]


@dataclass(frozen=True)
class ContentRevision:
    exists: bool
    digest: str | None


@dataclass(frozen=True)
class MonitorDraft:
    name: str
    keyword_expression: str
    journals: tuple[JournalConfig, ...]
    date_spec: DateRangeSpec
    output_dir: Path
    log_level: LogLevel | str
    monitor_revision: ContentRevision
    journal_revision: ContentRevision


class SettingsIssueSource(str, Enum):
    MONITOR_CONFIG = "monitor_config"
    JOURNAL_DATA = "journal_data"
    VALIDATION = "validation"
    STORAGE = "storage"


@dataclass(frozen=True)
class SettingsIssue:
    source: SettingsIssueSource
    message: str
    field: str | None = None
    path: Path | None = None


@dataclass(frozen=True)
class SettingsLoadResult:
    config_path: Path
    journal_path: Path
    venue_whitelist: Path
    draft: MonitorDraft
    issues: tuple[SettingsIssue, ...]


class SettingsValidationOutcome(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"


@dataclass(frozen=True)
class SettingsValidationResult:
    outcome: SettingsValidationOutcome
    issues: tuple[SettingsIssue, ...]
    config: LoadedConfig | None
    resolved_date_range: ResolvedDateRange | None


class SettingsSaveOutcome(str, Enum):
    SAVED = "SAVED"
    INVALID_DRAFT = "INVALID_DRAFT"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    WRITE_FAILED = "WRITE_FAILED"
    PARTIAL_SAVE = "PARTIAL_SAVE"


@dataclass(frozen=True)
class SettingsSaveResult:
    outcome: SettingsSaveOutcome
    validation: SettingsValidationResult
    state: SettingsLoadResult
    issues: tuple[SettingsIssue, ...]
    journal_written: bool
    monitor_written: bool
    monitor_revision: ContentRevision
    journal_revision: ContentRevision


@dataclass(frozen=True)
class _FileSnapshot:
    revision: ContentRevision
    contents: str | None
    issue: SettingsIssue | None


@dataclass(frozen=True)
class _DraftFields:
    name: str
    venue_whitelist: Path
    keyword_expression: str
    output_dir: Path
    date_spec: DateRangeSpec
    log_level: LogLevel | str


def _digest(contents: str) -> str:
    return hashlib.sha256(contents.encode("utf-8")).hexdigest()


def _read_snapshot(
    path: Path,
    *,
    source: SettingsIssueSource,
) -> _FileSnapshot:
    try:
        contents = read_text_exact(path)
    except FileNotFoundError:
        return _FileSnapshot(
            revision=ContentRevision(exists=False, digest=None),
            contents=None,
            issue=None,
        )
    except UnicodeError as error:
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            digest = None
        return _FileSnapshot(
            revision=ContentRevision(exists=True, digest=digest),
            contents=None,
            issue=SettingsIssue(
                source=source,
                message=f"file is not valid UTF-8: {error}",
                path=path,
            ),
        )
    except OSError as error:
        return _FileSnapshot(
            revision=ContentRevision(exists=path.exists(), digest=None),
            contents=None,
            issue=SettingsIssue(
                source=source,
                message=f"unable to read file: {error}",
                path=path,
            ),
        )
    return _FileSnapshot(
        revision=ContentRevision(exists=True, digest=_digest(contents)),
        contents=contents,
        issue=None,
    )


def _default_fields(config_path: Path) -> _DraftFields:
    return _DraftFields(
        name=config_path.stem,
        venue_whitelist=Path("list.md"),
        keyword_expression="",
        output_dir=Path("workspace"),
        date_spec=DateRangeSpec(window_days=DEFAULT_WINDOW_DAYS),
        log_level=LogLevel.INFO,
    )


def _recovery_fields(config_path: Path, raw: Any) -> _DraftFields:
    defaults = _default_fields(config_path)
    if not isinstance(raw, dict):
        return defaults

    name = raw.get("name")
    keyword = raw.get("keyword_expression")
    venue = raw.get("venue_whitelist")
    output = raw.get("output_dir")
    log_level = raw.get("log_level")

    recovered_name = name.strip() if isinstance(name, str) else defaults.name
    recovered_keyword = (
        keyword.strip() if isinstance(keyword, str) else defaults.keyword_expression
    )
    recovered_venue = (
        Path(venue) if isinstance(venue, str) and venue.strip() else defaults.venue_whitelist
    )
    recovered_output = (
        Path(output) if isinstance(output, str) and output.strip() else defaults.output_dir
    )
    if isinstance(log_level, str):
        try:
            recovered_log: LogLevel | str = LogLevel(log_level.upper())
        except ValueError:
            recovered_log = log_level
    else:
        recovered_log = defaults.log_level

    raw_from = raw.get("from_date")
    raw_to = raw.get("to_date")
    raw_window = raw.get("window_days")
    if raw_from is None and raw_to is None and raw_window is None:
        recovered_date = defaults.date_spec
    elif (
        (raw_from is None or isinstance(raw_from, date))
        and (raw_to is None or isinstance(raw_to, date))
        and (raw_window is None or isinstance(raw_window, int))
    ):
        recovered_date = DateRangeSpec(
            from_date=raw_from,
            to_date=raw_to,
            window_days=raw_window,
        )
    else:
        recovered_date = defaults.date_spec

    return _DraftFields(
        name=recovered_name,
        venue_whitelist=recovered_venue,
        keyword_expression=recovered_keyword,
        output_dir=recovered_output,
        date_spec=recovered_date,
        log_level=recovered_log,
    )


def _monitor_fields(
    config_path: Path,
    snapshot: _FileSnapshot,
) -> tuple[_DraftFields, tuple[SettingsIssue, ...]]:
    if not snapshot.revision.exists:
        return _default_fields(config_path), (
            SettingsIssue(
                source=SettingsIssueSource.MONITOR_CONFIG,
                message="monitor config file is missing",
                path=config_path,
            ),
        )
    if snapshot.contents is None:
        assert snapshot.issue is not None
        return _default_fields(config_path), (snapshot.issue,)

    try:
        raw = yaml.safe_load(snapshot.contents)
    except yaml.YAMLError as error:
        return _default_fields(config_path), (
            SettingsIssue(
                source=SettingsIssueSource.MONITOR_CONFIG,
                message=f"invalid YAML: {error}",
                path=config_path,
            ),
        )

    try:
        definition = parse_monitor_definition(config_path, raw)
    except ConfigurationError as error:
        return _recovery_fields(config_path, raw), (
            SettingsIssue(
                source=SettingsIssueSource.MONITOR_CONFIG,
                message=str(error),
                field=error.field,
                path=config_path,
            ),
        )

    return (
        _DraftFields(
            name=definition.name,
            venue_whitelist=definition.venue_whitelist,
            keyword_expression=definition.keyword_expression,
            output_dir=definition.output_dir,
            date_spec=definition.date_spec,
            log_level=definition.log_level,
        ),
        (),
    )


def load_settings(config_path: Path) -> SettingsLoadResult:
    """Load an editable Settings state without requiring valid runtime config."""

    config_path = config_path.resolve()
    monitor_snapshot = _read_snapshot(
        config_path,
        source=SettingsIssueSource.MONITOR_CONFIG,
    )
    fields, monitor_issues = _monitor_fields(config_path, monitor_snapshot)
    journal_path = resolve_config_path(
        config_path,
        fields.venue_whitelist,
        "list.md",
    )
    journal_snapshot = _read_snapshot(
        journal_path,
        source=SettingsIssueSource.JOURNAL_DATA,
    )

    journal_issues: list[SettingsIssue] = []
    journals: tuple[JournalConfig, ...] = ()
    if not journal_snapshot.revision.exists:
        journal_issues.append(
            SettingsIssue(
                source=SettingsIssueSource.JOURNAL_DATA,
                message="journal data file is missing",
                path=journal_path,
            )
        )
    elif journal_snapshot.contents is None:
        assert journal_snapshot.issue is not None
        journal_issues.append(journal_snapshot.issue)
    else:
        try:
            journals = parse_journal_whitelist_text(
                journal_snapshot.contents,
                path=journal_path,
            )
        except ConfigurationError as error:
            journal_issues.append(
                SettingsIssue(
                    source=SettingsIssueSource.JOURNAL_DATA,
                    message=str(error),
                    field="journals",
                    path=journal_path,
                )
            )

    draft = MonitorDraft(
        name=fields.name,
        keyword_expression=fields.keyword_expression,
        journals=journals,
        date_spec=fields.date_spec,
        output_dir=fields.output_dir,
        log_level=fields.log_level,
        monitor_revision=monitor_snapshot.revision,
        journal_revision=journal_snapshot.revision,
    )
    return SettingsLoadResult(
        config_path=config_path,
        journal_path=journal_path,
        venue_whitelist=fields.venue_whitelist,
        draft=draft,
        issues=tuple((*monitor_issues, *journal_issues)),
    )


def _draft_monitor_mapping(
    draft: MonitorDraft,
    *,
    venue_whitelist: Path,
) -> dict[str, object]:
    raw: dict[str, object] = {
        "name": draft.name,
        "venue_whitelist": venue_whitelist,
        "keyword_expression": draft.keyword_expression,
        "output_dir": draft.output_dir,
        "log_level": draft.log_level,
    }
    if draft.date_spec.from_date is not None:
        raw["from_date"] = draft.date_spec.from_date
    if draft.date_spec.to_date is not None:
        raw["to_date"] = draft.date_spec.to_date
    if draft.date_spec.window_days is not None:
        raw["window_days"] = draft.date_spec.window_days
    return raw


def validate_settings(
    config_path: Path,
    draft: MonitorDraft,
) -> SettingsValidationResult:
    """Validate an unsaved draft using the runtime deterministic rule boundary."""

    config_path = config_path.resolve()
    monitor_snapshot = _read_snapshot(
        config_path,
        source=SettingsIssueSource.MONITOR_CONFIG,
    )
    storage_fields, _ = _monitor_fields(config_path, monitor_snapshot)
    try:
        definition = parse_monitor_definition(
            config_path,
            _draft_monitor_mapping(
                draft,
                venue_whitelist=storage_fields.venue_whitelist,
            ),
        )
        config = build_loaded_config(config_path, definition, draft.journals)
        validate_journal_storage(config.journals)
        resolved = validate_runtime_config(config, today=date.today())
    except ConfigurationError as error:
        return SettingsValidationResult(
            outcome=SettingsValidationOutcome.INVALID,
            issues=(
                SettingsIssue(
                    source=SettingsIssueSource.VALIDATION,
                    message=str(error),
                    field=error.field,
                    path=error.path or config_path,
                ),
            ),
            config=None,
            resolved_date_range=None,
        )
    except SearchExpressionError as error:
        return SettingsValidationResult(
            outcome=SettingsValidationOutcome.INVALID,
            issues=(
                SettingsIssue(
                    source=SettingsIssueSource.VALIDATION,
                    message=str(error),
                    field="keyword_expression",
                    path=config_path,
                ),
            ),
            config=None,
            resolved_date_range=None,
        )
    except SearchBackendError as error:
        return SettingsValidationResult(
            outcome=SettingsValidationOutcome.INVALID,
            issues=(
                SettingsIssue(
                    source=SettingsIssueSource.VALIDATION,
                    message=str(error),
                    field="keyword_expression",
                    path=config_path,
                ),
            ),
            config=None,
            resolved_date_range=None,
        )
    except DateRangeError as error:
        return SettingsValidationResult(
            outcome=SettingsValidationOutcome.INVALID,
            issues=(
                SettingsIssue(
                    source=SettingsIssueSource.VALIDATION,
                    message=str(error),
                    field=error.field,
                    path=config_path,
                ),
            ),
            config=None,
            resolved_date_range=None,
        )

    return SettingsValidationResult(
        outcome=SettingsValidationOutcome.VALID,
        issues=(),
        config=config,
        resolved_date_range=resolved,
    )


def _render_monitor_yaml(
    draft: MonitorDraft,
    *,
    venue_whitelist: Path,
) -> str:
    values: dict[str, object] = {
        "name": draft.name,
        "venue_whitelist": str(venue_whitelist),
        "keyword_expression": draft.keyword_expression,
        "output_dir": str(draft.output_dir),
    }
    if draft.date_spec.from_date is not None:
        values["from_date"] = draft.date_spec.from_date.isoformat()
    if draft.date_spec.to_date is not None:
        values["to_date"] = draft.date_spec.to_date.isoformat()
    if draft.date_spec.window_days is not None:
        values["window_days"] = draft.date_spec.window_days
    values["log_level"] = (
        draft.log_level.value
        if isinstance(draft.log_level, LogLevel)
        else str(draft.log_level)
    )
    return yaml.safe_dump(values, sort_keys=False, allow_unicode=True)


def _write_snapshot_target(
    path: Path,
    contents: str,
    snapshot: _FileSnapshot,
) -> None:
    if snapshot.revision.exists:
        if snapshot.contents is None:
            raise CompareReadError("existing file could not be read exactly")
        replace_text_if_unchanged(
            path,
            contents,
            expected_contents=snapshot.contents,
        )
    else:
        create_text_exclusive(path, contents)


def _result_from_state(
    *,
    outcome: SettingsSaveOutcome,
    validation: SettingsValidationResult,
    state: SettingsLoadResult,
    issues: tuple[SettingsIssue, ...],
    journal_written: bool,
    monitor_written: bool,
) -> SettingsSaveResult:
    return SettingsSaveResult(
        outcome=outcome,
        validation=validation,
        state=state,
        issues=issues,
        journal_written=journal_written,
        monitor_written=monitor_written,
        monitor_revision=state.draft.monitor_revision,
        journal_revision=state.draft.journal_revision,
    )


def save_settings(
    config_path: Path,
    draft: MonitorDraft,
) -> SettingsSaveResult:
    """Validate and persist Settings with explicit two-file failure semantics."""

    config_path = config_path.resolve()
    validation = validate_settings(config_path, draft)
    if validation.outcome is SettingsValidationOutcome.INVALID:
        state = load_settings(config_path)
        return _result_from_state(
            outcome=SettingsSaveOutcome.INVALID_DRAFT,
            validation=validation,
            state=state,
            issues=validation.issues,
            journal_written=False,
            monitor_written=False,
        )

    assert validation.config is not None
    journal_path = validation.config.venue_whitelist

    monitor_snapshot = _read_snapshot(
        config_path,
        source=SettingsIssueSource.MONITOR_CONFIG,
    )
    journal_snapshot = _read_snapshot(
        journal_path,
        source=SettingsIssueSource.JOURNAL_DATA,
    )
    read_issues = tuple(
        issue
        for issue in (monitor_snapshot.issue, journal_snapshot.issue)
        if issue is not None
    )
    if read_issues:
        state = load_settings(config_path)
        return _result_from_state(
            outcome=SettingsSaveOutcome.WRITE_FAILED,
            validation=validation,
            state=state,
            issues=read_issues,
            journal_written=False,
            monitor_written=False,
        )

    if (
        monitor_snapshot.revision != draft.monitor_revision
        or journal_snapshot.revision != draft.journal_revision
    ):
        state = load_settings(config_path)
        return _result_from_state(
            outcome=SettingsSaveOutcome.REVISION_CONFLICT,
            validation=validation,
            state=state,
            issues=(
                SettingsIssue(
                    source=SettingsIssueSource.STORAGE,
                    message="Settings files changed after they were opened",
                ),
            ),
            journal_written=False,
            monitor_written=False,
        )

    storage_fields, _ = _monitor_fields(config_path, monitor_snapshot)

    # Both complete target documents are prepared before the first write starts.
    try:
        journal_target = render_journal_whitelist_text(
            journal_snapshot.contents,
            validation.config.journals,
            path=journal_path,
        )
        monitor_target = _render_monitor_yaml(
            draft,
            venue_whitelist=storage_fields.venue_whitelist,
        )
    except ConfigurationError as error:
        state = load_settings(config_path)
        issue = SettingsIssue(
            source=SettingsIssueSource.JOURNAL_DATA,
            message=str(error),
            field=error.field,
            path=error.path,
        )
        return _result_from_state(
            outcome=SettingsSaveOutcome.INVALID_DRAFT,
            validation=validation,
            state=state,
            issues=(issue,),
            journal_written=False,
            monitor_written=False,
        )

    try:
        _write_snapshot_target(journal_path, journal_target, journal_snapshot)
    except (ContentChangedError, FileExistsError):
        state = load_settings(config_path)
        return _result_from_state(
            outcome=SettingsSaveOutcome.REVISION_CONFLICT,
            validation=validation,
            state=state,
            issues=(
                SettingsIssue(
                    source=SettingsIssueSource.JOURNAL_DATA,
                    message="journal data changed before it could be written",
                    path=journal_path,
                ),
            ),
            journal_written=False,
            monitor_written=False,
        )
    except (CompareReadError, OSError) as error:
        state = load_settings(config_path)
        return _result_from_state(
            outcome=SettingsSaveOutcome.WRITE_FAILED,
            validation=validation,
            state=state,
            issues=(
                SettingsIssue(
                    source=SettingsIssueSource.JOURNAL_DATA,
                    message=f"unable to write journal data: {error}",
                    path=journal_path,
                ),
            ),
            journal_written=False,
            monitor_written=False,
        )

    try:
        _write_snapshot_target(config_path, monitor_target, monitor_snapshot)
    except (ContentChangedError, FileExistsError) as error:
        state = load_settings(config_path)
        return _result_from_state(
            outcome=SettingsSaveOutcome.PARTIAL_SAVE,
            validation=validation,
            state=state,
            issues=(
                SettingsIssue(
                    source=SettingsIssueSource.MONITOR_CONFIG,
                    message=f"monitor config changed before it could be written: {error}",
                    path=config_path,
                ),
            ),
            journal_written=True,
            monitor_written=False,
        )
    except (CompareReadError, OSError) as error:
        state = load_settings(config_path)
        return _result_from_state(
            outcome=SettingsSaveOutcome.PARTIAL_SAVE,
            validation=validation,
            state=state,
            issues=(
                SettingsIssue(
                    source=SettingsIssueSource.MONITOR_CONFIG,
                    message=f"unable to write monitor config: {error}",
                    path=config_path,
                ),
            ),
            journal_written=True,
            monitor_written=False,
        )

    state = load_settings(config_path)
    return _result_from_state(
        outcome=SettingsSaveOutcome.SAVED,
        validation=validation,
        state=state,
        issues=state.issues,
        journal_written=True,
        monitor_written=True,
    )
