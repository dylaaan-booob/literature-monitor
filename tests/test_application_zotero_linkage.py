from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import httpx
import pytest

import literature_monitor.application.zotero_linkage as linkage
import literature_monitor.safe_write as safe_write
from literature_monitor.application.zotero_linkage import LinkageOutcome, link_paper_to_zotero
from literature_monitor.markdown_state import parse_paper_state, serialize_document
from literature_monitor.materialize import render_paper_markdown
from literature_monitor.models import (
    Author, CanonicalMetadata, CanonicalPaper, ExternalIds, MetadataSource,
    PaperVersion, VersionKind, VersionRef, Workflow, WorkflowStatus,
)
from literature_monitor.zotero_local import (
    VerifiedZoteroItem, ZoteroIdentityResult, ZoteroLocalClient, ZoteroReadOutcome,
)


PAPER_ID = UUID("11111111-1111-4111-8111-111111111111")
DOI = "10.5555/linkage"
KEY = "PARENT01"
SERVER = "local-instance"
NOW = datetime(2026, 9, 30, 10, tzinfo=timezone.utc)


def verified_identity():
    return ZoteroIdentityResult(
        ZoteroReadOutcome.VERIFIED, VerifiedZoteroItem(KEY, DOI, SERVER), "Verified.",
    )


@pytest.fixture
def paper_path(tmp_path):
    version = PaperVersion(source="doi", identifier=DOI, kind=VersionKind.JOURNAL_FINAL, date=date(2026, 9, 20))
    paper = CanonicalPaper(
        id=PAPER_ID,
        metadata=CanonicalMetadata(title="Linkage Paper", journal="Biometrics", abstract="Original abstract."),
        external_ids=ExternalIds.model_validate({"doi": DOI, "openalex": "https://openalex.org/W123", "pmid": "123"}),
        authors=(Author(name="Ada Author"),),
        versions=(version,),
        sources=(MetadataSource(provider="openalex", record_id="https://openalex.org/W123", retrieved_at=NOW),),
        preferred_version=VersionRef(source="doi", identifier=DOI),
        workflow=Workflow(status=WorkflowStatus.IN_ZOTERO, discovered_at=NOW),
        journal_issns=("0006-341X",),
    )
    path = tmp_path / "Papers" / "unrelated-filename.md"
    path.parent.mkdir()
    path.write_text(render_paper_markdown(paper, ("ada-author",)), encoding="utf-8")
    return path


def state(path):
    parsed = parse_paper_state(path, safe_write.read_text_exact(path), path.parent.parent / "Authors")
    assert parsed is not None
    return parsed


def update(path, **changes):
    current = state(path)
    frontmatter = dict(current.frontmatter)
    frontmatter.update(changes)
    path.write_bytes(serialize_document(frontmatter, current.body).encode("utf-8"))


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("crlf", [False, True])
def test_linkage_changes_only_null_or_missing_key_and_preserves_complete_body(paper_path, tmp_path, missing, crlf):
    current = state(paper_path)
    frontmatter = dict(current.frontmatter)
    frontmatter["custom_field"] = {"nested": ["retain", 3, None]}
    if missing:
        del frontmatter["zotero_key"]
    body = current.body + "\n## Notes\n\nHuman note: 保留.\n\n## Custom\n\n```yaml\nstatus: untouched\n```\n"
    contents = serialize_document(frontmatter, body)
    if crlf:
        contents = contents.replace("\n", "\r\n")
    paper_path.write_bytes(contents.encode("utf-8"))
    before = state(paper_path)

    result = link_paper_to_zotero(tmp_path, PAPER_ID, verified_identity())

    after = state(paper_path)
    assert result.outcome is LinkageOutcome.LINKED
    assert result.paper_id == PAPER_ID and result.path == paper_path
    assert after.zotero_key == KEY
    assert after.status is WorkflowStatus.IN_ZOTERO
    assert after.problems == ()
    expected = dict(before.frontmatter, zotero_key=KEY)
    assert after.frontmatter == expected
    assert after.body.encode("utf-8") == before.body.encode("utf-8")
    assert list(tmp_path.iterdir()) == [paper_path.parent]
    assert list(paper_path.parent.iterdir()) == [paper_path]


