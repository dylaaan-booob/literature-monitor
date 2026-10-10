"""Settings application boundary for monitor and journal configuration."""

from __future__ import annotations

import errno
import hashlib
import os
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass, replace
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from literature_monitor.config import (
    AccessService,
    detect_list_schema,
    parse_access_services_text,
    validate_access_mapping,
    ConfigurationError,
    InstitutionConfig,
    JournalConfig,
    PublisherConfig,
    LegacyJournal,
    parse_legacy_journal_whitelist_text,
    parse_publisher_whitelist_text,
    validate_publisher_configs,
    validate_publisher_membership,
    render_settings_list_text,
    LoadedConfig,
    LogLevel,
    build_loaded_config,
    _journal_table_is_legacy,
    journal_whitelist_table,
    parse_journal_whitelist_text,
    parse_monitor_definition,
    validate_journal_storage,
    validate_runtime_config,
)
from literature_monitor.application.journal_migration import MigrationConfirmation, resolve_legacy_journals
from literature_monitor.openalex import OpenAlexClient, OpenAlexError, resolve_source_identities, resolve_publisher_metadata
from literature_monitor.date_range import (
    DEFAULT_WINDOW_DAYS,
    DateRangeError,
    DateRangeSpec,
    ResolvedDateRange,
    resolve_date_range,
)
from literature_monitor.safe_write import (
    CompareReadError,
    ContentChangedError,
    create_text_exclusive,
    read_text_exact,
    workspace_operation_lock,
    workspace_path_lock,
    replace_regular_text_if_unchanged as replace_text_if_unchanged,
)
from literature_monitor.search import SearchBackendError, SearchExpressionError, validate_search_expression

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
    "SettingsDeletionApproval",
    "SettingsDeletionPlan",
    "plan_settings_deletions",
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
    publishers: tuple[PublisherConfig, ...] = ()
    access_services: tuple[AccessService, ...] = ()
    legacy_journals: tuple[LegacyJournal, ...] = ()
    migration_confirmations: tuple[MigrationConfirmation, ...] = ()
    pending_journal_ids: tuple[str, ...] = ()  # Transient Add/Import placeholders.
    institution: InstitutionConfig | None = None


@dataclass(frozen=True)
class SettingsDeletionPlan:
    services: tuple[AccessService, ...]
    service_members: tuple[tuple[str, tuple[PublisherConfig, ...]], ...]
    publishers: tuple[PublisherConfig, ...]

    @property
    def required(self) -> bool:
        return bool(self.services or self.publishers)


@dataclass(frozen=True)
class SettingsDeletionApproval:
    monitor_revision: ContentRevision
    journal_revision: ContentRevision
    service_ids: tuple[str, ...]
    publisher_ids: tuple[str, ...]


def plan_settings_deletions(saved: MonitorDraft, proposed: MonitorDraft) -> SettingsDeletionPlan:
    """Derive destructive effects from stored identities, not browser-supplied hints."""
    remaining_services = {service.id for service in proposed.access_services}
    removed_services = tuple(service for service in saved.access_services if service.id not in remaining_services)
    active_publishers = {journal.publisher_id for journal in proposed.journals if journal.publisher_id}
    saved_by_issn = {journal.issn_l: journal for journal in saved.journals}
    for journal in proposed.journals:
        # 旧 ISSN-L 重新加入时可能仍是无 Publisher ID 的待解析行，
        # 解析阶段将恢复磁盘中已验证的 Publisher 关系，不应误判为级联删除。
        if journal.issn_l in proposed.pending_journal_ids and journal.publisher_id is None:
            previous = saved_by_issn.get(journal.issn_l)
            if previous and previous.publisher_id:
                active_publishers.add(previous.publisher_id)
    assigned_now = {publisher.publisher_id: publisher.access_service_id for publisher in proposed.publishers}
    # 只有实际继续保留、且解除外键的 Publisher 才会因 Service 删除变为 Unassigned。
    # 已重新指向其他 Service 的对象以及随 Journal 级联删除的对象不列作解绑。
    members = tuple((service.id, tuple(
        publisher for publisher in saved.publishers
        if publisher.access_service_id == service.id
        and publisher.publisher_id in active_publishers
        and publisher.publisher_id in assigned_now
        and assigned_now[publisher.publisher_id] is None
    )) for service in removed_services)
    cascades = tuple(publisher for publisher in saved.publishers
                     if publisher.publisher_id not in active_publishers
                     and (publisher.publisher_url or publisher.access_service_id))
    return SettingsDeletionPlan(removed_services, members, cascades)


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
    institution: InstitutionConfig | None = None


