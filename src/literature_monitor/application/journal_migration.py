"""Read-only legacy Journal and Paper compatibility analysis for SPEC §41."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from literature_monitor.config import ConfigurationError, JournalConfig, LegacyJournal, normalize_journal_issn
from literature_monitor.markdown_state import PaperJournalAttributionState, parse_paper_state
from literature_monitor.openalex import (
    OpenAlexRecordError, SourceEvidence, SourceEvidenceResolution, SourceEvidenceStatus,
    reconcile_source_evidence,
)


class MigrationStatus(str, Enum):
    ESTABLISHED = "established"
    SAFE = "safe"
    CONFIRMATION_REQUIRED = "confirmation_required"
    CONFLICT = "conflict"
    UNRESOLVED = "unresolved"


class EstablishmentMethod(str, Enum):
    AUTOMATIC_EXISTING_IDENTIFIER = "automatic_existing_identifier"
    INDEPENDENT_CONFIRMATION = "independent_confirmation"


@dataclass(frozen=True)
class MigrationConfirmation:
    row_number: int
    issn_l: str
    evidence: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "issn_l", normalize_journal_issn(self.issn_l))
        if self.row_number < 1 or not self.evidence.strip():
            raise ValueError("confirmation requires a positive row number and independent evidence")


@dataclass(frozen=True)
class JournalMigrationRow:
    row_number: int
    legacy: LegacyJournal
    resolutions: tuple[SourceEvidenceResolution, ...]
    source: SourceEvidence | None
    proposed_issn_l: str | None
    establishment_method: EstablishmentMethod | None
    confirmation: MigrationConfirmation | None
    proposed_group: str | None
    status: MigrationStatus
    diagnostics: tuple[str, ...]
    target_collision: bool = False


@dataclass(frozen=True)
class JournalMigrationAnalysis:
    rows: tuple[JournalMigrationRow, ...]

    @property
    def safe(self) -> bool:
        return bool(self.rows) and all(row.status is MigrationStatus.SAFE for row in self.rows)


def analyze_journal_migration(
    journals: Sequence[LegacyJournal], resolutions: Sequence[SourceEvidenceResolution], *,
    confirmations: Sequence[MigrationConfirmation] = (),
) -> JournalMigrationAnalysis:
    """Establish targets from transient evidence; SAFE additionally needs Paper proof."""
    by_identifier: dict[str, list[SourceEvidenceResolution]] = defaultdict(list)
    by_row: dict[int, list[MigrationConfirmation]] = defaultdict(list)
    for confirmation in confirmations:
        if confirmation.row_number > len(journals):
            raise ValueError("confirmation references an unknown Journal row")
        by_row[confirmation.row_number].append(confirmation)
    for result in resolutions:
        by_identifier[result.requested_issn].append(result)
    rows = []
    for row_number, journal in enumerate(journals, start=1):
        evidence, diagnostics, identifiers = [], [], set()
        status = MigrationStatus.ESTABLISHED
        for raw in journal.issn:
            try:
                issn = normalize_journal_issn(raw)
            except ValueError as error:
                diagnostics.append(str(error))
                status = MigrationStatus.UNRESOLVED
                continue
            identifiers.add(issn)
            matches = by_identifier.get(issn, [])
            if len(matches) != 1:
                diagnostics.append(f"{issn}: expected one resolution result, got {len(matches)}")
                status = MigrationStatus.UNRESOLVED
                continue
            result = matches[0]
            evidence.append(result)
            if result.evidence is None or result.status is not SourceEvidenceStatus.RESOLVED:
                diagnostics.append(f"{issn}: {result.status.value}: {result.diagnostic}")
                status = (MigrationStatus.CONFLICT if result.status is SourceEvidenceStatus.AMBIGUOUS_SOURCE
                          else MigrationStatus.UNRESOLVED)
        source, target, method, confirmation = None, None, None, None
        if status is MigrationStatus.ESTABLISHED:
            try:
                source = reconcile_source_evidence(tuple(result.evidence for result in evidence))
            except OpenAlexRecordError as error:
                status = MigrationStatus.CONFLICT
                diagnostics.append(str(error))
            else:
                diagnostics.extend(source.diagnostics)
                if any("inconsistent optional Publisher" in d for d in source.diagnostics):
                    status = MigrationStatus.CONFLICT
                    diagnostics.append("unresolved contradictory Source metadata")
                confirmed = by_row.get(row_number, [])
                if len(confirmed) > 1:
                    status = MigrationStatus.CONFLICT
                    diagnostics.append("multiple independent confirmations for one row")
                elif confirmed:
                    confirmation = confirmed[0]
                    if confirmation.issn_l not in source.aliases:
                        status = MigrationStatus.CONFLICT
                        diagnostics.append("confirmed ISSN-L is absent from usable Source membership")
                    else:
                        target = confirmation.issn_l
                        method = EstablishmentMethod.INDEPENDENT_CONFIRMATION
                        if target != source.provider_issn_l:
                            diagnostics.append(f"independently confirmed ISSN-L {target} differs from "
                                               f"Provider candidate {source.provider_issn_l}")
                elif status is MigrationStatus.ESTABLISHED and source.provider_issn_l is not None and source.provider_issn_l in identifiers:
                    target = source.provider_issn_l
                    method = EstablishmentMethod.AUTOMATIC_EXISTING_IDENTIFIER
                elif status is MigrationStatus.ESTABLISHED:
                    status = MigrationStatus.CONFIRMATION_REQUIRED
                    diagnostics.append("Provider candidate unavailable or absent from legacy set; "
                                       "independent confirmation required")
        rows.append(JournalMigrationRow(
            row_number, journal, tuple(evidence), source, target, method, confirmation,
            journal.group, status, tuple(diagnostics),
        ))

    targets: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        if row.proposed_issn_l is not None:
            targets[row.proposed_issn_l].append(index)
    for issn_l, indices in targets.items():
        if len(indices) < 2:
            continue
        conflicting_rows = ", ".join(str(rows[index].row_number) for index in indices)
        for index in indices:
            row = rows[index]
            rows[index] = replace(
                row, status=MigrationStatus.CONFLICT, target_collision=True,
                diagnostics=row.diagnostics + (
                    f"target ISSN-L {issn_l} collides across legacy rows {conflicting_rows}; explicit treatment required",
                ),
            )
    return JournalMigrationAnalysis(tuple(rows))


class MappingStatus(str, Enum):
    UNIQUE = "unique"
    UNMAPPED = "unmapped"
    AMBIGUOUS = "ambiguous"
    UNPROVEN = "unproven"


@dataclass(frozen=True)
class PaperVenueMapping:
    status: MappingStatus
    journal_rows: tuple[int, ...]
    group: str | None = None


@dataclass(frozen=True)
class PaperCompatibility:
    path: Path
    attribution_state: PaperJournalAttributionState | None
    journal_issns: tuple[str, ...]
    current: PaperVenueMapping | None
    proposed: PaperVenueMapping | None
    changed: bool | None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkspaceCompatibilityAnalysis:
    papers: tuple[PaperCompatibility, ...]
    diagnostics: tuple[str, ...] = ()
    migration_rows: tuple[JournalMigrationRow, ...] = ()

    @property
    def safe(self) -> bool:
        return not self.diagnostics and all(paper.changed is False for paper in self.papers)


def _mapping(
    identities: set[str], rows: Sequence[JournalMigrationRow], *, canonical: bool,
) -> PaperVenueMapping:
    # Unknown target identities cannot prove the absence of a second matching row.
    if canonical and any(row.proposed_issn_l is None for row in rows):
        return PaperVenueMapping(MappingStatus.UNPROVEN, ())
    matches = [row for row in rows if identities.intersection(
        (row.proposed_issn_l,) if canonical else row.legacy.issn
    )]
    if not matches:
        return PaperVenueMapping(MappingStatus.UNMAPPED, ())
    if len(matches) != 1:
        return PaperVenueMapping(MappingStatus.AMBIGUOUS, tuple(row.row_number for row in matches))
    return PaperVenueMapping(MappingStatus.UNIQUE, (matches[0].row_number,), matches[0].proposed_group if canonical else matches[0].legacy.group)


def analyze_workspace_compatibility(
    workspace: Path, migration: JournalMigrationAnalysis,
) -> WorkspaceCompatibilityAnalysis:
    """Use the production Paper parser; compare attribution without modifying any file."""
    papers = []
    papers_dir = workspace / "Papers"
    try:
        if any(part.is_symlink() for part in (workspace, *workspace.parents, papers_dir)):
            raise ValueError("workspace/Papers path is unsafe")
        # stat distinguishes genuine absence from permission/read errors.
        try:
            workspace.stat()
        except FileNotFoundError:
            return WorkspaceCompatibilityAnalysis((), migration_rows=migration.rows)
        if not workspace.is_dir():
            raise ValueError("workspace is not a directory")
        try:
            papers_dir.stat()
        except FileNotFoundError:
            return WorkspaceCompatibilityAnalysis((), migration_rows=migration.rows)
        if not papers_dir.is_dir():
            raise ValueError("Papers is not a directory")
        paths = sorted(path for path in papers_dir.iterdir() if path.suffix == ".md")
    except (OSError, ValueError) as error:
        return WorkspaceCompatibilityAnalysis((), (str(error),), migration.rows)
    for path in paths:
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError("Paper is not a regular non-symlink file")
            state = parse_paper_state(path, path.read_text(encoding="utf-8"), workspace / "Authors")
        except (OSError, UnicodeError, ValueError) as error:
            papers.append(PaperCompatibility(path, None, (), None, None, None, (str(error),)))
            continue
        if state is None:
            papers.append(PaperCompatibility(path, None, (), None, None, None, ("not a Paper",)))
            continue
        if state.problems:
            papers.append(PaperCompatibility(
                path, state.journal_attribution_state, state.journal_issns,
                None, None, None, state.problems,
            ))
            continue
        if state.journal_attribution_state is not PaperJournalAttributionState.VALID:
            papers.append(PaperCompatibility(
                path, state.journal_attribution_state, state.journal_issns, None, None, None,
                ("legacy missing/empty or malformed attribution; no name-based identity proof",),
            ))
            continue
        current = _mapping(set(state.journal_issns), migration.rows, canonical=False)
        proposed = _mapping(set(state.journal_issns), migration.rows, canonical=True)
        changed = (None if proposed.status is MappingStatus.UNPROVEN
                   else current != proposed or current.status is not MappingStatus.UNIQUE)
        papers.append(PaperCompatibility(
            path, state.journal_attribution_state, state.journal_issns, current, proposed, changed,
            ("target mapping is incompletely proven",) if changed is None else (),
        ))
    return WorkspaceCompatibilityAnalysis(tuple(papers), migration_rows=migration.rows)


def finalize_journal_migration(
    migration: JournalMigrationAnalysis, workspace: WorkspaceCompatibilityAnalysis,
) -> JournalMigrationAnalysis:
    """Grant SAFE only after complete, unchanged durable Paper/Group proof."""
    if workspace.migration_rows != migration.rows:
        raise ValueError("Paper compatibility proof belongs to different migration targets")
    if workspace.safe:
        return JournalMigrationAnalysis(tuple(
            replace(row, status=MigrationStatus.SAFE) if row.status is MigrationStatus.ESTABLISHED else row
            for row in migration.rows
        ))
    failure = (MigrationStatus.CONFLICT if any(paper.changed is True for paper in workspace.papers)
               else MigrationStatus.UNRESOLVED)
    return JournalMigrationAnalysis(tuple(
        replace(row, status=failure,
                diagnostics=row.diagnostics + ("complete Paper/Group compatibility proof did not pass",))
        if row.status is MigrationStatus.ESTABLISHED else row
        for row in migration.rows
    ))


def resolve_legacy_journals(client, journals: Sequence[LegacyJournal], *,
                            confirmations: Sequence[MigrationConfirmation] = (),
                            workspace: Path | None = None) -> tuple[JournalConfig, ...]:
    """Resolve the whole legacy input; explicit confirmations are bound to row positions."""
    from literature_monitor.openalex import resolve_source_identities

    identifiers = [issn for journal in journals for issn in journal.issn]
    identifiers.extend(c.issn_l for c in confirmations)
    resolutions = resolve_source_identities(client, identifiers)
    migration = analyze_journal_migration(journals, resolutions, confirmations=confirmations)
    # A corrected target also needs its own unique Source membership verification.
    outcomes = {r.requested_issn: r for r in resolutions}
    for row in migration.rows:
        if row.confirmation is not None and row.source is not None:
            result = outcomes[row.confirmation.issn_l]
            if result.evidence is None or result.evidence.source_id != row.source.source_id:
                raise ConfigurationError("confirmed target has unresolved/conflicting Source membership", field="journals")
            reconciled = reconcile_source_evidence((row.source, result.evidence))
            if any("inconsistent optional Publisher" in d for d in reconciled.diagnostics):
                raise ConfigurationError("confirmed target has conflicting direct Publisher evidence", field="journals")
    if workspace is not None:
        migration = finalize_journal_migration(migration, analyze_workspace_compatibility(workspace, migration))
        allowed = {MigrationStatus.SAFE}
    else:
        allowed = {MigrationStatus.ESTABLISHED}
    if not migration.rows or any(row.status not in allowed for row in migration.rows):
        detail = "; ".join(f"row {r.row_number}: {r.status.value}: {'; '.join(r.diagnostics)}"
                           for r in migration.rows if r.status not in allowed)
        raise ConfigurationError("legacy migration blocked: " + detail, field="journals")
    return tuple(JournalConfig(name=r.source.display_name, issn_l=r.proposed_issn_l,
                               publisher_id=r.source.publisher_id, group=r.proposed_group) for r in migration.rows)
