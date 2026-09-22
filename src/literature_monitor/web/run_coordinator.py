"""Process-local coordination for one synchronous production monitor run."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from literature_monitor.application.monitor import ProgressStage, RunResult, run_monitor

LOGGER = logging.getLogger(__name__)

__all__ = [
    "CoordinatorStatus",
    "StartOutcome",
    "UnexpectedRunError",
    "StartResult",
    "CoordinatorSnapshot",
    "RunCoordinator",
]


class CoordinatorStatus(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"


class StartOutcome(str, Enum):
    STARTED = "STARTED"
    ALREADY_RUNNING = "ALREADY_RUNNING"
    START_FAILED = "START_FAILED"


@dataclass(frozen=True)
class UnexpectedRunError:
    category: str
    message: str


@dataclass(frozen=True)
class StartResult:
    outcome: StartOutcome


@dataclass(frozen=True)
class CoordinatorSnapshot:
    status: CoordinatorStatus
    progress_stage: ProgressStage | None
    started_at: datetime | None
    finished_at: datetime | None
    result: RunResult | None
    unexpected_error: UnexpectedRunError | None


class RunCoordinator:
    """Coordinate one transient production run without persisting run state."""

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path
        self._lock = threading.Lock()
        self._status = CoordinatorStatus.IDLE
        self._progress_stage: ProgressStage | None = None
        self._started_at: datetime | None = None
        self._finished_at: datetime | None = None
        self._result: RunResult | None = None
        self._unexpected_error: UnexpectedRunError | None = None
        self._worker: threading.Thread | None = None

    def start(self) -> StartResult:
        """Start a production run on a dedicated worker if none is active."""

        with self._lock:
            if self._status is CoordinatorStatus.RUNNING:
                return StartResult(StartOutcome.ALREADY_RUNNING)

            self._status = CoordinatorStatus.RUNNING
            self._progress_stage = None
            self._started_at = datetime.now(timezone.utc)
            self._finished_at = None
            self._result = None
            self._unexpected_error = None
            worker = threading.Thread(
                target=self._run_worker,
                name="literature-monitor-run",
            )
            self._worker = worker

        try:
            worker.start()
        except Exception as error:
            LOGGER.exception("Failed to start monitor worker")
            failure = UnexpectedRunError(
                category=type(error).__name__,
                message="The monitor worker could not be started.",
            )
            with self._lock:
                if self._worker is worker and self._status is CoordinatorStatus.RUNNING:
                    self._status = CoordinatorStatus.FINISHED
                    self._finished_at = datetime.now(timezone.utc)
                    self._result = None
                    self._unexpected_error = failure
                    self._worker = None
            return StartResult(StartOutcome.START_FAILED)

        return StartResult(StartOutcome.STARTED)

    def snapshot(self) -> CoordinatorSnapshot:
        """Copy current transient state under the coordinator lock."""

        with self._lock:
            return CoordinatorSnapshot(
                status=self._status,
                progress_stage=self._progress_stage,
                started_at=self._started_at,
                finished_at=self._finished_at,
                result=self._result,
                unexpected_error=self._unexpected_error,
            )

    def _update_progress(self, stage: ProgressStage) -> None:
        with self._lock:
            if self._status is CoordinatorStatus.RUNNING:
                self._progress_stage = stage

    def _run_worker(self) -> None:
        try:
            result = run_monitor(
                self._config_path,
                progress_callback=self._update_progress,
            )
        except Exception as error:
            LOGGER.exception("Unexpected exception during monitor run")
            unexpected_error = UnexpectedRunError(
                category=type(error).__name__,
                message="The monitor run stopped because of an unexpected internal error.",
            )
            with self._lock:
                self._result = None
                self._unexpected_error = unexpected_error
                self._finished_at = datetime.now(timezone.utc)
                self._status = CoordinatorStatus.FINISHED
                self._worker = None
            return

        with self._lock:
            self._result = result
            self._unexpected_error = None
            self._finished_at = datetime.now(timezone.utc)
            self._status = CoordinatorStatus.FINISHED
            self._worker = None
