"""Independent, process-local single-active acquisition worker; no queue/history."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import threading
from uuid import UUID

from literature_monitor.application.acquisition import (
    AcquisitionOutcome, AcquisitionRecovery, AcquisitionResult, AcquisitionService, AcquisitionStage,
)


class AcquisitionCoordinatorStatus(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"


class AcquisitionStartOutcome(str, Enum):
    STARTED = "STARTED"
    ALREADY_RUNNING = "ALREADY_RUNNING"
    START_FAILED = "START_FAILED"


@dataclass(frozen=True)
class AcquisitionStartResult:
    outcome: AcquisitionStartOutcome


@dataclass(frozen=True)
class UnexpectedAcquisitionError:
    category: str
    message: str


@dataclass(frozen=True)
class AcquisitionSnapshot:
    status: AcquisitionCoordinatorStatus
    paper_id: UUID | None
    stage: AcquisitionStage | None
    result: AcquisitionResult | None
    unexpected_error: UnexpectedAcquisitionError | None


def _category(error: BaseException) -> str:
    return type(error).__name__ if type(error) in (RuntimeError, ValueError, OSError, KeyboardInterrupt, SystemExit) else "InternalError"


class AcquisitionCoordinator:
    def __init__(self, service: AcquisitionService | None = None):
        self._service = service
        self._lock = threading.Lock()
        self._snapshot = AcquisitionSnapshot(AcquisitionCoordinatorStatus.IDLE, None, None, None, None)

    def snapshot(self) -> AcquisitionSnapshot:
        with self._lock:
            return self._snapshot

    def start(
        self, paper_id: UUID, *, service_factory: Callable[[], AcquisitionService] | None = None,
    ) -> AcquisitionStartResult:
        if not isinstance(paper_id, UUID):
            raise TypeError("A Paper UUID is required.")
        with self._lock:
            if self._snapshot.status is AcquisitionCoordinatorStatus.RUNNING:
                return AcquisitionStartResult(AcquisitionStartOutcome.ALREADY_RUNNING)
            self._snapshot = AcquisitionSnapshot(AcquisitionCoordinatorStatus.RUNNING, paper_id,
                                                  AcquisitionStage.LOCATING_ZOTERO, None, None)
            try:
                # Bind once inside the shared slot; later Settings changes cannot
                # replace the service underneath this attempt. Factories are local-only.
                service = service_factory() if service_factory is not None else self._service
                if service is None:
                    raise ValueError("An acquisition service is required.")
                threading.Thread(target=self._run, args=(paper_id, service), name="literature-monitor-acquisition").start()
            except BaseException as error:
                self._snapshot = AcquisitionSnapshot(AcquisitionCoordinatorStatus.FINISHED, paper_id, AcquisitionStage.FAILED,
                    None, UnexpectedAcquisitionError(_category(error), "The acquisition worker could not be started."))
                return AcquisitionStartResult(AcquisitionStartOutcome.START_FAILED)
            return AcquisitionStartResult(AcquisitionStartOutcome.STARTED)

    def _stage(self, value: AcquisitionStage) -> None:
        with self._lock:
            current = self._snapshot
            if current.status is AcquisitionCoordinatorStatus.RUNNING:
                self._snapshot = AcquisitionSnapshot(current.status, current.paper_id, value, None, None)

    def _run(self, paper_id: UUID, service: AcquisitionService) -> None:
        result, error = None, None
        try:
            result = service.acquire(paper_id, stage_callback=self._stage)
            if not isinstance(result, AcquisitionResult) or result.paper_id != paper_id:
                raise ValueError("Invalid acquisition result.")
        except BaseException as caught:
            error = UnexpectedAcquisitionError(_category(caught), "Acquisition stopped because of an unexpected internal error.")
        finally:
            with self._lock:
                terminal = (
                    AcquisitionStage.FAILED if error or result is None
                    else AcquisitionStage.WAITING_FOR_INSTITUTION_AUTH if result.recovery is AcquisitionRecovery.INSTITUTION_LOGIN
                    else AcquisitionStage.SUCCEEDED if result.outcome in (AcquisitionOutcome.SUCCEEDED, AcquisitionOutcome.PDF_ALREADY_ATTACHED)
                    else AcquisitionStage.FAILED
                )
                self._snapshot = AcquisitionSnapshot(AcquisitionCoordinatorStatus.FINISHED, paper_id,
                    terminal, result if error is None else None, error)
