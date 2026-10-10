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
from test_web_run_settings import SETTINGS_NODE_DOM, SettingsDOM


def stamp(path: Path):
    info = path.stat()
    return path.read_bytes(), info.st_ino, info.st_mtime_ns


@pytest.mark.parametrize("assign", [False, True])
def test_group_visual_reordering_keeps_original_order_and_only_writes_changed_file(tmp_path, assign):
    node = shutil.which("node")
    assert node, "Node is required for the executable Settings interaction test"
    config, path, original_journal, publisher, service = current_mapping_document(tmp_path)
    # Interleaved Groups must not turn an unchanged Save into an order-only rewrite.
    journals = (
        original_journal.model_copy(update={"group": "First"}),
        JournalConfig(name="Beta Journal", issn_l=B, publisher_id=P1, group="Second"),
        JournalConfig(name="Gamma Journal", issn_l="0162-1459", publisher_id=P1, group="First"),
    )
    path.write_text(render_settings_list_text(
        path.read_text(), journals, (publisher,), access_services=(service,), path=path,
    ), encoding="utf-8")
    before_monitor, before_list = stamp(config), stamp(path)
    with TestClient(create_app(config), base_url="http://localhost") as browser:
        page = browser.get("/settings").text
        source = browser.get("/static/app.js").text
        program = (
            "const PAGE=" + json.dumps(SettingsDOM(page).root)
            + ", SOURCE=" + json.dumps(source) + ";\n"
            + SETTINGS_NODE_DOM
            + r"""
const editor = document.getElementById("settings-editor");
const rows = editor.querySelectorAll("[data-journal-rows] .journal-row");
const name = r => r.querySelector('[name="journal_name"]').value;
const row = rows.find(r => name(r) === "Beta Journal");
assert.equal(rows.map(name).join(","), "Biometrics,Gamma Journal,Beta Journal");
if (ASSIGN) {
  const select = row.querySelector('[name="journal_group"]');
  select.value = "First"; handlers.change({target: select});
  assert.equal(row.closest("[data-group-container]").dataset.groupName, "First");
}
const data = {};
for (const input of editor.querySelectorAll("input, select, textarea")) {
  const field = input.attrs.name;
  if (!field || input.closest("template") || (input.attrs.type === "checkbox" && !input.checked)) continue;
  (data[field] ||= []).push(input.value);
}
assert.deepEqual(data.journal_order, ["0", "2", "1"]);
console.log(JSON.stringify(data));
""".replace("ASSIGN", "true" if assign else "false")
        )
        result = subprocess.run([node, "-e", program], text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        response = browser.post("/settings/save", data=data)
        assert response.status_code == 200
        assert response.headers.get("HX-Trigger") == "settingsSaved", response.text
        assert "data-group-container" in response.text
    saved = load_settings(config).draft
    assert [journal.issn_l for journal in saved.journals] == [journal.issn_l for journal in journals]
    assert [journal.group for journal in saved.journals] == (
        ["First", "First", "First"] if assign else ["First", "Second", "First"]
    )
    assert saved.publishers == (publisher,)
    assert saved.access_services == (service,)
    assert stamp(config) == before_monitor
    if assign:
        assert path.read_bytes() != before_list[0]
    else:
        assert stamp(path) == before_list


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
