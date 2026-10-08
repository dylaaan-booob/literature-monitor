"""Pure Journal import parsing, preview, and draft-only Apply under SPEC §41.5."""

from __future__ import annotations

import csv
import os
from contextlib import nullcontext
from dataclasses import dataclass, replace
from enum import Enum
from io import StringIO
from pathlib import Path

from literature_monitor.application.settings import MonitorDraft
from literature_monitor.application.journal_migration import MigrationConfirmation, resolve_legacy_journals
from literature_monitor.openalex import OpenAlexClient, OpenAlexError
from literature_monitor.config import (
    ConfigurationError,
    JournalConfig,
    LegacyJournal,
    journal_whitelist_table,
    _journal_table_is_legacy,
    normalize_journal_issn,
    parse_journal_table_cells,
    validate_journal_configs,
    validate_journal_storage,
)


class JournalImportMode(str, Enum):
    MERGE = "MERGE"
    REPLACE = "REPLACE"


class JournalImportFormat(str, Enum):
    CSV = "CSV"
    TSV = "TSV"
    MARKDOWN = "MARKDOWN"


class JournalImportChange(str, Enum):
    ADD = "ADD"
    NO_OP_DUPLICATE = "NO_OP_DUPLICATE"
    ISSN_MERGE = "ISSN_MERGE"
    ISSN_REMOVE = "ISSN_REMOVE"
    GROUP_MOVE = "GROUP_MOVE"
    ORDER_CHANGE = "ORDER_CHANGE"
    REMOVE = "REMOVE"
    CONFLICT = "CONFLICT"
    INVALID_ROW = "INVALID_ROW"
    FORMAT_ERROR = "FORMAT_ERROR"
    MIGRATION_REQUIRED = "MIGRATION_REQUIRED"


@dataclass(frozen=True)
class JournalImportRow:
    source_row: int
    name: str
    issn_cell: str
    group: str | None


@dataclass(frozen=True)
class JournalImportEntry:
    kind: JournalImportChange
    journal_name: str | None = None
    source_rows: tuple[int, ...] = ()
    added_issns: tuple[str, ...] = ()
    removed_issns: tuple[str, ...] = ()
    previous_group: str | None = None
    new_group: str | None = None
    previous_position: int | None = None
    new_position: int | None = None
    previous_issns: tuple[str, ...] = ()
    new_issns: tuple[str, ...] = ()
    message: str | None = None


@dataclass(frozen=True)
class ParsedJournalImport:
    source_format: JournalImportFormat | None
    rows: tuple[JournalImportRow, ...]
    diagnostics: tuple[JournalImportEntry, ...] = ()
    migration_required: bool = False


@dataclass(frozen=True)
class JournalImportPlan:
    mode: JournalImportMode
    source_format: JournalImportFormat | None
    imported_journals: tuple[JournalConfig, ...]
    entries: tuple[JournalImportEntry, ...]
    resulting_journals: tuple[JournalConfig, ...] | None
    metadata_resolution_required: bool = False
    legacy_rows: tuple[JournalImportRow, ...] = ()

    @property
    def can_apply(self) -> bool:
        return self.resulting_journals is not None

    @property
    def can_reconcile(self) -> bool:
        return bool(self.legacy_rows) and not any(e.kind in (
            JournalImportChange.INVALID_ROW, JournalImportChange.FORMAT_ERROR,
            JournalImportChange.CONFLICT,
        ) for e in self.entries)


@dataclass(frozen=True)
class JournalImportApplyResult:
    draft: MonitorDraft
    plan: JournalImportPlan

    @property
    def applied(self) -> bool:
        return self.plan.can_apply


