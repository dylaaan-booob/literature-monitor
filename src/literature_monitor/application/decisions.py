"""Stable application boundary for Paper workflow decisions."""

from __future__ import annotations

from dataclasses import dataclass as _dataclass
from enum import Enum
import os as _os
from pathlib import Path
import re as _re
import stat as _stat
from uuid import UUID

from literature_monitor.identifiers import normalize_doi as _normalize_doi
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
    replace_text_if_unchanged as _replace_text_if_unchanged,
)
from literature_monitor.zotero_local import (
    ZoteroLocalClient as _ZoteroLocalClient,
    ZoteroReadOutcome as _ZoteroReadOutcome,
)

__all__ = [
    "DecisionOutcome",
    "DecisionResult",
    "ReconciliationGuard",
    "keep_paper",
    "reject_paper",
    "reconcile_paper_with_zotero",
]


class DecisionOutcome(str, Enum):
    UPDATED = "UPDATED"
    NOT_FOUND = "NOT_FOUND"
    INVALID_PAPER = "INVALID_PAPER"
    STATE_CONFLICT = "STATE_CONFLICT"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    IO_FAILURE = "IO_FAILURE"
    ZOTERO_NOT_FOUND = "ZOTERO_NOT_FOUND"
    ZOTERO_DUPLICATE = "ZOTERO_DUPLICATE"
    ZOTERO_FAILURE = "ZOTERO_FAILURE"


@_dataclass(frozen=True)
class ReconciliationGuard:
    output_dir: Path
    workspace_identity: tuple[int, int]
    papers_directory_identity: tuple[int, int]
    paper_path: Path
    paper_file_identity: tuple[int, int]
    paper_id: UUID
    normalized_doi: str


@_dataclass(frozen=True)
class DecisionResult:
    outcome: DecisionOutcome
    paper_id: UUID
    expected_status: WorkflowStatus
    current_status: WorkflowStatus | None
    resulting_status: WorkflowStatus | None
    path: Path | None
    message: str
    reconciled_doi: str | None = None
    capture_guard: ReconciliationGuard | None = None


def _failure(
    outcome: DecisionOutcome,
    paper_id: UUID,
    expected_status: WorkflowStatus,
    message: str,
    *,
    current_status: WorkflowStatus | None = None,
    path: Path | None = None,
    reconciled_doi: str | None = None,
    capture_guard: ReconciliationGuard | None = None,
) -> DecisionResult:
    return DecisionResult(
        outcome=outcome,
        paper_id=paper_id,
        expected_status=expected_status,
        current_status=current_status,
        resulting_status=None,
        path=path,
        message=message,
        reconciled_doi=reconciled_doi,
        capture_guard=capture_guard,
    )


@_dataclass(frozen=True)
class _ReconciliationRead:
    contents: str
    directory_identity: tuple[int, int]
    file_identity: tuple[int, int]


def _absolute_path(path: Path) -> Path:
    return Path(_os.path.abspath(_os.fspath(path)))


def _open_reconciliation_context(
    output_dir: Path,
) -> tuple[int, int, tuple[int, int]]:
    workspace: int | None = None
    papers: int | None = None
    try:
        workspace = _os.open(output_dir, _os.O_RDONLY | _os.O_DIRECTORY)
        workspace_stat = _os.fstat(workspace)
        papers = _os.open(
            "Papers",
            _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW,
            dir_fd=workspace,
        )
        return (
            workspace,
            papers,
            (workspace_stat.st_dev, workspace_stat.st_ino),
        )
    except Exception:
        if papers is not None:
            _os.close(papers)
        if workspace is not None:
            _os.close(workspace)
        raise


