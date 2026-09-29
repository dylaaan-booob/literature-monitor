from __future__ import annotations

import csv
from dataclasses import replace
from io import StringIO
from pathlib import Path
import socket
import subprocess
import sys

import pytest

import literature_monitor.application.settings as settings
from literature_monitor.application.journal_import import (
    JournalImportChange as Change,
    JournalImportFormat,
    JournalImportMode as Mode,
    apply_journal_import,
    parse_journal_import,
    preview_journal_import,
)
from literature_monitor.application.settings import ContentRevision, MonitorDraft
from literature_monitor.config import JournalConfig, LogLevel, validate_journal_configs
from literature_monitor.date_range import DateRangeSpec


def journal(
    name: str = "Biometrics",
    issns: tuple[str, ...] = ("0006-341X",),
    group: str | None = "Biostatistics",
) -> JournalConfig:
    return JournalConfig(name=name, issn=issns, group=group)


def draft(journals: tuple[JournalConfig, ...] | None = None) -> MonitorDraft:
    return MonitorDraft(
        name="Unsaved monitor",
        keyword_expression="causal AND inference",
        journals=journals if journals is not None else (
            journal(),
            journal("Annals of Applied Statistics", ("1932-6157", "1941-7330"), "Methods"),
        ),
        date_spec=DateRangeSpec(window_days=21),
        output_dir=Path("never-created-workspace"),
        log_level=LogLevel.DEBUG,
        monitor_revision=ContentRevision(True, "original-monitor-revision"),
        journal_revision=ContentRevision(True, "original-journal-revision"),
    )


