"""DOI-bound acquisition preflight/commit (SPEC §37.7)."""

from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
import os
import stat
from uuid import UUID, uuid4

from literature_monitor.identifiers import normalize_doi
from literature_monitor.markdown_state import PaperMarkdownState, parse_paper_state
from literature_monitor.models import WorkflowStatus
from literature_monitor.zotero_credentials import AuthorizationRetryBoundary, ZoteroAuthorizationRuntime
from literature_monitor.zotero_local import VerifiedZoteroItem, ZoteroLocalClient, ZoteroReadOutcome
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
    OPENING_CHROME = "OPENING_CHROME"
    HANDOFF = "HANDOFF"
    BROWSER_ACTION = "BROWSER_ACTION"
    RESOLVER_CHOICE = "RESOLVER_CHOICE"
    VALIDATING_PDF = "VALIDATING_PDF"
    WAITING_FOR_ZOTERO_AUTH = "WAITING_FOR_ZOTERO_AUTH"
    CANCELLED = "CANCELLED"
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
    CANCELLED = "CANCELLED"


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
            AcquisitionOutcome.BROWSER_UNAVAILABLE: "Normal Chrome unavailable; check Chrome installation.",
            AcquisitionOutcome.RESOLVER_FAILURE: "Institutional resolver failed; retry later.",
            AcquisitionOutcome.UPLOAD_FAILURE: "PDF upload did not complete; inspect Zotero before retrying.",
            AcquisitionOutcome.INTERNAL_FAILURE: "Acquisition stopped because of an unexpected internal error.",
            AcquisitionOutcome.CANCELLED: "Acquisition cancelled before Zotero content mutation.",
        }[self.outcome]
        if self.mutation_uncertain or self.upload_stage in (ZoteroUploadStage.CHILD_CREATED, ZoteroUploadStage.BYTES_UPLOADED):
            message += " A partial or uncertain attachment may remain in Zotero."
        if self.recovery is AcquisitionRecovery.RATE_LIMITED:
            message += " Honor the authorization retry boundary."
        if self.recovery is AcquisitionRecovery.ZOTERO_AUTHORIZATION:
            message += " Authorize in Settings → Advanced & Diagnostics → Zotero integration."
        return message


@dataclass(frozen=True)
class AcquisitionTask:
    """Process-local identity; continuations must never rebuild it from Paper."""

    task_id: UUID
    paper_id: UUID
    doi: str
    zotero_key: str
    server_id: str

    @property
    def parent(self) -> VerifiedZoteroItem:
        return VerifiedZoteroItem(self.zotero_key, self.doi, self.server_id)


@dataclass(frozen=True)
class PreparedAcquisition:
    task: AcquisitionTask
    linkage_completed: bool


@dataclass(frozen=True)
class _PaperAction:
    state: PaperMarkdownState
    doi: str
    directory_identity: tuple[int, int]
    file_identity: tuple[int, int]


@dataclass
class _Attempt:
    linked: bool = False
    writing: bool = False
    upload_stage: ZoteroUploadStage | None = None


def _read_paper(output_dir: Path, paper_id: UUID) -> _PaperAction | None:
    """One UUID scan/action snapshot, with no-follow regular candidate reads."""
    papers = output_dir / "Papers"
    directory = os.open(papers, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        opened_directory = os.fstat(directory)
        directory_identity = (opened_directory.st_dev, opened_directory.st_ino)
        matches: list[tuple[PaperMarkdownState, tuple[int, int]]] = []
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".md"):
                continue
            path = papers / name
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                opened = os.fstat(descriptor)
                if not stat.S_ISREG(opened.st_mode):
                    return None
                with os.fdopen(descriptor, "rb") as handle:
                    descriptor = None
                    contents = handle.read().decode("utf-8")
                located = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(located.st_mode) or (located.st_dev, located.st_ino) != (opened.st_dev, opened.st_ino):
                    return None
            finally:
                if descriptor is not None:
                    os.close(descriptor)
            state = parse_paper_state(path, contents, output_dir / "Authors")
            if state is not None:
                if state.paper_id is None:
                    return None  # An unreadable identity could hide a duplicate.
                if state.paper_id == paper_id:
                    matches.append((state, (opened.st_dev, opened.st_ino)))
        located_directory = papers.lstat()
        if (not stat.S_ISDIR(located_directory.st_mode)
                or (located_directory.st_dev, located_directory.st_ino) != (opened_directory.st_dev, opened_directory.st_ino)):
            return None
    finally:
        os.close(directory)
    if len(matches) != 1:
        return None
    state, file_identity = matches[0]
    if (state.problems or not state.updateable or state.frontmatter is None or state.body is None
            or state.status is not WorkflowStatus.IN_ZOTERO):
        return None
    doi = normalize_doi(state.external_ids.doi) if state.external_ids is not None else None
    if doi is None:
        return None
    return _PaperAction(state, doi, directory_identity, file_identity)


