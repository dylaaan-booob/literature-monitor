from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

import literature_monitor.application.decisions as decisions
import literature_monitor.web.app as web_app
import literature_monitor.web.capture_coordinator as capture_module
from literature_monitor.application.decisions import DecisionOutcome, DecisionResult
from literature_monitor.application.workspace import (
    WorkspaceAuthor,
    WorkspacePaper,
    WorkspaceSnapshot,
)
from literature_monitor.markdown_state import parse_paper_state
from literature_monitor.materialize import render_paper_markdown
from literature_monitor.models import (
    Author,
    CanonicalMetadata,
    CanonicalPaper,
    ExternalIds,
    Workflow,
    WorkflowStatus,
)
from literature_monitor.web.app import create_app
from literature_monitor.web.capture_coordinator import (
    ACTIVE_TIMEOUT_SECONDS,
    HEARTBEAT_STALE_SECONDS,
    WAITING_TIMEOUT_SECONDS,
    CaptureOutcome,
    CaptureStage,
    ConnectorReadiness,
)
from literature_monitor.web.zotero_capture import (
    SaveToZoteroOutcome,
    SaveToZoteroResult,
)
from literature_monitor.zotero_local import ZoteroLocalClient


NOW = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)
PAPER_ONE = UUID("11111111-1111-4111-8111-111111111111")
PAPER_TWO = UUID("22222222-2222-4222-8222-222222222222")


def write_config(tmp_path: Path) -> tuple[Path, Path]:
    (tmp_path / "list.md").write_text(
        "# Venues\n\n## Journals\n\n"
        "| Journal | ISSN/EISSN |\n"
        "|---|---|\n"
        "| Biometrics | 0006-341X |\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "monitor.yaml"
    config_path.write_text(
        "name: Web Capture\n"
        "venue_whitelist: list.md\n"
        "keyword_expression: causal\n"
        "output_dir: workspace\n"
        "window_days: 14\n"
        "log_level: INFO\n",
        encoding="utf-8",
    )
    return config_path, tmp_path / "workspace"


def write_paper(
    output_dir: Path,
    paper_id: UUID,
    *,
    doi: str,
    title: str,
    status: WorkflowStatus = WorkflowStatus.KEPT,
) -> Path:
    paper = CanonicalPaper(
        id=paper_id,
        metadata=CanonicalMetadata(
            title=title,
            journal="Biometrics",
            publication_date=date(2026, 10, 1),
            abstract="Automatic capture Web test.",
        ),
        external_ids=ExternalIds(doi=doi),
        authors=(Author(name="Ada Author"),),
        workflow=Workflow(
            status=status,
            discovered_at=NOW,
        ),
    )
    path = output_dir / "Papers" / f"{paper_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_paper_markdown(paper, ("ada-author",)),
        encoding="utf-8",
    )
    return path


