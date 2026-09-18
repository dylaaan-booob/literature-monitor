"""Parsing and merging helpers for durable Markdown workflow state."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from pydantic import ValidationError

from literature_monitor.identifiers import normalize_doi
from literature_monitor.models import (
    CanonicalPaper,
    ExternalIds,
    MetadataSource,
    PaperVersion,
    VersionKind,
    VersionRef,
    WorkflowStatus,
)


MISSING_ABSTRACT = "Abstract unavailable from current MVP data sources."
_ABSTRACT_END_MARKER = "<!-- literature-monitor:abstract-end -->"
_AUTHOR_LINK = re.compile(r"\[\[Authors/([^|\]]+)(?:\|([^\]]*))?\]\]")
_ORCID_PATTERN = re.compile(
    r"(?:https?://orcid\.org/)?(\d{4}-\d{4}-\d{4}-\d{3}[\dX])/?",
    re.IGNORECASE,
)
_HEADING = re.compile(r" {0,3}(#{1,6})[ \t]+(.+?)\s*$")
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")
_MANAGED_SECTIONS = ("abstract", "versions", "sources")
_VERSION_PRIORITY = {
    VersionKind.JOURNAL_FINAL: 4,
    VersionKind.JOURNAL_ONLINE: 3,
    VersionKind.ACCEPTED_MANUSCRIPT: 2,
    VersionKind.PREPRINT: 1,
    VersionKind.UNKNOWN: 0,
}


@dataclass(frozen=True)
class AuthorLink:
    stem: str
    alias: str
    path: Path


@dataclass(frozen=True)
class AuthorMarkdownState:
    path: Path
    original: str
    frontmatter: dict[str, Any]
    body: str
    name: str
    openalex_key: str | None
    orcid_key: str | None


@dataclass(frozen=True)
class PaperMarkdownState:
    path: Path
    original: str
    frontmatter: dict[str, Any] | None
    body: str | None
    paper_id: UUID | None
    identity_external_ids: frozenset[tuple[str, str]]
    identity_version_keys: frozenset[tuple[str, str]]
    identity_source_keys: frozenset[tuple[str, str]]
    updateable: bool
    problems: tuple[str, ...]
    title: str | None = None
    journal: str | None = None
    publication_date: date | None = None
    abstract: str | None = None
    author_keywords: tuple[str, ...] = ()
    author_links: tuple[AuthorLink, ...] = ()
    status: WorkflowStatus | None = None
    discovered_at: datetime | None = None
    zotero_key: str | None = None
    external_ids: ExternalIds | None = None
    versions: tuple[PaperVersion, ...] = ()
    sources: tuple[MetadataSource, ...] = ()
    preferred_version: VersionRef | None = None

    @property
    def has_identity(self) -> bool:
        return bool(
            self.paper_id
            or self.identity_external_ids
            or self.identity_version_keys
            or self.identity_source_keys
        )


@dataclass(frozen=True)
class MergedPaperState:
    title: str
    journal: str
    publication_date: date | None
    abstract: str | None
    author_keywords: tuple[str, ...]
    external_ids: ExternalIds
    versions: tuple[PaperVersion, ...]
    sources: tuple[MetadataSource, ...]
    preferred_version: VersionRef | None
    incoming_is_preferred: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _Heading:
    index: int
    level: int
    title: str


@dataclass(frozen=True)
class BodyLayout:
    headings: tuple[_Heading, ...]
    h1_index: int | None
    sections: dict[str, tuple[int, int]]
    notes_index: int | None
    abstract: str | None
    abstract_end_marker: int | None


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def normalize_openalex_author_id(value: str | None) -> str | None:
    if value is None:
        return None
    token = value.strip().rstrip("/").rsplit("/", maxsplit=1)[-1]
    return token.casefold() if token else None


def normalize_orcid(value: str | None) -> str | None:
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


def normalize_version_key(source: str, identifier: str) -> tuple[str, str]:
    normalized_source = source.strip().casefold()
    normalized_identifier = identifier.strip()
    if normalized_source == "doi":
        doi = normalize_doi(normalized_identifier)
        if doi is None:
            raise ValueError("empty DOI version identifier")
        normalized_identifier = doi
    if not normalized_source or not normalized_identifier:
        raise ValueError("version source and identifier must be non-empty")
    return normalized_source, normalized_identifier


def normalize_source_key(provider: str, record_id: str) -> tuple[str, str]:
    normalized_provider = provider.strip().casefold()
    normalized_record_id = record_id.strip()
    if not normalized_provider or not normalized_record_id:
        raise ValueError("source provider and record_id must be non-empty")
    return normalized_provider, normalized_record_id


def _parse_document(contents: str) -> tuple[dict[str, Any], str]:
    lines = contents.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ValueError("missing opening YAML frontmatter delimiter")
    closing = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.rstrip("\r\n") in {"---", "..."}
        ),
        None,
    )
    if closing is None:
        raise ValueError("missing closing YAML frontmatter delimiter")
    try:
        frontmatter = yaml.safe_load("".join(lines[1:closing]))
    except yaml.YAMLError as error:
        raise ValueError(f"invalid YAML frontmatter: {error}") from error
    if not isinstance(frontmatter, dict):
        raise ValueError("YAML frontmatter must be a mapping")
    return frontmatter, "".join(lines[closing + 1 :])


def serialize_document(frontmatter: dict[str, Any], body: str) -> str:
    rendered = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=True,
    ).rstrip()
    return f"---\n{rendered}\n---\n{body}"


def _heading_title(value: str) -> str:
    return re.sub(r"[ \t]+#+[ \t]*$", "", value).strip()


def _contains_markdown_h2(value: str) -> bool:
    fence_character: str | None = None
    fence_length = 0
    for line in value.splitlines():
        fence = _FENCE.match(line)
        if fence_character is not None:
            marker = fence.group(1) if fence is not None else ""
            trailing = line[fence.end() :].strip() if fence is not None else ""
            if (
                marker.startswith(fence_character)
                and len(marker) >= fence_length
                and not trailing
            ):
                fence_character = None
                fence_length = 0
            continue
        if fence is not None:
            marker = fence.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
            continue
        heading = _HEADING.match(line)
        if heading is not None and len(heading.group(1)) == 2:
            return True
    return False


def render_abstract_section(abstract: str | None) -> str:
    abstract_text = MISSING_ABSTRACT if abstract is None else abstract
    marker = (
        f"\n{_ABSTRACT_END_MARKER}"
        if _contains_markdown_h2(abstract_text)
        else ""
    )
    return f"## Abstract\n\n{abstract_text}{marker}\n\n"


def analyze_body(body: str) -> BodyLayout:
    lines = body.splitlines(keepends=True)
    headings: list[_Heading] = []
    abstract_end_markers: list[int] = []
    fence_character: str | None = None
    fence_length = 0
    for index, line in enumerate(lines):
        fence = _FENCE.match(line)
        if fence_character is not None:
            marker = fence.group(1) if fence is not None else ""
            trailing = line[fence.end() :].strip() if fence is not None else ""
            if (
                marker.startswith(fence_character)
                and len(marker) >= fence_length
                and not trailing
            ):
                fence_character = None
                fence_length = 0
            continue
        if fence is not None:
            marker = fence.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
            continue
        if line.strip() == _ABSTRACT_END_MARKER:
            abstract_end_markers.append(index)
            continue
        heading = _HEADING.match(line.rstrip("\r\n"))
        if heading is not None:
            headings.append(
                _Heading(
                    index=index,
                    level=len(heading.group(1)),
                    title=_heading_title(heading.group(2)),
                )
            )
    if fence_character is not None:
        raise ValueError("unclosed fenced code block")
    if len(abstract_end_markers) > 1:
        raise ValueError("multiple managed Abstract end markers")

    abstract_end_marker = (
        abstract_end_markers[0] if abstract_end_markers else None
    )
    marked_abstract_heading: _Heading | None = None
    if abstract_end_marker is not None:
        marked_candidates = [
            heading
            for heading in headings
            if heading.level == 2
            and normalize_text(heading.title) == "abstract"
            and heading.index < abstract_end_marker
        ]
        if not marked_candidates:
            raise ValueError("managed Abstract end marker has no Abstract heading")
        marked_abstract_heading = marked_candidates[0]
        headings = [
            heading
            for heading in headings
            if not (
                marked_abstract_heading.index < heading.index < abstract_end_marker
            )
        ]

    h1_indexes = [heading.index for heading in headings if heading.level == 1]
    if len(h1_indexes) > 1:
        raise ValueError("multiple primary H1 headings")
    sections: dict[str, tuple[int, int]] = {}
    notes_indexes: list[int] = []
    for position, heading in enumerate(headings):
        if heading.level != 2:
            continue
        normalized = normalize_text(heading.title)
        if normalized == "notes":
            notes_indexes.append(heading.index)
        if normalized not in _MANAGED_SECTIONS:
            continue
        if normalized in sections:
            raise ValueError(f"duplicate managed section {heading.title!r}")
        end = len(lines)
        for candidate in headings[position + 1 :]:
            if candidate.level <= 2:
                end = candidate.index
                break
        sections[normalized] = (heading.index, end)

    if marked_abstract_heading is not None:
        marker_end = abstract_end_marker + 1
        if marker_end < len(lines) and not lines[marker_end].strip():
            marker_end += 1
        sections["abstract"] = (marked_abstract_heading.index, marker_end)

    abstract: str | None = None
    if "abstract" in sections:
        start, end = sections["abstract"]
        content_end = abstract_end_marker if abstract_end_marker is not None else end
        value = "".join(lines[start + 1 : content_end]).strip("\r\n")
        abstract = None if value == MISSING_ABSTRACT else value
    return BodyLayout(
        headings=tuple(headings),
        h1_index=h1_indexes[0] if h1_indexes else None,
        sections=sections,
        notes_index=notes_indexes[0] if len(notes_indexes) == 1 else None,
        abstract=abstract,
        abstract_end_marker=abstract_end_marker,
    )


def _legacy_abstract_boundary_is_ambiguous(layout: BodyLayout) -> bool:
    if "abstract" not in layout.sections or layout.abstract_end_marker is not None:
        return False
    abstract_start = layout.sections["abstract"][0]
    safe_boundaries = [
        start
        for name, (start, _end) in layout.sections.items()
        if name != "abstract" and start > abstract_start
    ]
    if layout.notes_index is not None and layout.notes_index > abstract_start:
        safe_boundaries.append(layout.notes_index)
    safe_end = min(safe_boundaries, default=None)
    return any(
        heading.level == 2
        and abstract_start < heading.index
        and (safe_end is None or heading.index < safe_end)
        for heading in layout.headings
    )


def rewrite_managed_body(
    body: str,
    *,
    title: str,
    abstract: str | None,
    versions_summary: str,
    sources_summary: str,
) -> str:
    layout = analyze_body(body)
    lines = body.splitlines(keepends=True)
    blocks = {
        "abstract": render_abstract_section(abstract),
        "versions": f"## Versions\n\n{versions_summary}\n\n",
        "sources": f"## Sources\n\n{sources_summary}\n\n",
    }
    replacements: dict[int, tuple[int, str]] = {}
    for name, (start, end) in layout.sections.items():
        if name == "abstract" and _legacy_abstract_boundary_is_ambiguous(layout):
            raise ValueError("ambiguous legacy managed Abstract boundary")
        replacements[start] = (end, blocks[name])
    missing = [name for name in _MANAGED_SECTIONS if name not in layout.sections]
    insertion = layout.notes_index if missing and layout.notes_index is not None else None
    output: list[str] = []
    index = 0
    while index < len(lines):
        if insertion == index:
            output.extend(blocks[name] for name in missing)
        replacement = replacements.get(index)
        if replacement is not None:
            end, value = replacement
            output.append(value)
            index = end
            continue
        if layout.h1_index == index:
            output.append(f"# {title}\n")
        else:
            output.append(lines[index])
        index += 1

    rendered = "".join(output)
    if layout.h1_index is None:
        rendered = rendered.lstrip("\r\n")
        rendered = f"# {title}\n\n{rendered}"
    if missing and insertion is None:
        if rendered and not rendered.endswith("\n"):
            rendered += "\n"
        if rendered and not rendered.endswith("\n\n"):
            rendered += "\n"
        rendered += "".join(blocks[name] for name in missing).rstrip() + "\n"
    return rendered


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("discovered_at must be an ISO datetime")
    if parsed.utcoffset() is None:
        raise ValueError("discovered_at must include a timezone")
    return parsed


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        raise ValueError("publication_date must be a date")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ValueError("publication_date must be an ISO date or null")


def _identity_external_ids(
    frontmatter: dict[str, Any],
    problems: list[str],
) -> tuple[set[tuple[str, str]], dict[str, str | None]]:
    identities: set[tuple[str, str]] = set()
    values: dict[str, str | None] = {}
    raw = frontmatter.get("external_ids", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        problems.append("external_ids must be a mapping")
        raw = {}
    for namespace, identifier in raw.items():
        if not isinstance(namespace, str) or not namespace.strip():
            problems.append("external_ids contains an invalid namespace")
            continue
        normalized_namespace = namespace.strip().casefold()
        if identifier is None and normalized_namespace in {
            "openalex",
            "doi",
            "arxiv",
            "crossref",
        }:
            values[normalized_namespace] = None
            continue
        if not isinstance(identifier, str) or not identifier.strip():
            problems.append(f"external_ids.{namespace} must be a non-empty string")
            continue
        normalized_identifier = identifier.strip()
        if normalized_namespace == "doi":
            try:
                normalized_identifier = normalize_doi(normalized_identifier) or ""
            except ValueError:
                normalized_identifier = ""
        if not normalized_identifier:
            problems.append(f"external_ids.{namespace} is invalid")
            continue
        identities.add((normalized_namespace, normalized_identifier))
        previous = values.get(normalized_namespace)
        if previous is not None and previous != identifier.strip():
            problems.append(f"external_ids has conflicting {normalized_namespace} values")
        values[normalized_namespace] = identifier.strip()

    for field, namespace in (
        ("doi", "doi"),
        ("openalex_id", "openalex"),
        ("arxiv_id", "arxiv"),
    ):
        identifier = frontmatter.get(field)
        if identifier is None:
            continue
        if not isinstance(identifier, str) or not identifier.strip():
            problems.append(f"{field} must be a non-empty string or null")
            continue
        normalized_identifier = identifier.strip()
        if namespace == "doi":
            try:
                normalized_identifier = normalize_doi(normalized_identifier) or ""
            except ValueError:
                normalized_identifier = ""
        if not normalized_identifier:
            problems.append(f"{field} is invalid")
            continue
        identities.add((namespace, normalized_identifier))
        previous = values.get(namespace)
        if previous is not None and previous != identifier.strip():
            previous_normalized = (
                normalize_doi(previous) if namespace == "doi" else previous.strip()
            )
            if previous_normalized != normalized_identifier:
                problems.append(f"{field} conflicts with external_ids.{namespace}")
        values[namespace] = identifier.strip()
    return identities, values


def _parse_author_links(value: Any, authors_dir: Path) -> tuple[AuthorLink, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("authors must be a non-empty list of wikilinks")
    links: list[AuthorLink] = []
    for raw in value:
        if not isinstance(raw, str):
            raise ValueError("authors must contain only wikilink strings")
        match = _AUTHOR_LINK.fullmatch(raw.strip())
        if match is None:
            raise ValueError(f"invalid Author wikilink {raw!r}")
        stem = match.group(1)
        if stem.endswith(".md"):
            stem = stem[:-3]
        if not stem or "/" in stem or stem in {".", ".."}:
            raise ValueError(f"unsafe Author wikilink {raw!r}")
        alias = match.group(2) or stem
        links.append(AuthorLink(stem=stem, alias=alias, path=authors_dir / f"{stem}.md"))
    return tuple(links)


def parse_paper_state(
    path: Path,
    contents: str,
    authors_dir: Path,
) -> PaperMarkdownState | None:
    try:
        frontmatter, body = _parse_document(contents)
    except ValueError as error:
        return PaperMarkdownState(
            path=path,
            original=contents,
            frontmatter=None,
            body=None,
            paper_id=None,
            identity_external_ids=frozenset(),
            identity_version_keys=frozenset(),
            identity_source_keys=frozenset(),
            updateable=False,
            problems=(str(error),),
        )
    if frontmatter.get("type") != "paper":
        return None

    problems: list[str] = []
    paper_id: UUID | None = None
    try:
        paper_id = UUID(str(frontmatter.get("id")))
    except (TypeError, ValueError, AttributeError):
        problems.append("id must be a valid UUID")

    identity_external_ids, external_values = _identity_external_ids(
        frontmatter, problems
    )
    version_keys: set[tuple[str, str]] = set()
    versions: list[PaperVersion] = []
    raw_versions = frontmatter.get("versions", [])
    if not isinstance(raw_versions, list):
        problems.append("versions must be a list")
        raw_versions = []
    for index, raw in enumerate(raw_versions):
        if isinstance(raw, dict):
            source = raw.get("source")
            identifier = raw.get("identifier")
            if isinstance(source, str) and isinstance(identifier, str):
                try:
                    version_keys.add(normalize_version_key(source, identifier))
                except ValueError:
                    pass
        try:
            versions.append(PaperVersion.model_validate(raw))
        except ValidationError as error:
            problems.append(f"versions[{index}] is invalid: {error.errors()[0]['msg']}")
    if len({normalize_version_key(item.source, item.identifier) for item in versions}) != len(
        versions
    ):
        problems.append("versions contains duplicate source/identifier keys")

    source_keys: set[tuple[str, str]] = set()
    sources: list[MetadataSource] = []
    raw_sources = frontmatter.get("sources", [])
    if not isinstance(raw_sources, list):
        problems.append("sources must be a list")
        raw_sources = []
    for index, raw in enumerate(raw_sources):
        if isinstance(raw, dict):
            provider = raw.get("provider")
            record_id = raw.get("record_id")
            if isinstance(provider, str) and isinstance(record_id, str):
                try:
                    source_keys.add(normalize_source_key(provider, record_id))
                except ValueError:
                    pass
        try:
            sources.append(MetadataSource.model_validate(raw))
        except ValidationError as error:
            problems.append(f"sources[{index}] is invalid: {error.errors()[0]['msg']}")

    title = frontmatter.get("title")
    if not isinstance(title, str) or not title.strip():
        problems.append("title must be a non-empty string")
        title = None
    journal = frontmatter.get("journal")
    if not isinstance(journal, str) or not journal.strip():
        problems.append("journal must be a non-empty string")
        journal = None
    try:
        publication_date = _parse_date(frontmatter.get("publication_date"))
    except ValueError as error:
        problems.append(str(error))
        publication_date = None

    raw_keywords = frontmatter.get("author_keywords", [])
    if not isinstance(raw_keywords, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw_keywords
    ):
        problems.append("author_keywords must be a list of non-empty strings")
        keywords: tuple[str, ...] = ()
    else:
        keywords = tuple(item.strip() for item in raw_keywords)

    try:
        author_links = _parse_author_links(frontmatter.get("authors"), authors_dir)
    except ValueError as error:
        problems.append(str(error))
        author_links = ()
    try:
        status = WorkflowStatus(frontmatter.get("status"))
    except (TypeError, ValueError):
        problems.append("status is invalid")
        status = None
    try:
        discovered_at = _parse_datetime(frontmatter.get("discovered_at"))
    except (TypeError, ValueError) as error:
        problems.append(str(error))
        discovered_at = None
    zotero_key = frontmatter.get("zotero_key")
    if zotero_key is not None and (
        not isinstance(zotero_key, str) or not zotero_key.strip()
    ):
        problems.append("zotero_key must be a non-empty string or null")
        zotero_key = None

    try:
        external_ids = ExternalIds.model_validate(external_values)
    except ValidationError as error:
        problems.append(f"external_ids is invalid: {error.errors()[0]['msg']}")
        external_ids = None
    raw_preferred = frontmatter.get("preferred_version")
    if raw_preferred is None:
        preferred = None
    else:
        try:
            preferred = VersionRef.model_validate(raw_preferred)
            if normalize_version_key(
                preferred.source, preferred.identifier
            ) not in {normalize_version_key(item.source, item.identifier) for item in versions}:
                problems.append("preferred_version does not reference versions")
        except (ValidationError, ValueError) as error:
            problems.append(f"preferred_version is invalid: {error}")
            preferred = None
    try:
        layout = analyze_body(body)
        abstract = layout.abstract
    except ValueError as error:
        problems.append(str(error))
        abstract = None

    return PaperMarkdownState(
        path=path,
        original=contents,
        frontmatter=frontmatter,
        body=body,
        paper_id=paper_id,
        identity_external_ids=frozenset(identity_external_ids),
        identity_version_keys=frozenset(version_keys),
        identity_source_keys=frozenset(source_keys),
        updateable=not problems,
        problems=tuple(problems),
        title=title.strip() if title is not None else None,
        journal=journal.strip() if journal is not None else None,
        publication_date=publication_date,
        abstract=abstract,
        author_keywords=keywords,
        author_links=author_links,
        status=status,
        discovered_at=discovered_at,
        zotero_key=zotero_key.strip() if isinstance(zotero_key, str) else None,
        external_ids=external_ids,
        versions=tuple(versions),
        sources=tuple(sources),
        preferred_version=preferred,
    )


def parse_author_state(path: Path, contents: str) -> AuthorMarkdownState | None:
    try:
        frontmatter, body = _parse_document(contents)
    except ValueError:
        return None
    if frontmatter.get("type") != "author":
        return None
    name = frontmatter.get("name")
    openalex_id = frontmatter.get("openalex_id")
    orcid = frontmatter.get("orcid")
    if not isinstance(name, str) or not name.strip():
        return None
    if openalex_id is not None and not isinstance(openalex_id, str):
        return None
    if orcid is not None and not isinstance(orcid, str):
        return None
    return AuthorMarkdownState(
        path=path,
        original=contents,
        frontmatter=frontmatter,
        body=body,
        name=name.strip(),
        openalex_key=normalize_openalex_author_id(openalex_id),
        orcid_key=normalize_orcid(orcid),
    )


def merge_versions(
    existing: tuple[PaperVersion, ...],
    incoming: tuple[PaperVersion, ...],
) -> tuple[tuple[PaperVersion, ...], tuple[str, ...]]:
    merged = {
        normalize_version_key(item.source, item.identifier): item for item in existing
    }
    warnings: list[str] = []
    for candidate in incoming:
        key = normalize_version_key(candidate.source, candidate.identifier)
        current = merged.get(key)
        if current is None:
            merged[key] = candidate
            continue
        updates: dict[str, Any] = {}
        if current.kind is VersionKind.UNKNOWN and candidate.kind is not VersionKind.UNKNOWN:
            updates["kind"] = candidate.kind
        elif (
            current.kind is not VersionKind.UNKNOWN
            and candidate.kind is not VersionKind.UNKNOWN
            and current.kind is not candidate.kind
        ):
            warnings.append(f"version {key[0]}:{key[1]} has conflicting known kinds")
        for field in ("date", "url"):
            old = getattr(current, field)
            new = getattr(candidate, field)
            if old is None and new is not None:
                updates[field] = new
            elif old is not None and new is not None and str(old) != str(new):
                warnings.append(f"version {key[0]}:{key[1]} has conflicting {field}")
        if updates:
            merged[key] = current.model_copy(update=updates)
    ordered = tuple(merged[key] for key in sorted(merged))
    return ordered, tuple(warnings)


def select_preferred_version(versions: tuple[PaperVersion, ...]) -> VersionRef | None:
    if not versions:
        return None
    selected = min(
        versions,
        key=lambda version: (
            -_VERSION_PRIORITY[version.kind],
            version.date is None,
            -(version.date.toordinal() if version.date else 0),
            *normalize_version_key(version.source, version.identifier),
        ),
    )
    return VersionRef(source=selected.source, identifier=selected.identifier)


def merge_sources(
    existing: tuple[MetadataSource, ...],
    incoming: tuple[MetadataSource, ...],
) -> tuple[MetadataSource, ...]:
    merged: dict[tuple[str, str], MetadataSource] = {}
    for source in (*existing, *incoming):
        key = normalize_source_key(source.provider, source.record_id)
        current = merged.get(key)
        if current is None or source.retrieved_at > current.retrieved_at:
            merged[key] = source
    return tuple(merged[key] for key in sorted(merged))


def _preferred_key(value: VersionRef | None) -> tuple[str, str] | None:
    if value is None:
        return None
    return normalize_version_key(value.source, value.identifier)


def _external_mapping(value: ExternalIds) -> dict[str, str | None]:
    return {
        namespace.strip().casefold(): identifier
        for namespace, identifier in value.model_dump(exclude_none=False).items()
    }


def _merge_external_ids(
    existing: ExternalIds,
    incoming: ExternalIds,
) -> tuple[ExternalIds, tuple[str, ...]]:
    merged = _external_mapping(existing)
    warnings: list[str] = []
    for namespace, identifier in _external_mapping(incoming).items():
        current = merged.get(namespace)
        if current is None and identifier is not None:
            merged[namespace] = identifier
            continue
        if current is None or identifier is None:
            continue
        old_comparison = normalize_doi(current) if namespace == "doi" else current.strip()
        new_comparison = (
            normalize_doi(identifier) if namespace == "doi" else identifier.strip()
        )
        if old_comparison != new_comparison:
            warnings.append(f"external ID {namespace} conflicts with durable value")
    return ExternalIds.model_validate(merged), tuple(warnings)


def _version_for_preferred(
    versions: tuple[PaperVersion, ...],
    preferred: VersionRef | None,
) -> PaperVersion | None:
    key = _preferred_key(preferred)
    if key is None:
        return None
    return next(
        (
            version
            for version in versions
            if normalize_version_key(version.source, version.identifier) == key
        ),
        None,
    )


def _changed(left: Any, right: Any) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return normalize_text(left) != normalize_text(right)
    return left != right


def merge_paper_state(
    state: PaperMarkdownState,
    paper: CanonicalPaper,
) -> MergedPaperState:
    """Merge one canonical view into a validated durable Paper state."""

    warnings: list[str] = []
    versions, version_warnings = merge_versions(state.versions, paper.versions)
    warnings.extend(version_warnings)
    preferred = select_preferred_version(versions)
    incoming_is_preferred = _preferred_key(preferred) == _preferred_key(
        paper.preferred_version
    )
    external_ids, external_warnings = _merge_external_ids(
        state.external_ids or ExternalIds(), paper.external_ids
    )
    warnings.extend(external_warnings)
    sources = merge_sources(state.sources, paper.sources)

    title = state.title or paper.metadata.title
    journal = state.journal or paper.metadata.journal
    for field, durable, incoming in (
        ("title", title, paper.metadata.title),
        ("journal", journal, paper.metadata.journal),
    ):
        if incoming_is_preferred:
            if durable and _changed(durable, incoming):
                warnings.append(
                    f"{field} changed with the effective preferred version"
                )
            if field == "title":
                title = incoming
            else:
                journal = incoming
        elif durable and _changed(durable, incoming):
            warnings.append(
                f"ignored {field} from a non-effective incoming version"
            )

    abstract = state.abstract
    incoming_abstract = paper.metadata.abstract
    if incoming_abstract is not None:
        if abstract is None:
            abstract = incoming_abstract
        elif incoming_is_preferred and incoming_abstract != "":
            if _changed(abstract, incoming_abstract):
                warnings.append(
                    "abstract changed with the effective preferred version"
                )
            abstract = incoming_abstract
        elif _changed(abstract, incoming_abstract):
            warnings.append(
                "empty incoming abstract did not replace durable abstract"
                if incoming_is_preferred
                else "ignored abstract from a non-effective incoming version"
            )

    keywords = state.author_keywords
    incoming_keywords = tuple(paper.metadata.author_keywords)
    if incoming_keywords:
        if not keywords:
            keywords = incoming_keywords
        elif incoming_is_preferred:
            if keywords != incoming_keywords:
                warnings.append(
                    "author keywords changed with the effective preferred version"
                )
            keywords = incoming_keywords
        elif keywords != incoming_keywords:
            warnings.append(
                "ignored author keywords from a non-effective incoming version"
            )

    preferred_version = _version_for_preferred(versions, preferred)
    if preferred_version is not None and preferred_version.date is not None:
        publication_date = preferred_version.date
        if (
            state.publication_date is not None
            and state.publication_date != publication_date
        ):
            warnings.append(
                "publication date changed with the effective preferred version"
            )
    else:
        publication_date = state.publication_date
        incoming_date = paper.metadata.publication_date
        if publication_date is None:
            publication_date = incoming_date
        elif incoming_date is not None and publication_date != incoming_date:
            if incoming_is_preferred:
                warnings.append(
                    "publication date changed with the effective preferred version"
                )
                publication_date = incoming_date
            else:
                warnings.append(
                    "ignored publication date from a non-effective incoming version"
                )

    return MergedPaperState(
        title=title,
        journal=journal,
        publication_date=publication_date,
        abstract=abstract,
        author_keywords=keywords,
        external_ids=external_ids,
        versions=versions,
        sources=sources,
        preferred_version=preferred,
        incoming_is_preferred=incoming_is_preferred,
        warnings=tuple(warnings),
    )
