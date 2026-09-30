"""A5 orchestration uses synthetic Paper state and real A2 with MockTransport."""

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from literature_monitor.application import acquisition as application
from literature_monitor.application.acquisition import AcquisitionService, AuthorizationRetryBoundary, AcquisitionOutcome as Outcome, AcquisitionRecovery as Recovery, AcquisitionStage as Stage
from literature_monitor.institutional_resolver import ResolverCandidate, ResolverOutcome, ResolverResult
from literature_monitor.markdown_state import parse_paper_state, serialize_document
from literature_monitor.materialize import render_paper_markdown
from literature_monitor.models import Author, CanonicalMetadata, CanonicalPaper, ExternalIds, MetadataSource, PaperVersion, VersionKind, VersionRef, Workflow, WorkflowStatus
from literature_monitor.pdf_acquisition import AcquiredPdf, PdfAcquisitionResult, PdfAcquisitionOutcome, PdfSourceKind
from literature_monitor.zotero_local import VerifiedZoteroItem, ZoteroIdentityResult, ZoteroAttachmentResult, ZoteroReadOutcome as Read
from literature_monitor.zotero_write import ZoteroWriteClient, ZoteroUploadStage

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
        inspect_calls=0, resolver_calls=0, acquirer_calls=0, writer_calls=0, files=[], resolver_hook=None, acquirer_hook=None,
        identity_hook=None, attachment_hook=None, write_hook=None, stage_hook=None, secret_error=None,
        resolver_outcome=ResolverOutcome.RESOLVED, pdf_outcome=PdfAcquisitionOutcome.ACQUIRED,
        post_status={}, post_counts={}, protocol_full=False, remembered=True, authorize_status=200)
    class Local:
        def __enter__(self): return self
        def __exit__(self,*args): s.local_closed=True
        def resolve_identity(self,doi,key=None):
            s.resolve_calls.append((doi,key))
            if s.identity_hook: s.identity_hook(len(s.resolve_calls))
            parent = s.identities.pop(0) if s.identities else s.parent
            return ZoteroIdentityResult(s.read_outcome,parent if s.read_outcome is Read.VERIFIED else None,SECRET)
        def inspect_attachments(self,parent):
            s.inspect_calls += 1
            if s.attachment_hook: s.attachment_hook(s.inspect_calls)
            return ZoteroAttachmentResult(s.attachment_outcome,None if s.unknown_attachments else bool(s.pdf_file_keys),SECRET,s.pdf_keys,s.pdf_file_keys)
    class Resolver:
        def resolve(self,doi):
            s.resolver_calls+=1
            assert doi==DOI
            if s.resolver_hook:s.resolver_hook()
            return ResolverResult(s.resolver_outcome,(ResolverCandidate('FullText',TARGET),))
    class Acquirer:
        def acquire(self,candidates):
            s.acquirer_calls+=1
            assert candidates==(ResolverCandidate('FullText',TARGET),)
            if s.acquirer_hook:s.acquirer_hook()
            if s.pdf_outcome is not PdfAcquisitionOutcome.ACQUIRED:
                return PdfAcquisitionResult(s.pdf_outcome)
            directory=tmp_path/f'pdf-attempt-{s.acquirer_calls}'
            directory.mkdir();file=directory/'validated.pdf';file.write_bytes(b'%PDF-fixture')
            s.files.append(file)
            return PdfAcquisitionResult(PdfAcquisitionOutcome.ACQUIRED,AcquiredPdf(file,PdfSourceKind.DIRECT_RESPONSE,1,'FullText',len(file.read_bytes()),directory))
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
            return httpx.Response(s.authorize_status,headers=dict(headers,**{'Retry-After':'30'}),json={'key':SECRET,'remember':True})
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
    def writer(parent):
        s.writer_calls+=1
        assert parent==PARENT
        if s.secret_error:raise s.secret_error
        return ZoteroWriteClient(parent,credential_store=Store(),transport=httpx.MockTransport(handler))
    monkeypatch.setattr(application,'ZoteroLocalClient',Local)
    monkeypatch.setattr(application,'ZoteroWriteClient',writer)
    s.resolver,s.pdf_acquirer=Resolver(),Acquirer()
    s.service=AcquisitionService(root,resolver=s.resolver,pdf_acquirer=s.pdf_acquirer)
    def progress(stage):
        s.stages.append(stage)
        if s.stage_hook:s.stage_hook(stage)
    s.run=lambda: s.service.acquire(ID,stage_callback=progress)
    return s


