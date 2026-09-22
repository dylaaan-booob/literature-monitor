"""Stable application boundary for Paper workflow decisions."""

from __future__ import annotations

from dataclasses import dataclass as _dataclass
from enum import Enum
from pathlib import Path
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
    replace_text_if_unchanged as _replace_text_if_unchanged,
)

__all__ = [
    "DecisionOutcome",
    "DecisionResult",
    "keep_paper",
    "reject_paper",
    "mark_paper_in_zotero",
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


def _locate_paper(
    output_dir: Path,
    paper_id: UUID,
    expected_status: WorkflowStatus,
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
        paths = sorted(papers_dir.glob("*.md"))
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
    if matches:
        return matches[0], None
    if unsafe_candidate is not None:
        path, message = unsafe_candidate
        return None, _failure(
            DecisionOutcome.INVALID_PAPER,
            paper_id,
            expected_status,
            message,
            path=path,
        )
    return None, _failure(
        DecisionOutcome.NOT_FOUND,
        paper_id,
        expected_status,
        "Paper UUID was not found",
    )


def _apply_decision(
    output_dir: Path,
    paper_id: UUID,
    expected_status: WorkflowStatus,
    *,
    required_status: WorkflowStatus,
    target_status: WorkflowStatus,
) -> DecisionResult:
    path, failure = _locate_paper(output_dir, paper_id, expected_status)
    if failure is not None:
        return failure
    assert path is not None

    if path.is_symlink() or not path.is_file():
        return _failure(
            DecisionOutcome.INVALID_PAPER,
            paper_id,
            expected_status,
            "Paper target is no longer a safe regular file",
            path=path,
        )

    try:
        current_contents = _read_text_exact(path)
    except (OSError, UnicodeError) as error:
        return _failure(
            DecisionOutcome.IO_FAILURE,
            paper_id,
            expected_status,
            f"cannot read Paper before decision: {error}",
            path=path,
        )

    state = _parse_paper_state(path, current_contents, output_dir / "Authors")
    if state is None:
        return _failure(
            DecisionOutcome.INVALID_PAPER,
            paper_id,
            expected_status,
            "target is no longer a Paper record",
            path=path,
        )
    if state.paper_id != paper_id:
        return _failure(
            DecisionOutcome.INVALID_PAPER,
            paper_id,
            expected_status,
            "Paper UUID changed after relocation",
            current_status=state.status,
            path=path,
        )
    if (
        state.problems
        or not state.updateable
        or state.frontmatter is None
        or state.body is None
        or state.status is None
    ):
        message = (
            "; ".join(state.problems)
            if state.problems
            else "Paper cannot be safely updated"
        )
        return _failure(
            DecisionOutcome.INVALID_PAPER,
            paper_id,
            expected_status,
            message,
            current_status=state.status,
            path=path,
        )

    current_status = state.status
    if current_status is not expected_status:
        return _failure(
            DecisionOutcome.STATE_CONFLICT,
            paper_id,
            expected_status,
            (
                f"Paper status changed from expected {expected_status.value} "
                f"to {current_status.value}"
            ),
            current_status=current_status,
            path=path,
        )
    if current_status is not required_status:
        return _failure(
            DecisionOutcome.INVALID_TRANSITION,
            paper_id,
            expected_status,
            (
                f"cannot change Paper status from {current_status.value} "
                f"to {target_status.value}"
            ),
            current_status=current_status,
            path=path,
        )

    frontmatter = dict(state.frontmatter)
    frontmatter["status"] = target_status.value
    updated_contents = _serialize_document(frontmatter, state.body)

    try:
        _replace_text_if_unchanged(
            path,
            updated_contents,
            expected_contents=current_contents,
        )
    except _ContentChangedError:
        return _failure(
            DecisionOutcome.STATE_CONFLICT,
            paper_id,
            expected_status,
            "Paper changed on disk before the decision could be written",
            current_status=current_status,
            path=path,
        )
    except _CompareReadError as error:
        return _failure(
            DecisionOutcome.IO_FAILURE,
            paper_id,
            expected_status,
            f"cannot verify Paper before decision: {error}",
            current_status=current_status,
            path=path,
        )
    except OSError as error:
        return _failure(
            DecisionOutcome.IO_FAILURE,
            paper_id,
            expected_status,
            f"cannot write Paper decision: {error}",
            current_status=current_status,
            path=path,
        )

    return DecisionResult(
        outcome=DecisionOutcome.UPDATED,
        paper_id=paper_id,
        expected_status=expected_status,
        current_status=current_status,
        resulting_status=target_status,
        path=path,
        message=f"Paper status updated to {target_status.value}",
    )


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


def mark_paper_in_zotero(
    output_dir: Path,
    paper_id: UUID,
    expected_status: WorkflowStatus,
) -> DecisionResult:
    return _apply_decision(
        output_dir,
        paper_id,
        expected_status,
        required_status=WorkflowStatus.KEPT,
        target_status=WorkflowStatus.IN_ZOTERO,
    )
