"""A5 Kept Preparation controls use isolated saved configs and the real process bridge."""
from fastapi.testclient import TestClient

from literature_monitor.application.batch_import import BatchPhase
from literature_monitor.web.app import create_app
from literature_monitor.web.preparation_coordinator import EXECUTOR_CAPABILITY
from test_access_mapping_a4 import stamp
from preparation_helpers import prepared
from test_batch_import import paper, state
from test_web_run_settings import csrf_from_html

INSTANCE = "a5-preparation-connector-instance"
CAPABILITY = {"instance_id": INSTANCE, "capability": EXECUTOR_CAPABILITY, "version": "0.6.4"}


def configured(tmp_path):
    config, path, first, second_service = prepared(tmp_path)
    config.write_text(config.read_text(encoding="utf-8") +
                      "\ninstitution:\n  idp_entity_id: urn:mace:example.edu:campus\n", encoding="utf-8")
    return create_app(config), config, path, first, second_service


def command(client):
    result = client.post("/api/connector/preparation/claim", json={"instance_id": INSTANCE})
    assert result.status_code == 200
    return result.json()["command"]


def identity(cmd):
    return {"instance_id": INSTANCE, **{
        key: cmd[key] for key in ("task_id", "generation", "request_id", "service_id")}}


def test_kept_has_independent_buttons_and_only_saved_manual_urls(tmp_path):
    config, path, first, second_service = prepared(tmp_path)
    unchanged = stamp(path), stamp(config)
    app = create_app(config)
    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/?view=kept")
        assert page.status_code == 200
        assert 'data-import-form' in page.text
        assert 'data-preparation-start' in page.text
        assert 'Import to Zotero' in page.text
        assert 'Prepare CARSI Access' in page.text
        assert 'Institution IdP is not configured' in page.text
        assert '<button type="submit" disabled>Prepare CARSI Access</button>' in page.text
        assert 'href="' + first.access_url + '"' in page.text
        assert second_service.name in page.text
        assert 'No saved Service Access URL' in page.text
        assert 'https://second.example/manual' not in page.text
        assert 'Shibboleth.sso/Login?' not in page.text
        assert 'data-preparation-continue' not in page.text
        assert client.get("/?view=inbox").text.find('data-preparation-start') == -1
        assert app.state.batch_import.snapshot().phase is BatchPhase.IDLE
    assert (stamp(path), stamp(config)) == unchanged


def test_start_progress_results_reload_and_no_paper_or_native_save_mutation(tmp_path):
    app, config, path, first, second_service = configured(tmp_path)
    _, paper_path = paper(tmp_path / "workspace", "a5-kept")
    paper_before = paper_path.read_bytes()
    before = stamp(path), stamp(config)
    with TestClient(app, base_url="http://localhost") as client:
        token = csrf_from_html(client.get("/?view=kept").text)
        assert client.post("/preparation/start", data={"csrf_token": "wrong"}).status_code == 403
        assert client.post("/api/connector/preparation/heartbeat", json=CAPABILITY).json()["ready"]
        ready = client.get("/fragments/preparation").text
        assert 'data-preparation-refresh' in ready
        assert 'hx-trigger="every 3s"' not in ready
        assert 'of 2 Access Services' in ready
        started = client.post("/preparation/start", data={"csrf_token": token})
        assert 'data-preparation-phase="running"' in started.text
        cmd = command(client)
        ident = identity(cmd)
        assert client.post("/api/connector/preparation/waiting",
                           json={**ident, "waiting": True}).status_code == 200
        waiting = client.get("/fragments/preparation").text
        assert 'data-preparation-state>Waiting for institution login</strong>' in waiting
        assert client.post("/api/connector/preparation/waiting",
                           json={**ident, "waiting": False}).status_code == 200
        result_body = {**ident, "outcome": "Failed", "reason": "navigation_failed"}
        assert client.post("/api/connector/preparation/result", json=result_body).status_code == 200
        assert client.post("/api/connector/preparation/result", json=result_body).json() == {"accepted": True}
        mid = client.get("/fragments/preparation").text
        assert 'data-preparation-count>1</span>' in mid
        assert 'data-preparation-stat="failed">1' in mid
        assert 'navigation_failed' in mid and first.name in mid
        assert 'data-preparation-pending-result' not in mid
        assert client.get("/api/preparation/state").json()["results"] == [{
            "service_id": first.id, "outcome": "Failed", "reason": "navigation_failed",
        }]
        second = command(client)
        assert second["service_id"] == second_service.id
        assert client.post("/api/connector/preparation/result", json={
            **identity(second), "outcome": "Needs attention", "reason": "resource_not_found",
        }).status_code == 200
        completed = client.get("/?view=kept").text
        assert 'data-preparation-phase="completed"' in completed
        assert 'data-preparation-count>2</span>' in completed
        assert 'data-preparation-stat="opened">0' in completed
        assert 'data-preparation-stat="failed">1' in completed
        assert 'data-preparation-stat="needs-attention">1' in completed
        assert 'navigation_failed' in completed and 'resource_not_found' in completed
        assert 'Import to Zotero' in completed and 'a5-kept' in completed
        assert app.state.batch_import.snapshot().phase is BatchPhase.IDLE
    assert (stamp(path), stamp(config)) == before
    assert paper_path.read_bytes() == paper_before
    assert state(paper_path).status.value == "kept"


