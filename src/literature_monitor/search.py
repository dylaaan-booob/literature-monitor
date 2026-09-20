"""Transient SQLite FTS5 backend for local lexical search."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass

from literature_monitor.keywords import (
    And,
    KeywordExpression,
    Not,
    Or,
    Phrase,
    Prefix,
    Proximity,
    Term,
)
from literature_monitor.models import CanonicalMetadata, ProviderWorkEvidence


class SearchBackendError(RuntimeError):
    """Raised when the configured lexical-search backend cannot run."""


class SearchExpressionError(ValueError):
    """Raised when an AST operand violates the local lexical-search contract."""


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
        connection.execute(
            """
            CREATE VIRTUAL TABLE searchable_unit_tokens USING fts5vocab(
                searchable_units,
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


def _validated_operand_tokens(
    connection: sqlite3.Connection,
    node: Term | Phrase | Prefix | Proximity,
    token_cache: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    normalized = _normalize_lexical_text(node.value)
    if normalized not in token_cache:
        token_cache[normalized] = (
            _tokenize_atomic_operand(connection, normalized)
            if normalized
            else ()
        )
    tokens = token_cache[normalized]

    if isinstance(node, Prefix) and len(tokens) != 1:
        raise SearchExpressionError(
            f"prefix operand {node.value!r} must produce exactly one lexical token; "
            f"found {len(tokens)}"
        )
    if isinstance(node, Proximity):
        if len(tokens) < 2:
            raise SearchExpressionError(
                f"proximity operand {node.value!r} must contain at least two lexical "
                f"tokens; found {len(tokens)}"
            )
        if not 0 <= node.distance <= 50:
            raise SearchExpressionError(
                f"proximity operand {node.value!r} distance must be between 0 and 50"
            )
    return tokens


def _fts5_query_for_operand(
    node: Term | Phrase | Prefix | Proximity,
    tokens: tuple[str, ...],
) -> str:
    if isinstance(node, Prefix):
        return f"{_quoted_fts5_phrase(tokens)}*"
    if isinstance(node, Proximity):
        quoted_tokens = " ".join(
            _quoted_fts5_phrase((token,)) for token in tokens
        )
        fts_near_distance = node.distance + len(tokens) - 2
        return f"NEAR({quoted_tokens}, {fts_near_distance})"
    return _quoted_fts5_phrase(tokens)


def _matching_document_ids(
    connection: sqlite3.Connection,
    query: str,
) -> frozenset[int]:
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


def _repeated_proximity_row_matches(
    connection: sqlite3.Connection,
    row_id: int,
    tokens: tuple[str, ...],
    user_distance: int,
) -> bool:
    required = Counter(tokens)
    max_span = len(tokens) + user_distance
    relevant_by_column: dict[str, list[tuple[int, str]]] = {}
    for term, column, offset in connection.execute(
        """
        SELECT term, col, offset
        FROM searchable_unit_tokens
        WHERE doc = ?
        ORDER BY col, offset
        """,
        (row_id,),
    ):
        if term in required:
            relevant_by_column.setdefault(column, []).append((offset, term))

    for occurrences in relevant_by_column.values():
        observed: Counter[str] = Counter()
        left = 0
        for right_offset, right_term in occurrences:
            observed[right_term] += 1
            while right_offset - occurrences[left][0] + 1 > max_span:
                left_term = occurrences[left][1]
                observed[left_term] -= 1
                left += 1
            if all(observed[term] >= count for term, count in required.items()):
                return True
    return False


def _matching_repeated_proximity_document_ids(
    connection: sqlite3.Connection,
    query: str,
    tokens: tuple[str, ...],
    user_distance: int,
) -> frozenset[int]:
    matched: set[int] = set()
    for row_id, document_id in connection.execute(
        """
        SELECT rowid, CAST(document_id AS INTEGER)
        FROM searchable_units
        WHERE searchable_units MATCH ?
        """,
        (query,),
    ):
        if _repeated_proximity_row_matches(
            connection,
            row_id,
            tokens,
            user_distance,
        ):
            matched.add(document_id)
    return frozenset(matched)


def validate_search_expression(expression: KeywordExpression) -> None:
    """Validate FTS5-dependent lexical constraints without evaluating documents."""

    with closing(sqlite3.connect(":memory:")) as connection:
        _create_fts5_schema(connection)
        token_cache: dict[str, tuple[str, ...]] = {}

        def validate(node: KeywordExpression) -> None:
            if isinstance(node, (Term, Phrase, Prefix, Proximity)):
                _validated_operand_tokens(connection, node, token_cache)
                return
            if isinstance(node, (And, Or)):
                validate(node.left)
                validate(node.right)
                return
            if isinstance(node, Not):
                validate(node.operand)
                return
            raise TypeError(
                f"unsupported keyword expression node: {type(node).__name__}"
            )

        validate(expression)


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
        match_cache: dict[
            tuple[str, tuple[str, ...], int | None],
            frozenset[int],
        ] = {}

        def atomic_matches(
            node: Term | Phrase | Prefix | Proximity,
        ) -> frozenset[int]:
            tokens = _validated_operand_tokens(connection, node, token_cache)
            if not tokens:
                return frozenset()
            if isinstance(node, Prefix):
                cache_key = ("prefix", tokens, None)
            elif isinstance(node, Proximity):
                cache_key = ("proximity", tokens, node.distance)
            else:
                cache_key = ("exact", tokens, None)
            if cache_key not in match_cache:
                query = _fts5_query_for_operand(node, tokens)
                # FTS5 NEAR 会让重复 phrase 复用同一个 token occurrence。
                # 候选仍由受控 NEAR 查询产生，但重复 token 需要再用同一索引的
                # position evidence 验证实际 occurrence 数量和用户距离。
                if isinstance(node, Proximity) and len(set(tokens)) != len(tokens):
                    match_cache[cache_key] = (
                        _matching_repeated_proximity_document_ids(
                            connection,
                            query,
                            tokens,
                            node.distance,
                        )
                    )
                else:
                    match_cache[cache_key] = _matching_document_ids(
                        connection,
                        query,
                    )
            return match_cache[cache_key]

        def evaluate(node: KeywordExpression) -> frozenset[int]:
            if isinstance(node, (Term, Phrase, Prefix, Proximity)):
                return atomic_matches(node)
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