def csrf_from_html(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def selected_id(html: str) -> str:
    match = re.search(r'data-selected-paper-id="([^"]*)"', html)
    assert match is not None
    return match.group(1)


def paper_state(path: Path, output_dir: Path):
    state = parse_paper_state(
        path,
        path.read_text(encoding="utf-8"),
        output_dir / "Authors",
    )
    assert state is not None
    return state


def library_page(
    items: list[dict[str, object]] | tuple[dict[str, object], ...] = (),
) -> httpx.Response:
    return httpx.Response(
        200,
        json=list(items),
        headers={
            "Zotero-Server-ID": "local-instance",
            "Last-Modified-Version": "7",
            "Total-Results": str(len(items)),
        },
    )


def zotero_item(key: str, doi: str) -> dict[str, object]:
    return {
        "key": key,
        "data": {
            "key": key,
            "itemType": "journalArticle",
            "DOI": doi,
        },
    }


@pytest.fixture
def zotero_server(monkeypatch: pytest.MonkeyPatch):
    pending: list[httpx.Response | Exception] = []
    requests: list[httpx.Request] = []
    clients: list[ZoteroLocalClient] = []

    def enqueue(*responses: httpx.Response | Exception) -> None:
        pending.extend(responses)

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/users/0/items"
        assert pending, "Unexpected Zotero Local API request"
        response = pending.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def factory() -> ZoteroLocalClient:
        client = ZoteroLocalClient(
            transport=httpx.MockTransport(respond),
        )
        clients.append(client)
        return client

    monkeypatch.setattr(decisions, "_ZoteroLocalClient", factory)
    yield enqueue, requests
    assert all(client._http.is_closed for client in clients)


def seed_two_kept(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    config_path, output_dir = write_config(tmp_path)
    first = write_paper(
        output_dir,
        PAPER_ONE,
        doi="10.5555/web-capture-1",
        title="First kept",
    )
    second = write_paper(
        output_dir,
        PAPER_TWO,
        doi="10.5555/web-capture-2",
        title="Second kept",
    )
    return config_path, output_dir, first, second


def save_form(
    csrf: str,
    *,
    position: str = "0",
) -> dict[str, str]:
    return {
        "csrf_token": csrf,
        "expected_status": "kept",
        "view": "kept",
        "position": position,
    }


def capture_poll_form(html: str) -> dict[str, str]:
    match = re.search(
        r'<form id="capture-status".*?</form>',
        html,
        re.S,
    )
    assert match is not None
    form = match.group(0)
    names = re.findall(r'name="([^"]+)"', form)
    assert set(names) <= {"csrf_token", "view", "position", "paper"}
    assert not {
        "request_id",
        "doi",
        "normalized_doi",
        "output_dir",
        "path",
        "outcome",
        "zotero_key",
    }.intersection(names)
    values = dict(
        re.findall(
            r'name="([^"]+)" value="([^"]*)"',
            form,
        )
    )
    assert "csrf_token" in values
    assert values.get("view") == "kept"
    return values


def connect(app) -> None:
    assert app.state.capture_coordinator.heartbeat(
        connector_version="1.0.0",
        zotero_reachable=True,
    ) is ConnectorReadiness.CONNECTED


def test_save_route_uses_csrf_config_and_server_authority_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_id = PAPER_ONE
    paper = WorkspacePaper(
        paper_id=paper_id,
        title="Kept Paper",
        journal="Biometrics",
        publication_date=date(2026, 10, 1),
        discovered_at=NOW,
        status=WorkflowStatus.KEPT,
        abstract="Abstract",
        author_keywords=(),
        authors=(WorkspaceAuthor(name="Ada Author", note_stem="ada-author"),),
        external_ids=ExternalIds(doi="10.5555/server-doi"),
        sources=(),
        zotero_key=None,
    )
    snapshot = WorkspaceSnapshot((paper,), ())
    authoritative_output = tmp_path / "authoritative-workspace"
    monkeypatch.setattr(
        web_app,
        "load_config",
        lambda path: SimpleNamespace(
            output_dir=authoritative_output,
            journals=(),
        ),
    )
    monkeypatch.setattr(
        web_app,
        "load_workspace",
        lambda output, *, journals: snapshot,
    )
    calls: list[tuple[Path, UUID, WorkflowStatus, object]] = []
    preflight = DecisionResult(
        outcome=DecisionOutcome.INVALID_PAPER,
        paper_id=paper_id,
        expected_status=WorkflowStatus.KEPT,
        current_status=WorkflowStatus.KEPT,
        resulting_status=None,
        path=None,
        message="Expected precheck failure.",
    )

    def start(output_dir, requested_id, expected_status, coordinator):
        calls.append((output_dir, requested_id, expected_status, coordinator))
        return SaveToZoteroResult(
            SaveToZoteroOutcome.PRECHECK_FAILED,
            preflight,
        )

    monkeypatch.setattr(web_app, "start_save_to_zotero", start)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get(
            "/",
            params={"view": "kept", "paper": str(paper_id)},
        )
        csrf = csrf_from_html(page.text)
        missing = client.post(
            f"/papers/{paper_id}/save-to-zotero",
            data={
                "expected_status": "kept",
                "view": "kept",
            },
        )
        invalid_status = client.post(
            f"/papers/{paper_id}/save-to-zotero",
            data={
                "csrf_token": csrf,
                "expected_status": "candidate-ish",
                "view": "kept",
            },
        )
        valid = client.post(
            f"/papers/{paper_id}/save-to-zotero",
            data={
                **save_form(csrf),
                "doi": "10.9999/browser",
                "output_dir": "/tmp/browser",
                "request_id": "browser-request",
                "outcome": "CONFIRMED",
                "zotero_key": "BROWSER01",
            },
        )

    assert missing.status_code == 403
    assert invalid_status.status_code == 400
    assert calls == [
        (
            authoritative_output,
            paper_id,
            WorkflowStatus.KEPT,
            app.state.capture_coordinator,
        )
    ]
    assert valid.status_code == 200
    assert "Expected precheck failure." in valid.text
    assert "10.9999/browser" not in valid.text
    assert "/tmp/browser" not in valid.text
    assert "browser-request" not in valid.text
    assert "BROWSER01" not in valid.text


def test_capture_invalid_doi_result_fails_closed_without_polling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = WorkspacePaper(
        paper_id=PAPER_ONE,
        title="Kept Paper",
        journal="Biometrics",
        publication_date=date(2026, 10, 1),
        discovered_at=NOW,
        status=WorkflowStatus.KEPT,
        abstract="Abstract",
        author_keywords=(),
        authors=(WorkspaceAuthor(name="Ada Author", note_stem="ada-author"),),
        external_ids=ExternalIds(doi="10.5555/server-doi"),
        sources=(),
        zotero_key=None,
    )
    snapshot = WorkspaceSnapshot((paper,), ())
    monkeypatch.setattr(
        web_app,
        "load_config",
        lambda path: SimpleNamespace(output_dir=tmp_path, journals=()),
    )
    monkeypatch.setattr(
        web_app,
        "load_workspace",
        lambda output, *, journals: snapshot,
    )
    preflight = DecisionResult(
        outcome=DecisionOutcome.ZOTERO_NOT_FOUND,
        paper_id=PAPER_ONE,
        expected_status=WorkflowStatus.KEPT,
        current_status=WorkflowStatus.KEPT,
        resulting_status=None,
        path=None,
        message="No exact DOI match exists in Zotero My Library.",
        reconciled_doi="10.5555/server-doi",
    )
    monkeypatch.setattr(
        web_app,
        "start_save_to_zotero",
        lambda *args: SaveToZoteroResult(
            SaveToZoteroOutcome.CAPTURE_INVALID_DOI,
            preflight,
        ),
    )
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get(
            "/",
            params={"view": "kept", "paper": str(PAPER_ONE)},
        )
        response = client.post(
            f"/papers/{PAPER_ONE}/save-to-zotero",
            data=save_form(csrf_from_html(page.text)),
        )

    assert "authoritative DOI was invalid" in response.text
    assert 'hx-post="/capture/poll"' not in response.text


def test_unique_parent_reconciles_even_when_connector_is_unavailable_and_steps_neighbor(
    tmp_path: Path,
    zotero_server,
) -> None:
    enqueue, requests = zotero_server
    config_path, output_dir, first, _ = seed_two_kept(tmp_path)
    enqueue(
        library_page(
            [
                zotero_item(
                    "PARENT01",
                    "https://doi.org/10.5555/WEB-CAPTURE-1",
                )
            ]
        )
    )
    app = create_app(config_path)

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get(
            "/",
            params={"view": "kept", "paper": str(PAPER_ONE)},
        )
        response = client.post(
            f"/papers/{PAPER_ONE}/save-to-zotero",
            data=save_form(csrf_from_html(page.text)),
        )

    assert response.status_code == 200
    assert selected_id(response.text) == str(PAPER_TWO)
    assert 'data-selection-stepped="true"' in response.text
    assert 'hx-post="/capture/poll"' not in response.text
    assert app.state.capture_coordinator.snapshot().attempt is None
    state = paper_state(first, output_dir)
    assert state.status is WorkflowStatus.IN_ZOTERO
    assert state.zotero_key == "PARENT01"
    assert len(requests) == 1


def test_connector_unavailable_after_not_found_keeps_fallbacks_and_does_not_poll(
    tmp_path: Path,
    zotero_server,
) -> None:
    enqueue, requests = zotero_server
    config_path, output_dir, first, _ = seed_two_kept(tmp_path)
    enqueue(library_page())
    app = create_app(config_path)

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get(
            "/",
            params={"view": "kept", "paper": str(PAPER_ONE)},
        )
        response = client.post(
            f"/papers/{PAPER_ONE}/save-to-zotero",
            data=save_form(csrf_from_html(page.text)),
        )

    assert "Connector is not connected" in response.text
    assert "Open DOI" in response.text
    assert "Check Zotero" in response.text
    assert "Save to Zotero" in response.text
    assert 'hx-post="/capture/poll"' not in response.text
    assert app.state.capture_coordinator.snapshot().attempt is None
    assert paper_state(first, output_dir).status is WorkflowStatus.KEPT
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("response", "message"),
    (
        (
            library_page(
                [
                    zotero_item("PARENT01", "10.5555/web-capture-1"),
                    zotero_item("PARENT02", "10.5555/web-capture-1"),
                ]
            ),
            "Multiple exact DOI matches",
        ),
        (
            httpx.ConnectError("unavailable"),
            "Cannot verify complete Zotero My Library",
        ),
    ),
)
def test_precheck_duplicate_or_api_failure_warns_without_polling(
    tmp_path: Path,
    zotero_server,
    response: httpx.Response | Exception,
    message: str,
) -> None:
    enqueue, _ = zotero_server
    config_path, output_dir, first, _ = seed_two_kept(tmp_path)
    enqueue(response)
    app = create_app(config_path)
    connect(app)

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get(
            "/",
            params={"view": "kept", "paper": str(PAPER_ONE)},
        )
        result = client.post(
            f"/papers/{PAPER_ONE}/save-to-zotero",
            data=save_form(csrf_from_html(page.text)),
        )

    assert message in result.text
    assert 'hx-post="/capture/poll"' not in result.text
    assert app.state.capture_coordinator.snapshot().attempt is None
    assert paper_state(first, output_dir).status is WorkflowStatus.KEPT


def test_connected_not_found_starts_polling_and_refresh_reconstructs_same_attempt(
    tmp_path: Path,
    zotero_server,
) -> None:
    enqueue, requests = zotero_server
    config_path, output_dir, first, _ = seed_two_kept(tmp_path)
    enqueue(library_page())
    app = create_app(config_path)
    connect(app)

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get(
            "/",
            params={"view": "kept", "paper": str(PAPER_ONE)},
        )
        csrf = csrf_from_html(page.text)
        started = client.post(
            f"/papers/{PAPER_ONE}/save-to-zotero",
            data=save_form(csrf),
        )
        snapshot = app.state.capture_coordinator.snapshot()
        refreshed = client.get(
            "/",
            params={"view": "kept", "paper": str(PAPER_ONE)},
        )

    assert snapshot.attempt is not None
    request_id = snapshot.attempt.request_id
    assert snapshot.attempt.stage is CaptureStage.WAITING_FOR_CONNECTOR
    for response in (started, refreshed):
        assert "Saving to Zotero…" in response.text
        assert 'hx-post="/capture/poll"' in response.text
        assert 'hx-trigger="load delay:1s"' in response.text
        assert 'hx-sync="this:drop"' in response.text
        fields = capture_poll_form(response.text)
        assert fields["paper"] == str(PAPER_ONE)
        assert fields["position"] == "0"
    assert app.state.capture_coordinator.snapshot().attempt.request_id == request_id
    assert paper_state(first, output_dir).status is WorkflowStatus.KEPT
    assert len(requests) == 1


def test_another_paper_save_still_preflights_before_global_busy_result(
    tmp_path: Path,
    zotero_server,
) -> None:
    enqueue, requests = zotero_server
    config_path, _, _, _ = seed_two_kept(tmp_path)
    enqueue(library_page(), library_page())
    app = create_app(config_path)
    connect(app)

    with TestClient(app, base_url="http://localhost") as client:
        first_page = client.get(
            "/",
            params={"view": "kept", "paper": str(PAPER_ONE)},
        )
        csrf = csrf_from_html(first_page.text)
        first = client.post(
            f"/papers/{PAPER_ONE}/save-to-zotero",
            data=save_form(csrf, position="0"),
        )
        second = client.post(
            f"/papers/{PAPER_TWO}/save-to-zotero",
            data=save_form(csrf, position="1"),
        )

    assert "Saving to Zotero…" in first.text
    assert "Another automatic Zotero save is in progress" in second.text
    assert "An automatic Zotero save is in progress." in second.text
    assert len(requests) == 2
    snapshot = app.state.capture_coordinator.snapshot()
    assert snapshot.attempt is not None
    assert snapshot.attempt.paper_id == PAPER_ONE


def test_save_does_not_replace_pending_completion_and_poll_can_finish_it(
    tmp_path: Path,
    zotero_server,
) -> None:
    enqueue, requests = zotero_server
    config_path, output_dir, first, _ = seed_two_kept(tmp_path)
    enqueue(library_page(), library_page())
    app = create_app(config_path)
    connect(app)

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get(
            "/",
            params={"view": "kept", "paper": str(PAPER_ONE)},
        )
        csrf = csrf_from_html(page.text)
        client.post(
            f"/papers/{PAPER_ONE}/save-to-zotero",
            data=save_form(csrf, position="0"),
        )
        command = app.state.capture_coordinator.claim_command()
        assert command is not None
        assert app.state.capture_coordinator.submit_result(
            command.request_id,
            CaptureOutcome.CONFIRMED,
        )
        pending = client.post(
            f"/papers/{PAPER_TWO}/save-to-zotero",
            data=save_form(csrf, position="1"),
        )
        poll_values = capture_poll_form(pending.text)
        enqueue(
            library_page(
                [zotero_item("PARENT01", "10.5555/web-capture-1")]
            )
        )
        finished = client.post("/capture/poll", data=poll_values)

    assert "previous automatic Zotero save is ready to finish" in pending.text
    assert "Finishing Zotero reconciliation…" in pending.text
    assert finished.headers["HX-Retarget"] == "#workspace-root"
    assert paper_state(first, output_dir).status is WorkflowStatus.IN_ZOTERO
    assert len(requests) == 3


def test_waiting_and_active_poll_only_observe_coordinator_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zotero_server,
) -> None:
    enqueue, requests = zotero_server
    config_path, _, _, _ = seed_two_kept(tmp_path)
    enqueue(library_page())
    app = create_app(config_path)
    connect(app)
    monkeypatch.setattr(
        web_app,
        "process_capture_completion",
        lambda coordinator: pytest.fail(
            "Active polling must not process completion"
        ),
    )

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get(
            "/",
            params={"view": "kept", "paper": str(PAPER_ONE)},
        )
        started = client.post(
            f"/papers/{PAPER_ONE}/save-to-zotero",
            data=save_form(csrf_from_html(page.text)),
        )
        values = capture_poll_form(started.text)
        waiting = client.post("/capture/poll", data=values)
        command = app.state.capture_coordinator.claim_command()
        assert command is not None
        active = client.post("/capture/poll", data=values)

    assert waiting.status_code == active.status_code == 200
    assert "Saving to Zotero…" in waiting.text
    assert "Saving to Zotero…" in active.text
    assert app.state.capture_coordinator.snapshot().attempt.stage is (
        CaptureStage.CONNECTOR_ACTIVE
    )
    assert len(requests) == 1


