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
    Author,
    CanonicalMetadata,
    CanonicalPaper,
    ExternalIds,
    MetadataSource,
    Workflow,
    WorkflowStatus,
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
    paper = CanonicalPaper(
        id=PAPER_ID,
        metadata=CanonicalMetadata(title="Linkage Paper", journal="Biometrics", abstract="Original abstract."),
        external_ids=ExternalIds.model_validate({"doi": DOI, "openalex": "https://openalex.org/W123", "pmid": "123"}),
        authors=(Author(name="Ada Author"),),
        sources=(MetadataSource(provider="openalex", record_id="https://openalex.org/W123", retrieved_at=NOW),),
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


def file_identities(path):
    parent=path.parent.lstat();target=path.lstat()
    return dict(expected_directory_identity=(parent.st_dev,parent.st_ino),
                expected_file_identity=(target.st_dev,target.st_ino))


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

    result = link_paper_to_zotero(state(paper_path), verified_identity(), **file_identities(paper_path))

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
    result = link_paper_to_zotero(state(paper_path), verified_identity(), **file_identities(paper_path))
    assert result.outcome is LinkageOutcome.ALREADY_LINKED
    assert paper_path.read_bytes() == before


@pytest.mark.parametrize("status", ["candidate", "kept", "rejected"])
def test_wrong_status_cannot_be_linked(paper_path, tmp_path, status):
    update(paper_path, status=status)
    before = paper_path.read_bytes()
    result = link_paper_to_zotero(state(paper_path), verified_identity(), **file_identities(paper_path))
    assert result.outcome is LinkageOutcome.STATE_CONFLICT
    assert paper_path.read_bytes() == before


@pytest.mark.parametrize("doi", ["10.5555/changed", None])
def test_current_paper_doi_must_still_match_verification(paper_path, tmp_path, doi):
    current = state(paper_path)
    ids = dict(current.frontmatter["external_ids"], doi=doi)
    update(paper_path, doi=doi, external_ids=ids)
    before = paper_path.read_bytes()
    result = link_paper_to_zotero(state(paper_path), verified_identity(), **file_identities(paper_path))
    assert result.outcome is (LinkageOutcome.INVALID_PAPER if doi is None else LinkageOutcome.STATE_CONFLICT)
    assert paper_path.read_bytes() == before


def test_doi_url_and_bare_doi_are_same_linkage_identity(paper_path, tmp_path):
    current = state(paper_path)
    url = "https://doi.org/10.5555/LINKAGE"
    update(paper_path, doi=url, external_ids=dict(current.frontmatter["external_ids"], doi=url))
    assert link_paper_to_zotero(state(paper_path), verified_identity(), **file_identities(paper_path)).outcome is LinkageOutcome.LINKED


@pytest.mark.parametrize("changes", [
    {"title": None}, {"status": "unknown"}, {"zotero_key": []},
    {"zotero_key": ""}, {"external_ids": []}, {"sources": "invalid"},
])
def test_malformed_target_is_not_updated(paper_path, tmp_path, changes):
    update(paper_path, **changes)
    before = paper_path.read_bytes()
    result = link_paper_to_zotero(state(paper_path), verified_identity(), **file_identities(paper_path))
    assert result.outcome is LinkageOutcome.INVALID_PAPER
    assert paper_path.read_bytes() == before


@pytest.mark.parametrize("change", ["body", "status", "doi", "uuid", "key"])
def test_complete_content_compare_preserves_concurrent_edit(paper_path, tmp_path, monkeypatch, change):
    concurrent = None

    def edit_before_compare(path, updated, *, expected_contents, **kwargs):
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
        safe_write.replace_regular_text_at_identity(path, updated, expected_contents=expected_contents,**kwargs)

    monkeypatch.setattr(linkage, "replace_regular_text_at_identity", edit_before_compare)
    result = link_paper_to_zotero(state(paper_path), verified_identity(), **file_identities(paper_path))
    assert result.outcome is LinkageOutcome.STATE_CONFLICT
    assert paper_path.read_bytes() == concurrent


def test_disappearance_during_compare_is_conflict_without_recreation(paper_path, tmp_path, monkeypatch):
    def disappear(path, updated, *, expected_contents, **kwargs):
        path.unlink()
        safe_write.replace_regular_text_at_identity(path, updated, expected_contents=expected_contents,**kwargs)

    monkeypatch.setattr(linkage, "replace_regular_text_at_identity", disappear)
    result = link_paper_to_zotero(state(paper_path), verified_identity(), **file_identities(paper_path))
    assert result.outcome is LinkageOutcome.STATE_CONFLICT
    assert not paper_path.exists()


def test_compare_read_failure_is_io_failure_without_write(paper_path, tmp_path, monkeypatch):
    before = paper_path.read_bytes()

    def unreadable(path, updated, *, expected_contents, **kwargs):
        raise safe_write.CompareReadError("SECRET error")

    monkeypatch.setattr(linkage, "replace_regular_text_at_identity", unreadable)
    result = link_paper_to_zotero(state(paper_path), verified_identity(), **file_identities(paper_path))
    assert result.outcome is LinkageOutcome.IO_FAILURE
    assert "SECRET" not in result.message
    assert paper_path.read_bytes() == before


def test_atomic_write_failure_preserves_original_and_cleans_temp(paper_path, tmp_path, monkeypatch):
    before = paper_path.read_bytes()

    def fail_replace(*args,**kwargs):
        raise OSError("SECRET error")

    monkeypatch.setattr(safe_write.os, "replace", fail_replace)
    result = link_paper_to_zotero(state(paper_path), verified_identity(), **file_identities(paper_path))
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
    result = link_paper_to_zotero(state(paper_path), identity, **file_identities(paper_path))
    assert result.outcome is LinkageOutcome.UNVERIFIED
    assert paper_path.read_bytes() == before


def test_read_identity_inspect_attachment_then_link_original_action_snapshot(paper_path, tmp_path):
    requests = []
    actual_file = tmp_path / 'paper.pdf'
    actual_file.write_bytes(b'nonempty existing attachment')

    def respond(request):
        requests.append(request)
        assert request.method == "GET"
        if request.url.path.endswith("/PDF00001/file"):
            return httpx.Response(302, headers={
                "Zotero-Server-ID": SERVER, "Location": actual_file.as_uri(),
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

    snapshot = state(paper_path)
    with ZoteroLocalClient(transport=httpx.MockTransport(respond)) as client:
        identity = client.resolve_identity(DOI)
        children = client.inspect_attachments(identity.item)
    assert children.has_pdf is True
    assert len(requests) == 3
    before = state(paper_path)
    assert link_paper_to_zotero(snapshot, identity, **file_identities(paper_path)).outcome is LinkageOutcome.LINKED
    after = state(paper_path)
    assert after.frontmatter == dict(before.frontmatter, zotero_key=KEY)
    assert after.body == before.body


@pytest.mark.parametrize('kind',['symlink','directory','fifo','missing'])
def test_final_regular_compare_rejects_location_substitution(paper_path,tmp_path,monkeypatch,kind):
    import os
    snapshot=state(paper_path);original=paper_path.read_bytes();preserved=tmp_path/'preserved.md'
    calls=[]
    def substitute(path,contents,*,expected_contents,**kwargs):
        calls.append(path)
        assert expected_contents==snapshot.original
        path.rename(preserved)
        if kind=='symlink':path.symlink_to(preserved)
        elif kind=='directory':
            path.mkdir();(path/'sentinel').write_bytes(b'human object')
        elif kind=='fifo':os.mkfifo(path)
        safe_write.replace_regular_text_at_identity(path,contents,expected_contents=expected_contents,**kwargs)
    monkeypatch.setattr(linkage,'replace_regular_text_at_identity',substitute)
    result=link_paper_to_zotero(snapshot,verified_identity(),**file_identities(paper_path))
    assert result.outcome is LinkageOutcome.STATE_CONFLICT and calls==[paper_path]
    assert preserved.read_bytes()==original
    assert state(preserved).zotero_key is None
    if kind=='symlink':assert paper_path.is_symlink() and paper_path.read_bytes()==original
    elif kind=='directory':assert (paper_path/'sentinel').read_bytes()==b'human object'
    elif kind=='fifo':assert not paper_path.is_file()
    else:assert not paper_path.exists()


def test_linkage_uses_snapshot_without_semantic_read_or_parse(paper_path,monkeypatch):
    snapshot=state(paper_path)
    def forbidden(*args,**kwargs):raise AssertionError('Second semantic Paper read')
    monkeypatch.setattr(Path,'read_bytes',forbidden)
    result=link_paper_to_zotero(snapshot,verified_identity(),**file_identities(paper_path))
    assert result.outcome is LinkageOutcome.LINKED
    with paper_path.open('rb') as handle:
        after=parse_paper_state(paper_path,handle.read().decode(),paper_path.parent.parent/'Authors')
    assert after.zotero_key==KEY and after.body==snapshot.body


def test_unsafe_papers_directory_after_snapshot_never_links_outside(paper_path,tmp_path):
    snapshot=state(paper_path);before=paper_path.read_bytes();location=file_identities(paper_path)
    directory=paper_path.parent;preserved=tmp_path/'outside';directory.rename(preserved)
    directory.symlink_to(preserved,target_is_directory=True)
    result=link_paper_to_zotero(snapshot,verified_identity(),**location)
    assert result.outcome is LinkageOutcome.STATE_CONFLICT
    assert (preserved/paper_path.name).read_bytes()==before


@pytest.mark.parametrize('substitution',['parent_symlink','same_bytes_inode','different_directory'])
def test_original_action_location_substitution_at_real_linkage_boundary(paper_path,tmp_path,monkeypatch,substitution):
    snapshot=state(paper_path);location=file_identities(paper_path);original=paper_path.read_bytes()
    preserved=tmp_path/'preserved-object';calls=[]
    def substitute(path,contents,**kwargs):
        calls.append(path)
        assert kwargs['expected_contents']==snapshot.original
        assert kwargs['expected_directory_identity']==location['expected_directory_identity']
        assert kwargs['expected_file_identity']==location['expected_file_identity']
        if substitution=='same_bytes_inode':path.rename(preserved);path.write_bytes(original)
        else:
            path.parent.rename(preserved)
            if substitution=='parent_symlink':path.parent.symlink_to(preserved,target_is_directory=True)
            else:path.parent.mkdir();path.write_bytes(original)
        safe_write.replace_regular_text_at_identity(path,contents,**kwargs)
    monkeypatch.setattr(linkage,'replace_regular_text_at_identity',substitute)
    result=link_paper_to_zotero(snapshot,verified_identity(),**location)
    assert result.outcome is LinkageOutcome.STATE_CONFLICT and calls==[paper_path]
    moved=preserved if substitution=='same_bytes_inode' else preserved/paper_path.name
    assert moved.read_bytes()==original and paper_path.read_bytes()==original
    assert state(moved).zotero_key is None and state(paper_path).zotero_key is None
    if substitution=='parent_symlink':assert paper_path.parent.is_symlink()
