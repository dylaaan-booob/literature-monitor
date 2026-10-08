"""Read-only reconstruction of workflow views from durable Paper Markdown."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from uuid import UUID

from literature_monitor.config import JournalConfig
from literature_monitor.markdown_state import (
    PaperJournalAttributionState,
    PaperMarkdownState,
    parse_paper_state,
)
from literature_monitor.models import (
    ExternalIds,
    MetadataSource,
    WorkflowStatus,
)
from literature_monitor.safe_write import read_text_exact


@dataclass(frozen=True)
class WorkspaceAuthor:
    name: str
    note_stem: str


@dataclass(frozen=True)
class WorkspacePaper:
    paper_id: UUID
    title: str
    journal: str
    publication_date: date | None
    discovered_at: datetime
    status: WorkflowStatus
    abstract: str | None
    author_keywords: tuple[str, ...]
    authors: tuple[WorkspaceAuthor, ...]
    external_ids: ExternalIds
    sources: tuple[MetadataSource, ...]
    zotero_key: str | None
    journal_attribution_state: PaperJournalAttributionState = PaperJournalAttributionState.MISSING_OR_EMPTY
    journal_issns: tuple[str, ...] = ()


class WorkspaceSectionKind(str, Enum):
    GROUP = "group"
    UNGROUPED = "ungrouped"
    UNMAPPED = "unmapped"


@dataclass(frozen=True)
class WorkspaceSection:
    kind: WorkspaceSectionKind
    label: str
    papers: tuple[WorkspacePaper, ...]


@dataclass(frozen=True)
class WorkspaceIssue:
    path: Path
    message: str


@dataclass(frozen=True)
class WorkspaceSnapshot:
    papers: tuple[WorkspacePaper, ...]
    issues: tuple[WorkspaceIssue, ...]
    journals: tuple[JournalConfig, ...] = ()

    def sections_for(self, status: WorkflowStatus) -> tuple[WorkspaceSection, ...]:
        groups = dict.fromkeys(journal.group for journal in self.journals if journal.group is not None)
        keys = (
            *((WorkspaceSectionKind.GROUP, group) for group in groups),
            (WorkspaceSectionKind.UNGROUPED, "Ungrouped"),
            (WorkspaceSectionKind.UNMAPPED, "Unmapped journals"),
        )
        buckets: dict[tuple[WorkspaceSectionKind, str], list[WorkspacePaper]] = {}
        for paper in _ordered_papers([p for p in self.papers if p.status is status]):
            key = _section_key(paper, self.journals)
            buckets.setdefault(key, []).append(paper)
        return tuple(WorkspaceSection(kind, label, tuple(buckets[(kind, label)]))
                     for kind, label in keys if buckets.get((kind, label)))

    def papers_for(self, status: WorkflowStatus) -> tuple[WorkspacePaper, ...]:
        return tuple(paper for section in self.sections_for(status) for paper in section.papers)

    @property
    def inbox(self) -> tuple[WorkspacePaper, ...]:
        return self.papers_for(WorkflowStatus.CANDIDATE)

    @property
    def kept(self) -> tuple[WorkspacePaper, ...]:
        return self.papers_for(WorkflowStatus.KEPT)

    @property
    def rejected(self) -> tuple[WorkspacePaper, ...]:
        return self.papers_for(WorkflowStatus.REJECTED)

    @property
    def in_zotero(self) -> tuple[WorkspacePaper, ...]:
        return self.papers_for(WorkflowStatus.IN_ZOTERO)


def _journal_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _section_key(
    paper: WorkspacePaper, journals: Sequence[JournalConfig],
) -> tuple[WorkspaceSectionKind, str]:
    if paper.journal_attribution_state is PaperJournalAttributionState.VALID:
        identities = set(paper.journal_issns)
        matches = [journal for journal in journals if journal.issn_l in identities]
    elif paper.journal_attribution_state is PaperJournalAttributionState.MISSING_OR_EMPTY:
        matches = [journal for journal in journals if _journal_name(journal.name) == _journal_name(paper.journal)]
    else:
        matches = []
    if len(matches) != 1:
        return WorkspaceSectionKind.UNMAPPED, "Unmapped journals"
    group = matches[0].group
    return ((WorkspaceSectionKind.GROUP, group) if group is not None else
            (WorkspaceSectionKind.UNGROUPED, "Ungrouped"))


def _project_paper(state: PaperMarkdownState) -> WorkspacePaper:
    # A state with no parser problems satisfies these durable Paper invariants.
    assert state.paper_id is not None
    assert state.title is not None
    assert state.journal is not None
    assert state.discovered_at is not None
    assert state.status is not None
    assert state.external_ids is not None
    return WorkspacePaper(
        paper_id=state.paper_id,
        title=state.title,
        journal=state.journal,
        publication_date=state.publication_date,
        discovered_at=state.discovered_at,
        status=state.status,
        abstract=state.abstract,
        author_keywords=state.author_keywords,
        authors=tuple(
            WorkspaceAuthor(name=link.alias, note_stem=link.stem)
            for link in state.author_links
        ),
        external_ids=state.external_ids,
        sources=state.sources,
        zotero_key=state.zotero_key,
        journal_attribution_state=state.journal_attribution_state,
        journal_issns=state.journal_issns,
    )


def _ordered_papers(papers: list[WorkspacePaper]) -> tuple[WorkspacePaper, ...]:
    # Stable sorts express the specified precedence without inventing a date
    # for Papers whose optional publication_date is absent.
    ordered = sorted(papers, key=lambda paper: paper.title)
    ordered.sort(
        key=lambda paper: (
            paper.publication_date is not None,
            paper.publication_date,
        ),
        reverse=True,
    )
    ordered.sort(key=lambda paper: paper.discovered_at, reverse=True)
    return tuple(ordered)


def load_workspace(output_dir: Path, journals: Sequence[JournalConfig] = ()) -> WorkspaceSnapshot:
    """Load current Paper Markdown into pre-sorted workflow views without writes."""

    papers_dir = output_dir / "Papers"
    if not papers_dir.exists():
        return WorkspaceSnapshot(papers=(), issues=())
    if not papers_dir.is_dir():
        return WorkspaceSnapshot(
            papers=(),
            issues=(
                WorkspaceIssue(
                    path=papers_dir,
                    message="Paper directory is not a directory",
                ),
            ),
        )

    try:
        paths = sorted(papers_dir.glob("*.md"))
    except OSError as error:
        return WorkspaceSnapshot(
            papers=(),
            issues=(
                WorkspaceIssue(
                    path=papers_dir,
                    message=f"cannot scan Papers directory: {error}",
                ),
            ),
        )

    papers: list[WorkspacePaper] = []
    issues: list[WorkspaceIssue] = []
    authors_dir = output_dir / "Authors"
    for path in paths:
        if path.is_symlink() or not path.is_file():
            issues.append(
                WorkspaceIssue(
                    path=path,
                    message="Paper target is not a regular file",
                )
            )
            continue
        try:
            contents = read_text_exact(path)
        except (OSError, UnicodeError) as error:
            issues.append(
                WorkspaceIssue(path=path, message=f"cannot read Paper: {error}")
            )
            continue

        state = parse_paper_state(path, contents, authors_dir)
        if state is None:
            continue
        if state.problems:
            issues.extend(
                WorkspaceIssue(path=path, message=problem)
                for problem in state.problems
            )
            continue
        papers.append(_project_paper(state))

    return WorkspaceSnapshot(
        papers=_ordered_papers(papers),
        issues=tuple(issues),
        journals=tuple(journals),
    )
