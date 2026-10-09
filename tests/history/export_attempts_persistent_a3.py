from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timezone
import multiprocessing
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import yaml

import literature_monitor.application.decisions as decisions
import literature_monitor.application.export_attempts as attempts
import literature_monitor.safe_write as safe_write
import literature_monitor.web.zotero_capture as capture
from literature_monitor.application.decisions import DecisionOutcome
from literature_monitor.application.export_attempts import (
    ExportAttemptError, NoSaveEffectEvidence, NoSaveEffectReason,
    clear_export_attempt, mark_export_attempt_uncertain, reserve_export_attempt,
)
from literature_monitor.application.workspace import load_workspace
from literature_monitor.markdown_state import parse_paper_state, serialize_document
from literature_monitor.materialize import materialize_papers, render_paper_markdown
from literature_monitor.models import (
    Author, CanonicalMetadata, CanonicalPaper, ExportAttemptState, ExternalIds,
    Workflow, WorkflowStatus,
)
from literature_monitor.safe_write import CompareReadError, ContentChangedError, workspace_operation_lock
from literature_monitor.web.capture_coordinator import CaptureCoordinator, CaptureOutcome, CaptureStartOutcome
import literature_monitor.web.capture_coordinator as coordinator_module


PAPER_ID = UUID("88888888-8888-4888-8888-888888888888")
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def source_paper(status=WorkflowStatus.KEPT):
    return CanonicalPaper(
        id=PAPER_ID, metadata=CanonicalMetadata(title="Export Paper", journal="Biometrics",
                                               publication_date=date(2026, 10, 1)),
        external_ids=ExternalIds(doi="10.5555/export"), authors=(Author(name="Ada"),),
        workflow=Workflow(status=status, discovered_at=NOW),
    )


def parts(path):
    _, frontmatter, body = path.read_bytes().decode().split("---", 2)
    return yaml.safe_load(frontmatter), body[1:]


def edit(path, **changes):
    frontmatter, body = parts(path)
    frontmatter.update(changes)
    path.write_bytes(serialize_document(frontmatter, body).encode())


@pytest.fixture
def paper_path(tmp_path):
    path = tmp_path / "Papers" / "export.md"
    path.parent.mkdir()
    path.write_text(render_paper_markdown(source_paper(), ("ada",)))
    frontmatter, body = parts(path)
    frontmatter["custom"] = {"rating": 4, "tags": ["人工", "α"]}
    body += "\r\n## Personal Section\r\nExact human notes α.\r\n"
    path.write_bytes(serialize_document(frontmatter, body).encode())
    return path




def state(path):
    return parse_paper_state(path, path.read_bytes().decode(), path.parent.parent / "Authors")


def proof(reservation):
    return NoSaveEffectEvidence(reservation.attempt.attempt_id,
                              NoSaveEffectReason.COMMAND_NEVER_EXPOSED,
                              "Test reservation was never passed to any save caller")


def test_absent_reserve_and_repeat_preserve_paper(paper_path):
    root = paper_path.parent.parent
    before, body = parts(paper_path)
    assert state(paper_path).automatic_export_eligible
    assert state(paper_path).effective_export_attempt_state is None
    reservation = reserve_export_attempt(root, PAPER_ID)
    after, after_body = parts(paper_path)
    assert after.pop("export_attempt") == reservation.attempt.model_dump(mode="json")
    assert after == before and after_body == body
    assert reservation.attempt.state is ExportAttemptState.PENDING
    assert reservation.file_identity == (paper_path.stat().st_dev, paper_path.stat().st_ino)
    assert reservation.contents == paper_path.read_bytes().decode()
    assert len(reservation.attempt.attempt_id) == 64
    assert set(reservation.attempt.model_dump()) == {
        "state", "attempt_id", "paper_id", "doi", "expected_status", "workspace_path",
        "paper_path", "workspace_identity", "papers_identity",
    }
    with pytest.raises(FrozenInstanceError):
        reservation.contents = "changed"
    with pytest.raises(ExportAttemptError):
        reserve_export_attempt(root, PAPER_ID)
    assert parts(paper_path)[1] == body


def _reserve_worker(root, barrier, queue):
    barrier.wait(timeout=10)
    try:
        reservation = reserve_export_attempt(Path(root), PAPER_ID)
        queue.put(("reserved", reservation.attempt.attempt_id))
    except (ExportAttemptError, ContentChangedError) as error:
        queue.put(("rejected", str(error)))
    except OSError as error:
        queue.put(("os_error", (repr(error), Path(root).exists(),
                               (Path(root) / ".literature-monitor-operation.lock").exists())))


