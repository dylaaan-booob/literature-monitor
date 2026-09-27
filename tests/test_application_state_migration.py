"""A6 production cutover with live MockTransport evidence and real SQLite state."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import sqlite3
import threading
from uuid import UUID
from dataclasses import is_dataclass

import httpx
import pytest

from literature_monitor.application import monitor, provider_state as ps
from literature_monitor.cli import main
from literature_monitor.coverage import CoverageComponent, CoverageStatus
from literature_monitor.crossref import CrossrefClient, normalize_crossref_discovered_work
from literature_monitor.openalex import OpenAlexClient
from literature_monitor.search import SearchBackendError, SearchExpressionError

REV = "2026-02-01T00:00:00Z"
NEW = "2026-02-02T00:00:00Z"
NOW = datetime(2026, 2, 1, tzinfo=timezone.utc)
ISSN = "0006-341X"


def cr_record(doi="10.5555/a", revision=REV, title="statistics study"):
    return {"DOI": doi, "ISSN": [ISSN], "indexed": {"date-time": revision},
            "title": [title], "container-title": ["Biometrics"],
            "author": [{"given": "Ada", "family": "Author"}],
            "published": {"date-parts": [[2026, 1, 1]]}}


def cr_state(doi="10.5555/a", revision=REV, title="statistics study"):
    record, _ = normalize_crossref_discovered_work(cr_record(doi, revision, title), NOW)
    return ps.CrossrefRecordState.from_record(record)


def oa_work(number=1, doi="10.5555/a", title="statistics study", revision=REV):
    return {"id": f"https://openalex.org/W{number}", "doi": f"https://doi.org/{doi}",
            "title": title, "publication_date": "2026-01-01", "updated_date": revision,
            "abstract_inverted_index": None,
            "authorships": [{"author": {"display_name": "Ada Author"}}],
            "primary_location": {"source": {"id": "https://openalex.org/S1", "display_name": "Biometrics"}}}


def work_list(items):
    return {"status": "ok", "message-type": "work-list", "message": {"items": items, "total-results": len(items), "next-cursor": "unused"}}


class Transport(httpx.MockTransport):
    def __init__(self, route):
        self.requests = []
        self.closed = False
        def respond(request):
            assert not self.closed, "request after pooled client closed"
            self.requests.append(request)
            result = route(request)
            return result if isinstance(result, httpx.Response) else httpx.Response(200, json=result)
        super().__init__(respond)

    def close(self):
        self.closed = True
        super().close()


class Execution:
    def __init__(self, tmp_path, monkeypatch):
        self.config = tmp_path / "monitor.yaml"
        (tmp_path / "list.md").write_text("## Journals\n\n| Journal | ISSN/EISSN |\n|---|---|\n| Biometrics | 0006-341X |\n")
        self.config.write_text("venue_whitelist: list.md\nkeyword_expression: statistics\noutput_dir: workspace\nfrom_date: 2026-01-01\nto_date: 2026-01-31\n")
        self.output = tmp_path / "workspace"
        self.path = self.output / ".literature-monitor" / ps.STATE_FILENAME
        self.works = [oa_work()]
        self.records = {"10.5555/a": cr_record()}
        self.members = ["10.5555/a"]
        self.failed_dois = set()
        self.fail_versions = False
        self.aliases = {}
        self.locations = {}
        self.oa_transports, self.cr_transports = [], []
        def oa_factory(**kwargs):
            transport = Transport(self.route_oa)
            self.oa_transports.append(transport)
            return OpenAlexClient(transport=transport, sleep=lambda _: None, **kwargs)
        def cr_factory(**kwargs):
            transport = Transport(self.route_cr)
            self.cr_transports.append(transport)
            return CrossrefClient(transport=transport, sleep=lambda _: None, **kwargs)
        monkeypatch.setattr(monitor, "OpenAlexClient", oa_factory)
        monkeypatch.setattr(monitor, "CrossrefClient", cr_factory)

    def route_oa(self, request):
        if request.url.path == "/sources":
            return {"meta": {"count": 1}, "results": [{"id": "https://openalex.org/S1",
                    "display_name": "Biometrics", "issn_l": ISSN, "issn": [ISSN], "type": "journal"}]}
        assert request.url.path == "/works"
        if request.url.params["select"] == "id,locations":
            if self.fail_versions:
                return httpx.Response(403)
            requested = request.url.params["filter"].split(":", 1)[1].split("|")
            return {"results": [{"id": f"https://openalex.org/{work}",
                    "locations": self.locations.get(work, [{"id": "doi:10.5555/version",
                        "version": "acceptedVersion", "landing_page_url": "https://doi.org/10.5555/version"}])}
                    for work in requested]}
        assert "locations" not in request.url.params["select"]
        assert request.url.params["filter"].startswith("primary_location.source.id:S1,")
        return {"meta": {"count": len(self.works), "next_cursor": None}, "results": deepcopy(self.works)}

    def route_cr(self, request):
        if request.url.path != "/v1/works":
            doi = request.url.path.removeprefix("/v1/works/")
            if doi in self.aliases:
                return httpx.Response(301, headers={"Location": f"https://api.crossref.org/v1/works/{self.aliases[doi]}"})
            return httpx.Response(404)
        filters = request.url.params["filter"].split(",")
        if any(value.startswith("issn:") for value in filters):
            return work_list([self.records[doi] for doi in self.members])
        dois = [value.removeprefix("doi:") for value in filters]
        if "select" not in request.url.params and set(dois) <= self.failed_dois:
            return httpx.Response(403)
        return work_list([self.records[doi] for doi in dois if doi in self.records
                          and ("select" in request.url.params or doi not in self.failed_dois)])

    @property
    def version_requests(self):
        return [r for r in self.oa_transports[-1].requests if r.url.params.get("select") == "id,locations"]

    @property
    def full_requests(self):
        return [r for r in self.cr_transports[-1].requests if "select" not in r.url.params]

    def run(self):
        return monitor.run_monitor(self.config)


@pytest.fixture
def execution(tmp_path, monkeypatch):
    return Execution(tmp_path, monkeypatch)


def state_warnings(result):
    return [issue for issue in result.warnings if issue.component is monitor.MonitorIssueComponent.PROVIDER_STATE]


@pytest.mark.parametrize("raw_doi", [None, 42])
@pytest.mark.parametrize("diagnostic", [False, True])
def test_title_only_has_no_supplement_warning_requests_or_state(execution, monkeypatch, raw_doi, diagnostic):
    e = execution
    e.works[0]["doi"] = raw_doi
    e.members = []
    e.locations["W1"] = []
    if diagnostic:
        def forbidden(*args, **kwargs):
            pytest.fail("diagnostics must not read or write Provider state")
        for name in ("read_provider_state", "update_provider_state", "replace_invalid_provider_state"):
            monkeypatch.setattr(monitor, name, forbidden)
        result = monitor._run_canonical_core(e.config)
        assert len(result.papers) == 1
        assert result.pending_state.crossref_records == ()
        assert not e.path.exists()
    else:
        result = e.run()
        assert result.canonical_paper_count == 1
        assert len(list((e.output / "Papers").glob("*.md"))) == 1
        assert ps.read_provider_state(e.output).state.crossref_records == ()

    assert result.outcome is monitor.RunOutcome.COMPLETED
    assert not result.errors
    assert not result.warnings
    assert result.statistics.provider_issues == 0
    assert result.statistics.openalex_records == result.statistics.retained_clusters == 1
    assert result.statistics.crossref_supplement_records == 0
    assert not any(unit.component is CoverageComponent.CROSSREF_SUPPLEMENT for unit in result.coverage)
    request, = e.cr_transports[-1].requests
    assert request.url.path == "/v1/works"
    assert request.url.params["select"] == "DOI,ISSN,indexed"
    assert request.url.params["filter"].startswith(f"issn:{ISSN},")
    assert not e.full_requests


def test_openalex_only_doi_still_supplements_and_persists_current_evidence(execution):
    e = execution
    e.members = []
    result = e.run()
    assert result.outcome is monitor.RunOutcome.COMPLETED
    assert not result.warnings and not result.errors
    assert result.statistics.provider_issues == 0
    assert result.canonical_paper_count == result.statistics.crossref_supplement_records == 1
    unit, = [item for item in result.coverage if item.component is CoverageComponent.CROSSREF_SUPPLEMENT]
    assert unit.status is CoverageStatus.COMPLETE
    assert len(e.full_requests) == 1
    assert [row.doi for row in ps.read_provider_state(e.output).state.crossref_records] == ["10.5555/a"]


@pytest.fixture
def production_boundaries(monkeypatch):
    """Observe the real consolidation, matcher, hydration and canonicalization calls."""
    seen = {}
    original_consolidate = monitor.consolidate_evidence
    original_match = monitor.match_searchable_projections
    original_hydrate = monitor.hydrate_retained_openalex_versions
    original_canonicalize = monitor.canonicalize_records

    def consolidate(evidence):
        result = original_consolidate(evidence)
        seen["clusters"] = result.clusters
        return result

    def match(expression, projections):
        result = original_match(expression, projections)
        seen["projections"], seen["matches"] = projections, result
        return result

    def hydrate(client, records, **kwargs):
        seen["hydration_records"] = records
        return original_hydrate(client, records, **kwargs)

    def canonicalize(evidence):
        seen["canonical_evidence"] = evidence
        return original_canonicalize(evidence)

    monkeypatch.setattr(monitor, "consolidate_evidence", consolidate)
    monkeypatch.setattr(monitor, "match_searchable_projections", match)
    monkeypatch.setattr(monitor, "hydrate_retained_openalex_versions", hydrate)
    monkeypatch.setattr(monitor, "canonicalize_records", canonicalize)
    return seen


@pytest.mark.parametrize(("expression", "matched"), [("statistics", True), ("astronomy", False)])
def test_title_only_without_authors_warns_only_after_matching(execution, production_boundaries, expression, matched):
    e, seen = execution, production_boundaries
    e.members = []
    e.works[0].update(doi=None, authorships=None)
    e.locations["W1"] = []
    e.config.write_text(e.config.read_text().replace("keyword_expression: statistics",
                                                  f"keyword_expression: {expression}"))
    result = e.run()

    evidence, = seen["clusters"][0].evidence
    assert evidence.title == "statistics study" and not evidence.authors and evidence.external_ids.doi is None
    assert seen["projections"][0].titles == ("statistics study",)
    assert seen["matches"] == (matched,)
    assert bool(seen["canonical_evidence"]) is matched
    assert bool(seen["hydration_records"]) is matched
    assert bool(e.version_requests) is matched
    assert result.canonical_paper_count == 0
    assert not result.errors
    assert [issue.stage for issue in result.warnings] == (["insufficient_metadata"] if matched else [])
    assert result.outcome is (monitor.RunOutcome.COMPLETED_WITH_WARNINGS if matched else monitor.RunOutcome.COMPLETED)
    assert result.statistics.evidence_clusters == result.statistics.openalex_records == 1
    assert result.statistics.retained_clusters == result.statistics.canonicalization_issues == int(matched)
    assert result.statistics.provider_issues == result.statistics.crossref_supplement_records == 0
    assert not any(unit.component is CoverageComponent.CROSSREF_SUPPLEMENT for unit in result.coverage)
    assert len(e.cr_transports[-1].requests) == 1 and not e.full_requests


def test_doi_only_crossref_success_becomes_searchable_and_canonical(execution, production_boundaries):
    e, seen = execution, production_boundaries
    e.members = []
    e.works[0].update(title=None, authorships=None)
    e.locations["W1"] = []
    result = e.run()

    cluster, = seen["clusters"]
    oa, = [record for record in cluster.evidence if record.provenance.provider == "openalex"]
    cr, = [record for record in cluster.evidence if record.provenance.provider == "crossref"]
    assert oa.title is None and not oa.authors
    assert oa.external_ids.doi == cr.external_ids.doi == "10.5555/a"
    assert [(ref.provider, ref.record_id) for ref in cr.supplements] == [("openalex", oa.provenance.record_id)]
    assert seen["projections"][0].titles == ("statistics study",)
    assert seen["matches"] == (True,)
    assert {record.provenance.provider for record in seen["canonical_evidence"]} == {"openalex", "crossref"}
    assert [record.provenance.record_id for record in seen["hydration_records"]] == [oa.provenance.record_id]
    assert len(e.full_requests) == len(e.version_requests) == 1
    assert result.outcome is monitor.RunOutcome.COMPLETED
    assert not result.warnings and not result.errors
    assert result.statistics.provider_issues == result.statistics.canonicalization_issues == 0
    assert result.statistics.evidence_clusters == result.statistics.retained_clusters == 1
    assert result.canonical_paper_count == result.statistics.crossref_supplement_records == 1
    supplement, = [unit for unit in result.coverage if unit.component is CoverageComponent.CROSSREF_SUPPLEMENT]
    assert supplement.status is CoverageStatus.COMPLETE
    paper, = (e.output / "Papers").glob("*.md")
    assert "statistics study" in paper.read_text() and "Ada Author" in paper.read_text()


@pytest.mark.parametrize("failure", ["unavailable", "remote"])
@pytest.mark.parametrize("expression", ["NOT statistics", "statistics OR NOT astronomy"])
def test_doi_only_failed_supplement_remains_evidence_but_never_matches(
    execution, production_boundaries, failure, expression,
):
    e, seen = execution, production_boundaries
    e.members = []
    e.works[0].update(title=None, authorships=None)
    e.config.write_text(e.config.read_text().replace("keyword_expression: statistics",
                                                  f"keyword_expression: {expression}"))
    if failure == "unavailable":
        e.records = {}
    else:
        e.failed_dois.add("10.5555/a")
    result = e.run()

    cluster, = seen["clusters"]
    evidence, = cluster.evidence
    assert evidence.provenance.record_id == "https://openalex.org/W1"
    assert evidence.external_ids.doi == "10.5555/a"
    assert evidence.title is None and not evidence.author_keywords and evidence.abstract is None
    assert not evidence.authors
    assert seen["projections"] == seen["matches"] == ()
    assert not seen["canonical_evidence"] and not seen["hydration_records"] and not e.version_requests
    warning, = [issue for issue in result.warnings if issue.stage == "unsearchable"]
    assert warning.component is monitor.MonitorIssueComponent.SEARCH
    assert warning.severity is monitor.MonitorIssueSeverity.WARNING
    assert warning.record_ids == (evidence.provenance.record_id,)
    assert not any(issue.stage in {"missing_doi", "insufficient_metadata"} for issue in result.warnings)
    supplement, = [unit for unit in result.coverage if unit.component is CoverageComponent.CROSSREF_SUPPLEMENT]
    assert supplement.status is (CoverageStatus.UNAVAILABLE if failure == "unavailable" else CoverageStatus.FAILED)
    assert bool(result.errors) is (failure == "remote")
    assert all(issue.component is monitor.MonitorIssueComponent.CROSSREF_SUPPLEMENT for issue in result.errors)
    assert result.outcome is (monitor.RunOutcome.COMPLETED_WITH_WARNINGS if failure == "unavailable"
                              else monitor.RunOutcome.COMPLETED_WITH_ERRORS)
    assert result.statistics.evidence_clusters == result.statistics.openalex_records == 1
    assert result.statistics.retained_clusters == result.statistics.canonicalization_issues == 0
    assert result.statistics.provider_issues == len(result.errors)
    assert result.statistics.crossref_supplement_records == result.canonical_paper_count == 0
    assert not list((e.output / "Papers").glob("*.md"))


@pytest.mark.parametrize("expression", ["NOT statistics", "astronomy OR NOT statistics"])
def test_mixed_searchable_clusters_map_matches_and_hydration_to_original_evidence(
    execution, production_boundaries, expression,
):
    e, seen = execution, production_boundaries
    e.members = []
    e.records = {}
    e.works = [oa_work(1, "10.5555/empty", title=None),
               oa_work(2, title="astronomy study"), oa_work(3, title="statistics study")]
    for work in e.works[1:]:
        work["doi"] = None
    e.locations["W2"] = []
    e.config.write_text(e.config.read_text().replace("keyword_expression: statistics",
                                                  f"keyword_expression: {expression}"))
    result = e.run()

    assert len(seen["clusters"]) == 3
    assert [projection.titles for projection in seen["projections"]] == [("astronomy study",), ("statistics study",)]
    assert seen["matches"] == (True, False)
    assert [record.provenance.record_id for record in seen["canonical_evidence"]] == ["https://openalex.org/W2"]
    assert [record.provenance.record_id for record in seen["hydration_records"]] == ["https://openalex.org/W2"]
    request, = e.version_requests
    assert request.url.params["filter"] == "ids.openalex:W2"
    warning, = result.warnings
    assert warning.stage == "unsearchable" and warning.record_ids == ("https://openalex.org/W1",)
    assert not result.errors
    assert result.outcome is monitor.RunOutcome.COMPLETED_WITH_WARNINGS
    assert result.statistics.evidence_clusters == result.statistics.openalex_records == 3
    assert result.statistics.retained_clusters == result.canonical_paper_count == 1
    assert result.statistics.provider_issues == result.statistics.canonicalization_issues == 0
    paper, = (e.output / "Papers").glob("*.md")
    assert "astronomy study" in paper.read_text()


def test_all_unsearchable_clusters_each_have_a_warning_and_no_match_candidates(execution, production_boundaries):
    e, seen = execution, production_boundaries
    e.members = []
    e.records = {}
    e.works = [oa_work(1, "10.5555/a", title=None), oa_work(2, "10.5555/b", title=None)]
    e.config.write_text(e.config.read_text().replace("keyword_expression: statistics", "keyword_expression: NOT statistics"))
    result = e.run()

    assert len(seen["clusters"]) == 2
    assert seen["projections"] == seen["matches"] == ()
    assert not seen["canonical_evidence"] and not seen["hydration_records"] and not e.version_requests
    assert {(issue.stage, issue.record_ids) for issue in result.warnings} == {
        ("unsearchable", ("https://openalex.org/W1",)),
        ("unsearchable", ("https://openalex.org/W2",)),
    }
    assert len(result.warnings) == 2 and not result.errors
    assert result.statistics.evidence_clusters == 2
    assert result.statistics.retained_clusters == result.statistics.provider_issues == result.canonical_paper_count == 0


def test_abstract_only_projection_is_searchable_after_unavailable_supplement(execution, production_boundaries):
    e, seen = execution, production_boundaries
    e.members = []
    e.records = {}
    e.works[0].update(title=None, authorships=None, abstract_inverted_index={"statistics": [0]})
    e.locations["W1"] = []
    result = e.run()

    projection, = seen["projections"]
    assert not projection.titles and not projection.author_keywords
    assert projection.abstracts == ("statistics",)
    assert seen["matches"] == (True,)
    assert len(seen["canonical_evidence"]) == len(seen["hydration_records"]) == 1
    assert [issue.stage for issue in result.warnings] == ["insufficient_metadata"]
    assert result.statistics.retained_clusters == 1
    assert result.statistics.provider_issues == result.canonical_paper_count == 0


def run_parallel_discovery_case(e, first, unexpected=None, retained_ids=("https://openalex.org/W1",)):
    """Hold both real discovery calls, then force their completion order."""
    entered = {source: threading.Event() for source in ("openalex", "crossref")}
    release = {source: threading.Event() for source in entered}
    finished = {source: threading.Event() for source in entered}
    calls, completions, workers, events, papers, results, errors = [], [], [], [], [], [], []
    owner = []
    with pytest.MonkeyPatch.context() as patch:
        original_oa = monitor.discover_journals_batched
        original_cr = monitor.CrossrefRetrieval.discover
        original_supplement = monitor.CrossrefRetrieval.supplement
        original_sqlite = sqlite3.connect

        def discovery(source, call):
            assert threading.get_ident() != owner[0]
            assert "read" in events
            calls.append(source)
            workers.append(threading.current_thread())
            entered[source].set()
            assert release[source].wait(5), "Provider branch was not released"
            try:
                if unexpected == source:
                    raise RuntimeError(f"unexpected {source} future failure")
                return call()
            finally:
                completions.append(source)
                finished[source].set()

        def oa(*args, **kwargs):
            return discovery("openalex", lambda: original_oa(*args, retrieved_at=NOW, **kwargs))

        def cr(execution, *args, **kwargs):
            assert all(is_dataclass(row) for row in execution.state.values())
            execution.timestamp = NOW
            return discovery("crossref", lambda: original_cr(execution, *args, **kwargs))

        def supplement(execution, *args, **kwargs):
            assert all(event.is_set() for event in finished.values())
            assert threading.get_ident() == owner[0]
            events.append("supplement")
            return original_supplement(execution, *args, **kwargs)

        def connect(*args, **kwargs):
            assert threading.get_ident() == owner[0], "SQLite opened in a Provider worker"
            return original_sqlite(*args, **kwargs)

        def track(name, original, required=None):
            def call(*args, **kwargs):
                assert threading.get_ident() == owner[0]
                if required is not None:
                    assert required in events
                if name in {"update", "replace"}:
                    assert isinstance(args[1], ps.ProviderState)
                    assert all(is_dataclass(row) for row in (*args[1].crossref_records, *args[1].openalex_versions))
                events.append(name)
                return original(*args, **kwargs)
            return call

        def canonicalize(*args):
            result = monitor_canonicalize(*args)
            # Hold generated identities/timestamps fixed across the fresh workspaces.
            result = type(result)(papers=tuple(paper.model_copy(update={
                "id": UUID(int=index + 1), "workflow": paper.workflow.model_copy(update={"discovered_at": NOW}),
            })
                                              for index, paper in enumerate(result.papers)), issues=result.issues)
            papers.extend(result.papers)
            return result

        monitor_canonicalize = monitor.canonicalize_records
        import literature_monitor.naming as naming
        patch.setattr(naming, "uuid4", lambda: UUID(int=100))
        patch.setattr(sqlite3, "connect", connect)
        patch.setattr(monitor, "discover_journals_batched", oa)
        patch.setattr(monitor.CrossrefRetrieval, "discover", cr)
        patch.setattr(monitor.CrossrefRetrieval, "supplement", supplement)
        patch.setattr(monitor, "read_provider_state", track("read", monitor.read_provider_state))
        patch.setattr(monitor, "consolidate_evidence", track("consolidate", monitor.consolidate_evidence, "supplement"))
        patch.setattr(monitor, "match_searchable_projections", track("matching", monitor.match_searchable_projections, "consolidate"))
        original_hydrate = monitor.hydrate_retained_openalex_versions
        def hydrate(client, records, **kwargs):
            assert tuple(record.external_ids.openalex for record in records) == retained_ids
            return original_hydrate(client, records, retrieved_at=NOW, **kwargs)
        patch.setattr(monitor, "hydrate_retained_openalex_versions", track("hydrate", hydrate, "matching"))
        patch.setattr(monitor, "canonicalize_records", track("canonicalize", canonicalize, "hydrate"))
        patch.setattr(monitor, "materialize_papers", track("materialize", monitor.materialize_papers, "canonicalize"))
        patch.setattr(monitor, "update_provider_state", track("update", monitor.update_provider_state, "materialize"))
        patch.setattr(monitor, "replace_invalid_provider_state", track("replace", monitor.replace_invalid_provider_state, "materialize"))

        def run():
            owner.append(threading.get_ident())
            try:
                results.append(e.run())
            except BaseException as error:
                errors.append(error)

        caller = threading.Thread(target=run)
        caller.start()
        try:
            assert all(event.wait(5) for event in entered.values()), "discoveries did not overlap"
            assert len(workers) == 2 and len({worker.ident for worker in workers}) == 2
            release[first].set()
            assert finished[first].wait(5)
            if unexpected == first:
                assert caller.is_alive()
                assert all(not transport.closed for transport in (*e.oa_transports, *e.cr_transports))
            release["crossref" if first == "openalex" else "openalex"].set()
        finally:
            for event in release.values():
                event.set()
            caller.join(timeout=10)
        assert not caller.is_alive()
        assert all(not worker.is_alive() for worker in workers)
        assert sorted(calls) == ["crossref", "openalex"]
        assert completions[0] == first
        assert len(e.oa_transports) == len(e.cr_transports) == 1
        assert all(transport.closed for transport in (*e.oa_transports, *e.cr_transports))
        if unexpected:
            assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
            assert f"unexpected {unexpected} future failure" in str(errors[0])
            assert not results and not e.path.exists()
            assert not {"update", "replace", "materialize"}.intersection(events)
            return None
        assert not errors
        assert events == ["read", "supplement", "consolidate", "matching", "hydrate", "canonicalize", "materialize",
                          "replace" if "replace" in events else "update"]
        return results[0], tuple(papers)


def test_real_provider_overlap_both_completion_orders_preserve_production_outputs(tmp_path, monkeypatch):
    outputs = []
    for first in ("openalex", "crossref"):
        path = tmp_path / first
        path.mkdir()
        with pytest.MonkeyPatch.context() as patch:
            e = Execution(path, patch)
            e.works.append(oa_work(2, "10.5555/b", title="unmatched astronomy"))
            e.records["10.5555/b"] = cr_record("10.5555/b", title="unmatched astronomy")
            ps.update_provider_state(e.output, ps.ProviderState((cr_state(),)))
            result, papers = run_parallel_discovery_case(e, first)
            state = ps.read_provider_state(e.output).state
            markdown = {str(p.relative_to(e.output)): p.read_text() for p in e.output.rglob("*.md")}
            snapshot = json.loads(e.path.with_name("last-run.json").read_text())
            assert snapshot["schema_version"] == 2 and snapshot["reused_units"] == []
            assert not {"activities", "progress_stage", "last_activity_at"}.intersection(snapshot)
            assert all("eta_seconds" not in text and "last_activity_at" not in text for text in markdown.values())
            assert result.state_usage == monitor.ProviderStateUsage(1, 0, 1, 0, 1)
            assert len(e.version_requests) == 1
            outputs.append((result, papers, state, markdown, snapshot))
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize("source", ["openalex", "crossref"])
def test_unexpected_discovery_future_failure_drains_sibling_and_never_persists(execution, source):
    run_parallel_discovery_case(execution, source, unexpected=source)


@pytest.mark.parametrize("source", ["openalex", "crossref"])
def test_structured_provider_failure_does_not_cancel_sibling(execution, monkeypatch, source):
    e = execution
    if source == "openalex":
        original = e.route_oa
        monkeypatch.setattr(e, "route_oa", lambda request: httpx.Response(403)
                            if request.url.path == "/works" and request.url.params.get("select") != "id,locations"
                            else original(request))
    else:
        original = e.route_cr
        monkeypatch.setattr(e, "route_cr", lambda request: httpx.Response(403)
                            if "issn:" in request.url.params.get("filter", "") else original(request))
    result, _ = run_parallel_discovery_case(
        e, source, retained_ids=() if source == "openalex" else ("https://openalex.org/W1",),
    )
    failed = [unit for unit in result.coverage if unit.provider == source]
    peer = "crossref" if source == "openalex" else "openalex"
    assert any(unit.status is not CoverageStatus.COMPLETE for unit in failed)
    assert all(unit.status is CoverageStatus.COMPLETE for unit in result.coverage if unit.provider == peer)
    assert result.canonical_paper_count == 1
    assert len(e.oa_transports) == len(e.cr_transports) == 1
    assert all(transport.closed for transport in (*e.oa_transports, *e.cr_transports))


def test_invalid_state_replacement_stays_on_application_thread(execution):
    e = execution
    e.path.parent.mkdir(parents=True)
    e.path.write_bytes(b"invalid state")
    result, _ = run_parallel_discovery_case(e, "crossref")
    assert result.canonical_paper_count == 1 and state_warnings(result)
    assert ps.read_provider_state(e.output).status is ps.ProviderStateStatus.AVAILABLE


def test_missing_state_creates_db_after_materialization_and_keeps_cache_inert(execution, monkeypatch):
    e = execution
    cache = e.path.with_name("provider-cache.json")
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"arbitrary invalid historical bytes\x00\xff")
    before = cache.stat()
    events = []
    original_read, original_write = monitor.read_provider_state, monitor.update_provider_state
    def read(output):
        events.append("read")
        assert not e.path.exists()
        return original_read(output)
    def write(output, changes):
        events.append("write")
        assert list((output / "Papers").glob("*.md"))
        assert list((output / "Authors").glob("*.md"))
        assert e.oa_transports[-1].closed and e.cr_transports[-1].closed
        original_write(output, changes)
    monkeypatch.setattr(monitor, "read_provider_state", read)
    monkeypatch.setattr(monitor, "update_provider_state", write)
    result = e.run()
    assert events == ["read", "write"] and not state_warnings(result)
    assert result.canonical_paper_count == 1
    assert result.state_usage == monitor.ProviderStateUsage(0, 0, 1, 0, 1)
    state = ps.read_provider_state(e.output).state
    assert len(state.crossref_records) == len(state.openalex_versions) == 1
    assert cache.stat() == before
    assert cache.read_bytes() == b"arbitrary invalid historical bytes\x00\xff"
    snapshot = json.loads(e.path.with_name("last-run.json").read_text())
    assert snapshot["schema_version"] == 2 and snapshot["reused_units"] == []
    assert "state_usage" not in snapshot and "crossref_reused" not in snapshot
    paper, = (e.output / "Papers").glob("*.md")
    assert "10.5555/version" in paper.read_text()
    assert "state_usage" not in paper.read_text()


def test_matching_then_changed_revisions_keep_live_membership_and_statistics(execution):
    e = execution
    first = e.run()
    historical = ps.ProviderState((cr_state("10.5555/history"),), (
        ps.OpenAlexVersionState("https://openalex.org/W999", NOW, NOW, ()),))
    ps.update_provider_state(e.output, historical)
    same = e.run()
    assert same.state_usage == monitor.ProviderStateUsage(1, 0, 0, 1, 0)
    assert same.statistics == first.statistics
    assert len(e.cr_transports[-1].requests) == 1 and not e.full_requests
    assert len(e.oa_transports[-1].requests) == 2 and not e.version_requests
    assert same.canonical_paper_count == 1
    e.records["10.5555/a"]["indexed"]["date-time"] = NEW
    e.works[0]["updated_date"] = NEW
    refreshed = e.run()
    assert refreshed.state_usage == monitor.ProviderStateUsage(0, 1, 0, 0, 1)
    assert e.full_requests and e.version_requests
    state = ps.read_provider_state(e.output).state
    assert {row.doi for row in state.crossref_records} == {"10.5555/a", "10.5555/history"}
    assert {row.work_id for row in state.openalex_versions} == {"https://openalex.org/W1", "https://openalex.org/W999"}
    assert state.crossref_records[0].indexed_at == datetime.fromisoformat(NEW.replace("Z", "+00:00"))


@pytest.mark.parametrize("expression", [
    "statistics", '"statistics study"', "statist*",
    '"study statistics"~0', "statistics AND NOT astronomy",
])
def test_all_live_and_revision_reuse_preserve_retention_canonical_output_and_decisions(
    execution, monkeypatch, expression,
):
    import literature_monitor.openalex as openalex
    from literature_monitor.application import crossref_retrieval

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is not None else NOW.replace(tzinfo=None)

    # Equivalent retrieval timestamps make full semantic/provenance comparison exact.
    monkeypatch.setattr(openalex, "datetime", FixedDatetime)
    monkeypatch.setattr(crossref_retrieval, "datetime", FixedDatetime)
    e = execution
    e.config.write_text(e.config.read_text().replace(
        "keyword_expression: statistics", f"keyword_expression: '{expression}'",
    ))
    e.works.append(oa_work(2, "10.5555/b", "astronomy study"))
    e.records["10.5555/b"] = cr_record("10.5555/b", title="astronomy study")
    e.members.append("10.5555/b")
    matching, canonical = [], []
    original_match = monitor.match_searchable_projections
    original_canonicalize = monitor.canonicalize_records

    def match(ast, projections):
        result = original_match(ast, projections)
        matching.append((projections, result))
        return result

    def canonicalize(evidence):
        result = original_canonicalize(evidence)
        # New in-memory UUID/workflow defaults are recovered from durable Markdown.
        canonical.append(tuple(paper.model_dump(exclude={"id", "workflow"}) for paper in result.papers))
        return result

    monkeypatch.setattr(monitor, "match_searchable_projections", match)
    monkeypatch.setattr(monitor, "canonicalize_records", canonicalize)
    live = e.run()
    assert live.state_usage == monitor.ProviderStateUsage(0, 0, 2, 0, 1)
    paper, = (e.output / "Papers").glob("*.md")
    text = paper.read_text().replace("status: candidate", "status: kept")
    assert "status: kept" in text
    text = text.replace("\n---\n", "\nhuman_field: preserve\n---\n", 1)
    paper.write_text(text + "\nHuman reading notes.\n")
    before = {p.relative_to(e.output): p.read_bytes() for p in e.output.rglob("*.md")}
    reused = e.run()
    assert reused.state_usage == monitor.ProviderStateUsage(2, 0, 0, 1, 0)
    assert matching[0] == matching[1] and matching[0][1] == (True, False)
    assert canonical[0] == canonical[1] and len(canonical[0]) == 1
    assert live.statistics == reused.statistics and live.coverage == reused.coverage
    assert reused.matched_existing_papers == 1 and reused.created_papers == 0
    assert before == {p.relative_to(e.output): p.read_bytes() for p in e.output.rglob("*.md")}
    assert not e.full_requests and not e.version_requests
    for transport in (*e.oa_transports, *e.cr_transports):
        for request in transport.requests:
            assert not {"search", "q", "query", "keyword_expression"}.intersection(request.url.params)
            assert expression not in str(request.url)


def test_historical_rows_do_not_become_candidates_without_live_anchors(execution):
    e = execution
    ps.update_provider_state(e.output, ps.ProviderState((cr_state(),)))
    e.members, e.works = [], []
    result = e.run()
    assert result.canonical_paper_count == 0 and not e.full_requests and not e.version_requests
    assert result.state_usage == monitor.ProviderStateUsage()
    assert len(ps.read_provider_state(e.output).state.crossref_records) == 1


@pytest.mark.parametrize("revision", [None, NEW])
def test_retained_only_hydration_and_revision_binding(execution, revision):
    e = execution
    e.works[0]["updated_date"] = revision
    e.works.append(oa_work(2, "10.5555/b", "unrelated astronomy", revision))
    result = e.run()
    assert result.canonical_paper_count == 1 and result.statistics.retained_clusters == 1
    assert [r.url.params["filter"] for r in e.version_requests] == ["ids.openalex:W1"]
    versions = ps.read_provider_state(e.output).state.openalex_versions
    assert len(versions) == int(revision is not None)
    assert result.state_usage.openalex_versions_hydrated == 1


def test_unmatched_candidates_do_not_consume_even_matching_version_state(execution):
    e = execution
    e.works[0]["title"] = "astronomy study"
    e.records["10.5555/a"]["title"] = ["astronomy study"]
    row = ps.OpenAlexVersionState("https://openalex.org/W1", NOW, NOW, ())
    ps.update_provider_state(e.output, ps.ProviderState(openalex_versions=(row,)))
    result = e.run()
    assert result.statistics.retained_clusters == result.canonical_paper_count == 0
    assert not e.version_requests and result.state_usage.openalex_versions_reused == 0
    assert ps.read_provider_state(e.output).state.openalex_versions == (row,)


def test_missing_revisions_use_live_evidence_without_durable_pending_rows(execution):
    e = execution
    e.records["10.5555/a"].pop("indexed")
    e.works[0].pop("updated_date")
    result = e.run()
    assert result.canonical_paper_count == 1 and e.full_requests and e.version_requests
    assert result.state_usage == monitor.ProviderStateUsage(0, 0, 1, 0, 1)
    assert ps.read_provider_state(e.output).state == ps.ProviderState()


def test_version_failure_retains_candidates_and_discovery_coverage(execution):
    e = execution
    e.fail_versions = True
    result = e.run()
    assert result.canonical_paper_count == 1 and result.state_usage.openalex_versions_hydrated == 0
    assert all(unit.status is CoverageStatus.COMPLETE for unit in result.coverage)
    assert any(issue.component is monitor.MonitorIssueComponent.OPENALEX for issue in result.warnings)
    assert not ps.read_provider_state(e.output).state.openalex_versions


def test_shared_version_hints_cannot_merge_selected_research_work_partition(execution):
    e = execution
    e.works.append(oa_work(2, "10.5555/b", "statistics distinct", REV))
    e.records["10.5555/b"] = cr_record("10.5555/b", title="statistics distinct")
    e.members.append("10.5555/b")
    result = e.run()
    assert result.statistics.evidence_clusters == result.statistics.retained_clusters == 2
    assert result.canonical_paper_count == 2
    assert e.version_requests[0].url.params["filter"] == "ids.openalex:W1|W2"
    assert len(list((e.output / "Papers").glob("*.md"))) == 2


@pytest.mark.parametrize("kind", ["corrupt", "schema"])
def test_invalid_regular_db_runs_live_and_is_safely_replaced(execution, kind, monkeypatch):
    e = execution
    e.path.parent.mkdir(parents=True)
    if kind == "corrupt":
        e.path.write_bytes(b"invalid DB")
    else:
        with sqlite3.connect(e.path) as connection:
            connection.execute("CREATE TABLE incompatible (x TEXT)")
    before = e.path.read_bytes()
    original = monitor.replace_invalid_provider_state
    def replace(output, pending):
        assert e.path.read_bytes() == before
        assert list((output / "Papers").glob("*.md"))
        original(output, pending)
    monkeypatch.setattr(monitor, "replace_invalid_provider_state", replace)
    result = e.run()
    assert [(issue.stage, issue.path) for issue in state_warnings(result)] == [("read", e.path)]
    assert result.state_usage.crossref_new == 1 and e.full_requests
    assert ps.read_provider_state(e.output).status is ps.ProviderStateStatus.AVAILABLE
    assert not list(e.path.parent.glob(".provider-state-*"))


@pytest.mark.parametrize("kind", ["symlink", "directory", "metadata_symlink"])
def test_unsafe_state_path_is_untouched_while_materialization_continues(execution, kind):
    e = execution
    target = e.config.parent / "human-state"
    target.mkdir()
    sentinel = target / "sentinel"
    sentinel.write_bytes(b"human bytes")
    e.path.parent.parent.mkdir()
    if kind == "metadata_symlink":
        e.path.parent.symlink_to(target, target_is_directory=True)
    else:
        e.path.parent.mkdir()
        if kind == "directory":
            e.path.mkdir()
        else:
            e.path.symlink_to(sentinel)
    before = e.path.parent.lstat() if kind == "metadata_symlink" else e.path.lstat()
    result = e.run()
    assert {issue.stage for issue in state_warnings(result)} == {"read", "persistence"}
    assert (e.path.parent.lstat() if kind == "metadata_symlink" else e.path.lstat()) == before
    assert sentinel.read_bytes() == b"human bytes"
    assert result.created_papers == 1 and result.created_authors == 1


def test_busy_db_persistence_failure_preserves_prior_rows_and_materialization(execution):
    e = execution
    old = ps.ProviderState((cr_state("10.5555/history"),))
    ps.update_provider_state(e.output, old)
    before = e.path.read_bytes()
    connection = sqlite3.connect(e.path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        result = e.run()
        assert [(issue.stage, issue.path) for issue in state_warnings(result)] == [("persistence", e.path)]
        assert result.created_papers == 1 and result.created_authors == 1
    finally:
        connection.rollback()
        connection.close()
    assert e.path.read_bytes() == before and ps.read_provider_state(e.output).state == old
    assert all(unit.status is CoverageStatus.COMPLETE for unit in result.coverage)


def test_exclusive_lock_read_failure_never_replaces_existing_db(execution):
    e = execution
    old = ps.ProviderState((cr_state("10.5555/history"),))
    ps.update_provider_state(e.output, old)
    before = e.path.read_bytes()
    connection = sqlite3.connect(e.path)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        result = e.run()
        assert {issue.stage for issue in state_warnings(result)} == {"read", "persistence"}
        assert result.state_usage.crossref_new == 1 and result.created_papers == 1
    finally:
        connection.rollback()
        connection.close()
    assert e.path.read_bytes() == before and ps.read_provider_state(e.output).state == old


def test_invalid_replacement_failure_keeps_invalid_bytes_and_written_papers(execution, monkeypatch):
    e = execution
    e.path.parent.mkdir(parents=True)
    e.path.write_bytes(b"invalid original")
    def fail(output, pending):
        assert list((output / "Papers").glob("*.md"))
        raise OSError("simulated publication failure")
    monkeypatch.setattr(monitor, "replace_invalid_provider_state", fail)
    result = e.run()
    assert {issue.stage for issue in state_warnings(result)} == {"read", "persistence"}
    assert e.path.read_bytes() == b"invalid original" and result.created_papers == 1
    snapshot = json.loads(e.path.with_name("last-run.json").read_text())
    assert snapshot["outcome"] == result.outcome.value


def test_partial_retrieval_persists_successful_rows_without_overwriting_failed_refresh(execution):
    e = execution
    old = cr_state()
    ps.update_provider_state(e.output, ps.ProviderState((old,)))
    e.records["10.5555/a"]["indexed"]["date-time"] = NEW
    e.failed_dois.add("10.5555/a")
    e.records["10.5555/b"] = cr_record("10.5555/b", title="statistics peer")
    e.members.append("10.5555/b")
    result = e.run()
    assert result.outcome is monitor.RunOutcome.COMPLETED_WITH_ERRORS
    discovery, = (unit for unit in result.coverage if unit.component is CoverageComponent.CROSSREF_DISCOVERY)
    assert discovery.status is CoverageStatus.PARTIAL
    rows = {row.doi: row for row in ps.read_provider_state(e.output).state.crossref_records}
    assert rows[old.doi] == old and "10.5555/b" in rows
    assert result.state_usage.crossref_new == 1 and result.state_usage.crossref_refreshed == 0


@pytest.mark.parametrize("failure", ["preflight", "expression", "backend"])
def test_invalid_configuration_never_persists_state_or_touches_cache(execution, failure, monkeypatch):
    e = execution
    ps.update_provider_state(e.output, ps.ProviderState((cr_state("10.5555/history"),)))
    before = e.path.read_bytes()
    cache = e.path.with_name("provider-cache.json")
    cache.write_bytes(b"invalid legacy bytes")
    def forbidden(*args, **kwargs):
        pytest.fail("invalid configuration must not persist state")
    monkeypatch.setattr(monitor, "update_provider_state", forbidden)
    monkeypatch.setattr(monitor, "replace_invalid_provider_state", forbidden)
    if failure == "preflight":
        e.config.write_text(e.config.read_text().replace("statistics", "statistics AND"))
        monkeypatch.setattr(monitor, "read_provider_state", forbidden)
    else:
        error = SearchExpressionError if failure == "expression" else SearchBackendError
        def fail(*args):
            raise error("simulated search failure")
        monkeypatch.setattr(monitor, "match_searchable_projections", fail)
    result = e.run()
    assert result.outcome is monitor.RunOutcome.INVALID_CONFIGURATION
    assert e.path.read_bytes() == before and cache.read_bytes() == b"invalid legacy bytes"
    assert all(t.closed for t in (*e.oa_transports, *e.cr_transports))
    assert not list((e.output / "Papers").glob("*.md"))


@pytest.mark.parametrize("command", ["canonicalize", "materialize", "validate"])
def test_diagnostics_are_all_live_and_have_no_state_access(execution, command, monkeypatch, capsys):
    e = execution
    ps.update_provider_state(e.output, ps.ProviderState((cr_state(),)))
    before = e.path.read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail("diagnostic must not access Provider state")
    for name in ("read_provider_state", "update_provider_state", "replace_invalid_provider_state"):
        monkeypatch.setattr(monitor, name, forbidden)
    if command == "validate":
        result = monitor.validate_monitor(e.config)
        assert result.outcome is monitor.ValidationOutcome.VALID
        assert not e.cr_transports
        assert [r.url.path for r in e.oa_transports[0].requests] == ["/sources"]
    else:
        args = [command, "--config", str(e.config)]
        if command == "materialize":
            args += ["--output-dir", str(e.output)]
        assert main(args) == 0
        assert e.full_requests and e.version_requests
        capsys.readouterr()
    assert e.path.read_bytes() == before


def test_compatibility_cli_runs_automatic_state_pipeline_and_keeps_old_cache(execution, capsys):
    e = execution
    cache = e.path.with_name("provider-cache.json")
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"invalid legacy cache")
    before = cache.stat()
    assert main(("run", "--config", str(e.config), "--reuse-provider-cache")) == 0
    assert capsys.readouterr().err.count("Provider-state reuse is now automatic.") == 1
    assert cache.stat() == before
    assert cache.read_bytes() == b"invalid legacy cache"
    assert ps.read_provider_state(e.output).status is ps.ProviderStateStatus.AVAILABLE


def test_alias_supplement_preserves_requested_coverage_and_prime_evidence(execution):
    e = execution
    e.works[0]["doi"] = "https://doi.org/10.5555/alias"
    e.aliases["10.5555/alias"] = "10.5555/a"
    ps.update_provider_state(e.output, ps.ProviderState((cr_state(),)))
    result = e.run()
    assert result.state_usage.crossref_reused == 1
    assert result.statistics.crossref_supplement_records == 1
    assert not e.full_requests or all(r.url.path != "/v1/works" for r in e.full_requests)
    supplements = [unit for unit in result.coverage if unit.component is CoverageComponent.CROSSREF_SUPPLEMENT]
    assert len(supplements) == 1 and supplements[0].doi == "10.5555/alias"
    assert supplements[0].status is CoverageStatus.COMPLETE
    assert {row.doi for row in ps.read_provider_state(e.output).state.crossref_records} == {"10.5555/a"}


def test_later_alias_refresh_uses_current_prime_metadata_and_counts_identity_once(execution, monkeypatch):
    e = execution
    e.works.append(oa_work(2, "10.5555/alias", "statistics alias"))
    e.aliases["10.5555/alias"] = "10.5555/a"
    ps.update_provider_state(e.output, ps.ProviderState((cr_state(),)))
    original = e.route_cr
    current = cr_record(revision=NEW, title="current statistics metadata")
    def route(request):
        if request.url.path == "/v1/works" and "doi:10.5555/a" in request.url.params["filter"]:
            return work_list([])
        if request.url.path == "/v1/works/10.5555/a":
            return {"status": "ok", "message-type": "work", "message": current}
        return original(request)
    monkeypatch.setattr(e, "route_cr", route)
    assembled = []
    original_assembly = monitor.assemble_live_provider_evidence
    def assemble(*args):
        result = original_assembly(*args)
        assembled.extend(item for item in result if item.provenance.provider == "crossref")
        return result
    monkeypatch.setattr(monitor, "assemble_live_provider_evidence", assemble)
    result = e.run()
    assert result.state_usage.crossref_reused == 0 and result.state_usage.crossref_refreshed == 1
    assert len(assembled) == 1 and assembled[0].title == "current statistics metadata"
    assert {ref.record_id for ref in assembled[0].supplements} == {"https://openalex.org/W1", "https://openalex.org/W2"}
    row, = ps.read_provider_state(e.output).state.crossref_records
    assert row.record.title == "current statistics metadata" and row.indexed_at == datetime.fromisoformat(NEW.replace("Z", "+00:00"))


def test_pure_assembly_attaches_current_anchors_and_deduplicates_prime(execution):
    from literature_monitor.models import CanonicalMetadata, ExternalIds, MetadataSource, ProviderRecordRef
    from literature_monitor.openalex import OpenAlexWorkRecord
    from literature_monitor.retrieval import assemble_live_provider_evidence
    oa = OpenAlexWorkRecord(
        metadata=CanonicalMetadata(title="statistics study", journal="Biometrics"), authors=(),
        external_ids=ExternalIds(doi="10.5555/a", openalex="https://openalex.org/W1"),
        source_id="https://openalex.org/S1",
        provenance=MetadataSource(provider="openalex", record_id="https://openalex.org/W1", retrieved_at=NOW),
    )
    record = cr_state().record
    alias_anchor = ProviderRecordRef(provider="openalex", record_id="https://openalex.org/W2")
    result = assemble_live_provider_evidence((oa,), (record,), (record.to_evidence(supplements=(alias_anchor,)),))
    assert len(result) == 2
    assert result[1].supplements == (ProviderRecordRef(provider="openalex", record_id=oa.provenance.record_id), alias_anchor)
    assert result[1].external_ids.crossref == record.doi
    assert not execution.oa_transports and not execution.cr_transports
