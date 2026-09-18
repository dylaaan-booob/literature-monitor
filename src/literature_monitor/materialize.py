"""Incremental Obsidian Markdown materialization over durable corpus state."""

from __future__ import annotations

import os
import stat
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from uuid import UUID

import yaml

from literature_monitor.identifiers import normalize_doi
from literature_monitor.markdown_state import (
    MISSING_ABSTRACT,
    AuthorLink,
    AuthorMarkdownState,
    MergedPaperState,
    PaperMarkdownState,
    merge_paper_state,
    normalize_openalex_author_id,
    normalize_orcid,
    normalize_source_key,
    normalize_text,
    normalize_version_key,
    parse_author_state,
    parse_paper_state,
    render_abstract_section,
    rewrite_managed_body,
    serialize_document,
)
from literature_monitor.models import (
    Author,
    CanonicalPaper,
    ExternalIds,
    MetadataSource,
    PaperVersion,
)
from literature_monitor.naming import paper_filename


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


def _yaml_frontmatter(values: dict[str, object]) -> str:
    rendered = yaml.safe_dump(values, sort_keys=False, allow_unicode=True).rstrip()
    return f"---\n{rendered}\n---\n"


def _version_summary(versions: Sequence[PaperVersion]) -> str:
    if not versions:
        return "- None recorded."
    lines: list[str] = []
    for version in versions:
        details = [
            f"{version.kind.value}: {version.source} {version.identifier}",
            f"date={version.date.isoformat() if version.date else 'unknown'}",
        ]
        if version.url is not None:
            details.append(f"url={version.url}")
        lines.append(f"- {'; '.join(details)}")
    return "\n".join(lines)


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
    return (
        f"{_yaml_frontmatter(frontmatter)}\n"
        f"# {paper.metadata.title}\n\n"
        f"{render_abstract_section(paper.metadata.abstract)}"
        f"## Versions\n\n{_version_summary(paper.versions)}\n\n"
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
        with path.open("x", encoding="utf-8", newline="") as handle:
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


def _atomic_replace(path: Path, contents: str) -> str | None:
    temporary: Path | None = None
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except OSError as error:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return str(error)
    return None


def _read_text_exact(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


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
            contents = _read_text_exact(path)
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
            state = parse_author_state(path, _read_text_exact(path))
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


def _normalized_external_ids(external_ids: ExternalIds) -> set[tuple[str, str]]:
    identities: set[tuple[str, str]] = set()
    for namespace, identifier in external_ids.model_dump(exclude_none=True).items():
        normalized_namespace = namespace.strip().casefold()
        normalized_identifier = identifier.strip()
        if normalized_namespace == "doi":
            normalized_identifier = normalize_doi(normalized_identifier) or ""
        if normalized_namespace and normalized_identifier:
            identities.add((normalized_namespace, normalized_identifier))
    return identities


def _incoming_evidence(
    paper: CanonicalPaper,
) -> tuple[
    set[tuple[str, str]],
    set[tuple[str, str]],
    set[tuple[str, str]],
]:
    return (
        _normalized_external_ids(paper.external_ids),
        {
            normalize_version_key(version.source, version.identifier)
            for version in paper.versions
        },
        {
            normalize_source_key(source.provider, source.record_id)
            for source in paper.sources
        },
    )


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
    *,
    for_title_fallback: bool,
) -> bool:
    incoming_keys = _author_stable_keys(author)
    linked_state = author_states.get(link.path)
    linked_keys = _state_stable_keys(linked_state)
    if incoming_keys or linked_keys:
        if incoming_keys and linked_keys:
            return _stable_keys_match_without_conflict(incoming_keys, linked_keys)
        if linked_keys:
            return False
        if for_title_fallback:
            return False
    existing_name = linked_state.name if linked_state is not None else link.alias
    return normalize_text(existing_name) == normalize_text(author.name)


def _authors_compatible(
    state: PaperMarkdownState,
    paper: CanonicalPaper,
    author_states: dict[Path, AuthorMarkdownState],
) -> bool:
    if len(state.author_links) != len(paper.authors):
        return False
    return all(
        _link_compatible(
            link,
            author,
            author_states,
            for_title_fallback=True,
        )
        for link, author in zip(state.author_links, paper.authors, strict=True)
    )


def _external_evidence_compatible(
    state: PaperMarkdownState,
    paper: CanonicalPaper,
) -> bool:
    existing_dois = {
        identifier
        for namespace, identifier in state.identity_external_ids
        if namespace == "doi"
    }
    incoming_dois = {
        identifier
        for namespace, identifier in _normalized_external_ids(paper.external_ids)
        if namespace == "doi"
    }
    return not existing_dois or not incoming_dois or existing_dois == incoming_dois


def _match_papers(
    papers: Sequence[CanonicalPaper],
    states: Sequence[PaperMarkdownState],
    author_states: dict[Path, AuthorMarkdownState],
    papers_dir: Path,
    issues: list[MaterializationIssue],
) -> list[_PaperMatch]:
    uuid_index: dict[UUID, set[Path]] = defaultdict(set)
    external_index: dict[tuple[str, str], set[Path]] = defaultdict(set)
    version_index: dict[tuple[str, str], set[Path]] = defaultdict(set)
    source_index: dict[tuple[str, str], set[Path]] = defaultdict(set)
    states_by_path = {state.path: state for state in states}
    for state in states:
        if state.paper_id is not None:
            uuid_index[state.paper_id].add(state.path)
        for key in state.identity_external_ids:
            external_index[key].add(state.path)
        for key in state.identity_version_keys:
            version_index[key].add(state.path)
        for key in state.identity_source_keys:
            source_index[key].add(state.path)

    matches: list[_PaperMatch] = []
    for paper in papers:
        candidate_paths: set[Path] = set(uuid_index.get(paper.id, set()))
        external_ids, version_keys, source_keys = _incoming_evidence(paper)
        for key in external_ids:
            candidate_paths.update(external_index.get(key, set()))
        for key in version_keys:
            candidate_paths.update(version_index.get(key, set()))
        for key in source_keys:
            candidate_paths.update(source_index.get(key, set()))

        if len(candidate_paths) > 1:
            issues.append(
                MaterializationIssue(
                    papers_dir,
                    f"ambiguous identity for incoming Paper {paper.id}: "
                    + ", ".join(str(path) for path in sorted(candidate_paths)),
                )
            )
            matches.append(_PaperMatch(None, blocked=True))
            continue
        if len(candidate_paths) == 1:
            matches.append(_PaperMatch(states_by_path[next(iter(candidate_paths))]))
            continue

        title_candidates = [
            state
            for state in states
            if state.updateable
            and state.title is not None
            and normalize_text(state.title) == normalize_text(paper.metadata.title)
            and _external_evidence_compatible(state, paper)
            and _authors_compatible(state, paper, author_states)
        ]
        if len(title_candidates) > 1:
            issues.append(
                MaterializationIssue(
                    papers_dir,
                    f"ambiguous title/author match for incoming Paper {paper.id}",
                )
            )
            matches.append(_PaperMatch(None, blocked=True))
        elif title_candidates:
            matches.append(_PaperMatch(title_candidates[0]))
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
    error = _atomic_replace(state.path, contents)
    if error is not None:
        issues.append(MaterializationIssue(state.path, error))
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
                for_title_fallback=False,
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
                    for_title_fallback=False,
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
                for_title_fallback=False,
            ):
                issues.append(
                    MaterializationIssue(
                        target,
                        f"occupied Author note is incompatible with {author.name!r}",
                    )
                )
                return None
            enriched = _enrich_author_state(state, author, issues)
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
            outcome, error = _create_file(target, contents)
            if outcome == "created":
                created_authors.append(target)
            elif outcome == "existing":
                if target not in existing_authors:
                    existing_authors.append(target)
            else:
                issues.append(MaterializationIssue(target, error or "write failed"))
                return None
            try:
                parsed = parse_author_state(target, _read_text_exact(target))
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
            "preferred_version": (
                merged.preferred_version.model_dump(mode="json")
                if merged.preferred_version is not None
                else None
            ),
            "zotero_key": state.zotero_key,
            "external_ids": external_values,
            "versions": [
                version.model_dump(mode="json") for version in merged.versions
            ],
            "sources": [source.model_dump(mode="json") for source in merged.sources],
        }
    )
    body = rewrite_managed_body(
        state.body,
        title=merged.title,
        abstract=merged.abstract,
        versions_summary=_version_summary(merged.versions),
        sources_summary=_source_summary(merged.sources),
    )
    return serialize_document(frontmatter, body)


