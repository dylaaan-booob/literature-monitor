"""Shared transient runtime progress and activity state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum

DEFAULT_INACTIVITY_SECONDS = 60.0
DEFAULT_RATE_SMOOTHING = 0.3


class ProgressStage(str, Enum):
    CHECKING_MONITOR = "CHECKING_MONITOR"
    DISCOVERING_PAPERS = "DISCOVERING_PAPERS"
    COMBINING_METADATA = "COMBINING_METADATA"
    MATCHING_LITERATURE = "MATCHING_LITERATURE"
    UPDATING_WORKSPACE = "UPDATING_WORKSPACE"


PROGRESS_STAGES = (
    ProgressStage.CHECKING_MONITOR,
    ProgressStage.DISCOVERING_PAPERS,
    ProgressStage.COMBINING_METADATA,
    ProgressStage.MATCHING_LITERATURE,
    ProgressStage.UPDATING_WORKSPACE,
)


class ActivityKind(str, Enum):
    WORKING = "WORKING"
    WAITING = "WAITING"
    RETRYING = "RETRYING"


@dataclass(frozen=True)
class ActivityUpdate:
    kind: ActivityKind
    operation: str
    label: str
    source: str | None = None
    detail: str | None = None
    current: int | None = None
    total: int | None = None
    unit: str | None = None


@dataclass(frozen=True)
class ProgressEvent:
    stage: ProgressStage | None = None
    activity: ActivityUpdate | None = None

    def __post_init__(self) -> None:
        if self.stage is None and self.activity is None:
            raise ValueError("progress event must contain a stage or activity")


ProgressCallback = Callable[[ProgressEvent], None]


@dataclass(frozen=True)
class ActivitySnapshot:
    kind: ActivityKind
    operation: str
    label: str
    source: str | None
    detail: str | None
    current: int | None
    total: int | None
    unit: str | None
    started_at: datetime
    updated_at: datetime
    rate: float | None
    eta_seconds: float | None


@dataclass(frozen=True)
class ProgressStateSnapshot:
    progress_stage: ProgressStage | None
    stage_index: int | None
    stage_total: int
    activities: tuple[ActivitySnapshot, ...]
    stage_started_at: datetime | None
    last_activity_at: datetime | None
    inactivity_warning: bool

    @property
    def current_activity(self) -> ActivitySnapshot | None:
        return max(self.activities, key=lambda activity: activity.updated_at, default=None)


ActivityIdentity = tuple[ProgressStage | None, str | None, str, str | None]


class ProgressState:
    """Track one run's transient progress without owning clocks or persistence."""

    def __init__(
        self,
        *,
        smoothing: float = DEFAULT_RATE_SMOOTHING,
        inactivity_seconds: float = DEFAULT_INACTIVITY_SECONDS,
    ) -> None:
        if not 0.0 < smoothing <= 1.0:
            raise ValueError("smoothing must be greater than 0 and at most 1")
        if inactivity_seconds <= 0:
            raise ValueError("inactivity_seconds must be greater than 0")

        self._smoothing = smoothing
        self._inactivity_seconds = inactivity_seconds
        self.progress_stage: ProgressStage | None = None
        self.stage_started_at: datetime | None = None
        self.last_activity_at: datetime | None = None
        self._sources: dict[str | None, _SourceActivityState] = {}

    def apply(self, event: ProgressEvent, *, at: datetime) -> bool:
        """Apply one real worker event; callers serialize updates and snapshots."""
        stage_changed = event.stage is not None and event.stage is not self.progress_stage
        if not stage_changed and event.activity is None:
            return False

        if self._is_inactive_at(at):
            # Recovery invalidates every old estimate without inventing source events.
            for state in self._sources.values():
                state.invalidate_estimator()
        if stage_changed:
            self.progress_stage = event.stage
            self.stage_started_at = at
            self._sources.clear()

        if event.activity is not None:
            source = event.activity.source
            if source not in self._sources:
                self._sources[source] = _SourceActivityState(self._smoothing)
            state = self._sources[source]
            state.apply(event.activity, stage=self.progress_stage, at=at)

        self.last_activity_at = max(self.last_activity_at, at) if self.last_activity_at else at
        return True

    def snapshot(self, *, at: datetime, active: bool) -> ProgressStateSnapshot:
        """Return an immutable view; reading never creates worker activity."""
        inactivity_warning = active and self._is_inactive_at(at)
        source_order = {"application": 0, "openalex": 1, "crossref": 2, "workspace": 3, None: 4}
        activities = tuple(
            state.current_activity
            for source, state in sorted(
                self._sources.items(), key=lambda item: (source_order.get(item[0], 5), item[0] or ""),
            )
            if state.current_activity is not None
        )
        if inactivity_warning:
            activities = tuple(replace(activity, rate=None, eta_seconds=None) for activity in activities)
        return ProgressStateSnapshot(
            progress_stage=self.progress_stage,
            stage_index=PROGRESS_STAGES.index(self.progress_stage) + 1 if self.progress_stage else None,
            stage_total=len(PROGRESS_STAGES),
            activities=activities,
            stage_started_at=self.stage_started_at,
            last_activity_at=self.last_activity_at,
            inactivity_warning=inactivity_warning,
        )

    def _is_inactive_at(self, at: datetime) -> bool:
        return self.last_activity_at is not None and (
            at - self.last_activity_at
        ).total_seconds() >= self._inactivity_seconds


