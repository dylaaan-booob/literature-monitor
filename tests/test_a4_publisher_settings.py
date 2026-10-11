"""A4 storage, metadata, Settings Save and explicit legacy migration guard."""
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from literature_monitor.config import (
    JournalConfig, PublisherConfig, LegacyJournal, ConfigurationError,
    render_settings_list_text, parse_publisher_whitelist_text, validate_publisher_configs,
)
from literature_monitor.openalex import OpenAlexClient, OpenAlexRecordError, resolve_publisher_metadata
from literature_monitor.url_safety import normalize_public_http_url
from literature_monitor.application.settings import load_settings, save_settings, SettingsSaveOutcome
from literature_monitor.web.app import create_app
from test_application_settings import write_valid_settings_files
from test_web_run_settings import browser_settings_submission

A, B, X = '0006-341X', '0090-5364', '1541-0420'
P1, P2 = 'https://openalex.org/P1', 'https://openalex.org/P2'


def setup(tmp_path, *, url='https://custom.example/login'):
    config, path = write_valid_settings_files(tmp_path)
    journals = (JournalConfig(name='Canonical A', issn_l=A, publisher_id=P1, group='Stats'),)
    publishers = (PublisherConfig(name='Publisher A', publisher_id=P1, publisher_url=url),)
    path.write_text(render_settings_list_text(path.read_text(), journals, publishers, path=path))
    return config, path, load_settings(config).draft


def source(*, aliases=(B,), candidate=B, publisher=P2, sid='S2'):
    return dict(id='https://openalex.org/'+sid, display_name='Canonical Source', type='journal',
                issn=list(aliases), issn_l=candidate, host_organization=publisher,
                homepage_url='https://source-homepage.example')


def pub(identity=P2, *, name='Publisher B', url='https://publisher.example'):
    return dict(id=identity, display_name=name, homepage_url=url)


def provider(*, sources=None, publishers=None, fail=None, during=None):
    calls=[]
    def request(req):
        calls.append(req.url.path)
        if during: during(req)
        if fail == 'timeout': raise httpx.ReadTimeout('test timeout', request=req)
        if fail and fail == req.url.path: return httpx.Response(429)
        rows = (sources or []) if req.url.path == '/sources' else (publishers or [])
        if req.url.path.startswith('/sources/'): return httpx.Response(404)
        return httpx.Response(200, json={'meta':{'count':len(rows)}, 'results':rows})
    return OpenAlexClient(transport=httpx.MockTransport(request), sleep=lambda _:None),calls


@pytest.mark.parametrize('url', ['file:///tmp/x', '/relative', 'https://user:pwd@example.org',
    'https://localhost/x','http://x.localhost','https://127.0.0.1','http://10.0.0.1',
    'http://[::1]', 'http://[fc00::1]', 'http://192.0.2.1', 'https://example.org:bad',
    'https://', 'https://exa mple.org', 'https://example.org\\path', 'https://example.org\n',
    'https://example.org/\x00', 'http://[broken]', 'https://example.org:65536', '\n'])
def test_unsafe_urls_rejected(url):
    with pytest.raises(ValueError): normalize_public_http_url(url)
    with pytest.raises(ValueError): PublisherConfig(name='Pub', publisher_id=P1, publisher_url=url)


@pytest.mark.parametrize('url', ['', '   ', None, 'http://example.org', 'https://8.8.8.8:443/a', 'https://[2606:4700::1111]/'])
def test_safe_or_blank_urls(url):
    assert normalize_public_http_url(url) == (url if url and url.strip() else None)


@pytest.mark.parametrize('url', ['https://example.org/login', '', None])
def test_legacy_access_url_input_alias_maps_to_canonical_field_and_serialization(url):
    legacy = PublisherConfig(name='Publisher', publisher_id=P1, access_url=url)
    canonical = PublisherConfig(name='Publisher', publisher_id=P1, publisher_url=url)
    assert legacy == canonical
    assert legacy.publisher_url == (url or None)
    expected = {'publisher_id': P1, 'name': 'Publisher', 'publisher_url': url or None, 'access_service_id': None}
    assert legacy.model_dump() == legacy.model_dump(by_alias=True) == expected
    assert PublisherConfig.model_validate_json(legacy.model_dump_json()) == canonical


