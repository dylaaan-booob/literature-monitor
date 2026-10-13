"""Explicit, compare-and-replace Paper enrichment writes (SPEC §§48, 50–51)."""
from __future__ import annotations

import os
import json
from contextlib import ExitStack
from dataclasses import dataclass

import yaml
from pathlib import Path
from uuid import UUID

from literature_monitor.application.decisions import _locate_paper, _read_paper_candidate
from literature_monitor.markdown_state import (
    analyze_body, parse_author_state, parse_paper_identity, parse_paper_state, render_abstract_section,
)
from literature_monitor.materialize import _author_stem, _stable_author_compatible, render_author_markdown
from literature_monitor.models import Author, WorkflowStatus, parse_publication_date
from literature_monitor.safe_write import (
    CompareReadError, ContentChangedError, TextWriteCommittedError, replace_regular_text_at_identity,
    workspace_operation_lock, workspace_text_directory,
)


@dataclass(frozen=True)
class EnrichmentTarget:
    workspace: Path
    path: Path
    paper_id: UUID
    doi: str
    original: str
    workspace_identity: tuple[int, int]
    directory_identity: tuple[int, int]
    file_identity: tuple[int, int]
    missing_abstract: bool
    missing_date: bool
    missing_authors: bool


def read_enrichment_target(workspace: Path, paper_id: UUID) -> EnrichmentTarget:
    with workspace_operation_lock(workspace) as lock:
        directory = os.open('Papers', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=lock.directory)
        try:
            reads = {}
            path, failure = _locate_paper(lock.path, paper_id, WorkflowStatus.CANDIDATE,
                                         require_safe_candidates=True, directory=directory, paper_reads=reads)
            if failure:
                raise ValueError(failure.message)
            read = _read_paper_candidate(path, directory)
        finally:
            os.close(directory)
        state = parse_paper_state(path, read.contents, lock.path / 'Authors')
        identity = parse_paper_identity(path, read.contents)
        if state is None or not state.updateable or state.paper_id != paper_id:
            raise ValueError('Paper is invalid or its identity changed')
        if identity is None or identity.problems or len(identity.dois) != 1:
            raise ValueError('Paper DOI is missing or conflicting')
        missing_abstract = not state.abstract or not state.abstract.strip()
        missing_date = state.publication_date is None
        missing_authors = not state.author_links
        if not (missing_abstract or missing_date or missing_authors):
            raise ValueError('Paper already has an Abstract, publication date and Authors')
        lock.verify()
        return EnrichmentTarget(lock.path, path, paper_id, next(iter(identity.dois)), read.contents,
                              lock.identity, read.directory_identity, read.file_identity,
                              missing_abstract, missing_date, missing_authors)


def fill_abstract(original: str, abstract: str) -> str:
    if not isinstance(abstract, str) or not abstract.strip():
        raise ValueError('Abstract is empty')
    # Preserve the original YAML spelling and every byte outside the section.
    lines = original.splitlines(keepends=True)
    closing = next(i for i in range(1, len(lines)) if lines[i].rstrip('\r\n') in {'---', '...'})
    prefix, body = ''.join(lines[:closing + 1]), ''.join(lines[closing + 1:])
    layout = analyze_body(body)
    if layout.abstract and layout.abstract.strip():
        raise ValueError('Paper already has an Abstract')
    body_lines = body.splitlines(keepends=True)
    rendered = render_abstract_section(abstract)
    # A translator result must remain one unambiguous managed Abstract section.
    checked = analyze_body('# Paper\n\n' + rendered + '## Sources\n\n## Notes\n')
    if checked.abstract != abstract.strip("\r\n"):
        raise ValueError('Abstract has an ambiguous Markdown boundary')
    if 'abstract' in layout.sections:
        start, end = layout.sections['abstract']
    else:
        start = layout.sections.get('sources', (layout.notes_index if layout.notes_index is not None
                                                else len(body_lines),))[0]
        end = start
        if start and body_lines[start - 1].strip():
            rendered = '\n' + rendered
    return prefix + ''.join(body_lines[:start]) + rendered + ''.join(body_lines[end:])


def fill_publication_date(original: str, value: str) -> str:
    """Replace one null YAML scalar, without dumping other frontmatter."""
    if not isinstance(value, str) or parse_publication_date(value) is None:
        raise ValueError('Publication date must be a valid ISO string')
    replacement = json.dumps(value)
    lines = original.splitlines(keepends=True)
    closing = next(i for i in range(1, len(lines)) if lines[i].rstrip('\r\n') in {'---', '...'})
    payload = ''.join(lines[1:closing])
    document = yaml.compose(payload)
    if not isinstance(document, yaml.MappingNode) or document.flow_style:
        raise ValueError('Cannot safely locate publication_date in YAML')
    entries = [(key, node) for key, node in document.value if key.value == 'publication_date']
    if not entries:
        newline = '\r\n' if lines[0].endswith('\r\n') else '\n'
        return ''.join(lines[:closing]) + f'publication_date: {replacement}{newline}' + ''.join(lines[closing:])
    if len(entries) != 1:
        raise ValueError('Duplicate publication_date')
    key, node = entries[0]
    start, end = node.start_mark.index, node.end_mark.index
    if (not isinstance(node, yaml.ScalarNode) or node.tag != 'tag:yaml.org,2002:null'
            or node.style in {'|', '>'} or start < key.end_mark.index
            or any(isinstance(token, (yaml.AnchorToken, yaml.AliasToken))
                   and start <= token.start_mark.index < end for token in yaml.scan(payload))):
        raise ValueError('Cannot safely replace publication_date YAML scalar')
    if start == end and start and payload[start - 1] == ':':
        replacement = ' ' + replacement
    return lines[0] + payload[:start] + replacement + payload[end:] + ''.join(lines[closing:])


