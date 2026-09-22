from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

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
from literature_monitor.kept_export import KeptExportIssue, KeptExportResult
from literature_monitor.models import (
    ExternalIds,
    MetadataSource,
    PaperVersion,
    VersionKind,
    VersionRef,
    WorkflowStatus,
)
from literature_monitor.web.app import create_app


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
        versions=(
            PaperVersion(
                source="openalex",
                identifier="W123",
                kind=VersionKind.JOURNAL_FINAL,
                url="https://example.test/paper",
                date=date(2026, 9, 1),
            ),
        ),
        sources=(
            MetadataSource(
                provider="openalex",
                record_id="W123",
                retrieved_at=datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc),
            ),
        ),
        preferred_version=VersionRef(source="openalex", identifier="W123"),
        zotero_key=zotero_key,
    )


def fake_config(output_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(output_dir=output_dir)


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
    monkeypatch.setattr(web_app, "export_kept_papers", forbidden)

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
    monkeypatch.setattr(web_app, "load_workspace", lambda output_dir: snapshot)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<h1>Inbox</h1>" in response.text
    assert "Inbox Paper" in response.text
    assert "Kept Paper" not in response.text


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
    monkeypatch.setattr(web_app, "load_workspace", lambda output_dir: snapshot)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/", params={"view": view})

    assert response.status_code == 200
    assert title in response.text
    for other in papers:
        if other.status is not status:
            assert other.title not in response.text


def test_valid_papers_render_alongside_workspace_issues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = make_paper(title="Still Valid")
    snapshot = WorkspaceSnapshot(
        papers=(paper,),
        issues=(WorkspaceIssue(path=tmp_path / "bad.md", message="invalid frontmatter"),),
    )
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(web_app, "load_workspace", lambda output_dir: snapshot)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Still Valid" in response.text
    assert "invalid frontmatter" in response.text


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


def test_workspace_and_issue_fragments_render_fresh_application_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = make_paper(title="Fragment Paper")
    snapshot = WorkspaceSnapshot(
        papers=(paper,),
        issues=(WorkspaceIssue(path=tmp_path / "broken.md", message="broken Paper"),),
    )
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(web_app, "load_workspace", lambda output_dir: snapshot)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        workspace_response = client.get("/fragments/workspace")
        issues_response = client.get("/fragments/issues")

    assert workspace_response.status_code == 200
    assert "Fragment Paper" in workspace_response.text
    assert issues_response.status_code == 200
    assert "broken Paper" in issues_response.text


def test_paper_list_renders_projected_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = make_paper(title="Projected Metadata")
    snapshot = WorkspaceSnapshot(papers=(paper,), issues=())
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(web_app, "load_workspace", lambda output_dir: snapshot)
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
    monkeypatch.setattr(web_app, "load_workspace", lambda output_dir: snapshot)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get(f"/fragments/papers/{paper.paper_id}")

    assert response.status_code == 200
    assert "Detailed Paper" in response.text
    assert "A detailed abstract." in response.text
    assert "causal inference, statistics" in response.text
    assert "Ada Example, Lin Example" in response.text
    assert "10.1000/example" in response.text
    assert "journal_final" in response.text
    assert "openalex · W123" in response.text
    assert "ZOT123" in response.text


def test_unknown_paper_detail_is_safe_normal_fragment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(
        web_app,
        "load_workspace",
        lambda output_dir: WorkspaceSnapshot(papers=(), issues=()),
    )
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get(f"/fragments/papers/{uuid4()}")

    assert response.status_code == 200
    assert "Paper not found in the current workspace." in response.text
    assert "Traceback" not in response.text


def test_missing_and_invalid_csrf_block_decision_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = make_paper()
    snapshot = WorkspaceSnapshot(papers=(paper,), issues=())
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(tmp_path))
    monkeypatch.setattr(web_app, "load_workspace", lambda output_dir: snapshot)

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

    def fake_load_workspace(output_dir: Path) -> WorkspaceSnapshot:
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
        ("mark-in-zotero", "mark_paper_in_zotero", WorkflowStatus.KEPT),
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
    monkeypatch.setattr(web_app, "load_workspace", lambda output_dir: snapshot)

    def fake_action(
        output_dir: Path,
        paper_id: UUID,
        status: WorkflowStatus,
    ) -> DecisionResult:
        called.append(attribute)
        return decision_result(paper_id, status)

    monkeypatch.setattr(web_app, attribute, fake_action)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/", params={"paper": str(paper.paper_id)})
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

    def fake_load_workspace(output_dir: Path) -> WorkspaceSnapshot:
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
    assert "Status" in response.text
    assert "kept" in response.text
    assert load_calls == 2


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
    monkeypatch.setattr(web_app, "load_workspace", lambda output_dir: snapshot)

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


def test_no_generic_status_or_t7_run_settings_post_routes(tmp_path: Path) -> None:
    app = create_app(tmp_path / "missing.yaml")
    route_methods = {
        (route.path, method)
        for route in app.routes
        if hasattr(route, "methods")
        for method in route.methods
    }

    assert not any(path.endswith("/status") for path, _ in route_methods)
    assert ("/run", "POST") not in route_methods
    assert ("/fragments/run", "GET") not in route_methods
    assert ("/settings/validate", "POST") not in route_methods
    assert ("/settings/save", "POST") not in route_methods


def test_zotero_export_fragment_reuses_existing_export_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "workspace"
    calls: list[Path] = []
    monkeypatch.setattr(web_app, "load_config", lambda path: fake_config(output_dir))

    def fake_export(path: Path) -> KeptExportResult:
        calls.append(path)
        return KeptExportResult(
            entries=("10.1000/exported", "arXiv:2609.12345"),
            issues=(
                KeptExportIssue(
                    path=output_dir / "Papers" / "bad.md",
                    message="invalid Paper",
                ),
            ),
        )

    monkeypatch.setattr(web_app, "export_kept_papers", fake_export)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/fragments/zotero-export")

    assert response.status_code == 200
    assert calls == [output_dir]
    assert "10.1000/exported" in response.text
    assert "arXiv:2609.12345" in response.text
    assert "invalid Paper" in response.text


def test_settings_get_is_recoverable_placeholder(tmp_path: Path) -> None:
    app = create_app(tmp_path / "missing.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/settings")

    assert response.status_code == 200
    assert "Editing and Save/Validate actions arrive in the next Web task." in response.text
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
