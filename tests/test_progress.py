from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import FrozenInstanceError, replace

import pytest

from literature_monitor.progress import (
    PROGRESS_STAGES,
    ActivityKind,
    ActivityUpdate,
    ProgressEvent,
    ProgressStage,
    ProgressState,
)

BASE_TIME = datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc)


def moment(seconds: int) -> datetime:
    return BASE_TIME + timedelta(seconds=seconds)


def working(
    current: int | None,
    *,
    total: int | None = 5,
    source: str | None = "crossref",
    operation: str = "doi_lookup",
    unit: str | None = "doi",
    detail: str | None = None,
) -> ProgressEvent:
    return ProgressEvent(
        activity=ActivityUpdate(
            kind=ActivityKind.WORKING,
            source=source,
            operation=operation,
            label="Looking up DOI metadata",
            detail=detail,
            current=current,
            total=total,
            unit=unit,
        )
    )


def test_stage_order_and_indices_are_stable() -> None:
    assert PROGRESS_STAGES == (
        ProgressStage.CHECKING_MONITOR,
        ProgressStage.DISCOVERING_PAPERS,
        ProgressStage.COMBINING_METADATA,
        ProgressStage.MATCHING_LITERATURE,
        ProgressStage.UPDATING_WORKSPACE,
    )

    state = ProgressState()
    for index, stage in enumerate(PROGRESS_STAGES, start=1):
        assert state.apply(ProgressEvent(stage=stage), at=moment(index))
        snapshot = state.snapshot(at=moment(index), active=True)
        assert snapshot.progress_stage is stage
        assert snapshot.stage_index == index
        assert snapshot.stage_total == 5
        assert snapshot.stage_started_at == moment(index)
        assert snapshot.last_activity_at == moment(index)

    previous = state.snapshot(at=moment(10), active=True)
    assert not state.apply(
        ProgressEvent(stage=ProgressStage.UPDATING_WORKSPACE),
        at=moment(11),
    )
    repeated = state.snapshot(at=moment(12), active=True)
    assert repeated.stage_started_at == previous.stage_started_at
    assert repeated.last_activity_at == previous.last_activity_at


def test_activity_snapshot_and_eta_require_two_real_advancements() -> None:
    state = ProgressState()
    state.apply(
        ProgressEvent(stage=ProgressStage.COMBINING_METADATA),
        at=moment(0),
    )
    state.apply(working(0), at=moment(1))

    initial = state.snapshot(at=moment(1), active=True).current_activity
    assert initial is not None
    assert initial.started_at == moment(1)
    assert initial.updated_at == moment(1)
    assert initial.rate is None
    assert initial.eta_seconds is None

    state.apply(working(1, detail="first"), at=moment(2))
    one_sample = state.snapshot(at=moment(2), active=True).current_activity
    assert one_sample is not None
    assert one_sample.rate == pytest.approx(1.0)
    assert one_sample.eta_seconds is None

    state.apply(working(2, detail="second"), at=moment(3))
    estimated = state.snapshot(at=moment(3), active=True).current_activity
    assert estimated is not None
    assert estimated.started_at == moment(1)
    assert estimated.updated_at == moment(3)
    assert estimated.detail == "second"
    assert estimated.rate == pytest.approx(1.0)
    assert estimated.eta_seconds == pytest.approx(3.0)


def test_activity_identity_change_resets_started_at_rate_and_eta() -> None:
    state = ProgressState()
    state.apply(
        ProgressEvent(stage=ProgressStage.COMBINING_METADATA),
        at=moment(0),
    )
    state.apply(working(0), at=moment(1))
    state.apply(working(1), at=moment(2))
    state.apply(working(2), at=moment(3))
    assert (
        state.snapshot(at=moment(3), active=True).current_activity.eta_seconds
        is not None
    )

    state.apply(
        working(
            0,
            operation="journal_discovery",
            unit="journal",
        ),
        at=moment(4),
    )
    reset = state.snapshot(at=moment(4), active=True).current_activity

    assert reset is not None
    assert reset.operation == "journal_discovery"
    assert reset.started_at == moment(4)
    assert reset.rate is None
    assert reset.eta_seconds is None


