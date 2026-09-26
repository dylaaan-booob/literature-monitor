"""Directory boundary shared by application-owned runtime metadata files."""

from __future__ import annotations

import stat
from pathlib import Path

METADATA_DIRECTORY_NAME = ".literature-monitor"


def metadata_directory(output_dir: Path, *, create: bool) -> Path | None:
    path = output_dir / METADATA_DIRECTORY_NAME
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        if not create:
            return None
        try:
            path.mkdir(parents=True)
        except FileExistsError:
            pass
        except OSError as error:
            raise OSError(f"cannot create runtime metadata directory: {error}") from error
        try:
            mode = path.lstat().st_mode
        except OSError as error:
            raise OSError(f"cannot inspect runtime metadata directory: {error}") from error
    except OSError as error:
        raise OSError(f"cannot inspect runtime metadata directory: {error}") from error

    if not stat.S_ISDIR(mode):
        raise OSError("runtime metadata path is not a regular directory")
    return path
