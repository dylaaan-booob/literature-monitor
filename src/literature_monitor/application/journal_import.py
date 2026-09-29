"""Pure Journal import parsing, preview, and draft-only Apply under SPEC §34.4."""

from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from enum import Enum
from io import StringIO
from pathlib import Path

from literature_monitor.application.settings import MonitorDraft
from literature_monitor.config import (
    ConfigurationError,
    JournalConfig,
    journal_name_identity,
    journal_whitelist_table,
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


@dataclass(frozen=True)
class JournalImportPlan:
    mode: JournalImportMode
    source_format: JournalImportFormat | None
    imported_journals: tuple[JournalConfig, ...]
    entries: tuple[JournalImportEntry, ...]
    resulting_journals: tuple[JournalConfig, ...] | None

    @property
    def can_apply(self) -> bool:
        return self.resulting_journals is not None


@dataclass(frozen=True)
class JournalImportApplyResult:
    draft: MonitorDraft
    plan: JournalImportPlan

    @property
    def applied(self) -> bool:
        return self.plan.can_apply


def parse_journal_import(contents: str) -> ParsedJournalImport:
    """Recognize only explicit CSV/TSV headers or the supported Journals section."""

    headers = (("Journal", "ISSN/EISSN"), ("Journal", "ISSN/EISSN", "Group"))
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
        return ParsedJournalImport(source_format, tuple(rows), tuple(diagnostics))

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
                source_row, cells[0], cells[1], cells[2] if columns == 3 else None,
            ))
        return ParsedJournalImport(JournalImportFormat.MARKDOWN, tuple(rows), tuple(diagnostics))

    return ParsedJournalImport(None, (), (
        JournalImportEntry(
            JournalImportChange.FORMAT_ERROR,
            message="Expected CSV/TSV header 'Journal, ISSN/EISSN[, Group]' "
            "or a Literature Monitor Markdown Journals table.",
        ),
    ))


def _normalize_import_rows(
    parsed: ParsedJournalImport,
) -> tuple[tuple[JournalConfig, ...], dict[str, tuple[int, ...]], tuple[JournalImportEntry, ...]]:
    journals: dict[str, JournalConfig] = {}
    source_rows: dict[str, tuple[int, ...]] = {}
    owners: dict[str, tuple[str, int]] = {}
    diagnostics = list(parsed.diagnostics)
    for row in parsed.rows:
        try:
            identifiers = tuple(dict.fromkeys(
                normalize_journal_issn(value) for value in row.issn_cell.split("/")
            ))
            journal = validate_journal_configs((JournalConfig(
                name=row.name,
                issn=identifiers,
                group=(row.group.strip() or None) if row.group is not None else None,
            ),))[0]
            validate_journal_storage((journal,))
        except ValueError as error:
            diagnostics.append(JournalImportEntry(
                JournalImportChange.INVALID_ROW,
                journal_name=row.name,
                source_rows=(row.source_row,),
                message=f"Row {row.source_row}: {error}",
            ))
            continue

        key = journal_name_identity(journal.name)
        previous = journals.get(key)
        previous_rows = source_rows.get(key, ())
        if previous is not None:
            if (
                previous.group is not None
                and journal.group is not None
                and previous.group != journal.group
            ):
                diagnostics.append(JournalImportEntry(
                    JournalImportChange.CONFLICT,
                    previous.name,
                    previous_rows + (row.source_row,),
                    previous_group=previous.group,
                    new_group=journal.group,
                    message="Same imported Journal has conflicting non-empty Groups.",
                ))
            journal = JournalConfig(
                name=previous.name,
                issn=tuple(dict.fromkeys(previous.issn + journal.issn)),
                group=previous.group or journal.group,
            )

        for identifier in identifiers:
            owner = owners.get(identifier)
            if owner is not None and owner[0] != key:
                diagnostics.append(JournalImportEntry(
                    JournalImportChange.CONFLICT,
                    journal.name,
                    (owner[1], row.source_row),
                    message=f"ISSN {identifier} is also claimed by {journals[owner[0]].name!r}.",
                ))
            else:
                owners[identifier] = (key, row.source_row)
        journals[key] = journal
        source_rows[key] = previous_rows + (row.source_row,)
    return tuple(journals.values()), source_rows, tuple(diagnostics)


