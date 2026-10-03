"""Observable A5 requests, state reuse, isolation and current live anchors."""

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

import literature_monitor.crossref as crossref
from literature_monitor.application.crossref_retrieval import CrossrefRetrieval
from literature_monitor.application.provider_state import CrossrefRecordState, crossref_semantic_hash
from literature_monitor.config import JournalConfig
from literature_monitor.coverage import CoverageStatus
from literature_monitor.crossref import CrossrefClient, normalize_crossref_discovered_work, retrieve_crossref_manifests
from literature_monitor.models import CanonicalMetadata, ExternalIds, MetadataSource
from literature_monitor.openalex import OpenAlexWorkRecord

A, B = "0006-341X", "2168-2267"
DAY = date(2026, 1, 1)
NOW = datetime(2026, 2, 1, tzinfo=timezone.utc)
REV = "2026-01-02T00:00:00Z"
NEW_REV = "2026-01-03T00:00:00Z"
JOURNAL = JournalConfig(name="Biometrics", issn=(A, B))


def member(doi="10.1234/a", issns=(A,), revision=REV):
    return {"DOI": doi, "ISSN": list(issns), "indexed": {"date-time": revision}}


def full(doi="10.1234/a", issns=(A,), revision=REV):
    return {**member(doi, issns, revision), "title": ["Current paper"],
        "container-title": ["Biometrics"], "abstract": "<jats:p>Full abstract</jats:p>",
        "author": [{"given": "Ada", "family": "Author"}],
        "published": {"date-parts": [[2026, 1, 1]]},
        "relation": {"is-version-of": [{"id-type": "doi", "id": "10.1234/original"}]}}


def work_list(items=(), total=None, cursor="next"):
    return {"status": "ok", "message-type": "work-list", "message": {
        "items": list(items), "total-results": len(items) if total is None else total, "next-cursor": cursor}}


def singleton(item):
    return {"status": "ok", "message-type": "work", "message": item}


def state(doi="10.1234/a", issns=(A,), revision=REV):
    record, _ = normalize_crossref_discovered_work(full(doi, issns, revision), NOW - timedelta(days=1))
    return CrossrefRecordState.from_record(record)


def oa(doi="10.1234/a", work_id="W1"):
    return OpenAlexWorkRecord(metadata=CanonicalMetadata(title="Live OA", journal="Biometrics"),
        external_ids=ExternalIds(openalex=f"https://openalex.org/{work_id}", doi=doi),
        authors=(), source_id="https://openalex.org/S1", provenance=MetadataSource(
            provider="openalex", record_id=f"https://openalex.org/{work_id}", retrieved_at=NOW))


class HTTP:
    def __init__(self, handler):
        self.requests = []
        def respond(request):
            self.requests.append(request)
            result = handler(request)
            return result if isinstance(result, httpx.Response) else httpx.Response(200, json=result)
        self.client = CrossrefClient(transport=httpx.MockTransport(respond), sleep=lambda _: None)

    @property
    def full_requests(self):
        return [r for r in self.requests if r.url.path == "/v1/works" and "select" not in r.url.params]

    def retrieval(self, rows=()):
        return CrossrefRetrieval(self.client, record_state=rows, retrieved_at=NOW)


@pytest.fixture(autouse=True)
def no_persistence(monkeypatch):
    import sqlite3
    import literature_monitor.application.provider_state as provider_state
    def forbidden(*args, **kwargs):
        pytest.fail("A5 must not access persistent Provider state")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    for name in ("read_provider_state", "update_provider_state", "replace_invalid_provider_state"):
        monkeypatch.setattr(provider_state, name, forbidden)


def test_small_complete_manifest_uses_one_request_and_configured_order():
    http = HTTP(lambda r: work_list([member(issns=(A, B))]))
    units = retrieve_crossref_manifests(http.client, (B, A, B), DAY, DAY)
    assert [u.issn for u in units] == [B, A]
    assert all(u.complete for u in units) and len(http.requests) == 1
    assert all(len(u.members) == 1 for u in units)


def test_planner_splits_issns_before_nonoverlapping_exhaustive_dates(monkeypatch):
    monkeypatch.setattr(crossref, "CROSSREF_PAGE_SIZE", 2)
    def respond(r):
        filters = r.url.params["filter"].split(",")
        issns = [f[5:] for f in filters if f.startswith("issn:")]
        start = date.fromisoformat(next(f.split(":", 1)[1] for f in filters if f.startswith("from-pub-date:")))
        end = date.fromisoformat(next(f.split(":", 1)[1] for f in filters if f.startswith("until-pub-date:")))
        if len(issns) > 1 or start < end:
            return work_list([member(issns=issns)], total=3)
        return work_list([member(f"10.1234/{issns[0]}-{start.day}", issns=issns)])
    http = HTTP(respond)
    units = retrieve_crossref_manifests(http.client, (A, B), DAY, DAY + timedelta(days=3))
    assert all(u.complete and len(u.members) == 4 for u in units)
    queries = [dict(r.url.params) for r in http.requests]
    assert queries[0]["filter"].startswith(f"issn:{A},issn:{B},")
    assert queries[1]["filter"] == f"issn:{A},from-pub-date:2026-01-01,until-pub-date:2026-01-04"
    assert queries[2]["filter"] == f"issn:{A},from-pub-date:2026-01-01,until-pub-date:2026-01-02"
    leaf_dates = [q["filter"].split(",")[-2:] for q in queries if q["filter"].split(",")[-2].split(":")[1] == q["filter"].split(",")[-1].split(":")[1]]
    assert len(leaf_dates) == 8
    assert not any("cursor" in q for q in queries)


