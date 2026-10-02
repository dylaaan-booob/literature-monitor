"""Frozen plans and the real Python parser for A5-authenticated A7 evidence."""

from dataclasses import replace
from datetime import date
import json
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest

from literature_monitor.application.acquisition import AcquisitionClass, AcquisitionTask
from literature_monitor.browser_acquisition import (
    BrowserEvidenceError, PublisherState, download_evidence, navigation_plan,
    safe_runtime_url, xmu_fallback_url,
)
from literature_monitor.models import PaperVersion, VersionKind
from literature_monitor.web.browser_handoff import AuthenticatedBrowserEvent


@pytest.fixture
def task():
    return AcquisitionTask(uuid4(), uuid4(), '10.5705/ss.202024.0215', 'ITEMKEY1',
        PaperVersion(source='crossref', identifier='10.5705/ss.202024.0215',
                     kind=VersionKind.JOURNAL_FINAL, url='https://publisher.example/article', date=date(2024, 1, 1)),
        AcquisitionClass.PUBLISHED, 'server')


def payload(task):
    return {'doi': task.doi, 'download_id': 7, 'route': 'direct', 'ownership': 'task_navigation',
            'navigation_url': str(task.target_version.url), 'path': '/Users/user/Downloads/paper.pdf',
            'url': 'https://publisher.example/paper.pdf', 'final_url': 'https://publisher.example/paper.pdf',
            'referrer': str(task.target_version.url), 'mime': 'application/pdf', 'total_bytes': 123,
            'file_size': 123, 'category': '', 'version_labels': ['published'],
            'manifestation': task.target_version.model_dump(mode='json'),
            'navigation_time': 1000, 'start_time': 2000, 'state': 'complete', 'observed_doi': task.doi}


def test_published_always_starts_with_canonical_doi(task):
    plan = navigation_plan(task)
    assert plan.direct_url == 'https://doi.org/10.5705/ss.202024.0215'
    assert plan.target_version == task.target_version
    assert set(plan.message()) == {'task_id', 'doi', 'acquisition_class', 'target_version', 'direct_url'}
    assert 'zotero' not in json.dumps(plan.message()).lower()


def test_doi_reserved_characters_are_encoded_as_path_not_query(task):
    url = navigation_plan(replace(task, doi="10.1000/test(a)?#b")).direct_url
    assert url == 'https://doi.org/10.1000/test%28a%29%3F%23b'
    assert not urlsplit(url).query and not urlsplit(url).fragment


@pytest.mark.parametrize('kind,acquisition_class', [
    (VersionKind.ACCEPTED_MANUSCRIPT, AcquisitionClass.ACCEPTED_MANUSCRIPT),
    (VersionKind.PREPRINT, AcquisitionClass.PREPRINT),
])
def test_nonpublished_uses_only_frozen_manifestation(task, kind, acquisition_class):
    frozen = replace(task, target_version=task.target_version.model_copy(update={'kind': kind}), acquisition_class=acquisition_class)
    assert navigation_plan(frozen).direct_url == str(frozen.target_version.url)
    for target in (None, 'https://doi.org/10.5705/ss.202024.0215'):
        bad = replace(frozen, target_version=PaperVersion(source='test', identifier='id', kind=kind, url=target))
        with pytest.raises(BrowserEvidenceError):
            navigation_plan(bad)


@pytest.mark.parametrize('state', [PublisherState.ACCESSIBLE, PublisherState.HUMAN_REQUIRED])
def test_login_or_available_is_not_exhausted(task, state):
    with pytest.raises(BrowserEvidenceError):
        xmu_fallback_url(task, state)


def test_exact_xmu_context_after_published_exhaustion(task):
    url = urlsplit(xmu_fallback_url(task, PublisherState.EXHAUSTED))
    assert (url.scheme, url.hostname, url.path) == ('https', 'resolver.ebsco.com', '/c/45yels/result')
    assert parse_qs(url.query) == {'rft_id': ['info:doi/' + task.doi], 'x-opid': ['45yels'],
        'customer': ['s1215021'], 'group': ['main'], 'profile': ['ftf']}
    for acquisition_class, kind in [(AcquisitionClass.PREPRINT, VersionKind.PREPRINT),
                                    (AcquisitionClass.ACCEPTED_MANUSCRIPT, VersionKind.ACCEPTED_MANUSCRIPT)]:
        with pytest.raises(BrowserEvidenceError):
            xmu_fallback_url(replace(task, acquisition_class=acquisition_class,
                target_version=task.target_version.model_copy(update={'kind': kind})), PublisherState.EXHAUSTED)


@pytest.mark.parametrize('url', ['http://localhost/a', 'http://127.0.0.1/a', 'http://10.0.0.1/a',
                                'file:///tmp/a', 'https://user:password@example.org/a',
                                'https://example.org/a#secret', 'https://example.org/a\n', 'https://example.org\\evil'])
def test_unsafe_transport_targets_rejected_without_reflection(url):
    with pytest.raises(BrowserEvidenceError) as error:
        safe_runtime_url(url)
    assert url not in str(error.value)


