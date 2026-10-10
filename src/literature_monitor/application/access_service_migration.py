"""Explicit one-time §41 → §43 list.md upgrade; no upgrade during ordinary Save or Run."""

from __future__ import annotations

import hashlib
import stat
from dataclasses import dataclass
from pathlib import Path

from literature_monitor.config import (
    ConfigurationError,
    detect_list_schema,
    parse_journal_whitelist_text,
    parse_publisher_whitelist_text,
    parse_settings_list_text,
    render_settings_list_text,
)
from literature_monitor.safe_write import (
    ContentChangedError,
    TextWriteCommittedError,
    replace_regular_text_at_identity,
)
from literature_monitor.application.settings import (
    SettingsIssueSource,
    _read_snapshot,
)


@dataclass(frozen=True)
class AccessSchemaUpgradePreview:
    path: Path
    source_digest: str
    target_contents: str
    journal_count: int
    publisher_count: int
    directory_identity: tuple[int, int]
    file_identity: tuple[int, int]


class AccessSchemaUpgradeCommittedWarning(RuntimeError):
    """Upgrade bytes were verified on disk, but directory durability is unconfirmed."""

    def __init__(self, revision: str):
        super().__init__("Access schema upgrade replaced list.md but directory sync failed")
        self.revision = revision


class AccessSchemaUpgradeStateUncertain(RuntimeError):
    """Replacement was attempted; a trustworthy post-write observation is unavailable."""


def _source_identity(path: Path) -> tuple[tuple[int, int], tuple[int, int]]:
    parent = path.parent.lstat()
    file = path.lstat()
    if not stat.S_ISDIR(parent.st_mode) or not stat.S_ISREG(file.st_mode):
        raise ContentChangedError('upgrade path is not a regular file in a real directory')
    return (parent.st_dev, parent.st_ino), (file.st_dev, file.st_ino)


def preview_access_schema_upgrade(path: Path) -> AccessSchemaUpgradePreview:
    """Read current disk content without writing; never use the tracked example."""
    path = path.absolute()
    try:
        identity = _source_identity(path)
    except (OSError, ContentChangedError) as error:
        raise ConfigurationError(f'{path}: unsafe upgrade source: {error}', path=path) from error
    snapshot = _read_snapshot(path, source=SettingsIssueSource.JOURNAL_DATA)
    if snapshot.issue is not None or snapshot.contents is None:
        raise ConfigurationError(
            snapshot.issue.message if snapshot.issue else f"{path}: list.md does not exist",
            path=path,
        )
    source = snapshot.contents
    if _source_identity(path) != identity:
        raise ContentChangedError('source file identity changed during Preview')
    if detect_list_schema(source, path=path) != "legacy":
        raise ConfigurationError(f"{path}: schema already upgraded; no upgrade required", path=path)
    journals = parse_journal_whitelist_text(source, path=path)
    publishers = parse_publisher_whitelist_text(source, path=path)
    target = render_settings_list_text(source, journals, publishers, access_services=(), path=path)
    parse_settings_list_text(target, path=path)
    assert snapshot.revision.digest is not None
    return AccessSchemaUpgradePreview(
        path=path,
        source_digest=snapshot.revision.digest,
        target_contents=target,
        journal_count=len(journals),
        publisher_count=len(publishers),
        directory_identity=identity[0],
        file_identity=identity[1],
    )


def confirm_access_schema_upgrade(preview: AccessSchemaUpgradePreview, *, confirmed: bool) -> None:
    """CAS the originally previewed file; the caller must obtain explicit user consent."""
    if not confirmed:
        raise ConfigurationError("Access schema upgrade requires explicit confirmation", path=preview.path)
    snapshot = _read_snapshot(preview.path, source=SettingsIssueSource.JOURNAL_DATA)
    if snapshot.issue is not None or snapshot.contents is None:
        raise ConfigurationError(
            snapshot.issue.message if snapshot.issue else "upgrade source is missing", path=preview.path,
        )
    if snapshot.revision.digest != preview.source_digest:
        raise ContentChangedError('list.md changed since upgrade Preview')
    if _source_identity(preview.path) != (preview.directory_identity, preview.file_identity):
        raise ContentChangedError('list.md location changed since upgrade Preview')
    source = snapshot.contents
    if detect_list_schema(source, path=preview.path) != "legacy":
        raise ContentChangedError("list.md schema changed since upgrade Preview")
    journals = parse_journal_whitelist_text(source, path=preview.path)
    publishers = parse_publisher_whitelist_text(source, path=preview.path)
    expected_target = render_settings_list_text(source, journals, publishers, access_services=(), path=preview.path)
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != preview.source_digest or expected_target != preview.target_contents:
        raise ContentChangedError("upgrade plan does not match current source")
    parse_settings_list_text(expected_target, path=preview.path)
    try:
        replace_regular_text_at_identity(
            preview.path, expected_target, expected_contents=source,
            expected_directory_identity=preview.directory_identity,
            expected_file_identity=preview.file_identity,
        )
    except TextWriteCommittedError as error:
        # 替换已执行，但目录 fsync 失败。只能在新 inode、原目录和实际字节
        # 全部吻合时报告已写入；否则写入状态未知，不能说没有写入。
        try:
            before = _source_identity(preview.path)
            snapshot = _read_snapshot(preview.path, source=SettingsIssueSource.JOURNAL_DATA)
            after = _source_identity(preview.path)
            if (error.file_identity is None or before != after
                    or before != (preview.directory_identity, error.file_identity)
                    or snapshot.issue is not None or snapshot.contents != expected_target
                    or snapshot.revision.digest != hashlib.sha256(expected_target.encode("utf-8")).hexdigest()
                    or detect_list_schema(snapshot.contents, path=preview.path) != "current"):
                raise ContentChangedError("upgrade target bytes, identity or schema cannot be verified")
        except (OSError, ValueError, ConfigurationError, ContentChangedError) as read_error:
            raise AccessSchemaUpgradeStateUncertain(
                f"Upgrade replacement occurred, but actual list.md state is unconfirmed: {read_error}"
            ) from error
        raise AccessSchemaUpgradeCommittedWarning(snapshot.revision.digest) from error