class _SourceActivityState:
    """One source's Activity identity and EWMA sampling history."""

    def __init__(self, smoothing: float) -> None:
        self._smoothing = smoothing
        self.current_activity: ActivitySnapshot | None = None
        self._activity_identity: ActivityIdentity | None = None
        self._sample_current: int | None = None
        self._sample_at: datetime | None = None
        self._rate: float | None = None
        self._valid_samples = 0
        self._sampled_units = 0

    def apply(
        self, update: ActivityUpdate, *, stage: ProgressStage | None, at: datetime,
    ) -> None:
        identity: ActivityIdentity = (
            stage,
            update.source,
            update.operation,
            update.unit,
        )
        previous = (
            self.current_activity
            if self._activity_identity == identity
            else None
        )
        identity_changed = identity != self._activity_identity

        if identity_changed:
            self._activity_identity = identity
            self._reset_estimator()
            started_at = at
            self._set_sample_baseline(update.current, at=at)
        else:
            assert previous is not None
            started_at = previous.started_at
            estimator_invalid = (
                previous.total != update.total
                or (
                    previous.current is not None
                    and update.current is None
                )
                or (
                    previous.current is not None
                    and update.current is not None
                    and update.current < previous.current
                )
            )
            if estimator_invalid:
                self._reset_estimator()
                self._set_sample_baseline(update.current, at=at)
            else:
                self._sample_advancement(update, at=at)

        rate = self._rate if update.kind is ActivityKind.WORKING else None
        eta_seconds = (
            self._estimate_eta(update.current, update.total)
            if update.kind is ActivityKind.WORKING
            else None
        )
        self.current_activity = ActivitySnapshot(
            kind=update.kind,
            source=update.source,
            operation=update.operation,
            label=update.label,
            detail=update.detail,
            current=update.current,
            total=update.total,
            unit=update.unit,
            started_at=started_at,
            updated_at=at,
            rate=rate,
            eta_seconds=eta_seconds,
        )

    def _sample_advancement(self, update: ActivityUpdate, *, at: datetime) -> None:
        current = update.current
        if current is None:
            return
        if self._sample_current is None or self._sample_at is None:
            self._set_sample_baseline(current, at=at)
            return
        if current == self._sample_current:
            return
        if current < self._sample_current:
            self._reset_estimator()
            self._set_sample_baseline(current, at=at)
            return

        elapsed = (at - self._sample_at).total_seconds()
        if update.kind is not ActivityKind.WORKING or elapsed <= 0:
            # 非工作状态或零时长推进无法形成可信速度，只把当前位置作为新基线。
            self._reset_estimator()
            self._set_sample_baseline(current, at=at)
            return

        completed = current - self._sample_current
        sample_rate = completed / elapsed
        self._rate = (
            sample_rate
            if self._rate is None
            else (
                self._smoothing * sample_rate
                + (1.0 - self._smoothing) * self._rate
            )
        )
        self._valid_samples += 1
        self._sampled_units += completed
        self._sample_current = current
        self._sample_at = at

    def _estimate_eta(
        self,
        current: int | None,
        total: int | None,
    ) -> float | None:
        if (
            current is None
            or total is None
            or self._rate is None
            or self._valid_samples < 2
            or self._sampled_units < 2
            or current < 0
            or total < 0
            or current > total
        ):
            return None
        return (total - current) / self._rate

    def _set_sample_baseline(self, current: int | None, *, at: datetime) -> None:
        self._sample_current = current
        self._sample_at = at if current is not None else None

    def _reset_estimator(self) -> None:
        self._sample_current = None
        self._sample_at = None
        self._rate = None
        self._valid_samples = 0
        self._sampled_units = 0

    def invalidate_estimator(self) -> None:
        self._reset_estimator()
        if self.current_activity is not None:
            self.current_activity = replace(self.current_activity, rate=None, eta_seconds=None)
