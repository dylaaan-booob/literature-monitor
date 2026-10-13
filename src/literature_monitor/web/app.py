"""FastAPI adapter for local Workspace review."""

from __future__ import annotations

import hashlib
import hmac
import base64
import binascii
import os
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.datastructures import UploadFile
from starlette.concurrency import run_in_threadpool

from literature_monitor.application.decisions import (
    DecisionOutcome,
    DecisionResult,
    keep_paper,
    reject_paper,
)
from literature_monitor.application.export_attempts import ExportCompletionOutcome
from literature_monitor.application.workspace_reset import ResetPlan, ResetRefused, preview_reset, execute_reset
from literature_monitor.application.settings import (
    SettingsDeletionApproval,
    SettingsDeletionPlan,
    SettingsIssueSource,
    _read_snapshot,
    SettingsIssue,
    SettingsLoadResult,
    SettingsSaveOutcome,
    SettingsSaveResult,
    SettingsValidationOutcome,
    SettingsValidationResult,
    load_settings,
    plan_settings_deletions,
    save_settings,
    validate_settings,
)
from literature_monitor.application.workspace import (
    WorkspacePaper,
    WorkspaceSnapshot,
    load_workspace,
)
from literature_monitor.config import ConfigurationError, LoadedConfig, load_config, parse_settings_list_text, detect_list_schema
from literature_monitor.application.full_markdown_import import (
    MAX_MARKDOWN_BYTES, MarkdownImportCommittedWarning, preview_full_markdown, confirm_full_markdown,
)
from literature_monitor.application.ai_mapping_pack import generate_ai_mapping_pack, MappingPackError
from literature_monitor.application.access_service_migration import (
    AccessSchemaUpgradeCommittedWarning, AccessSchemaUpgradeStateUncertain,
    preview_access_schema_upgrade, confirm_access_schema_upgrade,
)
from literature_monitor.openalex import OpenAlexError
from literature_monitor.safe_write import CompareReadError
from literature_monitor.identifiers import normalize_doi
from literature_monitor.safe_write import ContentChangedError, WorkspaceOperationLock
from literature_monitor.models import WorkflowStatus
from literature_monitor.zotero_local import ZoteroLocalClient
from literature_monitor.web.capture_coordinator import (
    CONNECTOR_VERSION_MAX_LENGTH,
    REQUEST_ID_MAX_LENGTH,
    CaptureCoordinator,
    CaptureFailureStage,
    CaptureOutcome,
    CaptureStage,
    CaptureSnapshot,
    PdfOutcome,
    SaveInvocation,
)
from literature_monitor.web.paper_enrichment import PaperEnrichment, ENRICHMENT_CAPABILITY
from literature_monitor.web.run_coordinator import (
    CoordinatorSnapshot,
    CoordinatorStatus,
    RunCoordinator,
    StartOutcome,
    StartResult,
)
from literature_monitor.web.run_presentation import build_run_presentation
from literature_monitor.web.settings_form import (
    SettingsFormValues,
    settings_draft_from_form,
    settings_form_from_draft,
    settings_form_from_submission,
)
from literature_monitor.web.zotero_capture import process_capture_completion
from literature_monitor.application.batch_import import BatchImportService, BatchPhase
from literature_monitor.web.preparation_coordinator import (
    PreparationCoordinator, PreparationOutcome, PreparationPhase, PreparationSnapshot, VisitEvidence,
    read_saved_preparation_services, saved_institution_login_url,
)
from literature_monitor.application.access import AccessObservation
from literature_monitor.web.batch_presentation import build_batch_presentation

_PACKAGE_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
_STATIC_DIR = _PACKAGE_DIR / "static"
_ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
_VIEW_STATUSES = {
    "inbox": WorkflowStatus.CANDIDATE,
    "kept": WorkflowStatus.KEPT,
    "rejected": WorkflowStatus.REJECTED,
    "exported": WorkflowStatus.EXPORTED,
}

templates = Jinja2Templates(directory=_TEMPLATES_DIR)


@dataclass(frozen=True)
class _ImportTarget:
    config_path: Path
    config_digest: str
    config_identity: tuple[int, int]
    workspace: Path
    workspace_identity: tuple[int, int, int] | None
    papers_identity: tuple[int, int, int] | None
    lock_identity: tuple[int, int, int] | None

    @classmethod
    def read(cls, config_path: Path) -> tuple[_ImportTarget, LoadedConfig]:
        def identity(path: Path) -> tuple[int, int, int] | None:
            try:
                value = path.lstat()
                return value.st_dev, value.st_ino, value.st_mode
            except FileNotFoundError:
                return None

        contents = config_path.read_bytes()
        config_identity = identity(config_path)
        if config_identity is None:
            raise ContentChangedError("Configuration disappeared while reading the Workspace target")
        config = load_config(config_path)
        target = cls(config_path, hashlib.sha256(contents).hexdigest(), config_identity[:2],
                     config.output_dir, identity(config.output_dir),
                     identity(config.output_dir / 'Papers'),
                     identity(config.output_dir / '.literature-monitor-operation.lock'))
        if config_path.read_bytes() != contents or identity(config_path) != config_identity:
            raise ContentChangedError("Configuration changed while reading the Workspace target")
        return target, config

    def form_token(self, nonce: str) -> str:
        # 浏览器只持有签名，不能选择路径；同一 nonce 下不同目标的表单也不通用。
        return hmac.new(nonce.encode('ascii'), repr(self).encode('utf-8'), hashlib.sha256).hexdigest()

    def verify(self, lock: WorkspaceOperationLock) -> None:
        current, _ = self.read(self.config_path)
        if self.lock_identity is None:
            # 首次导入可创建空操作锁；已有锁的替换仍必须拒绝。
            current = replace(current, lock_identity=None)
        if current != self or self.workspace_identity is None or lock.identity != self.workspace_identity[:2]:
            raise ContentChangedError("Workspace changed since this import form was shown; no import started")
        lock.verify()


class _ConnectorHeartbeatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=CONNECTOR_VERSION_MAX_LENGTH)
    zotero_reachable: StrictBool
    capabilities: list[str] = []


class _EnrichmentIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    invocation_id: str
    doi_url: str


class _EnrichmentAuthorization(_EnrichmentIdentity):
    waiting_for_user: StrictBool = False


class _EnrichmentResult(_EnrichmentIdentity):
    items: list[dict] = []
    error: str | None = None


class _EnrichmentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csrf_token: str
    request_id: str | None = None


class _ImportBrowserIdleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=REQUEST_ID_MAX_LENGTH)
    invocation_id: str = Field(min_length=1, max_length=REQUEST_ID_MAX_LENGTH)
    doi_url: str = Field(min_length=1, max_length=8192)


class _PreparationIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_id: str = Field(min_length=16, max_length=128)
    task_id: str = Field(min_length=16, max_length=128)
    generation: StrictInt = Field(ge=1)
    request_id: str = Field(min_length=16, max_length=128)
    service_id: str = Field(pattern=r"^svc_[0-9a-f]{32}$")


class _PreparationHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_id: str = Field(min_length=16, max_length=128)
    capability: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=32)
    consent_capability: str | None = Field(default=None, max_length=128)


class _PreparationClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_id: str = Field(min_length=16, max_length=128)


class _PreparationVisitEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nonce: str = Field(min_length=32, max_length=128)
    visit_action_observed: StrictBool
    source_tab_id: StrictInt = Field(ge=0)
    target_tab_id: StrictInt = Field(ge=0)
    created_target_tab_id: StrictInt | None = Field(default=None, ge=0)
    navigation_observed: StrictBool

class _PreparationVisitAuthorization(_PreparationIdentity):
    source_tab_id: StrictInt = Field(ge=0)


class _PreparationResultBody(_PreparationIdentity):
    outcome: PreparationOutcome
    reason: str = Field(min_length=1, max_length=64)
    evidence: _PreparationVisitEvidence | None = None


class _PreparationStartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csrf_token: str


class _PreparationCancelBody(_PreparationStartBody):
    task_id: str = Field(min_length=16, max_length=128)
    generation: StrictInt = Field(ge=1)


class _PreparationWaitingBody(_PreparationIdentity):
    waiting: StrictBool


class _PreparationStartForm(BaseModel):
    csrf_token: str | None = None


class _PreparationCancelForm(_PreparationStartForm):
    task_id: str = ""
    generation: int = 0


