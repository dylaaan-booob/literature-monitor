from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

import literature_monitor.web.app as web_app
from capture_helpers import reservation
from literature_monitor.web.app import create_app
from literature_monitor.web.capture_coordinator import (
    CaptureOutcome,
    CaptureStage,
    CaptureStartOutcome,
    ConnectorReadiness,
)


def connect(app) -> None:
    readiness = app.state.capture_coordinator.heartbeat(
        connector_version="1.0.0",
        zotero_reachable=True,
    )
    assert readiness is ConnectorReadiness.CONNECTED


def start_capture(app):
    paper_id = UUID("11111111-1111-4111-8111-111111111111")
    result = app.state.capture_coordinator.start_capture(
        reservation(app.state.config_path.parent, paper_id=paper_id),
    )
    assert result.outcome is CaptureStartOutcome.STARTED
    assert result.request_id is not None
    return result


def test_bridge_heartbeat_requires_no_web_csrf_and_exposes_readiness(tmp_path: Path) -> None:
    app = create_app(tmp_path / "missing.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        heartbeat = client.post(
            "/api/connector/heartbeat",
            json={"version": " 1.2.3 ", "zotero_reachable": True},
        )
        state = client.get("/api/connector/state")

    assert heartbeat.status_code == 200
    assert heartbeat.json() == {"readiness": "connected"}
    assert state.status_code == 200
    assert state.json() == {
        "readiness": "connected",
        "connector_version": "1.2.3",
        "zotero_reachable": True,
        "capture": None,
    }


def test_bridge_claim_is_atomic_minimal_and_does_not_leak_previous_command(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "missing.yaml")
    connect(app)
    started = start_capture(app)

    with TestClient(app, base_url="http://localhost") as client:
        claimed = client.post("/api/connector/claim", json={})
        repeated = client.post("/api/connector/claim", json={})
        state = client.get("/api/connector/state")

    assert claimed.status_code == 200
    command = claimed.json()["command"]
    assert set(command) == {"request_id", "doi_url", "invocation_id"}
    assert command["request_id"] == started.request_id
    assert command["doi_url"] == "https://doi.org/10.1000/example"
    assert repeated.status_code == 200
    assert repeated.json() == {"command": None}
    assert state.json()["capture"] == {
        "stage": "CONNECTOR_ACTIVE",
        "terminal_outcome": None,
        "completion_pending": False,
    }
    assert "request_id" not in state.text
    assert "10.1000/example" not in state.text
    assert "/workspace" not in state.text
    assert "11111111-1111-4111-8111-111111111111" not in state.text


