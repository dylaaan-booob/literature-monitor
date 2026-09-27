"""Retained-only OpenAlex versions and pending state; no production integration."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from literature_monitor.application.provider_state import OpenAlexVersionState
from literature_monitor.openalex import (
    DiscoveryIssue, IssueSeverity, OpenAlexClient, OpenAlexWorkRecord, hydrate_work_versions,
)
from literature_monitor.progress import ProgressCallback


@dataclass(frozen=True)
class RetainedOpenAlexVersions:
    records: tuple[OpenAlexWorkRecord, ...]
    reused_work_ids: tuple[str, ...]
    hydrated_work_ids: tuple[str, ...]
    issues: tuple[DiscoveryIssue, ...]
    pending_changes: tuple[OpenAlexVersionState, ...]


def hydrate_retained_openalex_versions(
    client: OpenAlexClient, retained: Sequence[OpenAlexWorkRecord], *,
    version_state: Sequence[OpenAlexVersionState] = (), retrieved_at: datetime | None = None,
    progress_callback: ProgressCallback | None = None,
) -> RetainedOpenAlexVersions:
    """Caller supplies live retained records and valid in-memory historical state."""
    timestamp = retrieved_at or datetime.now(timezone.utc)
    if timestamp.utcoffset() is None:
        raise ValueError("retrieved_at must include a timezone")
    timestamp = timestamp.astimezone(timezone.utc)
    state = {row.work_id: row for row in version_state}
    if len(state) != len(version_state):
        raise ValueError("duplicate OpenAlex version-state identity")
    live: dict[str, list[OpenAlexWorkRecord]] = {}
    for record in retained:
        live.setdefault(record.external_ids.openalex, []).append(record)
    versions, reused, pending, issues = {}, [], [], []
    # Ambiguous duplicate revisions cannot safely bind durable state.
    revisions = {}
    for work_id, records in live.items():
        values = {record.updated_at for record in records}
        revisions[work_id] = next(iter(values)) if len(values) == 1 else None
    for work_id, revision in revisions.items():
        if revision is not None and work_id in state and revision == state[work_id].hydrated_against_updated_at:
            versions[work_id] = state[work_id].version_hints
            reused.append(work_id)
    requested = tuple(work_id for work_id in live if work_id not in versions)
    hydrated = []
    for result in hydrate_work_versions(client, requested, progress_callback=progress_callback):
        journal = live[result.work_id][0].metadata.journal or ""
        issues.extend(replace(issue, journal=journal) for issue in result.issues)
        versions[result.work_id] = result.version_hints
        if not result.succeeded:
            continue
        hydrated.append(result.work_id)
        revision = revisions[result.work_id]
        if revision is not None:
            try:
                pending.append(OpenAlexVersionState(result.work_id, revision, timestamp, result.version_hints))
            except ValueError as error:
                # Transient normalized hints remain useful even if strict state cannot store them.
                issues.append(DiscoveryIssue(IssueSeverity.WARNING, "version_hydration", journal,
                                             f"version hints cannot be persisted: {error}", record_id=result.work_id))
    return RetainedOpenAlexVersions(
        tuple(record.model_copy(update={"version_hints": versions[record.external_ids.openalex]}) for record in retained),
        tuple(reused), tuple(hydrated), tuple(issues), tuple(pending),
    )
