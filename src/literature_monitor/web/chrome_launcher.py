"""Open an ephemeral handoff in installed normal Chrome, never a new profile."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

from .browser_handoff import HandoffLaunch


def launch_normal_chrome(launch: HandoffLaunch) -> None:
    if not isinstance(launch, HandoffLaunch):
        raise ValueError('A browser handoff is required.')
    if sys.platform == 'darwin':
        command = ['/usr/bin/open', '-a', 'Google Chrome', launch.url]
    else:
        chrome = shutil.which('google-chrome') or shutil.which('google-chrome-stable')
        if sys.platform == 'win32':
            for variable in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
                root = os.environ.get(variable)
                candidate = Path(root) / 'Google/Chrome/Application/chrome.exe' if root else None
                if candidate is not None and candidate.is_file():
                    chrome = str(candidate)
                    break
        if chrome is None:
            raise RuntimeError('Normal Chrome is unavailable.')
        command = [chrome, launch.url]
    try:
        subprocess.run(command, check=True, timeout=10, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError('Normal Chrome could not be opened.') from None