@pytest.mark.parametrize(
    "terminal_outcome",
    (CaptureOutcome.CONFIRMED, CaptureOutcome.UNCONFIRMED),
)
def test_terminal_completion_poll_reconciles_once_and_steps_neighbor(
    tmp_path: Path,
    zotero_server,
    terminal_outcome: CaptureOutcome,
) -> None:
    enqueue, requests = zotero_server
    config_path, output_dir, first, _ = seed_two_kept(tmp_path)
    enqueue(library_page())
    app = create_app(config_path)
    connect(app)

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get(
            "/",
            params={"view": "kept", "paper": str(PAPER_ONE)},
        )
        started = client.post(
            f"/papers/{PAPER_ONE}/save-to-zotero",
            data=save_form(csrf_from_html(page.text)),
        )
        values = capture_poll_form(started.text)
        command = app.state.capture_coordinator.claim_command()
        assert command is not None
        assert app.state.capture_coordinator.submit_result(
            command.request_id,
            terminal_outcome,
        )
        enqueue(
            library_page(
                [zotero_item("PARENT01", "10.5555/web-capture-1")]
            )
        )
        finished = client.post("/capture/poll", data=values)
        duplicate = client.post("/capture/poll", data=values)

    assert finished.headers["HX-Retarget"] == "#workspace-root"
    assert finished.headers["HX-Reswap"] == "outerHTML"
    assert selected_id(finished.text) == str(PAPER_TWO)
    assert 'data-selection-stepped="true"' in finished.text
    assert paper_state(first, output_dir).status is WorkflowStatus.IN_ZOTERO
    assert duplicate.status_code == 200
    assert "capture-status" in duplicate.text
    assert len(requests) == 2


