from __future__ import annotations

import logging
import threading
from dataclasses import FrozenInstanceError
from enum import Enum
from pathlib import Path

import pytest

import literature_monitor.web.run_coordinator as coordinator_module
from literature_monitor.application.monitor import (
    MonitorStatistics,
    ProgressStage,
    RunOutcome,
    RunResult,
)
from literature_monitor.progress import (
    ActivityKind,
    ActivityUpdate,
    ProgressCallback,
    ProgressEvent,
    ProgressStage as SharedProgressStage,
)
from literature_monitor.web.run_coordinator import (
    CoordinatorStatus,
    RunCoordinator,
    StartOutcome,
)


def make_run_result(
    outcome: RunOutcome = RunOutcome.COMPLETED,
) -> RunResult:
    return RunResult(
        resolved_date_range=None,
        canonical_paper_count=0,
        created_papers=0,
        matched_existing_papers=0,
        updated_papers=0,
        created_authors=0,
        existing_authors=0,
        warnings=(),
        errors=(),
        outcome=outcome,
        statistics=MonitorStatistics(),
    )


def join_worker(worker: threading.Thread) -> None:
    worker.join(timeout=2)
    assert not worker.is_alive()


@pytest.mark.parametrize("reuse", [False, True])
def test_started_worker_binds_mode_opposite_request_does_not_change_it(tmp_path, monkeypatch, reuse):
    started = threading.Event()
    finish = threading.Event()
    modes = []
    def run(path, *, reuse_provider_cache, progress_callback):
        modes.append(reuse_provider_cache)
        started.set()
        assert finish.wait(2)
        return make_run_result()
    monkeypatch.setattr(coordinator_module, "run_monitor", run)
    coordinator = RunCoordinator(tmp_path / "monitor.yaml")
    assert coordinator.start(reuse_provider_cache=reuse).outcome is StartOutcome.STARTED
    assert started.wait(2)
    assert coordinator.start(reuse_provider_cache=not reuse).outcome is StartOutcome.ALREADY_RUNNING
    finish.set()
    join_worker(coordinator._worker)
    assert modes == [reuse]
    assert coordinator.start(reuse_provider_cache=not reuse).outcome is StartOutcome.STARTED
    join_worker(coordinator._worker)
    assert modes == [reuse, not reuse]


def test_fresh_snapshot_is_idle_empty_and_immutable(tmp_path: Path) -> None:
    coordinator = RunCoordinator(tmp_path / "monitor.yaml")

    snapshot = coordinator.snapshot()

    assert snapshot.status is CoordinatorStatus.IDLE
    assert snapshot.progress_stage is None
    assert snapshot.started_at is None
    assert snapshot.finished_at is None
    assert snapshot.result is None
    assert snapshot.unexpected_error is None
    assert snapshot.stage_index is None
    assert snapshot.stage_total == 5
    assert snapshot.current_activity is None
    assert snapshot.stage_started_at is None
    assert snapshot.last_activity_at is None
    assert not snapshot.worker_alive
    assert not snapshot.inactivity_warning
    with pytest.raises(FrozenInstanceError):
        snapshot.status = CoordinatorStatus.RUNNING  # type: ignore[misc]


