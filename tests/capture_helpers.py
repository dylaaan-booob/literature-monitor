"""Real durable reservations in isolated temporary Workspaces for Connector tests."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from literature_monitor.application.export_attempts import reserve_export_attempt
from literature_monitor.materialize import render_paper_markdown
from literature_monitor.models import CanonicalPaper, CanonicalMetadata, ExternalIds, Workflow, WorkflowStatus, Author
from literature_monitor.identifiers import normalize_doi


def reservation(root: Path, *, paper_id=None, doi="10.1000/example"):
    workspace = root / uuid4().hex
    path = workspace / "Papers" / "paper.md"
    path.parent.mkdir(parents=True)
    paper_id = paper_id or uuid4()
    try:
        normalized = normalize_doi(doi)
    except ValueError:
        normalized = None
    valid = normalized if normalized and normalized.startswith("10.") else "10.1000/example"
    paper = CanonicalPaper(id=paper_id, metadata=CanonicalMetadata(title="Capture Paper", journal="Biometrics"),
                           external_ids=ExternalIds(doi=valid), authors=(Author(name="Ada"),),
                           workflow=Workflow(status=WorkflowStatus.KEPT, discovered_at=datetime.now(timezone.utc)))
    path.write_text(render_paper_markdown(paper, ("ada",)))
    result = reserve_export_attempt(workspace, paper_id)
    if doi != valid:
        result = replace(result, attempt=result.attempt.model_copy(update={"doi": doi}))
    return result
