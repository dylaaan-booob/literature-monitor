"""Application orchestration for monitor validation, canonicalization, and runs."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date
from enum import Enum
from pathlib import Path

from literature_monitor.application.candidate_eligibility import (
    filter_candidate_evidence,
    openalex_attribution,
    valid_doi,
)
from literature_monitor.application.crossref_retrieval import CrossrefRetrieval, CrossrefSupplementResult
from literature_monitor.application.provider_state import (
    STATE_FILENAME, ProviderState, ProviderStateStatus,
    read_provider_state, replace_invalid_provider_state, update_provider_state,
)
from literature_monitor.application.runtime_metadata import METADATA_DIRECTORY_NAME
from literature_monitor.application.run_state import (
    LAST_RUN_SCHEMA_VERSION,
    LastRunSnapshot,
    LastRunSnapshotWriteError,
    RecordedRunOutcome,
    write_last_run_snapshot,
)
from literature_monitor.canonicalize import (
    CanonicalizationIssue,
    canonicalize_records,
    consolidate_evidence,
)
from literature_monitor.diagnostics import RunDiagnostic, RunDiagnosticKind, RunDiagnosticSummary, summarize_run_diagnostics
from literature_monitor.config import (
    ConfigurationError,
    JournalConfig,
    LoadedConfig,
    LogLevel,
    load_config,
    resolve_runtime_date,
    validate_runtime_keyword,
)
from literature_monitor.coverage import (
    CoverageSummary,
    CoverageUnit,
    summarize_coverage,
)
from literature_monitor.crossref import (
    CrossrefClient,
    CrossrefDiscoveryIssue,
    EnrichmentIssue,
    EnrichmentIssueSeverity,
)
from literature_monitor.date_range import (
    DateRangeError,
    ResolvedDateRange,
)
from literature_monitor.keywords import (
    KeywordExpression,
    KeywordSyntaxError,
)
from literature_monitor.safe_write import workspace_operation_lock, ContentChangedError, WorkspaceOperationLock
from literature_monitor.materialize import (
    MaterializationIssue,
    MaterializationIssueSeverity,
    materialize_papers,
    read_workspace_identities,
    WorkspaceIdentityIndex,
)
from literature_monitor.models import CanonicalPaper
from literature_monitor.progress import (
    ProgressCallback,
    ProgressEvent,
    ProgressStage,
)
from literature_monitor.openalex import (
    DiscoveryIssue,
    IssueSeverity,
    OpenAlexClient,
    ResolvedSource,
    discover_resolved_sources,
    resolve_journal_sources_batched,
)
from literature_monitor.search import (
    SearchBackendError,
    SearchExpressionError,
    build_searchable_projection,
    match_searchable_projections,
    SearchableProjection,
)


class MonitorIssueSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


class MonitorIssueComponent(str, Enum):
    CONFIGURATION = "configuration"
    SEARCH = "search"
    DATE_RANGE = "date_range"
    OPENALEX = "openalex"
    CROSSREF_DISCOVERY = "crossref_discovery"
    CROSSREF_SUPPLEMENT = "crossref_supplement"
    CONSOLIDATION = "consolidation"
    CANONICALIZATION = "canonicalization"
    MATERIALIZATION = "materialization"
    COVERAGE_SNAPSHOT = "coverage_snapshot"
    PROVIDER_STATE = "provider_state"


@dataclass(frozen=True)
class MonitorIssue:
    severity: MonitorIssueSeverity
    component: MonitorIssueComponent
    stage: str
    message: str
    journal: str | None = None
    issn: str | None = None
    record_id: str | None = None
    doi: str | None = None
    record_ids: tuple[str, ...] = ()
    path: Path | None = None
    field: str | None = None
    config_path: Path | None = None


class RunOutcome(str, Enum):
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"


@dataclass(frozen=True)
class MonitorStatistics:
    openalex_records: int = 0
    crossref_discovery_records: int = 0
    crossref_supplement_records: int = 0
    evidence_clusters: int = 0
    retained_clusters: int = 0
    consolidation_issues: int = 0
    canonicalization_issues: int = 0
    provider_issues: int = 0
    materialization_issues: int = 0
    metadata_success: int = 0
    metadata_failed: int = 0


@dataclass(frozen=True)
class ProviderStateUsage:
    crossref_reused: int = 0
    crossref_refreshed: int = 0
    crossref_new: int = 0


@dataclass(frozen=True)
class RunResult:
    resolved_date_range: ResolvedDateRange | None
    canonical_paper_count: int
    created_papers: int
    matched_existing_papers: int
    created_authors: int
    existing_authors: int
    warnings: tuple[MonitorIssue, ...]
    errors: tuple[MonitorIssue, ...]
    outcome: RunOutcome
    statistics: MonitorStatistics
    coverage: tuple[CoverageUnit, ...] = ()
    resolved_sources: tuple[ResolvedSource, ...] = ()
    log_level: LogLevel | None = None
    state_usage: ProviderStateUsage = ProviderStateUsage()
    diagnostics: tuple[RunDiagnostic, ...] = ()

    @property
    def coverage_summary(self) -> tuple[CoverageSummary, ...]:
        return summarize_coverage(self.coverage)

    @property
    def diagnostic_summary(self) -> tuple[RunDiagnosticSummary, ...]:
        return summarize_run_diagnostics(self.diagnostics)


@dataclass(frozen=True)
class _PreparedInvocation:
    config: LoadedConfig
    keyword_ast: KeywordExpression
    resolved_date_range: ResolvedDateRange
    journals: tuple[JournalConfig, ...]


@dataclass(frozen=True)
class _CanonicalCoreResult:
    config: LoadedConfig | None
    resolved_date_range: ResolvedDateRange | None
    papers: tuple[CanonicalPaper, ...]
    resolved_sources: tuple[ResolvedSource, ...]
    warnings: tuple[MonitorIssue, ...]
    errors: tuple[MonitorIssue, ...]
    outcome: RunOutcome
    statistics: MonitorStatistics
    coverage: tuple[CoverageUnit, ...] = ()
    pending_state: ProviderState = ProviderState()
    state_usage: ProviderStateUsage = ProviderStateUsage()
    diagnostics: tuple[RunDiagnostic, ...] = ()
    saved_dois: frozenset[str] = frozenset()

    @property
    def coverage_summary(self) -> tuple[CoverageSummary, ...]:
        return summarize_coverage(self.coverage)

    @property
    def log_level(self) -> LogLevel | None:
        return self.config.log_level if self.config is not None else None


def _emit_progress(
    callback: ProgressCallback | None,
    stage: ProgressStage,
) -> None:
    if callback is not None:
        callback(ProgressEvent(stage=stage))


def _split_issues(
    issues: Sequence[MonitorIssue],
) -> tuple[tuple[MonitorIssue, ...], tuple[MonitorIssue, ...]]:
    warnings = tuple(
        issue for issue in issues if issue.severity is MonitorIssueSeverity.WARNING
    )
    errors = tuple(
        issue for issue in issues if issue.severity is MonitorIssueSeverity.ERROR
    )
    return warnings, errors


def _run_outcome(
    warnings: Sequence[MonitorIssue],
    errors: Sequence[MonitorIssue],
) -> RunOutcome:
    if errors:
        return RunOutcome.COMPLETED_WITH_ERRORS
    if warnings:
        return RunOutcome.COMPLETED_WITH_WARNINGS
    return RunOutcome.COMPLETED


def _configuration_issue(
    error: ConfigurationError,
    config_path: Path,
) -> MonitorIssue:
    return MonitorIssue(
        severity=MonitorIssueSeverity.ERROR,
        component=MonitorIssueComponent.CONFIGURATION,
        stage="configuration",
        message=str(error),
        config_path=config_path,
    )


def _search_issue(
    error: SearchExpressionError | SearchBackendError | KeywordSyntaxError,
    *,
    config_path: Path,
    stage: str,
) -> MonitorIssue:
    return MonitorIssue(
        severity=MonitorIssueSeverity.ERROR,
        component=MonitorIssueComponent.SEARCH,
        stage=stage,
        message=str(error),
        field="keyword_expression",
        config_path=config_path,
    )


def _date_issue(
    error: DateRangeError,
    *,
    config_path: Path,
    stage: str,
) -> MonitorIssue:
    return MonitorIssue(
        severity=MonitorIssueSeverity.ERROR,
        component=MonitorIssueComponent.DATE_RANGE,
        stage=stage,
        message=str(error),
        field=error.field,
        config_path=config_path,
    )


def _openalex_issue(issue: DiscoveryIssue) -> MonitorIssue:
    return MonitorIssue(
        severity=(
            MonitorIssueSeverity.ERROR
            if issue.severity is IssueSeverity.ERROR
            else MonitorIssueSeverity.WARNING
        ),
        component=MonitorIssueComponent.OPENALEX,
        stage=issue.stage,
        message=issue.message,
        journal=issue.journal,
        issn=issue.issn,
        record_id=issue.record_id,
    )


def _crossref_discovery_issue(issue: CrossrefDiscoveryIssue) -> MonitorIssue:
    return MonitorIssue(
        severity=(
            MonitorIssueSeverity.ERROR
            if issue.severity is EnrichmentIssueSeverity.ERROR
            else MonitorIssueSeverity.WARNING
        ),
        component=MonitorIssueComponent.CROSSREF_DISCOVERY,
        stage=issue.stage,
        message=issue.message,
        journal=issue.journal,
        issn=issue.issn,
        record_id=issue.record_id,
        doi=issue.doi,
    )


def _enrichment_issue(issue: EnrichmentIssue) -> MonitorIssue:
    return MonitorIssue(
        severity=(
            MonitorIssueSeverity.ERROR
            if issue.severity is EnrichmentIssueSeverity.ERROR
            else MonitorIssueSeverity.WARNING
        ),
        component=MonitorIssueComponent.CROSSREF_SUPPLEMENT,
        stage=issue.stage,
        message=issue.message,
        record_id=issue.record_id,
        doi=issue.doi,
    )


def _canonicalization_issue(
    issue: CanonicalizationIssue,
    *,
    component: MonitorIssueComponent,
) -> MonitorIssue:
    return MonitorIssue(
        severity=MonitorIssueSeverity.WARNING,
        component=component,
        stage=issue.stage,
        message=issue.message,
        record_ids=issue.record_ids,
    )


def _materialization_issue(issue: MaterializationIssue) -> MonitorIssue:
    return MonitorIssue(
        severity=(
            MonitorIssueSeverity.ERROR
            if issue.severity is MaterializationIssueSeverity.ERROR
            else MonitorIssueSeverity.WARNING
        ),
        component=MonitorIssueComponent.MATERIALIZATION,
        stage="write",
        message=issue.message,
        path=issue.path,
    )


def _coverage_snapshot_issue(error: LastRunSnapshotWriteError) -> MonitorIssue:
    return MonitorIssue(
        severity=MonitorIssueSeverity.WARNING,
        component=MonitorIssueComponent.COVERAGE_SNAPSHOT,
        stage="write",
        message=f"failed to persist latest-run coverage snapshot: {error}",
        path=error.path,
    )


def _prepare_invocation(
    config_path: Path, *, loaded_config: LoadedConfig,
) -> tuple[_PreparedInvocation | None, LoadedConfig, MonitorIssue | None]:
    try:
        resolved = resolve_runtime_date(loaded_config, today=date.today())
        validate_runtime_keyword(loaded_config)
    except DateRangeError as error:
        return None, loaded_config, _date_issue(error, config_path=config_path, stage="configured_date")
    except (SearchExpressionError, SearchBackendError) as error:
        return None, loaded_config, _search_issue(error, config_path=config_path, stage="configured_keyword")
    return _PreparedInvocation(loaded_config, loaded_config.keyword_ast, resolved, loaded_config.journals), loaded_config, None


def _invalid_core_result(
    config: LoadedConfig | None,
    issue: MonitorIssue,
) -> _CanonicalCoreResult:
    return _CanonicalCoreResult(
        config=config,
        resolved_date_range=None,
        papers=(),
        resolved_sources=(),
        warnings=(),
        errors=(issue,),
        outcome=RunOutcome.INVALID_CONFIGURATION,
        statistics=MonitorStatistics(),
    )


def _execute_canonical_core(
    prepared: _PreparedInvocation, config_path: Path, *,
    identity: WorkspaceIdentityIndex,
    historical_state: ProviderState = ProviderState(),
    state_issues: tuple[MonitorIssue, ...] = (),
    progress_callback: ProgressCallback | None = None,
) -> _CanonicalCoreResult:
    _emit_progress(progress_callback, ProgressStage.DISCOVERING_PAPERS)
    saved: set[str] = set()
    issues = [*state_issues, *(_materialization_issue(issue) for issue in identity.issues)]
    diagnostics = []

    def needs_metadata(doi: str) -> bool:
        if doi in execution.excluded_dois:
            return False
        if doi in identity.saved:
            saved.add(doi)
            return False
        return identity.can_create(doi)

    with (
        CrossrefClient(mailto=os.environ.get("CROSSREF_MAILTO")) as crossref_client,
        OpenAlexClient(api_key=os.environ.get("OPENALEX_API_KEY")) as openalex_client,
    ):
        execution = CrossrefRetrieval(crossref_client, record_state=historical_state.crossref_records,
                                     progress_callback=progress_callback)
        resolutions = resolve_journal_sources_batched(openalex_client, prepared.journals,
                                                      progress_callback=progress_callback)
        sources = tuple(unit.source for unit in resolutions if unit.source is not None)
        with ThreadPoolExecutor(max_workers=2) as executor:
            oa_future = executor.submit(discover_resolved_sources, openalex_client, resolutions,
                prepared.resolved_date_range.from_date, prepared.resolved_date_range.to_date,
                progress_callback=progress_callback)
            cr_future = executor.submit(execution.discover_manifest, prepared.journals,
                prepared.resolved_date_range.from_date, prepared.resolved_date_range.to_date,
                resolved_sources=sources)
            openalex, manifest = oa_future.result(), cr_future.result()
        oa_venues = openalex_attribution(openalex, prepared.journals)
        selected = {doi for doi, venues in manifest.journal_issns.items()
                    if venues and doi not in execution.excluded_dois and needs_metadata(doi)}
        oa_records = tuple(record for record in openalex.records
                           if (doi := valid_doi(record.external_ids.doi)) in oa_venues
                           and doi not in execution.excluded_dois and needs_metadata(doi))
        _emit_progress(progress_callback, ProgressStage.MATCHING_LITERATURE)
        oa_groups = {}
        for record in oa_records:
            oa_groups.setdefault(record.external_ids.doi, []).append(record.to_evidence())
        oa_search = {doi: build_searchable_projection(records) for doi, records in oa_groups.items()}
        discovery = None
        try:
            hits = match_searchable_projections(prepared.keyword_ast, tuple(
                SearchableProjection(titles=value.titles, abstracts=value.abstracts)
                for value in oa_search.values())) if oa_search else ()
            matched = {doi for doi, hit in zip(oa_search, hits, strict=True) if hit}
            fallback = {doi for doi, value in oa_search.items()
                        if doi not in matched and (not value.titles or not value.abstracts)}
            capture_missing = {doi for doi in matched
                               if not oa_search[doi].titles or not any(item.journal for item in oa_groups[doi])}
            metadata_dois = (selected - oa_groups.keys()) | fallback | capture_missing
            discovery = execution.hydrate_manifest(manifest, tuple(sorted(selected & metadata_dois)))
            early_records = tuple(record for record in oa_records
                                  if record.external_ids.doi in metadata_dois
                                  and record.external_ids.doi not in selected)
            early = execution.supplement(early_records, should_process=needs_metadata)
            filtered_oa = replace(openalex, records=oa_records)
            first_evidence = filter_candidate_evidence(filtered_oa, manifest, discovery, early,
                                                       prepared.journals, execution.excluded_dois)
            groups = {}
            for item in first_evidence:
                groups.setdefault(item.external_ids.doi, []).append(item)
            aliases = dict(early.aliases)
            matched = {aliases.get(doi, doi) for doi in matched}
            search_dois, projections = [], []
            for doi in sorted({aliases.get(doi, doi) for doi in metadata_dois} - matched):
                records = groups.get(doi, ())
                oa = build_searchable_projection(tuple(item for item in records if item.provenance.provider == "openalex"))
                cr = build_searchable_projection(tuple(item for item in records if item.provenance.provider == "crossref"))
                search_dois.append(doi)
                # Crossref fills missing search fields; it never replaces a valid OpenAlex field.
                projections.append(SearchableProjection(titles=oa.titles or cr.titles,
                                                         abstracts=oa.abstracts or cr.abstracts))
            hits = match_searchable_projections(prepared.keyword_ast, projections) if projections else ()
            matched.update(doi for doi, hit in zip(search_dois, hits, strict=True) if hit)
        except (SearchExpressionError, SearchBackendError) as error:
            issues.append(_search_issue(error, config_path=config_path, stage="matching"))
            warnings, errors = _split_issues(issues)
            if discovery is None:
                discovery = execution.hydrate_manifest(manifest, ())
            return _CanonicalCoreResult(prepared.config, prepared.resolved_date_range, (), sources,
                warnings, errors, RunOutcome.INVALID_CONFIGURATION, MonitorStatistics(),
                coverage=(*openalex.coverage, *discovery.discovery.coverage))

        _emit_progress(progress_callback, ProgressStage.COMBINING_METADATA)
        rejected = {aliases.get(doi, doi) for doi in (selected | oa_groups.keys())} - matched
        early_requested = {record.external_ids.doi for record in early_records}
        late_records = tuple(record for record in oa_records
                             if record.external_ids.doi in matched
                             and record.external_ids.doi not in selected | early_requested)
        late = execution.supplement(late_records,
                                   should_process=lambda doi: needs_metadata(doi) and doi not in rejected,
                                   hydrate=False)
        batches = (early, late)
        retrieval = CrossrefSupplementResult(
            units=tuple(unit for batch in batches for unit in batch.units),
            records=tuple({record.doi: record for batch in batches for record in batch.records}.values()),
            reused_dois=tuple(dict.fromkeys(doi for batch in batches for doi in batch.reused_dois)),
            refreshed_dois=tuple(dict.fromkeys(doi for batch in batches for doi in batch.refreshed_dois)),
            new_dois=tuple(dict.fromkeys(doi for batch in batches for doi in batch.new_dois)),
            pending_changes=execution.pending_changes,
            skipped_aliases=tuple(pair for batch in batches for pair in batch.skipped_aliases),
            aliases=tuple(dict(pair for batch in batches for pair in batch.aliases).items()),
            unresolved_dois=tuple(doi for batch in batches for doi in batch.unresolved_dois),
        )
        final_aliases = dict(retrieval.aliases)
        final_matched = {final_aliases.get(doi, doi) for doi in matched} - rejected
        retained_oa = replace(openalex, records=tuple(record for record in oa_records
                             if aliases.get(record.external_ids.doi, record.external_ids.doi) in matched))
        evidence = filter_candidate_evidence(retained_oa, manifest, discovery, retrieval,
                                             prepared.journals, execution.excluded_dois)
        evidence = tuple(item for item in evidence
                         if item.external_ids.doi in final_matched and needs_metadata(item.external_ids.doi))
        for doi in sorted(execution.excluded_dois):
            diagnostics.append(RunDiagnostic(RunDiagnosticKind.NON_CANDIDATE_EXCLUSION,
                                             "Crossref journal-issue excluded", (doi,)))
        for member in execution._types.values():
            if member.type_conflict:
                diagnostics.append(RunDiagnostic(RunDiagnosticKind.SCOPE_DISPUTE,
                                                 "Conflicting Crossref types; no type exclusion", (member.doi,)))
        issues.extend(_openalex_issue(issue) for issue in openalex.issues)
        issues.extend(_crossref_discovery_issue(issue) for issue in discovery.discovery.issues)
        issues.extend(_enrichment_issue(issue) for issue in retrieval.issues)
        coverage = (*openalex.coverage, *discovery.discovery.coverage, *retrieval.coverage)
        consolidation = consolidate_evidence(evidence)
        diagnostics.extend(consolidation.diagnostics)
        issues.extend(_canonicalization_issue(issue, component=MonitorIssueComponent.CONSOLIDATION)
                      for issue in consolidation.issues)
        retained = consolidation.clusters
        canonical = canonicalize_records(tuple(item for cluster in retained for item in cluster.evidence)) if retained else None
        issues.extend(_canonicalization_issue(issue, component=MonitorIssueComponent.CANONICALIZATION)
                      for issue in (canonical.issues if canonical else ()))
        success, failed = execution.metadata_counts
        kinds = {doi: kind for result in (discovery, retrieval)
                 for kind, dois in (("reused", result.reused_dois), ("refreshed", result.refreshed_dois),
                                    ("new", result.new_dois)) for doi in dois}
        warnings, errors = _split_issues(issues)
        return _CanonicalCoreResult(
            config=prepared.config, resolved_date_range=prepared.resolved_date_range,
            papers=canonical.papers if canonical else (), resolved_sources=sources,
            warnings=warnings, errors=errors, outcome=_run_outcome(warnings, errors),
            statistics=MonitorStatistics(openalex_records=len(openalex.records),
                crossref_discovery_records=len(manifest.members), crossref_supplement_records=len(retrieval.records),
                evidence_clusters=len(consolidation.clusters), retained_clusters=len(retained),
                consolidation_issues=len(consolidation.issues), canonicalization_issues=len(canonical.issues) if canonical else 0,
                provider_issues=len(openalex.issues) + len(discovery.discovery.issues) + len(retrieval.issues),
                metadata_success=success, metadata_failed=failed),
            coverage=coverage, pending_state=ProviderState(crossref_records=execution.pending_changes),
            state_usage=ProviderStateUsage(*(sum(value == kind for value in kinds.values())
                                             for kind in ("reused", "refreshed", "new"))),
            diagnostics=tuple(diagnostics), saved_dois=frozenset(saved),
        )


def _materialize_canonical_result(
    core: _CanonicalCoreResult,
    output_dir: Path | None,
    *,
    progress_callback: ProgressCallback | None = None,
    operation_lock: WorkspaceOperationLock | None = None,
) -> RunResult:
    if core.outcome is RunOutcome.INVALID_CONFIGURATION:
        return RunResult(
            resolved_date_range=core.resolved_date_range,
            canonical_paper_count=len(core.papers),
            created_papers=0,
            matched_existing_papers=0,
            created_authors=0,
            existing_authors=0,
            warnings=core.warnings,
            errors=core.errors,
            outcome=RunOutcome.INVALID_CONFIGURATION,
            statistics=core.statistics,
            coverage=core.coverage,
            state_usage=core.state_usage,
            diagnostics=core.diagnostics,
            resolved_sources=core.resolved_sources,
            log_level=core.log_level,
        )

    assert output_dir is not None
    _emit_progress(progress_callback, ProgressStage.UPDATING_WORKSPACE)
    # Direct materialization callers still acquire their own lock; Run reuses its
    # whole-operation ownership instead of recursively acquiring a second flock.
    if operation_lock is None:
        materialization = materialize_papers(core.papers, output_dir, progress_callback=progress_callback)
    else:
        materialization = materialize_papers(
            core.papers, output_dir, progress_callback=progress_callback, operation_lock=operation_lock,
        )
    materialization_issues = tuple(
        _materialization_issue(issue) for issue in materialization.issues
    )
    warnings, errors = _split_issues(
        (*core.warnings, *core.errors, *materialization_issues)
    )
    return RunResult(
        resolved_date_range=core.resolved_date_range,
        canonical_paper_count=len(core.papers),
        created_papers=len(materialization.created_papers),
        matched_existing_papers=len(materialization.existing_papers),
        created_authors=len(materialization.created_authors),
        existing_authors=len(materialization.existing_authors),
        warnings=warnings,
        errors=errors,
        outcome=_run_outcome(warnings, errors),
        statistics=replace(
            core.statistics,
            materialization_issues=len(materialization.issues),
        ),
        coverage=core.coverage,
        state_usage=core.state_usage,
        diagnostics=core.diagnostics,
        resolved_sources=core.resolved_sources,
        log_level=core.log_level,
    )


def run_monitor(
    config_path: Path,
    *,
    progress_callback: ProgressCallback | None = None,
    operation_lock: WorkspaceOperationLock | None = None,
    loaded_config: LoadedConfig | None = None,
) -> RunResult:
    try:
        configured = loaded_config if loaded_config is not None else load_config(config_path)
    except ConfigurationError as error:
        _emit_progress(progress_callback, ProgressStage.CHECKING_MONITOR)
        return _materialize_canonical_result(
            _invalid_core_result(None, _configuration_issue(error, config_path)), None,
        )
    if operation_lock is not None:
        operation_lock.verify()
        if configured.output_dir != operation_lock.path:
            raise ContentChangedError("Run configuration differs from reserved Workspace")
        return _run_monitor_owned(config_path, progress_callback=progress_callback, operation_lock=operation_lock,
                                  loaded_config=configured)
    # Run 从网络发现开始排他，不能让 Reset 趁物化之前删除工作区。
    with workspace_operation_lock(configured.output_dir, create=True) as lock:
        return _run_monitor_owned(config_path, progress_callback=progress_callback, operation_lock=lock,
                                  loaded_config=configured)


def _run_monitor_owned(
    config_path: Path,
    *,
    loaded_config: LoadedConfig,
    progress_callback: ProgressCallback | None = None,
    operation_lock: WorkspaceOperationLock,
) -> RunResult:
    _emit_progress(progress_callback, ProgressStage.CHECKING_MONITOR)
    prepared, config, preflight_issue = _prepare_invocation(
        config_path, loaded_config=loaded_config,
    )
    if preflight_issue is not None:
        core = _invalid_core_result(config, preflight_issue)
        return _materialize_canonical_result(core, None)
    assert prepared is not None
    operation_lock.verify()
    if prepared.config.output_dir != operation_lock.path:
        raise ContentChangedError("Run configuration differs from owned Workspace")
    state_read = read_provider_state(prepared.config.output_dir)
    state_path = prepared.config.output_dir / METADATA_DIRECTORY_NAME / STATE_FILENAME
    state_issues = ()
    if state_read.status is ProviderStateStatus.INVALID:
        state_issues = (MonitorIssue(
            MonitorIssueSeverity.WARNING, MonitorIssueComponent.PROVIDER_STATE,
            "read", f"invalid Provider state; running live: {state_read.error}", path=state_path,
        ),)
    identity = read_workspace_identities(operation_lock)
    core = _execute_canonical_core(
        prepared, config_path, identity=identity, historical_state=state_read.state or ProviderState(),
        state_issues=state_issues, progress_callback=progress_callback,
    )
    output_dir = prepared.config.output_dir
    final_identity = read_workspace_identities(operation_lock)
    confirmed_saved = {doi for doi in core.saved_dois
                       if final_identity.complete and final_identity.saved.get(doi) == identity.saved.get(doi)
                       and doi in final_identity.saved}
    changed = core.saved_dois - confirmed_saved
    if changed:
        errors = (*core.errors, *(MonitorIssue(MonitorIssueSeverity.ERROR,
            MonitorIssueComponent.MATERIALIZATION, "identity_changed",
            "Saved Paper identity changed during Run", doi=doi) for doi in sorted(changed)))
        core = replace(core, errors=errors, outcome=_run_outcome(core.warnings, errors))
    result = _materialize_canonical_result(
        core,
        output_dir,
        progress_callback=progress_callback,
        operation_lock=operation_lock,
    )
    result = replace(result, matched_existing_papers=result.matched_existing_papers + len(confirmed_saved))
    if result.outcome is RunOutcome.INVALID_CONFIGURATION:
        return result

    assert output_dir is not None
    assert result.resolved_date_range is not None
    operation_lock.verify()
    try:
        if state_read.status is ProviderStateStatus.INVALID:
            if not state_read.replaceable:
                raise OSError("Provider-state path cannot be safely replaced")
            replace_invalid_provider_state(output_dir, core.pending_state)
        else:
            update_provider_state(output_dir, core.pending_state)
    except (OSError, sqlite3.DatabaseError, ValueError, TypeError) as error:
        warning = MonitorIssue(
            severity=MonitorIssueSeverity.WARNING,
            component=MonitorIssueComponent.PROVIDER_STATE,
            stage="persistence",
            message=f"failed to persist Provider state: {error}",
            path=state_path,
        )
        warnings = (*result.warnings, warning)
        result = replace(
            result,
            warnings=warnings,
            outcome=_run_outcome(warnings, result.errors),
        )
    snapshot = LastRunSnapshot(
        schema_version=LAST_RUN_SCHEMA_VERSION,
        resolved_date_range=result.resolved_date_range,
        outcome=RecordedRunOutcome(result.outcome.value),
        coverage=result.coverage,
        reused_units=(),
    )
    try:
        operation_lock.verify()
        write_last_run_snapshot(output_dir, snapshot)
    except LastRunSnapshotWriteError as error:
        warning = _coverage_snapshot_issue(error)
        warnings = (*result.warnings, warning)
        return replace(
            result,
            warnings=warnings,
            outcome=_run_outcome(warnings, result.errors),
        )
    return result
