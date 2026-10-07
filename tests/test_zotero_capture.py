from __future__ import annotations

import os
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import yaml

import literature_monitor.application.decisions as decisions
import literature_monitor.web.capture_coordinator as capture_module
import literature_monitor.web.zotero_capture as zotero_capture
from literature_monitor.application.decisions import (
    DecisionOutcome,
    DecisionResult,
    ReconciliationGuard,
)
from literature_monitor.materialize import render_paper_markdown
from literature_monitor.models import (
    Author,
    CanonicalMetadata,
    CanonicalPaper,
    ExternalIds,
    MetadataSource,
    Workflow,
    WorkflowStatus,
)
from literature_monitor.web.capture_coordinator import (
    ACTIVE_TIMEOUT_SECONDS,
    CaptureCoordinator,
    CaptureOutcome,
    CaptureStage,
    CaptureStartOutcome,
    CaptureStartResult,
)
from literature_monitor.web.zotero_capture import (
    CompletionProcessOutcome,
    SaveToZoteroOutcome,
    process_capture_completion,
    start_save_to_zotero,
)
from literature_monitor.zotero_local import LOCAL_API_BASE, ZoteroLocalClient


NOW = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)
PAPER_ID = UUID("41414141-4141-4141-8141-414141414141")
DEFAULT_DOI = "10.5555/capture"


def zotero_item(
    key: str = "PARENT01",
    doi: str = DEFAULT_DOI,
    *,
    item_type: str = "journalArticle",
) -> dict[str, object]:
    return {
        "key": key,
        "data": {
            "key": key,
            "itemType": item_type,
            "DOI": doi,
        },
    }


def library_page(
    items: tuple[dict[str, object], ...] | list[dict[str, object]] = (),
    *,
    total: int | None = None,
) -> httpx.Response:
    return httpx.Response(
        200,
        json=list(items),
        headers={
            "Zotero-Server-ID": "local-instance",
            "Last-Modified-Version": "7",
            "Total-Results": str(len(items) if total is None else total),
        },
    )


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
        assert request.url.params["itemType"] == "-attachment"
        assert pending, "Unexpected Zotero request"
        response = pending.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def factory() -> ZoteroLocalClient:
        client = ZoteroLocalClient(transport=httpx.MockTransport(respond))
        clients.append(client)
        return client

    monkeypatch.setattr(decisions, "_ZoteroLocalClient", factory)
    yield enqueue, requests
    assert all(client._http.is_closed for client in clients)


def write_paper(
    output_dir: Path,
    *,
    paper_id: UUID = PAPER_ID,
    status: WorkflowStatus = WorkflowStatus.KEPT,
    doi: str = DEFAULT_DOI,
) -> Path:
    paper = CanonicalPaper(
        id=paper_id,
        metadata=CanonicalMetadata(
            title="Capture Paper",
            journal="Biometrics",
            publication_date=date(2026, 10, 1),
            abstract="Capture orchestration test.",
            author_keywords=("capture",),
        ),
        external_ids=ExternalIds(doi=doi),
        authors=(Author(name="Ada Author"),),
        sources=(
            MetadataSource(
                provider="openalex",
                record_id="https://openalex.org/W-CAPTURE",
                retrieved_at=NOW,
            ),
        ),
        workflow=Workflow(status=status, discovered_at=NOW),
    )
    path = output_dir / "Papers" / "capture-paper.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_paper_markdown(paper, ("ada-author",)),
        encoding="utf-8",
    )
    return path


def document_parts(path: Path) -> tuple[dict[str, object], str]:
    opening, payload, body = path.read_text(encoding="utf-8").split("---", maxsplit=2)
    assert opening == ""
    values = yaml.safe_load(payload)
    assert isinstance(values, dict)
    return values, body


def replace_frontmatter(path: Path, **updates: object) -> None:
    values, body = document_parts(path)
    values.update(updates)
    rendered = yaml.safe_dump(values, sort_keys=False, allow_unicode=True).rstrip()
    path.write_text(f"---\n{rendered}\n---{body}", encoding="utf-8")


def connect(coordinator: CaptureCoordinator) -> None:
    coordinator.heartbeat(
        connector_version="1.0.0",
        zotero_reachable=True,
    )


def claim(coordinator: CaptureCoordinator):
    command = coordinator.claim_command()
    assert command is not None
    return command


def fake_guard(
    *,
    output_dir: Path = Path("/workspace"),
    paper_id: UUID = PAPER_ID,
    doi: str = DEFAULT_DOI,
) -> ReconciliationGuard:
    absolute_output = Path.cwd() / output_dir if not output_dir.is_absolute() else output_dir
    absolute_output = absolute_output.absolute()
    return ReconciliationGuard(
        output_dir=absolute_output,
        workspace_identity=(1, 10),
        papers_directory_identity=(1, 11),
        paper_path=absolute_output / "Papers" / "capture-paper.md",
        paper_file_identity=(1, 12),
        paper_id=paper_id,
        normalized_doi=doi,
    )


