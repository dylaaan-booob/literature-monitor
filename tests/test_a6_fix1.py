"""Observable regressions for the five SPEC §41.14 audit findings."""
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from literature_monitor.application import settings
from literature_monitor.application.journal_import import (
    JournalImportMode, apply_journal_import, preview_journal_import,
)
from literature_monitor.config import JournalConfig, PublisherConfig, render_settings_list_text
from literature_monitor.url_safety import normalize_public_http_url
from literature_monitor.web.app import create_app
from test_a4_publisher_settings import A, B, P1, P2, setup, provider, source, pub
from test_a5_settings_ui import nodes
from test_web_run_settings import browser_settings_submission, browser_settings_values


UNICODE_LOCAL_URLS = (
    'http://127。0。0。1', 'http://192。168。1。1', 'http://１０。０。０。１',
    'http://local\u00adhost', 'http://０x7f。０。０。１', 'http://LOCALHOST。',
)


@pytest.mark.parametrize('url', UNICODE_LOCAL_URLS)
def test_f1_normalized_local_hosts_rejected_by_validator_and_config(url):
    with pytest.raises(ValueError):
        normalize_public_http_url(url)
    with pytest.raises(ValueError):
        PublisherConfig(name='Publisher', publisher_id=P1, access_url=url)


@pytest.mark.parametrize('url', UNICODE_LOCAL_URLS)
def test_f1_web_rejects_persistence_and_open_link(tmp_path, monkeypatch, url):
    config, path, _ = setup(tmp_path)
    before = config.read_bytes(), path.read_bytes()
    monkeypatch.setattr(settings, 'OpenAlexClient', lambda **kwargs: pytest.fail('URL validation is offline'))
    with TestClient(create_app(config), base_url='http://localhost') as browser:
        data = browser_settings_submission(browser.get('/settings').text)
        data['publisher_access_url'] = [url]
        response = browser.post('/settings/save', data=data)
        assert 'HX-Trigger' not in response.headers
        assert not nodes(response.text, attribute='data-publisher-open')
        assert browser_settings_values(response.text).publishers[0].access_url == url
    assert (config.read_bytes(), path.read_bytes()) == before


def test_f1_browser_parser_normalization_and_public_idn(monkeypatch):
    assert shutil.which('node'), 'Node is required by the Settings harness'
    urls = list(UNICODE_LOCAL_URLS) + ['https://例子.中国/path', 'https://bücher.example/path']
    result = subprocess.run(['node', '-e',
        'process.stdout.write(JSON.stringify(JSON.parse(process.argv[1]).map(u=>new URL(u).hostname)))',
        json.dumps(urls)], check=True, capture_output=True, text=True)
    hosts = json.loads(result.stdout)
    assert hosts[:6] == ['127.0.0.1', '192.168.1.1', '10.0.0.1', 'localhost', '127.0.0.1', 'localhost.']
    import socket
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: pytest.fail('No DNS request allowed'))
    for url in urls[:6]:
        with pytest.raises(ValueError):
            normalize_public_http_url(url)
    for url, host in zip(urls[6:], hosts[6:]):
        assert host.startswith('xn--')
        assert normalize_public_http_url(url) == url
        assert PublisherConfig(name='IDN', publisher_id=P1, access_url=url).access_url == url


def damaged_settings(tmp_path, mode='missing'):
    config, path, original = setup(tmp_path)
    contents = path.read_text()
    start, end = contents.index('## Journals'), contents.index('## Publishers')
    if mode == 'missing':
        contents = contents[:start] + contents[end:]
    elif mode == 'malformed':
        contents = contents[:start] + '## Journals\n\nnot a table\n\n' + contents[end:]
    elif mode == 'bad_row':
        contents = contents[:end] + '| Broken | not-issn |  |  |\n\n' + contents[end:]
    contents += '\n## Private notes\n\nUser-owned text\n'
    path.write_text(contents)
    return config, path, original


@pytest.mark.parametrize('mode', ['missing', 'malformed', 'bad_row'])
def test_f4_explicit_repair_preserves_other_sections_and_publishers(tmp_path, mode):
    config, path, original = damaged_settings(tmp_path, mode)
    state = settings.load_settings(config)
    assert state.issues
    assert state.draft.publishers == original.publishers
    if mode == 'bad_row':
        assert state.draft.journals == original.journals
    else:
        assert state.draft.journals == ()
    before = config.read_bytes(), path.read_bytes()
    journal = original.journals[0] if mode == 'bad_row' else JournalConfig(name='Untrusted hint', issn_l=A, group='Recovered')
    client, calls = provider(sources=[source(aliases=(A,), candidate=A, publisher=P1)], publishers=[])
    with client:
        result = settings.save_settings(config, replace(state.draft, journals=(journal,)), client=client)
    # A1 does not allow the old Settings form to repair/upgrade ambiguous data.
    assert result.outcome is settings.SettingsSaveOutcome.INVALID_DRAFT
    assert not calls
    assert (config.read_bytes(), path.read_bytes()) == before


