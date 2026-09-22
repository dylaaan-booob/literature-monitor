"""Read-only reconstruction of workflow views from durable Paper Markdown."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

from literature_monitor.markdown_state import PaperMarkdownState, parse_paper_state
from literature_monitor.models import (
    ExternalIds,
    MetadataSource,
    PaperVersion,
    VersionRef,
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
    versions: tuple[PaperVersion, ...]
    sources: tuple[MetadataSource, ...]
    preferred_version: VersionRef | None
    zotero_key: str | None


@dataclass(frozen=True)
class WorkspaceIssue:
    path: Path
    message: str


@dataclass(frozen=True)
class WorkspaceSnapshot:
    papers: tuple[WorkspacePaper, ...]
    issues: tuple[WorkspaceIssue, ...]

    @property
    def inbox(self) -> tuple[WorkspacePaper, ...]:
        return tuple(
            paper
            for paper in self.papers
            if paper.status is WorkflowStatus.CANDIDATE
        )

    @property
    def kept(self) -> tuple[WorkspacePaper, ...]:
        return tuple(
            paper for paper in self.papers if paper.status is WorkflowStatus.KEPT
        )

    @property
    def rejected(self) -> tuple[WorkspacePaper, ...]:
        return tuple(
            paper
            for paper in self.papers
            if paper.status is WorkflowStatus.REJECTED
        )

    @property
    def in_zotero(self) -> tuple[WorkspacePaper, ...]:
        return tuple(
            paper
            for paper in self.papers
            if paper.status is WorkflowStatus.IN_ZOTERO
        )


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
        versions=state.versions,
        sources=state.sources,
        preferred_version=state.preferred_version,
        zotero_key=state.zotero_key,
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


def load_workspace(output_dir: Path) -> WorkspaceSnapshot:
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
    )
