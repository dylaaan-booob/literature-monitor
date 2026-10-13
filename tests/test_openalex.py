import json
import os
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import ValidationError

import literature_monitor.openalex as openalex_module
from literature_monitor.canonicalize import canonicalize_records
from literature_monitor.config import JournalConfig
from literature_monitor.coverage import CoverageComponent, CoverageStatus
from literature_monitor.models import (
    CanonicalMetadata,
)
from literature_monitor.openalex import (
    IssueSeverity,
    OpenAlexClient,
    OpenAlexRequestError,
)
from literature_monitor.progress import ActivityKind, ActivityUpdate, ProgressEvent


FIXTURES = Path(__file__).parent / "fixtures" / "openalex"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class SequenceTransport(httpx.MockTransport):
    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[httpx.Request] = []
        self.closed = False
        super().__init__(self._respond)

    def _respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.outcomes:
            raise AssertionError(f"unexpected HTTP request: {request.url}")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, httpx.Response):
            return outcome
        return httpx.Response(200, json=outcome)

    def close(self) -> None:
        self.closed = True
        super().close()

def http_error(code: int) -> httpx.Response:
    return httpx.Response(code)


def make_client(*outcomes: dict[str, Any] | httpx.Response | Exception) -> tuple[OpenAlexClient, SequenceTransport]:
    transport = SequenceTransport(*outcomes)
    return OpenAlexClient(transport=transport, sleep=lambda _: None), transport


def test_source_fields_add_only_publisher_access_presentation_metadata() -> None:
    assert openalex_module.SOURCE_FIELDS.split(",") == [
        "id",
        "display_name",
        "issn_l",
        "issn",
        "type",
        "alternate_titles",
        "abbreviated_title",
        "host_organization",
        "host_organization_name",
        "homepage_url",
    ]
    assert "host_organization_lineage" not in openalex_module.SOURCE_FIELDS


def test_retry_backoff_is_injected_and_never_really_sleeps() -> None:
    delays: list[float] = []
    transport = SequenceTransport(http_error(429), http_error(500), fixture("source_biometrics.json"))
    client = OpenAlexClient(transport=transport, sleep=delays.append)

    payload = client.get_source_by_issn("0006-341X")

    assert payload["id"] == "https://openalex.org/S8265502"
    assert delays == [1, 2]
    assert len(transport.requests) == 3


def test_timeout_retries_use_injected_backoff_without_real_sleep() -> None:
    delays: list[float] = []
    transport = SequenceTransport(
        httpx.ReadTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
        fixture("source_biometrics.json"),
    )
    client = OpenAlexClient(transport=transport, sleep=delays.append)

    payload = client.get_source_by_issn("0006-341X")

    assert payload["id"] == "https://openalex.org/S8265502"
    assert delays == [1, 2]
    assert len(transport.requests) == 3


def test_connection_retries_use_injected_backoff_without_real_sleep() -> None:
    delays: list[float] = []
    transport = SequenceTransport(
        httpx.ConnectError("connection refused"),
        httpx.ConnectError("connection refused"),
        fixture("source_biometrics.json"),
    )
    client = OpenAlexClient(transport=transport, sleep=delays.append)

    payload = client.get_source_by_issn("0006-341X")

    assert payload["id"] == "https://openalex.org/S8265502"
    assert delays == [1, 2]
    assert len(transport.requests) == 3


@pytest.mark.parametrize(
    "transient_failure",
    (http_error(429), http_error(500), httpx.ReadTimeout("timed out")),
)
def test_request_progress_reports_retry_before_backoff_and_success(
    transient_failure: httpx.Response | Exception,
) -> None:
    trace: list[tuple[str, object]] = []
    transport = SequenceTransport(transient_failure, fixture("source_biometrics.json"))

    def report(event: ProgressEvent) -> None:
        assert event.activity is not None
        trace.append(("activity", event.activity))

    def sleep(delay: float) -> None:
        trace.append(("sleep", delay))

    client = OpenAlexClient(transport=transport, sleep=sleep)
    activity = ActivityUpdate(
        kind=ActivityKind.WORKING,
        source="openalex",
        operation="source_resolution:0",
        label="Resolving OpenAlex journal source",
        detail="Biometrics · ISSN 0006-341X",
        current=0,
        total=1,
        unit="issn",
    )

    payload = client.get_source_by_issn(
        "0006-341X",
        progress_callback=report,
        activity=activity,
    )

    assert payload["id"] == "https://openalex.org/S8265502"
    assert len(transport.requests) == 2
    assert [kind for kind, _ in trace] == [
        "activity",
        "activity",
        "sleep",
        "activity",
        "activity",
    ]
    request, retry, (_sleep_kind, delay), request_again, response = trace
    assert request[1].label == "Requesting OpenAlex"
    assert retry[1].kind is ActivityKind.RETRYING
    assert delay == 1
    assert request_again[1].label == "Requesting OpenAlex"
    assert response[1].label == "Received OpenAlex response"
    identities = {
        (item.source, item.operation, item.unit, item.current, item.total)
        for kind, item in trace
        if kind == "activity"
    }
    assert identities == {("openalex", "source_resolution:0", "issn", 0, 1)}


@pytest.mark.parametrize("status_code", [400, 401, 403, 422])
def test_non_retryable_client_error_is_not_retried(status_code: int) -> None:
    client, transport = make_client(http_error(status_code))

    with pytest.raises(OpenAlexRequestError, match=f"HTTP {status_code}"):
        client.get_source_by_issn("0006-341X")

    assert len(transport.requests) == 1