def test_single_day_cursor_preserves_full_query_latest_cursor_dedup_and_count(monkeypatch):
    monkeypatch.setattr(crossref, "CROSSREF_PAGE_SIZE", 2)
    def respond(r):
        cursor = r.url.params.get("cursor")
        if cursor is None or cursor == "*":
            return work_list([member("10.1234/a"), member("10.1234/b")], total=4, cursor="A / cursor")
        if cursor == "A / cursor":
            return work_list([member("10.1234/b"), member("10.1234/c")], total=4, cursor="B / cursor")
        assert cursor == "B / cursor"
        return work_list([member("10.1234/d")], total=4)
    http = HTTP(respond)
    unit, = retrieve_crossref_manifests(http.client, (A,), DAY, DAY)
    assert unit.complete and [m.doi for m in unit.members] == [f"10.1234/{x}" for x in "abcd"]
    queries = [dict(r.url.params) for r in http.requests]
    assert [q.get("cursor") for q in queries] == [None, "*", "A / cursor", "B / cursor"]
    base = {k: v for k, v in queries[0].items() if k != "cursor"}
    assert all({k: v for k, v in q.items() if k != "cursor"} == base for q in queries)
    assert base["select"] == "DOI,ISSN,indexed" and base["rows"] == "2"
    assert base["filter"] == f"issn:{A},from-pub-date:2026-01-01,until-pub-date:2026-01-01"


@pytest.mark.parametrize("cursor", [None, "", 42, "*"])
def test_invalid_or_repeated_cursor_is_bounded_and_incomplete(monkeypatch, cursor):
    monkeypatch.setattr(crossref, "CROSSREF_PAGE_SIZE", 1)
    http = HTTP(lambda r: work_list([member()], total=2, cursor=cursor))
    unit, = retrieve_crossref_manifests(http.client, (A,), DAY, DAY)
    assert not unit.complete and len(unit.members) == 1
    assert len(http.requests) == 2


@pytest.mark.parametrize("bad", [
    {"DOI": None, "ISSN": [A], "indexed": {"date-time": REV}},
    {"DOI": "not-a-doi", "ISSN": [A], "indexed": {"date-time": REV}},
    {"DOI": "10.1234/a", "ISSN": ["bad"], "indexed": {"date-time": REV}},
    {"DOI": "10.1234/a", "ISSN": [B], "indexed": {"date-time": REV}},
    {"DOI": "10.1234/a", "ISSN": [A], "indexed": {"date-time": "2026-01-02T00:00:00"}},
    {"DOI": "10.1234/a", "ISSN": [A]},
    None,
])
def test_malformed_manifest_member_never_yields_complete(bad):
    http = HTTP(lambda r: work_list([bad]))
    unit, = retrieve_crossref_manifests(http.client, (A,), DAY, DAY)
    assert not unit.complete and unit.issues
    assert len(http.requests) == 2


@pytest.mark.parametrize("payload", [
    {}, {"status": "bad", "message-type": "work-list"},
    {"status": "ok", "message-type": "work-list", "message": {"items": "bad", "total-results": 0}},
    work_list([], total=True), work_list([], total=-1), work_list([], total="0"),
    work_list([member()], total=2),
])
def test_invalid_envelope_or_count_never_complete(payload):
    http = HTTP(lambda r: payload)
    unit, = retrieve_crossref_manifests(http.client, (A,), DAY, DAY)
    assert not unit.complete


def test_changed_total_during_cursor_is_incomplete(monkeypatch):
    monkeypatch.setattr(crossref, "CROSSREF_PAGE_SIZE", 1)
    http = HTTP(lambda r: work_list([member("10.1234/" + ("a" if r.url.params.get("cursor") != "next" else "b"))],
        total=3 if r.url.params.get("cursor") == "next" else 2))
    unit, = retrieve_crossref_manifests(http.client, (A,), DAY, DAY)
    assert not unit.complete and any("changed" in i for i in unit.issues)


def test_failed_partition_keeps_successful_sibling_evidence():
    def respond(r):
        filters = r.url.params["filter"]
        if f"issn:{B}" in filters:
            return httpx.Response(500)
        if "select" in r.url.params:
            return work_list([member()])
        return work_list([full()])
    http = HTTP(respond)
    result = http.retrieval().discover((JOURNAL,), DAY, DAY)
    assert [u.status for u in result.discovery.coverage] == [CoverageStatus.COMPLETE, CoverageStatus.FAILED]
    assert [r.doi for r in result.discovery.records] == ["10.1234/a"]
    assert len(http.requests) == 11  # three bounded HTTP attempts per failed partition/cursor