def _preparation_json(snapshot: PreparationSnapshot) -> dict[str, object]:
    return {
        "phase": snapshot.phase.value,
        "task_id": snapshot.task_id,
        "generation": snapshot.generation,
        "services": [{"id": s.id, "name": s.name, "access_url": s.access_url, "access_method": s.access_method}
                     for s in snapshot.services],
        "manual_services": [{"id": s.id, "name": s.name, "access_url": s.access_url, "access_method": s.access_method}
                            for s in snapshot.manual_services],
        "current_service_id": snapshot.current_service_id,
        "results": [{"service_id": r.service_id, "outcome": r.outcome.value, "reason": r.reason}
                    for r in snapshot.results],
        "message": snapshot.message,
        "authorization_pending": snapshot.authorization_pending,
        "waiting_for_user": snapshot.waiting_for_user,
    }


def _preparation_context(request: Request, config_path: Path) -> dict[str, object]:
    coordinator = request.app.state.preparation_coordinator
    snapshot = coordinator.snapshot()
    issue = None
    has_institution = True
    services = snapshot.services
    manual = snapshot.manual_services
    next_services = services
    if snapshot.phase is PreparationPhase.IDLE or (snapshot.task_id is None and snapshot.phase is PreparationPhase.BLOCKED):
        try:
            relevant = read_saved_preparation_services(config_path)
            services = tuple(service for service in relevant if service.access_method == "carsi")
            manual = tuple(service for service in relevant if service.access_method == "manual")
            next_services = relevant
        except (ConfigurationError, ContentChangedError, OSError):
            issue = "Saved Access Mapping needs attention. Review Settings."
    elif snapshot.phase in (PreparationPhase.COMPLETED, PreparationPhase.STOPPED, PreparationPhase.BLOCKED):
        try:
            # 保留本次结果与人工列表；按钮资格只反映下一次主动 Prepare 的当前输入。
            next_services = read_saved_preparation_services(config_path)
        except (ConfigurationError, ContentChangedError, OSError):
            issue = "Saved Access Mapping needs attention. Review Settings."
    if snapshot.phase is not PreparationPhase.RUNNING:
        try:
            has_institution = saved_institution_login_url(config_path) is not None
        except (ConfigurationError, ContentChangedError, OSError):
            issue = "Saved Institution settings need attention. Review Settings."
            has_institution = False
    by_id = {service.id: service for service in services}
    return {
        "snapshot": snapshot,
        "services": services,
        "manual_services": manual,
        "current_service": by_id.get(snapshot.current_service_id),
        "result_rows": [(by_id.get(item.service_id), item) for item in snapshot.results],
        "ready": coordinator.ready(),
        "can_start": (bool(next_services) and issue is None
                      and (not any(service.access_method == "carsi" for service in next_services)
                           or (coordinator.ready() and has_institution))
                      and snapshot.phase is not PreparationPhase.RUNNING
                      and not snapshot.authorization_pending),
        "issue": issue,
        "has_institution": has_institution,
    }


class _ImportStartForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csrf_token: str | None = None
    import_token: str | None = None


class _ImportPollForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csrf_token: str | None = None
    view: str = "kept"
    paper: UUID | None = None


class _ConnectorClaimBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ConnectorInvocationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=REQUEST_ID_MAX_LENGTH)
    invocation_id: str = Field(min_length=1, max_length=REQUEST_ID_MAX_LENGTH)
    doi_url: str = Field(min_length=1, max_length=8192)
    tab_id: StrictInt = Field(ge=0)
    session_id: str = Field(min_length=1, max_length=REQUEST_ID_MAX_LENGTH)


class _ConnectorResultBody(_ConnectorInvocationBody):
    tab_id: StrictInt | None = Field(ge=0)
    session_id: str | None = Field(min_length=1, max_length=REQUEST_ID_MAX_LENGTH)
    outcome: CaptureOutcome
    pdf_outcome: PdfOutcome
    failure_stage: CaptureFailureStage | None = None


class _ConnectorAccessBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=REQUEST_ID_MAX_LENGTH)
    invocation_id: str = Field(min_length=1, max_length=REQUEST_ID_MAX_LENGTH)
    doi_url: str = Field(min_length=1, max_length=8192)
    tab_id: StrictInt = Field(ge=0)
    observation: AccessObservation


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _csrf_valid(submitted_csrf: str | None, csrf_token: str) -> bool:
    submitted = (submitted_csrf or "").encode("utf-8")
    return secrets.compare_digest(submitted, csrf_token.encode("ascii"))


def _workspace_state(
    config_path: Path,
) -> tuple[WorkspaceSnapshot | None, str | None]:
    try:
        config = load_config(config_path)
    except ConfigurationError as error:
        return None, str(error)

    return load_workspace(config.output_dir, journals=config.journals), None


def _workspace_health_context(config_path: Path) -> dict[str, object]:
    workspace, config_error = _workspace_state(config_path)
    return {"health_workspace": workspace, "health_config_error": config_error}


def _resolve_output_dir(config_path: Path) -> tuple[Path | None, str | None]:
    try:
        config = load_config(config_path)
    except ConfigurationError as error:
        return None, str(error)
    return config.output_dir, None


def _view_papers(
    workspace: WorkspaceSnapshot | None,
    view: str,
) -> tuple[str, tuple[WorkspacePaper, ...]]:
    selected_view = view if view in _VIEW_STATUSES else "inbox"
    if workspace is None:
        return selected_view, ()
    return selected_view, workspace.papers_for(_VIEW_STATUSES[selected_view])


def _find_paper(
    papers: tuple[WorkspacePaper, ...],
    paper_id: UUID | None,
) -> WorkspacePaper | None:
    if paper_id is None:
        return None
    return next(
        (paper for paper in papers if paper.paper_id == paper_id),
        None,
    )


def _workspace_context(
    *,
    request: Request,
    config_path: Path,
    csrf_token: str,
    view: str,
    selected_paper_id: UUID | None = None,
    decision_result: DecisionResult | None = None,
    decision_message: str | None = None,
    decision_message_tone: str = "warning",
    navigation_position: str | None = None,
    capture_snapshot: CaptureSnapshot | None = None,
) -> dict[str, object]:
    # Capture progress first: a terminal snapshot's commits must precede the
    # Markdown read, so its final poll cannot retain a stale Kept list.
    batch_snapshot = request.app.state.batch_import.snapshot()
    import_token = ""
    try:
        target, config = _ImportTarget.read(config_path)
        workspace = load_workspace(target.workspace, journals=config.journals)
        # 页面必须展示签名所绑定的同一次配置读取结果；读取中途改变则不签发授权。
        current, _ = _ImportTarget.read(config_path)
        if current != target:
            raise ContentChangedError("Workspace changed while rendering; refresh before importing")
        with request.app.state.import_submission_lock:
            import_token = target.form_token(request.app.state.import_token)
        config_error = None
    except (ConfigurationError, ContentChangedError, OSError) as error:
        workspace, config_error = None, str(error)
    selected_view, view_papers = _view_papers(workspace, view)
    selected_paper = _find_paper(view_papers, selected_paper_id)
    selection_stepped = False
    if (selected_paper is None and view_papers and decision_result is not None
            and decision_result.outcome is DecisionOutcome.UPDATED):
        # 位置只影响成功后的展示；mutation 始终由 UUID 和 expected-status 决定。
        try:
            position = int(navigation_position) if navigation_position is not None else 0
        except ValueError:
            position = 0
        selected_paper = view_papers[max(0, min(position, len(view_papers) - 1))]
        selection_stepped = True
    selected_position = next((index for index, paper in enumerate(view_papers)
                              if paper == selected_paper), None)
    open_doi_url = None
    if selected_paper is not None:
        try:
            normalized_doi = normalize_doi(selected_paper.external_ids.doi)
        except ValueError:
            normalized_doi = None
        if normalized_doi is not None:
            open_doi_url = f"https://doi.org/{quote(normalized_doi, safe='/')}"
    return {
        "request": request,
        "csrf_token": csrf_token,
        "workspace": workspace,
        "config_error": config_error,
        "active_view": selected_view,
        "papers": view_papers,
        "paper_sections": workspace.sections_for(_VIEW_STATUSES[selected_view]) if workspace else (),
        "selected_paper": selected_paper,
        "open_doi_url": open_doi_url,
        "selected_position": selected_position,
        "selection_stepped": selection_stepped,
        "batch": build_batch_presentation(batch_snapshot, workspace),
        "preparation": _preparation_context(request, config_path) if selected_view == "kept" else None,
        "import_token": import_token,
        "decision_result": decision_result,
        "decision_message": decision_message,
        "decision_message_tone": decision_message_tone,
        "capture_presentation": _capture_presentation(
            capture_snapshot,
            selected_paper.paper_id if selected_paper is not None else None,
        ),
    }


