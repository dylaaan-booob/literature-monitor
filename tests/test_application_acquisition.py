"""Frozen preflight and A7-qualified commit with network-independent writer APIs."""

from dataclasses import FrozenInstanceError
import os
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from literature_monitor.application import acquisition as application
from literature_monitor.application.acquisition import AcquisitionService, AuthorizationRetryBoundary, AcquisitionOutcome as Outcome, AcquisitionRecovery as Recovery, AcquisitionStage as Stage, AcquisitionResult, PreparedAcquisition
from literature_monitor.markdown_state import parse_paper_state, serialize_document
from literature_monitor.materialize import render_paper_markdown
from literature_monitor.models import Author, CanonicalMetadata, CanonicalPaper, ExternalIds, MetadataSource, PaperVersion, VersionKind, VersionRef, Workflow, WorkflowStatus
from literature_monitor.zotero_local import VerifiedZoteroItem, ZoteroIdentityResult, ZoteroAttachmentResult, ZoteroReadOutcome as Read
from literature_monitor.zotero_write import ZoteroAuthorizationClient, ZoteroWriteClient, ZoteroUploadStage
from literature_monitor.zotero_credentials import ZoteroAuthorizationRuntime

ID = UUID('11111111-1111-4111-8111-111111111111')
DOI = '10.5555/acquisition'
PARENT = VerifiedZoteroItem('PARENT01', DOI, 'test-instance')
CHILD = 'CHILD001'
SECRET = 'SENTINELsecretCredential'.ljust(32, '0')
TARGET = 'https://publisher.example/file?signature=SENTINEL_SIGNED_URL'
NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)


def state(path):
    return parse_paper_state(path, path.read_text(), path.parent.parent/'Authors')


def update(path, **changes):
    current = state(path)
    frontmatter = dict(current.frontmatter)
    frontmatter.update(changes)
    path.write_text(serialize_document(frontmatter, current.body))


def change_doi(path):
    current = state(path)
    update(path, doi='10.5555/changed', external_ids=dict(current.frontmatter['external_ids'], doi='10.5555/changed'))


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    root = tmp_path/'vault'
    path = root/'Papers'/'unrelated-name.md'
    path.parent.mkdir(parents=True)
    version = PaperVersion(source='doi', identifier=DOI, kind=VersionKind.JOURNAL_FINAL, date=date(2026,9,20))
    paper = CanonicalPaper(id=ID, metadata=CanonicalMetadata(title='Synthetic test', journal='Biometrics'), external_ids=ExternalIds(doi=DOI),
        authors=(Author(name='Test Author'),), versions=(version,), sources=(MetadataSource(provider='openalex', record_id='https://openalex.org/W1', retrieved_at=NOW),),
        preferred_version=VersionRef(source='doi',identifier=DOI), workflow=Workflow(status=WorkflowStatus.IN_ZOTERO, discovered_at=NOW), journal_issns=('0006-341X',))
    path.write_text(render_paper_markdown(paper, ('test-author',)))
    current = state(path)
    frontmatter = dict(current.frontmatter, custom={'keep':[1,'human']})
    path.write_text(serialize_document(frontmatter,current.body+'\n## Notes\nHuman notes remain.\n'))
    s = SimpleNamespace(root=root, path=path, parent=PARENT, pdf_keys=(), pdf_file_keys=(), next_child_key=CHILD, identities=[], read_outcome=Read.VERIFIED,
        attachment_outcome=Read.CHECKED, unknown_attachments=False, local_closed=False, requests=[], tokens=[], stages=[], resolve_calls=[],
        inspect_calls=0, staging_calls=0, qualification_calls=0, writer_calls=0, files=[], prepared_hook=None, qualified_hook=None,
        identity_hook=None, attachment_hook=None, write_hook=None, stage_hook=None, secret_error=None,
        post_status={}, post_counts={}, protocol_full=False, remembered=True, authorize_status=200, authorize_remember=True)
    class Local:
        def __enter__(self): return self
        def __exit__(self,*args): s.local_closed=True
        def resolve_identity(self,doi,key=None):
            s.resolve_calls.append((doi,key))
            if s.identity_hook: s.identity_hook(len(s.resolve_calls))
            parent = s.identities.pop(0) if s.identities else s.parent
            return ZoteroIdentityResult(s.read_outcome,parent if s.read_outcome is Read.VERIFIED else None,SECRET)
        def verify_parent_key(self,doi,key,*,server_id=None):
            identity = self.resolve_identity(doi,key)
            if identity.item is not None and (identity.item.key != key or (server_id is not None and identity.item.server_id != server_id)):
                return ZoteroIdentityResult(Read.INVALID_RESPONSE,None,SECRET)
            return identity
        def inspect_attachments(self,parent):
            s.inspect_calls += 1
            if s.attachment_hook: s.attachment_hook(s.inspect_calls)
            return ZoteroAttachmentResult(s.attachment_outcome,None if s.unknown_attachments else bool(s.pdf_file_keys),SECRET,s.pdf_keys,s.pdf_file_keys)
    class Store:
        def load(self,server):return SECRET if s.remembered else None
        def save(self,server,key):s.remembered=True
        def delete(self,server):s.remembered=False
    def handler(request):
        s.requests.append(request)
        if s.write_hook:s.write_hook(request)
        headers={'Zotero-Server-ID':PARENT.server_id}
        if request.method=='GET':return httpx.Response(200,headers=headers)
        endpoint=request.url.path
        count=s.post_counts.get(endpoint,0);s.post_counts[endpoint]=count+1
        statuses=s.post_status.get(endpoint,[])
        status=statuses[count] if count<len(statuses) else 200
        if endpoint=='/api/local/authorize':
            return httpx.Response(s.authorize_status,headers=dict(headers,**{'Retry-After':'30'}),json={'key':SECRET,'remember':s.authorize_remember})
        if status!=200:return httpx.Response(status,headers=headers)
        if endpoint=='/api/users/0/items':
            s.tokens.append(request.headers['Zotero-Write-Token'])
            s.pdf_keys=(*s.pdf_keys,s.next_child_key)
            return httpx.Response(200,headers=headers,json={'successful':{'0':{'key':s.next_child_key,'data':{'key':s.next_child_key,'itemType':'attachment','parentItem':PARENT.key,'linkMode':'imported_file','contentType':'application/pdf'}}},'unchanged':{},'failed':{}})
        if endpoint.endswith('/file'):
            if b'upload=' in request.content:
                s.pdf_file_keys=(*s.pdf_file_keys,request.url.path.split('/')[-2])
                return httpx.Response(204,headers=headers)
            if not s.protocol_full:s.pdf_file_keys=(*s.pdf_file_keys,request.url.path.split('/')[-2])
            return httpx.Response(200,headers=headers,json=({'uploadKey':'TEST_UPLOAD','url':'http://localhost:23119/api/local/uploads/TEST_UPLOAD','contentType':'application/octet-stream','prefix':'','suffix':''} if s.protocol_full else {'exists':1}))
        if '/local/uploads/' in endpoint:return httpx.Response(201,headers=headers)
        raise AssertionError('Unexpected safe endpoint')
    def writer(parent,*,authorization_runtime):
        s.writer_calls+=1
        assert parent==PARENT
        if s.secret_error:raise s.secret_error
        return ZoteroWriteClient(parent,authorization_runtime=authorization_runtime,transport=httpx.MockTransport(handler))
    monkeypatch.setattr(application,'ZoteroAuthorizationRuntime',lambda **kwargs:ZoteroAuthorizationRuntime(credential_store=Store(),**kwargs))
    monkeypatch.setattr(application,'ZoteroLocalClient',Local)
    monkeypatch.setattr(application,'ZoteroWriteClient',writer)
    s.service=AcquisitionService(root)
    s.runtime=s.service._authorization_runtime
    def authorize():
        with ZoteroAuthorizationClient(PARENT.server_id,authorization_runtime=s.runtime,
                transport=httpx.MockTransport(handler)) as client:
            return client.authorize()
    s.authorize=authorize
    def progress(stage):
        s.stages.append(stage)
        if s.stage_hook:s.stage_hook(stage)
    s.sources=[]
    def run(service=None):
        service=service or s.service
        prepared=service.prepare(ID,stage_callback=progress)
        if isinstance(prepared,AcquisitionResult):return prepared
        s.staging_calls+=1
        if s.prepared_hook:s.prepared_hook()
        qualified=qualified_download(s,prepared,tmp_path)
        try:
            s.qualification_calls+=1
            if s.qualified_hook:s.qualified_hook()
            if s.stage_hook:s.stage_hook(Stage.ATTACHING)
            return service.commit(prepared.task,qualified,linkage_completed=prepared.linkage_completed)
        finally:qualified.artifact.cleanup()
    s.run=run
    return s


def qualified_download(s,prepared,tmp_path):
    from literature_monitor.browser_acquisition import download_evidence
    from literature_monitor.pdf_staging import stage_download
    from literature_monitor.version_qualification import qualify_pdf
    from literature_monitor.web.browser_handoff import AuthenticatedBrowserEvent
    task=prepared.task
    source=tmp_path/'chrome-downloads'/f'source-{len(s.sources)}.pdf'
    source.parent.mkdir(exist_ok=True);source.write_bytes(b'%PDF-1.7\nsynthetic published artifact')
    s.sources.append(source)
    size=source.stat().st_size
    # Explicit synthetic artifact metadata is supplied by this fixture; production
    # code still rejects absent/ambiguous version evidence.
    payload=dict(doi=DOI,download_id=11,route='direct',ownership='task_navigation',
        navigation_url='https://publisher.example/paper',path=str(source.resolve()),
        url='https://publisher.example/file.pdf',final_url='https://publisher.example/file.pdf',
        referrer='https://publisher.example/paper',mime='application/pdf',total_bytes=size,
        file_size=size,state='complete',category='',version_labels=['published'],
        manifestation=task.target_version.model_dump(mode='json'),observed_doi=DOI,
        navigation_time=1000,start_time=2000)
    evidence=download_evidence(AuthenticatedBrowserEvent(task.task_id,'tab-42','download_candidate',payload),task,'tab-42')
    artifact=stage_download(evidence,project_dir=s.root,workspace_dir=s.root,config_path=tmp_path/'config'/'monitor.yaml')
    s.files.append(artifact.path)
    try:return qualify_pdf(task,evidence,artifact,claimed_tab_binding='tab-42')
    except BaseException:
        artifact.cleanup();raise