def test_claimed_timeout_unconfirmed_uses_same_poll_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zotero_server,
) -> None:
    enqueue, requests = zotero_server
    clock = [100.0]
    monkeypatch.setattr(capture_module, "_monotonic", lambda: clock[0])
    config_path, output_dir, first, _ = seed_two_kept(tmp_path)
    enqueue(library_page())
    app = create_app(config_path)
    connect(app)

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get(
            "/",
            params={"view": "kept", "paper": str(PAPER_ONE)},
        )
        started = client.post(
            f"/papers/{PAPER_ONE}/save-to-zotero",
            data=save_form(csrf_from_html(page.text)),
        )
        values = capture_poll_form(started.text)
        command = app.state.capture_coordinator.claim_command()
        assert command is not None
        clock[0] += ACTIVE_TIMEOUT_SECONDS
        enqueue(
            library_page(
                [zotero_item("PARENT01", "10.5555/web-capture-1")]
            )
        )
        finished = client.post("/capture/poll", data=values)

    assert finished.headers["HX-Retarget"] == "#workspace-root"
    assert paper_state(first, output_dir).status is WorkflowStatus.IN_ZOTERO
    assert len(requests) == 2


def test_failed_capture_stops_polling_without_reconciliation_or_retry(
    tmp_path: Path,
    zotero_server,
) -> None:
    enqueue, requests = zotero_server
    config_path, output_dir, first, _ = seed_two_kept(tmp_path)
    enqueue(library_page())
    app = create_app(config_path)
    connect(app)

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get(
            "/",
            params={"view": "kept", "paper": str(PAPER_ONE)},
        )
        started = client.post(
            f"/papers/{PAPER_ONE}/save-to-zotero",
            data=save_form(csrf_from_html(page.text)),
        )
        values = capture_poll_form(started.text)
        command = app.state.capture_coordinator.claim_command()
        assert command is not None
        assert app.state.capture_coordinator.submit_result(
            command.request_id,
            CaptureOutcome.FAILED,
        )
        failed = client.post("/capture/poll", data=values)
        repeated = client.post("/capture/poll", data=values)

    assert "Automatic Zotero save failed" in failed.text
    assert 'hx-trigger="load delay:1s"' not in failed.text
    assert "Automatic Zotero save failed" in repeated.text
    assert paper_state(first, output_dir).status is WorkflowStatus.KEPT
    assert len(requests) == 1


