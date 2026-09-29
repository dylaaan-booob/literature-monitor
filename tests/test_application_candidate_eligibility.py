"""Candidate scope decisions and their observable production boundaries."""

from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import httpx
import pytest
import yaml

from literature_monitor.application import monitor
from literature_monitor.application.candidate_eligibility import (
    CandidateEligibility as State,
    classify_crossref, classify_publication, filter_candidate_evidence, target_venue,
)
from literature_monitor.application.crossref_retrieval import (
    CrossrefRetrieval, CrossrefSupplementResult, CrossrefSupplementUnit,
)
from literature_monitor.application.provider_state import (
    CrossrefRecordState, SCHEMA_VERSION as PROVIDER_STATE_SCHEMA_VERSION, read_provider_state,
)
from literature_monitor.diagnostics import RunDiagnosticKind
from literature_monitor.config import JournalConfig
from literature_monitor.canonicalize import canonicalize_records
from literature_monitor.coverage import CoverageComponent, CoverageStatus, CoverageUnit
from literature_monitor.crossref import (
    CrossrefClient, CrossrefDiscoveryResult, CrossrefDiscoveryUnitResult,
    CrossrefWorkRecord, EnrichmentIssue, EnrichmentIssueSeverity,
)
from literature_monitor.models import Author, ExternalIds, MetadataSource, ProviderRecordRef
from literature_monitor.openalex import DiscoveryResult, OpenAlexMetadata, OpenAlexWorkRecord, ResolvedSource, _normalize_work
from literature_monitor.retrieval import assemble_live_provider_evidence

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)
A = JournalConfig(name="Biometrics", issn=("0006-341X",))
B = JournalConfig(name="Annals of Statistics", issn=("0090-5364",))


def source(journal=A, number=1, display_name=None):
    return ResolvedSource(journal.name, journal.issn, journal.issn, (),
                          f"https://openalex.org/S{number}", display_name or journal.name,
                          journal.issn[0], journal.issn)


def oa(doi="10.5555/a", published=None, number=1, source_number=1, title="statistics", journal=A):
    return OpenAlexWorkRecord(
        metadata=OpenAlexMetadata(title=title, journal=journal.name),
        external_ids=ExternalIds(doi=doi, openalex=f"https://openalex.org/W{number}"),
        authors=(Author(name="Ada Author"),), source_id=f"https://openalex.org/S{source_number}",
        provenance=MetadataSource(provider="openalex", record_id=f"https://openalex.org/W{number}", retrieved_at=NOW),
        is_published=published,
    )


def cr(kind="journal-article", issns=A.issn, journal=A.name, doi="10.5555/a", title="statistics"):
    return CrossrefWorkRecord(
        doi=doi, work_type=kind, issns=issns, journal=journal, title=title,
        authors=(Author(name="Ada Author"),), indexed_at=NOW,
        provenance=MetadataSource(provider="crossref", record_id=doi, retrieved_at=NOW),
    )


def ref(record):
    return ProviderRecordRef(provider=record.provenance.provider, record_id=record.provenance.record_id)


def discovery(records=(), journal=A):
    coverage = CoverageUnit("crossref", CoverageComponent.CROSSREF_DISCOVERY,
                            CoverageStatus.COMPLETE, journal=journal.name, issn=journal.issn[0])
    unit = CrossrefDiscoveryUnitResult(journal, journal.issn[0], coverage, tuple(records), ())
    return CrossrefDiscoveryResult(tuple(records), (), (coverage,), (unit,))


def supplementation(*units):
    records = tuple({unit.record.doi: unit.record for unit in units if unit.record is not None}.values())
    return CrossrefSupplementResult(tuple(units), records, (), (), tuple(r.doi for r in records),
                                    tuple(CrossrefRecordState.from_record(r) for r in records))


def supplement(anchor, record=None, status=CoverageStatus.UNAVAILABLE):
    issues = (EnrichmentIssue(EnrichmentIssueSeverity.ERROR, "doi_supplement", "request failed", doi=anchor.external_ids.doi),) if status is CoverageStatus.FAILED else ()
    return CrossrefSupplementUnit(anchor.external_ids.doi, (ref(anchor),), record,
        CoverageUnit("crossref", CoverageComponent.CROSSREF_SUPPLEMENT,
                     CoverageStatus.COMPLETE if record is not None else status, doi=anchor.external_ids.doi), issues)


