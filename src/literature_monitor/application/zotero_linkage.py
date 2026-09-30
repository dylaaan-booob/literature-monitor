"""Independent null/missing Zotero key linkage; never a workflow decision (§35.4)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from uuid import UUID

from literature_monitor.identifiers import normalize_doi
from literature_monitor.markdown_state import parse_paper_state, serialize_document
from literature_monitor.models import WorkflowStatus
from literature_monitor.safe_write import (
    CompareReadError,
    ContentChangedError,
    read_text_exact,
    replace_text_if_unchanged,
)
from literature_monitor.zotero_local import ZoteroIdentityResult, ZoteroReadOutcome


class LinkageOutcome(str, Enum):
    LINKED = "LINKED"
    ALREADY_LINKED = "ALREADY_LINKED"
    UNVERIFIED = "UNVERIFIED"
    NOT_FOUND = "NOT_FOUND"
    INVALID_PAPER = "INVALID_PAPER"
    STATE_CONFLICT = "STATE_CONFLICT"
    IO_FAILURE = "IO_FAILURE"


@dataclass(frozen=True)
class LinkageResult:
    outcome: LinkageOutcome
    paper_id: UUID
    path: Path | None
    message: str


def _locate_paper(output_dir: Path, paper_id: UUID) -> tuple[Path | None, LinkageResult | None]:
    papers_dir = output_dir / "Papers"
    if papers_dir.is_symlink() or (papers_dir.exists() and not papers_dir.is_dir()):
        return None, LinkageResult(LinkageOutcome.IO_FAILURE, paper_id, papers_dir, "Papers path is not a safe directory.")
    if not papers_dir.exists():
        return None, LinkageResult(LinkageOutcome.NOT_FOUND, paper_id, None, "Paper UUID was not found.")
    try:
        paths = sorted(papers_dir.glob("*.md"))
    except OSError:
        return None, LinkageResult(LinkageOutcome.IO_FAILURE, paper_id, papers_dir, "Cannot scan Papers directory.")
    matches: list[Path] = []
    unsafe: Path | None = None
    for path in paths:
        if path.is_symlink() or not path.is_file():
            unsafe = unsafe or path
            continue
        try:
            contents = read_text_exact(path)
        except (OSError, UnicodeError):
            return None, LinkageResult(LinkageOutcome.IO_FAILURE, paper_id, path, "Cannot read a Paper candidate safely.")
        state = parse_paper_state(path, contents, output_dir / "Authors")
        if state is None:
            continue
        if state.paper_id is None:
            unsafe = unsafe or path
        elif state.paper_id == paper_id:
            matches.append(path)
    if len(matches) > 1:
        return None, LinkageResult(LinkageOutcome.INVALID_PAPER, paper_id, None, "Paper UUID has multiple locations.")
    if matches:
        return matches[0], None
    if unsafe is not None:
        return None, LinkageResult(LinkageOutcome.INVALID_PAPER, paper_id, unsafe, "Cannot determine a safe Paper UUID location.")
    return None, LinkageResult(LinkageOutcome.NOT_FOUND, paper_id, None, "Paper UUID was not found.")


def link_paper_to_zotero(
    output_dir: Path, paper_id: UUID, identity: ZoteroIdentityResult,
) -> LinkageResult:
    """Persist only a verified missing key, after current-disk eligibility checks."""
    if identity.outcome is not ZoteroReadOutcome.VERIFIED or identity.item is None:
        return LinkageResult(LinkageOutcome.UNVERIFIED, paper_id, None, "Zotero identity has not been verified.")
    try:
        path, failure = _locate_paper(output_dir, paper_id)
    except OSError:
        return LinkageResult(
            LinkageOutcome.IO_FAILURE, paper_id, None,
            "Cannot inspect the Paper location safely.",
        )
    if failure is not None:
        return failure
    assert path is not None

    def result(outcome: LinkageOutcome, message: str) -> LinkageResult:
        return LinkageResult(outcome, paper_id, path, message)

    try:
        if not path.exists():
            return result(LinkageOutcome.STATE_CONFLICT, "Paper disappeared after relocation.")
        if path.is_symlink() or not path.is_file():
            return result(LinkageOutcome.INVALID_PAPER, "Paper target is no longer a safe regular file.")
        contents = read_text_exact(path)
    except FileNotFoundError:
        return result(LinkageOutcome.STATE_CONFLICT, "Paper disappeared after relocation.")
    except (OSError, UnicodeError):
        return result(LinkageOutcome.IO_FAILURE, "Cannot read Paper before linkage.")
    state = parse_paper_state(path, contents, output_dir / "Authors")
    if state is None or state.paper_id != paper_id:
        return result(LinkageOutcome.STATE_CONFLICT, "Paper UUID changed after relocation.")
    if state.problems or not state.updateable or state.frontmatter is None or state.body is None:
        return result(LinkageOutcome.INVALID_PAPER, "Paper cannot be safely updated.")
    if state.status is not WorkflowStatus.IN_ZOTERO:
        return result(LinkageOutcome.STATE_CONFLICT, "Paper is no longer in_zotero.")
    try:
        doi = normalize_doi(state.external_ids.doi) if state.external_ids is not None else None
    except ValueError:
        doi = None
    if doi is None or doi != identity.item.normalized_doi:
        return result(LinkageOutcome.STATE_CONFLICT, "Paper DOI no longer matches the verified Zotero item.")
    if state.frontmatter.get("zotero_key") is not None:
        return result(LinkageOutcome.ALREADY_LINKED, "Existing Zotero key preserved without a write.")
    frontmatter = dict(state.frontmatter)
    frontmatter["zotero_key"] = identity.item.key
    updated = serialize_document(frontmatter, state.body)
    try:
        replace_text_if_unchanged(path, updated, expected_contents=contents)
    except ContentChangedError:
        return result(LinkageOutcome.STATE_CONFLICT, "Paper content changed before linkage could be written.")
    except CompareReadError:
        try:
            if not path.exists():
                return result(LinkageOutcome.STATE_CONFLICT, "Paper disappeared before linkage could be written.")
        except OSError:
            pass
        return result(LinkageOutcome.IO_FAILURE, "Cannot verify Paper content before linkage.")
    except OSError:
        return result(LinkageOutcome.IO_FAILURE, "Cannot write Paper linkage.")
    return result(LinkageOutcome.LINKED, "Verified Zotero key linked; Paper workflow status is unchanged.")
