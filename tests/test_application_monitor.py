from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from literature_monitor.application import monitor
from literature_monitor.application.monitor import (
    MonitorIssueComponent,
    MonitorStatistics,
    ProgressStage,
    RunOutcome,
    ValidationOutcome,
    _materialize_canonical_result,
    _run_canonical_core,
    run_monitor,
    validate_monitor,
)
from literature_monitor.canonicalize import (
    CanonicalizationIssue,
    CanonicalizationResult,
    EvidenceCluster,
    EvidenceConsolidationResult,
)
from literature_monitor.config import JournalConfig, load_config
from literature_monitor.crossref import (
    CrossrefDiscoveryIssue,
    CrossrefDiscoveryResult,
    EnrichmentIssue,
    EnrichmentIssueSeverity,
)
from literature_monitor.date_range import DateRangeSpec
from literature_monitor.materialize import (
    MaterializationIssue,
    MaterializationIssueSeverity,
    MaterializationResult,
)
from literature_monitor.models import (
    Author,
    CanonicalMetadata,
    CanonicalPaper,
    ExternalIds,
    PaperVersion,
    VersionKind,
    VersionRef,
)
from literature_monitor.openalex import (
    DiscoveryIssue,
    DiscoveryResult,
    IssueSeverity,
    OpenAlexClient,
    ResolvedSource,
)
from literature_monitor.progress import (
    ActivityKind,
    ActivityUpdate,
    ProgressCallback,
    ProgressEvent,
)
from literature_monitor.retrieval import EvidenceRetrievalResult
from literature_monitor.search import SearchBackendError, SearchExpressionError, SearchableProjection


class FixedDate(date):
    @classmethod
    def today(cls) -> date:
        return cls(2026, 9, 21)


