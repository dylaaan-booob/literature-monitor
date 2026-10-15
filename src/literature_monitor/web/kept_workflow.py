"""Process-local sequencing of existing Kept executors (SPEC §59)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import secrets
import threading
import time
from typing import TYPE_CHECKING

from literature_monitor.application.batch_import import BatchImportService, BatchPhase, BatchSnapshot
from literature_monitor.config import ConfigurationError, KeptWorkflowConfig, LoadedConfig
from literature_monitor.safe_write import ContentChangedError
from literature_monitor.web.preparation_coordinator import (
    PreparationCoordinator, PreparationOutcome, PreparationPhase,
)

if TYPE_CHECKING:
    from literature_monitor.web.app import _ImportTarget


@dataclass(frozen=True)
class WorkflowSnapshot:
    workflow_id: str | None = None
    generation: int = 0
    mode: str = "full"
    phase: str = "idle"
    settings: KeptWorkflowConfig = KeptWorkflowConfig()
    institution_name: str | None = None
    preparation_task_id: str | None = None
    preparation_generation: int = 0
    waiting_for_login: bool = False
    import_started: bool = False
    transition_deadline: float | None = None
    message: str = "Ready to prepare access and import Kept Papers."
    navigation: str | None = None

    @property
    def active(self) -> bool:
        return self.phase in {"preparing", "waiting_for_user", "transition", "importing", "stopping"}

    @property
    def can_continue(self) -> bool:
        return self.phase == "waiting_for_user" and not self.waiting_for_login

    @property
    def can_cancel(self) -> bool:
        return self.active and self.phase != "stopping"


class KeptWorkflow:
    def __init__(self, preparation: PreparationCoordinator, batch: BatchImportService,
                 lock: threading.RLock, *,
                 begin_import: Callable[[_ImportTarget, str], BatchSnapshot],
                 has_kept: Callable[[_ImportTarget], bool],
                 clock: Callable[[], float] = time.monotonic):
        self.preparation = preparation
        self.batch = batch
        self._lock = lock
        self._begin_import = begin_import
        self._has_kept = has_kept
        self._clock = clock
        self._snapshot = WorkflowSnapshot()
        self._target: _ImportTarget | None = None
        self._authorization: str | None = None
        self._due: float | None = None
        self._stop = threading.Event()

    def snapshot(self) -> WorkflowSnapshot:
        with self._lock:
            return self._snapshot

    def _publish(self, **changes) -> None:
        self._snapshot = replace(self._snapshot, **changes)

    def start(self, target: _ImportTarget, config: LoadedConfig, authorization: str,
              *, mode: str = "full") -> WorkflowSnapshot:
        with self._lock:
            if self._snapshot.active:
                return self._snapshot
            self._stop.set()
            self._stop = threading.Event()
            self._target, self._authorization, self._due = target, authorization, None
            self._snapshot = WorkflowSnapshot(
                workflow_id=secrets.token_urlsafe(24), generation=self._snapshot.generation + 1,
                mode=mode, phase="preparing", settings=config.kept_workflow,
                institution_name=config.institution.name if config.institution else None,
            )
            try:
                if not self._has_kept(target):
                    self._interrupt("No Kept Papers to import.")
                elif mode == "import_only":
                    self._start_import()
                else:
                    preparation = self.preparation.start(target.config_path)
                    self._publish(preparation_task_id=preparation.task_id,
                                  preparation_generation=preparation.generation)
                    if preparation.phase in {PreparationPhase.BLOCKED, PreparationPhase.STOPPED}:
                        self._interrupt(preparation.message)
                    else:
                        self.advance()
            except (ConfigurationError, ContentChangedError, OSError) as error:
                self._interrupt(str(error))
            if self._snapshot.active:
                threading.Thread(target=self._run,
                                 args=(self._snapshot.workflow_id, self._snapshot.generation, self._stop),
                                 daemon=True, name="kept-workflow").start()
            return self._snapshot

    def _interrupt(self, message: str) -> None:
        self._authorization = None
        self._publish(phase="interrupted", message=message, transition_deadline=None,
                      waiting_for_login=False, navigation="kept")

    def _start_import(self) -> None:
        # All callers share the existing submission lock; only this path consumes the grant.
        if self._snapshot.import_started or self._authorization is None:
            return
        self._publish(import_started=True, phase="importing", transition_deadline=None, waiting_for_login=False,
                      message="Importing all eligible Workspace Kept Papers.")
        authorization, self._authorization = self._authorization, None
        try:
            result = self._begin_import(self._target, authorization)
            self._finish_import(result)
        except (ConfigurationError, ContentChangedError, OSError) as error:
            self._interrupt(str(error))
            return

    def _finish_import(self, result: BatchSnapshot) -> None:
        if result.phase is BatchPhase.RUNNING:
            return
        if result.phase is not BatchPhase.COMPLETED:
            self._interrupt(result.message)
            return
        self._publish(phase="completed", message=result.message, navigation="kept")

    def advance(self) -> None:
        """Worker tick; GET/presentation never calls this method."""
        with self._lock:
            state = self._snapshot
            try:
                if state.phase in {"importing", "stopping"}:
                    self._finish_import(self.batch.snapshot())
                elif state.phase == "transition":
                    if self._clock() >= self._due:
                        self._start_import()
                elif state.phase in {"preparing", "waiting_for_user"}:
                    preparation = self.preparation.snapshot()
                    if (preparation.task_id, preparation.generation) != (
                            state.preparation_task_id, state.preparation_generation):
                        self._interrupt("Preparation task changed; start a new explicit workflow.")
                    elif preparation.phase in {PreparationPhase.BLOCKED, PreparationPhase.STOPPED}:
                        self._interrupt(preparation.message)
                    elif preparation.phase is PreparationPhase.RUNNING:
                        self._publish(
                            phase="waiting_for_user" if preparation.waiting_for_user else "preparing",
                            waiting_for_login=preparation.waiting_for_user,
                            message="Waiting for institution login in Chrome." if preparation.waiting_for_user
                            else preparation.message,
                        )
                    elif preparation.phase is PreparationPhase.COMPLETED and (state.phase == "preparing" or state.waiting_for_login):
                        safe = (not preparation.manual_services and all(
                            item.outcome is PreparationOutcome.OPENED for item in preparation.results))
                        if state.settings.auto_import_after_preparation and safe:
                            delay = state.settings.transition_delay_seconds
                            self._due = self._clock() + delay
                            self._publish(phase="transition", waiting_for_login=False, transition_deadline=time.time() + delay,
                                          message="Preparation finished. Import will start after the transition delay.")
                        else:
                            self._publish(phase="waiting_for_user", waiting_for_login=False,
                                          message="Review Service access and results, then continue to Import.")
            except (ConfigurationError, ContentChangedError, OSError) as error:
                self._interrupt(str(error))

    def _matches(self, workflow_id: str, generation: int) -> bool:
        return (workflow_id, generation) == (self._snapshot.workflow_id, self._snapshot.generation)

    def continue_import(self, workflow_id: str, generation: int) -> bool:
        with self._lock:
            if not self._matches(workflow_id, generation) or not self._snapshot.can_continue:
                return False
            self._start_import()
            return True

    def cancel(self, workflow_id: str, generation: int) -> bool:
        with self._lock:
            if not self._matches(workflow_id, generation) or not self._snapshot.can_cancel:
                return False
            if self._snapshot.import_started:
                self.batch.stop()
                self._publish(phase="stopping", message="Stopping; current native save is settling",
                              transition_deadline=None, navigation="kept")
                return True
            if self._snapshot.preparation_task_id:
                self.preparation.cancel(self._snapshot.preparation_task_id, self._snapshot.preparation_generation)
            self._interrupt("Workflow canceled; no automatic Import will start.")
            self._stop.set()
            return True

    def acknowledge_navigation(self, workflow_id: str, generation: int) -> bool:
        with self._lock:
            if not self._matches(workflow_id, generation):
                return False
            self._publish(navigation=None)
            return True

    def _run(self, workflow_id: str, generation: int, stop: threading.Event) -> None:
        while not stop.wait(0.1):
            with self._lock:
                if not self._matches(workflow_id, generation) or not self._snapshot.active:
                    return
                self.advance()

    def shutdown(self) -> None:
        with self._lock:
            self.cancel(self._snapshot.workflow_id, self._snapshot.generation)
            self._stop.set()
