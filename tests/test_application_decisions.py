from __future__ import annotations

import inspect
import os
import stat
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
import httpx
import yaml

import literature_monitor.application.decisions as decisions
import literature_monitor.safe_write as safe_write_module
from literature_monitor.application.decisions import (
    DecisionOutcome,
    keep_paper,
    reconcile_paper_with_zotero,
    reject_paper,
)
from literature_monitor.markdown_state import parse_paper_state
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
from literature_monitor.safe_write import ContentChangedError
from literature_monitor.zotero_local import LOCAL_API_BASE, ZoteroLocalClient


NOW = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def forbid_unconfigured_zotero_access(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden():
        pytest.fail("Ineligible/local-only decisions must not contact Zotero")
    monkeypatch.setattr(decisions, "_ZoteroLocalClient", forbidden)


@pytest.fixture
def zotero_reads(monkeypatch: pytest.MonkeyPatch):
    clients = []

    def configure(*responses):
        pending = list(responses)
        requests = []

        def respond(request):
            requests.append(request)
            assert request.method == "GET"
            assert request.url.path == "/api/users/0/items"
            assert request.url.params["itemType"] == "-attachment"
            assert request.headers["Zotero-API-Version"] == "3"
            assert "Zotero-API-Key" not in request.headers and "Authorization" not in request.headers
            assert pending, "Unexpected Zotero request"
            response = pending.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        def factory():
            client = ZoteroLocalClient(transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr(decisions, "_ZoteroLocalClient", factory)
        return requests

    yield configure
    assert all(client._http.is_closed for client in clients)


def zotero_item(
    key="PARENT01",
    doi="10.5555/decision",
    item_type="journalArticle",
    **metadata,
):
    return {
        "key": key,
        "data": {"key": key, "itemType": item_type, "DOI": doi, **metadata},
    }


def library_page(items=(), *, total=None, next_start=None, server="local-instance", version="4"):
    headers = {"Total-Results": str(len(items) if total is None else total)}
    if server is not None:
        headers["Zotero-Server-ID"] = server
    if version is not None:
        headers["Last-Modified-Version"] = version
    if next_start is not None:
        headers["Link"] = (
            f'<{LOCAL_API_BASE}/users/0/items?format=json&include=data&limit=100'
            f'&start={next_start}&itemType=-attachment>; rel="next"'
        )
    return httpx.Response(200, json=list(items), headers=headers)


def write_paper(
    output_dir: Path,
    paper_id: UUID,
    *,
    filename: str = "paper.md",
    status: WorkflowStatus = WorkflowStatus.CANDIDATE,
    title: str = "Decision Paper",
    zotero_key: str | None = None,
) -> Path:
    paper = CanonicalPaper(
        id=paper_id,
        metadata=CanonicalMetadata(
            title=title,
            journal="Biometrics",
            publication_date=date(2026, 9, 20),
            abstract="Decision abstract.",
            author_keywords=("statistics", "decisions"),
        ),
        external_ids=ExternalIds.model_validate(
            {
                "doi": "10.5555/decision",
                "openalex": "https://openalex.org/W-DECISION",
                "pmid": "12345678",
            }
        ),
        authors=(Author(name="Ada Author"),),
        sources=(
            MetadataSource(
                provider="openalex",
                record_id="https://openalex.org/W-DECISION",
                retrieved_at=NOW,
            ),
        ),
        workflow=Workflow(
            status=status,
            discovered_at=NOW,
            zotero_key=zotero_key,
        ),
    )
    path = output_dir / "Papers" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_paper_markdown(paper, ("ada-author",)),
        encoding="utf-8",
    )
    return path


def document_parts(path: Path) -> tuple[dict[str, object], str]:
    opening, payload, body = path.read_text(encoding="utf-8").split("---", maxsplit=2)
    assert opening == ""
    values = yaml.safe_load(payload)
    assert isinstance(values, dict)
    return values, body


def replace_frontmatter(path: Path, **updates: object) -> None:
    values, body = document_parts(path)
    values.update(updates)
    rendered = yaml.safe_dump(values, sort_keys=False, allow_unicode=True).rstrip()
    path.write_text(f"---\n{rendered}\n---{body}", encoding="utf-8")


@pytest.mark.parametrize(
    ("action", "initial", "expected", "target"),
    (
        (keep_paper, WorkflowStatus.CANDIDATE, WorkflowStatus.CANDIDATE, WorkflowStatus.KEPT),
        (
            reject_paper,
            WorkflowStatus.CANDIDATE,
            WorkflowStatus.CANDIDATE,
            WorkflowStatus.REJECTED,
        ),
        (
            reconcile_paper_with_zotero,
            WorkflowStatus.KEPT,
            WorkflowStatus.KEPT,
            WorkflowStatus.IN_ZOTERO,
        ),
    ),
)
def test_successful_transitions_change_only_workflow_status(
    action: object,
    initial: WorkflowStatus,
    expected: WorkflowStatus,
    target: WorkflowStatus,
    tmp_path: Path,
    zotero_reads,
) -> None:
    paper_id = UUID("11111111-1111-4111-8111-111111111111")
    path = write_paper(
        tmp_path,
        paper_id,
        status=initial,
        zotero_key="PARENT01",
    )
    if action is reconcile_paper_with_zotero:
        zotero_reads(library_page([zotero_item()]))
    before_frontmatter, before_body = document_parts(path)

    result = action(tmp_path, paper_id, expected)  # type: ignore[operator]

    after_frontmatter, after_body = document_parts(path)
    assert result.outcome is DecisionOutcome.UPDATED
    assert result.paper_id == paper_id
    assert result.expected_status is expected
    assert result.current_status is initial
    assert result.resulting_status is target
    assert result.path == path
    assert after_frontmatter["id"] == before_frontmatter["id"] == str(paper_id)
    assert after_frontmatter["status"] == target.value
    before_frontmatter.pop("status")
    after_frontmatter.pop("status")
    assert after_frontmatter == before_frontmatter
    assert after_body == before_body


def test_status_only_mutation_preserves_unknown_frontmatter_and_complete_human_body(
    tmp_path: Path,
) -> None:
    paper_id = UUID("22222222-2222-4222-8222-222222222222")
    path = write_paper(
        tmp_path,
        paper_id,
        status=WorkflowStatus.CANDIDATE,
        zotero_key="ZOT123",
    )
    values, body = document_parts(path)
    values["custom_field"] = {"nested": ["retain", 3]}
    body = (
        body
        + "\nFree-form prose.\n"
        + "\n## Notes\n\nHuman note.\n\n"
        + "### Note child\n\nKeep child.\n\n"
        + "## Custom Section\n\n~~~text\nstatus: not workflow\n~~~\n"
    )
    rendered = yaml.safe_dump(values, sort_keys=False, allow_unicode=True).rstrip()
    path.write_text(f"---\n{rendered}\n---{body}", encoding="utf-8")
    before_frontmatter, before_body = document_parts(path)

    result = keep_paper(tmp_path, paper_id, WorkflowStatus.CANDIDATE)

    after_frontmatter, after_body = document_parts(path)
    assert result.outcome is DecisionOutcome.UPDATED
    assert after_frontmatter["status"] == "kept"
    assert after_frontmatter["custom_field"] == {"nested": ["retain", 3]}
    assert "versions" not in after_frontmatter and "preferred_version" not in after_frontmatter
    assert after_frontmatter["sources"] == before_frontmatter["sources"]
    assert after_frontmatter["title"] == before_frontmatter["title"]
    assert after_frontmatter["journal"] == before_frontmatter["journal"]
    assert after_frontmatter["discovered_at"] == before_frontmatter["discovered_at"]
    assert after_frontmatter["zotero_key"] == "ZOT123"
    before_frontmatter.pop("status")
    after_frontmatter.pop("status")
    assert after_frontmatter == before_frontmatter
    assert after_body == before_body


def test_only_three_public_decision_actions_exist() -> None:
    public_functions = {
        name
        for name, value in inspect.getmembers(decisions, inspect.isfunction)
        if not name.startswith("_")
    }

    assert public_functions == {
        "keep_paper",
        "reject_paper",
        "reconcile_paper_with_zotero",
    }
    assert not hasattr(decisions, "set_status")
    for action_name in public_functions:
        assert tuple(inspect.signature(getattr(decisions, action_name)).parameters) == (
            "output_dir",
            "paper_id",
            "expected_status",
        )


@pytest.mark.parametrize("action,status", [
    (keep_paper, WorkflowStatus.CANDIDATE),
    (reconcile_paper_with_zotero, WorkflowStatus.KEPT),
])
def test_unknown_uuid_returns_not_found_without_writes(tmp_path: Path, action, status) -> None:
    existing_id = UUID("33333333-3333-4333-8333-333333333333")
    path = write_paper(tmp_path, existing_id, status=status)
    before = path.read_bytes()

    result = action(
        tmp_path,
        UUID("33333333-3333-4333-8333-444444444444"),
        status,
    )

    assert result.outcome is DecisionOutcome.NOT_FOUND
    assert path.read_bytes() == before


def test_unrelated_malformed_paper_does_not_block_valid_keep(tmp_path: Path) -> None:
    paper_id = UUID("34343434-3434-4434-8434-343434343434")
    valid = write_paper(
        tmp_path,
        paper_id,
        filename="valid.md",
        status=WorkflowStatus.CANDIDATE,
    )
    malformed = tmp_path / "Papers" / "a-malformed.md"
    malformed.write_text("---\ntype: paper\n", encoding="utf-8")
    malformed_before = malformed.read_bytes()

    result = keep_paper(tmp_path, paper_id, WorkflowStatus.CANDIDATE)

    assert result.outcome is DecisionOutcome.UPDATED
    assert document_parts(valid)[0]["status"] == "kept"
    assert malformed.read_bytes() == malformed_before


def test_unrelated_invalid_uuid_paper_does_not_block_valid_reject(
    tmp_path: Path,
) -> None:
    paper_id = UUID("35353535-3535-4535-8535-353535353535")
    valid = write_paper(
        tmp_path,
        paper_id,
        filename="valid.md",
        status=WorkflowStatus.CANDIDATE,
    )
    invalid = write_paper(
        tmp_path,
        UUID("35353535-3535-4535-8535-aaaaaaaaaaaa"),
        filename="a-invalid.md",
    )
    replace_frontmatter(invalid, id="not-a-uuid")
    invalid_before = invalid.read_bytes()

    result = reject_paper(tmp_path, paper_id, WorkflowStatus.CANDIDATE)

    assert result.outcome is DecisionOutcome.UPDATED
    assert document_parts(valid)[0]["status"] == "rejected"
    assert invalid.read_bytes() == invalid_before


def test_unrelated_non_regular_markdown_does_not_block_valid_decision(
    tmp_path: Path,
) -> None:
    paper_id = UUID("36363636-3636-4636-8636-363636363636")
    valid = write_paper(tmp_path, paper_id, filename="valid.md")
    non_regular = tmp_path / "Papers" / "a-directory.md"
    non_regular.mkdir()

    result = keep_paper(tmp_path, paper_id, WorkflowStatus.CANDIDATE)

    assert result.outcome is DecisionOutcome.UPDATED
    assert document_parts(valid)[0]["status"] == "kept"
    assert non_regular.is_dir()


def test_unrelated_symlink_markdown_does_not_block_valid_decision(
    tmp_path: Path,
) -> None:
    paper_id = UUID("37373737-3737-4737-8737-373737373737")
    valid = write_paper(tmp_path, paper_id, filename="valid.md")
    outside_id = UUID("37373737-3737-4737-8737-aaaaaaaaaaaa")
    outside = write_paper(tmp_path / "outside", outside_id)
    link = tmp_path / "Papers" / "a-linked.md"
    link.symlink_to(outside)
    outside_before = outside.read_bytes()

    result = keep_paper(tmp_path, paper_id, WorkflowStatus.CANDIDATE)

    assert result.outcome is DecisionOutcome.UPDATED
    assert document_parts(valid)[0]["status"] == "kept"
    assert link.is_symlink()
    assert outside.read_bytes() == outside_before


def test_unreadable_sibling_conservatively_blocks_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_id = UUID("38383838-3838-4838-8838-383838383838")
    valid = write_paper(tmp_path, paper_id, filename="valid.md")
    unreadable = write_paper(
        tmp_path,
        UUID("38383838-3838-4838-8838-aaaaaaaaaaaa"),
        filename="a-unreadable.md",
    )
    valid_before = valid.read_bytes()
    unreadable_before = unreadable.read_bytes()
    original_read = decisions._read_text_exact

    def fail_one_read(path: Path) -> str:
        if path == unreadable:
            raise PermissionError("read denied")
        return original_read(path)

    monkeypatch.setattr(decisions, "_read_text_exact", fail_one_read)

    result = keep_paper(tmp_path, paper_id, WorkflowStatus.CANDIDATE)

    assert result.outcome is DecisionOutcome.IO_FAILURE
    assert result.path == unreadable
    assert "read denied" in result.message
    assert valid.read_bytes() == valid_before
    assert unreadable.read_bytes() == unreadable_before


def test_duplicate_uuid_is_invalid_and_mutates_neither_file(tmp_path: Path) -> None:
    paper_id = UUID("44444444-4444-4444-8444-444444444444")
    first = write_paper(tmp_path, paper_id, filename="one.md")
    second = tmp_path / "Papers" / "two.md"
    second.write_bytes(first.read_bytes())
    before = {first: first.read_bytes(), second: second.read_bytes()}

    result = reject_paper(tmp_path, paper_id, WorkflowStatus.CANDIDATE)

    assert result.outcome is DecisionOutcome.INVALID_PAPER
    assert "multiple locations" in result.message
    assert {path: path.read_bytes() for path in before} == before


def test_filename_and_title_have_no_identity_authority(tmp_path: Path) -> None:
    paper_id = UUID("55555555-5555-4555-8555-555555555555")
    path = write_paper(
        tmp_path,
        paper_id,
        filename="completely-unrelated-name.md",
        title="A title that does not identify the request",
    )

    result = keep_paper(tmp_path, paper_id, WorkflowStatus.CANDIDATE)

    assert result.outcome is DecisionOutcome.UPDATED
    assert result.path == path
    assert document_parts(path)[0]["status"] == "kept"


def test_symlink_candidate_for_requested_uuid_is_rejected_without_mutating_target(
    tmp_path: Path,
) -> None:
    paper_id = UUID("66666666-6666-4666-8666-666666666666")
    outside = write_paper(tmp_path / "outside", paper_id)
    papers_dir = tmp_path / "workspace" / "Papers"
    papers_dir.mkdir(parents=True)
    link = papers_dir / "linked.md"
    link.symlink_to(outside)
    before = outside.read_bytes()

    result = keep_paper(
        tmp_path / "workspace",
        paper_id,
        WorkflowStatus.CANDIDATE,
    )

    assert result.outcome is DecisionOutcome.INVALID_PAPER
    assert result.path == link
    assert "symlink" in result.message
    assert outside.read_bytes() == before


def test_non_regular_markdown_candidate_fails_closed(tmp_path: Path) -> None:
    papers_dir = tmp_path / "Papers"
    papers_dir.mkdir()
    non_regular = papers_dir / "unsafe.md"
    non_regular.mkdir()

    result = keep_paper(
        tmp_path,
        UUID("77777777-7777-4777-8777-777777777777"),
        WorkflowStatus.CANDIDATE,
    )

    assert result.outcome is DecisionOutcome.INVALID_PAPER
    assert result.path == non_regular


@pytest.mark.parametrize(
    ("updates", "expected_message"),
    (
        ({"title": None}, "title must be a non-empty string"),
        ({"status": "reviewing"}, "status is invalid"),
    ),
)
def test_parser_problem_for_requested_uuid_is_invalid_paper(
    updates: dict[str, object],
    expected_message: str,
    tmp_path: Path,
) -> None:
    paper_id = UUID("88888888-8888-4888-8888-888888888888")
    path = write_paper(tmp_path, paper_id)
    replace_frontmatter(path, **updates)
    before = path.read_bytes()

    result = keep_paper(tmp_path, paper_id, WorkflowStatus.CANDIDATE)

    assert result.outcome is DecisionOutcome.INVALID_PAPER
    assert expected_message in result.message
    assert path.read_bytes() == before


def test_uuid_change_between_relocation_and_second_read_is_safe_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_id = UUID("99999999-9999-4999-8999-999999999999")
    path = write_paper(tmp_path, paper_id)
    replacement_id = UUID("99999999-9999-4999-8999-aaaaaaaaaaaa")
    original_read = decisions._read_text_exact
    reads = 0

    def read_then_change(target: Path) -> str:
        nonlocal reads
        reads += 1
        if target == path and reads == 2:
            replace_frontmatter(path, id=str(replacement_id))
        return original_read(target)

    monkeypatch.setattr(decisions, "_read_text_exact", read_then_change)

    result = keep_paper(tmp_path, paper_id, WorkflowStatus.CANDIDATE)

    assert result.outcome is DecisionOutcome.INVALID_PAPER
    assert "UUID changed" in result.message
    assert document_parts(path)[0]["id"] == str(replacement_id)
    assert document_parts(path)[0]["status"] == "candidate"


def test_paper_becoming_invalid_between_relocation_and_second_parse_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    path = write_paper(tmp_path, paper_id)
    original_read = decisions._read_text_exact
    reads = 0

    def read_then_invalidate(target: Path) -> str:
        nonlocal reads
        reads += 1
        if target == path and reads == 2:
            replace_frontmatter(path, status="reviewing")
        return original_read(target)

    monkeypatch.setattr(decisions, "_read_text_exact", read_then_invalidate)

    result = keep_paper(tmp_path, paper_id, WorkflowStatus.CANDIDATE)

    assert result.outcome is DecisionOutcome.INVALID_PAPER
    assert "status is invalid" in result.message
    assert document_parts(path)[0]["status"] == "reviewing"


@pytest.mark.parametrize(
    ("disk_status", "expected_status", "action"),
    (
        (WorkflowStatus.KEPT, WorkflowStatus.CANDIDATE, keep_paper),
        (
            WorkflowStatus.IN_ZOTERO,
            WorkflowStatus.KEPT,
            reconcile_paper_with_zotero,
        ),
    ),
)
def test_expected_status_mismatch_is_state_conflict(
    disk_status: WorkflowStatus,
    expected_status: WorkflowStatus,
    action: object,
    tmp_path: Path,
) -> None:
    paper_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    path = write_paper(tmp_path, paper_id, status=disk_status)
    before = path.read_bytes()

    result = action(tmp_path, paper_id, expected_status)  # type: ignore[operator]

    assert result.outcome is DecisionOutcome.STATE_CONFLICT
    assert result.current_status is disk_status
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("disk_status", "action"),
    (
        (WorkflowStatus.KEPT, reject_paper),
        (WorkflowStatus.REJECTED, keep_paper),
        (WorkflowStatus.CANDIDATE, reconcile_paper_with_zotero),
        (WorkflowStatus.IN_ZOTERO, keep_paper),
        (WorkflowStatus.IN_ZOTERO, reject_paper),
        (WorkflowStatus.IN_ZOTERO, reconcile_paper_with_zotero),
    ),
)
def test_disallowed_transition_is_invalid_transition_when_expectation_matches(
    disk_status: WorkflowStatus,
    action: object,
    tmp_path: Path,
) -> None:
    paper_id = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
    path = write_paper(tmp_path, paper_id, status=disk_status)
    before = path.read_bytes()

    result = action(tmp_path, paper_id, disk_status)  # type: ignore[operator]

    assert result.outcome is DecisionOutcome.INVALID_TRANSITION
    assert result.current_status is disk_status
    assert result.resulting_status is None
    assert path.read_bytes() == before


