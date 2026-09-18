import json
import socket
from datetime import datetime, timedelta, timezone
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
    EnrichmentIssueSeverity,
    enrich_records,
    normalize_crossref_work,
)
from literature_monitor.models import (
    Author,
    CanonicalMetadata,
    ExternalIds,
    MetadataSource,
)
from literature_monitor.openalex import OpenAlexWorkRecord


FIXTURES = Path(__file__).parent / "fixtures" / "crossref"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

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
    assert request.get_header("User-agent") == "literature-monitor/0.1"
    assert timeout == 17


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
