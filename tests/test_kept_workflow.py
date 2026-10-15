"""Focused workflow wiring with temporary Workspaces and synthetic executor results."""
from dataclasses import replace
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
import yaml

from literature_monitor.application import settings
from literature_monitor.application.batch_import import BatchPhase
from literature_monitor.config import KeptWorkflowConfig, load_config, render_settings_list_text
from literature_monitor.web.preparation_coordinator import (
    PreparationOutcome, PreparationPhase, PreparationResult, PreparationSnapshot,
)
from test_a4_publisher_settings import setup as settings_setup
from test_batch_import import paper, command, outcome, finished, CaptureOutcome
from kept_workflow_helpers import setup as import_setup, start_data
from settings_api_helpers import form as settings_form_data


def current_settings_form(client):
    return settings_form_data(client.get('/api/ui-next/settings').json())


def setup(tmp_path):
    app, config, root = import_setup(tmp_path)
    draft = settings.load_settings(config).draft
    mapping = config.parent / 'list.md'
    mapping.write_text(render_settings_list_text(mapping.read_text(), draft.journals, (), access_services=(), path=mapping))
    return app, config, root


def configure(config, **preferences):
    raw = yaml.safe_load(config.read_text())
    raw['kept_workflow'] = preferences
    config.write_text(yaml.safe_dump(raw))


def action(state, csrf):
    return dict(csrf_token=csrf, workflow_id=state['workflow_id'], generation=state['generation'])


def controlled(app, monkeypatch):
    clock = [0.0]
    workflow = app.state.kept_workflow
    monkeypatch.setattr(workflow, '_run', lambda *args: None)
    monkeypatch.setattr(workflow, '_clock', lambda: clock[0])
    return workflow, clock


def test_settings_defaults_and_form_save_reload_preserve_workflow(tmp_path):
    config, mapping, _ = settings_setup(tmp_path)
    defaults = KeptWorkflowConfig()
    assert load_config(config).kept_workflow == settings.load_settings(config).draft.kept_workflow == defaults
    before = mapping.read_bytes()
    from literature_monitor.web.app import create_app
    with TestClient(create_app(config), base_url='http://localhost') as client:
        data = current_settings_form(client)
        data.update(auto_import_after_preparation='1', transition_delay_seconds='0.25', display_name='Ada')
        from starlette.datastructures import FormData
        from literature_monitor.web.settings_form import settings_draft_from_form, settings_form_from_submission
        values = settings_form_from_submission(FormData([(key, v) for key, entries in data.items()
                                                        for v in (entries if isinstance(entries, list) else [entries])]))
        draft, issues = settings_draft_from_form(values)
        assert not issues and settings.validate_settings(config, draft).config.kept_workflow.transition_delay_seconds == .25
        saved = client.post('/settings/save', data=data)
        assert saved.status_code == 200 and saved.json()['saved']
        expected = KeptWorkflowConfig(auto_import_after_preparation=True, transition_delay_seconds=.25, display_name='Ada')
        assert load_config(config).kept_workflow == expected
        data = current_settings_form(client)
        data['name'] = 'Renamed monitor'
        assert client.post('/settings/save', data=data).status_code == 200
        assert settings.load_settings(config).draft.kept_workflow == expected
        assert load_config(config).kept_workflow == expected
        reload_data = current_settings_form(client)
        reload_data.update(display_name='Unsaved', reload_step='confirm', reload_word='RELOAD')
        reload_response = client.post('/settings/reload', data=reload_data)
        assert reload_response.json()['saved']
        assert reload_response.json()['values']['display_name'] == 'Ada'
        assert reload_response.json()['values']['transition_delay_seconds'] == '0.25'
        invalid = current_settings_form(client)
        invalid['transition_delay_seconds'] = '0'
        saved_bytes = config.read_bytes()
        assert client.post('/settings/save', data=invalid).status_code == 200
        assert config.read_bytes() == saved_bytes
    assert mapping.read_bytes() == before


