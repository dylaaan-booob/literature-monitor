from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import replace
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import httpx
from fastapi.testclient import TestClient

import literature_monitor.application.decisions as decisions
import literature_monitor.web.app as web_app
from literature_monitor.application.decisions import (
    DecisionOutcome,
    DecisionResult,
)
from literature_monitor.application.workspace import (
    WorkspaceAuthor,
    WorkspaceIssue,
    WorkspacePaper,
    WorkspaceSnapshot,
)
from literature_monitor.config import load_config
from literature_monitor.markdown_state import parse_paper_state
from literature_monitor.materialize import render_paper_markdown
from literature_monitor.models import (
    Author,
    CanonicalMetadata,
    CanonicalPaper,
    ExternalIds,
    MetadataSource,
    WorkflowStatus,
    Workflow,
)
from literature_monitor.web.app import create_app
from literature_monitor.zotero_local import ZoteroLocalClient


def write_valid_config(tmp_path: Path) -> tuple[Path, Path]:
    journal_path = tmp_path / "list.md"
    journal_path.write_text(
        """# Venues

## Journals

| Journal | ISSN/EISSN |
|---|---|
| Biometrics | 0006-341X |
""",
        encoding="utf-8",
    )
    config_path = tmp_path / "monitor.yaml"
    config_path.write_text(
        """name: Web Monitor
venue_whitelist: list.md
keyword_expression: causal
output_dir: workspace
window_days: 14
log_level: INFO
""",
        encoding="utf-8",
    )
    return config_path, tmp_path / "workspace"


def make_paper(
    *,
    status: WorkflowStatus = WorkflowStatus.CANDIDATE,
    title: str = "Causal Paper",
    paper_id: UUID | None = None,
    zotero_key: str | None = None,
) -> WorkspacePaper:
    identifier = paper_id or uuid4()
    return WorkspacePaper(
        paper_id=identifier,
        title=title,
        journal="Biometrics",
        publication_date=date(2026, 9, 1),
        discovered_at=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc),
        status=status,
        abstract="A detailed abstract.",
        author_keywords=("causal inference", "statistics"),
        authors=(
            WorkspaceAuthor(name="Ada Example", note_stem="Ada Example"),
            WorkspaceAuthor(name="Lin Example", note_stem="Lin Example"),
        ),
        external_ids=ExternalIds(
            openalex="W123",
            doi="10.1000/example",
            crossref="10.1000/example",
        ),
        sources=(
            MetadataSource(
                provider="openalex",
                record_id="W123",
                retrieved_at=datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc),
            ),
        ),
        zotero_key=zotero_key,
    )