def write_monitor(
    tmp_path: Path,
    *,
    date_policy: str = "from_date: 2026-01-01\nto_date: 2026-01-31\n",
    keyword_expression: str = "statistics",
    output_dir: str = "workspace",
) -> Path:
    whitelist = tmp_path / "journals.md"
    whitelist.write_text(
        "# List\n\n"
        "## Journals\n\n"
        "| Journal | ISSN/EISSN |\n"
        "|---|---|\n"
        "| Biometrics | 0006-341X / 1541-0420 |\n"
        "| Annals of Statistics | 0090-5364 |\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "monitor.yaml"
    config_path.write_text(
        "venue_whitelist: journals.md\n"
        f"keyword_expression: {keyword_expression!r}\n"
        f"output_dir: {output_dir}\n"
        "log_level: INFO\n"
        f"{date_policy}",
        encoding="utf-8",
    )
    return config_path


def canonical_paper() -> CanonicalPaper:
    version = PaperVersion(
        kind=VersionKind.JOURNAL_FINAL,
        source="doi",
        identifier="10.5555/paper",
    )
    return CanonicalPaper(
        metadata=CanonicalMetadata(title="Canonical paper", journal="Biometrics"),
        external_ids=ExternalIds(doi="10.5555/paper"),
        authors=(Author(name="Ada Author"),),
        versions=(version,),
        preferred_version=VersionRef(source="doi", identifier="10.5555/paper"),
    )


def resolved_source(journal: JournalConfig) -> ResolvedSource:
    return ResolvedSource(
        journal=journal.name,
        configured_issns=journal.issn,
        resolved_issns=journal.issn,
        unresolved_issns=(),
        openalex_id=f"https://openalex.org/S-{journal.name.replace(' ', '-')}",
        display_name=journal.name,
        issn_l=journal.issn[0],
        issn=journal.issn,
    )


def install_core_mocks(
    monkeypatch: pytest.MonkeyPatch,
    *,
    events: list[str] | None = None,
    ranges: list[tuple[str, date, date]] | None = None,
    journal_batches: list[tuple[str, ...]] | None = None,
    expressions: list[object] | None = None,
    openalex_issues: tuple[DiscoveryIssue, ...] = (),
    crossref_issues: tuple[CrossrefDiscoveryIssue, ...] = (),
    retrieval_issues: tuple[EnrichmentIssue, ...] = (),
    consolidation_issues: tuple[CanonicalizationIssue, ...] = (),
    canonicalization_issues: tuple[CanonicalizationIssue, ...] = (),
    progress_callbacks: list[ProgressCallback | None] | None = None,
    emit_provider_activity: bool = False,
) -> CanonicalPaper:
    oa_record = object()
    cr_record = object()
    openalex_evidence = object()
    crossref_evidence = object()
    paper = canonical_paper()

    monkeypatch.setattr(monitor, "OpenAlexClient", lambda **kwargs: object())
    monkeypatch.setattr(monitor, "CrossrefClient", lambda **kwargs: object())

    def discover_openalex(
        client: object,
        journals: tuple[JournalConfig, ...],
        from_date: date,
        to_date: date,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> DiscoveryResult:
        if progress_callbacks is not None:
            progress_callbacks.append(progress_callback)
        if emit_provider_activity and progress_callback is not None:
            progress_callback(
                ProgressEvent(
                    activity=ActivityUpdate(
                        kind=ActivityKind.WORKING,
                        source="openalex",
                        operation="test_openalex",
                        label="OpenAlex activity",
                    )
                )
            )
        if events is not None:
            events.append("openalex")
        if ranges is not None:
            ranges.append(("openalex", from_date, to_date))
        if journal_batches is not None:
            journal_batches.append(tuple(journal.name for journal in journals))
        return DiscoveryResult(
            sources=tuple(resolved_source(journal) for journal in journals),
            records=(oa_record,),  # type: ignore[arg-type]
            issues=openalex_issues,
        )

    def discover_crossref(
        client: object,
        journals: tuple[JournalConfig, ...],
        from_date: date,
        to_date: date,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> CrossrefDiscoveryResult:
        if progress_callbacks is not None:
            progress_callbacks.append(progress_callback)
        if emit_provider_activity and progress_callback is not None:
            progress_callback(
                ProgressEvent(
                    activity=ActivityUpdate(
                        kind=ActivityKind.WORKING,
                        source="crossref",
                        operation="test_crossref_discovery",
                        label="Crossref discovery activity",
                    )
                )
            )
        if events is not None:
            events.append("crossref")
        if ranges is not None:
            ranges.append(("crossref", from_date, to_date))
        return CrossrefDiscoveryResult(
            records=(cr_record,),  # type: ignore[arg-type]
            issues=crossref_issues,
        )

    def assemble(
        client: object,
        openalex_records: tuple[object, ...],
        crossref_records: tuple[object, ...],
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> EvidenceRetrievalResult:
        if progress_callbacks is not None:
            progress_callbacks.append(progress_callback)
        if emit_provider_activity and progress_callback is not None:
            progress_callback(
                ProgressEvent(
                    activity=ActivityUpdate(
                        kind=ActivityKind.WORKING,
                        source="crossref",
                        operation="test_crossref_supplement",
                        label="Crossref supplement activity",
                    )
                )
            )
        if events is not None:
            events.append("supplement")
        assert openalex_records == (oa_record,)
        assert crossref_records == (cr_record,)
        return EvidenceRetrievalResult(
            evidence=(openalex_evidence, crossref_evidence),  # type: ignore[arg-type]
            supplement_records=(cr_record,),  # type: ignore[arg-type]
            issues=retrieval_issues,
        )

    def consolidate(evidence: tuple[object, ...]) -> EvidenceConsolidationResult:
        if events is not None:
            events.append("consolidate")
        assert evidence == (openalex_evidence, crossref_evidence)
        return EvidenceConsolidationResult(
            clusters=(
                EvidenceCluster(evidence=(openalex_evidence,)),  # type: ignore[arg-type]
                EvidenceCluster(evidence=(crossref_evidence,)),  # type: ignore[arg-type]
            ),
            issues=consolidation_issues,
        )

    def build_projection(evidence: tuple[object, ...]) -> SearchableProjection:
        return SearchableProjection(titles=(str(id(evidence[0])),))

    def match(expression: object, projections: tuple[SearchableProjection, ...]) -> tuple[bool, ...]:
        if events is not None:
            events.append("match")
        if expressions is not None:
            expressions.append(expression)
        assert len(projections) == 2
        return (True, False)

    def canonicalize(evidence: tuple[object, ...]) -> CanonicalizationResult:
        if events is not None:
            events.append("canonicalize")
        assert evidence == (openalex_evidence,)
        return CanonicalizationResult(
            papers=(paper,),
            issues=canonicalization_issues,
        )

    monkeypatch.setattr(monitor, "discover_journals", discover_openalex)
    monkeypatch.setattr(monitor, "discover_crossref_journals", discover_crossref)
    monkeypatch.setattr(monitor, "assemble_provider_evidence", assemble)
    monkeypatch.setattr(monitor, "consolidate_evidence", consolidate)
    monkeypatch.setattr(monitor, "build_searchable_projection", build_projection)
    monkeypatch.setattr(monitor, "match_searchable_projections", match)
    monkeypatch.setattr(monitor, "canonicalize_records", canonicalize)
    return paper


def materialization_result(
    output_dir: Path,
    *,
    issues: tuple[MaterializationIssue, ...] = (),
) -> MaterializationResult:
    return MaterializationResult(
        created_papers=(output_dir / "Papers" / "new.md",),
        existing_papers=(output_dir / "Papers" / "existing.md",),
        updated_papers=(output_dir / "Papers" / "updated.md",),
        created_authors=(output_dir / "Authors" / "new.md",),
        existing_authors=(output_dir / "Authors" / "existing.md",),
        issues=issues,
    )


def test_runtime_contract_has_no_semantic_scholar_specific_issue_or_statistics_surface() -> None:
    assert "SEMANTIC_SCHOLAR" not in MonitorIssueComponent.__members__
    assert "semantic_scholar_supplement_records" not in MonitorStatistics.__dataclass_fields__
    assert "semantic_scholar_discovery_records" not in MonitorStatistics.__dataclass_fields__


def test_canonical_core_preserves_real_orchestration_order_and_stops_before_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = write_monitor(tmp_path)
    events: list[str] = []
    paper = install_core_mocks(monkeypatch, events=events)

    def unexpected_materialize(*args: object) -> object:
        raise AssertionError("canonical core must stop before materialization")

    monkeypatch.setattr(monitor, "materialize_papers", unexpected_materialize)

    result = _run_canonical_core(config_path)

    assert result.outcome is RunOutcome.COMPLETED
    assert result.papers == (paper,)
    assert events == [
        "openalex",
        "crossref",
        "supplement",
        "consolidate",
        "match",
        "canonicalize",
    ]


def test_canonical_core_applies_diagnostic_journal_and_keyword_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = write_monitor(tmp_path, keyword_expression="configured")
    journal_batches: list[tuple[str, ...]] = []
    expressions: list[object] = []
    install_core_mocks(
        monkeypatch,
        journal_batches=journal_batches,
        expressions=expressions,
    )

    result = _run_canonical_core(
        config_path,
        journal_name="Biometrics",
        keyword_expression='"diagnostic phrase"',
    )

    assert result.outcome is RunOutcome.COMPLETED
    assert journal_batches == [("Biometrics",)]
    assert expressions == [monitor.parse_keyword_expression('"diagnostic phrase"')]


def test_materialize_consumes_the_same_canonical_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = write_monitor(tmp_path)
    paper = install_core_mocks(monkeypatch)
    core = _run_canonical_core(config_path)
    output_dir = tmp_path / "explicit"
    received: list[tuple[tuple[CanonicalPaper, ...], Path]] = []
    received_callbacks: list[ProgressCallback | None] = []

    def materialize(
        papers: tuple[CanonicalPaper, ...],
        destination: Path,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> MaterializationResult:
        received.append((papers, destination))
        received_callbacks.append(progress_callback)
        return materialization_result(destination)

    monkeypatch.setattr(monitor, "materialize_papers", materialize)

    progress_events: list[ProgressEvent] = []

    def report(event: ProgressEvent) -> None:
        progress_events.append(event)

    result = _materialize_canonical_result(
        core,
        output_dir,
        progress_callback=report,
    )

    assert received == [((paper,), output_dir)]
    assert received_callbacks == [report]
    assert [event.stage for event in progress_events if event.stage is not None] == [
        ProgressStage.UPDATING_WORKSPACE
    ]
    assert result.canonical_paper_count == 1
    assert result.created_papers == 1
    assert result.matched_existing_papers == 1
    assert result.updated_papers == 1
    assert result.created_authors == 1
    assert result.existing_authors == 1


def test_run_monitor_uses_core_materialization_and_progress_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = write_monitor(tmp_path, output_dir="run-workspace")
    events: list[str] = []
    install_core_mocks(monkeypatch, events=events)
    progress_events: list[ProgressEvent] = []

    def materialize(
        papers: tuple[CanonicalPaper, ...],
        destination: Path,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> MaterializationResult:
        events.append("materialize")
        assert destination == (tmp_path / "run-workspace").resolve()
        assert progress_callback is not None
        progress_callback(
            ProgressEvent(
                activity=ActivityUpdate(
                    kind=ActivityKind.WORKING,
                    source="workspace",
                    operation="test_materialize",
                    label="Materialization activity",
                )
            )
        )
        return materialization_result(destination)

    monkeypatch.setattr(monitor, "materialize_papers", materialize)

    result = run_monitor(config_path, progress_callback=progress_events.append)

    assert result.outcome is RunOutcome.COMPLETED
    assert [event.stage for event in progress_events if event.stage is not None] == [
        ProgressStage.CHECKING_MONITOR,
        ProgressStage.DISCOVERING_PAPERS,
        ProgressStage.COMBINING_METADATA,
        ProgressStage.MATCHING_LITERATURE,
        ProgressStage.UPDATING_WORKSPACE,
    ]
    stage: ProgressStage | None = None
    activity_locations: list[tuple[ProgressStage | None, str]] = []
    for event in progress_events:
        if event.stage is not None:
            stage = event.stage
        if event.activity is not None:
            activity_locations.append((stage, event.activity.operation))
    assert activity_locations == [
        (ProgressStage.COMBINING_METADATA, "consolidate_evidence"),
        (ProgressStage.COMBINING_METADATA, "consolidate_evidence"),
        (ProgressStage.MATCHING_LITERATURE, "matching_literature"),
        (ProgressStage.MATCHING_LITERATURE, "matching_literature"),
        (ProgressStage.MATCHING_LITERATURE, "canonicalize_literature"),
        (ProgressStage.MATCHING_LITERATURE, "canonicalize_literature"),
        (ProgressStage.UPDATING_WORKSPACE, "test_materialize"),
    ]
    assert events[-1] == "materialize"
    assert result.canonical_paper_count == 1
    assert result.created_papers == 1
    assert result.matched_existing_papers == 1
    assert result.updated_papers == 1
    assert result.created_authors == 1
    assert result.existing_authors == 1


def test_canonical_core_does_not_read_or_construct_semantic_scholar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = write_monitor(tmp_path)
    install_core_mocks(monkeypatch)
    requested_environment: list[str] = []

    class TrackingEnvironment(dict[str, str]):
        def get(self, key: str, default: str | None = None) -> str | None:
            requested_environment.append(key)
            return super().get(key, default)

    monkeypatch.setattr(
        monitor.os,
        "environ",
        TrackingEnvironment(dict(monitor.os.environ)),
    )
    progress_events: list[ProgressEvent] = []

    result = _run_canonical_core(
        config_path,
        progress_callback=progress_events.append,
    )

    assert result.outcome is RunOutcome.COMPLETED
    assert requested_environment == ["CROSSREF_MAILTO", "OPENALEX_API_KEY"]
    assert not hasattr(monitor, "create_semantic_scholar_client")
    assert not hasattr(monitor, "augment_with_semantic_scholar")
    assert all(
        event.activity is None or event.activity.source != "semantic_scholar"
        for event in progress_events
    )


def test_run_monitor_passes_one_progress_callback_through_provider_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = write_monitor(tmp_path)
    progress_events: list[ProgressEvent] = []
    provider_callbacks: list[ProgressCallback | None] = []
    install_core_mocks(
        monkeypatch,
        progress_callbacks=provider_callbacks,
        emit_provider_activity=True,
    )
    monkeypatch.setattr(
        monitor,
        "materialize_papers",
        lambda papers, output_dir, **kwargs: materialization_result(output_dir),
    )

    def report(event: ProgressEvent) -> None:
        progress_events.append(event)

    result = run_monitor(config_path, progress_callback=report)

    assert result.outcome is RunOutcome.COMPLETED
    assert provider_callbacks == [report, report, report]
    assert [event.stage for event in progress_events if event.stage is not None] == [
        ProgressStage.CHECKING_MONITOR,
        ProgressStage.DISCOVERING_PAPERS,
        ProgressStage.COMBINING_METADATA,
        ProgressStage.MATCHING_LITERATURE,
        ProgressStage.UPDATING_WORKSPACE,
    ]
    assert [
        (
            event.activity.source,
            event.activity.operation,
        )
        for event in progress_events
        if event.activity is not None
        and event.activity.operation.startswith("test_")
    ] == [
        ("openalex", "test_openalex"),
        ("crossref", "test_crossref_discovery"),
        ("crossref", "test_crossref_supplement"),
    ]


@pytest.mark.parametrize(
    ("date_override", "expected"),
    (
        (DateRangeSpec(window_days=30), (date(2026, 8, 23), date(2026, 9, 21))),
        (
            DateRangeSpec(from_date=date(2026, 1, 1), to_date=date(2026, 1, 31)),
            (date(2026, 1, 1), date(2026, 1, 31)),
        ),
        (
            DateRangeSpec(from_date=date(2026, 9, 1), window_days=21),
            (date(2026, 9, 1), date(2026, 9, 21)),
        ),
        (
            DateRangeSpec(to_date=date(2026, 9, 21), window_days=14),
            (date(2026, 9, 8), date(2026, 9, 21)),
        ),
    ),
)
def test_application_date_override_forms_are_complete_and_ephemeral(
    date_override: DateRangeSpec,
    expected: tuple[date, date],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = write_monitor(tmp_path, date_policy="window_days: 7\n")
    ranges: list[tuple[str, date, date]] = []
    install_core_mocks(monkeypatch, ranges=ranges)
    monkeypatch.setattr(monitor, "date", FixedDate)
    monkeypatch.setattr(
        monitor,
        "materialize_papers",
        lambda papers, output_dir, **kwargs: materialization_result(output_dir),
    )

    result = run_monitor(config_path, date_override=date_override)

    assert result.resolved_date_range is not None
    assert (
        result.resolved_date_range.from_date,
        result.resolved_date_range.to_date,
    ) == expected
    assert [(provider, start, end) for provider, start, end in ranges] == [
        ("openalex", *expected),
        ("crossref", *expected),
    ]


def test_persisted_date_policy_is_used_without_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = write_monitor(
        tmp_path,
        date_policy="from_date: 2026-03-01\nto_date: 2026-03-15\n",
    )
    ranges: list[tuple[str, date, date]] = []
    install_core_mocks(monkeypatch, ranges=ranges)
    monkeypatch.setattr(
        monitor,
        "materialize_papers",
        lambda papers, output_dir, **kwargs: materialization_result(output_dir),
    )

    result = run_monitor(config_path)

    assert result.resolved_date_range is not None
    assert result.resolved_date_range.from_date == date(2026, 3, 1)
    assert result.resolved_date_range.to_date == date(2026, 3, 15)


def test_incomplete_override_never_borrows_config_date_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = write_monitor(tmp_path, date_policy="window_days: 14\n")

    def unexpected_provider(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider work must not start")

    monkeypatch.setattr(monitor, "OpenAlexClient", unexpected_provider)
    monkeypatch.setattr(monitor, "CrossrefClient", unexpected_provider)
    monkeypatch.setattr(monitor, "materialize_papers", unexpected_provider)

    result = run_monitor(
        config_path,
        date_override=DateRangeSpec(to_date=date(2026, 9, 1)),
    )

    assert result.outcome is RunOutcome.INVALID_CONFIGURATION
    assert result.errors[0].component is MonitorIssueComponent.DATE_RANGE
    assert "must be combined with another date field" in result.errors[0].message


def test_preflight_failure_occurs_before_provider_work_and_skips_workspace_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = write_monitor(tmp_path)
    progress_events: list[ProgressEvent] = []

    def fail_validation(*args: object) -> None:
        raise SearchBackendError("SQLite FTS5 is unavailable")

    def unexpected_provider(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider work must not start")

    monkeypatch.setattr(monitor, "validate_runtime_keyword", fail_validation)
    monkeypatch.setattr(monitor, "OpenAlexClient", unexpected_provider)

    result = run_monitor(config_path, progress_callback=progress_events.append)

    assert result.outcome is RunOutcome.INVALID_CONFIGURATION
    assert [event.stage for event in progress_events] == [
        ProgressStage.CHECKING_MONITOR
    ]
    assert all(event.activity is None for event in progress_events)


def test_provider_error_still_materializes_successful_papers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = write_monitor(tmp_path)
    install_core_mocks(
        monkeypatch,
        openalex_issues=(
            DiscoveryIssue(
                severity=IssueSeverity.ERROR,
                stage="work_retrieval",
                journal="Biometrics",
                message="provider unavailable",
            ),
        ),
    )
    materialized: list[CanonicalPaper] = []

    def materialize(
        papers: tuple[CanonicalPaper, ...],
        destination: Path,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> MaterializationResult:
        materialized.extend(papers)
        return materialization_result(destination)

    monkeypatch.setattr(monitor, "materialize_papers", materialize)

    result = run_monitor(config_path)

    assert len(materialized) == 1
    assert result.outcome is RunOutcome.COMPLETED_WITH_ERRORS
    assert any(issue.component is MonitorIssueComponent.OPENALEX for issue in result.errors)


def test_provider_warning_is_nonfatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = write_monitor(tmp_path)
    install_core_mocks(
        monkeypatch,
        openalex_issues=(
            DiscoveryIssue(
                severity=IssueSeverity.WARNING,
                stage="record_normalization",
                journal="Biometrics",
                message="provider warning",
            ),
        ),
    )
    monkeypatch.setattr(
        monitor,
        "materialize_papers",
        lambda papers, output_dir, **kwargs: materialization_result(output_dir),
    )

    result = run_monitor(config_path)

    assert result.outcome is RunOutcome.COMPLETED_WITH_WARNINGS
    assert result.errors == ()
    assert any(issue.component is MonitorIssueComponent.OPENALEX for issue in result.warnings)


@pytest.mark.parametrize(
    ("severity", "expected"),
    (
        (MaterializationIssueSeverity.WARNING, RunOutcome.COMPLETED_WITH_WARNINGS),
        (MaterializationIssueSeverity.ERROR, RunOutcome.COMPLETED_WITH_ERRORS),
    ),
)
def test_materialization_issue_controls_structured_run_outcome(
    severity: MaterializationIssueSeverity,
    expected: RunOutcome,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = write_monitor(tmp_path)
    install_core_mocks(monkeypatch)

    def materialize(
        papers: tuple[CanonicalPaper, ...],
        destination: Path,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> MaterializationResult:
        return materialization_result(
            destination,
            issues=(
                MaterializationIssue(
                    destination / "Papers" / "problem.md",
                    "materialization diagnostic",
                    severity,
                ),
            ),
        )

    monkeypatch.setattr(monitor, "materialize_papers", materialize)

    result = run_monitor(config_path)

    assert result.outcome is expected
    issues = result.errors if severity is MaterializationIssueSeverity.ERROR else result.warnings
    assert any(issue.component is MonitorIssueComponent.MATERIALIZATION for issue in issues)


def test_canonicalization_warning_is_nonfatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = write_monitor(tmp_path)
    install_core_mocks(
        monkeypatch,
        canonicalization_issues=(
            CanonicalizationIssue(
                stage="blocked_match",
                message="insufficient evidence",
                record_ids=("W1", "W2"),
            ),
        ),
    )
    monkeypatch.setattr(
        monitor,
        "materialize_papers",
        lambda papers, output_dir, **kwargs: materialization_result(output_dir),
    )

    result = run_monitor(config_path)

    assert result.outcome is RunOutcome.COMPLETED_WITH_WARNINGS
    assert result.errors == ()
    assert result.warnings[0].component is MonitorIssueComponent.CANONICALIZATION


def test_unexpected_programming_exception_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = write_monitor(tmp_path)
    install_core_mocks(monkeypatch)

    def explode(*args: object) -> object:
        raise RuntimeError("programming failure")

    monkeypatch.setattr(monitor, "canonicalize_records", explode)

    with pytest.raises(RuntimeError, match="programming failure"):
        run_monitor(config_path)


def test_validate_monitor_success_and_only_resolves_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = write_monitor(tmp_path)
    config = load_config(config_path)
    calls: list[str] = []
    progress_events: list[ProgressEvent] = []
    source_callbacks: list[ProgressCallback | None] = []

    monkeypatch.setattr(monitor, "OpenAlexClient", lambda **kwargs: object())

    def resolve(
        client: object,
        journal: JournalConfig,
        *,
        progress_callback: ProgressCallback | None = None,
        operation: str | None = None,
    ) -> tuple[ResolvedSource, tuple[DiscoveryIssue, ...]]:
        calls.append(journal.name)
        source_callbacks.append(progress_callback)
        if progress_callback is not None:
            progress_callback(
                ProgressEvent(
                    activity=ActivityUpdate(
                        kind=ActivityKind.WORKING,
                        source="openalex",
                        operation=operation or "source_resolution",
                        label="Requesting OpenAlex",
                    )
                )
            )
        return resolved_source(journal), ()

    monkeypatch.setattr(monitor, "resolve_journal_source", resolve)

    def unexpected(*args: object, **kwargs: object) -> object:
        raise AssertionError("validation must not enter Works or materialization paths")

    for name in (
        "discover_journals",
        "CrossrefClient",
        "discover_crossref_journals",
        "materialize_papers",
    ):
        monkeypatch.setattr(monitor, name, unexpected)

    def report(event: ProgressEvent) -> None:
        progress_events.append(event)

    result = validate_monitor(config_path, progress_callback=report)

    assert result.outcome is ValidationOutcome.VALID
    assert calls == [journal.name for journal in config.journals]
    assert source_callbacks == [report, report]
    assert all(event.stage is None for event in progress_events)
    journal_progress = [
        event.activity
        for event in progress_events
        if event.activity is not None
        and event.activity.operation == "validation_journals"
    ]
    assert [
        (activity.current, activity.total, activity.unit)
        for activity in journal_progress
    ] == [
        (0, 2, "journal"),
        (1, 2, "journal"),
        (2, 2, "journal"),
    ]
    assert [
        event.activity.operation
        for event in progress_events
        if event.activity is not None and event.activity.source == "openalex"
    ] == [
        "validation_source_resolution:0",
        "validation_source_resolution:1",
    ]
    assert result.configured_journal_count == 2
    assert result.configured_issn_count == 3
    assert len(result.resolved_sources) == 2


def test_validate_monitor_reuses_openalex_request_retry_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = write_monitor(tmp_path)
    calls: list[str] = []
    request_count = 0

    class Response:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = json.dumps(payload).encode()

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return self._payload

    def opener(request: object, *, timeout: float) -> Response:
        nonlocal request_count
        request_count += 1
        url = getattr(request, "full_url")
        calls.append(url)
        if request_count == 1:
            raise TimeoutError("simulated transient timeout")
        if "0090-5364" in url:
            return Response(
                {
                    "id": "https://openalex.org/S1234567",
                    "display_name": "Annals of Statistics",
                    "issn_l": "0090-5364",
                    "issn": ["0090-5364"],
                    "type": "journal",
                    "alternate_titles": [],
                    "abbreviated_title": None,
                }
            )
        return Response(
            {
                "id": "https://openalex.org/S8265502",
                "display_name": "Biometrics",
                "issn_l": "0006-341X",
                "issn": ["0006-341X", "1541-0420"],
                "type": "journal",
                "alternate_titles": [],
                "abbreviated_title": None,
            }
        )

    client = OpenAlexClient(opener=opener, sleep=lambda _delay: None)
    monkeypatch.setattr(monitor, "OpenAlexClient", lambda **kwargs: client)

    def unexpected(*args: object, **kwargs: object) -> object:
        raise AssertionError("validation must not expand beyond Source resolution")

    for name in (
        "discover_journals",
        "CrossrefClient",
        "discover_crossref_journals",
        "materialize_papers",
    ):
        monkeypatch.setattr(monitor, name, unexpected)

    progress_events: list[ProgressEvent] = []
    result = validate_monitor(
        config_path,
        progress_callback=progress_events.append,
    )

    assert result.outcome is ValidationOutcome.VALID
    assert request_count == 4
    assert not any("/works" in url for url in calls)
    openalex_activity = [
        event.activity
        for event in progress_events
        if event.activity is not None and event.activity.source == "openalex"
    ]
    assert any(
        activity.kind is ActivityKind.RETRYING
        and activity.operation == "validation_source_resolution:0"
        for activity in openalex_activity
    )
    assert any(
        activity.label == "Requesting OpenAlex"
        for activity in openalex_activity
    )
    assert any(
        activity.label == "Received OpenAlex response"
        for activity in openalex_activity
    )


@pytest.mark.parametrize(
    ("severity", "expected"),
    (
        (IssueSeverity.WARNING, ValidationOutcome.VALID_WITH_WARNINGS),
        (IssueSeverity.ERROR, ValidationOutcome.SOURCE_ERRORS),
    ),
)
def test_validate_monitor_source_issue_classification(
    severity: IssueSeverity,
    expected: ValidationOutcome,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = write_monitor(tmp_path)
    monkeypatch.setattr(monitor, "OpenAlexClient", lambda **kwargs: object())

    def resolve(client: object, journal: JournalConfig) -> tuple[ResolvedSource | None, tuple[DiscoveryIssue, ...]]:
        source = resolved_source(journal) if severity is IssueSeverity.WARNING else None
        return source, (
            DiscoveryIssue(
                severity=severity,
                stage="source_resolution",
                journal=journal.name,
                message="source diagnostic",
            ),
        )

    monkeypatch.setattr(monitor, "resolve_journal_source", resolve)

    result = validate_monitor(config_path)

    assert result.outcome is expected
    target = result.warnings if severity is IssueSeverity.WARNING else result.errors
    assert target
    assert all(issue.component is MonitorIssueComponent.OPENALEX for issue in target)


@pytest.mark.parametrize("failure", ("config", "lexical", "fts5", "date"))
def test_validate_monitor_local_failures_are_invalid_before_source_resolution(
    failure: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if failure == "config":
        config_path = tmp_path / "bad.yaml"
        config_path.write_text("keyword_expression: statistics\n", encoding="utf-8")
    elif failure == "date":
        config_path = write_monitor(
            tmp_path,
            date_policy="from_date: 9999-12-31\nwindow_days: 2\n",
        )
    else:
        config_path = write_monitor(tmp_path)

    if failure == "lexical":
        monkeypatch.setattr(
            monitor,
            "validate_runtime_keyword",
            lambda expression: (_ for _ in ()).throw(
                SearchExpressionError("invalid lexical expression")
            ),
        )
    elif failure == "fts5":
        monkeypatch.setattr(
            monitor,
            "validate_runtime_keyword",
            lambda expression: (_ for _ in ()).throw(
                SearchBackendError("SQLite FTS5 is unavailable")
            ),
        )

    def unexpected(*args: object, **kwargs: object) -> object:
        raise AssertionError("source resolution must not start")

    monkeypatch.setattr(monitor, "OpenAlexClient", unexpected)
    monkeypatch.setattr(monitor, "resolve_journal_source", unexpected)
    progress_events: list[ProgressEvent] = []

    result = validate_monitor(
        config_path,
        progress_callback=progress_events.append,
    )

    assert result.outcome is ValidationOutcome.INVALID_CONFIGURATION
    assert result.errors
    assert [
        event.activity.operation
        for event in progress_events
        if event.activity is not None
    ] == ["validation_preflight"]
    assert all(
        event.activity is None or event.activity.source != "openalex"
        for event in progress_events
    )
