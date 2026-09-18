"""Creation-only Obsidian Markdown materialization for Task 6."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from literature_monitor.models import Author, CanonicalPaper
from literature_monitor.naming import paper_filename


MISSING_ABSTRACT = "Abstract unavailable from current MVP data sources."
_ORCID_PATTERN = re.compile(
    r"(?:https?://orcid\.org/)?(\d{4}-\d{4}-\d{4}-\d{3}[\dX])/?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MaterializationIssue:
    path: Path
    message: str


@dataclass(frozen=True)
class MaterializationResult:
    created_papers: tuple[Path, ...]
    existing_papers: tuple[Path, ...]
    created_authors: tuple[Path, ...]
    existing_authors: tuple[Path, ...]
    issues: tuple[MaterializationIssue, ...]

    @property
    def has_errors(self) -> bool:
        return bool(self.issues)


@dataclass(frozen=True)
class _PaperTarget:
    paper: CanonicalPaper
    path: Path
    author_stems: tuple[str, ...]
    author_paths: tuple[Path, ...]


def _yaml_frontmatter(values: dict[str, object]) -> str:
    rendered = yaml.safe_dump(values, sort_keys=False, allow_unicode=True).rstrip()
    return f"---\n{rendered}\n---\n"


def _version_summary(paper: CanonicalPaper) -> str:
    if not paper.versions:
        return "- None recorded."
    lines: list[str] = []
    for version in paper.versions:
        details = [
            f"{version.kind.value}: {version.source} {version.identifier}",
            f"date={version.date.isoformat() if version.date else 'unknown'}",
        ]
        if version.url is not None:
            details.append(f"url={version.url}")
        lines.append(f"- {'; '.join(details)}")
    return "\n".join(lines)


def _source_summary(paper: CanonicalPaper) -> str:
    if not paper.sources:
        return "- None recorded."
    return "\n".join(
        f"- {source.provider}: {source.record_id}; "
        f"retrieved_at={source.retrieved_at.isoformat()}"
        for source in paper.sources
    )


def render_paper_markdown(
    paper: CanonicalPaper,
    author_note_stems: Sequence[str],
) -> str:
    """Render one Paper note without touching the filesystem."""

    if len(author_note_stems) != len(paper.authors):
        raise ValueError("author note stems must match paper authors")

    author_links = [
        f"[[Authors/{stem}|{author.name}]]"
        for author, stem in zip(paper.authors, author_note_stems, strict=True)
    ]
    workflow = paper.workflow.model_dump(mode="json")
    frontmatter: dict[str, object] = {
        "type": "paper",
        "id": str(paper.id),
        "title": paper.metadata.title,
        "authors": author_links,
        "journal": paper.metadata.journal,
        "publication_date": (
            paper.metadata.publication_date.isoformat()
            if paper.metadata.publication_date is not None
            else None
        ),
        "doi": paper.external_ids.doi,
        "openalex_id": paper.external_ids.openalex,
        "arxiv_id": paper.external_ids.arxiv,
        "author_keywords": list(paper.metadata.author_keywords),
        "status": workflow["status"],
        "discovered_at": workflow["discovered_at"],
        "preferred_version": (
            paper.preferred_version.model_dump(mode="json")
            if paper.preferred_version is not None
            else None
        ),
        "zotero_key": workflow["zotero_key"],
        "external_ids": paper.external_ids.model_dump(
            mode="json", exclude_none=False
        ),
        "versions": [version.model_dump(mode="json") for version in paper.versions],
        "sources": [source.model_dump(mode="json") for source in paper.sources],
    }
    abstract = (
        MISSING_ABSTRACT
        if paper.metadata.abstract is None
        else paper.metadata.abstract
    )
    return (
        f"{_yaml_frontmatter(frontmatter)}\n"
        f"# {paper.metadata.title}\n\n"
        f"## Abstract\n\n{abstract}\n\n"
        f"## Versions\n\n{_version_summary(paper)}\n\n"
        f"## Sources\n\n{_source_summary(paper)}\n\n"
        "## Notes\n"
    )


def render_author_markdown(author: Author) -> str:
    """Render one minimal Author note without touching the filesystem."""

    return _yaml_frontmatter(
        {
            "type": "author",
            "name": author.name,
            "openalex_id": author.openalex_id,
            "orcid": author.orcid,
        }
    )


def _normalize_orcid(value: str | None) -> str | None:
    if value is None:
        return None
    match = _ORCID_PATTERN.fullmatch(value.strip())
    if match is None:
        return None
    normalized = match.group(1).upper()
    digits = normalized.replace("-", "")
    total = 0
    for character in digits[:15]:
        total = (total + int(character)) * 2
    check_value = (12 - total % 11) % 11
    expected = "X" if check_value == 10 else str(check_value)
    return normalized if digits[-1] == expected else None


def _author_stem(author: Author, paper: CanonicalPaper, ordinal: int) -> str:
    if author.openalex_id is not None:
        token = author.openalex_id.rstrip("/").rsplit("/", maxsplit=1)[-1]
        return f"openalex-{token.casefold()}"
    orcid = _normalize_orcid(author.orcid)
    if orcid is not None:
        return f"orcid-{orcid.casefold()}"
    return f"unidentified-{paper.id.hex}-{ordinal:02d}"


def _ensure_directory(path: Path) -> MaterializationIssue | None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return MaterializationIssue(path=path, message=str(error))
    if not path.is_dir():
        return MaterializationIssue(path=path, message="target is not a directory")
    return None


def _create_file(path: Path, contents: str) -> tuple[str | None, str | None]:
    created = False
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            created = True
            handle.write(contents)
    except FileExistsError:
        if path.is_file():
            return "existing", None
        return None, "target exists but is not a regular file"
    except OSError as error:
        message = str(error)
        if created:
            try:
                path.unlink()
            except OSError as cleanup_error:
                message = (
                    f"{message}; failed to remove incomplete file: {cleanup_error}"
                )
        return None, message
    return "created", None


def materialize_papers(
    papers: Sequence[CanonicalPaper],
    output_dir: Path,
) -> MaterializationResult:
    """Create missing Paper and Author notes without rewriting existing files."""

    paper_ids = tuple(paper.id for paper in papers)
    authors_dir = output_dir / "Authors"
    papers_dir = output_dir / "Papers"
    issues: list[MaterializationIssue] = []
    authors_directory_issue = _ensure_directory(authors_dir)
    papers_directory_issue = _ensure_directory(papers_dir)
    if authors_directory_issue is not None:
        issues.append(authors_directory_issue)
    if papers_directory_issue is not None:
        issues.append(papers_directory_issue)

    author_targets: dict[Path, Author] = {}
    paper_targets: list[_PaperTarget] = []
    handled_paper_paths: set[Path] = set()
    for paper in papers:
        stems = tuple(
            _author_stem(author, paper, ordinal)
            for ordinal, author in enumerate(paper.authors, start=1)
        )
        author_paths = tuple(authors_dir / f"{stem}.md" for stem in stems)
        for path, author in zip(author_paths, paper.authors, strict=True):
            author_targets.setdefault(path, author)
        path = papers_dir / paper_filename(
            paper.metadata.title,
            paper.id,
            paper_ids,
        )
        if path in handled_paper_paths:
            continue
        handled_paper_paths.add(path)
        paper_targets.append(
            _PaperTarget(
                paper=paper,
                path=path,
                author_stems=stems,
                author_paths=author_paths,
            )
        )

    created_authors: list[Path] = []
    existing_authors: list[Path] = []
    available_authors: set[Path] = set()
    if authors_directory_issue is None:
        for path, author in author_targets.items():
            try:
                contents = render_author_markdown(author)
            except Exception as error:
                issues.append(MaterializationIssue(path=path, message=str(error)))
                continue
            outcome, error = _create_file(path, contents)
            if outcome == "created":
                created_authors.append(path)
                available_authors.add(path)
            elif outcome == "existing":
                existing_authors.append(path)
                available_authors.add(path)
            else:
                issues.append(
                    MaterializationIssue(path=path, message=error or "write failed")
                )

    created_papers: list[Path] = []
    existing_papers: list[Path] = []
    if papers_directory_issue is None:
        for target in paper_targets:
            if target.path.is_file():
                existing_papers.append(target.path)
                continue
            if target.path.exists():
                issues.append(
                    MaterializationIssue(
                        path=target.path,
                        message="target exists but is not a regular file",
                    )
                )
                continue
            if not all(path in available_authors for path in target.author_paths):
                continue
            try:
                contents = render_paper_markdown(target.paper, target.author_stems)
            except Exception as error:
                issues.append(
                    MaterializationIssue(path=target.path, message=str(error))
                )
                continue
            outcome, error = _create_file(target.path, contents)
            if outcome == "created":
                created_papers.append(target.path)
            elif outcome == "existing":
                existing_papers.append(target.path)
            else:
                issues.append(
                    MaterializationIssue(
                        path=target.path,
                        message=error or "write failed",
                    )
                )

    return MaterializationResult(
        created_papers=tuple(created_papers),
        existing_papers=tuple(existing_papers),
        created_authors=tuple(created_authors),
        existing_authors=tuple(existing_authors),
        issues=tuple(issues),
    )
