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
from literature_monitor.models import Author, CanonicalMetadata, CanonicalPaper, ExternalIds, Workflow, WorkflowStatus
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

    def acquire(self, paper_id, *, stage_callback):
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
    def factory(directory, config_path, *, authorization_retry_boundary):
        assert config_path == config.resolve()
        service = ControlledService(directory)
        service.retry_boundary = authorization_retry_boundary
        services.append(service)
        return service
    monkeypatch.setattr(web,'_build_acquisition_service',factory)
    monkeypatch.setattr(acquisition_application,'ZoteroLocalClient',forbidden)
    monkeypatch.setattr(web.XmuInstitutionalResolver,'resolve',forbidden)
    monkeypatch.setattr(web.GenericPdfAcquirer,'acquire',forbidden)
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
        assert 'manually in the dedicated browser' in response.text
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
        assert 'Complete institutional login or verification manually' in response.text


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
    service=AcquisitionService(s.output,resolver=SimpleNamespace(resolve=forbidden),pdf_acquirer=SimpleNamespace(acquire=forbidden))
    acquire=service.acquire
    def checked(*args,**kwargs):
        workers.append(threading.current_thread())
        result=acquire(*args,**kwargs);results.append(result);called.set();return result
    service.acquire=checked
    monkeypatch.setattr(web,'_build_acquisition_service',lambda *args,**kwargs:service)
    before=s.path.read_bytes()
    s.post(doi='10.5555/forged',zotero_key=SECRETS[0],path=SECRETS[5],expected_status='in_zotero')
    assert called.wait(5);workers[0].join(5)
    assert results[0].outcome is Outcome.INELIGIBLE and s.path.read_bytes()==before
    response=s.client.get(f'/fragments/acquisition/{ID}')
    assert results[0].message in response.text and all(secret not in response.text for secret in SECRETS)


def test_service_construction_is_local_only_with_protected_locations(tmp_path,monkeypatch):
    calls=[]
    class Resolver:
        def __init__(self,**kwargs):calls.append(('resolver',kwargs))
    class Acquirer:
        def __init__(self,**kwargs):calls.append(('acquirer',kwargs))
    monkeypatch.setattr(web,'XmuInstitutionalResolver',Resolver)
    monkeypatch.setattr(web,'GenericPdfAcquirer',Acquirer)
    service=web._build_acquisition_service(tmp_path/'workspace',tmp_path/'synthetic.yaml')
    expected={'project_dir':web._PACKAGE_DIR.parent,'workspace_dir':tmp_path/'workspace','config_path':tmp_path/'synthetic.yaml'}
    assert calls==[('resolver',expected),('acquirer',expected)]
    assert isinstance(service,AcquisitionService) and list(tmp_path.iterdir())==[]


def test_home_and_arbitrary_cwd_leave_dedicated_profile_valid(tmp_path,monkeypatch):
    from literature_monitor import institutional_resolver, pdf_acquisition
    from literature_monitor.institutional_browser import institutional_profile

    inputs=tmp_path/'inputs';inputs.mkdir()
    arbitrary=tmp_path/'arbitrary-cwd';arbitrary.mkdir()
    app_data=Path.home()/'Library'/'Application Support'/'Literature Monitor'
    profile=(app_data/'institutional-browser').resolve()
    monkeypatch.setattr(institutional_resolver,'user_data_path',lambda *args,**kwargs:app_data)
    monkeypatch.setattr(institutional_resolver,'browser_session',forbidden)
    monkeypatch.setattr(pdf_acquisition,'_browser_session',forbidden)
    paths=[];mkdir_calls=[]
    for cwd in (Path.home(),arbitrary):
        monkeypatch.chdir(cwd)
        service=web._build_acquisition_service(inputs/'workspace',inputs/'synthetic.yaml')
        paths.append((service._resolver._protected,service._pdf_acquirer._protected))
        # Exercise the real overlap checks without creating/touching the user's profile.
        with monkeypatch.context() as local:
            local.setattr(Path,'mkdir',lambda path,*args,**kwargs:mkdir_calls.append(path))
            assert service._resolver._profile()==profile
            assert institutional_profile(app_data,service._pdf_acquirer._protected)==profile
    expected=(web._PACKAGE_DIR.parent.resolve(),inputs/'workspace',inputs)
    assert paths==[(expected,expected)]*2 and mkdir_calls==[profile]*4


@pytest.mark.parametrize('location',['application','workspace','config'])
def test_stable_protected_locations_still_reject_overlapping_profile(tmp_path,monkeypatch,location):
    from literature_monitor import institutional_resolver
    from literature_monitor.institutional_browser import institutional_profile

    app_data=web._PACKAGE_DIR.parent/'synthetic-profile' if location=='application' else tmp_path/'app-data'
    workspace=app_data if location=='workspace' else tmp_path/'workspace'
    config=app_data/'synthetic.yaml' if location=='config' else tmp_path/'inputs'/'synthetic.yaml'
    monkeypatch.setattr(institutional_resolver,'user_data_path',lambda *args,**kwargs:app_data)
    service=web._build_acquisition_service(workspace,config)
    with pytest.raises(ValueError,match='overlaps protected'):
        service._resolver._profile()
    with pytest.raises(ValueError,match='overlaps protected'):
        institutional_profile(app_data,service._pdf_acquirer._protected)