def test_unknown_total_never_produces_eta() -> None:
    state = ProgressState()
    state.apply(
        ProgressEvent(stage=ProgressStage.COMBINING_METADATA),
        at=moment(0),
    )
    state.apply(working(0, total=None), at=moment(1))
    state.apply(working(1, total=None), at=moment(2))
    state.apply(working(2, total=None), at=moment(3))

    activity = state.snapshot(at=moment(3), active=True).current_activity
    assert activity is not None
    assert activity.rate == pytest.approx(1.0)
    assert activity.eta_seconds is None


def test_zero_time_and_non_advancing_events_do_not_create_rate_samples() -> None:
    state = ProgressState()
    state.apply(
        ProgressEvent(stage=ProgressStage.COMBINING_METADATA),
        at=moment(0),
    )
    state.apply(working(0), at=moment(1))
    state.apply(working(1), at=moment(1))
    state.apply(working(1, detail="still working"), at=moment(2))

    after_zero_time = state.snapshot(at=moment(2), active=True).current_activity
    assert after_zero_time is not None
    assert after_zero_time.rate is None
    assert after_zero_time.eta_seconds is None

    state.apply(working(2), at=moment(3))
    one_sample = state.snapshot(at=moment(3), active=True).current_activity
    assert one_sample is not None
    assert one_sample.rate is not None
    assert one_sample.eta_seconds is None


def test_waiting_and_retrying_events_do_not_manufacture_speed() -> None:
    state = ProgressState()
    state.apply(
        ProgressEvent(stage=ProgressStage.COMBINING_METADATA),
        at=moment(0),
    )
    state.apply(working(0), at=moment(1))
    state.apply(
        ProgressEvent(
            activity=ActivityUpdate(
                kind=ActivityKind.WAITING,
                source="crossref",
                operation="doi_lookup",
                label="Waiting for Crossref",
                current=0,
                total=5,
                unit="doi",
            )
        ),
        at=moment(2),
    )
    waiting = state.snapshot(at=moment(2), active=True).current_activity
    assert waiting is not None
    assert waiting.rate is None
    assert waiting.eta_seconds is None

    state.apply(
        ProgressEvent(
            activity=ActivityUpdate(
                kind=ActivityKind.RETRYING,
                source="crossref",
                operation="doi_lookup",
                label="Retrying Crossref",
                current=1,
                total=5,
                unit="doi",
            )
        ),
        at=moment(3),
    )
    retrying = state.snapshot(at=moment(3), active=True).current_activity
    assert retrying is not None
    assert retrying.rate is None
    assert retrying.eta_seconds is None

    state.apply(working(2), at=moment(4))
    assert (
        state.snapshot(at=moment(4), active=True).current_activity.eta_seconds
        is None
    )
    state.apply(working(3), at=moment(5))
    recovered = state.snapshot(at=moment(5), active=True).current_activity
    assert recovered is not None
    assert recovered.eta_seconds is not None


def test_inactivity_invalidates_eta_without_mutating_last_activity() -> None:
    state = ProgressState()
    state.apply(
        ProgressEvent(stage=ProgressStage.COMBINING_METADATA),
        at=moment(0),
    )
    state.apply(working(0, total=8), at=moment(1))
    state.apply(working(1, total=8), at=moment(2))
    state.apply(working(2, total=8), at=moment(3))

    before = state.snapshot(at=moment(3), active=True)
    assert before.current_activity is not None
    assert before.current_activity.eta_seconds is not None
    assert before.last_activity_at == moment(3)

    inactive = state.snapshot(at=moment(63), active=True)
    assert inactive.inactivity_warning
    assert inactive.current_activity is not None
    assert inactive.current_activity.rate is None
    assert inactive.current_activity.eta_seconds is None
    assert inactive.last_activity_at == moment(3)

    repeated_read = state.snapshot(at=moment(70), active=True)
    assert repeated_read.last_activity_at == moment(3)
    assert repeated_read.stage_started_at == before.stage_started_at

    state.apply(working(3, total=8), at=moment(71))
    recovered = state.snapshot(at=moment(71), active=True)
    assert not recovered.inactivity_warning
    assert recovered.current_activity is not None
    assert recovered.current_activity.rate is None
    assert recovered.current_activity.eta_seconds is None

    state.apply(working(4, total=8), at=moment(72))
    assert (
        state.snapshot(at=moment(72), active=True).current_activity.eta_seconds
        is None
    )
    state.apply(working(5, total=8), at=moment(73))
    resumed = state.snapshot(at=moment(73), active=True)
    assert resumed.current_activity is not None
    assert resumed.current_activity.eta_seconds is not None


