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
    scope_dispute_diagnostic,
)
from literature_monitor.application.crossref_retrieval import CrossrefRetrieval
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
from literature_monitor.diagnostics import RunDiagnostic, RunDiagnosticSummary, summarize_run_diagnostics
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
    DateRangeSpec,
    ResolvedDateRange,
)
from literature_monitor.keywords import (
    KeywordExpression,
    KeywordSyntaxError,
    parse_keyword_expression,
)
from literature_monitor.materialize import (
    MaterializationIssue,
    MaterializationIssueSeverity,
    materialize_papers,
)
from literature_monitor.models import CanonicalPaper
from literature_monitor.progress import (
    ActivityKind,
    ActivityUpdate,
    ProgressCallback,
    ProgressEvent,
    ProgressStage,
)
from literature_monitor.openalex import (
    DiscoveryIssue,
    IssueSeverity,
    OpenAlexClient,
    ResolvedSource,
    discover_journals_batched,
    resolve_journal_sources_batched,
)
from literature_monitor.retrieval import assemble_live_provider_evidence
from literature_monitor.search import (
    SearchBackendError,
    SearchExpressionError,
    build_searchable_projection,
    match_searchable_projections,
    validate_search_expression,
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
    CANDIDATE_ELIGIBILITY = "candidate_eligibility"
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


class ValidationOutcome(str, Enum):
    VALID = "VALID"
    VALID_WITH_WARNINGS = "VALID_WITH_WARNINGS"
    SOURCE_ERRORS = "SOURCE_ERRORS"
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
    updated_papers: int
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
class ValidationResult:
    resolved_date_range: ResolvedDateRange | None
    configured_journal_count: int
    configured_issn_count: int
    resolved_sources: tuple[ResolvedSource, ...]
    warnings: tuple[MonitorIssue, ...]
    errors: tuple[MonitorIssue, ...]
    outcome: ValidationOutcome
    log_level: LogLevel | None = None


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


def _emit_activity(
    callback: ProgressCallback | None,
    activity: ActivityUpdate,
) -> None:
    if callback is not None:
        callback(ProgressEvent(activity=activity))


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
    config_path: Path,
    *,
    date_override: DateRangeSpec | None,
    journal_name: str | None,
    keyword_expression: str | None,
) -> tuple[_PreparedInvocation | None, LoadedConfig | None, MonitorIssue | None]:
    try:
        config = load_config(config_path)
    except ConfigurationError as error:
        return None, None, _configuration_issue(error, config_path)

    date_spec = date_override if date_override is not None else config.date_spec
    try:
        resolved_date_range = resolve_runtime_date(
            config,
            today=date.today(),
            date_spec=date_spec,
        )
    except DateRangeError as error:
        return (
            None,
            config,
            _date_issue(
                error,
                config_path=config_path,
                stage="date_override" if date_override is not None else "configured_date",
            ),
        )

    journals = config.journals
    if journal_name is not None:
        journals = tuple(
            journal
            for journal in journals
            if journal.name.casefold() == journal_name.strip().casefold()
        )
        if not journals:
            return (
                None,
                config,
                MonitorIssue(
                    severity=MonitorIssueSeverity.ERROR,
                    component=MonitorIssueComponent.CONFIGURATION,
                    stage="journal_override",
                    message=f"unknown configured journal {journal_name!r}",
                    config_path=config_path,
                ),
            )

    try:
        validate_runtime_keyword(config)
    except SearchExpressionError as error:
        return (
            None,
            config,
            _search_issue(error, config_path=config_path, stage="configured_keyword"),
        )
    except SearchBackendError as error:
        return None, config, _search_issue(
            error,
            config_path=config_path,
            stage="fts5_backend",
        )

    keyword_ast = config.keyword_ast
    if keyword_expression is not None:
        try:
            keyword_ast = parse_keyword_expression(keyword_expression)
        except KeywordSyntaxError as error:
            return (
                None,
                config,
                _search_issue(error, config_path=config_path, stage="keyword_override"),
            )
        try:
            validate_search_expression(keyword_ast)
        except SearchExpressionError as error:
            return (
                None,
                config,
                _search_issue(error, config_path=config_path, stage="keyword_override"),
            )
        except SearchBackendError as error:
            return None, config, _search_issue(
                error,
                config_path=config_path,
                stage="fts5_backend",
            )

    return (
        _PreparedInvocation(
            config=config,
            keyword_ast=keyword_ast,
            resolved_date_range=resolved_date_range,
            journals=tuple(journals),
        ),
        config,
        None,
    )


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


