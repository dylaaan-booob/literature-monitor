from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest

from capture_helpers import reservation
from literature_monitor.application.export_attempts import resolve_export_completion
import literature_monitor.web.capture_coordinator as capture_module
from literature_monitor.web.capture_coordinator import (
    ACTIVE_TIMEOUT_SECONDS,
    HEARTBEAT_STALE_SECONDS,
    WAITING_TIMEOUT_SECONDS,
    CaptureCoordinator,
    CaptureOutcome,
    PdfOutcome,
    SaveInvocation,
    CaptureStage,
    CaptureStartOutcome,
    ConnectorReadiness,
)


class FakeClock:
    def __init__(self) -> None:
        self.monotonic_value = 100.0
        self.wall_value = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.monotonic_value

    def utc_now(self) -> datetime:
        return self.wall_value

    def advance(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.wall_value += timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    current = FakeClock()
    monkeypatch.setattr(capture_module, "_monotonic", current.monotonic)
    monkeypatch.setattr(capture_module, "_utc_now", current.utc_now)
    return current


def connect(coordinator: CaptureCoordinator) -> None:
    assert coordinator.heartbeat(
        connector_version="1.0.0",
        zotero_reachable=True,
    ) is ConnectorReadiness.CONNECTED


@pytest.fixture(autouse=True)
def reservation_root(tmp_path):
    global TEST_ROOT
    TEST_ROOT = tmp_path / "reservations"


def capture_guard(*, paper_id=None, doi="10.1000/example"):
    return reservation(TEST_ROOT, paper_id=paper_id, doi=doi)


def start(
    coordinator: CaptureCoordinator,
    *,
    paper_id: UUID | None = None,
    doi: str = "10.1000/example",
):
    return coordinator.start_capture(capture_guard(paper_id=paper_id, doi=doi))


def claim(coordinator: CaptureCoordinator):
    command = coordinator.claim_command()
    assert command is not None
    return command


def submit_result(coordinator, command, outcome, *, request_id=None):
    identity = dict(
        request_id=request_id if request_id is not None else command.request_id,
        invocation_id=command.invocation_id, doi_url=command.doi_url,
        tab_id=100, session_id="native-session",
    )
    if outcome is CaptureOutcome.CONFIRMED:
        native = SaveInvocation(**identity)
        if coordinator.authorize_parent_dispatch(native):
            assert coordinator.report_native_save_settled(native)
    return coordinator.submit_connector_result(
        **identity, outcome=outcome, pdf_outcome=PdfOutcome.UNVERIFIED,
    )


def test_fresh_state_is_unavailable_idle_and_immutable(clock: FakeClock) -> None:
    coordinator = CaptureCoordinator()

    snapshot = coordinator.snapshot()

    assert snapshot.readiness is ConnectorReadiness.UNAVAILABLE
    assert snapshot.connector_version is None
    assert snapshot.zotero_reachable is False
    assert snapshot.last_heartbeat_at is None
    assert snapshot.attempt is None
    assert snapshot.completion_pending is False
    with pytest.raises(FrozenInstanceError):
        snapshot.readiness = ConnectorReadiness.CONNECTED  # type: ignore[misc]


def test_heartbeat_requires_recent_reachable_presence(clock: FakeClock) -> None:
    coordinator = CaptureCoordinator()

    assert coordinator.heartbeat(
        connector_version=" 1.2.3 ",
        zotero_reachable=False,
    ) is ConnectorReadiness.UNAVAILABLE
    unreachable = coordinator.snapshot()
    assert unreachable.connector_version == "1.2.3"
    assert unreachable.zotero_reachable is False

    assert coordinator.heartbeat(
        connector_version="1.2.4",
        zotero_reachable=True,
    ) is ConnectorReadiness.CONNECTED
    assert coordinator.snapshot().readiness is ConnectorReadiness.CONNECTED

    clock.advance(HEARTBEAT_STALE_SECONDS + 0.001)

    stale = coordinator.snapshot()
    assert stale.readiness is ConnectorReadiness.UNAVAILABLE
    assert stale.connector_version == "1.2.4"
    assert stale.zotero_reachable is True


def test_new_coordinator_loses_presence_and_attempt_state(clock: FakeClock) -> None:
    first = CaptureCoordinator()
    connect(first)
    assert start(first).outcome is CaptureStartOutcome.STARTED

    restarted = CaptureCoordinator()

    assert restarted.snapshot().readiness is ConnectorReadiness.UNAVAILABLE
    assert restarted.snapshot().attempt is None
    assert restarted.consume_completion() is None


def test_start_rejects_unavailable_or_non_normalized_doi(clock: FakeClock) -> None:
    coordinator = CaptureCoordinator()

    unavailable = start(coordinator)
    assert unavailable.outcome is CaptureStartOutcome.CONNECTOR_UNAVAILABLE
    assert coordinator.snapshot().attempt is None

    connect(coordinator)
    for doi in ("", " 10.1000/example ", "10.1000/EXAMPLE", "https://doi.org/10.1000/example"):
        result = start(coordinator, doi=doi)
        assert result.outcome is CaptureStartOutcome.INVALID_DOI
        assert coordinator.snapshot().attempt is None


def test_single_active_start_is_serialized_under_concurrency(clock: FakeClock) -> None:
    coordinator = CaptureCoordinator()
    connect(coordinator)
    barrier = threading.Barrier(3)
    outcomes: list[CaptureStartOutcome] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=2)
            outcomes.append(start(coordinator).outcome)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert errors == []
    assert sorted(outcome.value for outcome in outcomes) == [
        CaptureStartOutcome.ALREADY_ACTIVE.value,
        CaptureStartOutcome.STARTED.value,
    ]
    assert coordinator.snapshot().attempt is not None