def table(rows: list[tuple[str, ...]], *, grouped: bool = True, format: str = "csv") -> str:
    header = ("Journal", "ISSN/EISSN", "Group") if grouped else ("Journal", "ISSN/EISSN")
    if format == "markdown":
        lines = [
            "# List", "", "## Journals", "",
            "| " + " | ".join(header) + " |",
            "|" + "|".join("---" for _ in header) + "|",
        ]
        lines.extend("| " + " | ".join(row) + " |" for row in rows)
        return "\n".join(lines) + "\n\n## Conferences\n\nIgnored arbitrary conference text.\n"
    buffer = StringIO()
    writer = csv.writer(buffer, delimiter="\t" if format == "tsv" else ",", lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


@pytest.mark.parametrize("format", ["csv", "tsv", "markdown"])
@pytest.mark.parametrize("grouped", [False, True])
def test_supported_formats_parse_and_normalize_equivalent_text(format: str, grouped: bool) -> None:
    rows = [
        (" 统计 Journal ", "1932-6157 / 1941-7330", " 统计 & Methods "),
        ("Biometrics", "0006-341x", ""),
    ]
    if not grouped:
        rows = [row[:2] for row in rows]
    text = table(rows, grouped=grouped, format=format)

    parsed = parse_journal_import(text)
    plan = preview_journal_import(draft(()), text)

    assert parsed.source_format is {
        "csv": JournalImportFormat.CSV,
        "tsv": JournalImportFormat.TSV,
        "markdown": JournalImportFormat.MARKDOWN,
    }[format]
    assert parsed.diagnostics == ()
    assert len(parsed.rows) == 2
    assert [row.source_row for row in parsed.rows] == ([7, 8] if format == "markdown" else [2, 3])
    assert parsed.rows[0].issn_cell == "1932-6157 / 1941-7330"
    assert plan.can_apply
    assert plan.imported_journals == (
        journal("统计 Journal", ("1932-6157", "1941-7330"), "统计 & Methods" if grouped else None),
        journal("Biometrics", ("0006-341X",), None),
    )
    # Pasted input has exactly the same content contract as an uploaded text.
    assert preview_journal_import(draft(()), str(text)) == plan


def test_quoted_csv_fields_preserve_commas_quotes_and_unicode() -> None:
    text = '"Journal","ISSN/EISSN","Group"\n"统计, Journal ""A""",1932-6157,"Methods, ""B"""\n'
    plan = preview_journal_import(draft(()), text)

    assert plan.can_apply
    assert plan.imported_journals == (journal('统计, Journal "A"', ("1932-6157",), 'Methods, "B"'),)


@pytest.mark.parametrize("text", [
    "", "Biometrics 0006-341X", "Name,ISSN\nBiometrics,0006-341X\n",
    "Journal;ISSN/EISSN\nBiometrics;0006-341X\n",
    "journal,ISSN/EISSN\nBiometrics,0006-341X\n",
    "Journal,ISSN/EISSN,Group,Extra\nBiometrics,0006-341X,Stats,extra\n",
    "Journal\tISSN/EISSN\tCategory\nBiometrics\t0006-341X\tStats\n",
    "## Journals\n| Journal | ISSN/EISSN | Category |\n|---|---|---|\n| Biometrics | 0006-341X | Stats |\n",
    "## Journals\n| Journal | ISSN/EISSN | Group | Extra |\n|---|---|---|---|\n| Biometrics | 0006-341X | Stats | Extra |\n",
    "## Journals\n| Journal | ISSN/EISSN | Group |\n|---|---|\n| Biometrics | 0006-341X | Stats |\n",
    "## Journals\n## Journals\n",
])
def test_unknown_or_unsupported_structure_has_format_failure_and_cannot_apply(text: str) -> None:
    original = draft()
    result = apply_journal_import(original, text)

    assert not result.applied
    assert result.draft is original
    assert result.plan.resulting_journals is None
    assert result.plan.entries[0].kind is Change.FORMAT_ERROR
    assert result.plan.entries[0].message


@pytest.mark.parametrize("format", ["csv", "tsv", "markdown"])
@pytest.mark.parametrize("bad_row", [
    ("Bad", "0006-3410", "Stats"),
    ("Bad", "not-an-issn", "Stats"),
    ("Bad", "1932-6157 / ", "Stats"),
    ("", "1932-6157", "Stats"),
    ("Bad", "", "Stats"),
    ("Bad", "1932-6157"),
    ("Bad", "1932-6157", "Stats", "Extra"),
])
def test_invalid_structured_row_blocks_all_valid_rows(format: str, bad_row: tuple[str, ...]) -> None:
    original = draft((journal(),))
    text = table([("New Journal", "1465-4644", "Methods"), bad_row], format=format)
    result = apply_journal_import(original, text)

    assert not result.applied
    assert result.draft is original
    invalid = [entry for entry in result.plan.entries if entry.kind is Change.INVALID_ROW]
    assert len(invalid) == 1
    assert invalid[0].source_rows == ((8,) if format == "markdown" else (3,))
    assert invalid[0].message
    assert any(entry.kind is Change.ADD for entry in result.plan.entries)


@pytest.mark.parametrize("group", ["Stats|Methods", "Stats\nMethods", "Stats\rMethods", "Stats\u2028Methods"])
def test_unsafe_group_is_invalid_even_in_legal_quoted_csv(group: str) -> None:
    result = apply_journal_import(draft(), table([("Biometrics", "0006-341X", group)]))

    assert not result.applied
    assert any(entry.kind is Change.INVALID_ROW and "group" in entry.message for entry in result.plan.entries)


def test_malformed_csv_after_recognized_header_preserves_invalid_row_diagnostic() -> None:
    result = apply_journal_import(draft(), 'Journal,ISSN/EISSN\n"unterminated,0006-341X\n')

    assert not result.applied
    assert result.plan.source_format is JournalImportFormat.CSV
    assert result.plan.entries[0].kind is Change.INVALID_ROW
    assert result.plan.entries[0].source_rows == (2,)
    assert "invalid CSV" in result.plan.entries[0].message


def test_normalization_merges_repeated_logical_journal_rows_in_source_order() -> None:
    text = table([
        ("  BIOMETRICS ", "0006-341x / 0006-341X", ""),
        ("Biostatistics", "1465-4644", "Biostatistics"),
        ("biometrics", "1541-0420 / 0006-341X", "Stats"),
        ("Biometrics", "1541-0420", " Stats "),
    ])
    plan = preview_journal_import(draft(()), text)

    assert plan.can_apply
    assert plan.imported_journals == (
        journal("BIOMETRICS", ("0006-341X", "1541-0420"), "Stats"),
        journal("Biostatistics", ("1465-4644",), "Biostatistics"),
    )
    assert [entry.source_rows for entry in plan.entries] == [(2, 4, 5), (3,)]
    assert preview_journal_import(draft(()), text) == plan


@pytest.mark.parametrize("rows", [
    [("One", "0006-341X", ""), ("Two", "0006-341x", "")],
    [("Biometrics", "0006-341X", "Stats"), ("biometrics", "1541-0420", "Other")],
    [("Biometrics", "0006-341X", "Stats"), ("biometrics", "1541-0420", "stats")],
])
def test_import_identity_or_group_conflict_blocks_whole_apply(rows: list[tuple[str, ...]]) -> None:
    original = draft()
    result = apply_journal_import(original, table(rows))

    assert not result.applied
    assert result.draft is original
    assert any(entry.kind is Change.CONFLICT and entry.source_rows == (2, 3) for entry in result.plan.entries)


@pytest.mark.parametrize("group", ["", " Biostatistics "])
def test_existing_equivalent_name_and_issns_are_no_op_and_never_rename(group: str) -> None:
    original = draft()
    result = apply_journal_import(original, table([(" BIOMETRICS ", "0006-341x", group)]))

    assert result.applied
    assert result.draft.journals == original.journals
    assert len(result.plan.entries) == 1
    entry = result.plan.entries[0]
    assert entry.kind is Change.NO_OP_DUPLICATE
    assert entry.journal_name == "Biometrics"


def test_merge_preserves_current_order_and_appends_new_journals_and_issns() -> None:
    original = draft()
    text = table([
        ("Annals of Applied Statistics", "1941-7330", ""),
        ("Biostatistics", "1465-4644", "Methods"),
        ("BIOMETRICS", "1541-0420 / 0006-341X", "Biostatistics"),
        ("Statistics in Medicine", "0277-6715", "Medical"),
    ])
    result = apply_journal_import(original, text)

    assert result.applied
    assert result.plan.mode is Mode.MERGE
    assert result.draft.journals == (
        journal("Biometrics", ("0006-341X", "1541-0420"), "Biostatistics"),
        original.journals[1],
        journal("Biostatistics", ("1465-4644",), "Methods"),
        journal("Statistics in Medicine", ("0277-6715",), "Medical"),
    )
    assert [entry.kind for entry in result.plan.entries] == [
        Change.NO_OP_DUPLICATE, Change.ADD, Change.ISSN_MERGE, Change.ADD,
    ]
    assert result.plan.entries[2].added_issns == ("1541-0420",)
    assert not any(entry.kind in (Change.REMOVE, Change.ISSN_REMOVE) for entry in result.plan.entries)


def test_merge_can_preview_issn_merge_and_group_move_for_same_journal() -> None:
    result = apply_journal_import(draft(), table([("Biometrics", "1541-0420", "New Group")]))

    assert result.applied
    assert result.draft.journals[0] == journal("Biometrics", ("0006-341X", "1541-0420"), "New Group")
    assert [entry.kind for entry in result.plan.entries] == [Change.ISSN_MERGE, Change.GROUP_MOVE]
    assert result.plan.entries[1].previous_group == "Biostatistics"
    assert result.plan.entries[1].new_group == "New Group"


@pytest.mark.parametrize("mode", [Mode.MERGE, Mode.REPLACE])
@pytest.mark.parametrize("name", ["Another Journal", "Annals of Applied Statistics"])
def test_current_issn_ownership_cannot_be_transferred_even_in_replace(mode: Mode, name: str) -> None:
    original = draft()
    result = apply_journal_import(original, table([
        ("New Valid Journal", "1465-4644", "New"), (name, "0006-341x", ""),
    ]), mode=mode)

    assert not result.applied
    assert result.draft is original
    assert any(entry.kind is Change.CONFLICT and "already belongs" in entry.message for entry in result.plan.entries)


def test_replace_uses_import_order_preserves_existing_names_and_blank_group() -> None:
    original = draft()
    result = apply_journal_import(original, table([
        ("annals of applied statistics", "1932-6157", ""),
        ("BIOMETRICS", "0006-341X / 1541-0420", "New Group"),
        ("Biostatistics", "1465-4644", ""),
    ]), mode=Mode.REPLACE)

    assert result.applied
    assert result.draft.journals == (
        journal("Annals of Applied Statistics", ("1932-6157",), "Methods"),
        journal("Biometrics", ("0006-341X", "1541-0420"), "New Group"),
        journal("Biostatistics", ("1465-4644",), None),
    )
    assert validate_journal_configs(result.draft.journals) == result.draft.journals
    assert [entry.kind for entry in result.plan.entries] == [
        Change.ISSN_REMOVE, Change.ORDER_CHANGE,
        Change.ISSN_MERGE, Change.GROUP_MOVE, Change.ORDER_CHANGE, Change.ADD,
    ]
    assert result.plan.entries[0].journal_name == "Annals of Applied Statistics"
    assert result.plan.entries[0].removed_issns == ("1941-7330",)


def test_replace_previews_removed_current_journals_in_current_order() -> None:
    original = draft()
    result = apply_journal_import(original, table([("New Journal", "1465-4644", "New")]), mode=Mode.REPLACE)

    assert result.applied
    assert result.draft.journals == (journal("New Journal", ("1465-4644",), "New"),)
    removals = [entry for entry in result.plan.entries if entry.kind is Change.REMOVE]
    assert [entry.journal_name for entry in removals] == [item.name for item in original.journals]
    assert [entry.removed_issns for entry in removals] == [item.issn for item in original.journals]
    assert [entry.previous_group for entry in removals] == [item.group for item in original.journals]


@pytest.mark.parametrize("mode", [Mode.MERGE, Mode.REPLACE])
def test_order_only_changes_are_previewed_in_replace_and_ignored_in_merge(mode: Mode) -> None:
    original = draft()
    text = table([
        ("Annals of Applied Statistics", "1941-7330 / 1932-6157", ""),
        ("Biometrics", "0006-341X", ""),
    ])
    result = apply_journal_import(original, text, mode=mode)

    assert result.applied
    if mode is Mode.MERGE:
        assert result.draft.journals == original.journals
        assert [entry.kind for entry in result.plan.entries] == [Change.NO_OP_DUPLICATE] * 2
    else:
        assert result.draft.journals == (
            journal("Annals of Applied Statistics", ("1941-7330", "1932-6157"), "Methods"),
            original.journals[0],
        )
        assert [entry.kind for entry in result.plan.entries] == [Change.ORDER_CHANGE] * 2
        first = result.plan.entries[0]
        assert (first.previous_position, first.new_position) == (1, 0)
        assert first.previous_issns == ("1932-6157", "1941-7330")
        assert first.new_issns == ("1941-7330", "1932-6157")


def test_group_move_without_issn_change_has_only_group_preview() -> None:
    result = apply_journal_import(draft(), table([("Biometrics", "0006-341X", "New")]))

    assert result.applied
    assert [entry.kind for entry in result.plan.entries] == [Change.GROUP_MOVE]
    assert result.draft.journals[0].group == "New"


def test_invalid_final_configuration_is_blocked_by_shared_validation() -> None:
    original = draft((journal("Invalid retained Journal", ("1234-5678",), None),))
    result = apply_journal_import(original, table([("New Journal", "1465-4644", "")]))

    assert not result.applied
    assert result.draft is original
    assert any(entry.kind is Change.CONFLICT and "Resulting Journal configuration" in entry.message for entry in result.plan.entries)


def test_replace_can_repair_invalid_removed_issn_without_bypassing_final_validation() -> None:
    original = draft((journal("Biometrics", ("1234-5678",), "Stats"),))
    result = apply_journal_import(original, table([("Biometrics", "0006-341X", "")]), mode=Mode.REPLACE)

    assert result.applied
    assert result.draft.journals == (journal("Biometrics", ("0006-341X",), "Stats"),)
    assert next(entry for entry in result.plan.entries if entry.kind is Change.ISSN_REMOVE).removed_issns == ("1234-5678",)


def test_empty_replace_cannot_create_invalid_empty_configuration() -> None:
    original = draft()
    result = apply_journal_import(original, "Journal,ISSN/EISSN\n", mode=Mode.REPLACE)

    assert not result.applied
    assert result.draft is original
    assert any(entry.kind is Change.CONFLICT and "no entries" in entry.message for entry in result.plan.entries)


def test_apply_replans_against_current_draft_instead_of_using_stale_preview() -> None:
    original = draft((journal(),))
    text = table([("New Journal", "1465-4644", "Methods")])
    assert preview_journal_import(original, text).can_apply
    current = replace(original, journals=original.journals + (journal("Different owner", ("1465-4644",), None),))

    result = apply_journal_import(current, text)

    assert not result.applied
    assert result.draft is current
    assert result.plan == preview_journal_import(current, text)


def test_successful_apply_changes_only_journals_and_retains_both_revision_objects() -> None:
    original = draft()
    before = replace(original)
    result = apply_journal_import(original, table([("New Journal", "1465-4644", "Methods")]))

    assert result.applied
    assert result.draft is not original
    assert original == before
    assert replace(result.draft, journals=original.journals) == original
    assert result.draft.monitor_revision is original.monitor_revision
    assert result.draft.journal_revision is original.journal_revision


@pytest.mark.parametrize("mode", [Mode.MERGE, Mode.REPLACE])
@pytest.mark.parametrize("grouped", [False, True])
def test_equivalent_formats_have_equivalent_changes_and_result(mode: Mode, grouped: bool) -> None:
    rows = [("BIOMETRICS", "0006-341X / 1541-0420", "New"), ("Biostatistics", "1465-4644", "")]
    if not grouped:
        rows = [row[:2] for row in rows]
    results = [apply_journal_import(draft(), table(rows, grouped=grouped, format=format), mode=mode)
               for format in ("csv", "tsv", "markdown")]

    assert all(result.applied for result in results)
    assert all(result.draft == results[0].draft for result in results)
    assert all(result.plan.imported_journals == results[0].plan.imported_journals for result in results)
    # Source line numbers remain format-specific; the preview changes are equivalent.
    changes = [tuple(replace(entry, source_rows=()) for entry in result.plan.entries) for result in results]
    assert changes[0] == changes[1] == changes[2]


def test_mode_requires_domain_enum() -> None:
    with pytest.raises(ValueError, match="JournalImportMode"):
        preview_journal_import(draft(), table([("Biometrics", "0006-341X", "")]), mode="REPLACE")


def test_parse_preview_apply_have_no_disk_network_or_settings_save_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = {
        tmp_path / "monitor.yaml": "name: Untouched monitor\n",
        tmp_path / "list.md": "Untouched saved journal configuration\n",
        tmp_path / "source.csv": table([("New Journal", "1465-4644", "Methods")]),
    }
    for path, contents in files.items():
        path.write_text(contents, encoding="utf-8")
    original = replace(draft(), output_dir=tmp_path / "workspace")
    text = files[tmp_path / "source.csv"]

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Journal import must have no external effects")

    with monkeypatch.context() as guard:
        guard.setattr("builtins.open", forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(Path, "mkdir", forbidden)
        guard.setattr(socket.socket, "connect", forbidden)
        guard.setattr(settings, "validate_settings", forbidden)
        guard.setattr(settings, "save_settings", forbidden)
        assert parse_journal_import(text).diagnostics == ()
        assert preview_journal_import(original, text).can_apply
        result = apply_journal_import(original, text)
        assert result.applied

    assert sorted(path.name for path in tmp_path.iterdir()) == ["list.md", "monitor.yaml", "source.csv"]
    assert all(path.read_text(encoding="utf-8") == contents for path, contents in files.items())
    assert not original.output_dir.exists()
    assert original.journals == draft().journals


def test_import_core_does_not_load_provider_modules() -> None:
    result = subprocess.run([
        sys.executable, "-c",
        "import sys; import literature_monitor.application.journal_import; "
        "assert not any(name.startswith(('literature_monitor.openalex', "
        "'literature_monitor.crossref', 'literature_monitor.providers')) for name in sys.modules)",
    ], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
