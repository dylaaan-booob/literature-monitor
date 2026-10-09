"""A6 application/HTTP evidence; isolated Workspaces, no browser or institution login."""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from literature_monitor.application.access import AccessObservation
from literature_monitor.application.batch_import import BatchPhase
from literature_monitor.models import WorkflowStatus
from literature_monitor.web.capture_coordinator import ACTIVE_TIMEOUT_SECONDS
import literature_monitor.web.capture_coordinator as capture_module
import literature_monitor.web.app as web
from test_batch_import import paper, command, finished, state
from test_unified_import_ui import setup, start_data, poll


def access_payload(cmd, reason="unknown_service", **observation):
    return {"request_id": cmd.request_id, "invocation_id": cmd.invocation_id,
            "doi_url": cmd.doi_url, "tab_id": 10,
            "observation": {"reason": reason, "landing_origin": "https://landing.example", **observation}}


def invocation(cmd, tab=10):
    return {"request_id": cmd.request_id, "invocation_id": cmd.invocation_id,
            "doi_url": cmd.doi_url, "tab_id": tab, "session_id": "native-session"}


def result(client, cmd, outcome):
    return client.post("/api/connector/result", json={**invocation(cmd), "outcome": outcome,
                                                     "pdf_outcome": "unverified"})


def test_origin_observation_is_readonly_and_official_parent_stays_pdf_unverified(tmp_path):
    app, config, root = setup(tmp_path)
    _, path = paper(root, "a")
    config_before = config.read_bytes()
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/imports/start", data=start_data(client)).status_code == 200
        cmd = command(app.state.batch_import)
        reserved = path.read_bytes()
        body = access_payload(cmd)
        for field in ("request_id", "invocation_id", "doi_url"):
            assert client.post("/api/connector/access", json={**body, field: "wrong"}).status_code == 409
        assert client.post("/api/connector/access", json=body).json() == {"accepted": True}
        assert client.post("/api/connector/access", json=body).status_code == 409
        assert path.read_bytes() == reserved
        for _ in range(3):
            html = poll(client, app).text
            assert "Access Service unknown" in html and "https://landing.example" in html
            assert "resource entitlement: unknown" in html
        # 观测既不能证明 parent，也不能替代一次性、匹配 tab 的 native permission。
        assert result(client, cmd, "CONFIRMED").status_code == 409
        assert client.post("/api/connector/dispatch", json=invocation(cmd, 11)).status_code == 409
        assert client.post("/api/connector/dispatch", json=invocation(cmd)).json()["accepted"] is True
        assert client.post("/api/connector/access", json=body).status_code == 409
        assert client.post("/api/connector/dispatch", json=invocation(cmd)).status_code == 409
        assert result(client, cmd, "CONFIRMED").status_code == 200
        snapshot = finished(app.state.batch_import)
        assert snapshot.successful and snapshot.statistics.exported_parent == 1
        assert snapshot.statistics.pdf_unverified == 1 and snapshot.statistics.pdf_verified_success == 0
        assert snapshot.results[0].access == AccessObservation(**body["observation"])
        html = poll(client, app).text
        assert "Access Service unknown" in html and "PDF: unverified" in html
    assert state(path).status is WorkflowStatus.EXPORTED
    assert "landing.example" not in path.read_text() and "access_context" not in path.read_text()
    assert config.read_bytes() == config_before


@pytest.mark.parametrize("reason", ["manual_challenge", "unsafe_destination", "access_timeout",
                                   "redirect_loop", "hop_limit", "access_failed", "service_deferred"])
def test_failed_preparation_blocks_native_permission_but_isolates_other_paper(tmp_path, reason):
    app, _, root = setup(tmp_path)
    _, first = paper(root, "a")
    _, second = paper(root, "b")
    with TestClient(app, base_url="http://localhost") as client:
        client.post("/imports/start", data=start_data(client))
        a = command(app.state.batch_import)
        assert client.post("/api/connector/access", json=access_payload(a, reason)).status_code == 200
        assert client.post("/api/connector/dispatch", json=invocation(a)).status_code == 409
        assert result(client, a, "FAILED").status_code == 200
        b = command(app.state.batch_import)
        assert b.doi_url.endswith("/b") and b.access_context == a.access_context
        assert client.post("/api/connector/access", json=access_payload(a)).status_code == 409
        assert client.post("/api/connector/dispatch", json=invocation(a)).status_code == 409
        assert client.post("/api/connector/access", json=access_payload(b)).status_code == 200
        assert client.post("/api/connector/dispatch", json=invocation(b)).status_code == 200
        assert result(client, b, "CONFIRMED").status_code == 200
        snapshot = finished(app.state.batch_import)
        assert snapshot.phase is BatchPhase.COMPLETED and not snapshot.successful
        assert snapshot.statistics.no_effect == snapshot.statistics.exported_parent == 1
        assert snapshot.results[0].access.reason == reason
        assert "Batch finished with errors" in poll(client, app).text
    assert state(first).status is WorkflowStatus.KEPT and not state(first).export_attempt_present
    assert state(second).status is WorkflowStatus.EXPORTED


