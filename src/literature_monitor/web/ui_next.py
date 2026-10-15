"""JSON and page adapters for the prototype-derived frontend (SPEC §60)."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import re
import secrets
from typing import Literal
from uuid import UUID

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from literature_monitor.application.decisions import DecisionOutcome, keep_paper, reject_paper
from literature_monitor.application.workspace import journal_for_paper, load_workspace
from literature_monitor.config import ConfigurationError, load_config, parse_settings_list_text
from literature_monitor.models import WorkflowStatus
from literature_monitor.safe_write import ContentChangedError
from literature_monitor.web.run_coordinator import StartOutcome
from literature_monitor.web.run_presentation import build_run_presentation


def ui_next_page() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static/ui_next/index.html",
                        headers={"Cache-Control": "no-store"})


class _Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csrf_token: str | None = None


class _Decision(_Action):
    expected_status: WorkflowStatus


def install_ui_next(app: FastAPI, config_path: Path, csrf_token: str, *, read_target) -> None:
    def valid(body: _Action) -> bool:
        return secrets.compare_digest((body.csrf_token or "").encode(), csrf_token.encode())

    def response(data, status=200):
        return JSONResponse(jsonable_encoder(data), status_code=status,
                            headers={"Cache-Control": "no-store"})

    @app.get("/ui-next", include_in_schema=False)
    @app.get("/ui-next/", include_in_schema=False)
    def page():
        return ui_next_page()

    @app.get("/api/ui-next/workspace")
    def workspace_data():
        try:
            target, config = read_target(config_path)
            journals, publishers, services = parse_settings_list_text(
                config.venue_whitelist.read_text(encoding="utf-8"), path=config.venue_whitelist)
            workspace = load_workspace(target.workspace, journals=journals)
            current, _ = read_target(config_path)
            if current != target:
                raise ContentChangedError("Workspace/configuration changed; refresh before continuing")
            with app.state.import_submission_lock:
                import_token = target.form_token(app.state.import_token)
            publisher_by_id = {p.publisher_id: p for p in publishers}
            service_by_id = {s.id: s for s in services}
            records = {}
            subjects = defaultdict(list)
            for journal in journals:
                group = journal.group or "Ungrouped"
                match = re.fullmatch(r"(.+?)\s*·\s*(T\d+(?:\.\d+)?)", group)
                subject, tier = match.groups() if match else (group, None)
                publisher = publisher_by_id.get(journal.publisher_id)
                service = service_by_id.get(publisher.access_service_id) if publisher else None
                record = dict(key=journal.issn_l, issn_l=journal.issn_l, name=journal.name,
                              group=journal.group, tier=tier,
                              publisher=publisher.name if publisher else None,
                              service=service.name if service else None,
                              service_id=service.id if service else None,
                              cover=f"/settings/covers/{journal.issn_l}/image", papers=[])
                records[journal.issn_l] = record
                subjects[subject].append(record)
            kept = []
            for paper in (*workspace.inbox, *workspace.kept):
                journal = journal_for_paper(paper, journals)
                key = journal.issn_l if journal else f"unmapped:{paper.journal}"
                if key not in records:
                    records[key] = dict(key=key, issn_l=None, name=paper.journal,
                                        group=None, tier=None, publisher=None, service=None,
                                        service_id=None, cover=None, papers=[])
                    subjects["Unmapped journals"].append(records[key])
                record = records[key]
                enrichment = app.state.paper_enrichment.state(paper.paper_id)
                item = dict(id=str(paper.paper_id), title=paper.title,
                            publication_date=paper.publication_date, abstract=paper.abstract,
                            authors=[a.name for a in paper.authors], doi=paper.external_ids.doi,
                            discovered_at=paper.discovered_at.isoformat(), status=paper.status.value,
                            enriched=enrichment["completed_in_session"],
                            enrichPhase=enrichment["phase"], enrichMessage=enrichment["message"],
                            enrichment_request_id=enrichment["request_id"],
                            syncWarning="sync failed" in enrichment["message"])
                if paper.status is WorkflowStatus.CANDIDATE:
                    record["papers"].append(item)
                else:
                    kept.append(dict(paper=item, journal={k: record[k] for k in ("key", "issn_l", "name")}))
            groups = []
            for name, entries in subjects.items():
                active = [entry for entry in entries if entry["papers"]]
                for entry in active:
                    entry["papers"].sort(key=lambda p: p["id"])
                    entry["papers"].sort(key=lambda p: p["discovered_at"], reverse=True)
                    entry["papers"].sort(key=lambda p: p["publication_date"] or "", reverse=True)
                active.sort(key=lambda j: float(j["tier"][1:]) if j["tier"] else float("inf"))
                if active:
                    groups.append(dict(name=name, journals=active))
            return response(dict(
                subjects=groups, csrf_token=csrf_token, import_token=import_token,
                counts=dict(candidates=len(workspace.inbox), active_journals=sum(len(g["journals"]) for g in groups)),
                kept=dict(papers=kept, journals=[{k: r[k] for k in ("key", "issn_l", "name", "service_id")} for r in records.values()],
                          services=[s.model_dump() for s in services], settings=config.kept_workflow,
                          titles={str(p.paper_id): p.title for p in workspace.papers}),
                issues=[issue.message for issue in workspace.issues],
            ))
        except (ConfigurationError, ContentChangedError, OSError) as error:
            return response({"message": str(error)}, 409)

    @app.post("/api/ui-next/papers/{paper_id}/{action}")
    def decision(paper_id: UUID, action: Literal["keep", "reject"], body: _Decision):
        if not valid(body):
            return response({"message": "Invalid or missing CSRF token; refresh the page."}, 403)
        try:
            config = load_config(config_path)
            result = {"keep": keep_paper, "reject": reject_paper}[action](
                config.output_dir, paper_id, body.expected_status)
            return response(result, 200 if result.outcome is DecisionOutcome.UPDATED else 409)
        except (ConfigurationError, OSError) as error:
            return response({"message": str(error)}, 409)

    def run_data(start=None):
        snapshot = app.state.run_coordinator.snapshot()
        return dict(snapshot=jsonable_encoder(snapshot),
                    progress=build_run_presentation(snapshot, now=datetime.now(timezone.utc)),
                    start_outcome=start.outcome.value if start else None)

    @app.get("/api/ui-next/run")
    def run_state():
        return response(run_data())

    @app.post("/api/ui-next/run")
    def run_start(body: _Action):
        if not valid(body):
            return response({"message": "Invalid or missing CSRF token; refresh the page."}, 403)
        start = app.state.run_coordinator.start()
        return response(run_data(start), 409 if start.outcome is StartOutcome.START_FAILED else 200)
