"""Inert, replaceable cache of clean COMPLETE provider work-unit results."""

from __future__ import annotations

import json
import re
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from literature_monitor.application.runtime_metadata import (
    METADATA_DIRECTORY_NAME,
    metadata_directory,
)
from literature_monitor.config import JournalConfig, validate_journal_configs
from literature_monitor.coverage import CoverageStatus
from literature_monitor.crossref import (
    CrossrefDiscoveryResult,
    CrossrefWorkRecord,
    crossref_record_matches_journal,
    validate_normalized_crossref_record,
)
from literature_monitor.date_range import ResolvedDateRange
from literature_monitor.identifiers import normalize_doi
from literature_monitor.models import DomainModel, NonEmptyStr
from literature_monitor.openalex import DiscoveryResult, OpenAlexWorkRecord, ResolvedSource
from literature_monitor.retrieval import EvidenceRetrievalResult
from literature_monitor.safe_write import atomic_create_text, atomic_replace_text, read_text_exact

PROVIDER_CACHE_SCHEMA_VERSION = 1
_FILENAME = "provider-cache.json"


def _require_normalized_doi(doi: str) -> None:
    if normalize_doi(doi) != doi:
        raise ValueError("DOI must already be normalized")


def _parse_cached_journal(value: object) -> object:
    if isinstance(value, dict):
        journal = JournalConfig.model_validate_json(json.dumps(value), strict=True)
        normalized = validate_journal_configs((journal,))[0]
        if value.get("issn") != list(normalized.issn):
            raise ValueError("configured journal ISSNs must already be normalized")
        return normalized
    if isinstance(value, JournalConfig):
        if validate_journal_configs((value,))[0] != value:
            raise ValueError("configured journal must already be normalized")
    return value


def _parse_crossref_record(value: object) -> object:
    if isinstance(value, dict):
        if isinstance(value.get("doi"), str):
            _require_normalized_doi(value["doi"])
        # Before-validators receive Python values. Re-enter JSON validation so
        # strict tuple/date fields accept their JSON array/string encodings.
        record = CrossrefWorkRecord.model_validate_json(json.dumps(value), strict=True)
        # Domain string fields trim whitespace. Durable identifiers must not
        # silently become canonical during schema parsing.
        if value.get("issns", []) != list(record.issns):
            raise ValueError("Crossref ISSNs must already be normalized")
        for raw_author, author in zip(value.get("authors", []), record.authors, strict=True):
            if raw_author.get("orcid") != author.orcid:
                raise ValueError("Crossref author ORCID must already be canonical")
        return record
    return value


class CachedOpenAlexDiscovery(DomainModel):
    provider: Literal["openalex"]
    component: Literal["openalex_discovery"]
    journal: JournalConfig
    source: ResolvedSource
    records: tuple[OpenAlexWorkRecord, ...]

    @field_validator("journal", mode="before")
    @classmethod
    def require_normalized_journal(cls, value):
        return _parse_cached_journal(value)

    @model_validator(mode="after")
    def validate_identity(self) -> CachedOpenAlexDiscovery:
        source = self.source
        if source.journal != self.journal.name or source.configured_issns != self.journal.issn:
            raise ValueError("OpenAlex source must match the configured journal")
        if source.resolved_issns != self.journal.issn or source.unresolved_issns != ():
            raise ValueError("OpenAlex reusable source requires clean resolution of all configured ISSNs")
        if not re.fullmatch(r"https://openalex\.org/S\d+", source.openalex_id):
            raise ValueError("invalid normalized OpenAlex source ID")
        if not source.display_name.strip() or not source.resolved_issns:
            raise ValueError("OpenAlex source requires display_name and resolved ISSNs")
        for issn in (*source.configured_issns, *source.resolved_issns,
                     *source.unresolved_issns, *source.issn):
            if not issn.strip():
                raise ValueError("invalid OpenAlex source ISSN")
        if source.issn_l is not None and not source.issn_l.strip():
            raise ValueError("invalid OpenAlex source ISSN-L")
        if not set(source.resolved_issns).issubset(source.issn):
            raise ValueError("OpenAlex source ISSN resolution identity is inconsistent")
        for record in self.records:
            if (record.source_id != source.openalex_id
                    or record.metadata.journal != source.display_name
                    or record.provenance.provider != "openalex"
                    or record.provenance.record_id != record.external_ids.openalex
                    or not re.fullmatch(r"https://openalex\.org/W\d+", record.provenance.record_id)):
                raise ValueError("OpenAlex record identity must match its source/provenance")
            if record.external_ids.doi is not None:
                _require_normalized_doi(record.external_ids.doi)
        return self


