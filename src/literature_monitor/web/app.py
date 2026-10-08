"""FastAPI adapter for local Workspace review."""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from literature_monitor.application.decisions import (
    DecisionOutcome,
    DecisionResult,
    keep_paper,
    reconcile_paper_with_zotero,
    reject_paper,
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
)
from literature_monitor.application.workspace import (
    WorkspacePaper,
    WorkspaceSnapshot,
    load_workspace,
)
from literature_monitor.config import ConfigurationError, load_config
from literature_monitor.identifiers import normalize_doi
from literature_monitor.models import WorkflowStatus
from literature_monitor.zotero_local import ZoteroLocalClient
from literature_monitor.web.capture_coordinator import (
    CONNECTOR_VERSION_MAX_LENGTH,
    REQUEST_ID_MAX_LENGTH,
    CaptureCoordinator,
    CaptureOutcome,
    CaptureStage,
    CaptureSnapshot,
)
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
from literature_monitor.web.zotero_capture import (
    CompletionProcessOutcome,
    SaveToZoteroOutcome,
    process_capture_completion,
    start_save_to_zotero,
)

_PACKAGE_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
_STATIC_DIR = _PACKAGE_DIR / "static"
_ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
_VIEW_STATUSES = {
    "inbox": WorkflowStatus.CANDIDATE,
    "kept": WorkflowStatus.KEPT,
    "rejected": WorkflowStatus.REJECTED,
    "in-zotero": WorkflowStatus.IN_ZOTERO,
}

templates = Jinja2Templates(directory=_TEMPLATES_DIR)


class _ConnectorHeartbeatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=CONNECTOR_VERSION_MAX_LENGTH)
    zotero_reachable: StrictBool