@pytest.mark.parametrize("kind,issns,name,expected,strong", [
    ("journal-issue", A.issn, A.name, State.INELIGIBLE, False),
    ("journal-issue", (), A.name, State.INELIGIBLE, False),
    ("journal-article", A.issn, "Other", State.ELIGIBLE, True),
    ("journal-article", B.issn, A.name, State.SCOPE_DISPUTED, False),
    ("journal-article", (), "  BIOMETRICS ", State.ELIGIBLE, False),
    ("journal-article", (), "Different", State.SCOPE_DISPUTED, False),
    ("book-chapter", (), A.name, State.INELIGIBLE, False),
    ("proceedings-article", B.issn, A.name, State.INELIGIBLE, False),
    ("book-chapter", A.issn, A.name, State.SCOPE_DISPUTED, False),
    ("other", A.issn, A.name, State.SCOPE_DISPUTED, False),
    (None, A.issn, A.name, State.SCOPE_DISPUTED, False),
])
def test_exact_crossref_type_and_target_venue(kind, issns, name, expected, strong):
    decision = classify_crossref(cr(kind, issns, name), target_venue(A, (source(),)))
    assert decision.state is expected and decision.strong is strong


def test_resolved_source_display_name_is_only_weak_support_without_issns():
    target = target_venue(A, (source(display_name="Biometrics: Journal of Statistics"),))
    decision = classify_crossref(cr(issns=(), journal="Ｂｉｏｍｅｔｒｉｃｓ: Journal of Statistics"), target)
    assert decision.state is State.ELIGIBLE and not decision.strong
    assert classify_crossref(cr(issns=B.issn, journal=target.names[-1]), target).state is State.SCOPE_DISPUTED
    assert classify_crossref(cr("book-chapter", (), target.names[-1]), target).state is State.INELIGIBLE


@pytest.mark.parametrize("status,expected", [
    (CoverageStatus.UNAVAILABLE, (State.ELIGIBLE, State.INELIGIBLE, State.SCOPE_DISPUTED)),
    (CoverageStatus.FAILED, (State.ELIGIBLE, State.SCOPE_DISPUTED, State.SCOPE_DISPUTED)),
    (None, (State.ELIGIBLE, State.INELIGIBLE, State.SCOPE_DISPUTED)),
])
def test_publication_fallbacks_are_three_distinct_cases(status, expected):
    decisions = [classify_publication(value, has_doi=status is not None, supplement_status=status)
                 for value in (True, False, None)]
    assert tuple(decision.state for decision in decisions) == expected
    assert not any(decision.strong for decision in decisions)


@pytest.mark.parametrize("kind", ["paratext", "book-chapter"])
def test_openalex_type_cannot_veto_discovered_exact_crossref(kind):
    resolved = source()
    anchor, warnings = _normalize_work({
        "id": "https://openalex.org/W1", "doi": "10.5555/a", "title": "statistics", "type": kind,
        "primary_location": {"source": {"id": resolved.openalex_id}, "is_published": False},
    }, resolved, NOW, thin=True)
    candidates = filter_candidate_evidence(DiscoveryResult((resolved,), (anchor,), ()),
        discovery((cr(),)), supplementation(), (A,))
    assert not warnings and candidates.openalex_records == (anchor,)
    assert candidates.crossref_records == (cr(),)
    decision, = candidates.attribution[ref(anchor)]
    assert decision.state is State.ELIGIBLE and decision.strong
    assert not candidates.supplement_evidence and not candidates.diagnostics


def test_alias_decisions_use_requested_anchors_and_never_leak_target_venue():
    first = oa(doi="10.5555/alias-a", published=False)
    second = oa(doi="10.5555/alias-b", published=True, number=2, source_number=2, journal=B)
    prime = cr("book-chapter", doi="10.5555/prime")
    supplied = supplementation(supplement(first, prime), supplement(second, prime))
    candidates = filter_candidate_evidence(DiscoveryResult((source(), source(B, 2)), (first, second), ()),
                                           discovery(), supplied, (A, B))
    assert candidates.openalex_records == (first,)
    evidence, = candidates.supplement_evidence
    assert evidence.external_ids.doi == prime.doi and evidence.supplements == (ref(first),)
    assert candidates.attribution[ref(first)][0].state is State.SCOPE_DISPUTED
    diagnostic, = candidates.diagnostics
    assert diagnostic.kind is RunDiagnosticKind.NON_CANDIDATE_EXCLUSION
    assert set(diagnostic.record_ids) == {second.provenance.record_id, prime.provenance.record_id}
    assert diagnostic.journal == B.name
    assert [u.coverage.doi for u in supplied.units] == [first.external_ids.doi, second.external_ids.doi]
    assert supplied.records == (prime,) and supplied.pending_changes[0].doi == prime.doi