def test_start_is_non_blocking_runs_off_caller_thread_and_exposes_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "monitor.yaml"
    coordinator = RunCoordinator(config_path)
    caller_ident = threading.get_ident()
    worker_threads: list[threading.Thread] = []
    first_progress = threading.Event()
    advance_progress = threading.Event()
    second_progress = threading.Event()
    finish_run = threading.Event()
    expected_result = make_run_result()

    def fake_run(
        path: Path,
        *,
        progress_callback: ProgressCallback,
        reuse_provider_cache: bool = False,
    ) -> RunResult:
        assert path == config_path
        worker_threads.append(threading.current_thread())
        # Calling snapshot from inside the worker proves run_monitor is not
        # executing under the coordinator lock.
        assert coordinator.snapshot().status is CoordinatorStatus.RUNNING
        progress_callback(ProgressEvent(stage=ProgressStage.CHECKING_MONITOR))
        first_progress.set()
        assert advance_progress.wait(timeout=2)
        progress_callback(ProgressEvent(stage=ProgressStage.DISCOVERING_PAPERS))
        second_progress.set()
        assert finish_run.wait(timeout=2)
        return expected_result

    monkeypatch.setattr(coordinator_module, "run_monitor", fake_run)

    start_result = coordinator.start()

    assert start_result.outcome is StartOutcome.STARTED
    assert first_progress.wait(timeout=2)
    assert worker_threads[0].ident != caller_ident
    first_snapshot = coordinator.snapshot()
    assert first_snapshot.status is CoordinatorStatus.RUNNING
    assert first_snapshot.progress_stage is ProgressStage.CHECKING_MONITOR
    assert first_snapshot.stage_index == 1
    assert first_snapshot.stage_total == 5
    assert first_snapshot.current_activity is None
    assert first_snapshot.stage_started_at is not None
    assert first_snapshot.last_activity_at == first_snapshot.stage_started_at
    assert first_snapshot.worker_alive
    assert not first_snapshot.inactivity_warning
    assert first_snapshot.started_at is not None
    assert first_snapshot.finished_at is None
    assert first_snapshot.result is None
    repeated_snapshot = coordinator.snapshot()
    assert repeated_snapshot.stage_started_at == first_snapshot.stage_started_at
    assert repeated_snapshot.last_activity_at == first_snapshot.last_activity_at

    advance_progress.set()
    assert second_progress.wait(timeout=2)
    second_snapshot = coordinator.snapshot()
    assert second_snapshot.status is CoordinatorStatus.RUNNING
    assert second_snapshot.progress_stage is ProgressStage.DISCOVERING_PAPERS
    assert second_snapshot.stage_index == 2
    assert second_snapshot.stage_started_at is not None
    assert second_snapshot.last_activity_at == second_snapshot.stage_started_at
    assert second_snapshot.worker_alive
    assert first_snapshot.progress_stage is ProgressStage.CHECKING_MONITOR
    assert not finish_run.is_set()

    finish_run.set()
    join_worker(worker_threads[0])

    finished = coordinator.snapshot()
    assert finished.status is CoordinatorStatus.FINISHED
    assert finished.result is expected_result
    assert finished.unexpected_error is None
    assert finished.finished_at is not None
    assert finished.started_at is not None
    assert finished.finished_at >= finished.started_at
    assert not finished.worker_alive
    assert not finished.inactivity_warning


def test_activity_event_updates_snapshot_without_read_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = RunCoordinator(tmp_path / "monitor.yaml")
    activity_reported = threading.Event()
    release = threading.Event()
    worker_threads: list[threading.Thread] = []

    def fake_run(
        path: Path,
        *,
        progress_callback: ProgressCallback,
        reuse_provider_cache: bool = False,
    ) -> RunResult:
        worker_threads.append(threading.current_thread())
        progress_callback(
            ProgressEvent(stage=ProgressStage.COMBINING_METADATA)
        )
        progress_callback(
            ProgressEvent(
                activity=ActivityUpdate(
                    kind=ActivityKind.WORKING,
                    source="crossref",
                    operation="doi_lookup",
                    label="Looking up DOI metadata",
                    current=0,
                    total=3,
                    unit="doi",
                )
            )
        )
        activity_reported.set()
        assert release.wait(timeout=2)
        return make_run_result()

    monkeypatch.setattr(coordinator_module, "run_monitor", fake_run)

    assert coordinator.start().outcome is StartOutcome.STARTED
    assert activity_reported.wait(timeout=2)
    first = coordinator.snapshot()
    second = coordinator.snapshot()

    assert first.progress_stage is ProgressStage.COMBINING_METADATA
    assert first.stage_index == 3
    assert first.current_activity is not None
    assert first.current_activity.kind is ActivityKind.WORKING
    assert first.current_activity.operation == "doi_lookup"
    assert first.current_activity.current == 0
    assert first.current_activity.total == 3
    assert first.current_activity.eta_seconds is None
    assert first.last_activity_at == first.current_activity.updated_at
    assert second.last_activity_at == first.last_activity_at
    assert second.current_activity == first.current_activity
    assert second.worker_alive

    release.set()
    join_worker(worker_threads[0])


def test_repeated_start_while_running_does_not_create_second_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = RunCoordinator(tmp_path / "monitor.yaml")
    entered = threading.Event()
    release = threading.Event()
    worker_threads: list[threading.Thread] = []
    invocation_count = 0

    def fake_run(
        path: Path,
        *,
        progress_callback: ProgressCallback,
        reuse_provider_cache: bool = False,
    ) -> RunResult:
        nonlocal invocation_count
        invocation_count += 1
        worker_threads.append(threading.current_thread())
        entered.set()
        assert release.wait(timeout=2)
        return make_run_result()

    monkeypatch.setattr(coordinator_module, "run_monitor", fake_run)

    first = coordinator.start()
    assert entered.wait(timeout=2)
    second = coordinator.start()

    assert first.outcome is StartOutcome.STARTED
    assert second.outcome is StartOutcome.ALREADY_RUNNING
    assert invocation_count == 1

    release.set()
    join_worker(worker_threads[0])


