import json
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

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


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class SequenceOpener:
    def __init__(self, *outcomes: dict[str, Any] | Exception) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[tuple[Any, float]] = []

    def __call__(self, request: Any, *, timeout: float) -> FakeResponse:
        self.requests.append((request, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)


def http_error(code: int) -> HTTPError:
    return HTTPError("https://api.openalex.org/test", code, "failure", {}, None)


def make_client(*outcomes: dict[str, Any] | Exception) -> tuple[OpenAlexClient, SequenceOpener]:
    opener = SequenceOpener(*outcomes)
    return OpenAlexClient(opener=opener, sleep=lambda _: None), opener


def test_singleton_requests_each_issn_independently_and_uses_bearer_key() -> None:
    payload = fixture("source_cybernetics.json")
    opener = SequenceOpener(payload, payload)
    client = OpenAlexClient(api_key="secret", opener=opener, sleep=lambda _: None)
    journal = JournalConfig(
        name="IEEE Transactions on Cybernetics",
        issn=("2168-2267", "2168-2275"),
    )

    source, issues = resolve_journal_source(client, journal)

    assert source is not None
    assert source.openalex_id == "https://openalex.org/S4210191041"
    assert source.resolved_issns == journal.issn
    assert not issues
    assert len(opener.requests) == 2
    assert [urlparse(request.full_url).path for request, _ in opener.requests] == [
        "/sources/issn:2168-2267",
        "/sources/issn:2168-2275",
    ]
    assert all(
        "filter" not in parse_qs(urlparse(request.full_url).query)
        for request, _ in opener.requests
    )
    assert all(
        request.get_header("Authorization") == "Bearer secret"
        for request, _ in opener.requests
    )
    assert all(
        request.get_header("User-agent") == "literature-monitor/0.4.2"
        for request, _ in opener.requests
    )
    assert all(timeout == 30 for _, timeout in opener.requests)


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
    client, _ = make_client(
        fixture("source_cybernetics.json"),
        TimeoutError("timed out"),
        TimeoutError("timed out"),
        TimeoutError("timed out"),
    )
    journal = JournalConfig(
        name="IEEE Transactions on Cybernetics",
        issn=("2168-2267", "2168-2275"),
    )

    source, issues = resolve_journal_source(client, journal)

    assert source is not None
    assert source.openalex_id == "https://openalex.org/S4210191041"
    assert len(issues) == 1
    assert issues[0].issn == "2168-2275"
    assert issues[0].severity is IssueSeverity.WARNING
    assert "incomplete ISSN verification" in issues[0].message
    assert "timed out" in issues[0].message


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
    opener = SequenceOpener(http_error(429), http_error(500), fixture("source_biometrics.json"))
    client = OpenAlexClient(opener=opener, sleep=delays.append)

    payload = client.get_source_by_issn("0006-341X")

    assert payload["id"] == "https://openalex.org/S8265502"
    assert delays == [1, 2]
    assert len(opener.requests) == 3


def test_timeout_retries_use_injected_backoff_without_real_sleep() -> None:
    delays: list[float] = []
    opener = SequenceOpener(
        TimeoutError("timed out"),
        TimeoutError("timed out"),
        fixture("source_biometrics.json"),
    )
    client = OpenAlexClient(opener=opener, sleep=delays.append)

    payload = client.get_source_by_issn("0006-341X")

    assert payload["id"] == "https://openalex.org/S8265502"
    assert delays == [1, 2]
    assert len(opener.requests) == 3


def test_connection_retries_use_injected_backoff_without_real_sleep() -> None:
    delays: list[float] = []
    opener = SequenceOpener(
        URLError("connection refused"),
        URLError("connection refused"),
        fixture("source_biometrics.json"),
    )
    client = OpenAlexClient(opener=opener, sleep=delays.append)

    payload = client.get_source_by_issn("0006-341X")

    assert payload["id"] == "https://openalex.org/S8265502"
    assert delays == [1, 2]
    assert len(opener.requests) == 3


@pytest.mark.parametrize(
    "transient_failure",
    (http_error(429), http_error(500), TimeoutError("timed out")),
)
def test_request_progress_reports_retry_before_backoff_and_success(
    transient_failure: Exception,
) -> None:
    trace: list[tuple[str, object]] = []
    opener = SequenceOpener(transient_failure, fixture("source_biometrics.json"))

    def report(event: ProgressEvent) -> None:
        assert event.activity is not None
        trace.append(("activity", event.activity))

    def sleep(delay: float) -> None:
        trace.append(("sleep", delay))

    client = OpenAlexClient(opener=opener, sleep=sleep)
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
    assert len(opener.requests) == 2
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


def test_non_retryable_client_error_is_not_retried() -> None:
    client, opener = make_client(http_error(400))

    with pytest.raises(OpenAlexRequestError, match="HTTP 400"):
        client.get_source_by_issn("0006-341X")

    assert len(opener.requests) == 1


def test_discovery_pages_normalizes_records_and_builds_venue_first_query() -> None:
    client, opener = make_client(
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

    work_requests = [request for request, _ in opener.requests[1:]]
    first_query = parse_qs(urlparse(work_requests[0].full_url).query)
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
    assert parse_qs(urlparse(work_requests[1].full_url).query)["cursor"] == ["next-page"]
    assert all(
        "search" not in parse_qs(urlparse(request.full_url).query)
        for request in work_requests
    )


def test_source_resolution_and_discovery_report_natural_progress_without_extra_requests() -> None:
    client, opener = make_client(
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
    assert len(opener.requests) == 3
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
        TimeoutError("timed out"),
        TimeoutError("timed out"),
        TimeoutError("timed out"),
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
        TimeoutError("timed out"),
        TimeoutError("timed out"),
        TimeoutError("timed out"),
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
    client, opener = make_client(fixture("source_biometrics.json"), page)
    events: list[ProgressEvent] = []

    result = discover_journals(
        client,
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
        progress_callback=events.append,
    )

    assert not result.has_errors
    assert len(opener.requests) == 2
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
    bad_page["results"][0]["title"] = None
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


def test_partial_records_preserve_usable_result_and_report_issues() -> None:
    client, _ = make_client(
        fixture("source_biometrics.json"), fixture("works_partial.json")
    )

    result = discover_journals(
        client,
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert len(result.records) == 1
    record = result.records[0]
    assert record.metadata.publication_date is None
    assert record.metadata.abstract is None
    assert record.external_ids.doi is None
    assert result.has_errors
    assert sum(issue.severity is IssueSeverity.WARNING for issue in result.issues) == 3
    assert any("no usable authors" in issue.message for issue in result.issues)


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
    client, opener = make_client()

    with pytest.raises(ValueError, match="from_date"):
        discover_journals(
            client,
            (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
            date(2026, 2, 1),
            date(2026, 1, 1),
        )

    assert not opener.requests