@pytest.mark.parametrize("locations", [
    None, {"not": "a list"},
    [{"id": "doi:10.5555/other", "version": "publishedVersion"},
     {"id": "pmh:oai:arXiv.org:2601.01234", "version": "submittedVersion"},
     {"id": "repository:item-1", "version": "acceptedVersion"}],
    ["malformed", {"id": None, "version": "unknownVersion"}],
])
def test_discovery_ignores_locations_and_emits_ordinary_metadata(locations) -> None:
    work = a4_work(doi="https://doi.org/10.5555/FINAL")
    work["keywords"] = [{"display_name": "Generated keyword"}]
    work["topics"] = [{"display_name": "Generated topic"}]
    work["locations"] = locations
    work["primary_location"]["is_published"] = True
    result = partial_discovery([work])
    record, = result.records
    assert not result.issues
    assert record.external_ids.doi == "10.5555/final"
    assert record.external_ids.arxiv is None
    assert record.metadata.author_keywords == ()
    assert record.source_id == "https://openalex.org/S8265502"
    assert "version_hints" not in record.model_dump()
    evidence = record.to_evidence()
    assert evidence.provenance == record.provenance
    assert evidence.title == record.metadata.title
    assert evidence.journal == record.metadata.journal
    assert evidence.publication_date == record.metadata.publication_date
    assert evidence.abstract == record.metadata.abstract
    assert evidence.author_keywords == record.metadata.author_keywords
    assert evidence.authors == record.authors
    assert evidence.external_ids == record.external_ids
    assert "version_hints" not in evidence.model_dump()


