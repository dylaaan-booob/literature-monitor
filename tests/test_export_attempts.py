"""Current transient import identity tests; old marker contract in tests/history."""
from __future__ import annotations

import pytest

from literature_monitor.application.export_attempts import (
    ExportAttemptError, reserve_export_attempt, validate_export_reservation,
)
from literature_monitor.models import WorkflowStatus
from literature_monitor.safe_write import ContentChangedError, workspace_operation_lock
from literature_monitor.web.capture_coordinator import (
    CaptureCoordinator, CaptureStartOutcome, CaptureOutcome, PdfOutcome, SaveInvocation,
)
from test_batch_import import paper, state, edit


def test_transient_reservation_never_changes_source(tmp_path):
    value, path = paper(tmp_path, "a")
    original = path.read_bytes()
    first = reserve_export_attempt(tmp_path, value.id)
    second = reserve_export_attempt(tmp_path, value.id)
    assert first.attempt.attempt_id != second.attempt.attempt_id
    assert path.read_bytes() == original
    assert not state(path).export_attempt_present
    validate_export_reservation(first)
    validate_export_reservation(second)


@pytest.mark.parametrize("change", ["body", "status", "doi", "inode", "path"])
def test_frozen_identity_and_revision_reject_changed_paper(tmp_path, change):
    value, path = paper(tmp_path, "a")
    reservation = reserve_export_attempt(tmp_path, value.id)
    if change == "body":
        path.write_text(path.read_text() + "\nHuman edit")
    elif change == "status":
        edit(path, status="rejected")
    elif change == "doi":
        edit(path, external_ids={"doi": "10.5555/changed"})
    elif change == "inode":
        moved = path.with_suffix(".old")
        path.rename(moved)
        path.write_bytes(moved.read_bytes())
    else:
        path.rename(path.with_name("renamed.md"))
    with pytest.raises((ExportAttemptError, ContentChangedError, OSError)):
        validate_export_reservation(reservation)


@pytest.mark.parametrize("marker", [None, "pending", "uncertain", {"state": "broken"}])
def test_legacy_field_alone_does_not_prevent_transient_reservation(tmp_path, marker):
    value, path = paper(tmp_path, "a")
    legacy = marker
    if marker in ("pending", "uncertain"):
        legacy = reserve_export_attempt(tmp_path, value.id).attempt.model_dump(mode="json")
        legacy["state"] = marker
    edit(path, export_attempt=legacy)
    before = path.read_bytes()
    token = reserve_export_attempt(tmp_path, value.id)
    assert token.attempt.paper_id == value.id
    assert path.read_bytes() == before
    assert state(path).automatic_export_eligible


def test_other_legacy_paper_does_not_block_target(tmp_path):
    old, previous = paper(tmp_path, "old")
    current, target = paper(tmp_path, "current")
    edit(previous, export_attempt={"unknown": "legacy"})
    token = reserve_export_attempt(tmp_path, current.id)
    assert token.attempt.paper_id == current.id
    validate_export_reservation(token)


def test_cross_process_slot_is_excluded_by_existing_flock(tmp_path):
    value, path = paper(tmp_path, "a")
    with workspace_operation_lock(tmp_path):
        # Production batch owns the same POSIX flock. A second coordinator or
        # different process cannot independently start a save for this Workspace.
        with pytest.raises(ContentChangedError):
            reserve_export_attempt(tmp_path, value.id)


def test_duplicate_dispatch_is_rejected_without_any_durable_marker(tmp_path):
    value, path = paper(tmp_path, "a")
    token = reserve_export_attempt(tmp_path, value.id)
    coordinator = CaptureCoordinator()
    coordinator.heartbeat(connector_version="test", zotero_reachable=True)
    assert coordinator.start_capture(token).outcome is CaptureStartOutcome.STARTED
    cmd = coordinator.claim_command()
    assert cmd is not None and coordinator.claim_command() is None
    invocation = SaveInvocation(cmd.request_id, cmd.invocation_id, cmd.doi_url, 1, "session")
    assert coordinator.authorize_parent_dispatch(invocation)
    assert not coordinator.authorize_parent_dispatch(invocation)
    assert state(path).status is WorkflowStatus.KEPT
    assert not state(path).export_attempt_present
    assert coordinator.submit_connector_result(**vars(invocation),
        outcome=CaptureOutcome.UNCONFIRMED, pdf_outcome=PdfOutcome.UNVERIFIED)
    completion = coordinator.consume_completion()
    assert completion.save_invocation == invocation
    assert coordinator.claim_completion(completion)
    coordinator.finish_resolution(completion)
    assert state(path).status is WorkflowStatus.KEPT
    # Uncertain historical save is a warning for Reset, not a new Import ban.
    second_token = reserve_export_attempt(tmp_path, value.id)
    assert coordinator.start_capture(second_token).outcome is CaptureStartOutcome.STARTED
