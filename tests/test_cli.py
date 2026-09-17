import logging
from pathlib import Path

from literature_monitor.cli import main
from literature_monitor.config import load_config
from literature_monitor.logging_setup import LOGGER_NAME, configure_logging


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
