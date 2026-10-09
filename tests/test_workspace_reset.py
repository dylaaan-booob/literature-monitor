"""A8 destruction tests use disposable, synthetic Workspaces only."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

import literature_monitor.application.workspace_reset as reset
from literature_monitor.application.workspace_reset import ResetRefused, execute_reset, preview_reset
from literature_monitor.inbox import render_default_inbox_base
from literature_monitor.materialize import materialize_papers
from literature_monitor.models import (
    Author, CanonicalMetadata, CanonicalPaper, ExternalIds,
    MetadataSource, Workflow, WorkflowStatus,
)
from literature_monitor.safe_write import workspace_operation_lock
from literature_monitor.web.app import create_app


@pytest.fixture
def dedicated(tmp_path: Path):
    config = tmp_path / "monitor.yaml"
    config.write_text(
        "name: Synthetic Monitor\nvenue_whitelist: list.md\nkeyword_expression: causal\n"
        "output_dir: workspace\nwindow_days: 14\nlog_level: INFO\n", encoding="utf-8",
    )
    (tmp_path / "list.md").write_text(
        "# Venues\n\n## Journals\n\n| Journal | ISSN-L | Publisher ID | Group |\n"
        "|---|---|---|---|\n| Biometrics | 0006-341X |  |  |\n", encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    source = CanonicalPaper(
        id=UUID("12345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        metadata=CanonicalMetadata(title="Synthetic Research", journal="Biometrics",
                                   publication_date=date(2026, 9, 20), abstract="Abstract"),
        external_ids=ExternalIds(doi="10.5555/synthetic"),
        authors=(Author(name="Synthetic Author"),),
        sources=(MetadataSource(provider="crossref", record_id="10.5555/synthetic",
                                retrieved_at=datetime(2026, 9, 21, tzinfo=timezone.utc)),),
        workflow=Workflow(status=WorkflowStatus.KEPT,
                          discovered_at=datetime(2026, 9, 21, tzinfo=timezone.utc)),
    )
    result = materialize_papers((source,), workspace)
    assert not result.has_errors
    (workspace / ".obsidian").mkdir()
    (workspace / ".obsidian" / "app.json").write_text('{"personal":true}', encoding="utf-8")
    (workspace / "monitor.yaml").write_text("protected local configuration", encoding="utf-8")
    (workspace / "list.md").write_text("protected list", encoding="utf-8")
    return config, workspace


def test_whole_workspace_deletes_unknown_legacy_and_obsidian(dedicated):
    config, workspace = dedicated
    before = config.read_bytes(), (config.parent / 'list.md').read_bytes()
    (workspace / '.DS_Store').write_bytes(b'Finder')
    (workspace / 'custom').mkdir()
    (workspace / 'custom' / 'notes.md').write_text('human notes')
    (workspace / 'Papers' / 'legacy.md').write_text('damaged or unsupported Paper')
    (workspace / 'Inbox.base').write_text('user modified')
    result = execute_reset(config, preview_reset(config))
    assert result.success and not workspace.exists()
    assert (config.read_bytes(), (config.parent / 'list.md').read_bytes()) == before
    assert not list(config.parent.glob('*reset-delete*'))
    assert not list(config.parent.glob('*recovery*'))


@pytest.mark.parametrize('marker', ['pending', 'uncertain', 'broken'])
def test_historical_attempt_marker_is_not_a_reset_blocker(dedicated, marker):
    config, workspace = dedicated
    path = next((workspace / 'Papers').iterdir())
    path.write_text(path.read_text().replace('type: paper', f'type: paper\nexport_attempt: {marker}'))
    assert execute_reset(config, preview_reset(config)).success
    assert not workspace.exists()


@pytest.mark.parametrize('kind', ['empty', 'missing', 'missing-parents'])
def test_empty_or_absent_workspace_is_success(tmp_path, kind):
    config = tmp_path / 'monitor.yaml'
    name = 'deep/missing/workspace' if kind == 'missing-parents' else 'workspace'
    config.write_text(f'keyword_expression: causal\noutput_dir: {name}\n')
    workspace = tmp_path / name
    if kind == 'empty': workspace.mkdir()
    assert execute_reset(config, preview_reset(config)).success
    assert not workspace.exists()
    assert not (tmp_path / 'deep').exists()


@pytest.mark.parametrize('name', ['/', str(Path.home()), str(Path.cwd()), '/tmp', '/Users', '/Library', '.'])
def test_dangerous_target_refuses_before_delete(tmp_path, name):
    config = tmp_path / 'monitor.yaml'
    config.write_text(f'keyword_expression: causal\noutput_dir: {name}\n')
    with pytest.raises(ResetRefused): preview_reset(config)


@pytest.mark.parametrize('name', ['src', 'src/literature_monitor', 'tests', 'connector', 'config', 'docs', '.git'])
def test_repository_non_workspace_directories_are_refused(tmp_path, monkeypatch, name):
    repository = tmp_path / 'repository'
    repository.mkdir()
    monkeypatch.setattr(reset, '__file__', str(repository / 'src/literature_monitor/application/workspace_reset.py'))
    config = repository / 'monitor.yaml'
    target = repository / name
    target.mkdir(parents=True)
    sentinel = target / 'private.txt'; sentinel.write_text('never delete')
    config.write_text(f'keyword_expression: causal\noutput_dir: {name}\n')
    with pytest.raises(ResetRefused): preview_reset(config)
    assert sentinel.read_text() == 'never delete'


def test_repository_dedicated_workspace_is_still_allowed(dedicated, monkeypatch):
    config, workspace = dedicated
    monkeypatch.setattr(reset, '__file__', str(config.parent / 'src/literature_monitor/application/workspace_reset.py'))
    assert execute_reset(config, preview_reset(config)).success
    assert not workspace.exists() and config.exists()


def test_repository_case_alias_cannot_bypass_dangerous_target_guard(tmp_path, monkeypatch):
    repository = tmp_path / 'repository'
    monkeypatch.setattr(reset, '__file__', str(repository / 'src/literature_monitor/application/workspace_reset.py'))
    with pytest.raises(ResetRefused):
        reset._safe_location(tmp_path / 'monitor.yaml', tmp_path / 'REPOSITORY/src')


def test_public_root_replaced_immediately_before_rmtree_is_preserved(dedicated, monkeypatch):
    config, workspace = dedicated
    plan = preview_reset(config)
    held = config.parent / 'original'
    original = reset.shutil.rmtree
    def replace(*args, **kwargs):
        if workspace.exists():
            workspace.rename(held)
        workspace.mkdir()
        (workspace / 'private.txt').write_text('replacement user directory')
        return original(*args, **kwargs)
    replace.avoids_symlink_attacks = True
    monkeypatch.setattr(reset.shutil, 'rmtree', replace)
    with next((workspace / 'Papers').iterdir()).open('rb') as confirmed_paper:
        result = execute_reset(config, plan)
        assert os.fstat(confirmed_paper.fileno()).st_nlink == 0
    assert (workspace / 'private.txt').read_text() == 'replacement user directory'
    assert not result.success
    if held.exists(): assert (held / 'Papers').exists()


@pytest.mark.parametrize('replacement', ['directory', 'symlink', 'missing'])
def test_root_replaced_at_atomic_capture_is_not_deleted(dedicated, monkeypatch, replacement):
    config, workspace = dedicated
    plan = preview_reset(config)
    held = config.parent / 'original'
    outside = config.parent / 'outside'; outside.mkdir()
    (outside / 'private.txt').write_text('external user file')
    original = reset.os.rename
    def replace(source, destination, **kwargs):
        if source == 'workspace' and destination == 'workspace':
            workspace.rename(held)
            if replacement == 'directory':
                workspace.mkdir(); (workspace / 'private.txt').write_text('replacement user directory')
            elif replacement == 'symlink': workspace.symlink_to(outside, target_is_directory=True)
        return original(source, destination, **kwargs)
    monkeypatch.setattr(reset.os, 'rename', replace)
    def no_delete(*args, **kwargs): pytest.fail('unconfirmed root must never reach rmtree')
    no_delete.avoids_symlink_attacks = True
    monkeypatch.setattr(reset.shutil, 'rmtree', no_delete)
    result = execute_reset(config, plan)
    assert not result.success and (held / 'Papers').exists()
    assert (outside / 'private.txt').read_text() == 'external user file'
    private = next(config.parent.glob('.literature-monitor-reset-*'))
    assert str(private) in result.message
    if replacement == 'directory':
        assert (private / 'workspace/private.txt').read_text() == 'replacement user directory'
    elif replacement == 'symlink': assert (private / 'workspace').is_symlink()


@pytest.mark.parametrize('linked', ['workspace', 'ancestor'])
def test_target_and_ancestor_links_never_delete_external_files(tmp_path, linked):
    config = tmp_path / 'monitor.yaml'
    outside = tmp_path / 'external'; outside.mkdir()
    (outside / 'workspace').mkdir()
    (outside / 'workspace' / 'private.txt').write_text('untouched')
    target = tmp_path / 'workspace'
    target.symlink_to(outside / 'workspace' if linked == 'workspace' else outside, target_is_directory=True)
    value = 'workspace' if linked == 'workspace' else 'workspace/workspace'
    config.write_text(f'keyword_expression: causal\noutput_dir: {value}\n')
    with pytest.raises(ResetRefused): preview_reset(config)
    assert (outside / 'workspace' / 'private.txt').read_text() == 'untouched'


def test_links_inside_workspace_are_removed_without_following(dedicated):
    config, workspace = dedicated
    outside = config.parent / 'external'; outside.mkdir()
    (outside / 'private.txt').write_text('untouched')
    (workspace / 'directory-link').symlink_to(outside, target_is_directory=True)
    (workspace / 'file-link').symlink_to(outside / 'private.txt')
    (workspace / 'broken-link').symlink_to(outside / 'missing')
    assert execute_reset(config, preview_reset(config)).success
    assert (outside / 'private.txt').read_text() == 'untouched'


def test_root_symlink_swap_at_recursive_open_preserves_external_data(dedicated, monkeypatch):
    config, workspace = dedicated
    outside = config.parent / 'external'; outside.mkdir()
    (outside / 'private.txt').write_text('untouched')
    plan = preview_reset(config)
    original = reset.os.open
    held = config.parent / 'original'
    def swap(name, flags, *args, **kwargs):
        if name == 'workspace' and not flags & os.O_NOFOLLOW and 'dir_fd' in kwargs:
            captured = next(config.parent.glob('.literature-monitor-reset-*/workspace'))
            captured.rename(held)
            captured.symlink_to(outside, target_is_directory=True)
        return original(name, flags, *args, **kwargs)
    monkeypatch.setattr(reset.os, 'open', swap)
    assert not execute_reset(config, plan).success
    assert (outside / 'private.txt').read_text() == 'untouched'
    assert (held / 'Papers').exists()


def test_git_repository_target_is_dangerous(dedicated):
    config, workspace = dedicated
    (workspace / '.git').mkdir()
    with pytest.raises(ResetRefused): preview_reset(config)


@pytest.mark.parametrize('change', ['config', 'directory', 'symlink'])
def test_stale_confirmation_does_not_delete_another_target(dedicated, change):
    config, workspace = dedicated
    plan = preview_reset(config)
    outside = config.parent / 'other'; outside.mkdir()
    (outside / 'private.txt').write_text('untouched')
    if change == 'config':
        config.write_text(config.read_text().replace('output_dir: workspace', 'output_dir: other'))
    else:
        workspace.rename(config.parent / 'original')
        if change == 'directory':
            workspace.mkdir(); (workspace / 'replacement').write_text('untouched')
        else: workspace.symlink_to(outside, target_is_directory=True)
    assert not execute_reset(config, plan).success
    assert (outside / 'private.txt').read_text() == 'untouched'
    if change == 'directory': assert (workspace / 'replacement').read_text() == 'untouched'


def test_contents_changed_since_confirmation_still_belong_to_whole_tree(dedicated):
    config, workspace = dedicated
    plan = preview_reset(config)
    (workspace / 'new-user-file').write_text('included')
    assert execute_reset(config, plan).success
    assert not workspace.exists()


def test_partial_delete_error_is_failure_without_retry(dedicated, monkeypatch):
    config, workspace = dedicated
    plan = preview_reset(config)
    removed = workspace / 'remove-me'; removed.write_text('included')
    calls = []
    def fail(name, *, dir_fd):
        calls.append(name)
        captured = next(config.parent.glob('.literature-monitor-reset-*/workspace'))
        (captured / 'remove-me').unlink()
        raise OSError('synthetic deletion failure')
    monkeypatch.setattr(reset.shutil, 'rmtree', fail)
    fail.avoids_symlink_attacks = True
    result = execute_reset(config, plan)
    assert not result.success and calls == ['workspace']
    captured = next(config.parent.glob('.literature-monitor-reset-*/workspace'))
    assert not (captured / 'remove-me').exists() and (captured / 'Papers').exists()
    assert not workspace.exists() and str(captured.parent) in result.message
    assert 'permanently deleted' in result.message and 'no recovery or automatic retry' in result.message


def test_safe_rmtree_capability_required(dedicated, monkeypatch):
    config, workspace = dedicated
    monkeypatch.setattr(reset.shutil.rmtree, 'avoids_symlink_attacks', False)
    assert not execute_reset(config, preview_reset(config)).success
    assert (workspace / 'Papers').exists()


def test_unsafe_external_lock_reports_failure_without_deletion(dedicated, monkeypatch):
    from literature_monitor.safe_write import CompareReadError
    config, workspace = dedicated
    def refuse(_path):
        raise CompareReadError('Workspace path lock is not a safe empty regular file')
    monkeypatch.setattr(reset, 'workspace_path_lock', refuse)
    result = execute_reset(config, preview_reset(config))
    assert not result.success and 'Workspace path lock' in result.message
    assert (workspace / 'Papers').exists()


def test_lost_delete_response_never_reports_success(dedicated, monkeypatch):
    config, workspace = dedicated
    original = reset.shutil.rmtree
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('lost response after tree deletion')
    fail.avoids_symlink_attacks = True
    monkeypatch.setattr(reset.shutil, 'rmtree', fail)
    result = execute_reset(config, preview_reset(config))
    assert not result.success and not workspace.exists()
    assert 'lost response' in result.message


def reset_data(client):
    html = client.get('/settings').text
    return {key: re.search(fr'name="{key}" value="([^"]+)"', html).group(1)
            for key in ('csrf_token', 'reset_token')} | {'reset_word': 'RESET'}


def test_settings_ui_csrf_one_confirmation_and_htmx(dedicated):
    config, workspace = dedicated
    with TestClient(create_app(config), base_url='http://localhost') as client:
        data = reset_data(client)
        html = client.get('/settings').text
        assert str(workspace) in html and 'entire configured Workspace' in html
        assert '.obsidian/' in html and 'custom files' in html
        assert 'Exact removal inventory' not in html
        data = reset_data(client)
        assert client.post('/settings/workspace-reset', data={**data, 'csrf_token':'bad'}).status_code == 403
        assert client.post('/settings/workspace-reset', data={**data, 'reset_word':'reset'}).status_code == 400
        assert client.post('/settings/workspace-reset', data=data).status_code == 409
        data = reset_data(client)
        response = client.post('/settings/workspace-reset', data=data, headers={'HX-Request':'true'})
        assert response.status_code == 200 and 'Workspace permanently deleted' in response.text
        assert '<html' not in response.text and not workspace.exists()
        assert client.post('/settings/workspace-reset', data=data).status_code == 409


def test_http_config_switch_refuses_stale_token(dedicated):
    config, workspace = dedicated
    other = config.parent / 'other'; other.mkdir()
    with TestClient(create_app(config), base_url='http://localhost') as client:
        data = reset_data(client)
        config.write_text(config.read_text().replace('output_dir: workspace', 'output_dir: other'))
        response = client.post('/settings/workspace-reset', data=data)
    assert response.status_code == 409 and workspace.exists() and other.exists()


def test_settings_save_uses_its_own_form_with_reset_visible(dedicated):
    from test_web_run_settings import browser_settings_submission
    config, workspace = dedicated
    with TestClient(create_app(config), base_url='http://localhost') as client:
        html = client.get('/settings').text
        assert 'name="reset_token"' in html
        fields = browser_settings_submission(html)
        assert 'reset_token' not in fields and 'reset_word' not in fields
        fields['institution_name'] = ['Example University']
        assert 'Settings saved.' in client.post('/settings/save', data=fields).text
    assert (workspace / 'Papers').exists()


def test_http_historical_uncertain_marker_allows_reset(dedicated):
    config, workspace = dedicated
    path = next((workspace / 'Papers').iterdir())
    path.write_text('old Paper\nexport_attempt: uncertain\n')
    with TestClient(create_app(config), base_url='http://localhost') as client:
        response = client.post('/settings/workspace-reset', data=reset_data(client))
    assert response.status_code == 200 and not workspace.exists()


def test_explicit_run_rebuilds_entire_deleted_workspace(dedicated, monkeypatch):
    from literature_monitor.application.monitor import run_monitor, RunOutcome
    from literature_monitor.application.provider_state import read_provider_state, ProviderStateStatus
    from literature_monitor.application.run_state import read_last_run_snapshot, LastRunReadStatus
    from test_application_monitor import install_core_mocks
    config, workspace = dedicated
    assert execute_reset(config, preview_reset(config)).success
    assert not workspace.exists()
    install_core_mocks(monkeypatch)
    result = run_monitor(config)
    assert result.outcome is RunOutcome.COMPLETED and result.created_papers == 1
    assert list((workspace / 'Papers').glob('*.md')) and list((workspace / 'Authors').glob('*.md'))
    assert (workspace / 'Inbox.base').read_text() == render_default_inbox_base()
    assert read_provider_state(workspace).status is ProviderStateStatus.AVAILABLE
    assert read_last_run_snapshot(workspace).status is LastRunReadStatus.AVAILABLE
    assert not (workspace / '.obsidian').exists()


def test_run_keeps_path_ownership_during_provider_work(dedicated, monkeypatch):
    import literature_monitor.application.monitor as monitor
    config, workspace = dedicated
    calls = []
    def core(*args, **kwargs):
        result = execute_reset(config, preview_reset(config))
        assert not result.success and workspace.exists()
        calls.append(True)
    monkeypatch.setattr(monitor, '_run_monitor_owned', core)
    monitor.run_monitor(config)
    assert calls == [True]


def test_external_lock_remains_exclusive_after_workspace_is_deleted(dedicated):
    from literature_monitor.application.monitor import run_monitor
    from literature_monitor.application.settings import load_settings, save_settings, SettingsSaveOutcome
    from literature_monitor.application.export_attempts import reserve_export_attempt
    from literature_monitor.application.batch_import import BatchPhase
    from literature_monitor.safe_write import ContentChangedError
    from literature_monitor.web.run_coordinator import RunCoordinator, StartOutcome
    from test_batch_import import service, finished
    config, workspace = dedicated
    program = """from pathlib import Path
