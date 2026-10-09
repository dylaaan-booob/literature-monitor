"""Explicit permanent deletion of the configured Workspace tree (§42.8)."""

from __future__ import annotations

import os
import shutil
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import yaml

from literature_monitor.config import parse_monitor_definition
from literature_monitor.safe_write import (
    CompareReadError, ContentChangedError, NATIVE_SAVE_RESET_GUARD,
    _open_directory_nofollow, workspace_path_lock,
)


class ResetRefused(RuntimeError):
    """The configured deletion target cannot be safely established."""


@dataclass(frozen=True)
class ResetPlan:
    workspace: Path
    configuration: bytes
    workspace_identity: tuple[int, int] | None


@dataclass(frozen=True)
class ResetResult:
    success: bool
    message: str


def _safe_location(config_path: Path, workspace: Path) -> None:
    repository = Path(__file__).resolve().parents[3]
    # macOS commonly treats case variants as the same directory.
    folded_workspace = Path(str(workspace).casefold())
    folded_repository = Path(str(repository).casefold())
    forbidden = (Path('/'), Path.home().resolve(), repository, config_path.parent,
                 Path('/tmp').resolve(), Path('/var'), Path('/usr'), Path('/etc'),
                 Path('/Users'), Path('/Library'), Path('/System'), Path('/Applications'), Path('/Volumes'))
    if (not workspace.is_absolute() or '..' in workspace.parts or os.path.ismount(workspace)
            or (folded_workspace.is_relative_to(folded_repository)
                and not folded_workspace.is_relative_to(folded_repository / 'workspace'))
            or (workspace / '.git').exists()
            or any(workspace == root or workspace in root.parents for root in forbidden)
            or any(part.casefold() in {'.git', 'zotero', '.zotero', 'chrome', 'chromium',
                                      'profiles', 'application support', 'user data'}
                   for part in workspace.parts)):
        raise ResetRefused('Workspace target is a dangerous location')


def _identity(workspace: Path) -> tuple[int, int] | None:
    try:
        descriptor = _open_directory_nofollow(workspace)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(descriptor)
        return info.st_dev, info.st_ino
    finally:
        os.close(descriptor)


def preview_reset(config_path: Path) -> ResetPlan:
    """Bind confirmation to configuration and a directory, without inspecting files."""
    config_path = config_path.resolve()
    try:
        raw = config_path.read_bytes()
        definition = parse_monitor_definition(config_path, yaml.safe_load(raw))
        workspace = definition.output_dir
        if not workspace.is_absolute():
            workspace = config_path.parent / workspace
        _safe_location(config_path, workspace)
        identity = _identity(workspace)  # Reject ancestor/target symlinks before normalization.
        if config_path.read_bytes() != raw:
            raise ContentChangedError('Configuration changed during Reset preview')
        return ResetPlan(workspace, raw, identity)
    except (OSError, ValueError, ContentChangedError, yaml.YAMLError) as error:
        raise ResetRefused(str(error)) from error


def execute_reset(config_path: Path, expected: ResetPlan) -> ResetResult:
    """Delete the entire tree once; failures never imply restoration or safe retry."""
    custody = None
    try:
        if not shutil.rmtree.avoids_symlink_attacks:
            raise ResetRefused('Safe directory-relative deletion is unavailable')
        _safe_location(config_path.resolve(), expected.workspace)
        with ExitStack() as guards:
            try:
                guards.enter_context(workspace_path_lock(expected.workspace / NATIVE_SAVE_RESET_GUARD))
            except ContentChangedError as error:
                raise ResetRefused('External native-save Reset guard is occupied or changed; '
                                   'another process may hold an unresolved native save') from error
            guards.enter_context(workspace_path_lock(expected.workspace))
            if preview_reset(config_path) != expected:
                raise ResetRefused('Workspace or configuration changed; reload Settings to confirm')
            if expected.workspace_identity is None:
                return ResetResult(True, 'Workspace is already absent. A later explicit Run can recreate it.')
            parent = _open_directory_nofollow(expected.workspace.parent)
            try:
                info = os.stat(expected.workspace.name, dir_fd=parent, follow_symlinks=False)
                if (not stat.S_ISDIR(info.st_mode)
                        or (info.st_dev, info.st_ino) != expected.workspace_identity):
                    raise ResetRefused('Workspace directory changed')
                # Take the root out of its public name before deletion. If the
                # rename captured a competing object, keep it intact and report it.
                slot = f'.literature-monitor-reset-{uuid4().hex}'
                os.mkdir(slot, 0o700, dir_fd=parent)
                custody = expected.workspace.parent / slot
                private = os.open(slot, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    os.rename(expected.workspace.name, 'workspace', src_dir_fd=parent, dst_dir_fd=private)
                    captured = os.stat('workspace', dir_fd=private, follow_symlinks=False)
                    if (not stat.S_ISDIR(captured.st_mode)
                            or (captured.st_dev, captured.st_ino) != expected.workspace_identity):
                        raise ResetRefused(f'Workspace root changed; captured object was not deleted at {custody / "workspace"}')
                    # CPython's descriptor-based traversal does not follow links
                    # within the confirmed root. Its public name is never deleted.
                    shutil.rmtree('workspace', dir_fd=private)
                finally:
                    os.close(private)
                os.rmdir(slot, dir_fd=parent)
                custody = None
                try:
                    os.stat(expected.workspace.name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise ResetRefused('Workspace is still present or was replaced')
                current = _open_directory_nofollow(expected.workspace.parent)
                try:
                    if not os.path.samestat(os.fstat(parent), os.fstat(current)):
                        raise ResetRefused('Workspace parent path changed')
                finally:
                    os.close(current)
                if config_path.read_bytes() != expected.configuration or _identity(expected.workspace) is not None:
                    raise ResetRefused('Configuration or Workspace changed during Reset')
            finally:
                os.close(parent)
        return ResetResult(True, 'Workspace permanently deleted. A later explicit Run can recreate it.')
    except (ResetRefused, CompareReadError, ContentChangedError, OSError, ValueError) as error:
        remaining = f' Any remaining or unconfirmed contents may be at {custody}.' if custody is not None else ''
        return ResetResult(False, f'Reset failed: {error}.{remaining} Some contents may already be permanently deleted. '
                           'There is no recovery or automatic retry; Reset does not undo Zotero saves or authorize import retry.')