def test_bridge_no_command_response_is_compact(tmp_path: Path) -> None:
    app = create_app(tmp_path / "missing.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.post("/api/connector/claim", json={})

    assert response.status_code == 200
    assert response.json() == {"command": None}


def test_simple_cross_origin_claim_cannot_consume_pending_command(tmp_path: Path) -> None:
    app = create_app(tmp_path / "missing.yaml")
    connect(app)
    started = start_capture(app)

    with TestClient(app, base_url="http://localhost") as client:
        missing_body = client.post(
            "/api/connector/claim",
            headers={"origin": "https://evil.example"},
        )
        form_encoded = client.post(
            "/api/connector/claim",
            headers={"origin": "https://evil.example"},
            data={},
        )
        text_plain = client.post(
            "/api/connector/claim",
            headers={
                "origin": "https://evil.example",
                "content-type": "text/plain",
            },
            content="{}",
        )
        multipart = client.post(
            "/api/connector/claim",
            headers={"origin": "https://evil.example"},
            files={"protocol": (None, "")},
        )
        extra_json = client.post(
            "/api/connector/claim",
            json={"unexpected": True},
        )

        waiting = app.state.capture_coordinator.snapshot()
        valid = client.post("/api/connector/claim", json={})
        repeated = client.post("/api/connector/claim", json={})

    for rejected in (
        missing_body,
        form_encoded,
        text_plain,
        multipart,
        extra_json,
    ):
        assert rejected.status_code == 422

    assert waiting.attempt is not None
    assert waiting.attempt.request_id == started.request_id
    assert waiting.attempt.stage is CaptureStage.WAITING_FOR_CONNECTOR
    assert waiting.attempt.claimed_at is None

    assert valid.status_code == 200
    command = valid.json()["command"]
    assert set(command) == {"request_id", "doi_url", "invocation_id"}
    assert command["request_id"] == started.request_id
    assert command["doi_url"] == "https://doi.org/10.1000/example"
    assert repeated.status_code == 200
    assert repeated.json() == {"command": None}


def test_result_route_only_updates_process_local_capture_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = tmp_path / "paper.md"
    sentinel.write_text("human-owned\n", encoding="utf-8")

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("Connector bridge must not enter Paper/Zotero authority")

    assert not hasattr(web_app, "reconcile_paper_with_zotero")
    monkeypatch.setattr(web_app, "load_config", forbidden)
    monkeypatch.setattr(web_app, "load_workspace", forbidden)

    app = create_app(tmp_path / "missing.yaml")
    connect(app)
    started = start_capture(app)

    with TestClient(app, base_url="http://localhost") as client:
        claimed = client.post("/api/connector/claim", json={})
        invocation = {**claimed.json()["command"], "tab_id": 100, "session_id": "native-session"}
        assert client.post("/api/connector/dispatch", json=invocation).status_code == 200
        response = client.post(
            "/api/connector/result",
            json={
                **invocation, "outcome": "CONFIRMED", "pdf_outcome": "unverified",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"accepted": True}
    snapshot = app.state.capture_coordinator.snapshot()
    assert snapshot.attempt is not None
    assert snapshot.attempt.request_id == started.request_id
    assert snapshot.attempt.stage is CaptureStage.FINISHED
    assert snapshot.attempt.terminal_outcome is CaptureOutcome.CONFIRMED
    assert snapshot.completion_pending
    assert sentinel.read_text(encoding="utf-8") == "human-owned\n"


def test_stale_malformed_and_oversized_results_do_not_change_active_attempt(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "missing.yaml")
    connect(app)
    started = start_capture(app)

    with TestClient(app, base_url="http://localhost") as client:
        claimed = client.post("/api/connector/claim", json={}).json()["command"]
        unknown = client.post(
            "/api/connector/result",
            json={**claimed, "request_id": "unknown-request", "tab_id": 100, "session_id": "native-session", "outcome": "CONFIRMED", "pdf_outcome": "unverified"},
        )
        invalid_outcome = client.post(
            "/api/connector/result",
            json={"request_id": claimed["request_id"], "outcome": "SUCCESS"},
        )
        oversized = client.post(
            "/api/connector/result",
            json={"request_id": "x" * 129, "outcome": "CONFIRMED"},
        )
        identity_data = client.post(
            "/api/connector/result",
            json={
                "request_id": claimed["request_id"],
                "outcome": "CONFIRMED",
                "item_key": "ABCDEF12",
            },
        )

    assert unknown.status_code == 409
    assert unknown.json() == {"accepted": False}
    assert invalid_outcome.status_code == 422
    assert oversized.status_code == 422
    assert identity_data.status_code == 422

    snapshot = app.state.capture_coordinator.snapshot()
    assert snapshot.attempt is not None
    assert snapshot.attempt.request_id == started.request_id
    assert snapshot.attempt.stage is CaptureStage.CONNECTOR_ACTIVE
    assert snapshot.attempt.terminal_outcome is None
    assert not snapshot.completion_pending


def test_bridge_heartbeat_schema_is_bounded_and_strict(tmp_path: Path) -> None:
    app = create_app(tmp_path / "missing.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        oversized = client.post(
            "/api/connector/heartbeat",
            json={"version": "v" * 65, "zotero_reachable": True},
        )
        wrong_boolean = client.post(
            "/api/connector/heartbeat",
            json={"version": "1.0.0", "zotero_reachable": "yes"},
        )
        extra = client.post(
            "/api/connector/heartbeat",
            json={"version": "1.0.0", "zotero_reachable": True, "paper": "secret"},
        )
        whitespace = client.post(
            "/api/connector/heartbeat",
            json={"version": "   ", "zotero_reachable": True},
        )

    assert oversized.status_code == 422
    assert wrong_boolean.status_code == 422
    assert extra.status_code == 422
    assert whitespace.status_code == 422
    assert app.state.capture_coordinator.snapshot().readiness is ConnectorReadiness.UNAVAILABLE


def test_bridge_keeps_trusted_host_boundary(tmp_path: Path) -> None:
    app = create_app(tmp_path / "missing.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        external = client.post(
            "/api/connector/heartbeat",
            headers={"host": "external.example"},
            json={"version": "1.0.0", "zotero_reachable": True},
        )
        loopback = client.post(
            "/api/connector/heartbeat",
            headers={"host": "127.0.0.1:8000"},
            json={"version": "1.0.0", "zotero_reachable": True},
        )

    assert external.status_code == 400
    assert loopback.status_code == 200


def test_create_app_instances_do_not_share_capture_or_presence_state(tmp_path: Path) -> None:
    first = create_app(tmp_path / "first.yaml")
    second = create_app(tmp_path / "second.yaml")
    assert first.state.capture_coordinator is not second.state.capture_coordinator
    assert first.state.capture_coordinator is not first.state.run_coordinator

    connect(first)
    start_capture(first)

    first_snapshot = first.state.capture_coordinator.snapshot()
    second_snapshot = second.state.capture_coordinator.snapshot()
    assert first_snapshot.readiness is ConnectorReadiness.CONNECTED
    assert first_snapshot.attempt is not None
    assert second_snapshot.readiness is ConnectorReadiness.UNAVAILABLE
    assert second_snapshot.attempt is None