def materialize_papers(
    papers: Sequence[CanonicalPaper],
    output_dir: Path,
) -> MaterializationResult:
    """Create or incrementally update Paper and Author notes."""

    authors_dir = output_dir / "Authors"
    papers_dir = output_dir / "Papers"
    issues: list[MaterializationIssue] = []
    authors_directory_issue = _ensure_directory(authors_dir)
    papers_directory_issue = _ensure_directory(papers_dir)
    if authors_directory_issue is not None:
        issues.append(authors_directory_issue)
    if papers_directory_issue is not None:
        issues.append(papers_directory_issue)

    states = (
        _scan_papers(papers_dir, authors_dir, issues)
        if papers_directory_issue is None
        else []
    )
    author_states, openalex_index, orcid_index = (
        _scan_authors(authors_dir)
        if authors_directory_issue is None
        else ({}, defaultdict(set), defaultdict(set))
    )
    matches = _match_papers(
        papers,
        states,
        author_states,
        papers_dir,
        issues,
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

    for paper, match in zip(papers, matches, strict=True):
        state = match.state
        if state is not None and state.path not in existing_papers:
            existing_papers.append(state.path)
        if match.blocked:
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
            if merged.incoming_is_preferred:
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
                )
                if resolved is None:
                    continue
                author_links, _stems = resolved
                durable_author_links = tuple(
                    f"[[Authors/{link.stem}|{link.alias}]]"
                    for link in state.author_links
                )
                if durable_author_links != author_links:
                    _warning(
                        issues,
                        state.path,
                        "authors changed with the effective preferred version",
                    )
            else:
                author_links = tuple(
                    f"[[Authors/{link.stem}|{link.alias}]]"
                    for link in state.author_links
                )
                authors_compatible = len(state.author_links) == len(paper.authors) and all(
                    _link_compatible(
                        link,
                        author,
                        author_states,
                        for_title_fallback=False,
                    )
                    for link, author in zip(
                        state.author_links, paper.authors, strict=True
                    )
                )
                if not authors_compatible:
                    _warning(
                        issues,
                        state.path,
                        "ignored authors from a non-effective incoming version",
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

        if papers_directory_issue is not None or authors_directory_issue is not None:
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

    for pending in pending_paper_writes:
        if pending.original is not None:
            error = _atomic_replace(pending.path, pending.contents)
            if error is None:
                updated_papers.append(pending.path)
            else:
                issues.append(MaterializationIssue(pending.path, error))
            continue
        outcome, error = _create_file(pending.path, pending.contents)
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

    return MaterializationResult(
        created_papers=tuple(created_papers),
        existing_papers=tuple(existing_papers),
        updated_papers=tuple(updated_papers),
        created_authors=tuple(dict.fromkeys(created_authors)),
        existing_authors=tuple(dict.fromkeys(existing_authors)),
        issues=tuple(issues),
    )
