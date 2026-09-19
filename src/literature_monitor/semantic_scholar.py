"""Semantic Scholar provider adapter and bounded R3 retrieval."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from httpx import HTTPError
from pydantic import ValidationError
from semanticscholar import SemanticScholar
from semanticscholar.SemanticScholarException import SemanticScholarException
from tenacity import RetryError

from literature_monitor.config import JournalConfig
from literature_monitor.identifiers import normalize_doi
from literature_monitor.keywords import KeywordExpression, broad_positive_query
from literature_monitor.models import (
    Author,
    DomainModel,
    EvidenceDate,
    EvidenceDateKind,
    ExternalIds,
    MetadataSource,
    NonEmptyStr,
    ProviderRecordRef,
    ProviderTopic,
    ProviderWorkEvidence,
)

SEMANTIC_SCHOLAR_BATCH_SIZE = 500
SEMANTIC_SCHOLAR_FIELDS = (
    "paperId",
    "corpusId",
    "externalIds",
    "title",
    "abstract",
    "authors",
    "year",
    "publicationDate",
    "journal",
    "publicationVenue",
    "venue",
    "fieldsOfStudy",
    "s2FieldsOfStudy",
)
_ISSN_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{3}[0-9X]$")
_ORCID_PATTERN = re.compile(
    r"^(?:https?://orcid\.org/)?\d{4}-\d{4}-\d{4}-\d{3}[\dX]/?$",
    re.IGNORECASE,
)
_EXTERNAL_ID_NAMES = {
    "arxiv": "arxiv",
    "corpusid": "corpus_id",
    "doi": "doi",
    "acl": "acl",
    "mag": "mag",
    "pmid": "pmid",
    "pubmed": "pmid",
    "pmcid": "pmcid",
    "pubmedcentral": "pmcid",
}


class SemanticScholarIssueSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class SemanticScholarIssue:
    severity: SemanticScholarIssueSeverity
    stage: str
    message: str
    journal: str | None = None
    paper_id: str | None = None
    doi: str | None = None


class SemanticScholarRecordError(RuntimeError):
    """A Semantic Scholar record cannot be normalized safely."""


class _SemanticScholarResponseError(RuntimeError):
    """A provider response violates the client contract."""


_PROVIDER_FAILURES = (
    SemanticScholarException,
    HTTPError,
    RetryError,
    ConnectionError,
    TimeoutError,
    PermissionError,
    _SemanticScholarResponseError,
)


class SemanticScholarWorkRecord(DomainModel):
    paper_id: NonEmptyStr
    corpus_id: NonEmptyStr | None = None
    external_ids: ExternalIds
    title: NonEmptyStr | None = None
    abstract: str | None = None
    authors: tuple[Author, ...] = ()
    publication_date: date | None = None
    publication_year: int | None = None
    journal: NonEmptyStr | None = None
    publication_venue_issns: tuple[NonEmptyStr, ...] = ()
    venue_names: tuple[NonEmptyStr, ...] = ()
    fields_of_study: tuple[NonEmptyStr, ...] = ()
    provider_topics: tuple[ProviderTopic, ...] = ()
    provenance: MetadataSource

    @property
    def doi(self) -> str | None:
        return self.external_ids.doi

    def to_evidence(
        self,
        *,
        supplements: tuple[ProviderRecordRef, ...] = (),
    ) -> ProviderWorkEvidence:
        dates = (
            (
                EvidenceDate(
                    kind=EvidenceDateKind.PUBLISHED,
                    year=self.publication_date.year,
                    month=self.publication_date.month,
                    day=self.publication_date.day,
                ),
            )
            if self.publication_date is not None
            else ()
        )
        return ProviderWorkEvidence(
            provenance=self.provenance,
            title=self.title,
            journal=self.journal,
            publication_date=self.publication_date,
            abstract=self.abstract,
            provider_topics=self.provider_topics,
            fields_of_study=self.fields_of_study,
            authors=self.authors,
            external_ids=self.external_ids,
            dates=dates,
            supplements=supplements,
        )


@dataclass(frozen=True)
class SemanticScholarRetrievalResult:
    evidence: tuple[ProviderWorkEvidence, ...]
    supplement_records: tuple[SemanticScholarWorkRecord, ...]
    discovered_records: tuple[SemanticScholarWorkRecord, ...]
    issues: tuple[SemanticScholarIssue, ...]

    @property
    def has_errors(self) -> bool:
        return any(
            issue.severity is SemanticScholarIssueSeverity.ERROR
            for issue in self.issues
        )


def create_semantic_scholar_client(api_key: str | None = None) -> SemanticScholar:
    normalized_key = api_key.strip() if api_key and api_key.strip() else None
    return SemanticScholar(api_key=normalized_key, retry=True)


def _raw_data(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    raw = getattr(value, "raw_data", None)
    if isinstance(raw, dict):
        return raw
    raise SemanticScholarRecordError("Semantic Scholar paper lacks raw_data")


def _optional_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _normalize_orcid(value: Any) -> str | None:
    candidate = _optional_string(value)
    if candidate is None or not _ORCID_PATTERN.fullmatch(candidate):
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


def _normalize_authors(value: Any, warnings: list[str]) -> tuple[Author, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        warnings.append("invalid author list was treated as missing")
        return ()
    authors: list[Author] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            warnings.append(f"invalid author entry {index} was ignored")
            continue
        name = _optional_string(item.get("name"))
        if name is None:
            warnings.append(f"author entry {index} without a usable name was ignored")
            continue
        external_ids = item.get("externalIds")
        raw_orcid = None
        if isinstance(external_ids, dict):
            raw_orcid = next(
                (
                    identifier
                    for key, identifier in external_ids.items()
                    if isinstance(key, str) and key.casefold() == "orcid"
                ),
                None,
            )
        orcid = _normalize_orcid(raw_orcid)
        if raw_orcid is not None and orcid is None:
            warnings.append(f"invalid ORCID for author entry {index} was ignored")
        authors.append(Author(name=name, orcid=orcid))
    return tuple(authors)


def _normalize_issn(value: Any) -> str | None:
    candidate = _optional_string(value)
    if candidate is None:
        return None
    candidate = candidate.upper()
    if not _ISSN_PATTERN.fullmatch(candidate):
        return None
    digits = candidate.replace("-", "")
    values = [10 if character == "X" else int(character) for character in digits]
    checksum = sum(
        number * weight
        for number, weight in zip(values, range(8, 0, -1), strict=True)
    )
    return candidate if checksum % 11 == 0 else None


def _normalize_external_ids(
    data: dict[str, Any],
    paper_id: str,
    warnings: list[str],
) -> tuple[ExternalIds, str | None]:
    values: dict[str, str] = {"semantic_scholar": paper_id}
    raw_ids = data.get("externalIds")
    if raw_ids is not None and not isinstance(raw_ids, dict):
        warnings.append("invalid externalIds mapping was treated as missing")
        raw_ids = {}
    for raw_namespace, raw_identifier in (raw_ids or {}).items():
        if not isinstance(raw_namespace, str):
            warnings.append("external identifier with invalid namespace was ignored")
            continue
        if isinstance(raw_identifier, bool) or not isinstance(
            raw_identifier, (str, int)
        ):
            warnings.append(
                f"invalid {raw_namespace} external identifier was ignored"
            )
            continue
        identifier = str(raw_identifier).strip()
        if not identifier:
            warnings.append(
                f"empty {raw_namespace} external identifier was ignored"
            )
            continue
        compact_namespace = re.sub(r"[^a-z0-9]", "", raw_namespace.casefold())
        namespace = _EXTERNAL_ID_NAMES.get(compact_namespace)
        if namespace is None:
            namespace = re.sub(
                r"[^a-z0-9]+", "_", raw_namespace.casefold()
            ).strip("_")
        if not namespace:
            warnings.append("external identifier with invalid namespace was ignored")
            continue
        if namespace == "doi":
            try:
                normalized_doi = normalize_doi(identifier)
            except ValueError:
                normalized_doi = None
            if normalized_doi is None:
                warnings.append("invalid DOI external identifier was ignored")
                continue
            identifier = normalized_doi
        values.setdefault(namespace, identifier)

    corpus_value = data.get("corpusId")
    corpus_id = values.get("corpus_id")
    if isinstance(corpus_value, int) and not isinstance(corpus_value, bool):
        corpus_id = str(corpus_value)
    elif isinstance(corpus_value, str) and corpus_value.strip():
        corpus_id = corpus_value.strip()
    elif corpus_value is not None:
        warnings.append("invalid corpusId was ignored")
    if corpus_id is not None:
        if (
            "corpus_id" in values
            and values["corpus_id"] != corpus_id
        ):
            warnings.append(
                "conflicting corpusId values were resolved to the top-level value"
            )
        values["corpus_id"] = corpus_id
    return ExternalIds.model_validate(values), corpus_id


def _normalize_publication_date(
    value: Any,
    warnings: list[str],
) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        warnings.append("invalid publicationDate was treated as missing")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        warnings.append("invalid publicationDate was treated as missing")
        return None


def _normalize_year(value: Any, warnings: list[str]) -> int | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= 9999
    ):
        warnings.append("invalid publication year was treated as missing")
        return None
    return value


def _normalize_string_list(
    value: Any,
    field: str,
    warnings: list[str],
) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        warnings.append(f"invalid {field} list was treated as missing")
        return ()
    normalized: set[str] = set()
    for index, item in enumerate(value):
        text = _optional_string(item)
        if text is None:
            warnings.append(f"invalid {field} entry {index} was ignored")
            continue
        normalized.add(text)
    return tuple(sorted(normalized, key=lambda item: (item.casefold(), item)))


def _normalize_venue(
    data: dict[str, Any],
    warnings: list[str],
) -> tuple[str | None, tuple[str, ...], tuple[str, ...]]:
    venue_names: set[str] = set()
    issns: set[str] = set()
    publication_venue = data.get("publicationVenue")
    if publication_venue is not None and not isinstance(publication_venue, dict):
        warnings.append("invalid publicationVenue was treated as missing")
        publication_venue = None
    if isinstance(publication_venue, dict):
        if name := _optional_string(publication_venue.get("name")):
            venue_names.add(name)
        alternate_names = publication_venue.get(
            "alternate_names",
            publication_venue.get("alternateNames"),
        )
        venue_names.update(
            _normalize_string_list(
                alternate_names,
                "publicationVenue alternate name",
                warnings,
            )
        )
        raw_issns = publication_venue.get("issn")
        candidates = raw_issns if isinstance(raw_issns, list) else [raw_issns]
        for index, candidate in enumerate(candidates):
            if candidate is None:
                continue
            issn = _normalize_issn(candidate)
            if issn is None:
                warnings.append(
                    f"invalid publicationVenue ISSN entry {index} was ignored"
                )
                continue
            issns.add(issn)

    journal = data.get("journal")
    if journal is not None and not isinstance(journal, dict):
        warnings.append("invalid journal object was treated as missing")
        journal = None
    journal_name = (
        _optional_string(journal.get("name"))
        if isinstance(journal, dict)
        else None
    )
    if journal_name is not None:
        venue_names.add(journal_name)
    if venue := _optional_string(data.get("venue")):
        venue_names.add(venue)

    ordered_names = tuple(
        sorted(venue_names, key=lambda item: (item.casefold(), item))
    )
    preferred_name = None
    if isinstance(publication_venue, dict):
        preferred_name = _optional_string(publication_venue.get("name"))
    preferred_name = (
        preferred_name or journal_name or _optional_string(data.get("venue"))
    )
    return preferred_name, tuple(sorted(issns)), ordered_names


def _normalize_topics(
    value: Any,
    warnings: list[str],
) -> tuple[ProviderTopic, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        warnings.append("invalid s2FieldsOfStudy list was treated as missing")
        return ()
    topics: set[ProviderTopic] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            warnings.append(f"invalid s2FieldsOfStudy entry {index} was ignored")
            continue
        category = _optional_string(item.get("category"))
        if category is None:
            warnings.append(f"s2FieldsOfStudy entry {index} without category was ignored")
            continue
        topics.add(
            ProviderTopic(
                value=category,
                source=_optional_string(item.get("source")),
            )
        )
    return tuple(
        sorted(
            topics,
            key=lambda item: (item.value.casefold(), item.source or ""),
        )
    )


def normalize_semantic_scholar_work(
    paper: object,
    retrieved_at: datetime,
) -> tuple[SemanticScholarWorkRecord, tuple[str, ...]]:
    """Normalize one Paper.raw_data payload behind the provider boundary."""

    if retrieved_at.utcoffset() is None:
        raise ValueError("retrieved_at must include a timezone")
    data = _raw_data(paper)
    paper_id = _optional_string(data.get("paperId"))
    if paper_id is None:
        raise SemanticScholarRecordError("Semantic Scholar paper lacks paperId")
    warnings: list[str] = []
    try:
        external_ids, corpus_id = _normalize_external_ids(data, paper_id, warnings)
        journal, venue_issns, venue_names = _normalize_venue(data, warnings)
        record = SemanticScholarWorkRecord(
            paper_id=paper_id,
            corpus_id=corpus_id,
            external_ids=external_ids,
            title=_optional_string(data.get("title")),
            abstract=_optional_string(data.get("abstract")),
            authors=_normalize_authors(data.get("authors"), warnings),
            publication_date=_normalize_publication_date(
                data.get("publicationDate"), warnings
            ),
            publication_year=_normalize_year(data.get("year"), warnings),
            journal=journal,
            publication_venue_issns=venue_issns,
            venue_names=venue_names,
            fields_of_study=_normalize_string_list(
                data.get("fieldsOfStudy"), "fieldsOfStudy", warnings
            ),
            provider_topics=_normalize_topics(
                data.get("s2FieldsOfStudy"), warnings
            ),
            provenance=MetadataSource(
                provider="semantic_scholar",
                record_id=paper_id,
                retrieved_at=retrieved_at.astimezone(timezone.utc),
            ),
        )
    except ValidationError as error:
        raise SemanticScholarRecordError(
            f"Semantic Scholar provider model validation failed: {error}"
        ) from error
    return record, tuple(warnings)


def _record_ref(record: ProviderWorkEvidence) -> ProviderRecordRef:
    return ProviderRecordRef(
        provider=record.provenance.provider,
        record_id=record.provenance.record_id,
    )


def _record_refs(
    records: Sequence[ProviderWorkEvidence],
) -> tuple[ProviderRecordRef, ...]:
    references = {
        (
            record.provenance.provider.casefold(),
            record.provenance.record_id,
        ): _record_ref(record)
        for record in records
    }
    return tuple(references[key] for key in sorted(references))


def _normalize_not_found_doi(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = (
        value.split(":", 1)[1]
        if value.casefold().startswith("doi:")
        else value
    )
    try:
        return normalize_doi(candidate)
    except ValueError:
        return None


def supplement_semantic_scholar_dois(
    client: Any,
    base_evidence: Sequence[ProviderWorkEvidence],
    *,
    retrieved_at: datetime,
) -> SemanticScholarRetrievalResult:
    by_doi: dict[str, list[ProviderWorkEvidence]] = defaultdict(list)
    for evidence in base_evidence:
        try:
            doi = normalize_doi(evidence.external_ids.doi)
        except ValueError:
            doi = None
        if doi is not None:
            by_doi[doi].append(evidence)

    records: list[SemanticScholarWorkRecord] = []
    evidence_additions: list[ProviderWorkEvidence] = []
    issues: list[SemanticScholarIssue] = []
    dois = sorted(by_doi)
    for start in range(0, len(dois), SEMANTIC_SCHOLAR_BATCH_SIZE):
        chunk = dois[start : start + SEMANTIC_SCHOLAR_BATCH_SIZE]
        requested = [f"DOI:{doi}" for doi in chunk]
        try:
            response = client.get_papers(
                requested,
                fields=list(SEMANTIC_SCHOLAR_FIELDS),
                return_not_found=True,
            )
            if not isinstance(response, tuple) or len(response) != 2:
                raise _SemanticScholarResponseError(
                    "batch response lacks papers/not-found tuple"
                )
            papers, not_found = response
        except _PROVIDER_FAILURES as error:
            issues.append(
                SemanticScholarIssue(
                    severity=SemanticScholarIssueSeverity.ERROR,
                    stage="batch_lookup",
                    message=f"Semantic Scholar batch lookup failed: {error}",
                )
            )
            continue

        for missing in not_found:
            issues.append(
                SemanticScholarIssue(
                    severity=SemanticScholarIssueSeverity.WARNING,
                    stage="not_found",
                    doi=_normalize_not_found_doi(missing),
                    message=f"Semantic Scholar did not find {missing}",
                )
            )
        for paper in papers:
            try:
                record, warnings = normalize_semantic_scholar_work(
                    paper, retrieved_at
                )
            except SemanticScholarRecordError as error:
                issues.append(
                    SemanticScholarIssue(
                        severity=SemanticScholarIssueSeverity.WARNING,
                        stage="record_normalization",
                        message=str(error),
                    )
                )
                continue
            if record.doi is None or record.doi not in chunk:
                issues.append(
                    SemanticScholarIssue(
                        severity=SemanticScholarIssueSeverity.WARNING,
                        stage="record_normalization",
                        paper_id=record.paper_id,
                        doi=record.doi,
                        message="returned DOI does not match this requested batch",
                    )
                )
                continue
            issues.extend(
                SemanticScholarIssue(
                    severity=SemanticScholarIssueSeverity.WARNING,
                    stage="field_normalization",
                    paper_id=record.paper_id,
                    doi=record.doi,
                    message=warning,
                )
                for warning in warnings
            )
            anchors = _record_refs(by_doi[record.doi])
            records.append(record)
            evidence_additions.append(record.to_evidence(supplements=anchors))

    return SemanticScholarRetrievalResult(
        evidence=tuple(evidence_additions),
        supplement_records=tuple(records),
        discovered_records=(),
        issues=tuple(issues),
    )


def _normalize_journal_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _date_matches(
    record: SemanticScholarWorkRecord,
    from_date: date,
    to_date: date,
) -> bool:
    if record.publication_date is not None:
        return from_date <= record.publication_date <= to_date
    if record.publication_year is None:
        return False
    return (
        from_date <= date(record.publication_year, 1, 1)
        and date(record.publication_year, 12, 31) <= to_date
    )


def _venue_matches(
    record: SemanticScholarWorkRecord,
    journal: JournalConfig,
    journals: Sequence[JournalConfig],
) -> bool:
    if record.publication_venue_issns:
        return bool(set(record.publication_venue_issns) & set(journal.issn))
    provider_names = {
        _normalize_journal_name(name) for name in record.venue_names
    }
    matching_journals = {
        candidate.name
        for candidate in journals
        if _normalize_journal_name(candidate.name) in provider_names
    }
    return matching_journals == {journal.name}


def discover_semantic_scholar_journals(
    client: Any,
    journals: Sequence[JournalConfig],
    from_date: date,
    to_date: date,
    expression: KeywordExpression,
    *,
    retrieved_at: datetime,
) -> SemanticScholarRetrievalResult:
    if from_date > to_date:
        raise ValueError("from_date must not be after to_date")
    query = broad_positive_query(expression)
    if query is None:
        return SemanticScholarRetrievalResult(
            evidence=(),
            supplement_records=(),
            discovered_records=(),
            issues=(
                SemanticScholarIssue(
                    severity=SemanticScholarIssueSeverity.WARNING,
                    stage="no_positive_query",
                    message=(
                        "local expression has no positive branch; Semantic Scholar "
                        "supplemental discovery was skipped"
                    ),
                ),
            ),
        )

    records: list[SemanticScholarWorkRecord] = []
    evidence: list[ProviderWorkEvidence] = []
    issues: list[SemanticScholarIssue] = []
    date_filter = f"{from_date.isoformat()}:{to_date.isoformat()}"
    for journal in journals:
        if "," in journal.name:
            issues.append(
                SemanticScholarIssue(
                    severity=SemanticScholarIssueSeverity.WARNING,
                    stage="unsupported_venue_filter",
                    journal=journal.name,
                    message=(
                        "supplemental discovery was skipped because the client "
                        "cannot safely represent this venue name"
                    ),
                )
            )
            continue
        try:
            search_options: dict[str, object] = {
                "venue": [journal.name],
                "fields": list(SEMANTIC_SCHOLAR_FIELDS),
                "publication_date_or_year": date_filter,
                "bulk": True,
                "sort": "paperId:asc",
            }
            results = client.search_paper(
                query,
                **search_options,
            )
            for paper in results:
                try:
                    record, warnings = normalize_semantic_scholar_work(
                        paper, retrieved_at
                    )
                except SemanticScholarRecordError as error:
                    issues.append(
                        SemanticScholarIssue(
                            severity=SemanticScholarIssueSeverity.WARNING,
                            stage="record_normalization",
                            journal=journal.name,
                            message=str(error),
                        )
                    )
                    continue
                issues.extend(
                    SemanticScholarIssue(
                        severity=SemanticScholarIssueSeverity.WARNING,
                        stage="field_normalization",
                        journal=journal.name,
                        paper_id=record.paper_id,
                        doi=record.doi,
                        message=warning,
                    )
                    for warning in warnings
                )
                if not _date_matches(record, from_date, to_date):
                    issues.append(
                        SemanticScholarIssue(
                            severity=SemanticScholarIssueSeverity.WARNING,
                            stage="date_validation",
                            journal=journal.name,
                            paper_id=record.paper_id,
                            doi=record.doi,
                            message="record cannot prove membership in the date window",
                        )
                    )
                    continue
                if not _venue_matches(record, journal, journals):
                    issues.append(
                        SemanticScholarIssue(
                            severity=SemanticScholarIssueSeverity.WARNING,
                            stage="venue_validation",
                            journal=journal.name,
                            paper_id=record.paper_id,
                            doi=record.doi,
                            message="record does not match configured journal identity",
                        )
                    )
                    continue
                records.append(record)
                evidence.append(record.to_evidence())
        except _PROVIDER_FAILURES as error:
            issues.append(
                SemanticScholarIssue(
                    severity=SemanticScholarIssueSeverity.ERROR,
                    stage="search_failure",
                    journal=journal.name,
                    message=f"Semantic Scholar search failed: {error}",
                )
            )

    return SemanticScholarRetrievalResult(
        evidence=tuple(evidence),
        supplement_records=(),
        discovered_records=tuple(records),
        issues=tuple(issues),
    )


def augment_with_semantic_scholar(
    client: Any,
    base_evidence: Sequence[ProviderWorkEvidence],
    journals: Sequence[JournalConfig],
    from_date: date,
    to_date: date,
    expression: KeywordExpression,
    *,
    retrieved_at: datetime | None = None,
) -> SemanticScholarRetrievalResult:
    if from_date > to_date:
        raise ValueError("from_date must not be after to_date")
    timestamp = retrieved_at or datetime.now(timezone.utc)
    if timestamp.utcoffset() is None:
        raise ValueError("retrieved_at must include a timezone")
    timestamp = timestamp.astimezone(timezone.utc)
    supplements = supplement_semantic_scholar_dois(
        client,
        base_evidence,
        retrieved_at=timestamp,
    )
    discovery = discover_semantic_scholar_journals(
        client,
        journals,
        from_date,
        to_date,
        expression,
        retrieved_at=timestamp,
    )
    return SemanticScholarRetrievalResult(
        evidence=(*supplements.evidence, *discovery.evidence),
        supplement_records=supplements.supplement_records,
        discovered_records=discovery.discovered_records,
        issues=(*supplements.issues, *discovery.issues),
    )
