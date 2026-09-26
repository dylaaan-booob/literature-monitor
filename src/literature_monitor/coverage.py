"""Provider retrieval coverage for run results and latest-run diagnostics."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from literature_monitor.identifiers import normalize_doi


class CoverageComponent(str, Enum):
    OPENALEX_DISCOVERY = "openalex_discovery"
    CROSSREF_DISCOVERY = "crossref_discovery"
    CROSSREF_SUPPLEMENT = "crossref_supplement"


class CoverageStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class CoverageUnit:
    """Execution completeness for one provider retrieval work unit."""

    provider: str
    component: CoverageComponent
    status: CoverageStatus
    journal: str | None = None
    issn: str | None = None
    doi: str | None = None


@dataclass(frozen=True)
class CoverageSummary:
    component: CoverageComponent
    total_units: int
    complete: int
    partial: int
    unavailable: int
    failed: int


@dataclass(frozen=True)
class ProviderReuseUnit:
    """Reporting identity, not a durable provider-cache matching key."""

    provider: str
    component: CoverageComponent
    journal: str | None = None
    issn: str | None = None
    doi: str | None = None


@dataclass(frozen=True)
class ProviderReuseSummary:
    component: CoverageComponent
    reused_units: int


def validate_reporting_identity(unit: CoverageUnit | ProviderReuseUnit) -> None:
    for field in ("provider", "journal", "issn", "doi"):
        value = getattr(unit, field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"reporting {field} must be a non-empty string or null")
    if unit.component is CoverageComponent.OPENALEX_DISCOVERY:
        valid = unit.provider == "openalex" and unit.journal is not None and unit.issn is None and unit.doi is None
    elif unit.component is CoverageComponent.CROSSREF_DISCOVERY:
        valid = unit.provider == "crossref" and unit.journal is not None and unit.issn is not None and unit.doi is None
    elif unit.component is CoverageComponent.CROSSREF_SUPPLEMENT:
        valid = unit.provider == "crossref" and unit.journal is None and unit.issn is None and unit.doi is not None
        if valid and normalize_doi(unit.doi) != unit.doi:
            raise ValueError("Crossref supplement reporting DOI must already be normalized")
    else:
        valid = False
    if not valid:
        raise ValueError("invalid provider/component reporting identity")


def reporting_identity(unit: CoverageUnit | ProviderReuseUnit) -> tuple[object, ...]:
    validate_reporting_identity(unit)
    return (unit.provider, unit.component, unit.journal, unit.issn, unit.doi)


def summarize_reuse(units: Sequence[ProviderReuseUnit]) -> tuple[ProviderReuseSummary, ...]:
    return tuple(ProviderReuseSummary(component, sum(unit.component is component for unit in units))
                 for component in CoverageComponent)


def summarize_coverage(units: Sequence[CoverageUnit]) -> tuple[CoverageSummary, ...]:
    """Summarize coverage deterministically in component declaration order."""

    summaries: list[CoverageSummary] = []
    for component in CoverageComponent:
        statuses = tuple(
            unit.status for unit in units if unit.component is component
        )
        summaries.append(
            CoverageSummary(
                component=component,
                total_units=len(statuses),
                complete=sum(status is CoverageStatus.COMPLETE for status in statuses),
                partial=sum(status is CoverageStatus.PARTIAL for status in statuses),
                unavailable=sum(
                    status is CoverageStatus.UNAVAILABLE for status in statuses
                ),
                failed=sum(status is CoverageStatus.FAILED for status in statuses),
            )
        )
    return tuple(summaries)
