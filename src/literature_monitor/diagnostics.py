"""Typed transient Run observations; never durable state or outcome severity."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum


class RunDiagnosticKind(str, Enum):
    NON_CANDIDATE_EXCLUSION = "non_candidate_exclusion"
    SCOPE_DISPUTE = "scope_dispute"
    REPEATED_TITLE_SEPARATION = "repeated_title_separation"
    CONFLICTING_DOI_SEPARATION = "conflicting_doi_separation"


@dataclass(frozen=True)
class RunDiagnostic:
    kind: RunDiagnosticKind
    message: str
    record_ids: tuple[str, ...]
    journal: str | None = None


@dataclass(frozen=True)
class RunDiagnosticSummary:
    kind: RunDiagnosticKind
    logical_groups: int


def summarize_run_diagnostics(diagnostics: Sequence[RunDiagnostic]) -> tuple[RunDiagnosticSummary, ...]:
    counts = Counter(diagnostic.kind for diagnostic in diagnostics)
    return tuple(RunDiagnosticSummary(kind, counts[kind]) for kind in RunDiagnosticKind if counts[kind])
