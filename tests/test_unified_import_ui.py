"""A5 Web/DOM evidence uses isolated Workspaces, never real Chrome or Zotero."""
import re

import pytest
from fastapi.testclient import TestClient

import literature_monitor.web.app as web
from literature_monitor.application.batch_import import BatchPhase
from literature_monitor.application.export_attempts import reserve_export_attempt
from literature_monitor.models import WorkflowStatus
from literature_monitor.web.capture_coordinator import CaptureOutcome, PdfOutcome
from test_batch_import import paper, command, outcome, finished, edit, state
from test_web_app import write_valid_config


def setup(tmp_path, *, connected=True):
    config, root = write_valid_config(tmp_path)
    app = web.create_app(config)
    if connected:
        app.state.capture_coordinator.heartbeat(connector_version='A5-test', zotero_reachable=True)
    return app, config, root


def start_data(client):
    html = client.get('/?view=kept').text
    return {field: re.search(fr'name="{field}" value="([^"]+)"', html).group(1)
            for field in ('csrf_token', 'import_token')}


def poll(client, app, **extra):
    return client.post('/imports/poll', data={'csrf_token': app.state.csrf_token, **extra})


def stat(html, key):
    return int(re.search(fr'data-stat="{key}">([0-9]+)', html).group(1))


def test_web_import_is_explicit_and_unconfirmed_remains_kept(tmp_path):
    app, _, root = setup(tmp_path)
    value, path = paper(root, "a")
    with TestClient(app, base_url="http://localhost") as client:
        client.get("/?view=kept")
        assert app.state.batch_import.snapshot().phase is BatchPhase.IDLE
        response = client.post("/imports/start", data=start_data(client))
        assert response.status_code == 200
        cmd = command(app.state.batch_import)
        invocation = {"request_id": cmd.request_id, "invocation_id": cmd.invocation_id,
                      "doi_url": cmd.doi_url, "tab_id": 10, "session_id": "native"}
        assert client.post("/api/connector/dispatch", json=invocation).status_code == 200
        result = client.post("/api/connector/result", json={
            **invocation, "outcome": "UNCONFIRMED", "pdf_outcome": "unverified",
            "failure_stage": "native_save",
        })
        assert result.status_code == 200
        snap = finished(app.state.batch_import)
        assert snap.phase is BatchPhase.PAUSED
        assert state(path).status is WorkflowStatus.KEPT
        assert not state(path).export_attempt_present
        html = poll(client, app, view="kept").text
        assert "Check Zotero before invoking Import again" in html
        retry = client.post("/imports/start", data=start_data(client))
        assert retry.status_code == 200
        cmd2 = command(app.state.batch_import)
        assert cmd2.request_id != cmd.request_id
        assert outcome(app.state.batch_import, cmd2)
        assert finished(app.state.batch_import).successful
        assert state(path).status is WorkflowStatus.EXPORTED


def test_web_rejects_reused_import_form_and_stale_connector_result(tmp_path):
    app, _, root = setup(tmp_path)
    _, path = paper(root, "a")
    with TestClient(app, base_url="http://localhost") as client:
        form = start_data(client)
        assert client.post("/imports/start", data=form).status_code == 200
        assert client.post("/imports/start", data=form).status_code == 409
        cmd = command(app.state.batch_import)
        invocation = {"request_id": cmd.request_id, "invocation_id": cmd.invocation_id,
                      "doi_url": cmd.doi_url, "tab_id": 10, "session_id": "native"}
        assert client.post("/api/connector/result", json={
            **invocation, "outcome": "CONFIRMED", "pdf_outcome": "unverified",
        }).status_code == 409
        assert client.post("/api/connector/dispatch", json=invocation).status_code == 200
        assert client.post("/api/connector/dispatch", json=invocation).status_code == 409
        # A finished upstream pipeline supplies separate custody evidence.
        assert client.post("/api/connector/native-save-settled", json=invocation).status_code == 200
        assert client.post("/api/connector/result", json={
            **invocation, "outcome": "CONFIRMED", "pdf_outcome": "unverified",
        }).status_code == 200
        assert finished(app.state.batch_import).successful
        assert client.post("/api/connector/result", json={
            **invocation, "outcome": "CONFIRMED", "pdf_outcome": "unverified",
        }).status_code == 409
        assert state(path).status is WorkflowStatus.EXPORTED


@pytest.mark.parametrize("marker", ["pending", "uncertain", "broken"])
def test_legacy_markers_are_preserved_and_not_a_web_blocker(tmp_path, marker):
    app, _, root = setup(tmp_path)
    value, path = paper(root, "a")
    if marker == "broken":
        payload = {"old": "unparseable"}
    else:
        payload = reserve_export_attempt(root, value.id).attempt.model_dump(mode="json")
        payload["state"] = marker
    edit(path, export_attempt=payload)
    with TestClient(app, base_url="http://localhost") as client:
        html = client.get("/?view=kept").text
        assert "Import to Zotero" in html
        assert "Legacy export-attempt metadata retained" in html
        assert client.post("/imports/start", data=start_data(client)).status_code == 200
        assert outcome(app.state.batch_import, command(app.state.batch_import))
        assert finished(app.state.batch_import).successful
    assert state(path).frontmatter["export_attempt"] == payload
    assert state(path).status is WorkflowStatus.EXPORTED


def test_csrf_denial_never_starts_import(tmp_path):
    app, _, root = setup(tmp_path)
    _, path = paper(root, "a")
    before = path.read_bytes()
    with TestClient(app, base_url="http://localhost") as client:
        values = start_data(client)
        del values["csrf_token"]
        assert client.post("/imports/start", data=values).status_code == 403
        assert client.post("/imports/poll", data={}).status_code == 403
        assert app.state.batch_import.snapshot().phase is BatchPhase.IDLE
        assert path.read_bytes() == before


def test_pdf_unverified_is_separate_from_native_parent_acceptance(tmp_path):
    app, _, root = setup(tmp_path)
    _, path = paper(root, "a")
    with TestClient(app, base_url="http://localhost") as client:
        client.post("/imports/start", data=start_data(client))
        assert outcome(app.state.batch_import, command(app.state.batch_import))
        assert finished(app.state.batch_import).successful
        html = poll(client, app, view="kept").text
        assert stat(html, "exported-parent") == 1
        assert stat(html, "pdf-unverified") == 1
        assert stat(html, "pdf-success") == stat(html, "pdf-failure") == 0
        assert "does not confirm that a PDF was saved" in html


def test_ui_shows_transient_failures_without_permanent_marker(tmp_path):
    app, _, root = setup(tmp_path)
    _, path = paper(root, "a")
    with TestClient(app, base_url="http://localhost") as client:
        client.post("/imports/start", data=start_data(client))
        cmd = command(app.state.batch_import)
        assert app.state.capture_coordinator.submit_connector_result(
            request_id=cmd.request_id, invocation_id=cmd.invocation_id,
            doi_url=cmd.doi_url, tab_id=None, session_id=None,
            outcome=CaptureOutcome.FAILED, pdf_outcome=PdfOutcome.UNVERIFIED,
        )
        assert finished(app.state.batch_import).statistics.no_effect == 1
        html = poll(client, app, view="kept").text
        assert "Pre-dispatch failure" in html
        assert state(path).status is WorkflowStatus.KEPT
        assert not state(path).export_attempt_present