def test_real_authorization_429_boundary_survives_settings_a_b_a_until_expiry(scenario,tmp_path,monkeypatch):
    import httpx
    from literature_monitor.application.acquisition import AuthorizationRetryBoundary
    from literature_monitor.institutional_resolver import ResolverResult, ResolverOutcome, ResolverCandidate
    from literature_monitor.pdf_acquisition import AcquiredPdf, PdfAcquisitionResult, PdfAcquisitionOutcome, PdfSourceKind
    from literature_monitor.zotero_local import VerifiedZoteroItem, ZoteroIdentityResult, ZoteroAttachmentResult, ZoteroReadOutcome
    from literature_monitor.zotero_write import ZoteroWriteClient

    now=[100.0];boundary=AuthorizationRetryBoundary(clock=lambda:now[0])
    monkeypatch.setattr(web,'AuthorizationRetryBoundary',lambda:boundary)
    parent=VerifiedZoteroItem('PARENT01','10.5555/test','synthetic-instance')
    child='CHILD001';metadata=[];requests=[];built=[];workers=[];completed=threading.Event()
    counts={'resolver':0,'pdf':0,'writer':0};files=[]
    class Local:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def resolve_identity(self,doi,key=None):
            assert doi==parent.normalized_doi
            return ZoteroIdentityResult(ZoteroReadOutcome.VERIFIED,parent,'Verified synthetic parent')
        def inspect_attachments(self,verified):
            assert verified==parent
            return ZoteroAttachmentResult(ZoteroReadOutcome.CHECKED,False,'Synthetic metadata',tuple(metadata))
    class Resolver:
        def resolve(self,doi):
            counts['resolver']+=1
            return ResolverResult(ResolverOutcome.RESOLVED,(ResolverCandidate('FullText','https://publisher.example/synthetic'),))
    class Acquirer:
        def acquire(self,candidates):
            counts['pdf']+=1
            directory=tmp_path/f'synthetic-pdf-{counts["pdf"]}';directory.mkdir()
            path=directory/'validated.pdf';path.write_bytes(b'%PDF-synthetic');files.append(path)
            return PdfAcquisitionResult(PdfAcquisitionOutcome.ACQUIRED,
                AcquiredPdf(path,PdfSourceKind.DIRECT_RESPONSE,1,'FullText',path.stat().st_size,directory))
    class Store:
        def load(self,*args):return None
        def save(self,*args):pass
        def delete(self,*args):pass
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
    def writer(verified):
        counts['writer']+=1
        return ZoteroWriteClient(verified,credential_store=Store(),transport=httpx.MockTransport(respond))
    def build(output,config_path,*,authorization_retry_boundary):
        assert authorization_retry_boundary is boundary
        built.append(output)
        service=AcquisitionService(output,resolver=Resolver(),pdf_acquirer=Acquirer(),
            authorization_retry_boundary=authorization_retry_boundary)
        acquire=service.acquire
        def tracked(*args,**kwargs):
            workers.append(threading.current_thread())
            try:return acquire(*args,**kwargs)
            finally:completed.set()
        service.acquire=tracked
        return service
    monkeypatch.setattr(acquisition_application,'ZoteroLocalClient',Local)
    monkeypatch.setattr(acquisition_application,'ZoteroWriteClient',writer)
    monkeypatch.setattr(web,'_build_acquisition_service',build)
    app=web.create_app(scenario.config);coordinator=app.state.acquisition_coordinator
    with TestClient(app,base_url='http://localhost') as client:
        s=SimpleNamespace(app=app,client=client,config=scenario.config)
        def attempt():
            completed.clear()
            response=client.post(f'/papers/{ID}/acquire-pdf',data={'csrf_token':app.state.csrf_token})
            assert response.status_code==200 and completed.wait(5)
            workers[-1].join(5);assert not workers[-1].is_alive()
            assert app.state.acquisition_coordinator is coordinator
            return coordinator.snapshot().result
        first=attempt()
        assert first.recovery is Recovery.RATE_LIMITED and boundary.remaining()==30
        assert counts=={'resolver':1,'pdf':1,'writer':1}
        before=len(requests)
        workspace_b=scenario.config.parent/'workspace-B';write_paper(workspace_b)
        save_workspace(s,'workspace-B');now[0]=110
        second=attempt()
        assert second.recovery is Recovery.RATE_LIMITED and second.retry_after_seconds==20
        assert counts=={'resolver':1,'pdf':1,'writer':1} and len(requests)==before
        save_workspace(s,'workspace');now[0]=120
        third=attempt()
        assert third.recovery is Recovery.RATE_LIMITED and third.retry_after_seconds==10
        assert counts=={'resolver':1,'pdf':1,'writer':1} and len(requests)==before
        assert built==[scenario.output,workspace_b,scenario.output]
        now[0]=130
        fourth=attempt()
        assert fourth.outcome is Outcome.SUCCEEDED and boundary.remaining()==0
        assert counts=={'resolver':2,'pdf':2,'writer':2}
        assert len([r for r in requests if r.url.path=='/api/local/authorize'])==2
        assert all(not path.exists() for path in files)