def posts(s):return [r for r in s.requests if r.method=='POST']


def assert_clean(s,result):
    assert all(not path.exists() for path in s.files)
    public=repr(result)+result.message
    assert SECRET not in public and TARGET not in public and 'TEST_UPLOAD' not in public
    assert not hasattr(result,'pdf_path') and not hasattr(result,'candidate')


def use_local_file_reads(s, monkeypatch, *, file_failure=None):
    """Exercise the real A1 reader against the same simulated A2 library."""
    from literature_monitor.zotero_local import ZoteroLocalClient

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
            headers['Location'] = 'file:///private/SENTINEL_FILE_PATH/paper.pdf' if key in s.pdf_file_keys else 'false'
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
    def raced_link(*args):
        assert state(s.path).zotero_key is None
        update(s.path,zotero_key=key)
        concurrent_bytes.append(s.path.read_bytes())
        linked = link(*args)
        assert linked.outcome is LinkageOutcome.ALREADY_LINKED
        return linked
    monkeypatch.setattr(application,'link_paper_to_zotero',raced_link)
    result = s.run()
    assert result.outcome is Outcome.CONFLICT and not result.linkage_completed
    assert s.path.read_bytes() == concurrent_bytes[0]
    assert s.inspect_calls == s.resolver_calls == s.acquirer_calls == s.writer_calls == 0
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
    assert s.resolver_calls == s.acquirer_calls == s.writer_calls == 0 and not posts(s)
    file_reads = [r for r in s.local_requests if r.url.path.endswith('/file')]
    assert len(file_reads) == len(metadata)
    assert 'SENTINEL_FILE_PATH' not in repr(result)


def test_metadata_only_child_enters_normal_acquisition_with_real_local_reader(scenario, monkeypatch):
    s = scenario;s.pdf_keys=('EMPTY001',)
    use_local_file_reads(s,monkeypatch)
    result = s.run()
    assert result.outcome is Outcome.SUCCEEDED
    assert s.resolver_calls == s.acquirer_calls == s.writer_calls == 1
    assert s.pdf_file_keys == (CHILD,) and 'EMPTY001' in s.pdf_keys
    assert_clean(s,result)


@pytest.mark.parametrize('status',[200,500])
def test_unknown_file_state_prevents_acquisition_with_real_local_reader(scenario, monkeypatch, status):
    s = scenario;s.pdf_keys=('EMPTY001',)
    use_local_file_reads(s,monkeypatch,file_failure=status)
    result = s.run()
    assert result.outcome is Outcome.ZOTERO_FAILURE
    assert s.resolver_calls == s.acquirer_calls == s.writer_calls == 0 and not posts(s)


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
    assert s.resolver_calls == s.acquirer_calls == s.writer_calls == 2
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
    assert s.stages==[Stage.LOCATING_ZOTERO,Stage.CHECKING_ATTACHMENT,Stage.RESOLVING,Stage.DISCOVERING_PDF,Stage.ATTACHING,Stage.SUCCEEDED]
    assert s.local_closed and s.writer_calls==1
    assert_clean(s,result)
    with pytest.raises(FrozenInstanceError):result.outcome=Outcome.CONFLICT


@pytest.mark.parametrize('key',[PARENT.key,'STALE001'])
def test_non_null_key_preserved_exactly_and_verified_fallback_used(scenario,key):
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
    assert not s.resolver_calls and not posts(s)


