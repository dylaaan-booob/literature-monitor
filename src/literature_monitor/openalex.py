"""OpenAlex venue-first discovery for Task 2."""

from __future__ import annotations

import json
import re
import socket
import time
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from literature_monitor.config import JournalConfig
from literature_monitor.models import (
    Author,
    CanonicalMetadata,
    DomainModel,
    ExternalIds,
    MetadataSource,
    NonEmptyStr,
)

OPENALEX_BASE_URL = "https://api.openalex.org"
SOURCE_FIELDS = (
    "id,display_name,issn_l,issn,type,alternate_titles,abbreviated_title"
)
WORK_FIELDS = (
    "id,doi,title,publication_date,abstract_inverted_index,authorships,primary_location"
)
_OPENALEX_ID_PATTERN = re.compile(
    r"^(?:https://openalex\.org/)?([SAW]\d+)$", re.IGNORECASE
)


class IssueSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class DiscoveryIssue:
    severity: IssueSeverity
    stage: str
    journal: str
    message: str
    issn: str | None = None
    record_id: str | None = None


@dataclass(frozen=True)
class ResolvedSource:
    journal: str
    configured_issns: tuple[str, ...]
    resolved_issns: tuple[str, ...]
    unresolved_issns: tuple[str, ...]
    openalex_id: str
    display_name: str
    issn_l: str | None
    issn: tuple[str, ...]


class OpenAlexWorkRecord(DomainModel):
    """Provider record that deliberately has no canonical UUID or workflow state."""

    metadata: CanonicalMetadata
    external_ids: ExternalIds
    authors: tuple[Author, ...]
    source_id: NonEmptyStr
    provenance: MetadataSource


@dataclass(frozen=True)
class DiscoveryResult:
    sources: tuple[ResolvedSource, ...]
    records: tuple[OpenAlexWorkRecord, ...]
    issues: tuple[DiscoveryIssue, ...]

    @property
    def has_errors(self) -> bool:
        return any(issue.severity is IssueSeverity.ERROR for issue in self.issues)


class OpenAlexError(RuntimeError):
    """Base class for OpenAlex request and response failures."""


class OpenAlexNotFoundError(OpenAlexError):
    """The requested singleton external identifier was not found."""


class OpenAlexRequestError(OpenAlexError):
    """A remote request failed or returned an invalid response."""


class OpenAlexRecordError(OpenAlexError):
    """One provider record lacks data required for normalization."""


class OpenAlexClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = OPENALEX_BASE_URL,
        timeout: float = 30,
        opener: Callable[..., Any] = urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key.strip() if api_key and api_key.strip() else None
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = opener
        self._sleep = sleep

    def get_source_by_issn(self, issn: str) -> dict[str, Any]:
        return self._request_json(
            f"/sources/issn:{issn}",
            {"select": SOURCE_FIELDS},
            not_found_message=f"ISSN {issn} is not present in OpenAlex",
        )

    def iter_work_pages(
        self,
        source_id: str,
        from_date: date,
        to_date: date,
    ) -> Iterator[dict[str, Any]]:
        cursor: str | None = "*"
        seen_cursors: set[str] = set()
        short_source_id = _short_openalex_id(source_id, "S")
        while cursor is not None:
            if cursor in seen_cursors:
                raise OpenAlexRequestError("OpenAlex returned a repeated cursor")
            seen_cursors.add(cursor)
            payload = self._request_json(
                "/works",
                {
                    "filter": (
                        f"primary_location.source.id:{short_source_id},"
                        f"from_publication_date:{from_date.isoformat()},"
                        f"to_publication_date:{to_date.isoformat()}"
                    ),
                    "select": WORK_FIELDS,
                    "per_page": "100",
                    "cursor": cursor,
                },
            )
            results = payload.get("results")
            meta = payload.get("meta")
            if not isinstance(results, list) or not isinstance(meta, dict):
                raise OpenAlexRequestError("OpenAlex Works response lacks results or meta")
            next_cursor = meta.get("next_cursor")
            if next_cursor is not None and not isinstance(next_cursor, str):
                raise OpenAlexRequestError("OpenAlex Works response has an invalid next_cursor")
            yield payload
            cursor = next_cursor

    def _request_json(
        self,
        path: str,
        params: dict[str, str],
        *,
        not_found_message: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}?{urlencode(params)}"
        headers = {
            "Accept": "application/json",
            "User-Agent": "literature-monitor/0.1",
        }
        if self.api_key is not None:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(url, headers=headers)

        for attempt in range(3):
            try:
                with self._opener(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read())
                if not isinstance(payload, dict):
                    raise OpenAlexRequestError("OpenAlex returned a non-object JSON response")
                return payload
            except HTTPError as error:
                if error.code == 404 and not_found_message is not None:
                    raise OpenAlexNotFoundError(not_found_message) from error
                if (error.code == 429 or error.code >= 500) and attempt < 2:
                    self._sleep(2**attempt)
                    continue
                raise OpenAlexRequestError(
                    f"OpenAlex request failed with HTTP {error.code}"
                ) from error
            except (TimeoutError, socket.timeout, URLError) as error:
                if attempt < 2:
                    self._sleep(2**attempt)
                    continue
                raise OpenAlexRequestError(f"OpenAlex request failed: {error}") from error
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise OpenAlexRequestError("OpenAlex returned invalid JSON") from error

        raise AssertionError("unreachable")


