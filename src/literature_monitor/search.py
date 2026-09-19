"""Transient SQLite FTS5 backend for local lexical search."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections.abc import Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass

from literature_monitor.keywords import And, KeywordExpression, Not, Or, Phrase, Term
from literature_monitor.models import CanonicalMetadata, ProviderWorkEvidence


class SearchBackendError(RuntimeError):
    """Raised when the configured lexical-search backend cannot run."""


@dataclass(frozen=True)
class SearchableProjection:
    titles: tuple[str, ...] = ()
    author_keywords: tuple[str, ...] = ()
    abstracts: tuple[str, ...] = ()


_WHITESPACE_PATTERN = re.compile(r"\s+")


def _normalize_lexical_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _WHITESPACE_PATTERN.sub(" ", normalized).strip()


def _deduplicate_units(values: Iterable[str | None]) -> tuple[str, ...]:
    units: dict[str, str] = {}
    for value in values:
        if value is None or not (normalized := _normalize_lexical_text(value)):
            continue
        units.setdefault(normalized, value.strip())
    return tuple(units.values())


def build_searchable_projection(
    evidence: Sequence[ProviderWorkEvidence],
) -> SearchableProjection:
    """Build independent searchable units from consolidated provider evidence."""

    return SearchableProjection(
        titles=_deduplicate_units(record.title for record in evidence),
        author_keywords=_deduplicate_units(
            keyword
            for record in evidence
            for keyword in record.author_keywords
        ),
        abstracts=_deduplicate_units(record.abstract for record in evidence),
    )


def build_metadata_searchable_projection(
    metadata: CanonicalMetadata,
) -> SearchableProjection:
    """Build searchable units from canonical metadata for diagnostic searches."""

    return SearchableProjection(
        titles=_deduplicate_units((metadata.title,)),
        author_keywords=_deduplicate_units(metadata.author_keywords),
        abstracts=_deduplicate_units((metadata.abstract,)),
    )


def _create_fts5_schema(connection: sqlite3.Connection) -> None:
    try:
        connection.execute(
            """
            CREATE VIRTUAL TABLE searchable_units USING fts5(
                document_id UNINDEXED,
                title,
                author_keywords,
                abstract,
                tokenize = 'unicode61'
            )
            """
        )
        connection.execute(
            """
            CREATE VIRTUAL TABLE atomic_operands USING fts5(
                value,
                tokenize = 'unicode61'
            )
            """
        )
        connection.execute(
            """
            CREATE VIRTUAL TABLE atomic_operand_tokens USING fts5vocab(
                atomic_operands,
                'instance'
            )
            """
        )
    except sqlite3.OperationalError as error:
        raise SearchBackendError(
            "SQLite FTS5 is unavailable in the current runtime"
        ) from error


def _normalized_unique_units(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            normalized
            for value in values
            if (normalized := _normalize_lexical_text(value))
        )
    )


def _index_projections(
    connection: sqlite3.Connection,
    projections: Sequence[SearchableProjection],
) -> None:
    rows: list[tuple[int, str | None, str | None, str | None]] = []
    for document_id, projection in enumerate(projections):
        rows.extend(
            (document_id, title, None, None)
            for title in _normalized_unique_units(projection.titles)
        )
        rows.extend(
            (document_id, None, keyword, None)
            for keyword in _normalized_unique_units(projection.author_keywords)
        )
        rows.extend(
            (document_id, None, None, abstract)
            for abstract in _normalized_unique_units(projection.abstracts)
        )

    connection.executemany(
        """
        INSERT INTO searchable_units(
            document_id,
            title,
            author_keywords,
            abstract
        ) VALUES (?, ?, ?, ?)
        """,
        rows,
    )


def _tokenize_atomic_operand(
    connection: sqlite3.Connection,
    normalized_value: str,
) -> tuple[str, ...]:
    cursor = connection.execute(
        "INSERT INTO atomic_operands(value) VALUES (?)",
        (normalized_value,),
    )
    row_id = cursor.lastrowid
    if row_id is None:
        raise SearchBackendError("SQLite FTS5 did not return an operand row id")
    return tuple(
        row[0]
        for row in connection.execute(
            """
            SELECT term
            FROM atomic_operand_tokens
            WHERE doc = ?
            ORDER BY offset
            """,
            (row_id,),
        )
    )


def _quoted_fts5_phrase(tokens: tuple[str, ...]) -> str:
    escaped = " ".join(token.replace('"', '""') for token in tokens)
    return f'"{escaped}"'


def _matching_document_ids(
    connection: sqlite3.Connection,
    tokens: tuple[str, ...],
) -> frozenset[int]:
    query = _quoted_fts5_phrase(tokens)
    return frozenset(
        row[0]
        for row in connection.execute(
            """
            SELECT DISTINCT CAST(document_id AS INTEGER)
            FROM searchable_units
            WHERE searchable_units MATCH ?
            """,
            (query,),
        )
    )


def match_searchable_projections(
    expression: KeywordExpression,
    projections: Sequence[SearchableProjection],
) -> tuple[bool, ...]:
    """Evaluate one keyword AST across a batch of searchable projections."""

    if not projections:
        return ()

    with closing(sqlite3.connect(":memory:")) as connection:
        _create_fts5_schema(connection)
        _index_projections(connection, projections)

        universe = frozenset(range(len(projections)))
        token_cache: dict[str, tuple[str, ...]] = {}
        match_cache: dict[tuple[str, ...], frozenset[int]] = {}

        def atomic_matches(value: str) -> frozenset[int]:
            normalized = _normalize_lexical_text(value)
            if not normalized:
                return frozenset()
            if normalized not in token_cache:
                token_cache[normalized] = _tokenize_atomic_operand(
                    connection,
                    normalized,
                )
            tokens = token_cache[normalized]
            if not tokens:
                return frozenset()
            if tokens not in match_cache:
                match_cache[tokens] = _matching_document_ids(connection, tokens)
            return match_cache[tokens]

        def evaluate(node: KeywordExpression) -> frozenset[int]:
            if isinstance(node, (Term, Phrase)):
                return atomic_matches(node.value)
            if isinstance(node, And):
                return evaluate(node.left) & evaluate(node.right)
            if isinstance(node, Or):
                return evaluate(node.left) | evaluate(node.right)
            if isinstance(node, Not):
                return universe - evaluate(node.operand)
            raise TypeError(
                f"unsupported keyword expression node: {type(node).__name__}"
            )

        matched = evaluate(expression)
        return tuple(document_id in matched for document_id in range(len(projections)))