def test_download_parser_keeps_ephemeral_evidence_private(task):
    body = payload(task)
    body['final_url'] += '?signed=runtime-secret'
    event = AuthenticatedBrowserEvent(task.task_id, 'tab-12', 'download_candidate', body)
    evidence = download_evidence(event, task, 'tab-12')
    assert evidence.manifestation == task.target_version
    assert evidence.final_url.endswith('runtime-secret')
    assert 'runtime-secret' not in repr(evidence)
    assert 'Downloads' not in repr(evidence)
    with pytest.raises(Exception):
        evidence.doi = 'changed'


@pytest.mark.parametrize('changes', [
    {'doi': '10.1000/wrong'}, {'state': 'in_progress'}, {'state': 'interrupted'},
    {'ownership': 'recent_pdf'}, {'file_size': 0}, {'download_id': True},
    {'start_time': 999}, {'start_time': 11001}, {'navigation_url': 'https://unrelated.example/'},
    {'version_labels': ['uncertain']}, {'category': 'Other'}, {'extra_secret': 'secret'},
    {'observed_doi': '10.1000/wrong'},
    {'path': '/tmp/secret\x00file'}, {'url': 'file:///tmp/a'},
    {'referrer': None},
])
def test_download_parser_rejects_unqualified_envelopes(task, changes):
    body = payload(task) | changes
    with pytest.raises(BrowserEvidenceError):
        download_evidence(AuthenticatedBrowserEvent(task.task_id, 'tab-12', 'download_candidate', body), task, 'tab-12')


def test_download_parser_rejects_wrong_task_or_claimed_tab(task):
    for event in (AuthenticatedBrowserEvent(uuid4(), 'tab-12', 'download_candidate', payload(task)),
                  AuthenticatedBrowserEvent(task.task_id, 'tab-99', 'download_candidate', payload(task))):
        with pytest.raises(BrowserEvidenceError):
            download_evidence(event, task, 'tab-12')


def test_resolver_provenance_is_narrow(task):
    body = payload(task) | {'route': 'xmu', 'category': 'FullText'}
    event = AuthenticatedBrowserEvent(task.task_id, 'tab-12', 'download_candidate', body)
    assert download_evidence(event, task, 'tab-12').category == 'FullText'
    for category in ('Other', 'SearchEngines', 'DocumentDelivery'):
        with pytest.raises(BrowserEvidenceError):
            download_evidence(replace(event, payload=body | {'category': category}), task, 'tab-12')


RECORD_URL = 'https://research.ebsco.com/c/testcontext/search/details/testrecord'


def blob_payload(task):
    return payload(task) | {
        'route': 'xmu', 'category': 'SmartLinks', 'ownership': 'user_arm',
        'navigation_url': RECORD_URL, 'referrer': RECORD_URL,
        'url': None, 'final_url': None, 'transport_kind': 'blob',
        'download_origin': 'https://research.ebsco.com', 'provider_record_url': RECORD_URL,
    }


def test_blob_parser_keeps_transport_typed_and_token_free(task):
    evidence = download_evidence(AuthenticatedBrowserEvent(task.task_id, 'tab-12',
        'download_candidate', blob_payload(task)), task, 'tab-12')
    assert evidence.transport_kind == 'blob' and evidence.ownership == 'user_arm'
    assert evidence.url is None and evidence.final_url is None
    assert evidence.download_origin == 'https://research.ebsco.com'
    assert evidence.provider_record_url == evidence.navigation_url
    assert 'research.ebsco.com' not in repr(evidence)
    assert 'blob:' not in repr(evidence)
    with pytest.raises(Exception):
        evidence.download_origin = 'https://other.example'


@pytest.mark.parametrize('changes', [
    {'ownership': 'task_navigation'}, {'ownership': 'extension_id'},
    {'download_origin': 'http://research.ebsco.com'},
    {'download_origin': 'https://other.example'}, {'download_origin': 'null'},
    {'download_origin': 'https://research.ebsco.com/path'},
    {'url': 'blob:https://research.ebsco.com/secret-token'},
    {'final_url': RECORD_URL}, {'url': RECORD_URL},
    {'referrer': RECORD_URL + '/other'},
    {'provider_record_url': RECORD_URL + 'other'},
    {'provider_record_url': 'https://research.ebsco.com/search'},
    {'start_time': 11001}, {'transport_kind': 'opaque'},
])
def test_blob_parser_rejects_unsafe_or_inconsistent_transport(task, changes):
    with pytest.raises(BrowserEvidenceError):
        download_evidence(AuthenticatedBrowserEvent(task.task_id, 'tab-12',
            'download_candidate', blob_payload(task) | changes), task, 'tab-12')


def test_blob_without_referrer_retains_explicit_arm_origin_evidence(task):
    evidence = download_evidence(AuthenticatedBrowserEvent(task.task_id, 'tab-12',
        'download_candidate', blob_payload(task) | {'referrer': ''}), task, 'tab-12')
    assert evidence.referrer == '' and evidence.ownership == 'user_arm'