def test_manifest_internal_batch_limit():
    # Build real check-digit-valid identities, exceeding the private production bound.
    def issn(n):
        digits = f"{n:07d}"
        check = (-sum(int(d) * w for d, w in zip(digits, range(8, 1, -1)))) % 11
        text = digits + ("X" if check == 10 else str(check))
        return text[:4] + "-" + text[4:]
    identities = tuple(issn(n) for n in range(1, 24))
    http = HTTP(lambda r: work_list())
    units = retrieve_crossref_manifests(http.client, identities, DAY, DAY)
    assert len(units) == 23 and all(u.complete for u in units)
    sizes = [sum(f.startswith("issn:") for f in r.url.params["filter"].split(",")) for r in http.requests]
    assert len(sizes) > 1 and sum(sizes) == 23 and max(sizes) < 23


def test_matching_revision_reuses_only_current_manifest_rows():
    http = HTTP(lambda r: work_list([member(revision="2026-01-02T01:00:00+01:00")]))
    current, history = state(), state("10.1234/history")
    result = http.retrieval((current, history)).discover((JournalConfig(name="Biometrics", issn=(A,)),), DAY, DAY)
    assert result.discovery.records == (current.record,)
    assert result.reused_dois == (current.doi,) and not result.pending_changes
    assert not result.refreshed_dois and not result.new_dois and not http.full_requests
    assert len(http.requests) == 1


@pytest.mark.parametrize("historical", [False, True])
def test_changed_or_new_full_hydration_retains_every_consumed_field(historical):
    http = HTTP(lambda r: work_list([member(issns=(A, B), revision=NEW_REV)]) if "select" in r.url.params
                else work_list([full(issns=(A, B), revision=NEW_REV)]))
    previous = state(issns=(A, B))
    result = http.retrieval((previous,) if historical else ()).discover((JOURNAL,), DAY, DAY)
    assert len(http.full_requests) == 1 and len(result.discovery.records) == len(result.pending_changes) == 1
    record, = result.discovery.records
    assert record.title and record.journal and record.abstract and record.authors and record.dates and record.relations
    assert record.issns == (A, B) and record.provenance.retrieved_at == NOW
    assert all(u.status is CoverageStatus.COMPLETE for u in result.discovery.coverage)
    assert all(u.records == (record,) for u in result.discovery.units)
    assert (result.refreshed_dois if historical else result.new_dois) == (record.doi,)
    pending, = result.pending_changes
    assert pending.indexed_at != previous.indexed_at and pending.retrieved_at == NOW
    assert pending.semantic_hash == previous.semantic_hash == crossref_semantic_hash(record)
    assert "select" not in http.full_requests[0].url.params


@pytest.mark.parametrize("revision", [None, "bad", "2026-01-02T00:00:00"])
def test_unusable_manifest_revision_never_reuses_and_keeps_coverage_conservative(revision):
    http = HTTP(lambda r: work_list([member(revision=revision)]) if "select" in r.url.params else work_list([full()]))
    result = http.retrieval((state(),)).discover((JournalConfig(name="Biometrics", issn=(A,)),), DAY, DAY)
    assert not result.reused_dois and result.refreshed_dois == ("10.1234/a",)
    assert len(http.full_requests) == 1 and len(result.pending_changes) == 1
    assert result.discovery.coverage[0].status is CoverageStatus.PARTIAL


def test_full_record_without_revision_is_transient_and_not_pending():
    http = HTTP(lambda r: work_list([member()]) if "select" in r.url.params else work_list([full(revision=None)]))
    result = http.retrieval().discover((JournalConfig(name="Biometrics", issn=(A,)),), DAY, DAY)
    assert len(result.discovery.records) == 1 and not result.pending_changes


@pytest.mark.parametrize("full_issns", [(B,), ("1234-5679",), ()])
def test_wrong_queried_issn_or_venue_cannot_yield_complete(full_issns):
    http = HTTP(lambda r: work_list([member()]) if "select" in r.url.params else work_list([full(issns=full_issns)]))
    result = http.retrieval().discover((JOURNAL,), DAY, DAY)
    assert result.discovery.coverage[0].status is CoverageStatus.FAILED
    assert not result.discovery.records and not result.pending_changes
    assert any(i.stage == "venue_validation" for i in result.discovery.issues)


@pytest.mark.parametrize("usable_peer", [False, True])
def test_confirmed_members_with_hydration_failure_are_partial_or_failed(usable_peer):
    def respond(r):
        filters = r.url.params["filter"]
        if "select" in r.url.params:
            return work_list([member(), member("10.1234/b")] if usable_peer else [member()])
        if "doi:10.1234/b" in filters:
            return work_list([full("10.1234/b")])
        return httpx.Response(500)
    http = HTTP(respond)
    result = http.retrieval().discover((JournalConfig(name="Biometrics", issn=(A,)),), DAY, DAY)
    assert result.discovery.coverage[0].status is (CoverageStatus.PARTIAL if usable_peer else CoverageStatus.FAILED)
    assert "10.1234/a" not in result.new_dois


def test_zero_member_live_manifest_complete_ignores_history():
    http = HTTP(lambda r: work_list())
    result = http.retrieval((state(),)).discover((JOURNAL,), DAY, DAY)
    assert not result.discovery.records and not http.full_requests
    assert all(u.status is CoverageStatus.COMPLETE for u in result.discovery.coverage)


