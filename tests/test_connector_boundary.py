"""Structural checks for the independent Literature Monitor Connector boundary."""

import os
import re
import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONNECTOR = ROOT / "connector"
PACKAGE = ROOT / "src" / "literature_monitor"
BUILD_SCRIPT = CONNECTOR / "build.sh"


def _read_upstream_lock() -> tuple[str, str, dict[str, str]]:
    repository = ""
    revision = ""
    submodules: dict[str, str] = {}
    for raw_line in (CONNECTOR / "upstream.lock").read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        kind, path_or_repository, commit = line.split()
        if kind == "upstream":
            repository = path_or_repository
            revision = commit
        elif kind == "submodule":
            submodules[path_or_repository] = commit
        else:
            raise AssertionError(f"unknown upstream.lock entry: {line}")
    return repository, revision, submodules


def test_connector_is_distinct_from_retired_browser_companion():
    assert CONNECTOR.is_dir()
    assert not (ROOT / "browser_companion").exists()
    assert CONNECTOR.parent == ROOT
    assert PACKAGE not in CONNECTOR.parents


def test_connector_upstream_is_exactly_pinned():
    repository, revision, submodules = _read_upstream_lock()
    assert repository == "https://github.com/zotero/zotero-connectors.git"
    assert re.fullmatch(r"[0-9a-f]{40}", revision)
    assert submodules
    assert all(
        re.fullmatch(r"[0-9a-f]{40}", commit) for commit in submodules.values()
    )


def test_connector_license_is_separate_from_python_mit_project():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["license"] == "MIT"
    connector_copying = (CONNECTOR / "COPYING").read_text()
    assert "GNU Affero General Public License, version 3 (AGPLv3)" in connector_copying
    assert (ROOT / "LICENSE").read_text() != connector_copying


def test_connector_delta_does_not_live_in_python_package():
    delta = (CONNECTOR / "delta.lock").read_text()
    assert "src/literature_monitor" not in delta
    assert not any(path.name == "connector" for path in PACKAGE.rglob("*"))


def test_connector_build_pins_release_version():
    script = BUILD_SCRIPT.read_text()
    assert 'CONNECTOR_VERSION="0.6.4"' in script
    assert './build.sh -d -v "$CONNECTOR_VERSION"' in script


def _run_build_with_fake_git(
    tmp_path: Path,
    *,
    work_root: Path | str,
    output_dir: Path | str,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(exist_ok=True)
    marker = tmp_path / "git-called"
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        "printf 'git %s\\n' \"$*\" >> \"$LM_TEST_GIT_MARKER\"\n"
        "exit 97\n"
    )
    fake_git.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["LM_TEST_GIT_MARKER"] = str(marker)
    env["LM_CONNECTOR_WORK_DIR"] = str(work_root)
    env["LM_CONNECTOR_OUTPUT_DIR"] = str(output_dir)
    result = subprocess.run(
        [str(BUILD_SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, marker


def test_existing_external_output_is_rejected_without_mutation(tmp_path: Path):
    output_dir = tmp_path / "existing-output"
    output_dir.mkdir()
    sentinel = output_dir / "sentinel.txt"
    sentinel.write_text("keep me")

    result, marker = _run_build_with_fake_git(
        tmp_path,
        work_root=tmp_path / "fresh-work",
        output_dir=output_dir,
    )

    assert result.returncode != 0
    assert "external output directory must not already exist" in result.stderr
    assert sentinel.read_text() == "keep me"
    assert not marker.exists()


def test_existing_external_upstream_source_is_rejected_without_mutation(
    tmp_path: Path,
):
    work_root = tmp_path / "existing-work"
    source_dir = work_root / "upstream"
    source_dir.mkdir(parents=True)
    sentinel = source_dir / "sentinel.txt"
    sentinel.write_text("keep me")

    result, marker = _run_build_with_fake_git(
        tmp_path,
        work_root=work_root,
        output_dir=tmp_path / "fresh-output",
    )

    assert result.returncode != 0
    assert "external upstream source target must not already exist" in result.stderr
    assert sentinel.read_text() == "keep me"
    assert not marker.exists()


def test_external_upstream_source_symlink_is_rejected_without_following_target(
    tmp_path: Path,
):
    work_root = tmp_path / "existing-work"
    work_root.mkdir()
    target = tmp_path / "unrelated-source"
    target.mkdir()
    sentinel = target / "sentinel.txt"
    sentinel.write_text("keep me")
    (work_root / "upstream").symlink_to(target, target_is_directory=True)

    result, marker = _run_build_with_fake_git(
        tmp_path,
        work_root=work_root,
        output_dir=tmp_path / "fresh-output",
    )

    assert result.returncode != 0
    assert "external upstream source target must not already exist" in result.stderr
    assert sentinel.read_text() == "keep me"
    assert not marker.exists()


def test_dangerous_repository_and_connector_output_equivalents_are_rejected(
    tmp_path: Path,
):
    dangerous_outputs = (
        CONNECTOR / "..",
        ROOT / "connector" / ".",
    )
    for index, output_dir in enumerate(dangerous_outputs):
        result, marker = _run_build_with_fake_git(
            tmp_path,
            work_root=tmp_path / f"fresh-work-{index}",
            output_dir=output_dir,
        )
        assert result.returncode != 0
        assert "external output directory must not already exist" in result.stderr
        assert not marker.exists()


def test_external_output_symlink_is_rejected_without_following_target(tmp_path: Path):
    target = tmp_path / "unrelated-target"
    target.mkdir()
    sentinel = target / "sentinel.txt"
    sentinel.write_text("keep me")
    output_link = tmp_path / "output-link"
    output_link.symlink_to(target, target_is_directory=True)

    result, marker = _run_build_with_fake_git(
        tmp_path,
        work_root=tmp_path / "fresh-work",
        output_dir=output_link,
    )

    assert result.returncode != 0
    assert "external output directory must not be a symlink" in result.stderr
    assert sentinel.read_text() == "keep me"
    assert not marker.exists()


def test_fresh_external_target_inside_another_git_worktree_is_rejected(
    tmp_path: Path,
):
    other_project = tmp_path / "other-project"
    other_project.mkdir()
    (other_project / ".git").write_text("gitdir: elsewhere\n")

    result, marker = _run_build_with_fake_git(
        tmp_path,
        work_root=tmp_path / "fresh-work",
        output_dir=other_project / "fresh-output",
    )

    assert result.returncode != 0
    assert "external output directory cannot be inside a Git worktree" in result.stderr
    assert not (other_project / "fresh-output").exists()
    assert not marker.exists()


def test_fresh_external_paths_pass_guard_before_reconstruction(tmp_path: Path):
    work_root = tmp_path / "fresh-work"
    output_dir = tmp_path / "fresh-output"

    result, marker = _run_build_with_fake_git(
        tmp_path,
        work_root=work_root,
        output_dir=output_dir,
    )

    assert result.returncode != 0
    assert marker.exists()
    assert "git init" in marker.read_text()
    assert work_root.is_dir()
    assert not output_dir.exists()