def _restart_worker(root, queue):
    paper = load_workspace(Path(root)).kept[0]
    queue.put((paper.effective_export_attempt_state.value, paper.automatic_export_eligible))


def test_two_real_spawned_processes_reserve_at_most_once(paper_path):
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    barrier = context.Barrier(3)
    workers = [context.Process(target=_reserve_worker, args=(str(paper_path.parent.parent), barrier, queue))
               for _ in range(2)]
    for worker in workers:
        worker.start()
    try:
        barrier.wait(timeout=10)
        outcomes = [queue.get(timeout=15) for _ in workers]
        for worker in workers:
            worker.join(timeout=15)
            assert worker.exitcode == 0
        assert sorted(outcome for outcome, _ in outcomes) == ["rejected", "reserved"], outcomes
        successful = [value for outcome, value in outcomes if outcome == "reserved"]
        assert len(successful) == 1
        assert state(paper_path).export_attempt.attempt_id == successful[0]
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join()
        queue.close()


def test_restart_pending_is_uncertain_without_rewrite_or_retry(paper_path):
    root = paper_path.parent.parent
    reserve_export_attempt(root, PAPER_ID)
    before = paper_path.read_bytes()
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    worker = context.Process(target=_restart_worker, args=(str(root), queue))
    worker.start()
    try:
        assert queue.get(timeout=15) == ("uncertain", False)
        worker.join(timeout=15)
        assert worker.exitcode == 0
    finally:
        if worker.is_alive():
            worker.terminate()
            worker.join()
        queue.close()
    assert paper_path.read_bytes() == before
    assert state(paper_path).export_attempt.state is ExportAttemptState.PENDING
    with pytest.raises(ExportAttemptError):
        reserve_export_attempt(root, PAPER_ID)


def test_uncertain_clearance_requires_matching_explicit_proof(paper_path):
    root = paper_path.parent.parent
    first = reserve_export_attempt(root, PAPER_ID)
    uncertain = mark_export_attempt_uncertain(first)
    before = paper_path.read_bytes()
    assert state(paper_path).export_attempt.state is ExportAttemptState.UNCERTAIN
    for evidence in (None, False, "FAILED", "timeout", proof(replace(first, attempt=first.attempt.model_copy(update={"attempt_id": "f" * 64})))):
        with pytest.raises(ExportAttemptError):
            clear_export_attempt(uncertain, evidence=evidence)
        assert paper_path.read_bytes() == before
    with pytest.raises(ValueError):
        NoSaveEffectEvidence(first.attempt.attempt_id, "FAILED", "failure")
    with pytest.raises(ExportAttemptError):
        clear_export_attempt(first, evidence=proof(first))
    clear_export_attempt(uncertain, evidence=proof(uncertain))
    assert not state(paper_path).export_attempt_present
    new = reserve_export_attempt(root, PAPER_ID)
    assert new.attempt.attempt_id != first.attempt.attempt_id
    with pytest.raises(ExportAttemptError):
        clear_export_attempt(first, evidence=proof(first))