def test_unclaimed_timeout_stops_polling_without_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zotero_server,
) -> None:
    enqueue, requests = zotero_server
    clock = [100.0]
    monkeypatch.setattr(capture_module, "_monotonic", lambda: clock[0])
    config_path, output_dir, first, _ = seed_two_kept(tmp_path)
    enqueue(library_page())
    app = create_app(config_path)
    connect(app)

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get(
            "/",
            params={"view": "kept", "paper": str(PAPER_ONE)},
        )
        started = client.post(
            f"/papers/{PAPER_ONE}/save-to-zotero",
            data=save_form(csrf_from_html(page.text)),
        )
        values = capture_poll_form(started.text)
        clock[0] += WAITING_TIMEOUT_SECONDS
        expired = client.post("/capture/poll", data=values)

    assert "did not claim the automatic save" in expired.text
    assert 'hx-trigger="load delay:1s"' not in expired.text
    assert paper_state(first, output_dir).status is WorkflowStatus.KEPT
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("completion_response", "message"),
    (
        (library_page(), "No exact DOI match exists"),
        (
            library_page(
                [
                    zotero_item("PARENT01", "10.5555/web-capture-1"),
                    zotero_item("PARENT02", "10.5555/web-capture-1"),
                ]
            ),
            "Multiple exact DOI matches",
        ),
        (
            httpx.ConnectError("completion unavailable"),
            "Cannot verify complete Zotero My Library",
        ),
    ),
)
def test_completion_reconciliation_failure_stops_without_retry(
    tmp_path: Path,
    zotero_server,
    completion_response: httpx.Response | Exception,
    message: str,
) -> None:
    enqueue, requests = zotero_server
    config_path, output_dir, first, _ = seed_two_kept(tmp_path)
    enqueue(library_page())
    app = create_app(config_path)
    connect(app)

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get(
            "/",
            params={"view": "kept", "paper": str(PAPER_ONE)},
        )
        started = client.post(
            f"/papers/{PAPER_ONE}/save-to-zotero",
            data=save_form(csrf_from_html(page.text)),
        )
        values = capture_poll_form(started.text)
        command = app.state.capture_coordinator.claim_command()
        assert command is not None
        assert app.state.capture_coordinator.submit_result(
            command.request_id,
            CaptureOutcome.CONFIRMED,
        )
        enqueue(completion_response)
        failed = client.post("/capture/poll", data=values)
        repeated = client.post("/capture/poll", data=values)

    assert failed.headers["HX-Retarget"] == "#workspace-root"
    assert message in failed.text
    assert "Open DOI" in failed.text
    assert "Check Zotero" in failed.text
    assert 'hx-post="/capture/poll"' not in failed.text
    assert repeated.status_code == 200
    assert paper_state(first, output_dir).status is WorkflowStatus.KEPT
    assert len(requests) == 2


