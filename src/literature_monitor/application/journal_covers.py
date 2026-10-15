"""Explicit local Journal cover acquisition and verified on-disk image assets."""

from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import stat
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from literature_monitor.config import JournalConfig, parse_journal_whitelist_text
from literature_monitor.safe_write import _open_directory_nofollow

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_MANIFEST_BYTES = 1024 * 1024
_FILENAME = re.compile(r"covers/([0-9]{4}-[0-9]{3}[0-9X])-([0-9a-f]{16})\.(png|jpg)\Z")


class CoverError(ValueError):
    """A cover download, asset or manifest cannot be trusted."""


def _normalize_image(data: bytes) -> tuple[bytes, str, int, int]:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise CoverError("Image is empty or exceeds the 5 MiB limit")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ("JPEG", "PNG", "WEBP", "GIF"):
                raise CoverError("Only JPEG, PNG, WebP and GIF images are supported")
            width, height = image.size
            if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
                raise CoverError("Image dimensions exceed the safety limit")
            # Opening a multi-frame image selects its first frame.
            image.load()
            image = ImageOps.exif_transpose(image)
            transparent = "A" in image.getbands() or "transparency" in image.info
            image_format = "PNG" if transparent else "JPEG"
            image = image.convert("RGBA" if transparent else "RGB")
            output = io.BytesIO()
            image.save(output, image_format, **({"quality": 85} if not transparent else {}))
            normalized = output.getvalue()
            width, height = image.size
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise CoverError(f"Invalid image data: {error}") from error
    if len(normalized) > MAX_IMAGE_BYTES:
        raise CoverError("Converted image exceeds the 5 MiB limit")
    return normalized, image_format, width, height


@contextmanager
def _assets(list_path: Path, *, create: bool = False):
    """Use directory descriptors so an attacker cannot substitute a symlinked asset tree."""
    parent = _open_directory_nofollow(list_path.absolute().parent)
    root = None
    try:
        if create:
            try:
                os.mkdir("journal-assets", mode=0o700, dir_fd=parent)
            except FileExistsError:
                pass
        root = os.open("journal-assets", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                       dir_fd=parent)
        yield root
    finally:
        if root is not None:
            os.close(root)
        os.close(parent)


def _regular_bytes(directory: int, filename: str, limit: int) -> bytes:
    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit or info.st_nlink != 1:
            raise CoverError("Unsafe or oversized asset file")
        with os.fdopen(os.dup(fd), "rb") as reader:
            data = reader.read(limit + 1)
        if len(data) > limit:
            raise CoverError("Asset file exceeds size limit")
        return data
    finally:
        os.close(fd)


def _manifest(root: int) -> dict[str, dict]:
    try:
        raw = _regular_bytes(root, "manifest.json", MAX_MANIFEST_BYTES)
    except FileNotFoundError:
        return {}
    try:
        document = json.loads(raw)
        records = document["journals"]
        if not isinstance(records, dict):
            raise ValueError("invalid records")
        for issn, item in records.items():
            if not isinstance(item, dict) or item.get("issn_l") != issn:
                raise ValueError("invalid Journal entry")
            filename = item.get("filename")
            match = _FILENAME.fullmatch(filename) if isinstance(filename, str) else None
            if match is None or match[1] != issn:
                raise ValueError("invalid asset filename")
            if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))):
                raise ValueError("invalid image digest")
            if match[2] != item["sha256"][:16]:
                raise ValueError("asset filename digest disagrees with record")
            if item.get("format") not in ("JPEG", "PNG"):
                raise ValueError("invalid image format")
            if (item["format"] == "PNG") != (match[3] == "png"):
                raise ValueError("asset filename format disagrees with record")
            if item.get("source_type") not in ("downloaded", "uploaded"):
                raise ValueError("invalid source type")
            if not isinstance(item.get("source_url"), str):
                raise ValueError("invalid source URL")
            if (type(item.get("width")) is not int or type(item.get("height")) is not int
                    or item["width"] <= 0 or item["height"] <= 0):
                raise ValueError("invalid dimensions")
        return records
    except (ValueError, KeyError, TypeError) as error:
        raise CoverError(f"Manifest is invalid: {error}") from error