@pytest.mark.parametrize('change',[lambda s:s.path.unlink(),lambda s:update(s.path,status='kept'),lambda s:change_doi(s.path),lambda s:update(s.path,id=str(UUID(int=3)))])
def test_changes_during_identity_prevent_linkage_and_upload(scenario,change):
    s=scenario;s.identity_hook=lambda count:change(s) if count==1 else None
    result=s.run()
    assert result.outcome is Outcome.CONFLICT and not result.linkage_completed and not posts(s)
    assert not s.resolver_calls


def test_wrong_identity_doi_prevents_linkage(scenario):
    s=scenario;before=s.path.read_bytes();s.parent=VerifiedZoteroItem(PARENT.key,'10.5555/wrong',PARENT.server_id)
    result=s.run()
    assert result.outcome is Outcome.ZOTERO_FAILURE and s.path.read_bytes()==before and not posts(s)


def test_existing_pdf_short_circuits_before_browser_and_authorization(scenario):
    s=scenario;s.pdf_keys=s.pdf_file_keys=('EXTERNAL',)
    result=s.run()
    assert result.outcome is Outcome.PDF_ALREADY_ATTACHED and result.linkage_completed
    assert s.resolver_calls==s.acquirer_calls==s.writer_calls==0 and posts(s)==[]


@pytest.mark.parametrize('kind',['unknown','failure'])
def test_incomplete_attachment_read_is_not_absence(scenario,kind):
    s=scenario
    if kind=='unknown':s.unknown_attachments=True
    else:s.attachment_outcome=Read.INVALID_RESPONSE
    result=s.run()
    assert result.outcome is Outcome.ZOTERO_FAILURE and result.linkage_completed
    assert not s.resolver_calls and not posts(s)


@pytest.mark.parametrize('outcome,expected',[(ResolverOutcome.AUTH_REQUIRED,Outcome.AUTH_REQUIRED),
    (ResolverOutcome.NO_ELIGIBLE_CANDIDATES,Outcome.NO_ELIGIBLE_CANDIDATES),(ResolverOutcome.BROWSER_UNAVAILABLE,Outcome.BROWSER_UNAVAILABLE),
    (ResolverOutcome.API_FAILURE,Outcome.RESOLVER_FAILURE),(ResolverOutcome.INVALID_RESPONSE,Outcome.RESOLVER_FAILURE),(ResolverOutcome.INVALID_DOI,Outcome.RESOLVER_FAILURE)])
def test_resolver_results_preserve_independent_linkage(scenario,outcome,expected):
    s=scenario;s.resolver_outcome=outcome
    result=s.run()
    assert result.outcome is expected and result.linkage_completed and state(s.path).zotero_key==PARENT.key
    assert not posts(s) and not s.acquirer_calls
    assert s.stages[-1] is (Stage.WAITING_FOR_INSTITUTION_AUTH if outcome is ResolverOutcome.AUTH_REQUIRED else Stage.FAILED)


@pytest.mark.parametrize('outcome,expected',[(PdfAcquisitionOutcome.AUTH_REQUIRED,Outcome.AUTH_REQUIRED),
    (PdfAcquisitionOutcome.NO_VALID_PDF,Outcome.NO_VALID_PDF),(PdfAcquisitionOutcome.BROWSER_UNAVAILABLE,Outcome.BROWSER_UNAVAILABLE),
    (PdfAcquisitionOutcome.API_FAILURE,Outcome.NO_VALID_PDF),(PdfAcquisitionOutcome.INVALID_RESPONSE,Outcome.NO_VALID_PDF)])
def test_acquirer_failures_do_not_authorize(scenario,outcome,expected):
    s=scenario;s.pdf_outcome=outcome
    result=s.run()
    assert result.outcome is expected and not posts(s)
    assert result.linkage_completed and state(s.path).status is WorkflowStatus.IN_ZOTERO
    assert_clean(s,result)


