from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
import yaml

import literature_monitor.materialize as materialize_module
from literature_monitor.inbox import render_default_inbox_base
from literature_monitor.kept_export import export_kept_papers
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


def manifestation(
    identifier: str,
    *,
    title: str,
    kind: VersionKind,
    version_source: str,
    version_identifier: str,
    version_date: date | None,
    external_doi: str | None = None,
    author: Author = Author(name="Ada Author"),
    shared_record_id: str = "shared-work",
) -> CanonicalPaper:
    version = PaperVersion(
        source=version_source,
        identifier=version_identifier,
        kind=kind,
        date=version_date,
    )
    return CanonicalPaper(
        id=UUID(identifier),
        metadata=CanonicalMetadata(
            title=title,
            journal="Biometrics",
            publication_date=version_date,
            abstract=f"{title} abstract",
            author_keywords=(title.casefold(),),
        ),
        external_ids=ExternalIds(doi=external_doi),
        authors=(author,),
        versions=(version,),
        sources=(
            MetadataSource(
                provider="openalex",
                record_id=shared_record_id,
                retrieved_at=NOW,
            ),
        ),
        workflow=Workflow(discovered_at=NOW),
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
    result = materialize_papers((), tmp_path)

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

    def fail_inbox_creation(path: Path, contents: str) -> tuple[str | None, str | None]:
        if path == inbox_path:
            return None, "simulated Inbox write failure"
        return create_file(path, contents)

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

    second = materialize_papers((source,), tmp_path)

    assert second.created_papers == ()
    assert second.created_authors == ()
    assert second.existing_papers == (paper_path,)
    assert second.existing_authors == (author_path,)
    assert paper_path.read_bytes() == modified_paper
    assert author_path.read_bytes() == modified_author


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


def test_fresh_uuid_recovers_existing_path_from_each_strong_identity(
    tmp_path: Path,
) -> None:
    cases = (
        ("uuid", True, ExternalIds(), (), ()),
        ("doi", False, ExternalIds(doi="10.1000/shared"), (), ()),
        (
            "external",
            False,
            ExternalIds.model_validate({"pmid": "12345"}),
            (),
            (),
        ),
        (
            "version",
            False,
            ExternalIds(),
            (
                PaperVersion(
                    source="arxiv",
                    identifier="2609.00001",
                    kind=VersionKind.PREPRINT,
                ),
            ),
            (),
        ),
        (
            "source",
            False,
            ExternalIds(),
            (),
            (
                MetadataSource(
                    provider="OpenAlex",
                    record_id="W-identity",
                    retrieved_at=NOW,
                ),
            ),
        ),
    )
    for ordinal, (name, same_uuid, external_ids, versions, sources) in enumerate(
        cases, start=1
    ):
        root = tmp_path / name
        first_id = UUID(f"{ordinal:08x}-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        initial = CanonicalPaper(
            id=first_id,
            metadata=CanonicalMetadata(title="Initial", journal="Biometrics"),
            external_ids=external_ids,
            authors=(Author(name="Ada"),),
            versions=versions,
            sources=sources,
            workflow=Workflow(discovered_at=NOW),
        )
        first = materialize_papers((initial,), root)
        incoming = initial.model_copy(
            update={
                "id": (
                    first_id
                    if same_uuid
                    else UUID(f"{ordinal + 20:08x}-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
                ),
                "metadata": initial.metadata.model_copy(update={"title": "Changed"}),
            }
        )

        second = materialize_papers((incoming,), root)

        assert second.created_papers == ()
        assert second.existing_papers == first.created_papers
        assert len(tuple((root / "Papers").glob("*.md"))) == 1
        assert frontmatter(first.created_papers[0].read_text())["id"] == str(first_id)


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


def test_preferred_version_upgrade_updates_bibliographic_snapshot(
    tmp_path: Path,
) -> None:
    preprint = manifestation(
        "16345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="Preprint title",
        kind=VersionKind.PREPRINT,
        version_source="doi",
        version_identifier="10.5555/preprint",
        version_date=date(2026, 1, 1),
        external_doi="10.5555/preprint",
    )
    preprint = preprint.model_copy(
        update={
            "workflow": Workflow(
                status=WorkflowStatus.KEPT,
                discovered_at=NOW,
            )
        }
    )
    final = manifestation(
        "17345678-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        title="Final title",
        kind=VersionKind.JOURNAL_FINAL,
        version_source="doi",
        version_identifier="10.5555/final",
        version_date=date(2026, 9, 10),
        external_doi="HTTPS://DOI.ORG/10.5555/FINAL",
    )
    first = materialize_papers((preprint,), tmp_path)

    result = materialize_papers((final,), tmp_path)

    values = frontmatter(first.created_papers[0].read_text())
    assert result.created_papers == ()
    assert result.updated_papers == first.created_papers
    assert values["id"] == str(preprint.id)
    assert values["title"] == "Final title"
    assert values["publication_date"] == "2026-09-10"
    assert values["preferred_version"] == {
        "source": "doi",
        "identifier": "10.5555/final",
    }
    assert values["doi"] == "10.5555/final"
    assert values["external_ids"]["doi"] == "10.5555/final"  # type: ignore[index]
    assert {
        (version["kind"], version["source"], version["identifier"])
        for version in values["versions"]  # type: ignore[union-attr]
    } == {
        ("preprint", "doi", "10.5555/preprint"),
        ("journal_final", "doi", "10.5555/final"),
    }
    assert any(
        issue.message == "doi changed with the effective preferred version"
        for issue in result.issues
    )
    assert not any(
        issue.message == "external ID doi conflicts with durable value"
        for issue in result.issues
    )
    assert not result.has_errors

    stable_bytes = first.created_papers[0].read_bytes()
    stable_rerun = materialize_papers((final,), tmp_path)

    assert stable_rerun.updated_papers == ()
    assert first.created_papers[0].read_bytes() == stable_bytes

    exported = export_kept_papers(tmp_path)

    assert exported.entries == ("10.5555/final",)
    assert exported.issues == ()


def test_lower_priority_rerun_preserves_effective_preferred_snapshot(
    tmp_path: Path,
) -> None:
    final = manifestation(
        "18345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="Final title",
        kind=VersionKind.JOURNAL_FINAL,
        version_source="doi",
        version_identifier="10.5555/final",
        version_date=date(2026, 9, 10),
        external_doi="10.5555/final",
    )
    preprint = manifestation(
        "19345678-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        title="Preprint title",
        kind=VersionKind.PREPRINT,
        version_source="doi",
        version_identifier="10.5555/preprint",
        version_date=date(2026, 1, 1),
        external_doi="10.5555/preprint",
    )
    first = materialize_papers((final,), tmp_path)

    result = materialize_papers((preprint,), tmp_path)

    values = frontmatter(first.created_papers[0].read_text())
    assert result.created_papers == ()
    assert values["title"] == "Final title"
    assert values["publication_date"] == "2026-09-10"
    assert values["preferred_version"] == {
        "source": "doi",
        "identifier": "10.5555/final",
    }
    assert values["external_ids"]["doi"] == "10.5555/final"  # type: ignore[index]
    assert {
        version["identifier"]
        for version in values["versions"]  # type: ignore[union-attr]
    } == {"10.5555/preprint", "10.5555/final"}
    assert any(
        issue.severity is MaterializationIssueSeverity.WARNING
        for issue in result.issues
    )
    assert any(
        issue.message == "external ID doi conflicts with durable value"
        for issue in result.issues
    )
    assert not result.has_errors


def test_version_enrichment_and_conflict_are_nonfatal_warnings(
    tmp_path: Path,
) -> None:
    initial = manifestation(
        "1a345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="Versioned",
        kind=VersionKind.UNKNOWN,
        version_source="doi",
        version_identifier="10.5555/version",
        version_date=None,
    )
    enriched = manifestation(
        "1b345678-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        title="Versioned",
        kind=VersionKind.JOURNAL_FINAL,
        version_source="doi",
        version_identifier="10.5555/version",
        version_date=date(2026, 9, 10),
    )
    first = materialize_papers((initial,), tmp_path)
    materialize_papers((enriched,), tmp_path)
    conflicting = enriched.model_copy(
        update={
            "versions": (
                enriched.versions[0].model_copy(
                    update={
                        "kind": VersionKind.ACCEPTED_MANUSCRIPT,
                        "date": date(2026, 9, 11),
                    }
                ),
            )
        }
    )

    result = materialize_papers((conflicting,), tmp_path)

    values = frontmatter(first.created_papers[0].read_text())
    version = values["versions"][0]  # type: ignore[index]
    assert version["kind"] == "journal_final"
    assert version["date"] == "2026-09-10"
    assert len(result.issues) >= 2
    assert all(
        issue.severity is MaterializationIssueSeverity.WARNING
        for issue in result.issues
    )
    assert not result.has_errors


def test_title_fallback_requires_author_note_stable_evidence(
    tmp_path: Path,
) -> None:
    original = CanonicalPaper(
        id=UUID("1c345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        metadata=CanonicalMetadata(title="Same Title", journal="Biometrics"),
        authors=(Author(name="Ada", openalex_id="https://openalex.org/A42"),),
        workflow=Workflow(discovered_at=NOW),
    )
    first = materialize_papers((original,), tmp_path)
    incoming = original.model_copy(
        update={"id": UUID("1d345678-bbbb-4bbb-8bbb-bbbbbbbbbbbb")}
    )

    matched = materialize_papers((incoming,), tmp_path)

    assert matched.existing_papers == first.created_papers
    assert matched.created_papers == ()

    first.created_authors[0].write_text("opaque author\n", encoding="utf-8")
    second_incoming = incoming.model_copy(
        update={"id": UUID("1e345678-cccc-4ccc-8ccc-cccccccccccc")}
    )
    blocked_fallback = materialize_papers((second_incoming,), tmp_path)

    assert len(blocked_fallback.created_papers) == 1
    assert len(tuple((tmp_path / "Papers").glob("*.md"))) == 2
    assert first.created_authors[0].read_text() == "opaque author\n"


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
    initial = manifestation(
        "1f345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="Initial",
        kind=VersionKind.PREPRINT,
        version_source="arxiv",
        version_identifier="2601.00002",
        version_date=date(2026, 1, 2),
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

    assert result.existing_papers == ()
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
        "<!-- literature-monitor:abstract-end -->\n\n## Versions",
        "<!-- literature-monitor:abstract-end -->\n\n"
        "## Custom\n\nKeep this section.\n\n## Versions",
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
    original = manifestation(
        "21345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="Original",
        kind=VersionKind.PREPRINT,
        version_source="arxiv",
        version_identifier="2601.00003",
        version_date=date(2026, 1, 3),
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

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("literature_monitor.safe_write.os.replace", fail_replace)
    result = materialize_papers((changed,), tmp_path)

    assert result.updated_papers == ()
    assert result.has_errors
    assert path.read_bytes() == before


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

    result = materialize_papers((changed, independent), tmp_path)

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
        .replace("zotero_key: null", "zotero_key: ZOT123")
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
    assert values["zotero_key"] == "ZOT123"
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
                {"doi": "10.5555/shared", "pmid": "old-pmid"}
            ),
            "sources": (
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
            "external_ids": ExternalIds.model_validate(
                {"pmid": "new-pmid", "semantic_scholar": "S2-1"}
            ),
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
    assert sources[0]["retrieved_at"] == "2026-09-18T00:00:00Z"  # type: ignore[index]
    assert any("external ID pmid conflicts" in issue.message for issue in result.issues)
    assert not result.has_errors


def test_ambiguous_existing_identity_and_multiple_incoming_fail_closed(
    tmp_path: Path,
) -> None:
    first = paper(
        "26345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        title="First duplicate",
    )
    second = paper(
        "27345678-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        title="Second duplicate",
    )
    seeded = materialize_papers((first, second), tmp_path)
    assert len(seeded.created_papers) == 2
    ambiguous = first.model_copy(
        update={
            "id": UUID("28345678-cccc-4ccc-8ccc-cccccccccccc"),
            "metadata": first.metadata.model_copy(update={"title": "Ambiguous"}),
        }
    )

    result = materialize_papers((ambiguous,), tmp_path)

    assert result.created_papers == ()
    assert result.has_errors
    assert len(tuple((tmp_path / "Papers").glob("*.md"))) == 2

    isolated_root = tmp_path / "single"
    original = materialize_papers((first,), isolated_root)
    incoming_one = first.model_copy(
        update={"id": UUID("29345678-dddd-4ddd-8ddd-dddddddddddd")}
    )
    incoming_two = first.model_copy(
        update={"id": UUID("2a345678-eeee-4eee-8eee-eeeeeeeeeeee")}
    )
    shared = materialize_papers((incoming_one, incoming_two), isolated_root)
    assert shared.created_papers == ()
    assert shared.existing_papers == original.created_papers
    assert shared.has_errors


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
