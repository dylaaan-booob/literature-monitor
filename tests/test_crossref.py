import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
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
    CrossrefDOIOutcomeKind,
    normalize_crossref_manifest_member,
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
        if isinstance(outcome, bytes):
            return httpx.Response(200, content=outcome)
        return httpx.Response(200, json=outcome)

    def close(self) -> None:
        self.closed = True
        super().close()


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    clock = FakeClock()
    monkeypatch.setattr("literature_monitor.crossref.monotonic", clock.monotonic)
    return clock


class TimedTransport(SequenceTransport):
    def __init__(
        self, clock: FakeClock, *outcomes: object, latencies: tuple[float, ...],
    ) -> None:
        self.clock = clock
        self.latencies = iter(latencies)
        self.starts: list[float] = []
        super().__init__(*outcomes)

    def _respond(self, request: httpx.Request) -> httpx.Response:
        self.starts.append(self.clock.now)
        self.clock.advance(next(self.latencies))
        return super()._respond(request)


def http_error(code: int) -> httpx.Response:
    return httpx.Response(code)

def make_response(payload: object, *, headers: dict[str, str] | None = None) -> httpx.Response:
    if isinstance(payload, bytes):
        return httpx.Response(200, content=payload, headers=headers)
    return httpx.Response(200, json=payload, headers=headers)


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
    transport = SequenceTransport(payload_for("10.1002/(abc)/x"))
    client = CrossrefClient(
        mailto=" monitor@example.com ",
        timeout=17,
        transport=transport,
        sleep=lambda _: None,
    )

    response = client.get_work_by_doi(" HTTPS://DOI.ORG/10.1002/(ABC)/X ")

    assert response["message"]["DOI"] == "10.1002/(abc)/x"
    request = transport.requests[0]
    parsed = urlparse(str(request.url))
    assert parsed.path == "/v1/works/10.1002%2F%28abc%29%2Fx"
    assert parse_qs(parsed.query) == {"mailto": ["monitor@example.com"]}
    assert request.headers["Accept"] == "application/json"
    assert request.headers["User-Agent"] == "literature-monitor/0.4.3"
    assert request.extensions["timeout"]["read"] == 17


def test_journal_client_uses_publication_filters_and_encoded_cursor_pages() -> None:
    transport = SequenceTransport(
        list_payload([{}, {}], "next / cursor"),
        list_payload([{}], "unused"),
    )
    client = CrossrefClient(transport=transport, sleep=lambda _: None)

    pages = list(
        client.iter_journal_work_pages(
            "0006-341X",
            date(2026, 1, 1),
            date(2026, 1, 31),
            rows=2,
        )
    )

    assert len(pages) == 2
    first = urlparse(str(transport.requests[0].url))
    second = urlparse(str(transport.requests[1].url))
    assert first.path == "/v1/journals/0006-341X/works"
    assert parse_qs(first.query) == {
        "filter": ["from-pub-date:2026-01-01,until-pub-date:2026-01-31"],
        "rows": ["2"],
        "cursor": ["*"],
    }
    assert parse_qs(second.query)["cursor"] == ["next / cursor"]
    assert "next+%2F+cursor" in second.query


def test_journal_client_defaults_to_rows_1000() -> None:
    transport = SequenceTransport(list_payload([{}]))
    client = CrossrefClient(transport=transport, sleep=lambda _: None)

    pages = list(
        client.iter_journal_work_pages(
            "0006-341X",
            date(2026, 1, 1),
            date(2026, 1, 31),
        )
    )

    assert len(pages) == 1
    query = parse_qs(urlparse(str(transport.requests[0].url)).query)
    assert query["rows"] == ["1000"]


def test_journal_client_traverses_multiple_default_size_cursor_pages() -> None:
    transport = SequenceTransport(
        list_payload([{}] * 1000, "second-page"),
        list_payload([{}], "unused"),
    )
    client = CrossrefClient(transport=transport, sleep=lambda _: None)

    pages = list(
        client.iter_journal_work_pages(
            "0006-341X",
            date(2026, 1, 1),
            date(2026, 1, 31),
        )
    )

    assert len(pages) == 2
    first_query = parse_qs(urlparse(str(transport.requests[0].url)).query)
    second_query = parse_qs(urlparse(str(transport.requests[1].url)).query)
    assert first_query["rows"] == ["1000"]
    assert first_query["cursor"] == ["*"]
    assert second_query["rows"] == ["1000"]
    assert second_query["cursor"] == ["second-page"]


