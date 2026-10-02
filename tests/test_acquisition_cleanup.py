"""Structural regressions for the completed acquisition replacement."""

import ast
import importlib.util
import inspect
from pathlib import Path
import tomllib

from literature_monitor.application.acquisition import AcquisitionService


ROOT = Path(__file__).resolve().parents[1]
LEGACY_MODULES = {"institutional_browser", "institutional_resolver", "pdf_acquisition"}


def test_legacy_modules_and_service_entry_point_are_removed():
    for name in LEGACY_MODULES:
        assert not (ROOT / "src" / "literature_monitor" / f"{name}.py").exists()
        assert importlib.util.find_spec(f"literature_monitor.{name}") is None
    parameters = inspect.signature(AcquisitionService).parameters
    assert "resolver" not in parameters and "pdf_acquirer" not in parameters
    assert not hasattr(AcquisitionService, "acquire")


def test_project_has_no_legacy_browser_dependencies():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    for dependency in project["dependencies"]:
        assert not dependency.lower().startswith(("playwright", "platformdirs"))


def test_production_imports_do_not_restore_legacy_acquisition():
    forbidden = LEGACY_MODULES | {"playwright", "platformdirs"}
    for path in (ROOT / "src" / "literature_monitor").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or "", *(alias.name for alias in node.names)]
            for name in names:
                assert not forbidden.intersection(name.split(".")), (path, name)
