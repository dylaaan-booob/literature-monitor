"""A3 durable parent completion; temporary Workspaces and no Zotero I/O."""
from dataclasses import replace
from pathlib import Path
import os
from uuid import uuid4

import pytest
import yaml

from capture_helpers import reservation
import literature_monitor.application.export_attempts as exports
import literature_monitor.safe_write as safe_write
import literature_monitor.web.capture_coordinator as capture_module
from literature_monitor.application.export_attempts import (
    ExportAttemptError, ExportCompletionOutcome, commit_exported, reserve_export_attempt,
    resolve_export_completion,
)
from literature_monitor.markdown_state import serialize_document, parse_paper_state
from literature_monitor.materialize import materialize_papers
from literature_monitor.models import ExportAttemptState, WorkflowStatus
from literature_monitor.web.capture_coordinator import (
    CaptureCoordinator, CaptureOutcome, CaptureStartOutcome, PdfOutcome, SaveInvocation,
)
from literature_monitor.web.zotero_capture import process_capture_completion


def parts(path):
    _, fm, body = path.read_bytes().decode().split('---', 2)
    return yaml.safe_load(fm), body[1:]


def path_of(handle):
    return Path(handle.attempt.workspace_path) / handle.attempt.paper_path


def edit(path, **changes):
    fm, body = parts(path)
    fm.update(changes)
    path.write_bytes(serialize_document(fm, body).encode())


def prepared(tmp_path, *, dispatch=True, outcome=CaptureOutcome.CONFIRMED):
    handle = reservation(tmp_path)
    coordinator = CaptureCoordinator()
    coordinator.heartbeat(connector_version='test', zotero_reachable=True)
    assert coordinator.start_capture(handle).outcome is CaptureStartOutcome.STARTED
    command = coordinator.claim_command()
    invocation = SaveInvocation(command.request_id, command.invocation_id, command.doi_url, 100, 'native-session')
    if dispatch:
        assert coordinator.authorize_parent_dispatch(invocation)
    assert coordinator.submit_connector_result(**vars(invocation), outcome=outcome, pdf_outcome=PdfOutcome.UNVERIFIED)
    return handle, coordinator, invocation


def test_confirmed_unverified_pdf_commits_one_atomic_status_and_marker_write(tmp_path, monkeypatch):
    handle = reservation(tmp_path)
    path = path_of(handle)
    # Add human content before reserving a fresh, exact revision.
    exports.clear_export_attempt(handle, evidence=exports.NoSaveEffectEvidence(
        handle.attempt.attempt_id, exports.NoSaveEffectReason.COMMAND_NEVER_EXPOSED, 'Test command never published'))
    fm, body = parts(path)
    fm['custom'] = {'notes': ['人工', 3]}
    body += '\r\n## Notes\r\nExact human text.\r\n'
    path.write_bytes(serialize_document(fm, body).encode())
    handle = reserve_export_attempt(Path(handle.attempt.workspace_path), handle.attempt.paper_id)
    coordinator = CaptureCoordinator()
    coordinator.heartbeat(connector_version='test', zotero_reachable=True)
    coordinator.start_capture(handle)
    command = coordinator.claim_command()
    invocation = SaveInvocation(command.request_id, command.invocation_id, command.doi_url, 100, 'native-session')
    assert coordinator.authorize_parent_dispatch(invocation)
    assert coordinator.submit_connector_result(**vars(invocation), outcome=CaptureOutcome.CONFIRMED, pdf_outcome=PdfOutcome.UNVERIFIED)
    before, before_body = parts(path)
    writes = []
    original = exports.replace_regular_text_at_identity
    def observe(target, contents, **kwargs):
        fm = yaml.safe_load(contents.split('---', 2)[1])
        writes.append((fm['status'], 'export_attempt' in fm))
        return original(target, contents, **kwargs)
    monkeypatch.setattr(exports, 'replace_regular_text_at_identity', observe)
    processed = process_capture_completion(coordinator)
    assert processed.result.outcome is ExportCompletionOutcome.EXPORTED
    assert processed.completion.pdf_outcome is PdfOutcome.UNVERIFIED
    assert writes == [('exported', False)]
    after, after_body = parts(path)
    assert after.pop('status') == 'exported'
    before.pop('status'); before.pop('export_attempt')
    assert before == after and before_body == after_body
    assert 'zotero_key' not in after
    assert process_capture_completion(coordinator).result is None
    assert not coordinator.authorize_parent_dispatch(invocation)
    assert not coordinator.submit_connector_result(**vars(invocation), outcome=CaptureOutcome.CONFIRMED, pdf_outcome=PdfOutcome.UNVERIFIED)
    with pytest.raises(ExportAttemptError):
        reserve_export_attempt(Path(handle.attempt.workspace_path), handle.attempt.paper_id)


