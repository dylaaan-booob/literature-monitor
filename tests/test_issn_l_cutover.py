"""Production topology and end-to-end attribution regression tests for SPEC §41."""
from dataclasses import replace
from datetime import date
from pathlib import Path
import json

import httpx
import pytest

from literature_monitor.application import monitor
from literature_monitor.application.candidate_eligibility import openalex_attribution
from literature_monitor.application.crossref_retrieval import CrossrefSupplementResult
from literature_monitor.config import JournalConfig, render_journal_whitelist_text
from literature_monitor.coverage import summarize_coverage
from literature_monitor.crossref import CrossrefDiscoveryResult, plan_crossref_queries
from literature_monitor.openalex import OpenAlexClient, ResolvedSource, DiscoveryResult
from literature_monitor.crossref import CrossrefClient


@pytest.mark.parametrize('source_available', [True, False])
def test_production_resolves_once_then_shares_alias_evidence_without_recursion(tmp_path, monkeypatch, source_available):
    journal = JournalConfig(issn_l='0006-341X', name='Unrelated configured display name')
    (tmp_path / 'list.md').write_text(render_journal_whitelist_text(None, (journal,), path=tmp_path / 'list.md'))
    config = tmp_path / 'monitor.yaml'
    config.write_text('keyword_expression: statistics\nfrom_month: 2026-01\nto_month: 2026-01\n')
    requests = []
    raw_source = json.loads((Path(__file__).parent / 'fixtures/openalex/source_biometrics.json').read_text())
    raw_source.update(issn_l='1541-0420', issn=['0006-341X', '1541-0420', '1526-5489'])
    raw_work = json.loads((Path(__file__).parent / 'fixtures/openalex/works_page_1.json').read_text())['results'][0]
    raw_work.update(doi='https://doi.org/10.5555/a3', title='Statistics', publication_date='2026-01-01')
    crossref_work = {'DOI': '10.5555/a3', 'ISSN': ['1541-0420' if source_available else '0006-341X', '1932-6157', '1526-5489'],
                     'indexed': {'date-time': '2026-01-02T00:00:00Z'}, 'title': ['Statistics'],
                     'container-title': ['Other display metadata'], 'type': 'journal-article',
                     'author': [{'given': 'Ada', 'family': 'Author'}], 'published': {'date-parts': [[2026, 1, 1]]}}
    def oa_response(request):
        requests.append(('oa', request))
        if request.url.path == '/sources':
            return httpx.Response(200, json={'meta': {'count': 1}, 'results': [raw_source]}) if source_available else httpx.Response(401)
        assert request.url.path == '/works'
        return httpx.Response(200, json={'meta': {'count': 1, 'next_cursor': None}, 'results': [raw_work]})
    def cr_response(request):
        requests.append(('cr', request))
        assert request.url.path == '/v1/works'
        return httpx.Response(200, json={'status': 'ok', 'message-type': 'work-list',
                                       'message': {'total-results': 1, 'next-cursor': 'done', 'items': [crossref_work]}})
    monkeypatch.setattr(monitor, 'OpenAlexClient', lambda **kw: OpenAlexClient(transport=httpx.MockTransport(oa_response), sleep=lambda _: None))
    monkeypatch.setattr(monitor, 'CrossrefClient', lambda **kw: CrossrefClient(transport=httpx.MockTransport(cr_response), sleep=lambda _: None))
    result = monitor.run_monitor(config)
    assert result.created_papers == 1
    sources = [request for provider, request in requests if provider == 'oa' and request.url.path.startswith('/sources')]
    assert len(sources) == 1 and requests[0][1] is sources[0]
    assert len([request for provider, request in requests if provider == 'oa' and request.url.path == '/works']) == int(source_available)
    queries = [request.url.params['filter'] for provider, request in requests if provider == 'cr' and 'select' in request.url.params]
    assert queries and all('1932-6157' not in q and '1526-5489' not in q for q in queries)
    query_ids = [unit.issn for unit in result.coverage if unit.provider == 'crossref']
    assert query_ids == (['0006-341X', '1541-0420'] if source_available else ['0006-341X'])
    assert journal.issn_l == '0006-341X'






def test_query_plan_keeps_canonical_first_and_ignores_provider_candidate():
    journal=JournalConfig(name='Hint',issn_l='0006-341X')
    source=ResolvedSource(journal.issn_l,'https://openalex.org/S1','Canonical',('1541-0420','0006-341X','1541-0420'),'1932-6157')
    assert plan_crossref_queries((journal,),(source,))[0].query_aliases == ('0006-341X','1541-0420')
    assert plan_crossref_queries((journal,))[0].query_aliases == ('0006-341X',)
