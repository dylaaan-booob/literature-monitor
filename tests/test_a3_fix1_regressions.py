"""A3 interim metadata ownership and global transient-alias reconciliation."""
from dataclasses import replace
from datetime import date, datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from literature_monitor.application.settings import load_settings, save_settings, SettingsSaveOutcome
from literature_monitor.application.journal_import import preview_journal_import, apply_journal_import
from literature_monitor.application.candidate_eligibility import filter_candidate_evidence, CandidateEligibility
from literature_monitor.config import JournalConfig, PublisherConfig, render_journal_whitelist_text, render_settings_list_text, load_config
from literature_monitor.crossref import plan_crossref_queries, discover_crossref_journals, CrossrefDiscoveryResult
from literature_monitor.openalex import ResolvedSource, DiscoveryResult
from literature_monitor.web.app import create_app
from test_application_settings import write_valid_settings_files
from test_web_run_settings import browser_settings_submission

A, B, X = '0006-341X', '0090-5364', '0162-1459'
DAY = date(2026, 1, 1)


def configured(tmp_path):
    config, path = write_valid_settings_files(tmp_path)
    journals = (JournalConfig(issn_l=A, name='Canonical A', publisher_id='P1', group='Stats'),
                JournalConfig(issn_l=B, name='Canonical B', publisher_id='P2'))
    path.write_text(render_settings_list_text(path.read_text(), journals, (PublisherConfig(publisher_id='P1',name='Pub A'),PublisherConfig(publisher_id='P2',name='Pub B')), path=path))
    return config, path, load_settings(config)


@pytest.mark.parametrize('change', ['name', 'publisher', 'new', 'identity'])
def test_save_rejects_unresolved_identity_or_machine_metadata_before_any_write(tmp_path, monkeypatch, change):
    config, path, opened = configured(tmp_path)
    before = config.read_bytes(), path.read_bytes()
    first, second = opened.draft.journals
    if change == 'name':
        journals = (first.model_copy(update={'name': 'Arbitrary text'}), second)
    elif change == 'publisher':
        journals = (first.model_copy(update={'publisher_id': 'https://openalex.org/P99'}), second)
    elif change == 'new':
        journals = (first, second, JournalConfig(issn_l=X, name='Unresolved hint'))
    else:
        journals = (first.model_copy(update={'issn_l': X}), second)
    def forbidden(*args, **kwargs):
        pytest.fail('metadata rejection must occur before writes or network')
    monkeypatch.setattr('literature_monitor.application.settings._write_snapshot_target', forbidden)
    if change in ('name', 'publisher'):
        monkeypatch.setattr(httpx.Client, 'request', forbidden)
    else:
        from literature_monitor.openalex import OpenAlexClient, OpenAlexRequestError
        def unavailable(*args, **kwargs):
            raise OpenAlexRequestError('requires metadata resolution: unavailable')
        monkeypatch.setattr(OpenAlexClient, '_request_json', unavailable)
    result = save_settings(config, replace(opened.draft, journals=journals, name='Also unsaved monitor'))
    assert result.outcome is SettingsSaveOutcome.INVALID_DRAFT
    assert any('metadata' in issue.message for issue in result.issues)
    assert not result.journal_written and not result.monitor_written
    assert (config.read_bytes(), path.read_bytes()) == before
    assert result.monitor_revision == opened.draft.monitor_revision
    assert result.journal_revision == opened.draft.journal_revision


@pytest.mark.parametrize('change', ['group', 'order', 'removal'])
def test_ordinary_save_preserves_machine_metadata_without_network(tmp_path, monkeypatch, change):
    config, path, opened = configured(tmp_path)
    first, second = opened.draft.journals
    journals = ((first.model_copy(update={'group': 'Methods'}), second) if change == 'group'
                else (second, first) if change == 'order' else (first,))
    monkeypatch.setattr(httpx.Client, 'request', lambda *a, **k: pytest.fail('ordinary Save must stay offline'))
    result = save_settings(config, replace(opened.draft, journals=journals))
    assert result.outcome is SettingsSaveOutcome.SAVED
    assert load_config(config).journals == journals


@pytest.mark.parametrize('new_identity', [False, True])
def test_import_apply_is_draft_only_and_save_enforces_resolution(tmp_path, new_identity, monkeypatch):
    from literature_monitor.openalex import OpenAlexClient, OpenAlexRequestError
    def unavailable(*args, **kwargs):
        raise OpenAlexRequestError("requires metadata resolution: unavailable")
    monkeypatch.setattr(OpenAlexClient, "_request_json", unavailable)
    config, path, opened = configured(tmp_path)
    before = config.read_bytes(), path.read_bytes()
    text = f'Journal,ISSN-L,Group\nArbitrary hint,{X if new_identity else A},Methods\n'
    plan = preview_journal_import(opened.draft, text)
    assert plan.can_apply and plan.metadata_resolution_required is new_identity
    result = apply_journal_import(opened.draft, text)
    assert result.applied and (config.read_bytes(), path.read_bytes()) == before
    assert result.draft.journals[0].name == 'Canonical A'
    assert result.draft.journals[0].publisher_id == 'https://openalex.org/P1'
    saved = save_settings(config, result.draft)
    assert saved.outcome is (SettingsSaveOutcome.INVALID_DRAFT if new_identity else SettingsSaveOutcome.SAVED)
    if new_identity:
        assert (config.read_bytes(), path.read_bytes()) == before
    else:
        assert load_config(config).journals[0].group == 'Methods'