class _ConnectorClaimBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ConnectorResultBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=REQUEST_ID_MAX_LENGTH)
    outcome: CaptureOutcome


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
    open_doi_url = None
    if selected_paper is not None and selected_paper.status is WorkflowStatus.KEPT:
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
            "message": "Finishing Zotero reconciliation…",
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
                "Automatic Zotero save failed. "
                "Use Open DOI or Check Zotero to recover."
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
                "save. Use Open DOI or Check Zotero to recover."
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
    return {
        "request": request,
        "csrf_token": csrf_token,
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
    app.state.capture_coordinator = CaptureCoordinator()

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
            )
        except ValueError:
            return JSONResponse({"accepted": False}, status_code=422)
        return JSONResponse({"readiness": readiness.value})

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
                    }
                )
            }
        )

    @app.post("/api/connector/result")
    def connector_result(body: _ConnectorResultBody) -> JSONResponse:
        accepted = app.state.capture_coordinator.submit_result(
            body.request_id,
            body.outcome,
        )
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
        context["connector_readiness"] = (
            app.state.capture_coordinator.snapshot().readiness.value
        )
        return templates.TemplateResponse(
            request,
            "settings.html",
            context,
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
            confirmation_source=str(form.get("import_confirmation_source", "")),
            confirmation_rows=tuple(str(v) for v in form.getlist("import_confirmation_row")),
            confirmation_targets=tuple(str(v) for v in form.getlist("import_confirmation_issn_l")),
        )
        try:
            mode = JournalImportMode(import_values.mode)
        except ValueError:
            mode = None
            import_values = replace(import_values, error="Choose Merge or Replace before importing.")

        applied = False
        message = "Import could not be applied." if apply else "Import could not be previewed."
        if draft is not None and mode is not None:
            plan = preview_journal_import(draft, import_values.contents, mode=mode)
            stale_source = bool(import_values.confirmation_source and import_values.confirmation_source != import_values.source_revision)
            if apply:
                try:
                    confirmations = import_values.confirmations(plan)
                    result = apply_journal_import(draft, import_values.contents, mode=mode, confirmations=confirmations)
                    plan = result.plan
                    applied = result.applied
                except ValueError as error:
                    import_values = replace(import_values, error=str(error))
                if applied:
                    values = settings_form_after_import(values, result.draft)
                    message = "Import applied to the unsaved Settings draft. Save to persist it."
                else:
                    message = "Import Apply blocked; the current draft is unchanged."
            else:
                message = "Import preview ready." if plan.can_apply or plan.can_reconcile else "Import Apply blocked."
            if stale_source:
                import_values = replace(import_values, confirmation_rows=(), confirmation_targets=(),
                                        error="Import source changed; confirm the current rows again.")
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

        result = save_settings(config_path, draft)
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
        if not snapshot.completion_pending:
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
        if completion_result.outcome is CompletionProcessOutcome.NO_COMPLETION:
            return render_capture_status(
                request,
                snapshot=app.state.capture_coordinator.snapshot(),
                view=view,
                selected_paper_id=paper,
                navigation_position=position,
            )

        completion = completion_result.completion
        reconciliation = completion_result.reconciliation
        assert completion is not None
        assert reconciliation is not None

        selected_paper_id = paper
        decision_result: DecisionResult | None = reconciliation
        decision_message: str | None = None
        decision_message_tone = "warning"
        navigation_position = None
        if completion_result.outcome is CompletionProcessOutcome.RECONCILED:
            current_output_dir, config_error = _resolve_output_dir(
                resolved_config_path,
            )
            current_workspace_is_origin = (
                config_error is None
                and current_output_dir is not None
                and Path(
                    os.path.abspath(os.fspath(current_output_dir)),
                )
                == completion.capture_guard.output_dir
            )
            if (
                current_workspace_is_origin
                and view == "kept"
                and paper == completion.paper_id
            ):
                selected_paper_id = completion.paper_id
                navigation_position = position
            else:
                # A completion can finish while the user is viewing another
                # Paper/workspace; browser hints stay presentation-only.
                decision_result = None
                decision_message = reconciliation.message
                decision_message_tone = "success"

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
        output_dir, parsed_status, failure = prepare_paper_mutation(
            request,
            paper_id=paper_id,
            expected_status_value=expected_status,
            submitted_csrf=csrf_token_value,
            view=view,
        )
        if failure is not None:
            return failure
        assert output_dir is not None
        assert parsed_status is not None

        result = start_save_to_zotero(
            output_dir,
            paper_id,
            parsed_status,
            app.state.capture_coordinator,
        )
        decision_result: DecisionResult | None = None
        decision_message: str | None = None
        decision_message_tone = "warning"
        if result.outcome is SaveToZoteroOutcome.RECONCILED:
            decision_result = result.preflight
        elif result.outcome is SaveToZoteroOutcome.CONNECTOR_UNAVAILABLE:
            decision_message = (
                "Automatic Zotero capture is unavailable because the "
                "Literature Monitor Connector is not connected. "
                "Use Open DOI or Check Zotero to recover."
            )
        elif result.outcome is SaveToZoteroOutcome.CAPTURE_BUSY:
            decision_message = (
                "Another automatic Zotero save is in progress. "
                "No second capture was started."
            )
        elif result.outcome is SaveToZoteroOutcome.COMPLETION_PENDING:
            decision_message = (
                "A previous automatic Zotero save is ready to finish. "
                "No second capture was started."
            )
        elif result.outcome is SaveToZoteroOutcome.PRECHECK_FAILED:
            decision_result = result.preflight
        elif result.outcome is SaveToZoteroOutcome.CAPTURE_INVALID_DOI:
            decision_message = (
                "Automatic Zotero capture could not start because the "
                "authoritative DOI was invalid."
            )

        context = _workspace_context(
            request=request,
            config_path=resolved_config_path,
            csrf_token=csrf_token,
            view=view,
            selected_paper_id=paper_id,
            decision_result=decision_result,
            decision_message=decision_message,
            decision_message_tone=decision_message_tone,
            navigation_position=position,
            capture_snapshot=app.state.capture_coordinator.snapshot(),
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

    @app.post("/papers/{paper_id}/check-zotero", response_class=HTMLResponse)
    def check_zotero_route(
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
            action=reconcile_paper_with_zotero,
        )

    return app
