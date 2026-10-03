"""Current-task transitions and deterministic race tests, with real A7 staging."""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace
import threading
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest

from literature_monitor.application.acquisition import (
    AcquisitionOutcome as Outcome, AcquisitionRecovery as Recovery,
    AcquisitionResult, AcquisitionStage as Stage, AcquisitionTask, PreparedAcquisition,
)
from literature_monitor.web import acquisition_coordinator as coordination
from literature_monitor.web.acquisition_coordinator import (
    AcquisitionCoordinator, AcquisitionCoordinatorStatus as Status,
    AcquisitionStartOutcome as Start, AcquisitionActionOutcome as Action,
)
from literature_monitor.web.browser_handoff import AuthenticatedBrowserEvent
from literature_monitor.zotero_write import ZoteroAuthorizationOutcome as Auth, ZoteroAuthorizationResult

ID = UUID(int=1)
OTHER = UUID(int=2)
TAB = 'tab-42'
DOI = '10.5555/test'
TARGET = 'https://publisher.example/file.pdf'
SECRET = 'SENTINEL_CREDENTIAL https://proxy.example/?signature=SECRET'


class ControlledService:
    def __init__(self, output_dir=None):
        self.output_dir = output_dir
        self.prepare_entered = threading.Event()
        self.prepare_release = threading.Event()
        self.commit_entered = threading.Event()
        self.commit_release = threading.Event(); self.commit_release.set()
        self.calls = []
        self.result = None
        self.error = None
        self.commit_error = None
        self.auth = Auth.AUTHORIZED
        self.auth_hook = None
        self.linked = False
        self.outcome = Outcome.SUCCEEDED
        self.prepared = None
        self.commits = []
        self.content_posts = 0

    def prepare(self, paper_id, *, stage_callback):
        self.calls.append((paper_id, threading.get_ident()))
        stage_callback(Stage.LOCATING_ZOTERO)
        self.prepare_entered.set()
        assert self.prepare_release.wait(5)
        if self.error:
            raise self.error
        if self.result is not None:
            return self.result
        self.prepared = PreparedAcquisition(AcquisitionTask(uuid4(), paper_id, DOI, 'PARENT01',
                                                          'synthetic-instance'), self.linked)
        return self.prepared

    def authorization_status(self, task):
        assert task is self.prepared.task
        if self.auth_hook:
            self.auth_hook()
        return ZoteroAuthorizationResult(self.auth, task.server_id)

    def commit(self, task, artifact, *, linkage_completed=False):
        assert task is self.prepared.task and artifact.task_id == task.task_id
        self.commits.append((task, artifact, linkage_completed))
        self.commit_entered.set()
        assert self.commit_release.wait(5)
        self.content_posts += 1
        if self.commit_error:
            raise self.commit_error
        return AcquisitionResult(task.paper_id, self.outcome, linkage_completed=linkage_completed)


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    real_thread = threading.Thread
    workers = []
    def thread(**kwargs):
        value = real_thread(**kwargs); workers.append(value); return value
    monkeypatch.setattr(coordination.threading, 'Thread', thread)
    source = tmp_path/'downloads'/'source.pdf'; source.parent.mkdir()
    source.write_bytes(b'%PDF-1.7\nsynthetic task bytes')
    root = tmp_path/'private-temp'; root.mkdir()
    options = dict(project_dir=tmp_path/'project', workspace_dir=tmp_path/'workspace',
                   config_path=tmp_path/'config'/'monitor.yaml', temp_root=root)
    service = ControlledService()
    launches = []
    coordinator = AcquisitionCoordinator(service, launcher=launches.append)
    s = SimpleNamespace(c=coordinator, service=service, workers=workers, launches=launches,
                        source=source, root=root, options=options, real_thread=real_thread)
    yield s
    service.prepare_release.set(); service.commit_release.set()
    for worker in workers:
        if worker.ident is not None:
            worker.join(5)
            assert not worker.is_alive()
    snapshot = coordinator.snapshot()
    if snapshot.status is Status.RUNNING and snapshot.cancel_available:
        coordinator.cancel(snapshot.paper_id, snapshot.attempt_id)


def join_workers(s):
    for worker in list(s.workers):
        worker.join(5)
        assert not worker.is_alive()