def test_poll_requires_csrf_and_get_cannot_process_pending_completion(
    tmp_path: Path,
    zotero_server,
) -> None:
    enqueue, requests = zotero_server
    config_path, output_dir, first, _ = seed_two_kept(tmp_path)
    enqueue(library_page())
    app = create_app(config_path)
    connect(app)

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get(
            "/",
            params={"view": "kept", "paper": str(PAPER_ONE)},
        )
        started = client.post(
            f"/papers/{PAPER_ONE}/save-to-zotero",
            data=save_form(csrf_from_html(page.text)),
        )
        values = capture_poll_form(started.text)
        command = app.state.capture_coordinator.claim_command()
        assert command is not None
        assert app.state.capture_coordinator.submit_result(
            command.request_id,
            CaptureOutcome.CONFIRMED,
        )
        get_result = client.get("/capture/poll")
        invalid = client.post(
            "/capture/poll",
            data={**values, "csrf_token": "invalid"},
        )

    assert get_result.status_code == 405
    assert invalid.status_code == 403
    assert app.state.capture_coordinator.snapshot().completion_pending
    assert paper_state(first, output_dir).status is WorkflowStatus.KEPT
    assert len(requests) == 1


class _LocalZoteroStatus:
    def __init__(self, server_id: str | None) -> None:
        self.server_id = server_id
        self.message = (
            "Zotero Local API is reachable."
            if server_id
            else "Zotero Local API unavailable."
        )


