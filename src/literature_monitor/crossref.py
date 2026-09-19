"""DOI-only Crossref enrichment for retained OpenAlex records."""

from __future__ import annotations

import json
import socket
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from html.parser import HTMLParser
from typing import Annotated, Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from pydantic import Field, ValidationError, model_validator

from literature_monitor.identifiers import normalize_doi
from literature_monitor.models import (
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

CROSSREF_BASE_URL = "https://api.crossref.org"


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


class CrossrefError(RuntimeError):
    """Base class for Crossref provider failures."""


class CrossrefNotFoundError(CrossrefError):
    """Crossref has no record for the requested DOI."""


class CrossrefRequestError(CrossrefError):
    """A Crossref request failed or returned invalid transport data."""


class CrossrefRecordError(CrossrefError):
    """A Crossref record cannot be normalized safely."""


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

    def get_work_by_doi(self, doi: str) -> dict[str, Any]:
        try:
            normalized_doi = normalize_doi(doi)
        except ValueError as error:
            raise CrossrefRecordError("invalid requested DOI") from error
        if normalized_doi is None:
            raise CrossrefRecordError("missing requested DOI")

        url = f"{self.base_url}/v1/works/{quote(normalized_doi, safe='')}"
        if self.mailto is not None:
            url = f"{url}?{urlencode({'mailto': self.mailto})}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "literature-monitor/0.1",
            },
        )

        for attempt in range(3):
            try:
                with self._opener(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read())
                if not isinstance(payload, dict):
                    raise CrossrefRequestError(
                        "Crossref returned a non-object JSON response"
                    )
                return payload
            except HTTPError as error:
                if error.code == 404:
                    raise CrossrefNotFoundError(
                        f"DOI {normalized_doi} is not present in Crossref"
                    ) from error
                if (error.code == 429 or error.code >= 500) and attempt < 2:
                    self._sleep(2**attempt)
                    continue
                raise CrossrefRequestError(
                    f"Crossref request failed with HTTP {error.code}"
                ) from error
            except (TimeoutError, socket.timeout, URLError, OSError) as error:
                if attempt < 2:
                    self._sleep(2**attempt)
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
    if not isinstance(message, dict):
        raise CrossrefRecordError("Crossref response lacks a work message")

    try:
        normalized_requested_doi = normalize_doi(requested_doi)
        normalized_response_doi = normalize_doi(message.get("DOI"))
    except ValueError as error:
        raise CrossrefRecordError("Crossref response has an invalid DOI") from error
    if normalized_requested_doi is None:
        raise CrossrefRecordError("requested DOI is missing")
    if normalized_response_doi is None:
        raise CrossrefRecordError("Crossref response is missing its DOI")
    if normalized_response_doi != normalized_requested_doi:
        raise CrossrefRecordError(
            "Crossref response DOI does not match the requested DOI"
        )

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
            doi=normalized_response_doi,
            title=title,
            journal=journal,
            abstract=abstract,
            dates=tuple(dates),
            relations=tuple(relations),
            work_type=work_type,
            provenance=MetadataSource(
                provider="crossref",
                record_id=normalized_response_doi,
                retrieved_at=normalized_retrieved_at,
            ),
        ),
        tuple(warnings),
    )


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
