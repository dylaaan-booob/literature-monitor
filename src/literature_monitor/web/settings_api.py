"""UI Next projections of the existing Settings operations (SPEC §63)."""
from pathlib import Path

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from literature_monitor.application.journal_covers import cover_details
from literature_monitor.application.settings import load_settings, _read_snapshot, SettingsIssueSource
from literature_monitor.config import ConfigurationError, detect_list_schema
from literature_monitor.web.settings_form import settings_form_from_draft


def json_response(data, status_code=200):
    return JSONResponse(jsonable_encoder(data), status_code=status_code,
                        headers={"Cache-Control": "no-store"})


def settings_error(message: str, status_code=403):
    return json_response({"message": message}, status_code)


def settings_state(config_path: Path, csrf_token: str, *, disk=None):
    disk = disk or load_settings(config_path)
    snapshot = _read_snapshot(disk.journal_path, source=SettingsIssueSource.JOURNAL_DATA)
    upgrade = False
    if snapshot.contents is not None:
        try:
            upgrade = detect_list_schema(snapshot.contents, path=disk.journal_path) == "legacy"
        except ConfigurationError:
            pass  # Keep the editable load issues; damaged storage is not an upgrade.
    return dict(values=settings_form_from_draft(disk.draft), issues=disk.issues,
                csrf_token=csrf_token, config_path=disk.config_path,
                journal_path=disk.journal_path, needs_access_upgrade=upgrade,
                list_text=snapshot.contents,
                saved_workspace=None if any(i.source == SettingsIssueSource.MONITOR_CONFIG for i in disk.issues)
                else (disk.config_path.parent / disk.draft.output_dir).absolute(),
                covers={j.issn_l: cover_details(disk.journal_path, j.issn_l)
                        for j in disk.draft.journals})


def settings_response(request: Request, context, *, status_code=200, saved=False):
    """Project the existing Settings result onto the UI Next JSON contract."""
    state = settings_state(request.app.state.config_path, context["csrf_token"],
                           disk=context.get("settings_disk_state"))
    state.update(saved=saved, values=context["settings_form"], issues=context["settings_issues"],
                 message=context["settings_message"], save=context["settings_save"],
                 validation=context["settings_validation"],
                 deletion_plan=context["settings_deletion_plan"],
                 deletion_proof=context["settings_deletion_proof"],
                 import_plan=context["markdown_plan"],
                 import_payload=context["markdown_payload"],
                 import_filename=context["markdown_filename"],
                 import_proof=context["markdown_proof"], dirty=context["markdown_dirty"],
                 export_guard=context["markdown_export_guard"],
                 reload_guard=context["markdown_reload_guard"],
                 upgrade_plan=context["access_upgrade_plan"],
                 upgrade_proof=context["access_upgrade_proof"],
                 upgrade_dirty=context["access_upgrade_dirty"])
    return json_response(state, status_code)


def install_settings_api(app, config_path, csrf_token, *, workspace_health,
                         zotero_integration, reset_preview):
    @app.get("/api/ui-next/settings")
    def get_settings():
        state = settings_state(config_path, csrf_token)
        state["cover_search"] = app.state.journal_cover_search.state()
        return json_response(state)

    @app.get("/api/ui-next/settings/diagnostics/{kind}")
    def diagnostics(kind: str):
        if kind == "health":
            return json_response(workspace_health(config_path))
        if kind == "zotero":
            return json_response(zotero_integration(app.state.capture_coordinator.snapshot()))
        return json_response({"message": "Unknown diagnostic"}, 404)

    @app.get("/api/ui-next/settings/reset-preview")
    def get_reset_preview():
        return json_response(reset_preview())
