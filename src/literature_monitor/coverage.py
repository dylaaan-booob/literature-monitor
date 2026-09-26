"""Provider retrieval coverage for run results and latest-run diagnostics."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum


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