@pytest.mark.parametrize('failure', ['missing_source', 'provider', 'publisher_loss', 'conflict'])
def test_f4_repair_failure_writes_neither_file(tmp_path, failure):
    config, path, original = damaged_settings(tmp_path)
    state = settings.load_settings(config)
    before = config.read_bytes(), path.read_bytes()
    rows = [] if failure == 'missing_source' else [source(aliases=(A,), candidate=A, publisher=None if failure == 'publisher_loss' else P1)]
    changed = False
    def during(request):
        nonlocal changed
        if failure == 'conflict' and not changed:
            config.write_text(config.read_text() + '# concurrent edit\n')
            changed = True
    client, _ = provider(sources=rows, fail='/sources' if failure == 'provider' else None, during=during)
    with client:
        result = settings.save_settings(config, replace(state.draft, journals=(JournalConfig(name='hint', issn_l=A),)), client=client)
    assert result.outcome is settings.SettingsSaveOutcome.INVALID_DRAFT
    assert not result.journal_written and not result.monitor_written
    assert path.read_bytes() == before[1]
    if failure != 'conflict':
        assert config.read_bytes() == before[0]


def test_f4_valid_row_metadata_cannot_be_forged_by_damaging_another_row(tmp_path):
    config, path, original = damaged_settings(tmp_path, 'bad_row')
    state = settings.load_settings(config)
    assert state.draft.journals == original.journals
    before = config.read_bytes(), path.read_bytes()
    forged = original.journals[0].model_copy(update={'name': 'Forged', 'publisher_id': P2})
    client, calls = provider()
    with client:
        result = settings.save_settings(config, replace(state.draft, journals=(forged,)), client=client)
    assert result.outcome is settings.SettingsSaveOutcome.INVALID_DRAFT
    assert not calls and (config.read_bytes(), path.read_bytes()) == before


@pytest.mark.parametrize('unsafe', ['symlink', 'directory', 'utf8', 'read_error', 'duplicate_section'])
def test_f4_unsafe_storage_is_not_overwritten(tmp_path, monkeypatch, unsafe):
    config, path, original = setup(tmp_path)
    if unsafe == 'symlink':
        target = tmp_path/'real-list.md'; path.rename(target); path.symlink_to(target)
        before = target.read_bytes()
    elif unsafe == 'directory':
        path.unlink(); path.mkdir()
    elif unsafe == 'utf8':
        path.write_bytes(b'\xff\xfe')
    elif unsafe == 'duplicate_section':
        path.write_text(path.read_text()+'\n## Journals\n\nambiguous\n')
    elif unsafe == 'read_error':
        read = settings.read_text_exact
        def cannot_read(p):
            if p == path: raise PermissionError('not readable')
            return read(p)
        monkeypatch.setattr(settings, 'read_text_exact', cannot_read)
    state = settings.load_settings(config)
    monitor_before = config.read_bytes()
    client, calls = provider(sources=[source(aliases=(A,), candidate=A, publisher=P1)])
    with client:
        result = settings.save_settings(config, replace(state.draft, journals=original.journals, publishers=original.publishers), client=client)
    assert result.outcome in (settings.SettingsSaveOutcome.WRITE_FAILED, settings.SettingsSaveOutcome.INVALID_DRAFT)
    assert not result.journal_written and not result.monitor_written and not calls
    assert config.read_bytes() == monitor_before
    if unsafe == 'symlink': assert path.is_symlink() and target.read_bytes() == before
    if unsafe == 'directory': assert path.is_dir()
    if unsafe == 'utf8': assert path.read_bytes() == b'\xff\xfe'


def test_f4_web_repair_and_partial_save(tmp_path, monkeypatch):
    config, path, _ = damaged_settings(tmp_path, 'malformed')
    metadata, _ = provider(sources=[source(aliases=(A,), candidate=A, publisher=P1)])
    monkeypatch.setattr(settings, 'OpenAlexClient', lambda **kw: metadata)
    write = settings._write_snapshot_target
    def fail_monitor(p, contents, snapshot):
        if p == config: raise OSError('monitor write unavailable')
        return write(p, contents, snapshot)
    monkeypatch.setattr(settings, '_write_snapshot_target', fail_monitor)
    before = config.read_bytes(), path.read_bytes()
    with TestClient(create_app(config), base_url='http://localhost') as browser:
        data = browser_settings_submission(browser.get('/settings').text)
        data.update(journal_name=['Pending resolution'], journal_issns=[A], journal_publisher_id=[''], journal_pending=['1'], journal_group=['Recovered'])
        response = browser.post('/settings/save', data=data)
        assert 'HX-Trigger' not in response.headers
        assert 'Settings issues' in response.text
    assert (config.read_bytes(), path.read_bytes()) == before


@pytest.mark.parametrize('mode', list(JournalImportMode))
@pytest.mark.parametrize('existing', [True, False])
def test_f2_identity_dedup_ignores_hints_and_preserves_canonical_rows(tmp_path, mode, existing):
    _, _, draft = setup(tmp_path)
    if not existing: draft = replace(draft, journals=(), publishers=())
    text = f'Journal,ISSN-L,Group\nHint one,{A},Stats\nHint two,{A},Stats\n'
    result = apply_journal_import(draft, text, mode=mode)
    assert result.applied and len(result.draft.journals) == 1
    assert result.draft.journals[0].name == ('Canonical A' if existing else 'Hint one')
    assert result.draft.journals[0].publisher_id == (P1 if existing else None)
    assert any(entry.source_rows == (2, 3) for entry in result.plan.entries)
    assert result.draft.monitor_revision == draft.monitor_revision and result.draft.journal_revision == draft.journal_revision