import sys
import literature_monitor.application.workspace_reset as reset
config=Path(sys.argv[1]); plan=reset.preview_reset(config)
original=reset.shutil.rmtree
def hold(*args, **kwargs):
    original(*args, **kwargs)
    print('TREE_DELETED_LOCK_HELD', flush=True)
    sys.stdin.readline()
hold.avoids_symlink_attacks=True
reset.shutil.rmtree=hold
print(reset.execute_reset(config,plan).success, flush=True)
"""
    child = subprocess.Popen([sys.executable, '-c', program, str(config)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'TREE_DELETED_LOCK_HELD'
        assert not workspace.exists()
        with pytest.raises(ContentChangedError): run_monitor(config)
        assert RunCoordinator(config).start().outcome is StartOutcome.START_FAILED
        assert save_settings(config, load_settings(config).draft).outcome is SettingsSaveOutcome.WRITE_FAILED
        batch=service(); batch.start(workspace)
        assert finished(batch).phase is BatchPhase.BLOCKED
        with pytest.raises(ContentChangedError): reserve_export_attempt(workspace, UUID('12345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa'))
        assert not execute_reset(config, preview_reset(config)).success
        assert not workspace.exists()
    finally:
        out,_=child.communicate(input='\n',timeout=10)
    assert child.returncode==0 and out.strip()=='True'


@pytest.mark.parametrize('owner', ['run', 'batch'])
def test_other_process_run_or_batch_blocks_reset(dedicated, owner):
    config, workspace = dedicated
    program = """from pathlib import Path
