"""OpenAlex venue-first discovery."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from pydantic import Field, ValidationError, field_validator

from literature_monitor.config import JournalConfig, normalize_journal_issn, normalize_publisher_id
from literature_monitor.url_safety import normalize_public_http_url
from literature_monitor.coverage import (
    CoverageComponent,
    CoverageStatus,
    CoverageUnit,
)
from literature_monitor.identifiers import normalize_doi
from literature_monitor.models import (
    Author,
    CanonicalMetadata,
    DomainModel,
    ExternalIds,
    MetadataSource,
    NonEmptyStr,
    ProviderWorkEvidence,
)
from literature_monitor.progress import (
    ActivityKind,
    ActivityUpdate,
    ProgressCallback,
    ProgressEvent,
)

OPENALEX_BASE_URL = "https://api.openalex.org"
SOURCE_FIELDS = (
    "id,display_name,issn_l,issn,type,alternate_titles,abbreviated_title,"
    "host_organization,host_organization_name,homepage_url"
)
THIN_WORK_FIELDS = (
    "id,doi,title,publication_date,abstract_inverted_index,authorships,"
    "primary_location"
)
_OPENALEX_BATCH_SIZE = 100
WORK_FIELDS = (
    "id,doi,title,publication_date,abstract_inverted_index,authorships,"
    "primary_location"
)
_OPENALEX_ID_PATTERN = re.compile(
    r"^(?:https://openalex\.org/)?([SAW]\d+)$", re.IGNORECASE
)
_ORCID_PATTERN = re.compile(
    r"^(?:https?://orcid\.org/)?\d{4}-\d{4}-\d{4}-\d{3}[\dX]/?$", re.IGNORECASE,
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
    configured_issn_l: str
    openalex_id: str
    display_name: str
    aliases: tuple[str, ...]
    provider_issn_l: str | None
    publisher_id: str | None = None
    journal: str = ""  # Presentation only; configured_issn_l owns the relationship.
    host_organization_name: str | None = None
    homepage_url: str | None = None
    homepage_hostname: str | None = None


class OpenAlexMetadata(DomainModel):
    """Partial bibliographic fields; journal comes from verified Source resolution."""

    title: NonEmptyStr | None = None
    journal: NonEmptyStr
    publication_date: date | None = None
    abstract: str | None = None
    author_keywords: tuple[NonEmptyStr, ...] = ()


class OpenAlexWorkRecord(DomainModel):
    """Provider record that deliberately has no canonical UUID or workflow state."""

    metadata: OpenAlexMetadata
    external_ids: ExternalIds
    authors: tuple[Author, ...] = ()
    source_id: NonEmptyStr
    provenance: MetadataSource
    is_published: bool | None = Field(default=None, exclude=True)

    @field_validator("is_published", mode="before")
    @classmethod
    def normalize_publication_flag(cls, value: object) -> bool | None:
        return value if type(value) is bool else None

    @field_validator("metadata", mode="before")
    @classmethod
    def accept_complete_metadata(cls, value: object) -> object:
        # Existing complete-record callers retain their input shape; ingestion is partial.
        return value.model_dump() if isinstance(value, CanonicalMetadata) else value

    def to_evidence(self) -> ProviderWorkEvidence:
        return ProviderWorkEvidence(
            provenance=self.provenance,
            title=self.metadata.title,
            journal=self.metadata.journal,
            publication_date=self.metadata.publication_date,
            abstract=self.metadata.abstract,
            author_keywords=self.metadata.author_keywords,
            authors=self.authors,
            external_ids=self.external_ids,
        )


@dataclass(frozen=True)
class OpenAlexDiscoveryUnitResult:
    """Journal-local normalized output and diagnostics from one execution."""

    journal: JournalConfig
    coverage: CoverageUnit | None
    source: ResolvedSource | None
    records: tuple[OpenAlexWorkRecord, ...]
    issues: tuple[DiscoveryIssue, ...]


@dataclass(frozen=True)
class DiscoveryResult:
    sources: tuple[ResolvedSource, ...]
    records: tuple[OpenAlexWorkRecord, ...]
    issues: tuple[DiscoveryIssue, ...]
    coverage: tuple[CoverageUnit, ...] = ()
    units: tuple[OpenAlexDiscoveryUnitResult, ...] = ()

    @property
    def has_errors(self) -> bool:
        return any(issue.severity is IssueSeverity.ERROR for issue in self.issues)


class OpenAlexError(RuntimeError):
    """Base class for OpenAlex request and response failures."""


class OpenAlexNotFoundError(OpenAlexError):
    """The requested singleton external identifier was not found."""


class OpenAlexFailureKind(str, Enum):
    RECOVERABLE = "recoverable"
    AUTHORIZATION = "authorization"
    QUOTA = "quota"
    CIRCUIT_OPEN = "circuit_open"


class OpenAlexRequestError(OpenAlexError):
    """A remote request failed or returned an invalid response."""

    def __init__(self, message: str, *, kind: OpenAlexFailureKind = OpenAlexFailureKind.RECOVERABLE):
        super().__init__(message)
        self.kind = kind

    @property
    def terminal(self) -> bool:
        return self.kind is not OpenAlexFailureKind.RECOVERABLE


class OpenAlexRecordError(OpenAlexError):
    """One provider record lacks data required for normalization."""


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


class OpenAlexClient:
    """Own one pooled HTTP client; close it after each execution, usually with `with`."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = OPENALEX_BASE_URL,
        timeout: float = 30,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key.strip() if api_key and api_key.strip() else None
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        try:
            self._http_client = httpx.Client(
                transport=transport,
                timeout=timeout,
                follow_redirects=True,
            )
        except httpx.InvalidURL:
            if transport is not None:
                raise
            # No URL configuration is passed here; InvalidURL comes from env proxies.
            self._http_client = httpx.Client(
                timeout=timeout,
                follow_redirects=True,
                trust_env=False,
            )
        self._sleep = sleep
        self._terminal_failure: OpenAlexRequestError | None = None

    def close(self) -> None:
        self._http_client.close()

    def __enter__(self) -> OpenAlexClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def get_source_by_issn(
        self,
        issn: str,
        *,
        progress_callback: ProgressCallback | None = None,
        activity: ActivityUpdate | None = None,
    ) -> dict[str, Any]:
        if activity is None and progress_callback is not None:
            activity = ActivityUpdate(
                kind=ActivityKind.WORKING,
                source="openalex",
                operation="source_resolution",
                label="Resolving OpenAlex source",
                detail=f"ISSN {issn}",
                unit="issn",
            )
        return self._request_json(
            f"/sources/issn:{issn}",
            {"select": SOURCE_FIELDS},
            not_found_message=f"ISSN {issn} is not present in OpenAlex",
            progress_callback=progress_callback,
            activity=activity,
        )

    def iter_work_pages(
        self,
        source_id: str,
        from_date: date,
        to_date: date,
        *,
        progress_callback: ProgressCallback | None = None,
        activity: ActivityUpdate | None = None,
    ) -> Iterator[dict[str, Any]]:
        yield from self._iter_work_pages(
            (source_id,), from_date, to_date, fields=WORK_FIELDS,
            progress_callback=progress_callback, activity=activity,
        )

    def get_sources_by_issns(
        self, issns: Sequence[str], *, progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if not 1 <= len(issns) <= _OPENALEX_BATCH_SIZE:
            raise ValueError("Source batch requires 1–100 ISSNs")
        return self._request_json(
            "/sources", {"filter": "issn:" + "|".join(issns), "select": SOURCE_FIELDS, "per_page": "100"},
            progress_callback=progress_callback,
            activity=ActivityUpdate(kind=ActivityKind.WORKING, source="openalex",
                                    operation="source_resolution:batch", label="Resolving OpenAlex sources", unit="issn"),
        )

    def get_publishers_by_ids(self, publisher_ids: Sequence[str]) -> dict[str, Any]:
        ids = tuple(dict.fromkeys(normalize_publisher_id(p) for p in publisher_ids))
        if not 1 <= len(ids) <= _OPENALEX_BATCH_SIZE:
            raise ValueError("Publisher batch requires 1–100 IDs")
        return self._request_json("/publishers", {
            "filter": "ids.openalex:" + "|".join(p.rsplit("/", 1)[-1] for p in ids), "select": "id,display_name,homepage_url", "per_page": "100",
        })

    def iter_thin_work_pages(
        self, source_ids: Sequence[str], from_date: date, to_date: date, *,
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[dict[str, Any]]:
        if not 1 <= len(source_ids) <= _OPENALEX_BATCH_SIZE:
            raise ValueError("Works batch requires 1–100 Sources")
        yield from self._iter_work_pages(
            source_ids, from_date, to_date, fields=THIN_WORK_FIELDS,
            progress_callback=progress_callback,
        )

    def _iter_work_pages(
        self, source_ids: Sequence[str], from_date: date, to_date: date, *, fields: str,
        progress_callback: ProgressCallback | None = None, activity: ActivityUpdate | None = None,
    ) -> Iterator[dict[str, Any]]:
        cursor: str | None = "*"
        seen_cursors: set[str] = set()
        short_source_id = "|".join(_short_openalex_id(source_id, "S") for source_id in source_ids)
        if activity is None and progress_callback is not None:
            activity = ActivityUpdate(
                kind=ActivityKind.WORKING,
                source="openalex",
                operation=f"works_discovery:{short_source_id}",
                label="Discovering OpenAlex works",
                detail=f"Source {short_source_id}",
                current=0,
                unit="work",
            )
        current = (
            activity.current
            if activity is not None and activity.current is not None
            else 0
        )
        total = activity.total if activity is not None else None
        activity_detail = activity.detail if activity is not None else None
        page_number = 0
        _report_activity(progress_callback, activity)
        while cursor is not None:
            if cursor in seen_cursors:
                raise OpenAlexRequestError("OpenAlex returned a repeated cursor")
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
                "/works",
                {
                    "filter": (
                        f"primary_location.source.id:{short_source_id},"
                        f"from_publication_date:{from_date.isoformat()},"
                        f"to_publication_date:{to_date.isoformat()}"
                    ),
                    "select": fields,
                    "per_page": "100",
                    "cursor": cursor,
                },
                progress_callback=progress_callback,
                activity=request_activity,
            )
            results = payload.get("results")
            meta = payload.get("meta")
            if not isinstance(results, list) or not isinstance(meta, dict):
                raise OpenAlexRequestError("OpenAlex Works response lacks results or meta")
            next_cursor = meta.get("next_cursor")
            if next_cursor is not None and not isinstance(next_cursor, str):
                raise OpenAlexRequestError("OpenAlex Works response has an invalid next_cursor")
            page_current = current + len(results)
            response_total = _progress_total(meta.get("count"))
            if response_total is not None and response_total >= page_current:
                total = response_total
            elif total is not None and total < page_current:
                total = None
            current = page_current
            if activity is not None:
                activity = replace(
                    activity,
                    kind=ActivityKind.WORKING,
                    label="Retrieved OpenAlex page",
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
            cursor = next_cursor
        if activity is not None:
            _report_activity(
                progress_callback,
                replace(
                    activity,
                    label="Completed OpenAlex journal discovery",
                    current=current,
                    total=total,
                ),
            )

    def _request_json(
        self,
        path: str,
        params: dict[str, str],
        *,
        not_found_message: str | None = None,
        progress_callback: ProgressCallback | None = None,
        activity: ActivityUpdate | None = None,
    ) -> dict[str, Any]:
        if self._terminal_failure is not None:
            raise OpenAlexRequestError(
                "OpenAlex execution circuit is open", kind=OpenAlexFailureKind.CIRCUIT_OPEN,
            ) from self._terminal_failure
        url = f"{self.base_url}{path}?{urlencode(params)}"
        headers = {
            "Accept": "application/json",
            "User-Agent": "literature-monitor/0.6.3",
        }
        if self.api_key is not None:
            headers["Authorization"] = f"Bearer {self.api_key}"

        for attempt in range(3):
            attempt_number = attempt + 1
            if activity is not None:
                _report_activity(
                    progress_callback,
                    replace(
                        activity,
                        kind=ActivityKind.WORKING,
                        label="Requesting OpenAlex",
                        detail=(
                            f"{activity.detail} · attempt {attempt_number}/3"
                            if activity.detail
                            else f"attempt {attempt_number}/3"
                        ),
                    ),
                )
            try:
                response = self._http_client.get(url, headers=headers, timeout=self.timeout)
                response.raise_for_status()
                payload = json.loads(response.content)
                if not isinstance(payload, dict):
                    raise OpenAlexRequestError("OpenAlex returned a non-object JSON response")
                if activity is not None:
                    _report_activity(
                        progress_callback,
                        replace(
                            activity,
                            kind=ActivityKind.WORKING,
                            label="Received OpenAlex response",
                            detail=(
                                f"{activity.detail} · attempt {attempt_number}/3"
                                if activity.detail
                                else f"attempt {attempt_number}/3"
                            ),
                        ),
                    )
                return payload
            except httpx.HTTPStatusError as error:
                status_code = error.response.status_code
                if status_code == 404 and not_found_message is not None:
                    raise OpenAlexNotFoundError(not_found_message) from error
                if (status_code == 429 or status_code >= 500) and attempt < 2:
                    delay = 2**attempt
                    if activity is not None:
                        # retry 事件必须先于 sleep，避免 backoff 被误判成无活动。
                        _report_activity(
                            progress_callback,
                            replace(
                                activity,
                                kind=ActivityKind.RETRYING,
                                label="Retrying OpenAlex request",
                                detail=(
                                    f"{activity.detail} · HTTP {status_code} · "
                                    f"backoff {delay}s"
                                    if activity.detail
                                    else f"HTTP {status_code} · backoff {delay}s"
                                ),
                            ),
                        )
                    self._sleep(delay)
                    continue
                kind = (OpenAlexFailureKind.AUTHORIZATION if status_code in {401, 403}
                        else OpenAlexFailureKind.QUOTA if status_code in {402, 429}
                        else OpenAlexFailureKind.RECOVERABLE)
                failure = OpenAlexRequestError(f"OpenAlex request failed with HTTP {status_code}", kind=kind)
                if failure.terminal:
                    self._terminal_failure = failure
                raise failure from error
            except httpx.RequestError as error:
                if attempt < 2:
                    delay = 2**attempt
                    if activity is not None:
                        _report_activity(
                            progress_callback,
                            replace(
                                activity,
                                kind=ActivityKind.RETRYING,
                                label="Retrying OpenAlex request",
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
                raise OpenAlexRequestError(f"OpenAlex request failed: {error}") from error
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise OpenAlexRequestError("OpenAlex returned invalid JSON") from error

        raise AssertionError("unreachable")


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


def _optional_host_organization(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(
        r"(?:https://openalex\.org/)?([A-Z]\d+)",
        value.strip(),
        flags=re.IGNORECASE,
    )
    return f"https://openalex.org/{match.group(1).upper()}" if match else None


def _optional_display_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or any(
        ord(character) < 0x20 or ord(character) == 0x7F
        for character in normalized
    ):
        return None
    return normalized


def _optional_homepage(value: Any) -> tuple[str | None, str | None]:
    if not isinstance(value, str):
        return None, None
    try:
        url = normalize_public_http_url(value.strip())
    except ValueError:
        return None, None
    return (url, urlsplit(url).hostname.lower().rstrip(".")) if url else (None, None)


def _stable_optional(values: Sequence[str | None]) -> str | None:
    distinct: list[str] = []
    for value in values:
        if value is not None and value not in distinct:
            distinct.append(value)
    return distinct[0] if len(distinct) == 1 else None


def _resolve_journal_source(
    client: OpenAlexClient, journal: JournalConfig, *,
    progress_callback: ProgressCallback | None = None, operation: str | None = None,
) -> tuple[ResolvedSource | None, tuple[DiscoveryIssue, ...], CoverageStatus | None]:
    try:
        evidence = _parse_identity_source(client.get_source_by_issn(
            journal.issn_l, progress_callback=progress_callback,
        ), journal.issn_l)
        outcome = SourceEvidenceResolution(journal.issn_l, evidence, SourceEvidenceStatus.RESOLVED)
    except OpenAlexError as error:
        outcome = SourceEvidenceResolution(
            journal.issn_l, None,
            SourceEvidenceStatus.NOT_FOUND if isinstance(error, OpenAlexNotFoundError)
            else SourceEvidenceStatus.INVALID_SOURCE if isinstance(error, OpenAlexRecordError)
            else SourceEvidenceStatus.REQUEST_FAILED, str(error),
        )
    unit = _configured_source_unit(journal, outcome)
    return unit.source, unit.issues, unit.status


def resolve_journal_source(
    client: OpenAlexClient,
    journal: JournalConfig,
    *,
    progress_callback: ProgressCallback | None = None,
    operation: str | None = None,
) -> tuple[ResolvedSource | None, tuple[DiscoveryIssue, ...]]:
    source, issues, _ = _resolve_journal_source(
        client,
        journal,
        progress_callback=progress_callback,
        operation=operation,
    )
    return source, issues


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
        if not isinstance(word, str) or not word.strip() or not isinstance(indexes, list):
            raise ValueError("invalid abstract_inverted_index")
        for index in indexes:
            if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index in positions:
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


def _work_source_id(payload: Any) -> str | None:
    """Absent nested identity is distinct from explicit invalid/conflicting identity."""
    if not isinstance(payload, dict):
        raise OpenAlexRecordError("work response entry is not an object")
    primary_location = payload.get("primary_location")
    if primary_location is None:
        return None
    if not isinstance(primary_location, dict):
        raise OpenAlexRecordError("invalid work primary_location")
    source_payload = primary_location.get("source")
    if source_payload is None:
        return None
    if not isinstance(source_payload, dict):
        raise OpenAlexRecordError("invalid work primary_location.source")
    if source_payload.get("id") is None:
        return None
    return _canonical_openalex_id(source_payload["id"], "S")


def _normalize_work(
    payload: Any,
    source: ResolvedSource,
    retrieved_at: datetime,
) -> tuple[OpenAlexWorkRecord | None, tuple[str, ...]]:
    if not isinstance(payload, dict):
        raise OpenAlexRecordError("work response entry is not an object")
    work_id = _canonical_openalex_id(payload.get("id"), "W")
    raw_title = payload.get("title")
    title = raw_title.strip() if isinstance(raw_title, str) and raw_title.strip() else None

    work_source_id = _work_source_id(payload)
    if work_source_id is not None and work_source_id != source.openalex_id:
        raise OpenAlexRecordError(
            f"work venue {work_source_id} does not match resolved Source {source.openalex_id}"
        )

    try:
        publication_date = _parse_publication_date(payload.get("publication_date"))
    except (TypeError, ValueError):
        publication_date = None
    try:
        doi = normalize_doi(payload.get("doi"))
    except ValueError:
        doi = None
    if doi is not None and re.fullmatch(r"10\.\d{4,9}/\S+", doi) is None:
        doi = None
    if doi is None and title is None:
        return None, ("work has neither a valid DOI nor a usable title",)
    try:
        abstract = _reconstruct_abstract(payload.get("abstract_inverted_index"))
    except ValueError:
        abstract = None

    authorships = payload.get("authorships")
    if not isinstance(authorships, list):
        authorships = []
    authors: list[Author] = []
    for authorship in authorships:
        if not isinstance(authorship, dict):
            continue
        author_payload = authorship.get("author")
        author_payload = author_payload if isinstance(author_payload, dict) else {}
        display_name = author_payload.get("display_name")
        raw_name = authorship.get("raw_author_name")
        name = display_name if isinstance(display_name, str) and display_name.strip() else raw_name
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            author_id = _optional_openalex_id(author_payload.get("id"), "A")
        except OpenAlexRecordError:
            author_id = None
        orcid_raw = author_payload.get("orcid")
        orcid = orcid_raw.strip() if isinstance(orcid_raw, str) and orcid_raw.strip() else None
        if orcid is not None and _ORCID_PATTERN.fullmatch(orcid) is None:
            orcid = None
        authors.append(Author(name=name, openalex_id=author_id, orcid=orcid))

    return (
        OpenAlexWorkRecord(
            metadata=OpenAlexMetadata(
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
            is_published=(
                payload["primary_location"].get("is_published")
                if isinstance(payload.get("primary_location"), dict) else None
            ),
        ),
        (),
    )


def discover_openalex_journal(
    client: OpenAlexClient,
    journal: JournalConfig,
    from_date: date,
    to_date: date,
    *,
    retrieved_at: datetime,
    progress_callback: ProgressCallback | None = None,
    unit_index: int = 0,
) -> OpenAlexDiscoveryUnitResult:
    """Execute one full configured journal, with a caller-owned phase timestamp."""

    if from_date > to_date:
        raise ValueError("from_date must not be after to_date")
    timestamp = retrieved_at or datetime.now(timezone.utc)
    if timestamp.utcoffset() is None:
        raise ValueError("retrieved_at must include a timezone")
    timestamp = timestamp.astimezone(timezone.utc)

    issues: list[DiscoveryIssue] = []
    unit_records: list[OpenAlexWorkRecord] = []
    coverage: list[CoverageUnit] = []
    source, resolution_issues, resolution_status = _resolve_journal_source(
        client,
        journal,
        progress_callback=progress_callback,
        operation=f"source_resolution:{unit_index}",
    )
    issues.extend(resolution_issues)
    if source is None:
        coverage.append(
            CoverageUnit(
                provider="openalex",
                component=CoverageComponent.OPENALEX_DISCOVERY,
                status=resolution_status or CoverageStatus.FAILED,
                journal=journal.issn_l,
            )
        )
        return OpenAlexDiscoveryUnitResult(
            journal=journal,
            coverage=coverage[-1],
            source=None,
            records=(),
            issues=tuple(issues),
        )
    activity = ActivityUpdate(
        kind=ActivityKind.WORKING,
        source="openalex",
        operation=f"works_discovery:{unit_index}",
        label="Discovering OpenAlex works",
        detail=journal.name,
        current=0,
        total=None,
        unit="work",
    )
    completed_pages = 0
    dropped_record = False
    try:
        if progress_callback is None:
            pages = client.iter_work_pages(source.openalex_id, from_date, to_date)
        else:
            pages = client.iter_work_pages(
                source.openalex_id,
                from_date,
                to_date,
                progress_callback=progress_callback,
                activity=activity,
            )
        for page in pages:
            completed_pages += 1
            for payload in page["results"]:
                record_id = payload.get("id") if isinstance(payload, dict) else None
                try:
                    record, warnings = _normalize_work(payload, source, timestamp)
                except (OpenAlexError, ValidationError) as error:
                    dropped_record = True
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
                if record is not None:
                    unit_records.append(record)
                for warning in warnings:
                    issues.append(
                        DiscoveryIssue(
                            severity=IssueSeverity.WARNING,
                            stage="record_normalization",
                            journal=journal.name,
                            record_id=_canonical_openalex_id(record_id, "W"),
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
        coverage.append(
            CoverageUnit(
                provider="openalex",
                component=CoverageComponent.OPENALEX_DISCOVERY,
                status=(
                    CoverageStatus.PARTIAL
                    if completed_pages
                    else CoverageStatus.FAILED
                ),
                journal=journal.issn_l,
            )
        )
    else:
        coverage.append(
            CoverageUnit(
                provider="openalex",
                component=CoverageComponent.OPENALEX_DISCOVERY,
                status=(
                    CoverageStatus.PARTIAL
                    if dropped_record
                    else CoverageStatus.COMPLETE
                ),
                journal=journal.issn_l,
            )
        )

    unit_records.sort(key=lambda record: (
        record.metadata.publication_date or date.max,
        record.external_ids.openalex or "",
    ))
    return OpenAlexDiscoveryUnitResult(
        journal=journal,
        coverage=coverage[-1],
        source=source,
        records=tuple(unit_records),
        issues=tuple(issues),
    )


def discover_journals(
    client: OpenAlexClient,
    journals: Sequence[JournalConfig],
    from_date: date,
    to_date: date,
    *,
    retrieved_at: datetime | None = None,
    progress_callback: ProgressCallback | None = None,
) -> DiscoveryResult:
    if from_date > to_date:
        raise ValueError("from_date must not be after to_date")
    timestamp = retrieved_at or datetime.now(timezone.utc)
    if timestamp.utcoffset() is None:
        raise ValueError("retrieved_at must include a timezone")
    timestamp = timestamp.astimezone(timezone.utc)

    units = tuple(
        discover_openalex_journal(
            client, journal, from_date, to_date, retrieved_at=timestamp,
            progress_callback=progress_callback, unit_index=index,
        )
        for index, journal in enumerate(journals)
    )
    return DiscoveryResult(
        sources=tuple(unit.source for unit in units if unit.source is not None),
        records=tuple(record for unit in units for record in unit.records),
        issues=tuple(issue for unit in units for issue in unit.issues),
        coverage=tuple(unit.coverage for unit in units),
        units=units,
    )


@dataclass(frozen=True)
class SourceResolutionUnit:
    journal: JournalConfig
    source: ResolvedSource | None
    issues: tuple[DiscoveryIssue, ...]
    status: CoverageStatus | None


class OpenAlexAmbiguousSourceError(OpenAlexRecordError):
    """More than one Source identity supports a queried identifier."""


@dataclass(frozen=True)
class SourceEvidence:
    source_id: str
    display_name: str
    aliases: tuple[str, ...]
    provider_issn_l: str | None
    publisher_id: str | None = None
    diagnostics: tuple[str, ...] = ()
    host_organization_name: str | None = None
    homepage_url: str | None = None
    homepage_hostname: str | None = None


class SourceEvidenceStatus(str, Enum):
    RESOLVED = "resolved"
    INVALID_IDENTIFIER = "invalid_identifier"
    NOT_FOUND = "not_found"
    INVALID_SOURCE = "invalid_source"
    AMBIGUOUS_SOURCE = "ambiguous_source"
    REQUEST_FAILED = "request_failed"


@dataclass(frozen=True)
class SourceEvidenceResolution:
    requested_issn: str
    evidence: SourceEvidence | None
    status: SourceEvidenceStatus
    diagnostic: str | None = None
    request_failure_kind: OpenAlexFailureKind | None = None


def _usable_source_aliases(payload: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    raw = payload.get("issn")
    if not isinstance(raw, list):
        raise OpenAlexRecordError("invalid Source ISSN membership structure")
    aliases, diagnostics = set(), []
    for value in raw:
        try:
            aliases.add(normalize_journal_issn(value))
        except (ValueError, TypeError, AttributeError):
            diagnostics.append(f"excluded malformed Source alias {value!r}")
    return tuple(sorted(aliases)), tuple(diagnostics)


def _parse_identity_source(payload: dict[str, Any], queried_issn: str) -> SourceEvidence:
    """Verify direct membership; Provider canonical metadata cannot redefine identity."""
    aliases, diagnostics = _usable_source_aliases(payload)
    if queried_issn not in aliases:
        raise OpenAlexRecordError(f"ISSN {queried_issn} is absent from the returned source ISSNs")
    if payload.get("type") != "journal":
        raise OpenAlexRecordError(f"ISSN {queried_issn} resolved to non-journal Source")
    source_id = _canonical_openalex_id(payload.get("id"), "S")
    display_name = _optional_display_string(payload.get("display_name"))
    if display_name is None:
        raise OpenAlexRecordError("invalid Source display_name")
    candidate = None
    raw_candidate = payload.get("issn_l")
    try:
        candidate = normalize_journal_issn(raw_candidate)
    except (ValueError, TypeError, AttributeError):
        diagnostics += (f"unavailable Provider ISSN-L {raw_candidate!r}",)
    else:
        if candidate not in aliases:
            diagnostics += (f"Provider ISSN-L {candidate} is outside usable Source aliases",)
            candidate = None
    publisher = _optional_host_organization(payload.get("host_organization"))
    if publisher is not None and not publisher.startswith("https://openalex.org/P"):
        publisher = None
    homepage_url, homepage_hostname = _optional_homepage(payload.get("homepage_url"))
    return SourceEvidence(source_id, display_name, aliases, candidate, publisher, diagnostics,
                          _optional_display_string(payload.get("host_organization_name")),
                          homepage_url, homepage_hostname)


def reconcile_source_evidence(observations: Sequence[SourceEvidence]) -> SourceEvidence:
    """Reconcile observations of one Source without choosing arbitrary metadata."""
    if len({item.source_id for item in observations}) != 1:
        raise OpenAlexAmbiguousSourceError("multiple distinct OpenAlex Sources support the identifier")
    if len({item.aliases for item in observations}) != 1:
        raise OpenAlexRecordError("Source-data conflict: same Source ID has contradictory membership")
    first = observations[0]
    diagnostics = set(message for item in observations for message in item.diagnostics)
    names = {item.display_name for item in observations}
    if len(names) > 1:
        diagnostics.add("same Source ID has varying display metadata; "
                        "lexical display choice only: " + ", ".join(sorted(names)))
    candidates = {item.provider_issn_l for item in observations}
    publishers = {item.publisher_id for item in observations}
    if len(candidates) > 1:
        diagnostics.add("Provider ISSN-L disagreement for same Source: " +
                        ", ".join(sorted(str(value) for value in candidates)))
    if len(publishers) > 1:
        diagnostics.add("inconsistent optional Publisher metadata for same Source")
    return replace(first, display_name=min(names), provider_issn_l=first.provider_issn_l if len(candidates) == 1 else None,
                   publisher_id=first.publisher_id if len(publishers) == 1 else None,
                   diagnostics=tuple(sorted(diagnostics)))


def _resolve_source_hits_batched(
    client: OpenAlexClient, issns: Sequence[str], *,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, SourceEvidence | OpenAlexError]:
    """Share bounded batch/singleton lookup between production and §41 analysis."""
    issns = tuple(dict.fromkeys(issns))
    outcomes: dict[str, SourceEvidence | OpenAlexError] = {}
    for offset in range(0, len(issns), _OPENALEX_BATCH_SIZE):
        batch = issns[offset:offset + _OPENALEX_BATCH_SIZE]
        candidates: dict[str, list[SourceEvidence]] = {issn: [] for issn in batch}
        try:
            payload = client.get_sources_by_issns(batch, progress_callback=progress_callback)
            results = payload.get("results")
            if not isinstance(results, list):
                raise OpenAlexRequestError("OpenAlex Sources response lacks results")
            count = _progress_total(payload.get("meta", {}).get("count")) if isinstance(payload.get("meta"), dict) else None
            if count is None:
                raise OpenAlexRequestError("OpenAlex Source batch lacks a valid total count")
            if count is not None and count != len(results):
                raise OpenAlexRequestError("incomplete OpenAlex Source batch")
            unsafe: set[str] = set()
            recovery: set[str] = set()
            source_ids: dict[str, set[str]] = {issn: set() for issn in batch}
            for raw in results:
                try:
                    if not isinstance(raw, dict):
                        raise OpenAlexRecordError("Source is not an object")
                    raw_issns, _ = _usable_source_aliases(raw)
                    if not set(raw_issns).intersection(batch):
                        raise OpenAlexRecordError("Source has no assignable queried ISSN alias")
                except OpenAlexRecordError:
                    recovery.update(batch)
                    continue
                for issn in batch:
                    if issn not in raw_issns:
                        continue
                    try:
                        source_ids[issn].add(_canonical_openalex_id(raw.get("id"), "S"))
                    except OpenAlexRecordError:
                        pass  # Parsing below retains the invalid-record failure.
                    try:
                        candidates[issn].append(
                            _parse_identity_source(raw, issn)
                        )
                    except OpenAlexRecordError as error:
                        unsafe.add(issn)
                        outcomes[issn] = error
            for issn, hits in candidates.items():
                if len(source_ids[issn]) > 1:
                    outcomes[issn] = OpenAlexAmbiguousSourceError(
                        "multiple distinct OpenAlex Sources support the identifier")
                    continue
                if issn in unsafe:
                    continue
                if hits:
                    try:
                        merged = reconcile_source_evidence(hits)
                    except OpenAlexRecordError as error:
                        outcomes[issn] = error
                    else:
                        if issn not in recovery:
                            outcomes[issn] = merged
        except OpenAlexRequestError as error:
            if error.terminal:
                outcomes.update((issn, error) for issn in batch)
        for issn in batch:
            if issn in outcomes:
                continue
            try:
                raw = client.get_source_by_issn(issn, progress_callback=progress_callback)
                outcomes[issn] = (
                    _parse_identity_source(raw, issn)
                )
            except OpenAlexError as error:
                outcomes[issn] = error
                if isinstance(error, OpenAlexRequestError) and error.terminal:
                    outcomes.update((remaining, error) for remaining in batch if remaining not in outcomes)
                    break
    return outcomes


def resolve_source_identities(
    client: OpenAlexClient, identifiers: Sequence[str], *,
    progress_callback: ProgressCallback | None = None,
) -> tuple[SourceEvidenceResolution, ...]:
    """Verify identifier membership independently of names and Provider ISSN-L choice."""
    normalized: dict[str, str | ValueError] = {}
    for raw in identifiers:
        try:
            normalized[raw] = normalize_journal_issn(raw)
        except ValueError as error:
            normalized[raw] = error
    outcomes = _resolve_source_hits_batched(
        client, tuple(value for value in normalized.values() if isinstance(value, str)),
        progress_callback=progress_callback,
    )
    results = []
    seen: set[str] = set()
    for raw, value in normalized.items():
        if isinstance(value, ValueError):
            results.append(SourceEvidenceResolution(
                raw, None, SourceEvidenceStatus.INVALID_IDENTIFIER, str(value),
            ))
            continue
        if value in seen:
            continue
        seen.add(value)
        outcome = outcomes[value]
        if isinstance(outcome, SourceEvidence):
            results.append(SourceEvidenceResolution(value, outcome, SourceEvidenceStatus.RESOLVED))
            continue
        status = (SourceEvidenceStatus.NOT_FOUND if isinstance(outcome, OpenAlexNotFoundError)
                  else SourceEvidenceStatus.AMBIGUOUS_SOURCE if isinstance(outcome, OpenAlexAmbiguousSourceError)
                  else SourceEvidenceStatus.INVALID_SOURCE if isinstance(outcome, OpenAlexRecordError)
                  else SourceEvidenceStatus.REQUEST_FAILED)
        results.append(SourceEvidenceResolution(
            value, None, status, str(outcome),
            outcome.kind if isinstance(outcome, OpenAlexRequestError) else None,
        ))
    return tuple(results)


def resolve_journal_sources_batched(
    client: OpenAlexClient, journals: Sequence[JournalConfig], *,
    progress_callback: ProgressCallback | None = None,
) -> tuple[SourceResolutionUnit, ...]:
    """Share identifier-first Source evidence without any name or canonical-field veto."""
    outcomes = {result.requested_issn: result for result in resolve_source_identities(
        client, tuple(journal.issn_l for journal in journals), progress_callback=progress_callback,
    )}
    return tuple(_configured_source_unit(journal, outcomes[journal.issn_l]) for journal in journals)


def _configured_source_unit(journal: JournalConfig, outcome: SourceEvidenceResolution) -> SourceResolutionUnit:
    evidence = outcome.evidence
    issues = []
    source, status = None, None
    if evidence is not None:
        source = ResolvedSource(
            configured_issn_l=journal.issn_l, openalex_id=evidence.source_id,
            display_name=evidence.display_name, aliases=evidence.aliases,
            provider_issn_l=evidence.provider_issn_l, publisher_id=evidence.publisher_id,
            journal=journal.name, host_organization_name=evidence.host_organization_name,
            homepage_url=evidence.homepage_url, homepage_hostname=evidence.homepage_hostname,
        )
        diagnostics = list(evidence.diagnostics)
        if evidence.provider_issn_l is not None and evidence.provider_issn_l != journal.issn_l:
            diagnostics.append(f"Provider ISSN-L {evidence.provider_issn_l} differs from configured ISSN-L {journal.issn_l}")
        issues.extend(DiscoveryIssue(IssueSeverity.WARNING, "source_resolution", journal.name,
                                     message, issn=journal.issn_l) for message in diagnostics)
    else:
        status = (CoverageStatus.UNAVAILABLE if outcome.status is SourceEvidenceStatus.NOT_FOUND
                  else CoverageStatus.FAILED)
        issues.append(DiscoveryIssue(
            IssueSeverity.WARNING if status is CoverageStatus.UNAVAILABLE else IssueSeverity.ERROR,
            "source_resolution", journal.name,
            f"configured ISSN-L {journal.issn_l}: {outcome.diagnostic}", issn=journal.issn_l,
        ))
    return SourceResolutionUnit(journal, source, tuple(issues), status)


def _record_order(record: OpenAlexWorkRecord) -> tuple[date, str]:
    return record.metadata.publication_date or date.max, record.external_ids.openalex or ""


def _fetch_thin_sources(
    client: OpenAlexClient, sources: dict[str, ResolvedSource], from_date: date, to_date: date,
    timestamp: datetime, progress_callback: ProgressCallback | None,
) -> dict[str, tuple[tuple[OpenAlexWorkRecord, ...], tuple[DiscoveryIssue, ...], CoverageStatus]]:
    records: dict[str, dict[str, OpenAlexWorkRecord]] = {key: {} for key in sources}
    issues: dict[str, list[DiscoveryIssue]] = {key: [] for key in sources}
    affected: set[str] = set()
    scope_conflicts: set[str] = set()
    seen: set[str] = set()
    obtained = 0
    expected: int | None = None
    terminal = False
    request_failed = False
    try:
        for page in client.iter_thin_work_pages(tuple(sources), from_date, to_date, progress_callback=progress_callback):
            count = _progress_total(page["meta"].get("count"))
            invalid_count = count is None or (expected is not None and expected != count)
            expected = count
            for raw in page["results"]:
                obtained += 1
                try:
                    source_id = _work_source_id(raw)
                    if source_id is None and len(sources) == 1:
                        source_id = next(iter(sources))
                    if source_id not in sources:
                        if source_id is not None:
                            scope_conflicts.update(sources)
                            raise OpenAlexRecordError(f"work venue {source_id} is outside requested Sources")
                        raise OpenAlexRecordError("unassignable primary Source")
                except OpenAlexRecordError as error:
                    affected.update(sources)
                    for key, source in sources.items():
                        issues[key].append(DiscoveryIssue(IssueSeverity.ERROR, "record_normalization", source.journal, str(error)))
                    continue
                record_id = raw.get("id")
                try:
                    work_id = _canonical_openalex_id(record_id, "W")
                    if work_id in seen:
                        affected.add(source_id)
                        issues[source_id].append(DiscoveryIssue(IssueSeverity.ERROR, "work_retrieval", sources[source_id].journal,
                                                               "duplicate Work in traversal", record_id=work_id))
                    seen.add(work_id)
                    record, warnings = _normalize_work(raw, sources[source_id], timestamp)
                except (OpenAlexError, ValidationError) as error:
                    affected.add(source_id)
                    issues[source_id].append(DiscoveryIssue(IssueSeverity.ERROR, "record_normalization", sources[source_id].journal, str(error), record_id=record_id if isinstance(record_id, str) else None))
                    continue
                if record is not None:
                    records[source_id][record.external_ids.openalex] = record
                issues[source_id].extend(DiscoveryIssue(IssueSeverity.WARNING, "record_normalization", sources[source_id].journal, warning, record_id=work_id) for warning in warnings)
            if invalid_count:
                raise OpenAlexRequestError("invalid or changing OpenAlex Works count")
        if expected != obtained:
            raise OpenAlexRequestError("incomplete OpenAlex Works traversal")
    except OpenAlexError as error:
        request_failed = True
        terminal = isinstance(error, OpenAlexRequestError) and error.terminal
        affected.update(sources)
        for key, source in sources.items():
            issues[key].append(DiscoveryIssue(IssueSeverity.ERROR, "work_retrieval", source.journal, str(error)))
    result = {
        key: (tuple(sorted(records[key].values(), key=_record_order)), tuple(issues[key]),
              (CoverageStatus.PARTIAL if records[key] or not request_failed else CoverageStatus.FAILED) if key in affected else CoverageStatus.COMPLETE)
        for key in sources
    }
    if affected and len(sources) > 1 and not terminal:
        # Retry only affected identities. Split a fully failed batch to bound its failure domain.
        keys = [key for key in sources if key in affected]
        midpoint = max(1, len(keys) // 2)
        for subset in (keys[:midpoint], keys[midpoint:]):
            if not subset:
                continue
            recovered = _fetch_thin_sources(client, {key: sources[key] for key in subset}, from_date, to_date, timestamp, progress_callback)
            for key, (new_records, new_issues, status) in recovered.items():
                # Recovery can restore absent attribution, but cannot erase explicit scope conflicts.
                if status is CoverageStatus.COMPLETE and key not in scope_conflicts:
                    result[key] = new_records, new_issues, status
                else:
                    merged = {record.external_ids.openalex: record for record in (*result[key][0], *new_records)}
                    partial = bool(merged) or status is CoverageStatus.PARTIAL or result[key][2] is CoverageStatus.PARTIAL
                    result[key] = (tuple(sorted(merged.values(), key=_record_order)), (*result[key][1], *new_issues),
                                   CoverageStatus.PARTIAL if partial else CoverageStatus.FAILED)
    return result


def discover_resolved_sources(
    client: OpenAlexClient, resolutions: Sequence[SourceResolutionUnit], from_date: date, to_date: date, *,
    retrieved_at: datetime | None = None, progress_callback: ProgressCallback | None = None,
) -> DiscoveryResult:
    """Retrieve Works from the shared resolution result; perform no Source lookup."""
    if from_date > to_date:
        raise ValueError("from_date must not be after to_date")
    timestamp = retrieved_at or datetime.now(timezone.utc)
    if timestamp.utcoffset() is None:
        raise ValueError("retrieved_at must include a timezone")
    timestamp = timestamp.astimezone(timezone.utc)
    sources = {unit.source.openalex_id: unit.source for unit in resolutions if unit.source is not None}
    keys = tuple(sources)
    fetched = {}
    for offset in range(0, len(keys), _OPENALEX_BATCH_SIZE):
        batch = keys[offset:offset + _OPENALEX_BATCH_SIZE]
        fetched.update(_fetch_thin_sources(client, {key: sources[key] for key in batch}, from_date, to_date, timestamp, progress_callback))
    units = []
    for resolution in resolutions:
        source = resolution.source
        records, issues, status = fetched[source.openalex_id] if source else ((), (), resolution.status or CoverageStatus.FAILED)
        # Source requests are shared; reporting remains journal-local even for aliases.
        issues = tuple(replace(issue, journal=resolution.journal.name) for issue in issues)
        units.append(OpenAlexDiscoveryUnitResult(
            resolution.journal,
            CoverageUnit("openalex", CoverageComponent.OPENALEX_DISCOVERY, status, journal=resolution.journal.issn_l),
            source, records, (*resolution.issues, *issues),
        ))
    return DiscoveryResult(
        tuple(unit.source for unit in units if unit.source),
        tuple(record for unit in units for record in unit.records),
        tuple(issue for unit in units for issue in unit.issues),
        tuple(unit.coverage for unit in units), tuple(units),
    )


def discover_journals_batched(
    client: OpenAlexClient, journals: Sequence[JournalConfig], from_date: date, to_date: date, *,
    retrieved_at: datetime | None = None, progress_callback: ProgressCallback | None = None,
) -> DiscoveryResult:
    """Diagnostic convenience entrypoint: resolve once, then retrieve Works."""
    resolutions = resolve_journal_sources_batched(client, journals, progress_callback=progress_callback)
    return discover_resolved_sources(client, resolutions, from_date, to_date,
                                     retrieved_at=retrieved_at, progress_callback=progress_callback)


@dataclass(frozen=True)
class PublisherMetadata:
    publisher_id: str
    display_name: str
    homepage_url: str | None
    diagnostics: tuple[str, ...] = ()


def resolve_publisher_metadata(client: OpenAlexClient, publisher_ids: Sequence[str]) -> tuple[PublisherMetadata, ...]:
    """Resolve every direct ID strictly; no name, lineage, cache or partial result authority."""
    ids = tuple(dict.fromkeys(normalize_publisher_id(p) for p in publisher_ids))
    resolved = {}
    for offset in range(0, len(ids), _OPENALEX_BATCH_SIZE):
        batch = ids[offset:offset + _OPENALEX_BATCH_SIZE]
        payload = client.get_publishers_by_ids(batch)
        results = payload.get("results")
        count = _progress_total(payload.get("meta", {}).get("count")) if isinstance(payload.get("meta"), dict) else None
        if not isinstance(results, list) or count != len(results):
            raise OpenAlexRecordError("incomplete Publisher batch")
        for raw in results:
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                raise OpenAlexRecordError("malformed Publisher object/ID")
            try:
                identity = normalize_publisher_id(raw["id"])
            except ValueError as error:
                raise OpenAlexRecordError(str(error)) from error
            if identity not in batch or identity in resolved:
                raise OpenAlexRecordError("unexpected or duplicate Publisher ID")
            name = raw.get("display_name")
            if not isinstance(name, str) or not name.strip() or any(c == "|" or ord(c) < 32 or ord(c) == 127 for c in name):
                raise OpenAlexRecordError("unusable canonical Publisher display_name")
            homepage = raw.get("homepage_url")
            diagnostics = ()
            try:
                if homepage is not None and not isinstance(homepage, str):
                    raise ValueError("homepage is not a string")
                url = normalize_public_http_url(homepage)
            except ValueError:
                url = None
                diagnostics = ("unsafe/malformed Publisher homepage ignored",)
            resolved[identity] = PublisherMetadata(identity, name.strip(), url, diagnostics)
        if any(p not in resolved for p in batch):
            raise OpenAlexRecordError("missing requested Publisher metadata")
    return tuple(resolved[p] for p in ids)
