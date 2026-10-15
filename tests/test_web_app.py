"""Web startup and host-safety checks for the five-page UI."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import literature_monitor.web.app as web_app
from literature_monitor.web.app import create_app


def write_valid_config(tmp_path: Path) -> tuple[Path, Path]:
    journal_path = tmp_path / "list.md"
    journal_path.write_text(
        '# Venues\n\n## Journals\n\n| Journal | ISSN-L | Publisher ID | Group |\n|---|---|---|---|\n| Biometrics | 0006-341X |  |  |\n',
        encoding="utf-8",
    )
    config_path = tmp_path / "monitor.yaml"
    config_path.write_text(
        """name: Web Monitor
venue_whitelist: list.md
keyword_expression: causal
output_dir: workspace
from_month: 2026-01\nto_month: 2026-01
log_level: INFO
""",
        encoding="utf-8",
    )
    return config_path, tmp_path / "workspace"


def test_application_construction_does_not_load_config_or_call_providers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("construction must not resolve config or providers")

    monkeypatch.setattr(web_app, "load_config", forbidden)
    monkeypatch.setattr(web_app, "load_settings", forbidden)
    monkeypatch.setattr(web_app, "load_workspace", forbidden)

    app = create_app(tmp_path / "monitor.yaml")

    assert app is not None


@pytest.mark.parametrize(
    "host",
    ("localhost", "localhost:8000", "127.0.0.1", "127.0.0.1:8000"),
)
def test_local_hosts_are_accepted(tmp_path: Path, host: str) -> None:
    app = create_app(tmp_path / "missing.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/", headers={"host": host})

    assert response.status_code == 200


def test_external_host_is_rejected(tmp_path: Path) -> None:
    app = create_app(tmp_path / "missing.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/", headers={"host": "external.example"})

    assert response.status_code == 400


def test_testclient_default_testserver_host_is_rejected(tmp_path: Path) -> None:
    app = create_app(tmp_path / "missing.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/", headers={"host": "testserver"})

    assert response.status_code == 400


def test_no_broad_cors_is_enabled(tmp_path: Path) -> None:
    app = create_app(tmp_path / "missing.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.options(
            "/",
            headers={
                "host": "localhost",
                "origin": "https://external.example",
                "access-control-request-method": "GET",
            },
        )

    assert "access-control-allow-origin" not in response.headers
