"""Pure explicit ISSN-L import and isolated legacy migration boundary (§41.5)."""
from dataclasses import replace
from pathlib import Path
from io import StringIO
import csv
import socket

import pytest

from literature_monitor.application.journal_import import (
    JournalImportChange as Change, JournalImportMode as Mode,
    apply_journal_import, parse_journal_import, preview_journal_import,
)
from literature_monitor.application.settings import ContentRevision, MonitorDraft
from literature_monitor.config import JournalConfig, LogLevel
from literature_monitor.date_range import DateRangeSpec


def journal(name='Biometrics', issn_l='0006-341X', group='Stats', publisher_id='P1'):
    return JournalConfig(name=name, issn_l=issn_l, group=group, publisher_id=publisher_id)


def draft(journals=None):
    return MonitorDraft(name='Unsaved monitor', keyword_expression='statistics',
                        journals=(journal(), journal('Annals', '0090-5364', 'Methods')) if journals is None else journals,
                        date_spec=DateRangeSpec(window_days=21), output_dir=Path('never-created-workspace'),
                        log_level=LogLevel.DEBUG, monitor_revision=ContentRevision(True, 'monitor-original'),
                        journal_revision=ContentRevision(True, 'list-original'))


def table(rows, format='csv', grouped=True, legacy=False):
    header = ('Journal', 'ISSN/EISSN' if legacy else 'ISSN-L') + (('Group',) if grouped else ())
    if format == 'markdown':
        return '\n'.join(['## Journals', '', '| ' + ' | '.join(header) + ' |',
                          '|' + '|'.join('---' for _ in header) + '|'] +
                         ['| ' + ' | '.join(row) + ' |' for row in rows]) + '\n'
    buffer = StringIO();writer = csv.writer(buffer, delimiter='\t' if format == 'tsv' else ',')
    writer.writerow(header);writer.writerows(rows)
    return buffer.getvalue()


@pytest.mark.parametrize('format', ['csv', 'tsv', 'markdown'])
@pytest.mark.parametrize('grouped', [False, True])
def test_explicit_formats_normalize_identity_without_publisher_guess(format, grouped):
    rows = [('Same', '0006-341x', 'Stats'), ('Same', '0090-5364', 'Methods')]
    if not grouped: rows = [r[:2] for r in rows]
    plan = preview_journal_import(draft(()), table(rows, format, grouped))
    assert plan.can_apply and len(plan.imported_journals) == 2
    assert plan.metadata_resolution_required
    assert [j.issn_l for j in plan.imported_journals] == ['0006-341X', '0090-5364']
    assert all(j.publisher_id is None for j in plan.imported_journals)
    assert [j.name for j in plan.imported_journals] == ['Same', 'Same']


@pytest.mark.parametrize('format', ['csv', 'tsv', 'markdown'])
def test_legacy_input_is_recognized_but_cannot_choose_first_issn(format, monkeypatch):
    from literature_monitor.openalex import OpenAlexClient, OpenAlexRequestError
    def unavailable(*args, **kwargs):
        raise OpenAlexRequestError("legacy reconciliation unavailable")
    monkeypatch.setattr(OpenAlexClient, "_request_json", unavailable)
    text = table([('Hint', '1541-0420 / 0006-341X', '')], format, legacy=True)
    parsed = parse_journal_import(text)
    assert parsed.migration_required and parsed.rows[0].issn_cell == '1541-0420 / 0006-341X'
    original = draft();result = apply_journal_import(original, text)
    assert not result.applied and result.draft is original
    assert result.plan.imported_journals == ()
    assert any(e.kind is Change.MIGRATION_REQUIRED for e in result.plan.entries)


@pytest.mark.parametrize('format', ['csv', 'tsv', 'markdown'])
@pytest.mark.parametrize('bad', ['', '0006-3410', '1234-5678', '0006-341X / 1541-0420'])
def test_invalid_identity_blocks_whole_draft_apply(format, bad):
    original = draft()
    result = apply_journal_import(original, table([('New', '1465-4644', ''), ('Bad', bad, '')], format))
    assert not result.applied and result.draft is original
    assert any(e.kind is Change.INVALID_ROW for e in result.plan.entries)
    assert any(e.kind is Change.ADD for e in result.plan.entries)


def test_same_issn_l_conflicting_import_groups_blocks_apply():
    original = draft();text = table([('Same', '0006-341X', 'Stats'), ('Different hint', '0006-341x', 'Other')])
    result = apply_journal_import(original, text)
    assert not result.applied and result.draft is original
    assert any(e.kind is Change.CONFLICT and e.source_rows == (2, 3) for e in result.plan.entries)


def test_equivalent_duplicate_identity_rows_are_deduplicated():
    plan = preview_journal_import(draft(()), table([('Same', '0006-341X', ''), ('Same', '0006-341x', 'Stats')]))
    assert plan.can_apply and plan.imported_journals == (journal('Same', group='Stats', publisher_id=None),)
    assert plan.entries[0].source_rows == (2, 3)


