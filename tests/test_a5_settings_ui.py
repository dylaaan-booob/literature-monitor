"""A5 unified Settings form and browser draft regression coverage."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from literature_monitor.application import settings
from literature_monitor.config import JournalConfig, PublisherConfig, render_settings_list_text
from literature_monitor.web.app import create_app
from test_a4_publisher_settings import A, B, P1, P2, setup, legacy_setup, provider, source, pub
from test_web_run_settings import (
    SettingsDOM, SETTINGS_NODE_DOM, browser_settings_submission, browser_settings_values,
)


def nodes(html, *, tag=None, attribute=None):
    result = []
    def walk(node):
        if isinstance(node, str) or node['tag'] == 'template':
            return
        if (tag is None or node['tag'] == tag) and (attribute is None or attribute in node['attrs']):
            result.append(node)
        for child in node['children']:
            walk(child)
    walk(html if isinstance(html, dict) else SettingsDOM(html).root)
    return result


def sample_issn(index):
    digits = f'{index + 1000000:07d}'
    check = (-sum(int(n) * w for n, w in zip(digits, range(8, 1, -1)))) % 11
    return digits[:4] + '-' + digits[4:] + ('X' if check == 10 else str(check))


def populated_settings(tmp_path):
    """74/20 independent test rows, never derived from the user's list.md."""
    config, path, _ = setup(tmp_path)
    publishers = tuple(PublisherConfig(
        name='Same publisher' if i < 2 else f'Publisher {i}', publisher_id=f'P{i + 1}',
        access_url=f'https://publisher{i}.example/login' if i < 19 else None,
    ) for i in range(20))
    journals = tuple(JournalConfig(name=f'Canonical Journal {i}', issn_l=sample_issn(i),
                                  publisher_id=publishers[i % 20].publisher_id, group='Methods')
                     for i in range(74))
    path.write_text(render_settings_list_text(path.read_text(), journals, publishers, path=path))
    return config, path


@pytest.fixture
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('GET and ordinary Save must not contact a metadata Provider')
    monkeypatch.setattr(settings, 'OpenAlexClient', forbidden)


def test_unified_74_journals_20_publishers_offline_get_and_routes(tmp_path, offline):
    config, path = populated_settings(tmp_path)
    before = config.read_bytes(), path.read_bytes()
    app = create_app(config)
    with TestClient(app, base_url='http://localhost') as browser:
        page = browser.get('/settings').text
        assert len(nodes(page, attribute='data-publisher-row')) == 20
        fields = browser_settings_submission(page)
        assert len(fields['journal_name']) == 74
        assert len(fields['publisher_name']) == len(fields['publisher_access_url']) == 20
        assert fields['publisher_name'][:2] == ['Same publisher'] * 2
        assert fields['publisher_id'][:2] == [P1, P2]
        assert fields['publisher_access_url'][-1] == ''
        inputs = [n['attrs'] for n in nodes(page, tag='input')]
        for field in ('journal_name', 'journal_issns', 'journal_publisher_id', 'publisher_name', 'publisher_id'):
            assert all(i['type'] == 'hidden' for i in inputs if i.get('name') == field)
        assert all(i.get('type') != 'hidden' for i in inputs if i.get('name') == 'publisher_access_url')
        forms = [n for n in nodes(page, tag='form') if n['attrs'].get('id') == 'settings-form']
        assert len(forms) == 1
        assert len([n for n in nodes(forms[0], tag='button') if n['attrs'].get('type') == 'submit']) == 1
        for link in nodes(page, attribute='data-publisher-open'):
            assert link['attrs']['target'] == '_blank'
            assert link['attrs']['rel'] == 'noopener noreferrer'
            assert link['attrs']['referrerpolicy'] == 'no-referrer'
        assert len(nodes(page, attribute='data-publisher-open')) == 19
        assert browser.get('/settings/publisher-access').status_code == 404
        assert browser.post('/settings/validate', data=fields).status_code == 404
        paths = {route.path for route in app.routes}
        assert '/settings/validate' not in paths and '/settings/publisher-access' not in paths
    assert (config.read_bytes(), path.read_bytes()) == before
    draft = settings.load_settings(config).draft
    assert settings.validate_settings(config, draft).outcome is settings.SettingsValidationOutcome.VALID


