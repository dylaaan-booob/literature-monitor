import json
import socket
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import ValidationError

from literature_monitor.crossref import (
    CrossrefClient,
    CrossrefDateKind,
    CrossrefNotFoundError,
    CrossrefPartialDate,
    CrossrefRecordError,
    CrossrefRequestError,
    EnrichedWorkRecord,
    EnrichmentIssueSeverity,
    discover_crossref_journals,
    enrich_records,
    normalize_crossref_discovered_work,
    normalize_crossref_work,
)
from literature_monitor.config import JournalConfig
from literature_monitor.coverage import CoverageComponent, CoverageStatus
from literature_monitor.models import (
    Author,
    CanonicalMetadata,
    ExternalIds,
    MetadataSource,
    ProviderRecordRef,
)
from literature_monitor.openalex import OpenAlexWorkRecord
from literature_monitor.progress import ActivityKind, ActivityUpdate, ProgressEvent
from literature_monitor.retrieval import assemble_provider_evidence


FIXTURES = Path(__file__).parent / "fixtures" / "crossref"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(
        self,
        payload: object,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload
        self.headers = headers or {}

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode()


class SequenceOpener:
    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[tuple[Any, float]] = []

    def __call__(self, request: Any, *, timeout: float) -> FakeResponse:
        self.requests.append((request, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, FakeResponse):
            return outcome
        return FakeResponse(outcome)


def http_error(code: int) -> HTTPError:
    return HTTPError("https://api.crossref.org/test", code, "failure", {}, None)


def openalex_record(identifier: str, doi: str | None) -> OpenAlexWorkRecord:
    timestamp = datetime(2026, 9, 18, tzinfo=timezone.utc)
    return OpenAlexWorkRecord(
        metadata=CanonicalMetadata(title=f"Paper {identifier}", journal="Biometrics"),
        external_ids=ExternalIds(
            openalex=f"https://openalex.org/{identifier}",
            doi=doi,
        ),
        authors=(Author(name="Ada Author"),),
        source_id="https://openalex.org/S8265502",
        provenance=MetadataSource(
            provider="openalex",
            record_id=f"https://openalex.org/{identifier}",
            retrieved_at=timestamp,
        ),
    )


def payload_for(doi: str) -> dict[str, Any]:
    payload = fixture("work_complete.json")
    payload["message"]["DOI"] = doi
    return payload


def list_payload(
    items: list[object],
    cursor: object = "next",
    *,
    total_results: object = None,
) -> dict[str, Any]:
    message: dict[str, object] = {"items": items, "next-cursor": cursor}
    if total_results is not None:
        message["total-results"] = total_results
    return {
        "status": "ok",
        "message-type": "work-list",
        "message": message,
    }


def test_client_uses_versioned_encoded_doi_endpoint_and_polite_headers() -> None:
    opener = SequenceOpener(payload_for("10.1002/(abc)/x"))
    client = CrossrefClient(
        mailto=" monitor@example.com ",
        timeout=17,
        opener=opener,
        sleep=lambda _: None,
    )

    response = client.get_work_by_doi(" HTTPS://DOI.ORG/10.1002/(ABC)/X ")

    assert response["message"]["DOI"] == "10.1002/(abc)/x"
    request, timeout = opener.requests[0]
    parsed = urlparse(request.full_url)
    assert parsed.path == "/v1/works/10.1002%2F%28abc%29%2Fx"
    assert parse_qs(parsed.query) == {"mailto": ["monitor@example.com"]}
    assert request.get_header("Accept") == "application/json"
    assert request.get_header("User-agent") == "literature-monitor/0.4.2"
    assert timeout == 17


def test_journal_client_uses_publication_filters_and_encoded_cursor_pages() -> None:
    opener = SequenceOpener(
        list_payload([{}, {}], "next / cursor"),
        list_payload([{}], "unused"),
    )
    client = CrossrefClient(opener=opener, sleep=lambda _: None)

    pages = list(
        client.iter_journal_work_pages(
            "0006-341X",
            date(2026, 1, 1),
            date(2026, 1, 31),
            rows=2,
        )
    )

    assert len(pages) == 2
    first = urlparse(opener.requests[0][0].full_url)
    second = urlparse(opener.requests[1][0].full_url)
    assert first.path == "/v1/journals/0006-341X/works"
    assert parse_qs(first.query) == {
        "filter": ["from-pub-date:2026-01-01,until-pub-date:2026-01-31"],
        "rows": ["2"],
        "cursor": ["*"],
    }
    assert parse_qs(second.query)["cursor"] == ["next / cursor"]
    assert "next+%2F+cursor" in second.query


def test_journal_client_defaults_to_rows_1000() -> None:
    opener = SequenceOpener(list_payload([{}]))
    client = CrossrefClient(opener=opener, sleep=lambda _: None)

    pages = list(
        client.iter_journal_work_pages(
            "0006-341X",
            date(2026, 1, 1),
            date(2026, 1, 31),
        )
    )

    assert len(pages) == 1
    query = parse_qs(urlparse(opener.requests[0][0].full_url).query)
    assert query["rows"] == ["1000"]


def test_journal_client_traverses_multiple_default_size_cursor_pages() -> None:
    opener = SequenceOpener(
        list_payload([{}] * 1000, "second-page"),
        list_payload([{}], "unused"),
    )
    client = CrossrefClient(opener=opener, sleep=lambda _: None)

    pages = list(
        client.iter_journal_work_pages(
            "0006-341X",
            date(2026, 1, 1),
            date(2026, 1, 31),
        )
    )

    assert len(pages) == 2
    first_query = parse_qs(urlparse(opener.requests[0][0].full_url).query)
    second_query = parse_qs(urlparse(opener.requests[1][0].full_url).query)
    assert first_query["rows"] == ["1000"]
    assert first_query["cursor"] == ["*"]
    assert second_query["rows"] == ["1000"]
    assert second_query["cursor"] == ["second-page"]


def test_client_uses_response_rate_headers_to_pace_later_requests() -> None:
    delays: list[float] = []
    opener = SequenceOpener(
        FakeResponse(
            payload_for("10.5555/first"),
            headers={
                "X-Rate-Limit-Limit": "4",
                "X-Rate-Limit-Interval": "2s",
            },
        ),
        FakeResponse(
            payload_for("10.5555/second"),
            headers={
                "X-Rate-Limit-Limit": "2",
                "X-Rate-Limit-Interval": "3s",
            },
        ),
        payload_for("10.5555/third"),
    )
    client = CrossrefClient(opener=opener, sleep=delays.append)

    client.get_work_by_doi("10.5555/first")
    client.get_work_by_doi("10.5555/second")
    client.get_work_by_doi("10.5555/third")

    assert delays == [0.5, 1.5]
    assert len(opener.requests) == 3


def test_client_missing_rate_headers_do_not_add_pacing_or_break_requests() -> None:
    delays: list[float] = []
    opener = SequenceOpener(
        payload_for("10.5555/first"),
        payload_for("10.5555/second"),
    )
    client = CrossrefClient(opener=opener, sleep=delays.append)

    assert client.get_work_by_doi("10.5555/first")["status"] == "ok"
    assert client.get_work_by_doi("10.5555/second")["status"] == "ok"

    assert delays == []


@pytest.mark.parametrize(
    ("limit", "interval"),
    [
        ("", "1s"),
        ("0", "1s"),
        ("not-a-number", "1s"),
        ("50", ""),
        ("50", "0s"),
        ("50", "1m"),
        ("50", "not-an-interval"),
    ],
)
def test_client_malformed_rate_headers_do_not_break_requests_or_enable_pacing(
    limit: str,
    interval: str,
) -> None:
    delays: list[float] = []
    opener = SequenceOpener(
        FakeResponse(
            payload_for("10.5555/first"),
            headers={
                "X-Rate-Limit-Limit": limit,
                "X-Rate-Limit-Interval": interval,
            },
        ),
        payload_for("10.5555/second"),
    )
    client = CrossrefClient(opener=opener, sleep=delays.append)

    assert client.get_work_by_doi("10.5555/first")["status"] == "ok"
    assert client.get_work_by_doi("10.5555/second")["status"] == "ok"

    assert delays == []


def test_client_rate_pacing_reports_waiting_activity_with_request_identity() -> None:
    trace: list[tuple[str, object]] = []
    opener = SequenceOpener(
        FakeResponse(
            payload_for("10.5555/first"),
            headers={
                "X-Rate-Limit-Limit": "5",
                "X-Rate-Limit-Interval": "1s",
            },
        ),
        payload_for("10.5555/second"),
    )

    def report(event: ProgressEvent) -> None:
        assert event.activity is not None
        trace.append(("activity", event.activity))

    def sleep(delay: float) -> None:
        trace.append(("sleep", delay))

    client = CrossrefClient(opener=opener, sleep=sleep)
    client.get_work_by_doi("10.5555/first")
    activity = ActivityUpdate(
        kind=ActivityKind.WORKING,
        source="crossref",
        operation="doi_supplement",
        label="Looking up Crossref DOI metadata",
        detail="DOI 10.5555/second",
        current=2,
        total=5,
        unit="doi",
    )

    client.get_work_by_doi(
        "10.5555/second",
        progress_callback=report,
        activity=activity,
    )

    assert [kind for kind, _ in trace] == [
        "activity",
        "sleep",
        "activity",
        "activity",
    ]
    waiting, (_sleep_kind, delay), request, response = trace
    assert waiting[1].kind is ActivityKind.WAITING
    assert waiting[1].label == "Waiting for Crossref rate limit"
    assert delay == 0.2
    assert request[1].label == "Requesting Crossref"
    assert response[1].label == "Received Crossref response"
    identities = {
        (item.source, item.operation, item.unit, item.current, item.total)
        for kind, item in trace
        if kind == "activity"
    }
    assert identities == {("crossref", "doi_supplement", "doi", 2, 5)}


def test_client_retry_wait_respects_slower_known_provider_pacing() -> None:
    delays: list[float] = []
    opener = SequenceOpener(
        FakeResponse(
            payload_for("10.5555/first"),
            headers={
                "X-Rate-Limit-Limit": "1",
                "X-Rate-Limit-Interval": "5s",
            },
        ),
        http_error(429),
        payload_for("10.5555/second"),
    )
    client = CrossrefClient(opener=opener, sleep=delays.append)

    client.get_work_by_doi("10.5555/first")
    assert client.get_work_by_doi("10.5555/second")["status"] == "ok"

    assert delays == [5, 5]
    assert len(opener.requests) == 3


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "ok", "message-type": "work-list", "message": []},
        {"status": "ok", "message-type": "work-list", "message": {}},
        {"status": "bad", "message-type": "work-list", "message": {"items": []}},
    ],
)
def test_journal_client_rejects_malformed_list_envelopes(payload: object) -> None:
    client = CrossrefClient(opener=SequenceOpener(payload), sleep=lambda _: None)

    with pytest.raises(CrossrefRequestError):
        list(
            client.iter_journal_work_pages(
                "0006-341X",
                date(2026, 1, 1),
                date(2026, 1, 31),
            )
        )


