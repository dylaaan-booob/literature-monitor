"""FastAPI adapter for local Workspace review."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from literature_monitor.application.decisions import (
    DecisionResult,
    keep_paper,
    mark_paper_in_zotero,
    reject_paper,
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
from literature_monitor.kept_export import export_kept_papers
from literature_monitor.models import WorkflowStatus
from literature_monitor.web.run_coordinator import (
    CoordinatorSnapshot,
    CoordinatorStatus,
    RunCoordinator,
    StartOutcome,
    StartResult,
)
from literature_monitor.web.settings_form import (
    SettingsFormValues,
    settings_draft_from_form,
    settings_form_from_draft,
    settings_form_from_submission,
)

_PACKAGE_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
_STATIC_DIR = _PACKAGE_DIR / "static"
_ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
_VIEW_ATTRIBUTES = {
    "inbox": "inbox",
    "kept": "kept",
    "rejected": "rejected",
    "in-zotero": "in_zotero",
}

templates = Jinja2Templates(directory=_TEMPLATES_DIR)


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

    return load_workspace(config.output_dir), None


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
    selected_view = view if view in _VIEW_ATTRIBUTES else "inbox"
    if workspace is None:
        return selected_view, ()
    return selected_view, getattr(workspace, _VIEW_ATTRIBUTES[selected_view])


def _find_paper(
    workspace: WorkspaceSnapshot | None,
    paper_id: UUID | None,
) -> WorkspacePaper | None:
    if workspace is None or paper_id is None:
        return None
    return next(
        (paper for paper in workspace.papers if paper.paper_id == paper_id),
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
) -> dict[str, object]:
    workspace, config_error = _workspace_state(config_path)
    selected_view, view_papers = _view_papers(workspace, view)
    selected_paper = _find_paper(workspace, selected_paper_id)
    return {
        "request": request,
        "csrf_token": csrf_token,
        "workspace": workspace,
        "config_error": config_error,
        "active_view": selected_view,
        "papers": view_papers,
        "selected_paper": selected_paper,
        "decision_result": decision_result,
        "decision_message": decision_message,
    }


def _export_context(
    request: Request,
    config_path: Path,
) -> dict[str, object]:
    try:
        config = load_config(config_path)
    except ConfigurationError as error:
        return {
            "request": request,
            "export_result": None,
            "config_error": str(error),
        }

    return {
        "request": request,
        "export_result": export_kept_papers(config.output_dir),
        "config_error": None,
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

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=_ALLOWED_HOSTS,
    )
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

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
        return templates.TemplateResponse(
            request,
            "settings.html",
            _settings_context(
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
            ),
        )

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
    def start_run(
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
        if (
            start_result.outcome is StartOutcome.STARTED
            and snapshot.status is CoordinatorStatus.FINISHED
        ):
            response.headers["HX-Trigger"] = "runCompleted"
        return response

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
            context["detail_message"] = "Paper not found in the current workspace."
        return templates.TemplateResponse(
            request,
            "fragments/paper_detail.html",
            context,
        )

    @app.get("/fragments/issues", response_class=HTMLResponse)
    def issues_fragment(request: Request) -> HTMLResponse:
        context = _workspace_context(
            request=request,
            config_path=resolved_config_path,
            csrf_token=csrf_token,
            view="inbox",
        )
        return templates.TemplateResponse(
            request,
            "fragments/workspace_issues.html",
            context,
        )

    @app.get("/fragments/zotero-export", response_class=HTMLResponse)
    def zotero_export_fragment(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "fragments/zotero_export.html",
            _export_context(request, resolved_config_path),
        )

    def apply_decision(
        request: Request,
        *,
        paper_id: UUID,
        expected_status_value: str | None,
        submitted_csrf: str | None,
        view: str,
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
    ) -> HTMLResponse:
        return apply_decision(
            request,
            paper_id=paper_id,
            expected_status_value=expected_status,
            submitted_csrf=csrf_token_value,
            view=view,
            action=keep_paper,
        )

    @app.post("/papers/{paper_id}/reject", response_class=HTMLResponse)
    def reject_route(
        request: Request,
        paper_id: UUID,
        expected_status: Annotated[str | None, Form()] = None,
        csrf_token_value: Annotated[str | None, Form(alias="csrf_token")] = None,
        view: Annotated[str, Form()] = "inbox",
    ) -> HTMLResponse:
        return apply_decision(
            request,
            paper_id=paper_id,
            expected_status_value=expected_status,
            submitted_csrf=csrf_token_value,
            view=view,
            action=reject_paper,
        )

    @app.post("/papers/{paper_id}/mark-in-zotero", response_class=HTMLResponse)
    def mark_in_zotero_route(
        request: Request,
        paper_id: UUID,
        expected_status: Annotated[str | None, Form()] = None,
        csrf_token_value: Annotated[str | None, Form(alias="csrf_token")] = None,
        view: Annotated[str, Form()] = "kept",
    ) -> HTMLResponse:
        return apply_decision(
            request,
            paper_id=paper_id,
            expected_status_value=expected_status,
            submitted_csrf=csrf_token_value,
            view=view,
            action=mark_paper_in_zotero,
        )

    return app
