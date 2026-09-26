"""Crossref journal discovery and DOI supplementation."""

from __future__ import annotations

import json
import math
import re
import socket
import time
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from enum import Enum
from html.parser import HTMLParser
from typing import Annotated, Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from pydantic import Field, ValidationError, model_validator

from literature_monitor.config import JournalConfig
from literature_monitor.coverage import (
    CoverageComponent,
    CoverageStatus,
    CoverageUnit,
)
from literature_monitor.identifiers import normalize_doi
from literature_monitor.models import (
    Author,
    DomainModel,
    EvidenceDate,
    EvidenceDateKind,
    EvidenceRelation,
    ExternalIds,
    MetadataSource,
    NonEmptyStr,
    ProviderRecordRef,
    ProviderWorkEvidence,
)
from literature_monitor.openalex import OpenAlexWorkRecord
from literature_monitor.progress import (
    ActivityKind,
    ActivityUpdate,
    ProgressCallback,
    ProgressEvent,
)

CROSSREF_BASE_URL = "https://api.crossref.org"
CROSSREF_PAGE_SIZE = 1000
_ISSN_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{3}[0-9X]$")
_RATE_LIMIT_INTERVAL_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)s\s*$", re.IGNORECASE)
_ORCID_PATTERN = re.compile(
    r"^(?:https?://orcid\.org/)?\d{4}-\d{4}-\d{4}-\d{3}[\dX]/?$",
    re.IGNORECASE,
)


class CrossrefDateKind(str, Enum):
    PUBLISHED = "published"
    PUBLISHED_ONLINE = "published-online"
    PUBLISHED_PRINT = "published-print"
    ISSUED = "issued"


_CROSSREF_DATE_KINDS = {kind.value: kind for kind in CrossrefDateKind}


StrictYear = Annotated[int, Field(strict=True, ge=1, le=9999)]
StrictMonth = Annotated[int, Field(strict=True, ge=1, le=12)]
StrictDay = Annotated[int, Field(strict=True, ge=1, le=31)]


class CrossrefPartialDate(DomainModel):
    kind: CrossrefDateKind
    year: StrictYear
    month: StrictMonth | None = None
    day: StrictDay | None = None

    @model_validator(mode="after")
    def validate_partial_date(self) -> CrossrefPartialDate:
        if self.day is not None and self.month is None:
            raise ValueError("day requires month")
        if self.month is not None:
            date(self.year, self.month, self.day or 1)
        return self


class CrossrefRelation(DomainModel):
    relation_type: NonEmptyStr
    id_type: NonEmptyStr
    identifier: NonEmptyStr
    asserted_by: NonEmptyStr | None = None


class CrossrefWorkRecord(DomainModel):
    doi: NonEmptyStr
    title: NonEmptyStr | None = None
    journal: NonEmptyStr | None = None
    abstract: NonEmptyStr | None = None
    authors: tuple[Author, ...] = ()
    issns: tuple[NonEmptyStr, ...] = ()
    dates: tuple[CrossrefPartialDate, ...] = ()
    relations: tuple[CrossrefRelation, ...] = ()
    work_type: NonEmptyStr | None = None
    provenance: MetadataSource

    def to_evidence(
        self,
        *,
        supplements: tuple[ProviderRecordRef, ...] = (),
    ) -> ProviderWorkEvidence:
        return ProviderWorkEvidence(
            provenance=self.provenance,
            title=self.title,
            journal=self.journal,
            abstract=self.abstract,
            authors=self.authors,
            external_ids=ExternalIds(
                doi=self.doi,
                crossref=self.provenance.record_id,
            ),
            dates=tuple(
                EvidenceDate(
                    kind=EvidenceDateKind(item.kind.value),
                    year=item.year,
                    month=item.month,
                    day=item.day,
                )
                for item in self.dates
            ),
            relations=tuple(
                EvidenceRelation(
                    relation_type=item.relation_type,
                    id_type=item.id_type,
                    identifier=item.identifier,
                    asserted_by=item.asserted_by,
                )
                for item in self.relations
            ),
            supplements=supplements,
        )


class EnrichedWorkRecord(DomainModel):
    openalex: OpenAlexWorkRecord
    crossref: CrossrefWorkRecord | None = None

    def to_evidence(self) -> tuple[ProviderWorkEvidence, ...]:
        openalex = self.openalex.to_evidence()
        if self.crossref is None:
            return (openalex,)
        anchor = ProviderRecordRef(
            provider=openalex.provenance.provider,
            record_id=openalex.provenance.record_id,
        )
        return (openalex, self.crossref.to_evidence(supplements=(anchor,)))


class EnrichmentIssueSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class EnrichmentIssue:
    severity: EnrichmentIssueSeverity
    stage: str
    message: str
    record_id: str | None = None
    doi: str | None = None


@dataclass(frozen=True)
class EnrichmentResult:
    records: tuple[EnrichedWorkRecord, ...]
    issues: tuple[EnrichmentIssue, ...]

    @property
    def has_errors(self) -> bool:
        return any(
            issue.severity is EnrichmentIssueSeverity.ERROR for issue in self.issues
        )


@dataclass(frozen=True)
class CrossrefDiscoveryIssue:
    severity: EnrichmentIssueSeverity
    stage: str
    journal: str
    issn: str
    message: str
    record_id: str | None = None
    doi: str | None = None


@dataclass(frozen=True)
class CrossrefDiscoveryUnitResult:
    """Normalized output and diagnostics for a configured journal/ISSN query."""

    journal: JournalConfig
    issn: str
    coverage: CoverageUnit
    records: tuple[CrossrefWorkRecord, ...]
    issues: tuple[CrossrefDiscoveryIssue, ...]


@dataclass(frozen=True)
class CrossrefDiscoveryResult:
    records: tuple[CrossrefWorkRecord, ...]
    issues: tuple[CrossrefDiscoveryIssue, ...]
    coverage: tuple[CoverageUnit, ...] = ()
    units: tuple[CrossrefDiscoveryUnitResult, ...] = ()

    @property
    def has_errors(self) -> bool:
        return any(
            issue.severity is EnrichmentIssueSeverity.ERROR
            for issue in self.issues
        )


class CrossrefError(RuntimeError):
    """Base class for Crossref provider failures."""


class CrossrefNotFoundError(CrossrefError):
    """Crossref has no record for the requested DOI."""


class CrossrefRequestError(CrossrefError):
    """A Crossref request failed or returned invalid transport data."""


class CrossrefRecordError(CrossrefError):
    """A Crossref record cannot be normalized safely."""


def _report_activity(
    callback: ProgressCallback | None,
    activity: ActivityUpdate | None,
) -> None:
    if callback is not None and activity is not None:
        callback(ProgressEvent(activity=activity))


def _progress_total(value: Any) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