def test_client_uses_response_rate_headers_to_pace_later_requests(clock: FakeClock) -> None:
    transport = SequenceTransport(
        make_response(
            payload_for("10.5555/first"),
            headers={
                "X-Rate-Limit-Limit": "4",
                "X-Rate-Limit-Interval": "2s",
            },
        ),
        make_response(
            payload_for("10.5555/second"),
            headers={
                "X-Rate-Limit-Limit": "2",
                "X-Rate-Limit-Interval": "3s",
            },
        ),
        payload_for("10.5555/third"),
    )
    client = CrossrefClient(transport=transport, sleep=clock.sleep)

    client.get_work_by_doi("10.5555/first")
    client.get_work_by_doi("10.5555/second")
    client.get_work_by_doi("10.5555/third")

    assert clock.sleeps == [0.5, 1.5]
    assert len(transport.requests) == 3


def test_client_missing_rate_headers_do_not_add_pacing_or_break_requests(clock: FakeClock) -> None:
    transport = SequenceTransport(
        payload_for("10.5555/first"),
        payload_for("10.5555/second"),
    )
    client = CrossrefClient(transport=transport, sleep=clock.sleep)

    assert client.get_work_by_doi("10.5555/first")["status"] == "ok"
    assert client.get_work_by_doi("10.5555/second")["status"] == "ok"

    assert clock.sleeps == []
    assert len(transport.requests) == 2


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
    clock: FakeClock,
) -> None:
    transport = SequenceTransport(
        make_response(
            payload_for("10.5555/first"),
            headers={
                "X-Rate-Limit-Limit": limit,
                "X-Rate-Limit-Interval": interval,
            },
        ),
        payload_for("10.5555/second"),
    )
    client = CrossrefClient(transport=transport, sleep=clock.sleep)

    assert client.get_work_by_doi("10.5555/first")["status"] == "ok"
    assert client.get_work_by_doi("10.5555/second")["status"] == "ok"

    assert clock.sleeps == []
    assert len(transport.requests) == 2


