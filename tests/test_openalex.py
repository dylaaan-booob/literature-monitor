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
    EvidenceVersionRole,
    VersionKind,
)
from literature_monitor.openalex import (
    IssueSeverity,
    OpenAlexClient,
    OpenAlexRequestError,
    OpenAlexVersion,
    discover_journals,
    resolve_journal_source,
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


def test_singleton_requests_each_issn_independently_and_uses_bearer_key() -> None:
    payload = fixture("source_cybernetics.json")
    transport = SequenceTransport(payload, payload)
    client = OpenAlexClient(api_key="secret", transport=transport, sleep=lambda _: None)
    journal = JournalConfig(
        name="IEEE Transactions on Cybernetics",
        issn=("2168-2267", "2168-2275"),
    )

    source, issues = resolve_journal_source(client, journal)

    assert source is not None
    assert source.openalex_id == "https://openalex.org/S4210191041"
    assert source.resolved_issns == journal.issn
    assert not issues
    assert len(transport.requests) == 2
    assert [urlparse(str(request.url)).path for request in transport.requests] == [
        "/sources/issn:2168-2267",
        "/sources/issn:2168-2275",
    ]
    assert all(
        "filter" not in parse_qs(urlparse(str(request.url)).query)
        for request in transport.requests
    )
    assert all(
        request.headers["Authorization"] == "Bearer secret"
        for request in transport.requests
    )
    assert all(
        request.headers["User-Agent"] == "literature-monitor/0.4.3"
        for request in transport.requests
    )
    assert all(request.extensions["timeout"]["read"] == 30 for request in transport.requests)


def test_one_resolved_and_one_missing_issn_resolves_with_warning() -> None:
    client, _ = make_client(fixture("source_cybernetics.json"), http_error(404))
    journal = JournalConfig(
        name="IEEE Transactions on Cybernetics",
        issn=("2168-2267", "2168-2275"),
    )

    source, issues = resolve_journal_source(client, journal)

    assert source is not None
    assert source.unresolved_issns == ("2168-2275",)
    assert [(issue.severity, issue.issn) for issue in issues] == [
        (IssueSeverity.WARNING, "2168-2275")
    ]


def test_conflicting_source_ids_are_an_error() -> None:
    client, _ = make_client(
        fixture("source_cybernetics.json"), fixture("source_conflict.json")
    )
    journal = JournalConfig(
        name="IEEE Transactions on Cybernetics",
        issn=("2168-2267", "0018-9472"),
    )

    source, issues = resolve_journal_source(client, journal)

    assert source is None
    assert len(issues) == 1
    assert issues[0].severity is IssueSeverity.ERROR
    assert "conflicting" in issues[0].message


def test_no_successful_issn_is_an_error() -> None:
    client, _ = make_client(http_error(404), http_error(404))
    journal = JournalConfig(name="Missing Journal", issn=("2168-2267", "2168-2275"))

    source, issues = resolve_journal_source(client, journal)

    assert source is None
    assert len(issues) == 1
    assert "no configured ISSN resolved" in issues[0].message


def test_remote_failure_is_not_treated_as_an_unresolved_issn() -> None:
    client, transport = make_client(
        fixture("source_cybernetics.json"),
        httpx.ReadTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
    )
    journal = JournalConfig(
        name="IEEE Transactions on Cybernetics",
        issn=("2168-2267", "2168-2275"),
    )

    source, issues = resolve_journal_source(client, journal)

    assert source is None
    assert len(issues) == 1
    assert issues[0].issn == "2168-2275"
    assert issues[0].severity is IssueSeverity.ERROR
    assert "incomplete ISSN verification" in issues[0].message
    assert "timed out" in issues[0].message
    assert len(transport.requests) == 4


def test_success_plus_source_semantic_failure_rejects_the_journal() -> None:
    non_journal = fixture("source_cybernetics.json")
    non_journal["type"] = "conference"
    client, _ = make_client(fixture("source_cybernetics.json"), non_journal)
    journal = JournalConfig(
        name="IEEE Transactions on Cybernetics",
        issn=("2168-2267", "2168-2275"),
    )

    source, issues = resolve_journal_source(client, journal)

    assert source is None
    assert len(issues) == 1
    assert issues[0].issn == "2168-2275"
    assert issues[0].severity is IssueSeverity.ERROR
    assert "non-journal" in issues[0].message
    assert "remote/API failure" not in issues[0].message


def test_no_successful_issn_after_remote_failures_is_a_journal_error() -> None:
    client, _ = make_client(*(http_error(500) for _ in range(6)))
    journal = JournalConfig(
        name="IEEE Transactions on Cybernetics",
        issn=("2168-2267", "2168-2275"),
    )

    source, issues = resolve_journal_source(client, journal)

    assert source is None
    assert len(issues) == 1
    assert issues[0].issn is None
    assert issues[0].severity is IssueSeverity.ERROR
    assert "no configured ISSN resolved" in issues[0].message
    assert "incomplete ISSN verification" in issues[0].message
    assert "2168-2267" in issues[0].message
    assert "2168-2275" in issues[0].message


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"type": "conference"}, "non-journal"),
        ({"issn": ["1541-0420"]}, "absent"),
        ({"display_name": "A Different Journal"}, "does not match"),
    ],
)
def test_source_consistency_failures_are_explicit(change: dict[str, Any], message: str) -> None:
    payload = fixture("source_biometrics.json")
    payload.update(change)
    client, _ = make_client(payload)

    source, issues = resolve_journal_source(
        client, JournalConfig(name="Biometrics", issn=("0006-341X",))
    )

    assert source is None
    assert message in issues[0].message


def test_normalized_name_allows_leading_the_and_punctuation() -> None:
    payload = fixture("source_biometrics.json")
    payload["display_name"] = "The Annals of Statistics"
    payload["issn"] = ["0090-5364"]
    payload["issn_l"] = "0090-5364"
    client, _ = make_client(payload)

    source, issues = resolve_journal_source(
        client, JournalConfig(name="Annals of Statistics", issn=("0090-5364",))
    )

    assert source is not None
    assert not issues


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


