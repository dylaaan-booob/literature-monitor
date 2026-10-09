"""Read-only presentation of the A4 snapshot (SPEC §42.4–42.7)."""
from literature_monitor.application.batch_import import BatchPhase, BatchSnapshot
from literature_monitor.application.workspace import WorkspaceSnapshot


def access_message(observation) -> str | None:
    if observation is None:
        return None
    explanation = {
        "unknown_service": "Access Service unknown; use Open DOI for manual institutional access.",
        "no_verified_route": "No verified federation route; use Open DOI for manual institutional access.",
        "preparing": "Preparing a verified service route; this does not prove authentication.",
        "prepared": "Navigation preparation completed; authentication and full-text entitlement remain unknown.",
        "manual_challenge": "Complete credentials, MFA or challenges manually; this task will not resume saving.",
        "redirect_loop": "Repeated access destination; automatic navigation stopped.",
        "hop_limit": "Access redirect limit reached; automatic navigation stopped.",
        "unsafe_destination": "Untrusted access destination; automatic navigation stopped.",
        "access_timeout": "Access preparation timed out; late callbacks cannot resume this task.",
        "access_failed": "Access preparation failed; use manual access.",
        "service_deferred": "This service already failed or is awaiting manual access in this batch.",
    }[observation.reason]
    service = observation.service_origin or "unknown"
    landing = observation.landing_origin or "unknown"
    return f"{explanation} Observed landing: {landing}. SP: {service}. IdP session, SP session, authentication and resource entitlement: unknown."


def build_batch_presentation(snapshot: BatchSnapshot, workspace: WorkspaceSnapshot | None) -> dict:
    titles = {p.paper_id: p.title for p in workspace.papers} if workspace else {}
    phase = snapshot.phase
    heading, tone = {
        BatchPhase.IDLE: ("Import Kept Papers", ""),
        BatchPhase.RUNNING: ("Import in progress", ""),
        BatchPhase.PAUSED: ("Import paused: parent acceptance unconfirmed", "warning"),
        BatchPhase.BLOCKED: ("Import blocked by safety checks", "error"),
        BatchPhase.STOPPED: ("Import stopped: Connector/Zotero unavailable or capture not started", "warning"),
        BatchPhase.COMPLETED: ("Batch finished", ""),
    }[phase]
    if phase is BatchPhase.COMPLETED:
        if snapshot.successful:
            heading, tone = "All planned parents exported", "success"
        elif snapshot.statistics.skipped_blocked or snapshot.results:
            heading, tone = "Batch finished with errors or unresolved results", "warning"
        else:
            heading = "No eligible Kept Papers to import"
    return {
        "snapshot": snapshot, "statistics": snapshot.statistics,
        "heading": heading, "tone": tone, "poll": phase is BatchPhase.RUNNING,
        "current_title": titles.get(snapshot.current_paper_id, str(snapshot.current_paper_id))
            if snapshot.current_paper_id else None,
        "current_access": access_message(snapshot.current_access),
        "results": tuple({"title": titles.get(r.paper_id, str(r.paper_id)), "result": r,
                          "access": access_message(r.access)}
                         for r in snapshot.results),
    }
