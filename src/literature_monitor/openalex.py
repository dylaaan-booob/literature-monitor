"""OpenAlex venue-first discovery."""

from __future__ import annotations

import json
import re
import time
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from pydantic import Field, ValidationError, field_validator

from literature_monitor.config import JournalConfig
from literature_monitor.coverage import (
    CoverageComponent,
    CoverageStatus,
    CoverageUnit,
)
from literature_monitor.identifiers import normalize_doi
from literature_monitor.provider_revision import parse_revision_timestamp
from literature_monitor.models import (
    Author,
    CanonicalMetadata,
    DomainModel,
    EvidenceVersionHint,
    EvidenceVersionRole,
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
    "id,display_name,issn_l,issn,type,alternate_titles,abbreviated_title"
)
THIN_WORK_FIELDS = (
    "id,doi,title,publication_date,abstract_inverted_index,authorships,"
    "primary_location,updated_date"
)
VERSION_FIELDS = "id,locations"
_OPENALEX_BATCH_SIZE = 100
WORK_FIELDS = (
    "id,doi,title,publication_date,abstract_inverted_index,authorships,"
    "primary_location,locations"
)
_OPENALEX_ID_PATTERN = re.compile(
    r"^(?:https://openalex\.org/)?([SAW]\d+)$", re.IGNORECASE
)
_ORCID_PATTERN = re.compile(
    r"^(?:https?://orcid\.org/)?\d{4}-\d{4}-\d{4}-\d{3}[\dX]/?$", re.IGNORECASE,
)
_ARXIV_LOCATION_ID = re.compile(
    r"^pmh:oai:arxiv\.org:(.+)$",
    re.IGNORECASE,
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


class OpenAlexVersion(str, Enum):
    PUBLISHED = "publishedVersion"
    ACCEPTED = "acceptedVersion"
    SUBMITTED = "submittedVersion"


class OpenAlexVersionHint(DomainModel):
    source: NonEmptyStr
    identifier: NonEmptyStr
    version: OpenAlexVersion
    url: NonEmptyStr | None = None


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
    version_hints: tuple[OpenAlexVersionHint, ...] = ()
    updated_at: datetime | None = Field(default=None, exclude=True)

    @field_validator("metadata", mode="before")
    @classmethod
    def accept_complete_metadata(cls, value: object) -> object:
        # Existing complete-record callers retain their input shape; ingestion is partial.
        return value.model_dump() if isinstance(value, CanonicalMetadata) else value

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_revision(cls, value: object) -> datetime | None:
        return parse_revision_timestamp(value)

    def to_evidence(self) -> ProviderWorkEvidence:
        roles = {
            OpenAlexVersion.PUBLISHED: EvidenceVersionRole.PUBLICATION,
            OpenAlexVersion.ACCEPTED: EvidenceVersionRole.MANUSCRIPT,
            OpenAlexVersion.SUBMITTED: EvidenceVersionRole.PREPRINT,
        }
        return ProviderWorkEvidence(
            provenance=self.provenance,
            title=self.metadata.title,
            journal=self.metadata.journal,
            publication_date=self.metadata.publication_date,
            abstract=self.metadata.abstract,
            author_keywords=self.metadata.author_keywords,
            authors=self.authors,
            external_ids=self.external_ids,
            version_hints=tuple(
                EvidenceVersionHint(
                    source=hint.source,
                    identifier=hint.identifier,
                    role=roles[hint.version],
                    url=hint.url,
                )
                for hint in self.version_hints
            ),
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

    def get_work_locations(
        self, work_ids: Sequence[str], *, progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if not 1 <= len(work_ids) <= _OPENALEX_BATCH_SIZE:
            raise ValueError("Version batch requires 1–100 Works")
        ids = "|".join(_short_openalex_id(work_id, "W") for work_id in work_ids)
        return self._request_json(
            "/works", {"filter": "ids.openalex:" + ids, "select": VERSION_FIELDS, "per_page": "100"},
            progress_callback=progress_callback,
            activity=ActivityUpdate(kind=ActivityKind.WORKING, source="openalex",
                                    operation="version_hydration", label="Hydrating OpenAlex versions", unit="work"),
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
            "User-Agent": "literature-monitor/0.4.4",
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


def _resolve_journal_source(
    client: OpenAlexClient,
    journal: JournalConfig,
    *,
    progress_callback: ProgressCallback | None = None,
    operation: str | None = None,
) -> tuple[
    ResolvedSource | None,
    tuple[DiscoveryIssue, ...],
    CoverageStatus | None,
]:
    hits: list[_SourceHit] = []
    unresolved: list[str] = []
    request_failures: list[tuple[str, str]] = []
    source_validation_failures: list[tuple[str, str]] = []
    issues: list[DiscoveryIssue] = []
    operation = operation or f"source_resolution:{_normalized_journal_name(journal.name)}"
    total_issns = len(journal.issn)

    for issn_index, issn in enumerate(journal.issn):
        activity = ActivityUpdate(
            kind=ActivityKind.WORKING,
            source="openalex",
            operation=operation,
            label="Resolving OpenAlex journal source",
            detail=f"{journal.name} · ISSN {issn}",
            current=issn_index,
            total=total_issns,
            unit="issn",
        )
        _report_activity(progress_callback, activity)
        try:
            if progress_callback is None:
                payload = client.get_source_by_issn(issn)
            else:
                payload = client.get_source_by_issn(
                    issn,
                    progress_callback=progress_callback,
                    activity=activity,
                )
        except OpenAlexNotFoundError:
            unresolved.append(issn)
        except OpenAlexRequestError as error:
            request_failures.append((issn, str(error)))
        else:
            try:
                hits.append(_parse_source(payload, issn))
            except OpenAlexRecordError as error:
                source_validation_failures.append((issn, str(error)))
        _report_activity(
            progress_callback,
            replace(
                activity,
                label="Checked OpenAlex ISSN",
                current=issn_index + 1,
            ),
        )

    if journal.issn:
        _report_activity(
            progress_callback,
            ActivityUpdate(
                kind=ActivityKind.WORKING,
                source="openalex",
                operation=operation,
                label="Completed OpenAlex source resolution",
                detail=journal.name,
                current=total_issns,
                total=total_issns,
                unit="issn",
            ),
        )

    return _finish_journal_source(journal, hits, unresolved, request_failures, source_validation_failures)


def _finish_journal_source(
    journal: JournalConfig, hits: list[_SourceHit], unresolved: list[str],
    request_failures: list[tuple[str, str]], source_validation_failures: list[tuple[str, str]],
) -> tuple[ResolvedSource | None, tuple[DiscoveryIssue, ...], CoverageStatus | None]:
    issues: list[DiscoveryIssue] = []
    if not hits:
        details: list[str] = []
        if unresolved:
            details.append(f"unresolved ISSNs: {', '.join(unresolved)}")
        if request_failures:
            failures = "; ".join(
                f"{issn}: {message}" for issn, message in request_failures
            )
            details.append(f"incomplete ISSN verification: {failures}")
        if source_validation_failures:
            failures = "; ".join(
                f"{issn}: {message}" for issn, message in source_validation_failures
            )
            details.append(f"Source validation failures: {failures}")
        status = (
            CoverageStatus.UNAVAILABLE
            if unresolved
            and not request_failures
            and not source_validation_failures
            else CoverageStatus.FAILED
        )
        issues.append(
            DiscoveryIssue(
                severity=(IssueSeverity.WARNING if status is CoverageStatus.UNAVAILABLE
                          else IssueSeverity.ERROR),
                stage="source_resolution",
                journal=journal.name,
                message=(
                    "no configured ISSN resolved in OpenAlex"
                    + (f" ({'; '.join(details)})" if details else "")
                ),
            )
        )
        return None, tuple(issues), status

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
    for issn, message in request_failures:
        issues.append(
            DiscoveryIssue(
                severity=IssueSeverity.ERROR,
                stage="source_resolution",
                journal=journal.name,
                issn=issn,
                message=f"incomplete ISSN verification after remote/API failure: {message}",
            )
        )
    for issn, message in source_validation_failures:
        issues.append(
            DiscoveryIssue(
                severity=IssueSeverity.ERROR,
                stage="source_resolution",
                journal=journal.name,
                issn=issn,
                message=f"resolved Source failed validation: {message}",
            )
        )

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
        return None, tuple(issues), CoverageStatus.FAILED

    if request_failures or source_validation_failures:
        return None, tuple(issues), CoverageStatus.FAILED

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
        return None, tuple(issues), CoverageStatus.FAILED

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
        None,
    )


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


def parse_openalex_updated_date(value: object) -> datetime | None:
    """OpenAlex defines its raw updated_date as UTC even when the offset is omitted."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("OpenAlex updated_date must be an ISO timestamp string")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?", value):
        value += "Z"
    return parse_revision_timestamp(value)


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


def _location_identity(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    identifier = value.strip()
    if identifier.casefold().startswith("doi:"):
        try:
            doi = normalize_doi(identifier[4:])
        except ValueError:
            return None
        return ("doi", doi) if doi is not None else None
    arxiv = _ARXIV_LOCATION_ID.fullmatch(identifier)
    if arxiv is not None and arxiv.group(1).strip():
        return "arxiv", arxiv.group(1).strip()
    return "openalex_location", identifier


def _location_url(location: dict[str, Any]) -> tuple[str | None, bool]:
    invalid = False
    for field in ("landing_page_url", "pdf_url"):
        value = location.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            invalid = True
            continue
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            invalid = True
            continue
        return value.strip(), invalid
    return None, invalid


def _normalize_version_hints(
    value: Any,
) -> tuple[tuple[OpenAlexVersionHint, ...], tuple[str, ...]]:
    if value is None:
        return (), ()
    if not isinstance(value, list):
        return (), ("invalid locations was ignored",)
    hints: dict[tuple[str, str, OpenAlexVersion], OpenAlexVersionHint] = {}
    warnings: list[str] = []
    for index, location in enumerate(value):
        if not isinstance(location, dict):
            warnings.append(f"ignored malformed location at index {index}")
            continue
        try:
            version = OpenAlexVersion(location.get("version"))
        except ValueError:
            warnings.append(
                f"ignored location at index {index} without a recognized version"
            )
            continue
        identity = _location_identity(location.get("id"))
        if identity is None:
            warnings.append(
                f"ignored location at index {index} without a stable identity"
            )
            continue
        url, invalid_url = _location_url(location)
        if invalid_url:
            warnings.append(f"ignored invalid location URL at index {index}")
        hint = OpenAlexVersionHint(
            source=identity[0],
            identifier=identity[1],
            version=version,
            url=url,
        )
        key = (hint.source, hint.identifier, hint.version)
        current = hints.get(key)
        if current is None or (current.url is None and hint.url is not None):
            hints[key] = hint
    return tuple(hints[key] for key in sorted(hints)), tuple(warnings)


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
    *, thin: bool = False,
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

    warnings: list[str] = []
    try:
        updated_at = parse_openalex_updated_date(payload.get("updated_date"))
    except ValueError:
        updated_at = None
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
    version_hints, location_warnings = ((), ()) if thin else _normalize_version_hints(payload.get("locations"))
    warnings.extend(location_warnings)

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
            version_hints=version_hints,
            updated_at=updated_at,
        ),
        tuple(warnings),
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
                journal=journal.name,
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
                journal=journal.name,
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
                journal=journal.name,
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


def resolve_journal_sources_batched(
    client: OpenAlexClient, journals: Sequence[JournalConfig], *,
    progress_callback: ProgressCallback | None = None,
) -> tuple[SourceResolutionUnit, ...]:
    """Resolve the complete configured sequence without changing legacy entrypoints."""
    issns = tuple(dict.fromkeys(issn for journal in journals for issn in journal.issn))
    outcomes: dict[str, _SourceHit | OpenAlexError] = {}
    for offset in range(0, len(issns), _OPENALEX_BATCH_SIZE):
        batch = issns[offset:offset + _OPENALEX_BATCH_SIZE]
        candidates: dict[str, list[_SourceHit]] = {issn: [] for issn in batch}
        try:
            payload = client.get_sources_by_issns(batch, progress_callback=progress_callback)
            results = payload.get("results")
            if not isinstance(results, list):
                raise OpenAlexRequestError("OpenAlex Sources response lacks results")
            count = _progress_total(payload.get("meta", {}).get("count")) if isinstance(payload.get("meta"), dict) else None
            if count is not None and count != len(results):
                raise OpenAlexRequestError("incomplete OpenAlex Source batch")
            unsafe: set[str] = set()
            for raw in results:
                if not isinstance(raw, dict) or not isinstance(raw.get("issn"), list):
                    continue
                for issn in batch:
                    if issn not in raw["issn"]:
                        continue
                    try:
                        candidates[issn].append(_parse_source(raw, issn))
                    except OpenAlexRecordError:
                        unsafe.add(issn)
            for issn, hits in candidates.items():
                if issn not in unsafe and hits and len({hit.openalex_id for hit in hits}) == 1:
                    outcomes[issn] = hits[0]
        except OpenAlexRequestError as error:
            if error.terminal:
                outcomes.update((issn, error) for issn in batch)
        for issn in batch:
            if issn in outcomes:
                continue
            try:
                raw = client.get_source_by_issn(issn, progress_callback=progress_callback)
                outcomes[issn] = _parse_source(raw, issn)
            except OpenAlexError as error:
                outcomes[issn] = error
                if isinstance(error, OpenAlexRequestError) and error.terminal:
                    outcomes.update((remaining, error) for remaining in batch if remaining not in outcomes)
                    break
    units = []
    for journal in journals:
        hits, unresolved, requests, validation = [], [], [], []
        for issn in journal.issn:
            outcome = outcomes[issn]
            if isinstance(outcome, _SourceHit):
                if _journal_name_matches(journal.name, outcome):
                    hits.append(outcome)
                else:
                    validation.append((issn, f"configured journal name does not match OpenAlex source {outcome.display_name!r}"))
            elif isinstance(outcome, OpenAlexNotFoundError):
                unresolved.append(issn)
            elif isinstance(outcome, OpenAlexRecordError):
                validation.append((issn, str(outcome)))
            else:
                requests.append((issn, str(outcome)))
        source, issues, status = _finish_journal_source(journal, hits, unresolved, requests, validation)
        units.append(SourceResolutionUnit(journal, source, issues, status))
    return tuple(units)


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
                    record, warnings = _normalize_work(raw, sources[source_id], timestamp, thin=True)
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


def discover_journals_batched(
    client: OpenAlexClient, journals: Sequence[JournalConfig], from_date: date, to_date: date, *,
    retrieved_at: datetime | None = None, progress_callback: ProgressCallback | None = None,
) -> DiscoveryResult:
    """v0.4.3 live thin discovery, still separate from production Run/validate."""
    if from_date > to_date:
        raise ValueError("from_date must not be after to_date")
    timestamp = retrieved_at or datetime.now(timezone.utc)
    if timestamp.utcoffset() is None:
        raise ValueError("retrieved_at must include a timezone")
    timestamp = timestamp.astimezone(timezone.utc)
    resolutions = resolve_journal_sources_batched(client, journals, progress_callback=progress_callback)
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
            CoverageUnit("openalex", CoverageComponent.OPENALEX_DISCOVERY, status, journal=resolution.journal.name),
            source, records, (*resolution.issues, *issues),
        ))
    return DiscoveryResult(
        tuple(unit.source for unit in units if unit.source),
        tuple(record for unit in units for record in unit.records),
        tuple(issue for unit in units for issue in unit.issues),
        tuple(unit.coverage for unit in units), tuple(units),
    )


@dataclass(frozen=True)
class OpenAlexVersionHydration:
    work_id: str
    version_hints: tuple[OpenAlexVersionHint, ...]
    issues: tuple[DiscoveryIssue, ...] = ()
    succeeded: bool = True


def hydrate_work_versions(
    client: OpenAlexClient, work_ids: Sequence[str], *, progress_callback: ProgressCallback | None = None,
) -> tuple[OpenAlexVersionHydration, ...]:
    """Locations only; no membership, retention decision, coverage, or durable access."""
    ids = tuple(dict.fromkeys(_canonical_openalex_id(work_id, "W") for work_id in work_ids))

    def fetch(batch: tuple[str, ...]) -> dict[str, OpenAlexVersionHydration]:
        mapped: dict[str, OpenAlexVersionHydration] = {}
        terminal = False
        failure = "missing or malformed OpenAlex location record"
        try:
            payload = client.get_work_locations(batch, progress_callback=progress_callback)
            results = payload.get("results")
            if not isinstance(results, list):
                raise OpenAlexRequestError("OpenAlex version response lacks results")
            duplicates: set[str] = set()
            seen_ids: set[str] = set()
            for raw in results:
                try:
                    work_id = _canonical_openalex_id(raw.get("id"), "W") if isinstance(raw, dict) else None
                except OpenAlexRecordError:
                    continue
                if work_id not in batch:
                    continue
                if work_id in seen_ids:
                    duplicates.add(work_id)
                seen_ids.add(work_id)
                if not isinstance(raw.get("locations"), list):
                    continue
                try:
                    hints, warnings = _normalize_version_hints(raw["locations"])
                except (ValueError, ValidationError):
                    continue
                mapped[work_id] = OpenAlexVersionHydration(work_id, hints, tuple(
                    DiscoveryIssue(IssueSeverity.WARNING, "version_hydration", "", warning, record_id=work_id)
                    for warning in warnings
                ))
            for work_id in duplicates:
                mapped.pop(work_id, None)
        except OpenAlexError as error:
            terminal = isinstance(error, OpenAlexRequestError) and error.terminal
            failure = str(error)
        missing = tuple(work_id for work_id in batch if work_id not in mapped)
        if missing and len(batch) > 1 and not terminal:
            midpoint = max(1, len(missing) // 2)
            for subset in (missing[:midpoint], missing[midpoint:]):
                if subset:
                    mapped.update(fetch(subset))
        for work_id in missing:
            if work_id not in mapped:
                mapped[work_id] = OpenAlexVersionHydration(work_id, (), (
                    DiscoveryIssue(IssueSeverity.WARNING, "version_hydration", "", failure, record_id=work_id),
                ), False)
        return mapped

    results = {}
    for offset in range(0, len(ids), _OPENALEX_BATCH_SIZE):
        results.update(fetch(ids[offset:offset + _OPENALEX_BATCH_SIZE]))
    return tuple(results[work_id] for work_id in ids)
