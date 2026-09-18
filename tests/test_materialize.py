from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from literature_monitor.materialize import (
    MISSING_ABSTRACT,
    materialize_papers,
    render_paper_markdown,
)
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
from literature_monitor.naming import paper_filename


NOW = datetime(2026, 9, 18, 8, 30, tzinfo=timezone.utc)


def paper(
    identifier: str,
    *,
    title: str = "A Materialized Paper",
    authors: tuple[Author, ...] = (Author(name="Ada Author"),),
    abstract: str | None = "Complete abstract with every original detail.",
) -> CanonicalPaper:
    version = PaperVersion(
        source="doi",
        identifier="10.5555/example",
        kind=VersionKind.JOURNAL_FINAL,
        url="https://doi.org/10.5555/example",
        date=date(2026, 9, 17),
    )
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
        versions=(version,),
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
        preferred_version=VersionRef(
            source=version.source,
            identifier=version.identifier,
        ),
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
        "preferred_version",
        "zotero_key",
        "external_ids",
        "versions",
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
    assert values["preferred_version"] == {
        "source": "doi",
        "identifier": "10.5555/example",
    }
    assert values["zotero_key"] is None
    assert values["external_ids"]["pmid"] == "12345678"  # type: ignore[index]
    assert values["versions"] == [source.versions[0].model_dump(mode="json")]
    assert values["sources"] == [source.sources[0].model_dump(mode="json")]
    assert source.metadata.abstract in contents
    assert "## Versions\n\n- journal_final: doi 10.5555/example" in contents
    assert "## Sources\n\n- openalex: https://openalex.org/W123" in contents
    assert contents.endswith("## Notes\n")

    author_values = frontmatter(expected_author.read_text(encoding="utf-8"))
    assert author_values == {
        "type": "author",
        "name": "Ada Author",
        "openalex_id": "https://openalex.org/A123456",
        "orcid": "https://orcid.org/0000-0002-1825-0097",
    }


def test_missing_abstract_and_empty_summaries_are_explicit() -> None:
    source = CanonicalPaper(
        id=UUID("22345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        metadata=CanonicalMetadata(title="No Abstract", journal="Biometrics"),
        authors=(Author(name="Ada Author"),),
        workflow=Workflow(discovered_at=NOW),
    )

    contents = render_paper_markdown(
        source,
        (f"unidentified-{source.id.hex}-01",),
    )

    assert f"## Abstract\n\n{MISSING_ABSTRACT}" in contents
    assert "## Versions\n\n- None recorded." in contents
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
    assert "## Abstract\n\n\n\n## Versions" in contents


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

    result = materialize_papers((first, second), tmp_path)

    assert {path.name for path in result.created_papers} == {
        paper_filename(first.metadata.title, first.id, (first.id, second.id)),
        paper_filename(second.metadata.title, second.id, (first.id, second.id)),
    }
    assert {path.name for path in result.created_authors} == {
        f"unidentified-{first.id.hex}-01.md",
        f"unidentified-{second.id.hex}-01.md",
    }


def test_rerun_preserves_existing_paper_and_author_bytes(tmp_path: Path) -> None:
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
    modified_author = b"---\ntype: author\nname: Human edited\n---\n"
    paper_path.write_bytes(modified_paper)
    author_path.write_bytes(modified_author)

    second = materialize_papers((source,), tmp_path)

    assert second.created_papers == ()
    assert second.created_authors == ()
    assert second.existing_papers == (paper_path,)
    assert second.existing_authors == (author_path,)
    assert paper_path.read_bytes() == modified_paper
    assert author_path.read_bytes() == modified_author


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

    result = materialize_papers((blocked, unrelated), tmp_path)

    blocked_path = tmp_path / "Papers" / paper_filename(
        blocked.metadata.title, blocked.id, (blocked.id, unrelated.id)
    )
    assert not blocked_path.exists()
    assert len(result.created_papers) == 1
    assert result.created_papers[0].name.startswith("unrelated--")
    assert {path.name for path in result.created_authors} == {"openalex-a2.md"}
    assert [issue.path for issue in result.issues] == [failed_target]


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