def _revalidate_not_found_capture_state(
    output_dir: Path,
    paper_id: UUID,
    expected_status: WorkflowStatus,
    *,
    capture_guard: ReconciliationGuard,
) -> DecisionResult | None:
    """Fail closed if the Paper changed while authoritative preflight was running."""

    workspace: int | None = None
    directory: int | None = None
    reads: dict[Path, _ReconciliationRead] = {}
    try:
        workspace, directory, workspace_identity = _open_reconciliation_context(
            output_dir,
        )
        current_path, failure = _locate_paper(
            output_dir,
            paper_id,
            expected_status,
            require_safe_candidates=True,
            directory=directory,
            reconciliation_reads=reads,
        )
    except FileNotFoundError:
        return _failure(
            DecisionOutcome.STATE_CONFLICT,
            paper_id,
            expected_status,
            "Paper disappeared during Zotero preflight.",
            path=capture_guard.paper_path,
        )
    except OSError:
        return _failure(
            DecisionOutcome.IO_FAILURE,
            paper_id,
            expected_status,
            "Cannot safely verify Paper after Zotero preflight.",
            path=capture_guard.paper_path,
        )
    finally:
        if directory is not None:
            _os.close(directory)
        if workspace is not None:
            _os.close(workspace)

    if failure is not None:
        return _failure(
            failure.outcome,
            paper_id,
            expected_status,
            failure.message,
            current_status=failure.current_status,
            path=failure.path,
        )
    assert current_path is not None
    current_read = reads[current_path]
    if (
        _absolute_path(output_dir) != capture_guard.output_dir
        or workspace_identity != capture_guard.workspace_identity
        or current_read.directory_identity
        != capture_guard.papers_directory_identity
        or _absolute_path(current_path) != capture_guard.paper_path
        or current_read.file_identity != capture_guard.paper_file_identity
    ):
        return _failure(
            DecisionOutcome.STATE_CONFLICT,
            paper_id,
            expected_status,
            "Paper location changed during Zotero preflight.",
            path=current_path,
        )

    state = _parse_paper_state(
        current_path,
        current_read.contents,
        output_dir / "Authors",
    )
    if (
        state is None
        or state.paper_id != paper_id
        or state.problems
        or not state.updateable
        or state.frontmatter is None
        or state.status is None
        or state.external_ids is None
    ):
        return _failure(
            DecisionOutcome.INVALID_PAPER,
            paper_id,
            expected_status,
            "Paper changed into an invalid state during Zotero preflight.",
            current_status=state.status if state is not None else None,
            path=current_path,
        )
    if state.status is not expected_status:
        return _failure(
            DecisionOutcome.STATE_CONFLICT,
            paper_id,
            expected_status,
            (
                f"Paper status changed from expected {expected_status.value} "
                f"to {state.status.value} during Zotero preflight."
            ),
            current_status=state.status,
            path=current_path,
        )
    try:
        current_doi = _normalize_doi(state.external_ids.doi)
    except ValueError:
        current_doi = None
    if current_doi != capture_guard.normalized_doi:
        return _failure(
            DecisionOutcome.STATE_CONFLICT,
            paper_id,
            expected_status,
            "Paper DOI changed during Zotero preflight.",
            current_status=state.status,
            path=current_path,
        )
    existing_key = state.frontmatter.get("zotero_key")
    if existing_key is not None and not _re.fullmatch(r"[A-Z0-9]{8}", existing_key):
        return _failure(
            DecisionOutcome.INVALID_PAPER,
            paper_id,
            expected_status,
            "Paper Zotero key is malformed; repair it before checking Zotero.",
            current_status=state.status,
            path=current_path,
        )
    return None


def _read_reconciliation_candidate(path: Path, directory: int) -> _ReconciliationRead:
    descriptor = _os.open(path.name, _os.O_RDONLY | _os.O_NOFOLLOW | _os.O_NONBLOCK, dir_fd=directory)
    try:
        parent = _os.fstat(directory)
        target = _os.fstat(descriptor)
        if not _stat.S_ISREG(target.st_mode):
            raise OSError("Paper candidate is not a regular file")
        with _os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            contents = handle.read().decode("utf-8")
        return _ReconciliationRead(contents, (parent.st_dev, parent.st_ino), (target.st_dev, target.st_ino))
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
    reconciliation_reads: dict[Path, _ReconciliationRead] | None = None,
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
                action_read = _read_reconciliation_candidate(path, directory)
                assert reconciliation_reads is not None
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
            reconciliation_reads[path] = action_read
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


