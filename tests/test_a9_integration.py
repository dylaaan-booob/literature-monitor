"""A9 HTTP integration regressions; Reset execution is always a non-deleting stub."""

import re

from fastapi.testclient import TestClient
import pytest

from literature_monitor.application.export_attempts import reserve_export_attempt
from literature_monitor.application.workspace_reset import ResetResult
from literature_monitor.web.capture_coordinator import CaptureOutcome, CaptureStage, SaveInvocation
import literature_monitor.web.app as web
from test_batch_import import command, finished, invocation, outcome, state
from test_workspace_reset import dedicated


def reset_form(client, app):
    page = client.get('/settings')
    token = re.search(r'name="reset_token" value="([^"]+)"', page.text).group(1)
    return {'csrf_token': app.state.csrf_token, 'reset_token': token, 'reset_word': 'RESET'}


@pytest.mark.parametrize('confirmed', [True, False])
def test_reset_after_retired_batch_completion_ignores_display_history(dedicated, monkeypatch, confirmed):
    config, workspace = dedicated
    app = web.create_app(config)
    coordinator = app.state.capture_coordinator
    coordinator.heartbeat(connector_version='A9-test', zotero_reachable=True)
    batch = app.state.batch_import
    batch.start(workspace)
    cmd = command(batch)
    assert outcome(batch, cmd, CaptureOutcome.CONFIRMED if confirmed else CaptureOutcome.FAILED,
                   dispatch=confirmed)
    assert finished(batch).statistics.exported_parent == int(confirmed)
    assert coordinator.snapshot().attempt.stage is CaptureStage.FINISHED
    before = {p: p.read_bytes() for p in workspace.rglob('*') if p.is_file()}
    calls = []
    def no_delete(config_path, plan):
        calls.append((config_path, plan.workspace))
        return ResetResult(False, 'A9 test executor: no deletion performed')
    monkeypatch.setattr(web, 'execute_reset', no_delete)
    with TestClient(app, base_url='http://localhost') as client:
        response = client.post('/settings/workspace-reset', data=reset_form(client, app),
                               headers={'HX-Request': 'true'})
    assert calls == [(config.resolve(), workspace.resolve())]
    assert response.status_code == 409  # The stub's failure must still be reported.
    assert 'A9 test executor: no deletion performed' in response.text
    assert not response.headers.get('HX-Trigger')
    assert {p: p.read_bytes() for p in workspace.rglob('*') if p.is_file()} == before


def test_uncertain_grant_survives_later_confirmed_batch_until_its_own_pipeline_receipt(dedicated, monkeypatch):
    config, workspace = dedicated
    app = web.create_app(config)
    coordinator = app.state.capture_coordinator
    coordinator.heartbeat(connector_version='lifecycle-test', zotero_reachable=True)
    batch = app.state.batch_import
    batch.start(workspace)
    first = command(batch)
    assert outcome(batch, first, CaptureOutcome.UNCONFIRMED)
    assert finished(batch).phase.value == 'paused'
    batch.start(workspace)
    second = command(batch)
    assert second.request_id != first.request_id
    assert outcome(batch, second, CaptureOutcome.CONFIRMED)
    assert finished(batch).phase.value == 'completed'
    assert coordinator.snapshot().attempt.stage is CaptureStage.FINISHED
    assert coordinator.snapshot().attempt.parent_outcome.value == 'CONFIRMED'
    assert coordinator.has_unresolved_save_authority

    before = {p: p.read_bytes() for p in workspace.rglob('*') if p.is_file()}
    calls = []
    def no_delete(*args):
        calls.append(args)
        return ResetResult(False, 'Non-deleting test executor')
    monkeypatch.setattr(web, 'execute_reset', no_delete)
    with TestClient(app, base_url='http://localhost') as client:
        refused = client.post('/settings/workspace-reset', data=reset_form(client, app))
        assert refused.status_code == 409
        assert '1 native-save dispatch grant(s)' in refused.text
        assert not calls
        # A new accepted parent did not terminate the first native grant.
        for wrong in (
            SaveInvocation('other-request', first.invocation_id, first.doi_url, 10, 'test-session'),
            SaveInvocation(first.request_id, 'other-invocation', first.doi_url, 10, 'test-session'),
            SaveInvocation(first.request_id, first.invocation_id, 'https://doi.org/10.5555/other', 10, 'test-session'),
            SaveInvocation(first.request_id, first.invocation_id, first.doi_url, 999, 'test-session'),
            SaveInvocation(first.request_id, first.invocation_id, first.doi_url, 10, 'other-session'),
        ):
            assert not coordinator.report_native_save_settled(wrong)
            assert client.post('/api/connector/native-save-settled', json=vars(wrong)).status_code == 409
        assert client.post('/api/connector/native-save-settled', json={
            **vars(invocation(first)), 'tab_id': True,
        }).status_code == 422
        assert not coordinator.submit_connector_result(
            **vars(invocation(first)), outcome=CaptureOutcome.CONFIRMED,
            pdf_outcome=web.PdfOutcome.UNVERIFIED)
        proof = client.post('/api/connector/native-save-settled', json=vars(invocation(first)))
        assert proof.status_code == 200 and proof.json() == {'accepted': True}
        assert client.post('/api/connector/native-save-settled', json=vars(invocation(first))).status_code == 409
        assert not coordinator.has_unresolved_save_authority
        after = client.post('/settings/workspace-reset', data=reset_form(client, app))
        assert after.status_code == 409
        assert 'Non-deleting test executor' in after.text
    assert len(calls) == 1
    assert {p: p.read_bytes() for p in workspace.rglob('*') if p.is_file()} == before


