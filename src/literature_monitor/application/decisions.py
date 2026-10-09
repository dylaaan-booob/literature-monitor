"""Stable application boundary for Paper workflow decisions."""

from __future__ import annotations

from dataclasses import dataclass as _dataclass
from enum import Enum
import os as _os
from pathlib import Path
import stat as _stat
from uuid import UUID

from literature_monitor.markdown_state import (
    parse_paper_state as _parse_paper_state,
    serialize_document as _serialize_document,
)
from literature_monitor.models import WorkflowStatus
from literature_monitor.safe_write import (
    CompareReadError as _CompareReadError,
    ContentChangedError as _ContentChangedError,
    read_text_exact as _read_text_exact,
    replace_regular_text_at_identity as _replace_regular_text_at_identity,
    workspace_operation_lock as _workspace_operation_lock,
    WorkspaceOperationLock as _WorkspaceOperationLock,
)

__all__ = [
    "DecisionOutcome",
    "DecisionResult",
    "keep_paper",
    "reject_paper",
]


class DecisionOutcome(str, Enum):
    UPDATED = "UPDATED"
    NOT_FOUND = "NOT_FOUND"
    INVALID_PAPER = "INVALID_PAPER"
    STATE_CONFLICT = "STATE_CONFLICT"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    IO_FAILURE = "IO_FAILURE"


@_dataclass(frozen=True)
class DecisionResult:
    outcome: DecisionOutcome
    paper_id: UUID
    expected_status: WorkflowStatus
    current_status: WorkflowStatus | None
    resulting_status: WorkflowStatus | None
    path: Path | None
    message: str


def _failure(
    outcome: DecisionOutcome,
    paper_id: UUID,
    expected_status: WorkflowStatus,
    message: str,
    *,
    current_status: WorkflowStatus | None = None,
    path: Path | None = None,
) -> DecisionResult:
    return DecisionResult(
        outcome=outcome,
        paper_id=paper_id,
        expected_status=expected_status,
        current_status=current_status,
        resulting_status=None,
        path=path,
        message=message,
    )


@_dataclass(frozen=True)
class _PaperRead:
    contents: str
    directory_identity: tuple[int, int]
    file_identity: tuple[int, int]


def _read_paper_candidate(path: Path, directory: int) -> _PaperRead:
    descriptor = _os.open(path.name, _os.O_RDONLY | _os.O_NOFOLLOW | _os.O_NONBLOCK, dir_fd=directory)
    try:
        parent = _os.fstat(directory)
        target = _os.fstat(descriptor)
        if not _stat.S_ISREG(target.st_mode):
            raise OSError("Paper candidate is not a regular file")
        with _os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            contents = handle.read().decode("utf-8")
        return _PaperRead(contents, (parent.st_dev, parent.st_ino), (target.st_dev, target.st_ino))
    finally:
        if descriptor is not None:
            _os.close(descriptor)


def _locate_paper(
    output_dir: Path,
    paper_id: UUID,
    expected_status: WorkflowStatus,
    *,
    require_safe_candidates: bool = False,
    directory: int | None = None,
    paper_reads: dict[Path, _PaperRead] | None = None,
) -> tuple[Path | None, DecisionResult | None]:
    papers_dir = output_dir / "Papers"
    if papers_dir.is_symlink():
        return None, _failure(
            DecisionOutcome.IO_FAILURE,
            paper_id,
            expected_status,
            "Papers path is not a safe directory",
            path=papers_dir,
        )
    if not papers_dir.exists():
        return None, _failure(
            DecisionOutcome.NOT_FOUND,
            paper_id,
            expected_status,
            "Paper UUID was not found",
        )
    if not papers_dir.is_dir():
        return None, _failure(
            DecisionOutcome.IO_FAILURE,
            paper_id,
            expected_status,
            "Papers path is not a safe directory",
            path=papers_dir,
        )

    try:
        paths = (sorted(papers_dir / name for name in _os.listdir(directory) if name.endswith(".md"))
                 if directory is not None else sorted(papers_dir.glob("*.md")))
    except OSError as error:
        return None, _failure(
            DecisionOutcome.IO_FAILURE,
            paper_id,
            expected_status,
            f"cannot scan Papers directory: {error}",
            path=papers_dir,
        )

    matches: list[Path] = []
    unsafe_candidate: tuple[Path, str] | None = None
    unreadable_candidate: tuple[Path, str] | None = None
    authors_dir = output_dir / "Authors"
    for path in paths:
        if path.is_symlink():
            if unsafe_candidate is None:
                unsafe_candidate = (
                    path,
                    "unsafe Paper symlink has no authoritative UUID",
                )
            continue
        if not path.is_file():
            if unsafe_candidate is None:
                unsafe_candidate = (
                    path,
                    "cannot determine Paper UUID from a non-regular candidate",
                )
            continue

        try:
            if directory is not None:
                action_read = _read_paper_candidate(path, directory)
                assert paper_reads is not None
                contents = action_read.contents
            else:
                contents = _read_text_exact(path)
        except (OSError, UnicodeError) as error:
            if unreadable_candidate is None:
                unreadable_candidate = (
                    path,
                    f"cannot read Paper candidate: {error}",
                )
            continue

        state = _parse_paper_state(path, contents, authors_dir)
        if state is None:
            continue
        if state.paper_id is None:
            if unsafe_candidate is None:
                unsafe_candidate = (
                    path,
                    "cannot safely determine UUID for a Paper candidate",
                )
            continue
        if state.paper_id != paper_id:
            continue
        if directory is not None:
            paper_reads[path] = action_read
        matches.append(path)

    if len(matches) > 1:
        return None, _failure(
            DecisionOutcome.INVALID_PAPER,
            paper_id,
            expected_status,
            "Paper UUID has multiple locations",
        )
    if unreadable_candidate is not None:
        path, message = unreadable_candidate
        return None, _failure(
            DecisionOutcome.IO_FAILURE,
            paper_id,
            expected_status,
            message,
            path=path,
        )
    if unsafe_candidate is not None and (require_safe_candidates or not matches):
        path, message = unsafe_candidate
        return None, _failure(
            DecisionOutcome.INVALID_PAPER,
            paper_id,
            expected_status,
            message,
            path=path,
        )
    if matches:
        return matches[0], None
    return None, _failure(
        DecisionOutcome.NOT_FOUND,
        paper_id,
        expected_status,
        "Paper UUID was not found",
    )


