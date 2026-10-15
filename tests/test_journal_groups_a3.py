"""A3 Journal Groups: executable DOM submissions and unchanged-file boundaries."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import FormData

from literature_monitor.application.settings import load_settings
from literature_monitor.config import JournalConfig, render_settings_list_text
from literature_monitor.web.app import create_app
from literature_monitor.web.settings_form import settings_form_from_submission
from test_access_service_schema import B, P1, current_mapping_document


def stamp(path: Path):
    info = path.stat()
    return path.read_bytes(), info.st_ino, info.st_mtime_ns


@pytest.mark.parametrize("order", [
    ["0", "0"], ["1", "01"], ["0"], ["x", "2"], ["1" * 4500, "2"],
])
def test_invalid_journal_order_is_rejected_without_changing_fields(order):
    entries = [
        ("journal_name", "One"), ("journal_name", "Two"),
        ("journal_issns", "0006-341X"), ("journal_issns", B),
        ("journal_pending", "0"), ("journal_pending", "0"),
        ("journal_group", "First"), ("journal_group", "Second"),
    ]
    entries += [("journal_order", item) for item in order]
    values = settings_form_from_submission(FormData(entries))
    assert [journal.name for journal in values.journals] == ["One", "Two"]
    assert any(issue.field == "journals" and "Journal order" in issue.message
               for issue in values.institution_issues)
