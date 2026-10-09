"""A5 Web/DOM evidence uses isolated Workspaces, never real Chrome or Zotero."""
from pathlib import Path
import json
import re
import shutil
import subprocess
import threading

import pytest
from fastapi.testclient import TestClient

import literature_monitor.application.decisions as decisions
import literature_monitor.application.batch_import as batch_module
import literature_monitor.web.app as web
from literature_monitor.application.batch_import import BatchPaperResult, BatchPhase, BatchSnapshot
from literature_monitor.application.export_attempts import reserve_export_attempt
from literature_monitor.application.workspace import WorkspaceImportPlan
from literature_monitor.application.workspace import plan_workspace_import
from literature_monitor.safe_write import workspace_operation_lock
from literature_monitor.models import WorkflowStatus
from literature_monitor.web.capture_coordinator import CaptureOutcome, PdfOutcome
from test_batch_import import paper, command, outcome, finished, edit, state
from test_web_app import write_valid_config, listed_ids, selected_id
from test_web_run_settings import browser_settings_submission


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


@pytest.mark.parametrize('change', ['external', 'settings'])
def test_stale_workspace_form_rejected_and_fresh_target_form_works(tmp_path, change):
    app, config, root_a = setup(tmp_path)
    root_b = tmp_path/'workspace-b'
    _, path_a = paper(root_a, 'a')
    value_b, path_b = paper(root_b, 'b')
    before = path_a.read_bytes(), path_b.read_bytes()
    with TestClient(app, base_url='http://localhost') as client:
        old_form = start_data(client)
        if change == 'settings':
            values = browser_settings_submission(client.get('/settings').text)
            values['output_dir'] = ['workspace-b']
            saved = client.post('/settings/save', data=values)
            assert 'Settings saved.' in saved.text
        else:
            config.write_text(config.read_text().replace('output_dir: workspace', 'output_dir: workspace-b'))
        response = client.post('/imports/start', data=old_form)
        assert response.status_code == 409
        assert 'Workspace changed' in response.text
        assert listed_ids(response.text) == [str(value_b.id)]
        assert client.post('/api/connector/claim', json={}).json()['command'] is None
        denied = client.post('/api/connector/dispatch', json={
            'request_id': 'stale-form', 'invocation_id': 'stale-invocation',
            'doi_url': 'https://doi.org/10.5555/b', 'tab_id': 10, 'session_id': 'test-session'})
        assert denied.status_code == 409 and denied.json()['accepted'] is False
        assert (path_a.read_bytes(), path_b.read_bytes()) == before
        assert client.post('/imports/start', data=old_form).status_code == 409
        assert client.post('/imports/start', data=start_data(client)).status_code == 200
        cmd = client.post('/api/connector/claim', json={}).json()['command']
        assert cmd['doi_url'] == 'https://doi.org/10.5555/b'
        invocation = {**{k:v for k,v in cmd.items() if k != 'access_context'}, 'tab_id': 10, 'session_id': 'test-session'}
        assert client.post('/api/connector/dispatch', json=invocation).json()['accepted'] is True
        assert client.post('/api/connector/result', json={**invocation,
            'outcome': 'CONFIRMED', 'pdf_outcome': 'unverified'}).json()['accepted'] is True
        assert finished(app.state.batch_import).successful
        print(f'{change}: stale form rejected; no B claim; no B dispatch permission; fresh B form works')
    assert path_a.read_bytes() == before[0]
    assert state(path_b).status is WorkflowStatus.EXPORTED