def _capture_presentation(
    snapshot: CaptureSnapshot | None,
    selected_paper_id: UUID | None,
) -> dict[str, object] | None:
    if snapshot is None or snapshot.attempt is None:
        return None
    attempt = snapshot.attempt
    if snapshot.completion_pending:
        return {
            "message": "Committing export outcome…",
            "poll": True,
            "tone": "warning",
        }
    if attempt.stage in {
        CaptureStage.WAITING_FOR_CONNECTOR,
        CaptureStage.CONNECTOR_ACTIVE,
    }:
        return {
            "message": (
                "Saving to Zotero…"
                if selected_paper_id == attempt.paper_id
                else "An automatic Zotero save is in progress."
            ),
            "poll": True,
            "tone": "warning",
        }
    if (
        attempt.stage is CaptureStage.FINISHED
        and attempt.terminal_outcome is CaptureOutcome.FAILED
    ):
        return {
            "message": (
                "Connector reported no parent dispatch. "
                "Export controls remain unavailable during the workflow transition."
            ),
            "poll": False,
            "tone": "warning",
        }
    if (
        attempt.stage is CaptureStage.FINISHED
        and attempt.terminal_outcome is None
    ):
        return {
            "message": (
                "The Literature Monitor Connector did not claim the automatic "
                "save. Automatic retry is blocked; inspect the unresolved export attempt."
            ),
            "poll": False,
            "tone": "warning",
        }
    return None


def _zotero_integration_state(
    capture_snapshot: CaptureSnapshot,
) -> dict[str, object]:
    with ZoteroLocalClient() as local:
        instance = local.current_instance()
    return {
        "zotero_instance": instance,
        "connector_readiness": capture_snapshot.readiness.value,
    }


def _run_context(
    request: Request,
    csrf_token: str,
    snapshot: CoordinatorSnapshot,
    *,
    start_result: StartResult | None = None,
) -> dict[str, object]:
    try:
        configured = load_config(request.app.state.config_path)
        months = (configured.date_spec.from_month, configured.date_spec.to_month)
        configuration_problem = None
    except ConfigurationError as error:
        months = None
        configuration_problem = str(error)
    return {
        "request": request,
        "csrf_token": csrf_token,
        "run_months": months,
        "run_configuration_problem": configuration_problem,
        "run_snapshot": snapshot,
        "run_progress": build_run_presentation(snapshot, now=_utc_now()),
        "run_start_result": start_result,
    }


def _capture_api_state(snapshot: CaptureSnapshot) -> dict[str, object]:
    attempt = snapshot.attempt
    return {
        "readiness": snapshot.readiness.value,
        "connector_version": snapshot.connector_version,
        "zotero_reachable": snapshot.zotero_reachable,
        "connector_online": snapshot.connector_online,
        "enrichment_capable": snapshot.enrichment_capable,
        "capture": (
            None
            if attempt is None
            else {
                "stage": attempt.stage.value,
                "terminal_outcome": (
                    attempt.terminal_outcome.value
                    if attempt.terminal_outcome is not None
                    else None
                ),
                "completion_pending": snapshot.completion_pending,
            }
        ),
    }


def _settings_context(
    *,
    request: Request,
    csrf_token: str,
    form_values: SettingsFormValues,
    issues: tuple[SettingsIssue, ...] = (),
    validation_result: SettingsValidationResult | None = None,
    save_result: SettingsSaveResult | None = None,
    disk_state: SettingsLoadResult | None = None,
    attempted_values: SettingsFormValues | None = None,
    message: str | None = None,
    message_tone: str = "warning",
    deletion_plan: SettingsDeletionPlan | None = None,
    deletion_proof: str = "",
    markdown_plan=None,
    markdown_contents: str = "",
    markdown_filename: str = "",
    markdown_proof: str = "",
    markdown_dirty: bool = False,
    markdown_export_guard: bool = False,
    markdown_export_kind: str = "markdown",
    markdown_reload_guard: bool = False,
    access_upgrade_plan=None,
    access_upgrade_proof: str = "",
    access_upgrade_dirty: bool = False,
) -> dict[str, object]:
    disk = disk_state or load_settings(request.app.state.config_path)
    current_list = _read_snapshot(disk.journal_path, source=SettingsIssueSource.JOURNAL_DATA)
    saved_service_urls: dict[str, str] = {}
    try:
        needs_access_upgrade = (current_list.contents is not None
                                and detect_list_schema(current_list.contents, path=disk.journal_path) == "legacy")
        if current_list.issue is None and current_list.contents is not None and not needs_access_upgrade:
            _, _, saved_services = parse_settings_list_text(current_list.contents, path=disk.journal_path)
            saved_service_urls = {
                service.id: service.access_url for service in saved_services if service.access_url
            }
    except ConfigurationError:
        needs_access_upgrade = False
    return {
        "request": request,
        "csrf_token": csrf_token,
        "settings_form": form_values,
        "settings_issues": issues,
        "settings_validation": validation_result,
        "settings_save": save_result,
        "settings_disk_state": disk_state,
        "settings_attempted": attempted_values,
        "settings_message": message,
        "settings_message_tone": message_tone,
        "settings_deletion_plan": deletion_plan,
        "settings_deletion_proof": deletion_proof,
        "markdown_plan": markdown_plan,
        "markdown_contents": markdown_contents,
        "markdown_payload": base64.b64encode(markdown_contents.encode("utf-8")).decode("ascii"),
        "markdown_filename": markdown_filename,
        "markdown_proof": markdown_proof,
        "markdown_dirty": markdown_dirty,
        "markdown_export_guard": markdown_export_guard,
        "markdown_export_kind": markdown_export_kind,
        "saved_service_urls": saved_service_urls,
        "markdown_reload_guard": markdown_reload_guard,
        "access_upgrade_plan": access_upgrade_plan,
        "access_upgrade_proof": access_upgrade_proof,
        "access_upgrade_dirty": access_upgrade_dirty,
        "needs_access_upgrade": needs_access_upgrade,
    }