def fake_preflight(
    outcome: DecisionOutcome,
    *,
    doi: str | None = None,
    status: WorkflowStatus = WorkflowStatus.KEPT,
    output_dir: Path = Path("/workspace"),
) -> DecisionResult:
    return DecisionResult(
        outcome=outcome,
        paper_id=PAPER_ID,
        expected_status=status,
        current_status=status,
        resulting_status=(
            WorkflowStatus.IN_ZOTERO
            if outcome is DecisionOutcome.UPDATED
            else None
        ),
        path=None,
        message="test preflight",
        reconciled_doi=doi,
        capture_guard=(
            fake_guard(output_dir=output_dir, doi=doi)
            if outcome is DecisionOutcome.ZOTERO_NOT_FOUND and doi is not None
            else None
        ),
    )


def complete_capture(
    output_dir: Path,
    coordinator: CaptureCoordinator,
    enqueue,
    *,
    outcome: CaptureOutcome = CaptureOutcome.CONFIRMED,
) -> None:
    enqueue(library_page())
    started = start_save_to_zotero(
        output_dir,
        PAPER_ID,
        WorkflowStatus.KEPT,
        coordinator,
    )
    assert started.outcome is SaveToZoteroOutcome.CAPTURE_STARTED
    command = claim(coordinator)
    assert coordinator.submit_result(command.request_id, outcome)


def test_unique_exact_doi_preflight_reconciles_without_connector_readiness(
    tmp_path: Path,
    zotero_server,
) -> None:
    enqueue, requests = zotero_server
    path = write_paper(tmp_path)
    enqueue(library_page([zotero_item()]))
    coordinator = CaptureCoordinator()

    result = start_save_to_zotero(
        tmp_path,
        PAPER_ID,
        WorkflowStatus.KEPT,
        coordinator,
    )

    assert result.outcome is SaveToZoteroOutcome.RECONCILED
    assert result.preflight.outcome is DecisionOutcome.UPDATED
    assert result.preflight.reconciled_doi == DEFAULT_DOI
    assert result.capture_start is None
    assert coordinator.snapshot().attempt is None
    values, _ = document_parts(path)
    assert values["status"] == "in_zotero"
    assert values["zotero_key"] == "PARENT01"
    assert len(requests) == 1


def test_not_found_starts_capture_with_exact_preflight_doi_and_keeps_paper(
    tmp_path: Path,
    zotero_server,
) -> None:
    enqueue, _ = zotero_server
    path = write_paper(tmp_path, doi="10.5555/a?b#c(d)")
    enqueue(library_page())
    coordinator = CaptureCoordinator()
    connect(coordinator)

    result = start_save_to_zotero(
        tmp_path,
        PAPER_ID,
        WorkflowStatus.KEPT,
        coordinator,
    )

    assert result.outcome is SaveToZoteroOutcome.CAPTURE_STARTED
    assert result.preflight.outcome is DecisionOutcome.ZOTERO_NOT_FOUND
    assert result.preflight.reconciled_doi == "10.5555/a?b#c(d)"
    assert result.capture_start is not None
    assert result.capture_start.outcome is CaptureStartOutcome.STARTED
    snapshot = coordinator.snapshot()
    assert snapshot.attempt is not None
    assert snapshot.attempt.normalized_doi == result.preflight.reconciled_doi
    assert snapshot.attempt.stage is CaptureStage.WAITING_FOR_CONNECTOR
    command = claim(coordinator)
    assert command.request_id == snapshot.attempt.request_id
    assert command.doi_url == "https://doi.org/10.5555/a%3Fb%23c%28d%29"
    values, _ = document_parts(path)
    assert values["status"] == "kept"
    assert values["zotero_key"] is None


@pytest.mark.parametrize(
    "preflight_outcome",
    (
        DecisionOutcome.ZOTERO_DUPLICATE,
        DecisionOutcome.ZOTERO_FAILURE,
        DecisionOutcome.INVALID_PAPER,
        DecisionOutcome.STATE_CONFLICT,
        DecisionOutcome.INVALID_TRANSITION,
        DecisionOutcome.IO_FAILURE,
        DecisionOutcome.NOT_FOUND,
    ),
)
def test_non_not_found_preflight_never_starts_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preflight_outcome: DecisionOutcome,
) -> None:
    preflight = fake_preflight(preflight_outcome)
    monkeypatch.setattr(
        zotero_capture,
        "reconcile_paper_with_zotero",
        lambda *args, **kwargs: preflight,
    )

    class ForbiddenCoordinator:
        def start_capture(self, *args, **kwargs):
            pytest.fail("Capture must start only after explicit ZOTERO_NOT_FOUND")

    result = start_save_to_zotero(
        tmp_path,
        PAPER_ID,
        WorkflowStatus.KEPT,
        ForbiddenCoordinator(),  # type: ignore[arg-type]
    )

    assert result.outcome is SaveToZoteroOutcome.PRECHECK_FAILED
    assert result.preflight is preflight
    assert result.capture_start is None


