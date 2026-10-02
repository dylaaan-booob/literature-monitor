"""Deterministic qualification against the complete frozen manifestation (§36.11)."""

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .application.acquisition import AcquisitionClass, AcquisitionTask
from .browser_acquisition import BrowserDownloadEvidence, VERSION_LABELS, ebsco_record_url, frozen_identity, safe_runtime_url
from .pdf_staging import StagedPdf


class VersionQualificationError(ValueError):
    pass


@dataclass(frozen=True)
class QualifiedPdf:
    artifact: StagedPdf = field(repr=False)
    acquisition_class: AcquisitionClass
    source_kind: str


_PUBLISHED = {'published', 'journal_final', 'journal_online'}
_ACCEPTED = {'aam', 'accepted author manuscript', 'accepted manuscript'}
_PREPRINT = {'preprint'}


def qualify_pdf(task: AcquisitionTask, evidence: BrowserDownloadEvidence,
                artifact: StagedPdf, *, claimed_tab_binding: str) -> QualifiedPdf:
    """No attachment permission is conferred; A8 must still guard all mutations.

    Explicit labels must describe this download, not merely its landing page.
    Published evidence needs both a matching frozen manifestation and a positive
    published label. Resolver category or DOI agreement alone is insufficient.
    """
    try:
        frozen_identity(task)
        if not isinstance(evidence, BrowserDownloadEvidence) or not isinstance(artifact, StagedPdf):
            raise ValueError
        if (evidence.task_id != task.task_id or artifact.task_id != task.task_id
                or evidence.tab_binding != claimed_tab_binding or evidence.doi != task.doi
                or artifact.download_id != evidence.download_id or artifact.byte_count != evidence.file_size
                or not artifact.validate()):
            raise ValueError
        if evidence.manifestation != task.target_version:
            raise ValueError
        if evidence.observed_doi is not None and evidence.observed_doi != task.doi:
            raise ValueError
        if evidence.transport_kind not in {'http', 'blob'}:
            raise ValueError
        if evidence.transport_kind == 'blob':
            navigation = urlsplit(safe_runtime_url(evidence.navigation_url))
            if evidence.ownership != 'user_arm' or evidence.url is not None or evidence.final_url is not None:
                raise ValueError
            if (navigation.scheme != 'https' or evidence.download_origin != f'https://{navigation.netloc}'
                    or evidence.referrer not in {'', evidence.navigation_url}):
                raise ValueError
            if evidence.route == 'xmu' and (
                    ebsco_record_url(evidence.provider_record_url) != evidence.navigation_url
                    or evidence.observed_doi != task.doi):
                raise ValueError
        # Require the frozen manifestation to occur in the download's observed
        # source/referrer chain, in addition to complete structured equality.
        target = str(task.target_version.url) if task.target_version.url is not None else None
        chain = {evidence.navigation_url, evidence.url, evidence.final_url, evidence.referrer}
        published_identity = (task.acquisition_class is AcquisitionClass.PUBLISHED
                              and evidence.observed_doi == task.doi)
        if not published_identity and (target is None or target not in chain):
            raise ValueError
        labels = set(evidence.version_labels)
        if not labels <= VERSION_LABELS:
            raise ValueError
        if task.acquisition_class is AcquisitionClass.PUBLISHED:
            if not labels & _PUBLISHED or labels & (_ACCEPTED | _PREPRINT):
                raise ValueError
        elif task.acquisition_class is AcquisitionClass.ACCEPTED_MANUSCRIPT:
            if not labels & _ACCEPTED or labels & (_PUBLISHED | _PREPRINT):
                raise ValueError
        elif task.acquisition_class is AcquisitionClass.PREPRINT:
            if not labels & _PREPRINT or labels & (_PUBLISHED | _ACCEPTED):
                raise ValueError
        else:
            raise ValueError
        if evidence.route == 'xmu' and task.acquisition_class is not AcquisitionClass.PUBLISHED:
            raise ValueError
        return QualifiedPdf(artifact, task.acquisition_class, evidence.route)
    except (ValueError, TypeError, AttributeError):
        raise VersionQualificationError('PDF does not qualify for the frozen version.') from None