class CrossrefClient:
    def __init__(
        self,
        *,
        mailto: str | None = None,
        base_url: str = CROSSREF_BASE_URL,
        timeout: float = 30,
        opener: Callable[..., Any] = urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.mailto = mailto.strip() if mailto and mailto.strip() else None
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = opener
        self._sleep = sleep
        self._rate_limit_delay: float | None = None

    def _update_rate_limit(self, headers: Any) -> None:
        if headers is None or not hasattr(headers, "get"):
            return
        raw_limit = headers.get("X-Rate-Limit-Limit")
        raw_interval = headers.get("X-Rate-Limit-Interval")
        if not isinstance(raw_limit, str) or not isinstance(raw_interval, str):
            return
        try:
            limit = int(raw_limit.strip())
        except ValueError:
            return
        match = _RATE_LIMIT_INTERVAL_PATTERN.fullmatch(raw_interval)
        if limit <= 0 or match is None:
            return
        interval = float(match.group(1))
        if not math.isfinite(interval) or interval <= 0:
            return
        delay = interval / limit
        if not math.isfinite(delay) or delay <= 0:
            return
        self._rate_limit_delay = delay

    def _wait_for_rate_limit(
        self,
        *,
        progress_callback: ProgressCallback | None,
        activity: ActivityUpdate | None,
    ) -> None:
        delay = self._rate_limit_delay
        if delay is None:
            return
        if activity is not None:
            _report_activity(
                progress_callback,
                replace(
                    activity,
                    kind=ActivityKind.WAITING,
                    label="Waiting for Crossref rate limit",
                    detail=(
                        f"{activity.detail} · provider pacing {delay:g}s"
                        if activity.detail
                        else f"provider pacing {delay:g}s"
                    ),
                ),
            )
        self._sleep(delay)

    def _retry_delay(self, attempt: int) -> float:
        fallback = 2**attempt
        if self._rate_limit_delay is None:
            return fallback
        return max(fallback, self._rate_limit_delay)

    def get_work_by_doi(
        self,
        doi: str,
        *,
        progress_callback: ProgressCallback | None = None,
        activity: ActivityUpdate | None = None,
    ) -> dict[str, Any]:
        try:
            normalized_doi = normalize_doi(doi)
        except ValueError as error:
            raise CrossrefRecordError("invalid requested DOI") from error
        if normalized_doi is None:
            raise CrossrefRecordError("missing requested DOI")

        if activity is None and progress_callback is not None:
            activity = ActivityUpdate(
                kind=ActivityKind.WORKING,
                source="crossref",
                operation="doi_lookup",
                label="Looking up Crossref DOI",
                detail=f"DOI {normalized_doi}",
                unit="doi",
            )
        return self._request_json(
            f"/v1/works/{quote(normalized_doi, safe='')}",
            {},
            not_found_message=(
                f"DOI {normalized_doi} is not present in Crossref"
            ),
            progress_callback=progress_callback,
            activity=activity,
        )

    def iter_journal_work_pages(
        self,
        issn: str,
        from_date: date,
        to_date: date,
        *,
        rows: int = CROSSREF_PAGE_SIZE,
        progress_callback: ProgressCallback | None = None,
        activity: ActivityUpdate | None = None,
    ) -> Iterator[dict[str, Any]]:
        if from_date > to_date:
            raise ValueError("from_date must not be after to_date")
        if rows < 1:
            raise ValueError("rows must be positive")
        if activity is None and progress_callback is not None:
            activity = ActivityUpdate(
                kind=ActivityKind.WORKING,
                source="crossref",
                operation=f"journal_discovery:{issn}",
                label="Discovering Crossref works",
                detail=f"ISSN {issn}",
                current=0,
                unit="work",
            )
        cursor = "*"
        seen_cursors: set[str] = set()
        current = (
            activity.current
            if activity is not None and activity.current is not None
            else 0
        )
        total = activity.total if activity is not None else None
        activity_detail = activity.detail if activity is not None else None
        page_number = 0
        _report_activity(progress_callback, activity)
        while True:
            if cursor in seen_cursors:
                raise CrossrefRequestError("Crossref returned a repeated cursor")
            seen_cursors.add(cursor)
            page_number += 1
            request_activity = (
                replace(
                    activity,
                    current=current,
                    total=total,
                    detail=(
                        f"{activity_detail} · page {page_number}"
                        if activity_detail
                        else f"page {page_number}"
                    ),
                )
                if activity is not None
                else None
            )
            payload = self._request_json(
                f"/v1/journals/{quote(issn, safe='')}/works",
                {
                    "filter": (
                        f"from-pub-date:{from_date.isoformat()},"
                        f"until-pub-date:{to_date.isoformat()}"
                    ),
                    "rows": str(rows),
                    "cursor": cursor,
                },
                not_found_message=f"ISSN {issn} is not present in Crossref",
                progress_callback=progress_callback,
                activity=request_activity,
            )
            if (
                payload.get("status") != "ok"
                or payload.get("message-type") != "work-list"
            ):
                raise CrossrefRequestError(
                    "Crossref journal response has an invalid envelope"
                )
            message = payload.get("message")
            if not isinstance(message, dict):
                raise CrossrefRequestError(
                    "Crossref journal response lacks a list message"
                )
            items = message.get("items")
            if not isinstance(items, list):
                raise CrossrefRequestError(
                    "Crossref journal response lacks a valid items list"
                )
            page_current = current + len(items)
            response_total = _progress_total(message.get("total-results"))
            if response_total is not None and response_total >= page_current:
                total = response_total
            elif total is not None and total < page_current:
                total = None
            current = page_current
            if activity is not None:
                activity = replace(
                    activity,
                    kind=ActivityKind.WORKING,
                    label="Retrieved Crossref page",
                    detail=(
                        f"{activity_detail} · page {page_number}"
                        if activity_detail
                        else f"page {page_number}"
                    ),
                    current=current,
                    total=total,
                )
                _report_activity(progress_callback, activity)
            yield payload
            if len(items) < rows:
                if activity is not None:
                    _report_activity(
                        progress_callback,
                        replace(
                            activity,
                            label="Completed Crossref ISSN discovery",
                            current=current,
                            total=total,
                        ),
                    )
                return
            next_cursor = message.get("next-cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                raise CrossrefRequestError(
                    "Crossref journal response lacks a valid next cursor"
                )
            cursor = next_cursor

    def _request_json(
        self,
        path: str,
        params: dict[str, str],
        *,
        not_found_message: str | None = None,
        progress_callback: ProgressCallback | None = None,
        activity: ActivityUpdate | None = None,
    ) -> dict[str, Any]:
        query = dict(params)
        if self.mailto is not None:
            query["mailto"] = self.mailto
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "literature-monitor/0.4.1",
            },
        )

        self._wait_for_rate_limit(
            progress_callback=progress_callback,
            activity=activity,
        )
        for attempt in range(3):
            attempt_number = attempt + 1
            if activity is not None:
                _report_activity(
                    progress_callback,
                    replace(
                        activity,
                        kind=ActivityKind.WORKING,
                        label="Requesting Crossref",
                        detail=(
                            f"{activity.detail} · attempt {attempt_number}/3"
                            if activity.detail
                            else f"attempt {attempt_number}/3"
                        ),
                    ),
                )
            try:
                with self._opener(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read())
                    response_headers = getattr(response, "headers", None)
                if not isinstance(payload, dict):
                    raise CrossrefRequestError(
                        "Crossref returned a non-object JSON response"
                    )
                self._update_rate_limit(response_headers)
                if activity is not None:
                    _report_activity(
                        progress_callback,
                        replace(
                            activity,
                            kind=ActivityKind.WORKING,
                            label="Received Crossref response",
                            detail=(
                                f"{activity.detail} · attempt {attempt_number}/3"
                                if activity.detail
                                else f"attempt {attempt_number}/3"
                            ),
                        ),
                    )
                return payload
            except HTTPError as error:
                if error.code == 404 and not_found_message is not None:
                    raise CrossrefNotFoundError(not_found_message) from error
                if (error.code == 429 or error.code >= 500) and attempt < 2:
                    delay = self._retry_delay(attempt)
                    if activity is not None:
                        # retry 事件先于 backoff，且沿用所属 ISSN/DOI activity identity。
                        _report_activity(
                            progress_callback,
                            replace(
                                activity,
                                kind=ActivityKind.RETRYING,
                                label="Retrying Crossref request",
                                detail=(
                                    f"{activity.detail} · HTTP {error.code} · "
                                    f"backoff {delay}s"
                                    if activity.detail
                                    else f"HTTP {error.code} · backoff {delay}s"
                                ),
                            ),
                        )
                    self._sleep(delay)
                    continue
                raise CrossrefRequestError(
                    f"Crossref request failed with HTTP {error.code}"
                ) from error
            except (TimeoutError, socket.timeout, URLError, OSError) as error:
                if attempt < 2:
                    delay = self._retry_delay(attempt)
                    if activity is not None:
                        _report_activity(
                            progress_callback,
                            replace(
                                activity,
                                kind=ActivityKind.RETRYING,
                                label="Retrying Crossref request",
                                detail=(
                                    f"{activity.detail} · transport failure · "
                                    f"backoff {delay}s"
                                    if activity.detail
                                    else f"transport failure · backoff {delay}s"
                                ),
                            ),
                        )
                    self._sleep(delay)
                    continue
                raise CrossrefRequestError(
                    f"Crossref request failed: {error}"
                ) from error
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise CrossrefRequestError("Crossref returned invalid JSON") from error

        raise AssertionError("unreachable")


