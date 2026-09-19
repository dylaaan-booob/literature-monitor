import pytest

from literature_monitor.keywords import (
    And,
    KeywordSyntaxError,
    Not,
    Or,
    Phrase,
    Term,
    broad_positive_query,
    parse_keyword_expression,
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


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("causal", "causal"),
        ('"causal inference"', '"causal inference"'),
        ("causal AND genomics", "(causal) + (genomics)"),
        ("causal OR bayesian", "(causal) | (bayesian)"),
        (
            "(causal OR bayesian) AND genomics",
            "((causal) | (bayesian)) + (genomics)",
        ),
        ("causal AND NOT review", "causal"),
        ("(causal OR bayesian) AND NOT editorial", "(causal) | (bayesian)"),
        ("NOT review", None),
    ],
)
def test_broad_positive_query_derivation(
    expression: str,
    expected: str | None,
) -> None:
    assert broad_positive_query(parse_keyword_expression(expression)) == expected


def test_broad_positive_query_sanitizes_provider_operators() -> None:
    expression = And(
        Term("causal|review"),
        Phrase('genomics + -editorial (survey) "quoted"'),
    )

    assert broad_positive_query(expression) == (
        '(causal review) + ("genomics editorial survey quoted")'
    )