def parse_journal_import(contents: str) -> ParsedJournalImport:
    """Recognize only explicit CSV/TSV headers or the supported Journals section."""

    target_headers = (("Journal", "ISSN-L"), ("Journal", "ISSN-L", "Group"))
    legacy_headers = (("Journal", "ISSN/EISSN"), ("Journal", "ISSN/EISSN", "Group"))
    headers = target_headers + legacy_headers
    text = contents.removeprefix("\ufeff")
    rows: list[JournalImportRow] = []
    diagnostics: list[JournalImportEntry] = []

    for delimiter, source_format in (
        (",", JournalImportFormat.CSV), ("\t", JournalImportFormat.TSV),
    ):
        reader = csv.reader(StringIO(text, newline=""), delimiter=delimiter, strict=True)
        try:
            header = next((row for row in reader if row), [])
        except csv.Error:
            continue
        if tuple(cell.strip() for cell in header) not in headers:
            continue

        while True:
            source_row = reader.line_num + 1
            try:
                cells = next(reader)
            except StopIteration:
                break
            except csv.Error as error:
                diagnostics.append(JournalImportEntry(
                    JournalImportChange.INVALID_ROW,
                    source_rows=(source_row,),
                    message=f"Row {source_row}: invalid {source_format.value}: {error}",
                ))
                break
            if not cells:
                continue
            if len(cells) != len(header):
                diagnostics.append(JournalImportEntry(
                    JournalImportChange.INVALID_ROW,
                    journal_name=cells[0],
                    source_rows=(source_row,),
                    message=f"Row {source_row}: expected {len(header)} columns, found {len(cells)}",
                ))
                continue
            rows.append(JournalImportRow(
                source_row, cells[0], cells[1], cells[2] if len(header) == 3 else None,
            ))
        return ParsedJournalImport(source_format, tuple(rows), tuple(diagnostics),
                                   tuple(cell.strip() for cell in header) in legacy_headers)

    if any(line.strip() == "## Journals" for line in text.splitlines()):
        path = Path("<journal import>")
        try:
            columns, raw_rows = journal_whitelist_table(text, path=path)
        except ConfigurationError as error:
            return ParsedJournalImport(JournalImportFormat.MARKDOWN, (), (
                JournalImportEntry(JournalImportChange.FORMAT_ERROR, message=str(error)),
            ))
        for source_row, raw_row in raw_rows:
            try:
                cells = parse_journal_table_cells(raw_row, path, source_row, columns)
            except ConfigurationError as error:
                diagnostics.append(JournalImportEntry(
                    JournalImportChange.INVALID_ROW,
                    source_rows=(source_row,),
                    message=str(error),
                ))
                continue
            rows.append(JournalImportRow(
                source_row, cells[0], cells[1], cells[2] if columns == 3 else cells[3] if columns == 4 else None,
            ))
        return ParsedJournalImport(JournalImportFormat.MARKDOWN, tuple(rows), tuple(diagnostics),
                                   _journal_table_is_legacy(text))

    return ParsedJournalImport(None, (), (
        JournalImportEntry(
            JournalImportChange.FORMAT_ERROR,
            message="Expected CSV/TSV header 'Journal, ISSN-L[, Group]' (legacy ISSN/EISSN is migration input) "
            "or a Literature Monitor Markdown Journals table.",
        ),
    ))


def _normalize_import_rows(
    parsed: ParsedJournalImport,
) -> tuple[tuple[JournalConfig, ...], dict[str, tuple[int, ...]], tuple[JournalImportEntry, ...]]:
    journals: dict[str, JournalConfig] = {}
    source_rows: dict[str, tuple[int, ...]] = {}
    diagnostics = list(parsed.diagnostics)
    if parsed.migration_required:
        for row in parsed.rows:
            try:
                LegacyJournal(name=row.name, issn=tuple(normalize_journal_issn(v) for v in row.issn_cell.split("/")), group=row.group or None)
            except ValueError as error:
                diagnostics.append(JournalImportEntry(JournalImportChange.INVALID_ROW, row.name, (row.source_row,), message=str(error)))
        diagnostics.append(JournalImportEntry(
            JournalImportChange.MIGRATION_REQUIRED,
            source_rows=tuple(row.source_row for row in parsed.rows),
            message="Legacy ISSN/EISSN import requires Provider reconciliation and identity confirmation (A4).",
        ))
        return (), {}, tuple(diagnostics)
    for row in parsed.rows:
        try:
            journal = JournalConfig(
                name=row.name, issn_l=normalize_journal_issn(row.issn_cell),
                group=(row.group.strip() or None) if row.group is not None else None,
            )
            validate_journal_storage((journal,))
        except ValueError as error:
            diagnostics.append(JournalImportEntry(
                JournalImportChange.INVALID_ROW, row.name, (row.source_row,),
                message=f"Row {row.source_row}: {error}",
            ))
            continue
        key = journal.issn_l
        previous = journals.get(key)
        previous_rows = source_rows.get(key, ())
        if previous is not None:
            # Keep the first display hint; only incompatible user Groups conflict.
            if (
                previous.group is not None and journal.group is not None and previous.group != journal.group
            ):
                diagnostics.append(JournalImportEntry(
                    JournalImportChange.CONFLICT, journal.name, previous_rows + (row.source_row,),
                    previous_group=previous.group, new_group=journal.group,
                    message=f"Same ISSN-L {key} has conflicting imported row metadata.",
                ))
            journal = previous.model_copy(update={"group": previous.group or journal.group})
        journals[key] = journal
        source_rows[key] = previous_rows + (row.source_row,)
    return tuple(journals.values()), source_rows, tuple(diagnostics)