@pytest.mark.parametrize("same_target", [True, False])
def test_alias_exclusions_group_current_relations_only_within_the_same_target(same_target):
    first = oa(doi="10.5555/alias-a")
    second = oa(doi="10.5555/alias-b", number=2, source_number=1 if same_target else 2,
                journal=A if same_target else B)
    prime = cr("journal-issue", doi="10.5555/prime")
    supplied = supplementation(supplement(first, prime), supplement(second, prime))
    candidates = filter_candidate_evidence(
        DiscoveryResult((source(), source(B, 2)), (first, second), ()),
        discovery(), supplied, (A, B),
    )
    assert not candidates.openalex_records and not candidates.supplement_evidence
    assert len(candidates.diagnostics) == (1 if same_target else 2)
    assert all(d.kind is RunDiagnosticKind.NON_CANDIDATE_EXCLUSION for d in candidates.diagnostics)
    if same_target:
        diagnostic, = candidates.diagnostics
        assert diagnostic.journal == A.name
        assert set(diagnostic.record_ids) == {first.provenance.record_id, second.provenance.record_id,
                                             prime.provenance.record_id}
    else:
        by_journal = {d.journal: set(d.record_ids) for d in candidates.diagnostics}
        assert by_journal == {
            A.name: {first.provenance.record_id, prime.provenance.record_id},
            B.name: {second.provenance.record_id, prime.provenance.record_id},
        }
    assert candidates.diagnostics == filter_candidate_evidence(
        DiscoveryResult((source(), source(B, 2)), (first, second), ()),
        discovery(), supplied, (A, B),
    ).diagnostics
    assert supplied.records == (prime,) and supplied.pending_changes[0].doi == prime.doi


def test_same_crossref_doi_exclusions_in_different_discovery_journals_stay_separate():
    record = cr("journal-issue", issns=(*A.issn, *B.issn))
    first, second = discovery((record,), A), discovery((record,), B)
    acquired = CrossrefDiscoveryResult((record,), (), (*first.coverage, *second.coverage),
                                       (*first.units, *second.units))
    candidates = filter_candidate_evidence(DiscoveryResult((), (), ()), acquired, supplementation(), (A, B))
    assert not candidates.crossref_records and len(candidates.diagnostics) == 2
    assert {d.journal for d in candidates.diagnostics} == {A.name, B.name}
    assert all(d.record_ids == (record.provenance.record_id,) for d in candidates.diagnostics)


@pytest.mark.parametrize("redirect", [301, 308])
@pytest.mark.parametrize("kind", ["journal-article", "journal-issue"])
def test_real_alias_supplement_retains_requested_coverage_and_prime_identity(redirect, kind):
    anchor = oa(doi="10.5555/alias", published=False)
    requests = []
    def respond(request):
        requests.append(request)
        if request.url.path.endswith("/10.5555/alias"):
            return httpx.Response(redirect, headers={"Location": "https://api.crossref.org/v1/works/10.5555/prime"})
        assert request.url.path == "/v1/works"
        items = [] if "doi:10.5555/alias" in request.url.params["filter"] else [{
            "DOI": "10.5555/prime", "ISSN": list(A.issn), "type": kind,
            "indexed": {"date-time": NOW.isoformat()}, "title": ["statistics"],
            "container-title": [A.name], "author": [{"given": "Ada", "family": "Author"}],
        }]
        return httpx.Response(200, json={"status": "ok", "message-type": "work-list",
                              "message": {"items": items, "total-results": len(items)}})
    with CrossrefClient(transport=httpx.MockTransport(respond), sleep=lambda _: None) as client:
        execution = CrossrefRetrieval(client, retrieved_at=NOW)
        result = execution.supplement((anchor,))
        count = len(requests)
        candidates = filter_candidate_evidence(DiscoveryResult((source(),), (anchor,), ()),
                                               discovery(), result, (A,))
        assert len(requests) == count  # Classification issues no Provider request.
    unit, = result.units
    assert unit.requested_doi == unit.coverage.doi == "10.5555/alias"
    assert unit.record.doi == "10.5555/prime" and unit.anchors == (ref(anchor),)
    if kind == "journal-article":
        assert candidates.openalex_records == (anchor,)
        assert candidates.attribution[ref(anchor)][0].strong
        assert candidates.supplement_evidence[0].supplements == (ref(anchor),)
    else:
        assert not candidates.openalex_records and not candidates.supplement_evidence
        assert candidates.diagnostics[0].kind is RunDiagnosticKind.NON_CANDIDATE_EXCLUSION
    assert execution.pending_changes[0].doi == "10.5555/prime"


