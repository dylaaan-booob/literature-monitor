"""Deterministic classes/conflicts with real byte-validated private artifacts."""

from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from literature_monitor.application.acquisition import AcquisitionClass, AcquisitionTask
from literature_monitor.browser_acquisition import BrowserDownloadEvidence
from literature_monitor.models import PaperVersion, VersionKind
from literature_monitor.pdf_staging import stage_download
from literature_monitor.version_qualification import VersionQualificationError, qualify_pdf


@pytest.fixture
def qualified_inputs(tmp_path):
    tmp_path = tmp_path.resolve()
    source_dir = tmp_path/'downloads'; source_dir.mkdir()
    source = source_dir/'paper.pdf'; source.write_bytes(b'%PDF-1.7\nvalid bytes')
    temp = tmp_path/'staging'; temp.mkdir()
    version = PaperVersion(source='crossref', identifier='10.5705/ss.202024.0215',
        kind=VersionKind.JOURNAL_FINAL, url='https://publisher.example/work', date=date(2024, 1, 1))
    task = AcquisitionTask(uuid4(), uuid4(), version.identifier, 'ITEMKEY1', version, AcquisitionClass.PUBLISHED, 'server')
    evidence = BrowserDownloadEvidence(task.task_id, 'tab-12', task.doi, 7, 'direct',
        'task_navigation', str(version.url), str(source), 'https://publisher.example/file.pdf',
        'https://publisher.example/file.pdf', str(version.url), 'application/pdf',
        source.stat().st_size, source.stat().st_size, '', ('published',), version, 1000, 2000)
    with stage_download(evidence, project_dir=tmp_path/'project', workspace_dir=tmp_path/'workspace',
                        config_path=tmp_path/'config'/'monitor.yaml', temp_root=temp) as artifact:
        yield task, evidence, artifact


def qualify(task, evidence, artifact):
    return qualify_pdf(task, evidence, artifact, claimed_tab_binding='tab-12')


@pytest.mark.parametrize('kind', [VersionKind.JOURNAL_FINAL, VersionKind.JOURNAL_ONLINE])
def test_positive_published_evidence_and_exact_doi_qualify(qualified_inputs, kind):
    task, evidence, artifact = qualified_inputs
    version = task.target_version.model_copy(update={'kind': kind})
    assert qualify(replace(task, target_version=version), replace(evidence, manifestation=version), artifact).acquisition_class is AcquisitionClass.PUBLISHED


@pytest.mark.parametrize('labels', [('aam',), ('accepted author manuscript',), ('accepted manuscript',),
                                    ('preprint',), ('published', 'aam'), (), ('uncertain',)])
def test_published_rejects_explicit_lower_version_or_missing_evidence(qualified_inputs, labels):
    # SPEC example: no production DOI-specific branch is involved.
    task, evidence, artifact = qualified_inputs
    assert task.doi == '10.5705/ss.202024.0215'
    with pytest.raises(VersionQualificationError):
        qualify(task, replace(evidence, version_labels=labels), artifact)


@pytest.mark.parametrize('changes', [
    {'doi': '10.1000/wrong'}, {'task_id': uuid4()}, {'tab_binding': 'tab-99'},
    {'download_id': 8}, {'manifestation': None}, {'file_size': 1},
    {'observed_doi': '10.1000/wrong'},
    {'navigation_url': 'https://other.example/', 'referrer': 'https://other.example/'},
])
def test_published_rejects_identity_or_source_ambiguity(qualified_inputs, changes):
    task, evidence, artifact = qualified_inputs
    with pytest.raises(VersionQualificationError):
        qualify(task, replace(evidence, **changes), artifact)


@pytest.mark.parametrize('field,value', [('source', 'other'), ('identifier', 'other'),
    ('date', date(2025, 1, 1)), ('url', 'https://other.example/work'), ('kind', VersionKind.UNKNOWN)])
def test_complete_frozen_version_must_agree(qualified_inputs, field, value):
    task, evidence, artifact = qualified_inputs
    with pytest.raises(VersionQualificationError):
        qualify(task, replace(evidence, manifestation=evidence.manifestation.model_copy(update={field: value})), artifact)


