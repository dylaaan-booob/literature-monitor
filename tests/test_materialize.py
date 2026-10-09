from __future__ import annotations

from datetime import date, datetime, timezone
import os
from pathlib import Path
from uuid import UUID

import pytest
import yaml

import literature_monitor.materialize as materialize_module
from literature_monitor.inbox import render_default_inbox_base
from literature_monitor.kept_export import export_kept_papers
from literature_monitor.markdown_state import (
    PaperJournalAttributionState,
    merge_paper_state,
    parse_paper_state,
    rewrite_managed_body,
    serialize_document,
)
from literature_monitor.materialize import (
    MISSING_ABSTRACT,
    MaterializationIssue,
    MaterializationIssueSeverity,
    MaterializationResult,
    materialize_papers,
    render_paper_markdown,
)
from literature_monitor.models import (
    Author,
    CanonicalMetadata,
    CanonicalPaper,
    ExternalIds,
    MetadataSource,
    Workflow,
    WorkflowStatus,
)
from literature_monitor.naming import paper_filename
from literature_monitor.progress import ProgressEvent


@pytest.mark.parametrize("replacement", ["directory", "symlink", "lock", "papers"])
@pytest.mark.parametrize("phase", ["initial", "write", "after_first_write"])
def test_materialization_rejects_identity_substitution(tmp_path, replacement, phase):
    root = tmp_path / "output"
    root.mkdir()
    root.joinpath("Papers").mkdir()
    sources = tuple(
        paper(str(UUID(int=index + 1)), title=f"Paper {index}").model_copy(
            update={"external_ids": ExternalIds(doi=f"10.5555/race-{index}")}
        ) for index in range(2)
    )
    moved = tmp_path / "preserved"
    injected = False

    def substitute(event):
        nonlocal injected
        activity = event.activity
        if injected or activity is None:
            return
        trigger = (
            phase == "initial" and activity.operation == "materialize_read"
            and activity.label == "Reading workspace"
        ) or (
            phase == "write" and activity.operation == "materialize_write" and activity.current == 0
        ) or (
            phase == "after_first_write" and activity.operation == "materialize_write" and activity.current == 1
        )
        if not trigger:
            return
        injected = True
        if replacement in {"directory", "symlink"}:
            root.rename(moved)
            if replacement == "directory":
                root.mkdir()
            else:
                root.symlink_to(moved, target_is_directory=True)
        elif replacement == "papers":
            root.joinpath("Papers").rename(moved)
            root.joinpath("Papers").mkdir()
        else:
            lock = root / ".literature-monitor-operation.lock"
            lock.rename(tmp_path / "old-lock")
            lock.touch()

    result = materialize_papers(sources, root, progress_callback=substitute)
    assert injected
    assert result.has_errors
    expected = 1 if phase == "after_first_write" else 0
    assert len(result.created_papers) == expected
    original_papers = moved / "Papers" if replacement in {"directory", "symlink"} else (
        moved if replacement == "papers" else root / "Papers"
    )
    assert len(list(original_papers.glob("*.md"))) == expected
    if replacement == "directory":
        assert not list(root.rglob("*.md"))
    elif replacement == "papers":
        assert not list(root.joinpath("Papers").glob("*.md"))