def test_inactivity_is_only_advisory_for_active_context() -> None:
    state = ProgressState()
    state.apply(
        ProgressEvent(stage=ProgressStage.DISCOVERING_PAPERS),
        at=moment(0),
    )

    assert state.snapshot(at=moment(60), active=True).inactivity_warning
    assert not state.snapshot(at=moment(60), active=False).inactivity_warning
    assert state.last_activity_at == moment(0)


def test_total_change_or_current_rollback_invalidates_old_estimator() -> None:
    state = ProgressState()
    state.apply(
        ProgressEvent(stage=ProgressStage.COMBINING_METADATA),
        at=moment(0),
    )
    state.apply(working(0, total=5), at=moment(1))
    state.apply(working(1, total=5), at=moment(2))
    state.apply(working(2, total=5), at=moment(3))
    assert (
        state.snapshot(at=moment(3), active=True).current_activity.eta_seconds
        is not None
    )

    state.apply(working(2, total=6), at=moment(4))
    changed_total = state.snapshot(at=moment(4), active=True).current_activity
    assert changed_total is not None
    assert changed_total.rate is None
    assert changed_total.eta_seconds is None

    state.apply(working(1, total=6), at=moment(5))
    rollback = state.snapshot(at=moment(5), active=True).current_activity
    assert rollback is not None
    assert rollback.rate is None
    assert rollback.eta_seconds is None


def source_activities(state, seconds):
    return {item.source: item for item in state.snapshot(at=moment(seconds), active=True).activities}


def paired_state():
    state = ProgressState()
    state.apply(ProgressEvent(stage=ProgressStage.DISCOVERING_PAPERS), at=moment(0))
    for seconds in range(3):
        for source in ("crossref", "openalex"):
            state.apply(working(seconds, source=source, total=20), at=moment(seconds))
    return state


@pytest.mark.parametrize("source", ["openalex", "crossref"])
@pytest.mark.parametrize("change", ["advance", "operation", "unit", "total", "rollback", "retry"])
def test_each_source_update_preserves_peer_activity_and_samples(source, change):
    state = paired_state()
    peer = "crossref" if source == "openalex" else "openalex"
    before = source_activities(state, 2)
    assert before[peer].eta_seconds is not None
    update = working(3, source=source, total=20).activity
    if change == "operation":
        update = replace(update, operation="next_operation")
    elif change == "unit":
        update = replace(update, unit="journal")
    elif change == "total":
        update = replace(update, total=30)
    elif change == "rollback":
        update = replace(update, current=1)
    elif change == "retry":
        update = replace(update, kind=ActivityKind.RETRYING)
    state.apply(ProgressEvent(activity=update), at=moment(3))
    after = source_activities(state, 3)
    assert after[peer] == before[peer]
    if change != "advance":
        assert after[source].eta_seconds is None
    else:
        assert after[source].eta_seconds is not None
    state.apply(working(3, source=peer, total=20), at=moment(4))
    assert source_activities(state, 4)[peer].eta_seconds is not None


def test_per_source_order_immutable_snapshots_and_stage_reset():
    state = ProgressState()
    sources = ("z-custom", None, "workspace", "crossref", "openalex", "application", "a-custom")
    for source in sources:
        state.apply(working(0, source=source), at=moment(0))
    first = state.snapshot(at=moment(0), active=True)
    assert tuple(item.source for item in first.activities) == (
        "application", "openalex", "crossref", "workspace", None, "a-custom", "z-custom",
    )
    assert first.current_activity.source == "application"  # deterministic equal-time tie
    with pytest.raises(FrozenInstanceError):
        first.activities[0].current = 1
    state.apply(ProgressEvent(stage=ProgressStage.MATCHING_LITERATURE), at=moment(5))
    cleared = state.snapshot(at=moment(6), active=True)
    assert cleared.activities == () and cleared.current_activity is None
    assert cleared.stage_started_at == cleared.last_activity_at == moment(5)
    assert not state.apply(ProgressEvent(stage=ProgressStage.MATCHING_LITERATURE), at=moment(7))
    assert state.snapshot(at=moment(8), active=True) == cleared
    assert len(first.activities) == len(sources)