def test_request_ids_are_opaque_distinct_and_doi_target_is_server_built(clock: FakeClock) -> None:
    coordinator = CaptureCoordinator()
    connect(coordinator)
    paper_id = UUID("11111111-1111-4111-8111-111111111111")
    doi = "10.1000/a?b#c(d)"

    first = coordinator.start_capture(capture_guard(paper_id=paper_id, doi=doi))
    assert first.outcome is CaptureStartOutcome.STARTED
    assert first.request_id is not None
    first_command = claim(coordinator)
    assert first_command.request_id == first.request_id
    assert first_command.doi_url == "https://doi.org/10.1000/a%3Fb%23c%28d%29"
    assert set(first_command.__dataclass_fields__) == {"request_id", "doi_url", "invocation_id", "access_context"}
    assert first_command.access_context is None
    assert str(paper_id) not in first.request_id
    assert doi not in first.request_id
    assert submit_result(coordinator, first_command, CaptureOutcome.FAILED)

    resolve_export_completion(coordinator.consume_completion(), coordinator=coordinator)
    second = start(coordinator)
    assert second.outcome is CaptureStartOutcome.STARTED
    assert second.request_id is not None
    assert second.request_id != first.request_id


def test_pending_command_is_claimed_exactly_once(clock: FakeClock) -> None:
    coordinator = CaptureCoordinator()
    connect(coordinator)
    started = start(coordinator)
    assert started.outcome is CaptureStartOutcome.STARTED

    command = coordinator.claim_command()

    assert command is not None
    assert command.request_id == started.request_id
    assert coordinator.claim_command() is None
    snapshot = coordinator.snapshot()
    assert snapshot.attempt is not None
    assert snapshot.attempt.stage is CaptureStage.CONNECTOR_ACTIVE
    assert snapshot.attempt.claimed_at is not None


def test_concurrent_claims_return_one_command(clock: FakeClock) -> None:
    coordinator = CaptureCoordinator()
    connect(coordinator)
    assert start(coordinator).outcome is CaptureStartOutcome.STARTED
    barrier = threading.Barrier(3)
    commands = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=2)
            commands.append(coordinator.claim_command())
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert errors == []
    assert sum(command is not None for command in commands) == 1


def test_url_like_normalized_input_cannot_override_doi_authority(clock: FakeClock) -> None:
    coordinator = CaptureCoordinator()
    connect(coordinator)

    started = start(coordinator, doi="https://evil.example/path?x#y")

    assert started.outcome is CaptureStartOutcome.INVALID_DOI
    assert coordinator.claim_command() is None


def test_waiting_timeout_delivers_uncertain_completion(clock: FakeClock) -> None:
    coordinator = CaptureCoordinator()
    connect(coordinator)
    first = start(coordinator)
    assert first.outcome is CaptureStartOutcome.STARTED

    clock.advance(WAITING_TIMEOUT_SECONDS)
    expired = coordinator.snapshot()

    assert expired.attempt is not None
    assert expired.attempt.stage is CaptureStage.FINISHED
    assert expired.attempt.terminal_outcome is CaptureOutcome.UNCONFIRMED
    assert expired.completion_pending
    completion = coordinator.consume_completion()
    resolve_export_completion(completion, coordinator=coordinator)
    assert coordinator.claim_command() is None


