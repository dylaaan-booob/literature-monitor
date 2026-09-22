from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import yaml

from literature_monitor.application.workspace import load_workspace
from literature_monitor.materialize import render_paper_markdown
from literature_monitor.models import (
    Author,
    CanonicalMetadata,
    CanonicalPaper,
    ExternalIds,
    MetadataSource,
    PaperVersion,
    VersionKind,
    VersionRef,
    Workflow,
    WorkflowStatus,
)


def write_paper(
    output_dir: Path,
    filename: str,
    ordinal: int,
    *,
    status: WorkflowStatus = WorkflowStatus.CANDIDATE,
    title: str = "A Paper",
    publication_date: date | None = date(2026, 9, 20),
    discovered_at: datetime = datetime(2026, 9, 21, tzinfo=timezone.utc),
    zotero_key: str | None = None,
) -> Path:
    version = PaperVersion(
        source="doi",
        identifier=f"10.5555/{ordinal}",
        kind=VersionKind.JOURNAL_FINAL,
        date=publication_date,
    )
    paper = CanonicalPaper(
        id=UUID(int=ordinal),
        metadata=CanonicalMetadata(
            title=title,
            journal="Biometrics",
            publication_date=publication_date,
            abstract=f"Abstract for {title}",
            author_keywords=("statistics",),
        ),
        external_ids=ExternalIds(
            doi=f"10.5555/{ordinal}",
            openalex=f"https://openalex.org/W{ordinal}",
        ),
        authors=(Author(name="Ada Author"),),
        versions=(version,),
        sources=(
            MetadataSource(
                provider="openalex",
                record_id=f"https://openalex.org/W{ordinal}",
                retrieved_at=discovered_at,
            ),
        ),
        workflow=Workflow(
            status=status,
            discovered_at=discovered_at,
            zotero_key=zotero_key,
        ),
        preferred_version=VersionRef(
            source=version.source,
            identifier=version.identifier,
        ),
    )
    path = output_dir / "Papers" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_paper_markdown(paper, ("ada-author",)),
        encoding="utf-8",
    )
    return path


def replace_frontmatter(path: Path, **updates: object) -> None:
    opening, payload, body = path.read_text(encoding="utf-8").split("---", maxsplit=2)
    assert opening == ""
    values = yaml.safe_load(payload)
    assert isinstance(values, dict)
    values.update(updates)
    rendered = yaml.safe_dump(values, sort_keys=False, allow_unicode=True).rstrip()
    path.write_text(f"---\n{rendered}\n---{body}", encoding="utf-8")