def provider_action_payload(task):
    return blob_payload(task) | {
        'attribution': 'ebsco_pdf_action', 'arm_time': 2000, 'action_time': 3000,
        'start_time': 27000, 'version_labels': ['published'],
    }


def test_verified_provider_action_accepts_delayed_start_with_explicit_clocks(task):
    evidence = download_evidence(AuthenticatedBrowserEvent(task.task_id, 'tab-12',
        'download_candidate', provider_action_payload(task)), task, 'tab-12')
    assert evidence.ownership == 'user_arm' and evidence.attribution == 'ebsco_pdf_action'
    assert (evidence.navigation_time, evidence.arm_time, evidence.action_time, evidence.start_time) == (1000, 2000, 3000, 27000)


@pytest.mark.parametrize('changes', [
    {'attribution': 'other'}, {'action_time': None}, {'action_time': True},
    {'action_time': '3000'}, {'action_time': 1999}, {'arm_time': -1},
    {'arm_time': True}, {'arm_time': 999}, {'action_time': 12001},
    {'start_time': 2999}, {'start_time': 123001}, {'route': 'direct', 'category': ''},
    {'category': 'Other'}, {'provider_record_url': None}, {'observed_doi': None},
    {'version_labels': []}, {'version_labels': ['aam']}, {'version_labels': ['published', 'preprint']},
    {'manifestation': None}, {'download_origin': 'https://other.example'},
    {'referrer': 'https://research.ebsco.com/'}, {'file_size': 0}, {'state': 'in_progress'},
    {'unexpected': 'field'},
])
def test_provider_action_rejects_incomplete_or_unbounded_proof(task, changes):
    with pytest.raises(BrowserEvidenceError):
        download_evidence(AuthenticatedBrowserEvent(task.task_id, 'tab-12',
            'download_candidate', provider_action_payload(task) | changes), task, 'tab-12')


@pytest.mark.parametrize('missing', ['arm_time', 'action_time'])
def test_provider_action_requires_both_timestamps(task, missing):
    body = provider_action_payload(task); del body[missing]
    with pytest.raises(BrowserEvidenceError):
        download_evidence(AuthenticatedBrowserEvent(task.task_id, 'tab-12',
            'download_candidate', body), task, 'tab-12')


def test_generic_arm_does_not_inherit_provider_preparation_window(task):
    with pytest.raises(BrowserEvidenceError):
        download_evidence(AuthenticatedBrowserEvent(task.task_id, 'tab-12',
            'download_candidate', blob_payload(task) | {'start_time': 25000}), task, 'tab-12')


def test_provider_action_requires_frozen_manifestation_and_binding(task):
    body = provider_action_payload(task)
    for changed in [body | {'manifestation': body['manifestation'] | {'identifier': 'other'}},
                    body | {'manifestation': body['manifestation'] | {'kind': 'preprint'}}]:
        with pytest.raises(BrowserEvidenceError):
            download_evidence(AuthenticatedBrowserEvent(task.task_id, 'tab-12',
                'download_candidate', changed), task, 'tab-12')
    for event in [AuthenticatedBrowserEvent(UUID('33333333-3333-4333-8333-333333333333'), 'tab-12', 'download_candidate', body),
                  AuthenticatedBrowserEvent(task.task_id, 'tab-99', 'download_candidate', body)]:
        with pytest.raises(BrowserEvidenceError):
            download_evidence(event, task, 'tab-12')


def test_provider_action_accepts_finite_boundary(task):
    evidence = download_evidence(AuthenticatedBrowserEvent(task.task_id, 'tab-12',
        'download_candidate', provider_action_payload(task) | {'start_time': 123000}), task, 'tab-12')
    assert evidence.start_time - evidence.action_time == 120000


def test_python_accepts_actual_shipped_worker_provider_payload():
    # Cross-language contract check, with simulated Chrome and no live writes.
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which('node')
    if node is None:
        pytest.skip('No existing JavaScript runtime for protocol integration.')
    result = subprocess.run([node, str(Path(__file__).with_name('browser_companion_runtime.cjs')),
                             '--provider-evidence'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    body = json.loads(next(line.removeprefix('Provider evidence: ') for line in result.stdout.splitlines()
                           if line.startswith('Provider evidence: ')))
    frozen = AcquisitionTask(UUID('22222222-2222-4222-8222-222222222222'), uuid4(), body['doi'], 'ITEMKEY1',
        PaperVersion.model_validate(body['manifestation']), AcquisitionClass.PUBLISHED, 'server')
    evidence = download_evidence(AuthenticatedBrowserEvent(frozen.task_id, 'tab-42',
        'download_candidate', body), frozen, 'tab-42')
    assert evidence.attribution == 'ebsco_pdf_action'
    assert evidence.navigation_time < evidence.arm_time < evidence.action_time < evidence.start_time
    assert evidence.start_time - evidence.action_time == 24000