def test_each_native_grant_requires_its_own_pipeline_receipt(dedicated):
    config, workspace = dedicated
    app = web.create_app(config)
    coordinator = app.state.capture_coordinator
    coordinator.heartbeat(connector_version='lifecycle-test', zotero_reachable=True)
    batch = app.state.batch_import
    batch.start(workspace)
    old = command(batch)
    assert outcome(batch, old, CaptureOutcome.UNCONFIRMED)
    finished(batch)
    batch.start(workspace)
    new = command(batch)
    native = invocation(new)
    assert coordinator.authorize_parent_dispatch(native)
    assert coordinator.submit_connector_result(
        **vars(native), outcome=CaptureOutcome.CONFIRMED, pdf_outcome=web.PdfOutcome.UNVERIFIED)
    assert finished(batch).phase.value == 'paused'  # Parent acceptance is not pipeline completion.
    assert '2 native-save dispatch grant(s)' in coordinator.reset_blocker
    assert coordinator.report_native_save_settled(invocation(old))
    assert '1 native-save dispatch grant(s)' in coordinator.reset_blocker
    assert coordinator.report_native_save_settled(native)
    assert not coordinator.has_unresolved_save_authority


@pytest.mark.parametrize('stage', ['waiting', 'claimed', 'dispatched', 'pending', 'delivered', 'resolving', 'uncertain'])
def test_reset_still_blocks_unresolved_save_authority(dedicated, monkeypatch, stage):
    config, workspace = dedicated
    app = web.create_app(config)
    coordinator = app.state.capture_coordinator
    coordinator.heartbeat(connector_version='A9-test', zotero_reachable=True)
    calls = []
    monkeypatch.setattr(web, 'execute_reset', lambda *args: calls.append(args))
    with TestClient(app, base_url='http://localhost') as client:
        form = reset_form(client, app)
        target = state(next((workspace / 'Papers').glob('*.md')))
        reservation = reserve_export_attempt(workspace, target.paper_id)
        assert coordinator.start_capture(export_reservation=reservation).request_id
        if stage != 'waiting':
            from literature_monitor.web.capture_coordinator import SaveInvocation, PdfOutcome
            cmd = coordinator.claim_command()
            invocation = SaveInvocation(cmd.request_id, cmd.invocation_id, cmd.doi_url, 10, 'test-session')
            if stage != 'claimed':
                assert coordinator.authorize_parent_dispatch(invocation)
            if stage not in ('claimed', 'dispatched'):
                assert coordinator.submit_connector_result(
                    request_id=cmd.request_id, invocation_id=cmd.invocation_id, doi_url=cmd.doi_url,
                    tab_id=10, session_id='test-session',
                    outcome=CaptureOutcome.UNCONFIRMED if stage == 'uncertain' else CaptureOutcome.CONFIRMED,
                    pdf_outcome=PdfOutcome.UNVERIFIED)
                if stage != 'pending':
                    completion = coordinator.consume_completion()
                    if stage != 'delivered':
                        assert coordinator.claim_completion(completion)
                    if stage == 'uncertain':
                        coordinator.finish_resolution(completion)
        response = client.post('/settings/workspace-reset', data=form)
    assert response.status_code == 409
    if stage == 'uncertain':
        assert 'native-save dispatch grant(s)' in response.text
    elif stage in {'waiting', 'claimed', 'dispatched', 'pending', 'delivered', 'resolving'}:
        assert 'Connector capture or its Workspace save slot is active' in response.text
    else:
        assert 'Connector completion is awaiting local resolution' in response.text
    assert not calls