@pytest.mark.parametrize('change', ['config', 'workspace', 'papers', 'lock'])
def test_target_change_between_post_check_and_locked_start_refuses_before_reservation(tmp_path, monkeypatch, change):
    app, config, root = setup(tmp_path)
    _, path = paper(root, 'a')
    _, b = paper(tmp_path/'workspace-b', 'b')
    with workspace_operation_lock(root):
        pass
    protected = path.read_bytes(), b.read_bytes()
    original = app.state.batch_import.start
    moved = None
    def change_before_start(workspace, **kwargs):
        nonlocal moved
        if change == 'config':
            config.write_text(config.read_text().replace('output_dir: workspace', 'output_dir: workspace-b'))
        else:
            target = {'workspace': root, 'papers': root/'Papers',
                      'lock': root/'.literature-monitor-operation.lock'}[change]
            moved = target.rename(target.with_name(target.name+'-old'))
            if change == 'lock':
                target.touch()
            else:
                target.mkdir()
                if change == 'workspace':
                    (target/'Papers').mkdir()
        return original(workspace, **kwargs)
    monkeypatch.setattr(app.state.batch_import, 'start', change_before_start)
    with TestClient(app, base_url='http://localhost') as client:
        response = client.post('/imports/start', data=start_data(client))
    assert response.status_code == 409 and 'Workspace changed' in response.text
    snapshot = finished(app.state.batch_import)
    assert snapshot.phase is BatchPhase.BLOCKED and snapshot.plan is None
    assert app.state.capture_coordinator.claim_command() is None
    actual = moved/'Papers/a.md' if change == 'workspace' else moved/'a.md' if change == 'papers' else path
    assert (actual.read_bytes(), b.read_bytes()) == protected


@pytest.mark.parametrize('change', ['metadata', 'config-identity', 'resolved-target'])
def test_same_output_setting_does_not_hide_changed_configuration_or_resolution(tmp_path, change):
    app, config, root = setup(tmp_path)
    _, a = paper(root, 'a')
    _, b = paper(tmp_path/'workspace-b', 'b')
    alias = tmp_path/'workspace-alias'
    if change == 'resolved-target':
        alias.symlink_to(root, target_is_directory=True)
        config.write_text(config.read_text().replace('output_dir: workspace', 'output_dir: workspace-alias'))
    before = a.read_bytes(), b.read_bytes()
    with TestClient(app, base_url='http://localhost') as client:
        data = start_data(client)
        if change == 'metadata':
            config.write_text(config.read_text().replace('causal', 'effect'))
        elif change == 'config-identity':
            config.rename(config.with_suffix('.old'))
            config.write_bytes(config.with_suffix('.old').read_bytes())
        else:
            alias.unlink()
            alias.symlink_to(tmp_path/'workspace-b', target_is_directory=True)
        response = client.post('/imports/start', data=data)
    assert response.status_code == 409 and 'Workspace changed' in response.text
    assert app.state.batch_import.snapshot().phase is BatchPhase.IDLE
    assert app.state.capture_coordinator.claim_command() is None
    assert (a.read_bytes(), b.read_bytes()) == before


def test_workspace_change_during_page_read_cannot_sign_new_target_for_old_papers(tmp_path, monkeypatch):
    app, config, root = setup(tmp_path)
    _, a = paper(root, 'a')
    _, b = paper(tmp_path/'workspace-b', 'b')
    original = web.load_workspace
    def read_then_switch(*args, **kwargs):
        result = original(*args, **kwargs)
        config.write_text(config.read_text().replace('output_dir: workspace\n', 'output_dir: workspace-b\n'))
        return result
    monkeypatch.setattr(web, 'load_workspace', read_then_switch)
    with TestClient(app, base_url='http://localhost') as client:
        response = client.get('/?view=kept')
        assert 'Workspace changed while rendering' in response.text
        assert 'name="import_token" value=""' in response.text
        refused = client.post('/imports/start', data={'csrf_token':app.state.csrf_token, 'import_token':''})
    assert refused.status_code == 409
    assert app.state.capture_coordinator.claim_command() is None
    assert not state(a).export_attempt_present and not state(b).export_attempt_present


