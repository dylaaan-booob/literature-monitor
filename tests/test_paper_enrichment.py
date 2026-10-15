from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from test_application_decisions import write_paper
from test_access_mapping_a4 import prepared
from test_web_app import write_valid_config
from literature_monitor.application.paper_enrichment import read_enrichment_target, save_enrichment
from literature_monitor.markdown_state import MISSING_ABSTRACT, parse_paper_state
from literature_monitor.models import WorkflowStatus
from literature_monitor.safe_write import ContentChangedError
from literature_monitor.web.paper_enrichment import PaperEnrichment
from literature_monitor.web.app import create_app
from literature_monitor.web.capture_coordinator import CaptureCoordinator, ConnectorReadiness


def missing(workspace, status=WorkflowStatus.CANDIDATE):
    paper_id = uuid4()
    path = write_paper(workspace, paper_id, status=status)
    original = path.read_text().replace('Decision abstract.', MISSING_ABSTRACT)
    original = original.replace('status:', '# Preserve YAML comment\ncustom: "do not normalize"\nstatus:')
    original += '\n## Custom section\n\nHuman notes.\n'
    path.write_text(original)
    return paper_id, path


@pytest.mark.parametrize('status', list(WorkflowStatus))
@pytest.mark.parametrize('mode', ['marker', 'blank', 'missing', 'crlf'])
def test_save_only_abstract_bytes(tmp_path, status, mode):
    paper_id, path = missing(tmp_path, status)
    data = path.read_text()
    old = f'## Abstract\n\n{MISSING_ABSTRACT}\n\n'
    if mode == 'blank':
        data = data.replace(MISSING_ABSTRACT, '  ')
        old = '## Abstract\n\n  \n\n'
    elif mode == 'missing':
        data = data.replace(old, '')
        old = ''
    elif mode == 'crlf':
        data = data.replace('\n', '\r\n')
        old = old.replace('\n', '\r\n')
    path.write_bytes(data.encode())
    target = read_enrichment_target(tmp_path, paper_id)
    abstract = 'Complete abstract.\n\n## Findings\n\nFull findings.'
    save_enrichment(target, abstract)
    after = path.read_bytes().decode()
    assert after.startswith(data.split('## Abstract' if old else '## Sources')[0])
    assert after[after.index('## Sources'):] == data[data.index('## Sources'):]
    state = parse_paper_state(path, after, tmp_path / 'Authors')
    assert state.abstract == abstract and state.status is status
    assert 'custom: "do not normalize"' in after
    with pytest.raises((ValueError, ContentChangedError)):
        save_enrichment(target, abstract)


def test_existing_abstract_and_duplicate_sections_refused(tmp_path):
    paper_id, path = missing(tmp_path)
    for replacement in ['Existing abstract.', MISSING_ABSTRACT + '\n\n## Abstract\n\n']:
        original = path.read_text()
        path.write_text(original.replace(MISSING_ABSTRACT, replacement))
        before = path.read_bytes()
        with pytest.raises(ValueError): read_enrichment_target(tmp_path, paper_id)
        assert path.read_bytes() == before
        path.write_text(original)


@pytest.mark.parametrize('mutation', ['contents', 'file', 'directory', 'workspace', 'doi', 'symlink'])
def test_external_changes_never_write(tmp_path, mutation):
    workspace = tmp_path / 'workspace'
    paper_id, path = missing(workspace)
    target = read_enrichment_target(workspace, paper_id)
    if mutation == 'contents': path.write_text(path.read_text() + 'User edit\n')
    elif mutation == 'doi': path.write_text(path.read_text().replace('10.5555/decision', '10.5555/other'))
    elif mutation == 'file':
        data = path.read_bytes(); path.unlink(); path.write_bytes(data)
    elif mutation == 'directory':
        old = workspace / 'OriginalPapers'; path.parent.rename(old)
        path.parent.mkdir(); path.write_bytes((old / path.name).read_bytes())
    elif mutation == 'workspace':
        workspace.rename(tmp_path / 'old'); paper_id2, path2 = missing(workspace)
        path2.write_bytes((tmp_path / 'old' / 'Papers' / 'paper.md').read_bytes())
    else:
        actual = path.with_suffix('.txt'); path.rename(actual); path.symlink_to(actual)
    before = path.read_bytes()
    with pytest.raises((ValueError, OSError, ContentChangedError)):
        save_enrichment(target, 'New abstract')
    assert path.read_bytes() == before