@dataclass(frozen=True)
class _SourceHit:
    queried_issn: str
    openalex_id: str
    display_name: str
    issn_l: str | None
    issn: tuple[str, ...]
    alternate_titles: tuple[str, ...]
    abbreviated_title: str | None


def _canonical_openalex_id(value: Any, prefix: str) -> str:
    if not isinstance(value, str):
        raise OpenAlexRecordError(f"missing OpenAlex {prefix} identifier")
    match = _OPENALEX_ID_PATTERN.fullmatch(value.strip())
    if match is None or not match.group(1).upper().startswith(prefix):
        raise OpenAlexRecordError(f"invalid OpenAlex {prefix} identifier {value!r}")
    return f"https://openalex.org/{match.group(1).upper()}"


def _short_openalex_id(value: Any, prefix: str) -> str:
    return _canonical_openalex_id(value, prefix).rsplit("/", maxsplit=1)[1]


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OpenAlexRecordError(f"missing {field}")
    return value.strip()


def _parse_string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise OpenAlexRecordError(f"invalid {field}")
    return tuple(item.strip().upper() for item in value)


def _parse_source(payload: dict[str, Any], queried_issn: str) -> _SourceHit:
    if payload.get("type") != "journal":
        raise OpenAlexRecordError(
            f"ISSN {queried_issn} resolved to non-journal source type {payload.get('type')!r}"
        )
    source_issns = _parse_string_tuple(payload.get("issn"), "source ISSNs")
    if queried_issn not in source_issns:
        raise OpenAlexRecordError(
            f"ISSN {queried_issn} is absent from the returned source ISSNs"
        )
    alternate_titles_raw = payload.get("alternate_titles", [])
    if not isinstance(alternate_titles_raw, list) or any(
        not isinstance(title, str) for title in alternate_titles_raw
    ):
        raise OpenAlexRecordError("invalid source alternate_titles")
    abbreviated_title_raw = payload.get("abbreviated_title")
    if abbreviated_title_raw is not None and not isinstance(abbreviated_title_raw, str):
        raise OpenAlexRecordError("invalid source abbreviated_title")
    issn_l_raw = payload.get("issn_l")
    if issn_l_raw is not None and not isinstance(issn_l_raw, str):
        raise OpenAlexRecordError("invalid source issn_l")
    return _SourceHit(
        queried_issn=queried_issn,
        openalex_id=_canonical_openalex_id(payload.get("id"), "S"),
        display_name=_nonempty_string(payload.get("display_name"), "source display_name"),
        issn_l=issn_l_raw.strip().upper() if issn_l_raw else None,
        issn=source_issns,
        alternate_titles=tuple(
            title.strip() for title in alternate_titles_raw if title.strip()
        ),
        abbreviated_title=(abbreviated_title_raw.strip() if abbreviated_title_raw else None),
    )


def _normalized_journal_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("&", " and ")
    words = re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)
    if words and words[0] == "the":
        words = words[1:]
    return "".join(words)


def _journal_name_matches(journal_name: str, source: _SourceHit) -> bool:
    expected = _normalized_journal_name(journal_name)
    candidates = (source.display_name, *source.alternate_titles)
    if source.abbreviated_title is not None:
        candidates += (source.abbreviated_title,)
    return expected in {_normalized_journal_name(candidate) for candidate in candidates}


