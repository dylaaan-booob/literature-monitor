"""Journal Run behavior through the production pipeline, using isolated Providers."""
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient

from literature_monitor.application import monitor
from literature_monitor.application.run_state import read_last_run_snapshot, LastRunReadStatus
from literature_monitor.web.app import create_app
from test_application_state_migration import Execution, cr_record, oa_work


@pytest.fixture
def execution(tmp_path, monkeypatch):
    return Execution(tmp_path, monkeypatch)


def install_core_mocks(monkeypatch):
    # Used by the existing Reset rebuild regression without changing its target/config.
    from contextlib import nullcontext
    from literature_monitor.crossref import CrossrefClient
    from literature_monitor.openalex import OpenAlexWorkRecord, OpenAlexMetadata, ResolvedSource, DiscoveryResult, SourceResolutionUnit
    from literature_monitor.models import ExternalIds, MetadataSource, Author
    from literature_monitor.coverage import CoverageStatus
    from datetime import datetime, timezone
    import httpx
    source = ResolvedSource('0006-341X','https://openalex.org/S1','Biometrics',('0006-341X',),'0006-341X')
    record = OpenAlexWorkRecord(metadata=OpenAlexMetadata(title='statistics causal',journal='Biometrics'),
        external_ids=ExternalIds(doi='10.5555/a',openalex='https://openalex.org/W1'),authors=(Author(name='Ada Author'),),
        source_id=source.openalex_id,provenance=MetadataSource(provider='openalex',record_id='https://openalex.org/W1',retrieved_at=datetime.now(timezone.utc)))
    def response(request):
        return httpx.Response(200,json={'status':'ok','message-type':'work-list','message':{'items':[cr_record(title='statistics causal')],'total-results':1}})
    monkeypatch.setattr(monitor,'OpenAlexClient',lambda **kw:nullcontext(object()))
    monkeypatch.setattr(monitor,'CrossrefClient',lambda **kw:CrossrefClient(transport=httpx.MockTransport(response),sleep=lambda _:None))
    monkeypatch.setattr(monitor,'resolve_journal_sources_batched',lambda client,journals,**kw:(SourceResolutionUnit(journals[0],source,(),CoverageStatus.COMPLETE),))
    monkeypatch.setattr(monitor,'discover_resolved_sources',lambda *a,**kw:DiscoveryResult((source,),(record,),()))


@pytest.mark.parametrize('status',['candidate','kept','rejected','exported'])
def test_repeat_skips_metadata_matching_and_preserves_notes(execution,monkeypatch,status):
    import literature_monitor.materialize as materialize
    e=execution
    assert e.run().created_papers == 1
    paper,= (e.output/'Papers').glob('*.md')
    paper.write_text(paper.read_text().replace('status: candidate',f'status: {status}')+'\nUser notes.\n')
    before={p:p.read_bytes() for p in e.output.rglob('*.md')}
    e.records['10.5555/a']['indexed']['date-time']='2026-03-01T00:00:00Z'
    for module,name in ((monitor,'match_searchable_projections'),(monitor,'canonicalize_records'),(materialize,'_scan_authors')):
        monkeypatch.setattr(module,name,lambda *a,**kw:pytest.fail('saved DOI entered new-paper processing'))
    result=e.run()
    assert (result.created_papers,result.matched_existing_papers)==(0,1)
    assert len(e.cr_transports[-1].requests)==1 and not e.full_requests
    assert before=={p:p.read_bytes() for p in e.output.rglob('*.md')}
    assert read_last_run_snapshot(e.output).status is LastRunReadStatus.AVAILABLE


def test_missing_indexed_does_not_split_and_repeat_reduces_requests(execution,monkeypatch):
    import literature_monitor.materialize as materialize
    e=execution;e.records['10.5555/a'].pop('indexed')
    canonical=Mock(wraps=monitor.canonicalize_records);authors=Mock(wraps=materialize._scan_authors)
    monkeypatch.setattr(monitor,'canonicalize_records',canonical);monkeypatch.setattr(materialize,'_scan_authors',authors)
    first=e.run();before=(len(e.cr_transports[-1].requests),canonical.call_count,authors.call_count)
    canonical.reset_mock();authors.reset_mock()
    second=e.run();after=(len(e.cr_transports[-1].requests),canonical.call_count,authors.call_count)
    assert (first.created_papers,second.matched_existing_papers)==(1,1)
    assert before==(1,1,1) and after==(1,0,0)
    assert all(unit.status.value=='COMPLETE' for unit in second.coverage)