@pytest.mark.parametrize(
    ("capture_outcome", "expected"),
    (
        (CaptureStartOutcome.STARTED, SaveToZoteroOutcome.CAPTURE_STARTED),
        (
            CaptureStartOutcome.CONNECTOR_UNAVAILABLE,
            SaveToZoteroOutcome.CONNECTOR_UNAVAILABLE,
        ),
        (CaptureStartOutcome.ALREADY_ACTIVE, SaveToZoteroOutcome.CAPTURE_BUSY),
        (
            CaptureStartOutcome.COMPLETION_PENDING,
            SaveToZoteroOutcome.COMPLETION_PENDING,
        ),
        (
            CaptureStartOutcome.INVALID_DOI,
            SaveToZoteroOutcome.CAPTURE_INVALID_DOI,
        ),
    ),
)
def test_capture_start_outcome_is_preserved_and_mapped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_outcome: CaptureStartOutcome,
    expected: SaveToZoteroOutcome,
) -> None:
    preflight = fake_preflight(
        DecisionOutcome.ZOTERO_NOT_FOUND,
        doi=DEFAULT_DOI,
    )
    monkeypatch.setattr(
        zotero_capture,
        "reconcile_paper_with_zotero",
        lambda *args, **kwargs: preflight,
    )
    captured: list[ReconciliationGuard] = []

    class StubCoordinator:
        def start_capture(
            self,
            capture_guard: ReconciliationGuard,
        ) -> CaptureStartResult:
            captured.append(capture_guard)
            return CaptureStartResult(
                capture_outcome,
                "request-id" if capture_outcome is CaptureStartOutcome.STARTED else None,
            )

    result = start_save_to_zotero(
        tmp_path,
        PAPER_ID,
        WorkflowStatus.KEPT,
        StubCoordinator(),  # type: ignore[arg-type]
    )

    assert result.outcome is expected
    assert result.preflight is preflight
    assert result.capture_start is not None
    assert result.capture_start.outcome is capture_outcome
    assert len(captured) == 1
    assert captured[0].paper_id == PAPER_ID
    assert captured[0].normalized_doi == DEFAULT_DOI


def test_connector_unavailable_is_checked_only_after_not_found_preflight(
    tmp_path: Path,
    zotero_server,
) -> None:
    enqueue, requests = zotero_server
    path = write_paper(tmp_path)
    enqueue(library_page())
    coordinator = CaptureCoordinator()

    result = start_save_to_zotero(
        tmp_path,
        PAPER_ID,
        WorkflowStatus.KEPT,
        coordinator,
    )

    assert result.outcome is SaveToZoteroOutcome.CONNECTOR_UNAVAILABLE
    assert result.preflight.outcome is DecisionOutcome.ZOTERO_NOT_FOUND
    assert result.capture_start is not None
    assert result.capture_start.outcome is CaptureStartOutcome.CONNECTOR_UNAVAILABLE
    assert coordinator.snapshot().attempt is None
    assert document_parts(path)[0]["status"] == "kept"
    assert len(requests) == 1


@pytest.mark.parametrize(
    "status",
    (
        WorkflowStatus.CANDIDATE,
        WorkflowStatus.REJECTED,
        WorkflowStatus.IN_ZOTERO,
    ),
)
def test_non_kept_paper_cannot_start_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: WorkflowStatus,
) -> None:
    path = write_paper(tmp_path, status=status)

    def forbidden() -> object:
        pytest.fail("Ineligible Paper must fail before contacting Zotero")

    monkeypatch.setattr(decisions, "_ZoteroLocalClient", forbidden)
    coordinator = CaptureCoordinator()
    connect(coordinator)

    result = start_save_to_zotero(
        tmp_path,
        PAPER_ID,
        status,
        coordinator,
    )

    assert result.outcome is SaveToZoteroOutcome.PRECHECK_FAILED
    assert result.preflight.outcome in {
        DecisionOutcome.INVALID_TRANSITION,
        DecisionOutcome.STATE_CONFLICT,
    }
    assert coordinator.snapshot().attempt is None
    assert document_parts(path)[0]["status"] == status.value


@pytest.mark.parametrize("doi", (None, "", "   ", 123))
def test_invalid_or_missing_doi_cannot_start_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    doi: object,
) -> None:
    path = write_paper(tmp_path)
    values, _ = document_parts(path)
    replace_frontmatter(
        path,
        doi=doi,
        external_ids={**values["external_ids"], "doi": doi},
    )

    def forbidden() -> object:
        pytest.fail("Invalid DOI must fail before contacting Zotero")

    monkeypatch.setattr(decisions, "_ZoteroLocalClient", forbidden)
    coordinator = CaptureCoordinator()
    connect(coordinator)

    result = start_save_to_zotero(
        tmp_path,
        PAPER_ID,
        WorkflowStatus.KEPT,
        coordinator,
    )

    assert result.outcome is SaveToZoteroOutcome.PRECHECK_FAILED
    assert result.preflight.outcome is DecisionOutcome.INVALID_PAPER
    assert coordinator.snapshot().attempt is None


