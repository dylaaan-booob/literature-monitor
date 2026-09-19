"""Canonical paper and supporting domain models."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any
from uuid import UUID, uuid4

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DomainModel(BaseModel):
    """Shared strict defaults for immutable domain values."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class CanonicalMetadata(DomainModel):
    title: NonEmptyStr
    journal: NonEmptyStr
    publication_date: Date | None = None
    abstract: str | None = None
    author_keywords: tuple[NonEmptyStr, ...] = ()


class ExternalIds(BaseModel):
    """Known external identifiers plus future provider-specific identifiers."""

    model_config = ConfigDict(extra="allow", frozen=True, str_strip_whitespace=True)

    openalex: NonEmptyStr | None = None
    doi: NonEmptyStr | None = None
    arxiv: NonEmptyStr | None = None
    crossref: NonEmptyStr | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_all_identifiers(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        cleaned: dict[str, Any] = {}
        for key, identifier in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("external identifier names must be non-empty strings")
            if identifier is None and key in {"openalex", "doi", "arxiv", "crossref"}:
                cleaned[key] = None
                continue
            if not isinstance(identifier, str) or not identifier.strip():
                raise ValueError(f"external identifier {key!r} must be a non-empty string")
            cleaned[key.strip()] = identifier.strip()
        return cleaned


class Author(DomainModel):
    name: NonEmptyStr
    openalex_id: NonEmptyStr | None = None
    orcid: NonEmptyStr | None = None


class VersionKind(str, Enum):
    JOURNAL_FINAL = "journal_final"
    JOURNAL_ONLINE = "journal_online"
    ACCEPTED_MANUSCRIPT = "accepted_manuscript"
    PREPRINT = "preprint"
    UNKNOWN = "unknown"


class VersionRef(DomainModel):
    source: NonEmptyStr
    identifier: NonEmptyStr


class PaperVersion(VersionRef):
    kind: VersionKind
    url: AnyHttpUrl | None = None
    date: Date | None = None


class MetadataSource(DomainModel):
    provider: NonEmptyStr
    record_id: NonEmptyStr
    retrieved_at: datetime

    @field_validator("retrieved_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("retrieved_at must include a timezone")
        return value


class ProviderRecordRef(DomainModel):
    provider: NonEmptyStr
    record_id: NonEmptyStr


class EvidenceDateKind(str, Enum):
    PUBLISHED = "published"
    PUBLISHED_ONLINE = "published-online"
    PUBLISHED_PRINT = "published-print"
    ISSUED = "issued"


class EvidenceDate(DomainModel):
    kind: EvidenceDateKind
    year: Annotated[int, Field(strict=True, ge=1, le=9999)]
    month: Annotated[int, Field(strict=True, ge=1, le=12)] | None = None
    day: Annotated[int, Field(strict=True, ge=1, le=31)] | None = None

    @model_validator(mode="after")
    def validate_partial_date(self) -> EvidenceDate:
        if self.day is not None and self.month is None:
            raise ValueError("day requires month")
        if self.month is not None:
            Date(self.year, self.month, self.day or 1)
        return self


class EvidenceRelation(DomainModel):
    relation_type: NonEmptyStr
    id_type: NonEmptyStr
    identifier: NonEmptyStr
    asserted_by: NonEmptyStr | None = None


class EvidenceVersionRole(str, Enum):
    PUBLICATION = "publication"
    MANUSCRIPT = "manuscript"
    PREPRINT = "preprint"


class EvidenceVersionHint(DomainModel):
    source: NonEmptyStr
    identifier: NonEmptyStr
    role: EvidenceVersionRole
    url: NonEmptyStr | None = None


class ProviderTopic(DomainModel):
    """Reconstructible provider taxonomy, distinct from author keywords."""

    value: NonEmptyStr
    source: NonEmptyStr | None = None


class ProviderWorkEvidence(DomainModel):
    """Transient provider-neutral evidence consumed by canonicalization."""

    provenance: MetadataSource
    title: NonEmptyStr | None = None
    journal: NonEmptyStr | None = None
    publication_date: Date | None = None
    abstract: str | None = None
    author_keywords: tuple[NonEmptyStr, ...] = ()
    provider_topics: tuple[ProviderTopic, ...] = ()
    fields_of_study: tuple[NonEmptyStr, ...] = ()
    authors: tuple[Author, ...] = ()
    external_ids: ExternalIds = Field(default_factory=ExternalIds)
    dates: tuple[EvidenceDate, ...] = ()
    relations: tuple[EvidenceRelation, ...] = ()
    version_hints: tuple[EvidenceVersionHint, ...] = ()
    supplements: tuple[ProviderRecordRef, ...] = ()


class WorkflowStatus(str, Enum):
    CANDIDATE = "candidate"
    REJECTED = "rejected"
    KEPT = "kept"
    IN_ZOTERO = "in_zotero"


class Workflow(DomainModel):
    status: WorkflowStatus = WorkflowStatus.CANDIDATE
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    zotero_key: NonEmptyStr | None = None

    @field_validator("discovered_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("discovered_at must include a timezone")
        return value


class CanonicalPaper(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    metadata: CanonicalMetadata
    external_ids: ExternalIds = Field(default_factory=ExternalIds)
    authors: tuple[Author, ...]
    versions: tuple[PaperVersion, ...] = ()
    sources: tuple[MetadataSource, ...] = ()
    workflow: Workflow = Field(default_factory=Workflow)
    preferred_version: VersionRef | None = None

    @field_validator("authors")
    @classmethod
    def require_author(cls, value: tuple[Author, ...]) -> tuple[Author, ...]:
        if not value:
            raise ValueError("at least one author is required")
        return value

    @model_validator(mode="after")
    def validate_versions(self) -> CanonicalPaper:
        keys = [(version.source, version.identifier) for version in self.versions]
        if len(keys) != len(set(keys)):
            raise ValueError("versions must not contain duplicate source/identifier pairs")
        if self.preferred_version is not None:
            preferred_key = (
                self.preferred_version.source,
                self.preferred_version.identifier,
            )
            if preferred_key not in keys:
                raise ValueError("preferred_version must reference an existing version")
        return self

    @property
    def doi(self) -> str | None:
        return self.external_ids.doi