@pytest.mark.parametrize('url', ['https://edited.example/login', ''])
def test_visible_url_survives_import_preview_apply_then_offline_save(tmp_path, offline, url):
    config, path, _ = setup(tmp_path)
    before = config.read_bytes(), path.read_bytes()
    with TestClient(create_app(config), base_url='http://localhost') as browser:
        data = browser_settings_submission(browser.get('/settings').text)
        old_revision = data['journal_revision_digest']
        data.update(publisher_access_url=[url], journal_import_text=['Journal,ISSN-L,Group\nHint,0006-341X,Changed\n'])
        preview = browser.post('/settings/import/preview', data=data)
        assert browser_settings_values(preview.text).publishers[0].access_url == url
        assert 'open' in nodes(preview.text, attribute='data-settings-disclosure')[0]['attrs']
        applied = browser.post('/settings/import/apply', data=browser_settings_submission(preview.text))
        values = browser_settings_values(applied.text)
        assert values.publishers[0].access_url == url
        assert values.journals[0].group == 'Changed'
        assert values.journal_revision_digest == old_revision[0]
        assert (config.read_bytes(), path.read_bytes()) == before
        saved = browser.post('/settings/save', data=browser_settings_submission(applied.text))
        assert saved.headers['HX-Trigger'] == 'settingsSaved'
        assert browser_settings_values(saved.text).publishers[0].access_url == url
        again = browser.get('/settings').text
        assert browser_settings_values(again).publishers[0].access_url == url
        assert bool(nodes(again, attribute='data-publisher-open')) == bool(url)
    assert settings.load_settings(config).draft.publishers[0].access_url == (url or None)


@pytest.mark.parametrize('url', ['http://localhost', 'javascript:alert(1)', 'https://u:p@example.org', 'not a URL', 'https://example.org\n'])
def test_rejected_url_is_retained_without_open_link_or_writes(tmp_path, offline, url):
    config, path, _ = setup(tmp_path)
    before = config.read_bytes(), path.read_bytes()
    with TestClient(create_app(config), base_url='http://localhost') as browser:
        data = browser_settings_submission(browser.get('/settings').text)
        data['publisher_access_url'] = [url]
        result = browser.post('/settings/save', data=data)
        assert 'HX-Trigger' not in result.headers
        assert browser_settings_values(result.text).publishers[0].access_url == url
        assert not nodes(result.text, attribute='data-publisher-open')
        assert 'Settings issues' in result.text
        assert browser_settings_values(result.text).journal_revision_digest == data['journal_revision_digest'][0]
    assert (config.read_bytes(), path.read_bytes()) == before


def test_pending_issn_l_save_provider_canonicalization(tmp_path, monkeypatch):
    config, path, _ = setup(tmp_path, url=None)
    metadata, calls = provider(sources=[source()], publishers=[pub()])
    monkeypatch.setattr(settings, 'OpenAlexClient', lambda **kwargs: metadata)
    with TestClient(create_app(config), base_url='http://localhost') as browser:
        data = browser_settings_submission(browser.get('/settings').text)
        assert not calls
        for field, new in [('journal_name', 'Pending resolution'), ('journal_issns', B),
                           ('journal_publisher_id', ''), ('journal_pending', '1'), ('journal_group', 'Stats')]:
            data[field].append(new)
        saved = browser.post('/settings/save', data=data)
        assert saved.headers['HX-Trigger'] == 'settingsSaved'
        values = browser_settings_values(saved.text)
        assert values.journals[-1].name == 'Canonical Source'
        assert values.journals[-1].issns == B and values.journals[-1].publisher_id == P2
        assert not values.journals[-1].pending
        assert values.publishers[0].access_url == ''
        assert values.publishers[-1].name == 'Publisher B'
        issns = [n['attrs'] for n in nodes(saved.text, tag='input') if n['attrs'].get('name') == 'journal_issns']
        assert all(i['type'] == 'hidden' for i in issns)
    assert calls == ['/sources', '/publishers']