def service(tmp_path):
    paper_id, path = missing(tmp_path)
    capture = CaptureCoordinator()
    capture.heartbeat(connector_version='0.6.8', zotero_reachable=False, enrichment_capable=True)
    assert capture.snapshot().connector_online
    assert capture.snapshot().readiness is ConnectorReadiness.UNAVAILABLE
    return paper_id, path, capture, PaperEnrichment(capture)


def test_cancel_and_browser_exclusion(tmp_path):
    paper_id, path, capture, enrich = service(tmp_path)
    original = path.read_bytes()
    started = enrich.start(tmp_path, paper_id); cmd = enrich.claim()
    assert enrich.claim() is None
    assert capture.reserve_browser_task('import') is None
    assert capture.reserve_browser_task('preparation') is None
    assert enrich.cancel(paper_id, started['request_id'])
    assert not enrich.result(tmp_path, cmd, [{'item_type': 'journalArticle', 'doi': 'https://doi.org/10.5555/decision', 'abstract': 'Late result'}])
    owner = capture.reserve_browser_task('preparation')
    assert owner is not None
    capture.release_browser_task('preparation', owner)
    assert path.read_bytes() == original
    assert enrich.state(paper_id)['phase'] == 'Idle'
    assert not enrich.result(tmp_path, cmd, [])
    assert path.read_bytes() == original


@pytest.mark.parametrize('items', [[], [{}, {}], [{'item_type': 'journalArticle', 'doi': '10.5555/wrong', 'abstract': 'Text'}],
    [{'item_type': 'journalArticle', 'doi': None, 'abstract': 'Text'}],
    [{'item_type': 'journalArticle', 'doi': '10.5555/decision', 'abstract': '  '}],
    [{'item_type': 'attachment', 'doi': '10.5555/decision', 'abstract': 'Text'}]])
def test_rejected_results(tmp_path, items):
    paper_id, path, capture, enrich = service(tmp_path)
    before = path.read_bytes(); enrich.start(tmp_path, paper_id); cmd = enrich.claim()
    assert enrich.result(tmp_path, cmd, items) == {'accepted': True, 'phase': 'Failed'}
    assert enrich.state(paper_id)['phase'] == 'Failed'
    assert path.read_bytes() == before


def test_connector_offline_capability_and_expiry(tmp_path, monkeypatch):
    paper_id, path, capture, enrich = service(tmp_path)
    capture.heartbeat(connector_version='old', zotero_reachable=True)
    with pytest.raises(ValueError): enrich.start(tmp_path, paper_id)
    capture.heartbeat(connector_version='new', zotero_reachable=False, enrichment_capable=True)
    enrich.start(tmp_path, paper_id); cmd = enrich.claim()
    enrich._task['deadline'] = 0
    assert not enrich.result(tmp_path, cmd, [])
    assert enrich.state(paper_id)['phase'] == 'Failed'
    assert capture.reserve_browser_task('preparation') is not None


def test_readonly_lease_expires_without_enrichment_polling(tmp_path, monkeypatch):
    paper_id, path, capture, enrich = service(tmp_path)
    enrich.start(tmp_path, paper_id)
    capture._browser_deadline = 0
    # Import can recover even if the old UI never polls or cancels again.
    owner = capture.reserve_browser_task('import')
    assert owner is not None
    assert capture.release_browser_task('import', owner)


def test_manual_wait_extends_only_matching_lease(tmp_path):
    paper_id, path, capture, enrich = service(tmp_path)
    enrich.start(tmp_path, paper_id); cmd = enrich.claim()
    ordinary = capture._browser_deadline
    assert not enrich.authorize({**cmd, 'invocation_id': 'wrong'}, True)
    assert capture._browser_deadline == ordinary
    assert enrich.authorize(cmd, True)
    assert capture._browser_deadline > ordinary + 500
    extended = capture._browser_deadline
    assert enrich.authorize(cmd, True) and capture._browser_deadline == extended


def test_unsettled_import_blocks_enrichment(tmp_path):
    paper_id, path, capture, enrich = service(tmp_path)
    capture._import_browser_unsettled.add(('request', 'invocation', 'https://doi.org/10.5555/decision'))
    with pytest.raises(ValueError, match='busy'): enrich.start(tmp_path, paper_id)
    assert enrich.claim() is None