def test_configuration_change_during_locked_plan_refuses_without_pending(tmp_path, monkeypatch):
    app, config, root = setup(tmp_path)
    _, a = paper(root, 'a')
    _, b = paper(tmp_path/'workspace-b', 'b')
    before = a.read_bytes(), b.read_bytes()
    original = batch_module.plan_workspace_import
    def plan_then_switch(*args, **kwargs):
        plan = original(*args, **kwargs)
        config.write_text(config.read_text().replace('output_dir: workspace', 'output_dir: workspace-b'))
        return plan
    monkeypatch.setattr(batch_module, 'plan_workspace_import', plan_then_switch)
    with TestClient(app, base_url='http://localhost') as client:
        response = client.post('/imports/start', data=start_data(client))
    assert response.status_code == 409 and 'Workspace changed' in response.text
    assert finished(app.state.batch_import).plan is None
    assert app.state.capture_coordinator.claim_command() is None
    assert (a.read_bytes(), b.read_bytes()) == before


def test_concurrent_stale_posts_and_fresh_two_page_posts_after_workspace_switch(tmp_path):
    app, config, root = setup(tmp_path)
    _, a = paper(root, 'a')
    _, b = paper(tmp_path/'workspace-b', 'b')
    before = a.read_bytes(), b.read_bytes()
    with TestClient(app, base_url='http://localhost') as client:
        old = start_data(client)
        config.write_text(config.read_text().replace('output_dir: workspace', 'output_dir: workspace-b'))
        for stale in (True, False):
            forms = [old, old] if stale else [start_data(client), start_data(client)]
            barrier = threading.Barrier(3)
            responses = []
            def post(form):
                barrier.wait(timeout=5)
                responses.append(client.post('/imports/start', data=form).status_code)
            workers = [threading.Thread(target=post, args=(form,)) for form in forms]
            for worker in workers:
                worker.start()
            barrier.wait(timeout=5)
            for worker in workers:
                worker.join(timeout=5)
                assert not worker.is_alive()
            assert sorted(responses) == ([409, 409] if stale else [200, 409])
            if stale:
                assert app.state.capture_coordinator.claim_command() is None
                assert (a.read_bytes(), b.read_bytes()) == before
        cmd = command(app.state.batch_import)
        assert cmd.doi_url == 'https://doi.org/10.5555/b'
        assert app.state.capture_coordinator.claim_command() is None
        assert outcome(app.state.batch_import, cmd)
        assert finished(app.state.batch_import).successful
    assert a.read_bytes() == before[0] and state(b).status is WorkflowStatus.EXPORTED


def test_three_navigation_entries_and_no_start_on_page_run_or_settings_refresh(tmp_path, monkeypatch):
    app, config, root = setup(tmp_path)
    kept, path = paper(root, 'a')
    before = path.read_bytes(), config.read_bytes(), (config.parent/'list.md').read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail('Read-only page/refresh must not start import')
    monkeypatch.setattr(app.state.batch_import, 'start', forbidden)
    with TestClient(app, base_url='http://localhost') as client:
        for route in ('/', '/?view=kept', '/settings', '/fragments/workspace?view=kept'):
            response = client.get(route)
            assert response.status_code == 200
            if route != '/fragments/workspace?view=kept':
                nav = re.search(r'<nav class="top-nav".*?</nav>', response.text, re.S).group()
                assert re.findall(r'>([^<]+)</a>', nav) == ['Inbox', 'Kept', 'Settings']
        assert 'data-import-form' not in client.get('/').text
        assert poll(client, app).status_code == 200
        assert app.state.batch_import.snapshot().phase is BatchPhase.IDLE
        assert app.state.capture_coordinator.claim_command() is None
    assert (path.read_bytes(), config.read_bytes(), (config.parent/'list.md').read_bytes()) == before


