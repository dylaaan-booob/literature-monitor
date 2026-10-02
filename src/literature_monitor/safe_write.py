"""Small reusable filesystem primitives for exact text replacement."""

from __future__ import annotations

import errno
import os
import stat
import tempfile
from pathlib import Path
from uuid import uuid4


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


def replace_regular_text_if_unchanged(
    path: Path,
    contents: str,
    *,
    expected_contents: str,
) -> None:
    """Compare a regular target without following a substituted symlink.

    This retains the existing compare/replace model, not an OS-level CAS.
    """

    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise CompareReadError("No-follow file comparison is unavailable on this platform")
    descriptor: int | None = None
    try:
        # Nonblocking open lets fstat reject a FIFO without waiting for a writer.
        descriptor = os.open(path, os.O_RDONLY | nofollow | os.O_NONBLOCK)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ContentChangedError("target is no longer a regular file")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            current = handle.read().decode("utf-8")
            located = path.lstat()
            if (
                not stat.S_ISREG(located.st_mode)
                or (located.st_dev, located.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise ContentChangedError("target location changed during comparison")
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOENT, errno.ENOTDIR):
            raise ContentChangedError("target location changed before comparison") from error
        raise CompareReadError("Cannot read the regular target for comparison") from error
    except UnicodeError as error:
        raise CompareReadError("Cannot decode the regular target for comparison") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if current != expected_contents:
        raise ContentChangedError("target changed after it was read")
    atomic_replace_text(path, contents)


def replace_regular_text_at_identity(
    path: Path,
    contents: str,
    *,
    expected_contents: str,
    expected_directory_identity: tuple[int, int],
    expected_file_identity: tuple[int, int],
) -> None:
    """Legacy acquisition compare/write bound to the original action objects.

    All file operations use the verified directory descriptor. This retains the
    compare/replace model, not an OS-level transactional CAS.
    """
    directory: int | None = None
    descriptor: int | None = None
    temporary: str | None = None
    try:
        try:
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            parent = os.fstat(directory)
            if (parent.st_dev, parent.st_ino) != expected_directory_identity:
                raise ContentChangedError("original directory identity changed")
            descriptor = os.open(
                path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory,
            )
            target = os.fstat(descriptor)
            if (not stat.S_ISREG(target.st_mode)
                    or (target.st_dev, target.st_ino) != expected_file_identity):
                raise ContentChangedError("original regular file identity changed")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = None
                current = handle.read()
        except OSError as error:
            if error.errno in (errno.ELOOP, errno.ENOENT, errno.ENOTDIR):
                raise ContentChangedError("original location changed before comparison") from error
            raise CompareReadError("Cannot read the original target for comparison") from error
        if current != expected_contents.encode("utf-8"):
            raise ContentChangedError("original target contents changed")

        def check_location() -> None:
            try:
                parent = path.parent.lstat()
                target = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            except OSError as error:
                if error.errno in (errno.ELOOP, errno.ENOENT, errno.ENOTDIR):
                    raise ContentChangedError("original location disappeared") from error
                raise CompareReadError("Cannot verify the original location") from error
            if (not stat.S_ISDIR(parent.st_mode)
                    or (parent.st_dev, parent.st_ino) != expected_directory_identity
                    or not stat.S_ISREG(target.st_mode)
                    or (target.st_dev, target.st_ino) != expected_file_identity):
                raise ContentChangedError("original location changed during comparison")

        check_location()
        # Create and replace relative to the same directory; never traverse the
        # parent path again for temporary creation, replacement, or cleanup.
        name = f".{path.name}.{uuid4().hex}.tmp"
        descriptor = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory,
        )
        temporary = name
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(contents.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), stat.S_IMODE(target.st_mode))
        check_location()
        os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        temporary = None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=directory)
            except OSError:
                pass
        if directory is not None:
            os.close(directory)
