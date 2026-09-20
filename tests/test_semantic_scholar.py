from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pytest

from literature_monitor.config import JournalConfig
from literature_monitor.keywords import parse_keyword_expression
from literature_monitor.models import ExternalIds, MetadataSource, ProviderWorkEvidence
from literature_monitor.semantic_scholar import (
    SEMANTIC_SCHOLAR_FIELDS,
    SemanticScholarIssue,
    SemanticScholarIssueSeverity,
    SemanticScholarRecordError,
    augment_with_semantic_scholar,
    create_semantic_scholar_client,
    discover_semantic_scholar_journals,
    normalize_semantic_scholar_work,
    supplement_semantic_scholar_dois,
)


TIMESTAMP = datetime(2026, 9, 19, tzinfo=timezone.utc)
BIOMETRICS = JournalConfig(name="Biometrics", issn=("0006-341X", "1541-0420"))


class RawPaper:
    def __init__(self, data: dict[str, Any]) -> None:
        self.raw_data = data


def paper(
    paper_id: str,
    *,
    doi: str | None = None,
    title: str = "A paper",
    abstract: str | None = "An abstract",
    publication_date: str | None = "2026-01-20",
    year: int | None = 2026,
    venue_name: str = "Biometrics",
    issn: str | None = "0006-341X",
) -> RawPaper:
    external_ids: dict[str, object] = {"ArXiv": "2601.00001", "MAG": 123}
    if doi is not None:
        external_ids["DOI"] = doi
    publication_venue: dict[str, object] = {
        "name": venue_name,
        "alternate_names": ["BIOMETRICS"],
    }
    if issn is not None:
        publication_venue["issn"] = issn
    return RawPaper(
        {
            "paperId": paper_id,
            "corpusId": 42,
            "externalIds": external_ids,
            "title": title,
            "abstract": abstract,
            "authors": [
                {
                    "authorId": "A1",
                    "name": "Ada Author",
                    "externalIds": {"ORCID": "0000-0002-1825-0097"},
                },
                {"authorId": "A2", "name": "Ben Writer"},
            ],
            "publicationDate": publication_date,
            "year": year,
            "journal": {"name": venue_name},
            "publicationVenue": publication_venue,
            "venue": venue_name,
            "fieldsOfStudy": ["Medicine", "Computer Science"],
            "s2FieldsOfStudy": [
                {"category": "Medicine", "source": "s2-fos-model"},
            ],
        }
    )


def evidence(provider: str, record_id: str, doi: str) -> ProviderWorkEvidence:
    return ProviderWorkEvidence(
        provenance=MetadataSource(
            provider=provider,
            record_id=record_id,
            retrieved_at=TIMESTAMP,
        ),
        external_ids=ExternalIds.model_validate({provider: record_id, "doi": doi}),
    )


def test_client_construction_uses_optional_api_key_and_library_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(
        "literature_monitor.semantic_scholar.SemanticScholar",
        FakeClient,
    )

    client = create_semantic_scholar_client("  secret  ")

    assert isinstance(client, FakeClient)
    assert calls == [{"api_key": "secret", "retry": True}]


def test_normalization_preserves_factual_fields_and_separates_taxonomy() -> None:
    payload = paper("S2-1", doi="HTTPS://DOI.ORG/10.5555/ONE")
    payload.raw_data["authors"].append({"authorId": "A3"})

    record, warnings = normalize_semantic_scholar_work(payload, TIMESTAMP)
    provider_evidence = record.to_evidence()

    assert record.paper_id == "S2-1"
    assert record.corpus_id == "42"
    assert record.external_ids.model_dump(exclude_none=True) == {
        "doi": "10.5555/one",
        "arxiv": "2601.00001",
        "semantic_scholar": "S2-1",
        "mag": "123",
        "corpus_id": "42",
    }
    assert [author.name for author in record.authors] == ["Ada Author", "Ben Writer"]
    assert record.authors[0].orcid == "https://orcid.org/0000-0002-1825-0097"
    assert all(author.openalex_id is None for author in record.authors)
    assert record.publication_date == date(2026, 1, 20)
    assert record.publication_year == 2026
    assert record.publication_venue_issns == ("0006-341X",)
    assert set(record.venue_names) == {"BIOMETRICS", "Biometrics"}
    assert set(record.fields_of_study) == {"Medicine", "Computer Science"}
    assert record.provider_topics[0].value == "Medicine"
    assert record.provider_topics[0].source == "s2-fos-model"
    assert provider_evidence.author_keywords == ()
    assert provider_evidence.fields_of_study == record.fields_of_study
    assert provider_evidence.provider_topics == record.provider_topics
    assert provider_evidence.provenance.provider == "semantic_scholar"
    assert any("without a usable name" in warning for warning in warnings)