def start_browser(s):
    s.service.prepare_release.set()
    assert s.c.start(ID, staging_options=s.options).outcome is Start.STARTED
    join_workers(s)
    assert s.c.snapshot().stage is Stage.HANDOFF
    s.task = s.service.prepared.task
    s.launch = s.c._active.launch
    s.initial = urlsplit(s.launch.url).fragment
    return s


def ready(s):
    claimed = s.c.registry.claim(str(s.task.task_id), s.initial, TAB)
    assert claimed is not None
    s.capability = claimed.event_capability
    event = dispatch(s, 'tab_ready', {})
    assert event.command['type'] == 'START'
    assert event.command['plan'] == coordination.navigation_plan(s.task).message()
    return event


def dispatch(s, kind, payload):
    return s.c.registry.receive_event(str(s.task.task_id), s.capability, TAB, kind, payload,
                                      on_event=s.c.handle_event)


def download_payload(s, **changes):
    size = s.source.stat().st_size
    payload = dict(doi=DOI, download_id=7, route='direct', ownership='task_navigation',
        navigation_url=TARGET, path=str(s.source.resolve()), url=TARGET, final_url=TARGET,
        referrer=TARGET, mime='application/pdf', total_bytes=size, file_size=size,
        state='complete', category='',
        navigation_time=1000, start_time=2000, observed_doi=DOI)
    payload.update(changes)
    return payload


def stage_candidate(s, **changes):
    dispatch(s, 'download_candidate', download_payload(s, **changes))
    join_workers(s)


def test_one_slot_reserved_before_bounded_preflight_no_queue(scenario):
    s = scenario
    assert s.c.snapshot().status is Status.IDLE
    assert s.c.start(ID).outcome is Start.STARTED
    assert s.service.prepare_entered.wait(5)
    barrier = threading.Barrier(3); results = []
    def caller():
        barrier.wait(); results.append(s.c.start(OTHER).outcome)
    callers = [s.real_thread(target=caller) for _ in range(2)]
    for worker in callers: worker.start()
    barrier.wait()
    for worker in callers: worker.join(5)
    assert results == [Start.ALREADY_RUNNING]*2
    assert s.service.calls == [(ID, s.workers[0].ident)] and len(s.workers) == 1
    snapshot = s.c.snapshot()
    with pytest.raises(FrozenInstanceError): snapshot.stage = Stage.ATTACHING
    s.c.cancel(ID, snapshot.attempt_id); s.service.prepare_release.set(); join_workers(s)
    assert not s.launches and s.c.snapshot().result.outcome is Outcome.CANCELLED


def test_preflight_exits_before_browser_wait_and_secret_stays_private(scenario, caplog):
    s = start_browser(scenario)
    snapshot = s.c.snapshot()
    assert snapshot.status is Status.RUNNING and snapshot.cancel_available
    assert s.service.calls[0][1] != threading.get_ident()
    assert all(not worker.is_alive() for worker in s.workers)
    assert s.initial not in repr(snapshot)+repr(s.launch)+caplog.text
    assert TARGET not in repr(snapshot) and str(s.source) not in repr(snapshot)
    assert s.c.snapshot() == snapshot and len(s.workers) == 1
    ready(s)
    assert s.c.snapshot().stage is Stage.BROWSER_ACTION and len(s.workers) == 1
    dispatch(s, 'human_action_needed', {'route':'direct'})
    assert s.c.snapshot().stage is Stage.WAITING_FOR_INSTITUTION_AUTH
    assert all(not worker.is_alive() for worker in s.workers)


@pytest.mark.parametrize('outcome', [Outcome.PDF_ALREADY_ATTACHED, Outcome.INELIGIBLE, Outcome.CONFLICT, Outcome.ZOTERO_FAILURE])
def test_terminal_preflight_never_issues_handoff_or_launch(scenario, outcome):
    s = scenario; s.service.result = AcquisitionResult(ID, outcome)
    s.service.prepare_release.set(); s.c.start(ID); join_workers(s)
    assert s.c.snapshot().result.outcome is outcome and s.c._active is None
    assert s.c.registry._active is None and s.launches == []
    assert s.c.start(OTHER).outcome is Start.STARTED
    join_workers(s)