@pytest.mark.parametrize('action', ['keep', 'reject'])
@pytest.mark.parametrize('fail', [False, True])
def test_inbox_decisions_reload_markdown_without_connector_dependency(tmp_path, monkeypatch, action, fail):
    app, config, root = setup(tmp_path, connected=False)
    first, path = paper(root, 'a', status=WorkflowStatus.CANDIDATE)
    second, _ = paper(root, 'b', status=WorkflowStatus.CANDIDATE)
    historical, history = paper(root, 'historical', status=WorkflowStatus.EXPORTED)
    history_bytes = history.read_bytes()
    path.write_text(path.read_text() + '\nHuman notes remain.\n')
    if fail:
        def deny(*args, **kwargs):
            raise OSError('A5 simulated write failure')
        monkeypatch.setattr(decisions, '_replace_regular_text_at_identity', deny)
    with TestClient(app, base_url='http://localhost') as client:
        response = client.post(f'/papers/{first.id}/{action}', data={
            'csrf_token': app.state.csrf_token, 'expected_status': 'candidate', 'view': 'inbox', 'position': '0'})
        assert response.status_code == 200
        ids = listed_ids(response.text)
        assert (str(first.id) in ids) is fail
        assert str(second.id) in ids and str(historical.id) not in ids
        if fail:
            assert 'notice warning' in response.text and state(path).status is WorkflowStatus.CANDIDATE
        else:
            assert 'notice success' in response.text
            assert selected_id(response.text) == str(second.id)
            assert state(path).status is (WorkflowStatus.KEPT if action == 'keep' else WorkflowStatus.REJECTED)
    assert 'Human notes remain.' in path.read_text()
    assert history.read_bytes() == history_bytes and app.state.capture_coordinator.claim_command() is None


def test_one_whole_workspace_action_double_posts_multiple_pages_and_read_only_polling(tmp_path, monkeypatch):
    app, config, root = setup(tmp_path)
    older, a = paper(root, 'older', days=40)
    newer, b = paper(root, 'newer')
    paper(root, 'candidate', status=WorkflowStatus.CANDIDATE)
    _, history = paper(root, 'history', status=WorkflowStatus.EXPORTED)
    before = history.read_bytes(), config.read_bytes()
    batch = app.state.batch_import
    with TestClient(app, base_url='http://localhost') as client:
        data = start_data(client)
        page = client.get('/?view=kept').text
        assert set(listed_ids(page)) == {str(older.id), str(newer.id)}
        assert page.count('>Import to Zotero</button>') == 1
        assert 'type="checkbox"' not in page and 'may create duplicate items' in page
        assert client.post('/imports/start', data=data).status_code == 200
        assert {p.paper_id for p in batch.snapshot().plan.papers} == {older.id, newer.id}
        first = command(batch)
        assert client.post('/imports/start', data=data).status_code == 409
        # A second page with a fresh form still shares the A4 active batch.
        assert client.post('/imports/start', data=start_data(client)).status_code == 200
        def forbidden(*args, **kwargs):
            pytest.fail('UI polling may not consume the batch completion')
        original = app.state.capture_coordinator.consume_completion
        monkeypatch.setattr(app.state.capture_coordinator, 'consume_completion', forbidden)
        for _ in range(3):
            response = poll(client, app)
            assert 'Import in progress' in response.text and first.doi_url.split('/')[-1] in response.text
            assert 'data-import-poll' in response.text
        monkeypatch.setattr(app.state.capture_coordinator, 'consume_completion', original)
        assert app.state.capture_coordinator.claim_command() is None
        assert outcome(batch, first)
        assert outcome(batch, command(batch))
        assert finished(batch).successful
        response = poll(client, app)
        assert 'All planned parents exported' in response.text
        assert stat(response.text, 'exported-parent') == 2
        assert stat(response.text, 'pdf-unverified') == 2 and stat(response.text, 'normal-excluded') == 2
        assert not listed_ids(response.text) and 'data-import-poll' not in response.text
    assert state(a).status is state(b).status is WorkflowStatus.EXPORTED
    assert (history.read_bytes(), config.read_bytes()) == before


