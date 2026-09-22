from __future__ import annotations

import inspect
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
import yaml

import literature_monitor.application.decisions as decisions
import literature_monitor.safe_write as safe_write_module
from literature_monitor.application.decisions import (
    DecisionOutcome,
    keep_paper,
    mark_paper_in_zotero,
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
    PaperVersion,
    VersionKind,
    VersionRef,
    Workflow,
    WorkflowStatus,
)
from literature_monitor.safe_write import ContentChangedError


NOW = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)


def write_paper(
    output_dir: Path,
    paper_id: UUID,
    *,
    filename: str = "paper.md",
    status: WorkflowStatus = WorkflowStatus.CANDIDATE,
    title: str = "Decision Paper",
    zotero_key: str | None = None,
) -> Path:
    version = PaperVersion(
        source="doi",
        identifier="10.5555/decision",
        kind=VersionKind.JOURNAL_FINAL,
        date=date(2026, 9, 20),
    )
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
        versions=(version,),
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
            mark_paper_in_zotero,
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
) -> None:
    paper_id = UUID("11111111-1111-4111-8111-111111111111")
    path = write_paper(
        tmp_path,
        paper_id,
        status=initial,
        zotero_key="ZOT-KEEP",
    )
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
    assert after_frontmatter["versions"] == before_frontmatter["versions"]
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
        "mark_paper_in_zotero",
    }
    assert not hasattr(decisions, "set_status")
    for action_name in public_functions:
        assert tuple(inspect.signature(getattr(decisions, action_name)).parameters) == (
            "output_dir",
            "paper_id",
            "expected_status",
        )


def test_unknown_uuid_returns_not_found_without_writes(tmp_path: Path) -> None:
    existing_id = UUID("33333333-3333-4333-8333-333333333333")
    path = write_paper(tmp_path, existing_id)
    before = path.read_bytes()

    result = keep_paper(
        tmp_path,
        UUID("33333333-3333-4333-8333-444444444444"),
        WorkflowStatus.CANDIDATE,
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
            mark_paper_in_zotero,
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
        (WorkflowStatus.CANDIDATE, mark_paper_in_zotero),
        (WorkflowStatus.IN_ZOTERO, keep_paper),
        (WorkflowStatus.IN_ZOTERO, reject_paper),
        (WorkflowStatus.IN_ZOTERO, mark_paper_in_zotero),
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