def test_launch_failure_reopens_same_private_authority(scenario):
    s = scenario; calls = []
    def fail(launch): calls.append(launch); raise RuntimeError(SECRET)
    s.c._launcher = fail
    start_browser(s)
    snapshot = s.c.snapshot()
    assert snapshot.open_retry_available and s.initial not in repr(snapshot)
    s.c._launcher = calls.append
    assert s.c.reopen(ID, snapshot.attempt_id) is Action.ACCEPTED
    join_workers(s)
    assert calls == [s.launch, s.launch]
    assert s.c.registry.status(str(s.task.task_id)).claimed is False
    ready(s)
    assert s.c.reopen(ID, snapshot.attempt_id) is Action.UNAVAILABLE


def test_wrong_or_late_task_tab_cannot_advance_or_get_commands(scenario):
    s = start_browser(scenario); ready(s); snapshot = s.c.snapshot()
    for task, tab, capability in [(uuid4(), TAB, s.capability), (s.task.task_id,'tab-99',s.capability),
                                  (s.task.task_id,TAB,'x'*43)]:
        assert s.c.registry.receive_event(str(task), capability, tab, 'tab_ready', {}, on_event=s.c.handle_event) is None
    assert s.c.handle_event(AuthenticatedBrowserEvent(uuid4(),TAB,'human_action_needed',{'route':'direct'})) is None
    assert s.c.handle_event(AuthenticatedBrowserEvent(s.task.task_id,'tab-99','human_action_needed',{'route':'direct'})) is None
    assert s.c.snapshot() == snapshot
    s.c.cancel(ID, snapshot.attempt_id)
    assert dispatch(s,'tab_ready',{}) is None and dispatch(s,'download_candidate',download_payload(s)) is None
    assert s.service.content_posts == 0


def test_explicit_fallback_and_current_resolver_choice_without_arbitrary_url(scenario):
    s = start_browser(scenario); ready(s)
    command = dispatch(s,'publisher_fallback_request',{}).command
    assert command == {'task_id':str(s.task.task_id),'type':'PUBLISHER_EXHAUSTED'}
    choices = [dict(id=i,category=category,label=label) for i,(category,label) in enumerate([('FullText','Provider A'),('SmartLinks','Provider B')])]
    dispatch(s,'resolver_choices',{'choices':choices})
    snapshot = s.c.snapshot()
    assert snapshot.stage is Stage.RESOLVER_CHOICE and [c.label for c in snapshot.choices] == ['Provider A','Provider B']
    for payload in ({'choice_id':7},{'choice_id':True},{'choice_id':0,'url':TARGET}):
        assert dispatch(s,'resolver_choice_request',payload).command is None
        assert s.c.snapshot() == snapshot
    command = dispatch(s,'resolver_choice_request',{'choice_id':1}).command
    assert command == {'task_id':str(s.task.task_id),'type':'CHOOSE','choice_id':1}
    assert s.c.snapshot().choices == () and s.c.snapshot().stage is Stage.RESOLVING
    assert dispatch(s,'user_download_request',{}).command == {'task_id':str(s.task.task_id),'type':'DOWNLOAD_CURRENT'}


@pytest.mark.parametrize('changes', [{'doi':'10.5555/wrong'}, {'observed_doi':'10.5555/wrong'}, {'path':'relative.pdf'}, {'file_size':1}])
def test_invalid_download_no_writer_and_source_unchanged(scenario, changes):
    s = start_browser(scenario); ready(s); before = s.source.read_bytes()
    stage_candidate(s, **changes)
    assert s.c.snapshot().result.outcome is Outcome.NO_VALID_PDF
    assert s.service.commits == [] and s.service.content_posts == 0
    assert s.source.read_bytes() == before and list(s.root.iterdir()) == []
    assert s.c.registry.status(str(s.task.task_id)) is None


@pytest.mark.parametrize('change', ['object_type', 'task_id', 'download_id', 'byte_count'])
def test_staged_artifact_binding_rejects_before_authorization(scenario, monkeypatch, change):
    s = start_browser(scenario); ready(s)
    original = coordination.stage_download
    def stage(*args, **kwargs):
        artifact = original(*args, **kwargs)
        if change == 'object_type':
            artifact.cleanup()
            return object()
        return replace(artifact, **{change: {
            'task_id': uuid4(), 'download_id': 8, 'byte_count': artifact.byte_count + 1,
        }[change]})
    def unexpected_authorization():
        pytest.fail('Invalid staged binding must stop before authorization')
    monkeypatch.setattr(coordination, 'stage_download', stage)
    s.service.auth_hook = unexpected_authorization
    stage_candidate(s)
    assert s.c.snapshot().result.outcome is Outcome.NO_VALID_PDF
    assert s.service.commits == [] and s.service.content_posts == 0
    assert list(s.root.iterdir()) == [] and s.source.exists()


