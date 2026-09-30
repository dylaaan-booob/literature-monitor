"""Shared dedicated browser session/profile ownership for SPEC §35.7."""

from contextlib import contextmanager
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


@contextmanager
def browser_session(factory=sync_playwright):
    # Driver debug output can contain authenticated targets.
    saved = {name: os.environ.pop(name) for name in ("DEBUG", "PWDEBUG") if name in os.environ}
    try:
        playwright = factory().start()
    finally:
        os.environ.update(saved)
    try:
        yield playwright
    finally:
        playwright.stop()


def institutional_profile(app_data: Path, protected: tuple[Path, ...]) -> Path:
    profile = (app_data / "institutional-browser").resolve()
    if any(profile.is_relative_to(root) or root.is_relative_to(profile) for root in protected):
        raise ValueError("Dedicated profile overlaps protected application state.")
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    return profile