def test_discovery_pages_normalizes_records_and_builds_venue_first_query() -> None:
    client, transport = make_client(
        fixture("source_biometrics.json"),
        fixture("works_page_1.json"),
        fixture("works_page_2.json"),
    )
    timestamp = datetime(2026, 9, 18, tzinfo=timezone.utc)

    result = discover_journals(
        client,
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 9, 18),
        retrieved_at=timestamp,
    )

    assert not result.has_errors
    assert len(result.sources) == 1
    assert len(result.coverage) == 1
    assert result.coverage[0].component is CoverageComponent.OPENALEX_DISCOVERY
    assert result.coverage[0].status is CoverageStatus.COMPLETE
    assert result.coverage[0].journal == "Biometrics"
    assert [record.external_ids.openalex for record in result.records] == [
        "https://openalex.org/W4389363697",
        "https://openalex.org/W7125247299",
    ]
    first, second = result.records
    assert first.external_ids.doi == "10.1093/biomtc/ujag004"
    assert first.metadata.abstract == "The Cox model"
    assert first.metadata.author_keywords == ()
    assert first.authors[0].openalex_id == "https://openalex.org/A5003554707"
    assert first.provenance.retrieved_at == timestamp
    assert second.external_ids.doi is None
    assert second.metadata.publication_date is None
    assert second.metadata.abstract is None
    assert second.authors[0].name == "Mehdi Moradi"
    assert second.authors[0].openalex_id is None
    assert result.units[0].journal.name == "Biometrics"
    assert result.units[0].source == result.sources[0]
    assert result.units[0].records == result.records
    assert result.units[0].issues == result.issues
    assert tuple(unit.coverage for unit in result.units) == result.coverage

    work_requests = [request for request in transport.requests[1:]]
    first_query = parse_qs(urlparse(str(work_requests[0].url)).query)
    assert first_query == {
        "filter": [
            "primary_location.source.id:S8265502,"
            "from_publication_date:2026-01-01,to_publication_date:2026-09-18"
        ],
        "select": [
            "id,doi,title,publication_date,abstract_inverted_index,authorships,"
            "primary_location,locations"
        ],
        "per_page": ["100"],
        "cursor": ["*"],
    }
    assert parse_qs(urlparse(str(work_requests[1].url)).query)["cursor"] == ["next-page"]
    assert all(
        "search" not in parse_qs(urlparse(str(request.url)).query)
        for request in work_requests
    )


def test_source_resolution_and_discovery_report_natural_progress_without_extra_requests() -> None:
    client, transport = make_client(
        fixture("source_biometrics.json"),
        fixture("works_page_1.json"),
        fixture("works_page_2.json"),
    )
    events: list[ProgressEvent] = []

    result = discover_journals(
        client,
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 9, 18),
        progress_callback=events.append,
    )

    assert not result.has_errors
    assert len(transport.requests) == 3
    activities = [event.activity for event in events if event.activity is not None]
    source_checks = [
        item for item in activities if item.label == "Checked OpenAlex ISSN"
    ]
    assert [(item.current, item.total) for item in source_checks] == [(1, 1)]

    works = [item for item in activities if item.operation == "works_discovery:0"]
    assert works[0].label == "Discovering OpenAlex works"
    assert (works[0].current, works[0].total) == (0, None)
    first_request = next(item for item in works if item.label == "Requesting OpenAlex")
    assert (first_request.current, first_request.total) == (0, None)
    pages = [item for item in works if item.label == "Retrieved OpenAlex page"]
    assert [(item.current, item.total) for item in pages] == [(1, 2), (2, 2)]
    assert works[-1].label == "Completed OpenAlex journal discovery"
    assert (works[-1].current, works[-1].total) == (2, 2)