def _digest(contents: str) -> str:
    return hashlib.sha256(contents.encode("utf-8")).hexdigest()


def _read_snapshot(
    path: Path,
    *,
    source: SettingsIssueSource,
) -> _FileSnapshot:
    try:
        if any(candidate.is_symlink() for candidate in (path, *path.parents)):
            raise OSError("Settings storage path must not contain a symlink")
        if path.exists() and not path.is_file():
            raise OSError("Settings storage must be a regular file")
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
    try:
        institution = InstitutionConfig.model_validate(raw["institution"]) if raw.get("institution") is not None else None
    except ValueError:
        institution = None  # Never project invalid or sensitive institution data.

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
        institution=institution,
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
            institution=definition.institution,
        ),
        (),
    )


def _journal_storage(contents: str, path: Path) -> tuple[tuple[JournalConfig, ...], tuple[LegacyJournal, ...], ConfigurationError | None]:
    """Distinguish valid storage from repairable content; retain trustworthy rows.

    Duplicate sections have no unambiguous replacement boundary. Damaged rows
    cannot confer canonical metadata authority and require normal resolution.
    """
    try:
        if _journal_table_is_legacy(contents):
            return (), parse_legacy_journal_whitelist_text(contents, path=path), None
        return parse_journal_whitelist_text(contents, path=path), (), None
    except ConfigurationError as error:
        if sum(line.strip() == "## Journals" for line in contents.splitlines()) > 1:
            raise
        recovered = []
        try:
            columns, rows = journal_whitelist_table(contents, path=path)
        except ConfigurationError:
            rows, columns = (), 0
        if columns == 4 and not _journal_table_is_legacy(contents):
            header = "## Journals\n\n| Journal | ISSN-L | Publisher ID | Group |\n| --- | --- | --- | --- |\n"
            for _, row in rows:
                try:
                    recovered.extend(parse_journal_whitelist_text(header + row + "\n", path=path))
                except ConfigurationError:
                    continue
        # A duplicate target is ambiguous, even when either individual row parses.
        unique = tuple(j for j in recovered if sum(r.issn_l == j.issn_l for r in recovered) == 1)
        return unique, (), error


def _journal_path(config_path: Path, fields: _DraftFields) -> Path:
    # Retain the lexical path so snapshot checks can reject symlink substitution.
    value = fields.venue_whitelist
    return value.absolute() if value.is_absolute() else (config_path.parent / value).absolute()


