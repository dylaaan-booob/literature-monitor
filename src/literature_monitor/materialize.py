"""Incremental Obsidian Markdown materialization over durable corpus state."""

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
    AuthorLink,
    AuthorMarkdownState,
    MergedPaperState,
    PaperMarkdownState,
    merge_paper_state,
    normalize_openalex_author_id,
    normalize_orcid,
    normalize_text,
    parse_author_state,
    parse_paper_state,
    render_abstract_section,
    rewrite_managed_body,
    serialize_document,
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
    updated_papers: tuple[Path, ...]
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
    state: PaperMarkdownState | None
    blocked: bool = False


@dataclass(frozen=True)
class _PaperWrite:
    path: Path
    contents: str
    original: str | None


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
    authors_dir: Path,
    issues: list[MaterializationIssue],
) -> list[PaperMarkdownState]:
    states: list[PaperMarkdownState] = []
    if not papers_dir.is_dir():
        return states
    for path in sorted(papers_dir.glob("*.md")):
        if not path.is_file():
            issues.append(
                MaterializationIssue(path, "Paper target is not a regular file")
            )
            continue
        try:
            contents = read_text_exact(path)
        except (OSError, UnicodeError) as error:
            issues.append(MaterializationIssue(path, f"cannot read Paper: {error}"))
            continue
        state = parse_paper_state(path, contents, authors_dir)
        if state is None:
            continue
        states.append(state)
        for problem in state.problems:
            issues.append(MaterializationIssue(path, problem))
    return states


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


def _link_compatible(
    link: AuthorLink,
    author: Author,
    author_states: dict[Path, AuthorMarkdownState],
) -> bool:
    incoming_keys = _author_stable_keys(author)
    linked_state = author_states.get(link.path)
    linked_keys = _state_stable_keys(linked_state)
    if incoming_keys or linked_keys:
        if incoming_keys and linked_keys:
            return _stable_keys_match_without_conflict(incoming_keys, linked_keys)
        if linked_keys:
            return False
    existing_name = linked_state.name if linked_state is not None else link.alias
    return normalize_text(existing_name) == normalize_text(author.name)


def _match_papers(
    papers: Sequence[CanonicalPaper],
    states: Sequence[PaperMarkdownState],
    papers_dir: Path,
    issues: list[MaterializationIssue],
) -> list[_PaperMatch]:
    uuid_index: dict[UUID, set[Path]] = defaultdict(set)
    doi_index: dict[str, set[Path]] = defaultdict(set)
    states_by_path = {state.path: state for state in states}
    for state in states:
        if state.paper_id is not None:
            uuid_index[state.paper_id].add(state.path)
        for namespace, identifier in state.identity_external_ids:
            if namespace == "doi":
                doi_index[identifier].add(state.path)

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
            durable_dois = {
                identifier for namespace, identifier in state.identity_external_ids
                if namespace == "doi"
            }
            if durable_dois != {doi}:
                issues.append(
                    MaterializationIssue(
                        state.path, "incoming DOI conflicts with durable Paper UUID",
                    )
                )
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


def _enrich_author_state(
    state: AuthorMarkdownState,
    author: Author,
    issues: list[MaterializationIssue],
    directory: WorkspaceTextDirectory,
) -> AuthorMarkdownState | None:
    frontmatter = dict(state.frontmatter)
    changed = False
    incoming_openalex = normalize_openalex_author_id(author.openalex_id)
    incoming_orcid = normalize_orcid(author.orcid)
    if (
        state.openalex_key is not None
        and incoming_openalex is not None
        and state.openalex_key != incoming_openalex
    ):
        _warning(issues, state.path, "Author OpenAlex ID conflicts with durable value")
    if (
        state.orcid_key is not None
        and incoming_orcid is not None
        and state.orcid_key != incoming_orcid
    ):
        _warning(issues, state.path, "Author ORCID conflicts with durable value")
    if frontmatter.get("openalex_id") is None and author.openalex_id is not None:
        frontmatter["openalex_id"] = author.openalex_id
        changed = True
    if frontmatter.get("orcid") is None and author.orcid is not None:
        frontmatter["orcid"] = author.orcid
        changed = True
    if not changed:
        return state
    contents = serialize_document(frontmatter, state.body)
    try:
        directory.replace(state.path.name, contents, expected_contents=state.original)
    except TextWriteCommittedError as error:
        issues.append(MaterializationIssue(state.path, str(error)))
    except (OSError, ContentChangedError, CompareReadError) as error:
        issues.append(MaterializationIssue(state.path, str(error)))
        return None
    return parse_author_state(state.path, contents)


