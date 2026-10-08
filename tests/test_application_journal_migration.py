from pathlib import Path
from dataclasses import replace

import pytest

from literature_monitor.application.journal_migration import (
    EstablishmentMethod,
    MigrationConfirmation,
    finalize_journal_migration,
    MappingStatus,
    MigrationStatus,
    analyze_journal_migration,
    analyze_workspace_compatibility,
)
from literature_monitor.config import LegacyJournal
from literature_monitor.markdown_state import PaperJournalAttributionState
from literature_monitor.openalex import SourceEvidence, SourceEvidenceResolution, SourceEvidenceStatus
from test_application_workspace import replace_frontmatter, write_paper


BIO = SourceEvidence("https://openalex.org/S1", "Biometrics",
                     ("0006-341X", "1541-0420"), "0006-341X", "https://openalex.org/P1")
CYBER = SourceEvidence("https://openalex.org/S2", "Cybernetics",
                       ("2168-2267", "2168-2275"), "2168-2267")


def journal(name="Legacy name", issns=BIO.aliases, group="Statistics"):
    return LegacyJournal(name=name, issn=issns, group=group)


def resolution(issn, identity=BIO):
    return SourceEvidenceResolution(issn, identity, SourceEvidenceStatus.RESOLVED)


def migration(journals, identities, **kwargs):
    return analyze_journal_migration(journals, tuple(resolution(issn, identity)
                                    for issn, identity in identities.items()), **kwargs)


def paper(workspace, identifiers, ordinal=1):
    path = write_paper(workspace, f"paper-{ordinal}.md", ordinal)
    replace_frontmatter(path, journal_issns=list(identifiers))
    return path


@pytest.mark.parametrize("issns", [(BIO.provider_issn_l,), BIO.aliases])
def test_legacy_identifiers_collapse_only_with_complete_consistent_identity(issns):
    result = migration((journal(issns=issns),), dict.fromkeys(issns, BIO))
    assert all(row.status is MigrationStatus.ESTABLISHED for row in result.rows)
    row, = result.rows
    assert row.source == BIO
    assert row.legacy.name == "Legacy name"  # presentation is retained, never consulted
    assert row.legacy.group == "Statistics"
    assert row.proposed_issn_l in row.legacy.issn
    assert row.establishment_method is EstablishmentMethod.AUTOMATIC_EXISTING_IDENTIFIER
    assert not result.safe  # complete Paper proof is a separate gate
    assert len(row.resolutions) == len(issns)


def test_conflicting_legacy_sources_are_not_reconciled_by_name():
    result = migration((journal(),), {"0006-341X": BIO, "1541-0420": CYBER})
    assert not result.safe
    assert result.rows[0].status is MigrationStatus.CONFLICT
    assert result.rows[0].source is None


def test_duplicate_display_names_with_distinct_target_identities_are_valid():
    result = migration((journal(issns=(BIO.provider_issn_l,)), journal(issns=(CYBER.provider_issn_l,))),
                       {BIO.provider_issn_l: BIO, CYBER.provider_issn_l: CYBER})
    assert all(row.status is MigrationStatus.ESTABLISHED for row in result.rows)
    assert result.rows[0].source != result.rows[1].source


def test_target_identity_collision_across_rows_and_groups_requires_treatment():
    result = migration((journal(issns=(BIO.provider_issn_l,)),
                        journal("Another row", ("1541-0420",), "Different Group")),
                       dict.fromkeys(BIO.aliases, BIO), confirmations=(MigrationConfirmation(2, BIO.provider_issn_l, "reviewed evidence"),))
    assert not result.safe
    assert all(row.status is MigrationStatus.CONFLICT for row in result.rows)
    assert all("collides" in row.diagnostics[-1] for row in result.rows)


def test_canonical_absent_from_old_set_is_unsafe_despite_verified_alias():
    result = migration((journal(issns=("1541-0420",)),), {"1541-0420": BIO})
    row, = result.rows
    assert row.source == BIO
    assert row.proposed_issn_l is None
    assert row.status is MigrationStatus.CONFIRMATION_REQUIRED
    assert not result.safe