@pytest.mark.parametrize('change', ['request', 'invocation', 'tab', 'session', 'doi', 'uuid', 'reservation', 'parent', 'coordinator'])
def test_unowned_or_substituted_completion_never_mutates_paper(tmp_path, change):
    handle, coordinator, invocation = prepared(tmp_path)
    completion = coordinator.consume_completion()
    path = path_of(handle); before = path.read_bytes()
    changed = completion
    other_coordinator = coordinator
    if change == 'request': changed = replace(completion, request_id='other')
    elif change in ('invocation', 'tab', 'session'):
        inv = replace(invocation, **{'invocation': {'invocation_id': 'other'}, 'tab': {'tab_id': 101}, 'session': {'session_id': 'other'}}[change])
        changed = replace(completion, save_invocation=inv)
    elif change == 'doi': changed = replace(completion, normalized_doi='10.1000/other')
    elif change == 'uuid': changed = replace(completion, paper_id=uuid4())
    elif change == 'reservation': changed = replace(completion, export_reservation=replace(handle, contents='other'))
    elif change == 'parent': changed = replace(completion, parent_outcome=capture_module.ParentOutcome.UNCERTAIN)
    else: other_coordinator = CaptureCoordinator()
    assert resolve_export_completion(changed, coordinator=other_coordinator).outcome is ExportCompletionOutcome.FAILED
    assert path.read_bytes() == before
    assert resolve_export_completion(completion, coordinator=coordinator).outcome is ExportCompletionOutcome.EXPORTED
    assert resolve_export_completion(completion, coordinator=coordinator).outcome is ExportCompletionOutcome.FAILED


@pytest.mark.parametrize('change', ['doi','uuid','status','path','marker','body','inode','duplicate','workspace','papers','lock','symlink','fifo'])
def test_confirmed_changed_identity_or_revision_refuses_export(tmp_path, change):
    handle, coordinator, _ = prepared(tmp_path)
    path = path_of(handle); root = path.parent.parent
    if change == 'doi': edit(path, doi='10.1000/other', external_ids={'doi':'10.1000/other'})
    elif change == 'uuid': edit(path, id=str(uuid4()))
    elif change == 'status': edit(path, status='rejected')
    elif change == 'path': path = path.rename(path.with_name('moved.md'))
    elif change == 'marker':
        fm,_ = parts(path); fm['export_attempt']['attempt_id']='f'*64; edit(path, export_attempt=fm['export_attempt'])
    elif change == 'body': path.write_bytes(path.read_bytes()+b'\nHuman concurrent edit\n')
    elif change == 'inode':
        payload=path.read_bytes();path.rename(path.with_suffix('.old'));path.write_bytes(payload)
    elif change == 'duplicate': path.with_name('duplicate.md').write_bytes(path.read_bytes())
    elif change == 'workspace':
        root.rename(root.with_name(root.name+'-old'));path.parent.mkdir(parents=True);path.write_bytes(handle.contents.encode())
    elif change == 'papers':
        path.parent.rename(root/'old-Papers');path.parent.mkdir();path.write_bytes(handle.contents.encode())
    elif change == 'lock':
        lock=root/'.literature-monitor-operation.lock';lock.rename(root/'old-lock');lock.touch()
    else:
        old=path.rename(path.with_suffix('.old'))
        if change=='symlink':path.symlink_to(old)
        else:os.mkfifo(path);path=old
    before = path.read_bytes()
    result = process_capture_completion(coordinator).result
    assert result.outcome is ExportCompletionOutcome.FAILED
    assert path.read_bytes() == before
    assert 'export_attempt' in parts(path)[0]
    assert parts(path)[0]['status'] != 'exported'


@pytest.mark.parametrize('failure', ['before_replace','replace','fsync_after_replace','uncertain_write'])
def test_confirmed_write_failure_retains_unresolved_protection(tmp_path, monkeypatch, failure):
    handle, coordinator, invocation = prepared(tmp_path)
    path=path_of(handle)
    def fail(*args, **kwargs): raise OSError('injected completion persistence failure')
    if failure in ('before_replace','uncertain_write'):monkeypatch.setattr(exports,'replace_regular_text_at_identity',fail)
    elif failure == 'replace':monkeypatch.setattr(safe_write.os,'replace',fail)
    else:
        original=os.fsync; directory_id=(path.parent.stat().st_dev,path.parent.stat().st_ino)
        def fsync(fd):
            info=os.fstat(fd)
            if (info.st_dev,info.st_ino)==directory_id: fail()
            return original(fd)
        monkeypatch.setattr(safe_write.os,'fsync',fsync)
    assert process_capture_completion(coordinator).result.outcome is ExportCompletionOutcome.FAILED
    fm,_=parts(path)
    assert fm['status']=='kept' and fm['export_attempt']['state'] in ('pending','uncertain')
    assert not coordinator.authorize_parent_dispatch(invocation)
    with pytest.raises(ExportAttemptError):reserve_export_attempt(path.parent.parent,handle.attempt.paper_id)
    assert process_capture_completion(coordinator).result is None