class CachedCrossrefDiscovery(DomainModel):
    provider: Literal["crossref"]
    component: Literal["crossref_discovery"]
    journal: JournalConfig
    issn: NonEmptyStr
    records: tuple[CrossrefWorkRecord, ...]

    @field_validator("journal", mode="before")
    @classmethod
    def require_normalized_journal(cls, value):
        return _parse_cached_journal(value)

    @field_validator("issn", mode="before")
    @classmethod
    def require_normalized_issn(cls, value):
        if isinstance(value, str) and value != value.strip():
            raise ValueError("queried ISSN must already be normalized")
        return value

    @field_validator("records", mode="before")
    @classmethod
    def require_normalized_records(cls, value):
        if isinstance(value, (list, tuple)):
            return tuple(_parse_crossref_record(record) for record in value)
        return value

    @model_validator(mode="after")
    def validate_identity(self) -> CachedCrossrefDiscovery:
        if self.issn not in self.journal.issn:
            raise ValueError("queried ISSN must belong to the configured journal")
        for record in self.records:
            validate_normalized_crossref_record(record)
            if not crossref_record_matches_journal(record, self.journal):
                raise ValueError("Crossref discovery record does not match the configured journal")
        return self


class CachedCrossrefSupplement(DomainModel):
    provider: Literal["crossref"]
    component: Literal["crossref_supplement"]
    doi: NonEmptyStr
    record: CrossrefWorkRecord

    @field_validator("doi", mode="before")
    @classmethod
    def require_normalized_identity(cls, value):
        if isinstance(value, str):
            _require_normalized_doi(value)
        return value

    @field_validator("record", mode="before")
    @classmethod
    def require_normalized_record(cls, value):
        return _parse_crossref_record(value)

    @model_validator(mode="after")
    def validate_identity(self) -> CachedCrossrefSupplement:
        _require_normalized_doi(self.doi)
        validate_normalized_crossref_record(self.record)
        if self.record.doi != self.doi:
            raise ValueError("supplement record must match the lookup DOI")
        return self


CachedProviderUnit = Annotated[
    CachedOpenAlexDiscovery | CachedCrossrefDiscovery | CachedCrossrefSupplement,
    Field(discriminator="component"),
]


class ProviderResultCache(DomainModel):
    schema_version: Annotated[int, Field(strict=True)]
    resolved_date_range: ResolvedDateRange
    units: tuple[CachedProviderUnit, ...]

    @model_validator(mode="after")
    def validate_contract(self) -> ProviderResultCache:
        if self.schema_version != PROVIDER_CACHE_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {self.schema_version!r}")
        phases = {"openalex_discovery": 0, "crossref_discovery": 1, "crossref_supplement": 2}
        previous_phase = -1
        seen: set[tuple[object, ...]] = set()
        for unit in self.units:
            phase = phases[unit.component]
            if phase < previous_phase:
                raise ValueError("provider component phase order must be monotonic")
            previous_phase = phase
            if isinstance(unit, CachedCrossrefSupplement):
                identity = (unit.provider, unit.component, unit.doi)
            else:
                # Retain configured identity rather than resolved provider IDs.
                journal_identity = (unit.journal.name.casefold(), unit.journal.issn)
                identity = (unit.provider, unit.component, journal_identity)
                if isinstance(unit, CachedCrossrefDiscovery):
                    identity = (*identity, unit.issn)
            if identity in seen:
                raise ValueError("duplicate work-unit identity in provider cache")
            seen.add(identity)
        return self


