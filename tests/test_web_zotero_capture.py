"""A3 Web retirement and one-shot completion delivery, without browser/Zotero I/O."""
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from capture_helpers import reservation
from test_web_app import write_valid_config, csrf_from_html
from literature_monitor.web.app import create_app
from literature_monitor.web.capture_coordinator import CaptureOutcome, PdfOutcome, SaveInvocation
from literature_monitor.application.export_attempts import reserve_export_attempt
from literature_monitor.models import WorkflowStatus
from literature_monitor.application.workspace import load_workspace


@pytest.mark.parametrize('route',['save-to-zotero','check-zotero'])
def test_legacy_routes_are_csrf_protected_and_cannot_publish_or_reconcile(tmp_path,route,monkeypatch):
    import literature_monitor.zotero_local as zotero
    def forbidden(*args,**kwargs):pytest.fail('Retired entrypoint must not contact Zotero')
    monkeypatch.setattr(zotero.ZoteroLocalClient,'resolve_identity',forbidden)
    handle=reservation(tmp_path);path=Path(handle.attempt.workspace_path)/handle.attempt.paper_path;before=path.read_bytes()
    app=create_app(tmp_path/'missing.yaml');coordinator=app.state.capture_coordinator
    coordinator.heartbeat(connector_version='test',zotero_reachable=True)
    with TestClient(app,base_url='http://localhost') as client:
        url=f'/papers/{handle.attempt.paper_id}/{route}'
        assert client.get(url).status_code==405
        assert client.post(url,data={'expected_status':'kept'}).status_code==403
        response=client.post(url,data={'csrf_token':app.state.csrf_token,'expected_status':'kept'})
        assert response.status_code==410 and 'retired' in response.text
    assert coordinator.claim_command() is None and path.read_bytes()==before


@pytest.mark.parametrize('outcome,status',[
    (CaptureOutcome.CONFIRMED,WorkflowStatus.EXPORTED),
    (CaptureOutcome.UNCONFIRMED,WorkflowStatus.KEPT),
    (CaptureOutcome.FAILED,WorkflowStatus.KEPT),
])
def test_poll_resolves_owned_completion_once_with_no_library_scan(tmp_path,outcome,status,monkeypatch):
    config,output=write_valid_config(tmp_path)
    handle=reservation(tmp_path)
    # Config points to this isolated Workspace. No user config is touched.
    config.write_text(config.read_text().replace('output_dir: workspace',f'output_dir: {handle.attempt.workspace_path}'))
    app=create_app(config);coordinator=app.state.capture_coordinator
    coordinator.heartbeat(connector_version='test',zotero_reachable=True);coordinator.start_capture(handle)
    command=coordinator.claim_command();inv=SaveInvocation(command.request_id,command.invocation_id,command.doi_url,100,'session')
    if outcome is CaptureOutcome.CONFIRMED:assert coordinator.authorize_parent_dispatch(inv)
    assert coordinator.submit_connector_result(**vars(inv),outcome=outcome,pdf_outcome=PdfOutcome.UNVERIFIED)
    path=Path(handle.attempt.workspace_path)/handle.attempt.paper_path;before=path.read_bytes()
    with TestClient(app,base_url='http://localhost') as client:
        # Read-only views and invalid polling must not complete an export.
        page=client.get('/',params={'view':'kept','paper':str(handle.attempt.paper_id)})
        assert page.status_code==200
        assert 'data-save-zotero-form' not in page.text and 'data-check-zotero-form' not in page.text
        assert path.read_bytes()==before
        assert client.post('/capture/poll',data={'view':'kept'}).status_code==403
        response=client.post('/capture/poll',data={'csrf_token':app.state.csrf_token,'view':'kept','paper':str(handle.attempt.paper_id)})
        assert response.status_code==200
        after=path.read_bytes()
        assert client.post('/capture/poll',data={'csrf_token':app.state.csrf_token,'view':'kept'}).status_code==200
        assert path.read_bytes()==after
    papers=load_workspace(Path(handle.attempt.workspace_path)).papers
    assert len(papers)==1 and papers[0].status is status
    assert papers[0].export_attempt is None


def test_bridge_has_no_unreserved_legacy_capture_bypass(tmp_path):
    app=create_app(tmp_path/'missing.yaml')
    with TestClient(app,base_url='http://localhost') as client:
        assert client.post('/api/connector/heartbeat',json={'version':'test','zotero_reachable':True}).status_code==200
        assert client.post('/api/connector/claim',json={}).json()=={'command':None}
        assert client.post('/api/connector/dispatch',json={'request_id':'old','invocation_id':'old','doi_url':'https://doi.org/10.1000/example','tab_id':100,'session_id':'session'}).status_code==409
