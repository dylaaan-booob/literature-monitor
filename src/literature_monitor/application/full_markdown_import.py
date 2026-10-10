"""Complete, revision-bound list.md import and export for SPEC §43.5."""

from __future__ import annotations

import hashlib
import difflib
import os
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from literature_monitor.application.access_service_migration import _source_identity
from literature_monitor.application.settings import SettingsIssueSource, _read_snapshot
from literature_monitor.config import ConfigurationError, detect_list_schema, parse_settings_list_text
from literature_monitor.openalex import (
    OpenAlexClient, SourceEvidenceStatus, resolve_publisher_metadata, resolve_source_identities,
)
from literature_monitor.safe_write import (
    ContentChangedError, TextWriteCommittedError, replace_regular_text_at_identity,
)

MAX_MARKDOWN_BYTES = 1024 * 1024
MANAGED = {"Journals", "Publishers", "Access Services"}


class MarkdownImportCommittedWarning(RuntimeError):
    """Atomic replacement occurred but directory synchronization reported an error."""

    def __init__(self, revision: str):
        super().__init__("list.md replacement committed, but directory fsync failed")
        self.revision = revision


@dataclass(frozen=True)
class FullMarkdownPreview:
    path: Path
    original: str
    proposed: str
    directory_identity: tuple[int, int]
    file_identity: tuple[int, int]
    changes: tuple[str, ...]
    new_journal_ids: tuple[str, ...]

    @property
    def target_digest(self) -> str:
        return hashlib.sha256(self.original.encode("utf-8")).hexdigest()

    @property
    def source_digest(self) -> str:
        return hashlib.sha256(self.proposed.encode("utf-8")).hexdigest()


def _other_content(contents: str) -> str:
    """Non-managed headings plus any surrounding prose are whole-file user data."""
    lines = contents.splitlines(keepends=True)
    output = []
    in_managed = False
    for line in lines:
        if line.startswith("## "):
            in_managed = line[3:].strip() in MANAGED
        if not in_managed:
            output.append(line)
    return "".join(output)


def preview_full_markdown(path: Path, proposed: str, *, filename: str) -> FullMarkdownPreview:
    path = path.absolute()
    if not filename.lower().endswith(".md") or Path(filename).name != filename:
        raise ConfigurationError("Import requires a complete .md file, not CSV/TSV or a filesystem path")
    if not proposed or len(proposed.encode("utf-8")) > MAX_MARKDOWN_BYTES or "\x00" in proposed:
        raise ConfigurationError("Markdown Import source is empty, contains NUL, or exceeds 1 MiB")
    if any(candidate.is_symlink() for candidate in (path, *path.parents)):
        raise ConfigurationError("unsafe list.md path: symlink")
    original_identity = _source_identity(path)
    disk = _read_snapshot(path, source=SettingsIssueSource.JOURNAL_DATA)
    if disk.issue or disk.contents is None:
        raise ConfigurationError(disk.issue.message if disk.issue else "list.md is missing")
    if detect_list_schema(disk.contents, path=path) != "current":
        raise ConfigurationError("explicit legacy Access schema Upgrade Preview/Confirm required before Import")
    old_journals, old_publishers, old_services = parse_settings_list_text(disk.contents, path=path)
    new_journals, new_publishers, new_services = parse_settings_list_text(proposed, path=path)
    if _source_identity(path) != original_identity:
        raise ContentChangedError("list.md identity changed during Import Preview")

    previous_j = {j.issn_l: j for j in old_journals}
    next_j = {j.issn_l: j for j in new_journals}
    previous_p = {p.publisher_id: p for p in old_publishers}
    next_p = {p.publisher_id: p for p in new_publishers}
    previous_s = {s.id: s for s in old_services}
    next_s = {s.id: s for s in new_services}
    changes = []
    for identity, current in next_j.items():
        old = previous_j.get(identity)
        if old is None:
            changes.append(f"Add Journal: {current.name} ({identity}); OpenAlex verification required")
        elif (old.name, old.publisher_id) != (current.name, current.publisher_id):
            raise ConfigurationError(f"Journal {identity}: machine-managed name or Publisher ID conflict")
        elif old.group != current.group:
            changes.append(f"Journal Group: {identity}: {old.group or 'Ungrouped'} → {current.group or 'Ungrouped'}")
    for identity, old in previous_j.items():
        if identity not in next_j:
            changes.append(f"Remove Journal: {old.name} ({identity})")
    for identity, current in next_p.items():
        old = previous_p.get(identity)
        if old is None:
            if current.access_service_id is not None:
                raise ConfigurationError(f"new Publisher {identity} must start Unassigned")
            changes.append(f"Add Publisher: {current.name} ({identity}); OpenAlex verification required")
        else:
            if old.name != current.name:
                raise ConfigurationError(f"Publisher {identity}: machine-managed canonical name conflict")
            if old.publisher_url != current.publisher_url:
                changes.append(f"Publisher URL: {identity}: {old.publisher_url or '(blank)'} → {current.publisher_url or '(blank)'}")
            if old.access_service_id != current.access_service_id:
                changes.append(f"Publisher default Service: {identity}: {old.access_service_id or 'Unassigned'} → {current.access_service_id or 'Unassigned'}")
    for identity, old in previous_p.items():
        if identity not in next_p:
            changes.append(f"Remove Publisher: {old.name} ({identity}); saved URL: {old.publisher_url or '(blank)'}; Service: {old.access_service_id or 'Unassigned'}")
            if old.publisher_url or old.access_service_id:
                changes.append(f"Publisher cascade / loss of manual metadata: {identity}")
    for identity, current in next_s.items():
        old = previous_s.get(identity)
        if old is None:
            changes.append(f"Add Access Service: {current.name} ({identity})")
        elif (old.name, old.access_url) != (current.name, current.access_url):
            changes.append(f"Access Service edit: {identity}: {old.name} / {old.access_url or '(blank)'} → {current.name} / {current.access_url or '(blank)'}")
    for identity, old in previous_s.items():
        if identity not in next_s:
            members = [p for p in old_publishers if p.access_service_id == identity]
            changes.append(f"Delete Access Service: {old.name} ({identity}); {len(members)} previous members")
            for publisher in members:
                replacement = next_p.get(publisher.publisher_id)
                destination = (replacement.access_service_id or "Unassigned") if replacement else "Publisher removed"
                changes.append(f"Service deletion affects Publisher: {publisher.name} ({publisher.publisher_id}) → {destination}")
    old_extra, new_extra = _other_content(disk.contents), _other_content(proposed)
    if old_extra != new_extra:
        changes.append(f"Replace Conferences / unknown sections / manual text ({len(old_extra)} → {len(new_extra)} characters); all previous non-managed content is discarded")
        diff = list(difflib.unified_diff(old_extra.splitlines(), new_extra.splitlines(),
                                         fromfile="saved non-managed text", tofile="imported non-managed text",
                                         lineterm=""))
        changes.extend(f"Non-managed: {line[:180]}" for line in diff[:70])
        if len(diff) > 70:
            changes.append(f"Non-managed diff truncated after 70 lines; {len(diff) - 70} more lines differ")
    if disk.contents != proposed and not changes:
        changes.append("Whole-file Markdown formatting, table order or surrounding content changes")
    return FullMarkdownPreview(path, disk.contents, proposed, original_identity[0], original_identity[1],
                               tuple(changes), tuple(j.issn_l for j in new_journals if j.issn_l not in previous_j))