def build_provider_cache(
    resolved_date_range: ResolvedDateRange,
    openalex: DiscoveryResult,
    crossref: CrossrefDiscoveryResult,
    retrieval: EvidenceRetrievalResult,
) -> ProviderResultCache:
    """Select at execution boundaries; never infer membership from flat records."""

    # Provider-domain values are already normalized. Defer cache-schema
    # validation to persistence so a rejected candidate cannot block Papers.
    units: list[CachedProviderUnit] = []
    for unit in openalex.units:
        if unit.coverage.status is CoverageStatus.COMPLETE and not unit.issues:
            units.append(CachedOpenAlexDiscovery.model_construct(
                provider="openalex", component="openalex_discovery",
                journal=unit.journal, source=unit.source, records=unit.records,
            ))
    for unit in crossref.units:
        if unit.coverage.status is CoverageStatus.COMPLETE and not unit.issues:
            units.append(CachedCrossrefDiscovery.model_construct(
                provider="crossref", component="crossref_discovery",
                journal=unit.journal, issn=unit.issn, records=unit.records,
            ))
    for unit in retrieval.units:
        if unit.coverage.status is CoverageStatus.COMPLETE and not unit.issues:
            units.append(CachedCrossrefSupplement.model_construct(
                provider="crossref", component="crossref_supplement",
                doi=unit.doi, record=unit.record,
            ))
    return ProviderResultCache.model_construct(
        schema_version=PROVIDER_CACHE_SCHEMA_VERSION,
        resolved_date_range=resolved_date_range,
        units=tuple(units),
    )


class ProviderCacheReadStatus(str, Enum):
    AVAILABLE = "available"
    NOT_FOUND = "not_found"
    INVALID = "invalid"


@dataclass(frozen=True)
class ProviderCacheReadResult:
    status: ProviderCacheReadStatus
    path: Path
    cache: ProviderResultCache | None = None
    error: str | None = None


class ProviderCacheWriteError(RuntimeError):
    def __init__(self, path: Path, message: str) -> None:
        super().__init__(message)
        self.path = path


def provider_cache_path(output_dir: Path) -> Path:
    return output_dir / METADATA_DIRECTORY_NAME / _FILENAME


def write_provider_cache(output_dir: Path, cache: ProviderResultCache) -> None:
    path = provider_cache_path(output_dir)
    try:
        contents = cache.model_dump_json(indent=2) + "\n"
        # Apply the same strict schema before publishing any complete file.
        ProviderResultCache.model_validate_json(contents, strict=True)
        metadata_directory(output_dir, create=True)
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            atomic_create_text(path, contents)
        else:
            if not stat.S_ISREG(mode):
                raise OSError("provider cache path is not a regular file")
            atomic_replace_text(path, contents)
    except (OSError, UnicodeError, ValueError) as error:
        raise ProviderCacheWriteError(path, str(error)) from error


def read_provider_cache(output_dir: Path) -> ProviderCacheReadResult:
    """Validate without mutation, repair, or provider execution."""

    path = provider_cache_path(output_dir)
    try:
        if metadata_directory(output_dir, create=False) is None:
            return ProviderCacheReadResult(ProviderCacheReadStatus.NOT_FOUND, path)
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return ProviderCacheReadResult(ProviderCacheReadStatus.NOT_FOUND, path)
        if not stat.S_ISREG(mode):
            raise OSError("provider cache path is not a regular file")
        cache = ProviderResultCache.model_validate_json(read_text_exact(path), strict=True)
    except (OSError, UnicodeError, ValueError) as error:
        return ProviderCacheReadResult(ProviderCacheReadStatus.INVALID, path, error=str(error))
    return ProviderCacheReadResult(ProviderCacheReadStatus.AVAILABLE, path, cache=cache)