def resolve_journal_source(
    client: OpenAlexClient,
    journal: JournalConfig,
) -> tuple[ResolvedSource | None, tuple[DiscoveryIssue, ...]]:
    hits: list[_SourceHit] = []
    unresolved: list[str] = []
    issues: list[DiscoveryIssue] = []
    failed = False

    for issn in journal.issn:
        try:
            payload = client.get_source_by_issn(issn)
            hits.append(_parse_source(payload, issn))
        except OpenAlexNotFoundError:
            unresolved.append(issn)
        except OpenAlexError as error:
            failed = True
            issues.append(
                DiscoveryIssue(
                    severity=IssueSeverity.ERROR,
                    stage="source_resolution",
                    journal=journal.name,
                    issn=issn,
                    message=str(error),
                )
            )

    if failed:
        for issn in unresolved:
            issues.append(
                DiscoveryIssue(
                    severity=IssueSeverity.WARNING,
                    stage="source_resolution",
                    journal=journal.name,
                    issn=issn,
                    message=f"ISSN {issn} is unresolved in OpenAlex",
                )
            )
        return None, tuple(issues)
    if not hits:
        issues.append(
            DiscoveryIssue(
                severity=IssueSeverity.ERROR,
                stage="source_resolution",
                journal=journal.name,
                message=f"no configured ISSN resolved in OpenAlex: {', '.join(unresolved)}",
            )
        )
        return None, tuple(issues)

    source_ids = {hit.openalex_id for hit in hits}
    if len(source_ids) != 1:
        mappings = ", ".join(f"{hit.queried_issn} -> {hit.openalex_id}" for hit in hits)
        issues.append(
            DiscoveryIssue(
                severity=IssueSeverity.ERROR,
                stage="source_resolution",
                journal=journal.name,
                message=(
                    "configured ISSNs resolve to conflicting OpenAlex Sources: "
                    f"{mappings}"
                ),
            )
        )
        return None, tuple(issues)

    source = hits[0]
    if not _journal_name_matches(journal.name, source):
        issues.append(
            DiscoveryIssue(
                severity=IssueSeverity.ERROR,
                stage="source_resolution",
                journal=journal.name,
                message=(
                    f"configured journal name does not match OpenAlex source "
                    f"{source.display_name!r} ({source.openalex_id})"
                ),
            )
        )
        return None, tuple(issues)

    for issn in unresolved:
        issues.append(
            DiscoveryIssue(
                severity=IssueSeverity.WARNING,
                stage="source_resolution",
                journal=journal.name,
                issn=issn,
                message=f"ISSN {issn} is unresolved; using the consistent resolved Source",
            )
        )

    return (
        ResolvedSource(
            journal=journal.name,
            configured_issns=journal.issn,
            resolved_issns=tuple(hit.queried_issn for hit in hits),
            unresolved_issns=tuple(unresolved),
            openalex_id=source.openalex_id,
            display_name=source.display_name,
            issn_l=source.issn_l,
            issn=source.issn,
        ),
        tuple(issues),
    )


