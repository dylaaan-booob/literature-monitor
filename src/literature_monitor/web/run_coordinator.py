"""Process-local coordination for one synchronous production monitor run."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from literature_monitor.application.monitor import RunResult, run_monitor
from literature_monitor.progress import (
    PROGRESS_STAGES,
    ActivitySnapshot,
    ProgressEvent,
    ProgressStage,
    ProgressState,
)

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
    stage_index: int | None = None
    stage_total: int = len(PROGRESS_STAGES)
    current_activity: ActivitySnapshot | None = None
    stage_started_at: datetime | None = None
    last_activity_at: datetime | None = None
    worker_alive: bool = False
    inactivity_warning: bool = False


class RunCoordinator:
    """Coordinate one transient production run without persisting run state."""

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path
        self._lock = threading.Lock()
        self._status = CoordinatorStatus.IDLE
        self._progress_state = ProgressState()
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
            self._progress_state = ProgressState()
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
                # 在线程启动成功前保持锁，避免暴露 RUNNING + 尚未启动 worker 的瞬时状态。
                worker.start()
            except Exception as error:
                self._status = CoordinatorStatus.FINISHED
                self._finished_at = datetime.now(timezone.utc)
                self._result = None
                self._unexpected_error = UnexpectedRunError(
                    category=type(error).__name__,
                    message="The monitor worker could not be started.",
                )
                start_error = error
            else:
                start_error = None

        if start_error is not None:
            LOGGER.error(
                "Failed to start monitor worker",
                exc_info=(
                    type(start_error),
                    start_error,
                    start_error.__traceback__,
                ),
            )
            return StartResult(StartOutcome.START_FAILED)

        return StartResult(StartOutcome.STARTED)

    def snapshot(self) -> CoordinatorSnapshot:
        """Copy current transient state under the coordinator lock."""

        now = datetime.now(timezone.utc)
        with self._lock:
            worker_alive = (
                self._status is CoordinatorStatus.RUNNING
                and self._worker is not None
                and self._worker.is_alive()
            )
            progress = self._progress_state.snapshot(
                at=now,
                active=self._status is CoordinatorStatus.RUNNING and worker_alive,
            )
            return CoordinatorSnapshot(
                status=self._status,
                progress_stage=progress.progress_stage,
                started_at=self._started_at,
                finished_at=self._finished_at,
                result=self._result,
                unexpected_error=self._unexpected_error,
                stage_index=progress.stage_index,
                stage_total=progress.stage_total,
                current_activity=progress.current_activity,
                stage_started_at=progress.stage_started_at,
                last_activity_at=progress.last_activity_at,
                worker_alive=worker_alive,
                inactivity_warning=progress.inactivity_warning,
            )

    def _update_progress(self, event: ProgressEvent) -> None:
        with self._lock:
            if self._status is CoordinatorStatus.RUNNING:
                self._progress_state.apply(
                    event,
                    at=datetime.now(timezone.utc),
                )

    def _run_worker(self) -> None:
        result: RunResult | None = None
        unexpected_error: UnexpectedRunError | None = None
        try:
            result = run_monitor(
                self._config_path,
                progress_callback=self._update_progress,
            )
        except BaseException as error:
            LOGGER.exception("Unexpected exception during monitor run")
            unexpected_error = UnexpectedRunError(
                category=type(error).__name__,
                message="The monitor run stopped because of an unexpected internal error.",
            )
        finally:
            # 所有 worker 退出路径都在执行边界收敛，不能依赖 HTTP polling 修复状态。
            with self._lock:
                self._result = result if unexpected_error is None else None
                self._unexpected_error = unexpected_error
                self._finished_at = datetime.now(timezone.utc)
                self._status = CoordinatorStatus.FINISHED