def confirm_full_markdown(
    preview: FullMarkdownPreview, *, client: OpenAlexClient | None = None,
    before_write: Callable[[], None] | None = None,
) -> str:
    """Revalidate every part of the preview, then atomically CAS one complete file."""
    current = preview_full_markdown(preview.path, preview.proposed, filename="list.md")
    if current != preview:
        raise ContentChangedError("Import source, target contents or target identity changed since Preview")
    new_journals, new_publishers, _ = parse_settings_list_text(preview.proposed, path=preview.path)
    new_j = {j.issn_l: j for j in new_journals}
    old_p = {p.publisher_id for p in parse_settings_list_text(preview.original, path=preview.path)[1]}
    new_p = {p.publisher_id: p for p in new_publishers}
    new_publishers_to_resolve = tuple(identity for identity in new_p if identity not in old_p)
    if preview.new_journal_ids or new_publishers_to_resolve:
        with (nullcontext(client) if client is not None else OpenAlexClient(api_key=os.environ.get("OPENALEX_API_KEY"))) as provider:
            if preview.new_journal_ids:
                resolutions = resolve_source_identities(provider, preview.new_journal_ids)
                if {result.requested_issn for result in resolutions} != set(preview.new_journal_ids) or len(resolutions) != len(preview.new_journal_ids):
                    raise ConfigurationError("incomplete OpenAlex Source resolution")
                for result in resolutions:
                    journal = new_j[result.requested_issn]
                    evidence = result.evidence
                    if (result.status is not SourceEvidenceStatus.RESOLVED
                            or evidence is None or evidence.display_name != journal.name
                            or evidence.publisher_id != journal.publisher_id
                            or any("inconsistent optional Publisher" in note for note in evidence.diagnostics)):
                        raise ConfigurationError(f"Journal {journal.issn_l}: unverified or conflicting OpenAlex Source identity: {result.status.value}")
            if new_publishers_to_resolve:
                metadata = resolve_publisher_metadata(provider, new_publishers_to_resolve)
                resolved = {m.publisher_id: m for m in metadata}
                for identity in new_publishers_to_resolve:
                    if identity not in resolved or resolved[identity].display_name != new_p[identity].name:
                        raise ConfigurationError(f"Publisher {identity}: unverified canonical OpenAlex name")
    # Provider resolution is transient; the originally previewed file and object must still match.
    current = preview_full_markdown(preview.path, preview.proposed, filename="list.md")
    if current != preview:
        raise ContentChangedError("list.md changed during Import verification")
    if preview.proposed != preview.original:
        try:
            replace_regular_text_at_identity(
                preview.path, preview.proposed, expected_contents=preview.original,
                expected_directory_identity=preview.directory_identity,
                expected_file_identity=preview.file_identity,
                validate_before_replace=before_write,
            )
        except TextWriteCommittedError:
            # §43.6: failure after replace must not be misreported as zero writes.
            observed = _read_snapshot(preview.path, source=SettingsIssueSource.JOURNAL_DATA)
            if observed.issue is None and observed.contents == preview.proposed:
                raise MarkdownImportCommittedWarning(observed.revision.digest or "") from None
            raise
    saved = _read_snapshot(preview.path, source=SettingsIssueSource.JOURNAL_DATA)
    if saved.issue or saved.contents != preview.proposed:
        raise ConfigurationError("Import write could not be confirmed by reading saved list.md")
    return saved.revision.digest or ""