def test_missing_papers_returns_empty_snapshot_without_creating_anything(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "missing-workspace"

    snapshot = load_workspace(output_dir)

    assert snapshot.papers == ()
    assert snapshot.issues == ()
    assert snapshot.inbox == ()
    assert snapshot.kept == ()
    assert snapshot.rejected == ()
    assert snapshot.in_zotero == ()
    assert not output_dir.exists()


def test_all_workflow_statuses_appear_only_in_their_derived_view(
    tmp_path: Path,
) -> None:
    for ordinal, status in enumerate(WorkflowStatus, start=1):
        write_paper(
            tmp_path,
            f"{status.value}.md",
            ordinal,
            status=status,
            title=status.value,
            zotero_key="ZOT-1" if status is WorkflowStatus.IN_ZOTERO else None,
        )

    snapshot = load_workspace(tmp_path)

    assert len(snapshot.papers) == 4
    assert [paper.status for paper in snapshot.inbox] == [WorkflowStatus.CANDIDATE]
    assert [paper.status for paper in snapshot.kept] == [WorkflowStatus.KEPT]
    assert [paper.status for paper in snapshot.rejected] == [WorkflowStatus.REJECTED]
    assert [paper.status for paper in snapshot.in_zotero] == [
        WorkflowStatus.IN_ZOTERO
    ]

    candidate = snapshot.inbox[0]
    assert candidate.paper_id == UUID(int=1)
    assert candidate.journal == "Biometrics"
    assert candidate.abstract == "Abstract for candidate"
    assert candidate.author_keywords == ("statistics",)
    assert candidate.authors[0].name == "Ada Author"
    assert candidate.authors[0].note_stem == "ada-author"
    assert candidate.external_ids.doi == "10.5555/1"
    assert len(candidate.versions) == 1
    assert len(candidate.sources) == 1


def test_workflow_views_use_discovered_date_then_publication_date_then_title(
    tmp_path: Path,
) -> None:
    discovered = datetime(2026, 9, 21, tzinfo=timezone.utc)
    write_paper(
        tmp_path,
        "recent.md",
        1,
        title="Recent discovery",
        publication_date=None,
        discovered_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
    )
    write_paper(
        tmp_path,
        "a.md",
        2,
        title="A same date",
        publication_date=date(2026, 9, 20),
        discovered_at=discovered,
    )
    write_paper(
        tmp_path,
        "b.md",
        3,
        title="B same date",
        publication_date=date(2026, 9, 20),
        discovered_at=discovered,
    )
    write_paper(
        tmp_path,
        "older-publication.md",
        4,
        title="Older publication",
        publication_date=date(2026, 9, 19),
        discovered_at=discovered,
    )
    write_paper(
        tmp_path,
        "missing-publication.md",
        5,
        title="No publication date",
        publication_date=None,
        discovered_at=discovered,
    )
    write_paper(
        tmp_path,
        "older-discovery.md",
        6,
        title="Older discovery",
        publication_date=date(2026, 9, 30),
        discovered_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )

    snapshot = load_workspace(tmp_path)

    assert [paper.title for paper in snapshot.inbox] == [
        "Recent discovery",
        "A same date",
        "B same date",
        "Older publication",
        "No publication date",
        "Older discovery",
    ]


def test_corrupt_and_undecodable_papers_are_isolated_from_valid_papers(
    tmp_path: Path,
) -> None:
    papers_dir = tmp_path / "Papers"
    papers_dir.mkdir()
    malformed = papers_dir / "malformed.md"
    malformed.write_text("---\ntype: paper\n", encoding="utf-8")
    undecodable = papers_dir / "undecodable.md"
    undecodable.write_bytes(b"\xff")
    write_paper(tmp_path, "valid.md", 1, title="Valid")

    snapshot = load_workspace(tmp_path)

    assert [paper.title for paper in snapshot.papers] == ["Valid"]
    assert [issue.path for issue in snapshot.issues] == [malformed, undecodable]
    assert "missing closing YAML frontmatter delimiter" in snapshot.issues[0].message
    assert "cannot read Paper" in snapshot.issues[1].message


def test_invalid_workflow_status_is_reported_and_excluded(tmp_path: Path) -> None:
    invalid = write_paper(tmp_path, "invalid-status.md", 1)
    replace_frontmatter(invalid, status="reviewing")

    snapshot = load_workspace(tmp_path)

    assert snapshot.papers == ()
    assert snapshot.inbox == ()
    assert [issue.path for issue in snapshot.issues] == [invalid]
    assert [issue.message for issue in snapshot.issues] == ["status is invalid"]


def test_invalid_uuid_is_reported_and_excluded(tmp_path: Path) -> None:
    invalid = write_paper(tmp_path, "invalid-id.md", 1)
    replace_frontmatter(invalid, id="not-a-uuid")

    snapshot = load_workspace(tmp_path)

    assert snapshot.papers == ()
    assert [issue.path for issue in snapshot.issues] == [invalid]
    assert [issue.message for issue in snapshot.issues] == [
        "id must be a valid UUID"
    ]


def test_second_load_uses_current_disk_status_without_membership_cache(
    tmp_path: Path,
) -> None:
    path = write_paper(tmp_path, "paper.md", 1, status=WorkflowStatus.CANDIDATE)

    first = load_workspace(tmp_path)
    replace_frontmatter(path, status=WorkflowStatus.KEPT.value)
    second = load_workspace(tmp_path)

    assert len(first.inbox) == 1
    assert first.kept == ()
    assert second.inbox == ()
    assert len(second.kept) == 1
    assert second.kept[0].paper_id == first.inbox[0].paper_id


def test_loading_workspace_preserves_bytes_and_creates_no_workspace_artifacts(
    tmp_path: Path,
) -> None:
    path = write_paper(tmp_path, "paper.md", 1)
    before = path.read_bytes()

    snapshot = load_workspace(tmp_path)

    assert len(snapshot.papers) == 1
    assert path.read_bytes() == before
    assert not (tmp_path / "Authors").exists()
    assert not (tmp_path / "Inbox.base").exists()
    assert sorted(item.name for item in tmp_path.iterdir()) == ["Papers"]


def test_valid_non_paper_markdown_is_ignored(tmp_path: Path) -> None:
    papers_dir = tmp_path / "Papers"
    papers_dir.mkdir()
    (papers_dir / "author.md").write_text(
        "---\ntype: author\nname: Ada Author\n---\nHuman notes.\n",
        encoding="utf-8",
    )

    snapshot = load_workspace(tmp_path)

    assert snapshot.papers == ()
    assert snapshot.issues == ()


def test_non_regular_markdown_entry_is_reported_without_blocking_valid_paper(
    tmp_path: Path,
) -> None:
    papers_dir = tmp_path / "Papers"
    papers_dir.mkdir()
    non_regular = papers_dir / "directory.md"
    non_regular.mkdir()
    write_paper(tmp_path, "valid.md", 1, title="Valid")

    snapshot = load_workspace(tmp_path)

    assert [paper.title for paper in snapshot.papers] == ["Valid"]
    assert len(snapshot.issues) == 1
    assert snapshot.issues[0].path == non_regular
    assert snapshot.issues[0].message == "Paper target is not a regular file"


def test_papers_path_that_is_not_a_directory_returns_issue(tmp_path: Path) -> None:
    papers_path = tmp_path / "Papers"
    papers_path.write_text("occupied", encoding="utf-8")

    snapshot = load_workspace(tmp_path)

    assert snapshot.papers == ()
    assert len(snapshot.issues) == 1
    assert snapshot.issues[0].path == papers_path
    assert snapshot.issues[0].message == "Paper directory is not a directory"