@pytest.mark.parametrize('mode', [Mode.MERGE, Mode.REPLACE])
def test_existing_identity_preserves_canonical_metadata_ignoring_display_hint(mode):
    original = draft();result = apply_journal_import(original, table([('Arbitrary hint', '0006-341X', '')]), mode=mode)
    assert result.applied and result.draft.journals[0] == original.journals[0]
    assert result.plan.entries[0].kind is Change.NO_OP_DUPLICATE
    assert not result.plan.metadata_resolution_required
    assert result.draft.monitor_revision is original.monitor_revision
    assert result.draft.journal_revision is original.journal_revision


def test_same_name_new_identity_adds_distinct_target_without_alias_merge():
    original = draft();result = apply_journal_import(original, table([('Biometrics', '1541-0420', 'New')]))
    assert result.applied and len(result.draft.journals) == 3
    assert result.draft.journals[-1] == journal('Biometrics', '1541-0420', 'New', None)
    assert result.draft.journals[0] == original.journals[0]
    assert result.plan.entries[0].kind is Change.ADD


def test_replace_order_removals_and_group_changes_are_explicit():
    original = draft();result = apply_journal_import(original, table([('Hint', '0090-5364', 'New')]), mode=Mode.REPLACE)
    assert result.applied and result.draft.journals == (replace_identity_group(original.journals[1], 'New'),)
    assert [e.kind for e in result.plan.entries] == [Change.GROUP_MOVE, Change.ORDER_CHANGE, Change.REMOVE]
    assert result.plan.entries[-1].removed_issns == ('0006-341X',)


def replace_identity_group(j, group):
    return j.model_copy(update={'group': group})


@pytest.mark.parametrize('group', ['bad|table', 'bad\nline', 'bad\rline', 'bad\u2028line'])
def test_unsafe_metadata_blocks_import(group):
    result = apply_journal_import(draft(), table([('Biometrics', '0006-341X', group)]))
    assert not result.applied and any(e.kind is Change.INVALID_ROW for e in result.plan.entries)


@pytest.mark.parametrize('text', ['', 'Name,ISSN\nA,0006-341X', 'journal,ISSN-L\nA,0006-341X', 'Journal;ISSN-L\nA;0006-341X'])
def test_unknown_format_is_blocked(text):
    assert not preview_journal_import(draft(), text).can_apply


def test_malformed_csv_and_empty_replace_are_blocked():
    result = apply_journal_import(draft(), 'Journal,ISSN-L\n"unterminated,0006-341X\n')
    assert not result.applied and result.plan.entries[0].kind is Change.INVALID_ROW
    assert not apply_journal_import(draft(), 'Journal,ISSN-L\n', mode=Mode.REPLACE).applied


def test_target_markdown_publisher_cells_are_not_imported():
    text = '## Journals\n| Journal | ISSN-L | Publisher ID | Group |\n|---|---|---|---|\n| Hint | 1465-4644 | P999 | New |\n'
    result = apply_journal_import(draft(), text)
    assert result.applied and result.draft.journals[-1].publisher_id is None


def test_apply_replans_against_current_identity_preserving_current_metadata():
    original = draft();text = table([('Hint', '1465-4644', '')])
    assert preview_journal_import(original, text).entries[0].kind is Change.ADD
    current = replace(original, journals=original.journals + (journal('Canonical', '1465-4644', 'Current', 'P2'),))
    result = apply_journal_import(current, text)
    assert result.applied and result.plan.entries[0].kind is Change.NO_OP_DUPLICATE
    assert result.draft.journals == current.journals


def test_import_is_pure_and_retains_original_revisions(monkeypatch):
    original = draft();text = table([('New', '1465-4644', '')])
    def forbidden(*args, **kwargs): raise AssertionError('external effect')
    with monkeypatch.context() as guard:
        guard.setattr(Path, 'open', forbidden);guard.setattr(Path, 'mkdir', forbidden)
        guard.setattr(socket.socket, 'connect', forbidden)
        result = apply_journal_import(original, text)
    assert result.applied and replace(result.draft, journals=original.journals, pending_journal_ids=()) == original
    assert result.draft.pending_journal_ids == ('1465-4644',)
    assert result.draft.monitor_revision is original.monitor_revision
    assert result.draft.journal_revision is original.journal_revision


def test_import_preview_performs_no_provider_requests(monkeypatch):
    from literature_monitor.openalex import OpenAlexClient
    def forbidden(*args, **kwargs): pytest.fail("Preview must be local")
    monkeypatch.setattr(OpenAlexClient, "_request_json", forbidden)
    assert preview_journal_import(draft(), "Journal,ISSN/EISSN\nHint,1541-0420 / 0006-341X\n").source_format is not None
