import pytest

from literature_monitor.keywords import (
    And,
    KeywordSyntaxError,
    Not,
    Or,
    Phrase,
    Prefix,
    Proximity,
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


def test_prefix_parses_as_explicit_operand() -> None:
    assert parse_keyword_expression("statist*") == Prefix("statist")


def test_prefix_validation_uses_nfkc_and_casefold() -> None:
    assert parse_keyword_expression("ﬃ*") == Prefix("ﬃ")
    assert parse_keyword_expression("ßa*") == Prefix("ßa")


@pytest.mark.parametrize(
    ("expression", "position"),
    [
        ("*ology", 0),
        ("stat*ical", 4),
        ("stat**", 5),
        ("ab*", 2),
    ],
)
def test_invalid_prefix_forms_report_wildcard_column(
    expression: str,
    position: int,
) -> None:
    with pytest.raises(KeywordSyntaxError) as error:
        parse_keyword_expression(expression)

    assert error.value.position == position
    assert "column" in str(error.value)


@pytest.mark.parametrize("expression", ["high-dimensional*", "high dimensional*"])
def test_prefix_rejects_punctuation_or_multiple_terms(expression: str) -> None:
    with pytest.raises(KeywordSyntaxError, match="column"):
        parse_keyword_expression(expression)


def test_quoted_prefix_text_remains_a_phrase() -> None:
    assert parse_keyword_expression('"statist*"') == Phrase("statist*")


def test_question_mark_has_no_wildcard_semantics() -> None:
    assert parse_keyword_expression("stat?") == Term("stat?")


def test_operator_name_with_trailing_star_is_a_prefix() -> None:
    assert parse_keyword_expression("AND*") == Prefix("AND")


def test_proximity_parses_with_or_without_space_before_distance() -> None:
    assert parse_keyword_expression('"causal inference"~0') == Proximity(
        "causal inference", 0
    )
    assert parse_keyword_expression('"causal inference" ~5') == Proximity(
        "causal inference", 5
    )


def test_phrase_without_proximity_suffix_remains_a_phrase() -> None:
    assert parse_keyword_expression('"causal inference"') == Phrase(
        "causal inference"
    )


@pytest.mark.parametrize(
    "expression",
    [
        '"causal inference"~',
        '"causal inference"~-1',
        '"causal inference"~51',
        '"causal inference"~x',
    ],
)
def test_invalid_proximity_syntax_includes_column(expression: str) -> None:
    with pytest.raises(KeywordSyntaxError, match="column"):
        parse_keyword_expression(expression)


def test_invalid_proximity_distance_reports_suffix_column() -> None:
    bare = '"causal inference"~'
    with pytest.raises(KeywordSyntaxError) as bare_error:
        parse_keyword_expression(bare)
    assert bare_error.value.position == bare.index("~")

    for expression in (
        '"causal inference"~-1',
        '"causal inference"~51',
        '"causal inference"~x',
    ):
        with pytest.raises(KeywordSyntaxError) as error:
            parse_keyword_expression(expression)
        assert error.value.position == expression.index("~") + 1


def test_proximity_preserves_phrase_escaping() -> None:
    expression = parse_keyword_expression(r'"high \"dimensional\" path\\name" ~5')
    assert expression == Proximity(r'high "dimensional" path\name', 5)


def test_prefix_marker_inside_proximity_content_is_not_prefix_syntax() -> None:
    expression = parse_keyword_expression('"statist* inference"~5')

    assert expression == Proximity("statist* inference", 5)
    assert broad_positive_query(expression) == "(statist) + (inference)"


def test_proximity_token_count_validation_is_deferred_to_fts5_backend() -> None:
    assert parse_keyword_expression('"causal"~2') == Proximity("causal", 2)
    assert parse_keyword_expression('"---"~2') == Proximity("---", 2)


def test_prefix_and_proximity_compose_with_boolean_precedence() -> None:
    assert parse_keyword_expression(
        '(statist* OR "causal inference"~5) AND NOT AND*'
    ) == And(
        Or(Prefix("statist"), Proximity("causal inference", 5)),
        Not(Prefix("AND")),
    )


@pytest.mark.parametrize(
    "word", ["android", "origin", "notebook", "ANDed", "x/y", "a-b", "p>>n"]
)
def test_operator_names_only_match_entire_word_token(word: str) -> None:
    assert parse_keyword_expression(word) == Term(word)


@pytest.mark.parametrize("word", ["a~b", "causal~2", "~"])
def test_unquoted_tilde_remains_ordinary_term_punctuation(word: str) -> None:
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
        ("statist*", "statist*"),
        ('"causal inference"~5', "(causal) + (inference)"),
        (
            'statist* AND "causal inference"~5',
            "(statist*) + ((causal) + (inference))",
        ),
        (
            'statist* OR "causal inference"~5',
            "(statist*) | ((causal) + (inference))",
        ),
        ('"causal inference"~5 AND NOT review', "(causal) + (inference)"),
        ("NOT review", None),
        ("NOT statist*", None),
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


def test_broad_positive_query_sanitizes_proximity_metacharacters() -> None:
    expression = Proximity('causal|inference + review* "quoted"', 5)

    assert broad_positive_query(expression) == (
        "(causal) + (inference) + (review) + (quoted)"
    )