@pytest.mark.parametrize('change', ['duplicate_uuid', 'conflicting_doi', 'duplicate_yaml'])
def test_ambiguous_identity_refused(tmp_path, change):
    paper_id, path = missing(tmp_path)
    original = path.read_text()
    if change == 'duplicate_uuid': path.with_name('duplicate.md').write_text(original)
    elif change == 'conflicting_doi': path.write_text(original.replace('title:', 'doi: 10.5555/conflicting\ntitle:', 1))
    else: path.write_text(original.replace('title:', 'title: Duplicate\ntitle:', 1))
    before = path.read_bytes()
    with pytest.raises(ValueError): read_enrichment_target(tmp_path, paper_id)
    assert path.read_bytes() == before


def test_candidate_trailing_newline_preserved_and_committed_error_reported(tmp_path, monkeypatch):
    import literature_monitor.application.paper_enrichment as writing
    from literature_monitor.safe_write import TextWriteCommittedError
    paper_id, path, capture, enrich = service(tmp_path)
    enrich.start(tmp_path, paper_id); cmd = enrich.claim()
    abstract = '\nFull original Abstract\n'
    original_write = writing.replace_regular_text_at_identity
    def committed(*args, **kwargs):
        original_write(*args, **kwargs)
        raise TextWriteCommittedError('sync failed')
    monkeypatch.setattr(writing, 'replace_regular_text_at_identity', committed)
    enrich.result(tmp_path, cmd, [{'item_type': 'journalArticle', 'doi': '10.5555/decision', 'abstract': abstract}])
    result = enrich.state(paper_id)
    assert result['phase'] == 'Completed' and 'sync failed' in result['message']
    assert abstract in path.read_text()


def test_cancelled_capability_loss_and_write_conflict_drop_candidates(tmp_path):
    paper_id, path, capture, enrich = service(tmp_path)
    start = enrich.start(tmp_path, paper_id); cmd = enrich.claim()
    capture.heartbeat(connector_version='old', zotero_reachable=True)
    assert not enrich.authorize(cmd)
    assert enrich.state(paper_id)['phase'] == 'Failed'
    capture.heartbeat(connector_version='new', zotero_reachable=False, enrichment_capable=True)
    enrich.start(tmp_path, paper_id); cmd = enrich.claim()
    path.write_text(path.read_text() + 'External edit\n'); before = path.read_bytes()
    assert enrich.result(tmp_path, cmd, [{'item_type': 'journalArticle', 'doi': '10.5555/decision', 'abstract': 'Candidate'}])['phase'] == 'Failed'
    assert path.read_bytes() == before
    assert 'abstract' not in enrich.state(paper_id)


def test_ui_next_enrichment_controls_and_api_start_state_cancel(tmp_path):
    config, _, _, _ = prepared(tmp_path)
    paper_id, path = missing(tmp_path / "workspace")
    original = path.read_bytes()
    app = create_app(config)
    app.state.capture_coordinator.heartbeat(
        connector_version="ui-next-test", zotero_reachable=False, enrichment_capable=True,
    )
    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/ui-next")
        script = client.get("/static/ui_next/app.js")
        assert page.status_code == script.status_code == 200
        assert 'id="enrich-paper"' in page.text and 'id="cancel-enrich"' in page.text
        assert 'enrichmentURL(operation)' in script.text
        assert "$('enrich-paper').addEventListener('click',startEnrich)" in script.text
        assert "$('cancel-enrich').addEventListener('click'" in script.text

        endpoint = f"/api/papers/{paper_id}/enrichment"
        assert client.post(f"{endpoint}/start", json={"csrf_token": "invalid"}).status_code == 403
        started = client.post(f"{endpoint}/start", json={"csrf_token": app.state.csrf_token})
        assert started.status_code == 200, started.text
        operation = started.json()
        assert operation["phase"] == "Retrieving"
        current = client.get(f"{endpoint}/state", params={"request_id": operation["request_id"]})
        assert current.status_code == 200 and current.json()["phase"] == "Retrieving"
        canceled = client.post(f"{endpoint}/cancel", json={
            "csrf_token": app.state.csrf_token, "request_id": operation["request_id"],
        })
        assert canceled.status_code == 200 and canceled.json() == {"accepted": True}
        assert client.get(f"{endpoint}/state").json()["phase"] == "Idle"
    assert path.read_bytes() == original


