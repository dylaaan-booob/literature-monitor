"""Transient batch test fixtures and concurrency checks.

Only isolated tmp Workspaces; no Chrome or Zotero service is opened.
"""
from datetime import datetime, timedelta, timezone
import threading
import time
from uuid import uuid4

import pytest
from literature_monitor.application.batch_import import BatchImportService, BatchPhase
from literature_monitor.application.workspace import plan_workspace_import
from literature_monitor.markdown_state import parse_paper_state, serialize_document
from literature_monitor.materialize import render_paper_markdown
from literature_monitor.models import Author, CanonicalMetadata, CanonicalPaper, ExternalIds, Workflow, WorkflowStatus
from literature_monitor.safe_write import workspace_operation_lock
from literature_monitor.web.capture_coordinator import CaptureCoordinator, CaptureOutcome, CaptureStage, PdfOutcome, SaveInvocation


def paper(root, name, *, status=WorkflowStatus.KEPT, doi=None, paper_id=None, days=0):
    value = CanonicalPaper(id=paper_id or uuid4(), metadata=CanonicalMetadata(title=name, journal="Test Journal"),
        external_ids=ExternalIds(doi=doi or f"10.5555/{name}"), authors=(Author(name="Ada"),),
        workflow=Workflow(status=status, discovered_at=datetime(2026, 10, 8, tzinfo=timezone.utc) - timedelta(days=days)))
    path = root / "Papers" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_paper_markdown(value, ("ada",)))
    return value, path


def state(path):
    return parse_paper_state(path, path.read_bytes().decode(), path.parent.parent / "Authors")


def edit(path, **changes):
    value = state(path)
    frontmatter = dict(value.frontmatter)
    frontmatter.update(changes)
    path.write_text(serialize_document(frontmatter, value.body))


def service():
    coordinator = CaptureCoordinator()
    coordinator.heartbeat(connector_version="A4-test", zotero_reachable=True)
    return BatchImportService(coordinator)


def wait_until(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        threading.Event().wait(.005)
    pytest.fail("Timed out waiting for test synchronization")


def command(batch):
    return wait_until(batch.coordinator.claim_command)


def invocation(cmd):
    return SaveInvocation(cmd.request_id, cmd.invocation_id, cmd.doi_url, 10, "test-session")


def outcome(batch, cmd, result=CaptureOutcome.CONFIRMED, *, dispatch=True):
    call = invocation(cmd)
    if dispatch:
        assert batch.coordinator.authorize_parent_dispatch(call)
        if result is CaptureOutcome.CONFIRMED:
            # Simulate the separate, verified upstream pipeline-complete receipt.
            assert batch.coordinator.report_native_save_settled(call)
    return batch.coordinator.submit_connector_result(
        request_id=call.request_id, invocation_id=call.invocation_id, doi_url=call.doi_url,
        tab_id=call.tab_id, session_id=call.session_id,
        outcome=result, pdf_outcome=PdfOutcome.UNVERIFIED)


def finished(batch):
    batch._worker.join(timeout=5)
    assert not batch._worker.is_alive()
    return batch.snapshot()


def test_overlapping_submissions_in_one_process_coalesce(tmp_path):
    from literature_monitor.application.batch_import import BatchPhase
    paper(tmp_path, "a")
    paper(tmp_path, "b")
    batch = service()
    started = batch.start(tmp_path)
    assert started.phase is BatchPhase.RUNNING
    assert batch.start(tmp_path).plan is started.plan
    cmd = command(batch)
    assert batch.coordinator.claim_command() is None
    assert outcome(batch, cmd)
    assert outcome(batch, command(batch))
    assert finished(batch).statistics.exported_parent == 2


def test_competing_coordinators_cannot_save_same_workspace_concurrently(tmp_path):
    paper(tmp_path, "a")
    paper(tmp_path, "b")
    first = service()
    first.start(tmp_path)
    cmd = command(first)
    second = service()
    blocked = second.start(tmp_path)
    assert blocked.phase is BatchPhase.BLOCKED
    assert second.coordinator.claim_command() is None
    assert not second.running
    assert outcome(first, cmd)
    assert outcome(first, command(first))
    assert finished(first).successful


def test_frozen_plan_detects_changed_target_without_new_workspace_scan(tmp_path):
    _, first = paper(tmp_path, "a")
    _, later = paper(tmp_path, "b")
    batch = service()
    batch.start(tmp_path)
    cmd = command(batch)
    edit(later, status="rejected")
    assert outcome(batch, cmd)
    snapshot = finished(batch)
    assert snapshot.statistics.exported_parent == 1
    assert snapshot.results[1].outcome == "skipped"
    assert state(later).status is WorkflowStatus.REJECTED


def test_only_eligible_kept_in_plan(tmp_path):
    for status in WorkflowStatus:
        paper(tmp_path, status.value, status=status)
    dup, _ = paper(tmp_path, "duplicate-a")
    paper(tmp_path, "duplicate-b", doi=dup.external_ids.doi)
    paper(tmp_path, "uuid-b", paper_id=dup.id)
    with workspace_operation_lock(tmp_path) as lock:
        plan = plan_workspace_import(lock)
    assert len(plan.papers) == 1
    assert any("Duplicate" in exclusion.reason for exclusion in plan.exclusions)
    assert all(exclusion.kind != "blocked" for exclusion in plan.exclusions)


def test_batch_no_effect_does_not_block_next_paper(tmp_path):
    _, path1 = paper(tmp_path, "a")
    _, path2 = paper(tmp_path, "b")
    batch = service()
    batch.start(tmp_path)
    assert outcome(batch, command(batch), CaptureOutcome.FAILED, dispatch=False)
    assert outcome(batch, command(batch))
    result = finished(batch)
    assert result.phase is BatchPhase.COMPLETED
    assert result.statistics.no_effect == 1
    assert result.statistics.exported_parent == 1
    assert state(path1).status is WorkflowStatus.KEPT
    assert state(path2).status is WorkflowStatus.EXPORTED