def test_compare_before_replace_race_keeps_external_edit_and_reports_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    path = write_paper(tmp_path, paper_id)

    def concurrent_edit(
        target: Path,
        contents: str,
        *,
        expected_contents: str,
    ) -> None:
        assert target == path
        assert expected_contents == path.read_text(encoding="utf-8")
        external = expected_contents + "\nExternal edit remains.\n"
        path.write_text(external, encoding="utf-8")
        raise ContentChangedError("target changed after it was read")

    monkeypatch.setattr(
        decisions,
        "_replace_text_if_unchanged",
        concurrent_edit,
    )

    result = keep_paper(tmp_path, paper_id, WorkflowStatus.CANDIDATE)

    contents = path.read_text(encoding="utf-8")
    assert result.outcome is DecisionOutcome.STATE_CONFLICT
    assert "External edit remains." in contents
    assert document_parts(path)[0]["status"] == "candidate"


def test_candidate_read_failure_is_structured_io_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_id = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
    path = write_paper(tmp_path, paper_id)
    before = path.read_bytes()

    def fail_read(target: Path) -> str:
        raise PermissionError("read denied")

    monkeypatch.setattr(decisions, "_read_text_exact", fail_read)

    result = keep_paper(tmp_path, paper_id, WorkflowStatus.CANDIDATE)

    assert result.outcome is DecisionOutcome.IO_FAILURE
    assert "read denied" in result.message
    assert path.read_bytes() == before


