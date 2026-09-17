import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest

from literature_monitor.config import JournalConfig
from literature_monitor.openalex import (
    IssueSeverity,
    OpenAlexClient,
    OpenAlexRequestError,
    discover_journals,
    resolve_journal_source,
)


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
        http_error(500),
        http_error(500),
        http_error(500),
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
    assert "HTTP 500" in issues[0].message


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

    work_requests = [request for request, _ in opener.requests[1:]]
    first_query = parse_qs(urlparse(work_requests[0].full_url).query)
    assert first_query == {
        "filter": [
            "primary_location.source.id:S8265502,"
            "from_publication_date:2026-01-01,to_publication_date:2026-09-18"
        ],
        "select": [
            "id,doi,title,publication_date,abstract_inverted_index,authorships,primary_location"
        ],
        "per_page": ["100"],
        "cursor": ["*"],
    }
    assert parse_qs(urlparse(work_requests[1].full_url).query)["cursor"] == ["next-page"]
    assert all(
        "search" not in parse_qs(urlparse(request.full_url).query)
        for request in work_requests
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