@pytest.mark.parametrize('dispatched', [False, True])
def test_uncertain_response_blocks_retry_independent_of_dispatch(tmp_path, dispatched):
    handle, coordinator, invocation=prepared(tmp_path,dispatch=dispatched,outcome=CaptureOutcome.UNCONFIRMED)
    assert process_capture_completion(coordinator).result.outcome is ExportCompletionOutcome.UNCERTAIN
    fm,_=parts(path_of(handle))
    assert fm['status']=='kept' and fm['export_attempt']['state']=='uncertain'
    before = path_of(handle).read_bytes()
    # A still-live escaped grant holds external exclusion; a historical marker
    # without a grant retains the existing durable Import fence.
    refused = safe_write.ContentChangedError if dispatched else ExportAttemptError
    with pytest.raises(refused):reserve_export_attempt(Path(handle.attempt.workspace_path),handle.attempt.paper_id)
    assert path_of(handle).read_bytes() == before


def test_verified_no_dispatch_failure_revokes_command_before_clearance(tmp_path):
    handle, coordinator, invocation=prepared(tmp_path,dispatch=False,outcome=CaptureOutcome.FAILED)
    assert not coordinator.authorize_parent_dispatch(invocation)
    assert process_capture_completion(coordinator).result.outcome is ExportCompletionOutcome.NO_EFFECT
    assert parts(path_of(handle))[0]['status']=='kept'
    assert 'export_attempt' not in parts(path_of(handle))[0]
    assert not coordinator.authorize_parent_dispatch(invocation)
    # A fresh explicit reservation has a new identity; old command remains unusable.
    fresh=reserve_export_attempt(Path(handle.attempt.workspace_path),handle.attempt.paper_id)
    assert fresh.attempt.attempt_id != handle.attempt.attempt_id


@pytest.mark.parametrize('claimed', [False,True])
def test_timeout_is_uncertain_and_late_confirmation_cannot_export(tmp_path,monkeypatch,claimed):
    now=[0.0];monkeypatch.setattr(capture_module,'_monotonic',lambda:now[0])
    handle=reservation(tmp_path);coordinator=CaptureCoordinator();coordinator.heartbeat(connector_version='test',zotero_reachable=True)
    coordinator.start_capture(handle)
    command=coordinator.claim_command() if claimed else None
    if claimed:
        invocation=SaveInvocation(command.request_id,command.invocation_id,command.doi_url,100,'session')
        assert coordinator.authorize_parent_dispatch(invocation)
    now[0]=1000
    assert process_capture_completion(coordinator).result.outcome is ExportCompletionOutcome.UNCERTAIN
    if claimed:
        assert not coordinator.submit_connector_result(**vars(invocation),outcome=CaptureOutcome.CONFIRMED,pdf_outcome=PdfOutcome.UNVERIFIED)
    assert parts(path_of(handle))[0]['export_attempt']['state']=='uncertain'


@pytest.mark.parametrize('legacy', [{'status':'in_zotero'},{'zotero_key':None},{'zotero_key':'PARENT01'},{'zotero_key':{}}])
def test_legacy_schema_anywhere_in_workspace_blocks_export_without_migration(tmp_path,legacy):
    handle=reservation(tmp_path);root=Path(handle.attempt.workspace_path);path=path_of(handle)
    exports.clear_export_attempt(handle,evidence=exports.NoSaveEffectEvidence(handle.attempt.attempt_id,exports.NoSaveEffectReason.COMMAND_NEVER_EXPOSED,'Never published'))
    sibling=path.with_name('legacy.md');sibling.write_bytes(path.read_bytes());edit(sibling,id=str(uuid4()),**legacy)
    before={p:p.read_bytes() for p in (path,sibling)}
    with pytest.raises(ExportAttemptError,match='Incompatible legacy Workspace'):
        reserve_export_attempt(root,handle.attempt.paper_id)
    assert before=={p:p.read_bytes() for p in before}
    parsed=parse_paper_state(sibling,sibling.read_text(),root/'Authors')
    assert not parsed.updateable and any('Incompatible legacy' in p for p in parsed.problems)


