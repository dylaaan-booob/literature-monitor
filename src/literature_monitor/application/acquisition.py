"""Synchronous user-triggered acquisition service (SPEC §§35.2–35.11)."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
import time
from uuid import UUID

from literature_monitor.identifiers import normalize_doi
from literature_monitor.institutional_resolver import ResolverOutcome, XmuInstitutionalResolver
from literature_monitor.markdown_state import parse_paper_state
from literature_monitor.models import WorkflowStatus
from literature_monitor.pdf_acquisition import GenericPdfAcquirer, PdfAcquisitionOutcome
from literature_monitor.safe_write import read_text_exact
from literature_monitor.zotero_local import ZoteroLocalClient, ZoteroReadOutcome
from literature_monitor.zotero_write import (
    ZoteroAuthorizationOutcome, ZoteroUploadOutcome, ZoteroUploadStage,
    ZoteroWriteClient, ZoteroWriteGuardPhase,
)

from .zotero_linkage import LinkageOutcome, link_paper_to_zotero


class AcquisitionStage(str, Enum):
    LOCATING_ZOTERO = "LOCATING_ZOTERO"
    CHECKING_ATTACHMENT = "CHECKING_ATTACHMENT"
    RESOLVING = "RESOLVING"
    WAITING_FOR_INSTITUTION_AUTH = "WAITING_FOR_INSTITUTION_AUTH"
    DISCOVERING_PDF = "DISCOVERING_PDF"
    ATTACHING = "ATTACHING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class AcquisitionOutcome(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    PDF_ALREADY_ATTACHED = "PDF_ALREADY_ATTACHED"
    CONFLICT = "CONFLICT"
    INELIGIBLE = "INELIGIBLE"
    ZOTERO_NOT_FOUND = "ZOTERO_NOT_FOUND"
    ZOTERO_DUPLICATE = "ZOTERO_DUPLICATE"
    ZOTERO_FAILURE = "ZOTERO_FAILURE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    NO_ELIGIBLE_CANDIDATES = "NO_ELIGIBLE_CANDIDATES"
    NO_VALID_PDF = "NO_VALID_PDF"
    BROWSER_UNAVAILABLE = "BROWSER_UNAVAILABLE"
    RESOLVER_FAILURE = "RESOLVER_FAILURE"
    UPLOAD_FAILURE = "UPLOAD_FAILURE"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"


class AcquisitionRecovery(str, Enum):
    NONE = "NONE"
    CHECK_PAPER = "CHECK_PAPER"
    CHECK_ZOTERO = "CHECK_ZOTERO"
    INSTITUTION_LOGIN = "INSTITUTION_LOGIN"
    ZOTERO_AUTHORIZATION = "ZOTERO_AUTHORIZATION"
    CHECK_BROWSER = "CHECK_BROWSER"
    RETRY = "RETRY"
    RATE_LIMITED = "RATE_LIMITED"


@dataclass(frozen=True)
class AcquisitionResult:
    paper_id: UUID
    outcome: AcquisitionOutcome
    recovery: AcquisitionRecovery = AcquisitionRecovery.NONE
    linkage_completed: bool = False
    upload_stage: ZoteroUploadStage | None = None
    mutation_uncertain: bool = False
    retry_after_seconds: float | None = None

    @property
    def message(self) -> str:
        message = {
            AcquisitionOutcome.SUCCEEDED: "PDF attached to the verified Zotero parent.",
            AcquisitionOutcome.PDF_ALREADY_ATTACHED: "PDF already attached; no upload performed.",
            AcquisitionOutcome.CONFLICT: "Current Paper or Zotero state changed; start a new attempt after checking it.",
            AcquisitionOutcome.INELIGIBLE: "A safe current in_zotero Paper with a DOI is required.",
            AcquisitionOutcome.ZOTERO_NOT_FOUND: "No exact Zotero DOI match; check My Library.",
            AcquisitionOutcome.ZOTERO_DUPLICATE: "Multiple exact Zotero DOI matches; resolve duplicates first.",
            AcquisitionOutcome.ZOTERO_FAILURE: "Cannot completely verify Zotero identity or attachments; check the Local API.",
            AcquisitionOutcome.AUTH_REQUIRED: "Human authorization or institutional verification is required before retrying.",
            AcquisitionOutcome.NO_ELIGIBLE_CANDIDATES: "Resolver returned no eligible full-text candidates.",
            AcquisitionOutcome.NO_VALID_PDF: "No validated PDF was acquired.",
            AcquisitionOutcome.BROWSER_UNAVAILABLE: "Dedicated browser unavailable; check Chrome and the profile lock.",
            AcquisitionOutcome.RESOLVER_FAILURE: "Institutional resolver failed; retry later.",
            AcquisitionOutcome.UPLOAD_FAILURE: "PDF upload did not complete; inspect Zotero before retrying.",
            AcquisitionOutcome.INTERNAL_FAILURE: "Acquisition stopped because of an unexpected internal error.",
        }[self.outcome]
        if self.mutation_uncertain or self.upload_stage in (ZoteroUploadStage.CHILD_CREATED, ZoteroUploadStage.BYTES_UPLOADED):
            message += " A partial or uncertain attachment may remain in Zotero."
        if self.recovery is AcquisitionRecovery.RATE_LIMITED:
            message += " Honor the authorization retry boundary."
        return message


@dataclass(frozen=True)
class _Paper:
    paper_id: UUID
    doi: str
    zotero_key: str | None


@dataclass
class _Attempt:
    linked: bool = False
    writing: bool = False
    upload_stage: ZoteroUploadStage | None = None


class AuthorizationRetryBoundary:
    """Transient monotonic authorization limit, shareable across workspaces."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._not_before = 0.0

    def remaining(self) -> float:
        return max(0.0, self._not_before - self._clock())

    def defer(self, delay: float) -> None:
        self._not_before = max(self._not_before, self._clock() + delay)