def test_client_rate_pacing_reports_waiting_activity_with_request_identity(clock: FakeClock) -> None:
    trace: list[tuple[str, object]] = []
    transport = SequenceTransport(
        make_response(
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
        clock.sleep(delay)

    client = CrossrefClient(transport=transport, sleep=sleep)
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


def test_client_retry_wait_respects_slower_known_provider_pacing(clock: FakeClock) -> None:
    transport = SequenceTransport(
        make_response(
            payload_for("10.5555/first"),
            headers={
                "X-Rate-Limit-Limit": "1",
                "X-Rate-Limit-Interval": "5s",
            },
        ),
        http_error(429),
        payload_for("10.5555/second"),
    )
    client = CrossrefClient(transport=transport, sleep=clock.sleep)

    client.get_work_by_doi("10.5555/first")
    assert client.get_work_by_doi("10.5555/second")["status"] == "ok"

    assert clock.sleeps == [5, 5]
    assert len(transport.requests) == 3


def pacing_response(interval: float, *, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        json=list_payload([], total_results=0),
        headers={"X-Rate-Limit-Limit": "1", "X-Rate-Limit-Interval": f"{interval:g}s"},
    )


def pacing_request(client: CrossrefClient, shape: str, **kwargs: Any) -> None:
    if shape == "singleton":
        client.get_work_by_doi("10.5555/pacing", **kwargs)
    elif shape == "alias":
        client.get_doi_outcome("10.5555/pacing", **kwargs)
    elif shape == "manifest":
        client.get_manifest_page(("0006-341X",), date(2026, 1, 1), date(2026, 1, 1), **kwargs)
    elif shape == "journal":
        list(client.iter_journal_work_pages("0006-341X", date(2026, 1, 1), date(2026, 1, 1), **kwargs))
    else:
        assert shape in ("probe", "hydration")
        client.get_doi_batch(("10.5555/pacing",), thin=shape == "probe", **kwargs)


@pytest.mark.parametrize("shape", ["singleton", "manifest"])
@pytest.mark.parametrize(("latency", "wait"), [(0.5, 1.5), (2, 0), (3, 0)])
def test_same_class_pacing_counts_response_latency(
    clock: FakeClock, monkeypatch: pytest.MonkeyPatch, shape: str, latency: float, wait: float,
) -> None:
    transport = TimedTransport(clock, pacing_response(2), list_payload([]), latencies=(latency, 0))
    events: list[ProgressEvent] = []
    with CrossrefClient(transport=transport, sleep=clock.sleep) as client:
        monkeypatch.setattr("time.time", lambda: 1000000)
        pacing_request(client, shape, progress_callback=events.append)
        assert clock.sleeps == []  # The first request needs neither a probe nor a wait.
        monkeypatch.setattr("time.time", lambda: -1000000)
        pacing_request(client, shape, progress_callback=events.append)

    assert clock.sleeps == ([wait] if wait else [])
    assert transport.starts == [0, max(2, latency)]
    assert len(transport.requests) == 2
    waiting = [e.activity for e in events if e.activity.kind is ActivityKind.WAITING]
    assert len(waiting) == (1 if wait else 0)


@pytest.mark.parametrize("list_shape", ["manifest", "journal", "probe", "hydration"])
@pytest.mark.parametrize("singleton_shape", ["singleton", "alias"])
@pytest.mark.parametrize("singleton_first", [True, False])
@pytest.mark.parametrize(("first_interval", "second_interval", "starts", "sleeps"), [
    (5, 1, [0, 0, 5, 5, 10], [5, 5]),
    (1, 5, [0, 0, 1, 5, 5], [1, 4]),
])
def test_request_classes_retain_independent_interleaved_deadlines(
    clock: FakeClock, list_shape: str, singleton_shape: str, singleton_first: bool,
    first_interval: float, second_interval: float, starts: list[float], sleeps: list[float],
) -> None:
    first, second = (
        (singleton_shape, list_shape) if singleton_first else (list_shape, singleton_shape)
    )
    transport = TimedTransport(clock, pacing_response(first_interval), pacing_response(second_interval),
        list_payload([]), list_payload([]), list_payload([]), latencies=(0, 0, 0, 0, 0))
    with CrossrefClient(transport=transport, sleep=clock.sleep) as client:
        for shape in (first, second, first, second, first):
            pacing_request(client, shape)

    assert transport.starts == starts
    assert clock.sleeps == sleeps
    assert len(transport.requests) == 5


@pytest.mark.parametrize("headers", [
    {}, {"X-Rate-Limit-Limit": "bad", "X-Rate-Limit-Interval": "1s"},
    {"X-Rate-Limit-Limit": "1"}, {"X-Rate-Limit-Interval": "1s"},
])
@pytest.mark.parametrize(("slow_shape", "fast_shape"), [
    ("manifest", "singleton"), ("singleton", "manifest"),
])
def test_unusable_headers_preserve_both_classes_latest_valid_rates(
    clock: FakeClock, headers: dict[str, str], slow_shape: str, fast_shape: str,
) -> None:
    transport = TimedTransport(clock,
        pacing_response(5), pacing_response(1), pacing_response(3),
        make_response(list_payload([]), headers=headers),
        list_payload([]), list_payload([]), list_payload([]),
        latencies=(0, 0, 0, 0, 0, 0, 0))
    with CrossrefClient(transport=transport, sleep=clock.sleep) as client:
        for shape in (slow_shape, fast_shape, slow_shape, slow_shape,
                      fast_shape, fast_shape, slow_shape):
            pacing_request(client, shape)

    assert transport.starts == [0, 0, 5, 8, 8, 9, 11]
    assert clock.sleeps == [5, 3, 1, 2]
    assert len(transport.requests) == 7  # No pacing-only request on malformed/missing headers.


@pytest.mark.parametrize("failure", [
    http_error(429), http_error(503), httpx.ReadTimeout("slow"), httpx.ConnectError("down"),
])
@pytest.mark.parametrize(("interval", "latency", "retry_wait", "next_wait"), [
    (5, 2, 3, 5), (5, 5, 0, 5), (5, 6, 0, 5),
    (0.5, 0, 1, 0.5), (0.5, 0.75, 0.25, 0.5), (0.5, 2, 0, 0.5),
])
def test_retry_waits_for_longest_remaining_deadline_and_resets_request_spacing(
    clock: FakeClock, failure: httpx.Response | Exception,
    interval: float, latency: float, retry_wait: float, next_wait: float,
) -> None:
    transport = TimedTransport(clock, pacing_response(interval), failure,
        list_payload([]), list_payload([]), latencies=(0, latency, 0, 0))
    trace: list[tuple[str, object]] = []

    def report(event: ProgressEvent) -> None:
        trace.append(("activity", event.activity))

    def sleep(seconds: float) -> None:
        trace.append(("sleep", seconds))
        clock.sleep(seconds)

    activity = ActivityUpdate(kind=ActivityKind.WORKING, source="crossref",
        operation="caller_label_is_not_request_class", label="Supplementing DOI",
        unit="doi", current=2, total=5)
    with CrossrefClient(transport=transport, sleep=sleep) as client:
        client.get_work_by_doi("10.5555/pacing")
        client.get_work_by_doi("10.5555/pacing", activity=activity, progress_callback=report)
        # Retry alone satisfies pacing; it must not emit a second WAITING.
        activities = [value for kind, value in trace if kind == "activity"]
        assert [a.kind for a in activities].count(ActivityKind.WAITING) == 1
        assert [a.kind for a in activities].count(ActivityKind.RETRYING) == 1
        assert {(a.source, a.operation, a.unit, a.current, a.total) for a in activities} == {
            ("crossref", "caller_label_is_not_request_class", "doi", 2, 5)}
        if retry_wait:
            retry_index = next(i for i, (kind, value) in enumerate(trace)
                               if kind == "activity" and value.kind is ActivityKind.RETRYING)
            assert trace[retry_index + 1] == ("sleep", retry_wait)
        client.get_work_by_doi("10.5555/pacing")

    expected_waits = [interval] + ([retry_wait] if retry_wait else []) + [next_wait]
    assert clock.sleeps == expected_waits
    retried_at = interval + max(latency, interval, 1)
    assert transport.starts == [0, interval, retried_at, retried_at + interval]
    assert len(transport.requests) == 4


def test_retry_uses_rate_headers_on_failed_response_and_counts_activity_handling(clock: FakeClock) -> None:
    transport = TimedTransport(clock, pacing_response(5, status=429), list_payload([]),
        list_payload([]), latencies=(2, 0, 0))
    events: list[ProgressEvent] = []

    def report(event: ProgressEvent) -> None:
        events.append(event)
        if event.activity.kind is ActivityKind.RETRYING:
            clock.advance(1)

    with CrossrefClient(transport=transport, sleep=clock.sleep) as client:
        pacing_request(client, "manifest", progress_callback=report)
        pacing_request(client, "manifest", progress_callback=events.append)

    assert transport.starts == [0, 5, 10]
    assert clock.sleeps == [2, 5]  # Response latency and retry reporting consumed 3 seconds.
    assert [e.activity.kind for e in events].count(ActivityKind.RETRYING) == 1
    assert [e.activity.kind for e in events].count(ActivityKind.WAITING) == 1


@pytest.mark.parametrize(("shape", "other_shape"), [
    ("singleton", "manifest"), ("manifest", "singleton"),
])
@pytest.mark.parametrize(("interval", "other_interval", "starts", "sleeps"), [
    (5, 0.5, [0, 0, 5, 10], [5, 5]),
    (0.5, 5, [0, 0, 0.5, 1.5], [0.5, 1]),
])
def test_retry_uses_its_own_class_even_after_another_class_updates_headers(
    clock: FakeClock, shape: str, other_shape: str, interval: float, other_interval: float,
    starts: list[float], sleeps: list[float],
) -> None:
    transport = TimedTransport(clock, pacing_response(interval), pacing_response(other_interval),
        http_error(503), list_payload([]), latencies=(0, 0, 0, 0))
    with CrossrefClient(transport=transport, sleep=clock.sleep) as client:
        pacing_request(client, shape)
        pacing_request(client, other_shape)
        pacing_request(client, shape)

    assert transport.starts == starts
    assert clock.sleeps == sleeps
    assert len(transport.requests) == 4


@pytest.mark.parametrize(("latencies", "starts", "sleeps"), [
    ((0.25, 0.75, 0), [0, 1, 3], [0.75, 1.25]),
    ((1, 3, 0), [0, 1, 4], []),
])
def test_both_retry_fallback_deadlines_count_elapsed_latency_without_headers(
    clock: FakeClock, latencies: tuple[float, ...], starts: list[float], sleeps: list[float],
) -> None:
    transport = TimedTransport(clock, http_error(429), httpx.ReadTimeout("slow"),
        payload_for("10.5555/pacing"), latencies=latencies)
    events: list[ProgressEvent] = []
    with CrossrefClient(transport=transport, sleep=clock.sleep) as client:
        pacing_request(client, "singleton", progress_callback=events.append)

    assert transport.starts == starts
    assert clock.sleeps == sleeps
    assert len(transport.requests) == 3
    assert [e.activity.kind for e in events].count(ActivityKind.RETRYING) == 2
    assert not any(e.activity.kind is ActivityKind.WAITING for e in events)


def test_alias_response_headers_pace_followup_singleton_not_list_requests(clock: FakeClock) -> None:
    redirect = httpx.Response(308, headers={
        "Location": "/v1/works/10.5555/prime",
        "X-Rate-Limit-Limit": "1", "X-Rate-Limit-Interval": "3s"})
    transport = TimedTransport(clock, redirect, list_payload([]), payload_for("10.5555/prime"),
        latencies=(1, 0, 0))
    with CrossrefClient(transport=transport, sleep=clock.sleep) as client:
        outcome = client.get_doi_outcome("10.5555/alias")
        assert outcome.kind is CrossrefDOIOutcomeKind.PRIME_REDIRECT
        assert outcome.prime_doi == "10.5555/prime"
        pacing_request(client, "probe")
        client.get_work_by_doi(outcome.prime_doi)

    assert transport.starts == [0, 1, 3]
    assert clock.sleeps == [2]
    assert len(transport.requests) == 3


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "ok", "message-type": "work-list", "message": []},
        {"status": "ok", "message-type": "work-list", "message": {}},
        {"status": "bad", "message-type": "work-list", "message": {"items": []}},
    ],
)
def test_journal_client_rejects_malformed_list_envelopes(payload: object) -> None:
    client = CrossrefClient(transport=SequenceTransport(payload), sleep=lambda _: None)

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
        transport=SequenceTransport(list_payload([{}], cursor)),
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
        transport=SequenceTransport(list_payload([{}], "*")),
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
    [http_error(429), http_error(500), httpx.ReadTimeout("timed out"), httpx.ConnectError("down"), httpx.ConnectError("dns")],
)
def test_client_retries_transient_failures(outcome: httpx.Response | Exception, clock: FakeClock) -> None:
    transport = SequenceTransport(outcome, outcome, payload_for("10.5555/retry"))
    client = CrossrefClient(transport=transport, sleep=clock.sleep)

    assert client.get_work_by_doi("10.5555/retry")["status"] == "ok"
    assert clock.sleeps == [1, 2]
    assert len(transport.requests) == 3