def test_enrichment_claim_rejects_simple_cross_origin_requests(tmp_path):
    config, workspace = write_valid_config(tmp_path)
    paper_id, path = missing(workspace)
    app = create_app(config)
    app.state.capture_coordinator.heartbeat(connector_version='new', zotero_reachable=False, enrichment_capable=True)
    app.state.paper_enrichment.start(workspace, paper_id)
    with TestClient(app, base_url='http://localhost') as client:
        url = '/api/connector/enrichment/claim'
        assert client.post(url).status_code == 422
        assert client.post(url, data={}).status_code == 422
        assert client.post(url, content='{}', headers={'content-type': 'text/plain', 'origin': 'https://evil.example'}).status_code == 422
        assert client.post(url, json={'extra': True}).status_code == 422
        assert client.post(url, json={}).json()['command'] is not None
        assert client.post(url, json={}).json()['command'] is None


@pytest.mark.parametrize('available', ['abstract', 'date', 'both', 'neither', 'invalid_date'])
def test_partial_enrichment_then_retry_remaining_fields_after_restart(tmp_path, available):
    paper_id, path, capture, enrich = service(tmp_path)
    path.write_text(path.read_text().replace("publication_date: '2026-09-20'", 'publication_date: null'))
    original = path.read_bytes()
    enrich.start(tmp_path, paper_id); cmd = enrich.claim()
    item = {'item_type': 'journalArticle', 'doi': '10.5555/decision'}
    if available in {'abstract', 'both', 'invalid_date'}: item['abstract'] = 'Candidate'
    if available in {'date', 'both', 'invalid_date'}:
        item.update(date='2024-02', date_parts={'year': 2024, 'month': 2})
    if available == 'invalid_date': item['date_parts']['day'] = 30
    response = enrich.result(tmp_path, cmd, [item])
    if available == 'neither':
        assert path.read_bytes() == original
        assert response['phase'] == 'Failed'; return
    assert response['phase'] == 'Completed'
    state = enrich.state(paper_id)
    assert state['phase'] == 'Completed'
    assert state['missing_abstract'] is (available == 'date')
    assert state['missing_date']  # A saved month date is still eligible for a complete date.
    if available != 'both':
        before = path.read_bytes()
        enrich = PaperEnrichment(capture)  # A new Web service session permits the remaining fields.
        enrich.start(tmp_path, paper_id); cmd = enrich.claim()
        enrich.result(tmp_path, cmd, [{'item_type': 'journalArticle', 'doi': '10.5555/decision', 'abstract': 'Final Abstract',
                             'date': '2025-03-04', 'date_parts': {'year': 2025, 'month': 3, 'day': 4}}])
        assert not enrich.state(paper_id)['missing_date'] and not enrich.state(paper_id)['missing_abstract']
        after = path.read_bytes()
        if available == 'date': assert b'publication_date: "2025-03-04"' in after
        else: assert b'Candidate' in after and b'Final Abstract' not in after


@pytest.mark.parametrize('raw,parts,expected', [
    ('2024', {'year': 2024}, '2024'), ('May 2024', {'year': 2024, 'month': 5}, '2024-05'),
    ('17 May 2024', {'year': 2024, 'month': 5, 'day': 17}, '2024-05-17'),
    ('2024-02-29', {'year': 2024, 'month': 2, 'day': 29}, '2024-02-29'),
    ('2023-02-29', {'year': 2023, 'month': 2, 'day': 29}, None),
    ('2024-02-30', {'year': 2024, 'month': 2, 'day': 30}, None),
    ('2024-00', {'year': 2024, 'month': 0}, None), ('2024-13', {'year': 2024, 'month': 13}, None),
    ('2024-05-00', {'year': 2024, 'month': 5, 'day': 0}, None),
    ('', {'year': 2024}, None), ('2024', {'year': True}, None), ('2024', {'year': '2024'}, None),
    ('2024', {'year': 0}, None), ('2024', {'year': 10000}, None), ('2024', {}, None),
    ('2024', {'year': 2024, 'day': 5}, None), ('Spring 2024', None, None),
])
def test_publication_date_candidate(raw, parts, expected):
    from literature_monitor.web.paper_enrichment import publication_date_candidate
    result = publication_date_candidate({'date': raw, 'date_parts': parts})
    assert result == expected


@pytest.mark.parametrize('line', ['publication_date: null # keep comment', 'publication_date: # keep comment',
    "'publication_date': ~ # keep comment", 'publication_date: NULL', None])