def create_app(config_path: Path) -> FastAPI:
    """Create the local Web adapter without requiring valid configuration."""

    resolved_config_path = config_path.resolve()
    csrf_token = secrets.token_urlsafe(32)
    deletion_key = secrets.token_bytes(32)
    app = FastAPI(
        title="Literature Monitor",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.config_path = resolved_config_path
    app.state.csrf_token = csrf_token
    app.state.run_coordinator = RunCoordinator(resolved_config_path)
    app.state.capture_coordinator = CaptureCoordinator()
    app.state.paper_enrichment = PaperEnrichment(app.state.capture_coordinator)
    app.state.batch_import = BatchImportService(app.state.capture_coordinator)
    # Only a verified Connector capability heartbeat can enable browser claims.
    app.state.preparation_coordinator = PreparationCoordinator(
        app.state.capture_coordinator, execution_enabled=True,
    )
    app.state.import_token = secrets.token_urlsafe(32)
    app.state.reset_confirmation = None
    app.state.reset_lock = threading.Lock()
    import_submission_lock = threading.RLock()
    app.state.import_submission_lock = import_submission_lock

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=_ALLOWED_HOSTS,
    )
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.post("/api/connector/heartbeat")
    def connector_heartbeat(body: _ConnectorHeartbeatBody) -> JSONResponse:
        try:
            readiness = app.state.capture_coordinator.heartbeat(
                connector_version=body.version,
                zotero_reachable=body.zotero_reachable,
                enrichment_capable=ENRICHMENT_CAPABILITY in body.capabilities,
            )
        except ValueError:
            return JSONResponse({"accepted": False}, status_code=422)
        return JSONResponse({"readiness": readiness.value})

    @app.post("/api/connector/enrichment/claim")
    def enrichment_claim(body: _ConnectorClaimBody):
        return JSONResponse({"command": app.state.paper_enrichment.claim()})

    @app.post("/api/connector/enrichment/authorize")
    def enrichment_authorize(body: _EnrichmentAuthorization):
        accepted = app.state.paper_enrichment.authorize(
            body.model_dump(exclude={'waiting_for_user'}), body.waiting_for_user)
        return JSONResponse({'accepted': accepted}, status_code=200 if accepted else 409)

    @app.post("/api/connector/enrichment/result")
    def enrichment_result(body: _EnrichmentResult):
        result = app.state.paper_enrichment.result(
            body.model_dump(exclude={'items', 'error'}), body.items, body.error)
        return JSONResponse(result or {'accepted': False}, status_code=200 if result else 409)

    @app.get("/api/papers/{paper_id}/enrichment/state")
    def enrichment_state(paper_id: UUID, request_id: str | None = None):
        return JSONResponse(app.state.paper_enrichment.state(paper_id, request_id))

    @app.post("/api/papers/{paper_id}/enrichment/{action}")
    def enrichment_action(paper_id: UUID, action: str, body: _EnrichmentAction):
        if not _csrf_valid(body.csrf_token, csrf_token):
            return JSONResponse({'error': 'Invalid CSRF token'}, status_code=403)
        service = app.state.paper_enrichment
        try:
            if action == 'cancel':
                accepted = service.cancel(paper_id, body.request_id)
                return JSONResponse({'accepted': accepted}, status_code=200 if accepted else 409)
            if action not in {'start', 'save'}:
                return JSONResponse({'error': 'Unknown action'}, status_code=404)
            workspace = load_config(resolved_config_path).output_dir
            state = service.start(workspace, paper_id) if action == 'start' else service.save(workspace, paper_id, body.request_id)
            return JSONResponse(state)
        except (ConfigurationError, ValueError, OSError, ContentChangedError, CompareReadError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=409)

    @app.post("/api/connector/preparation/heartbeat")
    def preparation_heartbeat(body: _PreparationHeartbeat) -> JSONResponse:
        ready = app.state.preparation_coordinator.heartbeat(
            body.instance_id, capability=body.capability, version=body.version,
            consent_capability=body.consent_capability,
        )
        return JSONResponse({"ready": ready})

    @app.post("/api/connector/import-browser-idle")
    def import_browser_idle(body: _ImportBrowserIdleBody) -> JSONResponse:
        accepted = app.state.capture_coordinator.report_import_browser_idle(**body.model_dump())
        return JSONResponse({"accepted": accepted}, status_code=200 if accepted else 409)

    @app.post("/api/connector/preparation/claim")
    def preparation_claim(body: _PreparationClaim) -> JSONResponse:
        command = app.state.preparation_coordinator.claim(body.instance_id)
        return JSONResponse({"command": None if command is None else {
            "kind": command.kind, "task_id": command.task_id,
            "generation": command.generation, "request_id": command.request_id,
            "service_id": command.service_id, "name": command.name,
            "access_url": command.access_url, "login_url": command.login_url,
            "last_service": command.last_service,
        }})

    @app.post("/api/connector/preparation/authorize")
    def preparation_authorize(body: _PreparationIdentity) -> JSONResponse:
        accepted = app.state.preparation_coordinator.authorize(**body.model_dump())
        return JSONResponse({"accepted": accepted}, status_code=200 if accepted else 409)

    @app.post("/api/connector/preparation/waiting")
    def preparation_waiting(body: _PreparationWaitingBody) -> JSONResponse:
        accepted = app.state.preparation_coordinator.report_waiting(**body.model_dump())
        return JSONResponse({"accepted": accepted}, status_code=200 if accepted else 409)

    @app.post("/api/connector/preparation/visit-authorize")
    def preparation_visit_authorize(body: _PreparationVisitAuthorization) -> JSONResponse:
        nonce = app.state.preparation_coordinator.authorize_visit(**body.model_dump())
        return JSONResponse({"nonce": nonce}, status_code=200 if nonce else 409)

    @app.post("/api/connector/preparation/result")
    def preparation_result(body: _PreparationResultBody) -> JSONResponse:
        values = body.model_dump()
        proof = values.pop("evidence")
        accepted = app.state.preparation_coordinator.result(
            **values, evidence=VisitEvidence(**proof) if proof is not None else None,
        )
        return JSONResponse({"accepted": accepted}, status_code=200 if accepted else 409)


    @app.post("/api/preparation/start")
    def preparation_start(body: _PreparationStartBody) -> JSONResponse:
        if not _csrf_valid(body.csrf_token, csrf_token):
            return JSONResponse({"error": "Invalid CSRF token"}, status_code=403)
        try:
            state = app.state.preparation_coordinator.start(resolved_config_path)
        except (ConfigurationError, OSError, ContentChangedError):
            return JSONResponse({"error": "Saved list.md or monitor.yaml Institution configuration invalid; review Settings"}, status_code=400)
        ready = state.phase in {PreparationPhase.RUNNING, PreparationPhase.COMPLETED}
        return JSONResponse(_preparation_json(state), status_code=200 if ready else 409)

    @app.get("/api/preparation/state")
    def preparation_state() -> JSONResponse:
        return JSONResponse(_preparation_json(app.state.preparation_coordinator.snapshot()))

    @app.post("/api/preparation/cancel")
    def preparation_cancel(body: _PreparationCancelBody) -> JSONResponse:
        if not _csrf_valid(body.csrf_token, csrf_token):
            return JSONResponse({"accepted": False}, status_code=403)
        accepted = app.state.preparation_coordinator.cancel(body.task_id, body.generation)
        return JSONResponse({"accepted": accepted}, status_code=200 if accepted else 409)

    @app.post("/api/connector/claim")
    def connector_claim(body: _ConnectorClaimBody) -> JSONResponse:
        command = app.state.capture_coordinator.claim_command()
        return JSONResponse(
            {
                "command": (
                    None
                    if command is None
                    else {
                        "request_id": command.request_id,
                        "doi_url": command.doi_url,
                        "invocation_id": command.invocation_id,
                        **({"access_context": command.access_context} if command.access_context else {}),
                    }
                )
            }
        )

    @app.post("/api/connector/dispatch")
    def connector_dispatch(body: _ConnectorInvocationBody) -> JSONResponse:
        accepted = app.state.capture_coordinator.authorize_parent_dispatch(
            SaveInvocation(**body.model_dump()),
        )
        return JSONResponse({"accepted": accepted}, status_code=200 if accepted else 409)

    @app.post("/api/connector/native-save-settled")
    def connector_native_save_settled(body: _ConnectorInvocationBody) -> JSONResponse:
        accepted = app.state.capture_coordinator.report_native_save_settled(
            SaveInvocation(**body.model_dump()),
        )
        return JSONResponse({"accepted": accepted}, status_code=200 if accepted else 409)

    @app.post("/api/connector/access")
    def connector_access(body: _ConnectorAccessBody) -> JSONResponse:
        accepted = app.state.capture_coordinator.record_access(**body.model_dump(exclude={'observation'}),
                                                              observation=body.observation)
        return JSONResponse({"accepted": accepted}, status_code=200 if accepted else 409)

    @app.post("/api/connector/result")
    def connector_result(body: _ConnectorResultBody) -> JSONResponse:
        accepted = app.state.capture_coordinator.submit_connector_result(**body.model_dump())
        return JSONResponse(
            {"accepted": accepted},
            status_code=200 if accepted else 409,
        )

    @app.get("/api/connector/state")
    def connector_state() -> JSONResponse:
        snapshot = app.state.capture_coordinator.snapshot()
        return JSONResponse(_capture_api_state(snapshot))

    @app.get("/", response_class=HTMLResponse)
    def workspace_page(
        request: Request,
        view: str = "inbox",
        paper: UUID | None = None,
    ) -> HTMLResponse:
        context = _workspace_context(
            request=request,
            config_path=resolved_config_path,
            csrf_token=csrf_token,
            view=view,
            selected_paper_id=paper,
            capture_snapshot=app.state.capture_coordinator.snapshot(),
        )
        return templates.TemplateResponse(request, "workspace.html", context)

    def reset_preview_context() -> dict[str, object]:
        with app.state.reset_lock:
            # Each display supersedes its predecessor; POST cannot choose a path.
            app.state.reset_confirmation = None
            try:
                plan = preview_reset(resolved_config_path)
                token = secrets.token_urlsafe(32)
                app.state.reset_confirmation = (token, plan)
                return {"reset_plan": plan, "reset_token": token, "reset_error": None}
            except (ResetRefused, OSError, ValueError) as error:
                return {"reset_plan": None, "reset_token": None, "reset_error": str(error)}

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request) -> HTMLResponse:
        settings_state = load_settings(resolved_config_path)
        context = _settings_context(
            request=request,
            csrf_token=csrf_token,
            form_values=settings_form_from_draft(settings_state.draft),
            issues=settings_state.issues,
            disk_state=settings_state,
            message=(
                "Configuration needs attention. You can repair it here."
                if settings_state.issues
                else None
            ),
        )
        context.update(_workspace_health_context(resolved_config_path))
        context.update(reset_preview_context())
        context["run_snapshot"] = app.state.run_coordinator.snapshot()
        context["connector_readiness"] = (
            app.state.capture_coordinator.snapshot().readiness.value
        )
        return templates.TemplateResponse(
            request,
            "settings.html",
            context,
        )

    @app.post("/settings/workspace-reset", response_class=HTMLResponse)
    async def reset_workspace_route(request: Request) -> HTMLResponse:
        form = await request.form()
        csrf = form.get("csrf_token")
        if not _csrf_valid(str(csrf) if csrf is not None else None, csrf_token):
            return HTMLResponse("Invalid or missing CSRF token.", status_code=403)
        reset_result = None
        with app.state.reset_lock:
            saved = app.state.reset_confirmation
            token = form.get("reset_token")
            if (saved is None or not isinstance(token, str)
                    or not secrets.compare_digest(token, saved[0])):
                result_message, status = "Reset confirmation is stale or already used. Reload Settings.", 409
            elif form.get("reset_word") != "RESET":
                app.state.reset_confirmation = None
                result_message, status = "Type the complete RESET confirmation. Reload Settings to retry.", 400
            elif app.state.run_coordinator.snapshot().status is CoordinatorStatus.RUNNING:
                app.state.reset_confirmation = None
                result_message, status = "A Run is active; Reset refused.", 409
            elif app.state.batch_import.running:
                app.state.reset_confirmation = None
                result_message, status = "A batch Import is active; Reset refused.", 409
            elif blocker := app.state.capture_coordinator.reset_blocker:
                app.state.reset_confirmation = None
                result_message, status = blocker, 409
            else:
                app.state.reset_confirmation = None
                result = execute_reset(resolved_config_path, saved[1])
                reset_result = result
                result_message = result.message
                status = 200 if result.success else 409
        return templates.TemplateResponse(
            request, "fragments/workspace_reset.html",
            {"request": request, "csrf_token": csrf_token,
             "reset_plan": None, "reset_token": None,
             "reset_error": result_message, "reset_success": status == 200,
             "reset_result": reset_result},
            status_code=status,
        )

    @app.get("/settings/zotero", response_class=HTMLResponse)
    def zotero_integration_status(request: Request) -> HTMLResponse:
        context = {
            "request": request,
            **_zotero_integration_state(
                app.state.capture_coordinator.snapshot(),
            ),
        }
        return templates.TemplateResponse(request, "fragments/zotero_integration.html", context)

    @app.get("/fragments/run", response_class=HTMLResponse)
    def run_fragment(request: Request) -> HTMLResponse:
        snapshot = app.state.run_coordinator.snapshot()
        headers = (
            {"HX-Trigger": "runCompleted"}
            if snapshot.status is CoordinatorStatus.FINISHED
            else None
        )
        return templates.TemplateResponse(
            request,
            "fragments/run.html",
            _run_context(request, csrf_token, snapshot),
            headers=headers,
        )

    @app.post("/run", response_class=HTMLResponse)
    async def start_run(
        request: Request,
        csrf_token_value: Annotated[str | None, Form(alias="csrf_token")] = None,
    ) -> HTMLResponse:
        if not _csrf_valid(csrf_token_value, csrf_token):
            return HTMLResponse(
                '<p class="notice error">Invalid or missing CSRF token.</p>',
                status_code=403,
            )
        start_result = app.state.run_coordinator.start()
        snapshot = app.state.run_coordinator.snapshot()
        response = templates.TemplateResponse(
            request,
            "fragments/run.html",
            _run_context(
                request,
                csrf_token,
                snapshot,
                start_result=start_result,
            ),
        )
        if start_result.outcome is StartOutcome.STARTED:
            response.headers["HX-Trigger"] = (
                "runStarted, runCompleted"
                if snapshot.status is CoordinatorStatus.FINISHED
                else "runStarted"
            )
        elif start_result.outcome is StartOutcome.START_FAILED:
            response.headers["HX-Trigger"] = "runStartFailed"
        return response

    @app.get("/fragments/current-run", response_class=HTMLResponse)
    def current_run_fragment(request: Request) -> HTMLResponse:
        snapshot = app.state.run_coordinator.snapshot()
        return templates.TemplateResponse(
            request,
            "fragments/current_run.html",
            {"run_snapshot": snapshot},
        )

    def markdown_reply(request: Request, values: SettingsFormValues, *, message: str,
                       plan=None, contents: str = "", filename: str = "",
                       proof: str = "", dirty: bool = False, issues=(),
                       export_guard: bool = False, export_kind: str = "markdown",
                       reload_guard: bool = False,
                       upgrade_plan=None, upgrade_proof: str = "",
                       upgrade_dirty: bool = False) -> HTMLResponse:
        return templates.TemplateResponse(request, "fragments/settings_editor.html",
            _settings_context(request=request, csrf_token=csrf_token, form_values=values,
                issues=issues, message=message, markdown_plan=plan,
                markdown_contents=contents, markdown_filename=filename,
                markdown_proof=proof, markdown_dirty=dirty,
                markdown_export_guard=export_guard, markdown_export_kind=export_kind,
                markdown_reload_guard=reload_guard,
                access_upgrade_plan=upgrade_plan, access_upgrade_proof=upgrade_proof,
                access_upgrade_dirty=upgrade_dirty))

    def submitted_settings(form):
        values = settings_form_from_submission(form)
        draft, issues = settings_draft_from_form(values)
        disk = load_settings(resolved_config_path)
        # Empty A3 Groups are presentation-only draft objects: compare them explicitly.
        # They are not part of MonitorDraft, but Import/Export must not discard them silently.
        occupied_groups = {row.group for row in values.journals if row.group}
        empty_draft_groups = {group for group in values.groups if group and group not in occupied_groups}
        dirty = draft is None or draft != disk.draft or bool(empty_draft_groups)
        return values, draft, issues, disk, dirty

    def require_settings_csrf(form) -> bool:
        supplied = form.get("csrf_token")
        return _csrf_valid(str(supplied) if supplied is not None else None, csrf_token)

    @app.post("/settings/upgrade/preview", response_class=HTMLResponse)
    async def preview_access_upgrade(request: Request) -> HTMLResponse:
        form = await request.form()
        if not require_settings_csrf(form):
            return HTMLResponse("Invalid or missing CSRF token.", status_code=403)
        values, draft, issues, disk, dirty = submitted_settings(form)
        try:
            if draft is None or issues:
                raise ConfigurationError("Invalid Settings draft prevents Access schema Upgrade")
            plan = preview_access_schema_upgrade(disk.journal_path)
            proof = hmac.new(deletion_key, repr((plan, draft)).encode("utf-8"), hashlib.sha256).hexdigest()
            return markdown_reply(request, values, message="Legacy Access schema Upgrade Preview; no files changed.",
                                  upgrade_plan=plan, upgrade_proof=proof, upgrade_dirty=dirty)
        except (ConfigurationError, ContentChangedError, OSError, ValueError) as error:
            return markdown_reply(request, values, message=f"Upgrade Preview blocked: {error}", issues=issues)

    @app.post("/settings/upgrade/confirm", response_class=HTMLResponse)
    async def confirm_access_upgrade(request: Request) -> HTMLResponse:
        form = await request.form()
        if not require_settings_csrf(form):
            return HTMLResponse("Invalid or missing CSRF token.", status_code=403)
        values, draft, issues, disk, dirty = submitted_settings(form)
        try:
            if draft is None or issues:
                raise ConfigurationError("Invalid Settings draft prevents Access schema Upgrade")
            if str(form.get("confirm_access_upgrade", "")) != "yes":
                raise ConfigurationError("Explicit Upgrade confirmation required")
            if dirty and str(form.get("upgrade_discard", "")) != "yes":
                raise ConfigurationError("Unsaved Settings Draft: Save or explicitly Discard before Upgrade")
            plan = preview_access_schema_upgrade(disk.journal_path)
            expected = hmac.new(deletion_key, repr((plan, draft)).encode("utf-8"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(str(form.get("access_upgrade_proof", "")), expected):
                raise ContentChangedError("Access schema Upgrade confirmation is stale; Preview again")
            if (draft.journal_revision != disk.draft.journal_revision
                    or draft.monitor_revision != disk.draft.monitor_revision):
                raise ContentChangedError("Settings changed since Upgrade Preview")
            confirm_access_schema_upgrade(plan, confirmed=True)
            saved = load_settings(resolved_config_path)
            response = markdown_reply(request, settings_form_from_draft(saved.draft),
                                      message="Access schema upgrade saved. Review and Preview Markdown Import again.",
                                      issues=saved.issues)
            response.headers["HX-Trigger"] = "settingsSaved"
            return response
        except AccessSchemaUpgradeCommittedWarning as warning:
            saved = load_settings(resolved_config_path)
            if saved.issues or saved.draft.journal_revision.digest != warning.revision:
                return markdown_reply(
                    request, values, issues=saved.issues,
                    message="Upgrade write occurred, but disk state changed or could not be reread reliably. "
                            "Durability is unconfirmed. Inspect storage and Reload; do not retry Upgrade directly.",
                )
            response = markdown_reply(
                request, settings_form_from_draft(saved.draft), issues=saved.issues,
                message="Legacy Access schema upgrade wrote list.md, but directory synchronization failed. "
                        "Durable persistence is not confirmed. "
                        f"Current disk revision: {warning.revision}. "
                        "Check storage health; do not retry Upgrade directly.",
            )
            response.headers["HX-Trigger"] = "settingsSaved"
            return response
        except AccessSchemaUpgradeStateUncertain as error:
            return markdown_reply(
                request, values, issues=issues,
                message=f"Legacy Access schema Upgrade write state cannot be confirmed: {error}. "
                        "Durability is unconfirmed; inspect storage and Reload before any further changes. "
                        "Do not retry Upgrade directly.",
            )
        except (ConfigurationError, ContentChangedError, CompareReadError, OSError, ValueError) as error:
            return markdown_reply(request, values, message=f"Upgrade not written: {error}", issues=issues)

    @app.post("/settings/import/preview", response_class=HTMLResponse)
    async def preview_markdown_import(request: Request) -> HTMLResponse:
        form = await request.form()
        if not require_settings_csrf(form):
            return HTMLResponse("Invalid or missing CSRF token.", status_code=403)
        values, draft, issues, disk, dirty = submitted_settings(form)
        upload = form.get("markdown_file")
        try:
            if draft is None or issues:
                raise ConfigurationError("Invalid Settings draft; preserve it and correct errors before Import")
            if not isinstance(upload, UploadFile) or not upload.filename:
                raise ConfigurationError("Choose a complete UTF-8 .md file")
            filename = upload.filename
            if not filename.lower().endswith(".md") or Path(filename).name != filename:
                raise ConfigurationError("Import requires a complete .md file")
            blob = await upload.read(MAX_MARKDOWN_BYTES + 1)
            if len(blob) > MAX_MARKDOWN_BYTES:
                raise ConfigurationError("Markdown Import file exceeds 1 MiB")
            contents = blob.decode("utf-8", errors="strict")
            plan = preview_full_markdown(disk.journal_path, contents, filename=filename)
            proof = hmac.new(deletion_key, repr((plan, draft)).encode("utf-8"), hashlib.sha256).hexdigest()
            return markdown_reply(request, values, message="Full Markdown replacement Preview; no files changed.",
                plan=plan, contents=contents, filename=filename, proof=proof, dirty=dirty)
        except (ConfigurationError, ContentChangedError, OSError, UnicodeError, ValueError) as error:
            return markdown_reply(request, values, message=f"Import Preview blocked: {error}", issues=issues)

    @app.post("/settings/import/confirm", response_class=HTMLResponse)
    async def confirm_markdown_import(request: Request) -> HTMLResponse:
        form = await request.form()
        if not require_settings_csrf(form):
            return HTMLResponse("Invalid or missing CSRF token.", status_code=403)
        values, draft, issues, disk, dirty = submitted_settings(form)
        payload = str(form.get("markdown_import_payload", ""))
        filename = str(form.get("markdown_import_filename", ""))
        try:
            if draft is None or issues:
                raise ConfigurationError("Invalid draft; cannot confirm Import")
            if form.get("confirm_full_import") != "yes":
                raise ConfigurationError("Explicit Confirm Import required")
            if dirty and form.get("import_discard") != "yes":
                raise ConfigurationError("Unsaved Settings Draft: Save or explicitly Discard before Import")
            # Base64 preserves the uploaded bytes exactly: browser FormData normalizes
            # textarea newlines, which would otherwise invalidate a valid LF-based source.
            try:
                binary = base64.b64decode(payload, validate=True)
                if len(binary) > MAX_MARKDOWN_BYTES:
                    raise ValueError("source exceeds 1 MiB")
                contents = binary.decode("utf-8", errors="strict")
            except (ValueError, UnicodeError, binascii.Error) as error:
                raise ConfigurationError("Import source payload changed or is invalid; Preview again") from error
            plan = preview_full_markdown(disk.journal_path, contents, filename=filename)
            expected = hmac.new(deletion_key, repr((plan, draft)).encode("utf-8"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(str(form.get("markdown_import_proof", "")), expected):
                raise ContentChangedError("Import source, draft, plan or target revision changed; Preview again")
            if (draft.journal_revision != disk.draft.journal_revision
                    or draft.monitor_revision != disk.draft.monitor_revision):
                raise ContentChangedError("Settings revisions changed; Reload and Preview again")
            def verify_monitor_before_list_write() -> None:
                monitor_snapshot = _read_snapshot(
                    resolved_config_path, source=SettingsIssueSource.MONITOR_CONFIG,
                )
                if monitor_snapshot.issue or monitor_snapshot.revision != draft.monitor_revision:
                    raise ContentChangedError("monitor.yaml changed before list.md Import replacement")
            digest = confirm_full_markdown(plan, before_write=verify_monitor_before_list_write)
            saved = load_settings(resolved_config_path)
            response = markdown_reply(request, settings_form_from_draft(saved.draft),
                message=f"Full Markdown Import saved to list.md. New revision: {digest}")
            response.headers["HX-Trigger"] = "settingsSaved"
            return response
        except MarkdownImportCommittedWarning as warning:
            saved = load_settings(resolved_config_path)
            response = markdown_reply(
                request, settings_form_from_draft(saved.draft),
                message=f"Import reached list.md but directory synchronization failed. Actual saved revision: {warning.revision}. Verify storage health.",
                issues=saved.issues,
            )
            response.headers["HX-Trigger"] = "settingsSaved"
            return response
        except (ConfigurationError, ContentChangedError, CompareReadError, OSError, UnicodeError, ValueError, OpenAlexError) as error:
            return markdown_reply(request, values, message=f"Import not written: {error}", issues=issues)

    @app.post("/settings/export", response_class=HTMLResponse)
    async def export_markdown(request: Request):
        form = await request.form()
        if not require_settings_csrf(form):
            return HTMLResponse("Invalid or missing CSRF token.", status_code=403)
        values, draft, issues, disk, dirty = submitted_settings(form)
        if dirty and form.get("export_discard") != "yes":
            return markdown_reply(request, values, message="Unsaved Settings Draft: Save or explicitly Discard before Export.",
                issues=issues, export_guard=True)
        try:
            snap = _read_snapshot(disk.journal_path, source=SettingsIssueSource.JOURNAL_DATA)
            if snap.issue or snap.contents is None:
                raise ConfigurationError(snap.issue.message if snap.issue else "list.md missing")
            parse_settings_list_text(snap.contents, path=disk.journal_path)
            if any(candidate.is_symlink() for candidate in (disk.journal_path, *disk.journal_path.parents)):
                raise ConfigurationError("Unsafe saved Markdown path")
            return Response(content=snap.contents.encode("utf-8"),
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="list.md"',
                         "Cache-Control": "no-store"})
        except (ConfigurationError, OSError) as error:
            return markdown_reply(request, values, message=f"Export blocked: {error}", issues=issues)

    @app.post("/settings/export-ai-mapping-pack", response_class=HTMLResponse)
    async def export_ai_mapping_pack(request: Request):
        form = await request.form()
        if not require_settings_csrf(form):
            return HTMLResponse("Invalid or missing CSRF token.", status_code=403)
        values, draft, issues, disk, dirty = submitted_settings(form)
        if dirty and form.get("export_discard") != "yes":
            return markdown_reply(
                request, values, message="Unsaved Settings Draft: Save or explicitly Discard before AI Mapping Pack export.",
                issues=issues, export_guard=True, export_kind="pack",
            )
        try:
            pack = await run_in_threadpool(generate_ai_mapping_pack, disk.journal_path)
            return Response(
                content=pack.markdown.encode("utf-8"), media_type="text/markdown; charset=utf-8",
                headers={
                    "Content-Disposition": f'attachment; filename="{pack.filename}"',
                    "Cache-Control": "no-store",
                },
            )
        except MappingPackError as error:
            return markdown_reply(request, values, message=f"AI Mapping Pack export blocked: {error}", issues=issues)

    @app.post("/settings/reload", response_class=HTMLResponse)
    async def reload_markdown(request: Request) -> HTMLResponse:
        form = await request.form()
        if not require_settings_csrf(form):
            return HTMLResponse("Invalid or missing CSRF token.", status_code=403)
        values, draft, issues, disk, dirty = submitted_settings(form)
        if form.get("reload_step") != "confirm" or form.get("reload_word") != "RELOAD":
            return markdown_reply(request, values,
                message="Reload discards unsaved Journals, Groups, Mapping, Monitor and Institution edits. Confirm explicitly.",
                reload_guard=True)
        saved = load_settings(resolved_config_path)
        response = markdown_reply(request, settings_form_from_draft(saved.draft),
            message="Reloaded both Settings files from disk. Prior unsaved edits discarded.", issues=saved.issues)
        response.headers["HX-Trigger"] = "settingsSaved"
        return response

    @app.post("/settings/save", response_class=HTMLResponse)
    async def save_settings_route(request: Request) -> HTMLResponse:
        form = await request.form()
        submitted_csrf = form.get("csrf_token")
        if not _csrf_valid(
            str(submitted_csrf) if submitted_csrf is not None else None,
            csrf_token,
        ):
            return HTMLResponse(
                '<p class="notice error">Invalid or missing CSRF token.</p>',
                status_code=403,
            )

        values = settings_form_from_submission(form)
        draft, form_issues = settings_draft_from_form(values)
        if draft is None:
            return templates.TemplateResponse(
                request,
                "fragments/settings_editor.html",
                _settings_context(
                    request=request,
                    csrf_token=csrf_token,
                    form_values=values,
                    issues=form_issues,
                    message="Settings were not saved.",
                ),
            )

        disk = load_settings(config_path)
        plan = plan_settings_deletions(disk.draft, draft)
        proof = hmac.new(deletion_key, repr((draft, plan)).encode("utf-8"), hashlib.sha256).hexdigest()
        approved = (
            plan.required
            and str(form.get("confirm_settings_deletions", "")) == "yes"
            and hmac.compare_digest(str(form.get("settings_deletion_proof", "")), proof)
        )
        # 确认仅由本进程签发，精确绑定全部草稿字段、真实删除目标及原始版本。
        # 不保存草稿副本；变更草稿、服务成员或文件版本都需重新预览。
        if plan.required and not approved and not disk.issues and (
            draft.monitor_revision == disk.draft.monitor_revision
            and draft.journal_revision == disk.draft.journal_revision
        ) and validate_settings(config_path, draft).outcome is SettingsValidationOutcome.VALID:
            return templates.TemplateResponse(
                request, "fragments/settings_editor.html",
                _settings_context(
                    request=request, csrf_token=csrf_token, form_values=values,
                    deletion_plan=plan, deletion_proof=proof,
                    message="Review the affected Services and Publishers, then confirm this Save.",
                ),
            )
        approval = SettingsDeletionApproval(
            draft.monitor_revision, draft.journal_revision,
            tuple(service.id for service in plan.services),
            tuple(publisher.publisher_id for publisher in plan.publishers),
        ) if approved else None
        result = (
            save_settings(config_path, draft, deletion_approval=approval)
            if approval is not None else save_settings(config_path, draft)
        )
        if result.outcome is SettingsSaveOutcome.SAVED:
            response = templates.TemplateResponse(
                request,
                "fragments/settings_editor.html",
                _settings_context(
                    request=request,
                    csrf_token=csrf_token,
                    form_values=settings_form_from_draft(result.state.draft),
                    issues=result.issues,
                    validation_result=result.validation,
                    save_result=result,
                    disk_state=result.state,
                    message="Settings saved.",
                    message_tone="success",
                ),
            )
            response.headers["HX-Trigger"] = "settingsSaved"
            return response

        if result.outcome is SettingsSaveOutcome.PARTIAL_SAVE:
            # 保留提交时的字段与原始 revision；只读磁盘状态单独报告实际写入。
            form_values = values
            message = (
                "Settings were partially saved: "
                f"journal data written: {'yes' if result.journal_written else 'no'}, "
                f"monitor configuration written: {'yes' if result.monitor_written else 'no'}. "
                "Reload and reconcile before another Save."
            )
        elif result.outcome is SettingsSaveOutcome.REVISION_CONFLICT:
            form_values = values
            message = (
                "Settings were not saved because files changed since this "
                "editor was opened. Reload and reconcile before retrying."
            )
        elif result.outcome is SettingsSaveOutcome.WRITE_FAILED:
            form_values = values
            message = "Settings could not be written."
        else:
            form_values = values
            message = "Settings draft is invalid and was not saved."

        return templates.TemplateResponse(
            request,
            "fragments/settings_editor.html",
            _settings_context(
                request=request,
                csrf_token=csrf_token,
                form_values=form_values,
                issues=result.issues,
                validation_result=result.validation,
                save_result=result,
                disk_state=result.state,
                attempted_values=values,
                message=message,
                message_tone="warning",
            ),
        )

    def render_import_workspace(request: Request, *, view: str = "kept",
                                paper: UUID | None = None, message: str | None = None,
                                status_code: int = 200) -> HTMLResponse:
        context = _workspace_context(request=request, config_path=resolved_config_path,
            csrf_token=csrf_token, view=view, selected_paper_id=paper, decision_message=message)
        return templates.TemplateResponse(request, "fragments/workspace.html", context,
                                          status_code=status_code)

    def render_preparation(request: Request, *, message: str | None = None) -> HTMLResponse:
        return templates.TemplateResponse(request, "fragments/preparation.html", {
            "csrf_token": csrf_token,
            "preparation": _preparation_context(request, resolved_config_path),
            "preparation_message": message,
        })

    @app.get("/fragments/preparation", response_class=HTMLResponse)
    def preparation_fragment(request: Request) -> HTMLResponse:
        return render_preparation(request)

    @app.post("/preparation/start", response_class=HTMLResponse)
    def preparation_start_form(request: Request,
                               body: Annotated[_PreparationStartForm, Form()]) -> HTMLResponse:
        if not _csrf_valid(body.csrf_token, csrf_token):
            return HTMLResponse("Invalid or missing CSRF token.", status_code=403)
        try:
            state = app.state.preparation_coordinator.start(resolved_config_path)
            message = (state.message if state.phase is PreparationPhase.BLOCKED else None)
        except (ConfigurationError, OSError, ContentChangedError):
            message = "Saved Access Mapping or Institution settings need attention. Review Settings."
        return render_preparation(request, message=message)

    @app.post("/preparation/cancel", response_class=HTMLResponse)
    def preparation_cancel_form(request: Request,
                                body: Annotated[_PreparationCancelForm, Form()]) -> HTMLResponse:
        if not _csrf_valid(body.csrf_token, csrf_token):
            return HTMLResponse("Invalid or missing CSRF token.", status_code=403)
        accepted = app.state.preparation_coordinator.cancel(body.task_id, body.generation)
        return render_preparation(request,
                                  message=None if accepted else "This Preparation task is no longer active; refresh its status.")

    @app.post("/imports/start", response_class=HTMLResponse)
    def import_start(request: Request, body: Annotated[_ImportStartForm, Form()]) -> HTMLResponse:
        if not _csrf_valid(body.csrf_token, csrf_token):
            return HTMLResponse("Invalid or missing CSRF token.", status_code=403)
        with import_submission_lock:
            try:
                target, _ = _ImportTarget.read(resolved_config_path)
            except (ConfigurationError, ContentChangedError, OSError):
                return render_import_workspace(request, message="Configuration needs attention before import.", status_code=400)
            if not _csrf_valid(body.import_token, target.form_token(app.state.import_token)):
                return render_import_workspace(request, message="Workspace changed or import request already submitted/expired. Review the current Workspace before importing.", status_code=409)
            # 消耗授权和启动保持串行；worker 持锁后再次复核，绝不按后来的配置改投其他 Workspace。
            app.state.import_token = secrets.token_urlsafe(32)
            snapshot = app.state.batch_import.start(target.workspace, validate_target=target.verify)
            if snapshot.plan is None and snapshot.phase is BatchPhase.BLOCKED:
                return render_import_workspace(request, message=snapshot.message, status_code=409)
        return render_import_workspace(request)

    @app.post("/imports/poll", response_class=HTMLResponse)
    def import_poll(request: Request, body: Annotated[_ImportPollForm, Form()]) -> HTMLResponse:
        if not _csrf_valid(body.csrf_token, csrf_token):
            return HTMLResponse("Invalid or missing CSRF token.", status_code=403)
        # Only snapshot() is read by presentation; A4 owns completion delivery.
        return render_import_workspace(request, view=body.view, paper=body.paper)

    @app.get("/fragments/workspace", response_class=HTMLResponse)
    def workspace_fragment(
        request: Request,
        view: str = "inbox",
        paper: UUID | None = None,
    ) -> HTMLResponse:
        context = _workspace_context(
            request=request,
            config_path=resolved_config_path,
            csrf_token=csrf_token,
            view=view,
            selected_paper_id=paper,
            capture_snapshot=app.state.capture_coordinator.snapshot(),
        )
        return templates.TemplateResponse(
            request,
            "fragments/workspace.html",
            context,
        )

    @app.get("/fragments/papers", response_class=HTMLResponse)
    def paper_list_fragment(
        request: Request,
        view: str = "inbox",
    ) -> HTMLResponse:
        context = _workspace_context(
            request=request,
            config_path=resolved_config_path,
            csrf_token=csrf_token,
            view=view,
            capture_snapshot=app.state.capture_coordinator.snapshot(),
        )
        return templates.TemplateResponse(
            request,
            "fragments/paper_list.html",
            context,
        )

    @app.get("/fragments/papers/{paper_id}", response_class=HTMLResponse)
    def paper_detail_fragment(
        request: Request,
        paper_id: UUID,
        view: str = "inbox",
    ) -> HTMLResponse:
        context = _workspace_context(
            request=request,
            config_path=resolved_config_path,
            csrf_token=csrf_token,
            view=view,
            selected_paper_id=paper_id,
            capture_snapshot=app.state.capture_coordinator.snapshot(),
        )
        if context["selected_paper"] is None:
            context["detail_message"] = "Paper not found in the current view."
        return templates.TemplateResponse(
            request,
            "fragments/paper_detail.html",
            context,
        )

    @app.get("/fragments/workspace-health", response_class=HTMLResponse)
    def workspace_health_fragment(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "fragments/workspace_health.html",
            _workspace_health_context(resolved_config_path),
        )

    def prepare_paper_mutation(
        request: Request,
        *,
        paper_id: UUID,
        expected_status_value: str | None,
        submitted_csrf: str | None,
        view: str,
    ) -> tuple[Path | None, WorkflowStatus | None, HTMLResponse | None]:
        if not _csrf_valid(submitted_csrf, csrf_token):
            return (
                None,
                None,
                HTMLResponse(
                    '<p class="notice error">Invalid or missing CSRF token.</p>',
                    status_code=403,
                ),
            )

        output_dir, config_error = _resolve_output_dir(resolved_config_path)
        if config_error is not None or output_dir is None:
            context = _workspace_context(
                request=request,
                config_path=resolved_config_path,
                csrf_token=csrf_token,
                view=view,
                selected_paper_id=paper_id,
                decision_message=(
                    "Configuration needs attention before decisions can be saved."
                ),
                capture_snapshot=app.state.capture_coordinator.snapshot(),
            )
            return (
                None,
                None,
                templates.TemplateResponse(
                    request,
                    "fragments/workspace.html",
                    context,
                ),
            )

        try:
            expected_status = WorkflowStatus(expected_status_value)
        except (TypeError, ValueError):
            context = _workspace_context(
                request=request,
                config_path=resolved_config_path,
                csrf_token=csrf_token,
                view=view,
                selected_paper_id=paper_id,
                decision_message="The submitted expected Paper status is invalid.",
                capture_snapshot=app.state.capture_coordinator.snapshot(),
            )
            return (
                None,
                None,
                templates.TemplateResponse(
                    request,
                    "fragments/workspace.html",
                    context,
                    status_code=400,
                ),
            )
        return output_dir, expected_status, None

    def apply_decision(
        request: Request,
        *,
        paper_id: UUID,
        expected_status_value: str | None,
        submitted_csrf: str | None,
        view: str,
        navigation_position: str | None,
        action: Callable[[Path, UUID, WorkflowStatus], DecisionResult],
    ) -> HTMLResponse:
        output_dir, expected_status, failure = prepare_paper_mutation(
            request,
            paper_id=paper_id,
            expected_status_value=expected_status_value,
            submitted_csrf=submitted_csrf,
            view=view,
        )
        if failure is not None:
            return failure
        assert output_dir is not None
        assert expected_status is not None

        result = action(output_dir, paper_id, expected_status)
        context = _workspace_context(
            request=request,
            config_path=resolved_config_path,
            csrf_token=csrf_token,
            view=view,
            selected_paper_id=paper_id,
            decision_result=result,
            navigation_position=navigation_position,
            capture_snapshot=app.state.capture_coordinator.snapshot(),
        )
        return templates.TemplateResponse(
            request,
            "fragments/workspace.html",
            context,
        )

    def render_capture_status(
        request: Request,
        *,
        snapshot: CaptureSnapshot,
        view: str,
        selected_paper_id: UUID | None,
        navigation_position: str | None,
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "fragments/capture_status.html",
            {
                "request": request,
                "csrf_token": csrf_token,
                "capture_presentation": _capture_presentation(
                    snapshot,
                    selected_paper_id,
                ),
                "active_view": view if view in _VIEW_STATUSES else "inbox",
                "selected_paper_id": selected_paper_id,
                "selected_position": navigation_position,
            },
        )

    @app.post("/capture/poll", response_class=HTMLResponse)
    def capture_poll(
        request: Request,
        csrf_token_value: Annotated[str | None, Form(alias="csrf_token")] = None,
        view: Annotated[str, Form()] = "kept",
        position: Annotated[str | None, Form()] = None,
        paper: Annotated[UUID | None, Form()] = None,
    ) -> HTMLResponse:
        if not _csrf_valid(csrf_token_value, csrf_token):
            return HTMLResponse(
                '<p class="notice error">Invalid or missing CSRF token.</p>',
                status_code=403,
            )

        snapshot = app.state.capture_coordinator.snapshot()
        if app.state.batch_import.running or not snapshot.completion_pending:
            return render_capture_status(
                request,
                snapshot=snapshot,
                view=view,
                selected_paper_id=paper,
                navigation_position=position,
            )

        completion_result = process_capture_completion(
            app.state.capture_coordinator,
        )
        if completion_result.result is None:
            return render_capture_status(request, snapshot=app.state.capture_coordinator.snapshot(),
                                         view=view, selected_paper_id=paper, navigation_position=position)
        result = completion_result.result
        selected_paper_id = paper
        decision_result = None
        decision_message = result.message
        decision_message_tone = "success" if result.outcome is ExportCompletionOutcome.EXPORTED else "warning"
        navigation_position = position
        context = _workspace_context(
            request=request,
            config_path=resolved_config_path,
            csrf_token=csrf_token,
            view=view,
            selected_paper_id=selected_paper_id,
            decision_result=decision_result,
            decision_message=decision_message,
            decision_message_tone=decision_message_tone,
            navigation_position=navigation_position,
            capture_snapshot=app.state.capture_coordinator.snapshot(),
        )
        response = templates.TemplateResponse(
            request,
            "fragments/workspace.html",
            context,
        )
        response.headers["HX-Retarget"] = "#workspace-root"
        response.headers["HX-Reswap"] = "outerHTML"
        return response

    @app.post("/papers/{paper_id}/save-to-zotero", response_class=HTMLResponse)
    def save_to_zotero_route(
        request: Request,
        paper_id: UUID,
        expected_status: Annotated[str | None, Form()] = None,
        csrf_token_value: Annotated[str | None, Form(alias="csrf_token")] = None,
        view: Annotated[str, Form()] = "kept",
        position: Annotated[str | None, Form()] = None,
    ) -> HTMLResponse:
        if not _csrf_valid(csrf_token_value, csrf_token):
            return HTMLResponse("Invalid or missing CSRF token.", status_code=403)
        return HTMLResponse("Legacy per-Paper Zotero saving is retired. Use Import to Zotero on Kept.", status_code=410)

    @app.post("/papers/{paper_id}/keep", response_class=HTMLResponse)
    def keep_route(
        request: Request,
        paper_id: UUID,
        expected_status: Annotated[str | None, Form()] = None,
        csrf_token_value: Annotated[str | None, Form(alias="csrf_token")] = None,
        view: Annotated[str, Form()] = "inbox",
        position: Annotated[str | None, Form()] = None,
    ) -> HTMLResponse:
        return apply_decision(
            request,
            paper_id=paper_id,
            expected_status_value=expected_status,
            submitted_csrf=csrf_token_value,
            view=view,
            navigation_position=position,
            action=keep_paper,
        )

    @app.post("/papers/{paper_id}/reject", response_class=HTMLResponse)
    def reject_route(
        request: Request,
        paper_id: UUID,
        expected_status: Annotated[str | None, Form()] = None,
        csrf_token_value: Annotated[str | None, Form(alias="csrf_token")] = None,
        view: Annotated[str, Form()] = "inbox",
        position: Annotated[str | None, Form()] = None,
    ) -> HTMLResponse:
        return apply_decision(
            request,
            paper_id=paper_id,
            expected_status_value=expected_status,
            submitted_csrf=csrf_token_value,
            view=view,
            navigation_position=position,
            action=reject_paper,
        )

    @app.post("/papers/{paper_id}/check-zotero", response_class=HTMLResponse)
    def check_zotero_route(
        request: Request,
        paper_id: UUID,
        expected_status: Annotated[str | None, Form()] = None,
        csrf_token_value: Annotated[str | None, Form(alias="csrf_token")] = None,
        view: Annotated[str, Form()] = "kept",
        position: Annotated[str | None, Form()] = None,
    ) -> HTMLResponse:
        if not _csrf_valid(csrf_token_value, csrf_token):
            return HTMLResponse("Invalid or missing CSRF token.", status_code=403)
        return HTMLResponse("Zotero reconciliation is retired. Exported records are historical parent-save outcomes.", status_code=410)

    return app