def test_pending_resolution_failure_keeps_issn_and_url_without_writes(tmp_path, monkeypatch):
    config, path, _ = setup(tmp_path)
    metadata, _ = provider(fail='/sources')
    monkeypatch.setattr(settings, 'OpenAlexClient', lambda **kwargs: metadata)
    before = config.read_bytes(), path.read_bytes()
    with TestClient(create_app(config), base_url='http://localhost') as browser:
        data = browser_settings_submission(browser.get('/settings').text)
        for field, new in [('journal_name', 'Pending resolution'), ('journal_issns', B),
                           ('journal_publisher_id', ''), ('journal_pending', '1'), ('journal_group', '')]:
            data[field].append(new)
        data['publisher_access_url'] = ['https://draft.example']
        saved = browser.post('/settings/save', data=data)
        assert 'HX-Trigger' not in saved.headers
        values = browser_settings_values(saved.text)
        assert values.journals[-1].pending and values.journals[-1].issns == B
        assert values.publishers[0].access_url == 'https://draft.example'
    assert (config.read_bytes(), path.read_bytes()) == before


@pytest.mark.parametrize('target', [B, 'bad-issn'])
def test_legacy_explicit_confirmation_ui_uses_a4_save(tmp_path, monkeypatch, target):
    config, path, _ = legacy_setup(tmp_path)
    before = config.read_bytes(), path.read_bytes()
    metadata, calls = provider(sources=[source(aliases=(A, B), candidate=A)], publishers=[pub()])
    monkeypatch.setattr(settings, 'OpenAlexClient', lambda **kwargs: metadata)
    with TestClient(create_app(config), base_url='http://localhost') as browser:
        page = browser.get('/settings').text
        assert 'Migration required.' in page and not calls
        assert (config.read_bytes(), path.read_bytes()) == before
        data = browser_settings_submission(page)
        assert data['migration_issn_l'] == ['']
        data['migration_issn_l'] = [target]
        result = browser.post('/settings/save', data=data)
        if target == B:
            assert result.headers['HX-Trigger'] == 'settingsSaved'
            assert browser_settings_values(result.text).journals[0].issns == B
            assert 'data-legacy-migration' not in result.text
            assert settings.load_settings(config).draft.journals[0].group == 'Stats'
        else:
            assert 'HX-Trigger' not in result.headers
            assert 'data-legacy-migration' in result.text
            assert browser_settings_submission(result.text)['migration_issn_l'] == [target]
            assert (config.read_bytes(), path.read_bytes()) == before


@pytest.mark.parametrize('failure', ['list', 'monitor', 'conflict'])
def test_publisher_save_failure_partial_and_conflict_truthful_state(tmp_path, monkeypatch, offline, failure):
    config, path, _ = setup(tmp_path)
    original_write = settings._write_snapshot_target
    with TestClient(create_app(config), base_url='http://localhost') as browser:
        data = browser_settings_submission(browser.get('/settings').text)
        data.update(name=['Attempted monitor'], publisher_access_url=['https://attempted.example'])
        if failure == 'conflict':
            path.write_text(path.read_text() + '\nExternal edit\n')
        else:
            def write(p, contents, snapshot):
                if p == (path if failure == 'list' else config):
                    raise OSError('test write failure')
                return original_write(p, contents, snapshot)
            monkeypatch.setattr(settings, '_write_snapshot_target', write)
        result = browser.post('/settings/save', data=data)
        assert 'HX-Trigger' not in result.headers
        values = browser_settings_values(result.text)
        assert values.publishers[0].access_url == 'https://attempted.example'
        if failure == 'monitor':
            assert 'Partial save.' in result.text and 'reread disk state' in result.text
            assert values.name != 'Attempted monitor'
            assert values.journal_revision_digest == settings.load_settings(config).draft.journal_revision.digest
            assert 'Attempted monitor' in result.text
        else:
            assert values.name == 'Attempted monitor'
            assert values.journal_revision_digest == data['journal_revision_digest'][0]
            assert settings.load_settings(config).draft.publishers[0].access_url == 'https://custom.example/login'
        assert 'Attempted draft' in result.text


