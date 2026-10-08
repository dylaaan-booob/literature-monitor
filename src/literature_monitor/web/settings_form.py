"""Browser form adaptation for the Settings application boundary."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, replace
from datetime import date
from itertools import zip_longest
from pathlib import Path

from starlette.datastructures import FormData

from literature_monitor.application.journal_import import JournalImportMode, JournalImportPlan
from literature_monitor.application.journal_migration import MigrationConfirmation
from literature_monitor.application.settings import (
    ContentRevision,
    MonitorDraft,
    SettingsIssue,
    SettingsIssueSource,
)
from literature_monitor.config import JournalConfig, LegacyJournal, LogLevel, PublisherConfig
from literature_monitor.date_range import DateRangeSpec
from literature_monitor.url_safety import normalize_public_http_url


@dataclass(frozen=True)
class SettingsJournalRow:
    name: str
    issns: str
    group: str = ""
    publisher_id: str = ""
    pending: bool = False


@dataclass(frozen=True)
class SettingsPublisherRow:
    name: str
    publisher_id: str
    access_url: str = ""

    @property
    def safe_access_url(self) -> str | None:
        try:
            return normalize_public_http_url(self.access_url)
        except ValueError:
            return None


@dataclass(frozen=True)
class SettingsFormValues:
    name: str
    keyword_expression: str
    journals: tuple[SettingsJournalRow, ...]
    from_date: str
    to_date: str
    window_days: str
    output_dir: str
    log_level: str
    monitor_revision_exists: str
    monitor_revision_digest: str
    journal_revision_exists: str
    journal_revision_digest: str
    publishers: tuple[SettingsPublisherRow, ...] = ()
    legacy_journals: str = ""
    migration_targets: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()  # Presentation only; empty Groups are not durable.

    @property
    def legacy_rows(self) -> tuple[LegacyJournal, ...]:
        try:
            return tuple(LegacyJournal.model_validate(j) for j in json.loads(self.legacy_journals)) if self.legacy_journals else ()
        except (ValueError, TypeError):
            return ()


@dataclass(frozen=True)
class SettingsImportValues:
    contents: str = ""
    mode: str = JournalImportMode.MERGE.value
    plan: JournalImportPlan | None = None
    error: str | None = None
    confirmation_source: str = ""
    confirmation_rows: tuple[str, ...] = ()
    confirmation_targets: tuple[str, ...] = ()

    @property
    def source_revision(self) -> str:
        return hashlib.sha256(self.contents.encode("utf-8")).hexdigest()

    def target_for(self, source_row: int) -> str:
        return next((target for row, target in zip(self.confirmation_rows, self.confirmation_targets)
                     if row == str(source_row)), "")

    def confirmations(self, plan: JournalImportPlan) -> tuple[MigrationConfirmation, ...]:
        if not self.confirmation_rows and not self.confirmation_targets:
            return ()
        if self.confirmation_source != self.source_revision:
            raise ValueError("Import source changed; Preview and confirm the current rows again.")
        if self.confirmation_rows != tuple(str(row.source_row) for row in plan.legacy_rows) or len(self.confirmation_targets) != len(plan.legacy_rows):
            raise ValueError("Import confirmation rows do not match this source; Preview again.")
        return tuple(MigrationConfirmation(i, target, "explicit user confirmation for this import row")
                     for i, target in enumerate(self.confirmation_targets, 1) if target.strip())


def settings_form_after_import(
    values: SettingsFormValues, draft: MonitorDraft,
) -> SettingsFormValues:
    """Project imported Journals, retaining only genuinely empty draft Groups."""

    projected = settings_form_from_draft(draft)
    previously_represented = {row.group for row in values.journals if row.group}
    empty_groups = tuple(group for group in values.groups if group not in previously_represented)
    # A2 changes only Journals. Preserve raw non-Journal browser strings/revisions.
    previous = {row.issns: row for row in values.journals}
    rows = tuple(replace(row, pending=previous[row.issns].pending if row.issns in previous else True)
                 for row in projected.journals)
    return replace(values, journals=rows,
                   groups=tuple(dict.fromkeys(projected.groups + empty_groups)))


def _revision_fields(revision: ContentRevision) -> tuple[str, str]:
    return ("1" if revision.exists else "0", revision.digest or "")


def settings_form_from_draft(draft: MonitorDraft) -> SettingsFormValues:
    """Project a structured draft into browser-editable field values."""

    monitor_exists, monitor_digest = _revision_fields(draft.monitor_revision)
    journal_exists, journal_digest = _revision_fields(draft.journal_revision)
    rows = tuple(
        SettingsJournalRow(
            name=journal.name,
            issns=journal.issn_l,
            group=journal.group or "",
            publisher_id=journal.publisher_id or "",
            pending=journal.issn_l in draft.pending_journal_ids,
        )
        for journal in draft.journals
    )
    if not rows:
        rows = (SettingsJournalRow(name="Pending resolution", issns="", pending=True),)

    log_level = (
        draft.log_level.value
        if isinstance(draft.log_level, LogLevel)
        else str(draft.log_level)
    )
    return SettingsFormValues(
        name=draft.name,
        keyword_expression=draft.keyword_expression,
        journals=rows,
        publishers=tuple(SettingsPublisherRow(p.name, p.publisher_id, p.access_url or "") for p in draft.publishers),
        legacy_journals=json.dumps([j.model_dump() for j in draft.legacy_journals]) if draft.legacy_journals else "",
        groups=tuple(dict.fromkeys(row.group for row in rows if row.group)),
        from_date=(
            draft.date_spec.from_date.isoformat()
            if draft.date_spec.from_date is not None
            else ""
        ),
        to_date=(
            draft.date_spec.to_date.isoformat()
            if draft.date_spec.to_date is not None
            else ""
        ),
        window_days=(
            str(draft.date_spec.window_days)
            if draft.date_spec.window_days is not None
            else ""
        ),
        output_dir=str(draft.output_dir),
        log_level=log_level,
        monitor_revision_exists=monitor_exists,
        monitor_revision_digest=monitor_digest,
        journal_revision_exists=journal_exists,
        journal_revision_digest=journal_digest,
    )


def settings_form_from_submission(form: FormData) -> SettingsFormValues:
    """Capture submitted browser strings exactly enough for redisplay."""

    names = tuple(str(value) for value in form.getlist("journal_name"))
    issns = tuple(str(value) for value in form.getlist("journal_issns"))
    publishers = tuple(str(value) for value in form.getlist("journal_publisher_id"))
    pending = tuple(str(value) == "1" for value in form.getlist("journal_pending"))
    assignments = tuple(str(value) for value in form.getlist("journal_group"))
    rows = tuple(
        SettingsJournalRow(name=name, issns=issn_values, group=group, publisher_id=publisher, pending=bool(is_pending))
        for name, issn_values, group, publisher, is_pending in zip_longest(names, issns, assignments, publishers, pending, fillvalue="")
    )
    if not rows:
        rows = (SettingsJournalRow(name="Pending resolution", issns="", pending=True),)

    group_names = tuple(str(value) for value in form.getlist("settings_group"))
    group_names += tuple(row.group for row in rows)

    return SettingsFormValues(
        name=str(form.get("name", "")),
        keyword_expression=str(form.get("keyword_expression", "")),
        journals=rows,
        publishers=tuple(SettingsPublisherRow(name, identity, url) for name, identity, url in zip_longest(
            (str(v) for v in form.getlist("publisher_name")),
            (str(v) for v in form.getlist("publisher_id")),
            (str(v) for v in form.getlist("publisher_access_url")), fillvalue="")),
        legacy_journals=str(form.get("legacy_journals", "")),
        migration_targets=tuple(str(v) for v in form.getlist("migration_issn_l")),
        groups=tuple(dict.fromkeys(group for group in group_names if group.strip())),
        from_date=str(form.get("from_date", "")),
        to_date=str(form.get("to_date", "")),
        window_days=str(form.get("window_days", "")),
        output_dir=str(form.get("output_dir", "")),
        log_level=str(form.get("log_level", "")),
        monitor_revision_exists=str(form.get("monitor_revision_exists", "")),
        monitor_revision_digest=str(form.get("monitor_revision_digest", "")),
        journal_revision_exists=str(form.get("journal_revision_exists", "")),
        journal_revision_digest=str(form.get("journal_revision_digest", "")),
    )


def _form_issue(field: str, message: str) -> SettingsIssue:
    return SettingsIssue(
        source=SettingsIssueSource.VALIDATION,
        field=field,
        message=message,
    )


def _parse_revision(
    exists_value: str,
    digest_value: str,
    *,
    field: str,
) -> tuple[ContentRevision | None, SettingsIssue | None]:
    if exists_value not in {"0", "1"}:
        return None, _form_issue(field, "Settings revision is invalid; reload Settings.")
    exists = exists_value == "1"
    digest = digest_value or None
    if not exists and digest is not None:
        return None, _form_issue(field, "Settings revision is invalid; reload Settings.")
    return ContentRevision(exists=exists, digest=digest), None


def _parse_optional_date(
    value: str,
    *,
    field: str,
) -> tuple[date | None, SettingsIssue | None]:
    if not value:
        return None, None
    try:
        return date.fromisoformat(value), None
    except ValueError:
        return None, _form_issue(field, "Enter a valid ISO date (YYYY-MM-DD).")


def settings_draft_from_form(
    values: SettingsFormValues,
) -> tuple[MonitorDraft | None, tuple[SettingsIssue, ...]]:
    """Construct application/domain values without reimplementing validation."""

    issues: list[SettingsIssue] = []
    from_date, issue = _parse_optional_date(values.from_date, field="from_date")
    if issue is not None:
        issues.append(issue)
    to_date, issue = _parse_optional_date(values.to_date, field="to_date")
    if issue is not None:
        issues.append(issue)

    window_days: int | None = None
    if values.window_days:
        try:
            window_days = int(values.window_days)
        except ValueError:
            issues.append(_form_issue("window_days", "Window days must be an integer."))

    monitor_revision, issue = _parse_revision(
        values.monitor_revision_exists,
        values.monitor_revision_digest,
        field="monitor_revision",
    )
    if issue is not None:
        issues.append(issue)
    journal_revision, issue = _parse_revision(
        values.journal_revision_exists,
        values.journal_revision_digest,
        field="journal_revision",
    )
    if issue is not None:
        issues.append(issue)

    publishers = []
    legacy = ()
    confirmations = ()
    try:
        publishers = [PublisherConfig(name=p.name, publisher_id=p.publisher_id, access_url=p.access_url) for p in values.publishers]
        if values.legacy_journals:
            legacy = tuple(LegacyJournal.model_validate(j) for j in json.loads(values.legacy_journals))
            if len(values.migration_targets) > len(legacy):
                raise ValueError("migration confirmation references an unknown legacy row")
            confirmations = tuple(MigrationConfirmation(i, value, "explicit user confirmation in Settings")
                                  for i, value in enumerate(values.migration_targets, 1) if value.strip())
    except (ValueError, TypeError) as error:
        issues.append(_form_issue("publishers/legacy_journals", str(error)))
    journals: list[JournalConfig] = []
    pending_ids = []
    for row in values.journals:
        if not row.issns and row.name in ("", "Pending resolution") and (legacy or row.pending) and not row.publisher_id:
            continue
        try:
            journals.append(
                JournalConfig(name=row.name, issn_l=row.issns, publisher_id=row.publisher_id or None, group=row.group.strip() or None)
            )
            if row.pending:
                pending_ids.append(journals[-1].issn_l)
        except ValueError as error:
            issues.append(_form_issue("journals", str(error)))

    if issues:
        return None, tuple(issues)
    assert monitor_revision is not None
    assert journal_revision is not None
    return (
        MonitorDraft(
            name=values.name,
            keyword_expression=values.keyword_expression,
            journals=tuple(journals),
            pending_journal_ids=tuple(pending_ids),
            publishers=tuple(publishers),
            legacy_journals=legacy,
            migration_confirmations=confirmations,
            date_spec=DateRangeSpec(
                from_date=from_date,
                to_date=to_date,
                window_days=window_days,
            ),
            output_dir=Path(values.output_dir.strip()),
            log_level=values.log_level,
            monitor_revision=monitor_revision,
            journal_revision=journal_revision,
        ),
        (),
    )
