"""FastAPI adapter for local Workspace review."""

from __future__ import annotations

import hashlib
import hmac
import base64
import binascii
import os
import secrets
import threading
import httpx
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.datastructures import UploadFile
from starlette.concurrency import run_in_threadpool

from literature_monitor.application.journal_covers import (
    MAX_IMAGE_BYTES, _saved_journal, cover_details, cover_image,
)

from literature_monitor.application.workspace_reset import ResetRefused, preview_reset, execute_reset
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
from literature_monitor.config import ConfigurationError, LoadedConfig, load_config, parse_settings_list_text
from literature_monitor.application.full_markdown_import import (
    MAX_MARKDOWN_BYTES, MarkdownImportCommittedWarning, preview_full_markdown, confirm_full_markdown,
)
from literature_monitor.application.ai_mapping_pack import generate_ai_mapping_pack, MappingPackError
from literature_monitor.web.journal_covers import COVER_CAPABILITY, MAX_IMAGE_BASE64, JournalCoverSearch
from literature_monitor.application.access_service_migration import (
    AccessSchemaUpgradeCommittedWarning, AccessSchemaUpgradeStateUncertain,
    preview_access_schema_upgrade, confirm_access_schema_upgrade,
)
from literature_monitor.openalex import OpenAlexError
from literature_monitor.safe_write import CompareReadError
from literature_monitor.safe_write import ContentChangedError, WorkspaceOperationLock
from literature_monitor.models import WorkflowStatus
from literature_monitor.zotero_local import ZoteroLocalClient
from literature_monitor.web.capture_coordinator import (
    CONNECTOR_VERSION_MAX_LENGTH,
    REQUEST_ID_MAX_LENGTH,
    CaptureCoordinator,
    CaptureFailureStage,
    CaptureOutcome,
    CaptureSnapshot,
    PdfOutcome,
    SaveInvocation,
)
from literature_monitor.web.paper_enrichment import PaperEnrichment, ENRICHMENT_CAPABILITY
from literature_monitor.web.run_coordinator import CoordinatorStatus, RunCoordinator
from literature_monitor.web.settings_api import install_settings_api, settings_response, settings_error
from literature_monitor.web.settings_form import (
    SettingsFormValues,
    settings_draft_from_form,
    settings_form_from_draft,
    settings_form_from_submission,
)
from literature_monitor.application.batch_import import BatchImportService, BatchPhase
from literature_monitor.web.preparation_coordinator import (
    PreparationCoordinator, PreparationOutcome, PreparationSnapshot, VisitEvidence,
)
from literature_monitor.application.access import AccessObservation
from literature_monitor.web.kept_workflow import KeptWorkflow
from literature_monitor.web.ui_next import install_ui_next, ui_next_page

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_ALLOWED_HOSTS = ["localhost", "127.0.0.1"]


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


class _CoverIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=REQUEST_ID_MAX_LENGTH)


class _CoverResult(_CoverIdentity):
    issn_l: str
    source_url: str | None = None
    image_base64: str | None = Field(default=None, max_length=MAX_IMAGE_BASE64)


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


class _PreparationWaitingBody(_PreparationIdentity):
    waiting: StrictBool


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


class _ImportStartForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csrf_token: str | None = None
    import_token: str | None = None


class _WorkflowStart(_ImportStartForm):
    mode: Literal["full", "import_only"] = "full"


class _WorkflowAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csrf_token: str | None = None
    workflow_id: str
    generation: int


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