def test_prepared_navigation_never_claims_authentication_or_entitlement(tmp_path):
    app, _, root = setup(tmp_path)
    paper(root, "a")
    with TestClient(app, base_url="http://localhost") as client:
        client.post("/imports/start", data=start_data(client))
        cmd = command(app.state.batch_import)
        body = access_payload(cmd, "prepared", service_origin="https://sp.example")
        assert client.post("/api/connector/access", json=body).status_code == 200
        html = poll(client, app).text
        assert "authentication and full-text entitlement remain unknown" in html
        assert result(client, cmd, "FAILED").status_code == 200
        finished(app.state.batch_import)


@pytest.mark.parametrize("origin", ["http://sp.example", "https://sp.example/path", "https://sp.example?SAMLResponse=secret",
                                     "https://sp.example#token", "https://user:password@sp.example",
                                     "https://127.0.0.1", "https://localhost", "https://sp.example:8443",
                                     "https://sp.example\n"])
def test_sensitive_or_unsafe_origin_rejected(origin):
    with pytest.raises(ValidationError):
        AccessObservation(reason="unknown_service", landing_origin=origin)


@pytest.mark.parametrize("field", ["cookies", "SAMLResponse", "credentials", "idp_session", "sp_session",
                                   "authentication", "entitlement"])
def test_unproven_sessions_and_sensitive_fields_rejected_by_http(tmp_path, field):
    app, _, root = setup(tmp_path)
    _, path = paper(root, "a")
    with TestClient(app, base_url="http://localhost") as client:
        client.post("/imports/start", data=start_data(client))
        cmd = command(app.state.batch_import)
        before = path.read_bytes()
        body = access_payload(cmd, **{field: "secret-or-confirmed"})
        assert client.post("/api/connector/access", json=body).status_code == 422
        assert client.post("/api/connector/access", data=body).status_code >= 400
        assert app.state.batch_import.snapshot().current_access is None
        assert path.read_bytes() == before
        assert result(client, cmd, "FAILED").status_code == 200
        finished(app.state.batch_import)


def test_timeout_restart_and_late_observation_cannot_revive_save(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(capture_module, "_monotonic", lambda: clock[0])
    app, config, root = setup(tmp_path)
    _, a = paper(root, "a")
    _, b = paper(root, "b")
    unsent = b.read_bytes()
    with TestClient(app, base_url="http://localhost") as client:
        client.post("/imports/start", data=start_data(client))
        cmd = command(app.state.batch_import)
        assert client.post("/api/connector/dispatch", json=invocation(cmd)).status_code == 200
        clock[0] += ACTIVE_TIMEOUT_SECONDS + 1
        assert client.post("/api/connector/access", json=access_payload(cmd, "prepared")).status_code == 409
        assert client.post("/api/connector/dispatch", json=invocation(cmd)).status_code == 409
        snapshot = finished(app.state.batch_import)
        assert snapshot.phase is BatchPhase.PAUSED and snapshot.statistics.uncertain == 1
        assert state(a).status is WorkflowStatus.KEPT and not state(a).export_attempt_present
        assert b.read_bytes() == unsent
    restarted = web.create_app(config)
    restarted.state.capture_coordinator.heartbeat(connector_version="A6-test", zotero_reachable=True)
    with TestClient(restarted, base_url="http://localhost") as client:
        assert client.post("/api/connector/access", json=access_payload(cmd)).status_code == 409
        assert client.post("/api/connector/dispatch", json=invocation(cmd)).status_code == 409
        client.post("/imports/start", data=start_data(client))
        fresh = command(restarted.state.batch_import)
        assert fresh.request_id != cmd.request_id
        assert client.post("/api/connector/dispatch", json=invocation(fresh)).status_code == 200
        assert result(client, fresh, "CONFIRMED").status_code == 200
        other = command(restarted.state.batch_import)
        assert client.post("/api/connector/dispatch", json=invocation(other)).status_code == 200
        assert result(client, other, "CONFIRMED").status_code == 200
        assert finished(restarted.state.batch_import).statistics.exported_parent == 2
    assert state(a).status is WorkflowStatus.EXPORTED
    assert state(b).status is WorkflowStatus.EXPORTED


def test_new_explicit_batch_gets_new_process_local_access_context(tmp_path):
    app, _, root = setup(tmp_path)
    paper(root, "a")
    with TestClient(app, base_url="http://localhost") as client:
        client.post("/imports/start", data=start_data(client))
        first = command(app.state.batch_import)
        assert first.access_context and str(root) not in first.access_context
        assert result(client, first, "FAILED").status_code == 200
        finished(app.state.batch_import)
        client.post("/imports/start", data=start_data(client))
        second = command(app.state.batch_import)
        assert second.access_context != first.access_context
        assert result(client, second, "FAILED").status_code == 200
        finished(app.state.batch_import)
