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
from literature_monitor.config import JournalConfig, load_config
from literature_monitor.crossref import (
    CrossrefWorkRecord,
    EnrichedWorkRecord,
    EnrichmentIssue,
    EnrichmentIssueSeverity,
    EnrichmentResult,
)
from literature_monitor.kept_export import KeptExportIssue, KeptExportResult
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
    ProviderWorkEvidence,
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


def test_validate_cli_reports_resolutions_and_dynamic_counts(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    config_path, config = validate_config(tmp_path)
    client = object()
    api_keys: list[str | None] = []
    calls: list[tuple[object, JournalConfig]] = []

    def fake_client(*, api_key: str | None = None) -> object:
        api_keys.append(api_key)
        return client

    def fake_resolve(
        received_client: object,
        journal: JournalConfig,
    ) -> tuple[ResolvedSource, tuple[DiscoveryIssue, ...]]:
        calls.append((received_client, journal))
        return resolved_source(journal), ()

    def unexpected_discovery(*args: object, **kwargs: object) -> object:
        raise AssertionError("validate must not request Works discovery")

    monkeypatch.setenv("OPENALEX_API_KEY", "test-key")  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.OpenAlexClient", fake_client
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.resolve_journal_source", fake_resolve
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.discover_journals", unexpected_discovery
    )

    assert main(("validate", "--config", str(config_path))) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert api_keys == ["test-key"]
    assert [journal for _, journal in calls] == list(config.journals)  # type: ignore[attr-defined]
    assert all(received_client is client for received_client, _ in calls)
    assert "resolved Biometrics to Biometrics" in captured.err
    assert "resolved Annals of Statistics to Annals of Statistics" in captured.err
    assert "2 configured journals, 3 configured ISSNs, 2 resolved sources" in captured.err
    assert "0 warnings, 0 errors" in captured.err


def test_validate_cli_resolution_error_does_not_stop_later_journal(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    config_path, config = validate_config(tmp_path)
    calls: list[str] = []

    def fake_resolve(
        client: object,
        journal: JournalConfig,
    ) -> tuple[ResolvedSource | None, tuple[DiscoveryIssue, ...]]:
        calls.append(journal.name)
        if journal.name == "Biometrics":
            return None, (
                DiscoveryIssue(
                    severity=IssueSeverity.ERROR,
                    stage="source_resolution",
                    journal=journal.name,
                    message="configured ISSNs resolve to conflicting Sources",
                ),
            )
        return resolved_source(journal), ()

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.resolve_journal_source", fake_resolve
    )

    assert main(("validate", "--config", str(config_path))) == 1
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert calls == [journal.name for journal in config.journals]  # type: ignore[attr-defined]
    assert "configured ISSNs resolve to conflicting Sources" in captured.err
    assert "resolved Annals of Statistics to Annals of Statistics" in captured.err
    assert "1 resolved sources, 0 warnings, 1 errors" in captured.err


def test_validate_cli_warning_only_resolution_returns_zero(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    config_path, _config = validate_config(tmp_path)

    def fake_resolve(
        client: object,
        journal: JournalConfig,
    ) -> tuple[ResolvedSource, tuple[DiscoveryIssue, ...]]:
        issues: tuple[DiscoveryIssue, ...] = ()
        if journal.name == "Biometrics":
            issues = (
                DiscoveryIssue(
                    severity=IssueSeverity.WARNING,
                    stage="source_resolution",
                    journal=journal.name,
                    issn=journal.issn[1],
                    message="ISSN is unresolved; using the consistent Source",
                ),
            )
        return resolved_source(journal), issues

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "literature_monitor.cli.resolve_journal_source", fake_resolve
    )

    assert main(("validate", "--config", str(config_path))) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "WARNING" in captured.err
    assert "ISSN 1541-0420" in captured.err
    assert "2 resolved sources, 1 warnings, 0 errors" in captured.err


def test_validate_cli_returns_two_and_logs_configuration_error(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    config_path = tmp_path / "bad.yaml"
    config_path.write_text("keyword_expression: alpha\n", encoding="utf-8")

    def unexpected_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider path must not be reached")

    for name in ("OpenAlexClient", "resolve_journal_source", "discover_journals"):
        monkeypatch.setattr(  # type: ignore[attr-defined]
            f"literature_monitor.cli.{name}", unexpected_call
        )

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
    received_by_canonicalization: list[ProviderWorkEvidence] = []
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
            records=(
                EnrichedWorkRecord(
                    openalex=records[0],
                    crossref=crossref_record("10.5555/one"),
                ),
                EnrichedWorkRecord(openalex=records[1]),
            ),
            issues=(),
        )

    def fake_canonicalize(
        records: tuple[ProviderWorkEvidence, ...],
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
    assert [
        record.provenance.provider for record in received_by_canonicalization
    ] == ["openalex", "crossref", "openalex"]
    assert received_by_canonicalization[1].supplements[0].record_id == (
        received_by_enrichment[0].provenance.record_id
    )
    assert len(rows) == 1
    assert rows[0]["metadata"]["title"] == "Canonical paper"
    assert rows[0]["preferred_version"] == {
        "source": "doi",
        "identifier": "10.5555/one",
    }
    assert "2 retained, 1 enriched, 1 canonical papers" in captured.err


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
        records: tuple[ProviderWorkEvidence, ...],
    ) -> CanonicalizationResult:
        events.append("canonicalize")
        assert [record.external_ids.openalex for record in records] == [
            record.external_ids.openalex for record in received_by_enrichment
        ]
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
        "canonicalize_records",
        "materialize_papers",
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
