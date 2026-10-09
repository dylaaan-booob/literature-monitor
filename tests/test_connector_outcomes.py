"""Deterministic A2 bridge contract; no browser or Zotero writes."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from capture_helpers import reservation
from literature_monitor.web.app import create_app
from literature_monitor.web.capture_coordinator import (
    ACTIVE_TIMEOUT_SECONDS, CaptureCoordinator, CaptureOutcome, ParentOutcome,
    PdfOutcome, SaveInvocation,
)
import literature_monitor.web.capture_coordinator as capture_module


def prepare(tmp_path):
    app = create_app(tmp_path / "missing.yaml")
    coordinator = app.state.capture_coordinator
    coordinator.heartbeat(connector_version="A2", zotero_reachable=True)
    coordinator.start_capture(reservation(tmp_path, doi="10.5555/a2"))
    command = coordinator.claim_command()
    assert command is not None
    invocation = dict(request_id=command.request_id, invocation_id=command.invocation_id,
                      doi_url=command.doi_url, tab_id=100, session_id="native-session")
    return app, coordinator, invocation


def result(invocation, outcome="CONFIRMED", pdf="unverified"):
    return {**invocation, "outcome": outcome, "pdf_outcome": pdf}


def test_parent_acceptance_delivers_independent_unverified_pdf(tmp_path):
    app, coordinator, invocation = prepare(tmp_path)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/api/connector/dispatch", json=invocation).status_code == 200
        assert client.post("/api/connector/result", json=result(invocation)).status_code == 200
        # An unproved PDF-failure assertion or duplicate cannot reverse a confirmed parent.
        assert client.post("/api/connector/result", json=result(invocation, pdf="verified failure")).status_code == 409
        assert client.post("/api/connector/result", json=result(invocation)).status_code == 409
    completion = coordinator.consume_completion()
    assert completion is not None
    assert completion.parent_outcome is ParentOutcome.CONFIRMED
    assert completion.pdf_outcome is PdfOutcome.UNVERIFIED
    assert completion.save_invocation == SaveInvocation(**invocation)


@pytest.mark.parametrize("field,value", [
    ("request_id", "previous-request"), ("invocation_id", "previous-save"),
    ("doi_url", "https://doi.org/10.5555/other"), ("tab_id", 101),
    ("session_id", "previous-session"),
])
def test_wrong_identity_and_late_results_cannot_confirm(tmp_path, field, value):
    app, coordinator, invocation = prepare(tmp_path)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/api/connector/dispatch", json=invocation).status_code == 200
        assert client.post("/api/connector/result", json=result({**invocation, field: value})).status_code == 409
    assert coordinator.snapshot().attempt.parent_outcome is None


@pytest.mark.parametrize("field,value", [
    ("request_id", "previous-request"), ("invocation_id", "previous-save"),
    ("doi_url", "https://doi.org/10.5555/other"),
])
def test_dispatch_capability_is_bound_to_claim_and_doi(tmp_path, field, value):
    app, coordinator, invocation = prepare(tmp_path)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/api/connector/dispatch", json={**invocation, field: value}).status_code == 409
        assert client.post("/api/connector/dispatch", json=invocation).status_code == 200
        assert client.post("/api/connector/dispatch", json=invocation).status_code == 409
        assert client.post("/api/connector/dispatch", json={**invocation, "tab_id": 101}).status_code == 409


@pytest.mark.parametrize("pdf", ["verified success", "verified failure"])
def test_unproved_pdf_labels_are_rejected_even_with_parent_dispatch(tmp_path, pdf):
    app, coordinator, invocation = prepare(tmp_path)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/api/connector/dispatch", json=invocation).status_code == 200
        assert client.post("/api/connector/result", json=result(invocation, pdf=pdf)).status_code == 409
    assert coordinator.snapshot().attempt.parent_outcome is None


@pytest.mark.parametrize("extra", [
    {"progress": 100}, {"mime_type": "application/pdf"}, {"title": "PDF"},
    {"pdf_url": "https://example.test/a.pdf"}, {"snapshot": True}, {"link_attachment": True},
])
def test_progress_metadata_snapshot_and_link_are_not_pdf_evidence(tmp_path, extra):
    app, coordinator, invocation = prepare(tmp_path)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/api/connector/result", json={**result(invocation), **extra}).status_code == 422
    assert coordinator.snapshot().attempt.parent_outcome is None


@pytest.mark.parametrize("dispatched", [False, True])
def test_no_effect_requires_no_dispatch_and_generic_failure_is_uncertain(tmp_path, dispatched):
    app, coordinator, invocation = prepare(tmp_path)
    with TestClient(app, base_url="http://localhost") as client:
        if dispatched:
            assert client.post("/api/connector/dispatch", json=invocation).status_code == 200
            assert client.post("/api/connector/result", json=result(invocation, "FAILED")).status_code == 409
            assert client.post("/api/connector/result", json=result(invocation, "UNCONFIRMED")).status_code == 200
        else:
            assert client.post("/api/connector/result", json=result(invocation)).status_code == 409
            assert client.post("/api/connector/result", json=result(invocation, "FAILED")).status_code == 200
    assert coordinator.snapshot().attempt.parent_outcome is (
        ParentOutcome.UNCERTAIN if dispatched else ParentOutcome.NO_EFFECT
    )


def test_expiry_restart_and_duplicate_do_not_restore_dispatch(tmp_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(capture_module, "_monotonic", lambda: now[0])
    app, coordinator, invocation = prepare(tmp_path)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/api/connector/dispatch", json=invocation).status_code == 200
        now[0] = ACTIVE_TIMEOUT_SECONDS
        assert client.post("/api/connector/dispatch", json=invocation).status_code == 409
        assert client.post("/api/connector/result", json=result(invocation)).status_code == 409
    assert coordinator.snapshot().attempt.parent_outcome is ParentOutcome.UNCERTAIN
    restarted = CaptureCoordinator()
    assert not restarted.authorize_parent_dispatch(SaveInvocation(**invocation))
    assert not restarted.submit_connector_result(
        **invocation, outcome=CaptureOutcome.CONFIRMED, pdf_outcome=PdfOutcome.UNVERIFIED,
    )


def test_unattributed_legacy_success_flag_is_not_a_bridge_result(tmp_path):
    app, _, invocation = prepare(tmp_path)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/api/connector/result", json={
            "request_id": invocation["request_id"], "outcome": "CONFIRMED",
        }).status_code == 422


def test_concurrent_dispatch_permissions_have_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    _, coordinator, identity = prepare(tmp_path)
    invocation = SaveInvocation(**identity)
    with ThreadPoolExecutor(max_workers=8) as workers:
        outcomes = list(workers.map(lambda _: coordinator.authorize_parent_dispatch(invocation), range(16)))
    assert outcomes.count(True) == 1
