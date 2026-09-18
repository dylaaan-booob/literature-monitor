from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from literature_monitor.kept_export import export_kept_papers
from literature_monitor.materialize import render_paper_markdown
from literature_monitor.models import (
    Author,
    CanonicalMetadata,
    CanonicalPaper,
    ExternalIds,
    Workflow,
    WorkflowStatus,
)


NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


def write_paper(
    output_dir: Path,
    filename: str,
    ordinal: int,
    *,
    status: WorkflowStatus = WorkflowStatus.KEPT,
    title: str = "A Paper",
    journal: str = "Biometrics",
    publication_date: date | None = date(2026, 9, 19),
    doi: str | None = None,
    arxiv: str | None = None,
) -> Path:
    paper = CanonicalPaper(
        id=UUID(int=ordinal),
        metadata=CanonicalMetadata(
            title=title,
            journal=journal,
            publication_date=publication_date,
        ),
        external_ids=ExternalIds(doi=doi, arxiv=arxiv),
        authors=(Author(name="Ada Author"),),
        workflow=Workflow(status=status, discovered_at=NOW),
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


def test_exports_only_kept_status(tmp_path: Path) -> None:
    for ordinal, status in enumerate(WorkflowStatus, start=1):
        write_paper(
            tmp_path,
            f"{status.value}.md",
            ordinal,
            status=status,
            doi=f"10.5555/{status.value}",
        )

    result = export_kept_papers(tmp_path)

    assert result.entries == ("10.5555/kept",)
    assert result.issues == ()


def test_doi_is_normalized_and_preferred_over_arxiv(tmp_path: Path) -> None:
    write_paper(
        tmp_path,
        "paper.md",
        1,
        doi="HTTPS://DOI.ORG/10.5555/EXAMPLE",
        arxiv="2601.01234",
    )

    result = export_kept_papers(tmp_path)

    assert result.entries == ("10.5555/example",)
    assert result.issues == ()


def test_arxiv_is_used_when_doi_is_missing(tmp_path: Path) -> None:
    write_paper(tmp_path, "paper.md", 1, arxiv="2601.01234")

    result = export_kept_papers(tmp_path)

    assert result.entries == ("arXiv:2601.01234",)
    assert result.issues == ()


@pytest.mark.parametrize(
    "unsafe_doi",
    ("10.5555/new\nline", "10.5555/tab\tvalue"),
)
def test_unsafe_doi_falls_back_to_arxiv(
    unsafe_doi: str,
    tmp_path: Path,
) -> None:
    write_paper(
        tmp_path,
        "paper.md",
        1,
        doi=unsafe_doi,
        arxiv="2601.01234",
    )

    result = export_kept_papers(tmp_path)

    assert result.entries == ("arXiv:2601.01234",)
    assert all(entry.splitlines() == [entry] for entry in result.entries)
    assert result.issues == ()


def test_unsafe_arxiv_falls_back_to_single_line_manual_entry(tmp_path: Path) -> None:
    write_paper(
        tmp_path,
        "paper.md",
        1,
        title="Manual Paper",
        journal="Biometrics",
        publication_date=date(2026, 1, 15),
        arxiv="2601.\n01234",
    )

    result = export_kept_papers(tmp_path)

    assert result.entries == (
        "MANUAL\tManual Paper\tBiometrics\t2026-01-15",
    )
    assert all(entry.splitlines() == [entry] for entry in result.entries)
    assert result.issues == ()


def test_manual_entries_use_iso_date_unknown_and_single_line_text(
    tmp_path: Path,
) -> None:
    write_paper(
        tmp_path,
        "dated.md",
        1,
        title=" A\tmanual\npaper ",
        journal=" Journal\r\n  Name ",
        publication_date=date(2026, 1, 15),
    )
    write_paper(
        tmp_path,
        "unknown.md",
        2,
        title="Unknown date",
        journal="Biometrics",
        publication_date=None,
    )

    result = export_kept_papers(tmp_path)

    assert result.entries == (
        "MANUAL\tA manual paper\tJournal Name\t2026-01-15",
        "MANUAL\tUnknown date\tBiometrics\tunknown",
    )
    assert all("\n" not in entry and "\r" not in entry for entry in result.entries)
    assert result.issues == ()


def test_entries_follow_filename_order_not_creation_order(tmp_path: Path) -> None:
    write_paper(tmp_path, "z-last.md", 1, doi="10.5555/z")
    write_paper(tmp_path, "a-first.md", 2, doi="10.5555/a")

    result = export_kept_papers(tmp_path)

    assert result.entries == ("10.5555/a", "10.5555/z")


def test_each_paper_problem_is_an_issue_and_prevents_export(tmp_path: Path) -> None:
    broken = write_paper(tmp_path, "broken.md", 1, doi="10.5555/broken")
    replace_frontmatter(
        broken,
        id="invalid",
        publication_date="not-a-date",
    )
    write_paper(tmp_path, "valid.md", 2, doi="10.5555/valid")

    result = export_kept_papers(tmp_path)

    assert result.entries == ("10.5555/valid",)
    assert [issue.path for issue in result.issues] == [broken, broken]
    assert [issue.message for issue in result.issues] == [
        "id must be a valid UUID",
        "Invalid isoformat string: 'not-a-date'",
    ]
    assert result.has_errors


def test_malformed_and_undecodable_files_do_not_block_valid_paper(
    tmp_path: Path,
) -> None:
    papers_dir = tmp_path / "Papers"
    papers_dir.mkdir()
    malformed = papers_dir / "malformed.md"
    malformed.write_text("---\ntype: paper\n", encoding="utf-8")
    undecodable = papers_dir / "undecodable.md"
    undecodable.write_bytes(b"\xff")
    write_paper(tmp_path, "valid.md", 1, doi="10.5555/valid")

    result = export_kept_papers(tmp_path)

    assert result.entries == ("10.5555/valid",)
    assert [issue.path for issue in result.issues] == [malformed, undecodable]
    assert "missing closing YAML frontmatter delimiter" in result.issues[0].message
    assert "cannot read Paper" in result.issues[1].message
    assert result.has_errors


def test_valid_non_paper_markdown_is_ignored(tmp_path: Path) -> None:
    papers_dir = tmp_path / "Papers"
    papers_dir.mkdir()
    (papers_dir / "author.md").write_text(
        "---\ntype: author\nname: Ada Author\n---\nHuman notes.\n",
        encoding="utf-8",
    )

    result = export_kept_papers(tmp_path)

    assert result.entries == ()
    assert result.issues == ()


def test_missing_and_empty_papers_directories_are_successful_and_read_only(
    tmp_path: Path,
) -> None:
    missing_output = tmp_path / "missing-output"

    missing = export_kept_papers(missing_output)

    assert missing.entries == ()
    assert missing.issues == ()
    assert not missing_output.exists()

    empty_output = tmp_path / "empty-output"
    papers_dir = empty_output / "Papers"
    papers_dir.mkdir(parents=True)

    empty = export_kept_papers(empty_output)

    assert empty.entries == ()
    assert empty.issues == ()
    assert tuple(papers_dir.iterdir()) == ()


def test_directory_enumeration_oserror_becomes_an_issue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    papers_dir = tmp_path / "Papers"
    papers_dir.mkdir()

    def fail_glob(path: Path, pattern: str) -> object:
        assert path == papers_dir
        assert pattern == "*.md"
        raise OSError("scan denied")

    monkeypatch.setattr(Path, "glob", fail_glob)

    result = export_kept_papers(tmp_path)

    assert result.entries == ()
    assert len(result.issues) == 1
    assert result.issues[0].path == papers_dir
    assert result.issues[0].message == "cannot scan Papers directory: scan denied"
    assert result.has_errors


def test_export_preserves_all_paper_and_author_bytes(tmp_path: Path) -> None:
    paper_path = write_paper(tmp_path, "paper.md", 1, doi="10.5555/example")
    authors_dir = tmp_path / "Authors"
    authors_dir.mkdir()
    author_path = authors_dir / "ada-author.md"
    author_path.write_bytes(b"---\ntype: author\nname: Ada Author\n---\nNotes.\n")
    before = {
        paper_path: paper_path.read_bytes(),
        author_path: author_path.read_bytes(),
    }

    first = export_kept_papers(tmp_path)
    second = export_kept_papers(tmp_path)

    assert first.entries == second.entries == ("10.5555/example",)
    assert first.issues == second.issues == ()
    assert {path: path.read_bytes() for path in before} == before