@pytest.mark.parametrize(
    ("response", "expected"),
    (
        (
            library_page([zotero_item(), zotero_item("PARENT02")]),
            DecisionOutcome.ZOTERO_DUPLICATE,
        ),
        (
            httpx.ConnectError("unavailable"),
            DecisionOutcome.ZOTERO_FAILURE,
        ),
    ),
)
def test_duplicate_or_zotero_failure_never_starts_capture(
    tmp_path: Path,
    zotero_server,
    response: httpx.Response | Exception,
    expected: DecisionOutcome,
) -> None:
    enqueue, _ = zotero_server
    path = write_paper(tmp_path)
    enqueue(response)
    coordinator = CaptureCoordinator()
    connect(coordinator)

    result = start_save_to_zotero(
        tmp_path,
        PAPER_ID,
        WorkflowStatus.KEPT,
        coordinator,
    )

    assert result.outcome is SaveToZoteroOutcome.PRECHECK_FAILED
    assert result.preflight.outcome is expected
    assert coordinator.snapshot().attempt is None
    assert document_parts(path)[0]["status"] == "kept"


def test_paper_doi_change_during_preflight_fails_closed_without_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zotero_server,
) -> None:
    enqueue, _ = zotero_server
    path = write_paper(tmp_path)
    enqueue(library_page())
    resolve = ZoteroLocalClient.resolve_identity

    def change_doi(client, doi, zotero_key=None):
        result = resolve(client, doi, zotero_key)
        values, _ = document_parts(path)
        replacement = "10.5555/changed"
        replace_frontmatter(
            path,
            doi=replacement,
            external_ids={**values["external_ids"], "doi": replacement},
        )
        return result

    monkeypatch.setattr(ZoteroLocalClient, "resolve_identity", change_doi)
    coordinator = CaptureCoordinator()
    connect(coordinator)

    result = start_save_to_zotero(
        tmp_path,
        PAPER_ID,
        WorkflowStatus.KEPT,
        coordinator,
    )

    assert result.outcome is SaveToZoteroOutcome.PRECHECK_FAILED
    assert result.preflight.outcome is DecisionOutcome.STATE_CONFLICT
    assert coordinator.snapshot().attempt is None
    assert document_parts(path)[0]["status"] == "kept"


@pytest.mark.parametrize(
    ("zotero_key", "valid"),
    (
        ("invalid-key", False),
        ("parent01", False),
        ("PARENT012", False),
        (" PARENT01 ", False),
        (None, True),
        ("PARENT01", True),
    ),
)
def test_paper_key_changed_during_preflight_is_revalidated_before_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zotero_server,
    zotero_key: str | None,
    valid: bool,
) -> None:
    enqueue, requests = zotero_server
    path = write_paper(tmp_path)
    replace_frontmatter(path, human_field="preserve me")
    path.write_text(path.read_text() + "\nHuman note.\n", encoding="utf-8")
    original_identity = (path.stat().st_dev, path.stat().st_ino)
    edited_contents = []
    enqueue(library_page())
    resolve = ZoteroLocalClient.resolve_identity

    def change_key(client, doi, existing_key=None):
        result = resolve(client, doi, existing_key)
        replace_frontmatter(path, zotero_key=zotero_key)
        assert (path.stat().st_dev, path.stat().st_ino) == original_identity
        edited_contents.append(path.read_text(encoding="utf-8"))
        return result

    monkeypatch.setattr(ZoteroLocalClient, "resolve_identity", change_key)
    coordinator = CaptureCoordinator()
    connect(coordinator)

    result = start_save_to_zotero(
        tmp_path,
        PAPER_ID,
        WorkflowStatus.KEPT,
        coordinator,
    )

    if valid:
        assert result.outcome is SaveToZoteroOutcome.CAPTURE_STARTED
        assert result.preflight.outcome is DecisionOutcome.ZOTERO_NOT_FOUND
        assert coordinator.claim_command() is not None
    else:
        assert result.outcome is SaveToZoteroOutcome.PRECHECK_FAILED
        assert result.preflight.outcome is DecisionOutcome.INVALID_PAPER
        assert result.preflight.capture_guard is None
        assert coordinator.snapshot().attempt is None
        assert coordinator.claim_command() is None
    assert len(requests) == 1
    assert path.read_text(encoding="utf-8") == edited_contents[0]
    values, body = document_parts(path)
    assert values["status"] == "kept"
    assert values["zotero_key"] == zotero_key
    assert values["human_field"] == "preserve me"
    assert "Human note." in body