def test_discovery_issn_units_count_exclusion_once_per_logical_venue():
    journal = JournalConfig(name=A.name, issn=("0006-341X", "1541-0420"))
    record = cr("journal-issue", issns=journal.issn)
    unit = discovery((record,), journal).units[0]
    acquired = CrossrefDiscoveryResult((record,), (), (), (unit, replace(unit, issn=journal.issn[1])))
    candidates = filter_candidate_evidence(DiscoveryResult((), (), ()), acquired, supplementation(), (journal,))
    assert not candidates.crossref_records and len(candidates.diagnostics) == 1
    assert acquired.records == (record,)


@pytest.fixture
def production(tmp_path, monkeypatch):
    """Real assembly/matching/canonicalization/materialization and SQLite; no network."""
    (tmp_path / "journals.md").write_text("## Journals\n| Journal | ISSN/EISSN |\n|---|---|\n| Biometrics | 0006-341X |\n")
    config = tmp_path / "monitor.yaml"
    config.write_text("venue_whitelist: journals.md\nkeyword_expression: statistics\noutput_dir: workspace\nfrom_date: 2026-01-01\nto_date: 2026-01-31\n")
    case = SimpleNamespace(openalex=DiscoveryResult((source(),), (), ()), crossref=discovery(),
                           supplements=supplementation(), hydrated=(), assembly=None, pending=())
    monkeypatch.setattr(monitor, "OpenAlexClient", lambda **kw: nullcontext(object()))
    monkeypatch.setattr(monitor, "CrossrefClient", lambda **kw: nullcontext(object()))
    monkeypatch.setattr(monitor, "discover_journals_batched", lambda *args, **kw: case.openalex)
    class Retrieval:
        def __init__(self, *args, **kw):
            pass
        @property
        def pending_changes(self):
            return case.pending
        def discover(self, *args):
            return SimpleNamespace(discovery=case.crossref, reused_dois=(), refreshed_dois=(),
                                   new_dois=tuple(r.doi for r in case.crossref.records))
        def supplement(self, records):
            assert records == case.openalex.records
            return case.supplements
    monkeypatch.setattr(monitor, "CrossrefRetrieval", Retrieval)
    def assemble(oa_records, cr_records, supplied, *, monitor_journal_issns=None):
        case.assembly = (tuple(oa_records), tuple(cr_records), tuple(supplied))
        case.monitor_journal_issns = monitor_journal_issns
        return assemble_live_provider_evidence(
            oa_records, cr_records, supplied, monitor_journal_issns=monitor_journal_issns,
        )
    monkeypatch.setattr(monitor, "assemble_live_provider_evidence", assemble)
    def hydrate(client, records, **kw):
        case.hydrated = records
        return SimpleNamespace(records=records, issues=(), reused_work_ids=(),
                               hydrated_work_ids=(), pending_changes=())
    monkeypatch.setattr(monitor, "hydrate_retained_openalex_versions", hydrate)
    case.config = config
    case.output = tmp_path / "workspace"
    return case


@pytest.mark.parametrize("expression", ["statistics", "NOT astronomy"])
@pytest.mark.parametrize("with_anchor", [True, False])
def test_ineligible_is_absent_before_assembly_and_keeps_acquired_state(production, expression, with_anchor):
    case = production
    anchor, record = oa(title=None), cr("journal-issue", title=None)
    case.openalex = replace(case.openalex, records=(anchor,) if with_anchor else ())
    case.crossref = discovery((record,))
    case.pending = (CrossrefRecordState.from_record(record),)
    case.config.write_text(case.config.read_text().replace("keyword_expression: statistics", f"keyword_expression: {expression}"))
    result = monitor.run_monitor(case.config)
    assert case.assembly == ((), (), ()) and not case.hydrated
    assert not result.warnings and not result.errors and result.outcome is monitor.RunOutcome.COMPLETED
    assert result.statistics.openalex_records == int(with_anchor)
    assert result.statistics.crossref_discovery_records == result.state_usage.crossref_new == 1
    assert result.statistics.evidence_clusters == result.canonical_paper_count == 0
    assert result.coverage == case.crossref.coverage
    assert all(d.kind is RunDiagnosticKind.NON_CANDIDATE_EXCLUSION for d in result.diagnostics)
    diagnostic, = result.diagnostics
    assert diagnostic.journal == A.name
    assert set(diagnostic.record_ids) == (
        {record.provenance.record_id, anchor.provenance.record_id} if with_anchor
        else {record.provenance.record_id}
    )
    assert read_provider_state(case.output).state.crossref_records == case.pending
    assert PROVIDER_STATE_SCHEMA_VERSION == 2
    snapshot = json.loads((case.output / ".literature-monitor/last-run.json").read_text())
    assert snapshot["schema_version"] == 2 and "diagnostics" not in snapshot
    assert not list((case.output / "Papers").glob("*.md"))