def _normalize_doi(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid DOI")
    doi = value.strip()
    prefix = "https://doi.org/"
    if doi.casefold().startswith(prefix):
        doi = doi[len(prefix) :]
    return doi.casefold()


def _parse_publication_date(value: Any) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid publication_date")
    return date.fromisoformat(value)


def _reconstruct_abstract(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("invalid abstract_inverted_index")
    positioned_words: list[tuple[int, str]] = []
    positions: set[int] = set()
    for word, indexes in value.items():
        if not isinstance(word, str) or not isinstance(indexes, list):
            raise ValueError("invalid abstract_inverted_index")
        for index in indexes:
            if not isinstance(index, int) or index < 0 or index in positions:
                raise ValueError("invalid abstract_inverted_index positions")
            positions.add(index)
            positioned_words.append((index, word))
    if not positioned_words:
        return None
    return " ".join(word for _, word in sorted(positioned_words))


def _optional_openalex_id(value: Any, prefix: str) -> str | None:
    if value is None:
        return None
    return _canonical_openalex_id(value, prefix)


def _normalize_work(
    payload: Any,
    source: ResolvedSource,
    retrieved_at: datetime,
) -> tuple[OpenAlexWorkRecord, tuple[str, ...]]:
    if not isinstance(payload, dict):
        raise OpenAlexRecordError("work response entry is not an object")
    work_id = _canonical_openalex_id(payload.get("id"), "W")
    title = _nonempty_string(payload.get("title"), "work title")

    primary_location = payload.get("primary_location")
    if not isinstance(primary_location, dict):
        raise OpenAlexRecordError("work lacks primary_location")
    source_payload = primary_location.get("source")
    if not isinstance(source_payload, dict):
        raise OpenAlexRecordError("work lacks primary_location.source")
    work_source_id = _canonical_openalex_id(source_payload.get("id"), "S")
    if work_source_id != source.openalex_id:
        raise OpenAlexRecordError(
            f"work venue {work_source_id} does not match resolved Source {source.openalex_id}"
        )

    warnings: list[str] = []
    try:
        publication_date = _parse_publication_date(payload.get("publication_date"))
    except (TypeError, ValueError):
        publication_date = None
        warnings.append("invalid publication_date was treated as missing")
    try:
        doi = _normalize_doi(payload.get("doi"))
    except ValueError:
        doi = None
        warnings.append("invalid DOI was treated as missing")
    try:
        abstract = _reconstruct_abstract(payload.get("abstract_inverted_index"))
    except ValueError:
        abstract = None
        warnings.append("invalid abstract_inverted_index was treated as missing")

    authorships = payload.get("authorships")
    if not isinstance(authorships, list):
        raise OpenAlexRecordError("work lacks an authorships list")
    authors: list[Author] = []
    for authorship in authorships:
        if not isinstance(authorship, dict):
            warnings.append("ignored malformed authorship")
            continue
        author_payload = authorship.get("author")
        author_payload = author_payload if isinstance(author_payload, dict) else {}
        display_name = author_payload.get("display_name")
        raw_name = authorship.get("raw_author_name")
        name = display_name if isinstance(display_name, str) and display_name.strip() else raw_name
        if not isinstance(name, str) or not name.strip():
            warnings.append("ignored authorship without a usable name")
            continue
        try:
            author_id = _optional_openalex_id(author_payload.get("id"), "A")
        except OpenAlexRecordError:
            author_id = None
            warnings.append(f"ignored invalid author ID for {name.strip()!r}")
        orcid_raw = author_payload.get("orcid")
        orcid = orcid_raw.strip() if isinstance(orcid_raw, str) and orcid_raw.strip() else None
        authors.append(Author(name=name, openalex_id=author_id, orcid=orcid))
    if not authors:
        raise OpenAlexRecordError("work has no usable authors")

    return (
        OpenAlexWorkRecord(
            metadata=CanonicalMetadata(
                title=title,
                journal=source.display_name,
                publication_date=publication_date,
                abstract=abstract,
                author_keywords=(),
            ),
            external_ids=ExternalIds(openalex=work_id, doi=doi),
            authors=tuple(authors),
            source_id=source.openalex_id,
            provenance=MetadataSource(
                provider="openalex",
                record_id=work_id,
                retrieved_at=retrieved_at,
            ),
        ),
        tuple(warnings),
    )


def discover_journals(
    client: OpenAlexClient,
    journals: Sequence[JournalConfig],
    from_date: date,
    to_date: date,
    *,
    retrieved_at: datetime | None = None,
) -> DiscoveryResult:
    if from_date > to_date:
        raise ValueError("from_date must not be after to_date")
    timestamp = retrieved_at or datetime.now(timezone.utc)
    if timestamp.utcoffset() is None:
        raise ValueError("retrieved_at must include a timezone")
    timestamp = timestamp.astimezone(timezone.utc)

    sources: list[ResolvedSource] = []
    records: list[tuple[int, OpenAlexWorkRecord]] = []
    issues: list[DiscoveryIssue] = []

    for journal_index, journal in enumerate(journals):
        source, resolution_issues = resolve_journal_source(client, journal)
        issues.extend(resolution_issues)
        if source is None:
            continue
        sources.append(source)
        try:
            for page in client.iter_work_pages(source.openalex_id, from_date, to_date):
                for payload in page["results"]:
                    record_id = payload.get("id") if isinstance(payload, dict) else None
                    try:
                        record, warnings = _normalize_work(payload, source, timestamp)
                    except OpenAlexError as error:
                        issues.append(
                            DiscoveryIssue(
                                severity=IssueSeverity.ERROR,
                                stage="record_normalization",
                                journal=journal.name,
                                record_id=record_id if isinstance(record_id, str) else None,
                                message=str(error),
                            )
                        )
                        continue
                    records.append((journal_index, record))
                    for warning in warnings:
                        issues.append(
                            DiscoveryIssue(
                                severity=IssueSeverity.WARNING,
                                stage="record_normalization",
                                journal=journal.name,
                                record_id=record.external_ids.openalex,
                                message=warning,
                            )
                        )
        except OpenAlexError as error:
            issues.append(
                DiscoveryIssue(
                    severity=IssueSeverity.ERROR,
                    stage="work_retrieval",
                    journal=journal.name,
                    message=str(error),
                )
            )

    records.sort(
        key=lambda item: (
            item[0],
            item[1].metadata.publication_date or date.max,
            item[1].external_ids.openalex or "",
        )
    )
    return DiscoveryResult(
        sources=tuple(sources),
        records=tuple(record for _, record in records),
        issues=tuple(issues),
    )