@pytest.mark.parametrize("cursor", [None, 42, ""])
def test_journal_client_rejects_missing_or_invalid_cursor_on_full_page(
    cursor: object,
) -> None:
    client = CrossrefClient(
        opener=SequenceOpener(list_payload([{}], cursor)),
        sleep=lambda _: None,
    )

    with pytest.raises(CrossrefRequestError, match="next cursor"):
        list(
            client.iter_journal_work_pages(
                "0006-341X",
                date(2026, 1, 1),
                date(2026, 1, 31),
                rows=1,
            )
        )


def test_journal_client_rejects_repeated_cursor() -> None:
    client = CrossrefClient(
        opener=SequenceOpener(list_payload([{}], "*")),
        sleep=lambda _: None,
    )

    with pytest.raises(CrossrefRequestError, match="repeated cursor"):
        list(
            client.iter_journal_work_pages(
                "0006-341X",
                date(2026, 1, 1),
                date(2026, 1, 31),
                rows=1,
            )
        )


@pytest.mark.parametrize(
    "outcome",
    [http_error(429), http_error(500), TimeoutError("timed out"), URLError("down"), socket.gaierror("dns")],
)
def test_client_retries_transient_failures(outcome: Exception) -> None:
    delays: list[float] = []
    opener = SequenceOpener(outcome, outcome, payload_for("10.5555/retry"))
    client = CrossrefClient(opener=opener, sleep=delays.append)

    assert client.get_work_by_doi("10.5555/retry")["status"] == "ok"
    assert delays == [1, 2]
    assert len(opener.requests) == 3