def _zotero_integration_state(
    capture_snapshot: CaptureSnapshot,
) -> dict[str, object]:
    with ZoteroLocalClient() as local:
        instance = local.current_instance()
    return {
        "zotero_instance": instance,
        "connector_readiness": capture_snapshot.readiness.value,
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


def _settings_result_context(
    *,
    csrf_token: str,
    form_values: SettingsFormValues,
    issues: tuple[SettingsIssue, ...] = (),
    validation_result: SettingsValidationResult | None = None,
    save_result: SettingsSaveResult | None = None,
    disk_state: SettingsLoadResult | None = None,
    message: str | None = None,
    deletion_plan: SettingsDeletionPlan | None = None,
    deletion_proof: str = "",
    markdown_plan=None,
    markdown_contents: str = "",
    markdown_filename: str = "",
    markdown_proof: str = "",
    markdown_dirty: bool = False,
    markdown_export_guard: bool = False,
    markdown_reload_guard: bool = False,
    access_upgrade_plan=None,
    access_upgrade_proof: str = "",
    access_upgrade_dirty: bool = False,
) -> dict[str, object]:
    return {
        "csrf_token": csrf_token,
        "settings_form": form_values,
        "settings_issues": issues,
        "settings_validation": validation_result,
        "settings_save": save_result,
        "settings_disk_state": disk_state,
        "settings_message": message,
        "settings_deletion_plan": deletion_plan,
        "settings_deletion_proof": deletion_proof,
        "markdown_plan": markdown_plan,
        "markdown_payload": base64.b64encode(markdown_contents.encode("utf-8")).decode("ascii"),
        "markdown_filename": markdown_filename,
        "markdown_proof": markdown_proof,
        "markdown_dirty": markdown_dirty,
        "markdown_export_guard": markdown_export_guard,
        "markdown_reload_guard": markdown_reload_guard,
        "access_upgrade_plan": access_upgrade_plan,
        "access_upgrade_proof": access_upgrade_proof,
        "access_upgrade_dirty": access_upgrade_dirty,
    }


def create_app(config_path: Path) -> FastAPI:
    """Create the local Web adapter without requiring valid configuration."""

    resolved_config_path = config_path.resolve()
    csrf_token = secrets.token_urlsafe(32)
    deletion_key = secrets.token_bytes(32)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            yield
        finally:
            application.state.kept_workflow.shutdown()

    app = FastAPI(
        lifespan=lifespan,
        title="Literature Monitor",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.config_path = resolved_config_path
    app.state.csrf_token = csrf_token
    app.state.run_coordinator = RunCoordinator(resolved_config_path)
    app.state.capture_coordinator = CaptureCoordinator()
    app.state.background_task_windows = False
    app.state.paper_enrichment = PaperEnrichment(app.state.capture_coordinator)
    app.state.journal_cover_search = JournalCoverSearch(app.state.capture_coordinator)
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

    def has_kept(target: _ImportTarget) -> bool:
        current, config = _ImportTarget.read(target.config_path)
        if target.lock_identity is None:
            current = replace(current, lock_identity=None)
        if current != target:
            raise ContentChangedError("Workspace or configuration changed; start a new explicit workflow.")
        workspace = load_workspace(target.workspace, journals=config.journals)
        return bool(workspace.papers_for(WorkflowStatus.KEPT))

    def begin_import(target: _ImportTarget, authorization: str):
        # The caller holds import_submission_lock, including automatic transitions.
        current, _ = _ImportTarget.read(resolved_config_path)
        if authorization != app.state.import_token or current != target:
            raise ContentChangedError("Workspace changed or import request already submitted/expired. Review the current Workspace before importing.")
        if not has_kept(target):
            raise ContentChangedError("No Kept Papers to import.")
        app.state.import_token = secrets.token_urlsafe(32)
        return app.state.batch_import.start(target.workspace, validate_target=target.verify)

    app.state.kept_workflow = KeptWorkflow(
        app.state.preparation_coordinator, app.state.batch_import, import_submission_lock,
        begin_import=begin_import, has_kept=has_kept,
    )

    def start_workflow(import_token: str | None, mode: str):
        with import_submission_lock:
            target, config = _ImportTarget.read(resolved_config_path)
            if not _csrf_valid(import_token, target.form_token(app.state.import_token)):
                raise ContentChangedError("Workspace changed or import request already submitted/expired. Review the current Workspace before importing.")
            workflow = app.state.kept_workflow
            if workflow.snapshot().phase == "importing" and app.state.batch_import.snapshot().phase is not BatchPhase.RUNNING:
                workflow.advance()
            if workflow.snapshot().active or app.state.batch_import.running:
                raise ContentChangedError("A Kept workflow or Import is already active.")
            # Reserve the signed click for this workflow; rendering never rotates the nonce.
            app.state.import_token = secrets.token_urlsafe(32)
            return app.state.kept_workflow.start(target, config, app.state.import_token, mode=mode)

    def workflow_json():
        workflow = app.state.kept_workflow.snapshot()
        batch = app.state.batch_import.snapshot()
        return {
            **jsonable_encoder(workflow), "active": workflow.active,
            "can_continue": workflow.can_continue, "can_cancel": workflow.can_cancel,
            "preparation": _preparation_json(app.state.preparation_coordinator.snapshot()),
            "batch": ({"phase": batch.phase.value, "successful": batch.successful,
                       "total": len(batch.plan.papers) if batch.plan else 0,
                       "current_paper_id": str(batch.current_paper_id) if batch.current_paper_id else None,
                       "statistics": jsonable_encoder(batch.statistics),
                       "results": jsonable_encoder(batch.results), "message": batch.message}
                      if workflow.import_started else None),
        }

    @app.get("/api/kept-workflow/state")
    def workflow_state() -> JSONResponse:
        return JSONResponse(workflow_json())

    @app.post("/api/kept-workflow/start")
    def workflow_start(body: _WorkflowStart) -> JSONResponse:
        if not _csrf_valid(body.csrf_token, csrf_token):
            return JSONResponse({"message": "Invalid or missing CSRF token."}, status_code=403)
        with import_submission_lock:
            if not app.state.kept_workflow.snapshot().active:
                try:
                    start_workflow(body.import_token, body.mode)
                except (ConfigurationError, ContentChangedError, OSError) as error:
                    return JSONResponse({"message": str(error)}, status_code=409)
            state = workflow_json()
            return JSONResponse(state, status_code=409 if state["phase"] == "interrupted" else 200)

    @app.post("/api/kept-workflow/{action}")
    def workflow_action(action: Literal["continue", "cancel", "acknowledge"],
                        body: _WorkflowAction) -> JSONResponse:
        if not _csrf_valid(body.csrf_token, csrf_token):
            return JSONResponse({"message": "Invalid or missing CSRF token."}, status_code=403)
        method = {"continue": app.state.kept_workflow.continue_import,
                  "cancel": app.state.kept_workflow.cancel,
                  "acknowledge": app.state.kept_workflow.acknowledge_navigation}[action]
        accepted = method(body.workflow_id, body.generation)
        return JSONResponse(workflow_json(), status_code=200 if accepted else 409)

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
                covers_capable=COVER_CAPABILITY in body.capabilities,
            )
        except ValueError:
            return JSONResponse({"accepted": False}, status_code=422)
        app.state.background_task_windows = "background-task-windows/v1" in body.capabilities
        return JSONResponse({"readiness": readiness.value})

    @app.post("/api/connector/covers/claim")
    def cover_claim(body: _ConnectorClaimBody):
        return JSONResponse({'command': app.state.journal_cover_search.claim()})

    @app.post("/api/connector/covers/authorize")
    def cover_authorize(body: _CoverIdentity):
        accepted = app.state.journal_cover_search.authorize(body.request_id)
        return JSONResponse({'accepted': accepted}, status_code=200 if accepted else 409)

    @app.post("/api/connector/covers/result")
    def cover_result(body: _CoverResult):
        result = app.state.journal_cover_search.result(body.request_id, body.issn_l, body.source_url, body.image_base64)
        return JSONResponse(result or {'accepted': False}, status_code=200 if result else 409)

    @app.post("/api/connector/covers/finish")
    def cover_finish(body: _CoverIdentity):
        accepted = app.state.journal_cover_search.finish(body.request_id)
        return JSONResponse({'accepted': accepted}, status_code=200 if accepted else 409)

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
            load_config(resolved_config_path).output_dir,
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
            if action != 'start':
                return JSONResponse({'error': 'Unknown action'}, status_code=404)
            workspace = load_config(resolved_config_path).output_dir
            state = service.start(workspace, paper_id)
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
        workflow = app.state.kept_workflow.snapshot()
        return JSONResponse({"command": None if command is None else {
            "kind": command.kind, "task_id": command.task_id,
            "generation": command.generation, "request_id": command.request_id,
            "service_id": command.service_id, "name": command.name,
            "access_url": command.access_url, "login_url": command.login_url,
            "last_service": command.last_service,
            "automatic_attribute_release_consent": command.automatic_attribute_release_consent,
            **({"window_context": (
                f"kept:{workflow.workflow_id}:{workflow.generation}"
                if workflow.active and (workflow.preparation_task_id, workflow.preparation_generation)
                == (command.task_id, command.generation)
                else f"prepare:{command.task_id}:{command.generation}"
            )} if app.state.background_task_windows else {}),
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


    @app.post("/api/connector/claim")
    def connector_claim(body: _ConnectorClaimBody) -> JSONResponse:
        command = app.state.capture_coordinator.claim_command()
        workflow = app.state.kept_workflow.snapshot()
        return JSONResponse(
            {
                **({"import_running": app.state.batch_import.running} if app.state.background_task_windows else {}),
                "command": (
                    None
                    if command is None
                    else {
                        "request_id": command.request_id,
                        "doi_url": command.doi_url,
                        "invocation_id": command.invocation_id,
                        **({"access_context": command.access_context} if command.access_context else {}),
                        **({"window_context": (
                            f"kept:{workflow.workflow_id}:{workflow.generation}"
                            if workflow.active and workflow.import_started
                            else f"import:{command.access_context or command.request_id}"
                        )} if app.state.background_task_windows else {}),
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

    @app.get("/", include_in_schema=False)
    def workspace_page():
        return ui_next_page()

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

    @app.post("/settings/workspace-reset", response_class=JSONResponse)
    async def reset_workspace_route(request: Request) -> JSONResponse:
        form = await request.form()
        csrf = form.get("csrf_token")
        if not _csrf_valid(str(csrf) if csrf is not None else None, csrf_token):
            return settings_error("Invalid or missing CSRF token.")
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
        return JSONResponse(jsonable_encoder({
            "reset_plan": None, "reset_token": None,
            "reset_error": result_message, "reset_success": status == 200,
            "reset_result": reset_result,
        }), status_code=status, headers={"Cache-Control": "no-store"})

    def markdown_reply(request: Request, values: SettingsFormValues, *, message: str,
                       plan=None, contents: str = "", filename: str = "",
                       proof: str = "", dirty: bool = False, issues=(),
                       export_guard: bool = False,
                       reload_guard: bool = False,
                       upgrade_plan=None, upgrade_proof: str = "",
                       upgrade_dirty: bool = False, saved: bool = False) -> JSONResponse:
        return settings_response(request,
            _settings_result_context(csrf_token=csrf_token, form_values=values,
                issues=issues, message=message, markdown_plan=plan,
                markdown_contents=contents, markdown_filename=filename,
                markdown_proof=proof, markdown_dirty=dirty,
                markdown_export_guard=export_guard,
                markdown_reload_guard=reload_guard,
                access_upgrade_plan=upgrade_plan, access_upgrade_proof=upgrade_proof,
                access_upgrade_dirty=upgrade_dirty), saved=saved)

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

    @app.get("/settings/covers/{issn_l}/image")
    def local_journal_cover(issn_l: str) -> Response:
        disk = load_settings(resolved_config_path)
        try:
            _saved_journal(disk.journal_path, issn_l)
            found = cover_image(disk.journal_path, issn_l)
        except (OSError, ValueError, ConfigurationError):
            found = None
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
        if found is None:
            return Response(status_code=404, headers=headers)
        data, content_type = found
        return Response(content=data, media_type=content_type, headers=headers)

    @app.get("/settings/covers/search")
    def cover_search_state():
        return JSONResponse(app.state.journal_cover_search.state())

    @app.post("/settings/covers/search")
    async def start_cover_search(request: Request):
        form = await request.form()
        if not require_settings_csrf(form):
            return JSONResponse({'message': 'Invalid CSRF token'}, status_code=403)
        disk = load_settings(resolved_config_path)
        try:
            state = app.state.journal_cover_search.start(disk.journal_path, disk.draft.journals,
                                                        str(form.get('issn_l', '')) or None)
            return JSONResponse(state)
        except (ValueError, OSError) as error:
            return JSONResponse({'message': str(error)}, status_code=409)

    @app.post("/settings/covers/search/cancel")
    async def cancel_cover_search(request: Request):
        form = await request.form()
        if not require_settings_csrf(form):
            return JSONResponse({'message': 'Invalid CSRF token'}, status_code=403)
        accepted = app.state.journal_cover_search.cancel(str(form.get('request_id', '')))
        return JSONResponse({'accepted': accepted}, status_code=200 if accepted else 409)

    @app.post("/settings/covers/search/continue")
    @app.post("/settings/covers/search/retry")
    async def resume_cover_search(request: Request):
        form = await request.form()
        if not require_settings_csrf(form):
            return JSONResponse({'message': 'Invalid CSRF token'}, status_code=403)
        try:
            state = app.state.journal_cover_search.resume(str(form.get('request_id', '')),
                                                        retry=request.url.path.endswith('/retry'))
            return JSONResponse(state)
        except ValueError as error:
            return JSONResponse({'message': str(error)}, status_code=409)

    @app.post("/settings/covers/reset")
    async def reset_journal_covers(request: Request):
        form = await request.form()
        if not require_settings_csrf(form):
            return JSONResponse({'message': 'Invalid CSRF token'}, status_code=403)
        disk = load_settings(resolved_config_path)
        try:
            state = await run_in_threadpool(app.state.journal_cover_search.reset, disk.journal_path)
            return JSONResponse(state)
        except (ValueError, OSError) as error:
            return JSONResponse({'message': str(error)}, status_code=409)

    @app.post("/settings/covers/{issn_l}/upload")
    async def upload_journal_cover(request: Request, issn_l: str) -> JSONResponse:
        if app.state.journal_cover_search.state()["busy"]:
            return JSONResponse({"ok": False, "message": "Cover search is running"}, status_code=409)
        async with request.form() as form:
            if not require_settings_csrf(form):
                return JSONResponse({"ok": False, "message": "Invalid CSRF token"}, status_code=403)
            upload = form.get("file")
            if not isinstance(upload, UploadFile):
                return JSONResponse({"ok": False, "message": "Choose an image file"}, status_code=400)
            data = await upload.read(MAX_IMAGE_BYTES + 1)
        disk = load_settings(resolved_config_path)
        try:
            outcome = await run_in_threadpool(app.state.journal_cover_search.upload, disk.journal_path, issn_l, data)
            state = await run_in_threadpool(cover_details, disk.journal_path, issn_l)
            return JSONResponse({"ok": True, "result": outcome, "state": state["state"]})
        except (OSError, ValueError, ConfigurationError) as error:
            return JSONResponse({"ok": False, "message": str(error)}, status_code=400)

    @app.post("/settings/upgrade/preview", response_class=JSONResponse)
    async def preview_access_upgrade(request: Request) -> JSONResponse:
        form = await request.form()
        if not require_settings_csrf(form):
            return settings_error("Invalid or missing CSRF token.")
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

    @app.post("/settings/upgrade/confirm", response_class=JSONResponse)
    async def confirm_access_upgrade(request: Request) -> JSONResponse:
        form = await request.form()
        if not require_settings_csrf(form):
            return settings_error("Invalid or missing CSRF token.")
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
                                      issues=saved.issues, saved=True)
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
                saved=True,
            )
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

    @app.post("/settings/import/preview", response_class=JSONResponse)
    async def preview_markdown_import(request: Request) -> JSONResponse:
        form = await request.form()
        if not require_settings_csrf(form):
            return settings_error("Invalid or missing CSRF token.")
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

    @app.post("/settings/import/confirm", response_class=JSONResponse)
    async def confirm_markdown_import(request: Request) -> JSONResponse:
        form = await request.form()
        if not require_settings_csrf(form):
            return settings_error("Invalid or missing CSRF token.")
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
                message=f"Full Markdown Import saved to list.md. New revision: {digest}", saved=True)
            return response
        except MarkdownImportCommittedWarning as warning:
            saved = load_settings(resolved_config_path)
            response = markdown_reply(
                request, settings_form_from_draft(saved.draft),
                message=f"Import reached list.md but directory synchronization failed. Actual saved revision: {warning.revision}. Verify storage health.",
                issues=saved.issues, saved=True,
            )
            return response
        except (ConfigurationError, ContentChangedError, CompareReadError, OSError, UnicodeError, ValueError, OpenAlexError) as error:
            return markdown_reply(request, values, message=f"Import not written: {error}", issues=issues)

    @app.post("/settings/export", response_class=JSONResponse)
    async def export_markdown(request: Request):
        form = await request.form()
        if not require_settings_csrf(form):
            return settings_error("Invalid or missing CSRF token.")
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

    @app.post("/settings/export-ai-mapping-pack", response_class=JSONResponse)
    async def export_ai_mapping_pack(request: Request):
        form = await request.form()
        if not require_settings_csrf(form):
            return settings_error("Invalid or missing CSRF token.")
        values, draft, issues, disk, dirty = submitted_settings(form)
        if dirty and form.get("export_discard") != "yes":
            return markdown_reply(
                request, values, message="Unsaved Settings Draft: Save or explicitly Discard before AI Mapping Pack export.",
                issues=issues, export_guard=True,
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

    @app.post("/settings/reload", response_class=JSONResponse)
    async def reload_markdown(request: Request) -> JSONResponse:
        form = await request.form()
        if not require_settings_csrf(form):
            return settings_error("Invalid or missing CSRF token.")
        values, draft, issues, disk, dirty = submitted_settings(form)
        if form.get("reload_step") != "confirm" or form.get("reload_word") != "RELOAD":
            return markdown_reply(request, values,
                message="Reload discards unsaved Journals, Groups, Mapping, Monitor, Institution and Kept Workflow edits. Confirm explicitly.",
                reload_guard=True)
        saved = load_settings(resolved_config_path)
        response = markdown_reply(request, settings_form_from_draft(saved.draft),
            message="Reloaded both Settings files from disk. Prior unsaved edits discarded.", issues=saved.issues, saved=True)
        return response

    @app.post("/settings/save", response_class=JSONResponse)
    async def save_settings_route(request: Request) -> JSONResponse:
        form = await request.form()
        submitted_csrf = form.get("csrf_token")
        if not _csrf_valid(
            str(submitted_csrf) if submitted_csrf is not None else None,
            csrf_token,
        ):
            return settings_error("Invalid or missing CSRF token.")

        values = settings_form_from_submission(form)
        draft, form_issues = settings_draft_from_form(values)
        if draft is None:
            return settings_response(request,
                _settings_result_context(
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
            return settings_response(request,
                _settings_result_context(
                    csrf_token=csrf_token, form_values=values,
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
            response = settings_response(request,
                _settings_result_context(
                    csrf_token=csrf_token,
                    form_values=settings_form_from_draft(result.state.draft),
                    issues=result.issues,
                    validation_result=result.validation,
                    save_result=result,
                    disk_state=result.state,
                    message="Settings saved.",
                ), saved=True,
            )
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

        return settings_response(request,
            _settings_result_context(
                csrf_token=csrf_token,
                form_values=form_values,
                issues=result.issues,
                validation_result=result.validation,
                save_result=result,
                disk_state=result.state,
                message=message,
            ),
        )

    install_settings_api(app, resolved_config_path, csrf_token,
                         workspace_health=_workspace_health_context,
                         zotero_integration=_zotero_integration_state,
                         reset_preview=reset_preview_context)
    install_ui_next(app, resolved_config_path, csrf_token, read_target=_ImportTarget.read)
    return app