def _read_image(root: int, record: dict) -> bytes | None:
    filename = record["filename"]
    match = _FILENAME.fullmatch(filename)
    if match is None or (record["format"] == "PNG") != (match[3] == "png"):
        return None
    covers = None
    try:
        covers = os.open("covers", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        data = _regular_bytes(covers, filename.split("/")[1], MAX_IMAGE_BYTES)
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            return None
        return data
    except (OSError, CoverError):
        return None
    finally:
        if covers is not None:
            os.close(covers)


def cover_details(list_path: Path, issn_l: str) -> dict:
    try:
        with _assets(list_path) as root:
            record = _manifest(root).get(issn_l)
            if record is None:
                return {"state": "Generated", "message": "", "has_record": False}
            if _read_image(root, record) is None:
                return {"state": "Generated", "message": "Saved image is missing or damaged.",
                        "has_record": True}
            state = "Uploaded" if record["source_type"] == "uploaded" else "Downloaded"
            return {"state": state, "message": "", "has_record": True}
    except FileNotFoundError:
        return {"state": "Generated", "message": "", "has_record": False}
    except (OSError, CoverError) as error:
        return {"state": "Generated", "message": str(error), "has_record": True}


def cover_image(list_path: Path, issn_l: str) -> tuple[bytes, str] | None:
    try:
        with _assets(list_path) as root:
            record = _manifest(root).get(issn_l)
            data = _read_image(root, record) if record else None
            if data is not None:
                return data, "image/png" if record["format"] == "PNG" else "image/jpeg"
    except (OSError, CoverError):
        pass
    return None


def _write_exclusive(directory: int, name: str, data: bytes) -> None:
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                 dir_fd=directory)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(data)
            output.flush()
            os.fsync(fd)
    except BaseException:
        os.unlink(name, dir_fd=directory)
        raise
    finally:
        os.close(fd)


def _saved_journal(list_path: Path, issn_l: str) -> JournalConfig:
    # Do not resolve the lexical path: Settings and downloads share the same on-disk list.md.
    directory = _open_directory_nofollow(list_path.absolute().parent)
    try:
        contents = _regular_bytes(directory, list_path.name, MAX_MANIFEST_BYTES).decode("utf-8")
        journal = next((j for j in parse_journal_whitelist_text(contents, path=list_path)
                        if j.issn_l == issn_l), None)
        if journal is None:
            raise CoverError("Journal does not exist in saved list.md")
        return journal
    finally:
        os.close(directory)


def _has_record(list_path: Path, issn_l: str) -> bool:
    try:
        with _assets(list_path) as root:
            return issn_l in _manifest(root)
    except FileNotFoundError:
        return False


def upload_cover(list_path: Path, issn_l: str, data: bytes) -> str:
    _saved_journal(list_path, issn_l)
    return _save_cover(list_path, issn_l, data, source_type="uploaded")


def _save_cover(list_path: Path, issn_l: str, data: bytes, *, source_type: str,
                source_url: str = "", missing_only: bool = False) -> str:
    normalized, image_format, width, height = _normalize_image(data)
    digest = hashlib.sha256(normalized).hexdigest()
    extension = "png" if image_format == "PNG" else "jpg"
    filename = f"covers/{issn_l}-{digest[:16]}.{extension}"
    with _assets(list_path, create=True) as root:
        lock = os.open(".manifest.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600,
                       dir_fd=root)
        try:
            info = os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise CoverError("Unsafe manifest lock")
            fcntl.flock(lock, fcntl.LOCK_EX)
            records = _manifest(root)
            if missing_only and issn_l in records:
                return "Skipped"
            _saved_journal(list_path, issn_l)
            try:
                os.mkdir("covers", mode=0o700, dir_fd=root)
            except FileExistsError:
                pass
            covers = os.open("covers", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            try:
                basename = filename.split("/")[1]
                try:
                    existing = _regular_bytes(covers, basename, MAX_IMAGE_BYTES)
                except FileNotFoundError:
                    _write_exclusive(covers, basename, normalized)
                else:
                    if hashlib.sha256(existing).hexdigest() != digest:
                        raise CoverError("Existing content-addressed image is damaged; not overwritten")
            finally:
                os.close(covers)
            record = {"issn_l": issn_l, "filename": filename, "source_url": source_url,
                      "source_type": source_type,
                      "sha256": digest, "format": image_format, "width": width, "height": height}
            if records.get(issn_l) == record:
                return "Unchanged"
            records[issn_l] = record
            encoded = (json.dumps({"journals": records}, ensure_ascii=False, indent=2,
                                  sort_keys=True) + "\n").encode("utf-8")
            if len(encoded) > MAX_MANIFEST_BYTES:
                raise CoverError("Manifest exceeds size limit")
            temporary = f".manifest-{uuid4().hex}.tmp"
            _write_exclusive(root, temporary, encoded)
            try:
                try:
                    existing = os.stat("manifest.json", dir_fd=root, follow_symlinks=False)
                    if not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1:
                        raise CoverError("Unsafe manifest target")
                except FileNotFoundError:
                    pass
                os.replace(temporary, "manifest.json", src_dir_fd=root, dst_dir_fd=root)
                os.fsync(root)
            finally:
                try:
                    os.unlink(temporary, dir_fd=root)
                except FileNotFoundError:
                    pass
            return "Uploaded" if source_type == "uploaded" else "Downloaded"
        finally:
            os.close(lock)


def reset_covers(list_path: Path) -> None:
    """Remove only the asset directory beside the saved configuration."""
    parent = _open_directory_nofollow(list_path.absolute().parent)
    try:
        try:
            info = os.stat("journal-assets", dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(info.st_mode):
            raise NotADirectoryError("Unsafe cover asset directory")
        shutil.rmtree("journal-assets", dir_fd=parent)
    finally:
        os.close(parent)
