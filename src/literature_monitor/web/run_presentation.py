"""Pure presentation helpers for the Local Web run panel."""

from __future__ import annotations

import hashlib
from datetime import datetime

from literature_monitor.progress import ActivityKind, ActivitySnapshot, ProgressStage
from literature_monitor.web.run_coordinator import (
    CoordinatorSnapshot,
    CoordinatorStatus,
)

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


def _duration_text(seconds: float) -> str:
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


def _activity_counter(activity: ActivitySnapshot) -> tuple[str | None, bool]:
    current = activity.current
    total = activity.total
    if current is None or current < 0:
        return None, False
    if total is not None and total >= 0 and current <= total:
        unit = _unit_label(activity.unit, total)
        text = f"{current} / {total}" + (f" {unit}" if unit else "")
        return text, total > 0
    unit = _unit_label(activity.unit, current)
    text = f"{current}" + (f" {unit}" if unit else "") + " processed"
    return text, False


def _semantic_key(*parts: str) -> str:
    identity = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(identity).hexdigest()[:16]


def _announcement(
    snapshot: CoordinatorSnapshot,
    *,
    stage_label: str | None,
    activity: ActivitySnapshot | None,
) -> tuple[str, str]:
    if snapshot.status is CoordinatorStatus.IDLE:
        return _semantic_key("idle"), "Monitor ready."

    if snapshot.status is CoordinatorStatus.RUNNING and not snapshot.worker_alive:
        return (
            _semantic_key("running", "worker-stopped"),
            "Run stopped. The monitor worker is no longer active.",
        )

    if snapshot.status is CoordinatorStatus.FINISHED:
        if snapshot.result is not None:
            outcome = snapshot.result.outcome.value
            return (
                _semantic_key("finished", "result", outcome),
                f"Run finished: {outcome}.",
            )
        if snapshot.unexpected_error is not None:
            category = snapshot.unexpected_error.category
            return (
                _semantic_key("finished", "error", category),
                "Run stopped because of an unexpected internal error.",
            )
        return _semantic_key("finished"), "Run finished."

    parts = ["running"]
    message_parts = ["Run in progress."]
    if snapshot.progress_stage is not None:
        parts.append(snapshot.progress_stage.value)
        if stage_label is not None and snapshot.stage_index is not None:
            message_parts.append(
                f"Stage {snapshot.stage_index} of {snapshot.stage_total}: "
                f"{stage_label}."
            )
    else:
        parts.append("starting")
        message_parts.append("Starting.")

    if activity is not None:
        parts.extend(
            (
                activity.kind.value,
                activity.source or "",
                activity.operation,
                activity.label,
            )
        )
        source = _source_label(activity.source)
        if source is not None:
            message_parts.append(f"{source}.")
        if activity.kind is ActivityKind.RETRYING:
            message_parts.append("Retrying.")
        elif activity.kind is ActivityKind.WAITING:
            message_parts.append("Waiting.")
        message_parts.append(f"{activity.label}.")

    parts.append(
        "inactive" if snapshot.inactivity_warning else "active"
    )
    if snapshot.inactivity_warning:
        message_parts.append("No recent activity.")

    return _semantic_key(*parts), " ".join(message_parts)


def build_run_presentation(
    snapshot: CoordinatorSnapshot,
    *,
    now: datetime,
) -> dict[str, object]:
    """Derive display-only fields without mutating coordinator state."""

    stage_label = (
        _STAGE_LABELS[snapshot.progress_stage]
        if snapshot.progress_stage is not None
        else None
    )
    elapsed_text = (
        _duration_text((now - snapshot.started_at).total_seconds())
        if snapshot.started_at is not None
        and snapshot.status is CoordinatorStatus.RUNNING
        else None
    )
    last_activity_text = (
        _duration_text((now - snapshot.last_activity_at).total_seconds())
        if snapshot.last_activity_at is not None
        else None
    )

    activity = snapshot.current_activity
    activity_counter, activity_determinate = (
        _activity_counter(activity) if activity is not None else (None, False)
    )
    activity_source = (
        _source_label(activity.source) if activity is not None else None
    )
    activity_state = (
        activity.kind.value.title()
        if activity is not None and activity.kind is not ActivityKind.WORKING
        else None
    )

    eta_text: str | None = None
    estimating = False
    if (
        activity is not None
        and activity.kind is ActivityKind.WORKING
        and not snapshot.inactivity_warning
    ):
        if activity.eta_seconds is not None:
            eta_text = _duration_text(activity.eta_seconds)
        elif (
            activity.current is not None
            and activity.total is not None
            and activity.current >= 0
            and activity.total >= 0
            and activity.current <= activity.total
            and activity.total > activity.current
        ):
            estimating = True

    announcement_key, announcement_message = _announcement(
        snapshot,
        stage_label=stage_label,
        activity=activity,
    )

    return {
        "stage_label": stage_label,
        "elapsed_text": elapsed_text,
        "last_activity_text": last_activity_text,
        "activity": activity,
        "activity_source": activity_source,
        "activity_state": activity_state,
        "activity_counter": activity_counter,
        "activity_determinate": activity_determinate,
        "eta_text": eta_text,
        "estimating": estimating,
        "announcement_key": announcement_key,
        "announcement_message": announcement_message,
    }
