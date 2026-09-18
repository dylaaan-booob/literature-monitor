"""Read-only export of kept Papers from durable Markdown state."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path

from literature_monitor.identifiers import normalize_doi
from literature_monitor.markdown_state import PaperMarkdownState, parse_paper_state
from literature_monitor.models import WorkflowStatus


@dataclass(frozen=True)
class KeptExportIssue:
    path: Path
    message: str


@dataclass(frozen=True)
class KeptExportResult:
    entries: tuple[str, ...]
    issues: tuple[KeptExportIssue, ...]

    @property
    def has_errors(self) -> bool:
        return bool(self.issues)


def _manual_text(value: str) -> str:
    return " ".join(value.split())


def _safe_identifier(value: str | None) -> str | None:
    if value is None or any(
        character.isspace() or unicodedata.category(character) == "Cc"
        for character in value
    ):
        return None
    return value


def _export_entry(state: PaperMarkdownState) -> str:
    assert state.external_ids is not None
    assert state.title is not None
    assert state.journal is not None

    doi = _safe_identifier(normalize_doi(state.external_ids.doi))
    if doi is not None:
        return doi
    arxiv = _safe_identifier(state.external_ids.arxiv)
    if arxiv is not None:
        return f"arXiv:{arxiv}"
    publication_date = (
        state.publication_date.isoformat()
        if state.publication_date is not None
        else "unknown"
    )
    return "\t".join(
        (
            "MANUAL",
            _manual_text(state.title),
            _manual_text(state.journal),
            publication_date,
        )
    )


def export_kept_papers(output_dir: Path) -> KeptExportResult:
    """Export kept Paper entries without changing durable Markdown state."""

    papers_dir = output_dir / "Papers"
    if not papers_dir.exists():
        return KeptExportResult(entries=(), issues=())
    if not papers_dir.is_dir():
        return KeptExportResult(
            entries=(),
            issues=(
                KeptExportIssue(
                    path=papers_dir,
                    message="Paper directory is not a directory",
                ),
            ),
        )

    entries: list[str] = []
    issues: list[KeptExportIssue] = []
    authors_dir = output_dir / "Authors"
    try:
        paper_paths = sorted(papers_dir.glob("*.md"))
    except OSError as error:
        return KeptExportResult(
            entries=(),
            issues=(
                KeptExportIssue(
                    path=papers_dir,
                    message=f"cannot scan Papers directory: {error}",
                ),
            ),
        )

    for path in paper_paths:
        if not path.is_file():
            issues.append(
                KeptExportIssue(path=path, message="Paper target is not a regular file")
            )
            continue
        try:
            contents = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeError) as error:
            issues.append(
                KeptExportIssue(path=path, message=f"cannot read Paper: {error}")
            )
            continue

        state = parse_paper_state(path, contents, authors_dir)
        if state is None:
            continue
        if state.problems:
            issues.extend(
                KeptExportIssue(path=path, message=problem)
                for problem in state.problems
            )
            continue
        if state.status is WorkflowStatus.KEPT:
            entries.append(_export_entry(state))

    return KeptExportResult(entries=tuple(entries), issues=tuple(issues))
