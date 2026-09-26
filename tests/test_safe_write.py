from __future__ import annotations

import stat
from pathlib import Path

import pytest

import literature_monitor.safe_write as safe_write_module
from literature_monitor.safe_write import (
    ContentChangedError,
    atomic_create_text,
    atomic_replace_text,
    create_text_exclusive,
    read_text_exact,
    replace_text_if_unchanged,
)


def test_read_text_exact_preserves_utf8_and_newlines(tmp_path: Path) -> None:
    path = tmp_path / "state.txt"
    payload = "α\r\nβ\n終"
    path.write_bytes(payload.encode("utf-8"))

    assert read_text_exact(path) == payload


def test_atomic_create_writes_complete_target_content(tmp_path: Path) -> None:
    path = tmp_path / "state.txt"

    atomic_create_text(path, "new\n完整内容\n")

    assert path.read_bytes() == "new\n完整内容\n".encode("utf-8")
    assert tuple(tmp_path.glob(".state.txt.*.tmp")) == ()


def test_atomic_create_refuses_existing_target_without_replacement(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.txt"
    path.write_text("external", encoding="utf-8")

    with pytest.raises(FileExistsError):
        atomic_create_text(path, "generated")

    assert path.read_text(encoding="utf-8") == "external"
    assert tuple(tmp_path.glob(".state.txt.*.tmp")) == ()


def test_atomic_replace_writes_complete_target_content(tmp_path: Path) -> None:
    path = tmp_path / "state.txt"
    path.write_text("old", encoding="utf-8")

    atomic_replace_text(path, "new\n完整内容\n")

    assert path.read_bytes() == "new\n完整内容\n".encode("utf-8")


def test_atomic_replace_preserves_existing_mode(tmp_path: Path) -> None:
    path = tmp_path / "state.txt"
    path.write_text("old", encoding="utf-8")
    path.chmod(0o640)

    atomic_replace_text(path, "new")

    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_exclusive_create_writes_complete_content(tmp_path: Path) -> None:
    path = tmp_path / "new.txt"

    create_text_exclusive(path, "created\n完整内容\n")

    assert path.read_bytes() == "created\n完整内容\n".encode("utf-8")


def test_exclusive_create_refuses_existing_target(tmp_path: Path) -> None:
    path = tmp_path / "existing.txt"
    path.write_text("external", encoding="utf-8")

    with pytest.raises(FileExistsError):
        create_text_exclusive(path, "generated")

    assert path.read_text(encoding="utf-8") == "external"


def test_compare_before_replace_succeeds_when_contents_are_unchanged(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.txt"
    path.write_bytes(b"original\r\n")

    replace_text_if_unchanged(
        path,
        "updated\n",
        expected_contents="original\r\n",
    )

    assert path.read_bytes() == b"updated\n"


def test_compare_before_replace_refuses_external_edit(tmp_path: Path) -> None:
    path = tmp_path / "state.txt"
    path.write_text("original", encoding="utf-8")
    expected = read_text_exact(path)
    path.write_text("external edit", encoding="utf-8")

    with pytest.raises(ContentChangedError):
        replace_text_if_unchanged(
            path,
            "generated update",
            expected_contents=expected,
        )

    assert path.read_text(encoding="utf-8") == "external edit"


def test_replace_failure_preserves_original_and_cleans_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state.txt"
    path.write_text("original", encoding="utf-8")

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(safe_write_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        atomic_replace_text(path, "updated")

    assert path.read_text(encoding="utf-8") == "original"
    assert tuple(tmp_path.glob(f".{path.name}.*.tmp")) == ()