def test_normalization_requires_paper_id_and_never_fabricates_date_from_year() -> None:
    with pytest.raises(SemanticScholarRecordError, match="paperId"):
        normalize_semantic_scholar_work({"title": "Missing ID"}, TIMESTAMP)

    payload = paper("S2-year", publication_date=None, year=2026)
    record, warnings = normalize_semantic_scholar_work(payload, TIMESTAMP)

    assert record.publication_date is None
    assert record.publication_year == 2026
    assert record.to_evidence().publication_date is None
    assert not warnings


class BatchClient:
    def __init__(self, papers: list[object] | None = None) -> None:
        self.papers = papers or []
        self.calls: list[tuple[list[str], list[str], bool]] = []

    def get_papers(
        self,
        ids: list[str],
        *,
        fields: list[str],
        return_not_found: bool,
    ) -> tuple[list[object], list[str]]:
        self.calls.append((ids, fields, return_not_found))
        return self.papers, []


def test_batch_chunks_at_500_and_uses_requested_fields() -> None:
    base = tuple(
        evidence("openalex", f"W{index}", f"10.5555/{index:03d}")
        for index in range(501)
    )
    client = BatchClient()

    result = supplement_semantic_scholar_dois(
        client,
        base,
        retrieved_at=TIMESTAMP,
    )

    assert not result.evidence
    assert [len(call[0]) for call in client.calls] == [500, 1]
    assert all(identifier.startswith("DOI:") for call in client.calls for identifier in call[0])
    assert client.calls[0][1] == list(SEMANTIC_SCHOLAR_FIELDS)
    assert all(call[2] for call in client.calls)


def test_batch_matches_returned_doi_not_position_and_anchors_all_evidence() -> None:
    base = (
        evidence("openalex", "W1", "10.5555/shared"),
        evidence("openalex", "W3", "10.5555/shared"),
        evidence("crossref", "10.5555/shared", "10.5555/shared"),
        evidence("openalex", "W2", "10.5555/other"),
        evidence("crossref", "10.5555/crossref-only", "10.5555/crossref-only"),
    )
    client = BatchClient(
        [
            paper("S2-other", doi="10.5555/other"),
            paper("S2-shared", doi="10.5555/shared"),
            paper("S2-crossref", doi="10.5555/crossref-only"),
        ]
    )

    result = supplement_semantic_scholar_dois(
        client,
        base,
        retrieved_at=TIMESTAMP,
    )

    by_id = {item.provenance.record_id: item for item in result.evidence}
    assert [(ref.provider, ref.record_id) for ref in by_id["S2-shared"].supplements] == [
        ("crossref", "10.5555/shared"),
        ("openalex", "W1"),
        ("openalex", "W3"),
    ]
    assert [ref.record_id for ref in by_id["S2-other"].supplements] == ["W2"]
    assert [ref.record_id for ref in by_id["S2-crossref"].supplements] == [
        "10.5555/crossref-only"
    ]


def test_batch_rejects_missing_or_mismatched_returned_doi() -> None:
    base = (evidence("openalex", "W1", "10.5555/requested"),)
    client = BatchClient(
        [
            paper("S2-mismatch", doi="10.5555/different"),
            paper("S2-missing"),
        ]
    )

    result = supplement_semantic_scholar_dois(
        client,
        base,
        retrieved_at=TIMESTAMP,
    )

    assert not result.evidence
    assert len(
        [issue for issue in result.issues if issue.stage == "record_normalization"]
    ) == 2


def test_batch_not_found_is_a_nonfatal_warning() -> None:
    class NotFoundClient:
        def get_papers(self, ids: list[str], **kwargs: object) -> object:
            return [], ["DOI:10.5555/missing"]

    result = supplement_semantic_scholar_dois(
        NotFoundClient(),
        (evidence("openalex", "W1", "10.5555/missing"),),
        retrieved_at=TIMESTAMP,
    )

    assert not result.has_errors
    assert len(result.issues) == 1
    assert result.issues[0].stage == "not_found"
    assert result.issues[0].doi == "10.5555/missing"
    assert result.issues[0].severity is SemanticScholarIssueSeverity.WARNING


