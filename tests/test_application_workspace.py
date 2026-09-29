from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import yaml
import pytest

from literature_monitor.application.workspace import WorkspaceSectionKind, load_workspace
from literature_monitor.config import JournalConfig
from literature_monitor.markdown_state import PaperJournalAttributionState
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


@pytest.mark.parametrize("group", ["Statistics", None, "unmapped"])
def test_workflow_views_use_discovered_date_then_publication_date_then_title(
    tmp_path: Path,
    group: str | None,
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

    journals = () if group == "unmapped" else (JournalConfig(name="Biometrics", issn=("0006-341X",), group=group),)
    snapshot = load_workspace(tmp_path, journals)

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


@pytest.mark.parametrize("attribution", ["missing", [], ["0006-341X"], "damaged", None, {"issn": "0006-341X"}, ["0006-341X", "invalid"]])
def test_optional_attribution_does_not_remove_papers_from_any_workflow_view(tmp_path, attribution):
    for ordinal, status in enumerate(WorkflowStatus, start=1):
        path = write_paper(tmp_path, f"{status.value}.md", ordinal, status=status,
                           zotero_key="ZOT123" if status is WorkflowStatus.IN_ZOTERO else None)
        if attribution != "missing":
            replace_frontmatter(path, journal_issns=attribution)
    before = {p: p.read_bytes() for p in (tmp_path / "Papers").glob("*.md")}
    snapshot = load_workspace(tmp_path)
    assert len(snapshot.papers) == 4 and snapshot.issues == ()
    for view, status in ((snapshot.inbox, WorkflowStatus.CANDIDATE), (snapshot.kept, WorkflowStatus.KEPT),
                         (snapshot.rejected, WorkflowStatus.REJECTED), (snapshot.in_zotero, WorkflowStatus.IN_ZOTERO)):
        assert len(view) == 1 and view[0].status is status
    assert all(p.read_bytes() == contents for p, contents in before.items())


A = JournalConfig(name="Biometrics", issn=("0006-341X", "1541-0420"), group="Z Statistics")
B = JournalConfig(name="Annals of Statistics", issn=("0090-5364",))
C = JournalConfig(name="Psychometrika", issn=("0033-3123",), group="Z Statistics")
D = JournalConfig(name="JASA", issn=("0162-1459",), group="A Methods")


@pytest.mark.parametrize("attribution,name,kind,label", [
    (["0006-341X"], "Biometrics", "group", "Z Statistics"),
    (["0006-341X", "1541-0420"], "Biometrics", "group", "Z Statistics"),
    (["0006-341X", "0092-5853"], "Biometrics", "group", "Z Statistics"),
    (["0006-341X"], "Provider changed display name", "group", "Z Statistics"),
    (["0090-5364"], "Biometrics", "ungrouped", "Ungrouped"),
    (["0006-341X", "0090-5364"], "Biometrics", "unmapped", "Unmapped journals"),
    (["0006-341X", "0033-3123"], "Biometrics", "unmapped", "Unmapped journals"),
    (["0092-5853"], "Biometrics", "unmapped", "Unmapped journals"),
    ("missing", "  Ｂｉｏｍｅｔｒｉｃｓ  ", "group", "Z Statistics"),
    ("missing", " ANNALS   OF\nSTATISTICS ", "ungrouped", "Ungrouped"),
    ([], "BIOMETRICS", "group", "Z Statistics"),
    ([], "Annals of Statistics", "ungrouped", "Ungrouped"),
    ("missing", "Unknown journal", "unmapped", "Unmapped journals"),
    ("missing", "Biometrics supplement", "unmapped", "Unmapped journals"),
    ("missing", "Biometric", "unmapped", "Unmapped journals"),
    ("missing", "Bio-metrics", "unmapped", "Unmapped journals"),
    ("0006-341X", "Biometrics", "unmapped", "Unmapped journals"),
    (None, "Biometrics", "unmapped", "Unmapped journals"),
    ({"issn": "0006-341X"}, "Biometrics", "unmapped", "Unmapped journals"),
    (["0006-341X", "invalid"], "Biometrics", "unmapped", "Unmapped journals"),
])
def test_current_journal_mapping_uses_parsed_attribution_and_only_allowed_name_fallback(tmp_path, attribution, name, kind, label):
    path = write_paper(tmp_path, "paper.md", 1)
    updates = {"journal": name}
    if attribution != "missing":
        updates["journal_issns"] = attribution
    replace_frontmatter(path, **updates)
    before = path.read_bytes()
    snapshot = load_workspace(tmp_path, (A, B, C, D))
    section, = snapshot.sections_for(WorkflowStatus.CANDIDATE)
    assert section.kind is WorkspaceSectionKind(kind) and section.label == label
    assert section.papers == snapshot.inbox == snapshot.papers
    assert snapshot.issues == () and path.read_bytes() == before
    parsed, = snapshot.papers
    if isinstance(attribution, list) and attribution and "invalid" not in attribution:
        assert parsed.journal_attribution_state is PaperJournalAttributionState.VALID
        assert parsed.journal_issns == tuple(sorted(set(attribution)))
    elif attribution == "missing" or attribution == []:
        assert parsed.journal_attribution_state is PaperJournalAttributionState.MISSING_OR_EMPTY
    else:
        assert parsed.journal_attribution_state is PaperJournalAttributionState.MALFORMED
        assert parsed.journal_issns == ()


@pytest.mark.parametrize("attribution", ["missing", []])
def test_ambiguous_normalized_journal_names_never_pick_first_match(tmp_path, attribution):
    path = write_paper(tmp_path, "paper.md", 1)
    if attribution != "missing":
        replace_frontmatter(path, journal_issns=attribution)
    alias = JournalConfig(name="ＢＩＯＭＥＴＲＩＣＳ", issn=("0092-5853",), group=A.group)
    snapshot = load_workspace(tmp_path, (A, alias))
    section, = snapshot.sections_for(WorkflowStatus.CANDIDATE)
    assert section.kind is WorkspaceSectionKind.UNMAPPED and not snapshot.issues


@pytest.mark.parametrize("status", list(WorkflowStatus))
def test_sections_follow_first_configured_group_occurrence_and_flat_navigation_order(tmp_path, status):
    journals = (A, B, D, C, JournalConfig(name="Empty journal", issn=("0092-5853",), group="Empty"))
    for ordinal, title, identities in (
        (1, "Z first group", ["0006-341X"]), (2, "Y same group", ["0033-3123"]),
        (3, "X second group", ["0162-1459"]), (4, "A ungrouped", ["0090-5364"]),
        (5, "B unmapped", ["0036-1992"]),
    ):
        path = write_paper(tmp_path, f"{ordinal}.md", ordinal, title=title, status=status,
                           zotero_key="ZOT123" if status is WorkflowStatus.IN_ZOTERO else None)
        replace_frontmatter(path, journal_issns=identities)
    snapshot = load_workspace(tmp_path, journals)
    sections = snapshot.sections_for(status)
    assert [s.label for s in sections] == ["Z Statistics", "A Methods", "Ungrouped", "Unmapped journals"]
    assert [p.title for p in sections[0].papers] == ["Y same group", "Z first group"]
    flattened = snapshot.papers_for(status)
    assert [p.title for p in flattened] == ["Y same group", "Z first group", "X second group", "A ungrouped", "B unmapped"]
    assert [p.title for p in snapshot.papers] == ["A ungrouped", "B unmapped", "X second group", "Y same group", "Z first group"]
    assert len({p.paper_id for p in flattened}) == len(flattened) == 5
    for other_status in WorkflowStatus:
        if other_status is not status:
            assert snapshot.papers_for(other_status) == () and snapshot.sections_for(other_status) == ()


@pytest.mark.parametrize("label", ["Ungrouped", "Unmapped journals", "Statistics"])
def test_group_identity_is_exact_and_separate_from_reserved_sections(tmp_path, label):
    grouped = A.model_copy(update={"group": label})
    lower = D.model_copy(update={"group": label.lower()})
    for ordinal, ids in ((1, ["0006-341X"]), (2, ["0162-1459"]), (3, ["0090-5364"]), (4, ["0036-1992"])):
        path = write_paper(tmp_path, f"{ordinal}.md", ordinal)
        replace_frontmatter(path, journal_issns=ids)
    sections = load_workspace(tmp_path, (grouped, lower, B)).sections_for(WorkflowStatus.CANDIDATE)
    assert [(s.kind, s.label) for s in sections] == [
        (WorkspaceSectionKind.GROUP, label), (WorkspaceSectionKind.GROUP, label.lower()),
        (WorkspaceSectionKind.UNGROUPED, "Ungrouped"), (WorkspaceSectionKind.UNMAPPED, "Unmapped journals"),
    ]


def test_current_config_changes_reproject_without_paper_writes_or_cache(tmp_path):
    first_path = write_paper(tmp_path, "a.md", 1)
    second_path = write_paper(tmp_path, "d.md", 2)
    replace_frontmatter(first_path, journal_issns=["0006-341X"])
    replace_frontmatter(second_path, journal_issns=["0162-1459"])
    before = {p: p.read_bytes() for p in (tmp_path / "Papers").glob("*.md")}
    renamed = A.model_copy(update={"group": "Renamed"})
    assigned = A.model_copy(update={"group": D.group})
    removed = JournalConfig(name="Biometrics", issn=("0092-5853",), group="Name fallback forbidden")
    for journals, expected in (
        ((A, D), ["Z Statistics", "A Methods"]),
        ((renamed, D), ["Renamed", "A Methods"]),
        ((D, A), ["A Methods", "Z Statistics"]),
        ((assigned, D), ["A Methods"]),
        ((D, removed), ["A Methods", "Unmapped journals"]),
    ):
        snapshot = load_workspace(tmp_path, journals)
        assert [s.label for s in snapshot.sections_for(WorkflowStatus.CANDIDATE)] == expected
        assert all(p.status is WorkflowStatus.CANDIDATE for p in snapshot.papers)
        assert all(p.read_bytes() == contents for p, contents in before.items())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Papers"]