def test_completed_no_effect_duplicate_post_cannot_retry_same_paper(tmp_path):
    app, _, root = setup(tmp_path)
    _, path = paper(root, 'a')
    with TestClient(app, base_url='http://localhost') as client:
        data = start_data(client)
        client.post('/imports/start', data=data)
        cmd = command(app.state.batch_import)
        assert outcome(app.state.batch_import, cmd, CaptureOutcome.FAILED, dispatch=False)
        finished(app.state.batch_import)
        response = poll(client, app)
        assert 'Batch finished with errors' in response.text and stat(response.text, 'no-effect') == 1
        contents = path.read_bytes()
        assert client.post('/imports/start', data=data).status_code == 409
        assert app.state.capture_coordinator.claim_command() is None and path.read_bytes() == contents
        # A new explicit action may retry proven no-effect work.
        assert client.post('/imports/start', data=start_data(client)).status_code == 200
        later = command(app.state.batch_import)
        assert later.request_id != cmd.request_id
        assert outcome(app.state.batch_import, later, CaptureOutcome.FAILED, dispatch=False)
        finished(app.state.batch_import)


def test_concurrent_duplicate_posts_expose_one_command(tmp_path):
    app, _, root = setup(tmp_path)
    paper(root, 'a')
    with TestClient(app, base_url='http://localhost') as client:
        data = start_data(client)
        barrier = threading.Barrier(3)
        responses = []
        def post():
            barrier.wait(timeout=5)
            responses.append(client.post('/imports/start', data=data).status_code)
        workers = [threading.Thread(target=post) for _ in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait(timeout=5)
        for worker in workers:
            worker.join(timeout=5)
            assert not worker.is_alive()
        assert sorted(responses) == [200, 409]
        cmd = command(app.state.batch_import)
        assert app.state.capture_coordinator.claim_command() is None
        assert outcome(app.state.batch_import, cmd)
        assert finished(app.state.batch_import).statistics.exported_parent == 1


def test_polling_cannot_take_pending_completion_from_worker(tmp_path, monkeypatch):
    app, _, root = setup(tmp_path)
    _, path = paper(root, 'a')
    coordinator = app.state.capture_coordinator
    original = coordinator.wait_for_completion
    entered, release = threading.Event(), threading.Event()
    def held_wait():
        entered.set()
        assert release.wait(timeout=5)
        return original()
    monkeypatch.setattr(coordinator, 'wait_for_completion', held_wait)
    try:
        with TestClient(app, base_url='http://localhost') as client:
            client.post('/imports/start', data=start_data(client))
            assert entered.wait(timeout=5)
            assert outcome(app.state.batch_import, command(app.state.batch_import))
            assert coordinator.snapshot().completion_pending
            for _ in range(3):
                assert poll(client, app).status_code == 200
                assert client.post('/capture/poll', data={'csrf_token':app.state.csrf_token}).status_code == 200
                assert coordinator.snapshot().completion_pending
                assert state(path).status is WorkflowStatus.KEPT
            release.set()
            assert finished(app.state.batch_import).successful
            assert not coordinator.snapshot().completion_pending
    finally:
        release.set()


def test_terminal_snapshot_is_taken_before_final_markdown_reload(tmp_path, monkeypatch):
    app, _, root = setup(tmp_path)
    value, path = paper(root, 'a')
    with workspace_operation_lock(root) as lock:
        plan = plan_workspace_import(lock)
    terminal = BatchSnapshot(BatchPhase.COMPLETED, plan, None,
        (BatchPaperResult(value.id, 'exported', 'Parent exported', PdfOutcome.UNVERIFIED),), 'Finished')
    def complete_during_snapshot():
        edit(path, status='exported')
        return terminal
    monkeypatch.setattr(app.state.batch_import, 'snapshot', complete_during_snapshot)
    with TestClient(app, base_url='http://localhost') as client:
        response = poll(client, app)
    assert 'All planned parents exported' in response.text
    assert not listed_ids(response.text)
    assert state(path).status is WorkflowStatus.EXPORTED


@pytest.mark.parametrize('marker_state', ['pending', 'uncertain'])
def test_unresolved_attempt_visible_in_kept_list_before_import_or_selection(tmp_path, marker_state):
    app, _, root = setup(tmp_path)
    value, path = paper(root, 'a')
    reserve_export_attempt(root, value.id)
    if marker_state == 'uncertain':
        fm = dict(state(path).frontmatter)
        fm['export_attempt']['state'] = 'uncertain'
        edit(path, **fm)
    protected = path.read_bytes()
    with TestClient(app, base_url='http://localhost') as client:
        response = client.get('/?view=kept')
    assert 'Pending/uncertain import; automatic retry blocked' in response.text
    assert 'Zotero may already have saved this Paper' in response.text
    assert path.read_bytes() == protected
    assert app.state.batch_import.snapshot().phase is BatchPhase.IDLE


@pytest.mark.parametrize('bad', ['csrf', 'token', 'path', 'json', 'get', 'host', 'config', 'workspace'])
def test_import_request_security_and_unusable_workspace_fail_closed(tmp_path, bad):
    app, config, root = setup(tmp_path)
    _, path = paper(root, 'a')
    protected = path.read_bytes()
    with TestClient(app, base_url='http://localhost') as client:
        data = start_data(client)
        if bad == 'csrf':
            del data['csrf_token']
        elif bad == 'token':
            data['import_token'] = 'wrong'
        elif bad == 'path':
            data['workspace_path'] = str(tmp_path/'outside')
        elif bad == 'config':
            config.write_text('invalid: [')
        elif bad == 'workspace':
            (root/'Papers').rename(root/'unsafe-papers')
            (root/'Papers').symlink_to(root/'unsafe-papers', target_is_directory=True)
        if bad == 'json':
            result = client.post('/imports/start', json=data)
        elif bad == 'get':
            result = client.get('/imports/start')
        elif bad == 'host':
            result = client.post('/imports/start', data=data, headers={'host': 'evil.example'})
        else:
            result = client.post('/imports/start', data=data)
        expected = {'csrf':403, 'token':409, 'path':422, 'json':403, 'get':405, 'host':400, 'config':400, 'workspace':409}
        assert result.status_code == expected[bad]
        if bad == 'workspace':
            assert 'Workspace changed' in result.text
            assert app.state.batch_import.snapshot().phase is BatchPhase.IDLE
        assert app.state.capture_coordinator.claim_command() is None
        assert path.read_bytes() == protected
        assert client.post('/imports/poll', data={'view':'kept'}).status_code == 403


@pytest.mark.parametrize('scenario', ['uncertain', 'blocked', 'unavailable', 'invalid-kept', 'changed-paper'])
def test_truthful_failure_and_markdown_refresh(tmp_path, scenario):
    app, _, root = setup(tmp_path, connected=scenario != 'unavailable')
    first, a = paper(root, 'a')
    second, b = paper(root, 'b')
    if scenario == 'blocked':
        reserve_export_attempt(root, first.id)
    elif scenario == 'invalid-kept':
        edit(b, doi='bad', external_ids={'doi':'bad'})
    batch = app.state.batch_import
    with TestClient(app, base_url='http://localhost') as client:
        response = client.post('/imports/start', data=start_data(client))
        if scenario not in ('blocked', 'unavailable'):
            cmd = command(batch)
            if scenario == 'changed-paper':
                edit(b, status='rejected')
            assert outcome(batch, cmd, CaptureOutcome.UNCONFIRMED if scenario == 'uncertain' else CaptureOutcome.CONFIRMED)
        snapshot = finished(batch)
        response = poll(client, app, paper=str(first.id))
        headings = {'uncertain':'Import paused', 'blocked':'Import blocked', 'unavailable':'Connector/Zotero unavailable',
                    'invalid-kept':'Batch finished with errors', 'changed-paper':'Batch finished with errors'}
        assert headings[scenario] in response.text
        assert not snapshot.successful
        if scenario in ('uncertain', 'blocked'):
            assert 'Automatic retry is blocked' in response.text and 'may already have saved' in response.text
        if scenario == 'uncertain':
            assert stat(response.text, 'uncertain') == 1 and state(a).export_attempt is not None
        elif scenario in ('invalid-kept', 'changed-paper'):
            assert stat(response.text, 'exported-parent') == 1 and stat(response.text, 'skipped-blocked') == 1
        elif scenario == 'unavailable':
            assert not state(a).export_attempt_present and not state(b).export_attempt_present
        if scenario == 'changed-paper':
            assert str(second.id) not in listed_ids(response.text) and state(b).status is WorkflowStatus.REJECTED


def test_pdf_statistics_use_snapshot_evidence_without_claiming_unverified_saved(tmp_path, monkeypatch):
    app, _, root = setup(tmp_path)
    value, path = paper(root, 'a')
    results = tuple(BatchPaperResult(value.id, 'exported', 'Attributable parent', pdf) for pdf in PdfOutcome)
    snapshot = BatchSnapshot(BatchPhase.COMPLETED, WorkspaceImportPlan((), (), (), (1, 2)), None, results, 'Results')
    monkeypatch.setattr(app.state.batch_import, 'snapshot', lambda: snapshot)
    with TestClient(app, base_url='http://localhost') as client:
        html = poll(client, app).text
    assert stat(html, 'pdf-success') == stat(html, 'pdf-failure') == stat(html, 'pdf-unverified') == 1
    assert 'PDF: unverified' in html and 'does not confirm that a PDF was saved' in html
    assert 'PDF saved' not in html and state(path).status is WorkflowStatus.KEPT


def test_executable_js_displays_replay_conflict_preserves_selection_and_csrf_denial(tmp_path):
    from test_web_run_settings import SettingsDOM, SETTINGS_NODE_DOM
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node unavailable for the existing DOM harness')
    app, _, root = setup(tmp_path)
    value, _ = paper(root, 'a')
    with TestClient(app, base_url='http://localhost') as client:
        html = client.get(f'/?view=kept&paper={value.id}').text
    source = Path('src/literature_monitor/web/static/app.js').read_text()
    harness = SETTINGS_NODE_DOM.replace('vm.runInNewContext(SOURCE, {', '''
Element.prototype.setAttribute = function(name, value) { this.attrs[name] = value; };
Element.prototype.removeAttribute = function(name) { delete this.attrs[name]; };
Object.defineProperty(Element.prototype, 'style', {get: () => ({removeProperty() {}})});
vm.runInNewContext(SOURCE, {''')
    script = f'const PAGE={json.dumps(SettingsDOM(html).root)}, SOURCE={json.dumps(source)};\n' + harness + r'''
const root = document.getElementById('workspace-root');
assert.equal(root.dataset.selectedPaperId, root.querySelector('#paper-detail').dataset.selectedPaperId);
for (const code of [200, 400, 409, 403]) {
  const detail = {target: root, xhr: {status: code}, shouldSwap: code === 200};
  bodyHandlers['htmx:beforeSwap']({detail});
  assert.equal(detail.shouldSwap, code !== 403);
}
assert.equal(root.querySelector('[data-import-form]').attrs['hx-disabled-elt'], 'find button');
assert.equal(root.querySelector('[data-import-form]').attrs['hx-sync'], 'this:drop');
const notice = root.querySelector('[data-import-error]');
bodyHandlers['htmx:responseError']({detail: {elt: root.querySelector('[data-import-form]'), xhr: {status: 403}}});
assert.ok(notice.textContent.includes('HTTP 403')); assert.equal(notice.hidden, false);
bodyHandlers['htmx:sendError']({detail: {elt: root.querySelector('[data-import-form]'), xhr: {status: 0}}});
assert.ok(notice.textContent.includes('Cannot reach the local application'));
'''
    result = subprocess.run([node, '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