def test_full_batch_failure_splits_and_keeps_successful_sibling():
    def respond(r):
        if "select" in r.url.params:
            return work_list([member(), member("10.1234/b")])
        if "," in r.url.params["filter"] or r.url.params["filter"] == "doi:10.1234/a":
            return httpx.Response(500)
        return work_list([full("10.1234/b")])
    http = HTTP(respond)
    result = http.retrieval().discover((JournalConfig(name="Biometrics", issn=(A,)),), DAY, DAY)
    assert [r.doi for r in result.discovery.records] == ["10.1234/b"]
    assert result.discovery.coverage[0].status is CoverageStatus.PARTIAL


@pytest.mark.parametrize("historical", [False, True])
def test_doi_probe_batches_duplicates_and_current_anchors(historical):
    http = HTTP(lambda r: work_list([member(), member("10.1234/b")]) if "select" in r.url.params
                else work_list([full(), full("10.1234/b")]))
    result = http.retrieval((state(), state("10.1234/b")) if historical else ()).supplement(
        (oa(), oa(work_id="W2"), oa("10.1234/b", "W3")))
    assert http.requests[0].url.params["filter"] == "doi:10.1234/a,doi:10.1234/b"
    assert len(http.full_requests) == (0 if historical else 1)
    assert len(result.units) == len(result.records) == 2
    assert (result.reused_dois if historical else result.new_dois) == ("10.1234/a", "10.1234/b")
    assert len(result.evidence[0].supplements) == 2
    assert all(ref.provider == "openalex" for e in result.evidence for ref in e.supplements)
    assert all(u.status is CoverageStatus.COMPLETE for u in result.coverage)


def test_changed_supplement_refresh_failure_never_uses_stale_state():
    http = HTTP(lambda r: work_list([member(revision=NEW_REV)]) if "select" in r.url.params else httpx.Response(500))
    result = http.retrieval((state(),)).supplement((oa(),))
    assert not result.records and not result.pending_changes and not result.reused_dois
    assert result.coverage[0].status is CoverageStatus.FAILED


@pytest.mark.parametrize("code", [200, 404])
@pytest.mark.parametrize("historical", [False, True])
def test_absent_probe_singleton_once_200_or_authoritative_404(code, historical):
    http = HTTP(lambda r: work_list() if r.url.path == "/v1/works" else httpx.Response(code, json=singleton(full())))
    result = http.retrieval((state(),) if historical else ()).supplement((oa(), oa(work_id="W2")))
    assert len(http.requests) == 2 and not http.full_requests
    assert result.coverage[0].doi == "10.1234/a"
    assert result.coverage[0].status is (CoverageStatus.COMPLETE if code == 200 else CoverageStatus.UNAVAILABLE)
    if code == 404:
        assert not result.records and not result.pending_changes and not result.reused_dois
    elif historical:
        assert result.reused_dois == ("10.1234/a",)
    else:
        assert result.new_dois == ("10.1234/a",) and len(result.pending_changes) == 1


@pytest.mark.parametrize("code", [301, 308])
@pytest.mark.parametrize("revision", [REV, NEW_REV])
def test_alias_prime_live_probe_reuses_or_refreshes_preserves_requested_coverage(code, revision):
    prime = "10.1234/prime"
    def respond(r):
        if r.url.path != "/v1/works":
            return httpx.Response(code, headers={"Location": f"https://api.crossref.org/works/{prime}"})
        if r.url.params["filter"] == "doi:10.1234/alias":
            return work_list()
        assert r.url.params["filter"] == f"doi:{prime}"
        return work_list([member(prime, revision=revision)]) if "select" in r.url.params else work_list([full(prime, revision=revision)])
    http = HTTP(respond)
    result = http.retrieval((state(prime),)).supplement((oa("10.1234/alias"),))
    assert len(http.requests) == (3 if revision == REV else 4)
    assert result.coverage[0].doi == "10.1234/alias" and result.coverage[0].status is CoverageStatus.COMPLETE
    assert result.records[0].doi == result.evidence[0].provenance.record_id == prime
    assert result.evidence[0].supplements[0].record_id == "https://openalex.org/W1"
    assert (result.reused_dois if revision == REV else result.refreshed_dois) == (prime,)
    if revision != REV:
        assert result.pending_changes[0].doi == prime
        assert "alias" not in repr(result.pending_changes)
    assert all(r.url.path != f"/works/{prime}" for r in http.requests)


@pytest.mark.parametrize("failure", ["bad_location", "prime_404", "refresh_failure"])
def test_alias_failure_is_failed_never_unavailable_or_stale(failure):
    def respond(r):
        if r.url.path.endswith("alias"):
            return httpx.Response(308, headers={"Location": "https://evil.example/works/10.1234/prime" if failure == "bad_location" else "/works/10.1234/prime"})
        if r.url.path != "/v1/works":
            return httpx.Response(404)
        if r.url.params["filter"] == "doi:10.1234/alias" or failure == "prime_404":
            return work_list()
        if "select" in r.url.params:
            return work_list([member("10.1234/prime", revision=NEW_REV)])
        return httpx.Response(500)
    http = HTTP(respond)
    result = http.retrieval((state("10.1234/prime"),)).supplement((oa("10.1234/alias"),))
    assert result.coverage[0].status is CoverageStatus.FAILED
    assert not result.records and not result.pending_changes and not result.reused_dois


