"""Capture-only Obsidian Markdown materialization over durable corpus state."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from uuid import UUID

import yaml

from literature_monitor.identifiers import normalize_doi
from literature_monitor.inbox import render_default_inbox_base
from literature_monitor.markdown_state import (
    MISSING_ABSTRACT,
    AuthorMarkdownState,
    PaperIdentity,
    parse_paper_identity,
    normalize_openalex_author_id,
    normalize_orcid,
    normalize_text,
    parse_author_state,
    render_abstract_section,
)
from literature_monitor.models import (
    Author,
    CanonicalPaper,
    MetadataSource,
)
from literature_monitor.naming import paper_filename
from literature_monitor.progress import (
    ActivityKind,
    ActivityUpdate,
    ProgressCallback,
    ProgressEvent,
)
from literature_monitor.safe_write import (
    CompareReadError,
    ContentChangedError,
    TextWriteCommittedError,
    WorkspaceOperationLock,
    WorkspaceTextDirectory,
    workspace_text_directory,
    read_text_exact,
    workspace_operation_lock,
)


class MaterializationIssueSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class MaterializationIssue:
    path: Path
    message: str
    severity: MaterializationIssueSeverity = MaterializationIssueSeverity.ERROR


@dataclass(frozen=True)
class MaterializationResult:
    created_papers: tuple[Path, ...]
    existing_papers: tuple[Path, ...]
    created_authors: tuple[Path, ...]
    existing_authors: tuple[Path, ...]
    issues: tuple[MaterializationIssue, ...]

    @property
    def has_errors(self) -> bool:
        return any(
            issue.severity is MaterializationIssueSeverity.ERROR
            for issue in self.issues
        )


@dataclass(frozen=True)
class _PaperMatch:
    state: PaperIdentity | None
    blocked: bool = False


@dataclass(frozen=True)
class _PaperWrite:
    path: Path
    contents: str


def _report_activity(
    callback: ProgressCallback | None,
    activity: ActivityUpdate,
) -> None:
    if callback is not None:
        callback(ProgressEvent(activity=activity))


def _yaml_frontmatter(values: dict[str, object]) -> str:
    rendered = yaml.safe_dump(values, sort_keys=False, allow_unicode=True).rstrip()
    return f"---\n{rendered}\n---\n"


def _source_summary(sources: Sequence[MetadataSource]) -> str:
    if not sources:
        return "- None recorded."
    return "\n".join(
        f"- {source.provider}: {source.record_id}; "
        f"retrieved_at={source.retrieved_at.isoformat()}"
        for source in sources
    )


def render_paper_markdown(
    paper: CanonicalPaper,
    author_note_stems: Sequence[str],
) -> str:
    """Render a new Paper note without touching the filesystem."""

    if len(author_note_stems) != len(paper.authors):
        raise ValueError("author note stems must match paper authors")

    author_links = [
        f"[[Authors/{stem}|{author.name}]]"
        for author, stem in zip(paper.authors, author_note_stems, strict=True)
    ]
    doi = normalize_doi(paper.external_ids.doi)
    if doi is None or not re.fullmatch(r"10\.\d{4,9}/[^\s]+", doi):
        raise ValueError("Paper requires a valid DOI")
    external_ids = paper.external_ids.model_copy(update={"doi": doi})
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
        "doi": doi,
        "openalex_id": paper.external_ids.openalex,
        "arxiv_id": paper.external_ids.arxiv,
        "author_keywords": list(paper.metadata.author_keywords),
        "status": workflow["status"],
        "discovered_at": workflow["discovered_at"],
        "external_ids": external_ids.model_dump(
            mode="json", exclude_none=False
        ),
        "sources": [source.model_dump(mode="json") for source in paper.sources],
    }
    if paper.journal_issns:
        frontmatter["journal_issns"] = list(paper.journal_issns)
    return (
        f"{_yaml_frontmatter(frontmatter)}\n"
        f"# {paper.metadata.title}\n\n"
        f"{render_abstract_section(paper.metadata.abstract)}"
        f"## Sources\n\n{_source_summary(paper.sources)}\n\n"
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


def _author_stem(author: Author, paper_id: UUID, ordinal: int) -> str:
    openalex = normalize_openalex_author_id(author.openalex_id)
    if openalex is not None:
        return f"openalex-{openalex}"
    orcid = normalize_orcid(author.orcid)
    if orcid is not None:
        return f"orcid-{orcid.casefold()}"
    return f"unidentified-{paper_id.hex}-{ordinal:02d}"


def _create_file(path: Path, contents: str, directory: WorkspaceTextDirectory) -> tuple[str | None, str | None]:
    try:
        directory.create(path.name, contents)
    except TextWriteCommittedError as error:
        return "created", str(error)
    except FileExistsError:
        target = directory.path / path.name
        if target.is_file() and not target.is_symlink():
            return "existing", None
        return None, "target exists but is not a regular file"
    except (OSError, ContentChangedError, CompareReadError) as error:
        return None, str(error)
    return "created", None


def _scan_papers(
    papers_dir: Path,
    issues: list[MaterializationIssue],
) -> tuple[list[PaperIdentity], bool]:
    states: list[PaperIdentity] = []
    complete = True
    try:
        paths = sorted(papers_dir.iterdir())
    except OSError as error:
        issues.append(MaterializationIssue(papers_dir, f"cannot enumerate Papers: {error}"))
        return states, False
    for path in paths:
        if path.suffix != ".md":
            continue
        if path.is_symlink() or not path.is_file():
            issues.append(MaterializationIssue(path, "Paper target is not a regular file"))
            complete = False
            continue
        try:
            state = parse_paper_identity(path, read_text_exact(path))
        except (OSError, UnicodeError) as error:
            issues.append(MaterializationIssue(path, f"cannot read Paper: {error}"))
            complete = False
            continue
        if state is None:
            continue
        states.append(state)
        complete = complete and state.index_complete
        issues.extend(MaterializationIssue(path, problem) for problem in state.problems)
    if not complete:
        issues.append(MaterializationIssue(papers_dir, "Paper identity index is incomplete; capture blocked"))
    return states, complete


def _scan_authors(
    authors_dir: Path,
) -> tuple[
    dict[Path, AuthorMarkdownState],
    dict[str, set[Path]],
    dict[str, set[Path]],
]:
    states: dict[Path, AuthorMarkdownState] = {}
    openalex_index: dict[str, set[Path]] = defaultdict(set)
    orcid_index: dict[str, set[Path]] = defaultdict(set)
    if not authors_dir.is_dir():
        return states, openalex_index, orcid_index
    for path in sorted(authors_dir.glob("*.md")):
        if not path.is_file():
            continue
        try:
            state = parse_author_state(path, read_text_exact(path))
        except (OSError, UnicodeError):
            state = None
        if state is None:
            continue
        states[path] = state
        if state.openalex_key is not None:
            openalex_index[state.openalex_key].add(path)
        if state.orcid_key is not None:
            orcid_index[state.orcid_key].add(path)
    return states, openalex_index, orcid_index


def _author_stable_keys(author: Author) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    openalex = normalize_openalex_author_id(author.openalex_id)
    orcid = normalize_orcid(author.orcid)
    if openalex is not None:
        keys.add(("openalex", openalex))
    if orcid is not None:
        keys.add(("orcid", orcid))
    return keys


def _state_stable_keys(state: AuthorMarkdownState | None) -> set[tuple[str, str]]:
    if state is None:
        return set()
    keys: set[tuple[str, str]] = set()
    if state.openalex_key is not None:
        keys.add(("openalex", state.openalex_key))
    if state.orcid_key is not None:
        keys.add(("orcid", state.orcid_key))
    return keys


def _stable_keys_match_without_conflict(
    incoming_keys: set[tuple[str, str]],
    linked_keys: set[tuple[str, str]],
) -> bool:
    incoming = dict(incoming_keys)
    linked = dict(linked_keys)
    shared_namespaces = incoming.keys() & linked.keys()
    if any(
        incoming[namespace] != linked[namespace]
        for namespace in shared_namespaces
    ):
        return False
    return bool(shared_namespaces)


def _stable_author_compatible(state: AuthorMarkdownState, author: Author) -> bool:
    incoming_keys = _author_stable_keys(author)
    linked_keys = _state_stable_keys(state)
    if incoming_keys and linked_keys:
        return _stable_keys_match_without_conflict(incoming_keys, linked_keys)
    if linked_keys:
        return False
    return normalize_text(state.name) == normalize_text(author.name)


def _match_papers(
    papers: Sequence[CanonicalPaper],
    states: Sequence[PaperIdentity],
    papers_dir: Path,
    issues: list[MaterializationIssue],
) -> list[_PaperMatch]:
    uuid_index: dict[UUID, set[Path]] = defaultdict(set)
    doi_index: dict[str, set[Path]] = defaultdict(set)
    states_by_path = {state.path: state for state in states}
    for state in states:
        if state.paper_id is not None:
            uuid_index[state.paper_id].add(state.path)
        for doi in state.dois:
            doi_index[doi].add(state.path)

    conflicted_paths: set[Path] = set()
    for label, index in (("DOI", doi_index), ("UUID", uuid_index)):
        for identifier, paths in index.items():
            if len(paths) > 1:
                conflicted_paths.update(paths)
                issues.append(MaterializationIssue(
                    papers_dir, f"duplicate durable {label} {identifier}: "
                    + ", ".join(str(path) for path in sorted(paths)),
                ))

    incoming_dois: dict[str, int] = defaultdict(int)
    incoming_uuids: dict[UUID, int] = defaultdict(int)
    for paper in papers:
        doi = normalize_doi(paper.external_ids.doi)
        if doi is not None:
            incoming_dois[doi] += 1
            incoming_uuids[paper.id] += 1

    matches: list[_PaperMatch] = []
    for paper in papers:
        doi = normalize_doi(paper.external_ids.doi)
        if doi is None or not re.fullmatch(r"10\.\d{4,9}/[^\s]+", doi):
            _warning(
                issues, papers_dir,
                f"skipped incoming Paper {paper.id} without valid DOI",
            )
            matches.append(_PaperMatch(None, blocked=True))
            continue
        if incoming_dois[doi] > 1 or incoming_uuids[paper.id] > 1:
            issues.append(
                MaterializationIssue(
                    papers_dir, f"ambiguous incoming DOI/UUID for Paper {paper.id}",
                )
            )
            matches.append(_PaperMatch(None, blocked=True))
            continue
        candidate_paths = uuid_index.get(paper.id, set()) | doi_index.get(doi, set())
        if len(candidate_paths) > 1:
            issues.append(
                MaterializationIssue(
                    papers_dir, f"ambiguous identity for incoming Paper {paper.id}: "
                    + ", ".join(str(path) for path in sorted(candidate_paths)),
                )
            )
            matches.append(_PaperMatch(None, blocked=True))
            continue
        if candidate_paths:
            state = states_by_path[next(iter(candidate_paths))]
            if state.dois != {doi}:
                issues.append(
                    MaterializationIssue(
                        state.path, "incoming DOI conflicts with durable Paper UUID",
                    )
                )
                matches.append(_PaperMatch(state, blocked=True))
            elif state.problems or state.path in conflicted_paths:
                matches.append(_PaperMatch(state, blocked=True))
            else:
                matches.append(_PaperMatch(state))
        else:
            matches.append(_PaperMatch(None))

    paths_to_indexes: dict[Path, list[int]] = defaultdict(list)
    for index, match in enumerate(matches):
        if match.state is not None:
            paths_to_indexes[match.state.path].append(index)
    for path, indexes in paths_to_indexes.items():
        if len(indexes) < 2:
            continue
        issues.append(
            MaterializationIssue(
                path,
                "multiple incoming Papers match the same durable Paper",
            )
        )
        for index in indexes:
            matches[index] = _PaperMatch(matches[index].state, blocked=True)
    return matches


def _warning(
    issues: list[MaterializationIssue],
    path: Path,
    message: str,
) -> None:
    issues.append(
        MaterializationIssue(
            path=path,
            message=message,
            severity=MaterializationIssueSeverity.WARNING,
        )
    )


def _index_author_state(
    state: AuthorMarkdownState,
    author_states: dict[Path, AuthorMarkdownState],
    openalex_index: dict[str, set[Path]],
    orcid_index: dict[str, set[Path]],
) -> None:
    author_states[state.path] = state
    if state.openalex_key is not None:
        openalex_index[state.openalex_key].add(state.path)
    if state.orcid_key is not None:
        orcid_index[state.orcid_key].add(state.path)


def _resolve_author_links(
    paper: CanonicalPaper,
    authors_dir: Path,
    author_states: dict[Path, AuthorMarkdownState],
    openalex_index: dict[str, set[Path]],
    orcid_index: dict[str, set[Path]],
    created_authors: list[Path],
    existing_authors: list[Path],
    issues: list[MaterializationIssue],
    directory: WorkspaceTextDirectory,
) -> tuple[str, ...] | None:
    stems: list[str] = []
    for ordinal, author in enumerate(paper.authors, start=1):
        candidates: set[Path] = set()
        openalex = normalize_openalex_author_id(author.openalex_id)
        orcid = normalize_orcid(author.orcid)
        if openalex is not None:
            candidates.update(openalex_index.get(openalex, set()))
        if orcid is not None:
            candidates.update(orcid_index.get(orcid, set()))
        if len(candidates) > 1:
            issues.append(
                MaterializationIssue(
                    authors_dir,
                    f"ambiguous stable Author identity for {author.name!r}",
                )
            )
            return None
        deterministic_target = authors_dir / f"{_author_stem(author, paper.id, ordinal)}.md"
        target = next(iter(candidates), deterministic_target)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            issues.append(MaterializationIssue(target, "Author target exists but is not a regular file"))
            return None
        if not target.exists():
            try:
                contents = render_author_markdown(author)
            except Exception as error:
                issues.append(MaterializationIssue(target, str(error)))
                return None
            outcome, error = _create_file(target, contents, directory)
            if outcome == "created":
                created_authors.append(target)
                if error is not None:
                    issues.append(MaterializationIssue(target, error))
            elif outcome != "existing":
                issues.append(MaterializationIssue(target, error or "write failed"))
                return None
        # Recheck the actual occupied note, including a lost create race. The scan
        # selects a candidate but cannot authorize reuse after a human edit.
        try:
            state = parse_author_state(target, read_text_exact(target))
        except (OSError, UnicodeError) as error:
            issues.append(MaterializationIssue(target, f"cannot read Author: {error}"))
            return None
        if state is not None:
            if not _stable_author_compatible(state, author):
                issues.append(MaterializationIssue(target, f"stable Author identity conflicts for {author.name!r}"))
                return None
            _index_author_state(state, author_states, openalex_index, orcid_index)
        elif target != deterministic_target:
            issues.append(MaterializationIssue(target, f"cannot safely reuse opaque Author note for {author.name!r}"))
            return None
        # Opaque human-owned notes remain reusable at their deterministic paths.
        if target not in created_authors and target not in existing_authors:
            existing_authors.append(target)
        stems.append(target.stem)
    return tuple(stems)


def materialize_papers(
    papers: Sequence[CanonicalPaper],
    output_dir: Path,
    *,
    progress_callback: ProgressCallback | None = None,
    operation_lock: WorkspaceOperationLock | None = None,
) -> MaterializationResult:
    """Serialize capture-only materialization with other Workspace operations."""
    try:
        ownership = (nullcontext(operation_lock) if operation_lock is not None
                     else workspace_operation_lock(output_dir, create=True))
        with ownership as lock, ExitStack() as stack:
            lock.verify()
            if lock.path != output_dir.absolute():
                raise ContentChangedError("Materialization lock targets another Workspace")
            directories = {}
            issues = []
            for name in ("Authors", "Papers"):
                try:
                    directories[name] = stack.enter_context(workspace_text_directory(lock, name))
                except (OSError, ContentChangedError, CompareReadError) as error:
                    issues.append(MaterializationIssue(lock.path / name, str(error)))
            return _materialize_papers(papers, lock, directories, issues, progress_callback=progress_callback)
    except (ContentChangedError, CompareReadError, OSError) as error:
        return MaterializationResult((), (), (), (), (MaterializationIssue(output_dir, str(error)),))


def _materialize_papers(
    papers: Sequence[CanonicalPaper],
    lock: WorkspaceOperationLock,
    directories: dict[str, WorkspaceTextDirectory],
    issues: list[MaterializationIssue],
    *,
    progress_callback: ProgressCallback | None = None,
) -> MaterializationResult:
    """Create new Paper and Author notes; never rewrite saved notes."""

    output_dir = lock.path
    read_activity = ActivityUpdate(
        kind=ActivityKind.WORKING,
        source="workspace",
        operation="materialize_read",
        label="Reading workspace",
    )
    _report_activity(progress_callback, read_activity)
    authors_dir = output_dir / "Authors"
    papers_dir = output_dir / "Papers"
    try:
        lock.verify()
        for directory in directories.values():
            directory.verify()
    except (OSError, ContentChangedError, CompareReadError) as error:
        return MaterializationResult((), (), (), (), tuple(issues) + (MaterializationIssue(output_dir, str(error)),))
    authors_directory_issue = "Authors" not in directories
    papers_directory_issue = "Papers" not in directories

    states, identity_index_complete = (
        _scan_papers(papers_dir, issues)
        if not papers_directory_issue
        else ([], False)
    )
    author_states, openalex_index, orcid_index = (
        _scan_authors(authors_dir)
        if not authors_directory_issue
        else ({}, defaultdict(set), defaultdict(set))
    )
    matches = _match_papers(
        papers,
        states,
        papers_dir,
        issues,
    )
    _report_activity(
        progress_callback,
        replace(
            read_activity,
            label="Completed workspace read",
            detail=f"{len(states)} papers · {len(author_states)} authors",
        ),
    )
    collision_ids = {
        state.paper_id for state in states if state.paper_id is not None
    }
    collision_ids.update(paper.id for paper in papers)

    created_papers: list[Path] = []
    existing_papers: list[Path] = []
    created_authors: list[Path] = []
    existing_authors: list[Path] = []
    handled_new_paths: set[Path] = set()
    pending_paper_writes: list[_PaperWrite] = []

    preparation_activity = ActivityUpdate(
        kind=ActivityKind.WORKING,
        source="workspace",
        operation="materialize_prepare",
        label="Preparing new papers",
        current=0,
        total=len(papers),
        unit="paper",
    )
    _report_activity(progress_callback, preparation_activity)

    for paper_index, (paper, match) in enumerate(
        zip(papers, matches, strict=True)
    ):
        completed = True
        try:
            state = match.state
            if match.blocked or not identity_index_complete:
                continue
            if state is not None:
                if state.path not in existing_papers:
                    existing_papers.append(state.path)
                continue

            if papers_directory_issue or authors_directory_issue:
                continue
            target = papers_dir / paper_filename(
                paper.metadata.title,
                paper.id,
                collision_ids,
            )
            if target in handled_new_paths:
                issues.append(
                    MaterializationIssue(
                        target,
                        "multiple incoming Papers resolve to the same new target",
                    )
                )
                continue
            handled_new_paths.add(target)
            resolved = _resolve_author_links(
                paper,
                authors_dir,
                author_states,
                openalex_index,
                orcid_index,
                created_authors,
                existing_authors,
                issues,
                directories["Authors"],
            )
            if resolved is None:
                continue
            stems = resolved
            if target.exists():
                if target.is_file():
                    issues.append(
                        MaterializationIssue(
                            target,
                            "occupied Paper path has no safe identity match",
                        )
                    )
                else:
                    if not any(issue.path == target for issue in issues):
                        issues.append(
                            MaterializationIssue(
                                target,
                                "Paper target exists but is not a regular file",
                            )
                        )
                continue
            try:
                contents = render_paper_markdown(paper, stems)
            except Exception as error:
                issues.append(MaterializationIssue(target, str(error)))
                continue
            pending_paper_writes.append(_PaperWrite(target, contents))

        except BaseException:
            completed = False
            raise
        finally:
            if completed:
                # 已隔离的 blocked/no-op/error 分支也完成了这个 incoming Paper work unit。
                _report_activity(
                    progress_callback,
                    replace(
                        preparation_activity,
                        detail=paper.metadata.title,
                        current=paper_index + 1,
                    ),
                )

    write_activity = ActivityUpdate(
        kind=ActivityKind.WORKING,
        source="workspace",
        operation="materialize_write",
        label="Writing new papers",
        current=0,
        total=len(pending_paper_writes),
        unit="file",
    )
    _report_activity(progress_callback, write_activity)

    for write_index, pending in enumerate(pending_paper_writes):
        completed = True
        try:
            outcome, error = _create_file(pending.path, pending.contents, directories["Papers"])
            if error is not None and outcome == "created":
                issues.append(MaterializationIssue(pending.path, error))
            if outcome == "created":
                created_papers.append(pending.path)
            elif outcome == "existing":
                issues.append(
                    MaterializationIssue(
                        pending.path,
                        "Paper path became occupied without a safe identity match",
                    )
                )
            else:
                issues.append(
                    MaterializationIssue(pending.path, error or "write failed")
                )

        except BaseException:
            completed = False
            raise
        finally:
            if completed:
                # write conflict 或 I/O failure 仍表示这一 pending write attempt 已处理完成。
                _report_activity(
                    progress_callback,
                    replace(
                        write_activity,
                        detail=pending.path.name,
                        current=write_index + 1,
                    ),
                )

    if not authors_directory_issue and not papers_directory_issue:
        inbox_path = output_dir / "Inbox.base"
        _outcome, error = _create_file(
            inbox_path,
            render_default_inbox_base(),
            WorkspaceTextDirectory(lock, lock.directory, lock.identity),
        )
        if error is not None:
            issues.append(MaterializationIssue(inbox_path, error))

    try:
        lock.verify()
        for directory in directories.values():
            directory.verify()
    except (OSError, ContentChangedError, CompareReadError) as error:
        issues.append(MaterializationIssue(output_dir, str(error)))
    return MaterializationResult(
        created_papers=tuple(created_papers),
        existing_papers=tuple(existing_papers),
        created_authors=tuple(dict.fromkeys(created_authors)),
        existing_authors=tuple(dict.fromkeys(existing_authors)),
        issues=tuple(issues),
    )
