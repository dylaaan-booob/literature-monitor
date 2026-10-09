"""Current native parent receipt and safe Paper commit tests (SPEC §42)."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from capture_helpers import reservation
import literature_monitor.application.export_attempts as attempts
from literature_monitor.application.export_attempts import (
    ExportCompletionOutcome, resolve_export_completion,
    reserve_export_attempt,
)
from literature_monitor.markdown_state import serialize_document
from literature_monitor.models import WorkflowStatus
from literature_monitor.web.capture_coordinator import (
    CaptureCoordinator, CaptureFailureStage, CaptureOutcome, CaptureStartOutcome,
    PdfOutcome, SaveInvocation,
)
from literature_monitor.web.zotero_capture import process_capture_completion
from test_batch_import import state


def prepared(tmp_path, *, outcome=CaptureOutcome.CONFIRMED, dispatch=True,
             failure_stage=None):
    token = reservation(tmp_path)
    coordinator = CaptureCoordinator()
    coordinator.heartbeat(connector_version="test", zotero_reachable=True)
    assert coordinator.start_capture(token).outcome is CaptureStartOutcome.STARTED
    cmd = coordinator.claim_command()
    invocation = SaveInvocation(cmd.request_id, cmd.invocation_id, cmd.doi_url,
                                10, "native-session")
    if dispatch:
        assert coordinator.authorize_parent_dispatch(invocation)
    assert coordinator.submit_connector_result(
        **vars(invocation), outcome=outcome, pdf_outcome=PdfOutcome.UNVERIFIED,
        failure_stage=failure_stage,
    )
    return token, coordinator, invocation


def test_native_parent_confirmation_commits_exported_without_marker(tmp_path):
    token, coordinator, invocation = prepared(tmp_path)
    path = Path(token.attempt.workspace_path) / token.attempt.paper_path
    before = state(path)
    processed = process_capture_completion(coordinator)
    assert processed.result.outcome is ExportCompletionOutcome.EXPORTED
    assert processed.completion.pdf_outcome is PdfOutcome.UNVERIFIED
    after = state(path)
    assert after.status is WorkflowStatus.EXPORTED
    expected = dict(before.frontmatter)
    expected["status"] = "exported"
    assert after.frontmatter == expected and after.body == before.body
    assert not after.export_attempt_present
    assert process_capture_completion(coordinator).result is None
    assert not coordinator.authorize_parent_dispatch(invocation)
    assert not coordinator.submit_connector_result(
        **vars(invocation), outcome=CaptureOutcome.CONFIRMED,
        pdf_outcome=PdfOutcome.UNVERIFIED,
    )


@pytest.mark.parametrize("change", [
    "request_id", "paper_id", "normalized_doi", "export_reservation", "save_invocation",
])
def test_substituted_native_confirmation_cannot_write(tmp_path, change):
    token, coordinator, invocation = prepared(tmp_path)
    completion = coordinator.consume_completion()
    path = Path(token.attempt.workspace_path) / token.attempt.paper_path
    protected = path.read_bytes()
    replacement = {
        "request_id": "other",
        "paper_id": uuid4(),
        "normalized_doi": "10.1234/other",
        "export_reservation": replace(token, contents="other"),
        "save_invocation": replace(invocation, session_id="substitute"),
    }[change]
    forged = replace(completion, **{change: replacement})
    assert resolve_export_completion(forged, coordinator=coordinator).outcome is ExportCompletionOutcome.FAILED
    assert path.read_bytes() == protected
    assert resolve_export_completion(completion, coordinator=coordinator).outcome is ExportCompletionOutcome.EXPORTED


def test_native_save_failure_does_not_claim_definite_failure_or_export(tmp_path):
    token, coordinator, _ = prepared(
        tmp_path, outcome=CaptureOutcome.UNCONFIRMED,
        failure_stage=CaptureFailureStage.NATIVE_SAVE,
    )
    path = Path(token.attempt.workspace_path) / token.attempt.paper_path
    original = path.read_bytes()
    result = process_capture_completion(coordinator)
    assert result.result.outcome is ExportCompletionOutcome.UNCERTAIN
    assert "Native save" in result.result.message
    assert path.read_bytes() == original
    assert state(path).status is WorkflowStatus.KEPT


def test_translator_pre_dispatch_failure_is_no_effect(tmp_path):
    token, coordinator, _ = prepared(
        tmp_path, dispatch=False, outcome=CaptureOutcome.FAILED,
        failure_stage=CaptureFailureStage.TRANSLATOR,
    )
    result = process_capture_completion(coordinator)
    assert result.result.outcome is ExportCompletionOutcome.NO_EFFECT
    assert "Translator" in result.result.message
    path = Path(token.attempt.workspace_path) / token.attempt.paper_path
    assert state(path).status is WorkflowStatus.KEPT


def test_persisted_historical_marker_survives_export(tmp_path):
    token = reservation(tmp_path)
    path = Path(token.attempt.workspace_path) / token.attempt.paper_path
    before = state(path)
    legacy = token.attempt.model_dump(mode="json")
    updated = dict(before.frontmatter)
    updated["export_attempt"] = legacy
    updated["user_tag"] = ["中文", "custom"]
    body = before.body + "\n## Personal commentary\nUntouched.\n"
    path.write_text(serialize_document(updated, body))
    fresh = reserve_export_attempt(Path(token.attempt.workspace_path), token.attempt.paper_id)
    coordinator = CaptureCoordinator()
    coordinator.heartbeat(connector_version="test", zotero_reachable=True)
    assert coordinator.start_capture(fresh).outcome is CaptureStartOutcome.STARTED
    command = coordinator.claim_command()
    invocation = SaveInvocation(command.request_id, command.invocation_id, command.doi_url,
                                10, "native-session")
    assert coordinator.authorize_parent_dispatch(invocation)
    assert coordinator.submit_connector_result(
        **vars(invocation), outcome=CaptureOutcome.CONFIRMED,
        pdf_outcome=PdfOutcome.UNVERIFIED,
    )
    assert process_capture_completion(coordinator).result.outcome is ExportCompletionOutcome.EXPORTED
    after = state(path)
    assert after.status is WorkflowStatus.EXPORTED
    assert after.frontmatter["export_attempt"] == legacy
    assert after.frontmatter["user_tag"] == updated["user_tag"]
    assert after.body == body


def test_local_write_error_reports_distinct_result_and_preserves_paper(tmp_path, monkeypatch):
    token, coordinator, _ = prepared(tmp_path)
    path = Path(token.attempt.workspace_path) / token.attempt.paper_path
    original = path.read_bytes()
    monkeypatch.setattr(attempts, "replace_regular_text_at_identity",
                        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("fsync failed")))
    result = process_capture_completion(coordinator)
    assert result.result.outcome is ExportCompletionOutcome.FAILED
    assert "Local Paper completion write failed" in result.result.message
    assert path.read_bytes() == original
    assert not state(path).export_attempt_present