@pytest.mark.parametrize("status", [SourceEvidenceStatus.NOT_FOUND, SourceEvidenceStatus.REQUEST_FAILED,
                                    SourceEvidenceStatus.INVALID_SOURCE, SourceEvidenceStatus.AMBIGUOUS_SOURCE])
def test_incomplete_legacy_evidence_never_permits_migration(status):
    result = analyze_journal_migration((journal(),), (
        resolution("0006-341X"), SourceEvidenceResolution("1541-0420", None, status, "failure"),
    ))
    assert not result.safe
    assert result.rows[0].status is (MigrationStatus.CONFLICT if status is SourceEvidenceStatus.AMBIGUOUS_SOURCE else MigrationStatus.UNRESOLVED)
    assert status.value in result.rows[0].diagnostics[0]
    assert len(result.rows[0].resolutions) == 2  # successful evidence survives


def test_missing_or_duplicated_resolution_evidence_is_not_accepted():
    for evidence in [(), (resolution(BIO.provider_issn_l), resolution(BIO.provider_issn_l))]:
        result = analyze_journal_migration((journal(issns=(BIO.provider_issn_l,)),), evidence)
        assert result.rows[0].status is MigrationStatus.UNRESOLVED


@pytest.mark.parametrize("identifiers", [(BIO.provider_issn_l,), BIO.aliases])
def test_canonical_and_multi_issn_papers_keep_same_journal_and_group(tmp_path, identifiers):
    path = paper(tmp_path, identifiers)
    before = path.read_bytes()
    result = migration((journal(),), dict.fromkeys(BIO.aliases, BIO))
    proof = analyze_workspace_compatibility(tmp_path, result)
    assert proof.safe
    record, = proof.papers
    assert record.attribution_state is PaperJournalAttributionState.VALID
    assert record.current == record.proposed
    assert record.proposed.status is MappingStatus.UNIQUE
    assert record.proposed.journal_rows == (1,)
    assert record.proposed.group == "Statistics"
    assert record.changed is False
    assert path.read_bytes() == before


def test_alias_only_paper_is_detected_as_becoming_unmapped_without_repair(tmp_path):
    path = paper(tmp_path, ("1541-0420",))
    before = path.read_bytes()
    result = migration((journal(),), dict.fromkeys(BIO.aliases, BIO))
    proof = analyze_workspace_compatibility(tmp_path, result)
    record, = proof.papers
    assert record.current.status is MappingStatus.UNIQUE
    assert record.proposed.status is MappingStatus.UNMAPPED
    assert record.changed is True
    assert not proof.safe
    assert path.read_bytes() == before


def test_ambiguous_and_unmapped_papers_are_reported_conservatively(tmp_path):
    paper(tmp_path, (BIO.provider_issn_l, CYBER.provider_issn_l))
    paper(tmp_path, ("0090-5364",), 2)
    result = migration((journal(), journal("Cyber", CYBER.aliases)),
                       {**dict.fromkeys(BIO.aliases, BIO), **dict.fromkeys(CYBER.aliases, CYBER)})
    proof = analyze_workspace_compatibility(tmp_path, result)
    assert [record.current.status for record in proof.papers] == [MappingStatus.AMBIGUOUS, MappingStatus.UNMAPPED]
    assert all(record.changed is True for record in proof.papers)
    assert not proof.safe


@pytest.mark.parametrize("attribution", [None, [], "malformed", ["bad"]])
def test_missing_empty_malformed_attribution_is_separate_without_name_inference(tmp_path, attribution):
    path = write_paper(tmp_path, "paper.md", 1)
    if attribution is not None:
        replace_frontmatter(path, journal_issns=attribution)
    before = path.read_bytes()
    proof = analyze_workspace_compatibility(tmp_path, migration((journal(),), dict.fromkeys(BIO.aliases, BIO)))
    record, = proof.papers
    assert record.attribution_state is not PaperJournalAttributionState.VALID
    assert record.changed is None and record.current is None and record.proposed is None
    assert path.read_bytes() == before


