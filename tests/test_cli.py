import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

from literature_monitor.canonicalize import (
    CanonicalizationIssue,
    CanonicalizationResult,
)
from literature_monitor.cli import main
from literature_monitor.config import load_config
from literature_monitor.crossref import (
    CrossrefWorkRecord,
    EnrichedWorkRecord,
    EnrichmentIssue,
    EnrichmentIssueSeverity,
    EnrichmentResult,
)
from literature_monitor.logging_setup import LOGGER_NAME, configure_logging
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


def application_handlers() -> list[logging.Handler]:
    return [
        handler
        for handler in logging.getLogger(LOGGER_NAME).handlers
        if getattr(handler, "_literature_monitor_handler", False)
    ]


def test_logging_configuration_is_idempotent() -> None:
    configure_logging("INFO")
    first = application_handlers()
    configure_logging("DEBUG")
    second = application_handlers()

    assert len(first) == len(second) == 1
    assert first[0] is not second[0]
    assert logging.getLogger(LOGGER_NAME).level == logging.DEBUG


def test_validate_cli_reports_dynamic_counts(capsys: object) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    config_path = repository_root / "config.example.yaml"
    config = load_config(config_path)
    expected = (
        f"validated {len(config.journals)} journals and "
        f"{sum(len(journal.issn) for journal in config.journals)} ISSNs"
    )

    assert main(("validate", "--config", str(config_path))) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert expected in captured.err


def test_validate_cli_returns_two_and_logs_configuration_error(
    tmp_path: Path, capsys: object
) -> None:
    config_path = tmp_path / "bad.yaml"
    config_path.write_text("keyword_expression: alpha\n", encoding="utf-8")

    assert main(("validate", "--config", str(config_path))) == 2
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "ERROR" in captured.err
    assert "venue_whitelist" in captured.err


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
    client_sentinel = object()

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: enrichment_diagnostic_result(),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.CrossrefClient",
        lambda **kwargs: client_sentinel,
    )

    def fake_enrich(
        client: object, records: tuple[OpenAlexWorkRecord, ...]
    ) -> EnrichmentResult:
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


