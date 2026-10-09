"""Small reusable filesystem primitives for exact text replacement."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


class ContentChangedError(RuntimeError):
    """The target no longer matches the contents previously read by the caller."""


class CompareReadError(RuntimeError):
    """The target could not be reread before a compare-and-replace operation."""


class TextWriteCommittedError(OSError):
    """The target was written, but subsequent sync or cleanup failed."""

    def __init__(self, message: str, *, file_identity: tuple[int, int] | None = None):
        super().__init__(message)
        self.file_identity = file_identity


def _open_directory_nofollow(path: Path, *, create: bool = False) -> int:
    """Open each path component without traversing a directory symlink."""
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in path.parts[1:]:
            if create:
                try:
                    os.mkdir(name, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def workspace_path_lock(path: Path, *, shared: bool = False):
    """Keep cooperative ownership outside the tree that Reset can remove."""
    import fcntl

    root = Path('/tmp').resolve() / f'literature-monitor-locks-{os.getuid()}'
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    directory = _open_directory_nofollow(root)
    descriptor = None
    try:
        info = os.fstat(directory)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise CompareReadError('Workspace lock directory is not private')
        # This hash names a lock for the absolute path; it is not a file inventory.
        name = hashlib.sha256(os.fsencode(os.path.abspath(path))).hexdigest() + '.lock'
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            descriptor = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
        except FileExistsError:
            descriptor = os.open(name, flags, dir_fd=directory)
        target = os.fstat(descriptor)
        if (not stat.S_ISREG(target.st_mode) or target.st_nlink != 1 or target.st_size != 0
                or target.st_uid != os.getuid()):
            raise CompareReadError('Workspace path lock is not a safe empty regular file')
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ContentChangedError('Workspace operation is already active') from error
        if not os.path.samestat(target, os.stat(name, dir_fd=directory, follow_symlinks=False)):
            raise ContentChangedError('Workspace path lock changed')
        yield root / name, (target.st_dev, target.st_ino), descriptor
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


# Empty external shared/exclusive flock: native saves hold shared custody,
# Reset requires exclusive custody. It is not a persisted per-Paper marker
# and never excludes a later explicit Import by historical attempt state.
NATIVE_SAVE_RESET_GUARD = ".literature-monitor-native-save-reset-guard"


@dataclass(frozen=True)
class WorkspaceOperationLock:
    """Live cooperative exclusion, with no persisted workflow state."""

    path: Path
    directory: int
    identity: tuple[int, int]
    lock_identity: tuple[int, int]
    path_lock: tuple[Path, tuple[int, int], int] | None = None

    def retain_path_ownership(self) -> int:
        """Keep the same flock alive after its enclosing operation finishes."""
        if self.path_lock is None:
            raise ContentChangedError('External Workspace ownership is unavailable')
        _name, identity, descriptor = self.path_lock
        target = os.fstat(descriptor)
        if (target.st_dev, target.st_ino) != identity:
            raise ContentChangedError('External Workspace ownership changed')
        return os.dup(descriptor)

    def verify(self) -> None:
        try:
            if self.path_lock is not None:
                name, identity, _descriptor = self.path_lock
                target = name.lstat()
                if not stat.S_ISREG(target.st_mode) or (target.st_dev, target.st_ino) != identity:
                    raise ContentChangedError('Workspace path lock changed')
            current = _open_directory_nofollow(self.path)
        except OSError as error:
            raise ContentChangedError("Workspace operation location changed") from error
        try:
            directory = os.fstat(current)
            lock = os.stat(".literature-monitor-operation.lock", dir_fd=current, follow_symlinks=False)
            if ((directory.st_dev, directory.st_ino) != self.identity
                    or not stat.S_ISREG(lock.st_mode) or lock.st_nlink != 1 or lock.st_size != 0
                    or (lock.st_dev, lock.st_ino) != self.lock_identity):
                raise ContentChangedError("Workspace operation lock location changed")
        except OSError as error:
            raise ContentChangedError("Workspace operation lock location changed") from error
        finally:
            os.close(current)


@contextmanager
def workspace_operation_lock(path: Path, *, create: bool = False):
    """Own the stable external lock, then bind the current Workspace for writes."""
    with workspace_path_lock(path) as ownership:
        with _workspace_directory_lock(path, create=create) as lock:
            bound = WorkspaceOperationLock(lock.path, lock.directory, lock.identity,
                                           lock.lock_identity, ownership)
            bound.verify()
            yield bound


@contextmanager
def _workspace_directory_lock(path: Path, *, create: bool = False):
    """Retain the existing internal identity fence for Paper/import operations.

    Explicit Reset may delete this marker under the external path lock. Ordinary
    writers still reject marker replacement; platforms without flock fail closed.
    """
    import fcntl

    absolute = path if path.is_absolute() else Path.cwd() / path
    path = Path(os.path.abspath(absolute))
    directory: int | None = None
    descriptor: int | None = None
    try:
        # Walk before lexical normalization: a symlink followed by '..' must
        # not disappear from the safety check or identify a different lock root.
        directory = _open_directory_nofollow(absolute, create=create)
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        # Exclusive creation avoids macOS concurrent O_CREAT/O_NOFOLLOW opens
        # returning ENOENT for an inode that the other process just created.
        try:
            descriptor = os.open(
                ".literature-monitor-operation.lock", flags | os.O_CREAT | os.O_EXCL,
                0o600, dir_fd=directory,
            )
        except FileExistsError:
            descriptor = os.open(".literature-monitor-operation.lock", flags, dir_fd=directory)
        target = os.fstat(descriptor)
        if not stat.S_ISREG(target.st_mode) or target.st_nlink != 1 or target.st_size != 0:
            raise CompareReadError("Workspace operation lock is not a safe empty regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ContentChangedError("Workspace operation is already active") from error
        parent = os.fstat(directory)
        lock = WorkspaceOperationLock(path, directory, (parent.st_dev, parent.st_ino), (target.st_dev, target.st_ino))
        lock.verify()
        yield lock
    finally:
        # Closing releases flock even after an exception or process exit. Keep
        # the empty inode for future users; it contains no attempt/queue data.
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def read_text_exact(path: Path) -> str:
    """Read UTF-8 without newline or Unicode normalization."""

    return path.read_bytes().decode("utf-8")


@dataclass(frozen=True)
class WorkspaceTextDirectory:
    """Writes stay on the opened directory even if its path is substituted."""

    lock: WorkspaceOperationLock
    directory: int
    identity: tuple[int, int]
    name: str | None = None

    @property
    def path(self) -> Path:
        return self.lock.path if self.name is None else self.lock.path / self.name

    def verify(self) -> None:
        self.lock.verify()
        if self.name is not None:
            current = os.stat(self.name, dir_fd=self.lock.directory, follow_symlinks=False)
            if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != self.identity:
                raise ContentChangedError("Workspace child directory identity changed")

    def create(self, name: str, contents: str) -> None:
        self.verify()
        temporary = f".{name}.{uuid4().hex}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self.directory)
        committed = False
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(contents.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            self.verify()
            os.link(temporary, name, src_dir_fd=self.directory, dst_dir_fd=self.directory,
                    follow_symlinks=False)
            committed = True
            os.fsync(self.directory)
        except OSError as error:
            if committed:
                raise TextWriteCommittedError(str(error)) from error
            raise
        finally:
            try:
                os.unlink(temporary, dir_fd=self.directory)
            except OSError as error:
                if committed:
                    raise TextWriteCommittedError(str(error)) from error
                raise

    def replace(self, name: str, contents: str, *, expected_contents: str) -> None:
        self.verify()
        target = os.stat(name, dir_fd=self.directory, follow_symlinks=False)
        replace_regular_text_at_identity(
            self.path / name, contents, expected_contents=expected_contents,
            expected_file_identity=(target.st_dev, target.st_ino),
            expected_directory_identity=self.identity,
            expected_workspace_identity=self.lock.identity,
            operation_lock=self.lock,
        )


@contextmanager
def workspace_text_directory(lock: WorkspaceOperationLock, name: str):
    lock.verify()
    try:
        os.mkdir(name, dir_fd=lock.directory)
    except FileExistsError:
        pass
    directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=lock.directory)
    try:
        target = os.fstat(directory)
        bound = WorkspaceTextDirectory(lock, directory, (target.st_dev, target.st_ino), name)
        bound.verify()
        yield bound
    finally:
        os.close(directory)


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
    expected_workspace_identity: tuple[int, int] | None = None,
    operation_lock: WorkspaceOperationLock | None = None,
    validate_before_replace: Callable[[], None] | None = None,
) -> None:
    """Compare/write bound to the original directory and regular-file objects.

    When a workspace identity is supplied, the Papers directory is opened
    relative to that verified workspace object as an additional causality
    boundary. This retains the compare/replace model, not an OS-level
    transactional CAS.
    """
    workspace: int | None = None
    directory: int | None = None
    descriptor: int | None = None
    temporary: str | None = None
    try:
        try:
            if expected_workspace_identity is None:
                directory = os.open(
                    path.parent,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                )
            else:
                workspace = (os.dup(operation_lock.directory) if operation_lock is not None else os.open(
                    path.parent.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                ))
                opened_workspace = os.fstat(workspace)
                if (
                    opened_workspace.st_dev,
                    opened_workspace.st_ino,
                ) != expected_workspace_identity:
                    raise ContentChangedError(
                        "original workspace identity changed"
                    )
                directory = os.open(
                    path.parent.name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=workspace,
                )
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
            if operation_lock is not None:
                operation_lock.verify()
            try:
                if expected_workspace_identity is not None:
                    current_workspace = path.parent.parent.lstat()
                    if (
                        not stat.S_ISDIR(current_workspace.st_mode)
                        or (
                            current_workspace.st_dev,
                            current_workspace.st_ino,
                        )
                        != expected_workspace_identity
                    ):
                        raise ContentChangedError(
                            "original workspace identity changed"
                        )
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
            # An editor can modify the same inode while the temporary file is
            # prepared. Recheck bytes as well as location at the final boundary.
            current_descriptor = os.open(
                path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory,
            )
            with os.fdopen(current_descriptor, "rb") as current_handle:
                current_stat = os.fstat(current_handle.fileno())
                if (not stat.S_ISREG(current_stat.st_mode)
                        or (current_stat.st_dev, current_stat.st_ino) != expected_file_identity
                        or current_handle.read() != expected_contents.encode("utf-8")):
                    raise ContentChangedError("original target changed during preparation")

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
            replacement_stat = os.fstat(handle.fileno())
            replacement_identity = (replacement_stat.st_dev, replacement_stat.st_ino)
        if validate_before_replace is not None:
            validate_before_replace()
        check_location()
        os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        temporary = None
        try:
            os.fsync(directory)
        except OSError as error:
            raise TextWriteCommittedError(str(error), file_identity=replacement_identity) from error
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
        if workspace is not None:
            os.close(workspace)