class AcquisitionService:
    """Prepare once and commit staged bytes without rereading Paper."""

    def __init__(self, output_dir: Path, *,
                 authorization_retry_boundary: AuthorizationRetryBoundary | None = None,
                 authorization_runtime: ZoteroAuthorizationRuntime | None = None):
        self._output_dir = Path(output_dir)
        self._authorization_runtime = (authorization_runtime if authorization_runtime is not None
                                       else ZoteroAuthorizationRuntime(retry_boundary=authorization_retry_boundary))
        self._authorization_retry_boundary = self._authorization_runtime.retry_boundary

    def prepare(self, paper_id: UUID, *, stage_callback=None) -> PreparedAcquisition | AcquisitionResult:
        if not isinstance(paper_id, UUID):
            raise TypeError("A Paper UUID is required.")
        attempt = _Attempt()
        try:
            return self._prepare(paper_id, stage_callback or (lambda value: None), attempt)
        except Exception:
            return AcquisitionResult(paper_id, AcquisitionOutcome.INTERNAL_FAILURE,
                AcquisitionRecovery.CHECK_ZOTERO, linkage_completed=attempt.linked)

    def authorization_status(self, task: AcquisitionTask):
        from literature_monitor.zotero_write import ZoteroAuthorizationClient
        with ZoteroAuthorizationClient(task.server_id, authorization_runtime=self._authorization_runtime) as client:
            return client.authorization_status()

    def commit(self, task: AcquisitionTask, artifact: 'StagedPdf', *, linkage_completed=False) -> AcquisitionResult:
        from literature_monitor.pdf_staging import StagedPdf
        if not isinstance(task, AcquisitionTask):
            raise TypeError('A frozen acquisition task is required.')
        attempt = _Attempt(linked=linkage_completed)
        if (not isinstance(artifact, StagedPdf) or artifact.task_id != task.task_id
                or not artifact.validate()):
            return AcquisitionResult(task.paper_id, AcquisitionOutcome.NO_VALID_PDF,
                AcquisitionRecovery.CHECK_ZOTERO, linkage_completed=linkage_completed)
        try:
            return self._upload(task, artifact.path, attempt, validate=artifact.validate)
        except Exception:
            return AcquisitionResult(task.paper_id, AcquisitionOutcome.INTERNAL_FAILURE,
                AcquisitionRecovery.CHECK_ZOTERO, linkage_completed=attempt.linked,
                upload_stage=attempt.upload_stage, mutation_uncertain=attempt.writing)

    def _prepare(self, paper_id, stage, attempt) -> PreparedAcquisition | AcquisitionResult:
        def result(outcome, recovery=AcquisitionRecovery.NONE, **kwargs):
            return AcquisitionResult(paper_id, outcome, recovery, linkage_completed=attempt.linked, **kwargs)
        stage(AcquisitionStage.LOCATING_ZOTERO)
        try:
            paper = _read_paper(self._output_dir, paper_id)
        except (OSError, UnicodeError, ValueError):
            paper = None
        if paper is None:
            return result(AcquisitionOutcome.INELIGIBLE, AcquisitionRecovery.CHECK_PAPER)
        with ZoteroLocalClient() as local:
            key = paper.state.frontmatter.get("zotero_key")
            identity = (local.resolve_identity(paper.doi) if key is None
                        else local.verify_parent_key(paper.doi, key))
            if identity.outcome is not ZoteroReadOutcome.VERIFIED or identity.item is None:
                outcome = {ZoteroReadOutcome.NOT_FOUND: AcquisitionOutcome.ZOTERO_NOT_FOUND,
                           ZoteroReadOutcome.DUPLICATE: AcquisitionOutcome.ZOTERO_DUPLICATE}.get(identity.outcome, AcquisitionOutcome.ZOTERO_FAILURE)
                return result(outcome, AcquisitionRecovery.CHECK_ZOTERO)
            parent = identity.item
            if parent.normalized_doi != paper.doi:
                return result(AcquisitionOutcome.ZOTERO_FAILURE, AcquisitionRecovery.CHECK_ZOTERO)
            if key is not None and parent.key != key:
                return result(AcquisitionOutcome.ZOTERO_FAILURE, AcquisitionRecovery.CHECK_ZOTERO)
            if key is None:
                verified = local.verify_parent_key(paper.doi, parent.key, server_id=parent.server_id)
                if verified.outcome is not ZoteroReadOutcome.VERIFIED or verified.item != parent:
                    return result(AcquisitionOutcome.ZOTERO_FAILURE, AcquisitionRecovery.CHECK_ZOTERO)
                linkage = link_paper_to_zotero(
                    paper.state, identity, expected_directory_identity=paper.directory_identity,
                    expected_file_identity=paper.file_identity,
                )
                if linkage.outcome is not LinkageOutcome.LINKED:
                    return result(AcquisitionOutcome.CONFLICT, AcquisitionRecovery.CHECK_PAPER)
                attempt.linked = True
            stage(AcquisitionStage.CHECKING_ATTACHMENT)
            attachments = local.inspect_attachments(parent)
            if attachments.outcome is not ZoteroReadOutcome.CHECKED or type(attachments.has_pdf) is not bool:
                return result(AcquisitionOutcome.ZOTERO_FAILURE, AcquisitionRecovery.CHECK_ZOTERO)
            task = AcquisitionTask(
                task_id=uuid4(), paper_id=paper_id, doi=paper.doi, zotero_key=parent.key,
                server_id=parent.server_id,
            )
            if attachments.has_pdf:
                return result(AcquisitionOutcome.PDF_ALREADY_ATTACHED)
            delay = self._authorization_retry_boundary.remaining()
            if delay > 0:
                return result(AcquisitionOutcome.UPLOAD_FAILURE, AcquisitionRecovery.RATE_LIMITED, retry_after_seconds=delay)
            return PreparedAcquisition(task, attempt.linked)

    def _upload(self, task, path, attempt, *, validate=None):
        def result(outcome, recovery=AcquisitionRecovery.NONE, **kwargs):
            return AcquisitionResult(task.paper_id, outcome, recovery, linkage_completed=attempt.linked, **kwargs)
        with ZoteroLocalClient() as local:
            def revalidate(own_child=None):
                verified = local.verify_parent_key(task.doi, task.zotero_key, server_id=task.server_id)
                if verified.outcome is not ZoteroReadOutcome.VERIFIED or verified.item != task.parent:
                    return result(AcquisitionOutcome.CONFLICT, AcquisitionRecovery.CHECK_ZOTERO)
                checked = local.inspect_attachments(task.parent)
                if checked.outcome is not ZoteroReadOutcome.CHECKED or type(checked.has_pdf) is not bool:
                    return result(AcquisitionOutcome.ZOTERO_FAILURE, AcquisitionRecovery.CHECK_ZOTERO)
                if own_child is not None:
                    if own_child not in checked.pdf_keys or any(key != own_child for key in checked.pdf_file_keys):
                        return result(AcquisitionOutcome.CONFLICT, AcquisitionRecovery.CHECK_ZOTERO)
                elif checked.has_pdf:
                    return result(AcquisitionOutcome.PDF_ALREADY_ATTACHED)
                return None
            guard_failure, retried = None, False
            def guard(context):
                nonlocal guard_failure, retried
                attempt.upload_stage = context.stage
                if context.phase is ZoteroWriteGuardPhase.BEFORE_AUTHORIZATION_RETRY:
                    if retried:
                        guard_failure = result(AcquisitionOutcome.UPLOAD_FAILURE, AcquisitionRecovery.CHECK_ZOTERO)
                        return False
                    retried = True
                if (context.stage in (ZoteroUploadStage.NO_CONFIRMED_MUTATION, ZoteroUploadStage.CHILD_CREATED)
                        and validate is not None and not validate()):
                    guard_failure = result(AcquisitionOutcome.CONFLICT, AcquisitionRecovery.CHECK_ZOTERO)
                    return False
                guard_failure = revalidate(context.attachment_key)
                return guard_failure is None
            with ZoteroWriteClient(task.parent, authorization_runtime=self._authorization_runtime) as writer:
                attempt.writing = True
                uploaded = writer.upload_pdf(path, guard=guard)
                attempt.upload_stage = uploaded.stage
            if guard_failure is not None:
                return replace(guard_failure, upload_stage=uploaded.stage, mutation_uncertain=uploaded.mutation_uncertain)
            if uploaded.outcome is ZoteroUploadOutcome.SUCCEEDED:
                return result(AcquisitionOutcome.SUCCEEDED, upload_stage=uploaded.stage)
            authorization = uploaded.authorization
            if authorization is not None and authorization.outcome is ZoteroAuthorizationOutcome.RATE_LIMITED:
                delay = authorization.retry_after_seconds
                return result(AcquisitionOutcome.UPLOAD_FAILURE, AcquisitionRecovery.RATE_LIMITED,
                              upload_stage=uploaded.stage, retry_after_seconds=delay, mutation_uncertain=uploaded.mutation_uncertain)
            if (authorization is not None and authorization.outcome is ZoteroAuthorizationOutcome.REQUIRED
                    and uploaded.stage is ZoteroUploadStage.NO_CONFIRMED_MUTATION):
                return result(AcquisitionOutcome.AUTH_REQUIRED, AcquisitionRecovery.ZOTERO_AUTHORIZATION,
                              upload_stage=uploaded.stage, mutation_uncertain=uploaded.mutation_uncertain)
            recovery = AcquisitionRecovery.ZOTERO_AUTHORIZATION if uploaded.failure is ZoteroUploadOutcome.AUTH_FAILURE else AcquisitionRecovery.CHECK_ZOTERO
            return result(AcquisitionOutcome.UPLOAD_FAILURE, recovery, upload_stage=uploaded.stage,
                          mutation_uncertain=uploaded.mutation_uncertain)