@pytest.mark.parametrize("replacement", ["directory", "symlink", "lock", "papers_symlink", "authors"])
def test_materialization_rechecks_identity_during_temporary_write(tmp_path, monkeypatch, replacement):
    root = tmp_path / "output"
    root.mkdir()
    source = paper("12345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    moved = tmp_path / "preserved"
    real_fsync = os.fsync
    injected = False

    def substitute(descriptor):
        nonlocal injected
        directory = root / ("Authors" if replacement == "authors" else "Papers")
        if not injected and any(directory.glob("*.tmp")):
            injected = True
            if replacement in {"directory", "symlink"}:
                root.rename(moved)
                if replacement == "directory":
                    root.mkdir()
                else:
                    root.symlink_to(moved, target_is_directory=True)
            elif replacement == "lock":
                lock = root / ".literature-monitor-operation.lock"
                lock.rename(tmp_path / "old-lock")
                lock.touch()
            else:
                directory.rename(moved)
                if replacement == "papers_symlink":
                    directory.symlink_to(moved, target_is_directory=True)
                else:
                    directory.mkdir()
        return real_fsync(descriptor)

    monkeypatch.setattr("literature_monitor.safe_write.os.fsync", substitute)
    result = materialize_papers((source,), root)
    assert injected and result.has_errors
    assert not result.created_papers
    assert not list((root / "Papers").glob("*.md"))
    assert not list(moved.glob("*.md"))
    assert not list(root.rglob("*.tmp"))


def test_materialization_reports_replacement_committed_before_sync_failure(tmp_path, monkeypatch):
    source = paper("12345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    first = materialize_papers((source,), tmp_path)
    target = first.created_papers[0]
    updated = source.model_copy(update={"metadata": source.metadata.model_copy(update={"title": "Updated"})})
    real_fsync = os.fsync
    papers_identity = (target.parent.stat().st_dev, target.parent.stat().st_ino)

    def fail_directory_sync(descriptor):
        current = os.fstat(descriptor)
        if (current.st_dev, current.st_ino) == papers_identity:
            raise OSError("simulated directory sync failure after replacement")
        return real_fsync(descriptor)

    monkeypatch.setattr("literature_monitor.safe_write.os.fsync", fail_directory_sync)
    result = materialize_papers((updated,), tmp_path)
    assert result.has_errors
    assert result.updated_papers == (target,)
    assert "title: Updated" in target.read_text()


def test_materialization_reports_creation_committed_before_sync_failure(tmp_path, monkeypatch):
    root = tmp_path / "output"
    root.mkdir()
    (root / "Papers").mkdir()
    source = paper("12345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    real_fsync = os.fsync
    identity = ((root / "Papers").stat().st_dev, (root / "Papers").stat().st_ino)

    def fail_directory_sync(descriptor):
        current = os.fstat(descriptor)
        if (current.st_dev, current.st_ino) == identity:
            raise OSError("simulated directory sync failure after creation")
        return real_fsync(descriptor)

    monkeypatch.setattr("literature_monitor.safe_write.os.fsync", fail_directory_sync)
    result = materialize_papers((source,), root)
    assert result.has_errors
    assert len(result.created_papers) == 1
    assert result.created_papers[0].is_file()
    assert not list(root.rglob("*.tmp"))


NOW = datetime(2026, 9, 18, 8, 30, tzinfo=timezone.utc)


def paper(
    identifier: str,
    *,
    title: str = "A Materialized Paper",
    authors: tuple[Author, ...] = (Author(name="Ada Author"),),
    abstract: str | None = "Complete abstract with every original detail.",
) -> CanonicalPaper:
    return CanonicalPaper(
        id=UUID(identifier),
        metadata=CanonicalMetadata(
            title=title,
            journal="Biometrics",
            publication_date=date(2026, 9, 17),
            abstract=abstract,
            author_keywords=("statistics", "multiview"),
        ),
        external_ids=ExternalIds.model_validate(
            {
                "openalex": "https://openalex.org/W123",
                "doi": "10.5555/example",
                "arxiv": "2609.01234",
                "crossref": "10.5555/example",
                "pmid": "12345678",
            }
        ),
        authors=authors,
        sources=(
            MetadataSource(
                provider="openalex",
                record_id="https://openalex.org/W123",
                retrieved_at=NOW,
            ),
        ),
        workflow=Workflow(
            status=WorkflowStatus.CANDIDATE,
            discovered_at=NOW,
        ),
    )


def author_paper(
    identifier: str,
    *,
    title: str,
    publication_date: date | None,
    external_doi: str | None = None,
    author: Author = Author(name="Ada Author"),
    shared_record_id: str = "shared-work",
) -> CanonicalPaper:
    return CanonicalPaper(
        id=UUID(identifier),
        metadata=CanonicalMetadata(
            title=title,
            journal="Biometrics",
            publication_date=publication_date,
            abstract=f"{title} abstract",
            author_keywords=(title.casefold(),),
        ),
        external_ids=ExternalIds(doi=external_doi or "10.5555/author"),
        authors=(author,),
        sources=(
            MetadataSource(
                provider="openalex",
                record_id=shared_record_id,
                retrieved_at=NOW,
            ),
        ),
        workflow=Workflow(discovered_at=NOW),
    )


def frontmatter(contents: str) -> dict[str, object]:
    opening, payload, _body = contents.split("---", maxsplit=2)
    assert opening == ""
    parsed = yaml.safe_load(payload)
    assert isinstance(parsed, dict)
    return parsed


def test_materializes_complete_paper_and_minimal_author_notes(tmp_path: Path) -> None:
    source = paper(
        "12345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        authors=(
            Author(
                name="Ada Author",
                openalex_id="https://openalex.org/A123456",
                orcid="https://orcid.org/0000-0002-1825-0097",
            ),
        ),
    )

    result = materialize_papers((source,), tmp_path)

    expected_paper = tmp_path / "Papers" / paper_filename(
        source.metadata.title, source.id, (source.id,)
    )
    expected_author = tmp_path / "Authors" / "openalex-a123456.md"
    assert result.created_papers == (expected_paper,)
    assert result.created_authors == (expected_author,)
    assert not result.has_errors

    contents = expected_paper.read_text(encoding="utf-8")
    values = frontmatter(contents)
    assert list(values) == [
        "type",
        "id",
        "title",
        "authors",
        "journal",
        "publication_date",
        "doi",
        "openalex_id",
        "arxiv_id",
        "author_keywords",
        "status",
        "discovered_at",
        "external_ids",
        "sources",
    ]
    assert values["id"] == str(source.id)
    assert values["title"] == source.metadata.title
    assert values["authors"] == [
        "[[Authors/openalex-a123456|Ada Author]]"
    ]
    assert values["journal"] == "Biometrics"
    assert values["publication_date"] == "2026-09-17"
    assert values["doi"] == "10.5555/example"
    assert values["openalex_id"] == "https://openalex.org/W123"
    assert values["arxiv_id"] == "2609.01234"
    assert values["author_keywords"] == ["statistics", "multiview"]
    assert values["status"] == "candidate"
    assert values["discovered_at"] == "2026-09-18T08:30:00Z"
    assert "zotero_key" not in values
    assert values["external_ids"]["pmid"] == "12345678"  # type: ignore[index]
    assert "versions" not in values and "preferred_version" not in values
    assert values["sources"] == [source.sources[0].model_dump(mode="json")]
    assert source.metadata.abstract in contents
    assert "## Versions" not in contents
    assert "## Sources\n\n- openalex: https://openalex.org/W123" in contents
    assert contents.endswith("## Notes\n")

    assert (tmp_path / "Inbox.base").read_text(encoding="utf-8") == (
        render_default_inbox_base()
    )

    author_values = frontmatter(expected_author.read_text(encoding="utf-8"))
    assert author_values == {
        "type": "author",
        "name": "Ada Author",
        "openalex_id": "https://openalex.org/A123456",
        "orcid": "https://orcid.org/0000-0002-1825-0097",
    }


def test_empty_materialization_initializes_workspace_and_inbox(
    tmp_path: Path,
) -> None:
    progress_events: list[ProgressEvent] = []
    result = materialize_papers(
        (),
        tmp_path,
        progress_callback=progress_events.append,
    )

    activities = [
        event.activity for event in progress_events if event.activity is not None
    ]
    assert [activity.operation for activity in activities] == [
        "materialize_read",
        "materialize_read",
        "materialize_prepare",
        "materialize_write",
    ]
    assert (
        activities[2].current,
        activities[2].total,
        activities[2].unit,
    ) == (0, 0, "paper")
    assert (
        activities[3].current,
        activities[3].total,
        activities[3].unit,
    ) == (0, 0, "file")
    assert (tmp_path / "Papers").is_dir()
    assert (tmp_path / "Authors").is_dir()
    assert (tmp_path / "Inbox.base").read_text(encoding="utf-8") == (
        render_default_inbox_base()
    )
    assert result.issues == ()


def test_rerun_preserves_custom_inbox_bytes_without_validation(
    tmp_path: Path,
) -> None:
    first = materialize_papers((), tmp_path)
    inbox_path = tmp_path / "Inbox.base"
    custom_bytes = b"\xffhuman-owned custom Base bytes\n"
    inbox_path.write_bytes(custom_bytes)

    second = materialize_papers((), tmp_path)

    assert first.issues == ()
    assert second.issues == ()
    assert inbox_path.read_bytes() == custom_bytes
    assert list(tmp_path.glob("*.base")) == [inbox_path]


def test_inbox_directory_collision_is_reported_without_replacement(
    tmp_path: Path,
) -> None:
    inbox_path = tmp_path / "Inbox.base"
    inbox_path.mkdir()
    marker = inbox_path / "human-owned"
    marker.write_bytes(b"preserve")

    result = materialize_papers((), tmp_path)

    assert result.has_errors
    assert [(issue.path, issue.message) for issue in result.issues] == [
        (inbox_path, "target exists but is not a regular file")
    ]
    assert inbox_path.is_dir()
    assert marker.read_bytes() == b"preserve"
    assert list(tmp_path.glob("*.base")) == [inbox_path]


def test_inbox_creation_failure_preserves_created_paper_and_author(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = paper("13345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    inbox_path = tmp_path / "Inbox.base"
    create_file = materialize_module._create_file

    def fail_inbox_creation(path: Path, contents: str, directory) -> tuple[str | None, str | None]:
        if path == inbox_path:
            return None, "simulated Inbox write failure"
        return create_file(path, contents, directory)

    monkeypatch.setattr(materialize_module, "_create_file", fail_inbox_creation)

    result = materialize_papers((source,), tmp_path)

    assert result.has_errors
    assert [(issue.path, issue.message) for issue in result.issues] == [
        (inbox_path, "simulated Inbox write failure")
    ]
    assert len(result.created_papers) == 1
    assert len(result.created_authors) == 1
    assert result.created_papers[0].is_file()
    assert result.created_authors[0].is_file()
    assert not inbox_path.exists()


def test_invalid_workspace_does_not_create_inbox(tmp_path: Path) -> None:
    (tmp_path / "Papers").write_bytes(b"not a directory")

    result = materialize_papers((), tmp_path)

    assert result.has_errors
    assert [issue.path for issue in result.issues] == [tmp_path / "Papers"]
    assert not (tmp_path / "Inbox.base").exists()


def test_missing_abstract_and_empty_summaries_are_explicit() -> None:
    source = CanonicalPaper(
        id=UUID("22345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        metadata=CanonicalMetadata(title="No Abstract", journal="Biometrics"),
        external_ids=ExternalIds(doi="10.5555/current"),
        authors=(Author(name="Ada Author"),),
        workflow=Workflow(discovered_at=NOW),
    )

    contents = render_paper_markdown(
        source,
        (f"unidentified-{source.id.hex}-01",),
    )

    assert f"## Abstract\n\n{MISSING_ABSTRACT}" in contents
    assert "## Versions" not in contents
    assert "## Sources\n\n- None recorded." in contents


def test_empty_canonical_abstract_is_preserved() -> None:
    source = paper(
        "2a345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        abstract="",
    )

    contents = render_paper_markdown(
        source,
        (f"unidentified-{source.id.hex}-01",),
    )

    assert MISSING_ABSTRACT not in contents
    assert "## Abstract\n\n\n\n## Sources" in contents


def test_renderer_rejects_author_stem_count_mismatch() -> None:
    source = paper("32345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

    with pytest.raises(ValueError, match="must match paper authors"):
        render_paper_markdown(source, ())


def test_stable_author_identities_are_reused_in_one_batch(tmp_path: Path) -> None:
    openalex_one = paper(
        "42345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="First",
        authors=(Author(name="First Name", openalex_id="https://openalex.org/A7"),),
    )
    openalex_two = paper(
        "52345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="Second",
        authors=(Author(name="Later Name", openalex_id="https://openalex.org/a7"),),
    )
    orcid_one = paper(
        "62345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="Third",
        authors=(Author(name="ORCID Name", orcid="0000-0002-1825-0097"),),
    )
    orcid_two = paper(
        "72345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="Fourth",
        authors=(
            Author(
                name="ORCID Variant",
                orcid="https://orcid.org/0000-0002-1825-0097",
            ),
        ),
    )

    openalex_one = openalex_one.model_copy(update={"external_ids": ExternalIds(doi="10.5555/openalex_one")})
    openalex_two = openalex_two.model_copy(update={"external_ids": ExternalIds(doi="10.5555/openalex_two")})
    orcid_one = orcid_one.model_copy(update={"external_ids": ExternalIds(doi="10.5555/orcid_one")})
    orcid_two = orcid_two.model_copy(update={"external_ids": ExternalIds(doi="10.5555/orcid_two")})
    result = materialize_papers(
        (openalex_one, openalex_two, orcid_one, orcid_two), tmp_path
    )

    assert {path.name for path in result.created_authors} == {
        "openalex-a7.md",
        "orcid-0000-0002-1825-0097.md",
    }
    assert len(result.created_papers) == 4
    second_contents = next(
        path.read_text(encoding="utf-8")
        for path in result.created_papers
        if path.name.startswith("second--")
    )
    assert "[[Authors/openalex-a7|Later Name]]" in second_contents


def test_invalid_orcid_and_name_only_authors_use_full_paper_uuid(
    tmp_path: Path,
) -> None:
    first = paper(
        "82345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="Invalid ORCID",
        authors=(Author(name="Same Name", orcid="0000-0002-1825-0098"),),
    )
    second = paper(
        "92345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="Name Only",
        authors=(Author(name="Same Name"),),
    )

    first = first.model_copy(update={"external_ids": ExternalIds(doi="10.5555/first")})
    second = second.model_copy(update={"external_ids": ExternalIds(doi="10.5555/second")})
    result = materialize_papers((first, second), tmp_path)

    assert {path.name for path in result.created_authors} == {
        f"unidentified-{first.id.hex}-01.md",
        f"unidentified-{second.id.hex}-01.md",
    }
    invalid_values = frontmatter(
        (tmp_path / "Authors" / f"unidentified-{first.id.hex}-01.md").read_text(
            encoding="utf-8"
        )
    )
    assert invalid_values["orcid"] == "0000-0002-1825-0098"


def test_paper_collision_context_does_not_change_name_only_author_path(
    tmp_path: Path,
) -> None:
    first = paper(
        "abcdef12-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="First Collision",
    )
    second = paper(
        "abcdef12-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        title="Second Collision",
    )

    first = first.model_copy(update={"external_ids": ExternalIds(doi="10.5555/first")})
    second = second.model_copy(update={"external_ids": ExternalIds(doi="10.5555/second")})
    result = materialize_papers((first, second), tmp_path)

    assert {path.name for path in result.created_papers} == {
        paper_filename(first.metadata.title, first.id, (first.id, second.id)),
        paper_filename(second.metadata.title, second.id, (first.id, second.id)),
    }
    assert {path.name for path in result.created_authors} == {
        f"unidentified-{first.id.hex}-01.md",
        f"unidentified-{second.id.hex}-01.md",
    }


def test_rerun_preserves_human_paper_content_and_opaque_author_bytes(
    tmp_path: Path,
) -> None:
    source = paper(
        "a2345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        authors=(Author(name="Ada", openalex_id="https://openalex.org/A9"),),
    )
    first = materialize_papers((source,), tmp_path)
    paper_path = first.created_papers[0]
    author_path = first.created_authors[0]
    modified_paper = (
        paper_path.read_text(encoding="utf-8")
        .replace("status: candidate", "status: rejected\ncustom_field: retained")
        .replace("## Notes\n", "## Notes\n\nHuman note.\n\n## Custom\n\nKeep me.\n")
    ).encode()
    modified_author = b"human-owned opaque author\n"
    paper_path.write_bytes(modified_paper)
    author_path.write_bytes(modified_author)

    progress_events: list[ProgressEvent] = []
    second = materialize_papers(
        (source,),
        tmp_path,
        progress_callback=progress_events.append,
    )

    assert second.created_papers == ()
    assert second.created_authors == ()
    assert second.existing_papers == (paper_path,)
    assert second.existing_authors == (author_path,)
    assert paper_path.read_bytes() == modified_paper
    assert author_path.read_bytes() == modified_author
    prepare = [
        event.activity
        for event in progress_events
        if event.activity is not None
        and event.activity.operation == "materialize_prepare"
    ]
    assert [(activity.current, activity.total) for activity in prepare] == [
        (0, 1),
        (1, 1),
    ]
    writes = [
        event.activity
        for event in progress_events
        if event.activity is not None
        and event.activity.operation == "materialize_write"
    ]
    assert [(activity.current, activity.total) for activity in writes] == [(0, 0)]


def test_parsed_author_note_is_enriched_without_losing_unknown_content(
    tmp_path: Path,
) -> None:
    source = paper(
        "aa345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        authors=(Author(name="Ada", openalex_id="https://openalex.org/A9"),),
    )
    first = materialize_papers((source,), tmp_path)
    author_path = first.created_authors[0]
    original = (
        "---\ntype: author\nname: Ada\ncustom: retained\n---\n\nHuman body.\n"
    )
    author_path.write_text(original, encoding="utf-8")

    result = materialize_papers((source,), tmp_path)

    contents = author_path.read_text(encoding="utf-8")
    values = frontmatter(contents)
    assert result.existing_authors == (author_path,)
    assert values["openalex_id"] == "https://openalex.org/A9"
    assert values["custom"] == "retained"
    assert contents.endswith("\nHuman body.\n")


def test_failed_author_blocks_dependent_paper_but_not_unrelated_paper(
    tmp_path: Path,
) -> None:
    blocked = paper(
        "b2345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="Blocked",
        authors=(Author(name="Blocked", openalex_id="https://openalex.org/A1"),),
    )
    unrelated = paper(
        "c2345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="Unrelated",
        authors=(Author(name="Available", openalex_id="https://openalex.org/A2"),),
    )
    failed_target = tmp_path / "Authors" / "openalex-a1.md"
    failed_target.mkdir(parents=True)

    progress_events: list[ProgressEvent] = []
    blocked = blocked.model_copy(update={"external_ids": ExternalIds(doi="10.5555/blocked")})
    unrelated = unrelated.model_copy(update={"external_ids": ExternalIds(doi="10.5555/unrelated")})
    result = materialize_papers(
        (blocked, unrelated),
        tmp_path,
        progress_callback=progress_events.append,
    )

    blocked_path = tmp_path / "Papers" / paper_filename(
        blocked.metadata.title, blocked.id, (blocked.id, unrelated.id)
    )
    assert not blocked_path.exists()
    assert len(result.created_papers) == 1
    assert result.created_papers[0].name.startswith("unrelated--")
    assert {path.name for path in result.created_authors} == {"openalex-a2.md"}
    assert [issue.path for issue in result.issues] == [failed_target]
    prepare = [
        event.activity
        for event in progress_events
        if event.activity is not None
        and event.activity.operation == "materialize_prepare"
    ]
    assert [(activity.current, activity.total) for activity in prepare] == [
        (0, 2),
        (1, 2),
        (2, 2),
    ]
    writes = [
        event.activity
        for event in progress_events
        if event.activity is not None
        and event.activity.operation == "materialize_write"
    ]
    assert [(activity.current, activity.total) for activity in writes] == [
        (0, 1),
        (1, 1),
    ]


def test_paper_write_failure_keeps_authors_and_other_papers(tmp_path: Path) -> None:
    blocked = paper(
        "d2345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="Blocked Paper",
        authors=(Author(name="One", openalex_id="https://openalex.org/A3"),),
    )
    successful = paper(
        "e2345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="Successful Paper",
        authors=(Author(name="Two", openalex_id="https://openalex.org/A4"),),
    )
    failed_target = tmp_path / "Papers" / paper_filename(
        blocked.metadata.title, blocked.id, (blocked.id, successful.id)
    )
    failed_target.mkdir(parents=True)

    blocked = blocked.model_copy(update={"external_ids": ExternalIds(doi="10.5555/blocked")})
    successful = successful.model_copy(update={"external_ids": ExternalIds(doi="10.5555/successful")})
    result = materialize_papers((blocked, successful), tmp_path)

    assert {path.name for path in result.created_authors} == {
        "openalex-a3.md",
        "openalex-a4.md",
    }
    assert len(result.created_papers) == 1
    assert result.created_papers[0].name.startswith("successful-paper--")
    assert [issue.path for issue in result.issues] == [failed_target]


def test_existing_author_file_is_available_to_new_paper(tmp_path: Path) -> None:
    source = paper(
        "f2345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        authors=(Author(name="Existing", openalex_id="https://openalex.org/A5"),),
    )
    author_path = tmp_path / "Authors" / "openalex-a5.md"
    author_path.parent.mkdir(parents=True)
    original = b"human-owned existing author\n"
    author_path.write_bytes(original)

    result = materialize_papers((source,), tmp_path)

    assert result.existing_authors == (author_path,)
    assert len(result.created_papers) == 1
    assert author_path.read_bytes() == original


def test_malformed_identity_readable_paper_blocks_duplicate_and_isolated_work(
    tmp_path: Path,
) -> None:
    original = paper("13345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    first = materialize_papers((original,), tmp_path)
    original_path = first.created_papers[0]
    malformed = original_path.read_text().replace(
        "status: candidate", "status: impossible"
    ).encode()
    original_path.write_bytes(malformed)
    duplicate = original.model_copy(
        update={
            "id": UUID("14345678-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            "metadata": original.metadata.model_copy(update={"title": "Duplicate"}),
        }
    )
    unrelated = paper(
        "15345678-cccc-4ccc-8ccc-cccccccccccc",
        title="Unrelated",
    ).model_copy(
        update={
            "external_ids": ExternalIds(doi="10.5555/unrelated"),
            "versions": (),
            "sources": (),
            "preferred_version": None,
        }
    )

    result = materialize_papers((duplicate, unrelated), tmp_path)

    assert result.has_errors
    assert original_path.read_bytes() == malformed
    assert result.existing_papers == (original_path,)
    assert len(result.created_papers) == 1
    assert result.created_papers[0].name.startswith("unrelated--")
    assert len(tuple((tmp_path / "Papers").glob("*.md"))) == 2


def test_title_author_fallback_does_not_cross_conflicting_dois(
    tmp_path: Path,
) -> None:
    author = Author(name="Ada", openalex_id="https://openalex.org/A43")
    initial = CanonicalPaper(
        id=UUID("1ea45678-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        metadata=CanonicalMetadata(title="Shared Title", journal="Biometrics"),
        external_ids=ExternalIds(doi="10.5555/first"),
        authors=(author,),
        workflow=Workflow(discovered_at=NOW),
    )
    incoming = initial.model_copy(
        update={
            "id": UUID("1eb45678-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            "external_ids": ExternalIds(doi="10.5555/second"),
        }
    )
    materialize_papers((initial,), tmp_path)

    result = materialize_papers((incoming,), tmp_path)

    assert len(result.created_papers) == 1
    assert len(tuple((tmp_path / "Papers").glob("*.md"))) == 2


def test_unidentified_author_gains_stable_id_without_renaming(tmp_path: Path) -> None:
    initial = author_paper(
        "1f345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="Initial",
        publication_date=date(2026, 1, 2),
        author=Author(name="Ada"),
    )
    first = materialize_papers((initial,), tmp_path)
    incoming = initial.model_copy(
        update={
            "id": UUID("20345678-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            "authors": (
                Author(name="Ada", openalex_id="https://openalex.org/A99"),
            ),
        }
    )

    result = materialize_papers((incoming,), tmp_path)

    author_path = first.created_authors[0]
    assert result.created_authors == ()
    assert not (tmp_path / "Authors" / "openalex-a99.md").exists()
    assert frontmatter(author_path.read_text())["openalex_id"] == (
        "https://openalex.org/A99"
    )


def test_name_only_authors_reuse_unambiguous_links_after_reordering(
    tmp_path: Path,
) -> None:
    ada = Author(name="Ada")
    bob = Author(name="Bob")
    initial = paper(
        "20445678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        authors=(ada, bob),
    )
    first = materialize_papers((initial,), tmp_path)
    incoming = initial.model_copy(update={"authors": (bob, ada)})

    result = materialize_papers((incoming,), tmp_path)

    values = frontmatter(first.created_papers[0].read_text())
    assert values["authors"] == [
        f"[[Authors/unidentified-{initial.id.hex}-02|Bob]]",
        f"[[Authors/unidentified-{initial.id.hex}-01|Ada]]",
    ]
    assert result.created_authors == ()
    assert not result.has_errors


def test_name_only_author_insertion_fails_closed_on_occupied_ordinal(
    tmp_path: Path,
) -> None:
    initial = paper(
        "20545678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        authors=(Author(name="Ada"), Author(name="Bob")),
    )
    first = materialize_papers((initial,), tmp_path)
    paper_path = first.created_papers[0]
    original = paper_path.read_bytes()
    incoming = initial.model_copy(
        update={
            "authors": (
                Author(name="Carol"),
                Author(name="Ada"),
                Author(name="Bob"),
            )
        }
    )

    result = materialize_papers((incoming,), tmp_path)

    assert result.updated_papers == ()
    assert result.created_authors == ()
    assert result.has_errors
    assert paper_path.read_bytes() == original
    assert len(tuple((tmp_path / "Authors").glob("*.md"))) == 2


def test_conflicting_openalex_id_blocks_same_orcid_author_match(
    tmp_path: Path,
) -> None:
    initial = CanonicalPaper(
        id=UUID("20645678-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        metadata=CanonicalMetadata(title="Stable Conflict", journal="Biometrics"),
        external_ids=ExternalIds(doi="10.5555/current"),
        authors=(
            Author(
                name="Ada",
                openalex_id="https://openalex.org/A1",
                orcid="0000-0002-1825-0097",
            ),
        ),
        workflow=Workflow(discovered_at=NOW),
    )
    first = materialize_papers((initial,), tmp_path)
    original = first.created_papers[0].read_bytes()
    incoming = initial.model_copy(
        update={
            "id": UUID("20745678-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            "authors": (
                Author(
                    name="Ada",
                    openalex_id="https://openalex.org/A2",
                    orcid="0000-0002-1825-0097",
                ),
            ),
        }
    )

    result = materialize_papers((incoming,), tmp_path)

    assert result.existing_papers == first.created_papers
    assert result.created_papers == ()
    assert result.has_errors
    assert first.created_papers[0].read_bytes() == original
    assert not (tmp_path / "Authors" / "openalex-a2.md").exists()


def test_abstract_h2_is_not_duplicated_and_rerun_is_byte_stable(
    tmp_path: Path,
) -> None:
    abstract = "Overview.\n\n## Methods\n\nComplete method details."
    source = paper(
        "20845678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        abstract=abstract,
    )
    first = materialize_papers((source,), tmp_path)
    paper_path = first.created_papers[0]
    customized = paper_path.read_text().replace(
        "## Notes\n",
        "## Notes\n\nHuman note.\n\n## Custom\n\nKeep this section.\n",
    )
    paper_path.write_text(customized, encoding="utf-8")
    expected = paper_path.read_bytes()

    second = materialize_papers((source,), tmp_path)
    third = materialize_papers((source,), tmp_path)

    contents = paper_path.read_text()
    assert second.updated_papers == ()
    assert third.updated_papers == ()
    assert paper_path.read_bytes() == expected
    assert contents.count("## Methods") == 1
    assert "## Custom\n\nKeep this section." in contents


def test_changed_abstract_h2_replaces_complete_managed_abstract(
    tmp_path: Path,
) -> None:
    initial = paper(
        "20945678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        abstract="Old overview.\n\n## Methods\n\nOld method details.",
    )
    first = materialize_papers((initial,), tmp_path)
    paper_path = first.created_papers[0]
    customized = paper_path.read_text().replace(
        "<!-- literature-monitor:abstract-end -->\n\n## Sources",
        "<!-- literature-monitor:abstract-end -->\n\n"
        "## Custom\n\nKeep this section.\n\n## Sources",
    )
    paper_path.write_text(customized, encoding="utf-8")
    changed = initial.model_copy(
        update={
            "metadata": initial.metadata.model_copy(
                update={
                    "abstract": "New overview.\n\n## Results\n\nNew result details."
                }
            )
        }
    )

    result = materialize_papers((changed,), tmp_path)

    contents = paper_path.read_text()
    assert result.updated_papers == (paper_path,)
    assert "Old overview." not in contents
    assert "## Methods" not in contents
    assert "Old method details." not in contents
    assert "New overview.\n\n## Results\n\nNew result details." in contents
    assert "## Custom\n\nKeep this section." in contents
    stable_bytes = paper_path.read_bytes()

    rerun = materialize_papers((changed,), tmp_path)

    assert rerun.updated_papers == ()
    assert paper_path.read_bytes() == stable_bytes


def test_changed_legacy_abstract_h2_with_ambiguous_boundary_fails_closed(
    tmp_path: Path,
) -> None:
    initial = paper(
        "20a45678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        abstract="Old overview.\n\n## Methods\n\nOld method details.",
    )
    first = materialize_papers((initial,), tmp_path)
    paper_path = first.created_papers[0]
    legacy = paper_path.read_text().replace(
        "<!-- literature-monitor:abstract-end -->\n",
        "",
    )
    paper_path.write_text(legacy, encoding="utf-8")
    original = paper_path.read_bytes()
    changed = initial.model_copy(
        update={
            "metadata": initial.metadata.model_copy(
                update={
                    "abstract": "New overview.\n\n## Results\n\nNew result details."
                }
            )
        }
    )

    result = materialize_papers((changed,), tmp_path)

    assert result.updated_papers == ()
    assert result.has_errors
    assert paper_path.read_bytes() == original


def test_atomic_replace_failure_preserves_original_paper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = author_paper(
        "21345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="Original",
        publication_date=date(2026, 1, 3),
        author=Author(name="Ada", openalex_id="https://openalex.org/A100"),
    )
    first = materialize_papers((original,), tmp_path)
    path = first.created_papers[0]
    before = path.read_bytes()
    changed = original.model_copy(
        update={
            "id": UUID("22345678-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            "metadata": original.metadata.model_copy(update={"title": "Changed"}),
        }
    )

    def fail_replace(source: object, destination: object, **kwargs) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("literature_monitor.safe_write.os.replace", fail_replace)
    progress_events: list[ProgressEvent] = []
    result = materialize_papers(
        (changed,),
        tmp_path,
        progress_callback=progress_events.append,
    )

    assert result.updated_papers == ()
    assert result.has_errors
    assert path.read_bytes() == before
    writes = [
        event.activity
        for event in progress_events
        if event.activity is not None
        and event.activity.operation == "materialize_write"
    ]
    assert [(activity.current, activity.total) for activity in writes] == [
        (0, 1),
        (1, 1),
    ]


def test_concurrent_paper_edit_aborts_update_without_blocking_other_papers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = paper("22945678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    first = materialize_papers((initial,), tmp_path)
    path = first.created_papers[0]
    changed = initial.model_copy(
        update={
            "metadata": initial.metadata.model_copy(
                update={"title": "Stale Generated Title"}
            )
        }
    )
    independent = paper(
        "22a45678-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        title="Independent Paper",
        authors=(
            Author(name="Grace", openalex_id="https://openalex.org/A-independent"),
        ),
    ).model_copy(
        update={
            "external_ids": ExternalIds(doi="10.5555/independent"),
            "versions": (),
            "sources": (),
            "preferred_version": None,
        }
    )
    render_updated_paper = materialize_module._render_updated_paper
    edit_injected = False

    def render_then_edit(*args: object, **kwargs: object) -> str:
        nonlocal edit_injected
        contents = render_updated_paper(*args, **kwargs)  # type: ignore[arg-type]
        state = args[0]
        if not edit_injected and state.path == path:  # type: ignore[union-attr]
            concurrent = path.read_text(encoding="utf-8").replace(
                "## Notes\n",
                "## Notes\n\nConcurrent human note.\n",
            )
            path.write_text(concurrent, encoding="utf-8")
            edit_injected = True
        return contents

    monkeypatch.setattr(materialize_module, "_render_updated_paper", render_then_edit)

    progress_events: list[ProgressEvent] = []
    result = materialize_papers(
        (changed, independent),
        tmp_path,
        progress_callback=progress_events.append,
    )

    contents = path.read_text(encoding="utf-8")
    conflict_errors = [
        issue
        for issue in result.issues
        if issue.path == path
        and issue.severity is MaterializationIssueSeverity.ERROR
    ]
    assert result.has_errors
    assert result.updated_papers == ()
    assert len(conflict_errors) == 1
    assert "changed on disk after it was scanned" in conflict_errors[0].message
    assert "Concurrent human note." in contents
    assert "Stale Generated Title" not in contents
    assert len(result.created_papers) == 1
    assert result.created_papers[0].name.startswith("independent-paper--")
    assert result.created_papers[0].is_file()
    writes = [
        event.activity
        for event in progress_events
        if event.activity is not None
        and event.activity.operation == "materialize_write"
    ]
    assert [(activity.current, activity.total) for activity in writes] == [
        (0, 2),
        (1, 2),
        (2, 2),
    ]


def test_issue_severity_controls_has_errors(tmp_path: Path) -> None:
    warning = MaterializationIssue(
        tmp_path,
        "recoverable conflict",
        MaterializationIssueSeverity.WARNING,
    )
    error = MaterializationIssue(tmp_path, "unsafe state")
    common = {
        "created_papers": (),
        "existing_papers": (),
        "updated_papers": (),
        "created_authors": (),
        "existing_authors": (),
    }

    assert not MaterializationResult(issues=(warning,), **common).has_errors
    assert MaterializationResult(issues=(warning, error), **common).has_errors


def test_update_preserves_workflow_unknown_frontmatter_and_unmanaged_body(
    tmp_path: Path,
) -> None:
    initial = paper("23345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    first = materialize_papers((initial,), tmp_path)
    path = first.created_papers[0]
    edited = (
        path.read_text()
        .replace(
            "status: candidate",
            "status: kept\ncustom_field:\n  nested: retained",
        )

        .replace(
            f"# {initial.metadata.title}\n\n",
            f"# {initial.metadata.title}\n\nIntro prose.\n\n"
            "## Custom Before\n\nKeep before.\n\n",
        )
        .replace(
            "## Notes\n",
            "## Notes\n\nHuman note.\n\n### Note child\n\nKeep child.\n\n"
            "## Custom After\n\nKeep after.\n",
        )
    )
    path.write_text(edited, encoding="utf-8")
    incoming = initial.model_copy(
        update={
            "metadata": initial.metadata.model_copy(
                update={"title": "Improved Title", "abstract": "Improved abstract."}
            )
        }
    )

    result = materialize_papers((incoming,), tmp_path)

    contents = path.read_text()
    values = frontmatter(contents)
    assert result.updated_papers == (path,)
    assert values["status"] == "kept"
    assert "zotero_key" not in values
    assert values["custom_field"] == {"nested": "retained"}
    for retained in (
        "Intro prose.",
        "## Custom Before\n\nKeep before.",
        "## Notes\n\nHuman note.",
        "### Note child\n\nKeep child.",
        "## Custom After\n\nKeep after.",
    ):
        assert retained in contents
    assert "# Improved Title" in contents
    assert "## Abstract\n\nImproved abstract." in contents

    stable_bytes = path.read_bytes()
    rerun = materialize_papers((incoming,), tmp_path)
    assert rerun.updated_papers == ()
    assert path.read_bytes() == stable_bytes


def test_external_ids_and_sources_union_preserve_durable_conflicts(
    tmp_path: Path,
) -> None:
    initial = paper("24345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa").model_copy(
        update={
            "external_ids": ExternalIds.model_validate(
                {
                    "doi": "10.5555/shared",
                    "pmid": "old-pmid",
                    "semantic_scholar": "S2-1",
                }
            ),
            "sources": (
                MetadataSource(
                    provider="semantic_scholar",
                    record_id="S2-1",
                    retrieved_at=datetime(2025, 12, 1, tzinfo=timezone.utc),
                ),
                MetadataSource(
                    provider="openalex",
                    record_id="W-union",
                    retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                ),
            ),
            "versions": (),
            "preferred_version": None,
        }
    )
    first = materialize_papers((initial,), tmp_path)
    incoming = initial.model_copy(
        update={
            "id": UUID("25345678-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            "external_ids": ExternalIds.model_validate({"doi": "10.5555/shared", "pmid": "new-pmid"}),
            "sources": (
                MetadataSource(
                    provider="OpenAlex",
                    record_id="W-union",
                    retrieved_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
                ),
            ),
        }
    )

    result = materialize_papers((incoming,), tmp_path)

    values = frontmatter(first.created_papers[0].read_text())
    external_ids = values["external_ids"]
    sources = values["sources"]
    assert external_ids["doi"] == "10.5555/shared"  # type: ignore[index]
    assert external_ids["pmid"] == "old-pmid"  # type: ignore[index]
    assert external_ids["semantic_scholar"] == "S2-1"  # type: ignore[index]
    assert any(
        source["provider"] == "semantic_scholar" and source["record_id"] == "S2-1"
        for source in sources  # type: ignore[union-attr]
    )
    assert any(
        source["provider"].casefold() == "openalex"
        and source["retrieved_at"] == "2026-09-18T00:00:00Z"
        for source in sources  # type: ignore[union-attr]
    )
    assert any("external ID pmid conflicts" in issue.message for issue in result.issues)
    assert not result.has_errors


def test_corpus_uuid_collision_only_changes_new_paper_filename(
    tmp_path: Path,
) -> None:
    existing = paper(
        "abcdef12-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="Existing Collision",
    ).model_copy(
        update={"external_ids": ExternalIds(doi="10.5555/existing")}
    )
    first = materialize_papers((existing,), tmp_path)
    existing_path = first.created_papers[0]
    new = paper(
        "abcdef12-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        title="New Collision",
    ).model_copy(
        update={
            "external_ids": ExternalIds(doi="10.5555/new"),
            "versions": (),
            "sources": (),
            "preferred_version": None,
        }
    )

    result = materialize_papers((new,), tmp_path)

    assert existing_path.exists()
    assert result.created_papers == (
        tmp_path
        / "Papers"
        / paper_filename(new.metadata.title, new.id, (existing.id, new.id)),
    )


@pytest.mark.parametrize(
    "body_suffix",
    (
        "\n# Second primary heading\n",
        "\n## Abstract\n\nDuplicate managed section.\n",
        "\n```python\n# heading inside an unclosed fence\n",
    ),
)
def test_ambiguous_body_blocks_update_without_creating_duplicate(
    body_suffix: str,
    tmp_path: Path,
) -> None:
    initial = paper("2b345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    first = materialize_papers((initial,), tmp_path)
    path = first.created_papers[0]
    original = path.read_bytes() + body_suffix.encode()
    path.write_bytes(original)
    incoming = initial.model_copy(
        update={
            "id": UUID("2c345678-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            "metadata": initial.metadata.model_copy(update={"title": "Changed"}),
        }
    )

    result = materialize_papers((incoming,), tmp_path)

    assert result.has_errors
    assert result.created_papers == ()
    assert result.updated_papers == ()
    assert path.read_bytes() == original
    assert len(tuple((tmp_path / "Papers").glob("*.md"))) == 1


def with_journal_attribution(contents: str, yaml_value: str | None) -> str:
    if yaml_value is None:
        return contents
    return contents.replace("type: paper\n", f"type: paper\njournal_issns: {yaml_value}\n", 1)


@pytest.mark.parametrize("yaml_value,expected,identities", [
    (None, "MISSING_OR_EMPTY", ()),
    ("[]", "MISSING_OR_EMPTY", ()),
    ("[0006-341X]", "VALID", ("0006-341X",)),
    ("[0006-341X, 1541-0420]", "VALID", ("0006-341X", "1541-0420")),
    ('[" 0006-341x "]', "VALID", ("0006-341X",)),
    ('[1541-0420, " 0006-341x ", 0006-341X, 1541-0420]', "VALID", ("0006-341X", "1541-0420")),
    ("0006-341X", "MALFORMED", ()),
    ("null", "MALFORMED", ()),
    ("{issn: 0006-341X}", "MALFORMED", ()),
    ("[123]", "MALFORMED", ()),
    ("[true]", "MALFORMED", ()),
    ('[""]', "MALFORMED", ()),
    ('["   "]', "MALFORMED", ()),
    ("[0006341X]", "MALFORMED", ()),
    ("[0006-3410]", "MALFORMED", ()),
    ("[0006-341X, invalid]", "MALFORMED", ()),
])
def test_optional_journal_attribution_parsing_is_separate_from_identity_and_safety(tmp_path, yaml_value, expected, identities):
    incoming = paper("31345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    contents = render_paper_markdown(incoming, ("ada-author",))
    path = tmp_path / "paper.md"
    baseline = parse_paper_state(path, contents, tmp_path / "Authors")
    state = parse_paper_state(path, with_journal_attribution(contents, yaml_value), tmp_path / "Authors")
    assert state is not None and baseline is not None
    assert state.journal_attribution_state is PaperJournalAttributionState(expected)
    assert state.journal_issns == identities
    assert state.updateable and state.problems == ()
    for attribute in ("paper_id", "identity_external_ids", "has_identity"):
        assert getattr(state, attribute) == getattr(baseline, attribute)
    assert state.frontmatter == frontmatter(with_journal_attribution(contents, yaml_value))


@pytest.mark.parametrize("identities", [(), ("0006-341X", "1541-0420")])
def test_new_paper_writes_optional_attribution_and_is_idempotent(tmp_path, identities):
    incoming = paper("32345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa").model_copy(update={"journal_issns": identities})
    original = paper(str(incoming.id))
    first = materialize_papers((incoming,), tmp_path)
    path, = first.created_papers
    values = frontmatter(path.read_text())
    default_keys = list(frontmatter(render_paper_markdown(original, ("ada-author",))))
    assert list(values) == default_keys + (["journal_issns"] if identities else [])
    if identities:
        assert values["journal_issns"] == list(identities)
    else:
        assert "journal_issns" not in values
    before = path.read_bytes()
    second = materialize_papers((incoming,), tmp_path)
    assert not second.issues and not second.created_papers and not second.updated_papers
    assert path.read_bytes() == before
    author_path, = first.created_authors
    assert "journal_issns" not in frontmatter(author_path.read_text())


@pytest.mark.parametrize("yaml_value", [
    None, "[]", '[" 0006-341x ", 1541-0420, 0006-341X]',
    "definitely-not-a-list-or-valid-issn", "null", "{custom: broken}", "[0006-341X, invalid]",
])
@pytest.mark.parametrize("incoming_nonempty", [False, True])
def test_existing_attribution_replacement_repair_or_raw_preservation(tmp_path, yaml_value, incoming_nonempty):
    initial = paper("33345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    first = materialize_papers((initial,), tmp_path)
    path, = first.created_papers
    contents = with_journal_attribution(path.read_text(), yaml_value)
    contents = contents.replace("status: candidate", "status: kept\ncustom_field:\n  nested: retained")
    contents = contents.replace("## Notes\n", "## Notes\n\nHuman note.\n\n## Custom\n\nKeep this.\n")
    path.write_text(contents)
    original_values = frontmatter(contents)
    author_before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in first.created_authors}
    incoming = initial.model_copy(update={
        "id": UUID("34345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        "journal_issns": ("0006-341X",) if incoming_nonempty else (),
        "metadata": initial.metadata.model_copy(update={"abstract": "Current abstract."}),
    })
    state = parse_paper_state(path, contents, tmp_path / "Authors")
    assert state is not None and state.updateable and not state.problems
    merged = merge_paper_state(state, incoming)
    assert merged.journal_issns_update == (("0006-341X",) if incoming_nonempty else None)
    result = materialize_papers((incoming,), tmp_path)
    assert not result.has_errors and not result.created_papers and result.updated_papers == (path,)
    assert len(tuple((tmp_path / "Papers").glob("*.md"))) == 1
    updated = path.read_text()
    values = frontmatter(updated)
    assert values["id"] == str(initial.id)
    if incoming_nonempty:
        assert values["journal_issns"] == ["0006-341X"]
        repaired = parse_paper_state(path, updated, tmp_path / "Authors")
        assert repaired.journal_attribution_state is PaperJournalAttributionState.VALID
        assert repaired.journal_issns == ("0006-341X",)
    else:
        assert ("journal_issns" in values) == ("journal_issns" in original_values)
        assert values.get("journal_issns") == original_values.get("journal_issns")
    assert values["status"] == "kept" and "zotero_key" not in values
    assert values["custom_field"] == {"nested": "retained"}
    assert "Human note.\n\n## Custom\n\nKeep this.\n" in updated
    for author_path, (author_bytes, mtime) in author_before.items():
        assert author_path.read_bytes() == author_bytes and author_path.stat().st_mtime_ns == mtime
    stable = path.read_bytes()
    rerun = materialize_papers((incoming,), tmp_path)
    assert not rerun.issues and not rerun.updated_papers and path.read_bytes() == stable


def test_shared_attribution_does_not_match_unrelated_papers(tmp_path):
    initial = paper("37345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa").model_copy(update={
        "journal_issns": ("0006-341X",), "external_ids": ExternalIds(doi="10.5555/initial"),
        "sources": (),
    })
    first = materialize_papers((initial,), tmp_path)
    incoming = initial.model_copy(update={
        "id": UUID("38345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        "external_ids": ExternalIds(doi="10.5555/unrelated"),
        "metadata": initial.metadata.model_copy(update={"title": "Unrelated title"}),
        "authors": (Author(name="Other Author"),),
    })
    result = materialize_papers((incoming,), tmp_path)
    assert not result.issues and len(result.created_papers) == 1
    assert len(tuple((tmp_path / "Papers").glob("*.md"))) == 2
    assert frontmatter(first.created_papers[0].read_text())["id"] == str(initial.id)


@pytest.mark.parametrize("legacy", [{"versions": []}, {"versions": None}, {"preferred_version": None},
    {"versions": [{"source": "doi", "identifier": "10.5555/example", "kind": "journal_final"}],
     "preferred_version": {"source": "doi", "identifier": "10.5555/example"}}])
def test_legacy_schema_is_blocked_unchanged_without_duplicate(tmp_path, legacy):
    initial = paper("39345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    first = materialize_papers((initial,), tmp_path)
    path, = first.created_papers
    current = parse_paper_state(path, path.read_text(), tmp_path / "Authors")
    path.write_text(serialize_document(dict(current.frontmatter, **legacy), current.body + "\n## Versions\n\nOld data.\n"))
    before = path.read_bytes()
    authors_before = {p: p.read_bytes() for p in (tmp_path / "Authors").glob("*.md")}
    incoming = initial.model_copy(update={"id": UUID(int=900), "authors": (Author(name="New Author"),)})
    result = materialize_papers((incoming,), tmp_path)
    assert result.has_errors and not result.created_papers and not result.updated_papers
    assert path.read_bytes() == before and len(list((tmp_path / "Papers").glob("*.md"))) == 1
    assert {p: p.read_bytes() for p in (tmp_path / "Authors").glob("*.md")} == authors_before
    state = parse_paper_state(path, path.read_text(), tmp_path / "Authors")
    assert not state.updateable and state.identity_external_ids == frozenset({("doi", "10.5555/example")})


def test_same_normalized_doi_recovers_uuid_despite_changed_provenance(tmp_path):
    initial = paper("40345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    first = materialize_papers((initial,), tmp_path)
    incoming = initial.model_copy(update={
        "id": UUID(int=901),
        "external_ids": ExternalIds(doi=" HTTPS://DOI.ORG/10.5555/EXAMPLE ", openalex="W-new"),
        "sources": (MetadataSource(provider="crossref", record_id="new-record", retrieved_at=NOW),),
    })
    result = materialize_papers((incoming,), tmp_path)
    assert not result.has_errors and not result.created_papers
    path, = first.created_papers
    values = frontmatter(path.read_text())
    assert values["id"] == str(initial.id) and values["doi"] == "10.5555/example"
    assert {source["provider"] for source in values["sources"]} == {"openalex", "crossref"}
    stable = path.read_bytes()
    assert not materialize_papers((incoming,), tmp_path).updated_papers
    assert path.read_bytes() == stable


def test_distinct_dois_never_merge_shared_ids_titles_or_authors(tmp_path):
    initial = paper("41345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    first = materialize_papers((initial,), tmp_path)
    path, = first.created_papers
    before = path.read_bytes()
    incoming = initial.model_copy(update={"id": UUID(int=902),
        "external_ids": initial.external_ids.model_copy(update={"doi": "10.5555/different"})})
    result = materialize_papers((incoming,), tmp_path)
    assert not result.has_errors and len(result.created_papers) == 1 and not result.updated_papers
    assert path.read_bytes() == before
    assert {frontmatter(p.read_text())["doi"] for p in (tmp_path / "Papers").glob("*.md")} == {
        "10.5555/example", "10.5555/different"}
    assert frontmatter(result.created_papers[0].read_text())["status"] == "candidate"


@pytest.mark.parametrize("mode", ["duplicate_doi", "uuid_doi_split", "uuid_retarget"])
def test_durable_doi_and_uuid_conflicts_fail_closed(tmp_path, mode):
    initial = paper("42345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    first = materialize_papers((initial,), tmp_path)
    path, = first.created_papers
    if mode != "uuid_retarget":
        other = initial.model_copy(update={"id": UUID(int=903), "external_ids": ExternalIds(
            doi="10.5555/example" if mode == "duplicate_doi" else "10.5555/other")})
        other_path = tmp_path / "Papers" / "other.md"
        other_path.write_text(render_paper_markdown(other, ("ada-author",)))
    incoming = initial.model_copy(update={
        "id": UUID(int=904) if mode == "duplicate_doi" else initial.id,
        "external_ids": ExternalIds(doi="10.5555/example" if mode == "duplicate_doi" else "10.5555/other"),
        "authors": (Author(name="Unrelated Author"),),
    })
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.md")}
    result = materialize_papers((incoming,), tmp_path)
    assert result.has_errors and not result.created_papers and not result.updated_papers and not result.created_authors
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.md")} == before


@pytest.mark.parametrize("doi", [None, "not-a-doi"])
def test_no_valid_doi_has_no_paper_or_author_side_effects(tmp_path, doi):
    initial = paper("43345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    first = materialize_papers((initial,), tmp_path)
    incoming = initial.model_copy(update={"external_ids": initial.external_ids.model_copy(update={"doi": doi}),
        "authors": (Author(name="New Author", openalex_id="A-new"),)})
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = materialize_papers((incoming,), tmp_path)
    assert not result.has_errors and result.issues
    assert not result.created_papers and not result.updated_papers and not result.created_authors
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_canonical_snapshot_supplies_new_and_updated_markdown(tmp_path):
    initial = paper("44345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    incoming = initial
    first = materialize_papers((initial,), tmp_path)
    path, = first.created_papers
    before = path.read_bytes()
    assert not materialize_papers((incoming,), tmp_path).updated_papers
    assert path.read_bytes() == before
    incoming = incoming.model_copy(update={"metadata": incoming.metadata.model_copy(update={
        "title": "Current snapshot", "abstract": "Current abstract", "author_keywords": ("new",),
        "publication_date": date(2026, 10, 1)}),
        "authors": (Author(name="Ada Author", openalex_id="A-enriched"),)})
    result = materialize_papers((incoming,), tmp_path)
    assert not result.has_errors and result.updated_papers == (path,)
    values = frontmatter(path.read_text())
    assert values["publication_date"] == "2026-10-01" and values["title"] == "Current snapshot"
    assert values["author_keywords"] == ["new"] and "Current abstract" in path.read_text()
    assert "versions" not in values and "preferred_version" not in values and "## Versions" not in path.read_text()
    author_path, = first.created_authors
    assert frontmatter(author_path.read_text())["openalex_id"] == "A-enriched"


def test_sources_and_non_doi_external_ids_are_not_identity(tmp_path):
    current = paper("45345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    contents = render_paper_markdown(current, ("ada-author",))
    state = parse_paper_state(tmp_path / "note.md", contents, tmp_path / "Authors")
    raw = dict(state.frontmatter, id="invalid", doi=None, external_ids={"openalex": "W123", "arxiv": "2609.01234"})
    state = parse_paper_state(tmp_path / "note.md", serialize_document(raw, state.body), tmp_path / "Authors")
    assert not state.has_identity and not state.updateable


def test_body_rewrite_preserves_human_versions_heading_without_managing_it():
    body = "# Title\n\n## Abstract\n\nOld.\n\n## Sources\n\nOld sources.\n\n## Versions\n\nHuman discussion.\n\n## Notes\n\nHuman notes.\n"
    rewritten = rewrite_managed_body(body, title="Title", abstract="New.", sources_summary="New sources.")
    assert "## Versions\n\nHuman discussion." in rewritten and "Human notes." in rewritten
    assert rewritten.count("## Versions") == 1


@pytest.mark.parametrize('status', [WorkflowStatus.REJECTED, WorkflowStatus.EXPORTED])
def test_terminal_history_survives_rerun_with_same_doi_and_uuid(tmp_path, status):
    source = paper('12345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
    first = materialize_papers((source,), tmp_path)
    path = first.created_papers[0]
    values = frontmatter(path.read_text())
    _, _, body = path.read_text().split('---', 2)
    values['status'] = status.value
    values['custom'] = {'human': 'retained'}
    path.write_text('---\n' + yaml.safe_dump(values, sort_keys=False) + '---' + body + '\n## Notes\nHuman history.\n')
    second = materialize_papers((source.model_copy(update={'id': UUID('abcdefab-aaaa-4aaa-8aaa-aaaaaaaaaaaa')}),), tmp_path)
    assert not second.has_errors and second.created_papers == ()
    after = frontmatter(path.read_text())
    assert after['status'] == status.value and after['id'] == str(source.id)
    assert after['custom'] == values['custom'] and after['discovered_at'] == values['discovered_at']
    assert after['doi'] == values['doi'] and 'Human history.' in path.read_text()
    assert 'zotero_key' not in after