@pytest.mark.parametrize("key", [KEY, "WRONG001", "stale key"])
def test_existing_non_null_key_is_preserved_without_write(paper_path, tmp_path, key):
    update(paper_path, zotero_key=key)
    before = paper_path.read_bytes()
    result = link_paper_to_zotero(tmp_path, PAPER_ID, verified_identity())
    assert result.outcome is LinkageOutcome.ALREADY_LINKED
    assert paper_path.read_bytes() == before


@pytest.mark.parametrize("status", ["candidate", "kept", "rejected"])
def test_wrong_status_cannot_be_linked(paper_path, tmp_path, status):
    update(paper_path, status=status)
    before = paper_path.read_bytes()
    result = link_paper_to_zotero(tmp_path, PAPER_ID, verified_identity())
    assert result.outcome is LinkageOutcome.STATE_CONFLICT
    assert paper_path.read_bytes() == before


@pytest.mark.parametrize("doi", ["10.5555/changed", None])
def test_current_paper_doi_must_still_match_verification(paper_path, tmp_path, doi):
    current = state(paper_path)
    ids = dict(current.frontmatter["external_ids"], doi=doi)
    update(paper_path, doi=doi, external_ids=ids)
    before = paper_path.read_bytes()
    result = link_paper_to_zotero(tmp_path, PAPER_ID, verified_identity())
    assert result.outcome is LinkageOutcome.STATE_CONFLICT
    assert paper_path.read_bytes() == before


def test_doi_url_and_bare_doi_are_same_linkage_identity(paper_path, tmp_path):
    current = state(paper_path)
    url = "https://doi.org/10.5555/LINKAGE"
    update(paper_path, doi=url, external_ids=dict(current.frontmatter["external_ids"], doi=url))
    assert link_paper_to_zotero(tmp_path, PAPER_ID, verified_identity()).outcome is LinkageOutcome.LINKED


@pytest.mark.parametrize("changes", [
    {"title": None}, {"status": "unknown"}, {"zotero_key": []},
    {"zotero_key": ""}, {"external_ids": []}, {"sources": "invalid"},
])
def test_malformed_target_is_not_updated(paper_path, tmp_path, changes):
    update(paper_path, **changes)
    before = paper_path.read_bytes()
    result = link_paper_to_zotero(tmp_path, PAPER_ID, verified_identity())
    assert result.outcome is LinkageOutcome.INVALID_PAPER
    assert paper_path.read_bytes() == before


def test_duplicate_uuid_locations_are_rejected(paper_path, tmp_path):
    sibling = paper_path.with_name("duplicate.md")
    sibling.write_bytes(paper_path.read_bytes())
    before = {p: p.read_bytes() for p in (paper_path, sibling)}
    result = link_paper_to_zotero(tmp_path, PAPER_ID, verified_identity())
    assert result.outcome is LinkageOutcome.INVALID_PAPER
    assert {p: p.read_bytes() for p in before} == before


def test_missing_papers_directory_creates_nothing(tmp_path):
    output = tmp_path / "missing"
    result = link_paper_to_zotero(output, PAPER_ID, verified_identity())
    assert result.outcome is LinkageOutcome.NOT_FOUND
    assert not output.exists()


def test_non_directory_papers_path_is_rejected(tmp_path):
    path = tmp_path / "Papers"
    path.write_bytes(b"User data")
    result = link_paper_to_zotero(tmp_path, PAPER_ID, verified_identity())
    assert result.outcome is LinkageOutcome.IO_FAILURE
    assert path.read_bytes() == b"User data"


@pytest.mark.parametrize("operation", ["stat", "scan"])
def test_location_inspection_failure_is_structured_without_write(paper_path, tmp_path, monkeypatch, operation):
    before = paper_path.read_bytes()
    if operation == "stat":
        original = Path.is_symlink

        def fail(path):
            if path == paper_path.parent:
                raise PermissionError("SECRET error")
            return original(path)

        monkeypatch.setattr(Path, "is_symlink", fail)
    else:
        def fail(path, pattern):
            raise PermissionError("SECRET error")

        monkeypatch.setattr(Path, "glob", fail)
    result = link_paper_to_zotero(tmp_path, PAPER_ID, verified_identity())
    assert result.outcome is LinkageOutcome.IO_FAILURE
    assert "SECRET" not in result.message
    assert paper_path.read_bytes() == before


