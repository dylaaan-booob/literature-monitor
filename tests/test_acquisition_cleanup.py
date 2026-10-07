"""Structural regressions for retired custom acquisition/browser production paths."""

import ast
import importlib.util
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "literature_monitor"
RETIRED_MODULES = {
    "literature_monitor.application.acquisition",
    "literature_monitor.application.zotero_linkage",
    "literature_monitor.browser_acquisition",
    "literature_monitor.pdf_staging",
    "literature_monitor.web.acquisition_coordinator",
    "literature_monitor.web.browser_handoff",
    "literature_monitor.web.chrome_launcher",
    "literature_monitor.zotero_credentials",
    "literature_monitor.zotero_write",
}
RETIRED_PATHS = {
    PACKAGE / "application" / "acquisition.py",
    PACKAGE / "application" / "zotero_linkage.py",
    PACKAGE / "browser_acquisition.py",
    PACKAGE / "pdf_staging.py",
    PACKAGE / "web" / "acquisition_coordinator.py",
    PACKAGE / "web" / "browser_handoff.py",
    PACKAGE / "web" / "chrome_launcher.py",
    PACKAGE / "web" / "templates" / "browser_handoff.html",
    PACKAGE / "web" / "templates" / "fragments" / "acquisition.html",
    PACKAGE / "zotero_credentials.py",
    PACKAGE / "zotero_write.py",
}


def test_retired_acquisition_artifacts_are_absent():
    assert not (ROOT / "browser_companion").exists()
    for path in RETIRED_PATHS:
        assert not path.exists(), path
    for module in RETIRED_MODULES:
        assert importlib.util.find_spec(module) is None, module


def test_production_imports_do_not_restore_retired_acquisition():
    for path in PACKAGE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not any(
                    name == retired or name.startswith(f"{retired}.")
                    for retired in RETIRED_MODULES
                ), (path, name)


def test_web_surface_has_no_retired_acquisition_routes_or_ui():
    app_source = (PACKAGE / "web" / "app.py").read_text()
    detail = (
        PACKAGE / "web" / "templates" / "fragments" / "paper_detail.html"
    ).read_text()
    for marker in (
        "/browser-handoff/",
        "/browser-handoff/claim",
        "/browser-handoff/events",
        "/fragments/acquisition/",
        "/acquire-pdf",
        "/acquisition/{action}",
        "/settings/zotero/authorize",
        "/mark-in-zotero",
        "/api/local/authorize",
        "acquisitionCompleted",
        "Add PDF to Zotero",
        "Copy DOI",
        "Mark in Zotero",
    ):
        assert marker not in app_source + detail, marker


def test_project_has_no_retired_runtime_dependencies():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    for dependency in project["dependencies"]:
        assert not dependency.lower().startswith(("playwright", "platformdirs", "keyring"))