def test_active_capture_and_pending_completion_are_not_overwritten(
    tmp_path: Path,
    zotero_server,
) -> None:
    enqueue, _ = zotero_server
    write_paper(tmp_path)
    coordinator = CaptureCoordinator()
    connect(coordinator)

    enqueue(library_page())
    first = start_save_to_zotero(
        tmp_path,
        PAPER_ID,
        WorkflowStatus.KEPT,
        coordinator,
    )
    assert first.outcome is SaveToZoteroOutcome.CAPTURE_STARTED

    enqueue(library_page())
    busy = start_save_to_zotero(
        tmp_path,
        PAPER_ID,
        WorkflowStatus.KEPT,
        coordinator,
    )
    assert busy.outcome is SaveToZoteroOutcome.CAPTURE_BUSY
    assert busy.capture_start is not None
    assert busy.capture_start.outcome is CaptureStartOutcome.ALREADY_ACTIVE

    command = claim(coordinator)
    assert coordinator.submit_result(command.request_id, CaptureOutcome.CONFIRMED)
    enqueue(library_page())
    pending = start_save_to_zotero(
        tmp_path,
        PAPER_ID,
        WorkflowStatus.KEPT,
        coordinator,
    )
    assert pending.outcome is SaveToZoteroOutcome.COMPLETION_PENDING
    assert pending.capture_start is not None
    assert pending.capture_start.outcome is CaptureStartOutcome.COMPLETION_PENDING


@pytest.mark.parametrize(
    "terminal_outcome",
    (CaptureOutcome.CONFIRMED, CaptureOutcome.UNCONFIRMED),
)
def test_terminal_completion_reconciles_exactly_once(
    tmp_path: Path,
    zotero_server,
    terminal_outcome: CaptureOutcome,
) -> None:
    enqueue, requests = zotero_server
    path = write_paper(tmp_path)
    coordinator = CaptureCoordinator()
    connect(coordinator)
    enqueue(library_page())

    started = start_save_to_zotero(
        tmp_path,
        PAPER_ID,
        WorkflowStatus.KEPT,
        coordinator,
    )
    assert started.outcome is SaveToZoteroOutcome.CAPTURE_STARTED
    command = claim(coordinator)
    assert coordinator.submit_result(command.request_id, terminal_outcome)

    enqueue(library_page([zotero_item()]))
    processed = process_capture_completion(coordinator)
    repeated = process_capture_completion(coordinator)

    assert processed.outcome is CompletionProcessOutcome.RECONCILED
    assert processed.completion is not None
    assert processed.completion.outcome is terminal_outcome
    assert processed.reconciliation is not None
    assert processed.reconciliation.outcome is DecisionOutcome.UPDATED
    assert processed.reconciliation.reconciled_doi == DEFAULT_DOI
    assert repeated.outcome is CompletionProcessOutcome.NO_COMPLETION
    assert repeated.completion is None
    assert repeated.reconciliation is None
    values, _ = document_parts(path)
    assert values["status"] == "in_zotero"
    assert values["zotero_key"] == "PARENT01"
    assert len(requests) == 2


def test_completion_is_bound_to_originating_workspace_with_same_uuid_and_doi(
    tmp_path: Path,
    zotero_server,
) -> None:
    enqueue, requests = zotero_server
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    paper_a = write_paper(workspace_a)
    paper_b = write_paper(workspace_b)
    coordinator = CaptureCoordinator()
    connect(coordinator)
    complete_capture(workspace_a, coordinator, enqueue)

    completion = coordinator.snapshot()
    assert completion.completion_pending
    enqueue(library_page([zotero_item()]))
    processed = process_capture_completion(coordinator)

    assert processed.outcome is CompletionProcessOutcome.RECONCILED
    assert processed.completion is not None
    assert processed.completion.capture_guard.output_dir == workspace_a.absolute()
    values_a, _ = document_parts(paper_a)
    values_b, _ = document_parts(paper_b)
    assert values_a["status"] == "in_zotero"
    assert values_a["zotero_key"] == "PARENT01"
    assert values_b["status"] == "kept"
    assert values_b["zotero_key"] is None
    assert len(requests) == 2


def test_completion_rejects_same_path_atomic_paper_replacement_before_zotero(
    tmp_path: Path,
    zotero_server,
) -> None:
    enqueue, requests = zotero_server
    path = write_paper(tmp_path)
    original_bytes = path.read_bytes()
    original_identity = (path.stat().st_dev, path.stat().st_ino)
    coordinator = CaptureCoordinator()
    connect(coordinator)
    complete_capture(tmp_path, coordinator, enqueue)

    replacement = path.with_name(".replacement.md")
    replacement.write_bytes(original_bytes)
    os.replace(replacement, path)
    replacement_identity = (path.stat().st_dev, path.stat().st_ino)
    assert replacement_identity != original_identity

    processed = process_capture_completion(coordinator)
    repeated = process_capture_completion(coordinator)

    assert processed.outcome is CompletionProcessOutcome.RECONCILIATION_FAILED
    assert processed.reconciliation is not None
    assert processed.reconciliation.outcome is DecisionOutcome.STATE_CONFLICT
    assert repeated.outcome is CompletionProcessOutcome.NO_COMPLETION
    values, _ = document_parts(path)
    assert values["status"] == "kept"
    assert values["zotero_key"] is None
    assert len(requests) == 1
    assert coordinator.claim_command() is None