def test_alias_prime_absent_probe_can_use_current_singleton_200():
    def respond(r):
        if r.url.path == "/v1/works":
            return work_list()
        if r.url.path.endswith("alias"):
            return httpx.Response(301, headers={"Location": "/works/10.1234/prime"})
        return singleton(full("10.1234/prime"))
    http = HTTP(respond)
    result = http.retrieval().supplement((oa("10.1234/alias"),))
    assert len(http.requests) == 4 and not http.full_requests
    assert result.new_dois == ("10.1234/prime",)
    assert result.coverage[0].status is CoverageStatus.COMPLETE


@pytest.mark.parametrize("historical", [False, True])
def test_successful_discovery_doi_is_not_supplemented(historical):
    http = HTTP(lambda r: work_list([member()]) if "select" in r.url.params else work_list([full()]))
    execution = http.retrieval((state(),) if historical else ())
    discovered = execution.discover((JournalConfig(name="Biometrics", issn=(A,)),), DAY, DAY)
    request_count = len(http.requests)
    classifications = execution._classification.copy()
    revisions = execution._revisions.copy()
    supplemented = execution.supplement((oa(), oa(work_id="W2")))
    assert len(http.requests) == request_count == (1 if historical else 2)
    assert supplemented.units == supplemented.coverage == supplemented.records == supplemented.evidence == ()
    assert not supplemented.issues and not supplemented.pending_changes
    assert not supplemented.reused_dois and not supplemented.refreshed_dois and not supplemented.new_dois
    assert discovered.discovery.records[0].doi == "10.1234/a"
    assert discovered.discovery.coverage[0].status is CoverageStatus.COMPLETE
    assert execution.pending_changes == discovered.pending_changes
    assert execution._classification == classifications and execution._revisions == revisions
    assert not http.client._http_client.is_closed  # caller owns lifetime


@pytest.mark.parametrize("mode", ["new", "refreshed", "reused"])
def test_mixed_openalex_dois_supplement_only_the_discovery_gap(mode):
    gap = "10.1234/b"
    def respond(request):
        if request.url.params["filter"].startswith("issn:"):
            return work_list([member()])
        if "select" in request.url.params:
            assert request.url.params["filter"] == f"doi:{gap}"
            return work_list([member(gap)])
        requested = request.url.params["filter"][4:]
        return work_list([full(requested)])
    history = () if mode == "new" else (state(gap, revision=REV if mode == "reused" else NEW_REV),)
    http = HTTP(respond)
    execution = http.retrieval(history)
    discovered = execution.discover((JournalConfig(name="Biometrics", issn=(A,)),), DAY, DAY)
    supplement_start = len(http.requests)
    supplemented = execution.supplement((oa(), oa(gap, "W2"), oa(gap, "W3")))
    assert [u.doi for u in supplemented.coverage] == [gap]
    assert supplemented.coverage[0].status is CoverageStatus.COMPLETE
    assert [record.doi for record in supplemented.records] == [gap]
    assert getattr(supplemented, f"{mode}_dois") == (gap,)
    assert len(supplemented.evidence[0].supplements) == 2
    assert all(request.url.params["filter"] == f"doi:{gap}" for request in http.requests[supplement_start:])
    assert len(http.requests) - supplement_start == (1 if mode == "reused" else 2)
    assert discovered.discovery.records[0].doi == "10.1234/a"


@pytest.mark.parametrize("issns", [(B,), ()])
def test_venue_rejected_discovery_doi_remains_supplement_eligible(issns):
    http = HTTP(lambda request: work_list([member()]) if "select" in request.url.params
                else work_list([full(issns=issns)]))
    execution = http.retrieval()
    discovered = execution.discover((JournalConfig(name="Biometrics", issn=(A,)),), DAY, DAY)
    assert not discovered.discovery.records and not discovered.pending_changes
    assert discovered.discovery.coverage[0].status is CoverageStatus.FAILED
    supplemented = execution.supplement((oa(),))
    assert len(http.requests) == 3 and len(http.full_requests) == 1
    assert http.requests[-1].url.params["filter"] == "doi:10.1234/a"
    assert supplemented.coverage[0].doi == "10.1234/a"
    assert supplemented.coverage[0].status is CoverageStatus.COMPLETE
    assert supplemented.records[0].issns == issns
    assert supplemented.new_dois == ("10.1234/a",)


def test_duplicate_doi_across_separate_manifest_batches_hydrates_once(monkeypatch):
    monkeypatch.setattr(crossref, "_CROSSREF_MANIFEST_BATCH_SIZE", 1)
    http = HTTP(lambda r: work_list([member(issns=(A, B))]) if "select" in r.url.params else work_list([full(issns=(A, B))]))
    result = http.retrieval().discover((JOURNAL,), DAY, DAY)
    assert len(http.full_requests) == 1 and len(result.pending_changes) == 1
    assert all(u.status is CoverageStatus.COMPLETE for u in result.discovery.coverage)