def load_settings(config_path: Path) -> SettingsLoadResult:
    """Load an editable Settings state without requiring valid runtime config."""

    config_path = config_path.resolve()
    monitor_snapshot = _read_snapshot(
        config_path,
        source=SettingsIssueSource.MONITOR_CONFIG,
    )
    fields, monitor_issues = _monitor_fields(config_path, monitor_snapshot)
    journal_path = _journal_path(config_path, fields)
    journal_snapshot = _read_snapshot(
        journal_path,
        source=SettingsIssueSource.JOURNAL_DATA,
    )

    journal_issues: list[SettingsIssue] = []
    journals: tuple[JournalConfig, ...] = ()
    publishers: tuple[PublisherConfig, ...] = ()
    access_services: tuple[AccessService, ...] = ()
    legacy_journals: tuple[LegacyJournal, ...] = ()
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
        contents = journal_snapshot.contents
        # 单个受管理区段损坏时，保留其他区段以及 Journals 中仍可信的行；
        # 联合 Schema 校验仅决定是否允许保存，不应清空可展示的数据。
        try:
            journals, legacy_journals, damage = _journal_storage(contents, journal_path)
            if damage is not None:
                journal_issues.append(SettingsIssue(SettingsIssueSource.JOURNAL_DATA, str(damage), field="journals", path=journal_path))
        except ConfigurationError as error:
            journal_issues.append(
                SettingsIssue(
                    source=SettingsIssueSource.JOURNAL_DATA,
                    message=str(error),
                    field="journals",
                    path=journal_path,
                )
            )
        try:
            publishers = parse_publisher_whitelist_text(contents, path=journal_path)
        except ConfigurationError as error:
            journal_issues.append(SettingsIssue(SettingsIssueSource.JOURNAL_DATA, str(error), field="publishers", path=journal_path))
        headings = {line.strip() for line in contents.splitlines()}
        if "## Access Services" in headings:
            try:
                access_services = parse_access_services_text(contents, path=journal_path)
            except ConfigurationError as error:
                journal_issues.append(SettingsIssue(SettingsIssueSource.JOURNAL_DATA, str(error), field="access_services", path=journal_path))
        if not journal_issues and not legacy_journals and (
            "## Publishers" in headings or "## Access Services" in headings
        ):
            try:
                detect_list_schema(contents, path=journal_path)
            except ConfigurationError as error:
                field = error.field or (
                    "access_services" if "## Access Services" not in headings else "publishers"
                )
                journal_issues.append(SettingsIssue(SettingsIssueSource.JOURNAL_DATA, str(error), field=field, path=journal_path))

    draft = MonitorDraft(
        name=fields.name,
        keyword_expression=fields.keyword_expression,
        journals=journals,
        publishers=publishers,
        access_services=access_services,
        legacy_journals=legacy_journals,
        date_spec=fields.date_spec,
        output_dir=fields.output_dir,
        log_level=fields.log_level,
        monitor_revision=monitor_snapshot.revision,
        journal_revision=journal_snapshot.revision,
        institution=fields.institution,
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
    raw["institution"] = draft.institution
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
        validate_publisher_configs(draft.publishers)
        validate_access_mapping(draft.publishers, draft.access_services)
        if draft.legacy_journals and not draft.journals:
            validate_search_expression(definition.keyword_ast)
            resolved = resolve_date_range(definition.date_spec, today=date.today())
            config = None
        else:
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
    if draft.institution is not None and (draft.institution.name or draft.institution.idp_entity_id):
        values["institution"] = draft.institution.model_dump(exclude_none=True)
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
    config_path: Path, draft: MonitorDraft, *, client: OpenAlexClient | None = None,
    deletion_approval: SettingsDeletionApproval | None = None,
) -> SettingsSaveResult:
    """Workspace-path changes share exclusion with batch/Run/decisions (§42.4)."""
    state = load_settings(config_path)
    def workspace_path(value):
        return Path(os.path.abspath(value if value.is_absolute() else config_path.absolute().parent / value))
    old, new = workspace_path(state.draft.output_dir), workspace_path(draft.output_dir)
    try:
        with ExitStack() as stack:
            locks = []
            for path in sorted({old, new}):
                if path.exists() or path.is_symlink():
                    locks.append(stack.enter_context(workspace_operation_lock(path)))
                else:
                    # Reset owns absent targets too; existence cannot bypass exclusion.
                    stack.enter_context(workspace_path_lock(path))
            return _save_settings(config_path, draft, client=client, operation_locks=tuple(locks),
                                  deletion_approval=deletion_approval)
    except (CompareReadError, ContentChangedError, OSError) as error:
        invalid_path = isinstance(error, OSError) and error.errno in (errno.ELOOP, errno.ENOTDIR)
        return _result_from_state(
            outcome=SettingsSaveOutcome.INVALID_DRAFT if invalid_path else SettingsSaveOutcome.WRITE_FAILED,
            validation=validate_settings(config_path, draft),
            state=load_settings(config_path),
            issues=(SettingsIssue(SettingsIssueSource.STORAGE, f"Workspace path change refused: {error}"),),
            journal_written=False, monitor_written=False,
        )