def preview_journal_import(
    draft: MonitorDraft, contents: str, *, mode: JournalImportMode = JournalImportMode.MERGE,
) -> JournalImportPlan:
    """Plan by explicit ISSN-L; new display hints await A4 canonicalization before Save."""
    if not isinstance(mode, JournalImportMode):
        raise ValueError("mode must be a JournalImportMode")
    parsed = parse_journal_import(contents)
    imported, source_rows, diagnostics = _normalize_import_rows(parsed)
    entries = list(diagnostics)
    if parsed.source_format is None or parsed.migration_required or any(
        entry.kind is JournalImportChange.FORMAT_ERROR for entry in diagnostics
    ):
        return JournalImportPlan(mode, parsed.source_format, imported, tuple(entries), None,
                                 legacy_rows=parsed.rows if parsed.migration_required else ())
    current = list(draft.journals)
    by_identity = {journal.issn_l: journal for journal in current}
    if len(by_identity) != len(current):
        entries.append(JournalImportEntry(JournalImportChange.CONFLICT,
                                         message="Current draft has duplicate ISSN-L identities."))
    positions = {journal.issn_l: index for index, journal in enumerate(current)}
    resulting = list(current) if mode is JournalImportMode.MERGE else []
    imported_keys = {journal.issn_l for journal in imported}
    for journal in imported:
        key, rows = journal.issn_l, source_rows[journal.issn_l]
        existing = by_identity.get(key)
        if existing is None:
            resulting.append(journal)
            entries.append(JournalImportEntry(JournalImportChange.ADD, journal.name, rows,
                                             added_issns=(key,), new_group=journal.group))
            continue
        group = journal.group if journal.group is not None else existing.group
        # Existing canonical name/Publisher metadata survives arbitrary imported hints.
        updated = existing.model_copy(update={"group": group})
        if mode is JournalImportMode.MERGE:
            resulting[positions[key]] = updated
        else:
            resulting.append(updated)
        changes = []
        if group != existing.group:
            changes.append(JournalImportEntry(JournalImportChange.GROUP_MOVE, existing.name, rows,
                                             previous_group=existing.group, new_group=group))
        if mode is JournalImportMode.REPLACE and positions[key] != len(resulting) - 1:
            changes.append(JournalImportEntry(JournalImportChange.ORDER_CHANGE, existing.name, rows,
                                             previous_position=positions[key], new_position=len(resulting) - 1,
                                             previous_issns=(key,), new_issns=(key,)))
        entries.extend(changes or [JournalImportEntry(JournalImportChange.NO_OP_DUPLICATE, existing.name, rows)])
    if mode is JournalImportMode.REPLACE:
        entries.extend(JournalImportEntry(JournalImportChange.REMOVE, journal.name,
                                         removed_issns=(journal.issn_l,), previous_group=journal.group)
                       for journal in current if journal.issn_l not in imported_keys)
    try:
        validated = validate_journal_configs(resulting)
        validate_journal_storage(validated)
    except ValueError as error:
        entries.append(JournalImportEntry(JournalImportChange.CONFLICT,
                                         message=f"Resulting Journal configuration is invalid: {error}"))
        validated = None
    if any(entry.kind in (JournalImportChange.INVALID_ROW, JournalImportChange.CONFLICT) for entry in entries):
        validated = None
    return JournalImportPlan(mode, parsed.source_format, imported, tuple(entries), validated,
                             any(journal.issn_l not in by_identity for journal in imported))


def apply_journal_import(
    draft: MonitorDraft,
    contents: str,
    *,
    mode: JournalImportMode = JournalImportMode.MERGE,
    client: OpenAlexClient | None = None,
    confirmations: tuple[MigrationConfirmation, ...] = (),
) -> JournalImportApplyResult:
    """Replan against the current draft and atomically replace only its Journals."""

    plan = preview_journal_import(draft, contents, mode=mode)
    parsed = parse_journal_import(contents)
    if parsed.migration_required and parsed.rows:
        try:
            if not plan.can_reconcile:
                raise ConfigurationError("invalid legacy import format")
            legacy = tuple(LegacyJournal(name=r.name, issn=tuple(normalize_journal_issn(v) for v in r.issn_cell.split("/")), group=r.group or None) for r in parsed.rows)
            with (nullcontext(client) if client is not None else OpenAlexClient(api_key=os.environ.get("OPENALEX_API_KEY"))) as provider:
                canonical = resolve_legacy_journals(provider, legacy, confirmations=confirmations)
            text = StringIO()
            writer = csv.writer(text)
            writer.writerow(("Journal", "ISSN-L", "Group"))
            writer.writerows((j.name, j.issn_l, j.group or "") for j in canonical)
            plan = preview_journal_import(draft, text.getvalue(), mode=mode)
            # Keep resolution metadata for new identities without changing saved rows.
            resolved = {j.issn_l: j for j in canonical}
            existing = {j.issn_l for j in draft.journals}
            targets = tuple(j if j.issn_l in existing else resolved[j.issn_l].model_copy(update={"group": j.group})
                            for j in plan.resulting_journals) if plan.resulting_journals is not None else None
            plan = replace(plan, source_format=parsed.source_format, legacy_rows=parsed.rows,
                           resulting_journals=targets)
        except (ValueError, OpenAlexError) as error:
            failed = replace(plan, resulting_journals=None, entries=plan.entries + (
                JournalImportEntry(JournalImportChange.CONFLICT, message=str(error)),))
            return JournalImportApplyResult(draft, failed)
    if not plan.can_apply:
        return JournalImportApplyResult(draft, plan)
    assert plan.resulting_journals is not None
    previous = {j.issn_l for j in draft.journals}
    pending = tuple(j.issn_l for j in plan.resulting_journals
                    if j.issn_l not in previous or j.issn_l in draft.pending_journal_ids)
    return JournalImportApplyResult(replace(draft, journals=plan.resulting_journals, pending_journal_ids=pending), plan)
