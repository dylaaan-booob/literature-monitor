"""v0.6.3 transient Kept import acceptance, isolated from real Workspace/Zotero."""
from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import pytest

import literature_monitor.application.batch_import as batch_module
import literature_monitor.application.export_attempts as attempts
import literature_monitor.web.capture_coordinator as capture_module
from literature_monitor.application.batch_import import BatchPhase
from literature_monitor.markdown_state import serialize_document
from literature_monitor.models import WorkflowStatus
from literature_monitor.web.capture_coordinator import (
    CaptureFailureStage, CaptureOutcome, PdfOutcome, SaveInvocation,
)
from test_batch_import import paper, service, command, outcome, finished, state, edit, invocation


def test_successful_three_paper_batch_uses_no_persistent_attempt(tmp_path):
    paths = [paper(tmp_path, str(i))[1] for i in range(3)]
    batch = service()
    plan = batch.start(tmp_path).plan
    assert len(plan.papers) == 3
    for _ in plan.papers:
        cmd = command(batch)
        assert all(not state(path).export_attempt_present for path in paths)
        assert outcome(batch, cmd)
    done = finished(batch)
    assert done.successful and done.statistics.exported_parent == 3
    assert done.statistics.pdf_unverified == 3
    assert all(state(path).status is WorkflowStatus.EXPORTED for path in paths)
    assert all(not state(path).export_attempt_present for path in paths)


def test_pre_dispatch_translator_failure_continues_and_retry_is_explicit(tmp_path):
    _, first = paper(tmp_path, "a")
    _, second = paper(tmp_path, "b")
    batch = service()
    batch.start(tmp_path)
    cmd = command(batch)
    assert batch.coordinator.submit_connector_result(
        request_id=cmd.request_id, invocation_id=cmd.invocation_id,
        doi_url=cmd.doi_url, tab_id=None, session_id=None,
        outcome=CaptureOutcome.FAILED, pdf_outcome=PdfOutcome.UNVERIFIED,
        failure_stage=CaptureFailureStage.TRANSLATOR,
    )
    assert outcome(batch, command(batch))
    done = finished(batch)
    assert done.phase is BatchPhase.COMPLETED
    assert done.statistics.no_effect == 1 and done.statistics.exported_parent == 1
    assert "Translator" in done.results[0].reason
    assert state(first).status is WorkflowStatus.KEPT
    assert state(second).status is WorkflowStatus.EXPORTED
    assert not state(first).export_attempt_present
    again = batch.start(tmp_path)
    assert [p.paper_id for p in again.plan.papers] == [state(first).paper_id]
    assert outcome(batch, command(batch))
    assert finished(batch).successful


def test_native_error_is_uncertain_and_next_explicit_batch_can_retry(tmp_path):
    value, target = paper(tmp_path, "a")
    batch = service()
    batch.start(tmp_path)
    cmd = command(batch)
    assert batch.coordinator.authorize_parent_dispatch(invocation(cmd))
    assert batch.coordinator.submit_connector_result(
        **vars(invocation(cmd)), outcome=CaptureOutcome.UNCONFIRMED,
        pdf_outcome=PdfOutcome.UNVERIFIED, failure_stage=CaptureFailureStage.NATIVE_SAVE,
    )
    done = finished(batch)
    assert done.phase is BatchPhase.PAUSED
    assert done.statistics.uncertain == 1
    assert "Native save" in done.results[0].reason
    assert state(target).status is WorkflowStatus.KEPT
    assert not state(target).export_attempt_present
    assert not batch.coordinator.submit_connector_result(
        **vars(invocation(cmd)), outcome=CaptureOutcome.CONFIRMED,
        pdf_outcome=PdfOutcome.UNVERIFIED,
    )
    assert not batch.coordinator.authorize_parent_dispatch(invocation(cmd))
    again = batch.start(tmp_path)
    assert [p.paper_id for p in again.plan.papers] == [value.id]
    fresh = command(batch)
    assert fresh.request_id != cmd.request_id
    assert outcome(batch, fresh)
    assert finished(batch).successful