def test_two_simultaneous_callers_start_exactly_one_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = RunCoordinator(tmp_path / "monitor.yaml")
    callers_ready = threading.Barrier(3)
    worker_entered = threading.Event()
    release_worker = threading.Event()
    worker_threads: list[threading.Thread] = []
    outcomes: list[StartOutcome] = []
    invocation_count = 0

    def fake_run(
        path: Path,
        *,
        progress_callback: ProgressCallback,
        reuse_provider_cache: bool = False,
    ) -> RunResult:
        nonlocal invocation_count
        invocation_count += 1
        worker_threads.append(threading.current_thread())
        worker_entered.set()
        assert release_worker.wait(timeout=2)
        return make_run_result()

    def call_start() -> None:
        callers_ready.wait()
        outcomes.append(coordinator.start().outcome)

    monkeypatch.setattr(coordinator_module, "run_monitor", fake_run)
    callers = [threading.Thread(target=call_start) for _ in range(2)]
    for caller in callers:
        caller.start()

    callers_ready.wait()
    for caller in callers:
        join_worker(caller)
    assert worker_entered.wait(timeout=2)

    assert sorted(outcome.value for outcome in outcomes) == [
        StartOutcome.ALREADY_RUNNING.value,
        StartOutcome.STARTED.value,
    ]
    assert invocation_count == 1

    release_worker.set()
    join_worker(worker_threads[0])


