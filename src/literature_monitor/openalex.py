"""OpenAlex venue-first discovery."""

from __future__ import annotations

import json
import re
import socket
import time
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from pydantic import ValidationError

from literature_monitor.config import JournalConfig
from literature_monitor.identifiers import normalize_doi
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
WORK_FIELDS = (
    "id,doi,title,publication_date,abstract_inverted_index,authorships,"
    "primary_location,locations"
)
_OPENALEX_ID_PATTERN = re.compile(
    r"^(?:https://openalex\.org/)?([SAW]\d+)$", re.IGNORECASE
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


class OpenAlexWorkRecord(DomainModel):
    """Provider record that deliberately has no canonical UUID or workflow state."""

    metadata: CanonicalMetadata
    external_ids: ExternalIds
    authors: tuple[Author, ...]
    source_id: NonEmptyStr
    provenance: MetadataSource
    version_hints: tuple[OpenAlexVersionHint, ...] = ()

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
        cursor: str | None = "*"
        seen_cursors: set[str] = set()
        short_source_id = _short_openalex_id(source_id, "S")
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
                    "select": WORK_FIELDS,
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
        url = f"{self.base_url}{path}?{urlencode(params)}"
        headers = {
            "Accept": "application/json",
            "User-Agent": "literature-monitor/0.4.1",
        }
        if self.api_key is not None:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(url, headers=headers)

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
                with self._opener(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read())
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
            except HTTPError as error:
                if error.code == 404 and not_found_message is not None:
                    raise OpenAlexNotFoundError(not_found_message) from error
                if (error.code == 429 or error.code >= 500) and attempt < 2:
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
                                    f"{activity.detail} · HTTP {error.code} · "
                                    f"backoff {delay}s"
                                    if activity.detail
                                    else f"HTTP {error.code} · backoff {delay}s"
                                ),
                            ),
                        )
                    self._sleep(delay)
                    continue
                raise OpenAlexRequestError(
                    f"OpenAlex request failed with HTTP {error.code}"
                ) from error
            except (TimeoutError, socket.timeout, URLError) as error:
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


def resolve_journal_source(
    client: OpenAlexClient,
    journal: JournalConfig,
    *,
    progress_callback: ProgressCallback | None = None,
    operation: str | None = None,
) -> tuple[ResolvedSource | None, tuple[DiscoveryIssue, ...]]:
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
        issues.append(
            DiscoveryIssue(
                severity=IssueSeverity.ERROR,
                stage="source_resolution",
                journal=journal.name,
                message=(
                    "no configured ISSN resolved in OpenAlex"
                    + (f" ({'; '.join(details)})" if details else "")
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
    for issn, message in request_failures:
        issues.append(
            DiscoveryIssue(
                severity=IssueSeverity.WARNING,
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
        return None, tuple(issues)

    if source_validation_failures:
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
        doi = normalize_doi(payload.get("doi"))
    except ValueError:
        doi = None
        warnings.append("invalid DOI was treated as missing")
    try:
        abstract = _reconstruct_abstract(payload.get("abstract_inverted_index"))
    except ValueError:
        abstract = None
        warnings.append("invalid abstract_inverted_index was treated as missing")
    version_hints, location_warnings = _normalize_version_hints(
        payload.get("locations")
    )
    warnings.extend(location_warnings)

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
            version_hints=version_hints,
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
    progress_callback: ProgressCallback | None = None,
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
        source, resolution_issues = resolve_journal_source(
            client,
            journal,
            progress_callback=progress_callback,
            operation=f"source_resolution:{journal_index}",
        )
        issues.extend(resolution_issues)
        if source is None:
            continue
        sources.append(source)
        activity = ActivityUpdate(
            kind=ActivityKind.WORKING,
            source="openalex",
            operation=f"works_discovery:{journal_index}",
            label="Discovering OpenAlex works",
            detail=journal.name,
            current=0,
            total=None,
            unit="work",
        )
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
                for payload in page["results"]:
                    record_id = payload.get("id") if isinstance(payload, dict) else None
                    try:
                        record, warnings = _normalize_work(payload, source, timestamp)
                    except (OpenAlexError, ValidationError) as error:
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