@pytest.mark.parametrize('boundary',['resolver','acquirer','attaching'])
@pytest.mark.parametrize('change',[lambda s:update(s.path,status='kept'),lambda s:change_doi(s.path),lambda s:s.path.unlink()])
def test_current_disk_revalidation_before_write(scenario,boundary,change):
    s=scenario
    if boundary=='resolver':s.resolver_hook=lambda:change(s)
    elif boundary=='acquirer':s.acquirer_hook=lambda:change(s)
    else:s.stage_hook=lambda stage:change(s) if stage is Stage.ATTACHING else None
    result=s.run()
    assert result.outcome is Outcome.CONFLICT and not posts(s)
    assert_clean(s,result)


@pytest.mark.parametrize('change',['parent','server'])
def test_parent_identity_revalidation_before_upload(scenario,change):
    s=scenario
    s.acquirer_hook=lambda:setattr(s,'parent',VerifiedZoteroItem('OTHER001' if change=='parent' else PARENT.key,DOI,'other-instance' if change=='server' else PARENT.server_id))
    result=s.run()
    assert result.outcome is Outcome.CONFLICT and not posts(s)
    assert_clean(s,result)


@pytest.mark.parametrize('boundary',['resolver','acquirer'])
def test_external_pdf_appeared_short_circuits_upload_and_cleans_temp(scenario,boundary):
    s=scenario
    def hook():
        s.pdf_keys=s.pdf_file_keys=('EXTERNAL',)
    if boundary=='resolver':s.resolver_hook=hook
    else:s.acquirer_hook=hook
    result=s.run()
    assert result.outcome is Outcome.PDF_ALREADY_ATTACHED and not posts(s)
    assert_clean(s,result)


@pytest.mark.parametrize('status',[403,429])
def test_authorization_denied_or_rate_limited_cleanup(scenario,status):
    s=scenario;s.remembered=False;s.authorize_status=status
    result=s.run()
    assert result.outcome is Outcome.UPLOAD_FAILURE
    assert len(posts(s))==1 and posts(s)[0].url.path=='/api/local/authorize'
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


@pytest.mark.parametrize('change',['status','doi','parent','server','attachment','missing'])
def test_401_guard_prevents_new_authorization_or_replay(scenario,change):
    s=scenario;s.post_status['/api/users/0/items']=[401]
    def changed(request):
        if request.method!='POST' or request.url.path!='/api/users/0/items':return
        if change=='status':update(s.path,status='kept')
        elif change=='doi':change_doi(s.path)
        elif change=='parent':s.parent=VerifiedZoteroItem('OTHER001',DOI,PARENT.server_id)
        elif change=='server':s.parent=VerifiedZoteroItem(PARENT.key,DOI,'other-instance')
        elif change=='attachment':s.pdf_keys=s.pdf_file_keys=('EXTERNAL',)
        else:s.path.unlink()
    s.write_hook=changed
    result=s.run()
    assert result.outcome is (Outcome.PDF_ALREADY_ATTACHED if change=='attachment' else Outcome.CONFLICT)
    assert len(posts(s))==1 and posts(s)[0].url.path=='/api/users/0/items'
    assert_clean(s,result)


def test_state_changed_during_fresh_authorization_blocks_replay(scenario):
    s=scenario;s.post_status['/api/users/0/items']=[401]
    s.write_hook=lambda request:change_doi(s.path) if request.url.path=='/api/local/authorize' else None
    result=s.run()
    assert result.outcome is Outcome.CONFLICT
    assert [r.url.path for r in posts(s)]==['/api/users/0/items','/api/local/authorize']
    assert_clean(s,result)


@pytest.mark.parametrize('phase',['create','prepare','register'])
def test_safe_401_retry_ignores_only_own_child_and_preserves_stage(scenario,phase):
    s=scenario;s.protocol_full=phase=='register'
    endpoint='/api/users/0/items' if phase=='create' else f'/api/users/0/items/{CHILD}/file'
    s.post_status[endpoint]=([200,401] if phase=='register' else [401])
    result=s.run()
    assert result.outcome is Outcome.SUCCEEDED
    assert len([r for r in posts(s) if r.url.path=='/api/local/authorize'])==1
    creates=[r for r in posts(s) if r.url.path=='/api/users/0/items']
    assert len(creates)==(2 if phase=='create' else 1)
    if phase=='create':assert creates[0].headers['Zotero-Write-Token']==creates[1].headers['Zotero-Write-Token']
    assert_clean(s,result)