@pytest.mark.parametrize(
    "transient_failure",
    (http_error(429), http_error(500), httpx.ReadTimeout("timed out")),
)
def test_client_progress_reports_retry_before_backoff_and_success(
    transient_failure: httpx.Response | Exception,
    clock: FakeClock,
) -> None:
    trace: list[tuple[str, object]] = []
    transport = SequenceTransport(
        transient_failure,
        payload_for("10.5555/retry-progress"),
    )

    def report(event: ProgressEvent) -> None:
        assert event.activity is not None
        trace.append(("activity", event.activity))

    def sleep(delay: float) -> None:
        trace.append(("sleep", delay))
        clock.sleep(delay)

    client = CrossrefClient(transport=transport, sleep=sleep)
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
    assert len(transport.requests) == 2
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
        transport=SequenceTransport(http_error(404)), sleep=lambda _: None
    )
    bad_client = CrossrefClient(
        transport=SequenceTransport(http_error(400)), sleep=lambda _: None
    )

    with pytest.raises(CrossrefNotFoundError):
        missing_client.get_work_by_doi("10.5555/missing")
    with pytest.raises(CrossrefRequestError, match="HTTP 400"):
        bad_client.get_work_by_doi("10.5555/bad")


def test_client_reports_exhausted_transient_failure() -> None:
    transport = SequenceTransport(*(httpx.ReadTimeout("timed out") for _ in range(3)))
    client = CrossrefClient(transport=transport, sleep=lambda _: None)

    with pytest.raises(CrossrefRequestError, match="timed out"):
        client.get_work_by_doi("10.5555/timeout")

    assert len(transport.requests) == 3