def test_abstract_only_crossref_match_field_priority_and_optional_authors(execution):
    from literature_monitor.markdown_state import parse_paper_state
    e=execution;e.works[0]['title']='OA display title';e.works[0]['authorships']=[]
    record=e.records['10.5555/a'];record['title']=['Crossref title'];record['author']=[]
    record.pop('published');record['abstract']='<jats:p>statistics exclusive abstract</jats:p>'
    e.works[0].pop('publication_date')
    result=e.run();assert result.created_papers==1
    path,=(e.output/'Papers').glob('*.md')
    paper=parse_paper_state(path,path.read_text(),e.output/'Authors')
    assert paper is not None and paper.title=='Crossref title' and not paper.author_links
    assert not list((e.output/'Authors').glob('*.md'))
    assert paper.publication_date is None
    with TestClient(create_app(e.config),base_url='http://localhost') as client:
        assert client.get('/').status_code==200
        response=client.post(f'/api/ui-next/papers/{paper.paper_id}/keep',json={
            'csrf_token':client.app.state.csrf_token,'expected_status':'candidate',
        })
        assert response.status_code==200
        assert response.json()['outcome']=='UPDATED'
        assert 'status: kept' in path.read_text()


def test_manifest_complete_metadata_failure_is_not_a_normal_nonmatch(execution):
    e=execution;e.failed_dois.add('10.5555/a');e.works[0]["title"]=None
    result=e.run()
    assert result.created_papers==0 and result.statistics.metadata_failed==1
    assert next(unit for unit in result.coverage if unit.component.value=='crossref_discovery').status.value=='COMPLETE'
    assert result.errors


@pytest.mark.parametrize('kind',['journal-issue','posted-content',None])
def test_only_explicit_journal_issue_is_excluded_across_paths(execution,kind):
    e=execution;e.records['10.5555/a']['type']=kind
    result=e.run()
    assert result.created_papers==int(kind!='journal-issue')
    if kind=='journal-issue':assert not e.full_requests


def test_prime_alias_saved_skips_prime_metadata(execution):
    e=execution;assert e.run().created_papers==1
    e.members=[];e.aliases={'10.5555/alias':'10.5555/a'};e.works=[oa_work(doi='10.5555/alias')]
    result=e.run()
    assert (result.created_papers,result.matched_existing_papers)==(0,1)
    assert not any(r.url.path == '/v1/works' for r in e.full_requests)
    assert not any('doi:10.5555/a' in r.url.params.get('filter','').split(',') for r in e.cr_transports[-1].requests)


@pytest.mark.parametrize("in_manifest",[False,True])
def test_prime_alias_new_creates_one_final_identity(execution,in_manifest):
    e=execution;e.members=e.members if in_manifest else [];e.aliases={'10.5555/alias':'10.5555/a'}
    e.works=[oa_work(doi='10.5555/alias'),oa_work(2)]
    result=e.run()
    assert result.created_papers==1
    path,=(e.output/'Papers').glob('*.md')
    assert 'doi: 10.5555/a' in path.read_text()
    if in_manifest:
        prime_queries=[r for r in e.cr_transports[-1].requests if 'doi:10.5555/a' in r.url.params.get('filter','').split(',')]
        assert not prime_queries


def test_local_identity_conflict_does_not_block_other_new_doi(execution):
    e=execution;assert e.run().created_papers==1
    paper,=(e.output/'Papers').glob('*.md');(paper.parent/'duplicate.md').write_bytes(paper.read_bytes())
    e.works.append(oa_work(2,'10.5555/b'));e.records['10.5555/b']=cr_record('10.5555/b');e.members.append('10.5555/b')
    result=e.run()
    assert result.created_papers==1 and result.matched_existing_papers==0 and result.errors


def test_run_configuration_is_frozen_before_provider_work(execution):
    e=execution
    def edit(event):
        if event.stage is monitor.ProgressStage.CHECKING_MONITOR:
            e.config.write_text(e.config.read_text().replace('2026-01','2026-02').replace('output_dir: workspace','output_dir: other'))
    result=monitor.run_monitor(e.config,progress_callback=edit)
    assert result.created_papers==1 and result.resolved_date_range.from_date.month==1
    assert not (e.config.parent/'other').exists()


