from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

import literature_monitor.search as search_backend
from literature_monitor.keywords import And, Not, Or, Phrase, Term, parse_keyword_expression
from literature_monitor.models import (
    CanonicalMetadata,
    MetadataSource,
    ProviderTopic,
    ProviderWorkEvidence,
)
from literature_monitor.search import (
    SearchBackendError,
    SearchableProjection,
    build_metadata_searchable_projection,
    build_searchable_projection,
    match_searchable_projections,
)


def evidence(
    provider: str,
    *,
    title: str | None = None,
    abstract: str | None = None,
    author_keywords: tuple[str, ...] = (),
    provider_topics: tuple[ProviderTopic, ...] = (),
    fields_of_study: tuple[str, ...] = (),
) -> ProviderWorkEvidence:
    return ProviderWorkEvidence(
        provenance=MetadataSource(
            provider=provider,
            record_id=f"{provider}-record",
            retrieved_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
        ),
        title=title,
        abstract=abstract,
        author_keywords=author_keywords,
        provider_topics=provider_topics,
        fields_of_study=fields_of_study,
    )


def matches(
    expression: str,
    projection: SearchableProjection,
) -> bool:
    return match_searchable_projections(
        parse_keyword_expression(expression),
        (projection,),
    )[0]


def test_evidence_projection_retains_independent_searchable_fields() -> None:
    projection = build_searchable_projection(
        (
            evidence(
                "openalex",
                title="OpenAlex title",
                abstract="OpenAlex abstract",
                author_keywords=("causal", "inference"),
            ),
            evidence(
                "crossref",
                title="Crossref title",
                abstract="Crossref abstract",
                author_keywords=("genomics",),
            ),
        )
    )

    assert projection.titles == ("OpenAlex title", "Crossref title")
    assert projection.abstracts == ("OpenAlex abstract", "Crossref abstract")
    assert projection.author_keywords == ("causal", "inference", "genomics")


def test_evidence_projection_ignores_missing_and_normalization_empty_units() -> None:
    projection = build_searchable_projection(
        (
            evidence("openalex", title="Title only", abstract=None),
            evidence("crossref", title=None, abstract=" \n "),
        )
    )

    assert projection == SearchableProjection(titles=("Title only",))


def test_evidence_projection_safely_deduplicates_equivalent_units() -> None:
    projection = build_searchable_projection(
        (
            evidence("openalex", title="Repeated   Title"),
            evidence("crossref", title=" repeated title "),
        )
    )

    assert len(projection.titles) == 1
    assert matches("repeated", projection)


def test_provider_taxonomy_is_excluded_from_projection() -> None:
    projection = build_searchable_projection(
        (
            evidence(
                "semantic_scholar",
                title="Unrelated",
                provider_topics=(ProviderTopic(value="Causal inference"),),
                fields_of_study=("Genomics",),
            ),
        )
    )

    assert not matches("causal OR genomics", projection)


def test_canonical_metadata_projection_uses_the_same_field_boundaries() -> None:
    projection = build_metadata_searchable_projection(
        CanonicalMetadata(
            title="Canonical title",
            journal="Test Journal",
            abstract="Canonical abstract",
            author_keywords=("first keyword", "second keyword"),
        )
    )

    assert projection == SearchableProjection(
        titles=("Canonical title",),
        author_keywords=("first keyword", "second keyword"),
        abstracts=("Canonical abstract",),
    )


@pytest.mark.parametrize(
    ("expression", "projection"),
    [
        ("titleword", SearchableProjection(titles=("TitleWord study",))),
        ("abstractword", SearchableProjection(abstracts=("AbstractWord result",))),
        ("keywordword", SearchableProjection(author_keywords=("KeywordWord",))),
    ],
)
def test_terms_match_each_searchable_field(
    expression: str,
    projection: SearchableProjection,
) -> None:
    assert matches(expression, projection)


def test_terms_do_not_match_embedded_token_substrings() -> None:
    assert not matches("net", SearchableProjection(titles=("internet methods",)))


def test_nfkc_casefold_and_whitespace_normalization_precede_tokenization() -> None:
    projection = SearchableProjection(
        titles=("ＦＵＬＬ width",),
        abstracts=("Die Straße", "deep\n\tlearning"),
    )

    assert matches("full", projection)
    assert matches("strasse", projection)
    assert matches('"deep    learning"', projection)


@pytest.mark.parametrize(
    ("expression", "text"),
    [
        ("high-dimensional", "high-dimensional inference"),
        ("high-dimensional", "high dimensional inference"),
        ("p>>n", "the p>>n regime"),
        ("p>>n", "the p n regime"),
        ("x/y", "x y analysis"),
    ],
)
def test_punctuation_terms_follow_unicode61_token_sequences(
    expression: str,
    text: str,
) -> None:
    assert matches(expression, SearchableProjection(titles=(text,)))


@pytest.mark.parametrize("operand", [Term("---"), Term("> >"), Phrase("   ")])
def test_zero_token_atomic_operands_match_nothing(
    operand: Term | Phrase,
) -> None:
    assert match_searchable_projections(
        operand,
        (SearchableProjection(titles=("anything",)),),
    ) == (False,)