@pytest.mark.parametrize("directory_link", [False, True])
def test_symlink_directory_or_candidate_never_writes_outside_workspace(paper_path, tmp_path, directory_link):
    output = tmp_path / "other-workspace"
    output.mkdir()
    if directory_link:
        (output / "Papers").symlink_to(paper_path.parent)
    else:
        (output / "Papers").mkdir()
        (output / "Papers" / "linked.md").symlink_to(paper_path)
    before = paper_path.read_bytes()
    result = link_paper_to_zotero(output, PAPER_ID, verified_identity())
    assert result.outcome in {LinkageOutcome.IO_FAILURE, LinkageOutcome.INVALID_PAPER}
    assert paper_path.read_bytes() == before


@pytest.mark.parametrize("malformed", [False, True])
def test_non_regular_or_unreadable_uuid_candidate_is_not_written(tmp_path, malformed):
    papers = tmp_path / "Papers"
    papers.mkdir()
    path = papers / "unsafe.md"
    if malformed:
        path.write_bytes(b"---\ntype: paper\nid: [broken")
        before = path.read_bytes()
    else:
        path.mkdir()
    result = link_paper_to_zotero(tmp_path, PAPER_ID, verified_identity())
    assert result.outcome is LinkageOutcome.INVALID_PAPER
    if malformed:
        assert path.read_bytes() == before
    else:
        assert path.is_dir()


def test_unreadable_sibling_cannot_hide_a_duplicate_uuid(paper_path, tmp_path, monkeypatch):
    sibling = paper_path.with_name("unreadable.md")
    sibling.write_bytes(paper_path.read_bytes())
    before = paper_path.read_bytes()
    original = linkage.read_text_exact

    def read(path):
        if path == sibling:
            raise PermissionError("SECRET details")
        return original(path)

    monkeypatch.setattr(linkage, "read_text_exact", read)
    result = link_paper_to_zotero(tmp_path, PAPER_ID, verified_identity())
    assert result.outcome is LinkageOutcome.IO_FAILURE
    assert "SECRET" not in result.message
    assert paper_path.read_bytes() == before


@pytest.mark.parametrize("change", ["uuid", "disappear", "unreadable", "malformed"])
def test_target_is_reread_after_relocation(paper_path, tmp_path, monkeypatch, change):
    original = linkage.read_text_exact
    reads = 0

    def read(path):
        nonlocal reads
        reads += 1
        if reads == 2:
            if change == "uuid":
                update(path, id="22222222-2222-4222-8222-222222222222")
            elif change == "disappear":
                path.unlink()
            elif change == "malformed":
                update(path, title=None)
            else:
                raise PermissionError("read denied")
        return original(path)

    before = paper_path.read_bytes()
    monkeypatch.setattr(linkage, "read_text_exact", read)
    result = link_paper_to_zotero(tmp_path, PAPER_ID, verified_identity())
    expected = {
        "uuid": LinkageOutcome.STATE_CONFLICT, "disappear": LinkageOutcome.STATE_CONFLICT,
        "unreadable": LinkageOutcome.IO_FAILURE, "malformed": LinkageOutcome.INVALID_PAPER,
    }
    assert result.outcome is expected[change]
    if change != "disappear":
        assert state(paper_path).zotero_key is None
    if change == "unreadable":
        assert paper_path.read_bytes() == before


@pytest.mark.parametrize("change", ["body", "status", "doi", "uuid", "key"])
def test_complete_content_compare_preserves_concurrent_edit(paper_path, tmp_path, monkeypatch, change):
    concurrent = None

    def edit_before_compare(path, updated, *, expected_contents):
        nonlocal concurrent
        if change == "body":
            path.write_bytes((expected_contents + "\nConcurrent human note.\n").encode())
        elif change == "status":
            update(path, status="kept")
        elif change == "doi":
            update(path, doi="10.5555/new")
        elif change == "uuid":
            update(path, id="22222222-2222-4222-8222-222222222222")
        else:
            update(path, zotero_key="NEWKEY01")
        concurrent = path.read_bytes()
        safe_write.replace_text_if_unchanged(path, updated, expected_contents=expected_contents)

    monkeypatch.setattr(linkage, "replace_text_if_unchanged", edit_before_compare)
    result = link_paper_to_zotero(tmp_path, PAPER_ID, verified_identity())
    assert result.outcome is LinkageOutcome.STATE_CONFLICT
    assert paper_path.read_bytes() == concurrent