@pytest.mark.parametrize("marker", ["pending", "uncertain", "malformed"])
def test_old_marker_is_preserved_and_does_not_block_another_paper(tmp_path, marker):
    old, path = paper(tmp_path, "old")
    _, other = paper(tmp_path, "other")
    raw = "unparseable legacy value"
    if marker != "malformed":
        raw = attempts.reserve_export_attempt(tmp_path, old.id).attempt.model_dump(mode="json")
        raw["state"] = marker
    edit(path, export_attempt=raw)
    before_body = state(path).body
    batch = service()
    planned = batch.start(tmp_path)
    assert len(planned.plan.papers) == 2
    for _ in planned.plan.papers:
        assert outcome(batch, command(batch))
    assert finished(batch).statistics.exported_parent == 2
    assert state(path).frontmatter["export_attempt"] == raw
    assert state(path).body == before_body
    assert state(path).status is WorkflowStatus.EXPORTED
    assert state(other).status is WorkflowStatus.EXPORTED


def test_local_commit_failure_is_distinct_and_original_paper_is_retryable(tmp_path, monkeypatch):
    _, path = paper(tmp_path, "a")
    original = path.read_bytes()
    real_replace = attempts.replace_regular_text_at_identity
    monkeypatch.setattr(attempts, "replace_regular_text_at_identity",
                        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("injected disk error")))
    batch = service()
    batch.start(tmp_path)
    assert outcome(batch, command(batch))
    done = finished(batch)
    assert done.results[0].outcome == "blocked"
    assert "Local Paper completion write failed" in done.results[0].reason
    assert path.read_bytes() == original
    monkeypatch.setattr(attempts, "replace_regular_text_at_identity", real_replace)
    batch.start(tmp_path)
    assert outcome(batch, command(batch))
    assert finished(batch).successful


def test_one_global_plan_and_targeted_rechecks(tmp_path, monkeypatch):
    _, first = paper(tmp_path, "a")
    _, second = paper(tmp_path, "b")
    calls = []
    original = batch_module.plan_workspace_import
    def counted(lock):
        calls.append(1)
        return original(lock)
    monkeypatch.setattr(batch_module, "plan_workspace_import", counted)
    batch = service()
    batch.start(tmp_path)
    cmd = command(batch)
    edit(second, status="rejected")
    assert outcome(batch, cmd)
    done = finished(batch)
    assert len(calls) == 1
    assert done.statistics.exported_parent == 1
    assert done.results[1].outcome == "skipped"
    assert state(second).status is WorkflowStatus.REJECTED


def test_custom_frontmatter_and_body_survive_native_parent_acceptance(tmp_path):
    _, path = paper(tmp_path, "note")
    before = state(path)
    front = dict(before.frontmatter)
    front["reviewer_comment"] = {"language": "中文", "tags": [1, "α"]}
    body = before.body + "\n## Personal notes\nPreserve exact notes.\n"
    path.write_text(serialize_document(front, body))
    batch = service()
    batch.start(tmp_path)
    assert outcome(batch, command(batch))
    assert finished(batch).successful
    after = state(path)
    assert after.frontmatter["reviewer_comment"] == front["reviewer_comment"]
    assert after.body == body
    assert after.status is WorkflowStatus.EXPORTED


def test_duplicate_claim_dispatch_and_late_callback_rejected(tmp_path):
    paper(tmp_path, "a")
    batch = service()
    batch.start(tmp_path)
    cmd = command(batch)
    assert batch.coordinator.claim_command() is None
    assert batch.coordinator.authorize_parent_dispatch(invocation(cmd))
    assert not batch.coordinator.authorize_parent_dispatch(invocation(cmd))
    assert batch.coordinator.report_native_save_settled(invocation(cmd))
    assert outcome(batch, cmd, dispatch=False)
    assert not outcome(batch, cmd, dispatch=False)
    assert finished(batch).successful