def test_fts_metacharacters_are_lexical_input_not_query_syntax() -> None:
    expression = parse_keyword_expression(r'"alpha\" OR beta*"')
    projections = (
        SearchableProjection(titles=("alpha or beta",)),
        SearchableProjection(titles=("alpha beta",)),
        SearchableProjection(titles=("alphabet",)),
    )

    assert match_searchable_projections(expression, projections) == (
        True,
        False,
        False,
    )


def test_embedded_quote_is_safely_tokenized() -> None:
    expression = parse_keyword_expression(r'"alpha\"beta"')

    assert match_searchable_projections(
        expression,
        (SearchableProjection(titles=("alpha beta",)),),
    ) == (True,)


@pytest.mark.parametrize(
    "projection",
    [
        SearchableProjection(titles=("deep",), abstracts=("learning",)),
        SearchableProjection(author_keywords=("deep", "learning")),
        SearchableProjection(titles=("deep", "learning")),
    ],
)
def test_phrase_cannot_span_searchable_units(
    projection: SearchableProjection,
) -> None:
    assert not matches('"deep learning"', projection)


def test_multi_token_term_cannot_span_searchable_units() -> None:
    projection = SearchableProjection(titles=("high",), abstracts=("dimensional",))

    assert not matches("high-dimensional", projection)


def test_phrase_cannot_span_provider_records() -> None:
    projection = build_searchable_projection(
        (
            evidence("openalex", title="deep"),
            evidence("crossref", title="learning"),
        )
    )

    assert not matches('"deep learning"', projection)


@pytest.mark.parametrize(
    "projection",
    [
        SearchableProjection(titles=("deep learning methods",)),
        SearchableProjection(abstracts=("applications of deep learning",)),
    ],
)
def test_phrase_matches_inside_one_searchable_unit(
    projection: SearchableProjection,
) -> None:
    assert matches('"deep learning"', projection)


def test_phrase_regression_and_boolean_across_units() -> None:
    projection = SearchableProjection(titles=("deep",), abstracts=("learning",))

    assert not matches('"deep learning"', projection)
    assert matches("deep AND learning", projection)


def test_boolean_and_may_match_across_provider_units() -> None:
    projection = build_searchable_projection(
        (
            evidence("openalex", title="Causal inference"),
            evidence(
                "semantic_scholar",
                abstract="Genomics applications",
            ),
        )
    )

    assert matches("causal AND genomics", projection)
    assert not matches('"inference genomics"', projection)


def test_or_not_nested_and_ast_precedence_use_work_level_sets() -> None:
    projections = (
        SearchableProjection(titles=("alpha",)),
        SearchableProjection(titles=("beta",)),
        SearchableProjection(titles=("beta gamma",)),
    )
    expression = Or(Term("alpha"), And(Term("beta"), Not(Term("gamma"))))

    assert match_searchable_projections(expression, projections) == (
        True,
        True,
        False,
    )


def test_not_complements_against_the_complete_document_universe() -> None:
    projections = (
        SearchableProjection(),
        SearchableProjection(titles=("present",)),
    )

    assert match_searchable_projections(Not(Term("missing")), projections) == (
        True,
        True,
    )
    assert match_searchable_projections(Not(Term("present")), projections) == (
        True,
        False,
    )


def test_repeated_atomic_operand_remains_correct() -> None:
    expression = And(Term("causal"), Or(Term("causal"), Term("other")))

    assert match_searchable_projections(
        expression,
        (SearchableProjection(titles=("causal methods",)),),
    ) == (True,)


def test_crossref_abstract_rescues_openalex_lexical_miss() -> None:
    projection = build_searchable_projection(
        (
            evidence("openalex", title="Unrelated title"),
            evidence(
                "crossref",
                title="Another title",
                abstract="Cox regression identifies the signal",
            ),
        )
    )

    assert matches('"cox regression"', projection)


def test_missing_optional_fields_degrade_to_available_units() -> None:
    projection = build_searchable_projection(
        (evidence("crossref", title="Available title"),)
    )

    assert matches("available", projection)
    assert not matches("missing", projection)


def test_batch_results_preserve_input_order_and_use_one_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_connect = sqlite3.connect
    databases: list[str] = []

    def counting_connect(database: str) -> sqlite3.Connection:
        databases.append(database)
        return real_connect(database)

    monkeypatch.setattr(search_backend.sqlite3, "connect", counting_connect)
    projections = (
        SearchableProjection(titles=("match",)),
        SearchableProjection(titles=("miss",)),
        SearchableProjection(abstracts=("another match",)),
    )

    assert match_searchable_projections(Term("match"), projections) == (
        True,
        False,
        True,
    )
    assert databases == [":memory:"]


def test_empty_batch_needs_no_index() -> None:
    assert match_searchable_projections(Term("anything"), ()) == ()


def test_fts5_unavailable_raises_clear_error_and_closes_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnavailableConnection:
        closed = False

        def execute(self, _statement: str) -> None:
            raise sqlite3.OperationalError("no such module: fts5")

        def close(self) -> None:
            self.closed = True

    connection = UnavailableConnection()
    monkeypatch.setattr(
        search_backend.sqlite3,
        "connect",
        lambda _database: connection,
    )

    with pytest.raises(SearchBackendError, match="SQLite FTS5 is unavailable"):
        match_searchable_projections(Term("anything"), (SearchableProjection(),))

    assert connection.closed