@pytest.mark.parametrize('url', ['http://localhost/private', 'https://user:pwd@example.org'])
def test_legacy_access_url_input_alias_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        PublisherConfig(name='Publisher', publisher_id=P1, access_url=url)
    with pytest.raises(ValueError):
        PublisherConfig(name='Publisher', publisher_id=P1, publisher_url=url)


def test_storage_roundtrip_duplicate_names_ids_and_consistency(tmp_path):
    original='# Heading\n\n## Journals\n\nold\n\n## Conferences\n\nkeep exact\n\n## Notes\n\nuser text\n'
    js=(JournalConfig(name='Same journal',issn_l=A,publisher_id=P1), JournalConfig(name='Same journal',issn_l=B,publisher_id=P2))
    ps=(PublisherConfig(name='Same publisher',publisher_id=P1),PublisherConfig(name='Same publisher',publisher_id=P2,publisher_url='https://example.org'))
    target=render_settings_list_text(original,js,ps,path=tmp_path/'list.md')
    assert parse_publisher_whitelist_text(target,path=tmp_path/'list.md') == ps
    assert target.split('## Conferences')[1] == original.split('## Conferences')[1]
    assert render_settings_list_text(target,js,ps,path=tmp_path/'list.md') == target
    with pytest.raises(ConfigurationError): validate_publisher_configs((ps[0],ps[0]))
    with pytest.raises(ConfigurationError): render_settings_list_text(target,js,ps[:1],path=tmp_path/'list.md')
    with pytest.raises(ConfigurationError): render_settings_list_text(target,js,(ps[0].model_copy(update={'name':'bad|cell'}),ps[1]),path=tmp_path/'list.md')
    with pytest.raises(ConfigurationError): parse_publisher_whitelist_text(target.replace('| Same publisher | '+P2,'| Same publisher | '+P1),path=tmp_path/'list.md')


@pytest.mark.parametrize('rows', [[pub()], [pub(),pub()], [pub(P1)], [pub(name='')], [pub(name='bad\nname')], [{'id':'bad','display_name':'Pub'}], [None]])
def test_strict_publisher_results(rows):
    client,_=provider(publishers=rows)
    with client, pytest.raises(OpenAlexRecordError): resolve_publisher_metadata(client,(P1,P2))


@pytest.mark.parametrize('homepage', [None,'http://127.0.0.1', 'https://publisher.example'])
def test_publisher_batch_dedup_and_homepage_policy(homepage):
    seen=[]
    def check(req):
        seen.append(dict(req.url.params))
        assert req.url.params['filter'] == 'ids.openalex:P1|P2'
        return httpx.Response(200,json={'meta':{'count':2},'results':[pub(P2,name='Same',url=homepage),pub(P1,name='Same',url=None)]})
    with OpenAlexClient(transport=httpx.MockTransport(check)) as client:
        rows=resolve_publisher_metadata(client,('P1',P2,P1))
    assert len(seen)==1 and [r.publisher_id for r in rows]==[P1,P2]
    assert rows[0].homepage_url is None
    assert rows[1].homepage_url == (homepage if homepage and '127.' not in homepage else None)