def test_discovery_zero_results_and_unresolved_alternate_issn_are_complete() -> None:
    empty_page = deepcopy(fixture("works_page_1.json"))
    empty_page["results"] = []
    empty_page["meta"]["count"] = 0
    empty_page["meta"]["next_cursor"] = None
    client, _ = make_client(
        fixture("source_cybernetics.json"),
        http_error(404),
        empty_page,
    )

    result = discover_journals(
        client,
        (
            JournalConfig(
                name="IEEE Transactions on Cybernetics",
                issn=("2168-2267", "2168-2275"),
            ),
        ),
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert result.records == ()
    assert result.coverage[0].status is CoverageStatus.COMPLETE


def test_discovery_later_page_failure_is_partial() -> None:
    client, _ = make_client(
        fixture("source_biometrics.json"),
        fixture("works_page_1.json"),
        httpx.ReadTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
    )

    result = discover_journals(
        client,
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert result.records
    assert result.coverage[0].status is CoverageStatus.PARTIAL
    assert any(issue.stage == "work_retrieval" for issue in result.issues)


def test_discovery_source_absence_is_unavailable() -> None:
    client, _ = make_client(http_error(404))

    result = discover_journals(
        client,
        (JournalConfig(name="Missing Journal", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert result.records == ()
    assert result.coverage[0].status is CoverageStatus.UNAVAILABLE


def test_discovery_source_request_failure_is_failed() -> None:
    client, _ = make_client(
        httpx.ReadTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
    )

    result = discover_journals(
        client,
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert result.records == ()
    assert result.coverage[0].status is CoverageStatus.FAILED


def test_discovery_source_validation_failure_is_failed() -> None:
    payload = fixture("source_biometrics.json")
    payload["type"] = "conference"
    client, _ = make_client(payload)

    result = discover_journals(
        client,
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert result.records == ()
    assert result.coverage[0].status is CoverageStatus.FAILED


@pytest.mark.parametrize("count", (None, "2", -1, 0, True))
def test_discovery_unusable_meta_count_stays_indeterminate(count: object) -> None:
    page = deepcopy(fixture("works_page_1.json"))
    page["meta"]["next_cursor"] = None
    if count is None:
        page["meta"].pop("count", None)
    else:
        page["meta"]["count"] = count
    client, transport = make_client(fixture("source_biometrics.json"), page)
    events: list[ProgressEvent] = []

    result = discover_journals(
        client,
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
        progress_callback=events.append,
    )

    assert not result.has_errors
    assert len(transport.requests) == 2
    works = [
        event.activity
        for event in events
        if event.activity is not None
        and event.activity.operation == "works_discovery:0"
    ]
    assert works
    assert all(item.total is None for item in works)


def test_normalization_preserves_inline_location_version_hints() -> None:
    page = deepcopy(fixture("works_page_1.json"))
    page["meta"]["next_cursor"] = None
    work = page["results"][0]
    work["doi"] = "https://doi.org/10.5555/FINAL"
    work["keywords"] = [{"display_name": "Generated keyword"}]
    work["topics"] = [{"display_name": "Generated topic"}]
    work["locations"] = [
        {
            "id": "doi:10.5555/FINAL",
            "version": "publishedVersion",
            "landing_page_url": "https://doi.org/10.5555/final",
        },
        {
            "id": "pmh:oai:arXiv.org:2601.01234",
            "version": "submittedVersion",
            "landing_page_url": "https://arxiv.org/abs/2601.01234",
        },
        {
            "id": "pmh:oai:repository.example:item-1",
            "version": "acceptedVersion",
            "landing_page_url": "https://repository.example/item-1",
        },
    ]
    client, _ = make_client(fixture("source_biometrics.json"), page)

    result = discover_journals(
        client,
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert not result.has_errors
    record = result.records[0]
    assert record.external_ids.doi == "10.5555/final"
    assert record.external_ids.arxiv is None
    assert record.metadata.author_keywords == ()
    assert {
        (hint.source, hint.identifier, hint.version)
        for hint in record.version_hints
    } == {
        ("doi", "10.5555/final", OpenAlexVersion.PUBLISHED),
        ("arxiv", "2601.01234", OpenAlexVersion.SUBMITTED),
        (
            "openalex_location",
            "pmh:oai:repository.example:item-1",
            OpenAlexVersion.ACCEPTED,
        ),
    }

    evidence = record.to_evidence()
    assert evidence.provenance == record.provenance
    assert evidence.title == record.metadata.title
    assert evidence.journal == record.metadata.journal
    assert evidence.publication_date == record.metadata.publication_date
    assert evidence.abstract == record.metadata.abstract
    assert evidence.author_keywords == record.metadata.author_keywords
    assert evidence.authors == record.authors
    assert evidence.external_ids == record.external_ids
    assert {
        (hint.source, hint.identifier, hint.role, hint.url)
        for hint in evidence.version_hints
    } == {
        (
            "doi",
            "10.5555/final",
            EvidenceVersionRole.PUBLICATION,
            "https://doi.org/10.5555/final",
        ),
        (
            "arxiv",
            "2601.01234",
            EvidenceVersionRole.PREPRINT,
            "https://arxiv.org/abs/2601.01234",
        ),
        (
            "openalex_location",
            "pmh:oai:repository.example:item-1",
            EvidenceVersionRole.MANUSCRIPT,
            "https://repository.example/item-1",
        ),
    }

    paper = canonicalize_records((evidence,)).papers[0]
    versions = {
        (version.source, version.identifier): version for version in paper.versions
    }
    assert versions[("doi", "10.5555/final")].kind is VersionKind.JOURNAL_FINAL
    assert versions[("arxiv", "2601.01234")].kind is VersionKind.PREPRINT
    assert versions[("arxiv", "2601.01234")].date is None
    assert (
        versions[("openalex_location", "pmh:oai:repository.example:item-1")].kind
        is VersionKind.ACCEPTED_MANUSCRIPT
    )
    assert paper.preferred_version is not None
    assert (paper.preferred_version.source, paper.preferred_version.identifier) == (
        "doi",
        "10.5555/final",
    )


def test_malformed_locations_fail_soft_without_dropping_work() -> None:
    page = deepcopy(fixture("works_page_1.json"))
    page["meta"]["next_cursor"] = None
    invalid_collection = page["results"][0]
    invalid_collection["locations"] = {"not": "a list"}
    mixed = deepcopy(invalid_collection)
    mixed["id"] = "https://openalex.org/W100"
    mixed["locations"] = [
        {
            "id": "doi:10.5555/valid",
            "version": "publishedVersion",
        },
        "malformed",
        {"id": "pmh:oai:arXiv.org:2601.99999", "version": "unknownVersion"},
        {"id": None, "version": "submittedVersion"},
        {"id": "pmh:oai:arXiv.org:2601.88888"},
    ]
    page["results"] = [invalid_collection, mixed]
    client, _ = make_client(fixture("source_biometrics.json"), page)

    result = discover_journals(
        client,
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert len(result.records) == 2
    assert not result.has_errors
    assert result.records[0].metadata.title
    mixed_record = next(
        record
        for record in result.records
        if record.external_ids.openalex == "https://openalex.org/W100"
    )
    assert [
        (hint.source, hint.identifier, hint.version)
        for hint in mixed_record.version_hints
    ] == [("doi", "10.5555/valid", OpenAlexVersion.PUBLISHED)]
    warning_messages = [issue.message for issue in result.issues]
    assert "invalid locations was ignored" in warning_messages
    assert any("malformed location" in message for message in warning_messages)
    assert any("without a recognized version" in message for message in warning_messages)
    assert any("without a stable identity" in message for message in warning_messages)


def test_normalization_preserves_author_order_orcid_and_abstract_positions() -> None:
    page = deepcopy(fixture("works_page_1.json"))
    page["meta"]["next_cursor"] = None
    work = page["results"][0]
    work["doi"] = "https://doi.org/"
    work["abstract_inverted_index"] = {"model": [2], "The": [0], "Cox": [1]}
    work["authorships"][0]["author"]["orcid"] = (
        "  https://orcid.org/0000-0002-9447-7023  "
    )
    work["authorships"].append(
        {
            "author": {
                "id": "https://openalex.org/A123",
                "display_name": "Second Author",
                "orcid": "https://orcid.org/0000-0001-2345-6789",
            },
            "raw_author_name": "Second Author",
        }
    )
    client, _ = make_client(fixture("source_biometrics.json"), page)

    result = discover_journals(
        client,
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert not result.has_errors
    assert len(result.records) == 1
    record = result.records[0]
    assert record.external_ids.doi is None
    assert record.metadata.abstract == "The Cox model"
    assert [author.name for author in record.authors] == ["Weihao Li", "Second Author"]
    assert [author.orcid for author in record.authors] == [
        "https://orcid.org/0000-0002-9447-7023",
        "https://orcid.org/0000-0001-2345-6789",
    ]


def test_model_validation_failure_skips_only_the_bad_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = deepcopy(fixture("works_page_1.json"))
    page["meta"]["next_cursor"] = None
    malformed = deepcopy(page["results"][0])
    malformed["id"] = "https://openalex.org/W100"
    malformed["title"] = "Trigger model validation"
    valid = deepcopy(page["results"][0])
    valid["id"] = "https://openalex.org/W101"
    page["results"] = [malformed, valid]
    original_normalize = openalex_module._normalize_work

    with pytest.raises(ValidationError) as captured:
        CanonicalMetadata(title="", journal="Biometrics")
    validation_error = captured.value

    def normalize_with_provider_validation(
        payload: Any,
        source: openalex_module.ResolvedSource,
        retrieved_at: datetime,
    ) -> tuple[openalex_module.OpenAlexWorkRecord, tuple[str, ...]]:
        if payload.get("title") == "Trigger model validation":
            raise validation_error
        return original_normalize(payload, source, retrieved_at)

    monkeypatch.setattr(openalex_module, "_normalize_work", normalize_with_provider_validation)
    client, _ = make_client(fixture("source_biometrics.json"), page)

    result = discover_journals(
        client,
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert [record.external_ids.openalex for record in result.records] == [
        "https://openalex.org/W101"
    ]
    assert result.has_errors
    assert len(result.issues) == 1
    assert result.issues[0].stage == "record_normalization"
    assert result.issues[0].record_id == "https://openalex.org/W100"
    assert result.coverage[0].status is CoverageStatus.PARTIAL


def test_bad_record_in_one_journal_does_not_stop_later_journals() -> None:
    bad_page = deepcopy(fixture("works_page_1.json"))
    bad_page["meta"]["next_cursor"] = None
    bad_page["results"][0]["id"] = "invalid Work identity"
    later_page = deepcopy(fixture("works_page_1.json"))
    later_page["meta"]["next_cursor"] = None
    later_page["results"][0]["id"] = "https://openalex.org/W200"
    later_page["results"][0]["primary_location"]["source"]["id"] = (
        "https://openalex.org/S4210191041"
    )
    client, _ = make_client(
        fixture("source_biometrics.json"),
        bad_page,
        fixture("source_cybernetics.json"),
        fixture("source_cybernetics.json"),
        later_page,
    )

    result = discover_journals(
        client,
        (
            JournalConfig(name="Biometrics", issn=("0006-341X",)),
            JournalConfig(
                name="IEEE Transactions on Cybernetics",
                issn=("2168-2267", "2168-2275"),
            ),
        ),
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert [record.external_ids.openalex for record in result.records] == [
        "https://openalex.org/W200"
    ]
    assert len(result.sources) == 2
    assert result.units[0].records == ()
    assert result.units[0].issues == result.issues
    assert result.units[1].records == result.records
    assert result.units[1].issues == ()
    assert tuple(unit.source for unit in result.units) == result.sources
    assert tuple(unit.coverage for unit in result.units) == result.coverage
    assert result.has_errors
    assert any(
        issue.journal == "Biometrics" and issue.stage == "record_normalization"
        for issue in result.issues
    )


def test_partial_records_preserve_usable_result_without_ingestion_issues() -> None:
    client, _ = make_client(
        fixture("source_biometrics.json"), fixture("works_partial.json")
    )

    result = discover_journals(
        client,
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert len(result.records) == 2
    records = {record.external_ids.openalex: record for record in result.records}
    record = records["https://openalex.org/W100"]
    assert record.metadata.publication_date is None
    assert record.metadata.abstract is None
    assert record.external_ids.doi is None
    assert records["https://openalex.org/W101"].authors == ()
    assert not result.issues
    assert result.coverage[0].status is CoverageStatus.COMPLETE


def test_failed_later_page_keeps_successful_earlier_records() -> None:
    client, _ = make_client(
        fixture("source_biometrics.json"),
        fixture("works_page_1.json"),
        http_error(500),
        http_error(500),
        http_error(500),
    )

    result = discover_journals(
        client,
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert len(result.records) == 1
    assert result.has_errors
    assert any(issue.stage == "work_retrieval" for issue in result.issues)


def test_reverse_date_range_is_rejected_before_requests() -> None:
    client, transport = make_client()

    with pytest.raises(ValueError, match="from_date"):
        discover_journals(
            client,
            (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
            date(2026, 2, 1),
            date(2026, 1, 1),
        )

    assert not transport.requests


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


@pytest.mark.parametrize("revision,expected", [
    (None, None),
    ("2026-09-26T10:30:00+08:00", datetime(2026, 9, 26, 2, 30, tzinfo=timezone.utc)),
    ("2026-09-26T10:30:00", datetime(2026, 9, 26, 10, 30, tzinfo=timezone.utc)),
    ("invalid", None),
])
def test_work_revision_is_optional_timezone_safe_and_not_evidence(revision: str | None, expected: datetime | None) -> None:
    source = openalex_module.ResolvedSource(
        "Biometrics", ("0006-341X",), ("0006-341X",), (),
        "https://openalex.org/S8265502", "Biometrics", "0006-341X", ("0006-341X",),
    )
    payload = fixture("works_page_1.json")["results"][0]
    plain, _ = openalex_module._normalize_work(payload, source, datetime(2026, 9, 26, tzinfo=timezone.utc))
    payload["updated_date"] = revision
    record, warnings = openalex_module._normalize_work(payload, source, plain.provenance.retrieved_at)
    assert record.updated_at == expected
    assert record.to_evidence() == plain.to_evidence()
    assert not warnings


@pytest.mark.parametrize("revision", [None, datetime(2026, 9, 26, tzinfo=timezone.utc)])
def test_default_record_serialization_preserves_released_shape(revision: datetime | None) -> None:
    source = openalex_module.ResolvedSource(
        "Biometrics", ("0006-341X",), ("0006-341X",), (),
        "https://openalex.org/S8265502", "Biometrics", "0006-341X", ("0006-341X",),
    )
    plain, _ = openalex_module._normalize_work(
        fixture("works_page_1.json")["results"][0], source,
        datetime(2026, 9, 26, tzinfo=timezone.utc),
    )
    record = plain.model_copy(update={"updated_at": revision})
    assert record.updated_at == revision
    expected_fields = {"metadata", "external_ids", "authors", "source_id", "provenance", "version_hints"}
    assert set(record.model_dump()) == expected_fields
    assert set(json.loads(record.model_dump_json())) == expected_fields
    assert record.model_dump() == plain.model_dump()
    assert record.model_dump_json() == plain.model_dump_json()


def a4_sources(*sources):
    return {"meta": {"count": len(sources)}, "results": list(sources)}


def a4_page(*works, cursor=None, count=None):
    return {"meta": {"count": len(works) if count is None else count, "next_cursor": cursor}, "results": list(works)}


def a4_work(work_id="W1", source_id="S8265502", **updates):
    raw = fixture("works_page_1.json")["results"][0]
    raw.update(id=f"https://openalex.org/{work_id}", updated_date="2026-01-31T00:00:00")
    raw["primary_location"]["source"]["id"] = f"https://openalex.org/{source_id}"
    raw.update(updates)
    return raw


def a4_journals():
    return (JournalConfig(name="Biometrics", issn=("0006-341X",)),
            JournalConfig(name="IEEE Transactions on Cybernetics", issn=("2168-2267",)))


def a4_discover(client, journals=None):
    return openalex_module.discover_journals_batched(
        client, journals or a4_journals(), date(2026, 1, 1), date(2026, 1, 31),
        retrieved_at=datetime(2026, 1, 31, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("size", [1, 2, 100, 101, 205])
def test_a4_source_batches_are_bounded_and_ordered(size):
    journals = tuple(JournalConfig(name=f"Journal {i}", issn=(f"{i:04d}-000X",)) for i in range(size))
    sources = [dict(fixture("source_biometrics.json"), id=f"S{i}", display_name=j.name, issn=list(j.issn)) for i, j in enumerate(journals)]
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
    assert requested == [j.issn[0] for j in journals]


def test_a4_one_source_satisfies_two_issns_without_singletons():
    journal = JournalConfig(name="Biometrics", issn=("0006-341X", "1541-0420"))
    client, transport = make_client(a4_sources(fixture("source_biometrics.json")))
    with client:
        unit, = openalex_module.resolve_journal_sources_batched(client, (journal,))
    assert unit.source.resolved_issns == journal.issn
    assert not unit.issues
    assert len(transport.requests) == 1


@pytest.mark.parametrize("damage", ["missing", "malformed", "ambiguous"])
def test_a4_source_recovery_requests_only_unsafe_issn(damage):
    biometrics, cyber = fixture("source_biometrics.json"), fixture("source_cybernetics.json")
    raws = [biometrics]
    if damage == "malformed":
        raws.append(dict(cyber, type="repository"))
    if damage == "ambiguous":
        raws.extend([cyber, dict(cyber, id="S999")])
    client, transport = make_client(a4_sources(*raws), cyber)
    with client:
        units = openalex_module.resolve_journal_sources_batched(client, a4_journals())
    assert all(unit.source and not unit.issues for unit in units)
    assert len(transport.requests) == 2
    assert transport.requests[-1].url.path == "/sources/issn:2168-2267"


@pytest.mark.parametrize("damage", ["conflict", "name", "issn", "type", "id"])
def test_a4_source_identity_validation_is_preserved(damage):
    first = fixture("source_biometrics.json")
    other = dict(first, id="S999", issn=["1541-0420"])
    journal = JournalConfig(name="Biometrics", issn=("0006-341X", "1541-0420"))
    if damage == "conflict":
        first["issn"] = ["0006-341X"]
        client, _ = make_client(a4_sources(first, other))
    else:
        bad = dict(first)
        bad[{"name": "display_name", "issn": "issn", "type": "type", "id": "id"}[damage]] = {
            "name": "Other", "issn": ["9999-9999"], "type": "repository", "id": "bad",
        }[damage]
        client, _ = make_client(a4_sources(bad), bad, bad)
    with client:
        unit, = openalex_module.resolve_journal_sources_batched(client, (journal,))
    assert unit.source is None
    assert unit.status is CoverageStatus.FAILED
    assert any(issue.severity is IssueSeverity.ERROR for issue in unit.issues)


@pytest.mark.parametrize("status,requests", [(401, 1), (403, 1), (429, 3)])
def test_a4_terminal_source_failure_opens_execution_circuit(status, requests):
    client, transport = make_client(*(http_error(status) for _ in range(requests)))
    with client:
        units = openalex_module.resolve_journal_sources_batched(client, a4_journals())
        assert all(unit.status is CoverageStatus.FAILED for unit in units)
        for call in (lambda: client.get_source_by_issn("0006-341X"),
                     lambda: list(client.iter_thin_work_pages(("S1",), date(2026, 1, 1), date(2026, 1, 31))),
                     lambda: client.get_work_locations(("W1",))):
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
    assert all(record.version_hints == () for record in result.records)
    assert all(record.updated_at == datetime(2026, 1, 31, tzinfo=timezone.utc) for record in result.records)
    assert not result.issues
    params = dict(transport.requests[-1].url.params)
    assert params["filter"] == "primary_location.source.id:S8265502|S4210191041,from_publication_date:2026-01-01,to_publication_date:2026-01-31"
    assert params["select"] == openalex_module.THIN_WORK_FIELDS
    assert "updated_date" in params["select"].split(",")
    assert "updated_at" not in params["select"].split(",")
    assert "locations" not in params["select"].split(",")
    assert set(params) == {"filter", "select", "per_page", "cursor"}
    source = result.units[0].source
    legacy, _ = openalex_module._normalize_work(first, source, result.records[0].provenance.retrieved_at)
    assert result.records[0].metadata == legacy.metadata
    assert result.records[0].authors == legacy.authors
    assert result.records[0].external_ids == legacy.external_ids


@pytest.mark.parametrize("revision", [None, "bad", "2026-02-30T00:00:00", "2026-01-31", 123, {}])
def test_a4_missing_or_invalid_revision_preserves_candidate_without_issue(revision):
    client, _ = make_client(a4_sources(fixture("source_biometrics.json")), a4_page(a4_work(updated_date=revision)))
    with client:
        result = a4_discover(client, a4_journals()[:1])
    assert len(result.records) == 1
    assert result.records[0].updated_at is None
    assert result.coverage[0].status is CoverageStatus.COMPLETE
    assert not result.issues


def test_a4_same_source_preserves_both_journal_reporting_units():
    source = fixture("source_biometrics.json")
    source["alternate_titles"] = ["Biometrics Alias"]
    journals = (a4_journals()[0], JournalConfig(name="Biometrics Alias", issn=("1541-0420",)))
    client, transport = make_client(a4_sources(source), a4_page(a4_work()))
    with client:
        result = a4_discover(client, journals)
    assert [unit.coverage.journal for unit in result.units] == [journal.name for journal in journals]
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


def test_a4_unresolved_issn_preserves_existing_warning_semantics():
    source = fixture("source_biometrics.json")
    source["issn"] = ["0006-341X"]
    journal = JournalConfig(name="Biometrics", issn=("0006-341X", "1541-0420"))
    client, transport = make_client(a4_sources(source), http_error(404))
    with client:
        unit, = openalex_module.resolve_journal_sources_batched(client, (journal,))
    assert unit.source.resolved_issns == ("0006-341X",)
    assert unit.status is None
    assert len(unit.issues) == 1
    assert unit.issues[0].severity is IssueSeverity.WARNING
    assert unit.issues[0].issn == "1541-0420"
    assert len(transport.requests) == 2


@pytest.mark.parametrize("failure", ["timeout", "server", "api"])
def test_batched_mixed_source_success_and_remote_failure_is_failed(failure):
    source = fixture("source_biometrics.json")
    source["issn"] = ["0006-341X"]
    journal = JournalConfig(name="Biometrics", issn=("0006-341X", "1541-0420"))
    failures = ([httpx.ReadTimeout("timed out")] * 3 if failure == "timeout" else
                [http_error(500)] * 3 if failure == "server" else [http_error(400)])
    client, transport = make_client(a4_sources(source), *failures)
    with client:
        unit, = openalex_module.resolve_journal_sources_batched(client, (journal,))
    assert unit.source is None
    assert unit.status is CoverageStatus.FAILED
    assert [(issue.severity, issue.issn) for issue in unit.issues] == [
        (IssueSeverity.ERROR, "1541-0420")
    ]
    assert "remote/API failure" in unit.issues[0].message
    assert len(transport.requests) == 1 + len(failures)


@pytest.mark.parametrize("batched", [False, True])
@pytest.mark.parametrize("alternate", ["remote", "absent"])
def test_mixed_source_resolution_controls_works_retrieval(batched, alternate):
    source = fixture("source_biometrics.json")
    source["issn"] = ["0006-341X"]
    journal = JournalConfig(name="Biometrics", issn=("0006-341X", "1541-0420"))
    outcomes = [a4_sources(source) if batched else source]
    outcomes += [http_error(500)] * 3 if alternate == "remote" else [http_error(404), a4_page()]
    client, transport = make_client(*outcomes)
    with client:
        result = (a4_discover(client, (journal,)) if batched else discover_journals(
            client, (journal,), date(2026, 1, 1), date(2026, 1, 31),
            retrieved_at=datetime(2026, 1, 31, tzinfo=timezone.utc)))
    assert not result.records
    assert result.coverage[0].status is (
        CoverageStatus.FAILED if alternate == "remote" else CoverageStatus.COMPLETE
    )
    assert bool(result.sources) is (alternate == "absent")
    assert (result.units[0].source is None) is (alternate == "remote")
    assert [(issue.severity, issue.issn) for issue in result.issues] == [
        (IssueSeverity.ERROR if alternate == "remote" else IssueSeverity.WARNING, "1541-0420")
    ]
    works_requests = [request for request in transport.requests if request.url.path == "/works"]
    assert len(works_requests) == (0 if alternate == "remote" else 1)
    assert not transport.outcomes


@pytest.mark.parametrize("discovery", [False, True])
def test_source_batch_remote_failure_recovered_by_singletons_remains_usable(discovery):
    source = fixture("source_biometrics.json")
    journal = JournalConfig(name="Biometrics", issn=("0006-341X", "1541-0420"))
    outcomes = [http_error(500)] * 3 + [source, source]
    if discovery:
        outcomes.append(a4_page())
    client, transport = make_client(*outcomes)
    with client:
        if discovery:
            result = a4_discover(client, (journal,))
            unit, = result.units
            assert unit.coverage.status is CoverageStatus.COMPLETE
        else:
            unit, = openalex_module.resolve_journal_sources_batched(client, (journal,))
            assert unit.status is None
        assert unit.source is not None
        assert unit.source.resolved_issns == journal.issn
        assert not unit.issues
    assert [request.url.path for request in transport.requests] == [
        "/sources", "/sources", "/sources",
        "/sources/issn:0006-341X", "/sources/issn:1541-0420",
    ] + (["/works"] if discovery else [])


def test_a4_works_batches_are_bounded_and_same_client_is_reused():
    journals = tuple(JournalConfig(name=f"Journal {i}", issn=(f"{i:04d}-000X",)) for i in range(101))
    sources = [dict(fixture("source_biometrics.json"), id=f"S{i}", display_name=j.name, issn=list(j.issn)) for i, j in enumerate(journals)]
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


@pytest.mark.parametrize("raw,expected", [
    (None, None),
    ("2026-09-26T08:19:03.415552", datetime(2026, 9, 26, 8, 19, 3, 415552, tzinfo=timezone.utc)),
    ("2026-09-26T08:19:03.415552Z", datetime(2026, 9, 26, 8, 19, 3, 415552, tzinfo=timezone.utc)),
    ("2026-09-26T16:19:03.415552+08:00", datetime(2026, 9, 26, 8, 19, 3, 415552, tzinfo=timezone.utc)),
    ("2026-09-26T08:19:03Z", datetime(2026, 9, 26, 8, 19, 3, tzinfo=timezone.utc)),
    ("2026-09-26T16:19:03+08:00", datetime(2026, 9, 26, 8, 19, 3, tzinfo=timezone.utc)),
])
def test_openalex_updated_date_adapter_preserves_utc_precision(raw, expected):
    assert openalex_module.parse_openalex_updated_date(raw) == expected
    if expected is not None:
        assert openalex_module.parse_openalex_updated_date(raw).tzinfo is timezone.utc


@pytest.mark.parametrize("raw", [
    "bad", "2026-09-26", "2026-02-30T08:19:03", "2026-09-26T25:19:03",
    "2026-09-26T08:19:03.1234567", "2026-09-26T08:19:03+00:99",
    "2026-09-26 08:19:03", " 2026-09-26T08:19:03", 123, True, {}, [],
    datetime(2026, 9, 26, tzinfo=timezone.utc),
])
def test_openalex_updated_date_adapter_rejects_invalid_raw_values(raw):
    with pytest.raises(ValueError):
        openalex_module.parse_openalex_updated_date(raw)


def test_generic_revision_parser_and_internal_model_still_reject_timezone_naive_values():
    from literature_monitor.provider_revision import parse_revision_timestamp

    raw = "2026-09-26T08:19:03.415552"
    with pytest.raises(ValueError):
        parse_revision_timestamp(raw)
    client, _ = make_client(a4_sources(fixture("source_biometrics.json")), a4_page(a4_work(updated_date=raw)))
    with client:
        record, = a4_discover(client, a4_journals()[:1]).records
    assert record.updated_at == datetime(2026, 9, 26, 8, 19, 3, 415552, tzinfo=timezone.utc)
    assert "updated_at" not in record.model_dump()
    assert "updated_at" not in json.loads(record.model_dump_json())
    assert "updated_at" not in record.to_evidence().model_dump()
    data = record.model_dump()
    data["updated_at"] = raw
    with pytest.raises(ValidationError):
        openalex_module.OpenAlexWorkRecord.model_validate(data)


def test_raw_updated_at_is_ignored_and_legacy_select_remains_unchanged():
    raw = a4_work()
    raw.pop("updated_date")
    raw["updated_at"] = "2026-09-26T08:19:03Z"
    client, _ = make_client(a4_sources(fixture("source_biometrics.json")), a4_page(raw))
    with client:
        result = a4_discover(client, a4_journals()[:1])
    assert result.records[0].updated_at is None
    assert not result.issues
    assert "updated_date" not in openalex_module.WORK_FIELDS.split(",")
    assert "updated_at" not in openalex_module.WORK_FIELDS.split(",")
    legacy, warnings = openalex_module._normalize_work(raw, result.sources[0], result.records[0].provenance.retrieved_at)
    assert legacy.updated_at is None
    assert not warnings


@pytest.mark.parametrize("mode", ["matching", "changed", "missing", "malformed"])
@pytest.mark.parametrize("partial", [False, True])
def test_provider_updated_date_drives_internal_version_state_binding(mode, partial):
    from literature_monitor.application.openalex_retrieval import hydrate_retained_openalex_versions
    from literature_monitor.application.provider_state import OpenAlexVersionState
    from literature_monitor.openalex import OpenAlexVersionHint

    revision = datetime(2026, 9, 26, 8, 19, 3, 415552, tzinfo=timezone.utc)
    old_hints = (OpenAlexVersionHint(source="doi", identifier="10.5555/old", version=OpenAlexVersion.ACCEPTED),)
    state = OpenAlexVersionState("https://openalex.org/W1", revision, revision, old_hints)
    raw_revision = {"matching": "2026-09-26T08:19:03.415552", "changed": "2026-09-27T08:19:03.415552",
                    "missing": None, "malformed": "bad"}[mode]
    raw = a4_work(updated_date=raw_revision)
    if partial:
        raw.update(title=None, authorships=None)
    outcomes = [a4_sources(fixture("source_biometrics.json")), a4_page(raw)]
    if mode != "matching":
        outcomes.append(a4_page({"id": "W1", "locations": []}))
    client, transport = make_client(*outcomes)
    with client:
        discovery = a4_discover(client, a4_journals()[:1])
        result = hydrate_retained_openalex_versions(client, discovery.records, version_state=(state,), retrieved_at=revision)
    assert len(transport.requests) == (2 if mode == "matching" else 3)
    assert len(result.records) == 1
    if mode == "matching":
        assert result.records[0].version_hints == old_hints
        assert result.reused_work_ids == (state.work_id,)
        assert not result.pending_changes
    else:
        assert result.hydrated_work_ids == (state.work_id,)
        assert len(result.pending_changes) == (1 if mode == "changed" else 0)
        if mode == "changed":
            assert result.pending_changes[0].hydrated_against_updated_at == discovery.records[0].updated_at
    assert discovery.coverage[0].status is CoverageStatus.COMPLETE
    assert not discovery.issues and not result.issues


def partial_discovery(raws, *, batched):
    if batched:
        client, transport = make_client(a4_sources(fixture("source_biometrics.json")), a4_page(*raws))
    else:
        client, transport = make_client(fixture("source_biometrics.json"), a4_page(*raws))
    with client:
        result = (a4_discover(client, a4_journals()[:1]) if batched else discover_journals(
            client, a4_journals()[:1], date(2026, 1, 1), date(2026, 1, 31),
            retrieved_at=datetime(2026, 1, 31, tzinfo=timezone.utc)))
    assert len(transport.requests) == 2
    assert transport.closed
    return result


@pytest.mark.parametrize("batched", [False, True])
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
    batched, title, doi, expected_title, expected_doi,
):
    result = partial_discovery([a4_work(title=title, doi=doi)], batched=batched)
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


@pytest.mark.parametrize("batched", [False, True])
@pytest.mark.parametrize(("title", "doi"), [(None, None), (" ", "bad"), ([], 42)])
def test_below_floor_records_are_excluded_without_execution_error(batched, title, doi):
    result = partial_discovery([a4_work(title=title, doi=doi)], batched=batched)
    assert not result.records
    assert result.coverage[0].status is CoverageStatus.COMPLETE
    assert len(result.issues) == 1
    assert result.issues[0].severity is IssueSeverity.WARNING
    assert not result.has_errors


@pytest.mark.parametrize("batched", [False, True])
@pytest.mark.parametrize("authorships", [None, {}, "bad", [], [None, {}, {"author": []}],
    [{"author": {"display_name": " "}, "raw_author_name": 42}],
])
def test_unusable_authorships_retain_empty_authors_without_issue(batched, authorships):
    result = partial_discovery([a4_work(authorships=authorships)], batched=batched)
    record, = result.records
    assert record.authors == record.to_evidence().authors == ()
    assert not result.issues
    assert result.coverage[0].status is CoverageStatus.COMPLETE
    canonical = canonicalize_records((record.to_evidence(),))
    assert not canonical.papers
    assert any(issue.stage == "insufficient_metadata" for issue in canonical.issues)


def test_partial_author_information_preserves_provider_order_and_safe_fields():
    authorships = [
        {"author": {"display_name": "First", "id": "bad", "orcid": "bad"}},
        {"author": None, "raw_author_name": "Second"},
        {"author": {"display_name": "Third", "id": "A123", "orcid": "0000-0001-2345-6789"}},
        {"author": {}},
    ]
    result = partial_discovery([a4_work(authorships=authorships)], batched=True)
    assert [(a.name, a.openalex_id, a.orcid) for a in result.records[0].authors] == [
        ("First", None, None), ("Second", None, None),
        ("Third", "https://openalex.org/A123", "0000-0001-2345-6789"),
    ]
    assert not result.issues


@pytest.mark.parametrize("batched", [False, True])
@pytest.mark.parametrize("abstract", [None, [], "bad", {"word": "bad"},
    {"word": [-1]}, {"word": [True]}, {"word": [0], "other": [0]}, {" ": [0]},
])
def test_missing_or_malformed_abstract_retains_evidence_without_issue(batched, abstract):
    result = partial_discovery([a4_work(abstract_inverted_index=abstract)], batched=batched)
    assert result.records[0].to_evidence().abstract is None
    assert not result.issues
    assert result.coverage[0].status is CoverageStatus.COMPLETE


@pytest.mark.parametrize("batched", [False, True])
@pytest.mark.parametrize("publication_date", [None, "bad", "2026-02-30", 42, {}])
def test_missing_or_invalid_publication_date_does_not_discard_evidence(batched, publication_date):
    result = partial_discovery([a4_work(publication_date=publication_date)], batched=batched)
    assert result.records[0].to_evidence().publication_date is None
    assert not result.issues
    assert result.coverage[0].status is CoverageStatus.COMPLETE


@pytest.mark.parametrize("batched", [False, True])
@pytest.mark.parametrize("work_id", [None, "", "bad", "A123", 42])
def test_missing_or_invalid_work_id_remains_structural_failure(batched, work_id):
    result = partial_discovery([a4_work(id=work_id)], batched=batched)
    assert not result.records
    assert result.has_errors
    assert result.coverage[0].status is CoverageStatus.PARTIAL
    assert all(issue.severity is IssueSeverity.ERROR for issue in result.issues)


@pytest.mark.parametrize("batched", [False, True])
@pytest.mark.parametrize("primary_location", [None, {}, {"source": None},
    {"source": {}}, {"source": {"id": None}},
])
def test_single_source_missing_nested_identity_uses_verified_request_scope(batched, primary_location):
    result = partial_discovery([a4_work(primary_location=primary_location)], batched=batched)
    record, = result.records
    assert record.source_id == result.sources[0].openalex_id == "https://openalex.org/S8265502"
    assert record.to_evidence().journal == result.sources[0].display_name
    assert not result.issues
    assert result.coverage[0].status is CoverageStatus.COMPLETE


@pytest.mark.parametrize("batched", [False, True])
@pytest.mark.parametrize("source_id", ["S999", "invalid Source identity"])
def test_single_source_explicit_conflict_or_invalid_identity_is_never_overridden(batched, source_id):
    raw = a4_work()
    raw["primary_location"]["source"]["id"] = source_id
    result = partial_discovery([raw], batched=batched)
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


@pytest.mark.parametrize("batched", [False, True])
def test_complete_traversal_coverage_is_independent_of_field_completeness(batched):
    records = [a4_work("W1", title=None), a4_work("W2", doi=None),
        a4_work("W3", authorships=None), a4_work("W4", abstract_inverted_index=None),
        a4_work("W5", updated_date=None, publication_date=None), a4_work("W6", title=None, doi=None)]
    result = partial_discovery(records, batched=batched)
    assert len(result.records) == 5
    assert not result.has_errors
    assert result.coverage[0].status is CoverageStatus.COMPLETE
    assert len(result.issues) == 1
    assert "neither a valid DOI nor a usable title" in result.issues[0].message


@pytest.mark.parametrize("batched", [False, True])
def test_normal_source_absence_is_unavailable_with_warning(batched):
    outcomes = [a4_sources()] if batched else []
    client, transport = make_client(*outcomes, http_error(404), http_error(404))
    journal = JournalConfig(name="Biometrics", issn=("0006-341X", "1541-0420"))
    with client:
        result = (a4_discover(client, (journal,)) if batched else discover_journals(
            client, (journal,), date(2026, 1, 1), date(2026, 1, 31)))
    assert result.coverage[0].status is CoverageStatus.UNAVAILABLE
    assert not result.sources and not result.records and not result.has_errors
    assert all(issue.severity is IssueSeverity.WARNING for issue in result.issues)
    assert len(transport.requests) == (3 if batched else 2)