@pytest.mark.parametrize('mode', list(JournalImportMode))
def test_f2_real_group_conflict_still_blocks_and_same_name_distinct_ids_coexist(tmp_path, mode):
    _, _, draft = setup(tmp_path)
    conflict = f'Journal,ISSN-L,Group\nSame,{A},Stats\nOther,{A},Methods\n'
    result = apply_journal_import(draft, conflict, mode=mode)
    assert not result.applied and result.draft is draft
    assert any(e.kind.value == 'CONFLICT' and e.source_rows == (2, 3) for e in result.plan.entries)
    result = apply_journal_import(draft, f'Journal,ISSN-L\nSame,{A}\nSame,{B}\n', mode=mode)
    assert result.applied and {j.issn_l for j in result.draft.journals} == {A, B}








@pytest.mark.parametrize('entry', ['add'])
@pytest.mark.parametrize('url', ['', 'https://edited.example/login'])
def test_f5_remove_reintroduce_saved_identity_offline_web_save(tmp_path, monkeypatch, entry, url):
    config, path, original = setup(tmp_path)
    monkeypatch.setattr(settings, 'OpenAlexClient', lambda **kw: pytest.fail('Saved identity reintroduction must be offline'))
    with TestClient(create_app(config), base_url='http://localhost') as browser:
        data = browser_settings_submission(browser.get('/settings').text)
        for name in ('journal_name', 'journal_issns', 'journal_group', 'journal_publisher_id', 'journal_pending'):
            data[name] = []
        data['publisher_access_url'] = [url]
        data.update(journal_name=['Pending resolution'], journal_issns=[A], journal_publisher_id=[''], journal_pending=['1'], journal_group=['Methods'])
        response = browser.post('/settings/save', data=data)
        assert response.headers.get('HX-Trigger') == 'settingsSaved', response.text
        values = browser_settings_values(response.text)
        assert not values.journals[0].pending
        assert values.journals[0].name == 'Canonical A' and values.journals[0].publisher_id == P1
    final = settings.load_settings(config).draft
    assert final.journals == (original.journals[0].model_copy(update={'group': 'Methods'}),)
    assert final.publishers[0].access_url == (url or None)


@pytest.mark.parametrize('pending,publisher', [(False, None), (False, P1), (True, P2)])
def test_f5_existing_metadata_tampering_still_rejected(tmp_path, pending, publisher):
    config, path, original = setup(tmp_path)
    before = config.read_bytes(), path.read_bytes()
    forged = original.journals[0].model_copy(update={'name': 'Forged', 'publisher_id': publisher})
    draft = replace(original, journals=(forged,))
    if pending:
        draft = replace(draft, pending_journal_ids=(A,))
    client, calls = provider()
    with client: result = settings.save_settings(config, draft, client=client)
    assert result.outcome is settings.SettingsSaveOutcome.INVALID_DRAFT
    assert not calls and (config.read_bytes(), path.read_bytes()) == before


def test_f5_direct_import_reintroduction_and_revision_conflict(tmp_path):
    config, path, original = setup(tmp_path)
    removed = replace(original, journals=())
    applied = apply_journal_import(removed, f'Journal,ISSN-L,Group\nHint,{A},Methods\n')
    assert applied.applied
    client, calls = provider()
    with client: result = settings.save_settings(config, applied.draft, client=client)
    assert result.outcome is settings.SettingsSaveOutcome.SAVED and not calls
    assert result.state.draft.journals[0].name == original.journals[0].name
    stale = apply_journal_import(replace(result.state.draft, journals=()), f'Journal,ISSN-L\nHint,{A}\n').draft
    path.write_text(path.read_text()+'# concurrent\n')
    before = config.read_bytes(), path.read_bytes()
    with client: result = settings.save_settings(config, stale, client=client)
    assert result.outcome is settings.SettingsSaveOutcome.REVISION_CONFLICT
    assert (config.read_bytes(), path.read_bytes()) == before


@pytest.mark.parametrize('web', [False, True])
def test_f4_symlink_monitor_never_overwrites_target(tmp_path, web):
    config, path, original = setup(tmp_path)
    target = tmp_path/'real-monitor.yaml'
    config.rename(target); config.symlink_to(target)
    before = target.read_bytes(), path.read_bytes()
    if web:
        with TestClient(create_app(config), base_url='http://localhost') as browser:
            data = browser_settings_submission(browser.get('/settings').text)
            response = browser.post('/settings/save', data=data)
            assert 'HX-Trigger' not in response.headers
    else:
        result = settings.save_settings(config, original)
        assert result.outcome is settings.SettingsSaveOutcome.WRITE_FAILED
    assert config.is_symlink() and (target.read_bytes(), path.read_bytes()) == before