def test_no_live_openalex_anchor_creates_no_supplement_or_request(tmp_path):
    http = HTTP(lambda r: pytest.fail("history cannot cause requests without current anchors"))
    result = http.retrieval((state(),)).supplement(())
    assert not result.units and not result.records and not result.pending_changes
    assert not (tmp_path / ".literature-monitor" / "provider-state.sqlite3").exists()


def test_supplement_probe_and_full_hydration_are_internally_bounded():
    dois = tuple(f"10.1234/{n}" for n in range(23))
    def respond(r):
        requested = [f[4:] for f in r.url.params["filter"].split(",")]
        factory = member if "select" in r.url.params else full
        return work_list([factory(doi) for doi in requested])
    http = HTTP(respond)
    result = http.retrieval().supplement(tuple(oa(doi, f"W{i + 1}") for i, doi in enumerate(dois)))
    assert len(result.records) == len(result.pending_changes) == 23
    assert len(http.full_requests) > 1
    assert all(len(r.url.params["filter"].split(",")) < 23 for r in http.requests)


def test_pending_changes_are_only_returned_in_memory_and_crossref_serialization_stays_stable(tmp_path, monkeypatch):
    import literature_monitor.application.provider_state as provider_state
    monkeypatch.chdir(tmp_path)
    http = HTTP(lambda r: work_list([member()]) if "select" in r.url.params else work_list([full()]))
    result = http.retrieval().discover((JournalConfig(name="Biometrics", issn=(A,)),), DAY, DAY)
    assert len(result.pending_changes) == 1 and not list(tmp_path.iterdir())
    assert provider_state.SCHEMA_VERSION == 3
    assert provider_state.CROSSREF_SERIALIZATION_VERSION == 1


def test_conflicting_duplicate_manifest_revisions_require_live_hydration_and_partial(monkeypatch):
    monkeypatch.setattr(crossref, "_CROSSREF_MANIFEST_BATCH_SIZE", 1)
    def respond(r):
        if "select" in r.url.params:
            revision = REV if f"issn:{A}" in r.url.params["filter"] else NEW_REV
            return work_list([member(issns=(A, B), revision=revision)])
        return work_list([full(issns=(A, B), revision=NEW_REV)])
    http = HTTP(respond)
    result = http.retrieval((state(issns=(A, B)),)).discover((JOURNAL,), DAY, DAY)
    assert len(http.full_requests) == 1 and not result.reused_dois
    assert all(u.status is CoverageStatus.PARTIAL for u in result.discovery.coverage)


def test_missing_total_preserves_safe_live_members_but_cannot_claim_complete():
    def respond(r):
        if "select" not in r.url.params:
            return work_list([full()])
        payload = work_list([member()])
        del payload["message"]["total-results"]
        return payload
    http = HTTP(respond)
    result = http.retrieval().discover((JournalConfig(name="Biometrics", issn=(A,)),), DAY, DAY)
    assert len(result.discovery.records) == 1
    assert result.discovery.coverage[0].status is CoverageStatus.PARTIAL


def test_current_full_hydration_rejects_unrequested_doi_and_conflicting_records():
    http = HTTP(lambda r: work_list([member()]) if "select" in r.url.params else work_list([
        full("10.1234/unrequested"), full(), {**full(), "title": ["Conflicting"]}]))
    result = http.retrieval().discover((JournalConfig(name="Biometrics", issn=(A,)),), DAY, DAY)
    assert not result.discovery.records and not result.pending_changes
    assert result.discovery.coverage[0].status is CoverageStatus.FAILED


def test_multiple_aliases_share_prime_probe_hydration_pending_and_anchors():
    def respond(r):
        if r.url.path != "/v1/works":
            return httpx.Response(301, headers={"Location": "/works/10.1234/prime"})
        if "doi:10.1234/alias" in r.url.params["filter"]:
            return work_list()
        return work_list([member("10.1234/prime")]) if "select" in r.url.params else work_list([full("10.1234/prime")])
    http = HTTP(respond)
    execution = http.retrieval()
    result = execution.supplement((oa("10.1234/alias1"), oa("10.1234/alias2", "W2")))
    assert len(http.requests) == 5 and len(http.full_requests) == 1
    assert len(result.pending_changes) == len(execution.pending_changes) == len(result.records) == 1
    assert len(result.evidence[0].supplements) == 2
    assert [u.doi for u in result.coverage] == ["10.1234/alias1", "10.1234/alias2"]


def test_changed_exact_doi_probe_refreshes_even_when_semantic_hash_unchanged():
    http = HTTP(lambda r: work_list([member(revision=NEW_REV)]) if "select" in r.url.params else work_list([full(revision=NEW_REV)]))
    previous = state()
    result = http.retrieval((previous,)).supplement((oa(),))
    pending, = result.pending_changes
    assert len(http.full_requests) == 1 and result.refreshed_dois == ("10.1234/a",)
    assert pending.semantic_hash == previous.semantic_hash and pending.indexed_at != previous.indexed_at
    assert pending.retrieved_at == NOW and result.coverage[0].status is CoverageStatus.COMPLETE