def test_direct_download_without_observed_doi_commits_staged_artifact(scenario):
    s = start_browser(scenario); ready(s)
    stage_candidate(s, observed_doi=None)
    assert s.c.snapshot().result.outcome is Outcome.SUCCEEDED
    assert s.service.content_posts == 1 and s.service.commits[0][1].task_id == s.task.task_id
    assert list(s.root.iterdir()) == [] and s.source.exists()


def test_event_handler_returns_while_staging_is_still_blocked(scenario, monkeypatch):
    s = start_browser(scenario); ready(s)
    original = coordination.stage_download
    entered, release = threading.Event(), threading.Event()
    def stage(*args, **kwargs):
        entered.set(); assert release.wait(5); return original(*args, **kwargs)
    monkeypatch.setattr(coordination, 'stage_download', stage)
    try:
        result = dispatch(s,'download_candidate',download_payload(s))
        assert result is not None and entered.wait(5)  # Callback returned before staging finished.
        assert s.c.snapshot().stage is Stage.VALIDATING_PDF and not s.service.commits
        snapshot = s.c.snapshot()
        assert s.c.cancel(ID,snapshot.attempt_id) is Action.ACCEPTED
    finally: release.set(); join_workers(s)
    assert not s.service.commits and list(s.root.iterdir()) == [] and s.source.exists()


@pytest.mark.parametrize('auth', [Auth.REQUIRED, Auth.SECURE_STORE_FAILURE, Auth.API_FAILURE])
def test_missing_authorization_retains_artifact_task_without_worker(scenario, auth):
    s = start_browser(scenario); ready(s); s.service.auth = auth
    stage_candidate(s)
    snapshot = s.c.snapshot(); artifact = s.c._active.artifact
    assert snapshot.stage is Stage.WAITING_FOR_ZOTERO_AUTH and snapshot.resume_available and snapshot.cancel_available
    assert artifact.validate() and not s.service.commits and all(not w.is_alive() for w in s.workers)
    assert s.c.resume(OTHER,snapshot.attempt_id) is Action.UNAVAILABLE
    assert s.c.resume(ID,uuid4()) is Action.UNAVAILABLE
    s.service.auth = Auth.AUTHORIZED
    assert s.c.resume(ID,snapshot.attempt_id) is Action.ACCEPTED
    join_workers(s)
    assert s.service.commits[0][0] is s.task and s.service.commits[0][1] is artifact
    assert s.service.content_posts == 1 and not artifact.path.exists() and s.source.exists()
    assert s.c.snapshot().result.outcome is Outcome.SUCCEEDED


@pytest.mark.parametrize('change', ['modify', 'delete'])
def test_resume_leaves_stale_artifact_rejection_to_real_service_commit(scenario, monkeypatch, change):
    from literature_monitor.application import acquisition as application
    from literature_monitor.pdf_staging import StagedPdf

    s = start_browser(scenario); ready(s); s.service.auth = Auth.REQUIRED
    stage_candidate(s); snapshot = s.c.snapshot()
    artifact = s.c._active.artifact
    if change == 'modify':artifact.path.write_bytes(b'changed')
    else:artifact.path.unlink()
    events = []
    s.service.auth_hook = lambda: events.append(('authorization',))
    real_service = application.AcquisitionService(s.options['workspace_dir'])
    validate = StagedPdf.validate

    def record_validation(value):
        assert value is artifact
        events.append(('validate', s.c.snapshot().stage))
        return validate(value)

    def commit(task, value, *, linkage_completed=False):
        assert task is s.task and value is artifact
        assert s.c._active.gate_entered and s.c.snapshot().stage is Stage.ATTACHING
        events.append(('commit',))
        s.service.commits.append((task, value, linkage_completed))
        return real_service.commit(task, value, linkage_completed=linkage_completed)

    def unexpected_writer(*args, **kwargs):
        pytest.fail('Stale commit must stop before writer construction or content POST')

    monkeypatch.setattr(StagedPdf, 'validate', record_validation)
    monkeypatch.setattr(s.service, 'commit', commit)
    monkeypatch.setattr(application, 'ZoteroWriteClient', unexpected_writer)
    s.service.auth = Auth.AUTHORIZED
    assert s.c.resume(ID,snapshot.attempt_id) is Action.ACCEPTED
    join_workers(s)
    assert events == [('authorization',), ('commit',), ('validate', Stage.ATTACHING)]
    result = s.c.snapshot().result
    assert result.outcome is Outcome.NO_VALID_PDF and not result.mutation_uncertain
    assert len(s.service.commits) == 1 and s.service.content_posts == 0
    assert s.c._active is None and s.c.registry.status(str(s.task.task_id)) is None
    assert not artifact.path.exists() and s.source.exists()


