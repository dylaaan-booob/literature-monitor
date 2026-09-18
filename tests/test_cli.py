import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from literature_monitor.cli import main
from literature_monitor.config import load_config
from literature_monitor.logging_setup import LOGGER_NAME, configure_logging
from literature_monitor.models import Author, CanonicalMetadata, ExternalIds, MetadataSource
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
