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
    current_activity: ActivitySnapshot | None
    stage_started_at: datetime | None
    last_activity_at: datetime | None
    inactivity_warning: bool


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
        self.current_activity: ActivitySnapshot | None = None
        self._activity_identity: ActivityIdentity | None = None
        self._sample_current: int | None = None
        self._sample_at: datetime | None = None
        self._rate: float | None = None
        self._valid_samples = 0
        self._sampled_units = 0

    def apply(self, event: ProgressEvent, *, at: datetime) -> bool:
        """Apply one real worker event and report whether state actually changed."""

        stage_changed = event.stage is not None and event.stage is not self.progress_stage
        has_activity = event.activity is not None
        if not stage_changed and not has_activity:
            return False

        # 恢复自长时间无活动后必须重新采样，避免把旧速率带入新的 ETA。
        if self._is_inactive_at(at):
            self._reset_estimator()

        if stage_changed:
            self.progress_stage = event.stage
            self.stage_started_at = at
            self.current_activity = None
            self._activity_identity = None
            self._reset_estimator()

        if event.activity is not None:
            self._apply_activity(event.activity, at=at)

        self.last_activity_at = at
        return True

    def snapshot(self, *, at: datetime, active: bool) -> ProgressStateSnapshot:
        """Return an immutable view; reading never creates worker activity."""

        inactivity_warning = active and self._is_inactive_at(at)
        activity = self.current_activity
        if inactivity_warning and activity is not None:
            activity = replace(activity, rate=None, eta_seconds=None)

        return ProgressStateSnapshot(
            progress_stage=self.progress_stage,
            stage_index=(
                PROGRESS_STAGES.index(self.progress_stage) + 1
                if self.progress_stage is not None
                else None
            ),
            stage_total=len(PROGRESS_STAGES),
            current_activity=activity,
            stage_started_at=self.stage_started_at,
            last_activity_at=self.last_activity_at,
            inactivity_warning=inactivity_warning,
        )

    def _apply_activity(self, update: ActivityUpdate, *, at: datetime) -> None:
        identity: ActivityIdentity = (
            self.progress_stage,
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

    def _is_inactive_at(self, at: datetime) -> bool:
        if self.last_activity_at is None:
            return False
        return (
            at - self.last_activity_at
        ).total_seconds() >= self._inactivity_seconds
