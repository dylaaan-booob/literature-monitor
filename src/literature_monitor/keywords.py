"""Tokenizer and syntax parser for configured keyword expressions."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum, auto
from typing import TypeAlias

from literature_monitor.models import CanonicalMetadata


class KeywordSyntaxError(ValueError):
    def __init__(self, message: str, position: int) -> None:
        self.position = position
        super().__init__(f"{message} at column {position + 1}")


@dataclass(frozen=True)
class Term:
    value: str


@dataclass(frozen=True)
class Phrase:
    value: str


@dataclass(frozen=True)
class Not:
    operand: KeywordExpression


@dataclass(frozen=True)
class And:
    left: KeywordExpression
    right: KeywordExpression


@dataclass(frozen=True)
class Or:
    left: KeywordExpression
    right: KeywordExpression


KeywordExpression: TypeAlias = Term | Phrase | Not | And | Or


class _TokenKind(Enum):
    WORD = auto()
    PHRASE = auto()
    AND = auto()
    OR = auto()
    NOT = auto()
    LPAREN = auto()
    RPAREN = auto()
    END = auto()


@dataclass(frozen=True)
class _Token:
    kind: _TokenKind
    value: str
    position: int


_OPERATORS = {
    "and": _TokenKind.AND,
    "or": _TokenKind.OR,
    "not": _TokenKind.NOT,
}

_WHITESPACE_PATTERN = re.compile(r"\s+")


def _tokenize(text: str) -> tuple[_Token, ...]:
    tokens: list[_Token] = []
    position = 0
    while position < len(text):
        character = text[position]
        if character.isspace():
            position += 1
            continue
        if character == "(":
            tokens.append(_Token(_TokenKind.LPAREN, character, position))
            position += 1
            continue
        if character == ")":
            tokens.append(_Token(_TokenKind.RPAREN, character, position))
            position += 1
            continue
        if character == '"':
            start = position
            position += 1
            phrase: list[str] = []
            while position < len(text) and text[position] != '"':
                if text[position] == "\\":
                    escape_position = position
                    position += 1
                    if position >= len(text):
                        raise KeywordSyntaxError("unfinished escape sequence", escape_position)
                    if text[position] not in {'"', "\\"}:
                        raise KeywordSyntaxError("unsupported escape sequence", escape_position)
                phrase.append(text[position])
                position += 1
            if position >= len(text):
                raise KeywordSyntaxError("unterminated phrase", start)
            if not phrase:
                raise KeywordSyntaxError("phrase must not be empty", start)
            tokens.append(_Token(_TokenKind.PHRASE, "".join(phrase), start))
            position += 1
            continue

        start = position
        while (
            position < len(text)
            and not text[position].isspace()
            and text[position] not in {'(', ')', '"'}
        ):
            position += 1
        value = text[start:position]
        kind = _OPERATORS.get(value.casefold(), _TokenKind.WORD)
        tokens.append(_Token(kind, value, start))

    tokens.append(_Token(_TokenKind.END, "", len(text)))
    return tuple(tokens)


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens = _tokenize(text)
        self.index = 0

    @property
    def current(self) -> _Token:
        return self.tokens[self.index]

    def advance(self) -> _Token:
        token = self.current
        self.index += 1
        return token

    def parse(self) -> KeywordExpression:
        if self.current.kind is _TokenKind.END:
            raise KeywordSyntaxError("expression must not be empty", self.current.position)
        expression = self.parse_or()
        if self.current.kind is not _TokenKind.END:
            raise KeywordSyntaxError(
                f"expected an operator, found {self.current.value!r}",
                self.current.position,
            )
        return expression

    def parse_or(self) -> KeywordExpression:
        expression = self.parse_and()
        while self.current.kind is _TokenKind.OR:
            self.advance()
            expression = Or(expression, self.parse_and())
        return expression

    def parse_and(self) -> KeywordExpression:
        expression = self.parse_unary()
        while self.current.kind is _TokenKind.AND:
            self.advance()
            expression = And(expression, self.parse_unary())
        return expression

    def parse_unary(self) -> KeywordExpression:
        if self.current.kind is _TokenKind.NOT:
            self.advance()
            return Not(self.parse_unary())
        return self.parse_primary()

    def parse_primary(self) -> KeywordExpression:
        token = self.current
        if token.kind is _TokenKind.WORD:
            self.advance()
            return Term(token.value)
        if token.kind is _TokenKind.PHRASE:
            self.advance()
            return Phrase(token.value)
        if token.kind is _TokenKind.LPAREN:
            self.advance()
            expression = self.parse_or()
            if self.current.kind is not _TokenKind.RPAREN:
                raise KeywordSyntaxError("expected closing parenthesis", self.current.position)
            self.advance()
            return expression
        if token.kind is _TokenKind.RPAREN:
            raise KeywordSyntaxError("unexpected closing parenthesis", token.position)
        if token.kind is _TokenKind.END:
            raise KeywordSyntaxError("expected a word, phrase, or parenthesized expression", token.position)
        raise KeywordSyntaxError(f"unexpected operator {token.value!r}", token.position)


def parse_keyword_expression(text: str) -> KeywordExpression:
    return _Parser(text).parse()


def _normalize_search_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _WHITESPACE_PATTERN.sub(" ", normalized).strip()


def _is_token_character(character: str) -> bool:
    return character == "_" or character.isalnum()


def _unit_contains_literal(unit: str, literal: str) -> bool:
    if not literal:
        return False

    require_left_boundary = _is_token_character(literal[0])
    require_right_boundary = _is_token_character(literal[-1])
    start = 0
    while True:
        index = unit.find(literal, start)
        if index < 0:
            return False
        end = index + len(literal)
        left_valid = (
            not require_left_boundary
            or index == 0
            or not _is_token_character(unit[index - 1])
        )
        right_valid = (
            not require_right_boundary
            or end == len(unit)
            or not _is_token_character(unit[end])
        )
        if left_valid and right_valid:
            return True
        start = index + 1


def evaluate_keyword_expression(
    expression: KeywordExpression,
    metadata: CanonicalMetadata,
) -> bool:
    """Evaluate an expression against independent title/keyword/abstract units."""

    values = (metadata.title, *metadata.author_keywords, metadata.abstract)
    units = tuple(
        normalized
        for value in values
        if value is not None
        if (normalized := _normalize_search_text(value))
    )

    def evaluate(node: KeywordExpression) -> bool:
        if isinstance(node, (Term, Phrase)):
            literal = _normalize_search_text(node.value)
            return any(_unit_contains_literal(unit, literal) for unit in units)
        if isinstance(node, Not):
            return not evaluate(node.operand)
        if isinstance(node, And):
            return evaluate(node.left) and evaluate(node.right)
        if isinstance(node, Or):
            return evaluate(node.left) or evaluate(node.right)
        raise TypeError(f"unsupported keyword expression node: {type(node).__name__}")

    return evaluate(expression)