@pytest.mark.parametrize("matched", [False, True])
def test_dispute_warns_only_after_topic_match_and_keeps_candidate(production, matched):
    case = production
    case.crossref = discovery((cr("other"),))
    if not matched:
        case.config.write_text(case.config.read_text().replace("statistics", "astronomy"))
    result = monitor.run_monitor(case.config)
    assert not result.errors
    assert result.canonical_paper_count == result.created_papers == int(matched)
    assert [w.component for w in result.warnings] == ([monitor.MonitorIssueComponent.CANDIDATE_ELIGIBILITY] if matched else [])
    assert result.outcome is (monitor.RunOutcome.COMPLETED_WITH_WARNINGS if matched else monitor.RunOutcome.COMPLETED)
    assert result.statistics.provider_issues == 0
    if not matched:
        diagnostic, = result.diagnostics
        assert diagnostic.kind is RunDiagnosticKind.SCOPE_DISPUTE
    else:
        paper, = (case.output / "Papers").glob("*.md")
        assert "candidate_eligibility" not in paper.read_text() and "scope_dispute" not in paper.read_text()


def test_excluded_supplement_still_counts_and_persists_acquired_prime_record(production):
    case = production
    anchor = oa(doi="10.5555/alias", published=True, title=None)
    prime = cr("journal-issue", doi="10.5555/prime", title=None)
    case.openalex = replace(case.openalex, records=(anchor,))
    case.supplements = supplementation(supplement(anchor, prime))
    case.pending = case.supplements.pending_changes
    result = monitor.run_monitor(case.config)
    assert case.assembly == ((), (), ()) and not case.hydrated
    assert not result.warnings and not result.errors and result.canonical_paper_count == 0
    assert result.statistics.openalex_records == result.statistics.crossref_supplement_records == 1
    assert result.statistics.evidence_clusters == 0 and result.state_usage.crossref_new == 1
    coverage, = [u for u in result.coverage if u.component is CoverageComponent.CROSSREF_SUPPLEMENT]
    assert coverage.status is CoverageStatus.COMPLETE and coverage.doi == anchor.external_ids.doi
    state = read_provider_state(case.output).state
    assert state.crossref_records == case.pending and state.crossref_records[0].doi == prime.doi
    assert state.crossref_records[0].record.work_type == "journal-issue"


@pytest.mark.parametrize("status", [CoverageStatus.UNAVAILABLE, CoverageStatus.FAILED, None])
@pytest.mark.parametrize("published", [True, False, None])
def test_production_publication_fallbacks_preserve_provider_errors(production, status, published):
    case = production
    anchor = oa(doi=None if status is None else "10.5555/a", published=published)
    case.openalex = replace(case.openalex, records=(anchor,))
    if status is not None:
        case.supplements = supplementation(supplement(anchor, status=status))
    result = monitor.run_monitor(case.config)
    excluded = published is False and status is not CoverageStatus.FAILED
    disputed = published is None or (published is False and status is CoverageStatus.FAILED)
    assert result.canonical_paper_count == result.created_papers == int(not excluded)
    assert bool(case.hydrated) is (not excluded)
    assert bool(result.errors) is (status is CoverageStatus.FAILED)
    assert all(e.component is monitor.MonitorIssueComponent.CROSSREF_SUPPLEMENT for e in result.errors)
    assert bool(result.warnings) is disputed
    assert all(w.component is monitor.MonitorIssueComponent.CANDIDATE_ELIGIBILITY for w in result.warnings)
    assert result.coverage == (*case.crossref.coverage, *case.supplements.coverage)
    expected = (monitor.RunOutcome.COMPLETED_WITH_ERRORS if status is CoverageStatus.FAILED else
                monitor.RunOutcome.COMPLETED_WITH_WARNINGS if disputed else monitor.RunOutcome.COMPLETED)
    assert result.outcome is expected


def test_strong_exact_evidence_suppresses_only_scope_warning(production):
    case = production
    # A DOI-less disputed snapshot joins by the unchanged conservative title/author rule.
    anchor = oa(doi=None, published=None)
    case.openalex = replace(case.openalex, records=(anchor,))
    case.crossref = discovery((cr(),))
    result = monitor.run_monitor(case.config)
    assert result.canonical_paper_count == 1 and not result.warnings and not result.errors
    assert result.outcome is monitor.RunOutcome.COMPLETED
    assert result.statistics.evidence_clusters == 1
    diagnostic, = result.diagnostics
    assert diagnostic.kind is RunDiagnosticKind.SCOPE_DISPUTE
    assert set(diagnostic.record_ids) == {anchor.provenance.record_id, "10.5555/a"}