def fill_authors(original: str, links: list[str]) -> str:
    """Splice one empty sequence; JSON strings are also safe YAML scalars."""
    lines = original.splitlines(keepends=True)
    closing = next(i for i in range(1, len(lines)) if lines[i].rstrip('\r\n') in {'---', '...'})
    payload = ''.join(lines[1:closing])
    document = yaml.compose(payload)
    if not isinstance(document, yaml.MappingNode) or document.flow_style:
        raise ValueError('Cannot safely locate authors in YAML')
    entries = [(key, node) for key, node in document.value if key.value == 'authors']
    if len(entries) != 1:
        raise ValueError('Cannot safely locate one authors sequence')
    key, node = entries[0]
    start, end = node.start_mark.index, node.end_mark.index
    if (not isinstance(node, yaml.SequenceNode) or node.value or start < key.end_mark.index
            or any(isinstance(token, (yaml.AnchorToken, yaml.AliasToken))
                   and start <= token.start_mark.index < end for token in yaml.scan(payload))):
        raise ValueError('Cannot safely replace authors YAML sequence')
    return lines[0] + payload[:start] + json.dumps(links, ensure_ascii=False) + payload[end:] + ''.join(lines[closing:])


def save_enrichment(target: EnrichmentTarget, abstract: str | None = None,
                    publication_date: str | None = None, authors: list[str] | None = None) -> None:
    current = read_enrichment_target(target.workspace, target.paper_id)
    if current != target:
        raise ContentChangedError('Paper or Workspace changed; enrichment was not saved')
    if abstract is None and publication_date is None and authors is None:
        raise ValueError('No enrichment candidate')
    contents = target.original
    if abstract is not None:
        if not target.missing_abstract:
            raise ValueError('Paper already has an Abstract')
        contents = fill_abstract(contents, abstract)
    if publication_date is not None:
        if not target.missing_date:
            raise ValueError('Paper already has a publication date')
        contents = fill_publication_date(contents, publication_date)
    author_notes = []
    if authors is not None:
        if not target.missing_authors:
            raise ValueError('Paper already has Authors')
        if not authors or any(not isinstance(name, str) or not name.strip() for name in authors):
            raise ValueError('Authors must contain nonempty names')
        for ordinal, name in enumerate(authors, start=1):
            author = Author(name=name)
            path = target.workspace / 'Authors' / f'{_author_stem(author, target.paper_id, ordinal)}.md'
            author_notes.append((path, author))
        contents = fill_authors(contents, [f'[[Authors/{path.stem}|{author.name}]]' for path, author in author_notes])
    state = parse_paper_state(target.path, contents, target.workspace / 'Authors')
    if state is None or not state.updateable:
        raise ValueError('Enrichment would produce an invalid Paper')
    with workspace_operation_lock(target.workspace) as lock, ExitStack() as stack:
        if lock.identity != target.workspace_identity:
            raise ContentChangedError('Workspace identity changed')
        created, reads = [], []
        directory = None
        try:
            if author_notes:
                # Recheck the Paper under this lock before creating any Author.
                papers = os.open('Papers', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=lock.directory)
                try:
                    read = _read_paper_candidate(target.path, papers)
                finally:
                    os.close(papers)
                if (read.contents, read.directory_identity, read.file_identity) != (
                        target.original, target.directory_identity, target.file_identity):
                    raise ContentChangedError('Paper changed before Author creation')
                directory = stack.enter_context(workspace_text_directory(lock, 'Authors'))
                for path, author in author_notes:
                    try:
                        directory.create(path.name, render_author_markdown(author))
                        created.append(path)
                    except FileExistsError:
                        pass
                    except TextWriteCommittedError as exc:
                        created.append(path)
                        raise OSError(f'Author created but filesystem sync failed: {path}: {exc}') from exc
                    read = _read_paper_candidate(path, directory.directory)
                    author_state = parse_author_state(path, read.contents)
                    if (author_state is None or not _stable_author_compatible(author_state, author)
                            or author_state.frontmatter.get('openalex_id') is not None
                            or author_state.frontmatter.get('orcid') is not None):
                        raise ValueError(f'Cannot safely reuse Author: {path}')
                    reads.append((path, read))

            def verify_authors():
                if directory is not None:
                    directory.verify()
                    for path, previous in reads:
                        if _read_paper_candidate(path, directory.directory) != previous:
                            raise ContentChangedError(f'Author changed before Paper save: {path}')

            replace_regular_text_at_identity(
                target.path, contents, expected_contents=target.original,
                expected_directory_identity=target.directory_identity,
                expected_file_identity=target.file_identity,
                expected_workspace_identity=target.workspace_identity, operation_lock=lock,
                validate_before_replace=verify_authors,
            )
        except TextWriteCommittedError:
            # Only the Paper commit reaches Web's Completed-with-warning path.
            raise
        except (ValueError, OSError, ContentChangedError, CompareReadError) as exc:
            retained = ', '.join(str(path) for path in created) or 'none'
            raise ValueError(f'Paper was not saved: {exc}; created Author files retained: {retained}') from exc