def preview_journal_import(
    draft: MonitorDraft,
    contents: str,
    *,
    mode: JournalImportMode = JournalImportMode.MERGE,
) -> JournalImportPlan:
    """Plan against this draft; changes follow import order, removals draft order."""

    if not isinstance(mode, JournalImportMode):
        raise ValueError("mode must be a JournalImportMode")
    parsed = parse_journal_import(contents)
    imported, source_rows, diagnostics = _normalize_import_rows(parsed)
    entries = list(diagnostics)
    if parsed.source_format is None or any(
        entry.kind is JournalImportChange.FORMAT_ERROR for entry in diagnostics
    ):
        return JournalImportPlan(mode, parsed.source_format, imported, tuple(entries), None)

    # Normalize valid existing ISSNs for comparison. Retain invalid values so the
    # final shared validation rejects them if Merge/Replace would keep them.
    current: list[JournalConfig] = []
    by_name: dict[str, JournalConfig] = {}
    owners: dict[str, set[str]] = {}
    for journal in draft.journals:
        identifiers: list[str] = []
        for raw in journal.issn:
            try:
                identifiers.append(normalize_journal_issn(raw))
            except ValueError:
                identifiers.append(raw)
        normalized = journal.model_copy(update={"issn": tuple(identifiers)})
        key = journal_name_identity(journal.name)
        if key in by_name:
            entries.append(JournalImportEntry(
                JournalImportChange.CONFLICT, journal.name,
                message="Current draft has ambiguous duplicate Journal names.",
            ))
        by_name[key] = normalized
        current.append(normalized)
        for identifier in identifiers:
            owners.setdefault(identifier, set()).add(key)

    resulting = list(current) if mode is JournalImportMode.MERGE else []
    positions = {
        journal_name_identity(journal.name): index for index, journal in enumerate(current)
    }
    imported_keys: set[str] = set()
    for journal in imported:
        key = journal_name_identity(journal.name)
        imported_keys.add(key)
        rows = source_rows[key]
        existing = by_name.get(key)
        for identifier in journal.issn:
            other_owners = owners.get(identifier, set()) - {key}
            if other_owners:
                owner_names = ", ".join(
                    repr(item.name) for item in current
                    if journal_name_identity(item.name) in other_owners
                )
                entries.append(JournalImportEntry(
                    JournalImportChange.CONFLICT, journal.name, rows,
                    message=f"ISSN {identifier} already belongs to current Journal {owner_names}.",
                ))
        if existing is None:
            resulting.append(journal)
            entries.append(JournalImportEntry(
                JournalImportChange.ADD, journal.name, rows,
                added_issns=journal.issn, new_group=journal.group,
            ))
            continue

        added = tuple(identifier for identifier in journal.issn if identifier not in existing.issn)
        removed = tuple(identifier for identifier in existing.issn if identifier not in journal.issn)
        group = journal.group if journal.group is not None else existing.group
        identifiers = existing.issn + added if mode is JournalImportMode.MERGE else journal.issn
        updated = existing.model_copy(update={"issn": identifiers, "group": group})
        if mode is JournalImportMode.MERGE:
            resulting[positions[key]] = updated
        else:
            resulting.append(updated)

        changes: list[JournalImportEntry] = []
        if added:
            changes.append(JournalImportEntry(
                JournalImportChange.ISSN_MERGE, existing.name, rows, added_issns=added,
            ))
        if removed and mode is JournalImportMode.REPLACE:
            changes.append(JournalImportEntry(
                JournalImportChange.ISSN_REMOVE, existing.name, rows, removed_issns=removed,
            ))
        if group != existing.group:
            changes.append(JournalImportEntry(
                JournalImportChange.GROUP_MOVE, existing.name, rows,
                previous_group=existing.group, new_group=group,
            ))
        if mode is JournalImportMode.REPLACE:
            new_position = len(resulting) - 1
            # Positions are zero-based. Report changed retained-ISSN order even
            # when the same row also adds/removes identifiers.
            old_retained = tuple(value for value in existing.issn if value in identifiers)
            new_retained = tuple(value for value in identifiers if value in existing.issn)
            if positions[key] != new_position or old_retained != new_retained:
                changes.append(JournalImportEntry(
                    JournalImportChange.ORDER_CHANGE, existing.name, rows,
                    previous_position=positions[key], new_position=new_position,
                    previous_issns=existing.issn, new_issns=identifiers,
                ))
        entries.extend(changes or [JournalImportEntry(
            JournalImportChange.NO_OP_DUPLICATE, existing.name, rows,
        )])

    if mode is JournalImportMode.REPLACE:
        for journal in current:
            if journal_name_identity(journal.name) not in imported_keys:
                entries.append(JournalImportEntry(
                    JournalImportChange.REMOVE, journal.name,
                    removed_issns=journal.issn, previous_group=journal.group,
                ))

    try:
        validated = validate_journal_configs(resulting)
        validate_journal_storage(validated)
    except ValueError as error:
        entries.append(JournalImportEntry(
            JournalImportChange.CONFLICT,
            message=f"Resulting Journal configuration is invalid: {error}",
        ))
        validated = None
    if any(
        entry.kind in (JournalImportChange.INVALID_ROW, JournalImportChange.CONFLICT)
        for entry in entries
    ):
        validated = None
    return JournalImportPlan(mode, parsed.source_format, imported, tuple(entries), validated)


def apply_journal_import(
    draft: MonitorDraft,
    contents: str,
    *,
    mode: JournalImportMode = JournalImportMode.MERGE,
) -> JournalImportApplyResult:
    """Replan against the current draft and atomically replace only its Journals."""

    plan = preview_journal_import(draft, contents, mode=mode)
    if not plan.can_apply:
        return JournalImportApplyResult(draft, plan)
    assert plan.resulting_journals is not None
    return JournalImportApplyResult(replace(draft, journals=plan.resulting_journals), plan)