@pytest.mark.parametrize('mutation', ['journal_name', 'journal_publisher_id'])
def test_web_save_cannot_persist_machine_metadata_tampering_or_new_import(tmp_path, mutation, monkeypatch):
    from literature_monitor.openalex import OpenAlexClient, OpenAlexRequestError
    def unavailable(*args, **kwargs):
        raise OpenAlexRequestError("requires metadata resolution: unavailable")
    monkeypatch.setattr(OpenAlexClient, "_request_json", unavailable)
    config, path, _ = configured(tmp_path)
    before = config.read_bytes(), path.read_bytes()
    with TestClient(create_app(config), base_url='http://localhost') as client:
        data = browser_settings_submission(client.get('/settings').text)
        data[mutation][0] = 'Tampered name' if mutation == 'journal_name' else 'https://openalex.org/P99'
        saved = client.post('/settings/save', data=data)
        assert saved.status_code == 200 and 'HX-Trigger' not in saved.headers
        assert 'metadata' in saved.text
    assert (config.read_bytes(), path.read_bytes()) == before


def evidence(*, configured_alias=False, same_name=False, same_source=False):
    journals = (JournalConfig(issn_l=A, name='Same' if same_name else 'A', publisher_id='P1'),
                JournalConfig(issn_l=B, name='Same' if same_name else 'B', publisher_id='P2'))
    alias = A if configured_alias else X
    sources = (ResolvedSource(A, 'https://openalex.org/S1', 'Unrelated metadata', (A, alias), alias, 'https://openalex.org/P10'),
               ResolvedSource(B, 'https://openalex.org/S1' if same_source else 'https://openalex.org/S2', 'Other metadata', (B, alias), alias, 'https://openalex.org/P20'))
    return journals, sources


@pytest.mark.parametrize('same_name', [False, True])
@pytest.mark.parametrize('configured_alias', [False, True])
def test_global_alias_ownership_ignores_names_publishers_and_provider_candidate(same_name, configured_alias):
    journals, sources = evidence(configured_alias=configured_alias, same_name=same_name)
    plans = plan_crossref_queries(journals, sources)
    assert [p.query_aliases for p in plans] == [(A,), (B,)]
    assert plans[1].excluded_aliases == (A if configured_alias else X,)
    assert bool(plans[0].diagnostics) is (not configured_alias)
    assert plans[1].diagnostics
    reversed_plans = plan_crossref_queries(tuple(reversed(journals)), tuple(reversed(sources)))
    assert {p.journal.issn_l: p for p in plans} == {p.journal.issn_l: p for p in reversed_plans}


def test_unique_alias_and_missing_source_keep_bounded_query_plan():
    journals, sources = evidence()
    plans = plan_crossref_queries(journals, sources[:1])
    assert plans[0].query_aliases == (A, X) and not plans[0].diagnostics
    assert plans[1].query_aliases == (B,)


def test_shared_source_remains_canonical_only_and_conflicted():
    journals, sources = evidence(same_source=True)
    plans = plan_crossref_queries(journals, sources)
    assert [p.query_aliases for p in plans] == [(A,), (B,)]
    assert all(p.excluded_aliases == (X,) and p.diagnostics for p in plans)


@pytest.mark.parametrize('lower_level', [False, True])
def test_discovery_surfaces_conflict_without_request_or_coverage_for_excluded_alias(lower_level):
    journals, sources = evidence()
    if lower_level:
        class EmptyClient:
            def iter_journal_work_pages(self, issn, *args, **kwargs):
                requested.append(issn)
                yield {'message': {'items': []}}
        requested = []
        result = discover_crossref_journals(EmptyClient(), journals, DAY, DAY,
            retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc), resolved_sources=sources)
        assert requested == [A, B]
    else:
        from test_application_crossref_retrieval import HTTP, work_list
        http = HTTP(lambda r: work_list())
        result = http.retrieval().discover(journals, DAY, DAY, resolved_sources=sources).discovery
        assert all(X not in r.url.params['filter'] for r in http.requests)
    assert [u.issn for u in result.coverage] == [A, B]
    assert all(u.status.value == 'COMPLETE' for u in result.coverage)
    assert len([i for i in result.issues if i.stage == 'alias_reconciliation']) == 2
    assert all(i.severity.value == 'warning' and i.issn == X for i in result.issues)