def test_compare_verification_read_failure_is_structured_io_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_id = UUID("edededed-eded-4ded-8ded-edededededed")
    path = write_paper(tmp_path, paper_id)
    before = path.read_bytes()

    def fail_compare(
        target: Path,
        contents: str,
        *,
        expected_contents: str,
    ) -> None:
        raise safe_write_module.CompareReadError("verify denied")

    monkeypatch.setattr(decisions, "_replace_text_if_unchanged", fail_compare)

    result = keep_paper(tmp_path, paper_id, WorkflowStatus.CANDIDATE)

    assert result.outcome is DecisionOutcome.IO_FAILURE
    assert "verify denied" in result.message
    assert path.read_bytes() == before


def test_atomic_replace_failure_is_io_failure_and_preserves_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_id = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
    path = write_paper(tmp_path, paper_id)
    before = path.read_bytes()

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("replace denied")

    monkeypatch.setattr(safe_write_module.os, "replace", fail_replace)

    result = keep_paper(tmp_path, paper_id, WorkflowStatus.CANDIDATE)

    assert result.outcome is DecisionOutcome.IO_FAILURE
    assert "replace denied" in result.message
    assert path.read_bytes() == before
    assert tuple(path.parent.glob(f".{path.name}.*.tmp")) == ()


def test_papers_scan_failure_is_structured_io_failure(
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

    result = keep_paper(
        tmp_path,
        UUID("12121212-1212-4212-8212-121212121212"),
        WorkflowStatus.CANDIDATE,
    )

    assert result.outcome is DecisionOutcome.IO_FAILURE
    assert "scan denied" in result.message


def test_failed_decision_on_missing_workspace_creates_nothing(tmp_path: Path) -> None:
    output_dir = tmp_path / "missing"

    result = reject_paper(
        output_dir,
        UUID("13131313-1313-4313-8313-131313131313"),
        WorkflowStatus.CANDIDATE,
    )

    assert result.outcome is DecisionOutcome.NOT_FOUND
    assert not output_dir.exists()


def test_successful_decision_persists_no_membership_or_history_elsewhere(
    tmp_path: Path,
) -> None:
    paper_id = UUID("14141414-1414-4414-8414-141414141414")
    path = write_paper(tmp_path, paper_id)

    result = reject_paper(tmp_path, paper_id, WorkflowStatus.CANDIDATE)

    assert result.outcome is DecisionOutcome.UPDATED
    assert document_parts(path)[0]["status"] == "rejected"
    assert sorted(item.name for item in tmp_path.iterdir()) == ["Papers"]
    assert sorted(item.name for item in (tmp_path / "Papers").iterdir()) == [
        path.name
    ]


def test_decision_result_matches_authoritative_parser_after_update(
    tmp_path: Path,
) -> None:
    paper_id = UUID("15151515-1515-4515-8515-151515151515")
    path = write_paper(tmp_path, paper_id)

    result = keep_paper(tmp_path, paper_id, WorkflowStatus.CANDIDATE)
    state = parse_paper_state(
        path,
        path.read_bytes().decode("utf-8"),
        tmp_path / "Authors",
    )

    assert result.outcome is DecisionOutcome.UPDATED
    assert state is not None
    assert state.problems == ()
    assert state.paper_id == paper_id
    assert state.status is WorkflowStatus.KEPT


@pytest.mark.parametrize("attribution", [
    ["0006-341X", "1541-0420"], [" 0006-341x ", "0006-341X"],
    "definitely-not-a-list-or-valid-issn", None, {"issn": "invalid"}, ["0006-341X", "invalid"],
])
@pytest.mark.parametrize("action,initial,target", [
    (keep_paper, WorkflowStatus.CANDIDATE, WorkflowStatus.KEPT),
    (reject_paper, WorkflowStatus.CANDIDATE, WorkflowStatus.REJECTED),
    (reconcile_paper_with_zotero, WorkflowStatus.KEPT, WorkflowStatus.IN_ZOTERO),
])
def test_decisions_preserve_valid_or_malformed_raw_attribution(tmp_path, attribution, action, initial, target, zotero_reads):
    paper_id = UUID("16161616-1616-4616-8616-161616161616")
    path = write_paper(tmp_path, paper_id, status=initial, zotero_key="PARENT01")
    if action is reconcile_paper_with_zotero:
        zotero_reads(library_page([zotero_item()]))
    replace_frontmatter(path, journal_issns=attribution, custom_field={"nested": ["retain", 3]})
    path.write_text(path.read_text() + "\nHuman note.\n\n## Custom\n\nPreserve this.\n")
    before, body_before = document_parts(path)
    result = action(tmp_path, paper_id, initial)
    after, body_after = document_parts(path)
    assert result.outcome is DecisionOutcome.UPDATED and after["status"] == target.value
    assert after["journal_issns"] == attribution
    before.pop("status")
    after.pop("status")
    assert before == after and body_before == body_after


@pytest.mark.parametrize("existing_key", [None, "missing", "PARENT01"])
def test_reconcile_atomically_persists_unique_key_and_status_preserving_all_other_content(
    tmp_path, monkeypatch, zotero_reads, existing_key,
):
    paper_id = UUID("17171717-1717-4717-8717-171717171717")
    path = write_paper(tmp_path, paper_id, status=WorkflowStatus.KEPT)
    values, body = document_parts(path)
    if existing_key == "missing":
        values.pop("zotero_key")
    else:
        values["zotero_key"] = existing_key
    values["doi"] = " HTTPS://DOI.ORG/10.5555/DECISION "
    values["external_ids"]["doi"] = values["doi"]
    values["custom_field"] = {"nested": ["retain", 3]}
    values["journal_issns"] = ["0006-341X"]
    body += "\n## Notes\n\nHuman note.\n\n### Child\nKeep child.\n\n## Custom\nKeep all.\n"
    path.write_text("---\n" + yaml.safe_dump(values, sort_keys=False) + "---" + body)
    before, before_body = document_parts(path)
    requests = zotero_reads(
        library_page([zotero_item(doi="10.5555/DECISION")], total=2, next_start=1),
        library_page([zotero_item("OTHER001", doi="10.5555/other")], total=2),
    )
    replace = decisions._replace_regular_text_at_identity
    original_directory, original_file = path.parent.stat(), path.stat()
    writes = []

    def record_atomic(target, contents, *, expected_contents, **identities):
        assert document_parts(path)[0]["status"] == "kept"
        assert document_parts(path)[0].get("zotero_key") == before.get("zotero_key")
        replacement = yaml.safe_load(contents.split("---", 2)[1])
        assert replacement["status"] == "in_zotero" and replacement["zotero_key"] == "PARENT01"
        assert identities['expected_directory_identity'] == (original_directory.st_dev, original_directory.st_ino)
        assert identities['expected_file_identity'] == (original_file.st_dev, original_file.st_ino)
        writes.append(contents)
        replace(target, contents, expected_contents=expected_contents, **identities)

    monkeypatch.setattr(decisions, "_replace_regular_text_at_identity", record_atomic)

    result = reconcile_paper_with_zotero(tmp_path, paper_id, WorkflowStatus.KEPT)

    assert result.outcome is DecisionOutcome.UPDATED and len(writes) == 1
    assert result.current_status is WorkflowStatus.KEPT
    assert result.resulting_status is WorkflowStatus.IN_ZOTERO
    after, after_body = document_parts(path)
    assert after.pop("status") == "in_zotero" and after.pop("zotero_key") == "PARENT01"
    before.pop("status")
    before.pop("zotero_key", None)
    assert after == before and after_body == before_body
    assert len(requests) == 2 and requests[1].headers["Zotero-Server-ID"] == "local-instance"


@pytest.mark.parametrize("existing_key", [None, "PARENT01"])
@pytest.mark.parametrize("failure", [
    "no_match", "duplicate", "incomplete", "invalid_json", "invalid_item",
    "missing_server", "missing_version", "changed_server", "unavailable",
])
def test_reconcile_zotero_verification_failure_preserves_complete_paper(
    tmp_path, zotero_reads, existing_key, failure,
):
    paper_id = UUID("18181818-1818-4818-8818-181818181818")
    path = write_paper(tmp_path, paper_id, status=WorkflowStatus.KEPT, zotero_key=existing_key)
    before = path.read_bytes()
    expected = DecisionOutcome.ZOTERO_FAILURE
    if failure == "no_match":
        replies = [library_page()]
        expected = DecisionOutcome.ZOTERO_NOT_FOUND
    elif failure == "duplicate":
        replies = [
            library_page([zotero_item()], total=2, next_start=1),
            library_page([zotero_item("PARENT02", doi="HTTPS://DOI.ORG/10.5555/DECISION")], total=2),
        ]
        expected = DecisionOutcome.ZOTERO_DUPLICATE
    elif failure == "incomplete":
        replies = [library_page([zotero_item()], total=2)]
    elif failure == "invalid_json":
        replies = [httpx.Response(200, content=b"secret-invalid-payload", headers={
            "Zotero-Server-ID": "local-instance", "Last-Modified-Version": "4", "Total-Results": "1",
        })]
    elif failure == "invalid_item":
        replies = [library_page([{"secret": "unreadable-item"}])]
    elif failure == "missing_server":
        replies = [library_page([zotero_item()], server=None)]
    elif failure == "missing_version":
        replies = [library_page([zotero_item()], version=None)]
    elif failure == "changed_server":
        replies = [
            library_page([zotero_item()], total=2, next_start=1),
            library_page([zotero_item("OTHER001")], total=2, server="different-instance"),
        ]
    else:
        replies = [httpx.ConnectError("secret-error-details")]
    requests = zotero_reads(*replies)

    result = reconcile_paper_with_zotero(tmp_path, paper_id, WorkflowStatus.KEPT)

    assert result.outcome is expected and result.resulting_status is None
    assert path.read_bytes() == before and requests
    assert "secret" not in result.message and "unreadable-item" not in result.message


@pytest.mark.parametrize("invalid_parent", [
    zotero_item(item_type="mysteryFutureType"),
    zotero_item(item_type="journalArticle", parentItem="PARENT02"),
])
def test_reconcile_unprovable_exact_doi_parent_never_mutates_paper(
    tmp_path,
    zotero_reads,
    invalid_parent,
):
    paper_id = UUID("29292929-2929-4929-8929-292929292929")
    path = write_paper(tmp_path, paper_id, status=WorkflowStatus.KEPT)
    before = path.read_bytes()
    requests = zotero_reads(library_page([invalid_parent]))

    result = reconcile_paper_with_zotero(
        tmp_path,
        paper_id,
        WorkflowStatus.KEPT,
    )

    assert result.outcome is DecisionOutcome.ZOTERO_FAILURE
    assert result.resulting_status is None
    assert path.read_bytes() == before
    values, _ = document_parts(path)
    assert values["status"] == "kept"
    assert values["zotero_key"] is None
    assert len(requests) == 1


@pytest.mark.parametrize("key", ["WRONG001", "STALE001"])
def test_reconcile_never_replaces_non_null_conflicting_key(tmp_path, zotero_reads, key):
    paper_id = UUID("19191919-1919-4919-8919-191919191919")
    path = write_paper(tmp_path, paper_id, status=WorkflowStatus.KEPT, zotero_key=key)
    before = path.read_bytes()
    requests = zotero_reads(library_page([zotero_item()]))

    result = reconcile_paper_with_zotero(tmp_path, paper_id, WorkflowStatus.KEPT)

    assert result.outcome is DecisionOutcome.STATE_CONFLICT
    assert result.resulting_status is None and path.read_bytes() == before
    assert len(requests) == 1


@pytest.mark.parametrize("doi", [None, "", "   ", 123])
def test_reconcile_invalid_doi_fails_before_zotero(tmp_path, doi):
    paper_id = UUID("20202020-2020-4020-8020-202020202020")
    path = write_paper(tmp_path, paper_id, status=WorkflowStatus.KEPT)
    values, _ = document_parts(path)
    replace_frontmatter(path, doi=doi, external_ids={**values["external_ids"], "doi": doi})
    before = path.read_bytes()

    result = reconcile_paper_with_zotero(tmp_path, paper_id, WorkflowStatus.KEPT)

    assert result.outcome is DecisionOutcome.INVALID_PAPER and path.read_bytes() == before


@pytest.mark.parametrize("key", ["", " ", "short", "parent01", " PARENT01 ", 123, {"key": "PARENT01"}])
def test_reconcile_malformed_non_null_key_fails_before_zotero(tmp_path, key):
    paper_id = UUID("21212121-2121-4121-8121-212121212121")
    path = write_paper(tmp_path, paper_id, status=WorkflowStatus.KEPT)
    replace_frontmatter(path, zotero_key=key)
    before = path.read_bytes()

    result = reconcile_paper_with_zotero(tmp_path, paper_id, WorkflowStatus.KEPT)

    assert result.outcome is DecisionOutcome.INVALID_PAPER and path.read_bytes() == before


@pytest.mark.parametrize("mode", [
    "duplicate", "unreadable", "symlink", "non_regular", "unsafe_sibling", "malformed",
])
def test_reconcile_unsafe_paper_location_fails_before_zotero(tmp_path, monkeypatch, mode):
    paper_id = UUID("22222222-2222-4222-8222-222222222222")
    path = write_paper(tmp_path, paper_id, status=WorkflowStatus.KEPT)
    before = path.read_bytes()
    if mode == "duplicate":
        path.with_name("duplicate.md").write_bytes(before)
    elif mode == "unreadable":
        sibling = path.with_name("unreadable.md")
        sibling.write_bytes(b"\xff")
    elif mode == "symlink":
        target = tmp_path / "outside.md"
        path.rename(target)
        path.symlink_to(target)
    elif mode == "non_regular":
        path.unlink()
        path.mkdir()
    elif mode == "unsafe_sibling":
        path.with_name("unsafe.md").mkdir()
    else:
        replace_frontmatter(path, title=None)
        before = path.read_bytes()

    result = reconcile_paper_with_zotero(tmp_path, paper_id, WorkflowStatus.KEPT)

    assert result.outcome in (DecisionOutcome.INVALID_PAPER, DecisionOutcome.IO_FAILURE)
    assert result.resulting_status is None
    if mode == "non_regular":
        assert path.is_dir()
    else:
        assert path.read_bytes() == before


@pytest.mark.parametrize("change", ["edit", "disappear"])
def test_reconcile_compare_replace_conflict_never_retries_or_partially_updates(
    tmp_path, monkeypatch, zotero_reads, change,
):
    paper_id = UUID("23232323-2323-4323-8323-232323232323")
    path = write_paper(tmp_path, paper_id, status=WorkflowStatus.KEPT)
    before = path.read_text()
    zotero_reads(library_page([zotero_item()]))
    replace = decisions._replace_regular_text_at_identity
    calls = []

    def race(target, contents, *, expected_contents, **identities):
        assert expected_contents == before
        calls.append(contents)
        if change == "edit":
            path.write_text(before + "\nConcurrent edit.\n")
        else:
            path.unlink()
        replace(target, contents, expected_contents=expected_contents, **identities)

    monkeypatch.setattr(decisions, "_replace_regular_text_at_identity", race)

    result = reconcile_paper_with_zotero(tmp_path, paper_id, WorkflowStatus.KEPT)

    assert result.outcome is DecisionOutcome.STATE_CONFLICT and len(calls) == 1
    assert result.resulting_status is None
    if change == "edit":
        assert path.read_text() == before + "\nConcurrent edit.\n"
        values, _ = document_parts(path)
        assert values["status"] == "kept" and values["zotero_key"] is None
    else:
        assert not path.exists()


@pytest.mark.parametrize("replacement", ["symlink", "directory"])
def test_reconcile_target_becoming_unsafe_during_zotero_read_never_mutates(
    tmp_path, monkeypatch, zotero_reads, replacement,
):
    paper_id = UUID("24242424-2424-4424-8424-242424242424")
    path = write_paper(tmp_path, paper_id, status=WorkflowStatus.KEPT)
    before = path.read_bytes()
    zotero_reads(library_page([zotero_item()]))
    resolve = ZoteroLocalClient.resolve_identity
    outside = tmp_path / "outside.md"

    def replace_target(client, doi, zotero_key=None):
        identity = resolve(client, doi, zotero_key)
        path.unlink()
        if replacement == "symlink":
            outside.write_bytes(before)
            path.symlink_to(outside)
        else:
            path.mkdir()
        return identity

    monkeypatch.setattr(ZoteroLocalClient, "resolve_identity", replace_target)

    result = reconcile_paper_with_zotero(tmp_path, paper_id, WorkflowStatus.KEPT)

    assert result.outcome is DecisionOutcome.STATE_CONFLICT
    if replacement == "symlink":
        assert path.is_symlink() and outside.read_bytes() == before
    else:
        assert path.is_dir() and not list(path.iterdir())


def test_reconcile_local_read_error_is_sanitized_and_does_not_contact_zotero(tmp_path, monkeypatch):
    paper_id = UUID("25252525-2525-4525-8525-252525252525")
    path = write_paper(tmp_path, paper_id, status=WorkflowStatus.KEPT)
    before = path.read_bytes()

    def unreadable(target, directory):
        raise PermissionError("secret-filesystem-detail")

    monkeypatch.setattr(decisions, "_read_reconciliation_candidate", unreadable)

    result = reconcile_paper_with_zotero(tmp_path, paper_id, WorkflowStatus.KEPT)

    assert result.outcome is DecisionOutcome.IO_FAILURE
    assert "secret" not in result.message and path.read_bytes() == before


@pytest.mark.parametrize("replacement", ["symlink", "directory", "fifo", "missing"])
def test_reconcile_final_compare_boundary_rejects_location_substitution(
    tmp_path, monkeypatch, zotero_reads, replacement,
):
    paper_id = UUID("26262626-2626-4626-8626-262626262626")
    path = write_paper(tmp_path, paper_id, status=WorkflowStatus.KEPT)
    original = path.read_bytes()
    preserved = tmp_path / "preserved-paper.md"
    requests = zotero_reads(library_page([zotero_item()]))
    compare = decisions._replace_regular_text_at_identity
    calls = []

    def substitute_at_compare(target, contents, *, expected_contents, **identities):
        assert target == path and path.is_file() and not path.is_symlink()
        assert expected_contents.encode("utf-8") == original and len(requests) == 1
        path.rename(preserved)
        if replacement == "symlink":
            path.symlink_to(preserved)
        elif replacement == "directory":
            path.mkdir()
            (path / "sentinel").write_bytes(b"replacement directory content")
        elif replacement == "fifo":
            os.mkfifo(path)
        calls.append(contents)
        compare(target, contents, expected_contents=expected_contents, **identities)

    monkeypatch.setattr(decisions, "_replace_regular_text_at_identity", substitute_at_compare)

    result = reconcile_paper_with_zotero(tmp_path, paper_id, WorkflowStatus.KEPT)

    assert result.outcome is DecisionOutcome.STATE_CONFLICT and len(calls) == 1
    assert result.resulting_status is None and preserved.read_bytes() == original
    preserved_fields, _ = document_parts(preserved)
    assert preserved_fields["status"] == "kept" and preserved_fields["zotero_key"] is None
    if replacement == "symlink":
        assert path.is_symlink() and path.read_bytes() == original
    elif replacement == "directory":
        assert path.is_dir() and (path / "sentinel").read_bytes() == b"replacement directory content"
    elif replacement == "fifo":
        assert stat.S_ISFIFO(path.lstat().st_mode)
    else:
        assert not path.exists()
    assert not any(
        b"status: in_zotero" in candidate.read_bytes()
        for candidate in path.parent.glob("*.md")
        if not candidate.is_symlink() and stat.S_ISREG(candidate.lstat().st_mode)
    )


def test_reconcile_unique_parent_succeeds_without_attachment_inspection(tmp_path, monkeypatch, zotero_reads):
    def forbidden(*args, **kwargs):
        raise AssertionError("Reconciliation must not inspect Zotero attachments")

    monkeypatch.setattr(ZoteroLocalClient, "inspect_attachments", forbidden)
    paper_id = UUID("27272727-2727-4727-8727-272727272727")
    path = write_paper(tmp_path, paper_id, status=WorkflowStatus.KEPT)
    requests = zotero_reads(library_page([zotero_item()]))

    result = reconcile_paper_with_zotero(tmp_path, paper_id, WorkflowStatus.KEPT)

    assert result.outcome is DecisionOutcome.UPDATED
    after, _ = document_parts(path)
    assert after["status"] == "in_zotero" and after["zotero_key"] == "PARENT01"
    assert requests and all(request.method == "GET" for request in requests)


@pytest.mark.parametrize('replacement', ['file', 'papers_directory'])
def test_reconcile_rejects_same_bytes_at_replaced_original_location(tmp_path, monkeypatch, zotero_reads, replacement):
    paper_id = UUID('28282828-2828-4828-8828-282828282828')
    path = write_paper(tmp_path, paper_id, status=WorkflowStatus.KEPT)
    original = path.read_bytes()
    original_file = path.stat()
    original_directory = path.parent.stat()
    requests = zotero_reads(library_page([zotero_item()]))
    resolve = ZoteroLocalClient.resolve_identity
    locate = decisions._locate_paper
    read = decisions._read_reconciliation_candidate
    compare = decisions._replace_regular_text_at_identity
    locations = []
    reads = []
    comparisons = []
    preserved = tmp_path / 'preserved-paper.md'
    def locate_once(*args, **kwargs):
        locations.append(args[1])
        return locate(*args, **kwargs)
    def read_once(target, directory):
        reads.append(target)
        return read(target, directory)
    def compare_once(target, contents, *, expected_contents, **identities):
        comparisons.append(target)
        assert expected_contents.encode('utf-8') == original
        assert identities['expected_directory_identity'] == (original_directory.st_dev, original_directory.st_ino)
        assert identities['expected_file_identity'] == (original_file.st_dev, original_file.st_ino)
        return compare(target, contents, expected_contents=expected_contents, **identities)
    def replace_during_lookup(client, doi, zotero_key=None):
        nonlocal preserved
        identity = resolve(client, doi, zotero_key)
        if replacement == 'file':
            path.rename(preserved)
        else:
            moved = tmp_path / 'original-Papers'
            path.parent.rename(moved)
            preserved = moved / path.name
            path.parent.mkdir()
            assert path.parent.stat().st_ino != original_directory.st_ino
        path.write_bytes(original)
        assert path.stat().st_ino != original_file.st_ino
        return identity
    monkeypatch.setattr(decisions, '_locate_paper', locate_once)
    monkeypatch.setattr(decisions, '_read_reconciliation_candidate', read_once)
    monkeypatch.setattr(decisions, '_replace_regular_text_at_identity', compare_once)
    monkeypatch.setattr(ZoteroLocalClient, 'resolve_identity', replace_during_lookup)
    result = reconcile_paper_with_zotero(tmp_path, paper_id, WorkflowStatus.KEPT)
    assert result.outcome is DecisionOutcome.STATE_CONFLICT
    assert result.resulting_status is None
    assert locations == [paper_id] and len(requests) == 1
    assert reads == comparisons == [path]
    assert all(request.method == 'GET' for request in requests)
    assert preserved.read_bytes() == path.read_bytes() == original
    for unchanged in (preserved, path):
        fields, _ = document_parts(unchanged)
        assert fields['status'] == 'kept' and fields['zotero_key'] is None
    assert not list(path.parent.glob('.*.tmp'))