@pytest.mark.parametrize(
    "outcome",
    (
        RunOutcome.INVALID_CONFIGURATION,
        RunOutcome.COMPLETED_WITH_ERRORS,
    ),
)
def test_structured_non_success_run_results_are_normal_completions(
    outcome: RunOutcome,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = RunCoordinator(tmp_path / "monitor.yaml")
    worker_entered = threading.Event()
    release = threading.Event()
    worker_threads: list[threading.Thread] = []
    expected_result = make_run_result(outcome)

    def fake_run(
        path: Path,
        *,
        progress_callback: ProgressCallback,
        reuse_provider_cache: bool = False,
    ) -> RunResult:
        worker_threads.append(threading.current_thread())
        worker_entered.set()
        assert release.wait(timeout=2)
        return expected_result

    monkeypatch.setattr(coordinator_module, "run_monitor", fake_run)

    assert coordinator.start().outcome is StartOutcome.STARTED
    assert worker_entered.wait(timeout=2)
    release.set()
    join_worker(worker_threads[0])

    snapshot = coordinator.snapshot()
    assert snapshot.status is CoordinatorStatus.FINISHED
    assert snapshot.result is expected_result
    assert snapshot.unexpected_error is None
    assert snapshot.finished_at is not None
    assert not snapshot.worker_alive


def test_unexpected_exception_is_logged_finishes_safely_and_restart_clears_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    coordinator = RunCoordinator(tmp_path / "monitor.yaml")
    first_entered = threading.Event()
    first_release = threading.Event()
    second_entered = threading.Event()
    second_release = threading.Event()
    worker_threads: list[threading.Thread] = []
    calls = 0
    second_result = make_run_result()

    def fake_run(
        path: Path,
        *,
        progress_callback: ProgressCallback,
        reuse_provider_cache: bool = False,
    ) -> RunResult:
        nonlocal calls
        calls += 1
        worker_threads.append(threading.current_thread())
        if calls == 1:
            first_entered.set()
            assert first_release.wait(timeout=2)
            raise RuntimeError("sensitive internal detail")
        second_entered.set()
        assert second_release.wait(timeout=2)
        return second_result

    monkeypatch.setattr(coordinator_module, "run_monitor", fake_run)
    caplog.set_level(logging.ERROR, logger=coordinator_module.__name__)

    assert coordinator.start().outcome is StartOutcome.STARTED
    assert first_entered.wait(timeout=2)
    first_release.set()
    join_worker(worker_threads[0])

    failed = coordinator.snapshot()
    assert failed.status is CoordinatorStatus.FINISHED
    assert failed.result is None
    assert failed.unexpected_error is not None
    assert failed.unexpected_error.category == "RuntimeError"
    assert "sensitive internal detail" not in failed.unexpected_error.message
    assert "sensitive internal detail" in caplog.text
    assert not failed.worker_alive

    assert coordinator.start().outcome is StartOutcome.STARTED
    assert second_entered.wait(timeout=2)
    restarted = coordinator.snapshot()
    assert restarted.status is CoordinatorStatus.RUNNING
    assert restarted.result is None
    assert restarted.unexpected_error is None
    assert restarted.progress_stage is None
    assert restarted.finished_at is None
    assert restarted.current_activity is None
    assert restarted.stage_started_at is None
    assert restarted.last_activity_at is None
    assert restarted.worker_alive

    second_release.set()
    join_worker(worker_threads[1])
    finished = coordinator.snapshot()
    assert finished.status is CoordinatorStatus.FINISHED
    assert finished.result is second_result
    assert finished.unexpected_error is None
    assert not finished.worker_alive


def test_restart_clears_old_result_and_second_completion_replaces_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = RunCoordinator(tmp_path / "monitor.yaml")
    run_entered = [threading.Event(), threading.Event()]
    run_release = [threading.Event(), threading.Event()]
    worker_threads: list[threading.Thread] = []
    results = [make_run_result(), make_run_result(RunOutcome.COMPLETED_WITH_WARNINGS)]
    calls = 0

    def fake_run(
        path: Path,
        *,
        progress_callback: ProgressCallback,
        reuse_provider_cache: bool = False,
    ) -> RunResult:
        nonlocal calls
        index = calls
        calls += 1
        worker_threads.append(threading.current_thread())
        progress_callback(ProgressEvent(stage=ProgressStage.CHECKING_MONITOR))
        if index == 0:
            progress_callback(
                ProgressEvent(
                    activity=ActivityUpdate(
                        kind=ActivityKind.WORKING,
                        operation="first_run_work",
                        label="First run work",
                    )
                )
            )
        run_entered[index].set()
        assert run_release[index].wait(timeout=2)
        return results[index]

    monkeypatch.setattr(coordinator_module, "run_monitor", fake_run)

    assert coordinator.start().outcome is StartOutcome.STARTED
    assert run_entered[0].wait(timeout=2)
    run_release[0].set()
    join_worker(worker_threads[0])
    first_finished = coordinator.snapshot()
    assert first_finished.result is results[0]
    assert first_finished.current_activity is not None

    assert coordinator.start().outcome is StartOutcome.STARTED
    assert run_entered[1].wait(timeout=2)
    running_again = coordinator.snapshot()
    assert running_again.status is CoordinatorStatus.RUNNING
    assert running_again.result is None
    assert running_again.unexpected_error is None
    assert running_again.finished_at is None
    assert running_again.progress_stage is ProgressStage.CHECKING_MONITOR
    assert running_again.current_activity is None
    assert running_again.stage_started_at is not None
    assert running_again.last_activity_at == running_again.stage_started_at
    assert running_again.worker_alive

    run_release[1].set()
    join_worker(worker_threads[1])
    final = coordinator.snapshot()
    assert final.result is results[1]
    assert not hasattr(coordinator, "history")
    assert not hasattr(final, "history")


def test_coordinator_reuses_application_progress_model_and_has_no_second_progress_enum() -> None:
    progress_enums = {
        name: value
        for name, value in vars(coordinator_module).items()
        if isinstance(value, type)
        and issubclass(value, Enum)
        and "Progress" in name
    }

    assert ProgressStage is SharedProgressStage
    assert coordinator_module.ProgressStage is SharedProgressStage
    assert progress_enums == {"ProgressStage": ProgressStage}


def test_base_exception_cannot_leave_dead_worker_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    coordinator = RunCoordinator(tmp_path / "monitor.yaml")
    worker_entered = threading.Event()
    worker_threads: list[threading.Thread] = []

    def fake_run(
        path: Path,
        *,
        progress_callback: ProgressCallback,
        reuse_provider_cache: bool = False,
    ) -> RunResult:
        worker_threads.append(threading.current_thread())
        worker_entered.set()
        raise SystemExit("worker exit")

    monkeypatch.setattr(coordinator_module, "run_monitor", fake_run)
    caplog.set_level(logging.ERROR, logger=coordinator_module.__name__)

    assert coordinator.start().outcome is StartOutcome.STARTED
    assert worker_entered.wait(timeout=2)
    join_worker(worker_threads[0])

    snapshot = coordinator.snapshot()
    assert snapshot.status is CoordinatorStatus.FINISHED
    assert not snapshot.worker_alive
    assert snapshot.result is None
    assert snapshot.unexpected_error is not None
    assert snapshot.unexpected_error.category == "SystemExit"
    assert "worker exit" not in snapshot.unexpected_error.message
    assert "worker exit" in caplog.text


def test_thread_start_failure_does_not_leave_coordinator_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    coordinator = RunCoordinator(tmp_path / "monitor.yaml")

    def fail_start(thread: threading.Thread) -> None:
        raise RuntimeError("thread unavailable")

    monkeypatch.setattr(coordinator_module.threading.Thread, "start", fail_start)
    caplog.set_level(logging.ERROR, logger=coordinator_module.__name__)

    result = coordinator.start()

    assert result.outcome is StartOutcome.START_FAILED
    snapshot = coordinator.snapshot()
    assert snapshot.status is CoordinatorStatus.FINISHED
    assert snapshot.result is None
    assert snapshot.unexpected_error is not None
    assert snapshot.unexpected_error.category == "RuntimeError"
    assert snapshot.started_at is not None
    assert snapshot.finished_at is not None
    assert not snapshot.worker_alive
    assert "thread unavailable" not in snapshot.unexpected_error.message
    assert "thread unavailable" in caplog.text