@pytest.mark.parametrize(
    "transient_failure",
    (http_error(429), http_error(500), TimeoutError("timed out")),
)
def test_client_progress_reports_retry_before_backoff_and_success(
    transient_failure: Exception,
) -> None:
    trace: list[tuple[str, object]] = []
    opener = SequenceOpener(
        transient_failure,
        payload_for("10.5555/retry-progress"),
    )

    def report(event: ProgressEvent) -> None:
        assert event.activity is not None
        trace.append(("activity", event.activity))

    def sleep(delay: float) -> None:
        trace.append(("sleep", delay))

    client = CrossrefClient(opener=opener, sleep=sleep)
    activity = ActivityUpdate(
        kind=ActivityKind.WORKING,
        source="crossref",
        operation="doi_supplement",
        label="Looking up Crossref DOI metadata",
        detail="DOI 10.5555/retry-progress",
        current=2,
        total=5,
        unit="doi",
    )

    response = client.get_work_by_doi(
        "10.5555/retry-progress",
        progress_callback=report,
        activity=activity,
    )

    assert response["status"] == "ok"
    assert len(opener.requests) == 2
    assert [kind for kind, _ in trace] == [
        "activity",
        "activity",
        "sleep",
        "activity",
        "activity",
    ]
    request, retry, (_sleep_kind, delay), request_again, response_event = trace
    assert request[1].label == "Requesting Crossref"
    assert retry[1].kind is ActivityKind.RETRYING
    assert delay == 1
    assert request_again[1].label == "Requesting Crossref"
    assert response_event[1].label == "Received Crossref response"
    identities = {
        (item.source, item.operation, item.unit, item.current, item.total)
        for kind, item in trace
        if kind == "activity"
    }
    assert identities == {("crossref", "doi_supplement", "doi", 2, 5)}


def test_client_distinguishes_not_found_from_request_failure() -> None:
    missing_client = CrossrefClient(
        opener=SequenceOpener(http_error(404)), sleep=lambda _: None
    )
    bad_client = CrossrefClient(
        opener=SequenceOpener(http_error(400)), sleep=lambda _: None
    )

    with pytest.raises(CrossrefNotFoundError):
        missing_client.get_work_by_doi("10.5555/missing")
    with pytest.raises(CrossrefRequestError, match="HTTP 400"):
        bad_client.get_work_by_doi("10.5555/bad")


def test_client_reports_exhausted_transient_failure() -> None:
    opener = SequenceOpener(*(TimeoutError("timed out") for _ in range(3)))
    client = CrossrefClient(opener=opener, sleep=lambda _: None)

    with pytest.raises(CrossrefRequestError, match="timed out"):
        client.get_work_by_doi("10.5555/timeout")

    assert len(opener.requests) == 3


@pytest.mark.parametrize("payload", [b"not json", ["not", "an", "object"]])
def test_client_rejects_invalid_json_transport(payload: object) -> None:
    client = CrossrefClient(opener=SequenceOpener(payload), sleep=lambda _: None)

    with pytest.raises(CrossrefRequestError):
        client.get_work_by_doi("10.5555/bad-json")