def _resolve_author_links(
    paper: CanonicalPaper,
    paper_id: UUID,
    authors_dir: Path,
    matched_state: PaperMarkdownState | None,
    author_states: dict[Path, AuthorMarkdownState],
    openalex_index: dict[str, set[Path]],
    orcid_index: dict[str, set[Path]],
    created_authors: list[Path],
    existing_authors: list[Path],
    issues: list[MaterializationIssue],
    directory: WorkspaceTextDirectory,
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    links: list[str] = []
    stems: list[str] = []
    used_old_targets: set[Path] = set()
    for ordinal, author in enumerate(paper.authors, start=1):
        candidates: set[Path] = set()
        openalex = normalize_openalex_author_id(author.openalex_id)
        orcid = normalize_orcid(author.orcid)
        incoming_has_stable_id = openalex is not None or orcid is not None
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
        target = next(iter(candidates), None)
        matched_link_target = False
        if target is not None:
            candidate_state = author_states.get(target)
            if candidate_state is None or not _link_compatible(
                AuthorLink(target.stem, candidate_state.name, target),
                author,
                author_states,
            ):
                issues.append(
                    MaterializationIssue(
                        target,
                        f"stable Author identity conflicts for {author.name!r}",
                    )
                )
                return None
        if target is None and matched_state is not None:
            compatible_links = [
                old_link
                for old_link in matched_state.author_links
                if old_link.path not in used_old_targets
                and _link_compatible(
                    old_link,
                    author,
                    author_states,
                )
            ]
            if len(compatible_links) > 1:
                issues.append(
                    MaterializationIssue(
                        matched_state.path,
                        f"ambiguous existing Author links for {author.name!r}",
                    )
                )
                return None
            if compatible_links:
                target = compatible_links[0].path
                matched_link_target = True
        if target is None:
            target = authors_dir / f"{_author_stem(author, paper_id, ordinal)}.md"

        if target.exists() and not target.is_file():
            issues.append(
                MaterializationIssue(
                    target,
                    "Author target exists but is not a regular file",
                )
            )
            return None
        state = author_states.get(target)
        if state is not None:
            if not _link_compatible(
                AuthorLink(target.stem, state.name, target),
                author,
                author_states,
            ):
                issues.append(
                    MaterializationIssue(
                        target,
                        f"occupied Author note is incompatible with {author.name!r}",
                    )
                )
                return None
            enriched = _enrich_author_state(state, author, issues, directory)
            if enriched is None:
                return None
            if enriched is not state:
                _index_author_state(
                    enriched, author_states, openalex_index, orcid_index
                )
        elif target.is_file():
            if (
                matched_state is not None
                and not incoming_has_stable_id
                and not matched_link_target
            ):
                issues.append(
                    MaterializationIssue(
                        target,
                        f"cannot safely reuse occupied name-only Author note for {author.name!r}",
                    )
                )
                return None
            if target not in existing_authors:
                existing_authors.append(target)
        else:
            try:
                contents = render_author_markdown(author)
            except Exception as error:
                issues.append(MaterializationIssue(target, str(error)))
                return None
            outcome, error = _create_file(target, contents, directory)
            if error is not None and outcome == "created":
                issues.append(MaterializationIssue(target, error))
            if outcome == "created":
                created_authors.append(target)
            elif outcome == "existing":
                if target not in existing_authors:
                    existing_authors.append(target)
            else:
                issues.append(MaterializationIssue(target, error or "write failed"))
                return None
            try:
                parsed = parse_author_state(target, read_text_exact(target))
            except (OSError, UnicodeError):
                parsed = None
            if parsed is not None:
                _index_author_state(
                    parsed, author_states, openalex_index, orcid_index
                )
        if target not in created_authors and target not in existing_authors:
            existing_authors.append(target)
        if matched_link_target:
            used_old_targets.add(target)
        stems.append(target.stem)
        links.append(f"[[Authors/{target.stem}|{author.name}]]")
    return tuple(links), tuple(stems)


def _render_updated_paper(
    state: PaperMarkdownState,
    merged: MergedPaperState,
    author_links: Sequence[str],
) -> str:
    assert state.frontmatter is not None
    assert state.body is not None
    assert state.paper_id is not None
    assert state.status is not None
    assert state.discovered_at is not None
    frontmatter = dict(state.frontmatter)
    external_values = merged.external_ids.model_dump(mode="json", exclude_none=False)
    frontmatter.update(
        {
            "type": "paper",
            "id": str(state.paper_id),
            "title": merged.title,
            "authors": list(author_links),
            "journal": merged.journal,
            "publication_date": (
                merged.publication_date.isoformat()
                if merged.publication_date is not None
                else None
            ),
            "doi": external_values.get("doi"),
            "openalex_id": external_values.get("openalex"),
            "arxiv_id": external_values.get("arxiv"),
            "author_keywords": list(merged.author_keywords),
            "status": state.status.value,
            "discovered_at": state.discovered_at.isoformat().replace(
                "+00:00", "Z"
            ),
            "external_ids": external_values,
            "sources": [source.model_dump(mode="json") for source in merged.sources],
        }
    )
    if merged.journal_issns_update is not None:
        frontmatter["journal_issns"] = list(merged.journal_issns_update)
    body = rewrite_managed_body(
        state.body,
        title=merged.title,
        abstract=merged.abstract,
        sources_summary=_source_summary(merged.sources),
    )
    return serialize_document(frontmatter, body)


def materialize_papers(
    papers: Sequence[CanonicalPaper],
    output_dir: Path,
    *,
    progress_callback: ProgressCallback | None = None,
    operation_lock: WorkspaceOperationLock | None = None,
) -> MaterializationResult:
    """Serialize workspace materialization with durable Paper attempt writes."""
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
        return MaterializationResult((), (), (), (), (), (MaterializationIssue(output_dir, str(error)),))


def _materialize_papers(
    papers: Sequence[CanonicalPaper],
    lock: WorkspaceOperationLock,
    directories: dict[str, WorkspaceTextDirectory],
    issues: list[MaterializationIssue],
    *,
    progress_callback: ProgressCallback | None = None,
) -> MaterializationResult:
    """Create or incrementally update Paper and Author notes."""

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
        return MaterializationResult((), (), (), (), (), tuple(issues) + (MaterializationIssue(output_dir, str(error)),))
    authors_directory_issue = "Authors" not in directories
    papers_directory_issue = "Papers" not in directories

    states = (
        _scan_papers(papers_dir, authors_dir, issues)
        if not papers_directory_issue
        else []
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
    updated_papers: list[Path] = []
    created_authors: list[Path] = []
    existing_authors: list[Path] = []
    handled_new_paths: set[Path] = set()
    pending_paper_writes: list[_PaperWrite] = []

    preparation_activity = ActivityUpdate(
        kind=ActivityKind.WORKING,
        source="workspace",
        operation="materialize_prepare",
        label="Preparing workspace updates",
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
            if state is not None and state.path not in existing_papers:
                existing_papers.append(state.path)
            if match.blocked or authors_directory_issue:
                continue
            if state is not None:
                if not state.updateable:
                    issues.append(
                        MaterializationIssue(
                            state.path,
                            "matched durable Paper is not safe to update",
                        )
                    )
                    continue
                merged = merge_paper_state(state, paper)
                for message in merged.warnings:
                    _warning(issues, state.path, message)
                resolved = _resolve_author_links(
                    paper,
                    state.paper_id or paper.id,
                    authors_dir,
                    state,
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
                author_links, _stems = resolved
                durable_author_links = tuple(
                    f"[[Authors/{link.stem}|{link.alias}]]" for link in state.author_links
                )
                if durable_author_links != author_links:
                    _warning(
                        issues, state.path,
                        "authors changed with incoming canonical metadata",
                    )
                try:
                    contents = _render_updated_paper(state, merged, author_links)
                except Exception as error:
                    issues.append(MaterializationIssue(state.path, str(error)))
                    continue
                if contents == state.original:
                    continue
                pending_paper_writes.append(
                    _PaperWrite(state.path, contents, state.original)
                )
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
                paper.id,
                authors_dir,
                None,
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
            _links, stems = resolved
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
            pending_paper_writes.append(_PaperWrite(target, contents, None))

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
        label="Writing workspace",
        current=0,
        total=len(pending_paper_writes),
        unit="file",
    )
    _report_activity(progress_callback, write_activity)

    for write_index, pending in enumerate(pending_paper_writes):
        completed = True
        try:
            if pending.original is not None:
                try:
                    directories["Papers"].replace(
                        pending.path.name, pending.contents, expected_contents=pending.original,
                    )
                except CompareReadError as error:
                    issues.append(
                        MaterializationIssue(
                            pending.path,
                            f"cannot verify Paper before update: {error}",
                        )
                    )
                except ContentChangedError:
                    issues.append(
                        MaterializationIssue(
                            pending.path,
                            "Paper changed on disk after it was scanned; update safely aborted",
                        )
                    )
                except TextWriteCommittedError as error:
                    updated_papers.append(pending.path)
                    issues.append(MaterializationIssue(pending.path, str(error)))
                except OSError as error:
                    issues.append(MaterializationIssue(pending.path, str(error)))
                else:
                    updated_papers.append(pending.path)
                continue
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
        updated_papers=tuple(updated_papers),
        created_authors=tuple(dict.fromkeys(created_authors)),
        existing_authors=tuple(dict.fromkeys(existing_authors)),
        issues=tuple(issues),
    )