def test_canonicalize_runs_full_pipeline_and_emits_canonical_ndjson(
    monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    received_by_enrichment: list[OpenAlexWorkRecord] = []
    received_by_canonicalization: list[EnrichedWorkRecord] = []
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
        received_by_enrichment.extend(records)
        return EnrichmentResult(
            records=tuple(EnrichedWorkRecord(openalex=record) for record in records),
            issues=(),
        )

    def fake_canonicalize(
        records: tuple[EnrichedWorkRecord, ...],
    ) -> CanonicalizationResult:
        received_by_canonicalization.extend(records)
        return CanonicalizationResult(papers=(canonical_paper(),), issues=())

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.enrich_records", fake_enrich
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.canonicalize_records", fake_canonicalize
    )

    result = main(
        (
            "canonicalize",
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
    assert [record.external_ids.openalex for record in received_by_enrichment] == [
        "https://openalex.org/W1",
        "https://openalex.org/W3",
    ]
    assert [record.openalex for record in received_by_canonicalization] == list(
        received_by_enrichment
    )
    assert len(rows) == 1
    assert rows[0]["metadata"]["title"] == "Canonical paper"
    assert rows[0]["preferred_version"] == {
        "source": "doi",
        "identifier": "10.5555/one",
    }
    assert "2 retained, 0 enriched, 1 canonical papers" in captured.err


def test_canonicalize_override_is_applied_before_enrichment(
    monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    received: list[OpenAlexWorkRecord] = []
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
        received.extend(records)
        return EnrichmentResult(
            records=tuple(EnrichedWorkRecord(openalex=record) for record in records),
            issues=(),
        )

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.enrich_records", fake_enrich
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.canonicalize_records",
        lambda records: CanonicalizationResult(
            papers=(canonical_paper("10.5555/two"),), issues=()
        ),
    )

    result = main(
        (
            "canonicalize",
            "--config",
            str(repository_root / "config.example.yaml"),
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


@pytest.mark.parametrize(
    "arguments",
    [
        ("--from-date", "2026-01-01", "--to-date", "2026-01-31", "--keyword-expression", "alpha AND"),
        ("--from-date", "2026-02-01", "--to-date", "2026-01-01"),
    ],
)
def test_canonicalize_invalid_input_stops_before_provider_path(
    arguments: tuple[str, ...], monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]

    def unexpected_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider path must not be reached")

    for name in (
        "OpenAlexClient",
        "CrossrefClient",
        "discover_journals",
        "enrich_records",
        "canonicalize_records",
    ):
        monkeypatch.setattr(  # type: ignore[attr-defined]
            f"literature_monitor.cli.{name}", unexpected_call
        )

    result = main(
        (
            "canonicalize",
            "--config",
            str(repository_root / "config.example.yaml"),
            *arguments,
        )
    )

    capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 2


def test_canonicalize_invalid_config_stops_before_provider_path(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    config_path = tmp_path / "bad.yaml"
    config_path.write_text("keyword_expression: alpha\n", encoding="utf-8")

    def unexpected_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider path must not be reached")

    for name in (
        "OpenAlexClient",
        "CrossrefClient",
        "discover_journals",
        "enrich_records",
        "canonicalize_records",
    ):
        monkeypatch.setattr(  # type: ignore[attr-defined]
            f"literature_monitor.cli.{name}", unexpected_call
        )

    result = main(
        (
            "canonicalize",
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
    assert "venue_whitelist" in captured.err


def test_canonicalize_warnings_keep_low_confidence_outputs_and_zero_exit(
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
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.enrich_records",
        lambda client, records: EnrichmentResult(
            records=tuple(EnrichedWorkRecord(openalex=record) for record in records),
            issues=(),
        ),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.canonicalize_records",
        lambda records: CanonicalizationResult(
            papers=(canonical_paper("10.5555/a"), canonical_paper("10.5555/b")),
            issues=(
                CanonicalizationIssue(
                    stage="blocked_match",
                    message="insufficient author evidence",
                    record_ids=("W1", "W2"),
                ),
            ),
        ),
    )

    result = main(
        (
            "canonicalize",
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
    assert len(captured.out.splitlines()) == 2
    assert "blocked_match" in captured.err
    assert "2 canonical papers, 1 canonicalization issues" in captured.err


def test_canonicalize_partial_upstream_failure_still_emits_successful_papers(
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
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.canonicalize_records",
        lambda records: CanonicalizationResult(
            papers=(canonical_paper(),), issues=()
        ),
    )

    result = main(
        (
            "canonicalize",
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
    assert json.loads(captured.out)["metadata"]["title"] == "Canonical paper"
    assert "bad record" in captured.err


def test_canonicalize_crossref_error_still_emits_successful_papers(
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
                    severity=EnrichmentIssueSeverity.ERROR,
                    stage="request_failure",
                    message="server unavailable",
                    record_id=records[0].external_ids.openalex,
                    doi=records[0].external_ids.doi,
                ),
            ),
        )

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.enrich_records", fake_enrich
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.canonicalize_records",
        lambda records: CanonicalizationResult(
            papers=(canonical_paper(),), issues=()
        ),
    )

    result = main(
        (
            "canonicalize",
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
    assert json.loads(captured.out)["metadata"]["title"] == "Canonical paper"
    assert "server unavailable" in captured.err


def test_materialize_runs_full_pipeline_in_order_without_stdout(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    output_dir = tmp_path / "Vault"
    events: list[str] = []
    received_by_enrichment: list[OpenAlexWorkRecord] = []
    received_by_materialization: list[CanonicalPaper] = []
    canonical = canonical_paper("10.5555/two")

    def fake_discover(*args: object) -> DiscoveryResult:
        events.append("discover")
        return enrichment_diagnostic_result()

    def fake_enrich(
        client: object, records: tuple[OpenAlexWorkRecord, ...]
    ) -> EnrichmentResult:
        events.append("enrich")
        received_by_enrichment.extend(records)
        return EnrichmentResult(
            records=tuple(EnrichedWorkRecord(openalex=record) for record in records),
            issues=(),
        )

    def fake_canonicalize(
        records: tuple[EnrichedWorkRecord, ...],
    ) -> CanonicalizationResult:
        events.append("canonicalize")
        assert [record.openalex for record in records] == received_by_enrichment
        return CanonicalizationResult(papers=(canonical,), issues=())

    def fake_materialize(
        papers: tuple[CanonicalPaper, ...], destination: Path
    ) -> MaterializationResult:
        events.append("materialize")
        received_by_materialization.extend(papers)
        assert destination == output_dir
        return MaterializationResult(
            created_papers=(destination / "Papers" / "paper.md",),
            existing_papers=(),
            updated_papers=(),
            created_authors=(destination / "Authors" / "author.md",),
            existing_authors=(),
            issues=(),
        )

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals", fake_discover
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.CrossrefClient", lambda **kwargs: object()
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.enrich_records", fake_enrich
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.canonicalize_records", fake_canonicalize
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.materialize_papers", fake_materialize
    )

    result = main(
        (
            "materialize",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
            "--keyword-expression",
            '"excluded paper"',
            "--output-dir",
            str(output_dir),
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 0
    assert events == ["discover", "enrich", "canonicalize", "materialize"]
    assert [record.external_ids.openalex for record in received_by_enrichment] == [
        "https://openalex.org/W2"
    ]
    assert received_by_materialization == [canonical]
    assert captured.out == ""
    assert "1 canonical papers, 1 paper files created" in captured.err
    assert "1 author files created, 0 author files existing" in captured.err


@pytest.mark.parametrize(
    "arguments",
    [
        (
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
            "--keyword-expression",
            "alpha AND",
        ),
        ("--from-date", "2026-02-01", "--to-date", "2026-01-01"),
    ],
)
def test_materialize_invalid_input_stops_before_provider_and_materializer(
    arguments: tuple[str, ...],
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]

    def unexpected_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider and materializer paths must not be reached")

    for name in (
        "OpenAlexClient",
        "CrossrefClient",
        "discover_journals",
        "enrich_records",
        "canonicalize_records",
        "materialize_papers",
    ):
        monkeypatch.setattr(  # type: ignore[attr-defined]
            f"literature_monitor.cli.{name}", unexpected_call
        )

    result = main(
        (
            "materialize",
            "--config",
            str(repository_root / "config.example.yaml"),
            *arguments,
            "--output-dir",
            str(tmp_path),
        )
    )

    capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 2


def test_materialize_invalid_config_stops_before_provider_and_materializer(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    config_path = tmp_path / "bad.yaml"
    config_path.write_text("keyword_expression: alpha\n", encoding="utf-8")

    def unexpected_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider and materializer paths must not be reached")

    for name in (
        "OpenAlexClient",
        "discover_journals",
        "materialize_papers",
    ):
        monkeypatch.setattr(  # type: ignore[attr-defined]
            f"literature_monitor.cli.{name}", unexpected_call
        )

    result = main(
        (
            "materialize",
            "--config",
            str(config_path),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
            "--output-dir",
            str(tmp_path / "Vault"),
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 2
    assert "venue_whitelist" in captured.err


def test_materialize_canonicalization_warning_is_nonfatal(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    canonical = canonical_paper()
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: enrichment_diagnostic_result(),
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
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.canonicalize_records",
        lambda records: CanonicalizationResult(
            papers=(canonical,),
            issues=(
                CanonicalizationIssue(
                    stage="blocked_match",
                    message="insufficient evidence",
                    record_ids=("W1", "W2"),
                ),
            ),
        ),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.materialize_papers",
        lambda papers, output_dir: MaterializationResult(
            created_papers=(output_dir / "Papers" / "paper.md",),
            existing_papers=(),
            updated_papers=(),
            created_authors=(),
            existing_authors=(),
            issues=(),
        ),
    )

    result = main(
        (
            "materialize",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
            "--output-dir",
            str(tmp_path),
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 0
    assert captured.out == ""
    assert "blocked_match" in captured.err
    assert "1 canonicalization issues" in captured.err


@pytest.mark.parametrize("upstream", ["openalex", "crossref"])
def test_materialize_partial_upstream_error_still_materializes_papers(
    upstream: str, tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    received: list[CanonicalPaper] = []
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: enrichment_diagnostic_result(with_error=upstream == "openalex"),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.CrossrefClient", lambda **kwargs: object()
    )
    enrichment_issues = (
        EnrichmentIssue(
            severity=EnrichmentIssueSeverity.ERROR,
            stage="request_failure",
            message="server unavailable",
            record_id="https://openalex.org/W1",
            doi="10.5555/one",
        ),
    ) if upstream == "crossref" else ()
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.enrich_records",
        lambda client, records: EnrichmentResult(
            records=tuple(EnrichedWorkRecord(openalex=record) for record in records),
            issues=enrichment_issues,
        ),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.canonicalize_records",
        lambda records: CanonicalizationResult(
            papers=(canonical_paper(),), issues=()
        ),
    )

    def fake_materialize(
        papers: tuple[CanonicalPaper, ...], output_dir: Path
    ) -> MaterializationResult:
        received.extend(papers)
        return MaterializationResult((), (), (), (), (), ())

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.materialize_papers", fake_materialize
    )

    result = main(
        (
            "materialize",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
            "--output-dir",
            str(tmp_path),
        )
    )

    capsys.readouterr()  # type: ignore[attr-defined]
    assert result == 1
    assert len(received) == 1


@pytest.mark.parametrize(
    ("severity", "expected_exit"),
    [
        (MaterializationIssueSeverity.WARNING, 0),
        (MaterializationIssueSeverity.ERROR, 1),
    ],
)
def test_materialize_issue_severity_controls_exit_code(
    severity: MaterializationIssueSeverity,
    expected_exit: int,
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    issue_path = tmp_path / "Papers" / "failed.md"
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals",
        lambda *args: enrichment_diagnostic_result(),
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
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.canonicalize_records",
        lambda records: CanonicalizationResult(
            papers=(canonical_paper(),), issues=()
        ),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.materialize_papers",
        lambda papers, output_dir: MaterializationResult(
            created_papers=(),
            existing_papers=(),
            updated_papers=(),
            created_authors=(),
            existing_authors=(),
            issues=(
                MaterializationIssue(
                    issue_path,
                    "materialization diagnostic",
                    severity,
                ),
            ),
        ),
    )

    result = main(
        (
            "materialize",
            "--config",
            str(repository_root / "config.example.yaml"),
            "--from-date",
            "2026-01-01",
            "--to-date",
            "2026-01-31",
            "--output-dir",
            str(tmp_path),
        )
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert result == expected_exit
    assert "materialization diagnostic" in captured.err
    expected_label = (
        "1 materialization warnings, 0 materialization errors"
        if severity is MaterializationIssueSeverity.WARNING
        else "0 materialization warnings, 1 materialization errors"
    )
    assert expected_label in captured.err