@pytest.mark.parametrize('change',['external_pdf','own_removed','paper_status'])
def test_retry_after_child_creation_fails_truthfully_when_state_changes(scenario,change):
    s=scenario;s.post_status[f'/api/users/0/items/{CHILD}/file']=[401]
    def changed(request):
        if request.method=='POST' and request.url.path.endswith('/file'):
            if change=='external_pdf':s.pdf_keys=(CHILD,'EXTERNAL');s.pdf_file_keys=('EXTERNAL',)
            elif change=='own_removed':s.pdf_keys=()
            else:update(s.path,status='kept')
    s.write_hook=changed
    result=s.run()
    assert result.outcome is Outcome.CONFLICT and result.upload_stage is ZoteroUploadStage.CHILD_CREATED
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


@pytest.mark.parametrize('change',['status','doi','server'])
def test_initial_authorization_wait_requires_current_state_before_create(scenario,change):
    s=scenario;s.remembered=False
    def changed(request):
        if request.url.path!='/api/local/authorize':return
        if change=='status':update(s.path,status='kept')
        elif change=='doi':change_doi(s.path)
        else:s.parent=VerifiedZoteroItem(PARENT.key,DOI,'other-instance')
    s.write_hook=changed
    result=s.run()
    assert result.outcome is Outcome.CONFLICT
    assert [r.url.path for r in posts(s)]==['/api/local/authorize']
    assert_clean(s,result)


def test_paper_changed_during_attachment_recheck_is_conflict_even_when_pdf_appeared(scenario):
    s=scenario
    def changed(count):
        if count==2:
            update(s.path,status='kept');s.pdf_keys=s.pdf_file_keys=('EXTERNAL',)
    s.attachment_hook=changed
    result=s.run()
    assert result.outcome is Outcome.CONFLICT and posts(s)==[]
    assert_clean(s,result)


@pytest.mark.parametrize('change',['external_pdf','own_removed','paper_status','doi','server'])
def test_register_401_guard_preserves_bytes_uploaded_and_blocks_dialog(scenario,change):
    s=scenario;s.protocol_full=True;s.post_status[f'/api/users/0/items/{CHILD}/file']=[200,401]
    def changed(request):
        if request.method!='POST' or b'upload=' not in request.content:return
        if change=='external_pdf':s.pdf_keys=(CHILD,'EXTERNAL');s.pdf_file_keys=('EXTERNAL',)
        elif change=='own_removed':s.pdf_keys=()
        elif change=='paper_status':update(s.path,status='kept')
        elif change=='doi':change_doi(s.path)
        else:s.parent=VerifiedZoteroItem(PARENT.key,DOI,'other-instance')
    s.write_hook=changed
    result=s.run()
    assert result.outcome is Outcome.CONFLICT and result.upload_stage is ZoteroUploadStage.BYTES_UPLOADED
    assert all(r.url.path!='/api/local/authorize' for r in posts(s))
    assert len([r for r in posts(s) if b'upload=' in r.content])==1
    assert_clean(s,result)


def test_cancelled_upload_removes_attempt_file(scenario):
    s=scenario
    s.write_hook=lambda request:(_ for _ in ()).throw(KeyboardInterrupt(SECRET)) if request.url.path=='/api/users/0/items' else None
    with pytest.raises(KeyboardInterrupt):s.run()
    assert all(not path.exists() for path in s.files)
    assert state(s.path).zotero_key==PARENT.key


