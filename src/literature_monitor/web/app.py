"""FastAPI adapter for local Workspace review."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import ClientDisconnect

from literature_monitor.application.decisions import (
    DecisionOutcome,
    DecisionResult,
    keep_paper,
    mark_paper_in_zotero,
    reject_paper,
)
from literature_monitor.application.acquisition import (
    AcquisitionRecovery, AcquisitionService, AcquisitionStage, AuthorizationRetryBoundary,
)
from literature_monitor.application.journal_import import (
    JournalImportMode,
    apply_journal_import,
    preview_journal_import,
)
from literature_monitor.application.settings import (
    SettingsIssue,
    SettingsLoadResult,
    SettingsSaveOutcome,
    SettingsSaveResult,
    SettingsValidationOutcome,
    SettingsValidationResult,
    load_settings,
    save_settings,
    validate_settings,
)
from literature_monitor.application.workspace import (
    WorkspacePaper,
    WorkspaceSnapshot,
    load_workspace,
)
from literature_monitor.config import ConfigurationError, load_config
from literature_monitor.identifiers import normalize_doi
from literature_monitor.models import WorkflowStatus
from literature_monitor.zotero_credentials import ZoteroAuthorizationRuntime
from literature_monitor.zotero_local import ZoteroLocalClient, ZoteroReadOutcome
from literature_monitor.zotero_write import ZoteroAuthorizationClient
from literature_monitor.web.acquisition_coordinator import (
    AcquisitionActionOutcome, AcquisitionCoordinator, AcquisitionCoordinatorStatus, AcquisitionSnapshot, AcquisitionStartOutcome,
)
from literature_monitor.web.browser_handoff import (
    BrowserHandoffRegistry, MAX_MESSAGE_BYTES, parse_handoff_message,
)
from literature_monitor.web.chrome_launcher import launch_normal_chrome
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
    SettingsImportValues,
    settings_draft_from_form,
    settings_form_after_import,
    settings_form_from_draft,
    settings_form_from_submission,
)

_PACKAGE_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
_STATIC_DIR = _PACKAGE_DIR / "static"
_ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
_HANDOFF_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                    "X-Content-Type-Options": "nosniff"}
_VIEW_STATUSES = {
    "inbox": WorkflowStatus.CANDIDATE,
    "kept": WorkflowStatus.KEPT,
    "rejected": WorkflowStatus.REJECTED,
    "in-zotero": WorkflowStatus.IN_ZOTERO,
}

templates = Jinja2Templates(directory=_TEMPLATES_DIR)


async def _handoff_message(request: Request) -> dict | None:
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if request.url.query or content_type != "application/json":
        return None
    raw = bytearray()
    try:
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_MESSAGE_BYTES:
                return None
            raw.extend(chunk)
    except ClientDisconnect:
        return None
    return parse_handoff_message(bytes(raw))


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
    navigation_position: str | None = None,
) -> dict[str, object]:
    workspace, config_error = _workspace_state(config_path)
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
    copy_doi = None
    acquisition_eligible = False
    if selected_paper is not None and selected_paper.status is WorkflowStatus.KEPT:
        try:
            copy_doi = normalize_doi(selected_paper.external_ids.doi)
        except ValueError:
            pass
    if selected_paper is not None and selected_paper.status is WorkflowStatus.IN_ZOTERO:
        try:
            acquisition_eligible = normalize_doi(selected_paper.external_ids.doi) is not None
        except ValueError:
            pass
    return {
        "request": request,
        "csrf_token": csrf_token,
        "workspace": workspace,
        "config_error": config_error,
        "active_view": selected_view,
        "papers": view_papers,
        "paper_sections": workspace.sections_for(_VIEW_STATUSES[selected_view]) if workspace else (),
        "selected_paper": selected_paper,
        "copy_doi": copy_doi,
        "acquisition_eligible": acquisition_eligible,
        **_acquisition_context(request, selected_paper.paper_id if selected_paper else None,
                               selected_view, request.app.state.acquisition_coordinator.snapshot()),
        "selected_position": selected_position,
        "selection_stepped": selection_stepped,
        "decision_result": decision_result,
        "decision_message": decision_message,
    }


def _build_acquisition_service(output_dir: Path, config_path: Path, *,
                               authorization_retry_boundary: AuthorizationRetryBoundary | None = None,
                               authorization_runtime: ZoteroAuthorizationRuntime | None = None) -> AcquisitionService:
    return AcquisitionService(output_dir,
                              authorization_retry_boundary=authorization_retry_boundary,
                              authorization_runtime=authorization_runtime)


def _zotero_integration_state(runtime: ZoteroAuthorizationRuntime, *, authorize=False) -> dict[str, object]:
    with ZoteroLocalClient() as local:
        instance = local.current_instance()
    authorization = None
    if instance.outcome is ZoteroReadOutcome.VERIFIED and instance.server_id is not None:
        with ZoteroAuthorizationClient(instance.server_id, authorization_runtime=runtime) as client:
            authorization = client.authorize() if authorize else client.authorization_status()
    return {"zotero_instance": instance, "zotero_authorization": authorization,
            "zotero_retry_remaining": runtime.retry_boundary.remaining()}


def _acquisition_context(
    request: Request, paper_id: UUID | None, view: str, snapshot: AcquisitionSnapshot,
    *, message: str | None = None,
) -> dict[str, object]:
    owns_attempt = paper_id is not None and snapshot.paper_id == paper_id
    stage_labels = {
        AcquisitionStage.LOCATING_ZOTERO: "Verifying Zotero item",
        AcquisitionStage.CHECKING_ATTACHMENT: "Checking existing PDF files",
        AcquisitionStage.RESOLVING: "Finding institutional full text",
        AcquisitionStage.WAITING_FOR_INSTITUTION_AUTH: "Institutional login required",
        AcquisitionStage.DISCOVERING_PDF: "Acquiring and validating PDF",
        AcquisitionStage.OPENING_CHROME: "Opening normal Chrome",
        AcquisitionStage.HANDOFF: "Waiting for the browser companion",
        AcquisitionStage.BROWSER_ACTION: "Publisher/browser action required",
        AcquisitionStage.RESOLVER_CHOICE: "Choose a resolver provider in the Chrome companion",
        AcquisitionStage.VALIDATING_PDF: "Validating downloaded PDF",
        AcquisitionStage.WAITING_FOR_ZOTERO_AUTH: "Zotero authorization required",
        AcquisitionStage.CANCELLED: "PDF acquisition cancelled",
        AcquisitionStage.ATTACHING: "Attaching PDF to Zotero",
        AcquisitionStage.SUCCEEDED: "PDF acquisition finished",
        AcquisitionStage.FAILED: "PDF acquisition stopped",
    }
    recovery_messages = {
        AcquisitionRecovery.CHECK_PAPER: "Check the current Paper, then start a new attempt.",
        AcquisitionRecovery.CHECK_ZOTERO: "Check the item and attachments in Zotero before retrying.",
        AcquisitionRecovery.INSTITUTION_LOGIN: "Complete institutional login or verification in the same normal Chrome task tab.",
        AcquisitionRecovery.ZOTERO_AUTHORIZATION: "Open Settings → Advanced & Diagnostics → Zotero integration.",
        AcquisitionRecovery.CHECK_BROWSER: "Check normal Chrome and its companion installation.",
        AcquisitionRecovery.RETRY: "Start a new attempt when ready.",
        AcquisitionRecovery.RATE_LIMITED: "Wait for Zotero's authorization retry boundary before starting another attempt.",
    }
    result = snapshot.result if owns_attempt else None
    return {
        "request": request,
        "csrf_token": request.app.state.csrf_token,
        "acquisition_paper_id": paper_id,
        "acquisition_view": view if view in _VIEW_STATUSES else "in-zotero",
        "acquisition_busy": snapshot.status is AcquisitionCoordinatorStatus.RUNNING,
        "acquisition_owns_attempt": owns_attempt,
        "acquisition_human_auth": owns_attempt and snapshot.stage is AcquisitionStage.WAITING_FOR_INSTITUTION_AUTH,
        "acquisition_stage_label": stage_labels.get(snapshot.stage) if owns_attempt else None,
        "acquisition_result": result,
        "acquisition_recovery": recovery_messages.get(result.recovery) if result else None,
        "acquisition_message": message,
        "acquisition_unexpected": owns_attempt and snapshot.unexpected_error is not None,
        "acquisition_attempt_id": snapshot.attempt_id if owns_attempt else None,
        "acquisition_cancel_available": owns_attempt and snapshot.cancel_available,
        "acquisition_resume_available": owns_attempt and snapshot.resume_available,
        "acquisition_open_retry_available": owns_attempt and snapshot.open_retry_available,
        "acquisition_zotero_auth": owns_attempt and snapshot.stage is AcquisitionStage.WAITING_FOR_ZOTERO_AUTH,
    }


def _run_context(
    request: Request,
    csrf_token: str,
    snapshot: CoordinatorSnapshot,
    *,
    start_result: StartResult | None = None,
) -> dict[str, object]:
    return {
        "request": request,
        "csrf_token": csrf_token,
        "run_snapshot": snapshot,
        "run_progress": build_run_presentation(snapshot, now=_utc_now()),
        "run_start_result": start_result,
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
    import_values: SettingsImportValues | None = None,
) -> dict[str, object]:
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
        "settings_import": import_values or SettingsImportValues(),
    }


def create_app(config_path: Path) -> FastAPI:
    """Create the local Web adapter without requiring valid configuration."""

    resolved_config_path = config_path.resolve()
    csrf_token = secrets.token_urlsafe(32)
    app = FastAPI(
        title="Literature Monitor",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.config_path = resolved_config_path
    app.state.csrf_token = csrf_token
    app.state.run_coordinator = RunCoordinator(resolved_config_path)
    browser_handoff = BrowserHandoffRegistry()
    app.state.browser_handoff = browser_handoff
    app.state.chrome_launcher = launch_normal_chrome
    app.state.acquisition_coordinator = AcquisitionCoordinator(registry=browser_handoff,
        launcher=lambda launch: app.state.chrome_launcher(launch))
    app.state.browser_handoff_event_handler = app.state.acquisition_coordinator.handle_event
    authorization_retry_boundary = AuthorizationRetryBoundary()
    authorization_runtime = ZoteroAuthorizationRuntime(retry_boundary=authorization_retry_boundary)
    app.state.zotero_authorization = authorization_runtime
    bound_output_dir, bound_service = None, None

    def acquisition_service_for(output_dir: Path) -> AcquisitionService:
        nonlocal bound_output_dir, bound_service
        # Called only for an accepted start under the coordinator lock. Each
        # workspace binding uses the same process-local authorization limit.
        if bound_service is None or bound_output_dir != output_dir:
            service = _build_acquisition_service(output_dir, resolved_config_path,
                authorization_retry_boundary=authorization_retry_boundary,
                authorization_runtime=authorization_runtime)
            bound_output_dir, bound_service = output_dir, service
        return bound_service

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=_ALLOWED_HOSTS,
    )
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/browser-handoff/{task_id}", response_class=HTMLResponse)
    def browser_handoff_page(request: Request, task_id: str) -> HTMLResponse:
        handoff = browser_handoff.status(task_id)
        if handoff is None or request.url.query:
            return HTMLResponse("Browser handoff is unavailable.", status_code=404, headers=_HANDOFF_HEADERS)
        return templates.TemplateResponse(request, "browser_handoff.html", {"handoff": handoff},
            headers={**_HANDOFF_HEADERS, "Content-Security-Policy":
                     "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"})

    @app.post("/browser-handoff/claim")
    async def claim_browser_handoff(request: Request) -> Response:
        message = await _handoff_message(request)
        if message is not None and set(message) == {"task_id", "capability", "tab_binding"}:
            claimed = browser_handoff.claim(**message)
            if claimed is not None:
                return JSONResponse({"event_capability": claimed.event_capability}, headers=_HANDOFF_HEADERS)
        return Response("Browser handoff request rejected.", status_code=403, headers=_HANDOFF_HEADERS)

    @app.post("/browser-handoff/events")
    async def browser_handoff_event(request: Request) -> Response:
        message = await _handoff_message(request)
        if message is not None and set(message) == {"task_id", "capability", "tab_binding", "event_type", "payload"}:
            try:
                event = browser_handoff.receive_event(**message, on_event=app.state.browser_handoff_event_handler)
            except Exception:
                return Response("Browser handoff event could not be handled.", status_code=500, headers=_HANDOFF_HEADERS)
            if event is not None:
                if event.command is not None:
                    return JSONResponse({"command": event.command}, headers=_HANDOFF_HEADERS)
                return Response(status_code=204, headers=_HANDOFF_HEADERS)
        return Response("Browser handoff request rejected.", status_code=403, headers=_HANDOFF_HEADERS)

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
        )
        return templates.TemplateResponse(request, "workspace.html", context)

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
        context["run_snapshot"] = app.state.run_coordinator.snapshot()
        return templates.TemplateResponse(
            request,
            "settings.html",
            context,
        )

    @app.get("/settings/zotero", response_class=HTMLResponse)
    def zotero_integration_status(request: Request) -> HTMLResponse:
        context = {"request": request, "csrf_token": csrf_token,
                   **_zotero_integration_state(authorization_runtime)}
        return templates.TemplateResponse(request, "fragments/zotero_integration.html", context)

    @app.post("/settings/zotero/authorize", response_class=HTMLResponse)
    def authorize_zotero(request: Request, csrf_token: Annotated[str | None, Form()] = None) -> HTMLResponse:
        if not _csrf_valid(csrf_token, app.state.csrf_token):
            return HTMLResponse("Invalid request. Reload Settings and try again.", status_code=403)
        context = {"request": request, "csrf_token": app.state.csrf_token,
                   **_zotero_integration_state(authorization_runtime, authorize=True)}
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

    def acquisition_response(request: Request, paper_id: UUID, view: str,
                             snapshot: AcquisitionSnapshot, *, message: str | None = None,
                             refresh_detail: bool = True) -> HTMLResponse:
        response = templates.TemplateResponse(
            request, "fragments/acquisition.html",
            _acquisition_context(request, paper_id, view, snapshot, message=message),
        )
        if snapshot.status is AcquisitionCoordinatorStatus.FINISHED and refresh_detail:
            response.headers["HX-Trigger-After-Swap"] = "acquisitionCompleted"
        return response

    @app.get("/fragments/acquisition/{paper_id}", response_class=HTMLResponse)
    def acquisition_fragment(request: Request, paper_id: UUID, view: str = "in-zotero") -> HTMLResponse:
        return acquisition_response(request, paper_id, view, app.state.acquisition_coordinator.snapshot())

    @app.post("/papers/{paper_id}/acquire-pdf", response_class=HTMLResponse)
    def start_acquisition(
        request: Request, paper_id: UUID,
        csrf_token_value: Annotated[str | None, Form(alias="csrf_token")] = None,
        view: Annotated[str, Form()] = "in-zotero",
    ) -> HTMLResponse:
        if not _csrf_valid(csrf_token_value, csrf_token):
            return HTMLResponse('<p class="notice error">Invalid or missing CSRF token.</p>', status_code=403)
        output_dir, config_error = _resolve_output_dir(resolved_config_path)
        if config_error is not None or output_dir is None:
            return acquisition_response(request, paper_id, view, app.state.acquisition_coordinator.snapshot(),
                message="Configuration needs attention. Open Settings before starting PDF acquisition.", refresh_detail=False)
        start = app.state.acquisition_coordinator.start(
            paper_id, service_factory=lambda: acquisition_service_for(output_dir),
            port=request.url.port or 80,
            staging_options={"project_dir": _PACKAGE_DIR.parents[2], "workspace_dir": output_dir,
                             "config_path": resolved_config_path},
        )
        message = "Another PDF acquisition is already in progress." if start.outcome is AcquisitionStartOutcome.ALREADY_RUNNING else None
        return acquisition_response(request, paper_id, view, app.state.acquisition_coordinator.snapshot(), message=message)

    @app.post("/papers/{paper_id}/acquisition/{action}", response_class=HTMLResponse)
    def acquisition_action(request: Request, paper_id: UUID, action: str,
        csrf_token_value: Annotated[str | None, Form(alias="csrf_token")] = None,
        attempt_id: Annotated[str | None, Form()] = None,
        view: Annotated[str, Form()] = "in-zotero") -> HTMLResponse:
        if not _csrf_valid(csrf_token_value, csrf_token):
            return HTMLResponse('<p class="notice error">Invalid or missing CSRF token.</p>', status_code=403)
        coordinator = app.state.acquisition_coordinator
        try:
            identity = UUID(attempt_id) if attempt_id is not None else None
        except (ValueError, TypeError):
            identity = None
        operation = {"cancel": coordinator.cancel, "resume": coordinator.resume, "open-retry": coordinator.reopen}.get(action)
        outcome = operation(paper_id, identity) if operation is not None and identity is not None else AcquisitionActionOutcome.UNAVAILABLE
        message = ("Zotero attachment has already begun; cancellation is too late." if outcome is AcquisitionActionOutcome.TOO_LATE
                   else "This action is unavailable for the current task." if outcome is AcquisitionActionOutcome.UNAVAILABLE else None)
        response = acquisition_response(request, paper_id, view, coordinator.snapshot(), message=message)
        if outcome is not AcquisitionActionOutcome.ACCEPTED:
            response.status_code = 409
        return response

    async def import_settings_route(request: Request, *, apply: bool) -> HTMLResponse:
        form = await request.form()
        submitted_csrf = form.get("csrf_token")
        if not _csrf_valid(str(submitted_csrf) if submitted_csrf is not None else None, csrf_token):
            return HTMLResponse('<p class="notice error">Invalid or missing CSRF token.</p>', status_code=403)

        values = settings_form_from_submission(form)
        draft, issues = settings_draft_from_form(values)
        import_values = SettingsImportValues(
            contents=str(form.get("journal_import_text", "")),
            mode=str(form.get("journal_import_mode", JournalImportMode.MERGE.value)),
        )
        try:
            mode = JournalImportMode(import_values.mode)
        except ValueError:
            mode = None
            import_values = replace(import_values, error="Choose Merge or Replace before importing.")

        applied = False
        message = "Import could not be applied." if apply else "Import could not be previewed."
        if draft is not None and mode is not None:
            if apply:
                result = apply_journal_import(draft, import_values.contents, mode=mode)
                plan = result.plan
                applied = result.applied
                if applied:
                    values = settings_form_after_import(values, result.draft)
                    message = "Import applied to the unsaved Settings draft. Validate and Save to persist it."
                else:
                    message = "Import Apply blocked; the current draft is unchanged."
            else:
                plan = preview_journal_import(draft, import_values.contents, mode=mode)
                message = "Import preview ready." if plan.can_apply else "Import Apply blocked."
            import_values = replace(import_values, plan=plan)

        response = templates.TemplateResponse(
            request, "fragments/settings_editor.html",
            _settings_context(request=request, csrf_token=csrf_token, form_values=values,
                              issues=issues, import_values=import_values, message=message,
                              message_tone="success" if applied else "warning"),
        )
        if applied:
            response.headers["HX-Trigger"] = "settingsDraftChanged"
        return response

    @app.post("/settings/import/preview", response_class=HTMLResponse)
    async def preview_settings_import(request: Request) -> HTMLResponse:
        return await import_settings_route(request, apply=False)

    @app.post("/settings/import/apply", response_class=HTMLResponse)
    async def apply_settings_import(request: Request) -> HTMLResponse:
        return await import_settings_route(request, apply=True)

    @app.post("/settings/validate", response_class=HTMLResponse)
    async def validate_settings_route(request: Request) -> HTMLResponse:
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
                    message="Settings could not be validated.",
                ),
            )

        validation = validate_settings(resolved_config_path, draft)
        message = (
            "Settings draft is valid."
            if validation.outcome is SettingsValidationOutcome.VALID
            else "Settings draft needs correction."
        )
        return templates.TemplateResponse(
            request,
            "fragments/settings_editor.html",
            _settings_context(
                request=request,
                csrf_token=csrf_token,
                form_values=values,
                issues=validation.issues,
                validation_result=validation,
                message=message,
                message_tone=(
                    "success"
                    if validation.outcome is SettingsValidationOutcome.VALID
                    else "warning"
                ),
            ),
        )

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

        result = save_settings(resolved_config_path, draft)
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
            form_values = settings_form_from_draft(result.state.draft)
            message = (
                "Settings were partially saved: journal data was written, "
                "but monitor configuration was not written."
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
        if not _csrf_valid(submitted_csrf, csrf_token):
            return HTMLResponse(
                "<p class=\"notice error\">Invalid or missing CSRF token.</p>",
                status_code=403,
            )

        output_dir, config_error = _resolve_output_dir(resolved_config_path)
        if config_error is not None or output_dir is None:
            context = _workspace_context(
                request=request,
                config_path=resolved_config_path,
                csrf_token=csrf_token,
                view=view,
                selected_paper_id=paper_id,
                decision_message="Configuration needs attention before decisions can be saved.",
            )
            return templates.TemplateResponse(
                request,
                "fragments/workspace.html",
                context,
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
            )
            return templates.TemplateResponse(
                request,
                "fragments/workspace.html",
                context,
                status_code=400,
            )

        result = action(output_dir, paper_id, expected_status)
        context = _workspace_context(
            request=request,
            config_path=resolved_config_path,
            csrf_token=csrf_token,
            view=view,
            selected_paper_id=paper_id,
            decision_result=result,
            navigation_position=navigation_position,
        )
        return templates.TemplateResponse(
            request,
            "fragments/workspace.html",
            context,
        )

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

    @app.post("/papers/{paper_id}/mark-in-zotero", response_class=HTMLResponse)
    def mark_in_zotero_route(
        request: Request,
        paper_id: UUID,
        expected_status: Annotated[str | None, Form()] = None,
        csrf_token_value: Annotated[str | None, Form(alias="csrf_token")] = None,
        view: Annotated[str, Form()] = "kept",
        position: Annotated[str | None, Form()] = None,
    ) -> HTMLResponse:
        return apply_decision(
            request,
            paper_id=paper_id,
            expected_status_value=expected_status,
            submitted_csrf=csrf_token_value,
            view=view,
            navigation_position=position,
            action=mark_paper_in_zotero,
        )

    return app