def test_executable_publisher_link_dirty_dual_scroll_and_htmx_draft(tmp_path, offline):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node unavailable for the existing Settings DOM harness')
    config, _, _ = setup(tmp_path)
    with TestClient(create_app(config), base_url='http://localhost') as browser:
        page = browser.get('/settings').text
        data = browser_settings_submission(page)
        data.update(publisher_access_url=['https://draft.example'], journal_import_text=['Journal,ISSN-L\nHint,0006-341X\n'])
        response = browser.post('/settings/import/preview', data=data).text
    source_js = Path('src/literature_monitor/web/static/app.js').read_text()
    script = f'const PAGE={json.dumps(SettingsDOM(page).root)}, SOURCE={json.dumps(source_js)}, RESPONSE={json.dumps(SettingsDOM(response).root)};\n' + SETTINGS_NODE_DOM + r'''
const editor = () => document.getElementById("settings-editor");
let input = editor().querySelector('[name="publisher_access_url"]');
let link = editor().querySelector('[data-publisher-open]');
input.value = 'http://localhost'; handlers.input({target: input});
assert.equal(link.hidden, true); assert.equal(document.documentElement.dataset.settingsDirty, 'true');
input.value = link.dataset.accessUrl; handlers.change({target: input}); assert.equal(link.hidden, false);
input.value = 'https://draft.example'; handlers.input({target: input}); assert.equal(link.hidden, true);
editor().querySelector('[data-journal-viewport]').scrollTop = 317;
editor().querySelector('[data-publisher-viewport]').scrollTop = 211;
editor().querySelector('[data-settings-disclosure="groups"]').open = true;
bodyHandlers['htmx:beforeSwap']({detail: {target: editor()}});
dom = build(RESPONSE);
bodyHandlers['htmx:afterSwap']({detail: {target: editor()}});
assert.equal(editor().querySelector('[data-journal-viewport]').scrollTop, 317);
assert.equal(editor().querySelector('[data-publisher-viewport]').scrollTop, 211);
assert.equal(editor().querySelector('[data-settings-disclosure="groups"]').open, true);
assert.equal(editor().querySelector('[name="publisher_access_url"]').value, 'https://draft.example');
assert.equal(editor().querySelector('[data-publisher-open]').attrs.href, 'https://draft.example');
bodyHandlers.settingsDraftChanged(); assert.equal(document.documentElement.dataset.settingsDirty, 'true');
bodyHandlers.settingsSaved(); assert.equal(document.documentElement.dataset.settingsDirty, 'false');
'''
    result = subprocess.run([node, '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('keep_shared', [True, False])
def test_web_remove_shared_and_last_association_offline(tmp_path, offline, keep_shared):
    config, path, draft = setup(tmp_path)
    journals = draft.journals + (
        JournalConfig(name='Shared publisher', issn_l=B, publisher_id=P1),
        JournalConfig(name='No publisher', issn_l='0162-1459'),
    )
    path.write_text(render_settings_list_text(path.read_text(), journals, draft.publishers, path=path))
    with TestClient(create_app(config), base_url='http://localhost') as browser:
        data = browser_settings_submission(browser.get('/settings').text)
        for name in ('journal_name', 'journal_issns', 'journal_publisher_id', 'journal_pending', 'journal_group'):
            data[name] = data[name][1 if keep_shared else 2:]
        result = browser.post('/settings/save', data=data)
        assert result.headers['HX-Trigger'] == 'settingsSaved'
        values = browser_settings_values(result.text)
        assert len(values.publishers) == int(keep_shared)
        if keep_shared:
            assert values.publishers[0].access_url == 'https://custom.example/login'
        else:
            assert not nodes(result.text, attribute='data-publisher-row')
    assert len(settings.load_settings(config).draft.publishers) == int(keep_shared)


def test_empty_journal_submission_recovers_editable_pending_row(tmp_path, offline):
    config, path, _ = setup(tmp_path)
    before = config.read_bytes(), path.read_bytes()
    with TestClient(create_app(config), base_url='http://localhost') as browser:
        data = browser_settings_submission(browser.get('/settings').text)
        for name in ('journal_name', 'journal_issns', 'journal_publisher_id', 'journal_pending', 'journal_group'):
            data.pop(name)
        result = browser.post('/settings/save', data=data)
        assert 'HX-Trigger' not in result.headers
        values = browser_settings_values(result.text)
        assert len(values.journals) == 1 and values.journals[0].pending
        controls = [n['attrs'] for n in nodes(result.text, tag='input') if n['attrs'].get('name') == 'journal_issns']
        assert len(controls) == 1 and controls[0].get('type') != 'hidden'
    assert (config.read_bytes(), path.read_bytes()) == before