def test_complete_work_normalization_preserves_provider_evidence() -> None:
    timestamp = datetime(2026, 9, 18, tzinfo=timezone.utc)

    record, warnings = normalize_crossref_work(
        fixture("work_complete.json"),
        "HTTPS://DOI.ORG/10.1093/BIOMTC/UJAG004",
        timestamp,
    )

    assert not warnings
    assert record.doi == "10.1093/biomtc/ujag004"
    assert record.title == "A Complete Crossref Study"
    assert record.journal == "Biometrics"
    assert record.abstract == "An important result & extension."
    assert record.work_type == "journal-article"
    assert record.dates == (
        CrossrefPartialDate(
            kind=CrossrefDateKind.PUBLISHED,
            year=2026,
            month=1,
            day=20,
        ),
    )
    assert record.provenance.provider == "crossref"
    assert record.provenance.record_id == record.doi
    assert record.provenance.retrieved_at == timestamp

    evidence = record.to_evidence()
    assert evidence.provenance == record.provenance
    assert evidence.title == record.title
    assert evidence.journal == record.journal
    assert evidence.abstract == record.abstract
    assert evidence.authors == ()
    assert evidence.external_ids.doi == record.doi
    assert evidence.external_ids.crossref == record.provenance.record_id
    assert [
        (item.kind.value, item.year, item.month, item.day)
        for item in evidence.dates
    ] == [
        (item.kind.value, item.year, item.month, item.day)
        for item in record.dates
    ]

    enriched = EnrichedWorkRecord(
        openalex=openalex_record("W1", record.doi),
        crossref=record,
    ).to_evidence()
    assert [item.provenance.provider for item in enriched] == [
        "openalex",
        "crossref",
    ]
    assert enriched[1].supplements == (
        ProviderRecordRef(
            provider="openalex",
            record_id="https://openalex.org/W1",
        ),
    )