class FailingChunkClient:
    def __init__(self) -> None:
        self.calls = 0

    def get_papers(self, ids: list[str], **kwargs: object) -> object:
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("first chunk unavailable")
        doi = ids[0].removeprefix("DOI:")
        return [paper("S2-last", doi=doi)], []


def test_failed_batch_chunk_does_not_erase_later_success() -> None:
    base = tuple(
        evidence("openalex", f"W{index}", f"10.5555/{index:03d}")
        for index in range(501)
    )

    result = supplement_semantic_scholar_dois(
        FailingChunkClient(),
        base,
        retrieved_at=TIMESTAMP,
    )

    assert len(result.evidence) == 1
    assert result.has_errors
    assert any(issue.stage == "batch_lookup" for issue in result.issues)


def test_batch_programming_error_propagates() -> None:
    class BrokenClient:
        def get_papers(self, ids: list[str], **kwargs: object) -> object:
            raise AssertionError("programming bug")

    with pytest.raises(AssertionError, match="programming bug"):
        supplement_semantic_scholar_dois(
            BrokenClient(),
            (evidence("openalex", "W1", "10.5555/one"),),
            retrieved_at=TIMESTAMP,
        )


class SearchClient:
    def __init__(self, results: dict[str, object]) -> None:
        self.results = results
        self.calls: list[tuple[str, dict[str, object]]] = []

    def search_paper(self, query: str, **kwargs: object) -> object:
        journal = kwargs["venue"][0]  # type: ignore[index]
        self.calls.append((query, kwargs))
        outcome = self.results[journal]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def discover_one(
    payload: RawPaper,
    *,
    from_date: date = date(2026, 1, 1),
    to_date: date = date(2026, 12, 31),
) -> object:
    return discover_semantic_scholar_journals(
        SearchClient({"Biometrics": [payload]}),
        (BIOMETRICS,),
        from_date,
        to_date,
        parse_keyword_expression("causal AND NOT review"),
        retrieved_at=TIMESTAMP,
    )


def test_discovery_uses_bulk_query_filters_and_isolates_journals() -> None:
    second = JournalConfig(name="Test Journal", issn=("1234-5679",))
    successful = paper(
        "S2-good",
        doi="10.5555/good",
        venue_name="Test Journal",
        issn="1234-5679",
    )
    client = SearchClient(
        {"Biometrics": ConnectionError("unavailable"), "Test Journal": [successful]}
    )

    result = discover_semantic_scholar_journals(
        client,
        (BIOMETRICS, second),
        date(2026, 1, 1),
        date(2026, 12, 31),
        parse_keyword_expression("causal AND NOT review"),
        retrieved_at=TIMESTAMP,
    )

    assert [record.paper_id for record in result.discovered_records] == ["S2-good"]
    assert result.has_errors
    assert [call[0] for call in client.calls] == ["causal", "causal"]
    assert all(call[1]["bulk"] is True for call in client.calls)
    assert all(call[1]["sort"] == "paperId:asc" for call in client.calls)
    assert all(
        call[1]["publication_date_or_year"] == "2026-01-01:2026-12-31"
        for call in client.calls
    )
    assert all(call[1]["fields"] == list(SEMANTIC_SCHOLAR_FIELDS) for call in client.calls)


def test_discovery_preserves_prefix_in_broad_positive_query() -> None:
    client = SearchClient({"Biometrics": []})

    discover_semantic_scholar_journals(
        client,
        (BIOMETRICS,),
        date(2026, 1, 1),
        date(2026, 12, 31),
        parse_keyword_expression("statist*"),
        retrieved_at=TIMESTAMP,
    )

    assert [call[0] for call in client.calls] == ["statist*"]


def test_discovery_lowers_proximity_and_removes_not_subtree() -> None:
    client = SearchClient({"Biometrics": []})

    discover_semantic_scholar_journals(
        client,
        (BIOMETRICS,),
        date(2026, 1, 1),
        date(2026, 12, 31),
        parse_keyword_expression('"causal inference"~5 AND NOT review'),
        retrieved_at=TIMESTAMP,
    )

    query = client.calls[0][0]
    assert query == "(causal) + (inference)"
    assert "~5" not in query
    assert "NEAR" not in query
    assert "review" not in query


