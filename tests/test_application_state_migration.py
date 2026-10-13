"""Production Crossref state reuse and safe replacement with real SQLite state."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import sqlite3
import threading
from uuid import UUID
from dataclasses import is_dataclass

import httpx
import pytest
import yaml

from literature_monitor.application import monitor, provider_state as ps
from literature_monitor.coverage import CoverageComponent, CoverageStatus
from literature_monitor.crossref import CrossrefClient, normalize_crossref_discovered_work
from literature_monitor.openalex import OpenAlexClient
from literature_monitor.search import SearchBackendError, SearchExpressionError

REV = "2026-02-01T00:00:00Z"
NEW = "2026-02-02T00:00:00Z"
NOW = datetime(2026, 2, 1, tzinfo=timezone.utc)
ISSN = "0006-341X"


def cr_record(doi="10.5555/a", revision=REV, title="statistics study"):
    return {"DOI": doi, "type": "journal-article", "ISSN": [ISSN], "indexed": {"date-time": revision},
            "title": [title], "container-title": ["Biometrics"],
            "author": [{"given": "Ada", "family": "Author"}],
            "published": {"date-parts": [[2026, 1, 1]]}}


def cr_state(doi="10.5555/a", revision=REV, title="statistics study"):
    record, _ = normalize_crossref_discovered_work(cr_record(doi, revision, title), NOW)
    return ps.CrossrefRecordState.from_record(record)


def oa_work(number=1, doi="10.5555/a", title="statistics study"):
    return {"id": f"https://openalex.org/W{number}", "doi": f"https://doi.org/{doi}",
            "title": title, "publication_date": "2026-01-01",
            "abstract_inverted_index": None,
            "authorships": [{"author": {"display_name": "Ada Author"}}],
            "primary_location": {"is_published": True,
                                 "source": {"id": "https://openalex.org/S1", "display_name": "Biometrics"}}}


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
        (tmp_path / "list.md").write_text('## Journals\n\n| Journal | ISSN-L | Publisher ID | Group |\n|---|---|---|---|\n| Biometrics | 0006-341X |  |  |\n')
        self.config.write_text("venue_whitelist: list.md\nkeyword_expression: statistics\noutput_dir: workspace\nfrom_month: 2026-01\nto_month: 2026-01\n")
        self.output = tmp_path / "workspace"
        self.path = self.output / ".literature-monitor" / ps.STATE_FILENAME
        self.works = [oa_work()]
        self.records = {"10.5555/a": cr_record()}
        self.members = ["10.5555/a"]
        self.failed_dois = set()
        self.aliases = {}
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
        assert not {"locations", "updated_date"}.intersection(request.url.params["select"].split(","))
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
    item = Execution(tmp_path, monkeypatch)
    # Cache/persistence tests need metadata independently of OpenAlex early matching.
    item.works[0]['title'] = None
    return item


def state_warnings(result):
    return [issue for issue in result.warnings if issue.component is monitor.MonitorIssueComponent.PROVIDER_STATE]


def durable_state(path):
    with sqlite3.connect(path) as connection:
        rows = tuple(connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                     for table in ("schema_metadata", "crossref_records"))
    connection.close()
    return rows


def seed_old_state(execution, version, work_type="journal-article"):
    e = execution
    current = ps.CrossrefRecordState.from_record(cr_state().record.model_copy(update={"work_type": work_type}))
    ps.update_provider_state(e.output, ps.ProviderState((current, cr_state("10.5555/history"))))
    with sqlite3.connect(e.path) as connection:
        connection.execute("UPDATE schema_metadata SET schema_version=?", (version,))
        connection.execute("CREATE TABLE openalex_versions (work_id TEXT PRIMARY KEY NOT NULL, hydrated_against_updated_at TEXT NOT NULL, retrieved_at TEXT NOT NULL, versions_json TEXT NOT NULL)")
        connection.execute("INSERT INTO openalex_versions VALUES (?, ?, ?, ?)", (
            "https://openalex.org/W999", ps._time_text(NOW), ps._time_text(NOW),
            '{"serialization_version":1,"version_hints":[]}',
        ))
    connection.close()
    return e.path.read_bytes()


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("work_type", ["journal-article", "journal-issue"])
def test_old_state_is_not_reused_and_replaced_only_after_materialization(execution, monkeypatch, version, work_type):
    e = execution
    before = seed_old_state(e, version, work_type)
    read = ps.read_provider_state(e.output)
    assert read.status is ps.ProviderStateStatus.INVALID and read.state is None and read.replaceable
    cache = e.path.with_name("provider-cache.json")
    cache.write_bytes(b"invalid legacy cache remains inert")
    cache_stat = cache.stat()
    original = monitor.replace_invalid_provider_state
    writes = []

    def persist(output, pending):
        assert e.path.read_bytes() == before
        assert list((output / "Papers").glob("*.md"))
        assert list((output / "Authors").glob("*.md"))
        writes.append(pending)
        original(output, pending)

    monkeypatch.setattr(monitor, "replace_invalid_provider_state", persist)
    result = e.run()
    assert len(writes) == 1
    assert result.state_usage == monitor.ProviderStateUsage(0, 0, 1)
    assert len(e.full_requests) == 1
    assert result.canonical_paper_count == 1
    assert result.outcome is monitor.RunOutcome.COMPLETED_WITH_WARNINGS
    assert [(issue.stage, issue.path) for issue in state_warnings(result)] == [("read", e.path)]
    assert not result.errors
    assert all(unit.status is CoverageStatus.COMPLETE for unit in result.coverage)
    metadata, rows = durable_state(e.path)
    assert metadata == [(1, 3)]
    assert {row[0] for row in rows} == {"10.5555/a"}
    assert ps.read_provider_state(e.output).state.crossref_records[0].record.work_type == "journal-article"
    with sqlite3.connect(e.path) as connection:
        assert {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {"schema_metadata", "crossref_records"}
    connection.close()
    assert cache.stat() == cache_stat and cache.read_bytes() == b"invalid legacy cache remains inert"


def nonclean_run(e, issue):
    if issue == "warning":
        e.records["10.5555/a"]["abstract"] = 123  # Real malformed-field warning, not ordinary Provider disagreement.
        e.records["10.5555/a"]["indexed"]["date-time"] = NEW
    elif issue == "error":
        e.records["10.5555/b"] = cr_record("10.5555/b", title="statistics peer")
        e.members.append("10.5555/b")
        e.failed_dois.add("10.5555/b")


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("issue", ["warning", "error"])
def test_nonclean_production_run_still_safely_replaces_old_state(execution, issue, version):
    e = execution
    seed_old_state(e, version)
    nonclean_run(e, issue)
    result = e.run()
    expected = monitor.RunOutcome.COMPLETED_WITH_WARNINGS if issue == "warning" else monitor.RunOutcome.COMPLETED_WITH_ERRORS
    assert result.outcome is expected
    assert bool(result.errors) is (issue == "error")
    assert result.canonical_paper_count == 1
    assert [warning.stage for warning in state_warnings(result)] == ["read"]
    assert durable_state(e.path)[0] == [(1, 3)]
    assert ps.read_provider_state(e.output).status is ps.ProviderStateStatus.AVAILABLE


@pytest.mark.parametrize("issue", [None, "warning", "error"])
def test_persistence_failure_preserves_materialization_state_and_issue_precedence(execution, monkeypatch, issue):
    e = execution
    ps.update_provider_state(e.output, ps.ProviderState((cr_state(), cr_state("10.5555/history"))))
    before = durable_state(e.path)
    e.records["10.5555/a"]["indexed"]["date-time"] = NEW
    nonclean_run(e, issue)
    original = ps._upsert_changes
    materialized = {}

    def fail_after_pending(connection, pending):
        assert connection.execute("SELECT schema_version FROM schema_metadata").fetchone() == (3,)
        original(connection, pending)
        for folder in ("Papers", "Authors"):
            paths = list((e.output / folder).glob("*.md"))
            assert paths
            materialized.update({path: path.read_bytes() for path in paths})
        raise sqlite3.IntegrityError("simulated persistence failure after pending upsert")

    monkeypatch.setattr(ps, "_upsert_changes", fail_after_pending)
    result = e.run()
    warning, = state_warnings(result)
    assert warning.severity is monitor.MonitorIssueSeverity.WARNING
    assert warning.stage == "persistence" and warning.path == e.path
    assert bool(result.errors) is (issue == "error")
    expected = monitor.RunOutcome.COMPLETED_WITH_ERRORS if issue == "error" else monitor.RunOutcome.COMPLETED_WITH_WARNINGS
    assert result.outcome is expected
    assert result.created_papers == result.created_authors == 1
    assert all(path.read_bytes() == content for path, content in materialized.items())
    assert durable_state(e.path) == before
    assert ps.read_provider_state(e.output).status is ps.ProviderStateStatus.AVAILABLE
    snapshot = json.loads(e.path.with_name("last-run.json").read_text())
    assert snapshot["schema_version"] == 2 and snapshot["outcome"] == expected.value
    assert snapshot["reused_units"] == []


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


def run_parallel_discovery_case(e, first, unexpected=None):
    """Hold both real discovery calls, then force their completion order."""
    entered = {source: threading.Event() for source in ("openalex", "crossref")}
    release = {source: threading.Event() for source in entered}
    finished = {source: threading.Event() for source in entered}
    calls, completions, workers, events, papers, results, errors = [], [], [], [], [], [], []
    owner = []
    with pytest.MonkeyPatch.context() as patch:
        original_oa = monitor.discover_resolved_sources
        original_cr = monitor.CrossrefRetrieval.discover_manifest
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
                    assert all(is_dataclass(row) for row in args[1].crossref_records)
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
        patch.setattr(sqlite3, "connect", connect)
        patch.setattr(monitor, "discover_resolved_sources", oa)
        patch.setattr(monitor.CrossrefRetrieval, "discover_manifest", cr)
        patch.setattr(monitor.CrossrefRetrieval, "supplement", supplement)
        patch.setattr(monitor, "read_provider_state", track("read", monitor.read_provider_state))
        patch.setattr(monitor, "consolidate_evidence", track("consolidate", monitor.consolidate_evidence, "matching"))
        patch.setattr(monitor, "match_searchable_projections", track("matching", monitor.match_searchable_projections, "read"))
        patch.setattr(monitor, "canonicalize_records", track("canonicalize", canonicalize, "matching"))
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
        assert events[0] == "read" and events[-1] in {"replace", "update"}
        assert events.index("matching") < events.index("consolidate") < events.index("canonicalize")
        assert events.index("canonicalize") < events.index("materialize") < len(events) - 1
        return results[0], tuple(papers)


def test_real_provider_overlap_both_completion_orders_preserve_production_outputs(tmp_path, monkeypatch):
    outputs = []
    for first in ("openalex", "crossref"):
        path = tmp_path / first
        path.mkdir()
        with pytest.MonkeyPatch.context() as patch:
            e = Execution(path, patch)
            e.works[0]['title'] = None
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
            assert result.state_usage == monitor.ProviderStateUsage(1, 0, 1)
            assert not e.version_requests
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
                            if request.url.path == "/works"
                            else original(request))
    else:
        original = e.route_cr
        monkeypatch.setattr(e, "route_cr", lambda request: httpx.Response(403)
                            if "issn:" in request.url.params.get("filter", "") else original(request))
    result, _ = run_parallel_discovery_case(e, source)
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
    assert result.state_usage == monitor.ProviderStateUsage(0, 0, 1)
    state = ps.read_provider_state(e.output).state
    assert len(state.crossref_records) == 1
    assert cache.stat() == before
    assert cache.read_bytes() == b"arbitrary invalid historical bytes\x00\xff"
    snapshot = json.loads(e.path.with_name("last-run.json").read_text())
    assert snapshot["schema_version"] == 2 and snapshot["reused_units"] == []
    assert "state_usage" not in snapshot and "crossref_reused" not in snapshot
    paper, = (e.output / "Papers").glob("*.md")
    assert "10.5555/a" in paper.read_text()
    assert "state_usage" not in paper.read_text()


def test_saved_doi_does_not_refresh_cached_metadata_when_revision_changes(execution):
    e = execution
    assert e.run().created_papers == 1
    before = durable_state(e.path)
    e.records["10.5555/a"]["indexed"]["date-time"] = NEW
    repeated = e.run()
    assert repeated.matched_existing_papers == 1 and repeated.canonical_paper_count == 0
    assert repeated.state_usage == monitor.ProviderStateUsage()
    assert not e.full_requests and durable_state(e.path) == before


def test_historical_rows_do_not_become_candidates_without_live_anchors(execution):
    e = execution
    ps.update_provider_state(e.output, ps.ProviderState((cr_state(),)))
    e.members, e.works = [], []
    result = e.run()
    assert result.canonical_paper_count == 0 and not e.full_requests and not e.version_requests
    assert result.state_usage == monitor.ProviderStateUsage()
    assert len(ps.read_provider_state(e.output).state.crossref_records) == 1


def test_run_never_requests_openalex_locations_after_matching(execution):
    e = execution
    e.works.append(oa_work(2, "10.5555/b", "unrelated astronomy"))
    e.records["10.5555/b"] = cr_record("10.5555/b", title="unrelated astronomy")
    events = []
    result = monitor.run_monitor(e.config, progress_callback=events.append)
    assert result.canonical_paper_count == 1 and result.statistics.retained_clusters == 1
    assert [request.url.path for request in e.oa_transports[-1].requests] == ["/sources", "/works"]
    assert not e.version_requests
    assert all(event.activity is None or event.activity.operation != "version_hydration" for event in events)
    assert result.statistics.provider_issues == 0
    assert all(unit.status is CoverageStatus.COMPLETE for unit in result.coverage)


def test_missing_crossref_revision_uses_live_evidence_without_durable_pending_rows(execution):
    e = execution
    e.records["10.5555/a"].pop("indexed")
    result = e.run()
    assert result.canonical_paper_count == 1 and e.full_requests and not e.version_requests
    assert result.state_usage == monitor.ProviderStateUsage(0, 0, 1)
    assert ps.read_provider_state(e.output).status is ps.ProviderStateStatus.MISSING


@pytest.mark.parametrize("kind", ["corrupt", "schema", "digest"])
def test_invalid_regular_db_runs_live_and_is_safely_replaced(execution, kind, monkeypatch):
    e = execution
    if kind == "digest":
        ps.update_provider_state(e.output, ps.ProviderState((cr_state(),)))
        with sqlite3.connect(e.path) as connection:
            connection.execute("UPDATE crossref_records SET semantic_hash='bad'")
        connection.close()
    else:
        e.path.parent.mkdir(parents=True)
    if kind == "corrupt":
        e.path.write_bytes(b"invalid DB")
    elif kind == "schema":
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
    assert durable_state(e.path)[0] == [(1, 3)]
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
    assert discovery.status is CoverageStatus.COMPLETE
    rows = {row.doi: row for row in ps.read_provider_state(e.output).state.crossref_records}
    assert rows[old.doi] == old and "10.5555/b" in rows
    assert result.state_usage.crossref_new == 1 and result.state_usage.crossref_refreshed == 0


@pytest.mark.parametrize("failure", ["preflight", "expression", "backend"])
@pytest.mark.parametrize("version", [1, 2, 3])
def test_invalid_configuration_never_persists_state_or_touches_cache(execution, failure, monkeypatch, version):
    e = execution
    if version in {1, 2}:
        seed_old_state(e, version)
    else:
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


def test_reused_provider_state_gets_current_configuration_attribution_without_schema_or_content_change(execution, monkeypatch):
    e = execution
    original_oa = e.route_oa
    def route_oa(request):
        result = original_oa(request)
        if request.url.path == "/sources":
            result["results"][0]["issn"].append("1541-0420")
        return result
    monkeypatch.setattr(e, "route_oa", route_oa)
    papers = []
    original = monitor.canonicalize_records
    def canonicalize(records):
        result = original(records)
        papers.extend(result.papers)
        return result
    monkeypatch.setattr(monitor, "canonicalize_records", canonicalize)
    first = e.run()
    assert first.canonical_paper_count == 1 and papers[-1].journal_issns == (ISSN,)
    before = durable_state(e.path)
    state_before = ps.read_provider_state(e.output).state
    (e.config.parent / "list.md").write_text(
        '## Journals\n\n| Journal | ISSN-L | Publisher ID | Group |\n|---|---|---|---|\n| Biometrics | 0006-341X |  |  |\n',
    )
    second = e.run()
    assert second.state_usage.crossref_reused == 0 and not e.full_requests
    assert papers[-1].journal_issns == (ISSN,)
    assert second.warnings == first.warnings and second.diagnostics == first.diagnostics
    assert ps.SCHEMA_VERSION == 3 and durable_state(e.path) == before
    assert ps.read_provider_state(e.output).state == state_before
    persisted, = (e.output / "Papers").glob("*.md")
    assert yaml.safe_load(persisted.read_text().split("---", 2)[1])["journal_issns"] == [ISSN]