def test_cancel_revokes_old_identity_and_recovers_after_bounded_grace(tmp_path, monkeypatch):
    from literature_monitor.web import preparation_coordinator as preparation_module

    app, config, path, _, _ = configured(tmp_path)
    before = stamp(path), stamp(config)
    now = [preparation_module.time.monotonic()]
    monkeypatch.setattr(preparation_module.time, "monotonic", lambda: now[0])
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/api/connector/preparation/heartbeat", json=CAPABILITY).json()["ready"]
        token = csrf_from_html(client.get("/?view=kept").text)
        client.post("/preparation/start", data={"csrf_token": token})
        cmd = command(client)
        ident = identity(cmd)
        assert client.post("/api/connector/preparation/waiting",
                           json={**ident, "waiting": True}).status_code == 200
        assert client.post("/preparation/cancel", data={
            "csrf_token": token, "task_id": cmd["task_id"],
            "generation": cmd["generation"] + 1,
        }).status_code == 200
        assert 'data-preparation-phase="running"' in client.get("/fragments/preparation").text
        assert client.post("/preparation/cancel", data={
            "csrf_token": token, "task_id": cmd["task_id"],
            "generation": cmd["generation"],
        }).status_code == 200
        stopped = client.get("/fragments/preparation").text
        assert 'data-preparation-phase="stopped"' in stopped
        assert 'data-preparation-state>Canceled</strong>' in stopped
        assert 'data-preparation-auth-wait' not in stopped
        assert 'data-preparation-recovery' in stopped
        assert '<button type="submit" disabled>Prepare CARSI Access</button>' in stopped
        assert client.get("/fragments/preparation").text.count('data-preparation-recovery') == 1
        assert client.post("/api/connector/preparation/authorize", json=ident).status_code == 409
        assert client.post("/api/connector/preparation/result", json={
            **ident, "outcome": "Failed", "reason": "connector_error",
        }).status_code == 409
        assert app.state.capture_coordinator.reserve_browser_task("import") is None
        now[0] += preparation_module.RELEASE_GRACE_SECONDS + 1
        available = client.get("/fragments/preparation").text
        assert 'data-preparation-recovery' not in available
        assert '<button type="submit" >Prepare CARSI Access</button>' in available
        restarted = client.post("/preparation/start", data={"csrf_token": token}).text
        assert 'data-preparation-phase="running"' in restarted
        assert command(client)["task_id"] != cmd["task_id"]
        assert client.post("/api/connector/preparation/authorize", json=ident).status_code == 409
    assert (stamp(path), stamp(config)) == before


def test_opened_counts_immediately_and_cancel_preserves_result(tmp_path):
    app, config, path, first, _ = configured(tmp_path)
    before = stamp(path), stamp(config)
    capture_before = app.state.capture_coordinator.snapshot()
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/api/connector/preparation/heartbeat", json=CAPABILITY).json()["ready"]
        token = csrf_from_html(client.get("/?view=kept").text)
        client.post("/preparation/start", data={"csrf_token": token})
        cmd = command(client)
        ident = identity(cmd)
        nonce = client.post("/api/connector/preparation/visit-authorize", json={
            **ident, "source_tab_id": 42,
        }).json()["nonce"]
        payload = {**ident, "outcome": "Opened", "reason": "target_opened", "evidence": {
            "nonce": nonce, "visit_action_observed": True, "source_tab_id": 42,
            "target_tab_id": 43, "created_target_tab_id": 43, "navigation_observed": True,
        }}
        assert client.post("/api/connector/preparation/result", json=payload).status_code == 200
        page = client.get("/fragments/preparation").text
        assert first.name in page and 'target_opened' in page
        assert 'data-preparation-count>1</span>' in page
        assert 'data-preparation-stat="opened">1' in page
        assert 'does not verify' not in page
        assert client.post("/api/connector/preparation/result", json=payload).json() == {"accepted": True}
        assert client.post("/api/connector/preparation/authorize", json=ident).status_code == 409
        canceled = client.post("/preparation/cancel", data={
            "csrf_token": token, "task_id": cmd["task_id"], "generation": cmd["generation"],
        }).text
        assert 'data-preparation-state>Canceled</strong>' in canceled
        assert 'data-preparation-count>1</span>' in canceled
        assert 'data-preparation-stat="opened">1' in canceled
        assert 'data-preparation-pending-result' not in canceled
        assert 'Import to Zotero' in client.get("/?view=kept").text
    assert (stamp(path), stamp(config)) == before
    assert app.state.capture_coordinator.snapshot() == capture_before
    assert app.state.batch_import.snapshot().phase is BatchPhase.IDLE


def test_no_unverified_continue_or_guessed_url_when_connector_unavailable(tmp_path):
    app, config, path, first, second_service = configured(tmp_path)
    with TestClient(app, base_url="http://localhost") as client:
        html = client.get("/?view=kept").text
        assert 'data-preparation-connector' in html
        token = csrf_from_html(html)
        blocked = client.post("/preparation/start", data={"csrf_token": token}).text
        assert 'data-preparation-state>Blocked</strong>' in blocked
        assert 'data-preparation-continue' not in html
        assert 'hx-post="/preparation/start"' in html
        assert second_service.access_url is None
        assert html.count('Open Service Access URL') == 1
        assert first.access_url in html
        assert 'https://ds.carsi.edu.cn/Shibboleth.sso/Login?' not in html
        assert 'Authenticated' not in html and 'Access granted' not in html