def test_normal_completion_has_no_library_lookup_dependency(tmp_path,monkeypatch):
    import literature_monitor.zotero_local as zotero
    def forbidden(*args,**kwargs):pytest.fail('Normal export must not read My Library or resolve item identity')
    monkeypatch.setattr(zotero.ZoteroLocalClient,'resolve_identity',forbidden)
    handle,coordinator,_=prepared(tmp_path)
    assert process_capture_completion(coordinator).result.outcome is ExportCompletionOutcome.EXPORTED


def test_in_place_edit_during_export_temp_write_is_not_overwritten(tmp_path, monkeypatch):
    handle, coordinator, _ = prepared(tmp_path)
    path = path_of(handle)
    original = os.fsync
    edited = False
    def fsync(fd):
        nonlocal edited
        if not edited:
            edited = True
            path.write_bytes(path.read_bytes() + b'\nConcurrent human note\n')
        return original(fd)
    monkeypatch.setattr(safe_write.os, 'fsync', fsync)
    assert process_capture_completion(coordinator).result.outcome is ExportCompletionOutcome.FAILED
    assert path.read_bytes().endswith(b'Concurrent human note\n')
    assert parts(path)[0]['status'] == 'kept' and 'export_attempt' in parts(path)[0]


@pytest.mark.parametrize('replacement', ['workspace_directory', 'workspace_symlink', 'papers_directory', 'lock'])
def test_identity_substitution_during_export_preparation_never_writes_replacement(tmp_path, monkeypatch, replacement):
    handle, coordinator, _ = prepared(tmp_path)
    path = path_of(handle); root = path.parent.parent
    original = os.fsync
    injected = False
    before = path.read_bytes()
    saved = None
    def fsync(fd):
        nonlocal injected, saved
        if not injected:
            injected = True
            if replacement.startswith('workspace'):
                saved = root.with_name(root.name+'-preserved')
                root.rename(saved)
                if replacement == 'workspace_symlink': root.symlink_to(saved, target_is_directory=True)
                else: root.mkdir(); (root/'Papers').mkdir()
            elif replacement == 'papers_directory':
                saved = root/'old-Papers'; path.parent.rename(saved); path.parent.mkdir()
            else:
                lock = root/'.literature-monitor-operation.lock'
                saved = root/'old-lock'; lock.rename(saved); lock.touch()
        return original(fd)
    monkeypatch.setattr(safe_write.os, 'fsync', fsync)
    assert process_capture_completion(coordinator).result.outcome is ExportCompletionOutcome.FAILED
    if replacement == 'workspace_directory':
        assert list((root/'Papers').iterdir()) == []
        assert (saved/handle.attempt.paper_path).read_bytes() == before
    elif replacement == 'workspace_symlink': assert path.read_bytes() == before
    elif replacement == 'papers_directory':
        assert list(path.parent.iterdir()) == [] and (saved/path.name).read_bytes() == before
    else: assert path.read_bytes() == before


def test_replayed_concurrent_completion_has_one_write_authority(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    handle, coordinator, _ = prepared(tmp_path)
    completion = coordinator.consume_completion()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: resolve_export_completion(completion, coordinator=coordinator), range(16)))
    assert sum(r.outcome is ExportCompletionOutcome.EXPORTED for r in results) == 1
    assert parts(path_of(handle))[0]['status'] == 'exported'


def test_direct_commit_requires_issued_parent_confirmation(tmp_path):
    handle, coordinator, _ = prepared(tmp_path, dispatch=False, outcome=CaptureOutcome.UNCONFIRMED)
    completion = coordinator.consume_completion()
    path = path_of(handle); before = path.read_bytes()
    for invalid in (None, completion, replace(completion, parent_outcome=capture_module.ParentOutcome.CONFIRMED)):
        with pytest.raises(ExportAttemptError):
            commit_exported(handle, completion=invalid, coordinator=coordinator)
        assert path.read_bytes() == before


def test_duplicate_uuid_appearing_during_export_preparation_refuses_commit(tmp_path, monkeypatch):
    handle, coordinator, _ = prepared(tmp_path)
    path = path_of(handle)
    original = os.fsync
    injected = False
    def fsync(fd):
        nonlocal injected
        if not injected:
            injected = True
            path.with_name('duplicate.md').write_bytes(path.read_bytes())
        return original(fd)
    monkeypatch.setattr(safe_write.os, 'fsync', fsync)
    assert process_capture_completion(coordinator).result.outcome is ExportCompletionOutcome.FAILED
    assert parts(path)[0]['status'] == 'kept' and 'export_attempt' in parts(path)[0]