def test_comma_bearing_venue_is_skipped_without_global_search() -> None:
    journal = JournalConfig(
        name="IEEE Transactions on Systems, Man and Cybernetics: Systems",
        issn=("2168-2216",),
    )
    valid_normal_result = paper("S2-normal", doi="10.5555/normal")
    client = SearchClient({"Biometrics": [valid_normal_result]})
    result = discover_semantic_scholar_journals(
        client,
        (journal, BIOMETRICS),
        date(2026, 1, 1),
        date(2026, 12, 31),
        parse_keyword_expression("causal AND NOT review"),
        retrieved_at=TIMESTAMP,
    )

    assert len(client.calls) == 1
    assert client.calls[0][0] == "causal"
    assert client.calls[0][1]["venue"] == ["Biometrics"]
    assert client.calls[0][1]["publication_date_or_year"] == (
        "2026-01-01:2026-12-31"
    )
    assert [record.paper_id for record in result.discovered_records] == [
        "S2-normal"
    ]
    assert len(result.evidence) == 1
    assert result.issues == (
        SemanticScholarIssue(
            severity=SemanticScholarIssueSeverity.WARNING,
            stage="unsupported_venue_filter",
            journal=journal.name,
            message=(
                "supplemental discovery was skipped because the client cannot "
                "safely represent this venue name"
            ),
        ),
    )
    assert result.has_errors is False


def test_comma_bearing_venue_skip_preserves_batch_supplementation() -> None:
    journal = JournalConfig(
        name="IEEE Transactions on Systems, Man and Cybernetics: Systems",
        issn=("2168-2216",),
    )

    class BatchOnlyClient:
        def __init__(self) -> None:
            self.batch_calls: list[list[str]] = []

        def get_papers(self, ids: list[str], **kwargs: object) -> object:
            self.batch_calls.append(ids)
            return [paper("S2-batch", doi="10.5555/one")], []

        def search_paper(self, query: str, **kwargs: object) -> object:
            raise AssertionError("unsupported venue must not trigger search")

    client = BatchOnlyClient()
    result = augment_with_semantic_scholar(
        client,
        (evidence("openalex", "W1", "10.5555/one"),),
        (journal,),
        date(2026, 1, 1),
        date(2026, 12, 31),
        parse_keyword_expression("causal"),
        retrieved_at=TIMESTAMP,
    )

    assert client.batch_calls == [["DOI:10.5555/one"]]
    assert [item.provenance.record_id for item in result.evidence] == ["S2-batch"]
    assert [item.stage for item in result.issues] == ["unsupported_venue_filter"]
    assert result.has_errors is False


def test_search_programming_error_propagates() -> None:
    client = SearchClient({"Biometrics": AssertionError("programming bug")})

    with pytest.raises(AssertionError, match="programming bug"):
        discover_semantic_scholar_journals(
            client,
            (BIOMETRICS,),
            date(2026, 1, 1),
            date(2026, 12, 31),
            parse_keyword_expression("causal"),
            retrieved_at=TIMESTAMP,
        )


def test_paginated_provider_failure_keeps_collected_evidence() -> None:
    def pages() -> object:
        yield paper("S2-first", doi="10.5555/first")
        raise ConnectionError("next page unavailable")

    client = SearchClient({"Biometrics": pages()})
    result = discover_semantic_scholar_journals(
        client,
        (BIOMETRICS,),
        date(2026, 1, 1),
        date(2026, 12, 31),
        parse_keyword_expression("causal"),
        retrieved_at=TIMESTAMP,
    )

    assert [record.paper_id for record in result.discovered_records] == [
        "S2-first"
    ]
    assert result.has_errors
    assert any(issue.stage == "search_failure" for issue in result.issues)


def test_malformed_discovery_record_does_not_stop_peer() -> None:
    client = SearchClient(
        {
            "Biometrics": [
                {"title": "Missing paper ID"},
                paper("S2-valid", doi="10.5555/valid"),
            ]
        }
    )

    result = discover_semantic_scholar_journals(
        client,
        (BIOMETRICS,),
        date(2026, 1, 1),
        date(2026, 12, 31),
        parse_keyword_expression("causal"),
        retrieved_at=TIMESTAMP,
    )

    assert [record.paper_id for record in result.discovered_records] == ["S2-valid"]
    assert any(issue.stage == "record_normalization" for issue in result.issues)