def _apply_decision(output_dir: Path, paper_id: UUID, expected_status: WorkflowStatus,
                    *, required_status: WorkflowStatus, target_status: WorkflowStatus) -> DecisionResult:
    try:
        with _workspace_operation_lock(output_dir) as lock:
            directory = _os.open("Papers", _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW, dir_fd=lock.directory)
            try:
                reads = {}
                path, failure = _locate_paper(lock.path, paper_id, expected_status,
                                             directory=directory, paper_reads=reads)
            finally:
                _os.close(directory)
            if failure is not None:
                return failure
            # Reopen the located UUID through the pinned Papers directory before
            # interpreting the current decision; final compare-write checks it again.
            directory = _os.open("Papers", _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW, dir_fd=lock.directory)
            try:
                read = _read_paper_candidate(path, directory)
            finally:
                _os.close(directory)
            state = _parse_paper_state(path, read.contents, lock.path / "Authors")
            if state is None or not state.updateable or state.paper_id != paper_id:
                return _failure(DecisionOutcome.INVALID_PAPER, paper_id, expected_status,
                                "Paper UUID changed" if state and state.paper_id != paper_id else "; ".join(state.problems) if state else "Invalid Paper", path=path)
            if state.status is not expected_status:
                return _failure(DecisionOutcome.STATE_CONFLICT, paper_id, expected_status,
                                "Paper status changed after the decision was requested", current_status=state.status, path=path)
            if state.status is not required_status:
                return _failure(DecisionOutcome.INVALID_TRANSITION, paper_id, expected_status,
                                "Keep/Reject requires candidate", current_status=state.status, path=path)
            frontmatter = dict(state.frontmatter)
            frontmatter["status"] = target_status.value
            _replace_regular_text_at_identity(
                path, _serialize_document(frontmatter, state.body), expected_contents=read.contents,
                expected_directory_identity=read.directory_identity, expected_file_identity=read.file_identity,
                expected_workspace_identity=lock.identity, operation_lock=lock,
            )
            return DecisionResult(DecisionOutcome.UPDATED, paper_id, expected_status, state.status,
                                  target_status, path, f"Paper status updated to {target_status.value}")
    except FileNotFoundError:
        return _failure(DecisionOutcome.NOT_FOUND, paper_id, expected_status, "Paper UUID was not found")
    except _ContentChangedError as error:
        return _failure(DecisionOutcome.STATE_CONFLICT, paper_id, expected_status, str(error))
    except (_CompareReadError, OSError) as error:
        return _failure(DecisionOutcome.IO_FAILURE, paper_id, expected_status, str(error))


def keep_paper(
    output_dir: Path,
    paper_id: UUID,
    expected_status: WorkflowStatus,
) -> DecisionResult:
    return _apply_decision(
        output_dir,
        paper_id,
        expected_status,
        required_status=WorkflowStatus.CANDIDATE,
        target_status=WorkflowStatus.KEPT,
    )


def reject_paper(
    output_dir: Path,
    paper_id: UUID,
    expected_status: WorkflowStatus,
) -> DecisionResult:
    return _apply_decision(
        output_dir,
        paper_id,
        expected_status,
        required_status=WorkflowStatus.CANDIDATE,
        target_status=WorkflowStatus.REJECTED,
    )
