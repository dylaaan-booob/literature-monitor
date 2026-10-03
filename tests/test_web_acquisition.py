"""A6 adapter tests use synthetic workspaces and controlled workers, no network."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
import threading
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from literature_monitor.application import acquisition as acquisition_application
from literature_monitor.application.acquisition import (
    AcquisitionService, AcquisitionResult, AcquisitionOutcome as Outcome,
    AcquisitionRecovery as Recovery, AcquisitionStage as Stage,
)
from literature_monitor.application.settings import load_settings
from literature_monitor.application.workspace import WorkspaceSnapshot
from literature_monitor.markdown_state import parse_paper_state, serialize_document
from literature_monitor.materialize import render_paper_markdown
from literature_monitor.models import (
    Author,
    CanonicalMetadata,
    CanonicalPaper,
    ExternalIds,
    Workflow,
    WorkflowStatus,
)
from literature_monitor.web import app as web
from literature_monitor.web.acquisition_coordinator import (
    AcquisitionSnapshot, AcquisitionCoordinatorStatus as Status, UnexpectedAcquisitionError,
)
from literature_monitor.zotero_write import ZoteroUploadStage

from test_web_app import make_paper, write_valid_config
from test_web_run_settings import valid_settings_form

ID = UUID(int=1)
OTHER = UUID(int=2)
SECRETS = ('SENTINEL_API_KEY', 'SENTINEL_COOKIE', 'https://publisher.example/?signature=SENTINEL_SIGNED',
           'https://proxy.example/SENTINEL_SESSION', 'SENTINEL_UPLOAD_KEY', '/private/SENTINEL_FILE.pdf')


def forbidden(*args, **kwargs):
    pytest.fail('Web presentation must not call external acquisition systems')


def write_paper(output, paper_id=ID, status=WorkflowStatus.IN_ZOTERO):
    paper = CanonicalPaper(id=paper_id, metadata=CanonicalMetadata(title='Synthetic Paper',journal='Biometrics'),
        authors=(Author(name='Test Author'),), external_ids=ExternalIds(doi='10.5555/test'), journal_issns=('0006-341X',),
        workflow=Workflow(status=status,discovered_at=datetime(2026,9,30,tzinfo=timezone.utc)))
    path = output/'Papers'/f'{paper_id}.md'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(render_paper_markdown(paper,('test-author',))+'\n## Notes\nHuman notes.\n')
    return path


class ControlledService:
    def __init__(self, output_dir):
        self.output_dir = output_dir
        self.entered = threading.Event()
        self.release = threading.Event()
        self.worker = None
        self.calls = []
        self.outcome = Outcome.SUCCEEDED
        self.on_complete = None
        self.error = None

    def prepare(self, paper_id, *, stage_callback):
        self.worker = threading.current_thread()
        self.calls.append(paper_id)
        stage_callback(Stage.RESOLVING)
        self.entered.set()
        assert self.release.wait(5)
        if self.error:
            raise self.error
        if self.on_complete:
            self.on_complete()
        return AcquisitionResult(paper_id,self.outcome)

    def finish(self):
        assert self.entered.wait(5)
        self.release.set()
        self.worker.join(5)
        assert not self.worker.is_alive()
        self.entered.clear()


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    config, output = write_valid_config(tmp_path)
    path = write_paper(output)
    services = []
    def factory(directory, config_path, *, authorization_retry_boundary, authorization_runtime):
        assert config_path == config.resolve()
        service = ControlledService(directory)
        service.retry_boundary = authorization_retry_boundary
        services.append(service)
        return service
    monkeypatch.setattr(web,'_build_acquisition_service',factory)
    monkeypatch.setattr(acquisition_application,'ZoteroLocalClient',forbidden)
    app = web.create_app(config)
    with TestClient(app,base_url='http://localhost') as client:
        s = SimpleNamespace(app=app,client=client,config=config,output=output,path=path,services=services)
        s.post = lambda route_paper_id=ID, **data: client.post(f'/papers/{route_paper_id}/acquire-pdf',
            data=dict(csrf_token=app.state.csrf_token,view='in-zotero',**data),headers={'HX-Request':'true'})
        yield s
        for service in services:
            service.release.set()
            if service.worker is not None:
                service.worker.join(5)
                assert not service.worker.is_alive()


@pytest.mark.parametrize('status,doi,eligible',[
    (WorkflowStatus.IN_ZOTERO,' https://doi.org/10.5555/TEST ',True),
    (WorkflowStatus.IN_ZOTERO,None,False),
    (WorkflowStatus.IN_ZOTERO,'https://doi.org/',False),
    (WorkflowStatus.IN_ZOTERO,123,False),
    (WorkflowStatus.CANDIDATE,'10.5555/test',False),
    (WorkflowStatus.KEPT,'10.5555/test',False),
    (WorkflowStatus.REJECTED,'10.5555/test',False),
])
@pytest.mark.filterwarnings('ignore:Pydantic serializer warnings:UserWarning')
def test_detail_eligibility_and_kept_only_copy(scenario,monkeypatch,status,doi,eligible):
    s = scenario
    paper = replace(make_paper(paper_id=ID,status=status),external_ids=ExternalIds.model_construct(doi=doi))
    monkeypatch.setattr(web,'load_workspace',lambda *args,**kwargs:WorkspaceSnapshot(papers=(paper,),issues=()))
    view = {WorkflowStatus.IN_ZOTERO:'in-zotero',WorkflowStatus.CANDIDATE:'inbox'}.get(status,status.value)
    for url in ('/',f'/fragments/papers/{ID}'):
        response = s.client.get(url,params={'paper':str(ID),'view':view})
        assert response.status_code == 200
        assert ('Add PDF to Zotero' in response.text) is eligible
        assert ('Copy DOI' in response.text) is (status is WorkflowStatus.KEPT)
    assert s.services == []


class FormFields(HTMLParser):
    def __init__(self):
        super().__init__();self.inside=False;self.forms=[];self.fields=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='form':
            self.inside=attrs.get('action','').endswith('/acquire-pdf')
            if self.inside:self.forms.append(attrs)
        if tag=='input' and self.inside:self.fields.append(attrs.get('name'))
    def handle_endtag(self,tag):
        if tag=='form':self.inside=False


def test_action_submits_uuid_route_and_only_csrf_view(scenario):
    s = scenario
    response = s.client.get(f'/fragments/papers/{ID}?view=in-zotero')
    parser = FormFields();parser.feed(response.text)
    assert parser.fields == ['csrf_token','view']
    assert len(parser.forms)==1
    form = parser.forms[0]
    assert form['action']==form['hx-post']==f'/papers/{ID}/acquire-pdf'
    assert form['method']=='post' and form['hx-target']=='#acquisition-panel'
    assert s.services == []


@pytest.mark.parametrize('token',[None,'wrong','é💥'])
def test_csrf_rejected_before_configuration_or_start(scenario,monkeypatch,token):
    s = scenario
    monkeypatch.setattr(web,'load_config',forbidden)
    monkeypatch.setattr(s.app.state.acquisition_coordinator,'start',forbidden)
    response = s.client.post(f'/papers/{ID}/acquire-pdf',data={} if token is None else {'csrf_token':token})
    assert response.status_code==403 and s.services==[]


def test_host_get_and_invalid_uuid_cannot_start(scenario):
    s = scenario
    assert s.client.post(f'/papers/{ID}/acquire-pdf',headers={'Host':'evil.example'},
        data={'csrf_token':s.app.state.csrf_token}).status_code==400
    assert s.client.get(f'/papers/{ID}/acquire-pdf').status_code==405
    assert s.client.post('/papers/not-a-uuid/acquire-pdf',data={'csrf_token':s.app.state.csrf_token}).status_code==422
    assert s.services==[] and s.app.state.acquisition_coordinator.snapshot().status is Status.IDLE


def test_start_binds_current_workspace_and_runs_off_request_thread(scenario,monkeypatch):
    s = scenario;caller_threads=[]
    original=web._resolve_output_dir
    def resolve(path):
        caller_threads.append(threading.get_ident());return original(path)
    monkeypatch.setattr(web,'_resolve_output_dir',resolve)
    before=s.path.read_bytes()
    response=s.post(doi=SECRETS[2],path=SECRETS[5],zotero_key=SECRETS[0],paper_id=str(OTHER))
    assert response.status_code==200 and 'id="acquisition-panel"' in response.text
    service=s.services[0];assert service.entered.wait(5)
    assert service.output_dir==s.output and service.calls==[ID]
    assert service.worker.ident!=caller_threads[0]
    assert s.path.read_bytes()==before
    assert all(secret not in response.text for secret in SECRETS)


def test_simultaneous_web_starts_share_one_slot_no_queue(scenario):
    s = scenario;barrier=threading.Barrier(2)
    coordinator=s.app.state.acquisition_coordinator
    def start(paper):
        barrier.wait();return s.post(paper)
    with ThreadPoolExecutor(max_workers=2) as callers:
        futures=[callers.submit(start,paper) for paper in (ID,OTHER)]
        responses=[future.result(5) for future in futures]
    assert all(response.status_code==200 for response in responses)
    assert sum('Another PDF acquisition is already in progress.' in r.text for r in responses)==1
    assert len(s.services)==1 and s.services[0].entered.wait(5)
    active=coordinator.snapshot().paper_id
    assert active in (ID,OTHER) and s.services[0].calls==[active]
    assert s.app.state.acquisition_coordinator is coordinator
    s.services[0].finish()
    assert s.services[0].calls==[active]


def test_navigation_and_polling_are_observational(scenario,monkeypatch):
    s = scenario;write_paper(s.output,OTHER)
    s.post();service=s.services[0];assert service.entered.wait(5)
    other=s.client.get(f'/fragments/papers/{OTHER}?view=in-zotero')
    back=s.client.get(f'/fragments/papers/{ID}?view=in-zotero')
    assert 'Another PDF acquisition is already in progress.' in other.text
    assert 'disabled>Add PDF to Zotero' in other.text
    assert 'Finding institutional full text' in back.text
    snapshot=s.app.state.acquisition_coordinator.snapshot()
    before=s.path.read_bytes()
    for name in ('load_config','load_workspace','_build_acquisition_service'):
        monkeypatch.setattr(web,name,forbidden)
    monkeypatch.setattr(s.app.state.acquisition_coordinator,'start',forbidden)
    for paper in (ID,OTHER):
        for _ in range(2):
            response=s.client.get(f'/fragments/acquisition/{paper}?view=in-zotero')
            assert response.status_code==200 and 'every 750ms' in response.text
            assert 'HX-Trigger-After-Swap' not in response.headers
    assert s.app.state.acquisition_coordinator.snapshot()==snapshot
    assert len(s.services)==1 and service.calls==[ID] and s.path.read_bytes()==before


def test_terminal_refresh_rereads_linkage_and_preserves_view_status_notes(scenario):
    s = scenario
    s.post();service=s.services[0];assert service.entered.wait(5)
    def link():
        state=parse_paper_state(s.path,s.path.read_text(),s.output/'Authors')
        s.path.write_text(serialize_document(dict(state.frontmatter,zotero_key='NEWKEY01'),state.body))
    service.on_complete=link;service.finish()
    polled=s.client.get(f'/fragments/acquisition/{ID}?view=in-zotero')
    assert polled.headers['HX-Trigger-After-Swap']=='acquisitionCompleted'
    assert 'every 750ms' not in polled.text
    assert AcquisitionResult(ID,Outcome.SUCCEEDED).message in polled.text
    detail=s.client.get(f'/fragments/papers/{ID}?view=in-zotero')
    assert 'NEWKEY01' in detail.text and '<dd>in_zotero</dd>' in detail.text
    assert f'hx-get="/fragments/papers/{ID}?view=in-zotero"' in detail.text
    assert 'hx-trigger="acquisitionCompleted from:body"' in detail.text
    assert 'disabled>Add PDF to Zotero' not in detail.text
    assert 'Human notes.' in s.path.read_text()
    other=s.client.get(f'/fragments/papers/{OTHER}?view=in-zotero')
    assert 'PDF attached to the verified' not in other.text


@pytest.mark.parametrize('outcome',list(Outcome))
def test_all_terminal_results_are_safe_and_distinct(scenario,outcome):
    s = scenario
    recovery=Recovery.INSTITUTION_LOGIN if outcome is Outcome.AUTH_REQUIRED else Recovery.NONE
    result=AcquisitionResult(ID,outcome,recovery)
    stage=Stage.WAITING_FOR_INSTITUTION_AUTH if outcome is Outcome.AUTH_REQUIRED else Stage.SUCCEEDED if outcome in (Outcome.SUCCEEDED,Outcome.PDF_ALREADY_ATTACHED) else Stage.FAILED
    snapshot=AcquisitionSnapshot(Status.FINISHED,ID,stage,result,None)
    s.app.state.acquisition_coordinator=SimpleNamespace(snapshot=lambda:snapshot)
    response=s.client.get(f'/fragments/acquisition/{ID}')
    assert escape(result.message) in response.text
    assert 'every 750ms' not in response.text and response.headers['HX-Trigger-After-Swap']=='acquisitionCompleted'
    if outcome is Outcome.AUTH_REQUIRED:
        assert 'same normal Chrome task tab' in response.text
    other=s.client.get(f'/fragments/acquisition/{OTHER}')
    assert result.message not in other.text
    assert s.services==[]


@pytest.mark.parametrize('stage',list(Stage))
def test_running_stage_presentation_and_manual_auth(scenario,stage):
    s = scenario
    snapshot=AcquisitionSnapshot(Status.RUNNING,ID,stage,None,None)
    s.app.state.acquisition_coordinator=SimpleNamespace(snapshot=lambda:snapshot)
    response=s.client.get(f'/fragments/acquisition/{ID}')
    assert 'PDF acquisition in progress' in response.text and 'every 750ms' in response.text
    assert stage.value not in response.text
    if stage is Stage.WAITING_FOR_INSTITUTION_AUTH:
        assert 'Complete institutional login or verification in the same normal Chrome task tab' in response.text


@pytest.mark.parametrize('stage',[ZoteroUploadStage.CHILD_CREATED,ZoteroUploadStage.BYTES_UPLOADED])
def test_partial_uncertain_upload_and_rate_limit_guidance(scenario,stage):
    s = scenario
    result=AcquisitionResult(ID,Outcome.UPLOAD_FAILURE,Recovery.RATE_LIMITED,
        upload_stage=stage,mutation_uncertain=True,retry_after_seconds=12345)
    s.app.state.acquisition_coordinator=SimpleNamespace(snapshot=lambda:AcquisitionSnapshot(Status.FINISHED,ID,Stage.FAILED,result,None))
    response=s.client.get(f'/fragments/acquisition/{ID}')
    assert 'partial or uncertain attachment may remain' in response.text
    assert 'Wait for Zotero' in response.text and '12345' not in response.text
    assert 'every 750ms' not in response.text


def test_unexpected_worker_error_does_not_expose_secrets(scenario,caplog):
    s = scenario;s.post();service=s.services[0];assert service.entered.wait(5)
    service.error=RuntimeError(' '.join(SECRETS));service.finish()
    response=s.client.get(f'/fragments/acquisition/{ID}')
    assert 'unexpected internal error' in response.text
    assert all(secret not in response.text+caplog.text for secret in SECRETS)


def test_public_html_uses_fixed_unexpected_error_guidance(scenario):
    s = scenario
    snapshot=AcquisitionSnapshot(Status.FINISHED,ID,Stage.FAILED,None,UnexpectedAcquisitionError(SECRETS[0],' '.join(SECRETS)))
    s.app.state.acquisition_coordinator=SimpleNamespace(snapshot=lambda:snapshot)
    response=s.client.get(f'/fragments/acquisition/{ID}')
    assert 'unexpected internal error' in response.text
    assert all(secret not in response.text for secret in SECRETS)


@pytest.mark.parametrize('mode',['missing','malformed'])
def test_invalid_config_is_normal_action_failure_without_worker(scenario,monkeypatch,mode):
    s = scenario
    if mode=='missing':s.config.unlink()
    else:s.config.write_text('output_dir: [\n')
    app=web.create_app(s.config)
    monkeypatch.setattr(app.state.acquisition_coordinator,'start',forbidden)
    with TestClient(app,base_url='http://localhost') as client:
        assert client.get('/').status_code==200
        response=client.post(f'/papers/{ID}/acquire-pdf',data={'csrf_token':app.state.csrf_token})
    assert response.status_code==200 and 'Configuration needs attention' in response.text
    assert s.services==[]


def save_workspace(s, name):
    state=load_settings(s.config)
    response=s.client.post('/settings/save',data=valid_settings_form(s.app.state.csrf_token,
        output_dir=name,monitor_revision_digest=state.draft.monitor_revision.digest,
        journal_revision_digest=state.draft.journal_revision.digest))
    assert response.status_code==200 and response.headers['HX-Trigger']=='settingsSaved'


@pytest.mark.parametrize('while_running',[False,True])
def test_settings_save_binds_next_attempt_without_replacing_active_service(scenario,while_running):
    s = scenario;coordinator=s.app.state.acquisition_coordinator
    s.post();first=s.services[0];assert first.entered.wait(5)
    if not while_running:first.finish()
    new_output=s.config.parent/'new-workspace';write_paper(new_output)
    save_workspace(s,'new-workspace')
    assert first.output_dir==s.output and s.app.state.acquisition_coordinator is coordinator
    if while_running:
        busy=s.post()
        assert 'Another PDF acquisition is already in progress.' in busy.text
        assert s.services==[first] and coordinator.snapshot().paper_id==ID
        first.finish()
    s.post();second=s.services[1];assert second.entered.wait(5)
    assert second.output_dir==new_output and first.calls==[ID] and second.calls==[ID]
    assert first.retry_boundary is second.retry_boundary
    assert s.app.state.acquisition_coordinator is coordinator
    second.finish()


def test_same_workspace_reuses_service_and_its_a5_retry_boundary(scenario):
    s = scenario;write_paper(s.output,OTHER)
    s.post();first=s.services[0];first.finish()
    other=s.client.get(f'/fragments/papers/{OTHER}?view=in-zotero')
    assert 'Add PDF to Zotero' in other.text and 'disabled>Add PDF to Zotero' not in other.text
    assert 'PDF attached to the verified' not in other.text
    s.post(OTHER);first.finish()
    assert s.services==[first] and first.calls==[ID,OTHER]


def test_service_construction_failure_is_sanitized_and_slot_reusable(scenario,monkeypatch):
    s = scenario;factory=web._build_acquisition_service
    def broken(*args,**kwargs):raise RuntimeError(' '.join(SECRETS))
    monkeypatch.setattr(web,'_build_acquisition_service',broken)
    response=s.post()
    assert response.status_code==200 and 'unexpected internal error' in response.text
    assert all(secret not in response.text for secret in SECRETS)
    assert s.services==[] and s.app.state.acquisition_coordinator.snapshot().status is Status.FINISHED
    monkeypatch.setattr(web,'_build_acquisition_service',factory)
    s.post();assert len(s.services)==1 and s.services[0].entered.wait(5)


def test_new_app_has_fresh_idle_slot(scenario):
    s = scenario;s.post();s.services[0].finish()
    fresh=web.create_app(s.config)
    assert fresh.state.acquisition_coordinator is not s.app.state.acquisition_coordinator
    assert fresh.state.acquisition_coordinator.snapshot()==AcquisitionSnapshot(Status.IDLE,None,None,None,None)
    with TestClient(fresh,base_url='http://localhost') as client:
        response=client.get(f'/fragments/papers/{ID}?view=in-zotero')
    assert 'PDF attached to the verified' not in response.text and len(s.services)==1


def test_normal_post_returns_intelligible_html(scenario):
    s = scenario
    response=s.client.post(f'/papers/{ID}/acquire-pdf',data={'csrf_token':s.app.state.csrf_token})
    assert response.status_code==200 and response.headers['Content-Type'].startswith('text/html')
    assert 'PDF acquisition in progress' in response.text and 'id="acquisition-panel"' in response.text


@pytest.mark.parametrize('status',[WorkflowStatus.CANDIDATE,WorkflowStatus.KEPT,WorkflowStatus.REJECTED])
def test_crafted_request_reaches_real_a5_current_disk_authority(scenario,monkeypatch,status):
    s = scenario;write_paper(s.output,status=status)
    called=threading.Event();results=[];workers=[]
    service=AcquisitionService(s.output)
    prepare=service.prepare
    def checked(*args,**kwargs):
        workers.append(threading.current_thread())
        result=prepare(*args,**kwargs);results.append(result);called.set();return result
    service.prepare=checked
    monkeypatch.setattr(web,'_build_acquisition_service',lambda *args,**kwargs:service)
    before=s.path.read_bytes()
    s.post(doi='10.5555/forged',zotero_key=SECRETS[0],path=SECRETS[5],expected_status='in_zotero')
    assert called.wait(5);workers[0].join(5)
    assert results[0].outcome is Outcome.INELIGIBLE and s.path.read_bytes()==before
    response=s.client.get(f'/fragments/acquisition/{ID}')
    assert results[0].message in response.text and all(secret not in response.text for secret in SECRETS)


def test_service_construction_is_local_only_without_legacy_browser(tmp_path,monkeypatch):
    service=web._build_acquisition_service(tmp_path/'workspace',tmp_path/'synthetic.yaml')
    assert isinstance(service,AcquisitionService) and list(tmp_path.iterdir())==[]
    assert not hasattr(service,'acquire') and not hasattr(service,'_resolver') and not hasattr(service,'_pdf_acquirer')


def test_home_and_arbitrary_cwd_do_not_create_browser_profiles(tmp_path,monkeypatch):
    arbitrary=tmp_path/'arbitrary';arbitrary.mkdir()
    for cwd in (Path.home(),arbitrary):
        monkeypatch.chdir(cwd)
        service=web._build_acquisition_service(tmp_path/'workspace',tmp_path/'synthetic.yaml')
        assert not hasattr(service,'acquire') and not hasattr(service,'_resolver') and not hasattr(service,'_pdf_acquirer')
    assert list(tmp_path.iterdir())==[arbitrary]


def test_web_binds_staging_protected_locations_and_current_port(scenario,monkeypatch):
    from literature_monitor.web.acquisition_coordinator import AcquisitionStartResult, AcquisitionStartOutcome
    calls=[]
    def start(paper,**kwargs):
        calls.append((paper,kwargs));return AcquisitionStartResult(AcquisitionStartOutcome.STARTED)
    monkeypatch.setattr(scenario.app.state.acquisition_coordinator,'start',start)
    scenario.post()
    assert calls[0][0]==ID
    assert calls[0][1]['staging_options']=={'project_dir':web._PACKAGE_DIR.parents[2],
        'workspace_dir':scenario.output,'config_path':scenario.config.resolve()}
    assert calls[0][1]['port']==80


def test_real_authorization_429_boundary_survives_settings_a_b_a_until_expiry(scenario,tmp_path,monkeypatch):
    import httpx
    from literature_monitor.application.acquisition import AuthorizationRetryBoundary
    from literature_monitor.zotero_local import VerifiedZoteroItem, ZoteroIdentityResult, ZoteroAttachmentResult, ZoteroReadOutcome, ZoteroInstanceResult
    from literature_monitor.zotero_write import ZoteroWriteClient, ZoteroAuthorizationClient

    now=[100.0];boundary=AuthorizationRetryBoundary(clock=lambda:now[0])
    monkeypatch.setattr(web,'AuthorizationRetryBoundary',lambda:boundary)
    parent=VerifiedZoteroItem('PARENT01','10.5555/test','synthetic-instance')
    child='CHILD001';metadata=[];requests=[];built=[];workers=[];completed=threading.Event()
    counts={'writer':0}
    class Local:
        def current_instance(self):
            return ZoteroInstanceResult(ZoteroReadOutcome.VERIFIED,parent.server_id,'Reachable')
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def resolve_identity(self,doi,key=None):
            assert doi==parent.normalized_doi
            return ZoteroIdentityResult(ZoteroReadOutcome.VERIFIED,parent,'Verified synthetic parent')
        def verify_parent_key(self,doi,key,*,server_id=None):
            assert key==parent.key and server_id in (None,parent.server_id)
            return self.resolve_identity(doi,key)
        def inspect_attachments(self,verified):
            assert verified==parent
            return ZoteroAttachmentResult(ZoteroReadOutcome.CHECKED,False,'Synthetic metadata',tuple(metadata))
    class Store:
        key=None
        def load(self,server):return self.key
        def save(self,server,key):self.key=key
        def delete(self,server):self.key=None
    def respond(request):
        requests.append(request)
        headers={'Zotero-Server-ID':parent.server_id}
        if request.method=='GET':return httpx.Response(200,headers=headers)
        if request.url.path=='/api/local/authorize':
            if now[0]<130:return httpx.Response(429,headers=dict(headers,**{'Retry-After':'30'}))
            return httpx.Response(200,headers=headers,json={'key':'A'*32,'remember':True})
        if request.url.path=='/api/users/0/items':
            metadata.append(child)
            return httpx.Response(200,headers=headers,json={'successful':{'0':{'key':child,'data':{
                'key':child,'itemType':'attachment','parentItem':parent.key,'linkMode':'imported_file','contentType':'application/pdf'}}},
                'unchanged':{},'failed':{}})
        assert request.url.path==f'/api/users/0/items/{child}/file'
        return httpx.Response(200,headers=headers,json={'exists':1})
    def writer(verified,*,authorization_runtime):
        counts['writer']+=1
        return ZoteroWriteClient(verified,authorization_runtime=authorization_runtime,transport=httpx.MockTransport(respond))
    def build(output,config_path,*,authorization_retry_boundary,authorization_runtime):
        assert authorization_retry_boundary is boundary
        built.append(output)
        service=AcquisitionService(output,
            authorization_retry_boundary=authorization_retry_boundary,authorization_runtime=authorization_runtime)
        prepare=service.prepare
        def tracked(*args,**kwargs):
            workers.append(threading.current_thread())
            try:return prepare(*args,**kwargs)
            finally:completed.set()
        service.prepare=tracked
        return service
    monkeypatch.setattr(acquisition_application,'ZoteroLocalClient',Local)
    monkeypatch.setattr(acquisition_application,'ZoteroWriteClient',writer)
    monkeypatch.setattr(web,'_build_acquisition_service',build)
    monkeypatch.setattr(web,'ZoteroLocalClient',Local)
    monkeypatch.setattr(web,'ZoteroAuthorizationClient',lambda server,**kwargs:ZoteroAuthorizationClient(server,transport=httpx.MockTransport(respond),**kwargs))
    app=web.create_app(scenario.config);coordinator=app.state.acquisition_coordinator
    launches=[];app.state.chrome_launcher=launches.append
    app.state.zotero_authorization.store=Store()
    with TestClient(app,base_url='http://localhost') as client:
        s=SimpleNamespace(app=app,client=client,config=scenario.config)
        def attempt():
            completed.clear()
            response=client.post(f'/papers/{ID}/acquire-pdf',data={'csrf_token':app.state.csrf_token})
            assert response.status_code==200 and completed.wait(5)
            workers[-1].join(5);assert not workers[-1].is_alive()
            assert app.state.acquisition_coordinator is coordinator
            return coordinator.snapshot().result
        auth=client.post('/settings/zotero/authorize',data={'csrf_token':app.state.csrf_token})
        assert 'rate limited' in auth.text
        first=attempt()
        assert first.recovery is Recovery.RATE_LIMITED and boundary.remaining()==30
        assert counts=={'writer':0}
        before=len(requests)
        workspace_b=scenario.config.parent/'workspace-B';write_paper(workspace_b)
        save_workspace(s,'workspace-B');now[0]=110
        second=attempt()
        assert second.recovery is Recovery.RATE_LIMITED and second.retry_after_seconds==20
        assert counts=={'writer':0} and len(requests)==before
        save_workspace(s,'workspace');now[0]=120
        third=attempt()
        assert third.recovery is Recovery.RATE_LIMITED and third.retry_after_seconds==10
        assert counts=={'writer':0} and len(requests)==before
        assert built==[scenario.output,workspace_b,scenario.output]
        now[0]=130
        auth=client.post('/settings/zotero/authorize',data={'csrf_token':app.state.csrf_token})
        assert 'Remembered write authorization is available' in auth.text
        fourth=attempt()
        assert fourth is None and boundary.remaining()==0
        assert coordinator.snapshot().stage is Stage.HANDOFF and len(launches)==1
        assert counts=={'writer':0}
        assert len([r for r in requests if r.url.path=='/api/local/authorize'])==2
        # Continue the same prepared task through real A5/A7/A8 and the HTTP
        # mock writer through the sole production acquisition path.
        from urllib.parse import urlsplit
        import literature_monitor.zotero_write as writer_module
        monkeypatch.setattr(writer_module,'ZoteroAuthorizationClient',
            lambda server,**kwargs:ZoteroAuthorizationClient(server,transport=httpx.MockTransport(respond),**kwargs))
        active=coordinator._active;task=active.task;service=active.service
        commit=service.commit;committed=threading.Event()
        def tracked_commit(*args,**kwargs):
            workers.append(threading.current_thread())
            try:return commit(*args,**kwargs)
            finally:committed.set()
        service.commit=tracked_commit
        def no_read(*args,**kwargs):raise AssertionError('No Paper action reread after task freeze')
        monkeypatch.setattr(acquisition_application,'_read_paper',no_read)
        before=(scenario.output/'Papers'/f'{ID}.md').read_bytes()
        claim=client.post('/browser-handoff/claim',json={'task_id':str(task.task_id),
            'capability':urlsplit(launches[0].url).fragment,'tab_binding':'tab-42'})
        assert claim.status_code==200
        capability=claim.json()['event_capability']
        def event(kind,payload):return client.post('/browser-handoff/events',json={
            'task_id':str(task.task_id),'capability':capability,'tab_binding':'tab-42','event_type':kind,'payload':payload})
        ready=event('tab_ready',{})
        assert ready.status_code==200 and ready.json()['command']['plan']==active.plan.message()
        source=tmp_path/'user-download.pdf';source.write_bytes(b'%PDF-1.7\nsynthetic actual staged bytes')
        payload=web_download(SimpleNamespace(task=task),source)
        assert event('download_candidate',payload).status_code==204
        assert committed.wait(5);workers[-1].join(5);assert not workers[-1].is_alive()
        assert coordinator.snapshot().result.outcome is Outcome.SUCCEEDED
        assert coordinator.snapshot().result.upload_stage is ZoteroUploadStage.REGISTERED
        assert counts=={'writer':1}
        assert source.read_bytes()==b'%PDF-1.7\nsynthetic actual staged bytes'
        assert (scenario.output/'Papers'/f'{ID}.md').read_bytes()==before
        assert app.state.browser_handoff.status(str(task.task_id)) is None
        assert event('tab_ready',{}).status_code==403
        assert len([r for r in requests if r.url.path=='/api/local/authorize'])==2


@pytest.fixture
def browser_scenario(scenario, monkeypatch):
    from urllib.parse import urlsplit
    from test_acquisition_coordinator import ControlledService as TaskService
    from literature_monitor.web import acquisition_coordinator as coordination

    services=[];workers=[];launches=[];real_thread=threading.Thread
    def thread(*args,**kwargs):
        worker=real_thread(*args,**kwargs)
        if kwargs.get('name','').startswith('literature-monitor-acquisition-'):workers.append(worker)
        return worker
    monkeypatch.setattr(coordination.threading,'Thread',thread)
    def build(directory,config_path,**kwargs):
        service=TaskService(directory);service.prepare_release.set();services.append(service);return service
    monkeypatch.setattr(web,'_build_acquisition_service',build)
    app=web.create_app(scenario.config);app.state.chrome_launcher=launches.append
    with TestClient(app,base_url='http://localhost:8765') as client:
        s=SimpleNamespace(app=app,client=client,services=services,workers=workers,launches=launches,
                          output=scenario.output,path=scenario.path,config=scenario.config)
        s.post=lambda:client.post(f'/papers/{ID}/acquire-pdf',data={'csrf_token':app.state.csrf_token})
        def join():
            for worker in list(workers):worker.join(5);assert not worker.is_alive()
        s.join=join
        def start():
            response=s.post();s.join();s.snapshot=app.state.acquisition_coordinator.snapshot()
            s.task=services[-1].prepared.task;s.attempt_id=s.snapshot.attempt_id
            return response
        s.start=start
        def claim():
            body={'task_id':str(s.task.task_id),'capability':urlsplit(launches[-1].url).fragment,'tab_binding':'tab-42'}
            response=client.post('/browser-handoff/claim',json=body)
            assert response.status_code==200;s.capability=response.json()['event_capability']
        s.claim=claim
        def event(kind,payload,**changes):
            message=dict(task_id=str(s.task.task_id),capability=s.capability,tab_binding='tab-42',event_type=kind,payload=payload)
            message.update(changes);return client.post('/browser-handoff/events',json=message)
        s.event=event
        def action(name,**changes):
            data=dict(csrf_token=app.state.csrf_token,attempt_id=str(s.attempt_id),view='in-zotero');data.update(changes)
            return client.post(f'/papers/{ID}/acquisition/{name}',data=data)
        s.action=action
        yield s
        for service in services:service.prepare_release.set();service.commit_release.set()
        s.join()
        snapshot=app.state.acquisition_coordinator.snapshot()
        if snapshot.cancel_available:app.state.acquisition_coordinator.cancel(snapshot.paper_id,snapshot.attempt_id)


def web_download(s, source, **changes):
    from test_acquisition_coordinator import TARGET
    size=source.stat().st_size
    payload=dict(doi=s.task.doi,download_id=7,route='direct',ownership='task_navigation',
        navigation_url=TARGET,path=str(source.resolve()),url=TARGET,final_url=TARGET,referrer=TARGET,
        mime='application/pdf',total_bytes=size,file_size=size,state='complete',category='',
        observed_doi=s.task.doi,navigation_time=1000,start_time=2000)
    payload.update(changes);return payload


def test_actual_add_pdf_uses_the_event_driven_normal_chrome_handoff(browser_scenario):
    s=browser_scenario;s.start()
    assert len(s.launches)==1 and s.snapshot.stage is Stage.HANDOFF
    s.claim();reply=s.event('tab_ready',{})
    assert reply.status_code==200 and reply.json()['command']['type']=='START'
    assert reply.json()['command']['plan']['task_id']==str(s.task.task_id)
    assert 'capability' not in reply.text and s.capability not in reply.text+repr(reply.headers)
    assert reply.headers['Cache-Control']=='no-store'
    assert len(s.workers)==1 and not s.workers[0].is_alive()
    page=s.client.get(f'/fragments/acquisition/{ID}')
    assert 'Cancel acquisition' in page.text and 'Publisher/browser action required' in page.text
    assert 'dedicated' not in page.text and 'start a new attempt' not in page.text


@pytest.mark.parametrize('changes',[{'capability':'x'*43},{'tab_binding':'tab-99'},{'task_id':str(OTHER)}])
def test_commands_require_current_event_authority(browser_scenario,changes):
    s=browser_scenario;s.start();s.claim()
    before=s.app.state.acquisition_coordinator.snapshot()
    response=s.event('tab_ready',{},**changes)
    assert response.status_code==403 and 'command' not in response.text
    assert s.app.state.acquisition_coordinator.snapshot()==before


def test_human_and_resolver_waits_remain_active_with_no_worker(browser_scenario):
    s=browser_scenario;s.start();s.claim();s.event('tab_ready',{})
    s.event('human_action_needed',{'route':'direct'})
    page=s.client.get(f'/fragments/acquisition/{ID}')
    assert 'same normal Chrome task tab' in page.text and 'Cancel acquisition' in page.text
    assert len(s.workers)==1 and not s.workers[0].is_alive()
    assert s.event('publisher_fallback_request',{}).json()['command']['type']=='PUBLISHER_EXHAUSTED'
    s.event('resolver_choices',{'choices':[{'id':0,'category':'FullText','label':'A'}, {'id':1,'category':'SmartLinks','label':'B'}]})
    assert s.app.state.acquisition_coordinator.snapshot().stage is Stage.RESOLVER_CHOICE
    assert 'Choose a resolver provider' in s.client.get(f'/fragments/acquisition/{ID}').text
    rejected=s.event('resolver_choice_request',{'choice_id':0,'url':'https://arbitrary.example/'})
    assert rejected.status_code==204 and 'command' not in rejected.text
    assert s.event('resolver_choice_request',{'choice_id':1}).json()['command']=={'task_id':str(s.task.task_id),'type':'CHOOSE','choice_id':1}


@pytest.mark.parametrize('action',['cancel','resume','open-retry'])
@pytest.mark.parametrize('token',[None,'wrong','é💥'])
def test_current_task_actions_enforce_csrf(browser_scenario,action,token):
    s=browser_scenario;s.start()
    before=s.app.state.acquisition_coordinator.snapshot()
    data={'attempt_id':str(s.attempt_id)}
    if token is not None:data['csrf_token']=token
    response=s.client.post(f'/papers/{ID}/acquisition/{action}',data=data)
    assert response.status_code==403 and s.app.state.acquisition_coordinator.snapshot()==before


def test_wrong_current_task_actions_and_trusted_host_fail_closed(browser_scenario):
    s=browser_scenario;s.start();before=s.app.state.acquisition_coordinator.snapshot()
    for action in ('cancel','resume','open-retry'):
        assert s.action(action,attempt_id=str(OTHER)).status_code==409
    assert s.client.post(f'/papers/{ID}/acquisition/cancel',data={'csrf_token':s.app.state.csrf_token,
        'attempt_id':str(s.attempt_id)},headers={'Host':'evil.example'}).status_code==400
    assert s.app.state.acquisition_coordinator.snapshot()==before


def test_cancel_ui_invalidates_authority_and_terminal_refresh(browser_scenario):
    s=browser_scenario;s.start();s.claim();s.event('tab_ready',{})
    response=s.action('cancel')
    assert response.status_code==200 and 'cancelled before Zotero content mutation' in response.text
    assert response.headers['HX-Trigger-After-Swap']=='acquisitionCompleted'
    assert 'Cancel acquisition' not in response.text and 'every 750ms' not in response.text
    assert s.event('tab_ready',{}).status_code==403
    assert s.app.state.browser_handoff.status(str(s.task.task_id)) is None
    assert s.services[0].content_posts==0


def test_open_retry_retains_secret_handoff_without_rendering_url(browser_scenario):
    from urllib.parse import urlsplit
    s=browser_scenario
    def fail(launch):s.launches.append(launch);raise RuntimeError(' '.join(SECRETS))
    s.app.state.chrome_launcher=fail;s.start()
    secret=urlsplit(s.launches[-1].url).fragment
    page=s.client.get(f'/fragments/acquisition/{ID}')
    assert 'Open in Chrome again' in page.text and secret not in page.text+repr(page.headers)
    assert all(secret_value not in page.text for secret_value in SECRETS)
    s.app.state.chrome_launcher=s.launches.append
    assert s.action('open-retry').status_code==200;s.join()
    assert s.launches[0] is s.launches[1]
    s.claim();s.event('tab_ready',{})
    assert s.action('open-retry').status_code==409


def test_missing_zotero_authorization_settings_resume_uses_same_artifact(browser_scenario,tmp_path,monkeypatch):
    import httpx
    from literature_monitor.zotero_local import ZoteroInstanceResult, ZoteroReadOutcome
    from literature_monitor.zotero_write import ZoteroAuthorizationClient, ZoteroAuthorizationOutcome
    s=browser_scenario;s.start();s.claim();s.event('tab_ready',{})
    service=s.services[0];runtime=s.app.state.zotero_authorization
    class Store:
        key=None
        def load(self,server):return self.key
        def save(self,server,key):self.key=key
        def delete(self,server):self.key=None
    runtime.store=Store();requests=[]
    class Local:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def current_instance(self):return ZoteroInstanceResult(ZoteroReadOutcome.VERIFIED,s.task.server_id,'Verified')
    def respond(request):
        requests.append(request)
        return httpx.Response(200,headers={'Zotero-Server-ID':s.task.server_id},
            json={'key':'A'*32,'remember':True} if request.method=='POST' else {})
    def auth_client(server,**kwargs):return ZoteroAuthorizationClient(server,transport=httpx.MockTransport(respond),**kwargs)
    monkeypatch.setattr(web,'ZoteroLocalClient',Local);monkeypatch.setattr(web,'ZoteroAuthorizationClient',auth_client)
    def status(task):
        with auth_client(task.server_id,authorization_runtime=runtime) as client:return client.authorization_status()
    service.authorization_status=status
    source=tmp_path/'chrome-source.pdf';source.write_bytes(b'%PDF-synthetic')
    assert s.event('download_candidate',web_download(s,source)).status_code==204;s.join()
    snapshot=s.app.state.acquisition_coordinator.snapshot();artifact=s.app.state.acquisition_coordinator._active.artifact
    assert snapshot.stage is Stage.WAITING_FOR_ZOTERO_AUTH and service.content_posts==0
    assert not any(r.method=='POST' for r in requests)
    page=s.client.get(f'/fragments/acquisition/{ID}')
    assert '/settings#advanced-diagnostics' in page.text and 'Continue after Zotero authorization' in page.text
    assert 'Cancel acquisition' in page.text and 'start a new attempt' not in page.text
    authorized=s.client.post('/settings/zotero/authorize',data={'csrf_token':s.app.state.csrf_token})
    assert 'Remembered write authorization is available' in authorized.text
    assert s.action('resume',attempt_id=str(OTHER)).status_code==409
    assert s.action('resume').status_code==200;s.join()
    assert service.commits[0][0] is s.task and service.commits[0][1] is artifact
    assert service.content_posts==1 and source.read_bytes()==b'%PDF-synthetic' and not artifact.path.exists()
    assert len([r for r in requests if r.method=='POST'])==1


def test_attaching_hides_cancel_and_crafted_cancel_is_too_late(browser_scenario,tmp_path):
    s=browser_scenario;s.start();s.claim();s.event('tab_ready',{})
    service=s.services[0];service.commit_release.clear()
    source=tmp_path/'chrome.pdf';source.write_bytes(b'%PDF-synthetic')
    try:
        s.event('download_candidate',web_download(s,source));assert service.commit_entered.wait(5)
        page=s.client.get(f'/fragments/acquisition/{ID}')
        assert 'Attaching PDF to Zotero' in page.text and 'Cancel acquisition' not in page.text
        response=s.action('cancel')
        assert response.status_code==409 and 'cancellation is too late' in response.text
        assert 'cancelled before' not in response.text
    finally:service.commit_release.set();s.join()
    assert service.content_posts==1
