"""Descriptor-based private PDF staging; Chrome's source is never mutated."""

from dataclasses import dataclass, field
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import stat
import tempfile
from uuid import UUID

from .browser_acquisition import BrowserDownloadEvidence

MAX_PDF_BYTES = 128 * 1024 * 1024  # 128 MiB, enforced during the copy.
_CHUNK = 1024 * 1024


class PdfStagingError(ValueError):
    pass


def _identity(info):
    return info.st_dev, info.st_ino


def _snapshot(info):
    return _identity(info), info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _bindings_match(bindings):
    for parent, name, identity in bindings:
        located = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(located.st_mode) or _identity(located) != identity:
            return False
    return True


@dataclass(frozen=True)
class StagedPdf:
    task_id: UUID
    download_id: int
    byte_count: int
    path: Path = field(repr=False)
    _directory: Path = field(repr=False)
    _directory_identity: tuple = field(repr=False)
    _file_snapshot: tuple = field(repr=False)

    @contextmanager
    def _open_directory(self):
        descriptors, bindings = [], []
        try:
            parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(parent)
            for name in self._directory.parts[1:]:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                descriptors.append(child)
                bindings.append((parent, name, _identity(os.fstat(child))))
                parent = child
            if _identity(os.fstat(parent)) != self._directory_identity:
                raise ValueError
            yield parent, bindings
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def validate(self) -> bool:
        """Recheck private artifact identity and byte signature before qualification."""
        fd = None
        try:
            with self._open_directory() as (directory, bindings):
                fd = os.open('artifact.pdf', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                info = os.fstat(fd)
                return (stat.S_ISREG(info.st_mode) and _snapshot(info) == self._file_snapshot
                        and 0 < info.st_size <= MAX_PDF_BYTES and info.st_size == self.byte_count
                        and os.read(fd, 4) == b'%PDF' and _snapshot(os.fstat(fd)) == self._file_snapshot
                        and _snapshot(os.stat('artifact.pdf', dir_fd=directory, follow_symlinks=False)) == self._file_snapshot
                        and _bindings_match(bindings))
        except (OSError, ValueError):
            return False
        finally:
            if fd is not None:
                os.close(fd)

    def cleanup(self) -> None:
        try:
            with self._open_directory() as (directory, bindings):
                leaf = os.stat('artifact.pdf', dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(leaf.st_mode) or _identity(leaf) != self._file_snapshot[0] or not _bindings_match(bindings):
                    return
                os.unlink('artifact.pdf', dir_fd=directory)
                if not _bindings_match(bindings):
                    return
                parent, name, _ = bindings[-1]
                os.rmdir(name, dir_fd=parent)
        except (OSError, ValueError):
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cleanup()


def stage_download(evidence: BrowserDownloadEvidence, *, project_dir: Path,
                   workspace_dir: Path, config_path: Path,
                   browser_profile_dir: Path | None = None,
                   download_dir: Path | None = None, temp_root: Path | None = None,
                   max_bytes: int = MAX_PDF_BYTES) -> StagedPdf:
    """Read an immutable opened source, checking each parent binding after copy.

    The caller supplies protected roots from application configuration. The
    source's parent is always protected even if Chrome used a custom Save As.
    Neither exceptions nor repr expose source paths, URLs or file contents.
    """
    descriptors, bindings = [], []
    directory = None
    try:
        if not isinstance(evidence, BrowserDownloadEvidence) or type(max_bytes) is not int or not 0 < max_bytes <= MAX_PDF_BYTES:
            raise ValueError
        raw = evidence.path
        if not isinstance(raw, str) or not raw.startswith('/') or '\x00' in raw or '\\' in raw or any(ord(c) < 32 for c in raw):
            raise ValueError
        parts = raw.split('/')[1:]
        if not parts or any(part in {'', '.', '..'} for part in parts):
            raise ValueError
        source = Path(raw)
        protected = [Path(project_dir).resolve(), Path(workspace_dir).resolve(), Path(config_path).resolve().parent, source.parent.resolve()]
        protected.extend(Path(p).resolve() for p in (browser_profile_dir, download_dir) if p is not None)
        parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        descriptors.append(parent)
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            descriptors.append(child)
            bindings.append((parent, part, _identity(os.fstat(child))))
            parent = child
        located_before = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        descriptors.append(fd)
        before = os.fstat(fd)
        if (_snapshot(located_before) != _snapshot(before) or not stat.S_ISREG(before.st_mode)
                or not before.st_mode & 0o444 or not 0 < before.st_size <= max_bytes):
            raise ValueError
        if before.st_size != evidence.file_size:
            raise ValueError
        root = Path(temp_root if temp_root is not None else tempfile.gettempdir()).resolve()
        if any(root == p or root.is_relative_to(p) for p in protected):
            raise ValueError
        directory = Path(tempfile.mkdtemp(prefix='literature-monitor-pdf-', dir=root)).resolve()
        if any(directory == p or directory.is_relative_to(p) for p in protected):
            raise ValueError
        directory_identity = _identity(directory.lstat())
        path = directory / 'artifact.pdf'
        out = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        descriptors.append(out)
        total = 0
        while True:
            chunk = os.read(fd, min(_CHUNK, max_bytes - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError
            view = memoryview(chunk)
            while view:
                written = os.write(out, view)
                if written <= 0:
                    raise ValueError
                view = view[written:]
        if total != before.st_size or _snapshot(os.fstat(fd)) != _snapshot(before):
            raise ValueError
        located = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if _snapshot(located) != _snapshot(before) or not stat.S_ISREG(located.st_mode):
            raise ValueError
        for parent_fd, name, identity in bindings:
            located = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(located.st_mode) or _identity(located) != identity:
                raise ValueError
        os.lseek(out, 0, os.SEEK_SET)
        staged = os.fstat(out)
        if not total or staged.st_size != total or os.read(out, 4) != b'%PDF':
            raise ValueError
        artifact = StagedPdf(evidence.task_id, evidence.download_id, total, path, directory,
                             directory_identity, _snapshot(staged))
        directory = None  # Ownership transfers only after all checks succeed.
        return artifact
    except (OSError, ValueError, TypeError, AttributeError):
        raise PdfStagingError('Download cannot be safely staged as a PDF.') from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