@pytest.mark.parametrize('change', ['group','reorder','remove','keyword','date','output','name','log','access','clear'])
def test_ordinary_save_offline(tmp_path,change):
    config,path,draft=setup(tmp_path)
    extra=JournalConfig(name='Null publisher',issn_l=B)
    path.write_text(render_settings_list_text(path.read_text(),draft.journals+(extra,),draft.publishers,path=path)); draft=load_settings(config).draft
    updates={}
    if change=='group': updates['journals']=(draft.journals[0].model_copy(update={'group':'Other'}),extra)
    elif change=='reorder': updates['journals']=tuple(reversed(draft.journals))
    elif change=='remove': updates['journals']=(extra,)
    elif change=='keyword': updates['keyword_expression']='statistics'
    elif change=='date': updates['date_spec']=replace(draft.date_spec,window_days=30)
    elif change=='output': updates['output_dir']=Path('other')
    elif change=='name': updates['name']='Changed'
    elif change=='log': updates['log_level']='DEBUG'
    else: updates['publishers']=(draft.publishers[0].model_copy(update={'publisher_url':None if change=='clear' else 'https://new.example'}),)
    def forbidden(req): pytest.fail('ordinary Save must perform no HTTP request')
    with OpenAlexClient(transport=httpx.MockTransport(forbidden)) as client:
        saved=save_settings(config,replace(draft,**updates),client=client)
    if change == 'remove':
        # Removing the last association would discard a manually owned Publisher URL.
        assert saved.outcome is SettingsSaveOutcome.INVALID_DRAFT
        assert 'Preview/Confirm' in saved.issues[0].message
        return
    assert saved.outcome is SettingsSaveOutcome.SAVED
    if change=='remove': assert saved.state.draft.publishers == ()
    elif change=='clear': assert saved.state.draft.publishers[0].publisher_url is None
    elif change!='access': assert saved.state.draft.publishers[0].publisher_url=='https://custom.example/login'


@pytest.mark.parametrize('direct', [P1,P2,None])
@pytest.mark.parametrize('homepage', [None,'https://publisher.example','http://localhost'])
def test_new_identity_canonicalizes_and_preserves_existing_url(tmp_path,direct,homepage):
    config,path,draft=setup(tmp_path,url=None)
    new=JournalConfig(name='Arbitrary hint',issn_l=B,publisher_id='P999',group='Methods')
    client,calls=provider(sources=[source(aliases=(A,B),candidate=A,publisher=direct)],publishers=[pub(url=homepage)])
    with client: saved=save_settings(config,replace(draft,journals=draft.journals+(new,)),client=client)
    assert saved.outcome is SettingsSaveOutcome.SAVED, saved.issues
    j=saved.state.draft.journals[-1]
    assert (j.issn_l,j.name,j.publisher_id,j.group)==(B,'Canonical Source',direct,'Methods')
    assert saved.state.draft.publishers[0].publisher_url is None
    assert calls == (['/sources','/publishers'] if direct==P2 else ['/sources'])
    if direct==P2: assert saved.state.draft.publishers[-1].publisher_url == (homepage if homepage and 'localhost' not in homepage else None)
    assert 'source-homepage' not in path.read_text()


@pytest.mark.parametrize('failure', ['source','publisher','timeout','missing','ambiguous','invalid','alias'])
def test_resolution_failure_zero_writes(tmp_path,failure):
    config,path,draft=setup(tmp_path); before=config.read_bytes(),path.read_bytes()
    sources=[source()]
    if failure=='missing': sources=[]
    if failure=='ambiguous': sources += [source(sid='S3')]
    if failure=='invalid': sources[0]['id']='invalid'
    if failure=='alias': sources[0]['issn']=[A]
    client,_=provider(sources=sources,publishers=[pub()],fail={'source':'/sources','publisher':'/publishers','timeout':'timeout'}.get(failure))
    with client: saved=save_settings(config,replace(draft,journals=draft.journals+(JournalConfig(name='hint',issn_l=B),)),client=client)
    assert saved.outcome is SettingsSaveOutcome.INVALID_DRAFT
    assert not saved.journal_written and not saved.monitor_written
    assert (config.read_bytes(),path.read_bytes())==before


