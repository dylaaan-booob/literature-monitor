import pytest

from literature_monitor.keywords import (
    And,
    KeywordSyntaxError,
    Not,
    Or,
    Phrase,
    Term,
    evaluate_keyword_expression,
    parse_keyword_expression,
)
from literature_monitor.models import CanonicalMetadata


def matches(
    expression: str,
    *,
    title: str = "Unrelated title",
    author_keywords: tuple[str, ...] = (),
    abstract: str | None = None,
) -> bool:
    return evaluate_keyword_expression(
        parse_keyword_expression(expression),
        CanonicalMetadata(
            title=title,
            journal="Test Journal",
            author_keywords=author_keywords,
            abstract=abstract,
        ),
    )


def test_operator_precedence_and_case_insensitivity() -> None:
    assert parse_keyword_expression("alpha oR beta aNd nOt gamma") == Or(
        Term("alpha"), And(Term("beta"), Not(Term("gamma")))
    )


def test_parentheses_override_precedence() -> None:
    assert parse_keyword_expression("(alpha OR beta) AND gamma") == And(
        Or(Term("alpha"), Term("beta")), Term("gamma")
    )


def test_phrases_support_quote_and_backslash_escapes() -> None:
    expression = parse_keyword_expression(r'"high \"dimensional\"" AND "path\\name"')
    assert expression == And(Phrase('high "dimensional"'), Phrase(r"path\name"))


@pytest.mark.parametrize(
    "word", ["android", "origin", "notebook", "ANDed", "x/y", "a-b", "p>>n"]
)
def test_operator_names_only_match_entire_word_token(word: str) -> None:
    assert parse_keyword_expression(word) == Term(word)


def test_p_greater_greater_n_is_one_word_inside_an_expression() -> None:
    assert parse_keyword_expression("p>>n AND statistics") == And(
        Term("p>>n"), Term("statistics")
    )


@pytest.mark.parametrize(
    "expression",
    [
        "",
        '""',
        "alpha beta",
        "alpha AND",
        "OR alpha",
        "(alpha OR beta",
        "alpha)",
        r'"bad\nescape"',
    ],
)
def test_invalid_expressions_include_column(expression: str) -> None:
    with pytest.raises(KeywordSyntaxError, match="column"):
        parse_keyword_expression(expression)


def test_terms_and_phrases_match_casefolded_nfkc_whitespace_normalized_units() -> None:
    assert matches("strasse", title="Die Straße")
    assert matches("p>>n", title="Ｐ>>Ｎ asymptotics")
    assert matches('"high dimensional"', abstract="high\n\tdimensional inference")


def test_title_abstract_and_each_author_keyword_are_independent_units() -> None:
    assert matches("titleword", title="TitleWord study")
    assert matches("abstractword", abstract="An AbstractWord appears here")
    assert matches("keywordword", author_keywords=("KeywordWord",))
    assert not matches('"deep learning"', title="deep", abstract="learning")
    assert not matches('"deep learning"', author_keywords=("deep", "learning"))


def test_and_subexpressions_may_match_different_units() -> None:
    assert matches(
        "causal AND genomics",
        title="Causal inference",
        abstract="Applications in genomics",
    )


def test_boolean_semantics_precedence_parentheses_and_not() -> None:
    assert not matches("alpha OR beta AND gamma", title="beta")
    assert matches("(alpha OR beta) AND gamma", title="beta", abstract="gamma")
    assert matches("alpha AND NOT excluded", title="alpha")
    assert not matches("alpha AND NOT excluded", title="alpha excluded")


def test_missing_optional_fields_degrade_to_available_units() -> None:
    assert matches("abstract", title="Other", abstract="Abstract match")
    assert matches("title", title="Title only")
    assert not matches("missing", title="Title only")


@pytest.mark.parametrize(
    ("expression", "matching", "nonmatching"),
    [
        ("high-dimensional", "high-dimensional statistics", "high dimensional statistics"),
        ("multi-view", "multi-view learning", "multi view learning"),
        ("p>>n", "the p>>n regime", "the p > > n regime"),
    ],
)
def test_punctuation_is_literal(
    expression: str, matching: str, nonmatching: str
) -> None:
    assert matches(expression, title=matching)
    assert not matches(expression, title=nonmatching)


def test_token_boundaries_reject_embedded_only_matches() -> None:
    assert not matches("net", title="internet")
    assert not matches("alpha", title="alpha_numeric")
    assert matches("alpha", title="alpha-beta")


def test_boundary_search_continues_after_an_invalid_occurrence() -> None:
    assert matches("net", title="internet methods for net analysis")


def test_boundary_constraints_only_apply_to_literal_token_edges() -> None:
    assert matches(">>n", title="p>>n")
    assert matches("p>>", title="p>>n")


def test_whitespace_only_phrase_does_not_match_everything() -> None:
    assert not matches('"   "', title="Any title")
