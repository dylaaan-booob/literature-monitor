"""Save-to-Zotero orchestration over authoritative reconciliation and capture."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from uuid import UUID

from literature_monitor.application.decisions import (
    DecisionOutcome,
    DecisionResult,
    reconcile_paper_with_zotero,
)
from literature_monitor.models import WorkflowStatus
from literature_monitor.web.capture_coordinator import (
    CaptureCompletion,
    CaptureCoordinator,
    CaptureStartOutcome,
    CaptureStartResult,
)


class SaveToZoteroOutcome(str, Enum):
    RECONCILED = "RECONCILED"
    CAPTURE_STARTED = "CAPTURE_STARTED"
    CONNECTOR_UNAVAILABLE = "CONNECTOR_UNAVAILABLE"
    CAPTURE_BUSY = "CAPTURE_BUSY"
    COMPLETION_PENDING = "COMPLETION_PENDING"
    PRECHECK_FAILED = "PRECHECK_FAILED"
    CAPTURE_INVALID_DOI = "CAPTURE_INVALID_DOI"


@dataclass(frozen=True)
class SaveToZoteroResult:
    outcome: SaveToZoteroOutcome
    preflight: DecisionResult
    capture_start: CaptureStartResult | None = None


class CompletionProcessOutcome(str, Enum):
    NO_COMPLETION = "NO_COMPLETION"
    RECONCILED = "RECONCILED"
    RECONCILIATION_FAILED = "RECONCILIATION_FAILED"


@dataclass(frozen=True)
class CompletionProcessResult:
    outcome: CompletionProcessOutcome
    completion: CaptureCompletion | None
    reconciliation: DecisionResult | None


_CAPTURE_START_OUTCOMES = {
    CaptureStartOutcome.STARTED: SaveToZoteroOutcome.CAPTURE_STARTED,
    CaptureStartOutcome.CONNECTOR_UNAVAILABLE: SaveToZoteroOutcome.CONNECTOR_UNAVAILABLE,
    CaptureStartOutcome.ALREADY_ACTIVE: SaveToZoteroOutcome.CAPTURE_BUSY,
    CaptureStartOutcome.COMPLETION_PENDING: SaveToZoteroOutcome.COMPLETION_PENDING,
    CaptureStartOutcome.INVALID_DOI: SaveToZoteroOutcome.CAPTURE_INVALID_DOI,
}


def start_save_to_zotero(
    output_dir: Path,
    paper_id: UUID,
    expected_status: WorkflowStatus,
    capture_coordinator: CaptureCoordinator,
) -> SaveToZoteroResult:
    """Reconcile first and start automatic capture only after explicit NOT_FOUND."""

    preflight = reconcile_paper_with_zotero(
        output_dir,
        paper_id,
        expected_status,
    )
    if preflight.outcome is DecisionOutcome.UPDATED:
        return SaveToZoteroResult(SaveToZoteroOutcome.RECONCILED, preflight)
    if (
        preflight.outcome is not DecisionOutcome.ZOTERO_NOT_FOUND
        or preflight.reconciled_doi is None
        or preflight.capture_guard is None
    ):
        return SaveToZoteroResult(SaveToZoteroOutcome.PRECHECK_FAILED, preflight)

    capture_start = capture_coordinator.start_capture(preflight.capture_guard)
    return SaveToZoteroResult(
        _CAPTURE_START_OUTCOMES[capture_start.outcome],
        preflight,
        capture_start,
    )


def process_capture_completion(
    capture_coordinator: CaptureCoordinator,
) -> CompletionProcessResult:
    """Consume at most one completion and reconcile it exactly once."""

    completion = capture_coordinator.consume_completion()
    if completion is None:
        return CompletionProcessResult(
            CompletionProcessOutcome.NO_COMPLETION,
            None,
            None,
        )

    reconciliation = reconcile_paper_with_zotero(
        completion.capture_guard.output_dir,
        completion.paper_id,
        WorkflowStatus.KEPT,
        expected_doi=completion.normalized_doi,
        capture_guard=completion.capture_guard,
    )
    outcome = (
        CompletionProcessOutcome.RECONCILED
        if reconciliation.outcome is DecisionOutcome.UPDATED
        else CompletionProcessOutcome.RECONCILIATION_FAILED
    )
    return CompletionProcessResult(outcome, completion, reconciliation)
