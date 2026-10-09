"""Explicit all-Kept serial imports; transient plans and progress (SPEC §42.3–42.5)."""
from __future__ import annotations

from dataclasses import dataclass, replace
from collections.abc import Callable
from enum import Enum
from pathlib import Path
import threading
from uuid import UUID, uuid4
from literature_monitor.application.access import AccessObservation

from literature_monitor.application.export_attempts import (
    ExportAttemptError, ExportCompletionOutcome, _reserve_export_attempt_locked, resolve_export_completion,
)
from literature_monitor.application.workspace import (
    WorkspaceImportPlan, plan_workspace_import,
)
from literature_monitor.safe_write import CompareReadError, ContentChangedError, WorkspaceOperationLock, workspace_operation_lock
from literature_monitor.web.capture_coordinator import (
    CaptureCoordinator, CaptureFailureStage, CaptureStartOutcome, CaptureStage, ConnectorReadiness, PdfOutcome,
)


class BatchPhase(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    STOPPED = "stopped"
    PAUSED = "paused"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class BatchPaperResult:
    paper_id: UUID
    outcome: str
    reason: str
    pdf_outcome: PdfOutcome | None = None
    access: AccessObservation | None = None
    failure_stage: CaptureFailureStage | None = None


@dataclass(frozen=True)
class BatchStatistics:
    exported_parent: int = 0
    no_effect: int = 0
    uncertain: int = 0
    skipped_blocked: int = 0
    pdf_verified_success: int = 0
    pdf_verified_failure: int = 0
    pdf_unverified: int = 0
    normal_excluded: int = 0


@dataclass(frozen=True)
class BatchSnapshot:
    phase: BatchPhase
    plan: WorkspaceImportPlan | None
    current_paper_id: UUID | None
    results: tuple[BatchPaperResult, ...]
    message: str
    current_access: AccessObservation | None = None

    @property
    def statistics(self) -> BatchStatistics:
        return BatchStatistics(
            exported_parent=sum(r.outcome == "exported" for r in self.results),
            no_effect=sum(r.outcome == "no_effect" for r in self.results),
            uncertain=sum(r.outcome == "uncertain" for r in self.results),
            skipped_blocked=(sum(e.kind != "normal" for e in self.plan.exclusions) if self.plan else 0)
                + sum(r.outcome in {"skipped", "blocked"} for r in self.results),
            pdf_verified_success=sum(r.pdf_outcome is PdfOutcome.VERIFIED_SUCCESS for r in self.results),
            pdf_verified_failure=sum(r.pdf_outcome is PdfOutcome.VERIFIED_FAILURE for r in self.results),
            pdf_unverified=sum(r.pdf_outcome is PdfOutcome.UNVERIFIED for r in self.results),
            normal_excluded=sum(e.kind == "normal" for e in self.plan.exclusions) if self.plan else 0,
        )

    @property
    def successful(self) -> bool:
        return (self.phase is BatchPhase.COMPLETED and self.plan is not None and bool(self.results)
                and all(e.kind == "normal" for e in self.plan.exclusions)
                and len(self.results) == len(self.plan.papers) and all(r.outcome == "exported" for r in self.results))


class BatchImportService:
    """A5 calls start(path) explicitly and snapshot() for immutable progress.

    Execution owns completion consumption; UI polling must not consume it. Each
    invocation starts a fresh scan. No timer, restart, or result retries a Paper.
    The existing bridge routes keep using this service's CaptureCoordinator.
    """

    def __init__(self, coordinator: CaptureCoordinator):
        self.coordinator = coordinator
        self._mutex = threading.Lock()
        self._snapshot = BatchSnapshot(BatchPhase.IDLE, None, None, (), "No batch started")
        self._worker: threading.Thread | None = None

    def snapshot(self) -> BatchSnapshot:
        capture = self.coordinator.snapshot().attempt
        with self._mutex:
            return replace(self._snapshot, current_access=capture.access
                           if capture and capture.stage is not CaptureStage.FINISHED
                           and capture.paper_id == self._snapshot.current_paper_id else None)

    @property
    def running(self) -> bool:
        with self._mutex:
            return self._worker is not None and self._worker.is_alive()

    def start(self, workspace: Path, *,
              validate_target: Callable[[WorkspaceOperationLock], None] | None = None) -> BatchSnapshot:
        """Start all eligible Kept; verify caller authority under the Workspace lock.

        The optional precondition runs before exposing the plan or reserving a
        Paper. It never retargets the frozen path. Overlapping calls coalesce.
        """
        with self._mutex:
            if self._worker is not None and self._worker.is_alive():
                return self._snapshot
            ready = threading.Event()
            self._snapshot = BatchSnapshot(BatchPhase.RUNNING, None, None, (), "Scanning all Workspace Kept Papers")
            self._worker = threading.Thread(target=self._execute, args=(workspace, ready, validate_target), daemon=True)
            self._worker.start()
        ready.wait()
        return self.snapshot()

    def _publish(self, phase, plan, current, results, message):
        with self._mutex:
            self._snapshot = BatchSnapshot(phase, plan, current, tuple(results), message)

    def _execute(self, workspace: Path, ready: threading.Event,
                 validate_target: Callable[[WorkspaceOperationLock], None] | None) -> None:
        plan = None
        results: list[BatchPaperResult] = []
        phase = BatchPhase.COMPLETED
        message = "Batch finished; inspect parent and PDF results"
        access_context = str(uuid4())
        try:
            with workspace_operation_lock(workspace) as lock:
                # 唯一性与全局候选资格只扫描一次，逐篇保存前再读取目标文件。
                planned = plan_workspace_import(lock)
                if validate_target is not None:
                    validate_target(lock)
                plan = planned
                self._publish(BatchPhase.RUNNING, plan, None, results, "Kept import plan created")
                for paper in plan.papers:
                    lock.verify()
                    if self.coordinator.snapshot().readiness is not ConnectorReadiness.CONNECTED:
                        phase, message = BatchPhase.STOPPED, "Connector/Zotero unavailable; remaining Papers stay kept"
                        break
                    self._publish(BatchPhase.RUNNING, plan, paper.paper_id, results, "Checking selected Paper")
                    try:
                        reservation = _reserve_export_attempt_locked(
                            lock, paper.paper_id, expected_paper=paper,
                            expected_papers_identity=plan.papers_identity,
                        )
                    except (ExportAttemptError, CompareReadError, ContentChangedError, OSError) as error:
                        results.append(BatchPaperResult(paper.paper_id, "skipped", f"Paper changed or unsafe: {error}"))
                        continue

                    started = self.coordinator.start_capture(reservation, operation_lock=lock,
                                                             access_context=access_context)
                    if started.outcome is not CaptureStartOutcome.STARTED:
                        if started.outcome is CaptureStartOutcome.CONNECTOR_UNAVAILABLE:
                            phase, message = BatchPhase.STOPPED, "Connector/Zotero became unavailable"
                            break
                        results.append(BatchPaperResult(paper.paper_id, "skipped",
                                                        f"Save was not started: {started.outcome.value}"))
                        continue

                    self._publish(BatchPhase.RUNNING, plan, paper.paper_id, results,
                                  "Waiting for Connector native parent result")
                    ready.set()
                    completion = self.coordinator.wait_for_completion()
                    can_continue = self.coordinator.save_authority_ended(completion)
                    result = resolve_export_completion(completion, coordinator=self.coordinator)
                    outcome = {
                        ExportCompletionOutcome.EXPORTED: "exported",
                        ExportCompletionOutcome.NO_EFFECT: "no_effect",
                        ExportCompletionOutcome.UNCERTAIN: "uncertain",
                    }.get(result.outcome, "blocked")
                    results.append(BatchPaperResult(paper.paper_id, outcome, result.message,
                                                    completion.pdf_outcome, completion.access,
                                                    completion.failure_stage))
                    if not can_continue:
                        phase, message = (BatchPhase.PAUSED,
                                          "Unconfirmed dispatched save; next explicit Import can retry kept Papers")
                        break

                if phase is not BatchPhase.COMPLETED:
                    finished = {result.paper_id for result in results}
                    results.extend(BatchPaperResult(p.paper_id, "skipped", message)
                                   for p in plan.papers if p.paper_id not in finished)
                lock.verify()
        except Exception as error:
            phase, message = BatchPhase.BLOCKED, f"Workspace/capture ownership failed: {error}"
            if plan is not None:
                finished = {result.paper_id for result in results}
                results.extend(BatchPaperResult(p.paper_id, "skipped", message)
                               for p in plan.papers if p.paper_id not in finished)
        finally:
            self._publish(phase, plan, None, results, message)
            ready.set()