def test_unresolved_target_identity_keeps_workspace_proof_unproven(tmp_path):
    paper(tmp_path, (BIO.provider_issn_l,))
    result = analyze_journal_migration((journal(),), (resolution(BIO.provider_issn_l),))
    proof = analyze_workspace_compatibility(tmp_path, result)
    assert proof.papers[0].proposed.status is MappingStatus.UNPROVEN
    assert proof.papers[0].changed is None
    assert not proof.safe


def test_missing_workspace_and_symlink_paper_are_unproven(tmp_path):
    proof = analyze_workspace_compatibility(tmp_path, migration((journal(),), dict.fromkeys(BIO.aliases, BIO)))
    assert proof.safe and not proof.papers and not proof.diagnostics
    path = paper(tmp_path, (BIO.provider_issn_l,))
    (path.parent / "link.md").symlink_to(path)
    proof = analyze_workspace_compatibility(tmp_path, migration((journal(),), dict.fromkeys(BIO.aliases, BIO)))
    assert not proof.safe
    assert any("non-symlink" in str(record.diagnostics) for record in proof.papers)


def test_changed_journal_and_group_and_new_ambiguity_are_detected(tmp_path):
    paper(tmp_path, (BIO.provider_issn_l,))
    journals = (journal(issns=(BIO.provider_issn_l,)), journal("Other", (CYBER.provider_issn_l,), "Engineering"))
    swap = migration(journals, {BIO.provider_issn_l: CYBER, CYBER.provider_issn_l: BIO},
                     confirmations=(MigrationConfirmation(1, CYBER.provider_issn_l, "reviewed"),
                                    MigrationConfirmation(2, BIO.provider_issn_l, "reviewed")))
    record, = analyze_workspace_compatibility(tmp_path, swap).papers
    assert record.changed is True
    assert record.current.journal_rows == (1,) and record.proposed.journal_rows == (2,)
    assert record.current.group == "Statistics" and record.proposed.group == "Engineering"
    collision = migration((journal(issns=(BIO.provider_issn_l,)), journal("Other", ("1541-0420",))),
                          dict.fromkeys(BIO.aliases, BIO), confirmations=(MigrationConfirmation(2, BIO.provider_issn_l, "reviewed"),))
    record, = analyze_workspace_compatibility(tmp_path, collision).papers
    assert record.changed is True and record.proposed.status is MappingStatus.AMBIGUOUS


def test_invalid_paper_state_cannot_be_a_successful_mapping_proof(tmp_path):
    path = paper(tmp_path, (BIO.provider_issn_l,))
    replace_frontmatter(path, id="invalid")
    before = path.read_bytes()
    proof = analyze_workspace_compatibility(tmp_path, migration((journal(),), dict.fromkeys(BIO.aliases, BIO)))
    assert not proof.safe
    assert proof.papers[0].changed is None and proof.papers[0].diagnostics
    assert path.read_bytes() == before


def test_independent_confirmation_selects_membership_not_provider_candidate(tmp_path):
    path = paper(tmp_path, ("1541-0420",))
    before = path.read_bytes()
    confirmed = MigrationConfirmation(1, "1541-0420", "independent authoritative review")
    result = migration((journal(),), dict.fromkeys(BIO.aliases, BIO), confirmations=(confirmed,))
    row, = result.rows
    assert row.proposed_issn_l == "1541-0420"
    assert row.source.provider_issn_l == "0006-341X"
    assert row.establishment_method is EstablishmentMethod.INDEPENDENT_CONFIRMATION
    assert any("differs" in message for message in row.diagnostics)
    proof = analyze_workspace_compatibility(tmp_path, result)
    assert proof.safe
    assert finalize_journal_migration(result, proof).safe
    assert path.read_bytes() == before


@pytest.mark.parametrize("value", ["bad", "1526-5489", "0006-3410"])
def test_invalid_confirmation_is_rejected(value):
    with pytest.raises(ValueError):
        MigrationConfirmation(1, value, "reviewed")


