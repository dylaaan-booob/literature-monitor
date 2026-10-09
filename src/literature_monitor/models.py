"""Canonical paper and supporting domain models."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import re
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import (
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
    supplements: tuple[ProviderRecordRef, ...] = ()
    monitor_journal_issns: tuple[str, ...] = ()


class WorkflowStatus(str, Enum):
    CANDIDATE = "candidate"
    REJECTED = "rejected"
    KEPT = "kept"
    EXPORTED = "exported"


class ExportAttemptState(str, Enum):
    PENDING = "pending"
    UNCERTAIN = "uncertain"


class ExportAttempt(DomainModel):
    """Minimal durable reservation binding under SPEC §42.3, not capture history."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    state: ExportAttemptState
    attempt_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    paper_id: UUID
    doi: str
    expected_status: Literal["kept"]
    workspace_path: str
    paper_path: str
    workspace_identity: tuple[
        Annotated[int, Field(strict=True, ge=0)], Annotated[int, Field(strict=True, ge=0)],
    ]
    papers_identity: tuple[
        Annotated[int, Field(strict=True, ge=0)], Annotated[int, Field(strict=True, ge=0)],
    ]

    @field_validator("doi")
    @classmethod
    def require_normalized_doi(cls, value: str) -> str:
        from literature_monitor.identifiers import normalize_doi

        if normalize_doi(value) != value or not re.fullmatch(r"10\.\d{4,9}/[^\s]+", value):
            raise ValueError("export attempt DOI must be valid and normalized")
        return value

    @model_validator(mode="after")
    def require_bound_paths(self) -> ExportAttempt:
        workspace = Path(self.workspace_path)
        paper = Path(self.paper_path)
        if not workspace.is_absolute() or str(workspace) != self.workspace_path or ".." in workspace.parts:
            raise ValueError("export attempt workspace path must be absolute and normalized")
        if (paper.parts[:1] != ("Papers",) or len(paper.parts) != 2
                or paper.name in {".", ".."} or paper.suffix != ".md"
                or str(paper) != self.paper_path):
            raise ValueError("export attempt paper path must be a direct Papers Markdown path")
        return self


class Workflow(DomainModel):
    status: WorkflowStatus = WorkflowStatus.CANDIDATE
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

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
    sources: tuple[MetadataSource, ...] = ()
    workflow: Workflow = Field(default_factory=Workflow)
    journal_issns: tuple[str, ...] = ()

    @field_validator("authors")
    @classmethod
    def require_author(cls, value: tuple[Author, ...]) -> tuple[Author, ...]:
        if not value:
            raise ValueError("at least one author is required")
        return value

    @property
    def doi(self) -> str | None:
        return self.external_ids.doi