def test_peer_activity_prevents_inactivity_and_recovery_invalidates_all_sources():
    state = paired_state()
    quiet = source_activities(state, 2)["openalex"]
    for seconds in (20, 40, 60, 67):
        state.apply(working(3, source="crossref", total=20), at=moment(seconds))
    active = state.snapshot(at=moment(72), active=True)
    assert not active.inactivity_warning
    assert active.last_activity_at == moment(67)
    assert source_activities(state, 72)["openalex"] == quiet
    inactive = state.snapshot(at=moment(127), active=True)
    assert inactive.inactivity_warning and len(inactive.activities) == 2
    assert all(item.eta_seconds is None and item.rate is None for item in inactive.activities)
    assert state.snapshot(at=moment(140), active=True).last_activity_at == moment(67)
    state.apply(working(3, source="openalex", total=20), at=moment(141))
    resumed = state.snapshot(at=moment(141), active=True)
    assert not resumed.inactivity_warning
    assert source_activities(state, 141)["openalex"].eta_seconds is None
    assert source_activities(state, 141)["crossref"].updated_at == moment(67)
    assert source_activities(state, 141)["crossref"].eta_seconds is None
    assert source_activities(state, 141)["crossref"].rate is None
    state.apply(working(4, source="openalex", total=20), at=moment(142))
    assert source_activities(state, 142)["openalex"].eta_seconds is None
    state.apply(working(5, source="openalex", total=20), at=moment(143))
    assert source_activities(state, 143)["openalex"].eta_seconds is not None
    state.apply(working(4, source="crossref", total=20), at=moment(140))
    assert state.last_activity_at == moment(143)


def test_whole_run_recovery_never_resurrects_quiet_source_eta():
    state = ProgressState()
    state.apply(ProgressEvent(stage=ProgressStage.DISCOVERING_PAPERS), at=moment(0))
    for current in range(3):
        for source in ("openalex", "crossref"):
            state.apply(working(current, source=source, total=10, detail="current batch"), at=moment(current + 1))
    before = state.snapshot(at=moment(3), active=True)
    assert all(activity.eta_seconds == pytest.approx(8) for activity in before.activities)
    stored_before = {source: vars(source_state).copy() for source, source_state in state._sources.items()}
    for _ in range(2):
        inactive = state.snapshot(at=moment(63), active=True)
        assert inactive.inactivity_warning
        assert all(activity.rate is None and activity.eta_seconds is None for activity in inactive.activities)
        assert inactive.last_activity_at == moment(3)
    assert {source: vars(source_state) for source, source_state in state._sources.items()} == stored_before
    assert state.snapshot(at=moment(3), active=True) == before

    state.apply(working(3, source="openalex", total=10), at=moment(64))
    recovered = state.snapshot(at=moment(64), active=True)
    assert not recovered.inactivity_warning and recovered.last_activity_at == moment(64)
    assert recovered.stage_started_at == before.stage_started_at
    assert all(activity.rate is None and activity.eta_seconds is None for activity in recovered.activities)
    quiet_before = next(activity for activity in before.activities if activity.source == "crossref")
    quiet_after = source_activities(state, 64)["crossref"]
    assert quiet_after == replace(quiet_before, rate=None, eta_seconds=None)

    state.apply(working(4, source="openalex", total=10), at=moment(65))
    assert source_activities(state, 65)["openalex"].eta_seconds is None
    state.apply(working(5, source="openalex", total=10), at=moment(66))
    assert source_activities(state, 66)["openalex"].eta_seconds is not None
    assert source_activities(state, 66)["crossref"] == quiet_after
    for current, seconds in ((3, 67), (4, 68)):
        state.apply(working(current, source="crossref", total=10), at=moment(seconds))
        assert source_activities(state, seconds)["crossref"].eta_seconds is None
    state.apply(working(5, source="crossref", total=10), at=moment(69))
    fresh = source_activities(state, 69)["crossref"]
    assert fresh.rate == pytest.approx(1) and fresh.eta_seconds == pytest.approx(5)
    assert fresh.started_at == quiet_before.started_at