def test_completion_rejects_replaced_papers_directory_before_zotero(
    tmp_path: Path,
    zotero_server,
) -> None:
    enqueue, requests = zotero_server
    path = write_paper(tmp_path)
    paper_bytes = path.read_bytes()
    papers_dir = tmp_path / "Papers"
    original_directory_identity = (
        papers_dir.stat().st_dev,
        papers_dir.stat().st_ino,
    )
    coordinator = CaptureCoordinator()
    connect(coordinator)
    complete_capture(tmp_path, coordinator, enqueue)

    preserved = tmp_path / "Papers-preserved"
    papers_dir.rename(preserved)
    papers_dir.mkdir()
    replacement_path = papers_dir / path.name
    replacement_path.write_bytes(paper_bytes)
    replacement_directory_identity = (
        papers_dir.stat().st_dev,
        papers_dir.stat().st_ino,
    )
    assert replacement_directory_identity != original_directory_identity

    processed = process_capture_completion(coordinator)

    assert processed.outcome is CompletionProcessOutcome.RECONCILIATION_FAILED
    assert processed.reconciliation is not None
    assert processed.reconciliation.outcome is DecisionOutcome.STATE_CONFLICT
    values, _ = document_parts(replacement_path)
    assert values["status"] == "kept"
    assert values["zotero_key"] is None
    assert len(requests) == 1


def test_completion_rejects_replaced_workspace_even_with_original_papers_identity(
    tmp_path: Path,
    zotero_server,
) -> None:
    enqueue, requests = zotero_server
    workspace = tmp_path / "workspace"
    path = write_paper(workspace)
    original_workspace_identity = (
        workspace.stat().st_dev,
        workspace.stat().st_ino,
    )
    original_papers_identity = (
        path.parent.stat().st_dev,
        path.parent.stat().st_ino,
    )
    original_paper_identity = (
        path.stat().st_dev,
        path.stat().st_ino,
    )
    coordinator = CaptureCoordinator()
    connect(coordinator)
    complete_capture(workspace, coordinator, enqueue)

    preserved_workspace = tmp_path / "workspace-preserved"
    workspace.rename(preserved_workspace)
    workspace.mkdir()
    (preserved_workspace / "Papers").rename(workspace / "Papers")
    replacement_path = workspace / "Papers" / path.name

    assert (
        workspace.stat().st_dev,
        workspace.stat().st_ino,
    ) != original_workspace_identity
    assert (
        replacement_path.parent.stat().st_dev,
        replacement_path.parent.stat().st_ino,
    ) == original_papers_identity
    assert (
        replacement_path.stat().st_dev,
        replacement_path.stat().st_ino,
    ) == original_paper_identity

    processed = process_capture_completion(coordinator)

    assert processed.outcome is CompletionProcessOutcome.RECONCILIATION_FAILED
    assert processed.reconciliation is not None
    assert processed.reconciliation.outcome is DecisionOutcome.STATE_CONFLICT
    values, _ = document_parts(replacement_path)
    assert values["status"] == "kept"
    assert values["zotero_key"] is None
    assert len(requests) == 1


def test_completion_rechecks_workspace_identity_at_final_compare_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zotero_server,
) -> None:
    enqueue, requests = zotero_server
    workspace = tmp_path / "workspace"
    path = write_paper(workspace)
    coordinator = CaptureCoordinator()
    connect(coordinator)
    complete_capture(workspace, coordinator, enqueue)
    resolve = ZoteroLocalClient.resolve_identity
    preserved_workspace = tmp_path / "workspace-preserved"

    def replace_workspace_during_lookup(client, doi, zotero_key=None):
        identity = resolve(client, doi, zotero_key)
        workspace.rename(preserved_workspace)
        workspace.mkdir()
        (preserved_workspace / "Papers").rename(workspace / "Papers")
        return identity

    monkeypatch.setattr(
        ZoteroLocalClient,
        "resolve_identity",
        replace_workspace_during_lookup,
    )
    enqueue(library_page([zotero_item()]))

    processed = process_capture_completion(coordinator)

    assert processed.outcome is CompletionProcessOutcome.RECONCILIATION_FAILED
    assert processed.reconciliation is not None
    assert processed.reconciliation.outcome is DecisionOutcome.STATE_CONFLICT
    current_path = workspace / "Papers" / path.name
    values, _ = document_parts(current_path)
    assert values["status"] == "kept"
    assert values["zotero_key"] is None
    assert len(requests) == 2


def test_completion_allows_in_place_human_note_on_same_paper_identity(
    tmp_path: Path,
    zotero_server,
) -> None:
    enqueue, requests = zotero_server
    path = write_paper(tmp_path)
    coordinator = CaptureCoordinator()
    connect(coordinator)
    complete_capture(tmp_path, coordinator, enqueue)
    original_identity = (path.stat().st_dev, path.stat().st_ino)

    note = "\nHuman note added while Connector was active.\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(note)
    assert (path.stat().st_dev, path.stat().st_ino) == original_identity

    enqueue(library_page([zotero_item()]))
    processed = process_capture_completion(coordinator)

    assert processed.outcome is CompletionProcessOutcome.RECONCILED
    values, _ = document_parts(path)
    assert values["status"] == "in_zotero"
    assert values["zotero_key"] == "PARENT01"
    assert note.strip() in path.read_text(encoding="utf-8")
    assert len(requests) == 2