def test_server_change_during_authorization_read_terminates_without_commit(scenario):
    s = start_browser(scenario); ready(s); s.service.auth = Auth.SERVER_ID_MISMATCH
    stage_candidate(s)
    assert s.c.snapshot().result.outcome is Outcome.CONFLICT and not s.service.commits
    assert list(s.root.iterdir()) == []


def test_cancel_wins_before_gate_even_after_authorization_check(scenario, monkeypatch):
    s = start_browser(scenario); ready(s)
    authorization_checked = threading.Event()
    s.service.auth_hook = authorization_checked.set
    entered, release = threading.Event(), threading.Event()
    original = s.c._enter_gate
    gate_results = []
    def gate(attempt):
        entered.set(); assert release.wait(5)
        accepted = original(attempt); gate_results.append(accepted); return accepted
    monkeypatch.setattr(s.c,'_enter_gate',gate)
    try:
        dispatch(s,'download_candidate',download_payload(s)); assert entered.wait(5)
        assert authorization_checked.is_set()
        snapshot = s.c.snapshot(); artifact = s.c._active.artifact
        assert s.c.cancel(ID,snapshot.attempt_id) is Action.ACCEPTED
        assert not artifact.path.exists() and s.c.registry.status(str(s.task.task_id)) is None
    finally: release.set(); join_workers(s)
    assert gate_results == [False] and s.service.commits == []
    assert s.service.content_posts == 0 and s.c.snapshot().result.outcome is Outcome.CANCELLED
    assert s.c._active is None and dispatch(s,'download_candidate',download_payload(s)) is None


def test_gate_wins_cancel_is_too_late_before_post_begins(scenario):
    s = start_browser(scenario); ready(s); s.service.commit_release.clear()
    try:
        dispatch(s,'download_candidate',download_payload(s)); assert s.service.commit_entered.wait(5)
        snapshot = s.c.snapshot()
        assert snapshot.stage is Stage.ATTACHING and not snapshot.cancel_available
        assert s.c._active.gate_entered and len(s.service.commits) == 1
        assert s.service.content_posts == 0
        assert s.c.cancel(ID,snapshot.attempt_id) is Action.TOO_LATE
        assert s.c.snapshot() == snapshot
    finally: s.service.commit_release.set(); join_workers(s)
    assert s.service.content_posts == 1 and s.c.snapshot().result.outcome is Outcome.SUCCEEDED


def test_cancel_and_gate_barrier_exactly_one_wins(scenario, monkeypatch):
    s = start_browser(scenario); ready(s); s.service.commit_release.clear()
    barrier = threading.Barrier(2); original = s.c._enter_gate; outcomes = []; gate_results = []
    def gate(attempt):
        barrier.wait(5)
        accepted = original(attempt); gate_results.append(accepted); return accepted
    monkeypatch.setattr(s.c,'_enter_gate',gate)
    identity = s.c.snapshot().attempt_id
    def cancel(): barrier.wait(5); outcomes.append(s.c.cancel(ID,identity))
    caller = s.real_thread(target=cancel); caller.start()
    try:
        dispatch(s,'download_candidate',download_payload(s)); caller.join(5); assert not caller.is_alive()
        assert outcomes[0] in (Action.ACCEPTED,Action.TOO_LATE)
    finally: s.service.commit_release.set(); join_workers(s)
    assert gate_results == [outcomes[0] is Action.TOO_LATE]
    assert len(s.service.commits) == (1 if outcomes[0] is Action.TOO_LATE else 0)
    assert (s.service.content_posts,s.c.snapshot().result.outcome) == ((0,Outcome.CANCELLED) if outcomes[0] is Action.ACCEPTED else (1,Outcome.SUCCEEDED))
    assert s.c._active is None and s.c.registry.status(str(s.task.task_id)) is None
    assert list(s.root.iterdir()) == [] and s.source.exists()