@pytest.mark.parametrize('crlf', [False, True])
def test_date_scalar_splice_preserves_other_bytes(tmp_path, line, crlf):
    paper_id, path = missing(tmp_path)
    original = path.read_text()
    old = "publication_date: '2026-09-20'\n"
    original = original.replace(old, line + '\n' if line else '')
    if crlf: original = original.replace('\n', '\r\n')
    path.write_bytes(original.encode())
    target = read_enrichment_target(tmp_path, paper_id)
    save_enrichment(target, publication_date='2024-01-01')
    after = path.read_bytes().decode()
    state = parse_paper_state(path, after, tmp_path / 'Authors')
    assert state.publication_date == '2024-01-01'
    assert after[after.index('\n---', 4):] == original[original.index('\n---', 4):]
    if line:
        # Removing only the changed date scalar leaves all original YAML spelling intact.
        expected = line.replace('null', '"2024-01-01"').replace('NULL', '"2024-01-01"').replace('~', '"2024-01-01"')
        if line.startswith('publication_date: #'): expected = line.replace(': #', ': "2024-01-01" #')
        assert expected in after
    else:
        inserted = 'publication_date: "2024-01-01"' + ('\r\n' if crlf else '\n')
        assert after.replace(inserted, '', 1) == original
    assert state.abstract is None


@pytest.mark.parametrize('date_line', ['publication_date: &empty null\ncustom_empty: *empty',
    'custom_empty: &empty null\npublication_date: *empty'])
def test_ambiguous_yaml_date_cannot_partially_save_abstract(tmp_path, date_line):
    paper_id, path = missing(tmp_path)
    original = path.read_text().replace("publication_date: '2026-09-20'", date_line)
    path.write_text(original)
    target = read_enrichment_target(tmp_path, paper_id)
    with pytest.raises(ValueError): save_enrichment(target, 'New Abstract', '2024-01-01')
    assert path.read_text() == original


def without_authors(workspace, status=WorkflowStatus.CANDIDATE, *, missing_abstract=False, missing_date=False):
    paper_id, path = missing(workspace, status)
    text = path.read_text()
    start, end = text.index('authors:\n'), text.index('journal:')
    text = text[:start] + 'authors: [] # retain author comment\n' + text[end:]
    if not missing_abstract: text = text.replace(MISSING_ABSTRACT, 'Existing Abstract')
    if missing_date: text = text.replace("publication_date: '2026-09-20'", 'publication_date: null')
    path.write_text(text)
    return paper_id, path


CREATORS = [
    {'creatorType': 'author', 'firstName': 'Ada', 'lastName': 'Lovelace', 'orcid': 'invented', 'openalex_id': 'invented'},
    {'creatorType': 'editor', 'firstName': 'Ignored', 'lastName': 'Editor'},
    {'creatorType': 'author', 'fieldMode': 1, 'lastName': 'Research Consortium'},
]
NAMES = ['Ada Lovelace', 'Research Consortium']


@pytest.mark.parametrize('fields,status,crlf', [
    ('a', WorkflowStatus.CANDIDATE, False), ('d', WorkflowStatus.KEPT, True),
    ('u', WorkflowStatus.REJECTED, False), ('ad', WorkflowStatus.EXPORTED, False),
    ('au', WorkflowStatus.CANDIDATE, True), ('du', WorkflowStatus.KEPT, False),
    ('adu', WorkflowStatus.EXPORTED, True),
])
def test_authors_enrichment_field_matrix_preserves_bytes(tmp_path, status, fields, crlf, monkeypatch):
    import json
    import literature_monitor.application.paper_enrichment as writing
    if 'u' in fields:
        paper_id, path = without_authors(tmp_path, status, missing_abstract='a' in fields, missing_date='d' in fields)
    else:
        paper_id, path = missing(tmp_path, status)
        text = path.read_text()
        if 'a' not in fields: text = text.replace(MISSING_ABSTRACT, 'Existing Abstract')
        if 'd' in fields: text = text.replace("publication_date: '2026-09-20'", 'publication_date: null')
        path.write_text(text)
    if crlf: path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
    original = path.read_bytes().decode()
    capture = CaptureCoordinator(); capture.heartbeat(connector_version='new', zotero_reachable=False, enrichment_capable=True)
    enrich = PaperEnrichment(capture); enrich.start(tmp_path, paper_id); cmd = enrich.claim()
    writes = []; actual = writing.replace_regular_text_at_identity
    def save_once(*args, **kwargs):
        writes.append(args); return actual(*args, **kwargs)
    monkeypatch.setattr(writing, 'replace_regular_text_at_identity', save_once)
    enrich.result(tmp_path, cmd, [{'item_type': 'journalArticle', 'doi': '10.5555/decision', 'abstract': 'Candidate Abstract',
                                    'date': '2024', 'date_parts': {'year': 2024}, 'creators': CREATORS}])
    result = enrich.state(paper_id)
    assert result['phase'] == 'Completed' and len(writes) == 1
    expected = original
    nl = '\r\n' if crlf else '\n'
    if 'a' in fields:
        expected = expected.replace(f'## Abstract{nl}{nl}{MISSING_ABSTRACT}{nl}{nl}', '## Abstract\n\nCandidate Abstract\n\n')
    if 'd' in fields: expected = expected.replace('publication_date: null', 'publication_date: "2024"')
    if 'u' in fields:
        links = [f'[[Authors/unidentified-{paper_id.hex}-{i:02d}|{name}]]' for i, name in enumerate(NAMES, 1)]
        expected = expected.replace('authors: []', 'authors: ' + json.dumps(links))
        from literature_monitor.markdown_state import parse_author_state
        for i, name in enumerate(NAMES, 1):
            note = tmp_path / 'Authors' / f'unidentified-{paper_id.hex}-{i:02d}.md'
            state = parse_author_state(note, note.read_text())
            assert state.name == name and state.openalex_key is None and state.orcid_key is None
        assert len(list((tmp_path / 'Authors').glob('*.md'))) == 2
    else:
        assert not (tmp_path / 'Authors').exists()
    assert path.read_bytes().decode() == expected
    assert not result['missing_abstract'] and not result['missing_date'] and not result['missing_authors']
    assert enrich.result(tmp_path, cmd, []) is None
    assert len(writes) == 1