@pytest.mark.parametrize('file', ['monitor','list'])
def test_revision_conflict_during_network(tmp_path,file):
    config,path,draft=setup(tmp_path); changed=config if file=='monitor' else path
    def during(req):
        if req.url.path=='/publishers': changed.write_text(changed.read_text()+'\n# concurrent edit\n')
    client,_=provider(sources=[source()],publishers=[pub()],during=during)
    other=path if file=='monitor' else config; before=other.read_bytes()
    with client: saved=save_settings(config,replace(draft,journals=draft.journals+(JournalConfig(name='hint',issn_l=B),)),client=client)
    assert saved.outcome is SettingsSaveOutcome.REVISION_CONFLICT
    assert not saved.journal_written and not saved.monitor_written and other.read_bytes()==before


@pytest.mark.parametrize('field', ['journal_name','journal_publisher','publisher_name','publisher_id','unsafe_url'])
def test_machine_tampering_and_unsafe_url_rejected_offline(tmp_path,field):
    config,path,draft=setup(tmp_path); before=config.read_bytes(),path.read_bytes()
    if field.startswith('journal'):
        draft=replace(draft,journals=(draft.journals[0].model_copy(update={'name':'Tampered'} if field=='journal_name' else {'publisher_id':P2}),))
    else:
        update={'name':'Tampered'} if field=='publisher_name' else {'publisher_id':P2} if field=='publisher_id' else {'publisher_url':'http://localhost'}
        draft=replace(draft,publishers=(draft.publishers[0].model_copy(update=update),))
    def forbidden(req): pytest.fail('tampering must fail offline')
    with OpenAlexClient(transport=httpx.MockTransport(forbidden)) as client:
        saved=save_settings(config,draft,client=client)
    assert saved.outcome is SettingsSaveOutcome.INVALID_DRAFT and (config.read_bytes(),path.read_bytes())==before


def legacy_setup(tmp_path, identifiers=A, *, output='workspace'):
    doc=f'## Journals\n\n| Journal | ISSN/EISSN | Group |\n| --- | --- | --- |\n| Arbitrary title hint | {identifiers} | Stats |\n\n## Conferences\n\nkeep\n'
    config,path=write_valid_settings_files(tmp_path,journal_contents=doc,output_dir=output)
    return config,path,load_settings(config).draft