@pytest.mark.parametrize("published", [True, None])
def test_empty_projection_warns_only_for_genuinely_eligible_clusters(production, published):
    case = production
    anchor = oa(published=published, title=None)
    case.openalex = replace(case.openalex, records=(anchor,))
    case.supplements = supplementation(supplement(anchor))
    case.config.write_text(case.config.read_text().replace("keyword_expression: statistics", "keyword_expression: NOT statistics"))
    result = monitor.run_monitor(case.config)
    assert result.canonical_paper_count == 0 and not case.hydrated and not result.errors
    assert [w.stage for w in result.warnings] == (["unsearchable"] if published else [])
    if published is None:
        diagnostic, = result.diagnostics
        assert diagnostic.kind is RunDiagnosticKind.SCOPE_DISPUTE
        assert result.outcome is monitor.RunOutcome.COMPLETED
    else:
        assert not result.diagnostics


def test_mixed_eligible_disputed_empty_cluster_keeps_both_signals_and_never_matches(production, monkeypatch):
    case = production
    eligible = oa(published=True, title=None)
    disputed = oa(published=None, title=None, number=2)
    case.openalex = replace(case.openalex, records=(eligible, disputed))
    unit = replace(supplement(eligible), anchors=(ref(eligible), ref(disputed)))
    case.supplements = supplementation(unit)
    case.config.write_text(case.config.read_text().replace("keyword_expression: statistics", "keyword_expression: NOT statistics"))
    matched_projections = []
    original_match = monitor.match_searchable_projections
    def match(expression, projections):
        matched_projections.append(projections)
        return original_match(expression, projections)
    monkeypatch.setattr(monitor, "match_searchable_projections", match)
    result = monitor.run_monitor(case.config)
    assert result.statistics.evidence_clusters == 1
    assert result.statistics.retained_clusters == result.canonical_paper_count == 0
    assert matched_projections == [()] and not case.hydrated and not result.errors
    warning, = result.warnings
    diagnostic, = result.diagnostics
    assert warning.stage == "unsearchable" and warning.component is monitor.MonitorIssueComponent.SEARCH
    assert diagnostic.kind is RunDiagnosticKind.SCOPE_DISPUTE
    assert set(warning.record_ids) == set(diagnostic.record_ids) == {
        eligible.provenance.record_id, disputed.provenance.record_id,
    }
    assert result.outcome is monitor.RunOutcome.COMPLETED_WITH_WARNINGS
    assert result.coverage == (*case.crossref.coverage, *case.supplements.coverage)


@pytest.mark.parametrize('separation', ['authors', 'doi', 'missing_authors'])
def test_production_logical_separations_are_diagnostics_only_and_not_doubled(production, separation):
    case = production
    records = tuple(oa(doi=f'10.5555/{i}' if separation == 'doi' else None,
                       published=True, number=i + 1).model_copy(update={
        'authors': (() if separation == 'missing_authors' else
                    (Author(name=f'Author {i}' if separation == 'authors' else 'Ada Author'),))
    }) for i in range(4))
    case.openalex = replace(case.openalex, records=records)
    if separation == 'doi':
        case.supplements = supplementation(*(supplement(r) for r in records))
    if separation == 'missing_authors':
        # Unmatched insufficient author evidence still gets a separation explanation.
        case.config.write_text(case.config.read_text().replace('keyword_expression: statistics',
                                                              'keyword_expression: astronomy'))
    result = monitor.run_monitor(case.config)
    assert result.outcome is monitor.RunOutcome.COMPLETED
    assert not result.warnings and not result.errors
    diagnostic, = result.diagnostics
    assert diagnostic.kind is (RunDiagnosticKind.CONFLICTING_DOI_SEPARATION if separation == 'doi'
                               else RunDiagnosticKind.REPEATED_TITLE_SEPARATION)
    assert diagnostic.record_ids == tuple(sorted(r.provenance.record_id for r in records))
    assert result.statistics.consolidation_issues == result.statistics.canonicalization_issues == 0
    assert result.statistics.evidence_clusters == 4
    assert result.canonical_paper_count == (0 if separation == 'missing_authors' else 4)
    assert result.coverage == (*case.crossref.coverage, *case.supplements.coverage)
    snapshot = json.loads((case.output / '.literature-monitor' / 'last-run.json').read_text())
    assert snapshot['schema_version'] == 2 and 'diagnostics' not in snapshot
    assert PROVIDER_STATE_SCHEMA_VERSION == 2
    assert read_provider_state(case.output).state.crossref_records == ()
    for paper in (case.output / 'Papers').glob('*.md'):
        assert diagnostic.kind.value not in paper.read_text()