@pytest.mark.parametrize(
    ("payload", "from_date", "to_date", "accepted"),
    [
        (paper("inside", publication_date="2026-06-01"), date(2026, 1, 1), date(2026, 12, 31), True),
        (paper("outside", publication_date="2025-12-31"), date(2026, 1, 1), date(2026, 12, 31), False),
        (paper("year", publication_date=None, year=2026), date(2026, 1, 1), date(2026, 12, 31), True),
        (paper("partial", publication_date=None, year=2026), date(2026, 1, 15), date(2026, 1, 31), False),
        (paper("missing", publication_date=None, year=None), date(2026, 1, 1), date(2026, 12, 31), False),
    ],
)
def test_discovery_date_validation(
    payload: RawPaper,
    from_date: date,
    to_date: date,
    accepted: bool,
) -> None:
    result = discover_one(payload, from_date=from_date, to_date=to_date)

    assert bool(result.discovered_records) is accepted  # type: ignore[attr-defined]
    if not accepted:
        assert any(issue.stage == "date_validation" for issue in result.issues)  # type: ignore[attr-defined]


def test_discovery_venue_validation_prefers_issn_then_strict_names() -> None:
    matching_eissn = discover_one(paper("eissn", issn="1541-0420"))
    mismatch = discover_one(paper("mismatch", issn="1234-5679"))
    exact_name = discover_one(paper("name", issn=None, venue_name="  BIOMETRICS  "))
    alternate = paper("alternate", issn=None, venue_name="Wrong name")
    alternate.raw_data["publicationVenue"]["alternate_names"] = ["Biometrics"]
    alternate_name = discover_one(alternate)
    wrong = paper("wrong", issn=None, venue_name="Biometric Methods")
    wrong.raw_data["publicationVenue"]["alternate_names"] = []
    wrong_name = discover_one(wrong)

    assert matching_eissn.discovered_records  # type: ignore[attr-defined]
    assert not mismatch.discovered_records  # type: ignore[attr-defined]
    assert any(issue.stage == "venue_validation" for issue in mismatch.issues)  # type: ignore[attr-defined]
    assert exact_name.discovered_records  # type: ignore[attr-defined]
    assert alternate_name.discovered_records  # type: ignore[attr-defined]
    assert not wrong_name.discovered_records  # type: ignore[attr-defined]


def test_discovery_rejects_name_only_venue_ambiguous_across_journals() -> None:
    second = JournalConfig(name="Test Journal", issn=("1234-5679",))
    ambiguous = paper("ambiguous", issn=None, venue_name="Biometrics")
    ambiguous.raw_data["publicationVenue"]["alternate_names"] = ["Test Journal"]
    client = SearchClient(
        {"Biometrics": [ambiguous], "Test Journal": [ambiguous]}
    )

    result = discover_semantic_scholar_journals(
        client,
        (BIOMETRICS, second),
        date(2026, 1, 1),
        date(2026, 12, 31),
        parse_keyword_expression("causal"),
        retrieved_at=TIMESTAMP,
    )

    assert not result.discovered_records
    assert [issue.stage for issue in result.issues] == [
        "venue_validation",
        "venue_validation",
    ]


def test_negative_only_query_skips_search_but_keeps_batch_supplementation() -> None:
    client = BatchClient([paper("S2-batch", doi="10.5555/one")])

    result = augment_with_semantic_scholar(
        client,
        (evidence("openalex", "W1", "10.5555/one"),),
        (BIOMETRICS,),
        date(2026, 1, 1),
        date(2026, 1, 31),
        parse_keyword_expression("NOT review"),
        retrieved_at=TIMESTAMP,
    )

    assert len(result.supplement_records) == 1
    assert not result.discovered_records
    assert any(
        issue.stage == "no_positive_query"
        and issue.severity is SemanticScholarIssueSeverity.WARNING
        for issue in result.issues
    )


def test_augmentation_runs_batch_before_supplemental_discovery() -> None:
    events: list[str] = []

    class OrderedClient:
        def get_papers(self, ids: list[str], **kwargs: object) -> object:
            events.append("batch")
            return [], []

        def search_paper(self, query: str, **kwargs: object) -> object:
            events.append("search")
            return []

    augment_with_semantic_scholar(
        OrderedClient(),
        (evidence("openalex", "W1", "10.5555/one"),),
        (BIOMETRICS,),
        date(2026, 1, 1),
        date(2026, 1, 31),
        parse_keyword_expression("causal"),
        retrieved_at=TIMESTAMP,
    )

    assert events == ["batch", "search"]