@pytest.mark.parametrize('creators,expected', [
    (CREATORS, NAMES), ([{'creatorType': 'author', 'lastName': 'Single Name'}], ['Single Name']),
    ([{'creatorType': 'author', 'firstName': '  张 ', 'lastName': ' 三 '}], ['张 三']),
    (None, None), ({}, None), ([], None), ([None], None),
    ([{'creatorType': 'editor', 'lastName': 'Editor'}], None),
    ([{'creatorType': 'author'}], None), ([{'creatorType': 'author', 'lastName': 3}], None),
    ([{'creatorType': 'author', 'fieldMode': 2, 'lastName': 'Name'}], None),
    ([{'creatorType': 'author', 'lastName': 'Bad]name'}], None),
    ([{'creatorType': 'author', 'lastName': 'Bad\nname'}], None),
    ([{'creatorType': 'author', 'lastName': 'Valid'}, {'creatorType': 'author'}], None),
])
def test_creators_candidate(creators, expected):
    from literature_monitor.web.paper_enrichment import authors_candidate
    assert authors_candidate({'creators': creators}) == expected


@pytest.mark.parametrize('first', ['authors', 'abstract', 'date'])
def test_authors_partial_save_then_enrich_remaining(tmp_path, first):
    paper_id, path = without_authors(tmp_path, missing_abstract=True, missing_date=True)
    capture = CaptureCoordinator(); capture.heartbeat(connector_version='new', zotero_reachable=False, enrichment_capable=True)
    enrich = PaperEnrichment(capture)
    values = {'authors': {'creators': CREATORS}, 'abstract': {'abstract': 'First Abstract'}, 'date': {'date': '2024', 'date_parts': {'year': 2024}}}
    enrich.start(tmp_path, paper_id); cmd = enrich.claim()
    item = {'item_type': 'journalArticle', 'doi': '10.5555/decision', **values[first]}
    enrich.result(tmp_path, cmd, [item]); saved = enrich.state(paper_id)
    assert saved['phase'] == 'Completed' and saved['missing_authors'] == (first != 'authors')
    initial_notes = {p: p.read_bytes() for p in (tmp_path / 'Authors').glob('*.md')}
    enrich = PaperEnrichment(capture)  # Retry remaining fields after a service restart.
    enrich.start(tmp_path, paper_id); cmd = enrich.claim()
    enrich.result(tmp_path, cmd, [{'item_type': 'journalArticle', 'doi': '10.5555/decision', 'creators': CREATORS,
                         'abstract': 'Final Abstract', 'date': '2025', 'date_parts': {'year': 2025}}])
    result = enrich.state(paper_id)
    assert not any(result[k] for k in ('missing_authors', 'missing_abstract', 'missing_date'))
    assert all(p.read_bytes() == data for p, data in initial_notes.items())
    state = parse_paper_state(path, path.read_text(), tmp_path / 'Authors')
    if first == 'abstract': assert state.abstract == 'First Abstract'
    if first == 'date': assert state.publication_date == '2024'