class _AbstractTextParser(HTMLParser):
    _BLOCK_TAGS = {
        "abstract",
        "br",
        "list-item",
        "p",
        "sec",
        "title",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.rsplit(":", maxsplit=1)[-1]

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if self._local_name(tag) in self._BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self._local_name(tag) in self._BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return " ".join("".join(self.parts).split())


def _first_optional_string(
    value: Any,
    field: str,
    warnings: list[str],
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, list):
        warnings.append(f"invalid {field} was treated as missing")
        return None
    for item in value:
        if isinstance(item, str) and item.strip():
            return item.strip()
    warnings.append(f"invalid {field} was treated as missing")
    return None


def _normalize_abstract(value: Any, warnings: list[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        warnings.append("invalid abstract was treated as missing")
        return None
    try:
        parser = _AbstractTextParser()
        parser.feed(value)
        parser.close()
        abstract = parser.text()
    except (ValueError, TypeError):
        warnings.append("invalid abstract was treated as missing")
        return None
    if not abstract:
        warnings.append("invalid abstract was treated as missing")
        return None
    return abstract


def _normalize_dates(message: dict[str, Any], warnings: list[str]) -> list[CrossrefPartialDate]:
    dates: list[CrossrefPartialDate] = []
    for field, value in message.items():
        kind = _CROSSREF_DATE_KINDS.get(field)
        if kind is None:
            continue
        if not isinstance(value, dict) or not isinstance(value.get("date-parts"), list):
            warnings.append(f"invalid {kind.value} date was ignored")
            continue
        for index, parts in enumerate(value["date-parts"]):
            if (
                not isinstance(parts, list)
                or not 1 <= len(parts) <= 3
                or any(type(part) is not int for part in parts)
            ):
                warnings.append(
                    f"invalid {kind.value} date-parts entry {index} was ignored"
                )
                continue
            year = parts[0]
            month = parts[1] if len(parts) >= 2 else None
            day = parts[2] if len(parts) == 3 else None
            try:
                if month is None:
                    date(year, 1, 1)
                else:
                    date(year, month, day or 1)
            except ValueError:
                warnings.append(
                    f"invalid {kind.value} date-parts entry {index} was ignored"
                )
                continue
            dates.append(
                CrossrefPartialDate(
                    kind=kind,
                    year=year,
                    month=month,
                    day=day,
                )
            )
    return dates


def _normalize_relations(
    value: Any,
    warnings: list[str],
) -> list[CrossrefRelation]:
    if value is None:
        return []
    if not isinstance(value, dict):
        warnings.append("invalid relation metadata was ignored")
        return []

    relations: list[CrossrefRelation] = []
    for relation_type, entries in value.items():
        if not isinstance(relation_type, str) or not relation_type.strip():
            warnings.append("relation with invalid type was ignored")
            continue
        if not isinstance(entries, list):
            warnings.append(f"invalid {relation_type} relation list was ignored")
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                warnings.append(
                    f"invalid {relation_type} relation entry {index} was ignored"
                )
                continue
            id_type = entry.get("id-type")
            identifier = entry.get("id")
            asserted_by = entry.get("asserted-by")
            if (
                not isinstance(id_type, str)
                or not id_type.strip()
                or not isinstance(identifier, str)
                or not identifier.strip()
                or (
                    asserted_by is not None
                    and (
                        not isinstance(asserted_by, str)
                        or not asserted_by.strip()
                    )
                )
            ):
                warnings.append(
                    f"invalid {relation_type} relation entry {index} was ignored"
                )
                continue
            normalized_identifier = identifier.strip()
            if id_type.strip().casefold() == "doi":
                try:
                    normalized_identifier = normalize_doi(identifier) or ""
                except ValueError:
                    normalized_identifier = ""
                if not normalized_identifier:
                    warnings.append(
                        f"invalid {relation_type} relation entry {index} was ignored"
                    )
                    continue
            relations.append(
                CrossrefRelation(
                    relation_type=relation_type,
                    id_type=id_type,
                    identifier=normalized_identifier,
                    asserted_by=asserted_by,
                )
            )
    return relations


def _normalize_orcid(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if not _ORCID_PATTERN.fullmatch(candidate):
        return None
    identifier = re.sub(
        r"^https?://orcid\.org/",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).rstrip("/").upper()
    digits = identifier.replace("-", "")
    total = 0
    for character in digits[:15]:
        total = (total + int(character)) * 2
    result = (12 - total % 11) % 11
    check = "X" if result == 10 else str(result)
    if digits[-1] != check:
        return None
    return f"https://orcid.org/{identifier}"


def _normalize_authors(value: Any, warnings: list[str]) -> list[Author]:
    if value is None:
        return []
    if not isinstance(value, list):
        warnings.append("invalid author list was treated as missing")
        return []
    authors: list[Author] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            warnings.append(f"invalid author entry {index} was ignored")
            continue
        name_parts = [
            part.strip()
            for field in ("given", "family")
            if isinstance((part := entry.get(field)), str) and part.strip()
        ]
        if name_parts:
            name = " ".join(name_parts)
        else:
            raw_name = entry.get("name")
            name = (
                raw_name.strip()
                if isinstance(raw_name, str) and raw_name.strip()
                else None
            )
        if name is None:
            warnings.append(f"author entry {index} without a usable name was ignored")
            continue
        raw_orcid = entry.get("ORCID")
        orcid = _normalize_orcid(raw_orcid)
        if raw_orcid is not None and orcid is None:
            warnings.append(f"invalid ORCID for author entry {index} was ignored")
        authors.append(Author(name=name, orcid=orcid))
    return authors


def _valid_issn(value: str) -> bool:
    if not _ISSN_PATTERN.fullmatch(value):
        return False
    digits = value.replace("-", "")
    values = [10 if character == "X" else int(character) for character in digits]
    return (
        sum(
            number * weight
            for number, weight in zip(values, range(8, 0, -1), strict=True)
        )
        % 11
        == 0
    )


def _normalize_issns(message: dict[str, Any], warnings: list[str]) -> list[str]:
    candidates: list[Any] = []
    raw_issns = message.get("ISSN")
    if raw_issns is not None:
        if isinstance(raw_issns, list):
            candidates.extend(raw_issns)
        else:
            warnings.append("invalid ISSN list was treated as missing")
    raw_types = message.get("issn-type")
    if raw_types is not None:
        if isinstance(raw_types, list):
            for index, item in enumerate(raw_types):
                if isinstance(item, dict):
                    candidates.append(item.get("value"))
                else:
                    warnings.append(f"invalid issn-type entry {index} was ignored")
        else:
            warnings.append("invalid issn-type list was treated as missing")

    normalized: set[str] = set()
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, str):
            warnings.append(f"invalid ISSN entry {index} was ignored")
            continue
        issn = candidate.strip().upper()
        if not _valid_issn(issn):
            warnings.append(f"invalid ISSN entry {index} was ignored")
            continue
        normalized.add(issn)
    return sorted(normalized)


def validate_normalized_crossref_record(record: CrossrefWorkRecord) -> None:
    """Validate durable normalized values without reconstructing raw warnings."""

    if normalize_doi(record.doi) != record.doi:
        raise ValueError("Crossref DOI must already be normalized")
    if record.provenance.provider != "crossref" or record.provenance.record_id != record.doi:
        raise ValueError("Crossref record provenance must match its DOI")
    if (any(not _valid_issn(issn) for issn in record.issns)
            or record.issns != tuple(sorted(set(record.issns)))):
        raise ValueError("Crossref ISSNs must be valid, normalized, sorted and unique")
    for author in record.authors:
        if author.orcid is not None and _normalize_orcid(author.orcid) != author.orcid:
            raise ValueError("Crossref author ORCID must already be canonical")


def _normalize_crossref_message(
    message: object,
    retrieved_at: datetime,
) -> tuple[CrossrefWorkRecord, tuple[str, ...]]:
    if not isinstance(message, dict):
        raise CrossrefRecordError("Crossref response lacks a work message")
    try:
        doi = normalize_doi(message.get("DOI"))
    except ValueError as error:
        raise CrossrefRecordError("Crossref response has an invalid DOI") from error
    if doi is None:
        raise CrossrefRecordError("Crossref response is missing its DOI")

    normalized_retrieved_at = (
        retrieved_at.astimezone(timezone.utc)
        if retrieved_at.utcoffset() is not None
        else retrieved_at
    )
    warnings: list[str] = []
    title = _first_optional_string(message.get("title"), "title", warnings)
    journal = _first_optional_string(
        message.get("container-title"), "container-title", warnings
    )
    abstract = _normalize_abstract(message.get("abstract"), warnings)
    authors = _normalize_authors(message.get("author"), warnings)
    issns = _normalize_issns(message, warnings)
    dates = _normalize_dates(message, warnings)
    relations = _normalize_relations(message.get("relation"), warnings)

    work_type_raw = message.get("type")
    if work_type_raw is None:
        work_type = None
    elif isinstance(work_type_raw, str) and work_type_raw.strip():
        work_type = work_type_raw.strip()
    else:
        warnings.append("invalid type was treated as missing")
        work_type = None

    return (
        CrossrefWorkRecord(
            doi=doi,
            title=title,
            journal=journal,
            abstract=abstract,
            authors=tuple(authors),
            issns=tuple(issns),
            dates=tuple(dates),
            relations=tuple(relations),
            work_type=work_type,
            provenance=MetadataSource(
                provider="crossref",
                record_id=doi,
                retrieved_at=normalized_retrieved_at,
            ),
        ),
        tuple(warnings),
    )


def _normalize_crossref_work(
    payload: object,
    requested_doi: str,
    retrieved_at: datetime,
) -> tuple[CrossrefWorkRecord, tuple[str, ...]]:
    if not isinstance(payload, dict):
        raise CrossrefRecordError("Crossref response is not an object")
    if payload.get("status") != "ok" or payload.get("message-type") != "work":
        raise CrossrefRecordError("Crossref response has an invalid envelope")
    message = payload.get("message")
    record, warnings = _normalize_crossref_message(message, retrieved_at)
    try:
        normalized_requested_doi = normalize_doi(requested_doi)
    except ValueError as error:
        raise CrossrefRecordError("requested DOI is invalid") from error
    if normalized_requested_doi is None:
        raise CrossrefRecordError("requested DOI is missing")
    if record.doi != normalized_requested_doi:
        raise CrossrefRecordError(
            "Crossref response DOI does not match the requested DOI"
        )
    return record, warnings


def normalize_crossref_work(
    payload: object,
    requested_doi: str,
    retrieved_at: datetime,
) -> tuple[CrossrefWorkRecord, tuple[str, ...]]:
    """Normalize one response and hide Pydantic failures behind the provider boundary."""

    try:
        return _normalize_crossref_work(payload, requested_doi, retrieved_at)
    except ValidationError as error:
        raise CrossrefRecordError(
            f"Crossref provider model validation failed: {error}"
        ) from error


def normalize_crossref_discovered_work(
    message: object,
    retrieved_at: datetime,
) -> tuple[CrossrefWorkRecord, tuple[str, ...]]:
    """Normalize one work-list item behind the provider boundary."""

    try:
        return _normalize_crossref_message(message, retrieved_at)
    except ValidationError as error:
        raise CrossrefRecordError(
            f"Crossref provider model validation failed: {error}"
        ) from error


def _normalize_journal_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def crossref_record_matches_journal(record: CrossrefWorkRecord, journal: JournalConfig) -> bool:
    """Match configured venue identity, using journal name only without ISSNs."""

    if record.issns:
        return bool(set(record.issns) & set(journal.issn))
    return (
        record.journal is not None
        and _normalize_journal_name(record.journal)
        == _normalize_journal_name(journal.name)
    )


def discover_crossref_journals(
    client: CrossrefClient,
    journals: Sequence[JournalConfig],
    from_date: date,
    to_date: date,
    *,
    retrieved_at: datetime | None = None,
    progress_callback: ProgressCallback | None = None,
) -> CrossrefDiscoveryResult:
    if from_date > to_date:
        raise ValueError("from_date must not be after to_date")
    timestamp = retrieved_at or datetime.now(timezone.utc)
    if timestamp.utcoffset() is None:
        raise ValueError("retrieved_at must include a timezone")
    timestamp = timestamp.astimezone(timezone.utc)

    records: list[tuple[int, int, CrossrefWorkRecord]] = []
    issues: list[CrossrefDiscoveryIssue] = []
    coverage: list[CoverageUnit] = []
    units: list[CrossrefDiscoveryUnitResult] = []
    for journal_index, journal in enumerate(journals):
        for issn_index, issn in enumerate(journal.issn):
            issue_start = len(issues)
            unit_records: list[CrossrefWorkRecord] = []
            activity = ActivityUpdate(
                kind=ActivityKind.WORKING,
                source="crossref",
                operation=f"journal_discovery:{journal_index}:{issn_index}",
                label="Discovering Crossref works",
                detail=f"{journal.name} · ISSN {issn}",
                current=0,
                total=None,
                unit="work",
            )
            completed_pages = 0
            dropped_record = False
            try:
                if progress_callback is None:
                    pages = client.iter_journal_work_pages(issn, from_date, to_date)
                else:
                    pages = client.iter_journal_work_pages(
                        issn,
                        from_date,
                        to_date,
                        progress_callback=progress_callback,
                        activity=activity,
                    )
                for page in pages:
                    completed_pages += 1
                    message = page["message"]
                    for item in message["items"]:
                        raw_doi = item.get("DOI") if isinstance(item, dict) else None
                        try:
                            record, warnings = normalize_crossref_discovered_work(
                                item,
                                timestamp,
                            )
                        except CrossrefRecordError as error:
                            dropped_record = True
                            issues.append(
                                CrossrefDiscoveryIssue(
                                    severity=EnrichmentIssueSeverity.WARNING,
                                    stage="record_normalization",
                                    journal=journal.name,
                                    issn=issn,
                                    doi=raw_doi if isinstance(raw_doi, str) else None,
                                    message=str(error),
                                )
                            )
                            continue
                        issues.extend(
                            CrossrefDiscoveryIssue(
                                severity=EnrichmentIssueSeverity.WARNING,
                                stage="field_normalization",
                                journal=journal.name,
                                issn=issn,
                                record_id=record.provenance.record_id,
                                doi=record.doi,
                                message=warning,
                            )
                            for warning in warnings
                        )
                        if not crossref_record_matches_journal(record, journal):
                            identity = (
                                f"ISSNs {', '.join(record.issns)}"
                                if record.issns
                                else f"journal {record.journal!r}"
                            )
                            issues.append(
                                CrossrefDiscoveryIssue(
                                    severity=EnrichmentIssueSeverity.WARNING,
                                    stage="venue_validation",
                                    journal=journal.name,
                                    issn=issn,
                                    record_id=record.provenance.record_id,
                                    doi=record.doi,
                                    message=(
                                        f"record {identity} does not match configured "
                                        "journal identity"
                                    ),
                                )
                            )
                            continue
                        records.append((journal_index, issn_index, record))
                        unit_records.append(record)
            except CrossrefNotFoundError as error:
                issues.append(
                    CrossrefDiscoveryIssue(
                        severity=EnrichmentIssueSeverity.WARNING,
                        stage="journal_not_found",
                        journal=journal.name,
                        issn=issn,
                        message=str(error),
                    )
                )
                status = (
                    CoverageStatus.PARTIAL
                    if completed_pages
                    else CoverageStatus.UNAVAILABLE
                )
            except CrossrefRequestError as error:
                issues.append(
                    CrossrefDiscoveryIssue(
                        severity=EnrichmentIssueSeverity.ERROR,
                        stage="work_retrieval",
                        journal=journal.name,
                        issn=issn,
                        message=str(error),
                    )
                )
                status = (
                    CoverageStatus.PARTIAL
                    if completed_pages
                    else CoverageStatus.FAILED
                )
            else:
                status = (
                    CoverageStatus.PARTIAL
                    if dropped_record
                    else CoverageStatus.COMPLETE
                )
            coverage.append(
                CoverageUnit(
                    provider="crossref",
                    component=CoverageComponent.CROSSREF_DISCOVERY,
                    status=status,
                    journal=journal.name,
                    issn=issn,
                )
            )
            unit_records.sort(key=lambda record: (record.doi, record.provenance.record_id))
            units.append(CrossrefDiscoveryUnitResult(
                journal=journal,
                issn=issn,
                coverage=coverage[-1],
                records=tuple(unit_records),
                issues=tuple(issues[issue_start:]),
            ))
        if journal.issn:
            _report_activity(
                progress_callback,
                ActivityUpdate(
                    kind=ActivityKind.WORKING,
                    source="crossref",
                    operation=f"journal_completion:{journal_index}",
                    label="Completed Crossref journal discovery",
                    detail=journal.name,
                    current=len(journal.issn),
                    total=len(journal.issn),
                    unit="issn",
                ),
            )

    records.sort(
        key=lambda item: (
            item[0],
            item[1],
            item[2].doi,
            item[2].provenance.record_id,
        )
    )
    return CrossrefDiscoveryResult(
        records=tuple(record for _, _, record in records),
        issues=tuple(issues),
        coverage=tuple(coverage),
        units=tuple(units),
    )


def enrich_records(
    client: CrossrefClient,
    records: Sequence[OpenAlexWorkRecord],
    *,
    retrieved_at: datetime | None = None,
) -> EnrichmentResult:
    timestamp = retrieved_at or datetime.now(timezone.utc)
    if timestamp.utcoffset() is None:
        raise ValueError("retrieved_at must include a timezone")
    timestamp = timestamp.astimezone(timezone.utc)

    enriched: list[EnrichedWorkRecord] = []
    issues: list[EnrichmentIssue] = []
    for record in records:
        record_id = record.external_ids.openalex
        doi = record.external_ids.doi
        if doi is None:
            enriched.append(EnrichedWorkRecord(openalex=record))
            issues.append(
                EnrichmentIssue(
                    severity=EnrichmentIssueSeverity.WARNING,
                    stage="missing_doi",
                    record_id=record_id,
                    message="record has no DOI; Crossref lookup was skipped",
                )
            )
            continue

        try:
            payload = client.get_work_by_doi(doi)
            crossref_record, warnings = normalize_crossref_work(
                payload,
                doi,
                timestamp,
            )
        except CrossrefNotFoundError as error:
            enriched.append(EnrichedWorkRecord(openalex=record))
            issues.append(
                EnrichmentIssue(
                    severity=EnrichmentIssueSeverity.WARNING,
                    stage="not_found",
                    record_id=record_id,
                    doi=doi,
                    message=str(error),
                )
            )
            continue
        except CrossrefRequestError as error:
            enriched.append(EnrichedWorkRecord(openalex=record))
            issues.append(
                EnrichmentIssue(
                    severity=EnrichmentIssueSeverity.ERROR,
                    stage="request_failure",
                    record_id=record_id,
                    doi=doi,
                    message=str(error),
                )
            )
            continue
        except CrossrefRecordError as error:
            enriched.append(EnrichedWorkRecord(openalex=record))
            issues.append(
                EnrichmentIssue(
                    severity=EnrichmentIssueSeverity.ERROR,
                    stage="record_normalization",
                    record_id=record_id,
                    doi=doi,
                    message=str(error),
                )
            )
            continue

        enriched.append(EnrichedWorkRecord(openalex=record, crossref=crossref_record))
        issues.extend(
            EnrichmentIssue(
                severity=EnrichmentIssueSeverity.WARNING,
                stage="field_normalization",
                record_id=record_id,
                doi=doi,
                message=warning,
            )
            for warning in warnings
        )

    return EnrichmentResult(records=tuple(enriched), issues=tuple(issues))
