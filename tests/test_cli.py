import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from literature_monitor.application.monitor import (
    MonitorIssue,
    MonitorIssueComponent,
    MonitorIssueSeverity,
    MonitorStatistics,
    RunOutcome,
    RunResult,
    ValidationOutcome,
    ValidationResult,
    _CanonicalCoreResult,
)
from literature_monitor.cli import _build_parser, main
from literature_monitor.config import JournalConfig, load_config
from literature_monitor.crossref import (
    CrossrefDiscoveryIssue,
    CrossrefDiscoveryResult,
    CrossrefWorkRecord,
    EnrichedWorkRecord,
    EnrichmentIssue,
    EnrichmentIssueSeverity,
    EnrichmentResult,
)
from literature_monitor.date_range import DateRangeSpec, ResolvedDateRange
from literature_monitor.kept_export import KeptExportIssue, KeptExportResult
from literature_monitor.logging_setup import LOGGER_NAME, configure_logging
from literature_monitor.models import (
    Author,
    CanonicalMetadata,
    CanonicalPaper,
    ExternalIds,
    MetadataSource,
    PaperVersion,
    VersionKind,
    VersionRef,
)
from literature_monitor.openalex import (
    DiscoveryIssue,
    DiscoveryResult,
    IssueSeverity,
    OpenAlexWorkRecord,
    ResolvedSource,
)
from literature_monitor.search import SearchBackendError, SearchableProjection


def application_handlers() -> list[logging.Handler]:
    return [
        handler
        for handler in logging.getLogger(LOGGER_NAME).handlers
        if getattr(handler, "_literature_monitor_handler", False)
    ]


def test_cli_help_uses_functional_diagnostic_names() -> None:
    help_text = _build_parser().format_help()

    assert "diagnose OpenAlex discovery" in help_text
    assert "diagnose local keyword filtering" in help_text
    assert "diagnose Crossref DOI enrichment" in help_text
    assert "diagnose canonicalization" in help_text
    assert "Task 2" not in help_text
    assert "Task 3" not in help_text
    assert "Task 4" not in help_text
    assert "Task 5" not in help_text


def test_logging_configuration_is_idempotent() -> None:
    configure_logging("INFO")
    first = application_handlers()
    configure_logging("DEBUG")
    second = application_handlers()

    assert len(first) == len(second) == 1
    assert first[0] is not second[0]
    assert logging.getLogger(LOGGER_NAME).level == logging.DEBUG


def validate_config(tmp_path: Path) -> tuple[Path, object]:
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
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "venue_whitelist: journals.md\n"
        "keyword_expression: statistics\n"
        "log_level: INFO\n",
        encoding="utf-8",
    )
    return config_path, load_config(config_path)


def config_with_date_policy(tmp_path: Path, date_policy: str) -> Path:
    config_path, _config = validate_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + date_policy,
        encoding="utf-8",
    )
    return config_path


class FixedDate(date):
    @classmethod
    def today(cls) -> date:
        return cls(2026, 9, 21)


def config_with_keyword_expression(tmp_path: Path, expression: str) -> Path:
    config_path, _config = validate_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "keyword_expression: statistics",
            f"keyword_expression: {json.dumps(expression)}",
        ),
        encoding="utf-8",
    )
    return config_path


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


def test_validate_cli_calls_application_boundary_and_logs_result(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    config_path = tmp_path / "monitor.yaml"
    journal = JournalConfig(name="Biometrics", issn=("0006-341X",))
    source = resolved_source(journal)
    warning = MonitorIssue(
        severity=MonitorIssueSeverity.WARNING,
        component=MonitorIssueComponent.OPENALEX,
        stage="source_resolution",
        message="ISSN is unresolved; using the consistent Source",
        journal=journal.name,
        issn=journal.issn[0],
    )
    calls: list[Path] = []

    def fake_validate(path: Path) -> ValidationResult:
        calls.append(path)
        return ValidationResult(
            resolved_date_range=ResolvedDateRange(
                from_date=date(2026, 1, 1),
                to_date=date(2026, 1, 31),
            ),
            configured_journal_count=1,
            configured_issn_count=1,
            resolved_sources=(source,),
            warnings=(warning,),
            errors=(),
            outcome=ValidationOutcome.VALID_WITH_WARNINGS,
        )

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.validate_monitor",
        fake_validate,
    )

    result = main(("validate", "--config", str(config_path)))

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 0
    assert calls == [config_path]
    assert captured.out == ""
    assert "resolved Biometrics to Biometrics" in captured.err
    assert "ISSN 0006-341X" in captured.err
    assert "1 configured journals, 1 configured ISSNs, 1 resolved sources" in captured.err
    assert "1 warnings, 0 errors" in captured.err