def posts(s):return [r for r in s.requests if r.method=='POST']


def assert_clean(s,result):
    assert all(not path.exists() for path in s.files)
    assert all(path.read_bytes()==b'%PDF-1.7\nsynthetic published artifact' for path in s.sources)
    public=repr(result)+result.message
    assert SECRET not in public and TARGET not in public and 'TEST_UPLOAD' not in public
    assert not hasattr(result,'pdf_path') and not hasattr(result,'candidate')


def use_local_file_reads(s, monkeypatch, *, file_failure=None, file_locations=None):
    """Exercise the real A1 reader against the same simulated A2 library."""
    from literature_monitor.zotero_local import ZoteroLocalClient

    locations = dict(file_locations or {})
    for key in s.pdf_file_keys:
        if key not in locations:
            path = s.root.parent / ('SENTINEL_FILE_PATH-' + key + '.pdf')
            path.write_bytes(b'nonempty existing attachment')
            locations[key] = path
    s.local_requests = []
    def handler(request):
        s.local_requests.append(request)
        assert request.method == 'GET'
        headers = {'Zotero-Server-ID':PARENT.server_id, 'Last-Modified-Version':'4'}
        parent = {'key':PARENT.key,'data':{'key':PARENT.key,'itemType':'journalArticle','DOI':DOI}}
        if request.url.path.endswith('/children'):
            assert request.headers['Zotero-Server-ID'] == PARENT.server_id
            payload = [{'key':key,'data':{'key':key,'itemType':'attachment',
                'parentItem':PARENT.key,'contentType':'application/pdf'}} for key in s.pdf_keys]
        elif request.url.path.endswith('/file'):
            assert request.headers['Zotero-Server-ID'] == PARENT.server_id
            if file_failure is not None:
                return httpx.Response(file_failure, headers=headers)
            key = request.url.path.split('/')[-2]
            headers['Location'] = locations[key].as_uri() if key in locations else 'false'
            return httpx.Response(302, headers=headers)
        elif request.url.path.endswith('/items'):
            payload = [parent]
        else:
            assert request.url.path == '/api/users/0/items/' + PARENT.key
            payload = parent
        if isinstance(payload,list):headers['Total-Results'] = str(len(payload))
        return httpx.Response(200, headers=headers, json=payload)
    monkeypatch.setattr(application,'ZoteroLocalClient',lambda:ZoteroLocalClient(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize('key',[PARENT.key,'RACE0001'])
def test_concurrent_nonnull_linkage_after_null_read_is_conflict(scenario, monkeypatch, key):
    from literature_monitor.application.zotero_linkage import LinkageOutcome

    s = scenario
    link = application.link_paper_to_zotero
    concurrent_bytes = []
    def raced_link(*args,**kwargs):
        assert state(s.path).zotero_key is None
        update(s.path,zotero_key=key)
        concurrent_bytes.append(s.path.read_bytes())
        linked = link(*args,**kwargs)
        assert linked.outcome is LinkageOutcome.STATE_CONFLICT
        return linked
    monkeypatch.setattr(application,'link_paper_to_zotero',raced_link)
    result = s.run()
    assert result.outcome is Outcome.CONFLICT and not result.linkage_completed
    assert s.path.read_bytes() == concurrent_bytes[0]
    assert s.inspect_calls == s.staging_calls == s.qualification_calls == s.writer_calls == 0
    assert not posts(s)


@pytest.mark.parametrize('metadata,files',[
    (('PDF00001',),('PDF00001',)),
    (('EMPTY001','PDF00001'),('PDF00001',)),
])
def test_confirmed_file_short_circuit_with_real_local_reader(scenario, monkeypatch, metadata, files):
    s = scenario;s.pdf_keys=metadata;s.pdf_file_keys=files
    use_local_file_reads(s,monkeypatch)
    result = s.run()
    assert result.outcome is Outcome.PDF_ALREADY_ATTACHED
    assert s.staging_calls == s.qualification_calls == s.writer_calls == 0 and not posts(s)
    file_reads = [r for r in s.local_requests if r.url.path.endswith('/file')]
    assert len(file_reads) == len(metadata)
    assert 'SENTINEL_FILE_PATH' not in repr(result)


def test_metadata_only_child_enters_normal_acquisition_with_real_local_reader(scenario, monkeypatch):
    s = scenario;s.pdf_keys=('EMPTY001',)
    use_local_file_reads(s,monkeypatch)
    result = s.run()
    assert result.outcome is Outcome.SUCCEEDED
    assert s.staging_calls == s.qualification_calls == s.writer_calls == 1
    assert s.pdf_file_keys == (CHILD,) and 'EMPTY001' in s.pdf_keys
    assert_clean(s,result)


@pytest.mark.parametrize('exists', [False, True])
def test_preflight_checks_actual_storage_before_browser_or_writer(scenario, tmp_path, monkeypatch, exists):
    s = scenario
    update(s.path, zotero_key=PARENT.key)
    before = s.path.read_bytes()
    s.pdf_keys = ('PARTIAL1',)
    file = tmp_path / 'SENTINEL_FILE_PATH-partial.pdf'
    if exists: file.write_bytes(b'nonempty current attachment')
    use_local_file_reads(s, monkeypatch, file_locations={'PARTIAL1': file})
    result = s.service.prepare(ID)
    if exists:
        assert result.outcome is Outcome.PDF_ALREADY_ATTACHED
    else:
        assert isinstance(result, PreparedAcquisition)
        assert result.task.zotero_key == PARENT.key
        assert not file.exists()
    with application.ZoteroLocalClient() as local:
        attachments = local.inspect_attachments(PARENT)
    assert attachments.pdf_keys == ('PARTIAL1',)
    assert attachments.pdf_file_keys == (('PARTIAL1',) if exists else ())
    assert s.pdf_keys == ('PARTIAL1',) and s.path.read_bytes() == before
    assert s.staging_calls == s.qualification_calls == s.writer_calls == 0 and not posts(s)
    assert all(request.method == 'GET' for request in s.local_requests)
    assert 'SENTINEL_FILE_PATH' not in repr(result) + repr(attachments)


@pytest.mark.parametrize('status',[200,500])
def test_unknown_file_state_prevents_acquisition_with_real_local_reader(scenario, monkeypatch, status):
    s = scenario;s.pdf_keys=('EMPTY001',)
    use_local_file_reads(s,monkeypatch,file_failure=status)
    result = s.run()
    assert result.outcome is Outcome.ZOTERO_FAILURE
    assert s.staging_calls == s.qualification_calls == s.writer_calls == 0 and not posts(s)


def test_new_attempt_after_child_created_failure_reverifies_and_uploads(scenario, monkeypatch):
    s = scenario;s.protocol_full=True
    use_local_file_reads(s,monkeypatch)
    s.post_status[f'/api/users/0/items/{CHILD}/file']=[500]
    first = s.run()
    assert first.outcome is Outcome.UPLOAD_FAILURE and first.upload_stage is ZoteroUploadStage.CHILD_CREATED
    assert s.pdf_keys == (CHILD,) and s.pdf_file_keys == ()
    assert_clean(s,first)
    previous_reads = len(s.local_requests)
    s.next_child_key = 'CHILD002'
    second = s.run()
    assert s.local_requests[previous_reads].url.path == '/api/users/0/items/' + PARENT.key
    assert second.outcome is Outcome.SUCCEEDED and second.upload_stage is ZoteroUploadStage.REGISTERED
    assert s.staging_calls == s.qualification_calls == s.writer_calls == 2
    assert s.pdf_keys == (CHILD,'CHILD002') and s.pdf_file_keys == ('CHILD002',)
    assert all(r.url.path != '/api/users/0/items/' + PARENT.key for r in posts(s))
    assert_clean(s,second)


def test_complete_chain_linkage_and_preservation(scenario):
    s=scenario;before=state(s.path)
    result=s.run()
    assert result.outcome is Outcome.SUCCEEDED and result.linkage_completed
    after=state(s.path)
    assert after.frontmatter==dict(before.frontmatter,zotero_key=PARENT.key) and after.body==before.body
    assert after.status is WorkflowStatus.IN_ZOTERO
    assert s.stages==[Stage.LOCATING_ZOTERO,Stage.CHECKING_ATTACHMENT]
    assert s.local_closed and s.writer_calls==1
    assert_clean(s,result)
    with pytest.raises(FrozenInstanceError):result.outcome=Outcome.CONFLICT


def test_non_null_key_preserved_and_verified_by_exact_key(scenario):
    key=PARENT.key
    s=scenario;update(s.path,zotero_key=key);before=s.path.read_bytes()
    result=s.run()
    assert result.outcome is Outcome.SUCCEEDED and not result.linkage_completed
    assert s.path.read_bytes()==before
    assert s.resolve_calls[0]==(DOI,key) and all(key==PARENT.key for _,key in s.resolve_calls[1:])
    assert_clean(s,result)


@pytest.mark.parametrize('status',['candidate','kept','rejected'])
def test_wrong_current_status_no_external_work(scenario,status):
    s=scenario;update(s.path,status=status)
    result=s.run()
    assert result.outcome is Outcome.INELIGIBLE and s.resolve_calls==[] and posts(s)==[]


@pytest.mark.parametrize('kind',['missing','symlink','duplicate','uuid','malformed','no_doi'])
def test_unsafe_or_ineligible_current_paper_stops_before_zotero(scenario,kind,tmp_path):
    s=scenario
    if kind=='missing':s.path.unlink()
    elif kind=='symlink':
        target=tmp_path/'outside.md';target.write_bytes(s.path.read_bytes());s.path.unlink();s.path.symlink_to(target)
    elif kind=='duplicate':s.path.with_name('duplicate.md').write_bytes(s.path.read_bytes())
    elif kind=='uuid':update(s.path,id=str(UUID(int=2)))
    elif kind=='malformed':update(s.path,zotero_key=[])
    else:
        current=state(s.path);update(s.path,doi=None,external_ids=dict(current.frontmatter['external_ids'],doi=None))
    result=s.run()
    assert result.outcome in (Outcome.INELIGIBLE,Outcome.CONFLICT)
    assert not s.resolve_calls and not posts(s)


@pytest.mark.parametrize('outcome,expected',[(Read.NOT_FOUND,Outcome.ZOTERO_NOT_FOUND),(Read.DUPLICATE,Outcome.ZOTERO_DUPLICATE),
    (Read.INVALID_RESPONSE,Outcome.ZOTERO_FAILURE),(Read.API_FAILURE,Outcome.ZOTERO_FAILURE),(Read.SERVER_ID_MISMATCH,Outcome.ZOTERO_FAILURE)])
def test_identity_failures_do_not_link_or_acquire(scenario,outcome,expected):
    s=scenario;before=s.path.read_bytes();s.read_outcome=outcome
    result=s.run()
    assert result.outcome is expected and s.path.read_bytes()==before
    assert not s.staging_calls and not posts(s)


@pytest.mark.parametrize('change',[lambda s:s.path.unlink(),lambda s:update(s.path,status='kept'),lambda s:change_doi(s.path),lambda s:update(s.path,id=str(UUID(int=3)))])
def test_changes_during_identity_prevent_linkage_and_upload(scenario,change):
    s=scenario;s.identity_hook=lambda count:change(s) if count==1 else None
    result=s.run()
    assert result.outcome is Outcome.CONFLICT and not result.linkage_completed and not posts(s)
    assert not s.staging_calls


def test_wrong_identity_doi_prevents_linkage(scenario):
    s=scenario;before=s.path.read_bytes();s.parent=VerifiedZoteroItem(PARENT.key,'10.5555/wrong',PARENT.server_id)
    result=s.run()
    assert result.outcome is Outcome.ZOTERO_FAILURE and s.path.read_bytes()==before and not posts(s)


def test_existing_pdf_short_circuits_before_browser_and_authorization(scenario):
    s=scenario;s.pdf_keys=s.pdf_file_keys=('EXTERNAL',)
    result=s.run()
    assert result.outcome is Outcome.PDF_ALREADY_ATTACHED and result.linkage_completed
    assert s.staging_calls==s.qualification_calls==s.writer_calls==0 and posts(s)==[]


@pytest.mark.parametrize('kind',['unknown','failure'])
def test_incomplete_attachment_read_is_not_absence(scenario,kind):
    s=scenario
    if kind=='unknown':s.unknown_attachments=True
    else:s.attachment_outcome=Read.INVALID_RESPONSE
    result=s.run()
    assert result.outcome is Outcome.ZOTERO_FAILURE and result.linkage_completed
    assert not s.staging_calls and not posts(s)


@pytest.mark.parametrize('boundary',['prepared','qualified','attaching'])
@pytest.mark.parametrize('change',[lambda s:update(s.path,status='kept'),lambda s:change_doi(s.path),lambda s:s.path.unlink()])
def test_frozen_task_survives_later_paper_changes(scenario,boundary,change):
    s=scenario
    if boundary=='prepared':s.prepared_hook=lambda:change(s)
    elif boundary=='qualified':s.qualified_hook=lambda:change(s)
    else:s.stage_hook=lambda stage:change(s) if stage is Stage.ATTACHING else None
    result=s.run()
    assert result.outcome is Outcome.SUCCEEDED and posts(s)
    assert_clean(s,result)


@pytest.mark.parametrize('change',['parent','server','doi','missing'])
def test_parent_identity_revalidation_before_upload(scenario,change):
    s=scenario
    def change_parent():
        if change=='missing':s.read_outcome=Read.NOT_FOUND
        else:s.parent=VerifiedZoteroItem('OTHER001' if change=='parent' else PARENT.key,
            '10.5555/changed' if change=='doi' else DOI,
            'other-instance' if change=='server' else PARENT.server_id)
    s.qualified_hook=change_parent
    result=s.run()
    assert result.outcome is Outcome.CONFLICT and not posts(s)
    assert_clean(s,result)


@pytest.mark.parametrize('boundary',['prepared','qualified'])
def test_external_pdf_appeared_short_circuits_upload_and_cleans_temp(scenario,boundary):
    s=scenario
    def hook():
        s.pdf_keys=s.pdf_file_keys=('EXTERNAL',)
    if boundary=='prepared':s.prepared_hook=hook
    else:s.qualified_hook=hook
    result=s.run()
    assert result.outcome is Outcome.PDF_ALREADY_ATTACHED and not posts(s)
    assert_clean(s,result)


@pytest.mark.parametrize('status',[403,429])
def test_authorization_denied_or_rate_limited_cleanup(scenario,status):
    s=scenario;s.post_status['/api/users/0/items']=[401];s.authorize_status=status
    result=s.run()
    assert result.outcome is Outcome.UPLOAD_FAILURE
    assert [r.url.path for r in posts(s)]==['/api/users/0/items','/api/local/authorize']
    assert result.recovery is (Recovery.RATE_LIMITED if status==429 else Recovery.ZOTERO_AUTHORIZATION)
    assert_clean(s,result)
    if status==429:
        previous=len(s.requests);repeated=s.run()
        assert repeated.recovery is Recovery.RATE_LIMITED and len(s.requests)==previous


@pytest.mark.parametrize('phase,status,stage',[
    ('create',500,ZoteroUploadStage.NO_CONFIRMED_MUTATION),('prepare',500,ZoteroUploadStage.CHILD_CREATED),
    ('register',500,ZoteroUploadStage.BYTES_UPLOADED)])
def test_partial_and_uncertain_upload_are_failures_with_cleanup(scenario,phase,status,stage):
    s=scenario;s.protocol_full=True
    endpoint='/api/users/0/items' if phase=='create' else f'/api/users/0/items/{CHILD}/file'
    s.post_status[endpoint]=([200,status] if phase=='register' else [status])
    result=s.run()
    assert result.outcome is Outcome.UPLOAD_FAILURE and result.upload_stage is stage
    if phase=='create':assert result.mutation_uncertain
    assert_clean(s,result)


@pytest.mark.parametrize('change',['parent','server','attachment'])
def test_401_guard_prevents_new_authorization_or_replay(scenario,change):
    s=scenario;s.post_status['/api/users/0/items']=[401]
    def changed(request):
        if request.method!='POST' or request.url.path!='/api/users/0/items':return
        if change=='parent':s.parent=VerifiedZoteroItem('OTHER001',DOI,PARENT.server_id)
        elif change=='server':s.parent=VerifiedZoteroItem(PARENT.key,DOI,'other-instance')
        elif change=='attachment':s.pdf_keys=s.pdf_file_keys=('EXTERNAL',)
    s.write_hook=changed
    result=s.run()
    assert result.outcome is (Outcome.PDF_ALREADY_ATTACHED if change=='attachment' else Outcome.CONFLICT)
    assert len(posts(s))==1 and posts(s)[0].url.path=='/api/users/0/items'
    assert_clean(s,result)


def test_paper_changed_during_fresh_authorization_keeps_frozen_replay(scenario):
    s=scenario;s.post_status['/api/users/0/items']=[401]
    s.write_hook=lambda request:change_doi(s.path) if request.url.path=='/api/local/authorize' else None
    result=s.run()
    assert result.outcome is Outcome.SUCCEEDED
    assert [r.url.path for r in posts(s)][:3]==['/api/users/0/items','/api/local/authorize','/api/users/0/items']
    assert state(s.path).external_ids.doi=='10.5555/changed'
    assert_clean(s,result)


@pytest.mark.parametrize('phase',['create','prepare','register'])
def test_401_retries_only_before_child_creation_and_preserves_stage(scenario,phase):
    s=scenario;s.protocol_full=phase=='register'
    endpoint='/api/users/0/items' if phase=='create' else f'/api/users/0/items/{CHILD}/file'
    s.post_status[endpoint]=([200,401] if phase=='register' else [401])
    result=s.run()
    assert result.outcome is (Outcome.SUCCEEDED if phase=='create' else Outcome.UPLOAD_FAILURE)
    assert result.upload_stage is {'create':ZoteroUploadStage.REGISTERED,'prepare':ZoteroUploadStage.CHILD_CREATED,'register':ZoteroUploadStage.BYTES_UPLOADED}[phase]
    assert len([r for r in posts(s) if r.url.path=='/api/local/authorize'])==(1 if phase=='create' else 0)
    creates=[r for r in posts(s) if r.url.path=='/api/users/0/items']
    assert len(creates)==(2 if phase=='create' else 1)
    if phase=='create':assert creates[0].headers['Zotero-Write-Token']==creates[1].headers['Zotero-Write-Token']
    assert_clean(s,result)


@pytest.mark.parametrize('change',['external_pdf','own_removed'])
def test_retry_after_child_creation_fails_truthfully_when_state_changes(scenario,change):
    s=scenario;s.post_status[f'/api/users/0/items/{CHILD}/file']=[401]
    def changed(request):
        if request.method=='POST' and request.url.path.endswith('/file'):
            if change=='external_pdf':s.pdf_keys=(CHILD,'EXTERNAL');s.pdf_file_keys=('EXTERNAL',)
            elif change=='own_removed':s.pdf_keys=()
    s.write_hook=changed
    result=s.run()
    assert result.outcome is Outcome.UPLOAD_FAILURE and result.upload_stage is ZoteroUploadStage.CHILD_CREATED
    assert len(posts(s))==2 and all(r.url.path!='/api/local/authorize' for r in posts(s))
    assert 'partial' in result.message
    assert_clean(s,result)


def test_only_one_fresh_authorization_across_whole_attempt(scenario):
    s=scenario;s.post_status['/api/users/0/items']=[401];s.post_status[f'/api/users/0/items/{CHILD}/file']=[401]
    result=s.run()
    assert result.outcome is Outcome.UPLOAD_FAILURE and result.upload_stage is ZoteroUploadStage.CHILD_CREATED
    assert len([r for r in posts(s) if r.url.path=='/api/local/authorize'])==1
    assert_clean(s,result)


def test_unexpected_writer_exception_is_sanitized_and_temp_removed(scenario):
    s=scenario;s.secret_error=RuntimeError(SECRET+' '+TARGET)
    result=s.run()
    assert result.outcome is Outcome.INTERNAL_FAILURE and result.linkage_completed
    assert_clean(s,result)


@pytest.mark.parametrize('change',['parent','doi','server'])
def test_fresh_authorization_requires_frozen_identity_before_replay(scenario,change):
    s=scenario;s.post_status['/api/users/0/items']=[401]
    def changed(request):
        if request.url.path!='/api/local/authorize':return
        if change=='parent':s.parent=VerifiedZoteroItem('OTHER001',DOI,PARENT.server_id)
        elif change=='doi':s.parent=VerifiedZoteroItem(PARENT.key,'10.5555/changed',PARENT.server_id)
        else:s.parent=VerifiedZoteroItem(PARENT.key,DOI,'other-instance')
    s.write_hook=changed
    result=s.run()
    assert result.outcome is Outcome.CONFLICT
    assert [r.url.path for r in posts(s)]==['/api/users/0/items','/api/local/authorize']
    assert_clean(s,result)


def test_pdf_appeared_during_attachment_recheck_suppresses_upload_despite_paper_edit(scenario):
    s=scenario
    def changed(count):
        if count==2:
            update(s.path,status='kept');s.pdf_keys=s.pdf_file_keys=('EXTERNAL',)
    s.attachment_hook=changed
    result=s.run()
    assert result.outcome is Outcome.PDF_ALREADY_ATTACHED and posts(s)==[]
    assert_clean(s,result)


@pytest.mark.parametrize('change',['external_pdf','own_removed','doi','server'])
def test_register_401_preserves_bytes_uploaded_and_never_opens_dialog(scenario,change):
    s=scenario;s.protocol_full=True;s.post_status[f'/api/users/0/items/{CHILD}/file']=[200,401]
    def changed(request):
        if request.method!='POST' or b'upload=' not in request.content:return
        if change=='external_pdf':s.pdf_keys=(CHILD,'EXTERNAL');s.pdf_file_keys=('EXTERNAL',)
        elif change=='own_removed':s.pdf_keys=()
        elif change=='doi':s.parent=VerifiedZoteroItem(PARENT.key,'10.5555/changed',PARENT.server_id)
        else:s.parent=VerifiedZoteroItem(PARENT.key,DOI,'other-instance')
    s.write_hook=changed
    result=s.run()
    assert result.outcome is Outcome.UPLOAD_FAILURE and result.upload_stage is ZoteroUploadStage.BYTES_UPLOADED
    assert all(r.url.path!='/api/local/authorize' for r in posts(s))
    assert len([r for r in posts(s) if b'upload=' in r.content])==1
    assert_clean(s,result)


def test_cancelled_upload_removes_attempt_file(scenario):
    s=scenario
    s.write_hook=lambda request:(_ for _ in ()).throw(KeyboardInterrupt(SECRET)) if request.url.path=='/api/users/0/items' else None
    with pytest.raises(KeyboardInterrupt):s.run()
    assert all(not path.exists() for path in s.files)
    assert state(s.path).zotero_key==PARENT.key


def test_authorization_retry_boundary_does_not_block_existing_pdf_noop(scenario):
    s=scenario;s.post_status['/api/users/0/items']=[401];s.authorize_status=429
    assert s.run().recovery is Recovery.RATE_LIMITED
    s.pdf_keys=s.pdf_file_keys=('EXTERNAL',)
    previous=len(s.requests)
    result=s.run()
    assert result.outcome is Outcome.PDF_ALREADY_ATTACHED and len(s.requests)==previous
    assert_clean(s,result)


def test_unsafe_papers_directory_blocks_all_external_work(scenario,tmp_path):
    s=scenario
    original=s.root/'Papers';outside=tmp_path/'outside-papers';original.rename(outside);original.symlink_to(outside,target_is_directory=True)
    result=s.run()
    assert result.outcome is Outcome.INELIGIBLE and not s.resolve_calls and not posts(s)


def test_linkage_failure_stops_attempt_without_other_paper_changes(scenario,monkeypatch):
    from literature_monitor.application.zotero_linkage import LinkageResult,LinkageOutcome
    s=scenario;before=s.path.read_bytes()
    monkeypatch.setattr(application,'link_paper_to_zotero',lambda *args,**kwargs:LinkageResult(LinkageOutcome.STATE_CONFLICT,ID,s.path,SECRET))
    result=s.run()
    assert result.outcome is Outcome.CONFLICT and not result.linkage_completed
    assert s.path.read_bytes()==before and not s.staging_calls and not posts(s)
    assert_clean(s,result)


def test_attachment_recheck_failure_cleans_acquired_file_before_authorization(scenario):
    s=scenario
    s.qualified_hook=lambda:setattr(s,'attachment_outcome',Read.INVALID_RESPONSE)
    result=s.run()
    assert result.outcome is Outcome.ZOTERO_FAILURE and not posts(s)
    assert_clean(s,result)


def test_401_retry_attachment_read_failure_prevents_dialog_and_replay(scenario):
    s=scenario;s.post_status['/api/users/0/items']=[401]
    s.write_hook=lambda request:setattr(s,'unknown_attachments',True) if request.url.path=='/api/users/0/items' else None
    result=s.run()
    assert result.outcome is Outcome.ZOTERO_FAILURE and len(posts(s))==1
    assert_clean(s,result)


def test_shared_authorization_retry_boundary_survives_new_services_and_expires(scenario):
    s=scenario;now=[100.0]
    boundary=AuthorizationRetryBoundary(clock=lambda:now[0])
    def service():
        return AcquisitionService(s.root,
            authorization_retry_boundary=boundary)
    s.service=service();s.runtime=s.service._authorization_runtime;s.post_status['/api/users/0/items']=[401];s.authorize_status=429
    first=s.run()
    assert first.recovery is Recovery.RATE_LIMITED and boundary.remaining()==30
    previous=(s.staging_calls,s.qualification_calls,s.writer_calls,len(s.requests))
    now[0]=110
    second=service().prepare(ID)
    assert second.recovery is Recovery.RATE_LIMITED and second.retry_after_seconds==20
    assert (s.staging_calls,s.qualification_calls,s.writer_calls,len(s.requests))==previous
    now[0]=130;s.authorize_status=200
    assert s.authorize().remembered
    third=s.run(service())
    assert third.outcome is Outcome.SUCCEEDED and boundary.remaining()==0
    assert len([r for r in posts(s) if r.url.path=='/api/local/authorize'])==2
    assert_clean(s,first);assert_clean(s,second);assert_clean(s,third)


def test_default_authorization_boundaries_are_independent(scenario):
    s=scenario;s.post_status['/api/users/0/items']=[401];s.authorize_status=429
    assert s.run().recovery is Recovery.RATE_LIMITED
    s.authorize_status=200;s.remembered=True
    separate=AcquisitionService(s.root)
    assert s.run(separate).outcome is Outcome.SUCCEEDED


@pytest.mark.parametrize('kind',['symlink','directory','fifo','unreadable','malformed'])
def test_unsafe_sibling_cannot_conceal_duplicate_identity(scenario,tmp_path,monkeypatch,kind):
    s=scenario;before=s.path.read_bytes();sibling=s.path.with_name('zzz-hidden.md')
    if kind=='symlink':sibling.symlink_to(s.path)
    elif kind=='directory':sibling.mkdir()
    elif kind=='fifo':os.mkfifo(sibling)
    elif kind=='malformed':sibling.write_bytes(b'---\ntype: paper\nid: [broken')
    else:
        sibling.write_bytes(before)
        original=os.open
        def unreadable(path,*args,**kwargs):
            if path==sibling.name:raise PermissionError(SECRET)
            return original(path,*args,**kwargs)
        monkeypatch.setattr(application.os,'open',unreadable)
    result=s.run()
    assert result.outcome is Outcome.INELIGIBLE
    assert not s.resolve_calls and s.staging_calls==s.qualification_calls==s.writer_calls==0 and not posts(s)
    assert s.path.read_bytes()==before
    assert_clean(s,result)


@pytest.mark.parametrize('kind',['missing','file','symlink','unreadable_scan'])
def test_unsafe_papers_directory_fails_before_zotero(scenario,tmp_path,monkeypatch,kind):
    s=scenario;directory=s.path.parent
    if kind=='unreadable_scan':
        def fail(*args):raise PermissionError(SECRET)
        monkeypatch.setattr(application.os,'listdir',fail)
    else:
        moved=tmp_path/'preserved-papers';directory.rename(moved)
        if kind=='file':directory.write_bytes(b'preserved unrelated object')
        elif kind=='symlink':directory.symlink_to(moved,target_is_directory=True)
    result=s.run()
    assert result.outcome is Outcome.INELIGIBLE and not s.resolve_calls and not posts(s)
    assert s.staging_calls==s.qualification_calls==s.writer_calls==0
    assert_clean(s,result)


@pytest.mark.parametrize('invalid',[
    {'preferred_version':None},
    {'preferred_version':{'source':'doi','identifier':'10.5555/not-present'}},
    {'preferred_version':{'source':'doi'}},
    {'preferred_version':[]},
    {'versions':'broken'},
    {'versions':[]},
    {'versions':[{'source':'doi','identifier':DOI,'kind':'invalid'}]},
    {'versions':[{'source':'doi','identifier':'','kind':'journal_final'}]},
    {'versions':[
        {'source':'doi','identifier':DOI,'kind':'journal_final'},
        {'source':' DOI ','identifier':'https://doi.org/10.5555/ACQUISITION','kind':'journal_online'},
    ]},
])
def test_invalid_preferred_version_state_prevents_all_external_work(scenario,invalid):
    s=scenario;update(s.path,**invalid);before=s.path.read_bytes()
    result=s.run()
    assert result.outcome is Outcome.INELIGIBLE
    assert not s.resolve_calls and not posts(s) and not s.staging_calls
    assert s.path.read_bytes()==before


@pytest.mark.parametrize('doi',[None,'',[],{}])
def test_invalid_action_doi_prevents_external_work(scenario,doi):
    s=scenario;current=state(s.path)
    update(s.path,doi=doi,external_ids=dict(current.frontmatter['external_ids'],doi=doi))
    before=s.path.read_bytes();result=s.run()
    assert result.outcome is Outcome.INELIGIBLE and not s.resolve_calls and not posts(s)
    assert s.path.read_bytes()==before


def capture_tasks(monkeypatch):
    tasks=[];constructor=application.AcquisitionTask
    class Capture(constructor):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);tasks.append(self)
    monkeypatch.setattr(application,'AcquisitionTask',Capture)
    return tasks


@pytest.mark.parametrize('kind,qualification',[
    (VersionKind.JOURNAL_FINAL,application.AcquisitionClass.PUBLISHED),
    (VersionKind.JOURNAL_ONLINE,application.AcquisitionClass.PUBLISHED),
    (VersionKind.ACCEPTED_MANUSCRIPT,application.AcquisitionClass.ACCEPTED_MANUSCRIPT),
    (VersionKind.PREPRINT,application.AcquisitionClass.PREPRINT),
])
def test_task_freezes_complete_normalized_preferred_entry(scenario,monkeypatch,kind,qualification):
    s=scenario
    # A non-DOI version shares the identifier but must not be selected by DOI alone.
    target={'source':' DOI ','identifier':'https://doi.org/10.5555/ACQUISITION',
        'kind':kind.value,'url':'https://publisher.example/expected-version','date':'2026-09-20'}
    update(s.path,versions=[{'source':'other','identifier':DOI,'kind':'journal_final'},target],
        preferred_version={'source':'doi','identifier':DOI},
        doi='https://doi.org/10.5555/ACQUISITION',
        external_ids={'doi':'https://doi.org/10.5555/ACQUISITION'},zotero_key=PARENT.key)
    before=s.path.read_bytes();tasks=capture_tasks(monkeypatch)
    assert isinstance(s.service.prepare(ID),PreparedAcquisition)
    assert isinstance(s.service.prepare(ID),PreparedAcquisition)
    first,second=tasks
    assert first.task_id!=second.task_id and isinstance(first.task_id,UUID)
    assert first.paper_id==ID and first.doi==DOI and first.zotero_key==PARENT.key
    assert first.server_id==PARENT.server_id and first.parent==PARENT
    assert first.acquisition_class is qualification
    assert first.target_version==PaperVersion.model_validate(target)
    assert first.target_version.date==date(2026,9,20)
    assert str(first.target_version.url)=='https://publisher.example/expected-version'
    with pytest.raises(FrozenInstanceError):first.doi='10.5555/retarget'
    with pytest.raises(ValidationError):first.target_version.kind=VersionKind.UNKNOWN
    assert s.path.read_bytes()==before
    assert list(s.root.iterdir())==[s.path.parent]


def test_unknown_preferred_does_not_fall_back_to_published_version(scenario):
    s=scenario
    update(s.path,versions=[
        {'source':'other','identifier':'unknown-target','kind':'unknown'},
        {'source':'doi','identifier':DOI,'kind':'journal_final'},
    ],preferred_version={'source':'other','identifier':'unknown-target'})
    result=s.run()
    assert result.outcome is Outcome.INELIGIBLE and not s.resolve_calls and not posts(s)


@pytest.mark.parametrize('legacy',[False,True])
def test_one_action_read_and_parse_through_writer_and_replay(scenario,monkeypatch,legacy):
    s=scenario
    if not legacy:update(s.path,zotero_key=PARENT.key)
    actions=[];parses=[];opens=[];comparisons=[]
    read_action=application._read_paper;parse=application.parse_paper_state;open_file=os.open
    from literature_monitor.application import zotero_linkage
    compare=zotero_linkage.replace_regular_text_at_identity
    def action(*args):actions.append(args);return read_action(*args)
    def parsed(path,*args):parses.append(path);return parse(path,*args)
    def opened(path,*args,**kwargs):
        if path==s.path.name and 'dir_fd' in kwargs:opens.append((path,bool(comparisons)))
        return open_file(path,*args,**kwargs)
    def compared(path,*args,**kwargs):comparisons.append(path);return compare(path,*args,**kwargs)
    monkeypatch.setattr(application,'_read_paper',action)
    monkeypatch.setattr(application,'parse_paper_state',parsed)
    monkeypatch.setattr(application.os,'open',opened)
    monkeypatch.setattr(zotero_linkage,'replace_regular_text_at_identity',compared)
    s.post_status['/api/users/0/items']=[401]
    result=s.run()
    assert result.outcome is Outcome.SUCCEEDED
    assert len(actions)==1 and parses==[s.path]
    assert opens==[(s.path.name,False)]+([(s.path.name,True)] if legacy else [])
    assert comparisons==([s.path] if legacy else [])
    assert len([r for r in posts(s) if r.url.path=='/api/local/authorize'])==1
    assert_clean(s,result)


@pytest.mark.parametrize('boundary',['prepared','qualified','authorization','retry','register'])
def test_later_full_paper_edit_preserves_frozen_identity_and_concurrent_bytes(scenario,monkeypatch,boundary):
    s=scenario;tasks=capture_tasks(monkeypatch);concurrent=[]
    def edit():
        current=state(s.path)
        frontmatter=dict(current.frontmatter,status='kept',doi='10.5555/changed',
            external_ids={'doi':'10.5555/changed'},zotero_key='OTHER001',
            versions=[{'source':'arxiv','identifier':'new-preprint','kind':'preprint'}],
            preferred_version={'source':'arxiv','identifier':'new-preprint'})
        s.path.write_text(serialize_document(frontmatter,current.body+'\nChanged human body.\n'))
        concurrent.append(s.path.read_bytes())
    if boundary=='prepared':s.prepared_hook=edit
    elif boundary=='qualified':s.qualified_hook=edit
    else:
        s.protocol_full=boundary=='register'
        if boundary in ('authorization','retry'):s.post_status['/api/users/0/items']=[401]
        def hook(request):
            if request.url.path=='/api/local/authorize' or (boundary=='register' and b'upload=' in request.content):edit()
        s.write_hook=hook
    result=s.run()
    assert result.outcome is Outcome.SUCCEEDED
    assert concurrent and s.path.read_bytes()==concurrent[-1]
    assert tasks[0].doi==DOI and tasks[0].zotero_key==PARENT.key
    assert tasks[0].target_version.kind is VersionKind.JOURNAL_FINAL
    assert s.run().outcome is Outcome.INELIGIBLE
    assert len(tasks)==1
    assert all(doi==DOI and key in (None,PARENT.key) for doi,key in s.resolve_calls)
    assert_clean(s,result)


@pytest.mark.parametrize('failure',['stale','wrong_doi','non_bibliographic','unreadable','server_missing','malformed_key','invalid_type'])
def test_nonnull_conflicting_key_never_uses_matching_doi_elsewhere(scenario,monkeypatch,failure):
    from literature_monitor.zotero_local import ZoteroLocalClient
    s=scenario;key='malformed key' if failure=='malformed_key' else 'STALE001'
    update(s.path,zotero_key=key);before=s.path.read_bytes();requests=[]
    def respond(request):
        requests.append(request);headers={'Zotero-Server-ID':PARENT.server_id,'Last-Modified-Version':'4'}
        good={'key':PARENT.key,'data':{'itemType':'journalArticle','DOI':DOI}}
        if request.url.path.endswith('/items'):
            return httpx.Response(200,json=[good],headers=dict(headers,**{'Total-Results':'1'}))
        assert request.url.path=='/api/users/0/items/STALE001'
        if failure=='stale':return httpx.Response(404,headers=headers)
        if failure=='unreadable':return httpx.Response(200,content=b'broken-json',headers=headers)
        if failure=='server_missing':headers.pop('Zotero-Server-ID')
        return httpx.Response(200,json={'key':'STALE001','data':{
            'itemType':{'non_bibliographic':'note','invalid_type':'inventedType'}.get(failure,'journalArticle'),
            'DOI':'10.5555/wrong' if failure=='wrong_doi' else DOI,
        }},headers=headers)
    monkeypatch.setattr(application,'ZoteroLocalClient',lambda:ZoteroLocalClient(transport=httpx.MockTransport(respond)))
    result=s.run()
    assert result.outcome is (Outcome.ZOTERO_NOT_FOUND if failure=='stale' else Outcome.ZOTERO_FAILURE)
    assert len(requests)==(0 if failure=='malformed_key' else 1)
    assert not any(r.url.path.endswith('/items') for r in requests)
    assert s.path.read_bytes()==before and not result.linkage_completed
    assert s.staging_calls==s.qualification_calls==s.writer_calls==0 and not posts(s)


def test_valid_nonnull_key_has_no_enumeration_with_real_reader(scenario,monkeypatch):
    s=scenario;update(s.path,zotero_key=PARENT.key);before=s.path.read_bytes()
    use_local_file_reads(s,monkeypatch)
    result=s.run()
    assert result.outcome is Outcome.SUCCEEDED and not result.linkage_completed
    assert s.path.read_bytes()==before
    assert all(r.url.path!='/api/users/0/items' for r in s.local_requests)
    keyed=[r for r in s.local_requests if r.url.path=='/api/users/0/items/'+PARENT.key]
    assert keyed and 'Zotero-Server-ID' not in keyed[0].headers
    assert all(r.headers['Zotero-Server-ID']==PARENT.server_id for r in keyed[1:])
    assert_clean(s,result)


@pytest.mark.parametrize('failure',['zero','multiple','incomplete','unreadable','unstable_later_page'])
def test_legacy_enumeration_failure_leaves_paper_and_external_work_untouched(scenario,monkeypatch,failure):
    from literature_monitor.zotero_local import ZoteroLocalClient
    s=scenario;before=s.path.read_bytes();requests=[]
    def respond(request):
        requests.append(request);assert request.method=='GET' and request.url.path=='/api/users/0/items'
        parent={'key':PARENT.key,'data':{'itemType':'journalArticle','DOI':DOI}}
        headers={'Zotero-Server-ID':PARENT.server_id,'Last-Modified-Version':'4','Total-Results':'1'}
        if failure=='zero':payload=[];headers['Total-Results']='0'
        elif failure=='multiple':
            payload=[parent,dict(parent,key='OTHER001')];headers['Total-Results']='2'
        elif failure=='incomplete':payload=[parent];headers['Total-Results']='2'
        elif failure=='unreadable':return httpx.Response(200,content=b'broken-json',headers=headers)
        elif len(requests)==1:
            payload=[parent];headers['Total-Results']='2'
            headers['Link']='<http://localhost:23119/api/users/0/items?format=json&include=data&limit=100&start=1&itemType=-attachment>; rel="next"'
        else:
            payload=[dict(parent,key='OTHER001')];headers.update({'Total-Results':'2','Last-Modified-Version':'5'})
        return httpx.Response(200,json=payload,headers=headers)
    monkeypatch.setattr(application,'ZoteroLocalClient',lambda:ZoteroLocalClient(transport=httpx.MockTransport(respond)))
    result=s.run()
    expected={'zero':Outcome.ZOTERO_NOT_FOUND,'multiple':Outcome.ZOTERO_DUPLICATE}.get(failure,Outcome.ZOTERO_FAILURE)
    assert result.outcome is expected and not result.linkage_completed
    assert s.path.read_bytes()==before and not s.staging_calls and not posts(s)


@pytest.mark.parametrize('failure',['doi','server','missing'])
def test_legacy_enumerated_parent_must_still_verify_before_linkage(scenario,monkeypatch,failure):
    from literature_monitor.zotero_local import ZoteroLocalClient
    s=scenario;before=s.path.read_bytes();requests=[]
    def respond(request):
        requests.append(request);headers={'Zotero-Server-ID':PARENT.server_id,'Last-Modified-Version':'4'}
        parent={'key':PARENT.key,'data':{'itemType':'journalArticle','DOI':DOI}}
        if request.url.path.endswith('/items'):
            return httpx.Response(200,json=[parent],headers=dict(headers,**{'Total-Results':'1'}))
        assert request.url.path=='/api/users/0/items/'+PARENT.key
        assert request.headers['Zotero-Server-ID']==PARENT.server_id
        if failure=='missing':return httpx.Response(404,headers=headers)
        if failure=='doi':parent['data']['DOI']='10.5555/changed'
        else:headers['Zotero-Server-ID']='changed-instance'
        return httpx.Response(200,json=parent,headers=headers)
    monkeypatch.setattr(application,'ZoteroLocalClient',lambda:ZoteroLocalClient(transport=httpx.MockTransport(respond)))
    result=s.run()
    assert result.outcome is Outcome.ZOTERO_FAILURE and len(requests)==2
    assert s.path.read_bytes()==before and not result.linkage_completed
    assert not s.staging_calls and not posts(s)


@pytest.mark.parametrize('missing',[False,True])
def test_legacy_linkage_compares_original_action_bytes_once(scenario,monkeypatch,missing):
    from literature_monitor.application import zotero_linkage
    s=scenario
    if missing:
        current=state(s.path);frontmatter=dict(current.frontmatter);frontmatter.pop('zotero_key')
        s.path.write_text(serialize_document(frontmatter,current.body))
    original=s.path.read_bytes();calls=[];compare=zotero_linkage.replace_regular_text_at_identity
    original_directory=s.path.parent.stat();original_file=s.path.stat()
    def compared(path,contents,*,expected_contents,**kwargs):
        calls.append(path);assert expected_contents.encode()==original
        assert kwargs['expected_directory_identity']==(original_directory.st_dev,original_directory.st_ino)
        assert kwargs['expected_file_identity']==(original_file.st_dev,original_file.st_ino)
        compare(path,contents,expected_contents=expected_contents,**kwargs)
    monkeypatch.setattr(zotero_linkage,'replace_regular_text_at_identity',compared)
    use_local_file_reads(s,monkeypatch)
    result=s.service.prepare(ID)
    assert isinstance(result,PreparedAcquisition) and result.linkage_completed
    assert calls==[s.path] and state(s.path).zotero_key==PARENT.key
    assert state(s.path).status is WorkflowStatus.IN_ZOTERO
    paths=[r.url.path for r in s.local_requests]
    assert paths==['/api/users/0/items','/api/users/0/items/'+PARENT.key,'/api/users/0/items/'+PARENT.key+'/children']
    assert not posts(s)


@pytest.mark.parametrize('kind',['symlink','directory','fifo','missing'])
def test_legacy_final_compare_location_conflict_aborts_all_continuations(scenario,tmp_path,monkeypatch,kind):
    from literature_monitor.application import zotero_linkage
    from literature_monitor.safe_write import replace_regular_text_at_identity
    s=scenario;original=s.path.read_bytes();preserved=tmp_path/'preserved-paper.md';calls=[]
    def replace_location(path,contents,*,expected_contents,**kwargs):
        calls.append(path);path.rename(preserved)
        if kind=='symlink':path.symlink_to(preserved)
        elif kind=='directory':path.mkdir();(path/'sentinel').write_bytes(b'human object')
        elif kind=='fifo':os.mkfifo(path)
        replace_regular_text_at_identity(path,contents,expected_contents=expected_contents,**kwargs)
    monkeypatch.setattr(zotero_linkage,'replace_regular_text_at_identity',replace_location)
    result=s.run()
    assert result.outcome is Outcome.CONFLICT and not result.linkage_completed
    assert calls==[s.path] and preserved.read_bytes()==original
    assert s.inspect_calls==s.staging_calls==s.qualification_calls==s.writer_calls==0 and not posts(s)
    if kind=='symlink':assert s.path.is_symlink()
    elif kind=='directory':assert (s.path/'sentinel').read_bytes()==b'human object'
    elif kind=='fifo':assert not s.path.is_file()
    else:assert not s.path.exists()


@pytest.mark.parametrize('substitution',['parent_symlink','same_bytes_inode','different_directory'])
def test_legacy_original_location_identity_conflict_at_compare_boundary(scenario,tmp_path,monkeypatch,substitution):
    from literature_monitor.application import zotero_linkage
    s=scenario;original=s.path.read_bytes();preserved=tmp_path/'original-object';calls=[]
    compare=zotero_linkage.replace_regular_text_at_identity
    def substitute(path,contents,**kwargs):
        calls.append(path)
        if substitution=='same_bytes_inode':
            path.rename(preserved);path.write_bytes(original)
        else:
            path.parent.rename(preserved)
            if substitution=='parent_symlink':path.parent.symlink_to(preserved,target_is_directory=True)
            else:path.parent.mkdir();path.write_bytes(original)
        compare(path,contents,**kwargs)
    monkeypatch.setattr(zotero_linkage,'replace_regular_text_at_identity',substitute)
    result=s.run()
    assert result.outcome is Outcome.CONFLICT and not result.linkage_completed
    assert calls==[s.path]
    moved=preserved if substitution=='same_bytes_inode' else preserved/s.path.name
    assert moved.read_bytes()==original and state(moved).zotero_key is None
    assert s.path.read_bytes()==original and state(s.path).zotero_key is None
    if substitution=='parent_symlink':assert s.path.parent.is_symlink()
    assert s.inspect_calls==s.staging_calls==s.qualification_calls==s.writer_calls==0 and not posts(s)


def test_missing_settings_authorization_cleans_pdf_and_preserves_frozen_linkage(scenario, monkeypatch):
    s = scenario; s.remembered = False
    tasks = capture_tasks(monkeypatch)
    result = s.run()
    assert result.outcome is Outcome.AUTH_REQUIRED
    assert result.recovery is Recovery.ZOTERO_AUTHORIZATION
    assert 'Settings → Advanced & Diagnostics → Zotero integration' in result.message
    assert result.upload_stage is ZoteroUploadStage.NO_CONFIRMED_MUTATION
    assert not result.mutation_uncertain and not posts(s)
    assert result.linkage_completed and state(s.path).zotero_key == PARENT.key
    assert tasks[0].doi == DOI and tasks[0].zotero_key == PARENT.key
    after_linkage = s.path.read_bytes()
    repeated = s.run()
    assert repeated.outcome is Outcome.AUTH_REQUIRED and not posts(s)
    assert s.path.read_bytes() == after_linkage
    assert_clean(s, result); assert_clean(s, repeated)


def test_settings_one_time_allow_is_consumed_without_second_dialog(scenario):
    s = scenario; s.remembered = False; s.authorize_remember = False
    assert s.authorize().remembered is False
    result = s.run()
    assert result.outcome is Outcome.UPLOAD_FAILURE
    assert result.recovery is Recovery.ZOTERO_AUTHORIZATION
    assert result.upload_stage is ZoteroUploadStage.CHILD_CREATED
    assert [r.url.path for r in posts(s)] == ['/api/local/authorize', '/api/users/0/items']
    assert not s.remembered
    repeated = s.run()
    assert repeated.outcome is Outcome.AUTH_REQUIRED
    assert len(posts(s)) == 2
    assert_clean(s, result); assert_clean(s, repeated)


def test_actual_pdf_added_during_remembered_refresh_suppresses_replay(scenario):
    s = scenario; s.post_status['/api/users/0/items'] = [401]
    # Metadata must also be complete for A3's actual-file check.
    def changed(request):
        if request.url.path == '/api/local/authorize':
            s.pdf_keys = s.pdf_file_keys = ('EXTERNAL',)
    s.write_hook = changed
    result = s.run()
    assert result.outcome is Outcome.PDF_ALREADY_ATTACHED
    assert result.upload_stage is ZoteroUploadStage.NO_CONFIRMED_MUTATION
    assert [r.url.path for r in posts(s)] == ['/api/users/0/items', '/api/local/authorize']
    assert_clean(s, result)


@pytest.fixture
def prepared_commit(scenario, tmp_path, monkeypatch):
    """Real A3 prepare + A7 parser/staging/qualification + actual HTTP mock writer."""
    from literature_monitor.application.acquisition import PreparedAcquisition
    from literature_monitor.browser_acquisition import download_evidence
    from literature_monitor.pdf_staging import stage_download
    from literature_monitor.version_qualification import qualify_pdf
    from literature_monitor.web.browser_handoff import AuthenticatedBrowserEvent

    s = scenario
    read = application._read_paper
    s.action_reads = []
    def counted(*args):
        s.action_reads.append(args); return read(*args)
    monkeypatch.setattr(application, '_read_paper', counted)
    prepared = s.service.prepare(ID)
    assert isinstance(prepared, PreparedAcquisition) and len(s.action_reads) == 1
    assert s.staging_calls == s.qualification_calls == s.writer_calls == 0
    task = prepared.task
    source = tmp_path/'chrome-downloads'/'source.pdf'; source.parent.mkdir()
    source.write_bytes(b'%PDF-1.7\nsource bytes')
    size = source.stat().st_size
    payload = dict(doi=DOI, download_id=11, route='direct', ownership='task_navigation',
        navigation_url='https://publisher.example/paper', path=str(source.resolve()),
        url='https://publisher.example/file.pdf', final_url='https://publisher.example/file.pdf',
        referrer='https://publisher.example/paper', mime='application/pdf',
        total_bytes=size, file_size=size, state='complete', category='', version_labels=['published'],
        manifestation=task.target_version.model_dump(mode='json'), observed_doi=DOI,
        navigation_time=1000, start_time=2000)
    evidence = download_evidence(AuthenticatedBrowserEvent(task.task_id,'tab-42','download_candidate',payload),task,'tab-42')
    artifact = stage_download(evidence,project_dir=s.root,workspace_dir=s.root,
                              config_path=tmp_path/'configuration'/'monitor.yaml')
    qualified = qualify_pdf(task,evidence,artifact,claimed_tab_binding='tab-42')
    s.prepared, s.task, s.qualified, s.source = prepared, task, qualified, source
    def no_reread(*args,**kwargs): raise AssertionError('Frozen commit must not reread Paper')
    monkeypatch.setattr(application,'_read_paper',no_reread)
    s.commit = lambda **kwargs: s.service.commit(task,qualified,
        linkage_completed=prepared.linkage_completed,**kwargs)
    yield s
    artifact.cleanup()
    assert source.read_bytes() == b'%PDF-1.7\nsource bytes'


def test_bounded_prepare_and_qualified_commit_keep_the_frozen_target(prepared_commit):
    s=prepared_commit
    update(s.path, status='rejected', preferred_version=None, custom='later human edit')
    before=s.path.read_bytes()
    result=s.commit()
    assert result.outcome is Outcome.SUCCEEDED and result.linkage_completed
    assert result.upload_stage is ZoteroUploadStage.REGISTERED
    assert s.writer_calls==1 and s.staging_calls==s.qualification_calls==0
    assert len([r for r in posts(s) if r.url.path=='/api/users/0/items'])==1
    assert all(r.headers['Zotero-Server-ID']==s.task.server_id for r in posts(s))
    assert s.path.read_bytes()==before and len(s.action_reads)==1


def test_writer_guard_owns_final_check_before_each_content_post(prepared_commit):
    s = prepared_commit
    s.protocol_full = True
    events = []

    def check_parent(count):
        # The writer is already constructed; no independent pre-writer check.
        assert s.writer_calls == 1
        events.append(('parent',))

    s.identity_hook = check_parent
    s.attachment_hook = lambda count: events.append(('attachments',))
    s.write_hook = lambda request: events.append((request.method, request.url.path))
    result = s.commit()
    assert result.outcome is Outcome.SUCCEEDED and result.upload_stage is ZoteroUploadStage.REGISTERED
    assert not result.mutation_uncertain
    expected_posts = ['/api/users/0/items', f'/api/users/0/items/{CHILD}/file',
                      '/api/local/uploads/TEST_UPLOAD', f'/api/users/0/items/{CHILD}/file']
    assert events == [event for endpoint in expected_posts for event in [
        ('GET', '/api/'), ('parent',), ('attachments',), ('POST', endpoint),
    ]]
    assert [r.url.path for r in posts(s)] == expected_posts
    assert posts(s)[2].content == s.source.read_bytes()


def test_final_commit_refuses_raw_paths_or_unqualified_pdf(prepared_commit):
    s=prepared_commit
    for raw in (s.source,s.qualified.artifact,SimpleNamespace(path=s.source),None):
        result=s.service.commit(s.task,raw,linkage_completed=s.prepared.linkage_completed)
        assert result.outcome is Outcome.NO_VALID_PDF
    assert s.writer_calls==0 and posts(s)==[]


@pytest.mark.parametrize('field,value',[('key','OTHER001'),('normalized_doi','10.5555/changed'),('server_id','new-instance')])
def test_final_zotero_only_identity_check_suppresses_upload(prepared_commit,field,value):
    from dataclasses import replace
    s=prepared_commit;s.parent=replace(PARENT,**{field:value})
    result=s.commit()
    assert result.outcome is Outcome.CONFLICT and s.writer_calls==1 and posts(s)==[]
    assert result.upload_stage is ZoteroUploadStage.NO_CONFIRMED_MUTATION and not result.mutation_uncertain
    assert [(r.method,r.url.path) for r in s.requests]==[('GET','/api/')]


def test_final_zotero_only_actual_pdf_check_suppresses_upload(prepared_commit):
    s=prepared_commit;s.pdf_keys=s.pdf_file_keys=('EXIST001',)
    result=s.commit()
    assert result.outcome is Outcome.PDF_ALREADY_ATTACHED and s.writer_calls==1 and posts(s)==[]
    assert result.upload_stage is ZoteroUploadStage.NO_CONFIRMED_MUTATION and not result.mutation_uncertain
    assert [(r.method,r.url.path) for r in s.requests]==[('GET','/api/')]


@pytest.mark.parametrize('incomplete',[False,True])
def test_final_attachment_inspection_fails_closed(prepared_commit,incomplete):
    s=prepared_commit
    if incomplete:s.unknown_attachments=True
    else:s.attachment_outcome=Read.INVALID_RESPONSE
    result=s.commit()
    assert result.outcome is Outcome.ZOTERO_FAILURE and s.writer_calls==1 and posts(s)==[]
    assert result.upload_stage is ZoteroUploadStage.NO_CONFIRMED_MUTATION and not result.mutation_uncertain
    assert [(r.method,r.url.path) for r in s.requests]==[('GET','/api/')]


@pytest.mark.parametrize('change',['modify','delete'])
def test_staged_artifact_changed_before_commit_never_reaches_writer(prepared_commit,change):
    s=prepared_commit
    if change=='modify':s.qualified.artifact.path.write_bytes(b'changed')
    else:s.qualified.artifact.path.unlink()
    result=s.commit()
    assert result.outcome is Outcome.NO_VALID_PDF and s.writer_calls==0 and posts(s)==[]
    assert not s.requests and not result.mutation_uncertain


@pytest.mark.parametrize('change', ['modify', 'delete'])
def test_staged_artifact_changed_after_commit_check_blocks_first_post(prepared_commit, change):
    s = prepared_commit

    def change_after_local_preparation(request):
        # This credential probe follows commit validation and writer file/hash preparation.
        if request.method == 'GET':
            if change == 'modify':s.qualified.artifact.path.write_bytes(b'changed')
            else:s.qualified.artifact.path.unlink()

    s.write_hook = change_after_local_preparation
    result = s.commit()
    assert result.outcome is Outcome.CONFLICT and result.upload_stage is ZoteroUploadStage.NO_CONFIRMED_MUTATION
    assert s.writer_calls == 1 and not posts(s) and not result.mutation_uncertain
    assert [(r.method, r.url.path) for r in s.requests] == [('GET', '/api/')]


@pytest.mark.parametrize('boundary', ['prepare', 'bytes'])
@pytest.mark.parametrize('change', ['modify', 'delete'])
def test_staged_artifact_changed_after_child_blocks_remaining_bytes_mutation(prepared_commit, boundary, change):
    s = prepared_commit
    s.protocol_full = True
    expected_posts = ['/api/users/0/items']
    if boundary == 'bytes':expected_posts.append(f'/api/users/0/items/{CHILD}/file')

    def change_before_boundary(request):
        if request.method == 'GET' and [r.url.path for r in posts(s)] == expected_posts:
            if change == 'modify':s.qualified.artifact.path.write_bytes(b'changed')
            else:s.qualified.artifact.path.unlink()

    s.write_hook = change_before_boundary
    result = s.commit()
    assert result.outcome is Outcome.CONFLICT and result.upload_stage is ZoteroUploadStage.CHILD_CREATED
    assert not result.mutation_uncertain and 'partial or uncertain' in result.message
    assert [r.url.path for r in posts(s)] == expected_posts
    assert s.pdf_keys == (CHILD,) and not s.pdf_file_keys


@pytest.mark.parametrize('change', ['modify', 'delete'])
def test_registration_succeeds_without_artifact_validation_after_confirmed_bytes(prepared_commit, monkeypatch, change):
    from literature_monitor.pdf_staging import StagedPdf

    s = prepared_commit
    s.protocol_full = True
    before_registration = ['/api/users/0/items', f'/api/users/0/items/{CHILD}/file',
                           '/api/local/uploads/TEST_UPLOAD']
    after_bytes = False
    validation_after_bytes = []
    validate = StagedPdf.validate

    def record_validation(artifact):
        if after_bytes:validation_after_bytes.append(artifact)
        return validate(artifact)

    monkeypatch.setattr(StagedPdf, 'validate', record_validation)

    def change_after_confirmed_bytes(request):
        nonlocal after_bytes
        # The registration credential probe happens only after the bytes response
        # returned 201 and the real writer advanced to BYTES_UPLOADED.
        if request.method == 'GET' and [r.url.path for r in posts(s)] == before_registration:
            after_bytes = True
            if change == 'modify':s.qualified.artifact.path.write_bytes(b'changed')
            else:s.qualified.artifact.path.unlink()

    s.write_hook = change_after_confirmed_bytes
    result = s.commit()
    assert after_bytes and validation_after_bytes == []
    assert result.outcome is Outcome.SUCCEEDED and result.upload_stage is ZoteroUploadStage.REGISTERED
    assert not result.mutation_uncertain
    assert [r.url.path for r in posts(s)] == before_registration + [f'/api/users/0/items/{CHILD}/file']
    assert posts(s)[2].content == s.source.read_bytes()
    assert posts(s)[-1].content == b'upload=TEST_UPLOAD'
    assert s.pdf_keys == s.pdf_file_keys == (CHILD,)


@pytest.mark.parametrize('boundary', ['prepare', 'bytes', 'register'])
@pytest.mark.parametrize('change', ['parent', 'doi', 'server', 'own_removed', 'external_pdf', 'incomplete'])
def test_current_zotero_state_blocks_each_remaining_mutation_boundary(prepared_commit, boundary, change):
    s = prepared_commit
    s.protocol_full = True
    expected_posts = ['/api/users/0/items']
    if boundary in ('bytes', 'register'):expected_posts.append(f'/api/users/0/items/{CHILD}/file')
    if boundary == 'register':expected_posts.append('/api/local/uploads/TEST_UPLOAD')
    inspections_before = s.inspect_calls

    def change_before_boundary(request):
        if request.method != 'GET' or [r.url.path for r in posts(s)] != expected_posts:return
        if change in ('parent', 'doi', 'server'):
            s.parent = VerifiedZoteroItem('OTHER001' if change == 'parent' else PARENT.key,
                '10.5555/changed' if change == 'doi' else DOI,
                'other-instance' if change == 'server' else PARENT.server_id)
        elif change == 'own_removed':s.pdf_keys = ()
        elif change == 'external_pdf':s.pdf_keys, s.pdf_file_keys = (CHILD, 'OTHER001'), ('OTHER001',)
        else:s.attachment_outcome = Read.INVALID_RESPONSE

    s.write_hook = change_before_boundary
    result = s.commit()
    assert result.outcome is (Outcome.ZOTERO_FAILURE if change == 'incomplete' else Outcome.CONFLICT)
    assert result.upload_stage is (ZoteroUploadStage.BYTES_UPLOADED if boundary == 'register'
                                   else ZoteroUploadStage.CHILD_CREATED)
    assert not result.mutation_uncertain and 'partial or uncertain' in result.message
    assert [r.url.path for r in posts(s)] == expected_posts
    if change not in ('parent', 'doi', 'server'):
        assert s.inspect_calls > inspections_before


def test_final_commit_remembered_401_rechecks_zotero_before_one_replay(prepared_commit):
    s=prepared_commit;s.post_status['/api/users/0/items']=[401,200]
    observed=[]
    def record(request):
        if request.method=='POST':observed.append((request.url.path,len(s.resolve_calls),s.inspect_calls))
    s.write_hook=record
    result=s.commit()
    assert result.outcome is Outcome.SUCCEEDED
    assert len([r for r in posts(s) if r.url.path=='/api/local/authorize'])==1
    creates=[entry for entry in observed if entry[0]=='/api/users/0/items']
    authorization=[entry for entry in observed if entry[0]=='/api/local/authorize'][0]
    assert len(creates)==2 and authorization[1]>creates[0][1] and authorization[2]>creates[0][2]
    assert creates[1][1]>authorization[1] and len(set(s.tokens))==1


def test_new_pdf_at_remembered_401_guard_prevents_dialog_and_replay(prepared_commit):
    s=prepared_commit;s.post_status['/api/users/0/items']=[401,200]
    def add_pdf(request):
        if request.method=='POST' and request.url.path=='/api/users/0/items':
            s.pdf_keys=s.pdf_file_keys=('OTHER001',)
    s.write_hook=add_pdf
    result=s.commit()
    assert result.outcome is Outcome.PDF_ALREADY_ATTACHED
    assert [r.url.path for r in posts(s)]==['/api/users/0/items']
    assert result.upload_stage is ZoteroUploadStage.NO_CONFIRMED_MUTATION and not result.mutation_uncertain


@pytest.mark.parametrize('boundary', ['authorization', 'replay'])
@pytest.mark.parametrize('change', ['modify', 'delete'])
def test_stale_artifact_at_remembered_401_guard_blocks_dialog_or_replay(prepared_commit, boundary, change):
    s = prepared_commit
    s.post_status['/api/users/0/items'] = [401, 200]
    endpoint = '/api/users/0/items' if boundary == 'authorization' else '/api/local/authorize'

    def change_before_guard(request):
        if request.method == 'POST' and request.url.path == endpoint:
            if change == 'modify':s.qualified.artifact.path.write_bytes(b'changed')
            else:s.qualified.artifact.path.unlink()

    s.write_hook = change_before_guard
    result = s.commit()
    assert result.outcome is Outcome.CONFLICT and result.upload_stage is ZoteroUploadStage.NO_CONFIRMED_MUTATION
    assert not result.mutation_uncertain
    assert [r.url.path for r in posts(s)] == ['/api/users/0/items'] + (
        ['/api/local/authorize'] if boundary == 'replay' else [])
    assert not s.pdf_keys and not s.pdf_file_keys


def test_one_time_401_cannot_authorize_or_replay_final_commit(prepared_commit):
    s=prepared_commit;s.remembered=False;s.authorize_remember=False
    s.authorize();s.requests.clear();s.post_status['/api/users/0/items']=[401,200]
    result=s.commit()
    assert result.outcome is Outcome.UPLOAD_FAILURE
    assert [r.url.path for r in posts(s)]==['/api/users/0/items']


@pytest.mark.parametrize('stage',[ZoteroUploadStage.CHILD_CREATED,ZoteroUploadStage.BYTES_UPLOADED])
def test_final_commit_later_401_preserves_partial_stage_without_replay(prepared_commit,stage):
    s=prepared_commit
    if stage is ZoteroUploadStage.BYTES_UPLOADED:
        s.protocol_full=True;s.post_status[f'/api/users/0/items/{CHILD}/file']=[200,401]
    else:s.post_status[f'/api/users/0/items/{CHILD}/file']=[401]
    result=s.commit()
    assert result.outcome is Outcome.UPLOAD_FAILURE and result.upload_stage is stage
    assert 'partial or uncertain' in result.message
    assert not any(r.url.path=='/api/local/authorize' for r in posts(s))
    assert len([r for r in posts(s) if r.url.path=='/api/users/0/items'])==1


def test_final_commit_missing_initial_authorization_never_prompts(prepared_commit):
    s=prepared_commit;s.remembered=False
    result=s.commit()
    assert result.outcome is Outcome.AUTH_REQUIRED and result.recovery is Recovery.ZOTERO_AUTHORIZATION
    assert posts(s)==[]