@pytest.mark.parametrize("unexpected_failure", [False, True])
def test_client_reuses_one_http_session_and_closes_it(
    monkeypatch: pytest.MonkeyPatch,
    unexpected_failure: bool,
) -> None:
    transport = SequenceTransport(
        fixture("source_biometrics.json"), fixture("source_biometrics.json"),
    )
    sessions: list[httpx.Client] = []
    real_client = httpx.Client

    def create_session(**kwargs: Any) -> httpx.Client:
        session = real_client(**kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr(httpx, "Client", create_session)

    def execute() -> None:
        with OpenAlexClient(transport=transport) as client:
            client.get_source_by_issn("0006-341X")
            client.get_source_by_issn("1541-0420")
            if unexpected_failure:
                raise RuntimeError("downstream failure")

    if unexpected_failure:
        with pytest.raises(RuntimeError, match="downstream failure"):
            execute()
    else:
        execute()

    assert len(sessions) == 1
    assert sessions[0].is_closed
    assert transport.closed
    assert len(transport.requests) == 2
    assert all(request.method == "GET" for request in transport.requests)


def set_proxy_environment(monkeypatch: pytest.MonkeyPatch, no_proxy: str) -> None:
    for name in tuple(os.environ):
        if name.lower().endswith("_proxy"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("NO_PROXY", no_proxy)


def test_client_constructs_and_closes_with_unparseable_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_proxy_environment(monkeypatch, "fc00::/7,fe80::/10")
    requests: list[httpx.Request] = []

    def unexpected_request(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("construction must not send a request")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", unexpected_request)
    with OpenAlexClient() as client:
        assert not client._http_client.trust_env
        assert not client._http_client.is_closed

    assert client._http_client.is_closed
    assert requests == []
    assert os.environ["NO_PROXY"] == "fc00::/7,fe80::/10"


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_default_client_uses_valid_environment_proxy(
    monkeypatch: pytest.MonkeyPatch, scheme: str,
) -> None:
    set_proxy_environment(monkeypatch, "localhost,127.0.0.1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9998")
    proxies: dict[httpx.HTTPTransport, httpx.Proxy | None] = {}
    used_proxies: list[httpx.Proxy | None] = []
    real_init = httpx.HTTPTransport.__init__

    def record_transport(self: httpx.HTTPTransport, **kwargs: Any) -> None:
        proxies[self] = kwargs.get("proxy")
        real_init(self, **kwargs)

    def respond(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        used_proxies.append(proxies[self])
        return httpx.Response(200, json=fixture("source_biometrics.json"))

    monkeypatch.setattr(httpx.HTTPTransport, "__init__", record_transport)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", respond)
    with OpenAlexClient(base_url=f"{scheme}://provider.example") as client:
        assert client._http_client.trust_env
        assert client.get_source_by_issn("0006-341X") == fixture("source_biometrics.json")

    assert len(used_proxies) == 1
    assert used_proxies[0] is not None
    assert used_proxies[0].url == httpx.URL(os.environ[f"{scheme.upper()}_PROXY"])
    assert client._http_client.is_closed


def test_mock_transport_works_with_unparseable_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_proxy_environment(monkeypatch, "fc00::/7,fe80::/10")
    payload = fixture("source_biometrics.json")
    transport = SequenceTransport(payload)
    with OpenAlexClient(transport=transport) as client:
        assert client.get_source_by_issn("0006-341X") == payload

    assert len(transport.requests) == 1
    assert transport.closed
    assert client._http_client.is_closed


@pytest.mark.parametrize("error, explicit_transport", [
    (ValueError("invalid configuration"), False),
    (RuntimeError("programming error"), False),
    (httpx.InvalidURL("explicit transport error"), True),
])
def test_client_does_not_fallback_for_unrelated_construction_errors(
    monkeypatch: pytest.MonkeyPatch, error: Exception, explicit_transport: bool,
) -> None:
    set_proxy_environment(monkeypatch, "localhost")
    calls: list[dict[str, Any]] = []

    def fail_construction(**kwargs: Any) -> httpx.Client:
        calls.append(kwargs)
        raise error

    monkeypatch.setattr(httpx, "Client", fail_construction)
    with pytest.raises(type(error)) as raised:
        OpenAlexClient(transport=SequenceTransport() if explicit_transport else None)

    assert raised.value is error
    assert len(calls) == 1


def test_default_record_serialization_contains_ordinary_provider_metadata() -> None:
    source = openalex_module.ResolvedSource(journal="Biometrics", configured_issn_l="0006-341X", openalex_id="https://openalex.org/S8265502", display_name="Biometrics", provider_issn_l="0006-341X", aliases=("0006-341X",))
    record, _ = openalex_module._normalize_work(
        fixture("works_page_1.json")["results"][0], source,
        datetime(2026, 9, 26, tzinfo=timezone.utc),
    )
    expected_fields = {"metadata", "external_ids", "authors", "source_id", "provenance"}
    assert set(record.model_dump()) == expected_fields
    assert set(json.loads(record.model_dump_json())) == expected_fields


def a4_sources(*sources):
    return {"meta": {"count": len(sources)}, "results": list(sources)}


def a4_page(*works, cursor=None, count=None):
    return {"meta": {"count": len(works) if count is None else count, "next_cursor": cursor}, "results": list(works)}


def a4_work(work_id="W1", source_id="S8265502", **updates):
    raw = fixture("works_page_1.json")["results"][0]
    raw.update(id=f"https://openalex.org/{work_id}")
    raw["primary_location"]["source"]["id"] = f"https://openalex.org/{source_id}"
    raw.update(updates)
    return raw


def a4_journals():
    return (JournalConfig(name="Biometrics", issn_l="0006-341X"),
            JournalConfig(name="IEEE Transactions on Cybernetics", issn_l="2168-2267"))


def test_a4_batched_source_projects_publisher_metadata_with_shared_select_contract():
    client, transport = make_client(
        a4_sources(fixture("source_biometrics_publisher.json"))
    )

    with client:
        unit, = openalex_module.resolve_journal_sources_batched(
            client,
            a4_journals()[:1],
        )

    assert unit.source is not None
    assert unit.source.publisher_id == "https://openalex.org/P4310320999"
    assert unit.source.host_organization_name == "Example Academic Publisher"
    assert unit.source.homepage_url == "https://journals.example.org/biometrics"
    assert unit.source.homepage_hostname == "journals.example.org"
    assert transport.requests[0].url.params["select"] == openalex_module.SOURCE_FIELDS


def a4_discover(client, journals=None):
    resolutions = openalex_module.resolve_journal_sources_batched(client, journals or a4_journals())
    return openalex_module.discover_resolved_sources(
        client, resolutions, date(2026, 1, 1), date(2026, 1, 31),
        retrieved_at=datetime(2026, 1, 31, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("size", [1, 2, 100, 101, 205])
def test_a4_source_batches_are_bounded_and_ordered(size):
    journals = tuple(JournalConfig(name=f"Journal {i}", issn_l=checksum_issn(i)) for i in range(size))
    sources = [dict(fixture("source_biometrics.json"), id=f"S{i}", display_name=j.name, issn_l=j.issn_l, issn=[j.issn_l]) for i, j in enumerate(journals)]
    client, transport = make_client(*(a4_sources(*sources[offset:offset + 100]) for offset in range(0, size, 100)))
    with client:
        units = openalex_module.resolve_journal_sources_batched(client, journals)
    assert [unit.journal for unit in units] == list(journals)
    assert all(unit.source and not unit.issues for unit in units)
    assert len(transport.requests) == (size + 99) // 100
    requested = []
    for request in transport.requests:
        params = dict(request.url.params)
        batch = params["filter"].removeprefix("issn:").split("|")
        assert len(batch) <= 100
        assert params["select"] == openalex_module.SOURCE_FIELDS
        requested.extend(batch)
    assert requested == [j.issn_l for j in journals]


@pytest.mark.parametrize("status,requests", [(401, 1), (403, 1), (429, 3)])
def test_a4_terminal_source_failure_opens_execution_circuit(status, requests):
    client, transport = make_client(*(http_error(status) for _ in range(requests)))
    with client:
        units = openalex_module.resolve_journal_sources_batched(client, a4_journals())
        assert all(unit.status is CoverageStatus.FAILED for unit in units)
        for call in (lambda: client.get_source_by_issn("0006-341X"),
                     lambda: list(client.iter_thin_work_pages(("S1",), date(2026, 1, 1), date(2026, 1, 31)))):
            with pytest.raises(OpenAlexRequestError) as raised:
                call()
            assert raised.value.kind is openalex_module.OpenAlexFailureKind.CIRCUIT_OPEN
    assert len(transport.requests) == requests
    assert transport.closed


def test_a4_thin_discovery_fields_mapping_and_searchable_metadata():
    first, second = a4_work(), a4_work("W2", "S4210191041")
    first["locations"] = [{"invalid": "must never be parsed during discovery"}]
    client, transport = make_client(a4_sources(fixture("source_biometrics.json"), fixture("source_cybernetics.json")), a4_page(second, first))
    with client:
        result = a4_discover(client)
    assert [unit.records[0].external_ids.openalex for unit in result.units] == ["https://openalex.org/W1", "https://openalex.org/W2"]
    assert all(unit.coverage.status is CoverageStatus.COMPLETE for unit in result.units)
    assert all("version_hints" not in record.to_evidence().model_dump() for record in result.records)
    assert not result.issues
    params = dict(transport.requests[-1].url.params)
    assert params["filter"] == "primary_location.source.id:S8265502|S4210191041,from_publication_date:2026-01-01,to_publication_date:2026-01-31"
    assert params["select"] == openalex_module.THIN_WORK_FIELDS
    assert "updated_date" not in params["select"].split(",")
    assert "updated_at" not in params["select"].split(",")
    assert "locations" not in params["select"].split(",")
    assert set(params) == {"filter", "select", "per_page", "cursor"}
    source = result.units[0].source
    legacy, _ = openalex_module._normalize_work(first, source, result.records[0].provenance.retrieved_at)
    assert result.records[0].metadata == legacy.metadata
    assert result.records[0].authors == legacy.authors
    assert result.records[0].external_ids == legacy.external_ids


def test_a4_same_source_preserves_both_journal_reporting_units():
    source = fixture("source_biometrics.json")
    source["alternate_titles"] = ["Biometrics Alias"]
    journals = (a4_journals()[0], JournalConfig(name="Biometrics Alias", issn_l="1541-0420"))
    client, transport = make_client(a4_sources(source), a4_page(a4_work()))
    with client:
        result = a4_discover(client, journals)
    assert [unit.coverage.journal for unit in result.units] == [journal.issn_l for journal in journals]
    assert all(unit.records for unit in result.units)
    assert len(transport.requests) == 2


@pytest.mark.parametrize("damage", ["missing_source", "bad_id"])
def test_a4_bad_work_recovers_safely_without_optimization_issue(damage):
    bad = a4_work()
    if damage == "missing_source":
        bad["primary_location"] = None
    else:
        bad["id"] = "bad"
    # Unassignable evidence affects every Source; known malformed works only their Source.
    outcomes = [a4_sources(fixture("source_biometrics.json"), fixture("source_cybernetics.json")), a4_page(bad, a4_work("W2", "S4210191041")), a4_page(a4_work())]
    if damage == "missing_source":
        outcomes.append(a4_page(a4_work("W2", "S4210191041")))
    client, transport = make_client(*outcomes)
    with client:
        result = a4_discover(client)
    assert all(unit.coverage.status is CoverageStatus.COMPLETE for unit in result.units)
    assert not result.issues
    assert len(result.records) == 2
    assert len(transport.requests) == (4 if damage == "missing_source" else 3)


def test_a4_unrecovered_malformed_work_is_partial_only_in_affected_unit():
    bad = a4_work("W3", id="invalid Work identity")
    client, _ = make_client(a4_sources(fixture("source_biometrics.json"), fixture("source_cybernetics.json")),
                            a4_page(a4_work(), bad, a4_work("W2", "S4210191041")), a4_page(a4_work(), bad))
    with client:
        result = a4_discover(client)
    assert [coverage.status for coverage in result.coverage] == [CoverageStatus.PARTIAL, CoverageStatus.COMPLETE]
    assert len(result.units[0].records) == 1


@pytest.mark.parametrize("recovered", [False, True])
def test_a4_pagination_failure_preserves_evidence_and_deduplicates_fallback(recovered):
    outcomes = [a4_sources(fixture("source_biometrics.json"), fixture("source_cybernetics.json")),
                a4_page(a4_work(), cursor="next", count=2), *[http_error(500)] * 3]
    outcomes += [a4_page(a4_work()) if recovered else a4_page(a4_work(), cursor="next", count=2)]
    if not recovered:
        outcomes += [http_error(500)] * 3
    outcomes += [a4_page()]
    client, transport = make_client(*outcomes)
    with client:
        result = a4_discover(client)
    assert len(result.units[0].records) == 1
    assert result.coverage[0].status is (CoverageStatus.COMPLETE if recovered else CoverageStatus.PARTIAL)
    assert result.coverage[1].status is CoverageStatus.COMPLETE
    assert bool(result.issues) is not recovered
    assert transport.requests[2].url.params["cursor"] == "next"


@pytest.mark.parametrize("status,requests", [(401, 1), (429, 3)])
def test_a4_terminal_works_failure_prevents_fallback(status, requests):
    client, transport = make_client(a4_sources(fixture("source_biometrics.json"), fixture("source_cybernetics.json")), *[http_error(status)] * requests)
    with client:
        result = a4_discover(client)
    assert all(coverage.status is CoverageStatus.FAILED for coverage in result.coverage)
    assert len(transport.requests) == 1 + requests


@pytest.mark.parametrize("bad_meta", [{"count": 2, "next_cursor": None}, {"count": None, "next_cursor": None}, {"count": 1, "next_cursor": "*"}])
def test_a4_incomplete_single_source_traversal_is_never_complete(bad_meta):
    page = a4_page(a4_work())
    page["meta"] = bad_meta
    client, _ = make_client(a4_sources(fixture("source_biometrics.json")), page)
    with client:
        result = a4_discover(client, a4_journals()[:1])
    assert result.coverage[0].status is not CoverageStatus.COMPLETE


def test_a4_duplicate_work_traversal_is_conservative_and_keeps_one_record():
    client, _ = make_client(a4_sources(fixture("source_biometrics.json")), a4_page(a4_work(), a4_work()))
    with client:
        result = a4_discover(client, a4_journals()[:1])
    assert len(result.records) == 1
    assert result.coverage[0].status is CoverageStatus.PARTIAL


def test_a4_works_batches_are_bounded_and_same_client_is_reused():
    journals = tuple(JournalConfig(name=f"Journal {i}", issn_l=checksum_issn(i)) for i in range(101))
    sources = [dict(fixture("source_biometrics.json"), id=f"S{i}", display_name=j.name, issn_l=j.issn_l, issn=[j.issn_l]) for i, j in enumerate(journals)]
    client, transport = make_client(a4_sources(*sources[:100]), a4_sources(*sources[100:]), a4_page(), a4_page())
    with client:
        pooled = client._http_client
        result = a4_discover(client, journals)
        assert client._http_client is pooled
    assert len(result.units) == 101
    assert all(unit.coverage.status is CoverageStatus.COMPLETE for unit in result.units)
    assert [len(request.url.params["filter"].split(",")[0].split("|")) for request in transport.requests[2:]] == [100, 1]
    assert pooled.is_closed and transport.closed


def test_a4_terminal_failure_after_usable_page_remains_partial_without_fallback():
    client, transport = make_client(a4_sources(fixture("source_biometrics.json"), fixture("source_cybernetics.json")),
                                    a4_page(a4_work(), cursor="next", count=2), http_error(401))
    with client:
        result = a4_discover(client)
    assert [coverage.status for coverage in result.coverage] == [CoverageStatus.PARTIAL, CoverageStatus.FAILED]
    assert len(result.records) == 1
    assert len(transport.requests) == 3


def test_a4_complete_traversal_excludes_below_floor_records_without_coverage_loss():
    client, _ = make_client(a4_sources(fixture("source_biometrics.json")), a4_page(a4_work(title=None, doi=None)))
    with client:
        result = a4_discover(client, a4_journals()[:1])
    assert not result.records
    assert result.coverage[0].status is CoverageStatus.COMPLETE
    assert len(result.issues) == 1
    assert result.issues[0].severity is IssueSeverity.WARNING
    assert not result.has_errors


def partial_discovery(raws):
    client, transport = make_client(a4_sources(fixture("source_biometrics.json")), a4_page(*raws))
    with client:
        result = a4_discover(client, a4_journals()[:1])
    assert len(transport.requests) == 2
    assert transport.closed
    return result


@pytest.mark.parametrize(("title", "doi", "expected_title", "expected_doi"), [
    ("Study", " HTTPS://DOI.ORG/10.5555/STUDY ", "Study", "10.5555/study"),
    (None, "10.5555/study", None, "10.5555/study"),
    (" \t ", "10.5555/study", None, "10.5555/study"),
    (42, "10.5555/study", None, "10.5555/study"),
    ("Study", None, "Study", None),
    ("Study", "invalid DOI", "Study", None),
    ("Study", "not-a-doi", "Study", None),
    ("Study", "10.5555/", "Study", None),
    ("Study", 42, "Study", None),
])
def test_partial_admission_requires_doi_or_title_not_canonical_metadata(
    title, doi, expected_title, expected_doi,
):
    result = partial_discovery([a4_work(title=title, doi=doi)])
    record, = result.records
    assert not isinstance(record.metadata, CanonicalMetadata)
    evidence = record.to_evidence()
    assert evidence.title == expected_title
    assert evidence.external_ids.doi == expected_doi
    assert evidence.external_ids.openalex == evidence.provenance.record_id == "https://openalex.org/W1"
    assert evidence.journal == "Biometrics"
    assert evidence.provenance.retrieved_at == datetime(2026, 1, 31, tzinfo=timezone.utc)
    assert not result.issues
    assert result.coverage[0].status is CoverageStatus.COMPLETE
    if expected_title is None:
        canonical = canonicalize_records((evidence,))
        assert not canonical.papers
        assert any(issue.stage == "insufficient_metadata" for issue in canonical.issues)


@pytest.mark.parametrize(("title", "doi"), [(None, None), (" ", "bad"), ([], 42)])
def test_below_floor_records_are_excluded_without_execution_error(title, doi):
    result = partial_discovery([a4_work(title=title, doi=doi)])
    assert not result.records
    assert result.coverage[0].status is CoverageStatus.COMPLETE
    assert len(result.issues) == 1
    assert result.issues[0].severity is IssueSeverity.WARNING
    assert not result.has_errors


@pytest.mark.parametrize("authorships", [None, {}, "bad", [], [None, {}, {"author": []}],
    [{"author": {"display_name": " "}, "raw_author_name": 42}],
])
def test_unusable_authorships_retain_empty_authors_without_issue(authorships):
    result = partial_discovery([a4_work(authorships=authorships)])
    record, = result.records
    assert record.authors == record.to_evidence().authors == ()
    assert not result.issues
    assert result.coverage[0].status is CoverageStatus.COMPLETE
    canonical = canonicalize_records((record.to_evidence(),))
    assert len(canonical.papers) == 1 and not canonical.papers[0].authors
    assert not canonical.issues


def test_partial_author_information_preserves_provider_order_and_safe_fields():
    authorships = [
        {"author": {"display_name": "Profile First", "id": "bad", "orcid": "bad"}, "raw_author_name": "First"},
        {"author": None, "raw_author_name": "Second"},
        {"author": {"display_name": "Third", "id": "A123", "orcid": "0000-0001-2345-6789"}},
        {"author": {}},
    ]
    result = partial_discovery([a4_work(authorships=authorships)])
    assert [(a.name, a.openalex_id, a.orcid) for a in result.records[0].authors] == [
        ("First", None, None), ("Second", None, None),
        ("Third", "https://openalex.org/A123", "0000-0001-2345-6789"),
    ]
    assert not result.issues


@pytest.mark.parametrize("abstract", [None, [], "bad", {"word": "bad"},
    {"word": [-1]}, {"word": [True]}, {"word": [0], "other": [0]}, {" ": [0]},
])
def test_missing_or_malformed_abstract_retains_evidence_without_issue(abstract):
    result = partial_discovery([a4_work(abstract_inverted_index=abstract)])
    assert result.records[0].to_evidence().abstract is None
    assert not result.issues
    assert result.coverage[0].status is CoverageStatus.COMPLETE


@pytest.mark.parametrize("publication_date", [None, "bad", "2026-02-30", 42, {}])
def test_missing_or_invalid_publication_date_does_not_discard_evidence(publication_date):
    result = partial_discovery([a4_work(publication_date=publication_date)])
    assert result.records[0].to_evidence().publication_date is None
    assert not result.issues
    assert result.coverage[0].status is CoverageStatus.COMPLETE


@pytest.mark.parametrize("work_id", [None, "", "bad", "A123", 42])
def test_missing_or_invalid_work_id_remains_structural_failure(work_id):
    result = partial_discovery([a4_work(id=work_id)])
    assert not result.records
    assert result.has_errors
    assert result.coverage[0].status is CoverageStatus.PARTIAL
    assert all(issue.severity is IssueSeverity.ERROR for issue in result.issues)


@pytest.mark.parametrize("primary_location", [None, {}, {"source": None},
    {"source": {}}, {"source": {"id": None}},
])
def test_single_source_missing_nested_identity_uses_verified_request_scope(primary_location):
    result = partial_discovery([a4_work(primary_location=primary_location)])
    record, = result.records
    assert record.source_id == result.sources[0].openalex_id == "https://openalex.org/S8265502"
    assert record.to_evidence().journal == result.sources[0].display_name
    assert not result.issues
    assert result.coverage[0].status is CoverageStatus.COMPLETE


@pytest.mark.parametrize("source_id", ["S999", "invalid Source identity"])
def test_single_source_explicit_conflict_or_invalid_identity_is_never_overridden(source_id):
    raw = a4_work()
    raw["primary_location"]["source"]["id"] = source_id
    result = partial_discovery([raw])
    assert not result.records
    assert result.has_errors
    assert result.coverage[0].status is CoverageStatus.PARTIAL


def test_multi_source_missing_nested_identity_splits_before_scope_fallback():
    first = a4_work(primary_location=None)
    second = a4_work("W2", "S4210191041", primary_location=None)
    client, transport = make_client(
        a4_sources(fixture("source_biometrics.json"), fixture("source_cybernetics.json")),
        a4_page(first, second), a4_page(first), a4_page(second))
    with client:
        result = a4_discover(client)
    assert [r.source_id for r in result.records] == [u.source.openalex_id for u in result.units]
    assert [r.external_ids.openalex for r in result.records] == ["https://openalex.org/W1", "https://openalex.org/W2"]
    filters = [request.url.params["filter"].split(",")[0] for request in transport.requests[1:]]
    assert filters == ["primary_location.source.id:S8265502|S4210191041",
                       "primary_location.source.id:S8265502", "primary_location.source.id:S4210191041"]
    assert all(unit.coverage.status is CoverageStatus.COMPLETE for unit in result.units)
    assert not result.issues


def test_multi_source_explicit_conflict_remains_error_even_after_successful_split():
    bad = a4_work(source_id="S999")
    client, transport = make_client(
        a4_sources(fixture("source_biometrics.json"), fixture("source_cybernetics.json")),
        a4_page(bad, a4_work("W2", "S4210191041")),
        a4_page(a4_work()), a4_page(a4_work("W2", "S4210191041")))
    with client:
        result = a4_discover(client)
    assert len(result.records) == 2
    assert all(r.source_id != "https://openalex.org/S999" for r in result.records)
    assert result.has_errors
    assert all(unit.coverage.status is CoverageStatus.PARTIAL for unit in result.units)
    assert any("outside requested Sources" in issue.message for issue in result.issues)
    assert len(transport.requests) == 4


def test_complete_traversal_coverage_is_independent_of_field_completeness():
    records = [a4_work("W1", title=None), a4_work("W2", doi=None),
        a4_work("W3", authorships=None), a4_work("W4", abstract_inverted_index=None),
        a4_work("W5", publication_date=None), a4_work("W6", title=None, doi=None)]
    result = partial_discovery(records)
    assert len(result.records) == 5
    assert not result.has_errors
    assert result.coverage[0].status is CoverageStatus.COMPLETE
    assert len(result.issues) == 1
    assert "neither a valid DOI nor a usable title" in result.issues[0].message


# §41 identity lookup is separate from the legacy configured-name authority.
def test_issn_l_identity_ignores_legacy_name_and_retains_complete_evidence():
    raw = fixture("source_biometrics_publisher.json")
    with make_client(a4_sources(raw))[0] as client:
        result, = openalex_module.resolve_source_identities(
            client, (" 0006-341x ", "0006-341X"),
        )
    assert result.status is openalex_module.SourceEvidenceStatus.RESOLVED
    assert result.evidence.provider_issn_l == "0006-341X"
    assert result.evidence.aliases == ("0006-341X", "1541-0420")
    assert result.evidence.source_id == "https://openalex.org/S8265502"
    assert result.evidence.display_name == "Biometrics"
    assert result.evidence.publisher_id == "https://openalex.org/P4310320999"
    # Production now shares the accepted A2 membership semantics.
    client, _ = make_client(a4_sources(raw))
    unit, = openalex_module.resolve_journal_sources_batched(
        client, (JournalConfig(name="Deliberately unrelated title", issn_l="0006-341X"),),
    )
    assert unit.source.configured_issn_l == "0006-341X"
    assert not unit.issues


@pytest.mark.parametrize("updates", [
    {"type": "repository"},
    {"id": "invalid"}, {"display_name": " "}, {"display_name": "bad\nname"},
])
def test_strict_source_identity_rejects_malformed_identity_without_fallback(updates):
    raw = dict(fixture("source_biometrics.json"), **updates)
    client, transport = make_client(a4_sources(raw))
    result, = openalex_module.resolve_source_identities(client, ("0006-341X",))
    assert result.evidence is None
    assert result.status is openalex_module.SourceEvidenceStatus.INVALID_SOURCE
    assert result.diagnostic
    assert len(transport.requests) == 1


@pytest.mark.parametrize("candidate", [None, "bad", "0006-3410", "2168-2267", "1541-0420", 42])
def test_provider_candidate_never_invalidates_direct_membership(candidate):
    raw = dict(fixture("source_biometrics.json"), issn_l=candidate)
    client, transport = make_client(a4_sources(raw))
    result, = openalex_module.resolve_source_identities(client, ("0006-341X",))
    assert result.status is openalex_module.SourceEvidenceStatus.RESOLVED
    assert result.evidence.provider_issn_l == (candidate if candidate == "1541-0420" else None)
    assert len(transport.requests) == 1
    if candidate != "1541-0420":
        assert result.evidence.diagnostics


def test_configured_alias_membership_does_not_require_provider_candidate_equality():
    client, _ = make_client(a4_sources(fixture("source_biometrics.json")))
    result, = openalex_module.resolve_source_identities(client, ("1541-0420",))
    assert result.status is openalex_module.SourceEvidenceStatus.RESOLVED
    assert result.evidence.provider_issn_l == "0006-341X"


def test_distinct_source_ambiguity_is_not_arbitrated_by_singleton():
    raw = fixture("source_biometrics.json")
    client, transport = make_client(a4_sources(raw, dict(raw, id="S999")))
    result, = openalex_module.resolve_source_identities(client, ("0006-341X",))
    assert result.status is openalex_module.SourceEvidenceStatus.AMBIGUOUS_SOURCE
    assert result.evidence is None
    assert len(transport.requests) == 1


@pytest.mark.parametrize("updates", [
    {"issn_l": "1541-0420"}, {"issn": ["1541-0420", "0006-341X"]},
    {"host_organization": "P123"}, {"display_name": "Different display metadata"},
])
def test_same_source_duplicates_reconcile_without_false_identity_ambiguity(updates):
    raw = fixture("source_biometrics.json")
    client, transport = make_client(a4_sources(raw, dict(raw, **updates)))
    result, = openalex_module.resolve_source_identities(client, ("0006-341X",))
    assert result.status is openalex_module.SourceEvidenceStatus.RESOLVED
    assert result.evidence.source_id == "https://openalex.org/S8265502"
    assert len(transport.requests) == 1
    if "issn_l" in updates:
        assert result.evidence.provider_issn_l is None
        assert any("disagreement" in message for message in result.evidence.diagnostics)


def test_same_source_contradictory_membership_is_explicit_data_conflict():
    raw = fixture("source_biometrics.json")
    client, _ = make_client(a4_sources(raw, dict(raw, issn=["0006-341X"])))
    result, = openalex_module.resolve_source_identities(client, ("0006-341X",))
    assert result.status is openalex_module.SourceEvidenceStatus.INVALID_SOURCE
    assert "Source-data conflict" in result.diagnostic


def test_identity_input_validation_prevents_any_request():
    client, transport = make_client()
    results = openalex_module.resolve_source_identities(client, ("bad", "0006-3410"))
    assert all(result.status is openalex_module.SourceEvidenceStatus.INVALID_IDENTIFIER for result in results)
    assert not transport.requests


def test_identity_not_found_is_distinct_from_quota_failure():
    client, transport = make_client(a4_sources(), http_error(404))
    missing, = openalex_module.resolve_source_identities(client, ("0006-341X",))
    assert missing.status is openalex_module.SourceEvidenceStatus.NOT_FOUND
    assert len(transport.requests) == 2
    client, transport = make_client(*[http_error(429)] * 3)
    failed = openalex_module.resolve_source_identities(client, ("0006-341X", "1541-0420"))
    assert all(result.status is openalex_module.SourceEvidenceStatus.REQUEST_FAILED for result in failed)
    assert all(result.request_failure_kind is openalex_module.OpenAlexFailureKind.QUOTA for result in failed)
    assert len(transport.requests) == 3


def test_identity_timeout_and_incomplete_batch_fail_closed_when_singleton_fails():
    client, _ = make_client({"meta": {"count": 1}, "results": []},
                           *[httpx.ReadTimeout("timeout")] * 3)
    result, = openalex_module.resolve_source_identities(client, ("0006-341X",))
    assert result.status is openalex_module.SourceEvidenceStatus.REQUEST_FAILED
    assert result.evidence is None
    assert "timeout" in result.diagnostic


def test_legacy_identity_batches_deduplicated_aliases_without_name_checks():
    client, transport = make_client(a4_sources(fixture("source_biometrics.json")))
    results = openalex_module.resolve_source_identities(
        client, ("0006-341X", "1541-0420", "0006-341X"),
    )
    assert len(results) == 2
    assert results[0].evidence == results[1].evidence
    assert len(transport.requests) == 1
    assert parse_qs(urlparse(str(transport.requests[0].url)).query)["filter"] == ["issn:0006-341X|1541-0420"]


def test_nonpublisher_host_organization_is_not_a_direct_publisher_id():
    raw = dict(fixture("source_biometrics.json"), host_organization="https://openalex.org/I123")
    client, _ = make_client(a4_sources(raw))
    result, = openalex_module.resolve_source_identities(client, ("0006-341X",))
    assert result.evidence.publisher_id is None


def test_singleton_identity_rejects_source_without_queried_alias():
    raw = dict(fixture("source_biometrics.json"), issn=["1541-0420"])
    client, _ = make_client(a4_sources(), raw)
    result, = openalex_module.resolve_source_identities(client, ("0006-341X",))
    assert result.status is openalex_module.SourceEvidenceStatus.INVALID_SOURCE
    assert "absent from the returned source ISSNs" in result.diagnostic


@pytest.mark.parametrize("malformed", [None, {"id": "S999"}, {"issn": []}, {"issn": ["bad"]}])
def test_unassignable_batch_evidence_uses_bounded_singleton_recovery(malformed):
    raw = fixture("source_biometrics.json")
    client, transport = make_client(a4_sources(raw, malformed), raw)
    result, = openalex_module.resolve_source_identities(client, ("0006-341X",))
    assert result.status is openalex_module.SourceEvidenceStatus.RESOLVED
    assert len(transport.requests) == 2


def test_unassignable_batch_with_failed_recovery_remains_unproven_and_bounded():
    raw = fixture("source_biometrics.json")
    client, transport = make_client(a4_sources(raw, None), *[httpx.ReadTimeout("timeout")] * 3)
    result, = openalex_module.resolve_source_identities(client, ("0006-341X",))
    assert result.evidence is None
    assert result.status is openalex_module.SourceEvidenceStatus.REQUEST_FAILED
    assert len(transport.requests) == 4


def test_unassignable_record_does_not_erase_known_distinct_source_ambiguity():
    raw = fixture("source_biometrics.json")
    client, transport = make_client(a4_sources(raw, dict(raw, id="S999"), None))
    result, = openalex_module.resolve_source_identities(client, ("0006-341X",))
    assert result.status is openalex_module.SourceEvidenceStatus.AMBIGUOUS_SOURCE
    assert len(transport.requests) == 1


def test_strict_batches_preserve_success_when_later_batch_hits_quota():
    identifiers = []
    raws = []
    for index in range(101):
        digits = f"{index:07d}"
        check = (-sum(int(digit) * weight for digit, weight in zip(digits, range(8, 1, -1)))) % 11
        issn = digits[:4] + "-" + digits[4:] + ("X" if check == 10 else str(check))
        identifiers.append(issn)
        raws.append(dict(fixture("source_biometrics.json"), id=f"S{index + 1}", issn_l=issn, issn=[issn]))
    client, transport = make_client(a4_sources(*raws[:100]), *[http_error(429)] * 3)
    results = openalex_module.resolve_source_identities(client, identifiers)
    assert len(results) == 101
    assert all(result.evidence is not None for result in results[:100])
    assert results[-1].status is openalex_module.SourceEvidenceStatus.REQUEST_FAILED
    assert len(transport.requests) == 4
    assert len(transport.requests[0].url.params["filter"].removeprefix("issn:").split("|")) == 100


def test_malformed_alias_in_one_assignable_source_preserves_other_source_evidence():
    invalid = dict(fixture("source_cybernetics.json"), issn=["2168-2267", "2168-2275", "1526-5489"])
    client, transport = make_client(a4_sources(fixture("source_biometrics.json"), invalid))
    results = openalex_module.resolve_source_identities(client, ("0006-341X", "2168-2267"))
    assert results[0].evidence is not None
    assert results[1].status is openalex_module.SourceEvidenceStatus.RESOLVED
    assert "1526-5489" not in results[1].evidence.aliases
    assert any("1526-5489" in message for message in results[1].evidence.diagnostics)
    assert len(transport.requests) == 1


@pytest.mark.parametrize("bad", ["1526-5489", None, 42, ""])
def test_msom_bad_additional_alias_is_excluded_individually(bad):
    raw = dict(fixture("source_biometrics.json"), issn_l="1523-4614",
               issn=["1523-4614", "1526-5498", bad, " 1526-5498 "])
    client, _ = make_client(a4_sources(raw))
    result, = openalex_module.resolve_source_identities(client, ("1523-4614",))
    assert result.status.value == "resolved"
    assert result.evidence.aliases == ("1523-4614", "1526-5498")
    assert result.evidence.provider_issn_l == "1523-4614"
    assert result.evidence.diagnostics


def test_assignable_invalid_record_affects_only_its_own_identifier():
    invalid = dict(fixture("source_cybernetics.json"), type="repository")
    client, transport = make_client(a4_sources(fixture("source_biometrics.json"), invalid))
    results = openalex_module.resolve_source_identities(client, ("0006-341X", "2168-2267"))
    assert results[0].status is openalex_module.SourceEvidenceStatus.RESOLVED
    assert results[1].status is openalex_module.SourceEvidenceStatus.INVALID_SOURCE
    assert len(transport.requests) == 1


def test_assignable_malformed_distinct_source_preserves_known_ambiguity():
    raw = fixture("source_biometrics.json")
    client, transport = make_client(a4_sources(raw, dict(raw, id="S999", type="repository")))
    result, = openalex_module.resolve_source_identities(client, ("0006-341X",))
    assert result.status is openalex_module.SourceEvidenceStatus.AMBIGUOUS_SOURCE
    assert len(transport.requests) == 1


def checksum_issn(number):
    stem = f"{number:07d}"
    check = (-sum(int(c) * w for c, w in zip(stem, range(8, 1, -1)))) % 11
    return stem[:4] + "-" + stem[4:] + ("X" if check == 10 else str(check))


@pytest.mark.parametrize('damage', ['name', 'provider_issn_l', 'malformed_alias', 'absent', 'type', 'id', 'ambiguous'])
def test_canonical_batched_membership_contract(damage):
    source = fixture('source_biometrics.json')
    if damage == 'name': source['display_name'] = 'Unrelated title'
    if damage == 'provider_issn_l': source['issn_l'] = '1541-0420'
    if damage == 'malformed_alias': source['issn'].append('1526-5489')
    if damage == 'absent': source['issn'] = ['1541-0420']
    if damage == 'type': source['type'] = 'repository'
    if damage == 'id': source['id'] = 'bad'
    raws = [source] + ([dict(source, id='S999')] if damage == 'ambiguous' else [])
    client, transport = make_client(a4_sources(*raws), source)
    with client:
        unit, = openalex_module.resolve_journal_sources_batched(client, a4_journals()[:1])
    if damage in ('name', 'provider_issn_l', 'malformed_alias'):
        assert unit.source.configured_issn_l == '0006-341X'
        assert '1526-5489' not in unit.source.aliases
        assert len(transport.requests) == 1
        assert all(issue.severity is IssueSeverity.WARNING for issue in unit.issues)
    else:
        assert unit.source is None and unit.status is CoverageStatus.FAILED
        assert any(issue.severity is IssueSeverity.ERROR for issue in unit.issues)
        if damage == 'ambiguous': assert len(transport.requests) == 1


@pytest.mark.parametrize('discovery', [False, True])
def test_batch_failure_recovers_only_configured_issn_l(discovery):
    source = fixture('source_biometrics.json')
    outcomes = [http_error(500)] * 3 + [source] + ([a4_page()] if discovery else [])
    client, transport = make_client(*outcomes)
    with client:
        if discovery:
            unit, = a4_discover(client, a4_journals()[:1]).units
            assert unit.coverage.status is CoverageStatus.COMPLETE
        else:
            unit, = openalex_module.resolve_journal_sources_batched(client, a4_journals()[:1])
        assert unit.source.configured_issn_l == '0006-341X' and not unit.issues
    assert [r.url.path for r in transport.requests] == ['/sources'] * 3 + ['/sources/issn:0006-341X'] + (['/works'] if discovery else [])


def test_alias_absence_does_not_create_configured_verification_failure():
    source = fixture('source_biometrics.json');source['issn'] = ['0006-341X']
    client, transport = make_client(a4_sources(source), a4_page())
    with client: result = a4_discover(client, a4_journals()[:1])
    assert result.sources[0].aliases == ('0006-341X',)
    assert result.coverage[0].status is CoverageStatus.COMPLETE and not result.issues
    assert len(transport.requests) == 2


@pytest.mark.parametrize('status', [404, 500])
def test_source_failure_prevents_works_but_preserves_other_journals(status):
    bio, cyber = fixture('source_biometrics.json'), fixture('source_cybernetics.json')
    failures = [http_error(status)] * (3 if status == 500 else 1)
    client, transport = make_client(a4_sources(bio), *failures, a4_page(a4_work()))
    with client: result = a4_discover(client)
    assert len(result.sources) == len(result.records) == 1
    assert result.coverage[0].status is CoverageStatus.COMPLETE
    assert result.coverage[1].status is (CoverageStatus.UNAVAILABLE if status == 404 else CoverageStatus.FAILED)
    assert transport.requests[-1].url.params['filter'].startswith('primary_location.source.id:S8265502,')
