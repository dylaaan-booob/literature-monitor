"""Browser form adaptation for the Settings application boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import zip_longest
from pathlib import Path

from starlette.datastructures import FormData

from literature_monitor.application.settings import (
    ContentRevision,
    MonitorDraft,
    SettingsIssue,
    SettingsIssueSource,
)
from literature_monitor.config import JournalConfig, LogLevel
from literature_monitor.date_range import DateRangeSpec


@dataclass(frozen=True)
class SettingsJournalRow:
    name: str
    issns: str


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


def _revision_fields(revision: ContentRevision) -> tuple[str, str]:
    return ("1" if revision.exists else "0", revision.digest or "")


def settings_form_from_draft(draft: MonitorDraft) -> SettingsFormValues:
    """Project a structured draft into browser-editable field values."""

    monitor_exists, monitor_digest = _revision_fields(draft.monitor_revision)
    journal_exists, journal_digest = _revision_fields(draft.journal_revision)
    rows = tuple(
        SettingsJournalRow(name=journal.name, issns=", ".join(journal.issn))
        for journal in draft.journals
    )
    if not rows:
        rows = (SettingsJournalRow(name="", issns=""),)

    log_level = (
        draft.log_level.value
        if isinstance(draft.log_level, LogLevel)
        else str(draft.log_level)
    )
    return SettingsFormValues(
        name=draft.name,
        keyword_expression=draft.keyword_expression,
        journals=rows,
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
    rows = tuple(
        SettingsJournalRow(name=name, issns=issn_values)
        for name, issn_values in zip_longest(names, issns, fillvalue="")
    )
    if not rows:
        rows = (SettingsJournalRow(name="", issns=""),)

    return SettingsFormValues(
        name=str(form.get("name", "")),
        keyword_expression=str(form.get("keyword_expression", "")),
        journals=rows,
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

    journals: list[JournalConfig] = []
    for row in values.journals:
        identifiers = tuple(
            identifier.strip()
            for identifier in row.issns.split(",")
            if identifier.strip()
        )
        try:
            journals.append(JournalConfig(name=row.name, issn=identifiers))
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