@pytest.mark.parametrize('outcome',[Outcome.SUCCEEDED,Outcome.PDF_ALREADY_ATTACHED,Outcome.CONFLICT,Outcome.UPLOAD_FAILURE])
def test_terminal_commit_always_invalidates_cleans_and_releases_slot(scenario,outcome):
    s = start_browser(scenario); ready(s); s.service.outcome=outcome; s.service.linked=True
    stage_candidate(s)
    assert s.c.snapshot().result.outcome is outcome and s.c._active is None
    assert s.c.registry.status(str(s.task.task_id)) is None and dispatch(s,'tab_ready',{}) is None
    assert list(s.root.iterdir()) == [] and s.source.exists()
    assert s.c.start(OTHER).outcome is Start.STARTED
    join_workers(s)


@pytest.mark.parametrize('error',[RuntimeError(SECRET),KeyboardInterrupt(SECRET),SystemExit(SECRET)])
def test_unexpected_preflight_failure_sanitized_reusable(scenario,error,caplog):
    s=scenario;s.service.error=error;s.service.prepare_release.set();s.c.start(ID);join_workers(s)
    snapshot=s.c.snapshot()
    assert snapshot.result.outcome is Outcome.INTERNAL_FAILURE and snapshot.unexpected_error is not None
    assert SECRET not in repr(snapshot)+caplog.text and s.c.registry._active is None
    s.service.error=None
    assert s.c.start(OTHER).outcome is Start.STARTED
    join_workers(s)


def test_unexpected_commit_uncertainty_is_preserved_and_artifact_cleaned(scenario,caplog):
    s=start_browser(scenario);ready(s);s.service.commit_error=RuntimeError(SECRET)
    stage_candidate(s)
    assert s.c.snapshot().result.mutation_uncertain and s.service.content_posts == 1
    assert SECRET not in repr(s.c.snapshot())+caplog.text
    assert list(s.root.iterdir()) == [] and s.c.registry._active is None


def test_worker_start_failure_finishes_and_does_not_leave_authority(scenario,monkeypatch):
    s=scenario
    def fail(**kwargs): raise RuntimeError(SECRET)
    monkeypatch.setattr(coordination.threading,'Thread',fail)
    assert s.c.start(ID).outcome is Start.START_FAILED
    assert s.c.snapshot().status is Status.FINISHED and s.c._active is None
    assert s.c.registry._active is None and not s.launches


def test_restart_is_empty_without_reconstructing_any_artifact(scenario):
    s=start_browser(scenario);ready(s);s.service.auth=Auth.REQUIRED;stage_candidate(s)
    fresh=AcquisitionCoordinator(s.service,launcher=s.launches.append)
    assert fresh.snapshot().status is Status.IDLE and fresh.registry._active is None
    assert fresh.snapshot().task_id is None and len(s.launches)==1
    assert fresh.registry.receive_event(str(s.task.task_id),s.capability,TAB,'tab_ready',{},on_event=fresh.handle_event) is None


def test_normal_chrome_launcher_has_fragment_only_and_no_profile_flags(scenario,monkeypatch):
    from literature_monitor.web import chrome_launcher
    s=start_browser(scenario);calls=[]
    monkeypatch.setattr(chrome_launcher.sys,'platform','darwin')
    monkeypatch.setattr(chrome_launcher.subprocess,'run',lambda command,**kwargs:calls.append((command,kwargs)))
    chrome_launcher.launch_normal_chrome(s.launch)
    command,options=calls[0]
    assert command==['/usr/bin/open','-a','Google Chrome',s.launch.url]
    assert urlsplit(command[-1]).fragment==s.initial and not urlsplit(command[-1]).query
    assert set(options)=={'check','timeout','stdin','stdout','stderr'} and options['timeout']==10
    assert options['stdin']==options['stdout']==options['stderr']==chrome_launcher.subprocess.DEVNULL
    assert all(flag not in ' '.join(command) for flag in ('--user-data-dir','--profile','--headless','cookie'))