@pytest.mark.parametrize(
    ("outcome", "expected_exit"),
    (
        (ValidationOutcome.VALID, 0),
        (ValidationOutcome.VALID_WITH_WARNINGS, 0),
        (ValidationOutcome.SOURCE_ERRORS, 1),
        (ValidationOutcome.INVALID_CONFIGURATION, 2),
    ),
)
def test_validate_cli_maps_application_outcome_to_exit_code(
    outcome: ValidationOutcome,
    expected_exit: int,
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    issue = MonitorIssue(
        severity=MonitorIssueSeverity.ERROR,
        component=(
            MonitorIssueComponent.CONFIGURATION
            if outcome is ValidationOutcome.INVALID_CONFIGURATION
            else MonitorIssueComponent.OPENALEX
        ),
        stage="configuration" if outcome is ValidationOutcome.INVALID_CONFIGURATION else "source_resolution",
        message="validation diagnostic",
        journal=None if outcome is ValidationOutcome.INVALID_CONFIGURATION else "Biometrics",
    )
    result_value = ValidationResult(
        resolved_date_range=None,
        configured_journal_count=0,
        configured_issn_count=0,
        resolved_sources=(),
        warnings=(),
        errors=(issue,) if expected_exit else (),
        outcome=outcome,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.validate_monitor",
        lambda path: result_value,
    )

    result = main(("validate", "--config", str(tmp_path / "monitor.yaml")))

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == expected_exit
    assert captured.out == ""
    if expected_exit:
        assert "validation diagnostic" in captured.err
    if outcome is ValidationOutcome.INVALID_CONFIGURATION:
        assert "Validation completed" not in captured.err


def diagnostic_result(*, with_error: bool = False) -> DiscoveryResult:
    timestamp = datetime(2026, 9, 18, tzinfo=timezone.utc)
    issues = (
        DiscoveryIssue(
            severity=IssueSeverity.ERROR,
            stage="record_normalization",
            journal="Biometrics",
            message="bad record",
            record_id="https://openalex.org/W2",
        ),
    ) if with_error else ()
    return DiscoveryResult(
        sources=(
            ResolvedSource(
                journal="Biometrics",
                configured_issns=("0006-341X",),
                resolved_issns=("0006-341X",),
                unresolved_issns=(),
                openalex_id="https://openalex.org/S8265502",
                display_name="Biometrics",
                issn_l="0006-341X",
                issn=("0006-341X", "1541-0420"),
            ),
        ),
        records=(
            OpenAlexWorkRecord(
                metadata=CanonicalMetadata(title="A Paper", journal="Biometrics"),
                external_ids=ExternalIds(openalex="https://openalex.org/W1"),
                authors=(Author(name="Ada Author"),),
                source_id="https://openalex.org/S8265502",
                provenance=MetadataSource(
                    provider="openalex",
                    record_id="https://openalex.org/W1",
                    retrieved_at=timestamp,
                ),
            ),
        ),
        issues=issues,
    )


def filter_diagnostic_result(*, with_error: bool = False) -> DiscoveryResult:
    timestamp = datetime(2026, 9, 18, tzinfo=timezone.utc)
    base = diagnostic_result(with_error=with_error)

    def record(
        identifier: str,
        title: str,
        *,
        abstract: str | None = None,
    ) -> OpenAlexWorkRecord:
        return OpenAlexWorkRecord(
            metadata=CanonicalMetadata(
                title=title,
                journal="Biometrics",
                abstract=abstract,
            ),
            external_ids=ExternalIds(openalex=f"https://openalex.org/{identifier}"),
            authors=(Author(name="Ada Author"),),
            source_id="https://openalex.org/S8265502",
            provenance=MetadataSource(
                provider="openalex",
                record_id=f"https://openalex.org/{identifier}",
                retrieved_at=timestamp,
            ),
        )

    return DiscoveryResult(
        sources=base.sources,
        records=(
            record(
                "W10",
                "High-dimensional models",
                abstract="New results in statistics",
            ),
            record("W11", "Bayesian multiview learning"),
            record("W12", "Unrelated paper"),
        ),
        issues=base.issues,
    )


def enrichment_diagnostic_result(
    *,
    all_retained: bool = False,
    with_error: bool = False,
) -> DiscoveryResult:
    timestamp = datetime(2026, 9, 18, tzinfo=timezone.utc)

    def record(
        identifier: str,
        title: str,
        abstract: str,
        doi: str | None,
    ) -> OpenAlexWorkRecord:
        return OpenAlexWorkRecord(
            metadata=CanonicalMetadata(
                title=title,
                journal="Biometrics",
                abstract=abstract,
            ),
            external_ids=ExternalIds(
                openalex=f"https://openalex.org/{identifier}",
                doi=doi,
            ),
            authors=(Author(name="Ada Author"),),
            source_id="https://openalex.org/S8265502",
            provenance=MetadataSource(
                provider="openalex",
                record_id=f"https://openalex.org/{identifier}",
                retrieved_at=timestamp,
            ),
        )

    return DiscoveryResult(
        sources=diagnostic_result().sources,
        records=(
            record(
                "W1",
                "High-dimensional models",
                "Statistics inference",
                "10.5555/one",
            ),
            record(
                "W2",
                "High-dimensional excluded" if all_retained else "Excluded paper",
                "Statistics methods" if all_retained else "Different topic",
                "10.5555/two",
            ),
            record(
                "W3",
                "High-dimensional analysis",
                "Statistics without identifiers",
                None,
            ),
        ),
        issues=diagnostic_result(with_error=with_error).issues,
    )


def crossref_record(doi: str) -> CrossrefWorkRecord:
    timestamp = datetime(2026, 9, 18, tzinfo=timezone.utc)
    return CrossrefWorkRecord(
        doi=doi,
        title="Crossref title",
        provenance=MetadataSource(
            provider="crossref",
            record_id=doi,
            retrieved_at=timestamp,
        ),
    )


def crossref_candidate(
    doi: str,
    *,
    title: str,
    abstract: str,
) -> CrossrefWorkRecord:
    timestamp = datetime(2026, 9, 18, tzinfo=timezone.utc)
    return CrossrefWorkRecord(
        doi=doi,
        title=title,
        journal="Biometrics",
        abstract=abstract,
        authors=(Author(name="Ada Author"),),
        issns=("0006-341X",),
        provenance=MetadataSource(
            provider="crossref",
            record_id=doi,
            retrieved_at=timestamp,
        ),
    )


def canonical_paper(identifier: str = "10.5555/one") -> CanonicalPaper:
    version = PaperVersion(
        kind=VersionKind.JOURNAL_FINAL,
        source="doi",
        identifier=identifier,
    )
    return CanonicalPaper(
        metadata=CanonicalMetadata(title="Canonical paper", journal="Biometrics"),
        external_ids=ExternalIds(doi=identifier),
        authors=(Author(name="Ada Author"),),
        versions=(version,),
        preferred_version=VersionRef(source="doi", identifier=identifier),
    )


def crossref_discovery_result(
    records: tuple[CrossrefWorkRecord, ...] = (),
    issues: tuple[CrossrefDiscoveryIssue, ...] = (),
) -> CrossrefDiscoveryResult:
    return CrossrefDiscoveryResult(records=records, issues=issues)


@pytest.mark.parametrize(
    "command",
    (
        "openalex-discover",
        "crossref-discover",
        "openalex-filter",
        "crossref-enrich",
        "canonicalize",
        "materialize",
        "run",
    ),
)
def test_date_bearing_commands_parse_without_cli_date_args(command: str) -> None:
    arguments = [command, "--config", "monitor.yaml"]
    if command == "materialize":
        arguments.extend(("--output-dir", "workspace"))

    args = _build_parser().parse_args(arguments)

    assert args.from_date is None
    assert args.to_date is None
    assert args.window_days is None


def test_date_bearing_parser_accepts_window_days_override() -> None:
    args = _build_parser().parse_args(
        ("openalex-discover", "--config", "monitor.yaml", "--window-days", "30")
    )

    assert args.from_date is None
    assert args.to_date is None
    assert args.window_days == 30


@pytest.mark.parametrize(
    "date_arguments",
    (
        ("--window-days", "30"),
        ("--from-date", "2026-01-01", "--to-date", "2026-01-31"),
        ("--from-date", "2026-09-01", "--window-days", "21"),
        ("--to-date", "2026-09-21", "--window-days", "14"),
    ),
)
def test_run_parser_accepts_shared_date_override_forms(
    date_arguments: tuple[str, ...],
) -> None:
    args = _build_parser().parse_args(
        ("run", "--config", "monitor.yaml", *date_arguments)
    )

    assert args.command == "run"


@pytest.mark.parametrize(
    "forbidden_arguments",
    (
        ("--journal", "Biometrics"),
        ("--keyword-expression", "causal"),
        ("--output-dir", "other-workspace"),
    ),
)
def test_run_parser_rejects_diagnostic_and_output_overrides(
    forbidden_arguments: tuple[str, ...],
) -> None:
    with pytest.raises(SystemExit) as captured:
        _build_parser().parse_args(
            ("run", "--config", "monitor.yaml", *forbidden_arguments)
        )

    assert captured.value.code == 2


def test_materialize_parser_still_requires_output_dir() -> None:
    with pytest.raises(SystemExit) as captured:
        _build_parser().parse_args(("materialize", "--config", "monitor.yaml"))

    assert captured.value.code == 2


@pytest.mark.parametrize(
    "arguments",
    (
        ("validate", "--config", "monitor.yaml", "--window-days", "14"),
        ("export-kept", "--output-dir", "workspace", "--window-days", "14"),
    ),
)
def test_non_date_commands_do_not_accept_date_arguments(
    arguments: tuple[str, ...],
) -> None:
    with pytest.raises(SystemExit) as captured:
        _build_parser().parse_args(arguments)

    assert captured.value.code == 2


@pytest.mark.parametrize(
    ("date_policy", "expected"),
    [
        ("", (date(2026, 9, 8), date(2026, 9, 21))),
        (
            "from_date: 2026-01-01\nto_date: 2026-01-31\n",
            (date(2026, 1, 1), date(2026, 1, 31)),
        ),
        (
            "to_date: 2026-09-21\nwindow_days: 14\n",
            (date(2026, 9, 8), date(2026, 9, 21)),
        ),
    ],
)
def test_openalex_discover_without_cli_dates_uses_config_policy(
    date_policy: str,
    expected: tuple[date, date],
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    config_path = (
        config_with_date_policy(tmp_path, date_policy)
        if date_policy
        else validate_config(tmp_path)[0]
    )
    received_ranges: list[tuple[date, date]] = []

    def fake_discover(
        client: object,
        journals: tuple[object, ...],
        from_date: date,
        to_date: date,
    ) -> DiscoveryResult:
        received_ranges.append((from_date, to_date))
        return diagnostic_result()

    monkeypatch.setattr("literature_monitor.cli.date", FixedDate)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.OpenAlexClient", lambda **kwargs: object()
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals", fake_discover
    )

    result = main(("openalex-discover", "--config", str(config_path)))

    capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 0
    assert received_ranges == [expected]


@pytest.mark.parametrize(
    ("date_arguments", "expected"),
    [
        (
            ("--window-days", "30"),
            (date(2026, 8, 23), date(2026, 9, 21)),
        ),
        (
            ("--from-date", "2026-01-01", "--to-date", "2026-01-31"),
            (date(2026, 1, 1), date(2026, 1, 31)),
        ),
        (
            ("--from-date", "2026-09-01", "--window-days", "21"),
            (date(2026, 9, 1), date(2026, 9, 21)),
        ),
        (
            ("--to-date", "2026-09-21", "--window-days", "14"),
            (date(2026, 9, 8), date(2026, 9, 21)),
        ),
    ],
)
def test_cli_date_override_forms_resolve_without_merging_config(
    date_arguments: tuple[str, ...],
    expected: tuple[date, date],
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    config_path = config_with_date_policy(tmp_path, "window_days: 14\n")
    received_ranges: list[tuple[date, date]] = []

    def fake_discover(
        client: object,
        journals: tuple[object, ...],
        from_date: date,
        to_date: date,
    ) -> DiscoveryResult:
        received_ranges.append((from_date, to_date))
        return diagnostic_result()

    monkeypatch.setattr("literature_monitor.cli.date", FixedDate)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.OpenAlexClient", lambda **kwargs: object()
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals", fake_discover
    )

    result = main(
        ("openalex-discover", "--config", str(config_path), *date_arguments)
    )

    capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 0
    assert received_ranges == [expected]


@pytest.mark.parametrize(
    ("config_policy", "date_arguments"),
    [
        (
            "window_days: 14\n",
            ("--to-date", "2026-09-01"),
        ),
        (
            "to_date: 2026-09-21\nwindow_days: 14\n",
            ("--from-date", "2026-01-01"),
        ),
    ],
)
def test_partial_cli_date_override_does_not_borrow_config_fields(
    config_policy: str,
    date_arguments: tuple[str, ...],
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    config_path = config_with_date_policy(tmp_path, config_policy)

    def unexpected_provider(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider path must not be reached")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.OpenAlexClient", unexpected_provider
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals", unexpected_provider
    )

    result = main(
        ("openalex-discover", "--config", str(config_path), *date_arguments)
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 2
    assert "must be combined with another date field" in captured.err


@pytest.mark.parametrize(
    ("date_arguments", "message"),
    [
        (
            (
                "--from-date",
                "2026-09-01",
                "--to-date",
                "2026-09-21",
                "--window-days",
                "21",
            ),
            "cannot all be specified",
        ),
        (("--window-days", "0"), "--window-days must be at least 1"),
        (("--window-days", "-1"), "--window-days must be at least 1"),
        (
            ("--from-date", "2026-09-21", "--to-date", "2026-09-01"),
            "--from-date must not be after --to-date",
        ),
    ],
)
def test_invalid_cli_date_overrides_fail_before_provider_work(
    date_arguments: tuple[str, ...],
    message: str,
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    config_path = validate_config(tmp_path)[0]

    def unexpected_provider(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider path must not be reached")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.OpenAlexClient", unexpected_provider
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals", unexpected_provider
    )

    result = main(
        ("openalex-discover", "--config", str(config_path), *date_arguments)
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 2
    assert message in captured.err


def cli_run_result(
    outcome: RunOutcome,
    *,
    warnings: tuple[MonitorIssue, ...] = (),
    errors: tuple[MonitorIssue, ...] = (),
) -> RunResult:
    return RunResult(
        resolved_date_range=ResolvedDateRange(
            from_date=date(2026, 1, 1),
            to_date=date(2026, 1, 31),
        ),
        canonical_paper_count=1,
        created_papers=1,
        matched_existing_papers=0,
        updated_papers=0,
        created_authors=1,
        existing_authors=0,
        warnings=warnings,
        errors=errors,
        outcome=outcome,
        statistics=MonitorStatistics(
            openalex_records=1,
            crossref_discovery_records=1,
            crossref_supplement_records=1,
            semantic_scholar_supplement_records=1,
            semantic_scholar_discovery_records=1,
            evidence_clusters=1,
            retained_clusters=1,
        ),
    )


@pytest.mark.parametrize(
    ("date_arguments", "expected_override"),
    (
        (
            ("--window-days", "30"),
            DateRangeSpec(window_days=30),
        ),
        (
            ("--from-date", "2026-01-01", "--to-date", "2026-01-31"),
            DateRangeSpec(
                from_date=date(2026, 1, 1),
                to_date=date(2026, 1, 31),
            ),
        ),
        (
            ("--from-date", "2026-09-01", "--window-days", "21"),
            DateRangeSpec(from_date=date(2026, 9, 1), window_days=21),
        ),
        (
            ("--to-date", "2026-09-21", "--window-days", "14"),
            DateRangeSpec(to_date=date(2026, 9, 21), window_days=14),
        ),
    ),
)
def test_run_cli_shapes_date_override_for_application(
    date_arguments: tuple[str, ...],
    expected_override: DateRangeSpec,
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    config_path = tmp_path / "monitor.yaml"
    calls: list[tuple[Path, DateRangeSpec | None]] = []

    def fake_run(
        path: Path,
        *,
        date_override: DateRangeSpec | None = None,
    ) -> RunResult:
        calls.append((path, date_override))
        return cli_run_result(RunOutcome.COMPLETED)

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.run_monitor",
        fake_run,
    )

    result = main(("run", "--config", str(config_path), *date_arguments))

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 0
    assert calls == [(config_path, expected_override)]
    assert captured.out == ""


def test_run_cli_passes_none_without_date_override(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    config_path = tmp_path / "monitor.yaml"
    received: list[DateRangeSpec | None] = []

    def fake_run(
        path: Path,
        *,
        date_override: DateRangeSpec | None = None,
    ) -> RunResult:
        received.append(date_override)
        return cli_run_result(RunOutcome.COMPLETED)

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.run_monitor",
        fake_run,
    )

    assert main(("run", "--config", str(config_path))) == 0
    capsys.readouterr()  # type: ignore[attr-defined]
    assert received == [None]


@pytest.mark.parametrize(
    ("outcome", "expected_exit"),
    (
        (RunOutcome.COMPLETED, 0),
        (RunOutcome.COMPLETED_WITH_WARNINGS, 0),
        (RunOutcome.COMPLETED_WITH_ERRORS, 1),
        (RunOutcome.INVALID_CONFIGURATION, 2),
    ),
)
def test_run_cli_maps_structured_outcome_to_exit_code(
    outcome: RunOutcome,
    expected_exit: int,
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    issue = MonitorIssue(
        severity=MonitorIssueSeverity.ERROR,
        component=MonitorIssueComponent.CONFIGURATION,
        stage="configuration",
        message="run diagnostic",
    )
    value = cli_run_result(
        outcome,
        errors=(issue,) if expected_exit else (),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.run_monitor",
        lambda path, *, date_override=None: value,
    )

    result = main(("run", "--config", str(tmp_path / "monitor.yaml")))

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == expected_exit
    assert captured.out == ""
    if outcome is RunOutcome.INVALID_CONFIGURATION:
        assert "Materialization completed" not in captured.err
    else:
        assert "1 canonical papers, 1 paper files created" in captured.err


@pytest.mark.parametrize(
    "command",
    (
        "openalex-discover",
        "openalex-filter",
        "crossref-discover",
        "crossref-enrich",
    ),
)
def test_historical_diagnostics_do_not_construct_semantic_scholar(
    command: str,
    monkeypatch: object,
    capsys: object,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]

    def unexpected_semantic_scholar(*args: object, **kwargs: object) -> object:
        raise AssertionError("historical diagnostics must not construct S2")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.OpenAlexClient", lambda **kwargs: object()
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.CrossrefClient", lambda **kwargs: object()
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: diagnostic_result(),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_crossref_journals",
        lambda *args: crossref_discovery_result(),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.enrich_records",
        lambda client, records: EnrichmentResult(
            records=tuple(EnrichedWorkRecord(openalex=record) for record in records),
            issues=(),
        ),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.application.monitor.create_semantic_scholar_client",
        unexpected_semantic_scholar,
    )

    result = main(
        (
            command,
            "--config",
            str(repository_root / "config.example.yaml"),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
        )
    )

    capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 0


def test_openalex_discover_cli_writes_diagnostic_ndjson_and_logs_to_stderr(
    monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    captured_journals: list[tuple[str, ...]] = []

    def fake_discover(
        client: object, journals: tuple[object, ...], *args: object
    ) -> DiscoveryResult:
        captured_journals.append(tuple(getattr(journal, "name") for journal in journals))
        return diagnostic_result()

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals", fake_discover
    )

    result = main(
        (
            "openalex-discover",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--journal",
            "biometrics",
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)
    assert result == 0
    assert payload["external_ids"]["openalex"] == "https://openalex.org/W1"
    assert "OpenAlex diagnostic completed" in captured.err
    assert captured_journals == [("BIOMETRICS",)]


def test_openalex_discover_cli_returns_one_for_partial_errors(
    monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: diagnostic_result(with_error=True),
    )

    result = main(
        (
            "openalex-discover",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--journal",
            "Biometrics",
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 1
    assert "bad record" in captured.err
    assert json.loads(captured.out)["metadata"]["title"] == "A Paper"


def test_openalex_discover_cli_rejects_unknown_journal_without_network(
    monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    called = False

    def fake_discover(*args: object) -> DiscoveryResult:
        nonlocal called
        called = True
        return diagnostic_result()

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals", fake_discover
    )

    result = main(
        (
            "openalex-discover",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--journal",
            "Not Configured",
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 2
    assert "unknown configured journal" in captured.err
    assert not called


def test_openalex_discover_cli_rejects_reverse_date_range_without_network(
    monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    called = False

    def fake_discover(*args: object) -> DiscoveryResult:
        nonlocal called
        called = True
        return diagnostic_result()

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals", fake_discover
    )

    result = main(
        (
            "openalex-discover",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--from-date",
            "2026-02-01",
            "--to-date",
            "2026-01-01",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 2
    assert "--from-date must not be after --to-date" in captured.err
    assert not called


def test_openalex_filter_uses_config_expression_and_reports_counts(
    monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: filter_diagnostic_result(),
    )

    result = main(
        (
            "openalex-filter",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--journal",
            "Biometrics",
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    rows = [json.loads(line) for line in captured.out.splitlines()]
    assert result == 0
    assert [row["external_ids"]["openalex"] for row in rows] == [
        "https://openalex.org/W10"
    ]
    assert "3 discovered, 1 retained, 2 filtered out" in captured.err


@pytest.mark.parametrize(
    ("expression", "expected_openalex_id"),
    [
        ("statist*", "https://openalex.org/W10"),
        ('"bayesian multiview"~0', "https://openalex.org/W11"),
    ],
)
def test_openalex_filter_prefix_and_proximity_overrides_use_local_search(
    expression: str,
    expected_openalex_id: str,
    monkeypatch: object,
    capsys: object,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    config_path = repository_root / "config.example.yaml"
    before = config_path.read_bytes()
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: filter_diagnostic_result(),
    )

    result = main(
        (
            "openalex-filter",
            "--config",
            str(config_path),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
            "--keyword-expression",
            expression,
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    rows = [json.loads(line) for line in captured.out.splitlines()]
    assert result == 0
    assert [row["external_ids"]["openalex"] for row in rows] == [
        expected_openalex_id
    ]
    assert config_path.read_bytes() == before


@pytest.mark.parametrize(
    ("expression", "expected_openalex_id"),
    [
        ("statist*", "https://openalex.org/W10"),
        ('"bayesian multiview"~0', "https://openalex.org/W11"),
    ],
)
def test_openalex_filter_uses_configured_prefix_and_proximity(
    expression: str,
    expected_openalex_id: str,
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    config_path = config_with_keyword_expression(tmp_path, expression)
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: filter_diagnostic_result(),
    )

    result = main(
        (
            "openalex-filter",
            "--config",
            str(config_path),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    rows = [json.loads(line) for line in captured.out.splitlines()]
    assert result == 0
    assert [row["external_ids"]["openalex"] for row in rows] == [
        expected_openalex_id
    ]


def test_openalex_filter_batches_all_records_and_preserves_retained_order(
    monkeypatch: object,
    capsys: object,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    batches: list[tuple[SearchableProjection, ...]] = []
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: filter_diagnostic_result(),
    )

    def fake_match(
        expression: object,
        projections: tuple[SearchableProjection, ...],
    ) -> tuple[bool, ...]:
        batches.append(projections)
        return (False, True, True)

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.match_searchable_projections",
        fake_match,
    )

    result = main(
        (
            "openalex-filter",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    rows = [json.loads(line) for line in captured.out.splitlines()]
    assert len(batches) == 1
    assert [projection.titles for projection in batches[0]] == [
        ("High-dimensional models",),
        ("Bayesian multiview learning",),
        ("Unrelated paper",),
    ]
    assert [row["external_ids"]["openalex"] for row in rows] == [
        "https://openalex.org/W11",
        "https://openalex.org/W12",
    ]
    assert result == 0
    assert "3 discovered, 2 retained, 1 filtered out" in captured.err


def test_openalex_filter_exposes_unicode61_punctuation_semantics(
    monkeypatch: object,
    capsys: object,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    discovery = filter_diagnostic_result()
    first = discovery.records[0]
    spaced_title = first.model_copy(
        update={
            "metadata": first.metadata.model_copy(
                update={"title": "High dimensional models"}
            )
        }
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: DiscoveryResult(
            sources=discovery.sources,
            records=(spaced_title, *discovery.records[1:]),
            issues=discovery.issues,
        ),
    )

    result = main(
        (
            "openalex-filter",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
            "--keyword-expression",
            "high-dimensional",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 0
    assert json.loads(captured.out)["external_ids"]["openalex"] == (
        "https://openalex.org/W10"
    )


def test_openalex_filter_reports_search_backend_failure_without_traceback(
    monkeypatch: object,
    capsys: object,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: filter_diagnostic_result(),
    )

    def fail_search(*args: object) -> tuple[bool, ...]:
        raise SearchBackendError("SQLite FTS5 is unavailable")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.match_searchable_projections",
        fail_search,
    )

    result = main(
        (
            "openalex-filter",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 2
    assert captured.out == ""
    assert "Local search / FTS5 backend failure" in captured.err
    assert "Traceback" not in captured.err


def test_openalex_filter_override_is_one_run_only_and_takes_precedence(
    monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    config_path = repository_root / "config.example.yaml"
    before = config_path.read_bytes()
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: filter_diagnostic_result(),
    )

    result = main(
        (
            "openalex-filter",
            "--config",
            str(config_path),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
            "--keyword-expression",
            '"multiview learning"',
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 0
    assert json.loads(captured.out)["external_ids"]["openalex"] == (
        "https://openalex.org/W11"
    )
    assert config_path.read_bytes() == before


def test_openalex_filter_invalid_override_does_not_construct_client_or_discover(
    monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]

    def unexpected_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("network path must not be reached")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.OpenAlexClient", unexpected_call
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals", unexpected_call
    )

    result = main(
        (
            "openalex-filter",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
            "--keyword-expression",
            "alpha AND",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 2
    assert "--keyword-expression" in captured.err
    assert "column" in captured.err


@pytest.mark.parametrize(
    "command",
    ("openalex-filter", "crossref-enrich"),
)
def test_lexically_invalid_override_stops_before_provider_clients(
    command: str,
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]

    def unexpected_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider path must not be reached")

    for name in (
        "OpenAlexClient",
        "CrossrefClient",
        "discover_journals",
        "discover_crossref_journals",
    ):
        monkeypatch.setattr(  # type: ignore[attr-defined]
            f"literature_monitor.cli.{name}",
            unexpected_call,
        )

    arguments = [
        command,
        "--config",
        str(repository_root / "config.example.yaml"),
        "--from-date",
        "2026-01-01",
        "--to-date",
        "2026-01-31",
        "--keyword-expression",
        '"causal"~2',
    ]
    if command == "materialize":
        arguments.extend(("--output-dir", str(tmp_path / "Vault")))

    result = main(tuple(arguments))

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 2
    assert captured.out == ""
    assert "--keyword-expression" in captured.err
    assert "at least two lexical tokens" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("command", ("openalex-filter",))
def test_lexically_invalid_config_stops_before_provider_clients(
    command: str,
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    config_path = config_with_keyword_expression(tmp_path, '"causal"~2')

    def unexpected_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider path must not be reached")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.OpenAlexClient",
        unexpected_call,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        unexpected_call,
    )

    result = main(
        (
            command,
            "--config",
            str(config_path),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 2
    assert captured.out == ""
    assert str(config_path) in captured.err
    assert "keyword_expression" in captured.err
    assert "at least two lexical tokens" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    "command",
    ("openalex-filter", "crossref-enrich"),
)
def test_lexically_invalid_config_cannot_be_hidden_by_valid_override(
    command: str,
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    config_path = config_with_keyword_expression(tmp_path, '"causal"~2')

    def unexpected_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider path must not be reached")

    for name in (
        "OpenAlexClient",
        "CrossrefClient",
        "discover_journals",
        "discover_crossref_journals",
    ):
        monkeypatch.setattr(  # type: ignore[attr-defined]
            f"literature_monitor.cli.{name}",
            unexpected_call,
        )

    arguments = [
        command,
        "--config",
        str(config_path),
        "--from-date",
        "2026-01-01",
        "--to-date",
        "2026-01-31",
        "--keyword-expression",
        "statist*",
    ]
    if command == "materialize":
        arguments.extend(("--output-dir", str(tmp_path / "Vault")))

    result = main(tuple(arguments))

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 2
    assert captured.out == ""
    assert str(config_path) in captured.err
    assert "keyword_expression" in captured.err
    assert "at least two lexical tokens" in captured.err
    assert "--keyword-expression" not in captured.err
    assert "Traceback" not in captured.err


def test_discovery_command_does_not_require_local_lexical_preflight(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    config_path = config_with_keyword_expression(tmp_path, '"causal"~2')
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: diagnostic_result(),
    )

    result = main(
        (
            "openalex-discover",
            "--config",
            str(config_path),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
        )
    )

    capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 0


def test_local_filter_preflight_preserves_fts5_backend_error_style(
    monkeypatch: object,
    capsys: object,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]

    def fail_validation(*args: object) -> None:
        raise SearchBackendError("SQLite FTS5 is unavailable")

    def unexpected_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider path must not be reached")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.validate_search_expression",
        fail_validation,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.OpenAlexClient",
        unexpected_call,
    )

    result = main(
        (
            "openalex-filter",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 2
    assert captured.out == ""
    assert "Local search / FTS5 backend failure" in captured.err
    assert "SQLite FTS5 is unavailable" in captured.err
    assert "Traceback" not in captured.err


def test_openalex_filter_validates_config_before_override(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    config_path = tmp_path / "bad.yaml"
    config_path.write_text("keyword_expression: alpha\n", encoding="utf-8")
    called = False

    def unexpected_discovery(*args: object) -> DiscoveryResult:
        nonlocal called
        called = True
        return filter_diagnostic_result()

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals", unexpected_discovery
    )

    result = main(
        (
            "openalex-filter",
            "--config",
            str(config_path),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
            "--keyword-expression",
            "alpha AND",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 2
    assert "venue_whitelist" in captured.err
    assert "--keyword-expression" not in captured.err
    assert not called


def test_openalex_filter_emits_retained_records_despite_partial_errors(
    monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: filter_diagnostic_result(with_error=True),
    )

    result = main(
        (
            "openalex-filter",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 1
    assert json.loads(captured.out)["external_ids"]["openalex"] == (
        "https://openalex.org/W10"
    )
    assert "bad record" in captured.err
    assert "3 discovered, 1 retained, 2 filtered out" in captured.err


def test_crossref_enrich_uses_config_filter_before_enrichment(
    monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    received: list[OpenAlexWorkRecord] = []
    match_batches: list[tuple[SearchableProjection, ...]] = []
    events: list[str] = []
    client_sentinel = object()

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: enrichment_diagnostic_result(),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.CrossrefClient",
        lambda **kwargs: client_sentinel,
    )

    def fake_match(
        expression: object,
        projections: tuple[SearchableProjection, ...],
    ) -> tuple[bool, ...]:
        events.append("filter")
        match_batches.append(projections)
        return (True, False, True)

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.match_searchable_projections",
        fake_match,
    )

    def fake_enrich(
        client: object, records: tuple[OpenAlexWorkRecord, ...]
    ) -> EnrichmentResult:
        events.append("enrich")
        assert client is client_sentinel
        received.extend(records)
        return EnrichmentResult(
            records=(
                EnrichedWorkRecord(
                    openalex=records[0],
                    crossref=crossref_record("10.5555/one"),
                ),
                EnrichedWorkRecord(openalex=records[1]),
            ),
            issues=(
                EnrichmentIssue(
                    severity=EnrichmentIssueSeverity.WARNING,
                    stage="missing_doi",
                    message="record has no DOI",
                    record_id=records[1].external_ids.openalex,
                ),
            ),
        )

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.enrich_records", fake_enrich
    )
    result = main(
        (
            "crossref-enrich",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--journal",
            "Biometrics",
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    rows = [json.loads(line) for line in captured.out.splitlines()]
    assert result == 0
    assert events == ["filter", "enrich"]
    assert len(match_batches) == 1
    assert [projection.titles for projection in match_batches[0]] == [
        ("High-dimensional models",),
        ("Excluded paper",),
        ("High-dimensional analysis",),
    ]
    assert [record.external_ids.openalex for record in received] == [
        "https://openalex.org/W1",
        "https://openalex.org/W3",
    ]
    assert [row["openalex"]["external_ids"]["openalex"] for row in rows] == [
        "https://openalex.org/W1",
        "https://openalex.org/W3",
    ]
    assert rows[0]["crossref"]["doi"] == "10.5555/one"
    assert rows[1]["crossref"] is None
    assert (
        "3 discovered, 2 retained, 1 enriched, 1 without DOI, "
        "0 unavailable, 0 failed" in captured.err
    )


def test_crossref_enrich_override_is_one_run_only_and_passes_mailto(
    monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    config_path = repository_root / "config.example.yaml"
    before = config_path.read_bytes()
    received: list[OpenAlexWorkRecord] = []
    mailto_values: list[str | None] = []
    monkeypatch.setenv("CROSSREF_MAILTO", "monitor@example.com")  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: enrichment_diagnostic_result(),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.CrossrefClient",
        lambda *, mailto=None: mailto_values.append(mailto) or object(),
    )

    def fake_enrich(
        client: object, records: tuple[OpenAlexWorkRecord, ...]
    ) -> EnrichmentResult:
        received.extend(records)
        return EnrichmentResult(
            records=tuple(EnrichedWorkRecord(openalex=record) for record in records),
            issues=(),
        )

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.enrich_records", fake_enrich
    )

    result = main(
        (
            "crossref-enrich",
            "--config",
            str(config_path),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
            "--keyword-expression",
            '"excluded paper"',
        )
    )

    capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 0
    assert [record.external_ids.openalex for record in received] == [
        "https://openalex.org/W2"
    ]
    assert mailto_values == ["monitor@example.com"]
    assert config_path.read_bytes() == before


def test_crossref_enrich_invalid_override_constructs_no_clients(
    monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]

    def unexpected_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider path must not be reached")

    for name in (
        "OpenAlexClient",
        "CrossrefClient",
        "discover_journals",
        "enrich_records",
    ):
        monkeypatch.setattr(  # type: ignore[attr-defined]
            f"literature_monitor.cli.{name}", unexpected_call
        )

    result = main(
        (
            "crossref-enrich",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
            "--keyword-expression",
            "alpha AND",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 2
    assert "--keyword-expression" in captured.err
    assert "column" in captured.err


def test_crossref_enrich_validates_config_before_override(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    config_path = tmp_path / "bad.yaml"
    config_path.write_text("keyword_expression: alpha\n", encoding="utf-8")

    def unexpected_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider path must not be reached")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.OpenAlexClient", unexpected_call
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.CrossrefClient", unexpected_call
    )

    result = main(
        (
            "crossref-enrich",
            "--config",
            str(config_path),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
            "--keyword-expression",
            "alpha AND",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 2
    assert "venue_whitelist" in captured.err
    assert "--keyword-expression" not in captured.err


def test_crossref_enrich_rejects_invalid_dates_before_provider_clients(
    monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]

    def unexpected_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider path must not be reached")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.OpenAlexClient", unexpected_call
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.CrossrefClient", unexpected_call
    )

    result = main(
        (
            "crossref-enrich",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--from-date",
            "2026-02-01",
            "--to-date",
            "2026-01-01",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 2
    assert "--from-date must not be after --to-date" in captured.err


def test_crossref_enrich_emits_all_records_on_partial_hard_failure(
    monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: enrichment_diagnostic_result(all_retained=True),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.CrossrefClient", lambda **kwargs: object()
    )

    def fake_enrich(
        client: object, records: tuple[OpenAlexWorkRecord, ...]
    ) -> EnrichmentResult:
        return EnrichmentResult(
            records=(
                EnrichedWorkRecord(
                    openalex=records[0],
                    crossref=crossref_record("10.5555/one"),
                ),
                EnrichedWorkRecord(openalex=records[1]),
                EnrichedWorkRecord(openalex=records[2]),
            ),
            issues=(
                EnrichmentIssue(
                    severity=EnrichmentIssueSeverity.ERROR,
                    stage="request_failure",
                    message="server unavailable",
                    record_id=records[1].external_ids.openalex,
                    doi=records[1].external_ids.doi,
                ),
                EnrichmentIssue(
                    severity=EnrichmentIssueSeverity.WARNING,
                    stage="missing_doi",
                    message="record has no DOI",
                    record_id=records[2].external_ids.openalex,
                ),
            ),
        )

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.enrich_records", fake_enrich
    )

    result = main(
        (
            "crossref-enrich",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    rows = [json.loads(line) for line in captured.out.splitlines()]
    assert result == 1
    assert len(rows) == 3
    assert [row["crossref"] is not None for row in rows] == [True, False, False]
    assert "server unavailable" in captured.err
    assert "1 failed" in captured.err


def test_crossref_enrich_not_found_is_nonfatal(
    monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: enrichment_diagnostic_result(),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.CrossrefClient", lambda **kwargs: object()
    )

    def fake_enrich(
        client: object, records: tuple[OpenAlexWorkRecord, ...]
    ) -> EnrichmentResult:
        return EnrichmentResult(
            records=tuple(EnrichedWorkRecord(openalex=record) for record in records),
            issues=(
                EnrichmentIssue(
                    severity=EnrichmentIssueSeverity.WARNING,
                    stage="not_found",
                    message="not found",
                    record_id=records[0].external_ids.openalex,
                    doi=records[0].external_ids.doi,
                ),
                EnrichmentIssue(
                    severity=EnrichmentIssueSeverity.WARNING,
                    stage="missing_doi",
                    message="record has no DOI",
                    record_id=records[1].external_ids.openalex,
                ),
            ),
        )

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.enrich_records", fake_enrich
    )

    result = main(
        (
            "crossref-enrich",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 0
    assert all(
        json.loads(line)["crossref"] is None for line in captured.out.splitlines()
    )
    assert "1 unavailable, 0 failed" in captured.err


def test_crossref_enrich_keeps_openalex_hard_error_exit_status(
    monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: enrichment_diagnostic_result(with_error=True),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.CrossrefClient", lambda **kwargs: object()
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.enrich_records",
        lambda client, records: EnrichmentResult(
            records=tuple(EnrichedWorkRecord(openalex=record) for record in records),
            issues=(),
        ),
    )

    result = main(
        (
            "crossref-enrich",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 1
    assert "bad record" in captured.err


@pytest.mark.parametrize("command", ["openalex-discover", "openalex-filter"])
def test_existing_openalex_commands_never_construct_crossref_client(
    command: str, monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]

    def unexpected_crossref(*args: object, **kwargs: object) -> object:
        raise AssertionError("Crossref client must not be constructed")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.CrossrefClient", unexpected_crossref
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: (
            diagnostic_result() if command == "openalex-discover" else filter_diagnostic_result()
        ),
    )

    result = main(
        (
            command,
            "--config",
            str(repository_root / "config.example.yaml"),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
        )
    )

    capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 0


def test_crossref_discover_is_crossref_only_and_emits_provider_ndjson(
    monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    client = object()
    calls: list[tuple[object, tuple[str, ...], object, object]] = []

    def unexpected_openalex(*args: object, **kwargs: object) -> object:
        raise AssertionError("OpenAlex client must not be constructed")

    def fake_discover(
        received_client: object,
        journals: tuple[JournalConfig, ...],
        from_date: object,
        to_date: object,
    ) -> CrossrefDiscoveryResult:
        calls.append(
            (
                received_client,
                tuple(journal.name for journal in journals),
                from_date,
                to_date,
            )
        )
        return crossref_discovery_result((crossref_record("10.5555/one"),))

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.OpenAlexClient", unexpected_openalex
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.CrossrefClient", lambda **kwargs: client
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_crossref_journals", fake_discover
    )

    result = main(
        (
            "crossref-discover",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--journal",
            "Biometrics",
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 0
    assert calls[0][0] is client
    assert calls[0][1] == ("BIOMETRICS",)
    assert json.loads(captured.out)["doi"] == "10.5555/one"
    assert "1 records, 0 issues" in captured.err


def test_crossref_discover_keeps_records_on_isolated_hard_error(
    monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    issue = CrossrefDiscoveryIssue(
        severity=EnrichmentIssueSeverity.ERROR,
        stage="work_retrieval",
        journal="Biometrics",
        issn="0006-341X",
        message="server unavailable",
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.CrossrefClient", lambda **kwargs: object()
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_crossref_journals",
        lambda *args: crossref_discovery_result(
            (crossref_record("10.5555/success"),),
            (issue,),
        ),
    )

    result = main(
        (
            "crossref-discover",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 1
    assert json.loads(captured.out)["doi"] == "10.5555/success"
    assert "server unavailable" in captured.err


def cli_core_result(
    outcome: RunOutcome = RunOutcome.COMPLETED,
    *,
    warnings: tuple[MonitorIssue, ...] = (),
    errors: tuple[MonitorIssue, ...] = (),
) -> _CanonicalCoreResult:
    return _CanonicalCoreResult(
        config=None,
        resolved_date_range=ResolvedDateRange(
            from_date=date(2026, 1, 1),
            to_date=date(2026, 1, 31),
        ),
        papers=(canonical_paper(),),
        resolved_sources=(),
        warnings=warnings,
        errors=errors,
        outcome=outcome,
        statistics=MonitorStatistics(
            openalex_records=1,
            crossref_discovery_records=1,
            crossref_supplement_records=1,
            semantic_scholar_supplement_records=1,
            semantic_scholar_discovery_records=1,
            evidence_clusters=1,
            retained_clusters=1,
        ),
    )


def test_canonicalize_cli_calls_shared_application_core_and_emits_ndjson(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    config_path = tmp_path / "monitor.yaml"
    calls: list[tuple[Path, DateRangeSpec | None, str | None, str | None]] = []
    core = cli_core_result()

    def fake_core(
        path: Path,
        *,
        date_override: DateRangeSpec | None = None,
        journal_name: str | None = None,
        keyword_expression: str | None = None,
        progress_callback: object = None,
    ) -> _CanonicalCoreResult:
        calls.append((path, date_override, journal_name, keyword_expression))
        return core

    def unexpected_materialization(*args: object, **kwargs: object) -> object:
        raise AssertionError("canonicalize must stop before materialization")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli._run_canonical_core",
        fake_core,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli._materialize_canonical_result",
        unexpected_materialization,
    )

    result = main(
        (
            "canonicalize",
            "--config",
            str(config_path),
            "--journal",
            "Biometrics",
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
            "--keyword-expression",
            "statistics",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 0
    assert calls == [
        (
            config_path,
            DateRangeSpec(
                from_date=date(2026, 1, 1),
                to_date=date(2026, 1, 31),
            ),
            "Biometrics",
            "statistics",
        )
    ]
    assert json.loads(captured.out)["metadata"]["title"] == "Canonical paper"
    assert "1 canonical papers" in captured.err


@pytest.mark.parametrize(
    ("outcome", "expected_exit"),
    (
        (RunOutcome.COMPLETED, 0),
        (RunOutcome.COMPLETED_WITH_WARNINGS, 0),
        (RunOutcome.COMPLETED_WITH_ERRORS, 1),
        (RunOutcome.INVALID_CONFIGURATION, 2),
    ),
)
def test_canonicalize_cli_maps_core_outcome_without_materializing(
    outcome: RunOutcome,
    expected_exit: int,
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    issue = MonitorIssue(
        severity=MonitorIssueSeverity.ERROR,
        component=MonitorIssueComponent.OPENALEX,
        stage="work_retrieval",
        message="provider diagnostic",
        journal="Biometrics",
    )
    core = cli_core_result(
        outcome,
        errors=(issue,) if expected_exit else (),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli._run_canonical_core",
        lambda *args, **kwargs: core,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli._materialize_canonical_result",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("canonicalize must not materialize")
        ),
    )

    result = main(
        (
            "canonicalize",
            "--config",
            str(tmp_path / "monitor.yaml"),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == expected_exit
    if outcome is RunOutcome.INVALID_CONFIGURATION:
        assert captured.out == ""
    else:
        assert json.loads(captured.out)["metadata"]["title"] == "Canonical paper"


def test_materialize_cli_uses_shared_core_then_formal_materialization_path(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    config_path = tmp_path / "monitor.yaml"
    output_dir = tmp_path / "Vault"
    core = cli_core_result()
    core_calls: list[tuple[Path, DateRangeSpec | None, str | None, str | None]] = []
    materialize_calls: list[tuple[_CanonicalCoreResult, Path]] = []

    def fake_core(
        path: Path,
        *,
        date_override: DateRangeSpec | None = None,
        journal_name: str | None = None,
        keyword_expression: str | None = None,
        progress_callback: object = None,
    ) -> _CanonicalCoreResult:
        core_calls.append((path, date_override, journal_name, keyword_expression))
        return core

    def fake_materialize(
        received_core: _CanonicalCoreResult,
        destination: Path,
        *,
        progress_callback: object = None,
    ) -> RunResult:
        materialize_calls.append((received_core, destination))
        return cli_run_result(RunOutcome.COMPLETED)

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli._run_canonical_core",
        fake_core,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli._materialize_canonical_result",
        fake_materialize,
    )

    result = main(
        (
            "materialize",
            "--config",
            str(config_path),
            "--journal",
            "Biometrics",
            "--window-days",
            "14",
            "--keyword-expression",
            "statistics",
            "--output-dir",
            str(output_dir),
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 0
    assert core_calls == [
        (
            config_path,
            DateRangeSpec(window_days=14),
            "Biometrics",
            "statistics",
        )
    ]
    assert materialize_calls == [(core, output_dir)]
    assert captured.out == ""
    assert "Materialization completed" in captured.err


@pytest.mark.parametrize(
    ("outcome", "expected_exit"),
    (
        (RunOutcome.COMPLETED, 0),
        (RunOutcome.COMPLETED_WITH_WARNINGS, 0),
        (RunOutcome.COMPLETED_WITH_ERRORS, 1),
    ),
)
def test_materialize_cli_maps_materialization_outcome(
    outcome: RunOutcome,
    expected_exit: int,
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    core = cli_core_result()
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli._run_canonical_core",
        lambda *args, **kwargs: core,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli._materialize_canonical_result",
        lambda *args, **kwargs: cli_run_result(outcome),
    )

    result = main(
        (
            "materialize",
            "--config",
            str(tmp_path / "monitor.yaml"),
            "--output-dir",
            str(tmp_path / "Vault"),
        )
    )

    capsys.readouterr()  # type: ignore[attr-defined]
    assert result == expected_exit


def test_export_kept_cli_writes_entries_without_entering_provider_pipeline(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    def unexpected_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider and materialization paths must not be reached")

    for name in (
        "load_config",
        "OpenAlexClient",
        "CrossrefClient",
        "discover_journals",
        "enrich_records",
        "run_monitor",
        "validate_monitor",
        "_run_canonical_core",
        "_materialize_canonical_result",
    ):
        monkeypatch.setattr(  # type: ignore[attr-defined]
            f"literature_monitor.cli.{name}", unexpected_call
        )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.export_kept_papers",
        lambda output_dir: KeptExportResult(
            entries=("10.5555/example", "arXiv:2601.01234"),
            issues=(),
        ),
    )

    result = main(("export-kept", "--output-dir", str(tmp_path)))

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 0
    assert captured.out == "10.5555/example\narXiv:2601.01234\n"
    assert "Kept export completed: 2 entries, 0 issues" in captured.err


def test_export_kept_cli_reports_issues_after_successful_entries(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    issue_path = tmp_path / "Papers" / "broken.md"
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.export_kept_papers",
        lambda output_dir: KeptExportResult(
            entries=("10.5555/valid",),
            issues=(KeptExportIssue(issue_path, "invalid durable state"),),
        ),
    )

    result = main(("export-kept", "--output-dir", str(tmp_path)))

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 1
    assert captured.out == "10.5555/valid\n"
    assert str(issue_path) in captured.err
    assert "invalid durable state" in captured.err
    assert "Kept export completed: 1 entries, 1 issues" in captured.err


def test_export_kept_cli_missing_papers_directory_is_empty_and_read_only(
    tmp_path: Path, capsys: object
) -> None:
    output_dir = tmp_path / "missing-output"

    result = main(("export-kept", "--output-dir", str(output_dir)))

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 0
    assert captured.out == ""
    assert "Kept export completed: 0 entries, 0 issues" in captured.err
    assert not output_dir.exists()
