"""Small reusable filesystem primitives for exact text replacement."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


class ContentChangedError(RuntimeError):
    """The target no longer matches the contents previously read by the caller."""


class CompareReadError(RuntimeError):
    """The target could not be reread before a compare-and-replace operation."""


def read_text_exact(path: Path) -> str:
    """Read UTF-8 without newline or Unicode normalization."""

    return path.read_bytes().decode("utf-8")


def create_text_exclusive(path: Path, contents: str) -> None:
    """Create complete UTF-8 text without replacing a concurrently created path."""

    created = False
    try:
        with path.open("x", encoding="utf-8", newline="") as handle:
            created = True
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise


def atomic_create_text(path: Path, contents: str) -> None:
    """Atomically create complete UTF-8 text without replacing an existing path."""

    temporary: Path | None = None
    descriptor: int | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="")
        descriptor = None
        with handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())

        # 先完成同目录临时文件，再用硬链接建立目标，避免首次创建时暴露半写文件。
        # 目标已存在时 link 会失败，因此不会覆盖并发创建的用户对象。
        os.link(temporary, path)
        temporary.unlink()
        temporary = None
    except Exception:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def atomic_replace_text(path: Path, contents: str) -> None:
    """Replace a path with complete UTF-8 text using a same-directory temp file."""

    temporary: Path | None = None
    descriptor: int | None = None
    try:
        mode = stat.S_IMODE(path.stat().st_mode)

        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="")
        descriptor = None
        with handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        temporary = None
    except Exception:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def replace_text_if_unchanged(
    path: Path,
    contents: str,
    *,
    expected_contents: str,
) -> None:
    """Replace only when the target still exactly matches a prior UTF-8 read."""

    try:
        current = read_text_exact(path)
    except (OSError, UnicodeError) as error:
        raise CompareReadError(str(error)) from error
    if current != expected_contents:
        raise ContentChangedError("target changed after it was read")
    atomic_replace_text(path, contents)