@pytest.mark.parametrize('invalid', [
    {'auto_import_after_preparation': 'true'}, {'transition_delay_seconds': 0},
    {'transition_delay_seconds': True}, {'transition_delay_seconds': float('inf')},
    {'password': 'secret'}, {'display_name': 'account@example.org'},
])
def test_settings_reject_invalid_workflow_values(invalid):
    with pytest.raises(ValueError):
        KeptWorkflowConfig.model_validate(invalid)


def test_no_service_auto_transition_is_once_and_get_never_advances(tmp_path, monkeypatch):
    app, config, root = setup(tmp_path)
    configure(config, auto_import_after_preparation=True, transition_delay_seconds=.25)
    _, path = paper(root, 'one')
    workflow, clock = controlled(app, monkeypatch)
    with TestClient(app, base_url='http://localhost') as client:
        form = start_data(client)
        started = client.post('/api/kept-workflow/start', json=form).json()
        assert started['phase'] == 'transition' and started['preparation']['task_id'] is None
        assert app.state.batch_import.snapshot().phase is BatchPhase.IDLE
        assert client.post('/api/kept-workflow/start', json=form).json()['workflow_id'] == started['workflow_id']
        clock[0] = 1
        read_only_state = client.get('/api/kept-workflow/state').json()
        for _ in range(2):
            assert client.get('/api/kept-workflow/state').json() == read_only_state
        assert app.state.batch_import.snapshot().phase is BatchPhase.IDLE
        workflow.advance()
        cmd = command(app.state.batch_import)
        assert client.post('/api/kept-workflow/start', json=form).json()['workflow_id'] == started['workflow_id']
        workflow.advance()
        assert app.state.capture_coordinator.claim_command() is None
        assert outcome(app.state.batch_import, cmd)
        assert finished(app.state.batch_import).successful
        workflow.advance()
        state = client.get('/api/kept-workflow/state').json()
        assert state['phase'] == 'completed' and state['navigation'] == 'kept'
        assert state['batch']['statistics']['pdf_unverified'] == 1
        assert client.get('/api/ui-next/workspace').status_code == 200
        assert client.post('/api/kept-workflow/acknowledge', json=action(state, app.state.csrf_token)).status_code == 200
        assert client.get('/api/kept-workflow/state').json()['navigation'] is None
        assert client.post('/api/kept-workflow/continue', json=action(state, app.state.csrf_token)).status_code == 409


@pytest.mark.parametrize('change', ['config', 'workspace', 'authorization'])
def test_changed_target_or_authorization_stops_transition(tmp_path, monkeypatch, change):
    app, config, root = setup(tmp_path)
    configure(config, auto_import_after_preparation=True, transition_delay_seconds=.25)
    _, path = paper(root, 'one')
    before = path.read_bytes()
    workflow, clock = controlled(app, monkeypatch)
    with TestClient(app, base_url='http://localhost') as client:
        client.post('/api/kept-workflow/start', json=start_data(client))
        if change == 'config':
            config.write_text(config.read_text() + '\n# edited during Preparation\n')
        elif change == 'workspace':
            root.rename(root.with_name('original-workspace'))
            root.mkdir()
        else:
            app.state.import_token = 'expired-authorization'
        clock[0] = 1
        workflow.advance()
        assert workflow.snapshot().phase == 'interrupted'
        assert app.state.batch_import.snapshot().phase is BatchPhase.IDLE
        target = path if change != 'workspace' else root.with_name('original-workspace') / 'Papers' / path.name
        assert target.read_bytes() == before


