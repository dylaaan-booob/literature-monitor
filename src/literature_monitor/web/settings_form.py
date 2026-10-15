"""Browser form adaptation for the Settings application boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path

from pydantic import ValidationError
from starlette.datastructures import FormData

from literature_monitor.application.journal_migration import MigrationConfirmation
from literature_monitor.application.settings import (
    ContentRevision,
    MonitorDraft,
    SettingsIssue,
    SettingsIssueSource,
)
from literature_monitor.config import AccessService, InstitutionConfig, KeptWorkflowConfig, JournalConfig, LegacyJournal, LogLevel, PublisherConfig
from literature_monitor.date_range import DateRangeSpec, DateRangeError, validate_date_range_spec
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
    publisher_url: str = ""
    access_service_id: str = ""

    @property
    def access_url(self) -> str:
        return self.publisher_url

    @property
    def safe_access_url(self) -> str | None:
        try:
            return normalize_public_http_url(self.publisher_url)
        except ValueError:
            return None


@dataclass(frozen=True)
class SettingsAccessServiceRow:
    id: str
    name: str
    access_url: str = ""
    access_method: str = "carsi"

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
    from_month: str
    to_month: str
    output_dir: str
    log_level: str
    monitor_revision_exists: str
    monitor_revision_digest: str
    journal_revision_exists: str
    journal_revision_digest: str
    publishers: tuple[SettingsPublisherRow, ...] = ()
    access_services: tuple[SettingsAccessServiceRow, ...] = ()
    legacy_journals: str = ""
    migration_targets: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()  # Presentation only; empty Groups are not durable.
    institution_name: str = ""
    institution_idp_entity_id: str = ""
    automatic_attribute_release_consent: bool = False
    institution_issues: tuple[SettingsIssue, ...] = ()
    auto_import_after_preparation: bool = False
    transition_delay_seconds: str = "3"
    display_name: str = ""

    @property
    def legacy_rows(self) -> tuple[LegacyJournal, ...]:
        try:
            return tuple(LegacyJournal.model_validate(j) for j in json.loads(self.legacy_journals)) if self.legacy_journals else ()
        except (ValueError, TypeError):
            return ()


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
        publishers=tuple(SettingsPublisherRow(p.name, p.publisher_id, p.publisher_url or "",
                                               p.access_service_id or "") for p in draft.publishers),
        access_services=tuple(SettingsAccessServiceRow(s.id, s.name, s.access_url or "", s.access_method) for s in draft.access_services),
        legacy_journals=json.dumps([j.model_dump() for j in draft.legacy_journals]) if draft.legacy_journals else "",
        groups=tuple(dict.fromkeys(row.group for row in rows if row.group)),
        from_month=draft.date_spec.from_month,
        to_month=draft.date_spec.to_month,
        output_dir=str(draft.output_dir),
        log_level=log_level,
        monitor_revision_exists=monitor_exists,
        monitor_revision_digest=monitor_digest,
        journal_revision_exists=journal_exists,
        journal_revision_digest=journal_digest,
        auto_import_after_preparation=draft.kept_workflow.auto_import_after_preparation,
        transition_delay_seconds=str(draft.kept_workflow.transition_delay_seconds),
        display_name=draft.kept_workflow.display_name or "",
        institution_name=draft.institution.name or "" if draft.institution else "",
        institution_idp_entity_id=draft.institution.idp_entity_id or "" if draft.institution else "",
        automatic_attribute_release_consent=draft.institution.automatic_attribute_release_consent if draft.institution else False,
    )


def settings_form_from_submission(form: FormData) -> SettingsFormValues:
    """Capture submitted browser strings exactly enough for redisplay."""

    names = tuple(str(value) for value in form.getlist("journal_name"))
    issns = tuple(str(value) for value in form.getlist("journal_issns"))
    publishers = tuple(str(value) for value in form.getlist("journal_publisher_id"))
    pending = tuple(str(value) == "1" for value in form.getlist("journal_pending"))
    assignments = tuple(str(value) for value in form.getlist("journal_group"))
    order_values = tuple(str(v) for v in form.getlist("journal_order"))
    raw_rows = tuple(zip_longest(names, issns, assignments, publishers, pending, fillvalue=""))
    rows = tuple(
        SettingsJournalRow(name=name, issns=issn_values, group=group, publisher_id=publisher,
                           pending=bool(is_pending))
        for name, issn_values, group, publisher, is_pending in raw_rows
    )
    order_is_numeric = all(item.isascii() and item.isdecimal() and len(item) <= 12 for item in order_values)
    invalid_order = bool(order_values) and (
        len(order_values) != len(rows)
        or not order_is_numeric
        or len({int(value) for value in order_values}) != len(order_values)
    )
    if order_values and not invalid_order:
        # 可视 Group 容器会改变表单节点顺序；保存仍须采用草稿原有 Journal 顺序。
        rows = tuple(row for _, row in sorted(zip((int(value) for value in order_values), rows)))
    if not rows:
        rows = (SettingsJournalRow(name="Pending resolution", issns="", pending=True),)

    group_names = tuple(str(value) for value in form.getlist("settings_group"))
    group_names += tuple(row.group for row in rows)
    institution_fields = {"institution_name", "institution_idp_entity_id", "automatic_attribute_release_consent"}
    # Enumerate the existing editor/import contract so credential aliases cannot be ignored.
    workflow_fields = {"auto_import_after_preparation", "transition_delay_seconds", "display_name"}
    supported_fields = institution_fields | workflow_fields | {
        "csrf_token", "name", "keyword_expression", "output_dir", "log_level",
        "from_month", "to_month",
        "monitor_revision_exists", "monitor_revision_digest", "journal_revision_exists", "journal_revision_digest",
        "journal_name", "journal_issns", "journal_publisher_id", "journal_pending", "journal_group", "journal_order", "settings_group",
        "publisher_name", "publisher_id", "publisher_access_url", "publisher_access_service_id", "publisher_order",
        "service_id", "service_name", "service_access_url", "service_access_method", "legacy_journals", "migration_issn_l",
        "settings_deletion_proof", "confirm_settings_deletions",
        "markdown_file", "markdown_import_payload", "markdown_import_filename",
        "markdown_import_proof", "confirm_full_import", "import_discard",
        "export_discard", "reload_step", "reload_word",
        "access_upgrade_proof", "confirm_access_upgrade", "upgrade_discard",
    }
    forbidden = any(key not in supported_fields for key in form)
    duplicate = any(len(form.getlist(key)) > 1 for key in institution_fields | workflow_fields)
    publisher_lengths = {len(form.getlist(key)) for key in (
        "publisher_name", "publisher_id", "publisher_access_url", "publisher_access_service_id"
    )}
    service_lengths = {len(form.getlist(key)) for key in ("service_id", "service_name", "service_access_url", "service_access_method")}
    journal_lengths = {len(names), len(issns), len(assignments), len(publishers), len(pending)}
    incomplete_rows = (len(journal_lengths) != 1 or len(publisher_lengths) != 1
                       or len(service_lengths) != 1)
    publisher_order = tuple(str(v) for v in form.getlist("publisher_order"))
    invalid_publisher_order = bool(publisher_order) and (
        len(publisher_order) != len(form.getlist("publisher_id"))
        or any(not value.isascii() or not value.isdecimal() or len(value) > 12 for value in publisher_order)
    )
    if publisher_order and not invalid_publisher_order:
        invalid_publisher_order = len({int(value) for value in publisher_order}) != len(publisher_order)
    institution_issues = (
        (_form_issue("institution", "Unsupported Settings field or duplicate institution identifier. Credentials, sessions and login routes are not settings."),)
        if forbidden or duplicate or form.get("automatic_attribute_release_consent", "") not in {"", "1"} else ()
    )
    if form.get("auto_import_after_preparation", "") not in {"", "1"}:
        institution_issues += (_form_issue("kept_workflow", "Invalid automatic continuation option."),)
    if incomplete_rows:
        institution_issues += (_form_issue(
            "journals/publishers/access_services", "Incomplete Journal/Publisher/Access Service form rows; reload Settings before saving.",
        ),)
    if invalid_order:
        institution_issues += (_form_issue("journals", "Invalid Journal order. Reload Settings before saving."),)
    if invalid_publisher_order:
        institution_issues += (_form_issue("publishers", "Invalid Publisher order. Reload Settings before saving."),)

    publisher_rows = tuple(SettingsPublisherRow(name, identity, url, service_id) for name, identity, url, service_id in zip_longest(
        (str(v) for v in form.getlist("publisher_name")),
        (str(v) for v in form.getlist("publisher_id")),
        (str(v) for v in form.getlist("publisher_access_url")),
        (str(v) for v in form.getlist("publisher_access_service_id")), fillvalue=""))
    if publisher_order and not invalid_publisher_order:
        publisher_rows = tuple(item for _, item in sorted(zip(map(int, publisher_order), publisher_rows)))

    return SettingsFormValues(
        name=str(form.get("name", "")),
        keyword_expression=str(form.get("keyword_expression", "")),
        journals=rows,
        publishers=publisher_rows,
        access_services=tuple(SettingsAccessServiceRow(identity, name, url, method) for identity, name, url, method in zip_longest(
            (str(v) for v in form.getlist("service_id")),
            (str(v) for v in form.getlist("service_name")),
            (str(v) for v in form.getlist("service_access_url")),
            (str(v) for v in form.getlist("service_access_method")), fillvalue="")),
        legacy_journals=str(form.get("legacy_journals", "")),
        migration_targets=tuple(str(v) for v in form.getlist("migration_issn_l")),
        groups=tuple(dict.fromkeys(group for group in group_names if group.strip())),
        from_month=str(form.get("from_month", "")),
        to_month=str(form.get("to_month", "")),
        output_dir=str(form.get("output_dir", "")),
        log_level=str(form.get("log_level", "")),
        monitor_revision_exists=str(form.get("monitor_revision_exists", "")),
        monitor_revision_digest=str(form.get("monitor_revision_digest", "")),
        journal_revision_exists=str(form.get("journal_revision_exists", "")),
        journal_revision_digest=str(form.get("journal_revision_digest", "")),
        institution_name=str(form.get("institution_name", "")),
        institution_idp_entity_id=str(form.get("institution_idp_entity_id", "")),
        automatic_attribute_release_consent=form.get("automatic_attribute_release_consent") == "1",
        institution_issues=institution_issues,
        auto_import_after_preparation=form.get("auto_import_after_preparation") == "1",
        transition_delay_seconds=str(form.get("transition_delay_seconds", "3")),
        display_name=str(form.get("display_name", "")),
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


def settings_draft_from_form(
    values: SettingsFormValues,
) -> tuple[MonitorDraft | None, tuple[SettingsIssue, ...]]:
    """Construct application/domain values without reimplementing validation."""

    issues: list[SettingsIssue] = list(values.institution_issues)
    institution = None
    try:
        public = InstitutionConfig(name=values.institution_name, idp_entity_id=values.institution_idp_entity_id,
                                   automatic_attribute_release_consent=values.automatic_attribute_release_consent)
        institution = public if public.name or public.idp_entity_id or public.automatic_attribute_release_consent else None
    except ValidationError as error:
        detail = error.errors()[0]
        issues.append(_form_issue("institution." + str(detail["loc"][0]), detail["msg"]))
    try:
        kept_workflow = KeptWorkflowConfig(
            auto_import_after_preparation=values.auto_import_after_preparation,
            transition_delay_seconds=float(values.transition_delay_seconds),
            display_name=values.display_name,
        )
    except (ValueError, TypeError) as error:
        issues.append(_form_issue("kept_workflow", str(error)))
    date_spec = DateRangeSpec(values.from_month, values.to_month)
    try:
        validate_date_range_spec(date_spec)
    except DateRangeError as error:
        issues.append(_form_issue(error.field, str(error)))

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
    access_services = []
    legacy = ()
    confirmations = ()
    try:
        publishers = [PublisherConfig(name=p.name, publisher_id=p.publisher_id,
                                      publisher_url=p.publisher_url, access_service_id=p.access_service_id or None)
                      for p in values.publishers]
        access_services = [AccessService(id=s.id, name=s.name, access_url=s.access_url, access_method=s.access_method)
                           for s in values.access_services]
        if values.legacy_journals:
            legacy = tuple(LegacyJournal.model_validate(j) for j in json.loads(values.legacy_journals))
            if len(values.migration_targets) > len(legacy):
                raise ValueError("migration confirmation references an unknown legacy row")
            confirmations = tuple(MigrationConfirmation(i, value, "explicit user confirmation in Settings")
                                  for i, value in enumerate(values.migration_targets, 1) if value.strip())
    except (ValueError, TypeError) as error:
        issues.append(_form_issue("publishers/access_services/legacy_journals", str(error)))
    journals: list[JournalConfig] = []
    pending_ids = []
    for row in values.journals:
        if not row.issns and row.name in ("", "Pending resolution") and (legacy or row.pending) and not row.publisher_id:
            continue
        try:
            journals.append(
                JournalConfig(name=row.name, issn_l=row.issns, publisher_id=row.publisher_id or None,
                              group=row.group.strip() or None)
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
            access_services=tuple(access_services),
            legacy_journals=legacy,
            migration_confirmations=confirmations,
            date_spec=date_spec,
            output_dir=Path(values.output_dir.strip()),
            log_level=values.log_level,
            monitor_revision=monitor_revision,
            journal_revision=journal_revision,
            institution=institution,
            kept_workflow=kept_workflow,
        ),
        (),
    )