@pytest.mark.parametrize('kind', ['same', 'other_name', 'stable_id', 'opaque', 'symlink', 'directory'])
def test_author_target_reuse_and_conflict(tmp_path, kind):
    from literature_monitor.materialize import render_author_markdown
    from literature_monitor.models import Author
    paper_id, path = without_authors(tmp_path)
    note = tmp_path / 'Authors' / f'unidentified-{paper_id.hex}-01.md'; note.parent.mkdir()
    contents = render_author_markdown(Author(name='Other' if kind == 'other_name' else 'Ada Lovelace',
                                           orcid='0000-0001-5660-1655' if kind == 'stable_id' else None))
    if kind == 'opaque': contents = 'Human document, not an Author'
    if kind == 'symlink':
        other = tmp_path / 'other.md'; other.write_text(contents); note.symlink_to(other)
    elif kind == 'directory': note.mkdir()
    else: note.write_text(contents + '\nHuman Author notes.\n')
    before = path.read_bytes()
    target = read_enrichment_target(tmp_path, paper_id)
    if kind == 'same':
        note_before = note.read_bytes(); save_enrichment(target, authors=['Ada Lovelace']); assert note.read_bytes() == note_before
    else:
        with pytest.raises(ValueError, match='Paper was not saved'): save_enrichment(target, authors=['Ada Lovelace'])
        assert path.read_bytes() == before


@pytest.mark.parametrize('failure', ['paper_conflict', 'author_create', 'author_sync', 'author_changed', 'directory_replaced'])
def test_author_files_retained_when_paper_save_fails(tmp_path, monkeypatch, failure):
    import literature_monitor.application.paper_enrichment as writing
    from literature_monitor.safe_write import WorkspaceTextDirectory, TextWriteCommittedError
    paper_id, path = without_authors(tmp_path)
    capture = CaptureCoordinator(); capture.heartbeat(connector_version='new', zotero_reachable=False, enrichment_capable=True)
    enrich = PaperEnrichment(capture); enrich.start(tmp_path, paper_id); cmd = enrich.claim()
    before = path.read_bytes(); actual_create = WorkspaceTextDirectory.create; actual_replace = writing.replace_regular_text_at_identity
    def create(directory, name, contents):
        if failure == 'author_create' and name.endswith('-02.md'): raise OSError('creation failed')
        actual_create(directory, name, contents)
        if failure == 'author_sync': raise TextWriteCommittedError('Author fsync failed')
    def replace(*args, **kwargs):
        if failure == 'paper_conflict': raise ContentChangedError('concurrent Paper edit')
        if failure == 'author_changed':
            note = next((tmp_path / 'Authors').glob('*.md')); note.write_text(note.read_text() + 'External edit')
        if failure == 'directory_replaced':
            (tmp_path / 'Authors').rename(tmp_path / 'OldAuthors'); (tmp_path / 'Authors').mkdir()
        return actual_replace(*args, **kwargs)
    with monkeypatch.context() as patching:
        patching.setattr(WorkspaceTextDirectory, 'create', create)
        patching.setattr(writing, 'replace_regular_text_at_identity', replace)
        enrich.result(tmp_path, cmd, [{'item_type': 'journalArticle', 'doi': '10.5555/decision', 'creators': CREATORS}])
        result = enrich.state(paper_id)
    assert result['phase'] == 'Failed' and 'Paper was not saved' in result['message']
    assert 'created Author files retained:' in result['message'] and str(tmp_path / 'Authors' / f'unidentified-{paper_id.hex}-01.md') in result['message']
    assert path.read_bytes() == before
    if failure in {'paper_conflict', 'author_create', 'author_sync'}:
        notes = {p: p.read_bytes() for p in (tmp_path / 'Authors').glob('*.md')}
        target = read_enrichment_target(tmp_path, paper_id); save_enrichment(target, authors=NAMES)
        assert all(p.read_bytes() == data for p, data in notes.items())


@pytest.mark.parametrize('yaml_value', ['&empty []', '*empty'])
def test_author_yaml_alias_is_rejected_without_files(tmp_path, yaml_value):
    paper_id, path = without_authors(tmp_path)
    text = path.read_text().replace('authors: []', 'empty: &empty []\nauthors: *empty' if yaml_value == '*empty' else 'authors: &empty []')
    path.write_text(text); before = path.read_bytes()
    with pytest.raises(ValueError, match='authors YAML sequence'): save_enrichment(read_enrichment_target(tmp_path, paper_id), authors=NAMES)
    assert path.read_bytes() == before and not (tmp_path / 'Authors').exists()