def _run_canonical_core(
    config_path: Path,
    *,
    date_override: DateRangeSpec | None = None,
    journal_name: str | None = None,
    keyword_expression: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> _CanonicalCoreResult:
    _emit_progress(progress_callback, ProgressStage.CHECKING_MONITOR)
    prepared, config, preflight_issue = _prepare_invocation(
        config_path,
        date_override=date_override,
        journal_name=journal_name,
        keyword_expression=keyword_expression,
    )
    if preflight_issue is not None:
        return _invalid_core_result(config, preflight_issue)
    assert prepared is not None

    return _execute_canonical_core(prepared, config_path, progress_callback=progress_callback)


def _execute_canonical_core(
    prepared: _PreparedInvocation,
    config_path: Path,
    *,
    historical_state: ProviderState = ProviderState(),
    state_issues: tuple[MonitorIssue, ...] = (),
    progress_callback: ProgressCallback | None = None,
) -> _CanonicalCoreResult:
    _emit_progress(progress_callback, ProgressStage.DISCOVERING_PAPERS)
    with (
        CrossrefClient(mailto=os.environ.get("CROSSREF_MAILTO")) as crossref_client,
        OpenAlexClient(api_key=os.environ.get("OPENALEX_API_KEY")) as openalex_client,
    ):
        crossref_execution = CrossrefRetrieval(
            crossref_client, record_state=historical_state.crossref_records,
            progress_callback=progress_callback,
        )
        # Join inside client ownership so even a future exception drains workers first.
        with ThreadPoolExecutor(max_workers=2) as executor:
            openalex_future = executor.submit(
                discover_journals_batched,
                openalex_client, prepared.journals,
                prepared.resolved_date_range.from_date,
                prepared.resolved_date_range.to_date,
                progress_callback=progress_callback,
            )
            crossref_future = executor.submit(
                crossref_execution.discover,
                prepared.journals, prepared.resolved_date_range.from_date,
                prepared.resolved_date_range.to_date,
            )
            openalex = openalex_future.result()
            discovery = crossref_future.result()
        crossref = discovery.discovery
        _emit_progress(progress_callback, ProgressStage.COMBINING_METADATA)
        retrieval = crossref_execution.supplement(openalex.records)
        candidates = filter_candidate_evidence(openalex, crossref, retrieval, prepared.journals)
        diagnostics = list(candidates.diagnostics)
        evidence = assemble_live_provider_evidence(
            candidates.openalex_records, candidates.crossref_records, candidates.supplement_evidence,
            monitor_journal_issns=candidates.monitor_journal_issns,
        )
        coverage = (*openalex.coverage, *crossref.coverage, *retrieval.coverage)
        _emit_activity(
            progress_callback,
            ActivityUpdate(
                kind=ActivityKind.WORKING,
                source="application",
                operation="consolidate_evidence",
                label="Consolidating provider evidence",
                detail=f"{len(evidence)} evidence records",
            ),
        )
        consolidation = consolidate_evidence(evidence)
        diagnostics.extend(consolidation.diagnostics)
        _emit_activity(
            progress_callback,
            ActivityUpdate(
                kind=ActivityKind.WORKING,
                source="application",
                operation="consolidate_evidence",
                label="Completed provider evidence consolidation",
                detail=f"{len(consolidation.clusters)} candidate works",
            ),
        )

        issues: list[MonitorIssue] = [
            *state_issues,
            *(_openalex_issue(issue) for issue in openalex.issues),
            *(_crossref_discovery_issue(issue) for issue in crossref.issues),
            *(_enrichment_issue(issue) for issue in retrieval.issues),
            *(
                _canonicalization_issue(
                    issue,
                    component=MonitorIssueComponent.CONSOLIDATION,
                )
                for issue in consolidation.issues
            ),
        ]

        _emit_progress(progress_callback, ProgressStage.MATCHING_LITERATURE)
        searchable_clusters = []
        for cluster in consolidation.clusters:
            eligibility = candidates.cluster_eligibility(cluster.evidence)
            projection = build_searchable_projection(cluster.evidence)
            if projection.titles or projection.author_keywords or projection.abstracts:
                searchable_clusters.append((cluster, projection, eligibility))
            else:
                if eligibility.has_eligible:
                    issues.append(
                        MonitorIssue(
                            severity=MonitorIssueSeverity.WARNING,
                            component=MonitorIssueComponent.SEARCH,
                            stage="unsearchable",
                            message="consolidated evidence has no searchable title, author keywords, or abstract",
                            record_ids=tuple(sorted({
                                record.provenance.record_id for record in cluster.evidence
                            })),
                        )
                    )
                if eligibility.has_disputed:
                    diagnostics.append(scope_dispute_diagnostic(cluster.evidence))
        projections = tuple(
            projection for _cluster, projection, _eligibility in searchable_clusters
        )
        _emit_activity(
            progress_callback,
            ActivityUpdate(
                kind=ActivityKind.WORKING,
                source="application",
                operation="matching_literature",
                label="Matching literature",
                detail=f"{len(projections)} candidate works",
            ),
        )
        try:
            matches = match_searchable_projections(prepared.keyword_ast, projections)
        except SearchExpressionError as error:
            issue = _search_issue(
                error,
                config_path=config_path,
                stage="matching",
            )
            warnings, errors = _split_issues((*issues, issue))
            return _CanonicalCoreResult(
                config=prepared.config,
                resolved_date_range=prepared.resolved_date_range,
                papers=(),
                resolved_sources=openalex.sources,
                warnings=warnings,
                errors=errors,
                outcome=RunOutcome.INVALID_CONFIGURATION,
                statistics=MonitorStatistics(
                    openalex_records=len(openalex.records),
                    crossref_discovery_records=len(crossref.records),
                    crossref_supplement_records=len(retrieval.records),
                    evidence_clusters=len(consolidation.clusters),
                    consolidation_issues=len(consolidation.issues),
                    provider_issues=(
                        len(openalex.issues)
                        + len(crossref.issues)
                        + len(retrieval.issues)
                    ),
                ),
                coverage=coverage,
                diagnostics=tuple(diagnostics),
            )
        except SearchBackendError as error:
            issue = _search_issue(
                error,
                config_path=config_path,
                stage="fts5_backend",
            )
            warnings, errors = _split_issues((*issues, issue))
            return _CanonicalCoreResult(
                config=prepared.config,
                resolved_date_range=prepared.resolved_date_range,
                papers=(),
                resolved_sources=openalex.sources,
                warnings=warnings,
                errors=errors,
                outcome=RunOutcome.INVALID_CONFIGURATION,
                statistics=MonitorStatistics(
                    openalex_records=len(openalex.records),
                    crossref_discovery_records=len(crossref.records),
                    crossref_supplement_records=len(retrieval.records),
                    evidence_clusters=len(consolidation.clusters),
                    consolidation_issues=len(consolidation.issues),
                    provider_issues=(
                        len(openalex.issues)
                        + len(crossref.issues)
                        + len(retrieval.issues)
                    ),
                ),
                coverage=coverage,
                diagnostics=tuple(diagnostics),
            )

        _emit_activity(
            progress_callback,
            ActivityUpdate(
                kind=ActivityKind.WORKING,
                source="application",
                operation="matching_literature",
                label="Completed literature matching",
                detail=f"{sum(matches)} matched clusters",
            ),
        )
        retained_clusters = []
        for (cluster, _projection, eligibility), matched in zip(searchable_clusters, matches, strict=True):
            if eligibility.has_disputed:
                if matched and not eligibility.has_strong_eligible:
                    issues.append(MonitorIssue(
                        severity=MonitorIssueSeverity.WARNING,
                        component=MonitorIssueComponent.CANDIDATE_ELIGIBILITY,
                        stage="scope_dispute",
                        message="topic-matched candidate scope is unresolved without strong eligible evidence",
                        record_ids=tuple(sorted({record.provenance.record_id for record in cluster.evidence})),
                    ))
                else:
                    diagnostics.append(scope_dispute_diagnostic(cluster.evidence))
            if matched:
                retained_clusters.append(cluster)
        retained_evidence = tuple(
            evidence
            for cluster in retained_clusters
            for evidence in cluster.evidence
        )
        _emit_activity(
            progress_callback,
            ActivityUpdate(
                kind=ActivityKind.WORKING,
                source="application",
                operation="canonicalize_literature",
                label="Canonicalizing literature",
                detail=f"{len(retained_clusters)} matched clusters",
            ),
        )
        crossref_kinds = {
            doi: kind
            for result in (discovery, retrieval)
            for kind, dois in (("reused", result.reused_dois),
                               ("refreshed", result.refreshed_dois), ("new", result.new_dois))
            for doi in dois
        }
        usage = ProviderStateUsage(
            crossref_reused=sum(kind == "reused" for kind in crossref_kinds.values()),
            crossref_refreshed=sum(kind == "refreshed" for kind in crossref_kinds.values()),
            crossref_new=sum(kind == "new" for kind in crossref_kinds.values()),
        )
        canonicalization = canonicalize_records(retained_evidence)
        _emit_activity(
            progress_callback,
            ActivityUpdate(
                kind=ActivityKind.WORKING,
                source="application",
                operation="canonicalize_literature",
                label="Completed literature canonicalization",
                detail=f"{len(canonicalization.papers)} canonical papers",
            ),
        )
        issues.extend(
            _canonicalization_issue(
                issue,
                component=MonitorIssueComponent.CANONICALIZATION,
            )
            for issue in canonicalization.issues
        )
        warnings, errors = _split_issues(issues)
        statistics = MonitorStatistics(
            openalex_records=len(openalex.records),
            crossref_discovery_records=len(crossref.records),
            crossref_supplement_records=len(retrieval.records),
            evidence_clusters=len(consolidation.clusters),
            retained_clusters=len(retained_clusters),
            consolidation_issues=len(consolidation.issues),
            canonicalization_issues=len(canonicalization.issues),
            provider_issues=(
                len(openalex.issues)
                + len(crossref.issues)
                + len(retrieval.issues)
            ),
        )
        return _CanonicalCoreResult(
            config=prepared.config,
            resolved_date_range=prepared.resolved_date_range,
            papers=canonicalization.papers,
            resolved_sources=openalex.sources,
            warnings=warnings,
            errors=errors,
            outcome=_run_outcome(warnings, errors),
            statistics=statistics,
            coverage=coverage,
            pending_state=ProviderState(crossref_records=crossref_execution.pending_changes),
            state_usage=usage,
            diagnostics=tuple(diagnostics),
        )


def _materialize_canonical_result(
    core: _CanonicalCoreResult,
    output_dir: Path | None,
    *,
    progress_callback: ProgressCallback | None = None,
) -> RunResult:
    if core.outcome is RunOutcome.INVALID_CONFIGURATION:
        return RunResult(
            resolved_date_range=core.resolved_date_range,
            canonical_paper_count=len(core.papers),
            created_papers=0,
            matched_existing_papers=0,
            updated_papers=0,
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
    materialization = materialize_papers(
        core.papers,
        output_dir,
        progress_callback=progress_callback,
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
        updated_papers=len(materialization.updated_papers),
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
    date_override: DateRangeSpec | None = None,
    progress_callback: ProgressCallback | None = None,
) -> RunResult:
    _emit_progress(progress_callback, ProgressStage.CHECKING_MONITOR)
    prepared, config, preflight_issue = _prepare_invocation(
        config_path, date_override=date_override, journal_name=None, keyword_expression=None,
    )
    if preflight_issue is not None:
        core = _invalid_core_result(config, preflight_issue)
        return _materialize_canonical_result(core, None)
    assert prepared is not None
    state_read = read_provider_state(prepared.config.output_dir)
    state_path = prepared.config.output_dir / METADATA_DIRECTORY_NAME / STATE_FILENAME
    state_issues = ()
    if state_read.status is ProviderStateStatus.INVALID:
        state_issues = (MonitorIssue(
            MonitorIssueSeverity.WARNING, MonitorIssueComponent.PROVIDER_STATE,
            "read", f"invalid Provider state; running live: {state_read.error}", path=state_path,
        ),)
    core = _execute_canonical_core(
        prepared, config_path, historical_state=state_read.state or ProviderState(),
        state_issues=state_issues, progress_callback=progress_callback,
    )
    output_dir = core.config.output_dir if core.config is not None else None
    result = _materialize_canonical_result(
        core,
        output_dir,
        progress_callback=progress_callback,
    )
    if result.outcome is RunOutcome.INVALID_CONFIGURATION:
        return result

    assert output_dir is not None
    assert result.resolved_date_range is not None
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


def validate_monitor(
    config_path: Path,
    *,
    progress_callback: ProgressCallback | None = None,
) -> ValidationResult:
    _emit_activity(
        progress_callback,
        ActivityUpdate(
            kind=ActivityKind.WORKING,
            source="application",
            operation="validation_preflight",
            label="Checking monitor configuration",
        ),
    )
    prepared, config, preflight_issue = _prepare_invocation(
        config_path,
        date_override=None,
        journal_name=None,
        keyword_expression=None,
    )
    if preflight_issue is not None:
        return ValidationResult(
            resolved_date_range=None,
            configured_journal_count=len(config.journals) if config is not None else 0,
            configured_issn_count=(
                sum(len(journal.issn) for journal in config.journals)
                if config is not None
                else 0
            ),
            resolved_sources=(),
            warnings=(),
            errors=(preflight_issue,),
            outcome=ValidationOutcome.INVALID_CONFIGURATION,
            log_level=config.log_level if config is not None else None,
        )
    assert prepared is not None

    _emit_activity(
        progress_callback,
        ActivityUpdate(
            kind=ActivityKind.WORKING,
            source="application",
            operation="validation_preflight",
            label="Completed monitor configuration check",
        ),
    )
    with OpenAlexClient(api_key=os.environ.get("OPENALEX_API_KEY")) as client:
        sources: list[ResolvedSource] = []
        issues: list[MonitorIssue] = []
        journal_activity = ActivityUpdate(
            kind=ActivityKind.WORKING, source="application", operation="validation_journals",
            label="Resolving journal sources", current=0, total=len(prepared.journals), unit="journal",
        )
        _emit_activity(progress_callback, journal_activity)
        for index, unit in enumerate(resolve_journal_sources_batched(
            client, prepared.journals, progress_callback=progress_callback,
        )):
            if unit.source is not None:
                sources.append(unit.source)
            issues.extend(_openalex_issue(issue) for issue in unit.issues)
            _emit_activity(progress_callback, replace(journal_activity, detail=unit.journal.name, current=index + 1))
    warnings, errors = _split_issues(issues)
    outcome = (
        ValidationOutcome.SOURCE_ERRORS
        if errors
        else ValidationOutcome.VALID_WITH_WARNINGS
        if warnings
        else ValidationOutcome.VALID
    )
    return ValidationResult(
        resolved_date_range=prepared.resolved_date_range,
        configured_journal_count=len(prepared.config.journals),
        configured_issn_count=sum(
            len(journal.issn) for journal in prepared.config.journals
        ),
        resolved_sources=tuple(sources),
        warnings=warnings,
        errors=errors,
        outcome=outcome,
        log_level=prepared.config.log_level,
    )