def test_production_a2_and_consolidation_diagnostics_share_the_transient_surface(production):
    case = production
    first = oa(doi=None, published=None)
    second = oa(doi=None, published=True, number=2).model_copy(update={'authors': (Author(name='Grace Author'),)})
    excluded = cr('journal-issue')
    case.openalex = replace(case.openalex, records=(first, second))
    case.crossref = discovery((excluded,))
    case.config.write_text(case.config.read_text().replace('keyword_expression: statistics',
                                                          'keyword_expression: astronomy'))
    result = monitor.run_monitor(case.config)
    assert result.outcome is monitor.RunOutcome.COMPLETED and not result.warnings and not result.errors
    assert {d.kind for d in result.diagnostics} == {
        RunDiagnosticKind.NON_CANDIDATE_EXCLUSION, RunDiagnosticKind.SCOPE_DISPUTE,
        RunDiagnosticKind.REPEATED_TITLE_SEPARATION,
    }
    assert len(result.diagnostics) == 3 and result.statistics.consolidation_issues == 0
    assert case.assembly[1] == () and result.coverage == case.crossref.coverage


def test_production_established_snapshot_identifier_conflict_still_warns(production):
    case = production
    anchor = oa(published=True)
    left = anchor.model_copy(update={'external_ids': anchor.external_ids.model_copy(update={'arxiv': '2601.00001'})})
    right = anchor.model_copy(update={'external_ids': anchor.external_ids.model_copy(update={'arxiv': '2601.00002'})})
    case.openalex = replace(case.openalex, records=(left, right))
    case.supplements = supplementation(supplement(anchor))
    result = monitor.run_monitor(case.config)
    assert result.outcome is monitor.RunOutcome.COMPLETED_WITH_WARNINGS and not result.errors
    assert any(w.component is monitor.MonitorIssueComponent.CONSOLIDATION and
               w.stage == 'identifier_conflict' for w in result.warnings)
    assert result.statistics.consolidation_issues > 0 and not result.diagnostics


def test_production_representation_equivalence_preserves_markdown_names_and_abstract(production):
    case = production
    raw_abstract = '<p>We find 3 effects.</p>'
    anchor = oa(published=True).model_copy(update={
        'authors': (Author(name='Ada Lovelace', openalex_id='A1'),),
        'metadata': oa().metadata.model_copy(update={'abstract': raw_abstract, 'author_keywords': ('statistics',)}),
    })
    case.openalex = replace(case.openalex, records=(anchor,))
    case.supplements = supplementation(supplement(anchor))
    first = monitor.run_monitor(case.config)
    assert first.outcome is monitor.RunOutcome.COMPLETED
    paper, = (case.output / 'Papers').glob('*.md')
    original = paper.read_text()
    assert raw_abstract in original and 'Ada Lovelace' in original
    additional = cr().model_copy(update={
        'abstract': 'We find 3 effects.',
        'authors': (Author(name='Lovelace, A.', orcid='O1'),),
    })
    case.crossref = discovery((additional,))
    second = monitor.run_monitor(case.config)
    assert second.outcome is monitor.RunOutcome.COMPLETED
    assert not second.warnings and not second.errors and not second.diagnostics
    assert second.created_papers == 0 and second.matched_existing_papers == 1
    updated = paper.read_text()
    assert raw_abstract in updated and 'Ada Lovelace' in updated
    assert 'Lovelace, A.' not in updated
    author_file, = (case.output / 'Authors').glob('*.md')
    assert 'Ada Lovelace' in author_file.read_text() and 'O1' in author_file.read_text()


@pytest.mark.parametrize("kind", ["journal-article", "other"])
def test_configured_authority_chain_excludes_provider_only_venue_identities(kind):
    journal = JournalConfig(name=A.name, issn=(*A.issn, "1541-0420"))
    resolved = replace(source(journal), issn=B.issn, issn_l=B.issn[0])
    anchor = oa()
    record = cr(kind, issns=(*B.issn, "0162-1459"))
    candidates = filter_candidate_evidence(
        DiscoveryResult((resolved,), (anchor,), ()), discovery((record,), journal),
        supplementation(), (journal,),
    )
    assert B.issn[0] in target_venue(journal, (resolved,)).issns
    expected = tuple(sorted(journal.issn))
    assert candidates.monitor_journal_issns == {ref(anchor): expected, ref(record): expected}
    assembled = assemble_live_provider_evidence(
        candidates.openalex_records, candidates.crossref_records, candidates.supplement_evidence,
        monitor_journal_issns=candidates.monitor_journal_issns,
    )
    assert all(e.monitor_journal_issns == expected for e in assembled)
    paper, = canonicalize_records(assembled).papers
    assert paper.journal_issns == expected and B.issn[0] not in paper.journal_issns
    assert candidates.attribution[ref(record)][0].state is (
        State.ELIGIBLE if kind == "journal-article" else State.SCOPE_DISPUTED
    )