import sys
from literature_monitor.safe_write import workspace_operation_lock
from literature_monitor.application.monitor import run_monitor
config=Path(sys.argv[1]); workspace=Path(sys.argv[2])
if sys.argv[3]=='run':
 import literature_monitor.application.monitor as monitor
 def hold(*args, **kwargs):
  print('OWNED',flush=True); sys.stdin.readline()
 monitor._run_monitor_owned=hold
 run_monitor(config)
else:
 from literature_monitor.application.batch_import import BatchImportService
 from literature_monitor.web.capture_coordinator import CaptureCoordinator
 import time
 coordinator=CaptureCoordinator();coordinator.heartbeat(connector_version='temporary',zotero_reachable=True)
 batch=BatchImportService(coordinator);batch.start(workspace)
 while coordinator.claim_command() is None: time.sleep(.005)
 print('OWNED',flush=True);sys.stdin.readline()
"""
    child=subprocess.Popen([sys.executable,'-c',program,str(config),str(workspace),owner],
                           stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='OWNED'
        assert not execute_reset(config,preview_reset(config)).success
        assert (workspace/'Papers').exists()
    finally:
        child.communicate(input='\n',timeout=10)
    assert child.returncode==0


@pytest.mark.parametrize('remove_inner_lock', [False, True])
def test_escaped_browser_grant_keeps_cross_process_ownership(dedicated, remove_inner_lock):
    from test_batch_import import service, command, finished, invocation
    from literature_monitor.web.capture_coordinator import CaptureOutcome, PdfOutcome
    config, workspace = dedicated
    batch = service(); batch.start(workspace)
    call = invocation(command(batch))
    assert batch.coordinator.authorize_parent_dispatch(call)
    if remove_inner_lock:
        (workspace / '.literature-monitor-operation.lock').unlink()
    assert batch.coordinator.submit_connector_result(
        request_id=call.request_id, invocation_id=call.invocation_id, doi_url=call.doi_url,
        tab_id=call.tab_id, session_id=call.session_id,
        outcome=CaptureOutcome.UNCONFIRMED, pdf_outcome=PdfOutcome.UNVERIFIED)
    finished(batch)
    assert batch.coordinator.has_unresolved_save_authority
    program = ('from pathlib import Path; import sys; '
               'from literature_monitor.application.workspace_reset import preview_reset,execute_reset; '
               'config=Path(sys.argv[1]); print(execute_reset(config,preview_reset(config)).success)')
    child = subprocess.run([sys.executable, '-c', program, str(config)],
                           capture_output=True, text=True, timeout=10)
    assert child.returncode == 0 and child.stdout.strip() == 'False'
    assert (workspace / 'Papers').exists()


def test_cross_process_native_guard_clears_only_after_old_pipeline_receipt(dedicated):
    from test_batch_import import service, command, finished, invocation, outcome
    from literature_monitor.web.capture_coordinator import CaptureOutcome
    from literature_monitor.safe_write import NATIVE_SAVE_RESET_GUARD
    config, workspace = dedicated
    batch = service()
    batch.start(workspace)
    old = command(batch)
    assert outcome(batch, old, CaptureOutcome.UNCONFIRMED)
    finished(batch)
    batch.start(workspace)
    new = command(batch)
    assert outcome(batch, new, CaptureOutcome.CONFIRMED)
    assert finished(batch).phase.value == 'completed'

    program = ('from pathlib import Path; import sys; '
               'from literature_monitor.safe_write import workspace_path_lock; '
               'with_lock=workspace_path_lock(Path(sys.argv[1])); '
               'with_lock.__enter__(); print("ACQUIRED"); with_lock.__exit__(None,None,None)')
    def probe():
        return subprocess.run([sys.executable, '-c', program, str(workspace / NATIVE_SAVE_RESET_GUARD)],
                              capture_output=True, text=True, timeout=10)

    before = probe()
    assert before.returncode != 0 and 'Workspace operation is already active' in before.stderr
    refused = execute_reset(config, preview_reset(config))
    assert not refused.success and 'External native-save Reset guard' in refused.message
    assert (workspace / 'Papers').exists()
    assert batch.coordinator.report_native_save_settled(invocation(old))
    after = probe()
    assert after.returncode == 0 and after.stdout.strip() == 'ACQUIRED', after.stderr
    assert (workspace / 'Papers').exists()


def test_reset_failure_htmx_swap_runtime(dedicated):
    import json
    import shutil
    from test_web_run_settings import SettingsDOM, SETTINGS_NODE_DOM

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node unavailable for the existing Settings DOM harness")
    config, _ = dedicated
    client = TestClient(create_app(config), base_url="http://localhost")
    page = client.get("/settings").text
    source = Path("src/literature_monitor/web/static/app.js").read_text()
    script = f"const PAGE={json.dumps(SettingsDOM(page).root)}, SOURCE={json.dumps(source)};\n" + SETTINGS_NODE_DOM + r'''
for (const status of [400, 409, 403, 500]) {
  const detail = {target: {id: "danger-zone"}, xhr: {status}, shouldSwap: false, isError: true};
  bodyHandlers["htmx:beforeSwap"]({detail});
  assert.equal(detail.shouldSwap, status === 400 || status === 409);
  assert.equal(detail.isError, status !== 400 && status !== 409);
}
'''
    result = subprocess.run([node], input=script, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