def test_unusable_doi_probe_revision_never_reuses_state():
    http = HTTP(lambda r: work_list([member(revision="bad")]) if "select" in r.url.params else work_list([full()]))
    result = http.retrieval((state(),)).supplement((oa(),))
    assert len(http.full_requests) == 1 and not result.reused_dois
    assert result.refreshed_dois == ("10.1234/a",) and len(result.pending_changes) == 1


def test_unrequested_probe_identity_requires_exact_singleton_fallback():
    http = HTTP(lambda r: work_list([member("10.1234/wrong")]) if r.url.path == "/v1/works" else singleton(full()))
    result = http.retrieval().supplement((oa(),))
    assert len(http.requests) == 2 and result.records[0].doi == "10.1234/a"


def test_singleton_200_with_wrong_identity_cannot_use_historical_state():
    http = HTTP(lambda r: work_list() if r.url.path == "/v1/works" else singleton(full("10.1234/wrong")))
    result = http.retrieval((state(),)).supplement((oa(),))
    assert result.coverage[0].status is CoverageStatus.FAILED and not result.records


def test_repeated_revision_observation_change_never_rehydrates_or_reuses_stale_record():
    seen = 0
    def respond(r):
        nonlocal seen
        if r.url.path != "/v1/works":
            return httpx.Response(301, headers={"Location": "/works/10.1234/a"})
        if r.url.params["filter"] == "doi:10.1234/alias":
            return work_list()
        if "select" in r.url.params:
            seen += 1
            return work_list([member(revision=REV if seen == 1 else NEW_REV)])
        return work_list([full()])
    http = HTTP(respond)
    execution = http.retrieval()
    execution.discover((JournalConfig(name="Biometrics", issn=(A,)),), DAY, DAY)
    # An undiscovered alias still requires a current live probe of its discovered prime.
    result = execution.supplement((oa("10.1234/alias"),))
    assert len(http.full_requests) == 1 and not result.records
    assert result.coverage[0].status is CoverageStatus.FAILED and not execution.pending_changes


def test_terminal_manifest_authorization_failure_does_not_split(monkeypatch):
    http = HTTP(lambda r: httpx.Response(403))
    units = retrieve_crossref_manifests(http.client, (A, B), DAY, DAY + timedelta(days=30))
    assert len(http.requests) == 1 and all(not unit.complete for unit in units)


def test_alias_singleton_full_record_can_revision_match_prime_state():
    def respond(r):
        if r.url.path == "/v1/works":
            return work_list()
        if r.url.path.endswith("alias"):
            return httpx.Response(308, headers={"Location": "/works/10.1234/prime"})
        return singleton(full("10.1234/prime"))
    http = HTTP(respond)
    previous = state("10.1234/prime")
    result = http.retrieval((previous,)).supplement((oa("10.1234/alias"),))
    assert result.records == (previous.record,) and result.reused_dois == (previous.doi,)
    assert len(http.requests) == 4 and not http.full_requests


def test_malformed_batched_probe_falls_back_and_preserves_successful_supplement():
    def respond(r):
        if r.url.path == "/v1/works":
            return {"status": "bad"}
        return httpx.Response(404) if r.url.path.endswith("b") else singleton(full())
    http = HTTP(respond)
    result = http.retrieval().supplement((oa(), oa("10.1234/b", "W2")))
    assert [u.status for u in result.coverage] == [CoverageStatus.COMPLETE, CoverageStatus.UNAVAILABLE]
    assert result.new_dois == ("10.1234/a",) and len(http.requests) == 3


def test_cursor_with_new_tokens_but_no_doi_progress_is_bounded(monkeypatch):
    monkeypatch.setattr(crossref, "CROSSREF_PAGE_SIZE", 1)
    http = HTTP(lambda r: work_list([member()], total=2, cursor=f"token-{len(http.requests)}"))
    unit, = retrieve_crossref_manifests(http.client, (A,), DAY, DAY)
    assert not unit.complete and len(http.requests) == 3
    assert any("no unique DOI progress" in message for message in unit.issues)


def test_full_hydration_authorization_failure_does_not_split():
    http = HTTP(lambda r: work_list([member(), member("10.1234/b")]) if "select" in r.url.params else httpx.Response(403))
    result = http.retrieval().supplement((oa(), oa("10.1234/b", "W2")))
    assert len(http.full_requests) == 1
    assert all(unit.status is CoverageStatus.FAILED for unit in result.coverage)


