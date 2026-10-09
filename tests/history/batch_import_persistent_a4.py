"""A4 deterministic bridge tests and real POSIX spawned-process ownership.

Only isolated tmp Workspaces; no Chrome or Zotero service is opened.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import multiprocessing
from pathlib import Path
import threading
import time
from uuid import uuid4

import pytest
import yaml

from literature_monitor.application.batch_import import BatchImportService, BatchPhase
from literature_monitor.application.decisions import keep_paper
from literature_monitor.application.export_attempts import reserve_export_attempt
from literature_monitor.application.workspace import plan_workspace_import
from literature_monitor.markdown_state import parse_paper_state, serialize_document
from literature_monitor.materialize import render_paper_markdown, materialize_papers
from literature_monitor.models import Author, CanonicalMetadata, CanonicalPaper, ExternalIds, Workflow, WorkflowStatus
from literature_monitor.safe_write import workspace_operation_lock
from literature_monitor.web.capture_coordinator import CaptureCoordinator, CaptureOutcome, CaptureStage, PdfOutcome, SaveInvocation
import literature_monitor.web.capture_coordinator as capture_module
import literature_monitor.application.export_attempts as attempts


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
    return batch.coordinator.submit_connector_result(
        request_id=call.request_id, invocation_id=call.invocation_id, doi_url=call.doi_url,
        tab_id=call.tab_id, session_id=call.session_id,
        outcome=result, pdf_outcome=PdfOutcome.UNVERIFIED)


def finished(batch):
    batch._worker.join(timeout=5)
    assert not batch._worker.is_alive()
    return batch.snapshot()


def test_whole_workspace_multiple_runs_all_kept_default_and_frozen_plan(tmp_path):
    older, _ = paper(tmp_path, "older", status=WorkflowStatus.CANDIDATE, days=10)
    newer, _ = paper(tmp_path, "newer", status=WorkflowStatus.CANDIDATE)
    # Real materialization from separate Runs, then human Keep decisions.
    runroot = tmp_path / "runs"
    assert materialize_papers([older], runroot).created_papers
    assert keep_paper(runroot, older.id, WorkflowStatus.CANDIDATE).resulting_status is WorkflowStatus.KEPT
    assert materialize_papers([newer], runroot).created_papers
    assert keep_paper(runroot, newer.id, WorkflowStatus.CANDIDATE).resulting_status is WorkflowStatus.KEPT
    batch = service()
    started = batch.start(runroot)
    assert {p.paper_id for p in started.plan.papers} == {older.id, newer.id}
    assert started.phase is BatchPhase.RUNNING
    late, late_path = paper(runroot, "later-keep")
    for planned in started.plan.papers:
        cmd = command(batch)
        assert cmd.doi_url.endswith(planned.doi)
        assert outcome(batch, cmd)
    snapshot = finished(batch)
    assert snapshot.successful
    assert snapshot.statistics.exported_parent == 2
    assert snapshot.statistics.pdf_unverified == 2
    assert snapshot.statistics.pdf_verified_success == snapshot.statistics.pdf_verified_failure == 0
    assert state(late_path).automatic_export_eligible
    assert [r.paper_id for r in snapshot.results] == [p.paper_id for p in started.plan.papers]
    # No plan/manifest/history file; only the shared empty operation inode.
    assert not list(runroot.rglob('*batch*'))
    next_batch = batch.start(runroot)
    assert [p.paper_id for p in next_batch.plan.papers] == [late.id]
    assert outcome(batch, command(batch))
    finished(batch)


@pytest.mark.parametrize("change", ["status", "doi", "notes", "location", "duplicate-doi", "duplicate-uuid"])
def test_revalidate_changed_planned_paper_before_reservation(tmp_path, change):
    _, first_path = paper(tmp_path, "a")
    second, path = paper(tmp_path, "b")
    batch = service()
    batch.start(tmp_path)
    cmd = command(batch)
    if change == "status":
        edit(path, status="rejected")
    elif change == "doi":
        edit(path, external_ids={"doi": "10.5555/changed"})
    elif change == "notes":
        path.write_text(path.read_text() + "\nHuman change\n")
    elif change == "location":
        path.rename(path.with_name("renamed.md"))
    elif change == "duplicate-doi":
        paper(tmp_path, "duplicate", doi=second.external_ids.doi)
    else:
        paper(tmp_path, "duplicate", paper_id=second.id)
    assert outcome(batch, cmd)
    result = finished(batch)
    assert result.statistics.exported_parent == 1
    assert result.results[1].outcome == "skipped"
    assert batch.coordinator.claim_command() is None
    assert state(first_path).status is WorkflowStatus.EXPORTED
    if path.exists():
        assert not state(path).export_attempt_present


def test_exclusion_reasons_and_duplicates(tmp_path):
    for status in WorkflowStatus:
        paper(tmp_path, status.value, status=status)
    dup, _ = paper(tmp_path, "duplicate-a")
    paper(tmp_path, "duplicate-b", doi=dup.external_ids.doi)
    paper(tmp_path, "uuid-b", paper_id=dup.id)
    _, invalid = paper(tmp_path, "invalid")
    edit(invalid, external_ids={"doi": "invalid"})
    with workspace_operation_lock(tmp_path) as lock:
        plan = plan_workspace_import(lock)
    assert len(plan.papers) == 1
    reasons = " ".join(e.reason for e in plan.exclusions)
    for reason in ("candidate", "rejected", "exported", "Duplicate Paper UUID", "Duplicate normalized DOI", "external_ids.doi"):
        assert reason in reasons


@pytest.mark.parametrize("problem", ["legacy", "old-version", "symlink", "hardlink", "invalid-uuid", "malformed-marker", "pending", "uncertain"])
def test_unknown_workspace_save_authority_blocks_even_different_paper(tmp_path, problem):
    old, path = paper(tmp_path, "a")
    _, unsent = paper(tmp_path, "b")
    if problem == "legacy":
        edit(path, zotero_key="OLDKEY01")
    elif problem == "old-version":
        edit(path, versions=[])
    elif problem == "symlink":
        path.rename(tmp_path / "outside.md")
        path.symlink_to(tmp_path / "outside.md")
    elif problem == "hardlink":
        import os
        os.link(path, tmp_path / "outside.md")
    elif problem == "invalid-uuid":
        edit(path, id="invalid")
    elif problem == "malformed-marker":
        edit(path, export_attempt="broken")
    else:
        reservation = reserve_export_attempt(tmp_path, old.id)
        if problem == "uncertain":
            attempts.mark_export_attempt_uncertain(reservation)
    before = unsent.read_bytes()
    batch = service()
    result = batch.start(tmp_path)
    assert finished(batch).phase is BatchPhase.BLOCKED
    assert result.plan.blockers
    assert batch.coordinator.claim_command() is None
    assert unsent.read_bytes() == before


def test_double_click_and_two_coordinators_same_process(tmp_path):
    paper(tmp_path, "a")
    _, unsent = paper(tmp_path, "b")
    batch = service()
    batch.start(tmp_path)
    barrier = threading.Barrier(3)
    snapshots = []
    def repeated():
        barrier.wait(timeout=3)
        snapshots.append(batch.start(tmp_path))
    threads = [threading.Thread(target=repeated) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=3)
    for thread in threads:
        thread.join(timeout=3)
    assert len(snapshots) == 2
    assert all(s.plan is batch.snapshot().plan for s in snapshots)
    competitor = service()
    assert competitor.start(tmp_path).phase is BatchPhase.BLOCKED
    finished(competitor)
    assert not state(unsent).export_attempt_present
    for _ in range(2):
        cmd = command(batch)
        assert batch.coordinator.claim_command() is None
        assert outcome(batch, cmd)
        assert not batch.coordinator.authorize_parent_dispatch(invocation(cmd))
        assert not outcome(batch, cmd, dispatch=False)
    assert finished(batch).statistics.exported_parent == 2


@pytest.mark.parametrize("dispatched", [False, True])
def test_timeout_requires_proven_revocation_and_late_callback_cannot_save_next(tmp_path, monkeypatch, dispatched):
    _, first = paper(tmp_path, "a")
    _, second = paper(tmp_path, "b")
    now = [0.0]
    monkeypatch.setattr(capture_module, "_monotonic", lambda: now[0])
    batch = service()
    batch.start(tmp_path)
    old = command(batch)
    if dispatched:
        assert batch.coordinator.authorize_parent_dispatch(invocation(old))
    now[0] = capture_module.ACTIVE_TIMEOUT_SECONDS
    # Heartbeat expires the UI wait but never treats elapsed time as browser revocation.
    batch.coordinator.heartbeat(connector_version="test", zotero_reachable=True)
    if dispatched:
        snap = finished(batch)
        assert snap.phase is BatchPhase.PAUSED
        assert snap.statistics.uncertain == 1 and snap.statistics.skipped_blocked == 1
        assert "may still save" in snap.message
        assert not state(second).export_attempt_present
        restart = service()
        assert restart.start(tmp_path).phase is BatchPhase.BLOCKED
        finished(restart)
        assert not restart.coordinator.authorize_parent_dispatch(invocation(old))
    else:
        next_command = command(batch)
        assert next_command.request_id != old.request_id
        assert outcome(batch, next_command)
        snap = finished(batch)
        assert snap.phase is BatchPhase.COMPLETED
        assert snap.statistics.uncertain == snap.statistics.exported_parent == 1
        assert not snap.successful
    assert state(first).export_attempt.state.value == "uncertain"
    assert not batch.coordinator.authorize_parent_dispatch(invocation(old))
    assert not outcome(batch, old, dispatch=False)


def test_waiting_timeout_retains_marker_but_terminal_no_grant_allows_next(tmp_path, monkeypatch):
    _, path = paper(tmp_path, "a")
    paper(tmp_path, "b")
    now = [0.0]
    monkeypatch.setattr(capture_module, "_monotonic", lambda: now[0])
    batch = service()
    batch.start(tmp_path)
    now[0] = capture_module.WAITING_TIMEOUT_SECONDS
    batch.coordinator.heartbeat(connector_version="test", zotero_reachable=True)
    cmd = command(batch)
    assert cmd.doi_url.endswith("10.5555/b")
    assert outcome(batch, cmd)
    snap = finished(batch)
    assert snap.statistics.uncertain == 1
    assert state(path).export_attempt.state.value == "uncertain"


def test_single_no_effect_isolation_and_global_unavailable_stop(tmp_path):
    _, a = paper(tmp_path, "a")
    _, b = paper(tmp_path, "b")
    _, c = paper(tmp_path, "c")
    batch = service()
    batch.start(tmp_path)
    assert outcome(batch, command(batch), CaptureOutcome.FAILED, dispatch=False)
    cmd = command(batch)
    batch.coordinator.heartbeat(connector_version="test", zotero_reachable=False)
    assert outcome(batch, cmd)
    snap = finished(batch)
    assert snap.phase is BatchPhase.STOPPED and not snap.successful
    assert snap.statistics.no_effect == snap.statistics.exported_parent == snap.statistics.skipped_blocked == 1
    assert state(a).automatic_export_eligible
    assert state(b).status is WorkflowStatus.EXPORTED
    assert state(c).automatic_export_eligible


@pytest.mark.parametrize("after_write", [False, True])
def test_pending_failure_never_exposes_command(tmp_path, monkeypatch, after_write):
    paper(tmp_path, "a")
    _, b = paper(tmp_path, "b")
    original = attempts._write_marker
    def fail(lock, read, marker):
        if after_write:
            original(lock, read, marker)
        raise OSError("injected reservation persistence failure")
    monkeypatch.setattr(attempts, "_write_marker", fail)
    batch = service()
    batch.start(tmp_path)
    snap = finished(batch)
    assert batch.coordinator.claim_command() is None
    assert snap.statistics.exported_parent == 0
    assert not state(b).export_attempt_present


@pytest.mark.parametrize("replacement", ["workspace", "papers", "lock", "workspace-symlink"])
def test_path_lock_and_directory_replacement_fail_closed(tmp_path, replacement):
    root = tmp_path / "root"
    paper(root, "a")
    _, b = paper(root, "b")
    batch = service()
    batch.start(root)
    cmd = command(batch)
    if replacement == "workspace":
        root.rename(tmp_path / "old")
        paper(root, "replacement")
    elif replacement == "papers":
        (root / "Papers").rename(root / "OldPapers")
        paper(root, "replacement")
    elif replacement == "lock":
        lock = root / ".literature-monitor-operation.lock"
        lock.rename(root / "old-lock")
        lock.touch()
    else:
        root.rename(tmp_path / "old")
        root.symlink_to(tmp_path / "old", target_is_directory=True)
    assert not batch.coordinator.authorize_parent_dispatch(invocation(cmd))
    assert outcome(batch, cmd, CaptureOutcome.UNCONFIRMED, dispatch=False)
    snap = finished(batch)
    assert snap.phase is BatchPhase.BLOCKED
    assert snap.statistics.exported_parent == 0


def _compete(root, barrier, queue, release):
    batch = service()
    barrier.wait(timeout=15)
    snap = batch.start(Path(root))
    queue.put((snap.phase.value, None if snap.current_paper_id is None else str(snap.current_paper_id)))
    if snap.phase is BatchPhase.RUNNING:
        cmd = batch.coordinator.claim_command()
        queue.put(("grant", batch.coordinator.authorize_parent_dispatch(invocation(cmd))))
        release.wait(timeout=15)
        outcome(batch, cmd, CaptureOutcome.UNCONFIRMED, dispatch=False)
    finished(batch)


def test_two_real_spawned_processes_exclude_parallel_different_papers(tmp_path):
    paper(tmp_path, "a")
    _, b = paper(tmp_path, "b")
    ctx = multiprocessing.get_context("spawn")
    barrier, queue, release = ctx.Barrier(3), ctx.Queue(), ctx.Event()
    workers = [ctx.Process(target=_compete, args=(str(tmp_path), barrier, queue, release)) for _ in range(2)]
    for worker in workers:
        worker.start()
    try:
        barrier.wait(timeout=15)
        messages = [queue.get(timeout=15) for _ in range(3)]
        assert sorted(m[0] for m in messages) == ["blocked", "grant", "running"]
        assert ("grant", True) in messages
        assert not state(b).export_attempt_present
        release.set()
        for worker in workers:
            worker.join(timeout=15)
            assert worker.exitcode == 0
    finally:
        release.set()
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join()
        queue.close()
    restarted = service()
    assert restarted.start(tmp_path).phase is BatchPhase.BLOCKED
    finished(restarted)
    assert state(b).automatic_export_eligible


def _dying_process(root, queue, dispatched):
    batch = service()
    batch.start(Path(root))
    cmd = batch.coordinator.claim_command()
    if dispatched:
        assert batch.coordinator.authorize_parent_dispatch(invocation(cmd))
    queue.put((cmd.request_id, cmd.invocation_id, cmd.doi_url))
    threading.Event().wait(30)


@pytest.mark.parametrize("dispatched", [False, True])
def test_real_process_exit_releases_flock_but_cannot_restore_batch_or_browser_authority(tmp_path, dispatched):
    paper(tmp_path, "a")
    _, b = paper(tmp_path, "b")
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    worker = ctx.Process(target=_dying_process, args=(str(tmp_path), queue, dispatched))
    worker.start()
    try:
        request_id, invocation_id, doi_url = queue.get(timeout=15)
        worker.terminate()
        worker.join(timeout=15)
        assert not worker.is_alive()
        restarted = service()
        assert restarted.snapshot().phase is BatchPhase.IDLE
        assert restarted.coordinator.claim_command() is None
        # The actual OS lock is free; the durable marker still blocks ALL saves.
        with workspace_operation_lock(tmp_path):
            pass
        assert restarted.start(tmp_path).phase is BatchPhase.BLOCKED
        finished(restarted)
        assert not restarted.coordinator.authorize_parent_dispatch(SaveInvocation(request_id, invocation_id, doi_url, 10, "test-session"))
        assert not state(b).export_attempt_present
    finally:
        if worker.is_alive():
            worker.terminate()
            worker.join()
        queue.close()


def test_shared_operation_exclusion_covers_run_decisions_and_settings_path(tmp_path):
    from test_application_settings import write_valid_settings_files
    from literature_monitor.application.settings import load_settings, save_settings, SettingsSaveOutcome
    from literature_monitor.application.decisions import DecisionOutcome
    config, journal = write_valid_settings_files(tmp_path)
    root = tmp_path / "workspace"
    paper(root, "a")
    candidate, _ = paper(root, "candidate", status=WorkflowStatus.CANDIDATE)
    batch = service()
    batch.start(root)
    before_config, before_journal = config.read_bytes(), journal.read_bytes()
    draft = replace(load_settings(config).draft, output_dir=Path("other-workspace"))
    save = save_settings(config, draft)
    assert save.outcome is SettingsSaveOutcome.WRITE_FAILED
    assert "already active" in save.issues[0].message
    assert config.read_bytes() == before_config and journal.read_bytes() == before_journal
    assert keep_paper(root, candidate.id, WorkflowStatus.CANDIDATE).outcome is DecisionOutcome.STATE_CONFLICT
    assert materialize_papers([candidate], root).issues
    assert outcome(batch, command(batch))
    finished(batch)


def test_web_bridge_batch_completes_without_ui_consuming_completion(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from literature_monitor.web.app import create_app
    import literature_monitor.web.app as web_app
    paper(tmp_path, "a")
    paper(tmp_path, "b")
    app = create_app(tmp_path / "missing.yaml")
    batch = app.state.batch_import
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/api/connector/heartbeat", json={"version": "A4", "zotero_reachable": True}).status_code == 200
        batch.start(tmp_path)
        def wrong_owner(*args):
            pytest.fail("UI stole a batch-owned completion")
        monkeypatch.setattr(web_app, "process_capture_completion", wrong_owner)
        assert client.post("/capture/poll", data={"csrf_token": app.state.csrf_token}).status_code == 200
        for _ in range(2):
            cmd = wait_until(lambda: client.post("/api/connector/claim", json={}).json()["command"])
            assert client.post("/api/connector/claim", json={}).json()["command"] is None
            payload = {**{k:v for k,v in cmd.items() if k != 'access_context'}, "tab_id": 10, "session_id": "web-session"}
            assert client.post("/api/connector/dispatch", json=payload).status_code == 200
            assert client.post("/api/connector/dispatch", json=payload).status_code == 409
            assert client.post("/api/connector/result", json={**payload, "outcome": "CONFIRMED", "pdf_outcome": "unverified"}).status_code == 200
            assert client.post("/api/connector/result", json={**payload, "outcome": "CONFIRMED", "pdf_outcome": "unverified"}).status_code == 409
    assert finished(batch).statistics.exported_parent == 2


def test_delayed_positive_permission_response_after_timeout_pauses_before_native_save(tmp_path, monkeypatch):
    paper(tmp_path, "a")
    _, b = paper(tmp_path, "b")
    now = [0.0]
    monkeypatch.setattr(capture_module, "_monotonic", lambda: now[0])
    batch = service()
    batch.start(tmp_path)
    old = command(batch)
    granted, release = threading.Event(), threading.Event()
    native_calls = []
    def browser():
        permission = batch.coordinator.authorize_parent_dispatch(invocation(old))
        granted.set()
        release.wait(timeout=5)  # deterministic delayed HTTP permission reply
        if permission:
            native_calls.append(old.doi_url)
    thread = threading.Thread(target=browser)
    thread.start()
    assert granted.wait(timeout=5)
    now[0] = capture_module.ACTIVE_TIMEOUT_SECONDS
    batch.coordinator.heartbeat(connector_version="test", zotero_reachable=True)
    snap = finished(batch)
    assert snap.phase is BatchPhase.PAUSED and native_calls == []
    competitor = service()
    assert competitor.start(tmp_path).phase is BatchPhase.BLOCKED
    finished(competitor)
    release.set()
    thread.join(timeout=5)
    assert native_calls == [old.doi_url]
    assert not outcome(batch, old, dispatch=False)
    assert not state(b).export_attempt_present


def test_confirmed_parent_revision_conflict_retains_human_edit_and_continues(tmp_path):
    _, a = paper(tmp_path, "a")
    _, b = paper(tmp_path, "b")
    batch = service()
    batch.start(tmp_path)
    cmd = command(batch)
    assert batch.coordinator.authorize_parent_dispatch(invocation(cmd))
    a.write_text(a.read_text() + "\nHuman concurrent note α\n")
    protected = a.read_bytes()
    assert outcome(batch, cmd, dispatch=False)
    assert outcome(batch, command(batch))
    snap = finished(batch)
    assert snap.statistics.uncertain == snap.statistics.exported_parent == 1
    assert not snap.successful
    assert a.read_bytes() == protected and state(a).export_attempt_present
    assert state(b).status is WorkflowStatus.EXPORTED
    # This same process still has the one-shot retirement proof; another process
    # cannot inherit it. A new explicit plan excludes A and can import a later Keep.
    later, _ = paper(tmp_path, "c")
    batch.start(tmp_path)
    assert [p.paper_id for p in batch.snapshot().plan.papers] == [later.id]
    assert outcome(batch, command(batch))
    finished(batch)


def test_unavailable_service_leaves_all_unsent_papers_unchanged(tmp_path):
    _, a = paper(tmp_path, "a")
    _, b = paper(tmp_path, "b")
    before = (a.read_bytes(), b.read_bytes())
    batch = BatchImportService(CaptureCoordinator())
    batch.start(tmp_path)
    snap = finished(batch)
    assert snap.phase is BatchPhase.STOPPED and not snap.successful
    assert snap.statistics.skipped_blocked == 2
    assert (a.read_bytes(), b.read_bytes()) == before


def test_untrusted_retirement_cannot_release_escaped_save_slot(tmp_path):
    paper(tmp_path, "a")
    paper(tmp_path, "b")
    batch = service()
    batch.start(tmp_path)
    cmd = command(batch)
    assert batch.coordinator.authorize_parent_dispatch(invocation(cmd))
    assert outcome(batch, cmd, CaptureOutcome.UNCONFIRMED, dispatch=False)
    snap = finished(batch)
    assert snap.phase is BatchPhase.PAUSED
    # Public helpers must not accept a dataclass-replaced parent label as proof.
    from literature_monitor.web.capture_coordinator import CaptureCompletion, ParentOutcome
    attempt = batch.coordinator._attempt
    forged = CaptureCompletion(cmd.request_id, attempt.paper_id, attempt.normalized_doi,
        CaptureOutcome.CONFIRMED, datetime.now(timezone.utc), attempt.export_reservation,
        parent_outcome=ParentOutcome.CONFIRMED, save_invocation=attempt.save_invocation)
    batch.coordinator.finish_resolution(forged)
    assert not batch.coordinator.retired_attempts
    assert service().start(tmp_path).phase is BatchPhase.BLOCKED


def test_retirement_proof_cannot_be_rebound_to_another_paper(tmp_path):
    _, a = paper(tmp_path, "a")
    second, b = paper(tmp_path, "b")
    batch = service()
    batch.start(tmp_path)
    cmd = command(batch)
    marker = state(a).export_attempt.model_copy(update={
        "paper_id": second.id, "doi": second.external_ids.doi, "paper_path": "Papers/b.md"})
    edit(b, export_attempt=marker.model_dump(mode="json"))
    assert outcome(batch, cmd, CaptureOutcome.UNCONFIRMED, dispatch=False)
    snap = finished(batch)
    assert snap.phase is BatchPhase.PAUSED
    assert batch.coordinator.claim_command() is None
    assert snap.statistics.uncertain == 1 and snap.statistics.exported_parent == 0


def test_completion_resolution_keeps_coordinator_slot_until_guarded_write_finishes(tmp_path, monkeypatch):
    paper(tmp_path, "a")
    batch = service()
    batch.start(tmp_path)
    cmd = command(batch)
    entered, release = threading.Event(), threading.Event()
    original = attempts.commit_exported
    def held(*args, **kwargs):
        completion = kwargs['completion']
        coordinator = kwargs['coordinator']
        # Exercise the exact interval after receipt consumption, before mutation.
        assert coordinator.claim_completion(completion)
        entered.set()
        release.wait(timeout=5)
        raise attempts.ExportAttemptError("injected persistence interruption")
    monkeypatch.setattr(attempts, "commit_exported", held)
    assert outcome(batch, cmd)
    assert entered.wait(timeout=5)
    other, _ = paper(tmp_path / "other", "other")
    reserved = reserve_export_attempt(tmp_path / "other", other.id)
    from literature_monitor.web.capture_coordinator import CaptureStartOutcome
    assert batch.coordinator.start_capture(reserved).outcome is CaptureStartOutcome.COMPLETION_PENDING
    release.set()
    snap = finished(batch)
    assert snap.statistics.uncertain == 1


def test_direct_a3_commit_reuses_and_releases_owned_operation_lock(tmp_path):
    first, a = paper(tmp_path, "a")
    second, _ = paper(tmp_path, "b")
    coordinator = CaptureCoordinator()
    coordinator.heartbeat(connector_version="test", zotero_reachable=True)
    reservation = reserve_export_attempt(tmp_path, first.id)
    coordinator.start_capture(reservation)
    cmd = coordinator.claim_command()
    call = invocation(cmd)
    assert coordinator.authorize_parent_dispatch(call)
    assert coordinator.submit_connector_result(request_id=call.request_id, invocation_id=call.invocation_id,
        doi_url=call.doi_url, tab_id=call.tab_id, session_id=call.session_id,
        outcome=CaptureOutcome.CONFIRMED, pdf_outcome=PdfOutcome.UNVERIFIED)
    attempts.commit_exported(reservation, completion=coordinator.consume_completion(), coordinator=coordinator)
    assert state(a).status is WorkflowStatus.EXPORTED
    from literature_monitor.web.capture_coordinator import CaptureStartOutcome
    next_reservation = reserve_export_attempt(tmp_path, second.id)
    assert coordinator.start_capture(next_reservation).outcome is CaptureStartOutcome.STARTED
    # Retire the second test command without any browser dispatch.
    cmd = coordinator.claim_command()
    call = invocation(cmd)
    assert coordinator.submit_connector_result(request_id=call.request_id, invocation_id=call.invocation_id,
        doi_url=call.doi_url, tab_id=call.tab_id, session_id=call.session_id,
        outcome=CaptureOutcome.FAILED, pdf_outcome=PdfOutcome.UNVERIFIED)
    attempts.resolve_export_completion(coordinator.consume_completion(), coordinator=coordinator)


@pytest.mark.parametrize("marker_change", ["pending", "uncertain", "malformed", "missing-identity", "mismatched-identity", "invalid-yaml", "missing-delimiter"])
def test_review_f1_type_note_cannot_hide_pending_attempt(tmp_path, marker_change):
    old, a = paper(tmp_path, "a")
    _, b = paper(tmp_path, "b")
    reserve_export_attempt(tmp_path, old.id)
    value = state(a)
    frontmatter = dict(value.frontmatter)
    frontmatter['type'] = 'note'
    if marker_change == 'uncertain':
        frontmatter['export_attempt']['state'] = 'uncertain'
    elif marker_change == 'malformed':
        frontmatter['export_attempt'] = 'broken'
    elif marker_change == 'missing-identity':
        del frontmatter['export_attempt']['paper_id']
        del frontmatter['id']
    elif marker_change == 'mismatched-identity':
        frontmatter['export_attempt']['paper_id'] = str(uuid4())
    contents = serialize_document(frontmatter, value.body)
    if marker_change == 'invalid-yaml':
        contents = contents.replace('type: note', 'type: note\nbroken: [')
    elif marker_change == 'missing-delimiter':
        contents = contents.removeprefix('---\n')
    a.write_text(contents)
    protected = (a.read_bytes(), b.read_bytes())
    batch = service()
    batch.start(tmp_path)
    assert batch.snapshot().phase is BatchPhase.BLOCKED
    result = finished(batch)
    assert result.plan.blockers
    assert batch.coordinator.claim_command() is None
    assert (a.read_bytes(), b.read_bytes()) == protected


def test_review_f2_invalid_kept_exclusion_prevents_whole_batch_success(tmp_path):
    paper(tmp_path, "a")
    _, b = paper(tmp_path, "b")
    edit(b, doi='invalid', external_ids={'doi': 'invalid'})
    batch = service()
    batch.start(tmp_path)
    assert outcome(batch, command(batch))
    result = finished(batch)
    assert result.statistics.exported_parent == 1
    assert result.plan.exclusions[0].kind == "error"
    assert result.statistics.skipped_blocked == 1
    assert result.statistics.normal_excluded == 0
    assert not result.successful


def test_review_f1_different_coordinator_cannot_dispatch_past_hidden_attempt(tmp_path):
    old, a = paper(tmp_path, 'a')
    other, b = paper(tmp_path, 'b')
    reserve_export_attempt(tmp_path, old.id)
    value = state(a)
    fm = dict(value.frontmatter)
    fm['type'] = 'note'
    a.write_text(serialize_document(fm, value.body))
    frozen = a.read_bytes()
    # A1 reservation is not save authority; every new coordinator must still
    # apply the complete Workspace safety scan before claim/dispatch.
    reservation = reserve_export_attempt(tmp_path, other.id)
    from literature_monitor.web.capture_coordinator import CaptureStartOutcome
    for _ in range(2):
        coordinator = service().coordinator
        started = coordinator.start_capture(reservation)
        assert started.outcome is not CaptureStartOutcome.STARTED
        assert coordinator.claim_command() is None
        assert not coordinator.authorize_parent_dispatch(SaveInvocation('unknown', 'unknown', 'https://doi.org/10.5555/b', 10, 'session'))
    assert a.read_bytes() == frozen
    assert state(b).export_attempt == reservation.attempt


def _hidden_marker_scan(root, queue):
    batch = service()
    batch.start(Path(root))
    snapshot = finished(batch)
    cmd = batch.coordinator.claim_command()
    permission = batch.coordinator.authorize_parent_dispatch(SaveInvocation('unknown', 'unknown', 'https://doi.org/10.5555/b', 10, 'session'))
    queue.put((snapshot.phase.value, bool(snapshot.plan.blockers), cmd is None, permission))


@pytest.mark.parametrize('marker_change', ['pending', 'uncertain', 'malformed'])
def test_review_f1_real_spawned_process_cannot_dispatch_hidden_attempt(tmp_path, marker_change):
    old, a = paper(tmp_path, 'a')
    _, b = paper(tmp_path, 'b')
    reserve_export_attempt(tmp_path, old.id)
    value = state(a)
    fm = dict(value.frontmatter)
    fm['type'] = 'note'
    if marker_change == 'uncertain':
        fm['export_attempt']['state'] = 'uncertain'
    elif marker_change == 'malformed':
        fm['export_attempt'] = None
    a.write_text(serialize_document(fm, value.body))
    protected = (a.read_bytes(), b.read_bytes())
    ctx = multiprocessing.get_context('spawn')
    queue = ctx.Queue()
    worker = ctx.Process(target=_hidden_marker_scan, args=(str(tmp_path), queue))
    worker.start()
    try:
        assert queue.get(timeout=15) == ('blocked', True, True, False)
        worker.join(timeout=15)
        assert worker.exitcode == 0
    finally:
        if worker.is_alive():
            worker.terminate()
            worker.join()
        queue.close()
    assert (a.read_bytes(), b.read_bytes()) == protected


@pytest.mark.parametrize('status', [WorkflowStatus.CANDIDATE, WorkflowStatus.REJECTED, WorkflowStatus.EXPORTED])
@pytest.mark.parametrize('invalid_doi', [False, True])
def test_review_f2_non_target_states_are_normal_exclusions(tmp_path, status, invalid_doi):
    paper(tmp_path, 'a')
    _, other = paper(tmp_path, 'other', status=status)
    if invalid_doi:
        edit(other, doi='invalid', external_ids={'doi': 'invalid'})
    batch = service()
    batch.start(tmp_path)
    assert outcome(batch, command(batch))
    snapshot = finished(batch)
    assert snapshot.successful
    assert snapshot.plan.exclusions[0].kind == 'normal'
    assert snapshot.statistics.normal_excluded == 1
    assert snapshot.statistics.skipped_blocked == 0
    assert snapshot.statistics.exported_parent == 1


def test_review_non_paper_without_marker_is_normal_but_unparseable_is_blocked(tmp_path):
    paper(tmp_path, 'a')
    note = tmp_path/'Papers/note.md'
    note.write_text(serialize_document({'type': 'note', 'title': 'Ordinary note'}, 'Human text mentions export_attempt.\n'))
    batch = service()
    batch.start(tmp_path)
    assert outcome(batch, command(batch))
    snapshot = finished(batch)
    assert snapshot.successful
    assert snapshot.statistics.normal_excluded == 1
    assert snapshot.statistics.skipped_blocked == 0
    assert snapshot.plan.exclusions[0].kind == 'normal'
    note.write_text('---\ntype: note\nunknown: [\n---\nCannot determine old authority\n')
    new, unsent = paper(tmp_path, 'b')
    protected = unsent.read_bytes()
    batch.start(tmp_path)
    snapshot = finished(batch)
    assert snapshot.phase is BatchPhase.BLOCKED
    assert snapshot.plan.blockers[0].kind == 'blocked'
    assert not snapshot.successful
    assert unsent.read_bytes() == protected


def test_review_f2_other_invalid_kept_and_duplicate_identity_are_errors(tmp_path):
    paper(tmp_path, 'a')
    duplicate, b = paper(tmp_path, 'b')
    paper(tmp_path, 'c', doi=duplicate.external_ids.doi)
    _, missing_title = paper(tmp_path, 'missing-title')
    edit(missing_title, title='')
    batch = service()
    batch.start(tmp_path)
    assert outcome(batch, command(batch))
    snapshot = finished(batch)
    assert not snapshot.successful
    assert snapshot.statistics.skipped_blocked == 3
    assert {e.kind for e in snapshot.plan.exclusions} == {'error'}
    assert not state(b).export_attempt_present


def test_review_safely_retired_attempt_remains_excluded_without_automatic_retry(tmp_path):
    _, a = paper(tmp_path, 'a')
    paper(tmp_path, 'b')
    batch = service()
    batch.start(tmp_path)
    old = command(batch)
    assert outcome(batch, old, CaptureOutcome.UNCONFIRMED, dispatch=False)
    next_command = command(batch)
    assert next_command.doi_url.endswith('10.5555/b')
    assert outcome(batch, next_command)
    assert finished(batch).statistics.uncertain == 1
    protected = a.read_bytes()
    later, _ = paper(tmp_path, 'c')
    batch.start(tmp_path)
    assert [p.paper_id for p in batch.snapshot().plan.papers] == [later.id]
    assert outcome(batch, command(batch))
    snapshot = finished(batch)
    assert a.read_bytes() == protected
    assert not snapshot.successful
    assert snapshot.statistics.skipped_blocked == 1
    assert snapshot.plan.exclusions[0].kind == 'error'
    assert not batch.coordinator.authorize_parent_dispatch(invocation(old))


@pytest.mark.parametrize(('suffix', 'marker_change'), [
    ('.txt', 'pending'), ('.bak', 'uncertain'), ('.backup', 'malformed'),
    ('', 'missing-identity'), ('.txt', 'type-note'),
])
def test_review_f3_renamed_marker_blocks_batch_and_different_coordinators(tmp_path, suffix, marker_change):
    old, a = paper(tmp_path, 'a')
    other, b = paper(tmp_path, 'b')
    reserve_export_attempt(tmp_path, old.id)
    if marker_change == 'uncertain':
        fm = dict(state(a).frontmatter)
        fm['export_attempt']['state'] = 'uncertain'
        edit(a, **fm)
    elif marker_change == 'malformed':
        edit(a, export_attempt='broken')
    elif marker_change == 'missing-identity':
        fm = dict(state(a).frontmatter)
        del fm['id']
        del fm['export_attempt']['paper_id']
        a.write_text(serialize_document(fm, state(a).body))
    elif marker_change == 'type-note':
        edit(a, type='note')
    renamed = a.rename(a.with_suffix(suffix))
    protected = (renamed.read_bytes(), b.read_bytes())
    batch = service()
    assert batch.start(tmp_path).phase is BatchPhase.BLOCKED
    snapshot = finished(batch)
    assert any(e.path == renamed and e.kind == 'blocked' for e in snapshot.plan.blockers)
    assert batch.coordinator.claim_command() is None
    assert (renamed.read_bytes(), b.read_bytes()) == protected
    # A reservation alone cannot bypass the shared safety scan at start/claim.
    reservation = reserve_export_attempt(tmp_path, other.id)
    from literature_monitor.web.capture_coordinator import CaptureStartOutcome
    for _ in range(2):
        coordinator = service().coordinator
        assert coordinator.start_capture(reservation).outcome is not CaptureStartOutcome.STARTED
        assert coordinator.claim_command() is None
    assert renamed.read_bytes() == protected[0]


def _review_f3_old_dispatched_process(root, queue, hold):
    batch = service()
    batch.start(Path(root))
    call = invocation(command(batch))
    queue.put((batch.coordinator.authorize_parent_dispatch(call), call))
    hold.wait(timeout=15)


def _review_f3_new_process(root, queue, old_call):
    batch = service()
    snapshot = batch.start(Path(root))
    cmd = batch.coordinator.claim_command()
    granted = cmd is not None and batch.coordinator.authorize_parent_dispatch(invocation(cmd))
    queue.put((snapshot.phase.value, bool(snapshot.plan.blockers), cmd is not None,
               granted, batch.coordinator.authorize_parent_dispatch(old_call)))
    if cmd is not None:
        # Permit this probe to terminate even on the vulnerable implementation.
        outcome(batch, cmd, CaptureOutcome.UNCONFIRMED, dispatch=False)
    finished(batch)


@pytest.mark.parametrize(('suffix', 'uncertain'), [('.txt', False), ('.bak', True)])
def test_review_f3_real_old_dispatch_exit_rename_new_process_denies_save(tmp_path, suffix, uncertain):
    _, a = paper(tmp_path, 'a')
    _, b = paper(tmp_path, 'b')
    ctx = multiprocessing.get_context('spawn')
    queue, hold = ctx.Queue(), ctx.Event()
    old = ctx.Process(target=_review_f3_old_dispatched_process, args=(str(tmp_path), queue, hold))
    new = None
    old.start()
    try:
        granted, old_call = queue.get(timeout=15)
        print('OLD_DISPATCH_GRANTED', granted)
        assert granted is True
        old.terminate()
        old.join(timeout=15)
        assert not old.is_alive()
        with workspace_operation_lock(tmp_path):
            pass  # Actual flock release does not establish browser revocation.
        if uncertain:
            fm = dict(state(a).frontmatter)
            fm['export_attempt']['state'] = 'uncertain'
            edit(a, **fm)
        protected = (a.read_bytes(), b.read_bytes())
        renamed = a.rename(a.with_suffix(suffix))
        print('OLD_MARKER_PRESERVED_IN_RENAMED_FILE', renamed.read_bytes() == protected[0])
        new = ctx.Process(target=_review_f3_new_process, args=(str(tmp_path), queue, old_call))
        new.start()
        phase, blocked, exposed, permission, old_permission = queue.get(timeout=15)
        new.join(timeout=15)
        assert new.exitcode == 0
        print('NEW_PROCESS_COMMAND', exposed)
        print('NEW_PROCESS_DISPATCH_GRANTED', permission)
        assert (phase, blocked, exposed, permission, old_permission) == ('blocked', True, False, False, False)
        assert (renamed.read_bytes(), b.read_bytes()) == protected
    finally:
        for worker in (old, new):
            if worker is not None and worker.is_alive():
                worker.terminate()
                worker.join()
        queue.close()


@pytest.mark.parametrize('suffix', ['.txt', '.bak', '.other', ''])
def test_review_f3_safe_non_markdown_without_marker_is_normal_exclusion(tmp_path, suffix):
    paper(tmp_path, 'a')
    note = tmp_path/'Papers'/('note' + suffix)
    note.write_text(serialize_document({'type': 'note', 'title': 'Human note'}, 'Mentions export_attempt only in body.\n'))
    batch = service()
    batch.start(tmp_path)
    assert outcome(batch, command(batch))
    snapshot = finished(batch)
    assert snapshot.successful
    assert snapshot.statistics.normal_excluded == 1
    assert snapshot.statistics.skipped_blocked == 0
    assert note.read_text().endswith('Mentions export_attempt only in body.\n')


@pytest.mark.parametrize('unsafe', ['yaml', 'utf8', 'symlink', 'hardlink', 'directory', 'fifo'])
def test_review_f3_unverifiable_non_markdown_entry_blocks_dispatch(tmp_path, unsafe):
    import os
    _, a = paper(tmp_path, 'a')
    suspect = tmp_path/'Papers/suspect.bak'
    if unsafe == 'yaml':
        suspect.write_text('---\ntype: note\nexport_attempt: [\n---\n')
    elif unsafe == 'utf8':
        suspect.write_bytes(b'\xffexport_attempt')
    elif unsafe == 'symlink':
        suspect.symlink_to(a)
    elif unsafe == 'hardlink':
        os.link(a, suspect)
    elif unsafe == 'directory':
        suspect.mkdir()
    else:
        os.mkfifo(suspect)
    protected = a.read_bytes()
    batch = service()
    assert batch.start(tmp_path).phase is BatchPhase.BLOCKED
    assert finished(batch).plan.blockers
    assert batch.coordinator.claim_command() is None
    assert a.read_bytes() == protected