def test_confirmation_outside_source_membership_is_conflict():
    result = migration((journal(),), dict.fromkeys(BIO.aliases, BIO),
                       confirmations=(MigrationConfirmation(1, CYBER.provider_issn_l, "reviewed"),))
    assert result.rows[0].status is MigrationStatus.CONFLICT
    assert result.rows[0].proposed_issn_l is None


def test_confirmed_target_collision_ignores_different_provider_candidates():
    other = replace(BIO, provider_issn_l="1541-0420")
    result = migration((journal(issns=("0006-341X",)), journal(issns=("1541-0420",))),
                       {"0006-341X": BIO, "1541-0420": other}, confirmations=(
                           MigrationConfirmation(1, "0006-341X", "reviewed A"),
                           MigrationConfirmation(2, "0006-341X", "reviewed B")))
    assert all(row.target_collision and row.status is MigrationStatus.CONFLICT for row in result.rows)


def test_same_display_names_with_distinct_confirmed_targets_are_valid():
    result = migration((journal(issns=("0006-341X",)), journal(issns=("2168-2267",))),
                       {"0006-341X": BIO, "2168-2267": CYBER}, confirmations=(
                           MigrationConfirmation(1, "1541-0420", "reviewed A"),
                           MigrationConfirmation(2, "2168-2275", "reviewed B")))
    assert all(row.status is MigrationStatus.ESTABLISHED for row in result.rows)
    assert all(not row.target_collision for row in result.rows)


def test_same_source_candidate_disagreement_requires_confirmation_not_false_source_conflict():
    other = replace(BIO, provider_issn_l="1541-0420")
    result = migration((journal(),), {"0006-341X": BIO, "1541-0420": other})
    assert result.rows[0].status is MigrationStatus.CONFIRMATION_REQUIRED
    assert result.rows[0].source.provider_issn_l is None
    result = migration((journal(),), {"0006-341X": BIO, "1541-0420": other},
                       confirmations=(MigrationConfirmation(1, "0006-341X", "reviewed"),))
    assert result.rows[0].status is MigrationStatus.ESTABLISHED
    assert any("disagreement" in message for message in result.rows[0].diagnostics)


def test_paper_compatibility_failure_cannot_finalize_identity_as_safe(tmp_path):
    paper(tmp_path, ("1541-0420",))
    result = migration((journal(),), dict.fromkeys(BIO.aliases, BIO))
    proof = analyze_workspace_compatibility(tmp_path, result)
    final = finalize_journal_migration(result, proof)
    assert not final.safe
    assert final.rows[0].status is MigrationStatus.CONFLICT


def test_group_only_change_is_detected(tmp_path):
    paper(tmp_path, ("0006-341X",))
    result = migration((journal(),), dict.fromkeys(BIO.aliases, BIO))
    changed = replace(result, rows=(replace(result.rows[0], proposed_group="Different"),))
    record, = analyze_workspace_compatibility(tmp_path, changed).papers
    assert record.current.journal_rows == record.proposed.journal_rows
    assert record.current.group != record.proposed.group and record.changed


def test_automatic_target_becomes_safe_only_after_unchanged_paper_proof(tmp_path):
    paper(tmp_path, BIO.aliases)
    result = migration((journal(),), dict.fromkeys(BIO.aliases, BIO))
    assert not result.safe
    final = finalize_journal_migration(result, analyze_workspace_compatibility(tmp_path, result))
    assert final.safe
    assert final.rows[0].establishment_method is EstablishmentMethod.AUTOMATIC_EXISTING_IDENTIFIER


def test_paper_proof_cannot_finalize_a_different_target_set(tmp_path):
    paper(tmp_path, BIO.aliases)
    result = migration((journal(),), dict.fromkeys(BIO.aliases, BIO))
    proof = analyze_workspace_compatibility(tmp_path, result)
    different = replace(result, rows=(replace(result.rows[0], proposed_issn_l="1541-0420"),))
    with pytest.raises(ValueError, match="different migration targets"):
        finalize_journal_migration(different, proof)