@pytest.mark.parametrize('valid_abstract', [False, True])
def test_invalid_creators_do_not_discard_valid_abstract(tmp_path, valid_abstract):
    paper_id, path = without_authors(tmp_path, missing_abstract=valid_abstract)
    capture = CaptureCoordinator(); capture.heartbeat(connector_version='new', zotero_reachable=False, enrichment_capable=True)
    enrich = PaperEnrichment(capture); enrich.start(tmp_path, paper_id); cmd = enrich.claim(); before = path.read_bytes()
    item = {'item_type': 'journalArticle', 'doi': '10.5555/decision', 'creators': [{'creatorType': 'author', 'lastName': 'Valid'}, {'creatorType': 'author'}]}
    if valid_abstract: item['abstract'] = 'Valid Abstract'
    assert enrich.result(tmp_path, cmd, [item])['phase'] == ('Completed' if valid_abstract else 'Failed')
    assert not (tmp_path / 'Authors').exists()
    if not valid_abstract:
        assert path.read_bytes() == before
    if valid_abstract:
        result = enrich.state(paper_id)
        assert result['phase'] == 'Completed' and result['missing_authors']
        assert not (tmp_path / 'Authors').exists()


def test_author_enrichment_reports_committed_paper_sync_warning(tmp_path, monkeypatch):
    import literature_monitor.application.paper_enrichment as writing
    from literature_monitor.safe_write import TextWriteCommittedError
    paper_id, path = without_authors(tmp_path)
    capture = CaptureCoordinator(); capture.heartbeat(connector_version='new', zotero_reachable=False, enrichment_capable=True)
    enrich = PaperEnrichment(capture); enrich.start(tmp_path, paper_id); cmd = enrich.claim()
    actual = writing.replace_regular_text_at_identity
    def committed(*args, **kwargs):
        actual(*args, **kwargs); raise TextWriteCommittedError('Paper fsync failed')
    monkeypatch.setattr(writing, 'replace_regular_text_at_identity', committed)
    enrich.result(tmp_path, cmd, [{'item_type': 'journalArticle', 'doi': '10.5555/decision', 'creators': CREATORS}])
    result = enrich.state(paper_id)
    assert result['phase'] == 'Completed' and 'Enrichment saved; filesystem sync failed' in result['message']
    assert not result['missing_authors']
    state = parse_paper_state(path, path.read_text(), tmp_path / 'Authors')
    assert [link.alias for link in state.author_links] == NAMES


def test_author_snapshot_conflict_creates_no_author(tmp_path):
    paper_id, path = without_authors(tmp_path)
    target = read_enrichment_target(tmp_path, paper_id)
    path.write_text(path.read_text() + 'External edit\n'); edited = path.read_bytes()
    with pytest.raises(ContentChangedError): save_enrichment(target, authors=NAMES)
    assert path.read_bytes() == edited and not (tmp_path / 'Authors').exists()


@pytest.mark.parametrize('value', [2024, '0000', '2024-00', '2024-13', '2023-02-29',
                                   '2024-02-30', '05-16', '2024-5', '2024-05-16T00:00:00'])
def test_invalid_paper_date_is_rejected_without_writes(tmp_path, value):
    import yaml
    paper_id, path = missing(tmp_path)
    original = path.read_text().replace("publication_date: '2026-09-20'", 'publication_date: null')
    path.write_text(original)
    target = read_enrichment_target(tmp_path, paper_id)
    with pytest.raises(ValueError):
        save_enrichment(target, publication_date=value)
    invalid = original.replace('publication_date: null', 'publication_date: ' + yaml.safe_dump(value).splitlines()[0])
    assert not parse_paper_state(path, invalid, tmp_path / 'Authors').updateable
    assert path.read_text() == original


def test_historical_yaml_date_and_null_are_read_without_precision_changes(tmp_path):
    paper_id, path = missing(tmp_path)
    original = path.read_text()
    historical = original.replace("publication_date: '2026-09-20'", 'publication_date: 2026-09-20')
    assert parse_paper_state(path, historical, tmp_path / 'Authors').publication_date == '2026-09-20'
    empty = historical.replace('publication_date: 2026-09-20', 'publication_date: null')
    assert parse_paper_state(path, empty, tmp_path / 'Authors').publication_date is None