def test_manual_continue_cancel_empty_and_restart(tmp_path, monkeypatch):
    app, config, root = setup(tmp_path)
    workflow, clock = controlled(app, monkeypatch)
    with TestClient(app, base_url='http://localhost') as client:
        empty = client.post('/api/kept-workflow/start', json=start_data(client))
        assert empty.status_code == 409 and not empty.json()['import_started']
        paper(root, 'one')
        started = client.post('/api/kept-workflow/start', json=start_data(client)).json()
        assert started['can_continue'] and started['phase'] == 'waiting_for_user'
        clock[0] = 100
        workflow.advance()
        assert app.state.batch_import.snapshot().phase is BatchPhase.IDLE
        stale = {**action(started, app.state.csrf_token), 'generation': started['generation'] - 1}
        assert client.post('/api/kept-workflow/continue', json=stale).status_code == 409
        assert client.post('/api/kept-workflow/cancel', json=action(started, app.state.csrf_token)).status_code == 200
        workflow.advance()
        assert not workflow.snapshot().import_started
        started = client.post('/api/kept-workflow/start', json=start_data(client)).json()
        continued = client.post('/api/kept-workflow/continue', json=action(started, app.state.csrf_token))
        assert continued.status_code == 200 and continued.json()['import_started']
        assert client.post('/api/kept-workflow/continue', json=action(started, app.state.csrf_token)).status_code == 409
        assert outcome(app.state.batch_import, command(app.state.batch_import), CaptureOutcome.UNCONFIRMED)
        assert finished(app.state.batch_import).phase is BatchPhase.PAUSED
        workflow.advance()
        assert workflow.snapshot().phase == 'interrupted' and workflow.snapshot().navigation == 'kept'
    from literature_monitor.web.app import create_app
    restarted = create_app(config)
    assert restarted.state.kept_workflow.snapshot().phase == 'idle'
    assert restarted.state.batch_import.snapshot().phase is BatchPhase.IDLE


@pytest.mark.parametrize('manual, result, blocked', [
    (False, PreparationOutcome.OPENED, False), (True, PreparationOutcome.OPENED, False),
    (False, PreparationOutcome.NEEDS_ATTENTION, False), (False, PreparationOutcome.FAILED, False),
    (False, PreparationOutcome.OPENED, True),
])
def test_preparation_waiting_and_outcomes_gate_automatic_import(tmp_path, monkeypatch, manual, result, blocked):
    app, config, root = setup(tmp_path)
    configure(config, auto_import_after_preparation=True)
    paper(root, 'one')
    workflow, clock = controlled(app, monkeypatch)
    prep = PreparationSnapshot(PreparationPhase.RUNNING, 'task', 1, (), None, (), 'Login wait', waiting_for_user=True)
    current = [prep]
    monkeypatch.setattr(app.state.preparation_coordinator, 'start', lambda _: current[0])
    monkeypatch.setattr(app.state.preparation_coordinator, 'snapshot', lambda: current[0])
    with TestClient(app, base_url='http://localhost') as client:
        state = client.post('/api/kept-workflow/start', json=start_data(client)).json()
        assert state['waiting_for_login'] and not state['can_continue']
        clock[0] = 100
        workflow.advance()
        assert not workflow.snapshot().import_started
        assert client.post('/api/kept-workflow/continue', json=action(state, app.state.csrf_token)).status_code == 409
        current[0] = replace(prep, phase=PreparationPhase.BLOCKED if blocked else PreparationPhase.COMPLETED,
                             waiting_for_user=False, manual_services=(SimpleNamespace(id='manual'),) if manual else (),
                             results=(PreparationResult('svc', result, 'result'),))
        workflow.advance()
        expected = 'interrupted' if blocked else 'waiting_for_user' if manual or result is not PreparationOutcome.OPENED else 'transition'
        assert workflow.snapshot().phase == expected and not workflow.snapshot().import_started