def test_chrome_launch_error_never_contains_launch_url(scenario,monkeypatch):
    from literature_monitor.web import chrome_launcher
    s=start_browser(scenario)
    def fail(command,**kwargs):raise chrome_launcher.subprocess.CalledProcessError(1,command)
    monkeypatch.setattr(chrome_launcher.subprocess,'run',fail)
    with pytest.raises(RuntimeError) as error:chrome_launcher.launch_normal_chrome(s.launch)
    assert str(error.value)=='Normal Chrome could not be opened.' and s.initial not in str(error.value)


def test_cancel_preserves_completed_legacy_linkage(scenario):
    s=scenario;s.service.linked=True;start_browser(s)
    snapshot=s.c.snapshot()
    assert s.c.cancel(ID,snapshot.attempt_id) is Action.ACCEPTED
    assert s.c.snapshot().result.linkage_completed and s.service.content_posts==0


@pytest.mark.parametrize('reason',['navigation_failed','download_unavailable','ambiguous_download_ownership'])
def test_browser_failure_is_terminal_without_workflow_or_zotero_write(scenario,reason):
    s=start_browser(scenario);ready(s)
    dispatch(s,'browser_path_failure',{'reason':reason})
    assert s.c.snapshot().result.outcome is Outcome.NO_VALID_PDF and s.service.content_posts==0
    assert s.c.registry._active is None and s.source.exists()


def test_unknown_or_malformed_browser_observations_do_not_advance(scenario):
    s=start_browser(scenario);ready(s);snapshot=s.c.snapshot()
    for kind,payload in [('publisher_state',{'state':[]}),('navigation_state',{'identity':{'scheme':[],'host':'example.org'},'route':'direct'}),
                         ('browser_path_failure',{'reason':SECRET}),('unknown_command',{'url':TARGET})]:
        assert dispatch(s,kind,payload).command is None
        assert s.c.snapshot()==snapshot


@pytest.mark.parametrize('command',['START','PUBLISHER_EXHAUSTED','CHOOSE','DOWNLOAD_CURRENT'])
def test_approved_command_failure_terminalizes_the_pretransition(scenario,command,caplog):
    s=start_browser(scenario);ready(s)
    if command in {'PUBLISHER_EXHAUSTED','CHOOSE'}:
        assert dispatch(s,'publisher_fallback_request',{}).command['type']=='PUBLISHER_EXHAUSTED'
    if command=='CHOOSE':
        dispatch(s,'resolver_choices',{'choices':[dict(id=i,category='FullText',label=f'Provider {i}') for i in range(2)]})
        assert dispatch(s,'resolver_choice_request',{'choice_id':1}).command['type']=='CHOOSE'
    if command=='DOWNLOAD_CURRENT':
        assert dispatch(s,'user_download_request',{}).command['type']=='DOWNLOAD_CURRENT'
    before=s.c.snapshot()
    reason='download_unavailable' if command=='DOWNLOAD_CURRENT' else 'navigation_failed'
    for task,tab,token in [(uuid4(),TAB,s.capability),(s.task.task_id,'tab-99',s.capability),(s.task.task_id,TAB,'x'*43)]:
        assert s.c.registry.receive_event(str(task),token,tab,'browser_path_failure',{'reason':reason},on_event=s.c.handle_event) is None
    for payload in ({'reason':SECRET},{'reason':reason,'exception':SECRET},{'reason':reason,'url':TARGET}):
        assert dispatch(s,'browser_path_failure',payload).command is None
    assert s.c.snapshot()==before
    assert dispatch(s,'browser_path_failure',{'reason':reason}) is not None
    snapshot=s.c.snapshot()
    assert snapshot.status is Status.FINISHED and snapshot.stage is Stage.FAILED
    assert snapshot.result.outcome is (Outcome.RESOLVER_FAILURE if command in {'PUBLISHER_EXHAUSTED','CHOOSE'} else Outcome.NO_VALID_PDF)
    assert SECRET not in repr(snapshot)+snapshot.result.message+caplog.text
    assert s.c._active is None and s.c.registry.status(str(s.task.task_id)) is None
    assert s.service.commits==[] and s.service.content_posts==0 and s.source.exists()
    assert dispatch(s,'tab_ready',{}) is None and dispatch(s,'browser_path_failure',{'reason':reason}) is None
    assert s.c.start(OTHER).outcome is Start.STARTED
    join_workers(s)