@pytest.mark.parametrize('acquisition_class,kind,positive,conflicts', [
    (AcquisitionClass.ACCEPTED_MANUSCRIPT, VersionKind.ACCEPTED_MANUSCRIPT, 'aam', ['published', 'preprint']),
    (AcquisitionClass.PREPRINT, VersionKind.PREPRINT, 'preprint', ['published', 'aam']),
])
def test_nonpublished_requires_exact_manifestation_and_compatible_labels(qualified_inputs, acquisition_class, kind, positive, conflicts):
    task, evidence, artifact = qualified_inputs
    version = task.target_version.model_copy(update={'kind': kind, 'source': 'repository', 'identifier': 'exact-manifestation'})
    frozen = replace(task, target_version=version, acquisition_class=acquisition_class)
    matching = replace(evidence, manifestation=version, version_labels=(positive,))
    assert qualify(frozen, matching, artifact).acquisition_class is acquisition_class
    for labels in [(), *[(conflict,) for conflict in conflicts], (positive, conflicts[0])]:
        with pytest.raises(VersionQualificationError):
            qualify(frozen, replace(matching, version_labels=labels), artifact)
    with pytest.raises(VersionQualificationError):
        qualify(frozen, replace(matching, manifestation=version.model_copy(update={'identifier': 'wrong'})), artifact)


def test_unknown_class_mapping_and_unvalidated_bytes_reject(qualified_inputs):
    task, evidence, artifact = qualified_inputs
    with pytest.raises(VersionQualificationError):
        qualify(replace(task, target_version=task.target_version.model_copy(update={'kind': VersionKind.UNKNOWN})), evidence, artifact)
    artifact.path.write_bytes(b'HTML login page')
    with pytest.raises(VersionQualificationError):
        qualify(task, evidence, artifact)


def test_explicit_published_doi_evidence_can_qualify_missing_frozen_url(qualified_inputs):
    task, evidence, artifact = qualified_inputs
    version = task.target_version.model_copy(update={'url': None})
    frozen = replace(task, target_version=version)
    observed = replace(evidence, manifestation=version, observed_doi=task.doi)
    assert qualify(frozen, observed, artifact).acquisition_class is AcquisitionClass.PUBLISHED
    with pytest.raises(VersionQualificationError):
        qualify(frozen, replace(observed, observed_doi=None), artifact)


def test_no_doi_specific_exception_in_production():
    import literature_monitor.version_qualification as policy
    assert '10.5705' not in policy.__file__
    from pathlib import Path
    assert '10.5705' not in Path(policy.__file__).read_text()


def test_real_shaped_xmu_blob_record_qualifies_only_with_published_evidence(qualified_inputs):
    from literature_monitor.browser_acquisition import download_evidence
    from literature_monitor.web.browser_handoff import AuthenticatedBrowserEvent
    from test_browser_acquisition import blob_payload
    task, original, artifact = qualified_inputs
    body = blob_payload(task) | {'path': original.path, 'file_size': artifact.byte_count,
                                'total_bytes': artifact.byte_count}
    evidence = download_evidence(AuthenticatedBrowserEvent(task.task_id, 'tab-12',
        'download_candidate', body), task, 'tab-12')
    assert qualify(task, evidence, artifact).source_kind == 'xmu'
    for changes in ({'version_labels': ()}, {'version_labels': ('aam',)},
                    {'version_labels': ('preprint',)}, {'version_labels': ('published', 'aam')},
                    {'observed_doi': '10.9999/wrong'}, {'provider_record_url': None},
                    {'download_origin': 'https://other.example'}, {'transport_kind': 'opaque'},
                    {'ownership': 'task_navigation'}, {'url': evidence.navigation_url},
                    {'provider_record_url': evidence.navigation_url + 'other'},
                    {'navigation_url': evidence.navigation_url + 'other'},
                    {'manifestation': task.target_version.model_copy(update={'identifier': 'other'})}):
        with pytest.raises(VersionQualificationError):
            qualify(task, replace(evidence, **changes), artifact)
    assert artifact.validate() and artifact.path.read_bytes().startswith(b'%PDF')