def _read_paper(output_dir: Path, paper_id: UUID, expected_doi: str | None = None) -> _Paper | None:
    """UUID relocation and current safe read; no submitted path or cached state."""
    papers = output_dir / "Papers"
    if papers.is_symlink() or not papers.is_dir():
        return None
    matches = []
    for path in sorted(papers.glob("*.md")):
        if path.is_symlink() or not path.is_file():
            continue
        state = parse_paper_state(path, read_text_exact(path), output_dir / "Authors")
        if state is not None and state.paper_id == paper_id:
            matches.append(path)
    if len(matches) != 1:
        return None
    path = matches[0]
    if papers.is_symlink() or path.is_symlink() or not path.is_file():
        return None
    state = parse_paper_state(path, read_text_exact(path), output_dir / "Authors")
    if (state is None or state.paper_id != paper_id or state.problems or not state.updateable
            or state.frontmatter is None or state.body is None or state.status is not WorkflowStatus.IN_ZOTERO):
        return None
    doi = normalize_doi(state.external_ids.doi) if state.external_ids is not None else None
    if doi is None or (expected_doi is not None and doi != expected_doi):
        return None
    return _Paper(paper_id, doi, state.zotero_key)


class AcquisitionService:
    """Own one synchronous chain; the separate coordinator supplies serialization."""

    def __init__(self, output_dir: Path, *, resolver: XmuInstitutionalResolver, pdf_acquirer: GenericPdfAcquirer,
                 authorization_retry_boundary: AuthorizationRetryBoundary | None = None):
        self._output_dir = Path(output_dir)
        self._resolver = resolver
        self._pdf_acquirer = pdf_acquirer
        # Retry-After enforcement only, never acquisition timing/history.
        self._authorization_retry_boundary = (
            authorization_retry_boundary if authorization_retry_boundary is not None else AuthorizationRetryBoundary()
        )

    def acquire(self, paper_id: UUID, *, stage_callback: Callable[[AcquisitionStage], None] | None = None) -> AcquisitionResult:
        if not isinstance(paper_id, UUID):
            raise TypeError("A Paper UUID is required.")
        attempt = _Attempt()
        def stage(value):
            if stage_callback is not None:
                stage_callback(value)
        try:
            result = self._acquire(paper_id, stage, attempt)
        except Exception:
            result = AcquisitionResult(paper_id, AcquisitionOutcome.INTERNAL_FAILURE,
                AcquisitionRecovery.CHECK_ZOTERO if attempt.writing else AcquisitionRecovery.RETRY,
                linkage_completed=attempt.linked, upload_stage=attempt.upload_stage, mutation_uncertain=attempt.writing)
        terminal = (
            AcquisitionStage.WAITING_FOR_INSTITUTION_AUTH if result.recovery is AcquisitionRecovery.INSTITUTION_LOGIN
            else AcquisitionStage.SUCCEEDED if result.outcome in (AcquisitionOutcome.SUCCEEDED, AcquisitionOutcome.PDF_ALREADY_ATTACHED)
            else AcquisitionStage.FAILED
        )
        stage(terminal)
        return result

    def _acquire(self, paper_id, stage, attempt) -> AcquisitionResult:
        def result(outcome, recovery=AcquisitionRecovery.NONE, **kwargs):
            return AcquisitionResult(paper_id, outcome, recovery, linkage_completed=attempt.linked, **kwargs)
        def current(doi=None):
            try:
                return _read_paper(self._output_dir, paper_id, doi)
            except (OSError, UnicodeError, ValueError):
                return None
        stage(AcquisitionStage.LOCATING_ZOTERO)
        paper = current()
        if paper is None:
            return result(AcquisitionOutcome.INELIGIBLE, AcquisitionRecovery.CHECK_PAPER)
        with ZoteroLocalClient() as local:
            identity = local.resolve_identity(paper.doi, paper.zotero_key)
            if identity.outcome is not ZoteroReadOutcome.VERIFIED or identity.item is None:
                outcome = {ZoteroReadOutcome.NOT_FOUND: AcquisitionOutcome.ZOTERO_NOT_FOUND,
                           ZoteroReadOutcome.DUPLICATE: AcquisitionOutcome.ZOTERO_DUPLICATE}.get(identity.outcome, AcquisitionOutcome.ZOTERO_FAILURE)
                return result(outcome, AcquisitionRecovery.CHECK_ZOTERO)
            parent = identity.item
            if parent.normalized_doi != paper.doi:
                return result(AcquisitionOutcome.ZOTERO_FAILURE, AcquisitionRecovery.CHECK_ZOTERO)
            paper = current(parent.normalized_doi)
            if paper is None:
                return result(AcquisitionOutcome.CONFLICT, AcquisitionRecovery.CHECK_PAPER)
            if paper.zotero_key is None:
                linkage = link_paper_to_zotero(self._output_dir, paper_id, identity)
                if linkage.outcome is not LinkageOutcome.LINKED:
                    return result(AcquisitionOutcome.CONFLICT, AcquisitionRecovery.CHECK_PAPER)
                attempt.linked = linkage.outcome is LinkageOutcome.LINKED
            stage(AcquisitionStage.CHECKING_ATTACHMENT)
            attachments = local.inspect_attachments(parent)
            if attachments.outcome is not ZoteroReadOutcome.CHECKED or type(attachments.has_pdf) is not bool:
                return result(AcquisitionOutcome.ZOTERO_FAILURE, AcquisitionRecovery.CHECK_ZOTERO)
            if attachments.has_pdf:
                return result(AcquisitionOutcome.PDF_ALREADY_ATTACHED)
            delay = self._authorization_retry_boundary.remaining()
            if delay > 0:
                return result(AcquisitionOutcome.UPLOAD_FAILURE, AcquisitionRecovery.RATE_LIMITED, retry_after_seconds=delay)
            stage(AcquisitionStage.RESOLVING)
            resolved = self._resolver.resolve(paper.doi)
            if resolved.outcome is not ResolverOutcome.RESOLVED:
                outcome, recovery = {
                    ResolverOutcome.AUTH_REQUIRED: (AcquisitionOutcome.AUTH_REQUIRED, AcquisitionRecovery.INSTITUTION_LOGIN),
                    ResolverOutcome.NO_ELIGIBLE_CANDIDATES: (AcquisitionOutcome.NO_ELIGIBLE_CANDIDATES, AcquisitionRecovery.RETRY),
                    ResolverOutcome.BROWSER_UNAVAILABLE: (AcquisitionOutcome.BROWSER_UNAVAILABLE, AcquisitionRecovery.CHECK_BROWSER),
                }.get(resolved.outcome, (AcquisitionOutcome.RESOLVER_FAILURE, AcquisitionRecovery.RETRY))
                return result(outcome, recovery)
            stage(AcquisitionStage.DISCOVERING_PDF)
            acquired = self._pdf_acquirer.acquire(resolved.candidates)
            if acquired.outcome is not PdfAcquisitionOutcome.ACQUIRED or acquired.pdf is None:
                if acquired.pdf is not None:
                    acquired.pdf.cleanup()
                outcome, recovery = {
                    PdfAcquisitionOutcome.AUTH_REQUIRED: (AcquisitionOutcome.AUTH_REQUIRED, AcquisitionRecovery.INSTITUTION_LOGIN),
                    PdfAcquisitionOutcome.BROWSER_UNAVAILABLE: (AcquisitionOutcome.BROWSER_UNAVAILABLE, AcquisitionRecovery.CHECK_BROWSER),
                }.get(acquired.outcome, (AcquisitionOutcome.NO_VALID_PDF, AcquisitionRecovery.RETRY))
                return result(outcome, recovery)
            with acquired.pdf as pdf:
                def revalidate(own_child=None):
                    if current(parent.normalized_doi) is None:
                        return result(AcquisitionOutcome.CONFLICT, AcquisitionRecovery.CHECK_PAPER)
                    verified = local.resolve_identity(parent.normalized_doi, parent.key)
                    if verified.outcome is not ZoteroReadOutcome.VERIFIED or verified.item != parent:
                        return result(AcquisitionOutcome.CONFLICT, AcquisitionRecovery.CHECK_ZOTERO)
                    checked = local.inspect_attachments(parent)
                    if checked.outcome is not ZoteroReadOutcome.CHECKED or type(checked.has_pdf) is not bool:
                        return result(AcquisitionOutcome.ZOTERO_FAILURE, AcquisitionRecovery.CHECK_ZOTERO)
                    # Reads/auth dialogs may have allowed a Paper edit meanwhile.
                    if current(parent.normalized_doi) is None:
                        return result(AcquisitionOutcome.CONFLICT, AcquisitionRecovery.CHECK_PAPER)
                    if own_child is not None:
                        if own_child not in checked.pdf_keys or any(key != own_child for key in checked.pdf_file_keys):
                            return result(AcquisitionOutcome.CONFLICT, AcquisitionRecovery.CHECK_ZOTERO)
                    elif checked.has_pdf:
                        return result(AcquisitionOutcome.PDF_ALREADY_ATTACHED)
                    return None
                failure = revalidate()
                if failure is not None:
                    return failure
                stage(AcquisitionStage.ATTACHING)
                guard_failure, retried = None, False
                def guard(context):
                    nonlocal guard_failure, retried
                    attempt.upload_stage = context.stage
                    if context.phase is ZoteroWriteGuardPhase.BEFORE_AUTHORIZATION_RETRY:
                        if retried:
                            guard_failure = result(AcquisitionOutcome.UPLOAD_FAILURE, AcquisitionRecovery.CHECK_ZOTERO)
                            return False
                        retried = True
                    guard_failure = revalidate(context.attachment_key)
                    return guard_failure is None
                with ZoteroWriteClient(parent) as writer:
                    attempt.writing = True
                    uploaded = writer.upload_pdf(pdf.path, guard=guard)
                    attempt.upload_stage = uploaded.stage
                if guard_failure is not None:
                    return replace(guard_failure, upload_stage=uploaded.stage, mutation_uncertain=uploaded.mutation_uncertain)
                if uploaded.outcome is ZoteroUploadOutcome.SUCCEEDED:
                    return result(AcquisitionOutcome.SUCCEEDED, upload_stage=uploaded.stage)
                authorization = uploaded.authorization
                if authorization is not None and authorization.outcome is ZoteroAuthorizationOutcome.RATE_LIMITED:
                    delay = authorization.retry_after_seconds
                    if delay is not None:
                        self._authorization_retry_boundary.defer(delay)
                    return result(AcquisitionOutcome.UPLOAD_FAILURE, AcquisitionRecovery.RATE_LIMITED,
                                  upload_stage=uploaded.stage, retry_after_seconds=delay, mutation_uncertain=uploaded.mutation_uncertain)
                recovery = AcquisitionRecovery.ZOTERO_AUTHORIZATION if uploaded.outcome is ZoteroUploadOutcome.AUTH_FAILURE else AcquisitionRecovery.CHECK_ZOTERO
                return result(AcquisitionOutcome.UPLOAD_FAILURE, recovery, upload_stage=uploaded.stage,
                              mutation_uncertain=uploaded.mutation_uncertain)