def _save_settings(
    config_path: Path,
    draft: MonitorDraft,
    *, client: OpenAlexClient | None = None, operation_locks: tuple = (),
    deletion_approval: SettingsDeletionApproval | None = None,
) -> SettingsSaveResult:
    """Validate and persist Settings with explicit two-file failure semantics."""

    config_path = config_path.absolute()
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

    stored_state = load_settings(config_path)
    journal_path = stored_state.journal_path

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
    list_unchanged = (
        (draft.journals, draft.publishers, draft.access_services)
        == (stored_state.draft.journals, stored_state.draft.publishers, stored_state.draft.access_services)
    )
    # 缺失的 Monitor 可以由用户明确提交的有效 Draft 首次创建；
    # 已存在但损坏的 Monitor，以及任何 Journal/Mapping 问题都不能借此覆盖。
    blocking_issues = tuple(
        issue for issue in stored_state.issues
        if not (
            not monitor_snapshot.revision.exists
            and issue.source is SettingsIssueSource.MONITOR_CONFIG
            and issue.message == "monitor config file is missing"
        )
    )
    # 仅 Preview/Confirm 可以升级 §41。可信行恢复仅用于展示，损坏磁盘不能经普通 Save 修复。
    try:
        if blocking_issues:
            raise ConfigurationError("Settings storage has unresolved issues; reload or repair explicitly")
        if journal_snapshot.contents is None:
            raise ConfigurationError("journal configuration is missing")
        headings = {line.strip() for line in journal_snapshot.contents.splitlines()}
        if _journal_table_is_legacy(journal_snapshot.contents):
            raise ConfigurationError("legacy ISSN/EISSN storage must be migrated explicitly")
        validate_publisher_membership(stored_state.draft.journals, stored_state.draft.publishers)
        if "## Publishers" in headings or "## Access Services" in headings or not list_unchanged:
            if detect_list_schema(journal_snapshot.contents, path=journal_path) != "current":
                raise ConfigurationError("explicit Access schema upgrade Preview/Confirm required")
        # 只更新 Monitor/Institution 时允许历史上没有 Publishers 区段的只读 Journal 表。
        # 该路径从不生成或写入任何 list.md。
        deletion_plan = plan_settings_deletions(stored_state.draft, draft)
        if deletion_plan.required:
            expected = SettingsDeletionApproval(
                draft.monitor_revision, draft.journal_revision,
                tuple(s.id for s in deletion_plan.services),
                tuple(p.publisher_id for p in deletion_plan.publishers),
            )
            if deletion_approval != expected:
                raise ConfigurationError("Deletion requires current Settings Preview/Confirm",
                                         field="access_services" if deletion_plan.services else "publishers")
    except ConfigurationError as error:
        return _result_from_state(
            outcome=SettingsSaveOutcome.INVALID_DRAFT, validation=validation,
            state=stored_state,
            issues=(SettingsIssue(SettingsIssueSource.JOURNAL_DATA, str(error),
                                  field=error.field, path=journal_path),),
            journal_written=False, monitor_written=False,
        )

    try:
        if not list_unchanged:
            draft = _resolve_settings_targets(config_path, draft, journal_snapshot, storage_fields, client,
                                             deletion_approval=deletion_approval)
        validation = validate_settings(config_path, draft)
        if validation.outcome is SettingsValidationOutcome.INVALID:
            raise ConfigurationError("; ".join(i.message for i in validation.issues))
        if validation.config is not None:
            draft = replace(draft, institution=validation.config.institution)
    except (ConfigurationError, OpenAlexError, ValueError) as error:
        return _result_from_state(
            outcome=SettingsSaveOutcome.INVALID_DRAFT, validation=validation,
            state=load_settings(config_path),
            issues=(SettingsIssue(SettingsIssueSource.VALIDATION, str(error)),),
            journal_written=False, monitor_written=False,
        )

    # Provider work is transient. Recheck BOTH original revisions before any write.
    monitor_snapshot = _read_snapshot(config_path, source=SettingsIssueSource.MONITOR_CONFIG)
    journal_snapshot = _read_snapshot(journal_path, source=SettingsIssueSource.JOURNAL_DATA)
    if monitor_snapshot.revision != draft.monitor_revision or journal_snapshot.revision != draft.journal_revision:
        return _result_from_state(
            outcome=SettingsSaveOutcome.REVISION_CONFLICT, validation=validation,
            state=load_settings(config_path),
            issues=(SettingsIssue(SettingsIssueSource.STORAGE, "Settings files changed during metadata resolution"),),
            journal_written=False, monitor_written=False,
        )
    try:
        # 数据归属决定写入目标。即使原 YAML 排序或排版不同，纯 Markdown 编辑也不触及它。
        journal_changed = (
            (draft.journals, draft.publishers, draft.access_services)
            != (stored_state.draft.journals, stored_state.draft.publishers, stored_state.draft.access_services)
        )
        monitor_changed = (
            not monitor_snapshot.revision.exists
            or _draft_monitor_mapping(draft, venue_whitelist=storage_fields.venue_whitelist)
            != _draft_monitor_mapping(stored_state.draft, venue_whitelist=storage_fields.venue_whitelist)
        )
        journal_target = (
            render_settings_list_text(journal_snapshot.contents, draft.journals, draft.publishers,
                                      access_services=draft.access_services, path=journal_path)
            if journal_changed else journal_snapshot.contents
        )
        monitor_target = (
            _render_monitor_yaml(draft, venue_whitelist=storage_fields.venue_whitelist)
            if monitor_changed else monitor_snapshot.contents
        )
        journal_changed = journal_changed and journal_target != journal_snapshot.contents
        monitor_changed = monitor_changed and monitor_target != monitor_snapshot.contents
    except ConfigurationError as error:
        return _result_from_state(
            outcome=SettingsSaveOutcome.INVALID_DRAFT, validation=validation,
            state=load_settings(config_path), issues=(SettingsIssue(SettingsIssueSource.JOURNAL_DATA, str(error)),),
            journal_written=False, monitor_written=False,
        )

    try:
        for lock in operation_locks:
            lock.verify()
        # list-only Save 仍在实际写入边界检查未写的 monitor 原始版本。
        if journal_changed and _read_snapshot(
            config_path, source=SettingsIssueSource.MONITOR_CONFIG,
        ).revision != draft.monitor_revision:
            raise ContentChangedError("monitor config changed before journal data write")
        if journal_changed:
            _write_snapshot_target(journal_path, journal_target, journal_snapshot)
    except (ContentChangedError, FileExistsError) as error:
        state = load_settings(config_path)
        return _result_from_state(
            outcome=SettingsSaveOutcome.REVISION_CONFLICT,
            validation=validation,
            state=state,
            issues=(
                SettingsIssue(
                    source=SettingsIssueSource.STORAGE,
                    message=f"Settings revision conflict before journal data write: {error}",
                ),
            ),
            journal_written=False,
            monitor_written=False,
        )
    except (CompareReadError, OSError) as error:
        state = load_settings(config_path)
        # 安全写入在替换成功后也可能于目录 fsync 抛错；以真实磁盘内容确认结果。
        committed = journal_changed and state.draft.journal_revision.digest == _digest(journal_target)
        return _result_from_state(
            outcome=SettingsSaveOutcome.PARTIAL_SAVE if committed else SettingsSaveOutcome.WRITE_FAILED,
            validation=validation,
            state=state,
            issues=(
                SettingsIssue(
                    source=SettingsIssueSource.JOURNAL_DATA,
                    message=f"unable to finish journal data write: {error}",
                    path=journal_path,
                ),
            ),
            journal_written=committed,
            monitor_written=False,
        )

    try:
        for lock in operation_locks:
            lock.verify()
        if not journal_changed and _read_snapshot(journal_path, source=SettingsIssueSource.JOURNAL_DATA).revision != draft.journal_revision:
            raise ContentChangedError("journal data changed before monitor configuration write")
        if monitor_changed:
            _write_snapshot_target(config_path, monitor_target, monitor_snapshot)
    except (ContentChangedError, FileExistsError) as error:
        state = load_settings(config_path)
        return _result_from_state(
            outcome=SettingsSaveOutcome.PARTIAL_SAVE if journal_changed else SettingsSaveOutcome.REVISION_CONFLICT,
            validation=validation,
            state=state,
            issues=(
                SettingsIssue(
                    source=SettingsIssueSource.MONITOR_CONFIG,
                    message=f"monitor config changed before it could be written: {error}",
                    path=config_path,
                ),
            ),
            journal_written=journal_changed,
            monitor_written=False,
        )
    except (CompareReadError, OSError) as error:
        state = load_settings(config_path)
        committed = monitor_changed and state.draft.monitor_revision.digest == _digest(monitor_target)
        return _result_from_state(
            outcome=SettingsSaveOutcome.PARTIAL_SAVE if journal_changed or committed else SettingsSaveOutcome.WRITE_FAILED,
            validation=validation,
            state=state,
            issues=(
                SettingsIssue(
                    source=SettingsIssueSource.MONITOR_CONFIG,
                    message=f"unable to finish monitor config write: {error}",
                    path=config_path,
                ),
            ),
            journal_written=journal_changed,
            monitor_written=committed,
        )

    state = load_settings(config_path)
    return _result_from_state(
        outcome=SettingsSaveOutcome.SAVED,
        validation=validation,
        state=state,
        issues=state.issues,
        journal_written=journal_changed,
        monitor_written=monitor_changed,
    )