def fake_config(output_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(output_dir=output_dir, journals=())


def csrf_from_html(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def decision_result(
    paper_id: UUID,
    expected_status: WorkflowStatus,
    *,
    outcome: DecisionOutcome = DecisionOutcome.UPDATED,
    current_status: WorkflowStatus | None = None,
    resulting_status: WorkflowStatus | None = None,
    message: str = "Decision applied.",
) -> DecisionResult:
    return DecisionResult(
        outcome=outcome,
        paper_id=paper_id,
        expected_status=expected_status,
        current_status=current_status or expected_status,
        resulting_status=resulting_status,
        path=None,
        message=message,
    )


def test_create_app_succeeds_with_valid_config(tmp_path: Path) -> None:
    config_path, _ = write_valid_config(tmp_path)

    app = create_app(config_path)

    assert app.title == "Literature Monitor"


def test_create_app_succeeds_with_missing_monitor_config(tmp_path: Path) -> None:
    app = create_app(tmp_path / "missing.yaml")

    assert app is not None


def test_create_app_succeeds_with_malformed_monitor_config(tmp_path: Path) -> None:
    config_path = tmp_path / "monitor.yaml"
    config_path.write_text("venue_whitelist: [\n", encoding="utf-8")

    app = create_app(config_path)

    assert app is not None


def test_application_construction_does_not_load_config_or_call_providers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("construction must not resolve config or providers")

    monkeypatch.setattr(web_app, "load_config", forbidden)
    monkeypatch.setattr(web_app, "load_settings", forbidden)
    monkeypatch.setattr(web_app, "load_workspace", forbidden)

    app = create_app(tmp_path / "monitor.yaml")

    assert app is not None


@pytest.mark.parametrize(
    "host",
    ("localhost", "localhost:8000", "127.0.0.1", "127.0.0.1:8000"),
)
def test_local_hosts_are_accepted(tmp_path: Path, host: str) -> None:
    app = create_app(tmp_path / "missing.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/", headers={"host": host})

    assert response.status_code == 200


def test_external_host_is_rejected(tmp_path: Path) -> None:
    app = create_app(tmp_path / "missing.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/", headers={"host": "external.example"})

    assert response.status_code == 400


def test_testclient_default_testserver_host_is_rejected(tmp_path: Path) -> None:
    app = create_app(tmp_path / "missing.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/", headers={"host": "testserver"})

    assert response.status_code == 400


def test_no_broad_cors_is_enabled(tmp_path: Path) -> None:
    app = create_app(tmp_path / "missing.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.options(
            "/",
            headers={
                "host": "localhost",
                "origin": "https://external.example",
                "access-control-request-method": "GET",
            },
        )

    assert "access-control-allow-origin" not in response.headers


def test_workspace_page_defaults_to_inbox_and_returns_html(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = make_paper(title="Inbox Paper")
    kept = make_paper(status=WorkflowStatus.KEPT, title="Kept Paper")
    snapshot = WorkspaceSnapshot(papers=(candidate, kept), issues=())
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(web_app, "load_workspace", lambda output_dir, *, journals: snapshot)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<h1>Inbox</h1>" in response.text
    assert "Inbox Paper" in response.text
    assert "Kept Paper" not in response.text
    assert "Workspace issues" not in response.text
    assert "No workspace issues." not in response.text
    assert "workspace-health-indicator" not in response.text


@pytest.mark.parametrize(
    ("view", "status", "title"),
    (
        ("inbox", WorkflowStatus.CANDIDATE, "Inbox Paper"),
        ("kept", WorkflowStatus.KEPT, "Kept Paper"),
        ("rejected", WorkflowStatus.REJECTED, "Rejected Paper"),
        ("in-zotero", WorkflowStatus.IN_ZOTERO, "Zotero Paper"),
    ),
)
def test_workspace_views_derive_from_snapshot_membership(
    view: str,
    status: WorkflowStatus,
    title: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    papers = tuple(
        make_paper(status=paper_status, title=paper_title)
        for paper_status, paper_title in (
            (WorkflowStatus.CANDIDATE, "Inbox Paper"),
            (WorkflowStatus.KEPT, "Kept Paper"),
            (WorkflowStatus.REJECTED, "Rejected Paper"),
            (WorkflowStatus.IN_ZOTERO, "Zotero Paper"),
        )
    )
    snapshot = WorkspaceSnapshot(papers=papers, issues=())
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(web_app, "load_workspace", lambda output_dir, *, journals: snapshot)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/", params={"view": view})

    assert response.status_code == 200
    assert title in response.text
    for other in papers:
        if other.status is not status:
            assert other.title not in response.text


@pytest.mark.parametrize("surface", ("/", "/fragments/workspace"))
def test_valid_papers_render_with_only_workspace_issue_indicator(
    surface: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = make_paper(title="Still Valid")
    snapshot = WorkspaceSnapshot(
        papers=(paper,),
        issues=(
            WorkspaceIssue(path=tmp_path / "bad.md", message="invalid frontmatter"),
            WorkspaceIssue(path=tmp_path / "other.md", message="invalid status"),
        ),
    )
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(web_app, "load_workspace", lambda output_dir, *, journals: snapshot)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get(surface)

    assert response.status_code == 200
    assert "Still Valid" in response.text
    assert "Workspace · 2 issues" in response.text
    assert 'href="/settings#workspace-health">View details</a>' in response.text
    assert "Workspace issues" not in response.text
    for issue in snapshot.issues:
        assert str(issue.path) not in response.text
        assert issue.message not in response.text


def test_missing_workspace_is_normal_empty_state_and_not_created(tmp_path: Path) -> None:
    config_path, output_dir = write_valid_config(tmp_path)
    assert not output_dir.exists()
    app = create_app(config_path)

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "No Papers in this view." in response.text
    assert not output_dir.exists()


@pytest.mark.parametrize("mode", ("missing", "malformed"))
def test_missing_or_malformed_config_is_recoverable_html(
    mode: str,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "monitor.yaml"
    if mode == "malformed":
        config_path.write_text("keyword_expression: [\n", encoding="utf-8")
    app = create_app(config_path)

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Configuration needs attention" in response.text
    assert 'href="/settings"' in response.text
    assert "Traceback" not in response.text


@pytest.mark.parametrize("mode", ("missing", "malformed"))
def test_missing_or_malformed_journal_config_is_recoverable_html(
    mode: str,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "monitor.yaml"
    config_path.write_text(
        """name: Web Monitor
venue_whitelist: list.md
keyword_expression: causal
output_dir: workspace
""",
        encoding="utf-8",
    )
    if mode == "malformed":
        (tmp_path / "list.md").write_text(
            "# Venues\n\n## Journals\n\nnot a table\n",
            encoding="utf-8",
        )
    app = create_app(config_path)

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Configuration needs attention" in response.text
    assert 'href="/settings"' in response.text
    assert "Traceback" not in response.text


def test_workspace_health_fragment_replaces_old_issue_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = make_paper(title="Fragment Paper")
    snapshot = WorkspaceSnapshot(
        papers=(paper,),
        issues=(WorkspaceIssue(path=tmp_path / "broken.md", message="broken Paper"),),
    )
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(web_app, "load_workspace", lambda output_dir, *, journals: snapshot)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        workspace_response = client.get("/fragments/workspace")
        health_response = client.get("/fragments/workspace-health")
        old_issues_response = client.get("/fragments/issues")

    assert workspace_response.status_code == 200
    assert "Fragment Paper" in workspace_response.text
    assert "broken Paper" not in workspace_response.text
    assert health_response.status_code == 200
    assert '<h2>Workspace health</h2>' in health_response.text
    assert str(tmp_path / "broken.md") in health_response.text
    assert "broken Paper" in health_response.text
    assert 'hx-trigger="settingsSaved from:body"' in health_response.text
    assert "every " not in health_response.text
    assert old_issues_response.status_code == 404


def test_paper_list_renders_projected_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = make_paper(title="Projected Metadata")
    snapshot = WorkspaceSnapshot(papers=(paper,), issues=())
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(web_app, "load_workspace", lambda output_dir, *, journals: snapshot)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/fragments/papers", params={"view": "inbox"})

    assert response.status_code == 200
    assert "Projected Metadata" in response.text
    assert "Biometrics" in response.text
    assert "2026-09-01" in response.text
    assert "Ada Example, Lin Example" in response.text
    assert "candidate" in response.text


def test_paper_detail_is_uuid_addressed_and_renders_existing_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = make_paper(title="Detailed Paper", zotero_key="ZOT123")
    snapshot = WorkspaceSnapshot(papers=(paper,), issues=())
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(web_app, "load_workspace", lambda output_dir, *, journals: snapshot)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get(f"/fragments/papers/{paper.paper_id}")

    assert response.status_code == 200
    assert "Detailed Paper" in response.text
    assert "A detailed abstract." in response.text
    assert "causal inference, statistics" in response.text
    assert "Ada Example, Lin Example" in response.text
    assert "10.1000/example" in response.text
    assert "<h3>Versions</h3>" not in response.text
    assert "journal_final" not in response.text
    assert "openalex · W123" in response.text
    assert "ZOT123" in response.text


@pytest.mark.parametrize(
    "status, doi, arxiv, expected_url",
    [
        (WorkflowStatus.KEPT, " 10.1000/EXAMPLE ", None, "https://doi.org/10.1000/example"),
        (WorkflowStatus.KEPT, " https://doi.org/10.1000/EXAMPLE ", None, "https://doi.org/10.1000/example"),
        (WorkflowStatus.KEPT, "10.1000/a?b#c(d)", None, "https://doi.org/10.1000/a%3Fb%23c%28d%29"),
        (WorkflowStatus.CANDIDATE, "10.1000/example", None, None),
        (WorkflowStatus.REJECTED, "10.1000/example", None, None),
        (WorkflowStatus.IN_ZOTERO, "10.1000/example", None, None),
        (WorkflowStatus.KEPT, None, None, None),
        (WorkflowStatus.KEPT, "", None, None),
        (WorkflowStatus.KEPT, 123, None, None),
        (WorkflowStatus.KEPT, "https://doi.org/", None, None),
        (WorkflowStatus.KEPT, None, "2609.12345", None),
        (
            WorkflowStatus.KEPT,
            '10.1000/"<script>bad</script>',
            None,
            "https://doi.org/10.1000/%22%3Cscript%3Ebad%3C/script%3E",
        ),
    ],
)
@pytest.mark.filterwarnings("ignore:Pydantic serializer warnings:UserWarning")
def test_open_doi_and_check_zotero_are_server_normalized_kept_only_presentation(
    tmp_path, monkeypatch, status, doi, arxiv, expected_url,
):
    # Bypass model validation to exercise malformed adapter input without changing the domain schema.
    paper = replace(make_paper(status=status), external_ids=ExternalIds.model_construct(doi=doi, arxiv=arxiv))
    snapshot = WorkspaceSnapshot(papers=(paper,), issues=())
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(web_app, "load_workspace", lambda path, *, journals: snapshot)
    monkeypatch.setattr(
        web_app,
        "reconcile_paper_with_zotero",
        lambda *args: pytest.fail("Open DOI presentation must not mutate"),
    )
    normalized_inputs = []
    original_normalize = web_app.normalize_doi

    def normalize(value):
        normalized_inputs.append(value)
        return original_normalize(value)

    monkeypatch.setattr(web_app, "normalize_doi", normalize)
    view = {WorkflowStatus.CANDIDATE: "inbox", WorkflowStatus.IN_ZOTERO: "in-zotero"}.get(status, status.value)

    class CaptureActions(HTMLParser):
        def __init__(self):
            super().__init__()
            self.open_links = []
            self.check_forms = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "a" and "data-open-doi" in attrs:
                self.open_links.append(attrs)
            if tag == "form" and "data-check-zotero-form" in attrs:
                self.check_forms.append(attrs)

    with TestClient(create_app(tmp_path / "monitor.yaml"), base_url="http://localhost") as client:
        responses = (
            client.get("/", params={"view": view, "paper": str(paper.paper_id)}),
            client.get(f"/fragments/papers/{paper.paper_id}", params={"view": view}),
        )
    for response in responses:
        assert response.status_code == 200
        parser = CaptureActions()
        parser.feed(response.text)
        assert "Copy DOI" not in response.text
        assert "Mark in Zotero" not in response.text
        if expected_url is None:
            assert not parser.open_links and not parser.check_forms
            assert "Open DOI" not in response.text and "Check Zotero" not in response.text
        else:
            assert len(parser.open_links) == 1 and len(parser.check_forms) == 1
            link = parser.open_links[0]
            assert link["href"] == expected_url
            assert link["target"] == "_blank"
            assert set(link["rel"].split()) == {"noopener", "noreferrer"}
            assert link["referrerpolicy"] == "no-referrer"
            assert set(link) == {
                "class", "href", "target", "rel", "referrerpolicy", "data-open-doi"
            }
            form = parser.check_forms[0]
            assert form["action"] == form["hx-post"] == f"/papers/{paper.paper_id}/check-zotero"
            assert form["hx-target"] == "#workspace-root" and form["hx-swap"] == "outerHTML"
            assert 'name="expected_status" value="kept"' in response.text
            assert 'name="csrf_token"' in response.text
            assert "<script>bad</script>" not in response.text
        assert "Add PDF to Zotero" not in response.text
        assert "HX-Trigger" not in response.headers
    assert normalized_inputs == ([doi, doi] if status is WorkflowStatus.KEPT else [])
    assert paper.status is status and paper.zotero_key is None


def test_open_doi_return_reconciliation_is_one_shot_page_local_js(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable for the executable Open DOI return-flow test")
    with TestClient(create_app(tmp_path / "monitor.yaml"), base_url="http://localhost") as client:
        javascript = client.get("/static/app.js").text
    assert 'htmx.ajax("POST", pending.form.action' in javascript
    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
        assert forbidden not in javascript
    return_flow = javascript.split("function rememberOpenDoi(link)", 1)[1].split("function syncRunAnnouncement", 1)[0]
    for forbidden in ("setTimeout", "setInterval", "inspect_attachments"):
        assert forbidden not in return_flow
    harness = r"""
const vm = require("node:vm");
const assert = require("node:assert/strict");
const handlers = {};
const windowHandlers = {};
const requests = [];
class Element {}
class Form extends Element {
  constructor() {
    super();
    this.action = "/papers/paper-1/check-zotero";
    this.values = [["csrf_token", "csrf"], ["expected_status", "kept"], ["view", "kept"], ["position", "0"]];
  }
}
class Link extends Element {
  constructor(form) { super(); this.form = form; }
  closest(selector) {
    if (selector === "[data-open-doi]") return this;
    if (selector === ".decision-actions") return {querySelector: () => this.form};
    return null;
  }
}
class FormData {
  constructor(form) { this.values = form.values; }
  entries() { return this.values[Symbol.iterator](); }
}
const document = {
  documentElement: {dataset: {}},
  visibilityState: "visible",
  getElementById: () => null,
  querySelector: () => null,
  addEventListener: (name, handler) => { handlers[name] = handler; },
  body: {addEventListener: () => {}},
};
const htmx = {ajax: (method, path, options) => {
  requests.push({method, path, options});
  return Promise.resolve();
}};
vm.runInNewContext(SOURCE, {
  document, Element, HTMLElement: Element, HTMLDetailsElement: Element, FormData, htmx,
  window: {location: {hash: ""}, innerWidth: 1024, innerHeight: 800,
           addEventListener: (name, handler) => { windowHandlers[name] = handler; }},
});
(async () => {
  const form = new Form();
  handlers.click({target: new Link(form)});
  windowHandlers.focus();
  assert.equal(requests.length, 0);
  windowHandlers.blur();
  windowHandlers.focus();
  handlers.visibilitychange();
  windowHandlers.focus();
  await Promise.resolve();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].method, "POST");
  assert.equal(requests[0].path, form.action);
  assert.equal(requests[0].options.target, "#workspace-root");
  assert.equal(requests[0].options.swap, "outerHTML");
  assert.deepEqual({...requests[0].options.values}, {
    csrf_token: "csrf", expected_status: "kept", view: "kept", position: "0"
  });
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    harness = "const SOURCE = " + json.dumps(javascript) + ";\n" + harness
    result = subprocess.run([node], input=harness, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_unknown_paper_detail_is_safe_normal_fragment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(
        web_app,
        "load_workspace",
        lambda output_dir, *, journals: WorkspaceSnapshot(papers=(), issues=()),
    )
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get(f"/fragments/papers/{uuid4()}")

    assert response.status_code == 200
    assert "Paper not found in the current view." in response.text
    assert "Traceback" not in response.text


def test_missing_and_invalid_csrf_block_decision_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = make_paper()
    snapshot = WorkspaceSnapshot(papers=(paper,), issues=())
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(web_app, "load_workspace", lambda output_dir, *, journals: snapshot)

    def fake_keep(*args: object) -> DecisionResult:
        calls.append(args)
        raise AssertionError("decision must not be called")

    monkeypatch.setattr(web_app, "keep_paper", fake_keep)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        missing = client.post(
            f"/papers/{paper.paper_id}/keep",
            data={"expected_status": "candidate", "view": "inbox"},
        )
        invalid = client.post(
            f"/papers/{paper.paper_id}/keep",
            data={
                "csrf_token": "wrong",
                "expected_status": "candidate",
                "view": "inbox",
            },
        )
        non_ascii = client.post(
            f"/papers/{paper.paper_id}/keep",
            data={
                "csrf_token": "錯誤-token",
                "expected_status": "candidate",
                "view": "inbox",
            },
        )

    assert missing.status_code == 403
    assert invalid.status_code == 403
    assert non_ascii.status_code == 403
    assert calls == []


def test_valid_csrf_reaches_keep_boundary_with_submitted_expected_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = make_paper()
    snapshot = WorkspaceSnapshot(papers=(paper,), issues=())
    captured: list[tuple[Path, UUID, WorkflowStatus]] = []
    workspace_loads: list[Path] = []
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))

    def fake_load_workspace(output_dir: Path, *, journals) -> WorkspaceSnapshot:
        workspace_loads.append(output_dir)
        return snapshot

    monkeypatch.setattr(web_app, "load_workspace", fake_load_workspace)

    def fake_keep(
        output_dir: Path,
        paper_id: UUID,
        expected_status: WorkflowStatus,
    ) -> DecisionResult:
        captured.append((output_dir, paper_id, expected_status))
        return decision_result(
            paper_id,
            expected_status,
            resulting_status=WorkflowStatus.KEPT,
        )

    monkeypatch.setattr(web_app, "keep_paper", fake_keep)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/", params={"paper": str(paper.paper_id)})
        csrf = csrf_from_html(page.text)
        response = client.post(
            f"/papers/{paper.paper_id}/keep",
            data={
                "csrf_token": csrf,
                "expected_status": "candidate",
                "view": "inbox",
            },
        )

    assert response.status_code == 200
    assert captured == [(tmp_path, paper.paper_id, WorkflowStatus.CANDIDATE)]
    assert workspace_loads == [tmp_path, tmp_path]


@pytest.mark.parametrize(
    ("route_suffix", "attribute", "expected_status"),
    (
        ("keep", "keep_paper", WorkflowStatus.CANDIDATE),
        ("reject", "reject_paper", WorkflowStatus.CANDIDATE),
        ("check-zotero", "reconcile_paper_with_zotero", WorkflowStatus.KEPT),
    ),
)
def test_each_decision_route_calls_exact_application_action(
    route_suffix: str,
    attribute: str,
    expected_status: WorkflowStatus,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = make_paper(status=expected_status)
    snapshot = WorkspaceSnapshot(papers=(paper,), issues=())
    called: list[str] = []
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(web_app, "load_workspace", lambda output_dir, *, journals: snapshot)

    def fake_action(
        output_dir: Path,
        paper_id: UUID,
        status: WorkflowStatus,
    ) -> DecisionResult:
        called.append(attribute)
        return decision_result(paper_id, status)

    monkeypatch.setattr(web_app, attribute, fake_action)
    app = create_app(tmp_path / "monitor.yaml")
    view = "kept" if expected_status is WorkflowStatus.KEPT else "inbox"

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/", params={"paper": str(paper.paper_id), "view": view})
        csrf = csrf_from_html(page.text)
        response = client.post(
            f"/papers/{paper.paper_id}/{route_suffix}",
            data={
                "csrf_token": csrf,
                "expected_status": expected_status.value,
                "view": "kept" if expected_status is WorkflowStatus.KEPT else "inbox",
            },
        )

    assert response.status_code == 200
    assert called == [attribute]


def test_state_conflict_renders_message_and_refreshed_disk_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_id = uuid4()
    candidate = make_paper(
        paper_id=paper_id,
        status=WorkflowStatus.CANDIDATE,
        title="Concurrent Paper",
    )
    current = make_paper(
        paper_id=paper_id,
        status=WorkflowStatus.KEPT,
        title="Concurrent Paper",
    )
    snapshots = [
        WorkspaceSnapshot(papers=(candidate,), issues=()),
        WorkspaceSnapshot(papers=(current,), issues=()),
    ]
    load_calls = 0
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))

    def fake_load_workspace(output_dir: Path, *, journals) -> WorkspaceSnapshot:
        nonlocal load_calls
        snapshot = snapshots[min(load_calls, len(snapshots) - 1)]
        load_calls += 1
        return snapshot

    monkeypatch.setattr(web_app, "load_workspace", fake_load_workspace)

    def conflict(
        output_dir: Path,
        requested_id: UUID,
        expected_status: WorkflowStatus,
    ) -> DecisionResult:
        return decision_result(
            requested_id,
            expected_status,
            outcome=DecisionOutcome.STATE_CONFLICT,
            current_status=WorkflowStatus.KEPT,
            message="Paper status changed on disk.",
        )

    monkeypatch.setattr(web_app, "keep_paper", conflict)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/", params={"paper": str(paper_id)})
        csrf = csrf_from_html(page.text)
        response = client.post(
            f"/papers/{paper_id}/keep",
            data={
                "csrf_token": csrf,
                "expected_status": "candidate",
                "view": "inbox",
            },
        )

    assert response.status_code == 200
    assert "Paper status changed on disk." in response.text
    assert 'data-selected-paper-id=""' in response.text
    assert 'aria-current="true"' not in response.text
    assert load_calls == 2


def selected_id(html: str) -> str:
    return re.search(r'data-selected-paper-id="([^"]*)"', html).group(1)


@pytest.mark.parametrize("surface", ["/", "/fragments/workspace", "/fragments/papers"])
@pytest.mark.parametrize("in_view", [False, True])
def test_selection_is_current_view_only_and_detail_click_keeps_list_target(tmp_path, monkeypatch, surface, in_view):
    candidate = make_paper(title="Current view Paper")
    kept = make_paper(title="Other view Paper", status=WorkflowStatus.KEPT)
    snapshot = WorkspaceSnapshot((candidate, kept), ())
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(web_app, "load_workspace", lambda output, *, journals: snapshot)
    requested = candidate if in_view else kept
    detail_only = surface == "/fragments/papers"
    route = f"{surface}/{requested.paper_id}" if detail_only else surface
    with TestClient(create_app(tmp_path / "monitor.yaml"), base_url="http://localhost") as client:
        text = client.get(route, params={"view": "inbox", "paper": str(requested.paper_id)}).text
    assert selected_id(text) == (str(candidate.paper_id) if in_view else "")
    assert "Other view Paper" not in text
    if detail_only:
        assert 'id="paper-list"' not in text
        assert 'data-active-view="inbox"' in text
    else:
        assert text.count('aria-current="true"') == int(in_view)
        assert f'href="/?view=inbox&paper={candidate.paper_id}"' in text
        assert f'hx-get="/fragments/papers/{candidate.paper_id}?view=inbox"' in text
        assert 'hx-target="#paper-detail"' in text
        if in_view:
            assert 'name="position" value="0"' in text


@pytest.mark.parametrize("index,count,position,retained,expected", [
    (1, 3, "1", False, "C"),
    (2, 3, "2", False, "B"),
    (0, 1, "0", False, None),
    (1, 3, "999999", True, "B refreshed"),
    (1, 3, "-7", False, "A"),
    (1, 3, "999999999999999999999999999", False, "C"),
    (1, 3, "malformed", False, "A"),
    (1, 3, None, False, "A"),
])
def test_successful_decision_uses_refreshed_view_for_safe_neighbor_navigation(
    tmp_path, monkeypatch, index, count, position, retained, expected,
):
    before = WorkspaceSnapshot(tuple(make_paper(title=label) for label in ("A", "B", "C")[:count]), ())
    target = before.papers[index]
    after = WorkspaceSnapshot(tuple(
        replace(paper, title="B refreshed", status=WorkflowStatus.CANDIDATE)
        if retained and paper.paper_id == target.paper_id else
        replace(paper, status=WorkflowStatus.KEPT) if paper.paper_id == target.paper_id else paper
        for paper in before.papers
    ), ())
    current = before
    loads, calls = [], []
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))

    def load(output, *, journals):
        loads.append(current)
        return current

    def action(output, paper_id, expected_status):
        nonlocal current
        calls.append((output, paper_id, expected_status))
        current = after
        return decision_result(paper_id, expected_status, resulting_status=WorkflowStatus.KEPT)

    monkeypatch.setattr(web_app, "load_workspace", load)
    monkeypatch.setattr(web_app, "keep_paper", action)
    with TestClient(create_app(tmp_path / "monitor.yaml"), base_url="http://localhost") as client:
        page = client.get("/", params={"paper": str(target.paper_id)})
        form = {"csrf_token": csrf_from_html(page.text), "expected_status": "candidate", "view": "inbox"}
        if position is not None:
            form["position"] = position
        response = client.post(f"/papers/{target.paper_id}/keep", data=form)
    assert response.status_code == 200
    assert calls == [(tmp_path, target.paper_id, WorkflowStatus.CANDIDATE)]
    assert loads == [before, after]
    selected = next((paper for paper in after.inbox if paper.title == expected), None)
    assert selected_id(response.text) == (str(selected.paper_id) if selected else "")
    assert response.text.count('aria-current="true"') == int(selected is not None)
    assert f'data-selection-stepped="{"true" if selected and not retained else "false"}"' in response.text


@pytest.mark.parametrize("outcome", [DecisionOutcome.STATE_CONFLICT, DecisionOutcome.IO_FAILURE])
@pytest.mark.parametrize("retained", [False, True])
def test_failed_decision_ignores_position_and_revalidates_original_uuid(tmp_path, monkeypatch, outcome, retained):
    before = WorkspaceSnapshot(tuple(make_paper(title=label) for label in ("A", "B", "C")), ())
    target = before.papers[1]
    after = before if retained else WorkspaceSnapshot(tuple(
        replace(paper, status=WorkflowStatus.KEPT) if paper.paper_id == target.paper_id else paper
        for paper in before.papers
    ), ())
    current, calls = before, []
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(web_app, "load_workspace", lambda output, *, journals: current)

    def action(output, paper_id, expected_status):
        nonlocal current
        calls.append((output, paper_id, expected_status))
        current = after
        return decision_result(paper_id, expected_status, outcome=outcome, message="Expected decision failure")

    monkeypatch.setattr(web_app, "keep_paper", action)
    with TestClient(create_app(tmp_path / "monitor.yaml"), base_url="http://localhost") as client:
        page = client.get("/", params={"paper": str(target.paper_id)})
        text = client.post(f"/papers/{target.paper_id}/keep", data={
            "csrf_token": csrf_from_html(page.text), "expected_status": "candidate", "view": "inbox", "position": "1",
        }).text
    assert calls == [(tmp_path, target.paper_id, WorkflowStatus.CANDIDATE)]
    assert "Expected decision failure" in text
    assert selected_id(text) == (str(target.paper_id) if retained else "")
    assert 'data-selection-stepped="false"' in text


@pytest.mark.parametrize("left_view", [False, True])
def test_workspace_refresh_revalidates_transient_selected_uuid(tmp_path, monkeypatch, left_view):
    paper = make_paper()
    current = WorkspaceSnapshot((paper,), ())
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(web_app, "load_workspace", lambda output, *, journals: current)
    with TestClient(create_app(tmp_path / "monitor.yaml"), base_url="http://localhost") as client:
        params = {"view": "inbox", "paper": str(paper.paper_id)}
        assert selected_id(client.get("/", params=params).text) == str(paper.paper_id)
        if left_view:
            current = WorkspaceSnapshot((replace(paper, status=WorkflowStatus.KEPT),), ())
        text = client.get("/fragments/workspace", params=params).text
    assert selected_id(text) == ("" if left_view else str(paper.paper_id))
    assert text.count('aria-current="true"') == int(not left_view)


def test_workspace_pane_css_and_transient_selection_contract(tmp_path):
    with TestClient(create_app(tmp_path / "monitor.yaml"), base_url="http://localhost") as client:
        css = client.get("/static/app.css").text
        js = client.get("/static/app.js").text
    assert "@media (min-width: 761px)" in css
    assert "height: var(--workspace-pane-height, auto)" in css
    assert "overflow-y: auto" in css and '[aria-current="true"]' in css
    mobile = css.split("@media (max-width: 760px)")[1]
    assert "height: auto" in mobile and "overflow: visible" in mobile
    assert "getBoundingClientRect().top" in js and "window.innerHeight" in js
    assert "localStorage" not in js and "sessionStorage" not in js


@pytest.mark.parametrize("failure", ["invalid_status", "config"])
def test_pre_action_failure_has_no_neighbor_navigation_or_mutation(tmp_path, monkeypatch, failure):
    target, peer = make_paper(title="Original"), make_paper(title="Peer")
    current = WorkspaceSnapshot((target, peer), ())
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(web_app, "load_workspace", lambda output, *, journals: current)
    monkeypatch.setattr(web_app, "keep_paper", lambda *args: pytest.fail("invalid input must not mutate"))
    with TestClient(create_app(tmp_path / "monitor.yaml"), base_url="http://localhost") as client:
        page = client.get("/", params={"paper": str(target.paper_id)})
        current = WorkspaceSnapshot((replace(target, status=WorkflowStatus.KEPT), peer), ())
        if failure == "config":
            def config_failed(path):
                raise web_app.ConfigurationError("Configuration changed on disk")
            monkeypatch.setattr(web_app, "load_config", config_failed)
        response = client.post(f"/papers/{target.paper_id}/keep", data={
            "csrf_token": csrf_from_html(page.text), "expected_status": "invalid",
            "view": "inbox", "position": "0",
        })
    assert response.status_code == (400 if failure == "invalid_status" else 200)
    if failure == "invalid_status":
        assert selected_id(response.text) == ""
    else:
        assert "Configuration needs attention" in response.text
        assert 'id="paper-detail"' not in response.text
    assert 'data-selection-stepped="false"' in response.text


@pytest.mark.parametrize(
    "outcome",
    (
        DecisionOutcome.NOT_FOUND,
        DecisionOutcome.INVALID_PAPER,
        DecisionOutcome.INVALID_TRANSITION,
        DecisionOutcome.IO_FAILURE,
    ),
)
def test_expected_decision_failures_remain_normal_html_states(
    outcome: DecisionOutcome,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = make_paper()
    snapshot = WorkspaceSnapshot(papers=(paper,), issues=())
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(web_app, "load_workspace", lambda output_dir, *, journals: snapshot)

    def expected_failure(
        output_dir: Path,
        paper_id: UUID,
        expected_status: WorkflowStatus,
    ) -> DecisionResult:
        return decision_result(
            paper_id,
            expected_status,
            outcome=outcome,
            message=f"Expected failure: {outcome.value}",
        )

    monkeypatch.setattr(web_app, "reject_paper", expected_failure)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/", params={"paper": str(paper.paper_id)})
        csrf = csrf_from_html(page.text)
        response = client.post(
            f"/papers/{paper.paper_id}/reject",
            data={
                "csrf_token": csrf,
                "expected_status": "candidate",
                "view": "inbox",
            },
        )

    assert response.status_code == 200
    assert f"Expected failure: {outcome.value}" in response.text
    assert "Traceback" not in response.text


def test_no_generic_status_and_run_settings_routes_are_explicit(tmp_path: Path) -> None:
    app = create_app(tmp_path / "missing.yaml")
    route_methods = {
        (route.path, method)
        for route in app.routes
        if hasattr(route, "methods")
        for method in route.methods
    }

    assert not any(path.endswith("/status") for path, _ in route_methods)
    assert not any("copy" in path or path == "/fragments/zotero-export" for path, _ in route_methods)
    assert ("/run", "POST") in route_methods
    assert ("/fragments/run", "GET") in route_methods
    assert ("/settings/validate", "POST") in route_methods
    assert ("/settings/save", "POST") in route_methods


def test_web_zotero_export_is_removed(tmp_path, monkeypatch):
    paper = make_paper(status=WorkflowStatus.KEPT)
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(web_app, "load_workspace", lambda path, *, journals: WorkspaceSnapshot(papers=(paper,), issues=()))
    app = create_app(tmp_path / "monitor.yaml")
    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/", params={"view": "kept", "paper": str(paper.paper_id)})
        fragment = client.get("/fragments/workspace", params={"view": "kept"})
        removed = client.get("/fragments/zotero-export")
    assert removed.status_code == 404
    for response in (page, fragment):
        for old_export in ("Zotero export", "Load export", "zotero-export", "<textarea"):
            assert old_export not in response.text
    assert not hasattr(web_app, "export_kept_papers")
    assert not hasattr(web_app, "_export_context")
    assert not (Path(web_app.__file__).parent / "templates/fragments/zotero_export.html").exists()


@pytest.mark.parametrize("duplicate", [False, True])
def test_check_zotero_route_verifies_zotero_and_refreshes_real_workspace_without_false_navigation(
    tmp_path, monkeypatch, duplicate,
):
    config_path, output_dir = write_valid_config(tmp_path)
    papers_dir = output_dir / "Papers"
    papers_dir.mkdir(parents=True)
    paths = []
    for ordinal in (1, 2):
        paper = CanonicalPaper(
            id=UUID(int=ordinal), metadata=CanonicalMetadata(title=f"Kept {ordinal}", journal="Biometrics"),
            external_ids=ExternalIds(doi=f"10.5555/reconcile-{ordinal}"), authors=(Author(name="Ada Author"),),
            workflow=Workflow(status=WorkflowStatus.KEPT, discovered_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
                              zotero_key="PARENT01" if ordinal == 1 else None),
        )
        path = papers_dir / f"{ordinal}.md"
        path.write_text(render_paper_markdown(paper, ("ada-author",)))
        paths.append(path)
    before = {path: path.read_bytes() for path in paths}
    requests = []
    clients = []

    def respond(request):
        requests.append(request)
        assert request.method == "GET" and request.url.path == "/api/users/0/items"
        assert "Zotero-API-Key" not in request.headers and "Authorization" not in request.headers
        keys = ["PARENT01", "PARENT02"] if duplicate else ["PARENT01"]
        return httpx.Response(200, headers={
            "Zotero-Server-ID": "local-instance", "Last-Modified-Version": "4", "Total-Results": str(len(keys)),
        }, json=[{"key": key, "data": {
            "key": key, "itemType": "journalArticle", "DOI": "HTTPS://DOI.ORG/10.5555/RECONCILE-1",
        }} for key in keys])

    def factory():
        client = ZoteroLocalClient(transport=httpx.MockTransport(respond))
        clients.append(client)
        return client

    monkeypatch.setattr(decisions, "_ZoteroLocalClient", factory)
    with TestClient(create_app(config_path), base_url="http://localhost") as client:
        page = client.get("/", params={"view": "kept", "paper": str(UUID(int=1))})
        form = {
            "csrf_token": csrf_from_html(page.text), "expected_status": "kept", "view": "kept", "position": "0",
        }
        response = client.post(f"/papers/{UUID(int=1)}/check-zotero", data=form)
        retry = client.post(f"/papers/{UUID(int=1)}/check-zotero", data=form) if duplicate else None
        in_zotero = client.get("/fragments/workspace", params={"view": "in-zotero"})

    assert response.status_code == 200 and len(requests) == (2 if duplicate else 1)
    assert all(client._http.is_closed for client in clients)
    state = parse_paper_state(paths[0], paths[0].read_text(), output_dir / "Authors")
    assert state is not None and state.updateable
    if duplicate:
        assert state.status is WorkflowStatus.KEPT
        assert {path: path.read_bytes() for path in paths} == before
        assert "Multiple exact DOI matches" in response.text
        assert selected_id(response.text) == str(UUID(int=1))
        assert 'data-selection-stepped="false"' in response.text
        assert retry is not None and retry.status_code == 200
        assert "Multiple exact DOI matches" in retry.text
        assert selected_id(retry.text) == str(UUID(int=1))
        assert listed_ids(in_zotero.text) == []
    else:
        assert state.status is WorkflowStatus.IN_ZOTERO and state.zotero_key == "PARENT01"
        assert paths[1].read_bytes() == before[paths[1]]
        assert listed_ids(response.text) == [str(UUID(int=2))]
        assert selected_id(response.text) == str(UUID(int=2))
        assert 'data-selection-stepped="true"' in response.text
        assert listed_ids(in_zotero.text) == [str(UUID(int=1))]


def test_settings_get_is_recoverable_editor(tmp_path: Path) -> None:
    app = create_app(tmp_path / "missing.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/settings")

    assert response.status_code == 200
    assert 'id="settings-form"' in response.text
    assert 'name="monitor_revision_exists"' in response.text
    assert 'name="journal_revision_exists"' in response.text
    assert "Configuration needs attention" in response.text


def test_vendored_htmx_is_local_and_package_relative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app(tmp_path / "missing.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/")
        asset = client.get("/static/vendor/htmx.min.js")

    assert page.status_code == 200
    assert "/static/vendor/htmx.min.js" in page.text
    assert "cdn.jsdelivr" not in page.text
    assert "unpkg.com" not in page.text
    assert asset.status_code == 200
    assert b"htmx" in asset.content[:500]


def seed_grouped_workspace(tmp_path: Path) -> tuple[Path, Path]:
    config_path, output_dir = write_valid_config(tmp_path)
    (tmp_path / "list.md").write_text(
        "## Journals\n| Journal | ISSN/EISSN | Group |\n|---|---|---|\n"
        "| Biometrics | 0006-341X | Z Statistics |\n"
        "| Annals of Statistics | 0090-5364 | |\n"
        "| JASA | 0162-1459 | A Methods |\n"
        "| Psychometrika | 0033-3123 | Z Statistics |\n"
        "| Unused | 0092-5853 | Empty group |\n",
    )
    for ordinal, title, issn, status in (
        (1, "Z grouped", "0006-341X", WorkflowStatus.CANDIDATE),
        (2, "Y grouped", "0033-3123", WorkflowStatus.CANDIDATE),
        (3, "A methods", "0162-1459", WorkflowStatus.CANDIDATE),
        (4, "B ungrouped", "0090-5364", WorkflowStatus.CANDIDATE),
        (5, "C unmapped", "0036-1992", WorkflowStatus.CANDIDATE),
        (6, "K kept", "0006-341X", WorkflowStatus.KEPT),
    ):
        paper = CanonicalPaper(
            id=UUID(int=ordinal), metadata=CanonicalMetadata(title=title, journal="Provider label"),
            authors=(Author(name="Ada Author"),), external_ids=ExternalIds(doi=f"10.5555/{ordinal}"),
            journal_issns=(issn,), workflow=Workflow(status=status, discovered_at=datetime(2026, 9, 20, tzinfo=timezone.utc)),
        )
        path = output_dir / "Papers" / f"{ordinal}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_paper_markdown(paper, ("ada-author",)))
    return config_path, output_dir


def group_headings(html: str) -> list[tuple[str, str]]:
    return re.findall(r'<section class="paper-group" data-section-kind="([^"]+)">\s*<div class="paper-group-heading">\s*<h2>(.*?)</h2>', html)


def listed_ids(html: str) -> list[str]:
    return re.findall(r'data-paper-id="([^"]+)"', html)


def test_sectioned_web_list_uses_saved_journals_and_one_flat_selection_order(tmp_path, monkeypatch):
    config_path, output_dir = seed_grouped_workspace(tmp_path)
    expected_journals = load_config(config_path).journals
    captured = []
    original = web_app.load_workspace
    def load(output, *, journals):
        assert output == output_dir and journals == expected_journals
        captured.append(journals)
        return original(output, journals=journals)
    monkeypatch.setattr(web_app, "load_workspace", load)
    with TestClient(create_app(config_path), base_url="http://localhost") as client:
        page = client.get("/", params={"paper": str(UUID(int=3))})
        outside = client.get("/fragments/workspace", params={"paper": str(UUID(int=6))})
        detail = client.get(f"/fragments/papers/{UUID(int=3)}", params={"view": "inbox"})
    assert len(captured) == 3
    assert group_headings(page.text) == [
        ("group", "Z Statistics"), ("group", "A Methods"),
        ("ungrouped", "Ungrouped"), ("unmapped", "Unmapped journals"),
    ]
    assert listed_ids(page.text) == [str(UUID(int=i)) for i in (2, 1, 3, 4, 5)]
    nav = re.search(r'<nav class="view-tabs".*?</nav>', page.text, re.S).group()
    assert re.findall(r'href="/\?view=([^"]+)"', nav) == ["inbox", "kept", "rejected", "in-zotero"]
    assert page.text.count('class="view-tabs"') == 1 and "Empty group" not in page.text
    assert '<h1>Inbox</h1>\n    <span class="count">5</span>' in page.text
    assert selected_id(page.text) == str(UUID(int=3)) and page.text.count('aria-current="true"') == 1
    assert 'name="position" value="2"' in page.text
    assert f'href="/?view=inbox&paper={UUID(int=3)}"' in page.text
    assert f'hx-get="/fragments/papers/{UUID(int=3)}?view=inbox"' in page.text
    assert 'hx-target="#paper-detail"' in page.text
    assert selected_id(outside.text) == "" and 'aria-current="true"' not in outside.text
    assert 'id="paper-detail"' in detail.text and 'id="paper-list"' not in detail.text
    assert 'name="position" value="2"' in detail.text
    assert 'hx-trigger="runCompleted from:body, settingsSaved from:body"' in page.text


def test_successful_decision_selects_neighbor_across_section_boundary_from_disk(tmp_path):
    config_path, output_dir = seed_grouped_workspace(tmp_path)
    target = UUID(int=1)
    with TestClient(create_app(config_path), base_url="http://localhost") as client:
        page = client.get("/", params={"paper": str(target)})
        assert 'name="position" value="1"' in page.text
        response = client.post(f"/papers/{target}/keep", data={
            "csrf_token": csrf_from_html(page.text), "expected_status": "candidate", "view": "inbox", "position": "1",
        })
    assert response.status_code == 200
    assert selected_id(response.text) == str(UUID(int=3))
    assert listed_ids(response.text) == [str(UUID(int=i)) for i in (2, 3, 4, 5)]
    assert 'data-selection-stepped="true"' in response.text and 'name="position" value="1"' in response.text
    assert {p.paper_id for p in web_app.load_workspace(output_dir, load_config(config_path).journals).kept} == {UUID(int=1), UUID(int=6)}


@pytest.mark.parametrize("outcome", [DecisionOutcome.STATE_CONFLICT, DecisionOutcome.IO_FAILURE])
def test_sectioned_decision_failure_keeps_current_uuid_and_does_not_step_or_write(tmp_path, monkeypatch, outcome):
    config_path, output_dir = seed_grouped_workspace(tmp_path)
    before = {p: p.read_bytes() for p in (output_dir / "Papers").glob("*.md")}
    target = UUID(int=1)
    monkeypatch.setattr(web_app, "keep_paper", lambda output, paper_id, expected_status: decision_result(
        paper_id, expected_status, outcome=outcome, message="Expected sectioned failure",
    ))
    with TestClient(create_app(config_path), base_url="http://localhost") as client:
        page = client.get("/", params={"paper": str(target)})
        response = client.post(f"/papers/{target}/keep", data={
            "csrf_token": csrf_from_html(page.text), "expected_status": "candidate", "view": "inbox", "position": "999",
        })
    assert selected_id(response.text) == str(target) and 'data-selection-stepped="false"' in response.text
    assert 'name="position" value="1"' in response.text and "Expected sectioned failure" in response.text
    assert all(p.read_bytes() == value for p, value in before.items())


def test_workspace_reload_after_settings_save_reprojects_groups_without_paper_writes(tmp_path):
    from literature_monitor.application.settings import load_settings, save_settings, SettingsSaveOutcome
    config_path, output_dir = seed_grouped_workspace(tmp_path)
    before = {p: p.read_bytes() for p in (output_dir / "Papers").glob("*.md")}
    with TestClient(create_app(config_path), base_url="http://localhost") as client:
        page = client.get("/")
        state = load_settings(config_path)
        journals = state.draft.journals
        unsaved = replace(state.draft, journals=tuple(j.model_copy(update={"group": "Unsaved"}) for j in journals))
        assert group_headings(client.get("/fragments/workspace").text) == group_headings(page.text)
        assert unsaved.journals != load_config(config_path).journals
        changed = (journals[2].model_copy(update={"group": "Unmapped journals"}),
                   journals[0].model_copy(update={"group": "Ungrouped"}),
                   journals[1], journals[3].model_copy(update={"group": "Ungrouped"}), journals[4])
        saved = save_settings(config_path, replace(state.draft, journals=changed))
        assert saved.outcome is SettingsSaveOutcome.SAVED
        refreshed = client.get("/fragments/workspace")
    assert group_headings(refreshed.text) == [
        ("group", "Unmapped journals"), ("group", "Ungrouped"),
        ("ungrouped", "Ungrouped"), ("unmapped", "Unmapped journals"),
    ]
    assert listed_ids(refreshed.text) == [str(UUID(int=i)) for i in (3, 2, 1, 4, 5)]
    assert all(p.read_bytes() == value for p, value in before.items())
    assert sorted(p.name for p in output_dir.iterdir()) == ["Papers"]