def test_discovered_work_preserves_author_order_orcid_and_venue_ids() -> None:
    message = payload_for("10.5555/discovered")["message"]
    message["author"] = [
        {
            "given": "Ada",
            "family": "Lovelace",
            "ORCID": "https://orcid.org/0000-0002-1825-0097",
        },
        {"name": "Statistics Consortium"},
        {"given": "Missing family", "ORCID": "not-an-orcid"},
        42,
        {},
    ]
    message["ISSN"] = ["0006-341X", "bad"]
    message["issn-type"] = [
        {"type": "electronic", "value": "1541-0420"},
        {"type": "bad", "value": "1234-5678"},
    ]

    record, warnings = normalize_crossref_discovered_work(
        message,
        datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    assert [author.name for author in record.authors] == [
        "Ada Lovelace",
        "Statistics Consortium",
        "Missing family",
    ]
    assert record.authors[0].orcid == "https://orcid.org/0000-0002-1825-0097"
    assert record.authors[2].orcid is None
    assert record.issns == ("0006-341X", "1541-0420")
    assert record.to_evidence().authors == record.authors
    assert any("invalid ORCID" in warning for warning in warnings)
    assert any("invalid author entry" in warning for warning in warnings)
    assert any("without a usable name" in warning for warning in warnings)
    assert sum("invalid ISSN entry" in warning for warning in warnings) == 2


class JournalDiscoveryClient:
    def __init__(self, outcomes: dict[str, object]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    def iter_journal_work_pages(
        self,
        issn: str,
        from_date: date,
        to_date: date,
    ) -> Any:
        self.calls.append(issn)
        outcome = self.outcomes[issn]
        if isinstance(outcome, Exception):
            raise outcome
        return iter(outcome)


def discovered_message(
    doi: str,
    *,
    journal: str = "Biometrics",
    issns: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "DOI": doi,
        "title": [f"Paper {doi}"],
        "container-title": [journal],
        "author": [{"given": "Ada", "family": "Author"}],
        "ISSN": issns,
        "published": {"date-parts": [[2026, 1, 10]]},
        "relation": {},
        "type": "journal-article",
    }


def test_discovery_isolates_issn_failures_and_validates_venue_identity() -> None:
    valid = discovered_message("10.5555/valid", issns=["1541-0420"])
    mismatch = discovered_message("10.5555/wrong", issns=["0006-3444"])
    client = JournalDiscoveryClient(
        {
            "0006-341X": CrossrefRequestError("temporary failure"),
            "1541-0420": [list_payload([valid, mismatch])],
        }
    )

    result = discover_crossref_journals(
        client,  # type: ignore[arg-type]
        (JournalConfig(name="Biometrics", issn=("0006-341X", "1541-0420")),),
        date(2026, 1, 1),
        date(2026, 1, 31),
        retrieved_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    assert client.calls == ["0006-341X", "1541-0420"]
    assert [unit.issn for unit in result.units] == client.calls
    assert result.units[0].records == ()
    assert result.units[1].records == result.records
    assert [issue.stage for issue in result.units[0].issues] == ["work_retrieval"]
    assert [issue.stage for issue in result.units[1].issues] == ["venue_validation"]
    assert tuple(issue for unit in result.units for issue in unit.issues) == result.issues
    assert tuple(unit.coverage for unit in result.units) == result.coverage
    assert [record.doi for record in result.records] == ["10.5555/valid"]
    assert result.has_errors
    assert {issue.stage for issue in result.issues} >= {
        "work_retrieval",
        "venue_validation",
    }
    assert [
        (unit.issn, unit.status) for unit in result.coverage
    ] == [
        ("0006-341X", CoverageStatus.FAILED),
        ("1541-0420", CoverageStatus.COMPLETE),
    ]
    assert all(
        unit.component is CoverageComponent.CROSSREF_DISCOVERY
        and unit.journal == "Biometrics"
        for unit in result.coverage
    )


def test_discovery_keeps_records_from_pages_before_later_request_failure() -> None:
    valid = discovered_message("10.5555/first-page", issns=["0006-341X"])

    class PartialFailureClient:
        def iter_journal_work_pages(
            self,
            issn: str,
            from_date: date,
            to_date: date,
        ) -> Any:
            yield list_payload([valid])
            raise CrossrefRequestError("later page failed")

    result = discover_crossref_journals(
        PartialFailureClient(),  # type: ignore[arg-type]
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
        retrieved_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    assert [record.doi for record in result.records] == ["10.5555/first-page"]
    assert result.has_errors
    assert [issue.stage for issue in result.issues] == ["work_retrieval"]
    assert result.coverage[0].status is CoverageStatus.PARTIAL


def test_discovery_later_page_not_found_is_partial() -> None:
    valid = discovered_message("10.5555/first-page-404", issns=["0006-341X"])

    class PartialNotFoundClient:
        def iter_journal_work_pages(
            self,
            issn: str,
            from_date: date,
            to_date: date,
        ) -> Any:
            yield list_payload([valid])
            raise CrossrefNotFoundError("later page was not found")

    result = discover_crossref_journals(
        PartialNotFoundClient(),  # type: ignore[arg-type]
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
        retrieved_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    assert [record.doi for record in result.records] == ["10.5555/first-page-404"]
    assert [issue.stage for issue in result.issues] == ["journal_not_found"]
    assert result.coverage[0].status is CoverageStatus.PARTIAL


def test_discovery_uses_alternate_issn_after_not_found() -> None:
    valid = discovered_message("10.5555/alternate", issns=["1541-0420"])
    client = JournalDiscoveryClient(
        {
            "0006-341X": CrossrefNotFoundError("ISSN was not found"),
            "1541-0420": [list_payload([valid])],
        }
    )

    result = discover_crossref_journals(
        client,  # type: ignore[arg-type]
        (JournalConfig(name="Biometrics", issn=("0006-341X", "1541-0420")),),
        date(2026, 1, 1),
        date(2026, 1, 31),
        retrieved_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    assert [record.doi for record in result.records] == ["10.5555/alternate"]
    assert [issue.stage for issue in result.issues] == ["journal_not_found"]
    assert not result.has_errors
    assert [unit.status for unit in result.coverage] == [
        CoverageStatus.UNAVAILABLE,
        CoverageStatus.COMPLETE,
    ]


def test_discovery_zero_results_is_complete() -> None:
    client = JournalDiscoveryClient({"0006-341X": [list_payload([])]})

    result = discover_crossref_journals(
        client,  # type: ignore[arg-type]
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
        retrieved_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    assert result.records == ()
    assert result.coverage[0].status is CoverageStatus.COMPLETE


def test_discovery_field_warning_keeps_complete_coverage() -> None:
    message = discovered_message("10.5555/warning", issns=["0006-341X"])
    message["abstract"] = 42
    client = JournalDiscoveryClient(
        {"0006-341X": [list_payload([message])]}
    )

    result = discover_crossref_journals(
        client,  # type: ignore[arg-type]
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
        retrieved_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    assert [record.doi for record in result.records] == ["10.5555/warning"]
    assert any(issue.stage == "field_normalization" for issue in result.issues)
    assert result.coverage[0].status is CoverageStatus.COMPLETE


def test_discovery_progress_uses_total_results_without_extra_request() -> None:
    first = discovered_message("10.5555/first", issns=["0006-341X"])
    second = discovered_message("10.5555/second", issns=["0006-341X"])
    opener = SequenceOpener(list_payload([first, second], total_results=2))
    client = CrossrefClient(opener=opener, sleep=lambda _: None)
    events: list[ProgressEvent] = []

    result = discover_crossref_journals(
        client,
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
        progress_callback=events.append,
    )

    assert [record.doi for record in result.records] == [
        "10.5555/first",
        "10.5555/second",
    ]
    assert len(opener.requests) == 1
    activities = [event.activity for event in events if event.activity is not None]
    issn_activity = [
        item
        for item in activities
        if item.operation == "journal_discovery:0:0"
    ]
    assert (issn_activity[0].current, issn_activity[0].total) == (0, None)
    page = next(
        item for item in issn_activity if item.label == "Retrieved Crossref page"
    )
    assert (page.current, page.total) == (2, 2)
    assert issn_activity[-1].label == "Completed Crossref ISSN discovery"
    assert (issn_activity[-1].current, issn_activity[-1].total) == (2, 2)
    journal = next(
        item
        for item in activities
        if item.label == "Completed Crossref journal discovery"
    )
    assert (journal.current, journal.total, journal.unit) == (1, 1, "issn")


@pytest.mark.parametrize("total_results", (None, "2", -1, 0, True))
def test_discovery_unusable_total_results_stays_indeterminate(
    total_results: object,
) -> None:
    item = discovered_message("10.5555/indeterminate", issns=["0006-341X"])
    opener = SequenceOpener(
        list_payload([item], total_results=total_results)
    )
    client = CrossrefClient(opener=opener, sleep=lambda _: None)
    events: list[ProgressEvent] = []

    result = discover_crossref_journals(
        client,
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
        progress_callback=events.append,
    )

    assert not result.has_errors
    assert len(opener.requests) == 1
    issn_activity = [
        event.activity
        for event in events
        if event.activity is not None
        and event.activity.operation == "journal_discovery:0:0"
    ]
    assert issn_activity
    assert all(item.total is None for item in issn_activity)


def test_discovery_skips_malformed_item_without_discarding_valid_peer() -> None:
    valid = discovered_message("10.5555/valid-peer", issns=["0006-341X"])
    client = JournalDiscoveryClient(
        {"0006-341X": [list_payload([{"title": ["No DOI"]}, valid])]}
    )

    result = discover_crossref_journals(
        client,  # type: ignore[arg-type]
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
        retrieved_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    assert [record.doi for record in result.records] == ["10.5555/valid-peer"]
    assert any(issue.stage == "record_normalization" for issue in result.issues)
    assert result.coverage[0].status is CoverageStatus.PARTIAL


@pytest.mark.parametrize(
    ("message", "accepted"),
    [
        (discovered_message("10.5555/issn", issns=["0006-341X"]), True),
        (discovered_message("10.5555/alternate", issns=["1541-0420"]), True),
        (
            discovered_message(
                "10.5555/mismatch",
                journal="Biometrics",
                issns=["0006-3444"],
            ),
            False,
        ),
        (discovered_message("10.5555/name", journal=" BIOMETRICS "), True),
        (discovered_message("10.5555/wrong-name", journal="Other"), False),
    ],
)
def test_discovery_venue_validation_paths(
    message: dict[str, Any],
    accepted: bool,
) -> None:
    if message.get("ISSN") is None:
        message.pop("ISSN", None)
    from literature_monitor.crossref import crossref_record_matches_journal

    journal = JournalConfig(name="Biometrics", issn=("0006-341X", "1541-0420"))
    normalized, _ = normalize_crossref_discovered_work(
        message, datetime(2026, 9, 19, tzinfo=timezone.utc),
    )
    assert crossref_record_matches_journal(normalized, journal) is accepted
    client = JournalDiscoveryClient(
        {"0006-341X": [list_payload([message])], "1541-0420": []}
    )

    result = discover_crossref_journals(
        client,  # type: ignore[arg-type]
        (journal,),
        date(2026, 1, 1),
        date(2026, 1, 31),
        retrieved_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    assert bool(result.records) is accepted
    assert all(
        unit.status is CoverageStatus.COMPLETE for unit in result.coverage
    )


def test_discovered_crossref_record_attaches_all_openalex_doi_anchors() -> None:
    discovered, _warnings = normalize_crossref_discovered_work(
        discovered_message("10.5555/shared", issns=["0006-341X"]),
        datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    class NoLookupClient:
        def get_work_by_doi(self, doi: str) -> dict[str, Any]:
            raise AssertionError(f"unexpected DOI lookup: {doi}")

    result = assemble_provider_evidence(
        NoLookupClient(),  # type: ignore[arg-type]
        (
            openalex_record("W1", "10.5555/shared"),
            openalex_record("W2", "10.5555/shared"),
        ),
        (discovered,),
        retrieved_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    crossref_evidence = next(
        item for item in result.evidence if item.provenance.provider == "crossref"
    )
    assert [reference.record_id for reference in crossref_evidence.supplements] == [
        "https://openalex.org/W1",
        "https://openalex.org/W2",
    ]
    assert result.supplement_records == ()
    assert result.coverage == ()


def test_doi_gap_supplementation_fetches_shared_doi_once() -> None:
    opener = SequenceOpener(payload_for("10.5555/shared"))
    client = CrossrefClient(opener=opener, sleep=lambda _: None)

    result = assemble_provider_evidence(
        client,
        (
            openalex_record("W1", "10.5555/shared"),
            openalex_record("W2", "10.5555/shared"),
        ),
        (),
        retrieved_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    assert len(opener.requests) == 1
    assert len(result.supplement_records) == 1
    assert result.units[0].doi == "10.5555/shared"
    assert result.units[0].record == result.supplement_records[0]
    assert result.units[0].issues == result.issues
    assert tuple(unit.coverage for unit in result.units) == result.coverage
    assert len(result.coverage) == 1
    assert result.coverage[0].component is CoverageComponent.CROSSREF_SUPPLEMENT
    assert result.coverage[0].status is CoverageStatus.COMPLETE
    assert result.coverage[0].doi == "10.5555/shared"
    crossref_evidence = next(
        item for item in result.evidence if item.provenance.provider == "crossref"
    )
    assert len(crossref_evidence.supplements) == 2


def test_doi_supplement_progress_has_exact_total_and_completes_every_work_unit() -> None:
    opener = SequenceOpener(
        payload_for("10.5555/a"),
        http_error(404),
        http_error(500),
        payload_for("10.5555/c"),
        http_error(400),
        payload_for("10.5555/not-e"),
    )
    client = CrossrefClient(opener=opener, sleep=lambda _: None)
    events: list[ProgressEvent] = []

    result = assemble_provider_evidence(
        client,
        (
            openalex_record("W1", "10.5555/a"),
            openalex_record("W2", "10.5555/a"),
            openalex_record("W3", "10.5555/b"),
            openalex_record("W4", "10.5555/c"),
            openalex_record("W5", "10.5555/d"),
            openalex_record("W6", "10.5555/e"),
        ),
        (),
        progress_callback=events.append,
    )

    assert len(opener.requests) == 6
    assert tuple(unit.coverage for unit in result.units) == result.coverage
    assert tuple(unit.record for unit in result.units if unit.record is not None) == result.supplement_records
    assert tuple(issue for unit in result.units for issue in unit.issues) == result.issues
    assert all(issue.doi == unit.doi for unit in result.units for issue in unit.issues)
    assert [record.doi for record in result.supplement_records] == [
        "10.5555/a",
        "10.5555/c",
    ]
    assert [
        (unit.doi, unit.status) for unit in result.coverage
    ] == [
        ("10.5555/a", CoverageStatus.COMPLETE),
        ("10.5555/b", CoverageStatus.UNAVAILABLE),
        ("10.5555/c", CoverageStatus.COMPLETE),
        ("10.5555/d", CoverageStatus.FAILED),
        ("10.5555/e", CoverageStatus.FAILED),
    ]
    completed = [
        event.activity
        for event in events
        if event.activity is not None
        and event.activity.label == "Completed Crossref DOI lookup"
    ]
    assert [(item.current, item.total, item.unit) for item in completed] == [
        (1, 5, "doi"),
        (2, 5, "doi"),
        (3, 5, "doi"),
        (4, 5, "doi"),
        (5, 5, "doi"),
    ]
    request_activity = [
        event.activity
        for event in events
        if event.activity is not None
        and event.activity.label
        in {
            "Requesting Crossref",
            "Retrying Crossref request",
            "Received Crossref response",
        }
    ]
    assert request_activity
    assert {
        (item.source, item.operation, item.unit, item.total)
        for item in request_activity
    } == {("crossref", "doi_supplement", "doi", 5)}
    retry = next(
        item for item in request_activity if item.kind is ActivityKind.RETRYING
    )
    assert retry.current == 2
    assert {issue.stage for issue in result.issues} >= {
        "not_found",
        "record_normalization",
        "request_failure",
    }


def test_partial_dates_preserve_provider_order_without_fabricating_components() -> None:
    record, warnings = normalize_crossref_work(
        fixture("work_partial_dates.json"),
        "10.5555/partial-dates",
        datetime(2026, 9, 18, tzinfo=timezone.utc),
    )

    assert not warnings
    assert [(item.kind.value, item.year, item.month, item.day) for item in record.dates] == [
        ("issued", 2026, None, None),
        ("issued", 2026, 5, None),
        ("published-print", 2026, 4, 12),
        ("published", 2026, None, None),
        ("published-online", 2026, 4, None),
    ]


def test_partial_date_model_rejects_impossible_structures_and_dates() -> None:
    assert CrossrefPartialDate(kind="issued", year=2026).month is None
    assert CrossrefPartialDate(kind="issued", year=2026, month=4).day is None
    assert CrossrefPartialDate(kind="issued", year=2024, month=2, day=29).day == 29

    with pytest.raises(ValidationError, match="day requires month"):
        CrossrefPartialDate(kind="issued", year=2026, day=12)
    with pytest.raises(ValidationError):
        CrossrefPartialDate(kind="issued", year=2026, month=13)
    with pytest.raises(ValidationError):
        CrossrefPartialDate(kind="issued", year=2026, month=2, day=29)


def test_all_relation_types_are_preserved_and_doi_targets_are_normalized() -> None:
    record, warnings = normalize_crossref_work(
        fixture("work_relations.json"),
        "10.5555/relations",
        datetime(2026, 9, 18, tzinfo=timezone.utc),
    )

    assert not warnings
    assert [relation.relation_type for relation in record.relations] == [
        "is-preprint-of",
        "references",
    ]
    assert record.relations[0].identifier == "10.5555/target"
    assert record.relations[0].asserted_by == "subject"
    assert record.relations[1].identifier == "123456"
    assert [item.model_dump() for item in record.to_evidence().relations] == [
        item.model_dump() for item in record.relations
    ]


def test_optional_malformed_fields_warn_while_other_metadata_survives() -> None:
    payload = payload_for("10.5555/warnings")
    payload["message"].update(
        {
            "title": "not-an-array",
            "abstract": 42,
            "published-online": {"date-parts": [[2026, 2, 29], [2026, 6]]},
            "relation": {
                "is-identical-to": [
                    {"id-type": "doi", "id": "   "},
                    {"id-type": "pmid", "id": "777"},
                ]
            },
            "type": 12,
        }
    )

    record, warnings = normalize_crossref_work(
        payload,
        "10.5555/warnings",
        datetime(2026, 9, 18, tzinfo=timezone.utc),
    )

    assert record.title is None
    assert record.journal == "Biometrics"
    assert record.abstract is None
    assert ("published-online", 2026, 6, None) in [
        (item.kind.value, item.year, item.month, item.day) for item in record.dates
    ]
    assert [relation.identifier for relation in record.relations] == ["777"]
    assert record.work_type is None
    assert len(warnings) == 5


@pytest.mark.parametrize(
    "change",
    [
        {"status": "failed"},
        {"message-type": "work-list"},
        {"message": []},
    ],
)
def test_invalid_envelope_is_a_record_error(change: dict[str, object]) -> None:
    payload = payload_for("10.5555/envelope")
    payload.update(change)

    with pytest.raises(CrossrefRecordError):
        normalize_crossref_work(
            payload,
            "10.5555/envelope",
            datetime(2026, 9, 18, tzinfo=timezone.utc),
        )


def test_response_doi_must_match_requested_doi() -> None:
    with pytest.raises(CrossrefRecordError, match="does not match"):
        normalize_crossref_work(
            payload_for("10.5555/other"),
            "10.5555/requested",
            datetime(2026, 9, 18, tzinfo=timezone.utc),
        )


def test_provider_model_validation_is_wrapped_as_record_error() -> None:
    with pytest.raises(CrossrefRecordError) as captured:
        normalize_crossref_work(
            payload_for("10.5555/naive"),
            "10.5555/naive",
            datetime(2026, 9, 18),
        )

    assert isinstance(captured.value.__cause__, ValidationError)


def test_direct_normalization_converts_aware_retrieved_at_to_utc() -> None:
    supplied = datetime(2026, 9, 18, 8, tzinfo=timezone(timedelta(hours=8)))

    record, _ = normalize_crossref_work(
        payload_for("10.5555/timezone"),
        "10.5555/timezone",
        supplied,
    )

    assert record.provenance.retrieved_at == datetime(
        2026, 9, 18, tzinfo=timezone.utc
    )


class BatchClient:
    def __init__(self, outcomes: dict[str, object]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    def get_work_by_doi(self, doi: str) -> dict[str, Any]:
        self.calls.append(doi)
        outcome = self.outcomes[doi]
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, dict)
        return outcome


def test_batch_isolates_expected_failures_and_preserves_order() -> None:
    records = (
        openalex_record("W1", "10.5555/a"),
        openalex_record("W2", None),
        openalex_record("W3", "10.5555/c"),
        openalex_record("W4", "10.5555/d"),
        openalex_record("W5", "10.5555/e"),
    )
    client = BatchClient(
        {
            "10.5555/a": payload_for("10.5555/a"),
            "10.5555/c": CrossrefNotFoundError("not found"),
            "10.5555/d": CrossrefRequestError("server unavailable"),
            "10.5555/e": payload_for("10.5555/e"),
        }
    )

    result = enrich_records(  # type: ignore[arg-type]
        client,
        records,
        retrieved_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
    )

    assert [item.openalex for item in result.records] == list(records)
    assert [item.crossref is not None for item in result.records] == [
        True,
        False,
        False,
        False,
        True,
    ]
    assert client.calls == ["10.5555/a", "10.5555/c", "10.5555/d", "10.5555/e"]
    assert [issue.stage for issue in result.issues] == [
        "missing_doi",
        "not_found",
        "request_failure",
    ]
    assert result.has_errors
    assert result.issues[-1].severity is EnrichmentIssueSeverity.ERROR


def test_batch_turns_normalization_warnings_into_field_issues() -> None:
    payload = payload_for("10.5555/warn")
    payload["message"]["abstract"] = 42
    client = BatchClient({"10.5555/warn": payload})

    result = enrich_records(  # type: ignore[arg-type]
        client,
        (openalex_record("W1", "10.5555/warn"),),
    )

    assert result.records[0].crossref is not None
    assert [issue.stage for issue in result.issues] == ["field_normalization"]
    assert not result.has_errors


def test_batch_isolates_record_normalization_failure_and_continues() -> None:
    records = (
        openalex_record("W1", "10.5555/requested"),
        openalex_record("W2", "10.5555/later"),
    )
    client = BatchClient(
        {
            "10.5555/requested": payload_for("10.5555/mismatch"),
            "10.5555/later": payload_for("10.5555/later"),
        }
    )

    result = enrich_records(client, records)  # type: ignore[arg-type]

    assert result.records[0].crossref is None
    assert result.records[1].crossref is not None
    assert [issue.stage for issue in result.issues] == ["record_normalization"]
    assert result.has_errors


def test_batch_normalizes_one_aware_timestamp_for_all_records() -> None:
    records = (
        openalex_record("W1", "10.5555/a"),
        openalex_record("W2", "10.5555/b"),
    )
    client = BatchClient(
        {
            "10.5555/a": payload_for("10.5555/a"),
            "10.5555/b": payload_for("10.5555/b"),
        }
    )
    supplied = datetime(2026, 9, 18, 8, tzinfo=timezone(timedelta(hours=8)))

    result = enrich_records(client, records, retrieved_at=supplied)  # type: ignore[arg-type]

    timestamps = [item.crossref.provenance.retrieved_at for item in result.records if item.crossref]
    assert timestamps == [datetime(2026, 9, 18, tzinfo=timezone.utc)] * 2


def test_batch_rejects_naive_timestamp_before_requests() -> None:
    client = BatchClient({"10.5555/a": payload_for("10.5555/a")})

    with pytest.raises(ValueError, match="timezone"):
        enrich_records(  # type: ignore[arg-type]
            client,
            (openalex_record("W1", "10.5555/a"),),
            retrieved_at=datetime(2026, 9, 18),
        )

    assert not client.calls


def test_batch_does_not_swallow_unexpected_programming_errors() -> None:
    client = BatchClient({"10.5555/a": RuntimeError("programming bug")})

    with pytest.raises(RuntimeError, match="programming bug"):
        enrich_records(  # type: ignore[arg-type]
            client,
            (openalex_record("W1", "10.5555/a"),),
        )