def test_claimed_timeout_unconfirmed_uses_same_one_shot_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zotero_server,
) -> None:
    enqueue, requests = zotero_server
    write_paper(tmp_path)
    clock = [100.0]
    monkeypatch.setattr(capture_module, "_monotonic", lambda: clock[0])
    coordinator = CaptureCoordinator()
    connect(coordinator)
    enqueue(library_page())

    started = start_save_to_zotero(
        tmp_path,
        PAPER_ID,
        WorkflowStatus.KEPT,
        coordinator,
    )
    assert started.outcome is SaveToZoteroOutcome.CAPTURE_STARTED
    claim(coordinator)
    clock[0] += ACTIVE_TIMEOUT_SECONDS

    enqueue(library_page([zotero_item()]))
    processed = process_capture_completion(coordinator)
    repeated = process_capture_completion(coordinator)

    assert processed.outcome is CompletionProcessOutcome.RECONCILED
    assert processed.completion is not None
    assert processed.completion.outcome is CaptureOutcome.UNCONFIRMED
    assert repeated.outcome is CompletionProcessOutcome.NO_COMPLETION
    assert len(requests) == 2


@pytest.mark.parametrize(
    ("response", "expected"),
    (
        (library_page(), DecisionOutcome.ZOTERO_NOT_FOUND),
        (
            library_page([zotero_item(), zotero_item("PARENT02")]),
            DecisionOutcome.ZOTERO_DUPLICATE,
        ),
        (httpx.ConnectError("unavailable"), DecisionOutcome.ZOTERO_FAILURE),
    ),
)
def test_completion_failure_keeps_paper_and_never_restarts_capture(
    tmp_path: Path,
    zotero_server,
    response: httpx.Response | Exception,
    expected: DecisionOutcome,
) -> None:
    enqueue, requests = zotero_server
    path = write_paper(tmp_path)
    coordinator = CaptureCoordinator()
    connect(coordinator)
    enqueue(library_page())
    started = start_save_to_zotero(
        tmp_path,
        PAPER_ID,
        WorkflowStatus.KEPT,
        coordinator,
    )
    assert started.outcome is SaveToZoteroOutcome.CAPTURE_STARTED
    original_request_id = started.capture_start.request_id  # type: ignore[union-attr]
    command = claim(coordinator)
    assert coordinator.submit_result(command.request_id, CaptureOutcome.CONFIRMED)

    enqueue(response)
    processed = process_capture_completion(coordinator)
    repeated = process_capture_completion(coordinator)

    assert processed.outcome is CompletionProcessOutcome.RECONCILIATION_FAILED
    assert processed.reconciliation is not None
    assert processed.reconciliation.outcome is expected
    assert repeated.outcome is CompletionProcessOutcome.NO_COMPLETION
    snapshot = coordinator.snapshot()
    assert snapshot.attempt is not None
    assert snapshot.attempt.request_id == original_request_id
    assert snapshot.attempt.stage is CaptureStage.FINISHED
    assert coordinator.claim_command() is None
    assert document_parts(path)[0]["status"] == "kept"
    assert len(requests) == 2


@pytest.mark.parametrize("change", ("status", "doi", "unsafe"))
def test_stale_completion_cannot_authorize_changed_paper(
    tmp_path: Path,
    zotero_server,
    change: str,
) -> None:
    enqueue, requests = zotero_server
    path = write_paper(tmp_path)
    coordinator = CaptureCoordinator()
    connect(coordinator)
    enqueue(library_page())
    started = start_save_to_zotero(
        tmp_path,
        PAPER_ID,
        WorkflowStatus.KEPT,
        coordinator,
    )
    assert started.outcome is SaveToZoteroOutcome.CAPTURE_STARTED
    command = claim(coordinator)
    assert coordinator.submit_result(command.request_id, CaptureOutcome.CONFIRMED)

    if change == "status":
        replace_frontmatter(path, status="rejected")
    elif change == "doi":
        values, _ = document_parts(path)
        replacement = "10.5555/new-doi"
        replace_frontmatter(
            path,
            doi=replacement,
            external_ids={**values["external_ids"], "doi": replacement},
        )
    else:
        outside = tmp_path / "outside.md"
        path.rename(outside)
        path.symlink_to(outside)

    processed = process_capture_completion(coordinator)

    assert processed.outcome is CompletionProcessOutcome.RECONCILIATION_FAILED
    assert processed.reconciliation is not None
    assert processed.reconciliation.outcome in {
        DecisionOutcome.STATE_CONFLICT,
        DecisionOutcome.INVALID_PAPER,
        DecisionOutcome.IO_FAILURE,
    }
    assert len(requests) == 1
    if change == "unsafe":
        assert path.is_symlink()
        values, _ = document_parts(tmp_path / "outside.md")
    else:
        values, _ = document_parts(path)
    assert values["status"] != "in_zotero"