def test_timeout_keeps_paper_and_old_invocation_cannot_finish_new_batch(tmp_path, monkeypatch):
    value, path = paper(tmp_path, "a")
    now = [0.]
    monkeypatch.setattr(capture_module, "_monotonic", lambda: now[0])
    batch = service()
    batch.start(tmp_path)
    old = command(batch)
    assert batch.coordinator.authorize_parent_dispatch(invocation(old))
    now[0] = capture_module.ACTIVE_TIMEOUT_SECONDS + 1.
    batch.coordinator.snapshot()
    assert finished(batch).phase is BatchPhase.PAUSED
    assert state(path).status is WorkflowStatus.KEPT
    assert not batch.coordinator.authorize_parent_dispatch(invocation(old))
    batch.coordinator.heartbeat(connector_version="retry", zotero_reachable=True)
    batch.start(tmp_path)
    fresh = command(batch)
    assert fresh.request_id != old.request_id
    assert not outcome(batch, old, CaptureOutcome.CONFIRMED, dispatch=False)
    assert outcome(batch, fresh)
    assert finished(batch).successful


def _exit_with_live_command(root: str, pipe) -> None:
    batch = service()
    batch.start(Path(root))
    cmd = command(batch)
    granted = batch.coordinator.authorize_parent_dispatch(invocation(cmd))
    pipe.send((granted, vars(invocation(cmd))))
    pipe.close()
    os._exit(0)


def test_process_exit_does_not_persist_attempt_or_accept_old_receipt(tmp_path):
    value, path = paper(tmp_path, "a")
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_exit_with_live_command, args=(str(tmp_path), child))
    process.start()
    assert parent.poll(15), "Child did not dispatch a command"
    granted, old_payload = parent.recv()
    process.join(timeout=10)
    assert process.exitcode == 0 and granted
    assert not state(path).export_attempt_present
    replacement = service()
    replacement.start(tmp_path)
    fresh = command(replacement)
    assert not replacement.coordinator.authorize_parent_dispatch(SaveInvocation(**old_payload))
    assert not replacement.coordinator.submit_connector_result(
        **old_payload, outcome=CaptureOutcome.CONFIRMED, pdf_outcome=PdfOutcome.UNVERIFIED)
    assert fresh.request_id != old_payload["request_id"]
    assert outcome(replacement, fresh)
    assert finished(replacement).successful


def _explicit_retry_in_competing_process(root: str, pipe) -> None:
    try:
        batch = service()
        started = batch.start(Path(root))
        if not started.plan or len(started.plan.papers) != 1:
            pipe.send(("error", str(started)))
        else:
            saved = outcome(batch, command(batch))
            done = finished(batch)
            pipe.send(("success", saved and done.successful))
    except Exception as error:
        pipe.send(("error", repr(error)))
    finally:
        pipe.close()


def test_uncertain_grant_does_not_create_cross_process_retry_block(tmp_path):
    value, path = paper(tmp_path, "a")
    first = service()
    first.start(tmp_path)
    cmd = command(first)
    assert first.coordinator.authorize_parent_dispatch(invocation(cmd))
    assert first.coordinator.submit_connector_result(
        **vars(invocation(cmd)), outcome=CaptureOutcome.UNCONFIRMED,
        pdf_outcome=PdfOutcome.UNVERIFIED)
    assert finished(first).phase is BatchPhase.PAUSED
    assert first.coordinator.has_unresolved_save_authority
    assert state(path).status is WorkflowStatus.KEPT

    # The first process stays alive and continues holding a shared Reset
    # safety guard, but it must not exclude a new explicit import.
    ctx = multiprocessing.get_context("spawn")
    recv, send = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_explicit_retry_in_competing_process,
                          args=(str(tmp_path), send))
    process.start()
    assert recv.poll(15), "Second process did not finish explicit retry"
    result = recv.recv()
    process.join(timeout=10)
    assert process.exitcode == 0 and result == ("success", True)
    assert state(path).status is WorkflowStatus.EXPORTED