def _resolve_settings_targets(config_path: Path, draft: MonitorDraft, snapshot: _FileSnapshot,
                              fields: _DraftFields, client: OpenAlexClient | None,
                              *, deletion_approval: SettingsDeletionApproval | None = None) -> MonitorDraft:
    contents = snapshot.contents
    persisted, legacy, damage = _journal_storage(contents, config_path) if contents else ((), (), None)
    saved_publishers = parse_publisher_whitelist_text(contents, path=config_path) if contents else ()
    by_identity = {j.issn_l: j for j in persisted}
    by_publisher = {p.publisher_id: p for p in saved_publishers}
    submitted_publishers = {p.publisher_id: p for p in validate_publisher_configs(draft.publishers)}
    for publisher in submitted_publishers.values():
        saved = by_publisher.get(publisher.publisher_id)
        if saved is None or publisher.name != saved.name:
            raise ConfigurationError("Publisher ID/name are machine-managed metadata", field="publishers")
    journals = []
    for journal in draft.journals:
        saved = by_identity.get(journal.issn_l)
        if saved and journal.issn_l in draft.pending_journal_ids and journal.publisher_id is None:
            journal = saved.model_copy(update={"group": journal.group})
        elif saved and (journal.name != saved.name or journal.publisher_id != saved.publisher_id):
            raise ConfigurationError(f"Journal {journal.issn_l}: canonical name and Publisher ID are machine-managed metadata", field="journals")
        journals.append(journal)
    draft = replace(draft, journals=tuple(journals))
    if draft.legacy_journals and draft.legacy_journals != legacy:
        raise ConfigurationError("legacy migration input differs from persisted rows", field="journals")
    retained = {j.publisher_id for j in draft.journals if j.issn_l in by_identity and j.publisher_id is not None}
    if any(p not in submitted_publishers for p in retained if p in by_publisher):
        raise ConfigurationError("retained Publisher ID/name must match persisted state", field="publishers")
    new = [j for j in draft.journals if j.issn_l not in by_identity]
    journals = draft.journals
    needs_network = bool(legacy or new)
    with (nullcontext(client) if client is not None or not needs_network else OpenAlexClient(api_key=os.environ.get("OPENALEX_API_KEY"))) as provider:
        if legacy:
            confirmations = draft.migration_confirmations
            if draft.journals:
                if len(draft.journals) != len(legacy):
                    raise ConfigurationError("legacy migration cannot remove or partially migrate rows", field="journals")
                if not confirmations:
                    confirmations = tuple(MigrationConfirmation(i, j.issn_l, "explicit user-submitted target ISSN-L")
                                          for i, j in enumerate(draft.journals, 1))
            journals = resolve_legacy_journals(provider, legacy, confirmations=confirmations,
                workspace=fields.output_dir if fields.output_dir.is_absolute() else config_path.parent / fields.output_dir)
            if draft.journals:
                if any(j.group != old.group for j, old in zip(draft.journals, legacy)):
                    raise ConfigurationError("legacy migration must preserve Group for historical compatibility", field="journals")
        elif new:
            results = resolve_source_identities(provider, [j.issn_l for j in new])
            resolved = {r.requested_issn: r for r in results}
            canonical = {}
            for journal in new:
                result = resolved[journal.issn_l]
                if result.evidence is None:
                    raise ConfigurationError(f"Journal {journal.issn_l} requires metadata resolution: {result.status.value}: {result.diagnostic}", field="journals")
                evidence = result.evidence
                if any("inconsistent optional Publisher" in d for d in evidence.diagnostics):
                    raise ConfigurationError("unresolved contradictory Source metadata", field="journals")
                canonical[journal.issn_l] = JournalConfig(issn_l=journal.issn_l, name=evidence.display_name,
                                                         publisher_id=evidence.publisher_id, group=journal.group)
            journals = tuple(canonical.get(j.issn_l, j) for j in journals)
        active = tuple(dict.fromkeys(j.publisher_id for j in journals if j.publisher_id is not None))
        # Provider 解析可能改变实际级联集合，必须重新核对已确认的目标。
        cascaded = [p for p in saved_publishers if p.publisher_id not in active
                    and (p.publisher_url or p.access_service_id)]
        if cascaded and (deletion_approval is None
                         or deletion_approval.publisher_ids != tuple(p.publisher_id for p in cascaded)
                         or deletion_approval.journal_revision != draft.journal_revision
                         or deletion_approval.monitor_revision != draft.monitor_revision):
            raise ConfigurationError(
                "Publisher removal requires a cascade Preview/Confirm (Publisher URL / Access Mapping): "
                + ", ".join(p.publisher_id for p in cascaded), field="publishers",
            )
        if damage is not None and any(p not in active for p in by_publisher):
            raise ConfigurationError("Journal repair would discard saved Publisher metadata/Access URLs; restore their Journal associations", field="publishers")
        missing = [p for p in active if p not in by_publisher]
        if missing and not needs_network:
            raise ConfigurationError("active Publisher durable metadata missing; explicit migration required", field="publishers")
        metadata = resolve_publisher_metadata(provider, missing) if missing else ()
    initialized = {m.publisher_id: PublisherConfig(publisher_id=m.publisher_id, name=m.display_name, publisher_url=m.homepage_url) for m in metadata}
    publishers = tuple(submitted_publishers.get(p, by_publisher.get(p)) or initialized[p] for p in active)
    return replace(draft, journals=journals, publishers=publishers, legacy_journals=(), migration_confirmations=(), pending_journal_ids=())