def test_crossref_manifest_attribution_survives_sparse_full_issns(execution,monkeypatch):
    from copy import deepcopy
    e=execution;e.works=[]
    original=e.route_cr
    def response(request):
        result=original(request)
        if request.url.path=='/v1/works' and 'select' not in request.url.params:
            result=deepcopy(result)
            for record in result['message']['items']:record.pop('ISSN',None)
        return result
    monkeypatch.setattr(e,'route_cr',response)
    assert e.run().created_papers==1


@pytest.mark.parametrize("title,abstract,crossref_abstract,oa_present,created,full", [
    ("Unrelated biology", "unrelated biology", "statistics", True, 0, 0),
    ("statistics study", None, None, True, 1, 0),
    ("Unrelated biology", None, "statistics methods", True, 1, 1),
    (None, "unrelated biology", None, True, 1, 1),
    ("Unrelated biology", None, None, True, 0, 1),
    (None, None, "statistics methods", False, 1, 1),
])
def test_early_matching_and_missing_field_fallback(execution, title, abstract, crossref_abstract, oa_present, created, full):
    e = execution
    e.works[0]['title'] = title
    if abstract:
        e.works[0]['abstract_inverted_index'] = {word: [i] for i, word in enumerate(abstract.split())}
    if not oa_present:
        e.works = []
    e.records['10.5555/a']['title'] = ['statistics Crossref title']
    if crossref_abstract:
        e.records['10.5555/a']['abstract'] = crossref_abstract
    events = []
    result = monitor.run_monitor(e.config, progress_callback=events.append)
    assert result.created_papers == created and not result.errors
    assert len(e.full_requests) == full
    assert result.statistics.metadata_success == full and result.statistics.metadata_failed == 0
    assert all(unit.status.value == 'COMPLETE' for unit in result.coverage)
    from literature_monitor.progress import PROGRESS_STAGES
    assert tuple(event.stage for event in events if event.stage) == PROGRESS_STAGES
    if not full:
        assert not e.path.exists() and result.state_usage == monitor.ProviderStateUsage()


def test_openalex_only_match_uses_identity_probe_without_full_hydration(execution):
    e = execution
    e.members = []
    result = e.run()
    assert result.created_papers == 1 and not result.errors
    assert not e.full_requests and result.statistics.metadata_success == result.statistics.metadata_failed == 0
    assert all(unit.status.value == 'COMPLETE' for unit in result.coverage)
    assert result.state_usage == monitor.ProviderStateUsage() and not e.path.exists()


def test_not_configuration_stops_before_provider_or_workspace_work(execution):
    e = execution
    e.config.write_text(e.config.read_text().replace('keyword_expression: statistics', 'keyword_expression: statistics AND NOT biology'))
    result = e.run()
    assert result.outcome is monitor.RunOutcome.INVALID_CONFIGURATION
    assert any('NOT operator is no longer supported' in issue.message for issue in result.errors)
    assert not e.oa_transports and not e.cr_transports and not e.output.exists()


def test_alias_cannot_resurrect_a_rejected_prime(execution):
    e = execution
    e.works = [oa_work(doi='10.5555/alias')]
    e.aliases = {'10.5555/alias': '10.5555/a'}
    e.records['10.5555/a']['title'] = ['Unrelated biology']
    result = e.run()
    assert result.created_papers == 0 and not result.errors
    assert len(e.full_requests) == 2  # One required fallback batch and the alias singleton.
    assert result.statistics.metadata_success == 1


def test_matched_alias_shares_earlier_prime_metadata(execution):
    e = execution
    e.members = []
    e.works[0]['title'] = None
    e.works.append(oa_work(2, '10.5555/alias'))
    e.aliases = {'10.5555/alias': '10.5555/a'}
    result = e.run()
    assert result.created_papers == 1 and not result.errors
    prime = [r for r in e.cr_transports[-1].requests if r.url.params.get('filter') == 'doi:10.5555/a']
    assert len(prime) == 2 and sum('select' not in r.url.params for r in prime) == 1
    assert result.statistics.metadata_success == 1


def test_failed_identity_request_blocks_capture_without_fake_metadata_failure(execution, monkeypatch):
    import httpx
    e = execution
    e.members = []
    e.records = {}
    original = e.route_cr
    monkeypatch.setattr(e, 'route_cr', lambda request: httpx.Response(403)
                        if request.url.path != '/v1/works' else original(request))
    result = e.run()
    assert result.created_papers == 0 and result.errors
    assert result.statistics.metadata_failed == 0
    assert any(unit.status.value == 'FAILED' for unit in result.coverage if unit.component.value == 'crossref_supplement')