def test_claimed_timeout_becomes_one_shot_unconfirmed_completion(clock: FakeClock) -> None:
    coordinator = CaptureCoordinator()
    connect(coordinator)
    identity = capture_guard(
        paper_id=UUID("22222222-2222-4222-8222-222222222222"),
    )
    started = coordinator.start_capture(identity)
    command = claim(coordinator)

    clock.advance(ACTIVE_TIMEOUT_SECONDS)
    expired = coordinator.snapshot()

    assert expired.attempt is not None
    assert expired.attempt.stage is CaptureStage.FINISHED
    assert expired.attempt.terminal_outcome is CaptureOutcome.UNCONFIRMED
    assert expired.completion_pending
    assert start(coordinator).outcome is CaptureStartOutcome.COMPLETION_PENDING

    completion = coordinator.consume_completion()
    assert completion is not None
    assert completion.request_id == command.request_id == started.request_id
    assert completion.paper_id == UUID("22222222-2222-4222-8222-222222222222")
    assert completion.normalized_doi == "10.1000/example"
    assert completion.export_reservation is identity
    assert completion.outcome is CaptureOutcome.UNCONFIRMED
    assert coordinator.consume_completion() is None


@pytest.mark.parametrize(
    ("outcome", "has_completion"),
    (
        (CaptureOutcome.CONFIRMED, True),
        (CaptureOutcome.UNCONFIRMED, True),
        (CaptureOutcome.FAILED, True),
    ),
)
def test_terminal_results_finish_current_claimed_attempt_once(
    clock: FakeClock,
    outcome: CaptureOutcome,
    has_completion: bool,
) -> None:
    coordinator = CaptureCoordinator()
    connect(coordinator)
    started = start(coordinator)
    command = claim(coordinator)

    assert submit_result(coordinator, command, outcome)
    assert not submit_result(coordinator, command, outcome)

    snapshot = coordinator.snapshot()
    assert snapshot.attempt is not None
    assert snapshot.attempt.stage is CaptureStage.FINISHED
    assert snapshot.attempt.terminal_outcome is outcome
    assert snapshot.completion_pending is has_completion

    if has_completion:
        assert start(coordinator).outcome is CaptureStartOutcome.COMPLETION_PENDING
    completion = coordinator.consume_completion()
    if has_completion:
        assert completion is not None
        assert completion.outcome is outcome
        assert completion.request_id == started.request_id
    else:
        assert completion is None
    assert coordinator.consume_completion() is None


def test_stale_or_unknown_result_cannot_finish_current_attempt(clock: FakeClock) -> None:
    coordinator = CaptureCoordinator()
    connect(coordinator)

    first = start(coordinator)
    first_command = claim(coordinator)
    assert submit_result(coordinator, first_command, CaptureOutcome.FAILED)

    resolve_export_completion(coordinator.consume_completion(), coordinator=coordinator)
    second = start(coordinator)
    second_command = claim(coordinator)
    assert first.request_id != second.request_id

    assert not submit_result(coordinator, first_command, CaptureOutcome.CONFIRMED)
    assert not submit_result(coordinator, second_command, CaptureOutcome.CONFIRMED, request_id="unknown-request")
    current = coordinator.snapshot()
    assert current.attempt is not None
    assert current.attempt.request_id == second_command.request_id
    assert current.attempt.stage is CaptureStage.CONNECTOR_ACTIVE
    assert current.attempt.terminal_outcome is None


def test_malformed_or_oversized_result_cannot_change_state(clock: FakeClock) -> None:
    coordinator = CaptureCoordinator()
    connect(coordinator)
    started = start(coordinator)
    command = claim(coordinator)

    assert not submit_result(coordinator, command, CaptureOutcome.CONFIRMED, request_id="x" * 1000)
    assert not submit_result(coordinator, command, "CONFIRMED")  # type: ignore[arg-type]

    snapshot = coordinator.snapshot()
    assert snapshot.attempt is not None
    assert snapshot.attempt.request_id == started.request_id
    assert snapshot.attempt.stage is CaptureStage.CONNECTOR_ACTIVE
    assert snapshot.attempt.terminal_outcome is None


def test_coordinator_never_writes_filesystem_state(
    clock: FakeClock,
    tmp_path: Path,
) -> None:
    sentinel = tmp_path / "paper.md"
    sentinel.write_text("human-owned\n", encoding="utf-8")

    coordinator = CaptureCoordinator()
    connect(coordinator)
    started = start(coordinator)
    command = claim(coordinator)
    assert submit_result(coordinator, command, CaptureOutcome.CONFIRMED)
    assert coordinator.consume_completion() is not None
    assert started.request_id == command.request_id

    assert sentinel.read_text(encoding="utf-8") == "human-owned\n"