def test_import_only_and_remaining_kept_prevent_return(tmp_path, monkeypatch):
    app, _, root = setup(tmp_path)
    paper(root, 'one')
    workflow, _ = controlled(app, monkeypatch)
    monkeypatch.setattr(app.state.preparation_coordinator, 'start', lambda _: pytest.fail('Import Only must skip Preparation'))
    with TestClient(app, base_url='http://localhost') as client:
        form = start_data(client)
        assert client.post('/api/kept-workflow/start', json={**form, 'csrf_token': 'wrong'}).status_code == 403
        state = client.post('/api/kept-workflow/start', json={**form, 'mode': 'import_only'}).json()
        assert state['phase'] == 'importing' and state['import_started']
        paper(root, 'new-kept')
        assert outcome(app.state.batch_import, command(app.state.batch_import))
        assert finished(app.state.batch_import).successful
        workflow.advance()
        assert workflow.snapshot().phase == 'completed' and workflow.snapshot().navigation == 'kept'
        assert any(entry['paper']['title'] == 'new-kept'
                   for entry in client.get('/api/ui-next/workspace').json()['kept']['papers'])


def test_busy_preparation_cannot_be_adopted_by_new_workflow(tmp_path, monkeypatch):
    app, config, root = setup(tmp_path)
    paper(root, 'one')
    workflow, _ = controlled(app, monkeypatch)
    previous = PreparationSnapshot(PreparationPhase.RUNNING, 'previous-task', 9, (), None, (), 'Existing task')
    monkeypatch.setattr(app.state.preparation_coordinator, 'snapshot', lambda: previous)
    monkeypatch.setattr(app.state.preparation_coordinator, 'start', lambda _: replace(previous, phase=PreparationPhase.BLOCKED))
    with TestClient(app, base_url='http://localhost') as client:
        response = client.post('/api/kept-workflow/start', json=start_data(client))
        assert response.status_code == 409 and workflow.snapshot().phase == 'interrupted'
        assert app.state.batch_import.snapshot().phase is BatchPhase.IDLE


def test_worker_continues_without_frontend_polling(tmp_path, monkeypatch):
    app, config, root = setup(tmp_path)
    configure(config, auto_import_after_preparation=True, transition_delay_seconds=.25)
    paper(root, 'one')
    clock = [0.0]
    monkeypatch.setattr(app.state.kept_workflow, '_clock', lambda: clock[0])
    with TestClient(app, base_url='http://localhost') as client:
        assert client.post('/api/kept-workflow/start', json=start_data(client)).json()['phase'] == 'transition'
        clock[0] = 1
        assert outcome(app.state.batch_import, command(app.state.batch_import))
        assert finished(app.state.batch_import).successful
        from test_batch_import import wait_until
        wait_until(lambda: app.state.kept_workflow.snapshot().phase == 'completed')
        assert app.state.kept_workflow.snapshot().navigation == 'kept'


def test_real_manual_services_show_links_and_require_continue(tmp_path, monkeypatch):
    from preparation_helpers import prepared
    from literature_monitor.web.app import create_app
    config, mapping, first, second = prepared(tmp_path)
    draft = settings.load_settings(config).draft
    services = tuple(item.model_copy(update={'access_method': 'manual'}) for item in draft.access_services)
    mapping.write_text(render_settings_list_text(mapping.read_text(), draft.journals, draft.publishers,
                                                access_services=services, path=mapping))
    configure(config, auto_import_after_preparation=True)
    app = create_app(config)
    workflow, clock = controlled(app, monkeypatch)
    with TestClient(app, base_url='http://localhost') as client:
        state = client.post('/api/kept-workflow/start', json=start_data(client)).json()
        assert state['phase'] == 'waiting_for_user' and state['can_continue']
        assert state['preparation']['task_id'] is None
        assert any(service['access_url'] == first.access_url
                   for service in state['preparation']['manual_services'])
        clock[0] = 100
        workflow.advance()
        assert app.state.batch_import.snapshot().phase is BatchPhase.IDLE
        assert app.state.preparation_coordinator.snapshot().phase is not PreparationPhase.RUNNING
        assert workflow.snapshot().phase == 'waiting_for_user'