def test_unexpected_resolver_error_keeps_completed_linkage_and_sanitizes(scenario):
    s=scenario
    s.resolver_hook=lambda:(_ for _ in ()).throw(RuntimeError(SECRET+' '+TARGET))
    result=s.run()
    assert result.outcome is Outcome.INTERNAL_FAILURE and result.linkage_completed
    assert state(s.path).status is WorkflowStatus.IN_ZOTERO
    assert_clean(s,result)


def test_authorization_retry_boundary_does_not_block_existing_pdf_noop(scenario):
    s=scenario;s.remembered=False;s.authorize_status=429
    assert s.run().recovery is Recovery.RATE_LIMITED
    s.pdf_keys=s.pdf_file_keys=('EXTERNAL',)
    previous=len(s.requests)
    result=s.run()
    assert result.outcome is Outcome.PDF_ALREADY_ATTACHED and len(s.requests)==previous
    assert_clean(s,result)


def test_malformed_acquirer_failure_with_handle_still_cleans_owned_file(scenario):
    s=scenario
    original=s.service._pdf_acquirer.acquire
    def malformed(candidates):
        result=original(candidates)
        return PdfAcquisitionResult(PdfAcquisitionOutcome.INVALID_RESPONSE,result.pdf)
    s.service._pdf_acquirer.acquire=malformed
    result=s.run()
    assert result.outcome is Outcome.NO_VALID_PDF and not posts(s)
    assert_clean(s,result)


def test_unsafe_papers_directory_blocks_all_external_work(scenario,tmp_path):
    s=scenario
    original=s.root/'Papers';outside=tmp_path/'outside-papers';original.rename(outside);original.symlink_to(outside,target_is_directory=True)
    result=s.run()
    assert result.outcome is Outcome.INELIGIBLE and not s.resolve_calls and not posts(s)


def test_linkage_failure_stops_attempt_without_other_paper_changes(scenario,monkeypatch):
    from literature_monitor.application.zotero_linkage import LinkageResult,LinkageOutcome
    s=scenario;before=s.path.read_bytes()
    monkeypatch.setattr(application,'link_paper_to_zotero',lambda *args:LinkageResult(LinkageOutcome.STATE_CONFLICT,ID,s.path,SECRET))
    result=s.run()
    assert result.outcome is Outcome.CONFLICT and not result.linkage_completed
    assert s.path.read_bytes()==before and not s.resolver_calls and not posts(s)
    assert_clean(s,result)


def test_attachment_recheck_failure_cleans_acquired_file_before_authorization(scenario):
    s=scenario
    s.acquirer_hook=lambda:setattr(s,'attachment_outcome',Read.INVALID_RESPONSE)
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
        return AcquisitionService(s.root,resolver=s.resolver,pdf_acquirer=s.pdf_acquirer,
            authorization_retry_boundary=boundary)
    s.service=service();s.remembered=False;s.authorize_status=429
    first=s.run()
    assert first.recovery is Recovery.RATE_LIMITED and boundary.remaining()==30
    previous=(s.resolver_calls,s.acquirer_calls,s.writer_calls,len(s.requests))
    now[0]=110
    second=service().acquire(ID)
    assert second.recovery is Recovery.RATE_LIMITED and second.retry_after_seconds==20
    assert (s.resolver_calls,s.acquirer_calls,s.writer_calls,len(s.requests))==previous
    now[0]=130;s.authorize_status=200
    third=service().acquire(ID)
    assert third.outcome is Outcome.SUCCEEDED and boundary.remaining()==0
    assert len([r for r in posts(s) if r.url.path=='/api/local/authorize'])==2
    assert_clean(s,first);assert_clean(s,second);assert_clean(s,third)


def test_default_authorization_boundaries_are_independent(scenario):
    s=scenario;s.remembered=False;s.authorize_status=429
    assert s.run().recovery is Recovery.RATE_LIMITED
    s.authorize_status=200
    separate=AcquisitionService(s.root,resolver=s.resolver,pdf_acquirer=s.pdf_acquirer)
    assert separate.acquire(ID).outcome is Outcome.SUCCEEDED