@pytest.mark.parametrize('configured_alias', [False, True])
def test_candidate_uses_global_plan_without_double_attribution_from_excluded_alias(configured_alias, monkeypatch):
    from test_application_candidate_eligibility import cr, discovery, supplementation
    import literature_monitor.application.candidate_eligibility as eligibility
    journals, sources = evidence(configured_alias=configured_alias)
    alias = A if configured_alias else X
    record = cr(issns=(alias,))
    units = tuple(discovery((record,), j).units[0] for j in journals)
    crossref = CrossrefDiscoveryResult((record,), (), tuple(u.coverage for u in units), units)
    calls = []
    real_plan = eligibility.plan_crossref_queries
    def plan(js, ss):
        calls.append(tuple(j.issn_l for j in js))
        return real_plan(js, ss)
    monkeypatch.setattr(eligibility, 'plan_crossref_queries', plan)
    result = filter_candidate_evidence(DiscoveryResult(sources, (), ()), crossref, supplementation(), journals)
    assert calls == [(A, B)]
    assert set(result.monitor_journal_issns.values()) == ({(A,)} if configured_alias else {()})
    decisions = next(iter(result.attribution.values()))
    assert sum(d.strong for d in decisions) == int(configured_alias)
    assert decisions[-1].state is CandidateEligibility.SCOPE_DISPUTED


def test_configured_owner_precedence_survives_its_source_failure():
    journals, sources = evidence(configured_alias=True)
    plans = plan_crossref_queries(journals, sources[1:])
    assert plans[0].query_aliases == (A,) and not plans[0].diagnostics
    assert plans[1].query_aliases == (B,) and plans[1].excluded_aliases == (A,)
    assert 'durable configured owner' in plans[1].diagnostics[0].message


@pytest.mark.parametrize('changed_file', ['monitor', 'journals'])
def test_revision_conflict_still_precedes_metadata_rejection(tmp_path, changed_file):
    config, path, opened = configured(tmp_path)
    changed = config if changed_file == 'monitor' else path
    changed.write_bytes(changed.read_bytes() + b'\n')
    before = config.read_bytes(), path.read_bytes()
    draft = replace(opened.draft, journals=(opened.draft.journals[0].model_copy(update={'name': 'Tampered'}),))
    result = save_settings(config, draft)
    assert result.outcome is SettingsSaveOutcome.REVISION_CONFLICT
    assert (config.read_bytes(), path.read_bytes()) == before


def test_excluded_alias_supplement_cannot_attribute_the_crossref_record_to_both_targets():
    from test_application_candidate_eligibility import cr, oa, supplementation, supplement
    journals, sources = evidence()
    record = cr(issns=(X,))
    anchors = (oa(journal=journals[0]), oa(journal=journals[1], source_number=2, number=2))
    units = tuple(supplement(anchor, record) for anchor in anchors)
    candidates = filter_candidate_evidence(DiscoveryResult(sources, anchors, ()), CrossrefDiscoveryResult((), ()),
                                          supplementation(*units), journals)
    crossref_ref = next(ref for ref in candidates.monitor_journal_issns if ref.provider == 'crossref')
    assert candidates.monitor_journal_issns[crossref_ref] == ()
    assert not any(d.strong for decisions in candidates.attribution.values() for d in decisions)


def test_production_collision_resolution_adds_no_source_lookup(tmp_path, monkeypatch):
    from literature_monitor.application import monitor
    from literature_monitor.openalex import OpenAlexClient
    from literature_monitor.crossref import CrossrefClient
    from test_application_crossref_retrieval import work_list
    journals, sources = evidence(same_name=True)
    (tmp_path / 'list.md').write_text(render_journal_whitelist_text(None, journals, path=tmp_path / 'list.md'))
    config = tmp_path / 'monitor.yaml'
    config.write_text('keyword_expression: statistics\nfrom_date: 2026-01-01\nto_date: 2026-01-01\n')
    requests = []
    def oa_response(request):
        requests.append(('oa', request))
        if request.url.path == '/sources':
            return httpx.Response(200, json={'meta': {'count': 2}, 'results': [
                {'id': s.openalex_id, 'display_name': s.display_name, 'type': 'journal',
                 'issn': list(s.aliases), 'issn_l': s.provider_issn_l} for s in sources]})
        assert request.url.path == '/works'
        return httpx.Response(200, json={'meta': {'count': 0, 'next_cursor': None}, 'results': []})
    def cr_response(request):
        requests.append(('cr', request))
        assert X not in request.url.params['filter']
        return httpx.Response(200, json=work_list())
    monkeypatch.setattr(monitor, 'OpenAlexClient', lambda **kw: OpenAlexClient(transport=httpx.MockTransport(oa_response)))
    monkeypatch.setattr(monitor, 'CrossrefClient', lambda **kw: CrossrefClient(transport=httpx.MockTransport(cr_response)))
    result = monitor._run_canonical_core(config)
    assert len([r for provider, r in requests if provider == 'oa' and r.url.path.startswith('/sources')]) == 1
    assert [u.issn for u in result.coverage if u.provider == 'crossref'] == [A, B]
    assert len([i for i in result.warnings if i.stage == 'alias_reconciliation']) == 2
    assert not (tmp_path / 'workspace').exists()