def test_disappearance_during_compare_is_conflict_without_recreation(paper_path, tmp_path, monkeypatch):
    def disappear(path, updated, *, expected_contents):
        path.unlink()
        safe_write.replace_text_if_unchanged(path, updated, expected_contents=expected_contents)

    monkeypatch.setattr(linkage, "replace_text_if_unchanged", disappear)
    result = link_paper_to_zotero(tmp_path, PAPER_ID, verified_identity())
    assert result.outcome is LinkageOutcome.STATE_CONFLICT
    assert not paper_path.exists()


def test_compare_read_failure_is_io_failure_without_write(paper_path, tmp_path, monkeypatch):
    before = paper_path.read_bytes()

    def unreadable(path, updated, *, expected_contents):
        raise safe_write.CompareReadError("SECRET error")

    monkeypatch.setattr(linkage, "replace_text_if_unchanged", unreadable)
    result = link_paper_to_zotero(tmp_path, PAPER_ID, verified_identity())
    assert result.outcome is LinkageOutcome.IO_FAILURE
    assert "SECRET" not in result.message
    assert paper_path.read_bytes() == before


def test_atomic_write_failure_preserves_original_and_cleans_temp(paper_path, tmp_path, monkeypatch):
    before = paper_path.read_bytes()

    def fail_replace(*args):
        raise OSError("SECRET error")

    monkeypatch.setattr(safe_write.os, "replace", fail_replace)
    result = link_paper_to_zotero(tmp_path, PAPER_ID, verified_identity())
    assert result.outcome is LinkageOutcome.IO_FAILURE
    assert "SECRET" not in result.message
    assert paper_path.read_bytes() == before
    assert tuple(paper_path.parent.glob(".*.tmp")) == ()


@pytest.mark.parametrize("outcome", [
    ZoteroReadOutcome.NOT_FOUND, ZoteroReadOutcome.DUPLICATE,
    ZoteroReadOutcome.API_FAILURE, ZoteroReadOutcome.INVALID_RESPONSE,
    ZoteroReadOutcome.SERVER_ID_MISMATCH, ZoteroReadOutcome.INVALID_DOI,
])
def test_failed_identity_resolution_cannot_mutate_paper(paper_path, tmp_path, outcome):
    before = paper_path.read_bytes()
    identity = ZoteroIdentityResult(outcome, None, "Failed read.")
    result = link_paper_to_zotero(tmp_path, PAPER_ID, identity)
    assert result.outcome is LinkageOutcome.UNVERIFIED
    assert paper_path.read_bytes() == before


def test_read_identity_inspect_attachment_then_independently_link(paper_path, tmp_path):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "GET"
        if request.url.path.endswith("/PDF00001/file"):
            return httpx.Response(302, headers={
                "Zotero-Server-ID": SERVER, "Location": "file:///private/synthetic/paper.pdf",
            })
        if request.url.path.endswith("/children"):
            return httpx.Response(200, json=[{
                "key": "PDF00001", "data": {
                    "itemType": "attachment", "parentItem": KEY, "contentType": "application/pdf",
                },
            }], headers={"Zotero-Server-ID": SERVER, "Total-Results": "1", "Last-Modified-Version": "4"})
        return httpx.Response(200, json=[{
            "key": KEY, "data": {"itemType": "journalArticle", "DOI": DOI},
        }], headers={"Zotero-Server-ID": SERVER, "Total-Results": "1", "Last-Modified-Version": "4"})

    with ZoteroLocalClient(transport=httpx.MockTransport(respond)) as client:
        identity = client.resolve_identity(DOI)
        children = client.inspect_attachments(identity.item)
    assert children.has_pdf is True
    assert len(requests) == 3
    before = state(paper_path)
    assert link_paper_to_zotero(tmp_path, PAPER_ID, identity).outcome is LinkageOutcome.LINKED
    after = state(paper_path)
    assert after.frontmatter == dict(before.frontmatter, zotero_key=KEY)
    assert after.body == before.body