@pytest.mark.parametrize("historical", [False, True])
def test_hydrated_revision_advancement_survives_later_matching_probe(historical):
    def respond(request):
        if request.url.path != "/v1/works":
            return httpx.Response(308, headers={"Location": "/works/10.1234/a"})
        if request.url.params["filter"] == "doi:10.1234/alias":
            return work_list()
        if "select" not in request.url.params:
            return work_list([full(revision=NEW_REV)])
        if request.url.params["filter"].startswith("issn:"):
            return work_list([member(revision=REV)])
        return work_list([member(revision=NEW_REV)])
    http = HTTP(respond)
    previous = state(revision="2026-01-01T00:00:00Z")
    execution = http.retrieval((previous,) if historical else ())
    discovered = execution.discover((JournalConfig(name="Biometrics", issn=(A,)),), DAY, DAY)
    current, = discovered.discovery.records
    pending, = discovered.pending_changes
    assert current.indexed_at == pending.indexed_at == datetime(2026, 1, 3, tzinfo=timezone.utc)
    assert discovered.discovery.coverage[0].status is CoverageStatus.COMPLETE

    # Successful discovery excludes the exact DOI, but an alias gap still probes prime B.
    supplemented = execution.supplement((oa("10.1234/alias"), oa("10.1234/alias", "W2")))
    assert supplemented.records == (current,)
    assert supplemented.coverage[0].status is CoverageStatus.COMPLETE
    assert supplemented.pending_changes == execution.pending_changes == (pending,)
    expected = ("10.1234/a",)
    assert (supplemented.refreshed_dois if historical else supplemented.new_dois) == expected
    assert (discovered.refreshed_dois if historical else discovered.new_dois) == expected
    assert not supplemented.reused_dois and not supplemented.issues
    assert supplemented.coverage[0].doi == "10.1234/alias"
    assert len(http.full_requests) == 1 and len(http.requests) == 5
    if historical:
        assert current != previous.record and pending.indexed_at != previous.indexed_at


@pytest.mark.parametrize("history", ["none", "stale", "matches_singleton"])
def test_singleton_success_replaces_cached_discovery_hydration_failure(history):
    # A matching historical singleton revision must not override freshly supplied evidence.
    live_revision = NEW_REV if history == "matches_singleton" else REV
    singleton_revision = REV
    def respond(request):
        if request.url.path != "/v1/works":
            return singleton(full(revision=singleton_revision))
        if "select" not in request.url.params:
            return httpx.Response(500)
        if request.url.params["filter"].startswith("issn:"):
            return work_list([member(revision=live_revision)])
        return {"status": "bad"}  # forces a singleton current full record
    previous = state(revision="2026-01-01T00:00:00Z" if history == "stale" else REV)
    http = HTTP(respond)
    execution = http.retrieval(()) if history == "none" else http.retrieval((previous,))
    discovered = execution.discover((JournalConfig(name="Biometrics", issn=(A,)),), DAY, DAY)
    assert discovered.discovery.coverage[0].status is CoverageStatus.FAILED
    assert not discovered.discovery.records and not discovered.pending_changes
    failed_attempts = len(http.full_requests)
    assert failed_attempts == 3  # existing bounded transport retry policy

    supplemented = execution.supplement((oa(), oa(work_id="W2")))
    current, = supplemented.records
    pending, = supplemented.pending_changes
    assert supplemented.coverage[0].status is CoverageStatus.COMPLETE
    assert current.indexed_at == pending.indexed_at == datetime(2026, 1, 2, tzinfo=timezone.utc)
    assert current.provenance.retrieved_at == pending.retrieved_at == NOW
    assert execution.pending_changes == (pending,)
    assert (supplemented.new_dois if history == "none" else supplemented.refreshed_dois) == (current.doi,)
    assert not supplemented.reused_dois
    assert len(http.full_requests) == failed_attempts
    assert sum(request.url.path != "/v1/works" for request in http.requests) == 1
    if history != "none":
        assert current != previous.record  # current singleton provenance, never stale state


def test_current_singleton_supersedes_older_successful_execution_revision():
    probe_count = 0
    def respond(request):
        nonlocal probe_count
        if request.url.path != "/v1/works":
            return singleton(full(revision=NEW_REV))
        probe_count += 1
        if probe_count == 1:
            return work_list([member()])
        return work_list()
    previous = state()
    http = HTTP(respond)
    execution = http.retrieval((previous,))
    initial = execution.supplement((oa(),))
    assert initial.reused_dois == (previous.doi,)
    supplemented = execution.supplement((oa(),))
    assert supplemented.coverage[0].status is CoverageStatus.COMPLETE
    assert supplemented.refreshed_dois == (previous.doi,) and not supplemented.reused_dois
    assert supplemented.records[0].indexed_at == datetime(2026, 1, 3, tzinfo=timezone.utc)
    assert supplemented.pending_changes == execution.pending_changes
    assert not http.full_requests  # singleton already supplied all current metadata


def test_supplied_recovery_without_revision_remains_transient():
    def respond(request):
        if request.url.path != "/v1/works":
            return singleton(full(revision=None))
        if "select" not in request.url.params:
            return httpx.Response(500)
        if request.url.params["filter"].startswith("issn:"):
            return work_list([member()])
        return work_list()
    http = HTTP(respond)
    execution = http.retrieval((state(revision="2026-01-01T00:00:00Z"),))
    execution.discover((JournalConfig(name="Biometrics", issn=(A,)),), DAY, DAY)
    supplemented = execution.supplement((oa(),))
    assert supplemented.coverage[0].status is CoverageStatus.COMPLETE
    assert supplemented.records[0].indexed_at is None
    assert not supplemented.pending_changes and not execution.pending_changes
    assert supplemented.refreshed_dois == ("10.1234/a",)
    assert len(http.full_requests) == 3