def _apply_decision(
    output_dir: Path,
    paper_id: UUID,
    expected_status: WorkflowStatus,
    *,
    required_status: WorkflowStatus,
    target_status: WorkflowStatus,
    expected_reconciliation_doi: str | None = None,
    expected_capture_guard: ReconciliationGuard | None = None,
) -> DecisionResult:
    is_reconciliation = target_status is WorkflowStatus.IN_ZOTERO
    reconciliation_reads: dict[Path, _ReconciliationRead] = {}
    workspace_identity: tuple[int, int] | None = None
    if is_reconciliation:
        workspace = None
        directory = None
        try:
            # Bind the reconciliation read to one workspace/Papers directory pair.
            workspace, directory, workspace_identity = _open_reconciliation_context(
                output_dir,
            )
            path, failure = _locate_paper(
                output_dir, paper_id, expected_status, require_safe_candidates=True,
                directory=directory, reconciliation_reads=reconciliation_reads,
            )
        except FileNotFoundError:
            return _failure(DecisionOutcome.NOT_FOUND, paper_id, expected_status, "Paper UUID was not found")
        except OSError:
            return _failure(DecisionOutcome.IO_FAILURE, paper_id, expected_status,
                            "Cannot safely read or locate the requested Paper.")
        finally:
            if directory is not None:
                _os.close(directory)
            if workspace is not None:
                _os.close(workspace)
    else:
        path, failure = _locate_paper(output_dir, paper_id, expected_status)
    if failure is not None:
        if is_reconciliation and failure.outcome is DecisionOutcome.IO_FAILURE:
            return _failure(
                failure.outcome, paper_id, expected_status,
                "Cannot safely read or locate the requested Paper.", path=failure.path,
            )
        return failure
    assert path is not None

    if not is_reconciliation and (path.is_symlink() or not path.is_file()):
        return _failure(
            DecisionOutcome.INVALID_PAPER,
            paper_id,
            expected_status,
            "Paper target is no longer a safe regular file",
            path=path,
        )

    try:
        current_contents = reconciliation_reads[path].contents if is_reconciliation else _read_text_exact(path)
    except (OSError, UnicodeError) as error:
        return _failure(
            DecisionOutcome.IO_FAILURE,
            paper_id,
            expected_status,
            "Cannot read Paper before Zotero reconciliation." if is_reconciliation else f"cannot read Paper before decision: {error}",
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
        if is_reconciliation:
            message = "Paper cannot be safely updated; repair its invalid state before checking Zotero."
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
    if is_reconciliation:
        assert workspace_identity is not None
        try:
            doi = _normalize_doi(state.external_ids.doi) if state.external_ids else None
        except ValueError:
            doi = None
        if doi is None:
            return _failure(
                DecisionOutcome.INVALID_PAPER, paper_id, expected_status,
                "A valid Paper DOI is required to check Zotero.",
                current_status=current_status, path=path,
            )
        if expected_reconciliation_doi is not None:
            try:
                normalized_expected_doi = _normalize_doi(expected_reconciliation_doi)
            except ValueError:
                normalized_expected_doi = None
            if normalized_expected_doi != expected_reconciliation_doi:
                return _failure(
                    DecisionOutcome.INVALID_PAPER,
                    paper_id,
                    expected_status,
                    "Automatic Zotero reconciliation expected DOI is invalid.",
                    current_status=current_status,
                    path=path,
                )
            if doi != expected_reconciliation_doi:
                return _failure(
                    DecisionOutcome.STATE_CONFLICT,
                    paper_id,
                    expected_status,
                    "Paper DOI changed after automatic capture started.",
                    current_status=current_status,
                    path=path,
                )
        action_read = reconciliation_reads[path]
        current_capture_guard = ReconciliationGuard(
            output_dir=_absolute_path(output_dir),
            workspace_identity=workspace_identity,
            papers_directory_identity=action_read.directory_identity,
            paper_path=_absolute_path(path),
            paper_file_identity=action_read.file_identity,
            paper_id=paper_id,
            normalized_doi=doi,
        )
        if (
            expected_capture_guard is not None
            and current_capture_guard != expected_capture_guard
        ):
            return _failure(
                DecisionOutcome.STATE_CONFLICT,
                paper_id,
                expected_status,
                "Paper or originating workspace identity changed after automatic capture started.",
                current_status=current_status,
                path=path,
            )
        existing_key = frontmatter.get("zotero_key")
        if existing_key is not None and not _re.fullmatch(r"[A-Z0-9]{8}", existing_key):
            return _failure(
                DecisionOutcome.INVALID_PAPER, paper_id, expected_status,
                "Paper Zotero key is malformed; repair it before checking Zotero.",
                current_status=current_status, path=path,
            )
        # Reconciliation always proves DOI uniqueness by complete My Library
        # enumeration; an existing key must never enable the keyed fast path.
        with _ZoteroLocalClient() as client:
            identity = client.resolve_identity(doi)
        if identity.outcome is not _ZoteroReadOutcome.VERIFIED or identity.item is None:
            outcome, message = {
                _ZoteroReadOutcome.NOT_FOUND: (
                    DecisionOutcome.ZOTERO_NOT_FOUND,
                    "No exact DOI match exists in Zotero My Library.",
                ),
                _ZoteroReadOutcome.DUPLICATE: (
                    DecisionOutcome.ZOTERO_DUPLICATE,
                    "Multiple exact DOI matches exist; resolve Zotero duplicates first.",
                ),
            }.get(identity.outcome, (
                DecisionOutcome.ZOTERO_FAILURE,
                "Cannot verify complete Zotero My Library; check Zotero Desktop and its Local API setting.",
            ))
            if outcome is DecisionOutcome.ZOTERO_NOT_FOUND:
                preflight_failure = _revalidate_not_found_capture_state(
                    output_dir,
                    paper_id,
                    expected_status,
                    capture_guard=current_capture_guard,
                )
                if preflight_failure is not None:
                    return preflight_failure
            return _failure(
                outcome, paper_id, expected_status, message,
                current_status=current_status,
                path=path,
                reconciled_doi=doi,
                capture_guard=(
                    current_capture_guard
                    if outcome is DecisionOutcome.ZOTERO_NOT_FOUND
                    else None
                ),
            )
        if existing_key is not None and existing_key != identity.item.key:
            return _failure(
                DecisionOutcome.STATE_CONFLICT, paper_id, expected_status,
                "Paper Zotero key conflicts with the unique DOI match; repair the linkage before checking Zotero.",
                current_status=current_status, path=path, reconciled_doi=doi,
            )
        frontmatter["zotero_key"] = identity.item.key
    frontmatter["status"] = target_status.value
    updated_contents = _serialize_document(frontmatter, state.body)

    try:
        if is_reconciliation:
            action_read = reconciliation_reads[path]
            _replace_regular_text_at_identity(
                path, updated_contents, expected_contents=action_read.contents,
                expected_directory_identity=action_read.directory_identity,
                expected_file_identity=action_read.file_identity,
                expected_workspace_identity=(
                    expected_capture_guard.workspace_identity
                    if expected_capture_guard is not None
                    else None
                ),
            )
        else:
            _replace_text_if_unchanged(path, updated_contents, expected_contents=current_contents)
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
        if is_reconciliation and isinstance(error.__cause__, FileNotFoundError):
            return _failure(
                DecisionOutcome.STATE_CONFLICT, paper_id, expected_status,
                "Paper disappeared before Zotero reconciliation could be written.",
                current_status=current_status, path=path,
            )
        return _failure(
            DecisionOutcome.IO_FAILURE,
            paper_id,
            expected_status,
            "Cannot verify Paper before Zotero reconciliation." if is_reconciliation else f"cannot verify Paper before decision: {error}",
            current_status=current_status,
            path=path,
        )
    except OSError as error:
        return _failure(
            DecisionOutcome.IO_FAILURE,
            paper_id,
            expected_status,
            "Cannot write Zotero reconciliation result." if is_reconciliation else f"cannot write Paper decision: {error}",
            current_status=current_status,
            path=path,
        )

    message = (
        "Zotero exact DOI match verified; Paper linked in Zotero."
        if is_reconciliation
        else f"Paper status updated to {target_status.value}"
    )
    return DecisionResult(
        outcome=DecisionOutcome.UPDATED,
        paper_id=paper_id,
        expected_status=expected_status,
        current_status=current_status,
        resulting_status=target_status,
        path=path,
        message=message,
        reconciled_doi=doi if is_reconciliation else None,
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


def reconcile_paper_with_zotero(
    output_dir: Path,
    paper_id: UUID,
    expected_status: WorkflowStatus,
    *,
    expected_doi: str | None = None,
    capture_guard: ReconciliationGuard | None = None,
) -> DecisionResult:
    return _apply_decision(
        output_dir,
        paper_id,
        expected_status,
        required_status=WorkflowStatus.KEPT,
        target_status=WorkflowStatus.IN_ZOTERO,
        expected_reconciliation_doi=expected_doi,
        expected_capture_guard=capture_guard,
    )
