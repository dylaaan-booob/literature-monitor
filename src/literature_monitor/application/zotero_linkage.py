"""Original-action null/missing Zotero key linkage (SPEC §36.4)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from uuid import UUID

from literature_monitor.identifiers import normalize_doi
from literature_monitor.markdown_state import PaperMarkdownState, serialize_document
from literature_monitor.models import WorkflowStatus
from literature_monitor.safe_write import (
    CompareReadError,
    ContentChangedError,
    replace_regular_text_at_identity,
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


def link_paper_to_zotero(
    state: PaperMarkdownState, identity: ZoteroIdentityResult,
    *, expected_directory_identity: tuple[int, int], expected_file_identity: tuple[int, int],
) -> LinkageResult:
    """Compare only against the acquisition action snapshot; never relocate/reparse."""
    paper_id, path = state.paper_id, state.path
    if paper_id is None:
        raise ValueError("A Paper action UUID is required.")

    def result(outcome: LinkageOutcome, message: str) -> LinkageResult:
        return LinkageResult(outcome, paper_id, path, message)

    if identity.outcome is not ZoteroReadOutcome.VERIFIED or identity.item is None:
        return result(LinkageOutcome.UNVERIFIED, "Zotero identity has not been verified.")
    if state.problems or not state.updateable or state.frontmatter is None or state.body is None:
        return result(LinkageOutcome.INVALID_PAPER, "Paper action snapshot cannot be safely updated.")
    if state.status is not WorkflowStatus.IN_ZOTERO:
        return result(LinkageOutcome.STATE_CONFLICT, "Paper action snapshot is not in_zotero.")
    try:
        doi = normalize_doi(state.external_ids.doi) if state.external_ids is not None else None
    except ValueError:
        doi = None
    if doi is None or doi != identity.item.normalized_doi:
        return result(LinkageOutcome.STATE_CONFLICT, "Paper action DOI does not match the verified Zotero parent.")
    if state.frontmatter.get("zotero_key") is not None:
        return result(LinkageOutcome.ALREADY_LINKED, "Existing Zotero key preserved without a write.")
    frontmatter = dict(state.frontmatter)
    frontmatter["zotero_key"] = identity.item.key
    updated = serialize_document(frontmatter, state.body)
    try:
        replace_regular_text_at_identity(
            path, updated, expected_contents=state.original,
            expected_directory_identity=expected_directory_identity,
            expected_file_identity=expected_file_identity,
        )
    except ContentChangedError:
        return result(LinkageOutcome.STATE_CONFLICT, "Paper content changed before linkage could be written.")
    except CompareReadError:
        return result(LinkageOutcome.IO_FAILURE, "Cannot verify Paper content before linkage.")
    except OSError:
        return result(LinkageOutcome.IO_FAILURE, "Cannot write Paper linkage.")
    return result(LinkageOutcome.LINKED, "Verified Zotero key linked; Paper workflow status is unchanged.")