@pytest.mark.parametrize("provider", ["openalex", "crossref"])
def test_generic_disputed_without_current_venue_has_no_attribution(provider):
    anchor, record = oa(source_number=999), cr()
    candidates = filter_candidate_evidence(
        DiscoveryResult((source(),), (anchor,) if provider == "openalex" else (), ()),
        CrossrefDiscoveryResult((record,) if provider == "crossref" else (), (), ()),
        supplementation(), (A,),
    )
    retained = anchor if provider == "openalex" else record
    assert candidates.monitor_journal_issns == {ref(retained): ()}
    assert candidates.attribution[ref(retained)][0].state is State.SCOPE_DISPUTED
    assembled = assemble_live_provider_evidence(
        candidates.openalex_records, candidates.crossref_records, candidates.supplement_evidence,
        monitor_journal_issns=candidates.monitor_journal_issns,
    )
    assert canonicalize_records(assembled).papers[0].journal_issns == ()


@pytest.mark.parametrize("kind,expected", [
    ("journal-article", tuple(sorted((*A.issn, *B.issn)))),
    ("book-chapter", A.issn),
    ("journal-issue", ()),
])
def test_same_record_unions_retained_contexts_and_ignores_excluded_context(kind, expected):
    anchor, record = oa(), cr(kind)
    first, second = discovery((record,), A), discovery((record,), B)
    acquired = CrossrefDiscoveryResult((record,), (), (), (*first.units, *second.units))
    for journals, sources in [((A, B), (source(), source(B))), ((B, A), (source(B), source()))]:
        candidates = filter_candidate_evidence(
            DiscoveryResult(sources, (anchor,), ()), acquired, supplementation(), journals,
        )
        for retained_ref in (ref(anchor), ref(record)):
            assert candidates.monitor_journal_issns.get(retained_ref, ()) == expected
        assert bool(candidates.openalex_records) == bool(expected)
        assert bool(candidates.crossref_records) == bool(expected)


@pytest.mark.parametrize("kind,expected", [
    ("journal-article", tuple(sorted((*A.issn, *B.issn)))),
    ("book-chapter", A.issn),
])
def test_alias_prime_receives_only_retained_anchor_contexts(kind, expected):
    first = oa(doi="10.5555/alias-a")
    second = oa(doi="10.5555/alias-b", number=2, source_number=2, journal=B)
    prime = cr(kind, doi="10.5555/prime")
    supplied = supplementation(supplement(first, prime), supplement(second, prime))
    for anchors in ((first, second), (second, first)):
        candidates = filter_candidate_evidence(
            DiscoveryResult((source(), source(B, 2)), anchors, ()), discovery(), supplied, (A, B),
        )
        assert candidates.monitor_journal_issns[ref(prime)] == expected
        assembled = assemble_live_provider_evidence(
            candidates.openalex_records, candidates.crossref_records, candidates.supplement_evidence,
            monitor_journal_issns=candidates.monitor_journal_issns,
        )
        prime_evidence, = (e for e in assembled if e.provenance.provider == "crossref")
        assert prime_evidence.monitor_journal_issns == expected
        assert set(prime_evidence.supplements) == {ref(r) for r in candidates.openalex_records}
        assert canonicalize_records(assembled).papers[0].journal_issns == expected


@pytest.mark.parametrize("kind", ["journal-article", "other"])
def test_production_passes_configured_attribution_without_changing_scope_signals(production, monkeypatch, kind):
    case = production
    anchor, record = oa(), cr(kind)
    case.openalex = replace(case.openalex, records=(anchor,))
    case.crossref = discovery((record,))
    papers = []
    original = monitor.canonicalize_records
    def canonicalize(records):
        result = original(records)
        papers.extend(result.papers)
        return result
    monkeypatch.setattr(monitor, "canonicalize_records", canonicalize)
    result = monitor.run_monitor(case.config)
    assert case.monitor_journal_issns == {ref(anchor): A.issn, ref(record): A.issn}
    paper, = papers
    assert paper.journal_issns == A.issn
    assert result.canonical_paper_count == 1 and not result.errors
    assert [w.component for w in result.warnings] == (
        [monitor.MonitorIssueComponent.CANDIDATE_ELIGIBILITY] if kind == "other" else []
    )
    assert not result.diagnostics and result.coverage == case.crossref.coverage
    persisted, = (case.output / "Papers").glob("*.md")
    assert yaml.safe_load(persisted.read_text().split("---", 2)[1])["journal_issns"] == list(A.issn)