def test_failed_capture_has_no_completion_reconciliation(
    tmp_path: Path,
    zotero_server,
) -> None:
    enqueue, requests = zotero_server
    write_paper(tmp_path)
    coordinator = CaptureCoordinator()
    connect(coordinator)
    enqueue(library_page())
    started = start_save_to_zotero(
        tmp_path,
        PAPER_ID,
        WorkflowStatus.KEPT,
        coordinator,
    )
    assert started.outcome is SaveToZoteroOutcome.CAPTURE_STARTED
    command = claim(coordinator)
    assert coordinator.submit_result(command.request_id, CaptureOutcome.FAILED)

    processed = process_capture_completion(coordinator)

    assert processed.outcome is CompletionProcessOutcome.NO_COMPLETION
    assert processed.reconciliation is None
    assert len(requests) == 1


def test_completion_path_never_inspects_attachments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    zotero_server,
) -> None:
    enqueue, _ = zotero_server
    write_paper(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("A3 must not inspect Zotero attachments")

    monkeypatch.setattr(ZoteroLocalClient, "inspect_attachments", forbidden)
    coordinator = CaptureCoordinator()
    connect(coordinator)
    enqueue(library_page())
    start_save_to_zotero(
        tmp_path,
        PAPER_ID,
        WorkflowStatus.KEPT,
        coordinator,
    )
    command = claim(coordinator)
    assert coordinator.submit_result(command.request_id, CaptureOutcome.CONFIRMED)
    enqueue(library_page([zotero_item()]))

    processed = process_capture_completion(coordinator)

    assert processed.outcome is CompletionProcessOutcome.RECONCILED


def test_two_concurrent_save_calls_create_only_one_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = CaptureCoordinator()
    connect(coordinator)
    barrier = threading.Barrier(3)
    preflight_barrier = threading.Barrier(2)
    outcomes: list[SaveToZoteroOutcome] = []
    errors: list[BaseException] = []

    def concurrent_not_found(*args, **kwargs) -> DecisionResult:
        preflight_barrier.wait(timeout=2)
        return fake_preflight(DecisionOutcome.ZOTERO_NOT_FOUND, doi=DEFAULT_DOI)

    monkeypatch.setattr(
        zotero_capture,
        "reconcile_paper_with_zotero",
        concurrent_not_found,
    )

    def worker() -> None:
        try:
            barrier.wait(timeout=2)
            result = start_save_to_zotero(
                tmp_path,
                uuid4(),
                WorkflowStatus.KEPT,
                coordinator,
            )
            outcomes.append(result.outcome)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert errors == []
    assert sorted(outcome.value for outcome in outcomes) == sorted(
        (
            SaveToZoteroOutcome.CAPTURE_STARTED.value,
            SaveToZoteroOutcome.CAPTURE_BUSY.value,
        )
    )
    snapshot = coordinator.snapshot()
    assert snapshot.attempt is not None
    assert snapshot.attempt.stage is CaptureStage.WAITING_FOR_CONNECTOR


def test_completion_is_consumed_once_under_concurrency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = CaptureCoordinator()
    connect(coordinator)
    capture_guard = fake_guard(output_dir=tmp_path)
    started = coordinator.start_capture(capture_guard)
    assert started.outcome is CaptureStartOutcome.STARTED
    command = claim(coordinator)
    assert coordinator.submit_result(command.request_id, CaptureOutcome.CONFIRMED)

    reconciliation_calls: list[
        tuple[Path, UUID, str | None, ReconciliationGuard | None]
    ] = []

    def reconcile(
        output_dir,
        paper_id,
        expected_status,
        *,
        expected_doi=None,
        capture_guard=None,
    ):
        reconciliation_calls.append(
            (output_dir, paper_id, expected_doi, capture_guard)
        )
        return DecisionResult(
            outcome=DecisionOutcome.UPDATED,
            paper_id=paper_id,
            expected_status=expected_status,
            current_status=WorkflowStatus.KEPT,
            resulting_status=WorkflowStatus.IN_ZOTERO,
            path=None,
            message="reconciled",
            reconciled_doi=expected_doi,
        )

    monkeypatch.setattr(zotero_capture, "reconcile_paper_with_zotero", reconcile)
    barrier = threading.Barrier(3)
    outcomes: list[CompletionProcessOutcome] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=2)
            outcomes.append(process_capture_completion(coordinator).outcome)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert errors == []
    assert sorted(outcome.value for outcome in outcomes) == sorted(
        (
            CompletionProcessOutcome.RECONCILED.value,
            CompletionProcessOutcome.NO_COMPLETION.value,
        )
    )
    assert reconciliation_calls == [
        (capture_guard.output_dir, PAPER_ID, DEFAULT_DOI, capture_guard)
    ]
