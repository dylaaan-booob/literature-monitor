from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import uvicorn

import literature_monitor.cli as cli
import literature_monitor.web.app as web_app


def test_gui_parser_exposes_required_config() -> None:
    args = cli._build_parser().parse_args(
        ("gui", "--config", "monitor.yaml")
    )

    assert args.command == "gui"
    assert args.config == Path("monitor.yaml")
    assert not hasattr(args, "from_date")
    assert not hasattr(args, "to_date")
    assert not hasattr(args, "window_days")


def test_gui_parser_requires_config() -> None:
    with pytest.raises(SystemExit) as captured:
        cli._build_parser().parse_args(("gui",))

    assert captured.value.code == 2


@pytest.mark.parametrize(
    "forbidden_arguments",
    (
        ("--from-date", "2026-01-01"),
        ("--to-date", "2026-01-31"),
        ("--window-days", "30"),
        ("--host", "0.0.0.0"),
        ("--bind", "0.0.0.0"),
        ("--listen", "0.0.0.0"),
        ("--port", "9999"),
    ),
)
def test_gui_parser_rejects_date_network_and_port_overrides(
    forbidden_arguments: tuple[str, str],
) -> None:
    with pytest.raises(SystemExit) as captured:
        cli._build_parser().parse_args(
            ("gui", "--config", "monitor.yaml", *forbidden_arguments)
        )

    assert captured.value.code == 2


def test_gui_dispatch_creates_existing_app_and_runs_loopback_uvicorn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "monitor.yaml"
    sentinel_app = object()
    created: list[Path] = []
    runs: list[tuple[object, dict[str, Any]]] = []

    def fake_create_app(path: Path) -> object:
        created.append(path)
        return sentinel_app

    def fake_run(app: object, **kwargs: Any) -> None:
        runs.append((app, kwargs))

    monkeypatch.setattr(web_app, "create_app", fake_create_app)
    monkeypatch.setattr(uvicorn, "run", fake_run)

    result = cli.main(("gui", "--config", str(config_path)))

    assert result == 0
    assert created == [config_path]
    assert runs == [
        (
            sentinel_app,
            {
                "host": "127.0.0.1",
                "port": 8000,
                "workers": 1,
            },
        )
    ]


@pytest.mark.parametrize("mode", ("missing", "malformed"))
def test_gui_invalid_configuration_still_reaches_uvicorn(
    mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "monitor.yaml"
    if mode == "malformed":
        config_path.write_text("keyword_expression: [\n", encoding="utf-8")

    started_apps: list[object] = []

    def fake_run(app: object, **kwargs: Any) -> None:
        started_apps.append(app)

    def forbidden_run_monitor(*args: object, **kwargs: object) -> object:
        raise AssertionError("GUI launcher must not call run_monitor")

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(cli, "run_monitor", forbidden_run_monitor)

    result = cli.main(("gui", "--config", str(config_path)))

    assert result == 0
    assert len(started_apps) == 1
    app = started_apps[0]
    assert app.state.config_path == config_path.resolve()


def test_gui_help_is_local_only_and_has_no_date_or_network_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as captured:
        cli._build_parser().parse_args(("gui", "--help"))

    assert captured.value.code == 0
    help_text = capsys.readouterr().out
    assert "--config" in help_text
    for forbidden in (
        "--from-date",
        "--to-date",
        "--window-days",
        "--host",
        "--bind",
        "--listen",
        "--port",
    ):
        assert forbidden not in help_text


def test_top_level_help_exposes_gui() -> None:
    help_text = cli._build_parser().format_help()

    assert "gui" in help_text
    assert "launch the local Web UI" in help_text
