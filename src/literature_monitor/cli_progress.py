"""Private CLI runtime-progress presentation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import TextIO

from literature_monitor.progress import (
    ActivityKind,
    ActivitySnapshot,
    ProgressEvent,
    ProgressStage,
    ProgressState,
    ProgressStateSnapshot,
)

_Clock = Callable[[], datetime]

_STAGE_LABELS = {
    ProgressStage.CHECKING_MONITOR: "Checking monitor",
    ProgressStage.DISCOVERING_PAPERS: "Discovering papers",
    ProgressStage.COMBINING_METADATA: "Combining metadata",
    ProgressStage.MATCHING_LITERATURE: "Matching literature",
    ProgressStage.UPDATING_WORKSPACE: "Updating workspace",
}

_SOURCE_LABELS = {
    "application": "Application",
    "crossref": "Crossref",
    "openalex": "OpenAlex",
    "semantic_scholar": "Semantic Scholar",
    "workspace": "Workspace",
}

_UNIT_LABELS = {
    "doi": ("DOI", "DOI"),
    "issn": ("ISSN", "ISSNs"),
    "file": ("file", "files"),
    "journal": ("journal", "journals"),
    "paper": ("paper", "papers"),
    "work": ("work", "works"),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_duration(seconds: float) -> str:
    whole_seconds = max(0, int(seconds))
    minutes, seconds = divmod(whole_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _source_label(source: str | None) -> str | None:
    if source is None:
        return None
    return _SOURCE_LABELS.get(source, source.replace("_", " ").title())


def _unit_label(unit: str | None, value: int) -> str | None:
    if unit is None:
        return None
    singular, plural = _UNIT_LABELS.get(
        unit,
        (unit.replace("_", " "), f"{unit.replace('_', ' ')}s"),
    )
    return singular if value == 1 else plural


def _format_counter(activity: ActivitySnapshot) -> str | None:
    current = activity.current
    total = activity.total
    if current is None or current < 0:
        return None
    if total is not None and total >= 0 and current <= total:
        unit = _unit_label(activity.unit, total)
        return f"{current}/{total}" + (f" {unit}" if unit else "")
    unit = _unit_label(activity.unit, current)
    return f"{current}" + (f" {unit}" if unit else "") + " processed"


def _activity_parts(activity: ActivitySnapshot) -> list[str]:
    parts: list[str] = []
    source = _source_label(activity.source)
    if source is not None:
        parts.append(source)
    if activity.kind in {ActivityKind.WAITING, ActivityKind.RETRYING}:
        parts.append(activity.kind.value)
    parts.append(activity.label)
    if activity.detail:
        parts.append(activity.detail)
    counter = _format_counter(activity)
    if counter is not None:
        parts.append(counter)
    return parts


class _CliProgressRenderer:
    """Render shared progress state to one CLI stderr stream."""

    def __init__(
        self,
        stream: TextIO,
        *,
        show_run_stages: bool,
        clock: _Clock = _utc_now,
    ) -> None:
        self._stream = stream
        self._show_run_stages = show_run_stages
        self._clock = clock
        self._state = ProgressState()
        self._started_at = clock()
        self._tty = bool(stream.isatty())
        self._line_active = False
        self._closed = False

    def __call__(self, event: ProgressEvent) -> None:
        if self._closed:
            return
        now = self._clock()
        self._state.apply(event, at=now)
        snapshot = self._state.snapshot(at=now, active=True)
        if self._tty:
            self._write_tty(snapshot, now=now)
        else:
            self._write_event_line(event, snapshot)

    def close(self) -> None:
        if self._closed:
            return
        if self._tty and self._line_active:
            # 先清掉临时动态行，再让既有 summary/error logging 接管 stderr。
            self._stream.write("\r\x1b[2K\n")
            self._stream.flush()
            self._line_active = False
        self._closed = True

    def _write_tty(
        self,
        snapshot: ProgressStateSnapshot,
        *,
        now: datetime,
    ) -> None:
        parts: list[str] = []
        if (
            self._show_run_stages
            and snapshot.progress_stage is not None
            and snapshot.stage_index is not None
        ):
            parts.append(
                f"Stage {snapshot.stage_index} of {snapshot.stage_total}"
            )
            parts.append(_STAGE_LABELS[snapshot.progress_stage])

        activity = snapshot.current_activity
        if activity is not None:
            parts.extend(_activity_parts(activity))

        if not parts:
            return

        elapsed = (now - self._started_at).total_seconds()
        parts.append(f"elapsed {_format_duration(elapsed)}")
        if activity is not None and activity.eta_seconds is not None:
            parts.append(f"ETA {_format_duration(activity.eta_seconds)}")
        if snapshot.inactivity_warning:
            parts.append("No recent activity")

        self._stream.write("\r\x1b[2K" + " · ".join(parts))
        self._stream.flush()
        self._line_active = True

    def _write_event_line(
        self,
        event: ProgressEvent,
        snapshot: ProgressStateSnapshot,
    ) -> None:
        if (
            event.stage is not None
            and self._show_run_stages
            and snapshot.stage_index is not None
        ):
            self._stream.write(
                "[progress] "
                f"Stage {snapshot.stage_index} of {snapshot.stage_total} · "
                f"{_STAGE_LABELS[event.stage]}\n"
            )

        if event.activity is not None and snapshot.current_activity is not None:
            self._stream.write(
                "[progress] "
                + " · ".join(_activity_parts(snapshot.current_activity))
                + "\n"
            )

        self._stream.flush()