@pytest.mark.parametrize("payload", [b"not json", ["not", "an", "object"]])
def test_client_rejects_invalid_json_transport(payload: object) -> None:
    client = CrossrefClient(transport=SequenceTransport(payload), sleep=lambda _: None)

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
    transport = SequenceTransport(list_payload([first, second], total_results=2))
    client = CrossrefClient(transport=transport, sleep=lambda _: None)
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
    assert len(transport.requests) == 1
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
    transport = SequenceTransport(
        list_payload([item], total_results=total_results)
    )
    client = CrossrefClient(transport=transport, sleep=lambda _: None)
    events: list[ProgressEvent] = []

    result = discover_crossref_journals(
        client,
        (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date(2026, 1, 1),
        date(2026, 1, 31),
        progress_callback=events.append,
    )

    assert not result.has_errors
    assert len(transport.requests) == 1
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
    transport = SequenceTransport(payload_for("10.5555/shared"))
    client = CrossrefClient(transport=transport, sleep=lambda _: None)

    result = assemble_provider_evidence(
        client,
        (
            openalex_record("W1", "10.5555/shared"),
            openalex_record("W2", "10.5555/shared"),
        ),
        (),
        retrieved_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )

    assert len(transport.requests) == 1
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
    transport = SequenceTransport(
        payload_for("10.5555/a"),
        http_error(404),
        http_error(500),
        payload_for("10.5555/c"),
        http_error(400),
        payload_for("10.5555/not-e"),
    )
    client = CrossrefClient(transport=transport, sleep=lambda _: None)
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

    assert len(transport.requests) == 6
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


@pytest.mark.parametrize("unexpected_failure", [False, True])
def test_discovery_and_doi_requests_share_one_http_session_and_close_it(
    monkeypatch: pytest.MonkeyPatch,
    unexpected_failure: bool,
) -> None:
    transport = SequenceTransport(
        list_payload([], total_results=0), payload_for("10.5555/shared-session"),
    )
    sessions: list[httpx.Client] = []
    real_client = httpx.Client

    def create_session(**kwargs: Any) -> httpx.Client:
        session = real_client(**kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr(httpx, "Client", create_session)

    def execute() -> None:
        with CrossrefClient(transport=transport) as client:
            pages = list(client.iter_journal_work_pages(
                "0006-341X", date(2026, 1, 1), date(2026, 1, 31),
            ))
            assert pages[0]["message"]["items"] == []
            client.get_work_by_doi("10.5555/shared-session")
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


@pytest.mark.parametrize("status_code", [301, 308])
def test_alias_redirect_is_observable_without_following_it(status_code: int) -> None:
    transport = SequenceTransport(httpx.Response(
        status_code, headers={"Location": "https://api.crossref.org/v1/works/10.5555/prime"},
    ))
    with CrossrefClient(transport=transport) as client:
        with pytest.raises(CrossrefRequestError, match=f"HTTP {status_code}") as raised:
            client.get_work_by_doi("10.5555/alias")

    assert isinstance(raised.value.__cause__, httpx.HTTPStatusError)
    assert raised.value.__cause__.response.status_code == status_code
    assert len(transport.requests) == 1
    assert transport.closed


@pytest.mark.parametrize("status_code", [400, 401, 403, 422])
def test_nonretryable_http_status_fails_without_backoff(status_code: int) -> None:
    delays: list[float] = []
    transport = SequenceTransport(http_error(status_code))
    with CrossrefClient(transport=transport, sleep=delays.append) as client:
        with pytest.raises(CrossrefRequestError, match=f"HTTP {status_code}"):
            client.get_work_by_doi("10.5555/rejected")

    assert len(transport.requests) == 1
    assert delays == []
    assert transport.closed


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
    with CrossrefClient() as client:
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
    payload = payload_for("10.5555/proxy")

    def record_transport(self: httpx.HTTPTransport, **kwargs: Any) -> None:
        proxies[self] = kwargs.get("proxy")
        real_init(self, **kwargs)

    def respond(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        used_proxies.append(proxies[self])
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(httpx.HTTPTransport, "__init__", record_transport)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", respond)
    with CrossrefClient(base_url=f"{scheme}://provider.example") as client:
        assert client._http_client.trust_env
        assert client.get_work_by_doi("10.5555/proxy") == payload

    assert len(used_proxies) == 1
    assert used_proxies[0] is not None
    assert used_proxies[0].url == httpx.URL(os.environ[f"{scheme.upper()}_PROXY"])
    assert client._http_client.is_closed


def test_mock_transport_works_with_unparseable_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_proxy_environment(monkeypatch, "fc00::/7,fe80::/10")
    payload = payload_for("10.5555/proxy")
    transport = SequenceTransport(payload)
    with CrossrefClient(transport=transport) as client:
        assert client.get_work_by_doi("10.5555/proxy") == payload

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
        CrossrefClient(transport=SequenceTransport() if explicit_transport else None)

    assert raised.value is error
    assert len(calls) == 1


def test_full_and_manifest_style_indexed_share_canonical_parser() -> None:
    from literature_monitor.crossref import parse_crossref_indexed_at

    indexed = {"date-time": "2026-09-26T10:30:00+08:00", "timestamp": 0}
    payload = payload_for("10.5555/revision")
    payload["message"]["indexed"] = indexed
    record, warnings = normalize_crossref_work(payload, "10.5555/revision", datetime(2026, 9, 26, tzinfo=timezone.utc))
    assert not warnings
    assert record.indexed_at == parse_crossref_indexed_at(indexed)
    assert record.indexed_at == parse_crossref_indexed_at("2026-09-26T02:30:00Z")
    assert record.indexed_at.utcoffset() == timedelta(0)
    discovered, _ = normalize_crossref_discovered_work(payload["message"], record.provenance.retrieved_at)
    assert discovered.indexed_at == record.indexed_at
    assert "indexed_at" not in record.to_evidence().model_dump()


@pytest.mark.parametrize("revision", ["bad", "2026-09-26T00:00:00", 123, {}, {"date-time": None}, "2026-09-26T00:00:00+00:99"])
def test_malformed_crossref_revision_is_rejected_by_parser_but_not_record(revision: object) -> None:
    from literature_monitor.crossref import parse_crossref_indexed_at

    with pytest.raises(ValueError):
        parse_crossref_indexed_at(revision)
    payload = payload_for("10.5555/revision")
    payload["message"]["indexed"] = revision
    record, warnings = normalize_crossref_work(payload, "10.5555/revision", datetime(2026, 9, 26, tzinfo=timezone.utc))
    assert record.indexed_at is None
    assert "invalid indexed was treated as missing" in warnings


@pytest.mark.parametrize("revision", [None, datetime(2026, 9, 26, tzinfo=timezone.utc)])
def test_default_record_serialization_preserves_released_shape(revision: datetime | None) -> None:
    plain, _ = normalize_crossref_work(
        payload_for("10.5555/shape"), "10.5555/shape",
        datetime(2026, 9, 26, tzinfo=timezone.utc),
    )
    record = plain.model_copy(update={"indexed_at": revision})
    assert record.indexed_at == revision
    expected_fields = {"doi", "title", "journal", "abstract", "authors", "issns", "dates", "relations", "work_type", "provenance"}
    assert set(record.model_dump()) == expected_fields
    assert set(json.loads(record.model_dump_json())) == expected_fields
    assert record.model_dump() == plain.model_dump()
    assert record.model_dump_json() == plain.model_dump_json()


def test_a5_manifest_and_doi_batches_use_repeated_filters_and_same_pool() -> None:
    transport = SequenceTransport(*(list_payload([], total_results=0) for _ in range(3)))
    with CrossrefClient(transport=transport, mailto="a@example.com", sleep=lambda _: None) as client:
        pool = client._http_client
        client.get_manifest_page(("0006-341X", "2168-2267"), date(2026, 1, 1), date(2026, 1, 31))
        client.get_doi_batch(("10.1234/a", "10.1234/b"), thin=True)
        client.get_doi_batch(("10.1234/a", "10.1234/b"))
        assert client._http_client is pool and not pool.is_closed
    assert pool.is_closed and transport.closed
    queries = [dict(request.url.params) for request in transport.requests]
    assert queries[0]["filter"] == "issn:0006-341X,issn:2168-2267,from-pub-date:2026-01-01,until-pub-date:2026-01-31"
    assert queries[1]["filter"] == queries[2]["filter"] == "doi:10.1234/a,doi:10.1234/b"
    assert queries[0]["select"] == queries[1]["select"] == "DOI,ISSN,indexed"
    assert "select" not in queries[2]
    for request, query in zip(transport.requests, queries, strict=True):
        assert request.url.path == "/v1/works"
        assert query["mailto"] == "a@example.com"
        assert "|" not in query["filter"]
        assert not {"query", "keyword_expression", "abstract"}.intersection(query)
        assert request.headers["User-Agent"] == "literature-monitor/0.4.3"


@pytest.mark.parametrize("code", [301, 308])
@pytest.mark.parametrize("location", ["https://api.crossref.org/works/10.1234%2FPRIME", "/v1/works/10.1234/prime"])
def test_a5_singleton_exposes_prime_redirect_without_following(code, location) -> None:
    transport = SequenceTransport(httpx.Response(code, headers={"Location": location}))
    with CrossrefClient(transport=transport) as client:
        result = client.get_doi_outcome("10.1234/alias")
        assert client._http_client.follow_redirects is False
    assert result.kind is CrossrefDOIOutcomeKind.PRIME_REDIRECT
    assert result.prime_doi == "10.1234/prime" and result.payload is None
    assert len(transport.requests) == 1


@pytest.mark.parametrize("location", [
    "https://evil.example/works/10.1234/prime", "https://api.crossref.org.evil/works/10.1234/prime",
    "https://user@api.crossref.org/works/10.1234/prime", "http://api.crossref.org/works/10.1234/prime",
    "https://api.crossref.org:9999/works/10.1234/prime", "/journals/10.1234/prime",
    "/works/not-a-doi", "/works/10.1234/prime?x=1", "/works/10.1234/prime#fragment", "",
    "/works/10.1234/prime%ZZ", "/works/10.1234/prime%20suffix",
])
def test_a5_redirect_rejects_unsafe_location(location) -> None:
    transport = SequenceTransport(httpx.Response(301, headers={"Location": location}))
    with CrossrefClient(transport=transport) as client:
        with pytest.raises(CrossrefRequestError):
            client.get_doi_outcome("10.1234/alias")
    assert len(transport.requests) == 1


def test_a5_redirect_rejects_multiple_location_headers() -> None:
    transport = SequenceTransport(httpx.Response(308, headers=[
        ("Location", "/works/10.1234/a"), ("Location", "/works/10.1234/b")]))
    with CrossrefClient(transport=transport) as client:
        with pytest.raises(CrossrefRequestError, match="unambiguous"):
            client.get_doi_outcome("10.1234/alias")


@pytest.mark.parametrize("code", [200, 404])
def test_a5_singleton_typed_record_or_not_found(code) -> None:
    transport = SequenceTransport(httpx.Response(code, json=payload_for("10.1234/a")))
    with CrossrefClient(transport=transport) as client:
        outcome = client.get_doi_outcome("10.1234/a")
    assert outcome.kind is (CrossrefDOIOutcomeKind.RECORD if code == 200 else CrossrefDOIOutcomeKind.NOT_FOUND)


def test_a5_manifest_uses_shared_canonical_revision_parser() -> None:
    member, warnings = normalize_crossref_manifest_member({"DOI": " HTTPS://DOI.ORG/10.1234/A ",
        "ISSN": ["2168-2267", "0006-341x", "0006-341X"],
        "indexed": {"date-time": "2026-01-02T01:00:00+01:00"}})
    record, _ = normalize_crossref_discovered_work({"DOI": "10.1234/a",
        "indexed": {"date-time": "2026-01-02T00:00:00Z"}}, datetime.now(timezone.utc))
    assert member.doi == "10.1234/a" and member.issns == ("0006-341X", "2168-2267")
    assert member.indexed_at == record.indexed_at == datetime(2026, 1, 2, tzinfo=timezone.utc)
    assert not warnings
    assert set(member.__dataclass_fields__) == {"doi", "issns", "indexed_at"}


def test_a5_requests_preserve_retry_pacing_and_activity_before_sleep(clock: FakeClock) -> None:
    events, sleeps = [], []
    transport = SequenceTransport(httpx.Response(500), make_response(list_payload([], total_results=0),
        headers={"X-Rate-Limit-Limit": "2", "X-Rate-Limit-Interval": "1s"}),
        list_payload([], total_results=0))
    def sleep(delay):
        sleeps.append((delay, events[-1].activity.kind))
        clock.sleep(delay)
    with CrossrefClient(transport=transport, sleep=sleep) as client:
        client.get_manifest_page(("0006-341X",), date(2026, 1, 1), date(2026, 1, 1), progress_callback=events.append)
        client.get_doi_batch(("10.1234/a",), progress_callback=events.append)
    assert sleeps == [(1, ActivityKind.RETRYING), (0.5, ActivityKind.WAITING)]
    assert len(transport.requests) == 3
    assert any(e.activity.operation == "manifest" for e in events)
    assert any(e.activity.operation == "full_hydration" for e in events)
