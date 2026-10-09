"""Transient import identity and guarded parent completion (SPEC §42.3).

Existing export_attempt frontmatter is legacy user data. Never generate, clear
or repurpose it for new imports.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import secrets
from uuid import UUID
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from literature_monitor.application.workspace import PlannedImportPaper
    from literature_monitor.web.capture_coordinator import CaptureCompletion, CaptureCoordinator

from literature_monitor.application.decisions import _locate_paper, _read_paper_candidate
from literature_monitor.markdown_state import PaperMarkdownState, parse_paper_state, serialize_document
from literature_monitor.models import ExportAttempt, ExportAttemptState, WorkflowStatus
from literature_monitor.safe_write import (
    ContentChangedError, CompareReadError, WorkspaceOperationLock,
    replace_regular_text_at_identity, workspace_operation_lock,
)


class ExportAttemptError(RuntimeError):
    """An import cannot safely use the selected Paper revision."""


class UntrustedCompletionError(ExportAttemptError):
    """The result has no live coordinator authority."""


@dataclass(frozen=True)
class ExportReservation:
    """An invocation-local identity fence; never a persisted Paper marker."""

    attempt: ExportAttempt
    file_identity: tuple[int, int]
    contents: str
    lock_identity: tuple[int, int]


@dataclass(frozen=True)
class _PaperRead:
    state: PaperMarkdownState
    directory_identity: tuple[int, int]
    file_identity: tuple[int, int]


@contextmanager
def export_operation_lock(output_dir: Path, operation_lock: WorkspaceOperationLock | None = None):
    if operation_lock is None:
        with workspace_operation_lock(output_dir) as lock:
            yield lock
    else:
        if Path(os.path.abspath(output_dir)) != operation_lock.path:
            raise ExportAttemptError("Workspace operation owner does not match")
        operation_lock.verify()
        yield operation_lock


def _read_target(lock: WorkspaceOperationLock, paper_id: UUID, path: Path | None = None) -> _PaperRead:
    """Read only the frozen path for batch work; standalone callers locate once."""
    lock.verify()
    directory = os.open("Papers", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=lock.directory)
    try:
        if path is None:
            located, failure = _locate_paper(lock.path, paper_id, WorkflowStatus.KEPT,
                                             require_safe_candidates=True, directory=directory,
                                             paper_reads={})
            if failure is not None:
                raise ExportAttemptError(failure.message)
            path = located
        assert path is not None
        if path.parent != lock.path / "Papers" or path.name in {".", ".."}:
            raise ExportAttemptError("Paper location changed")
        info = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        if info.st_nlink != 1:
            raise ExportAttemptError("Paper has multiple hard-link locations")
        candidate = _read_paper_candidate(path, directory)
        state = parse_paper_state(path, candidate.contents, lock.path / "Authors")
        if (state is None or not state.updateable or state.paper_id != paper_id
                or state.status is not WorkflowStatus.KEPT or state.external_ids is None):
            raise ExportAttemptError("Import requires a safe kept Paper with valid DOI")
        lock.verify()
        return _PaperRead(state, candidate.directory_identity, candidate.file_identity)
    finally:
        os.close(directory)


def reserve_export_attempt(output_dir: Path, paper_id: UUID) -> ExportReservation:
    """Compatibility entrypoint for local callers; makes no disk changes."""
    with workspace_operation_lock(output_dir) as lock:
        return _reserve_export_attempt_locked(lock, paper_id)


def _reserve_export_attempt_locked(lock: WorkspaceOperationLock, paper_id: UUID, *,
                                   expected_paper: PlannedImportPaper | None = None,
                                   expected_papers_identity: tuple[int, int] | None = None) -> ExportReservation:
    read = _read_target(lock, paper_id, expected_paper.path if expected_paper else None)
    if expected_papers_identity is not None and read.directory_identity != expected_papers_identity:
        raise ContentChangedError("Papers directory replaced after planning")
    if expected_paper is not None and (
        read.state.original != expected_paper.contents
        or read.file_identity != expected_paper.file_identity
        or read.state.external_ids.doi != expected_paper.doi
    ):
        raise ContentChangedError("Planned Paper changed before import")
    # 临时凭据只用于本次命令、回执及 Paper CAS；旧 export_attempt 不参与授权。
    attempt = ExportAttempt(
        state=ExportAttemptState.PENDING, attempt_id=secrets.token_hex(32),
        paper_id=paper_id, doi=read.state.external_ids.doi, expected_status="kept",
        workspace_path=str(lock.path), paper_path=str(read.state.path.relative_to(lock.path)),
        workspace_identity=lock.identity, papers_identity=read.directory_identity,
    )
    return ExportReservation(attempt, read.file_identity, read.state.original, lock.lock_identity)


def _matching_read(lock: WorkspaceOperationLock, reservation: ExportReservation) -> _PaperRead:
    attempt = reservation.attempt
    if lock.lock_identity != reservation.lock_identity or attempt.workspace_identity != lock.identity:
        raise ExportAttemptError("Workspace lock identity changed")
    path = lock.path / attempt.paper_path
    read = _read_target(lock, attempt.paper_id, path)
    if (attempt.workspace_path != str(lock.path) or attempt.papers_identity != read.directory_identity
            or attempt.doi != read.state.external_ids.doi
            or read.state.original != reservation.contents or read.file_identity != reservation.file_identity):
        raise ContentChangedError("Import Paper identity or revision changed")
    return read


def validate_export_reservation(reservation: ExportReservation, *,
                                operation_lock: WorkspaceOperationLock | None = None) -> None:
    """Require the exact planned revision immediately before one native dispatch."""
    with export_operation_lock(Path(reservation.attempt.workspace_path), operation_lock) as lock:
        _matching_read(lock, reservation)


class ExportCompletionOutcome(str, Enum):
    EXPORTED = "EXPORTED"
    UNCERTAIN = "UNCERTAIN"
    NO_EFFECT = "NO_EFFECT"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ExportCompletionResult:
    outcome: ExportCompletionOutcome
    message: str


def commit_exported(reservation: ExportReservation, *, completion: CaptureCompletion,
                    coordinator: CaptureCoordinator,
                    operation_lock: WorkspaceOperationLock | None = None) -> None:
    from literature_monitor.web.capture_coordinator import CaptureCompletion, CaptureCoordinator, ParentOutcome
    if (not isinstance(completion, CaptureCompletion) or not isinstance(coordinator, CaptureCoordinator)
            or completion.parent_outcome is not ParentOutcome.CONFIRMED
            or completion.export_reservation is not reservation or completion.save_invocation is None
            or not coordinator.claim_completion(completion)):
        raise UntrustedCompletionError("Attributable native parent acceptance required")
    with export_operation_lock(Path(reservation.attempt.workspace_path),
                               operation_lock or coordinator.operation_lock) as lock:
        read = _matching_read(lock, reservation)
        frontmatter = dict(read.state.frontmatter)
        frontmatter["status"] = WorkflowStatus.EXPORTED.value
        # Old export_attempt is historical metadata; no implicit cleanup of user data.
        contents = serialize_document(frontmatter, read.state.body)
        replace_regular_text_at_identity(
            read.state.path, contents, expected_contents=read.state.original,
            expected_directory_identity=read.directory_identity,
            expected_file_identity=read.file_identity,
            expected_workspace_identity=lock.identity, operation_lock=lock,
            validate_before_replace=lambda: _matching_read(lock, reservation),
        )


def resolve_export_completion(completion: CaptureCompletion, *,
                              coordinator: CaptureCoordinator) -> ExportCompletionResult:
    """Consume one native result; uncertain outcomes remain kept and retryable."""
    from literature_monitor.web.capture_coordinator import CaptureCoordinator, ParentOutcome

    if not isinstance(coordinator, CaptureCoordinator) or not coordinator.owns_completion(completion):
        return ExportCompletionResult(ExportCompletionOutcome.FAILED, "Stale or unauthenticated result rejected.")
    try:
        if completion.parent_outcome is ParentOutcome.CONFIRMED:
            commit_exported(completion.export_reservation, completion=completion, coordinator=coordinator)
            return ExportCompletionResult(ExportCompletionOutcome.EXPORTED, "Native parent acceptance confirmed; PDF unverified.")
        if not coordinator.claim_completion(completion):
            raise UntrustedCompletionError("Coordinator completion already consumed")
        if completion.parent_outcome is ParentOutcome.NO_EFFECT:
            cause = ("Translator failed before native dispatch"
                     if completion.failure_stage and completion.failure_stage.value == "translator"
                     else "Navigation or Connector failed before native dispatch")
            return ExportCompletionResult(ExportCompletionOutcome.NO_EFFECT,
                                          f"{cause}; Paper remains kept.")
        if completion.failure_stage and completion.failure_stage.value == "native_save":
            return ExportCompletionResult(ExportCompletionOutcome.UNCERTAIN,
                                          "Native save reported an error without confirmed acceptance; side effects are uncertain.")
        return ExportCompletionResult(ExportCompletionOutcome.UNCERTAIN,
                                      "Native parent acceptance not confirmed; a new explicit import may create a duplicate.")
    except (ExportAttemptError, ContentChangedError, CompareReadError, OSError) as error:
        return ExportCompletionResult(ExportCompletionOutcome.FAILED,
                                      f"Local Paper completion write failed or conflicted: {error}; inspect Zotero before retry.")
    finally:
        coordinator.finish_resolution(completion)