def test_ordinary_save_requires_explicit_legacy_issn_migration_without_provider_or_writes(tmp_path, monkeypatch):
    config, path, draft = legacy_setup(tmp_path)
    before = [(p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in (config, path)]
    def forbidden(**kwargs):
        pytest.fail('Legacy storage must be rejected before Provider access')
    monkeypatch.setattr('literature_monitor.application.settings.OpenAlexClient', forbidden)
    saved = save_settings(config, replace(draft, name='Attempted monitor edit'))
    assert saved.outcome is SettingsSaveOutcome.INVALID_DRAFT
    assert [issue.message for issue in saved.issues] == ['legacy ISSN/EISSN storage must be migrated explicitly']
    assert not saved.journal_written and not saved.monitor_written
    assert [(p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in (config, path)] == before


def test_web_roundtrip_preserves_publishers_and_rejects_hidden_metadata(tmp_path):
    config,path,draft=setup(tmp_path)
    with TestClient(create_app(config),base_url='http://localhost') as browser:
        data=browser_settings_submission(browser.get('/settings').text)
        assert data['publisher_access_url']==['https://custom.example/login']
        data['keyword_expression']=['statistics']
        saved=browser.post('/settings/save',data=data)
        assert saved.headers['HX-Trigger']=='settingsSaved'
        assert load_settings(config).draft.publishers==draft.publishers
        data=browser_settings_submission(saved.text); data['publisher_name']=['Tampered']
        before=config.read_bytes(),path.read_bytes()
        saved=browser.post('/settings/save',data=data)
        assert 'HX-Trigger' not in saved.headers and (config.read_bytes(),path.read_bytes())==before


@pytest.mark.parametrize('fail_at', ['render','list','monitor'])
def test_complete_target_preparation_and_partial_save(tmp_path,monkeypatch,fail_at):
    from literature_monitor.application import settings
    config,path,draft=setup(tmp_path); before=config.read_bytes(),path.read_bytes()
    events=[]
    original_render=settings._render_monitor_yaml
    original_write=settings._write_snapshot_target
    def render(*args,**kwargs):
        events.append('prepared monitor')
        if fail_at=='render': raise ConfigurationError('test render failure')
        return original_render(*args,**kwargs)
    def write(p,contents,snapshot):
        assert events[0]=='prepared monitor'
        events.append('write '+p.name)
        if fail_at=='list' and p==path or fail_at=='monitor' and p==config: raise OSError('test write failure')
        return original_write(p,contents,snapshot)
    monkeypatch.setattr(settings,'_render_monitor_yaml',render)
    monkeypatch.setattr(settings,'_write_snapshot_target',write)
    client,_=provider(sources=[source()],publishers=[pub()])
    with client: saved=save_settings(config,replace(draft,name='Edited monitor',
        journals=draft.journals+(JournalConfig(name='hint',issn_l=B),)),client=client)
    assert config.read_bytes()==before[0]
    if fail_at=='monitor':
        assert saved.outcome is SettingsSaveOutcome.PARTIAL_SAVE and saved.journal_written and not saved.monitor_written
        assert saved.state.draft.journals[-1].name=='Canonical Source' and len(saved.state.draft.publishers)==2
    else:
        assert saved.outcome is (SettingsSaveOutcome.INVALID_DRAFT if fail_at=='render' else SettingsSaveOutcome.WRITE_FAILED)
        assert (config.read_bytes(),path.read_bytes())==before
        assert events==(['prepared monitor'] if fail_at=='render' else ['prepared monitor','write '+path.name])


def test_removed_shared_publisher_remains_active(tmp_path):
    config,path,draft=setup(tmp_path)
    shared=JournalConfig(name='Same publisher journal',issn_l=B,publisher_id=P1)
    path.write_text(render_settings_list_text(path.read_text(),draft.journals+(shared,),draft.publishers,path=path)); draft=load_settings(config).draft
    client,calls=provider()
    with client: saved=save_settings(config,replace(draft,journals=(shared,)),client=client)
    assert saved.outcome is SettingsSaveOutcome.SAVED and saved.state.draft.publishers==draft.publishers and calls==[]


def test_missing_retained_publisher_submission_is_rejected_offline(tmp_path):
    config,path,draft=setup(tmp_path)
    client,calls=provider()
    with client: saved=save_settings(config,replace(draft,publishers=()),client=client)
    assert saved.outcome is SettingsSaveOutcome.INVALID_DRAFT and calls==[]


@pytest.mark.parametrize('url', ['https://example.org:', 'http://0x7f000001','http://127.1'])
def test_malformed_or_alternate_literal_urls_rejected(url):
    with pytest.raises(ValueError): normalize_public_http_url(url)


def test_one_publisher_lookup():
    client,calls=provider(publishers=[pub(P1)])
    with client: rows=resolve_publisher_metadata(client,(P1,))
    assert len(rows)==1 and rows[0].publisher_id==P1 and calls==['/publishers']


def test_web_legacy_load_save_requires_no_paper_directory(tmp_path,monkeypatch):
    config,path,_=legacy_setup(tmp_path)
    client,calls=provider(sources=[source(aliases=(A,),candidate=A)],publishers=[pub()])
    monkeypatch.setattr('literature_monitor.application.settings.OpenAlexClient',lambda **kwargs:client)
    with TestClient(create_app(config),base_url='http://localhost') as browser:
        before=config.read_bytes(),path.read_bytes()
        data=browser_settings_submission(browser.get('/settings').text)
        assert (config.read_bytes(),path.read_bytes())==before
        response=browser.post('/settings/save',data=data)
        assert 'HX-Trigger' not in response.headers
        assert (config.read_bytes(),path.read_bytes())==before
    assert load_settings(config).draft.legacy_journals and not calls