class _FakeLocalZotero:
    def __init__(self, server_id: str | None = "local-instance") -> None:
        self.status = _LocalZoteroStatus(server_id)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def current_instance(self):
        return self.status


def test_settings_connector_readiness_is_process_state_not_local_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        web_app,
        "ZoteroLocalClient",
        lambda: _FakeLocalZotero("reachable-local-api"),
    )
    app = create_app(tmp_path / "missing.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        unavailable = client.get("/settings/zotero")
        assert "Zotero Local API is reachable" in unavailable.text
        assert "Literature Monitor Connector: <strong>unavailable</strong>" in (
            unavailable.text
        )

        app.state.capture_coordinator.heartbeat(
            connector_version="9.9.9",
            zotero_reachable=True,
        )
        connected = client.get("/settings/zotero")

        app.state.capture_coordinator.heartbeat(
            connector_version="9.9.9",
            zotero_reachable=False,
        )
        unreachable = client.get("/settings/zotero")

    assert "Literature Monitor Connector: <strong>connected</strong>" in connected.text
    assert "Literature Monitor Connector: <strong>unavailable</strong>" in (
        unreachable.text
    )
    combined = unavailable.text + connected.text + unreachable.text
    for forbidden in (
        "9.9.9",
        "request_id",
        "paper_id",
        "normalized_doi",
        "output_dir",
        "filesystem",
        "heartbeat history",
        "attempt history",
    ):
        assert forbidden not in combined


def test_settings_stale_heartbeat_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    monkeypatch.setattr(capture_module, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(
        web_app,
        "ZoteroLocalClient",
        lambda: _FakeLocalZotero("reachable-local-api"),
    )
    app = create_app(tmp_path / "missing.yaml")
    app.state.capture_coordinator.heartbeat(
        connector_version="1.0.0",
        zotero_reachable=True,
    )
    clock[0] += HEARTBEAT_STALE_SECONDS + 0.001

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/settings")
        status = client.get("/settings/zotero")

    assert "Literature Monitor Connector: <strong>unavailable</strong>" in page.text
    assert "Literature Monitor Connector: <strong>unavailable</strong>" in status.text


def test_capture_routes_are_post_only_and_bridge_privacy_shape_is_unchanged(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "missing.yaml")
    route_methods = {
        (route.path, method)
        for route in app.routes
        if hasattr(route, "methods")
        for method in route.methods
    }

    assert ("/capture/poll", "POST") in route_methods
    assert ("/capture/poll", "GET") not in route_methods
    assert ("/papers/{paper_id}/save-to-zotero", "POST") in route_methods
    assert ("/papers/{paper_id}/save-to-zotero", "GET") not in route_methods

    with TestClient(app, base_url="http://localhost") as client:
        state = client.get("/api/connector/state")

    assert state.status_code == 200
    assert state.json()["capture"] is None
    for forbidden in (
        "paper_id",
        "normalized_doi",
        "output_dir",
        "paper_path",
        "filesystem",
        "capture_guard",
    ):
        assert forbidden not in state.text