@pytest.mark.parametrize("stage", ["before_replace", "replace", "directory_fsync", "readback"])
def test_write_failure_never_returns_save_authority(paper_path, monkeypatch, stage):
    root = paper_path.parent.parent
    before = paper_path.read_bytes()
    def fail(*args, **kwargs):
        raise OSError("injected persistence failure")
    if stage == "before_replace":
        monkeypatch.setattr(attempts, "replace_regular_text_at_identity", fail)
    elif stage == "replace":
        monkeypatch.setattr(safe_write.os, "replace", fail)
    elif stage == "directory_fsync":
        original = safe_write.os.fsync
        def fsync(descriptor):
            import stat
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                fail()
            original(descriptor)
        monkeypatch.setattr(safe_write.os, "fsync", fsync)
    else:
        original = attempts._read_paper
        calls = 0
        def read(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                fail()
            return original(*args)
        monkeypatch.setattr(attempts, "_read_paper", read)
    with pytest.raises(OSError):
        reserve_export_attempt(root, PAPER_ID)
    if stage in {"before_replace", "replace"}:
        assert paper_path.read_bytes() == before
    else:
        assert state(paper_path).effective_export_attempt_state is ExportAttemptState.UNCERTAIN
        assert not state(paper_path).automatic_export_eligible
    assert not list(paper_path.parent.glob(".*.tmp"))


@pytest.mark.parametrize("change", ["doi", "uuid", "status", "path", "attempt_id", "body", "same_bytes_inode", "duplicate"])
@pytest.mark.parametrize("operation", ["uncertain", "clear"])
def test_changed_identity_or_contents_refuse_writeback(paper_path, change, operation):
    root = paper_path.parent.parent
    reservation = reserve_export_attempt(root, PAPER_ID)
    if change == "doi":
        values, _ = parts(paper_path)
        values["external_ids"]["doi"] = "10.5555/changed"
        edit(paper_path, doi="10.5555/changed", external_ids=values["external_ids"])
    elif change == "uuid":
        edit(paper_path, id=str(uuid4()))
    elif change == "status":
        edit(paper_path, status="rejected")
    elif change == "path":
        paper_path = paper_path.rename(paper_path.with_name("moved.md"))
    elif change == "attempt_id":
        marker, _ = parts(paper_path)
        marker["export_attempt"]["attempt_id"] = "e" * 64
        edit(paper_path, export_attempt=marker["export_attempt"])
    elif change == "body":
        with paper_path.open("a") as handle:
            handle.write("\nConcurrent note\n")
    elif change == "same_bytes_inode":
        payload = paper_path.read_bytes()
        paper_path.rename(paper_path.with_suffix(".preserved"))
        paper_path.write_bytes(payload)
    elif change == "duplicate":
        paper_path.with_name("duplicate.md").write_bytes(paper_path.read_bytes())
    before = paper_path.read_bytes()
    with pytest.raises(ExportAttemptError):
        if operation == "uncertain":
            mark_export_attempt_uncertain(reservation)
        else:
            clear_export_attempt(reservation, evidence=proof(reservation))
    assert paper_path.read_bytes() == before


@pytest.mark.parametrize("marker", [None, {}, [], "pending", {"state": "pending"}, {"state": "absent"}])
def test_malformed_marker_is_not_absent_and_blocks_reservation(paper_path, marker):
    edit(paper_path, export_attempt=marker)
    root = paper_path.parent.parent
    before = paper_path.read_bytes()
    parsed = state(paper_path)
    assert parsed.export_attempt_present and not parsed.updateable and not parsed.automatic_export_eligible
    with pytest.raises(ValueError):
        _ = parsed.effective_export_attempt_state
    with pytest.raises(ExportAttemptError):
        reserve_export_attempt(root, PAPER_ID)
    assert paper_path.read_bytes() == before
    assert load_workspace(root).issues


@pytest.mark.parametrize("field,value", [
    ("doi", "https://doi.org/10.5555/export"), ("doi", "NOT-DOI"),
    ("attempt_id", "predictable"), ("expected_status", "candidate"),
    ("workspace_path", "relative"), ("paper_path", "Papers/../else.md"),
    ("workspace_identity", [True, 3]), ("state", "FAILED"), ("secret", "forbidden"),
])
def test_damaged_marker_bindings_fail_closed(paper_path, field, value):
    reservation = reserve_export_attempt(paper_path.parent.parent, PAPER_ID)
    marker = reservation.attempt.model_dump(mode="json")
    marker[field] = value
    edit(paper_path, export_attempt=marker)
    assert not state(paper_path).updateable
    with pytest.raises(ExportAttemptError):
        reserve_export_attempt(paper_path.parent.parent, PAPER_ID)


@pytest.mark.parametrize("replacement", ["symlink", "directory", "fifo", "missing"])
def test_unsafe_paper_target_is_never_read_or_replaced(paper_path, replacement):
    root = paper_path.parent.parent
    reserved = reserve_export_attempt(root, PAPER_ID)
    preserved = paper_path.with_suffix(".preserved")
    paper_path.rename(preserved)
    before = preserved.read_bytes()
    if replacement == "symlink":
        paper_path.symlink_to(preserved)
    elif replacement == "directory":
        paper_path.mkdir()
    elif replacement == "fifo":
        os.mkfifo(paper_path)
    with pytest.raises(ExportAttemptError):
        reserve_export_attempt(root, PAPER_ID)
    with pytest.raises(ExportAttemptError):
        mark_export_attempt_uncertain(reserved)
    assert preserved.read_bytes() == before


@pytest.mark.parametrize("target", ["workspace", "papers", "lock"])
def test_symlink_directories_and_lock_fail_closed(paper_path, target):
    root = paper_path.parent.parent
    reserved = reserve_export_attempt(root, PAPER_ID)
    if target == "lock":
        path = root / ".literature-monitor-operation.lock"
    elif target == "papers":
        path = root / "Papers"
    else:
        path = root
    preserved = path.with_name(path.name + "-preserved")
    path.rename(preserved)
    path.symlink_to(preserved, target_is_directory=target != "lock")
    with pytest.raises((OSError, CompareReadError, ContentChangedError, ExportAttemptError)):
        mark_export_attempt_uncertain(reserved)
    assert reserved.attempt.state is ExportAttemptState.PENDING


def test_workspace_object_replacement_refuses_same_bytes(paper_path):
    root = paper_path.parent.parent
    reserved = reserve_export_attempt(root, PAPER_ID)
    preserved = root.with_name(root.name + "-old")
    root.rename(preserved)
    paper_path.parent.mkdir(parents=True)
    paper_path.write_bytes(reserved.contents.encode())
    with pytest.raises(ExportAttemptError):
        clear_export_attempt(reserved, evidence=proof(reserved))
    assert paper_path.read_bytes() == reserved.contents.encode()


def test_in_place_edit_during_temporary_write_is_not_overwritten(paper_path, monkeypatch):
    original = safe_write.os.fsync
    injected = False
    def fsync(descriptor):
        nonlocal injected
        if not injected:
            injected = True
            with paper_path.open("a") as handle:
                handle.write("\nLate human edit\n")
        original(descriptor)
    monkeypatch.setattr(safe_write.os, "fsync", fsync)
    with pytest.raises(ContentChangedError):
        reserve_export_attempt(paper_path.parent.parent, PAPER_ID)
    assert paper_path.read_text().endswith("Late human edit\n")
    assert not state(paper_path).export_attempt_present


def test_workspace_symlink_substitution_during_write_aborts(paper_path, monkeypatch):
    root = paper_path.parent.parent
    original = safe_write.os.fsync
    preserved = root.with_name(root.name + "-preserved")
    before = paper_path.read_bytes()
    injected = False
    def fsync(descriptor):
        nonlocal injected
        if not injected:
            injected = True
            root.rename(preserved)
            root.symlink_to(preserved, target_is_directory=True)
        original(descriptor)
    monkeypatch.setattr(safe_write.os, "fsync", fsync)
    with pytest.raises(ContentChangedError):
        reserve_export_attempt(root, PAPER_ID)
    assert paper_path.read_bytes() == before


def test_symlink_component_is_not_hidden_by_dotdot(paper_path):
    root = paper_path.parent.parent
    link = root / "link"
    link.symlink_to(root / "Papers", target_is_directory=True)
    before = paper_path.read_bytes()
    with pytest.raises(OSError):
        reserve_export_attempt(link / "..", PAPER_ID)
    assert paper_path.read_bytes() == before


@pytest.mark.parametrize("uncertain", [False, True])
def test_materialization_preserves_marker_notes_and_metadata(paper_path, uncertain):
    root = paper_path.parent.parent
    reserved = reserve_export_attempt(root, PAPER_ID)
    if uncertain:
        reserved = mark_export_attempt_uncertain(reserved)
    before, body = parts(paper_path)
    incoming = source_paper().model_copy(update={"metadata": source_paper().metadata.model_copy(update={"title": "Refreshed Title"})})
    result = materialize_papers((incoming,), root)
    assert not result.has_errors
    after, new_body = parts(paper_path)
    assert after["export_attempt"] == before["export_attempt"]
    for key in ("custom", "id", "doi", "discovered_at", "status", "sources"):
        assert after[key] == before[key]
    assert "Exact human notes α.\r\n" in new_body
    assert after["title"] == "Refreshed Title"
    assert not load_workspace(root).kept[0].automatic_export_eligible
    with pytest.raises(ExportAttemptError):
        mark_export_attempt_uncertain(reserved)


def test_workspace_lock_excludes_other_writers_and_retains_empty_inode(paper_path):
    root = paper_path.parent.parent
    before = paper_path.read_bytes()
    with workspace_operation_lock(root):
        with pytest.raises(ContentChangedError):
            reserve_export_attempt(root, PAPER_ID)
        assert materialize_papers((source_paper(),), root).has_errors
        result = decisions.keep_paper(root, PAPER_ID, WorkflowStatus.CANDIDATE)
        assert result.outcome is DecisionOutcome.STATE_CONFLICT
    lock = root / ".literature-monitor-operation.lock"
    identity = lock.stat().st_ino
    assert lock.read_bytes() == b"" and paper_path.read_bytes() == before
    with workspace_operation_lock(root):
        assert lock.stat().st_ino == identity






@pytest.mark.parametrize("action,target", [(decisions.keep_paper, "kept"), (decisions.reject_paper, "rejected")])
def test_unmarked_candidate_decisions_still_work_offline(paper_path, action, target):
    edit(paper_path, status="candidate")
    result = action(paper_path.parent.parent, PAPER_ID, WorkflowStatus.CANDIDATE)
    assert result.outcome is DecisionOutcome.UPDATED
    assert parts(paper_path)[0]["status"] == target




@pytest.mark.parametrize("window", ["published", "claimed", "failed", "waiting_timeout", "active_timeout", "restart"])
def test_capture_excludes_new_reservation_throughout_save_lifetime(paper_path, monkeypatch, window):
    root = paper_path.parent.parent
    clock = [100.0]
    monkeypatch.setattr(coordinator_module, "_monotonic", lambda: clock[0])
    coordinator = CaptureCoordinator()
    coordinator.heartbeat(connector_version="test", zotero_reachable=True)
    started = coordinator.start_capture(reserve_export_attempt(root, PAPER_ID))
    assert started.outcome is CaptureStartOutcome.STARTED
    if window in {"claimed", "failed", "active_timeout"}:
        command = coordinator.claim_command()
        assert command is not None
        if window == "failed":
            assert coordinator.submit_connector_result(
                request_id=command.request_id, invocation_id=command.invocation_id,
                doi_url=command.doi_url, tab_id=100, session_id=None,
                outcome=CaptureOutcome.FAILED, pdf_outcome=coordinator_module.PdfOutcome.UNVERIFIED,
            )
    if "timeout" in window:
        clock[0] += 1000
        coordinator.snapshot()
    if window == "restart":
        coordinator = CaptureCoordinator()
    with pytest.raises((ExportAttemptError, ContentChangedError)):
        reserve_export_attempt(root, PAPER_ID)
    assert state(paper_path).export_attempt_present
    assert not load_workspace(root).kept[0].automatic_export_eligible
    if window == "published":
        assert coordinator.claim_command() is not None




@pytest.mark.parametrize("mutation", ["notes", "id", "doi", "status", "attempt", "path", "lock"])
def test_claim_revalidates_reserved_identity_and_contents(paper_path, monkeypatch, mutation):
    root = paper_path.parent.parent
    coordinator = CaptureCoordinator()
    coordinator.heartbeat(connector_version="test", zotero_reachable=True)
    started = coordinator.start_capture(reserve_export_attempt(root, PAPER_ID))
    assert started.outcome is CaptureStartOutcome.STARTED
    if mutation == "notes":
        with paper_path.open("a") as handle:
            handle.write("\nConcurrent human note\n")
    elif mutation == "id":
        edit(paper_path, id=str(uuid4()))
    elif mutation == "doi":
        edit(paper_path, doi="10.5555/changed", external_ids={"doi": "10.5555/changed"})
    elif mutation == "status":
        edit(paper_path, status="candidate")
    elif mutation == "attempt":
        marker = parts(paper_path)[0]["export_attempt"]
        marker["attempt_id"] = "f" * 64
        edit(paper_path, export_attempt=marker)
    elif mutation == "path":
        paper_path.rename(paper_path.with_name("moved.md"))
    else:
        lock = root / ".literature-monitor-operation.lock"
        lock.rename(root / "old-lock")
        lock.touch()
    assert coordinator.claim_command() is None


def _capture_race_worker(root, barrier, queue):
    path = Path(root) / "Papers" / "export.md"
    coordinator = CaptureCoordinator()
    coordinator.heartbeat(connector_version="test", zotero_reachable=True)
    barrier.wait(timeout=10)
    try:
        reservation = reserve_export_attempt(Path(root), PAPER_ID)
    except (ExportAttemptError, ContentChangedError):
        queue.put(("capture_rejected", "reservation refused"))
        return
    assert coordinator.start_capture(reservation).outcome is CaptureStartOutcome.STARTED
    assert coordinator.claim_command() is not None
    queue.put(("capture", reservation.attempt.attempt_id))


def test_real_process_capture_and_reservation_have_one_winner(paper_path):
    root = paper_path.parent.parent
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    barrier = context.Barrier(3)
    workers = [context.Process(target=target, args=(str(root), barrier, queue))
               for target in (_capture_race_worker, _reserve_worker)]
    for worker in workers:
        worker.start()
    try:
        barrier.wait(timeout=10)
        outcomes = [queue.get(timeout=15)[0] for _ in workers]
        for worker in workers:
            worker.join(timeout=15)
            assert worker.exitcode == 0
        assert sorted(outcomes) in [["capture", "rejected"], ["capture_rejected", "reserved"]], outcomes
        # A claimed capture command can outlive its publishing process. The
        # durable marker continues excluding a new reservation after exit.
        assert state(paper_path).export_attempt_present
        with pytest.raises(ExportAttemptError):
            reserve_export_attempt(root, PAPER_ID)
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join()
        queue.close()
