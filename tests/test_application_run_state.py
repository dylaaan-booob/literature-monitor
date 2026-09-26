from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import literature_monitor.safe_write as safe_write_module
from literature_monitor.application.run_state import (
    LAST_RUN_SCHEMA_VERSION,
    LastRunReadStatus,
    LastRunSnapshot,
    LastRunSnapshotWriteError,
    RecordedRunOutcome,
    last_run_snapshot_path,
    read_last_run_snapshot,
    write_last_run_snapshot,
)
from literature_monitor.coverage import (
    CoverageComponent,
    CoverageStatus,
    CoverageUnit,
    ProviderReuseUnit,
)
from literature_monitor.date_range import ResolvedDateRange


def sample_coverage() -> tuple[CoverageUnit, ...]:
    return (
        CoverageUnit(
            provider="openalex",
            component=CoverageComponent.OPENALEX_DISCOVERY,
            status=CoverageStatus.COMPLETE,
            journal="Biometrics",
        ),
        CoverageUnit(
            provider="crossref",
            component=CoverageComponent.CROSSREF_DISCOVERY,
            status=CoverageStatus.PARTIAL,
            journal="Biometrics",
            issn="0006-341X",
        ),
        CoverageUnit(
            provider="crossref",
            component=CoverageComponent.CROSSREF_SUPPLEMENT,
            status=CoverageStatus.UNAVAILABLE,
            doi="10.5555/missing",
        ),
    )


def sample_snapshot(
    *,
    from_date: date = date(2026, 1, 1),
    to_date: date = date(2026, 1, 31),
    outcome: RecordedRunOutcome = RecordedRunOutcome.COMPLETED_WITH_WARNINGS,
    coverage: tuple[CoverageUnit, ...] | None = None,
) -> LastRunSnapshot:
    return LastRunSnapshot(
        schema_version=LAST_RUN_SCHEMA_VERSION,
        resolved_date_range=ResolvedDateRange(
            from_date=from_date,
            to_date=to_date,
        ),
        outcome=outcome,
        coverage=sample_coverage() if coverage is None else coverage,
    )


def raw_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "resolved_date_range": {
            "from_date": "2026-01-01",
            "to_date": "2026-01-31",
        },
        "outcome": "COMPLETED",
        "coverage": [
            {
                "provider": "openalex",
                "component": "openalex_discovery",
                "status": "COMPLETE",
                "journal": "Biometrics",
                "issn": None,
                "doi": None,
            }
        ],
    }


def write_raw_snapshot(output_dir: Path, payload: object) -> Path:
    path = last_run_snapshot_path(output_dir)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_snapshot_roundtrip_preserves_schema_order_and_derived_summary(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "workspace"
    original = sample_snapshot()

    write_last_run_snapshot(output_dir, original)

    path = last_run_snapshot_path(output_dir)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert list(payload) == [
        "schema_version",
        "resolved_date_range",
        "outcome",
        "coverage",
        "reused_units",
    ]
    assert "coverage_summary" not in payload
    assert set(payload["coverage"][0]) == {
        "provider",
        "component",
        "status",
        "journal",
        "issn",
        "doi",
    }

    result = read_last_run_snapshot(output_dir)

    assert result.status is LastRunReadStatus.AVAILABLE
    assert result.error is None
    assert result.snapshot == original
    assert result.snapshot is not None
    assert [unit.component for unit in result.snapshot.coverage] == [
        CoverageComponent.OPENALEX_DISCOVERY,
        CoverageComponent.CROSSREF_DISCOVERY,
        CoverageComponent.CROSSREF_SUPPLEMENT,
    ]
    assert [
        (summary.component, summary.total_units)
        for summary in result.snapshot.coverage_summary
    ] == [
        (CoverageComponent.OPENALEX_DISCOVERY, 1),
        (CoverageComponent.CROSSREF_DISCOVERY, 1),
        (CoverageComponent.CROSSREF_SUPPLEMENT, 1),
    ]


def test_snapshot_payload_contains_only_a4_contract_fields(tmp_path: Path) -> None:
    output_dir = tmp_path / "workspace"
    write_last_run_snapshot(output_dir, sample_snapshot())

    payload = json.loads(
        last_run_snapshot_path(output_dir).read_text(encoding="utf-8")
    )

    assert set(payload) == {
        "schema_version",
        "resolved_date_range",
        "outcome",
        "coverage",
        "reused_units",
    }
    persisted_keys = set(payload)
    persisted_keys.update(payload["resolved_date_range"])
    for unit in payload["coverage"]:
        persisted_keys.update(unit)
    assert persisted_keys == {
        "schema_version",
        "resolved_date_range",
        "outcome",
        "coverage",
        "reused_units",
        "from_date",
        "to_date",
        "provider",
        "component",
        "status",
        "journal",
        "issn",
        "doi",
    }


def test_snapshot_write_leaves_workflow_files_unchanged(tmp_path: Path) -> None:
    output_dir = tmp_path / "workspace"
    paper = output_dir / "Papers" / "paper.md"
    author = output_dir / "Authors" / "author.md"
    paper.parent.mkdir(parents=True)
    author.parent.mkdir(parents=True)
    paper.write_bytes(b"paper workflow state\n")
    author.write_bytes(b"author workflow state\n")
    before = {paper: paper.read_bytes(), author: author.read_bytes()}

    write_last_run_snapshot(output_dir, sample_snapshot())

    for path, contents in before.items():
        assert path.read_bytes() == contents


def test_second_snapshot_replaces_single_previous_file(tmp_path: Path) -> None:
    output_dir = tmp_path / "workspace"
    write_last_run_snapshot(output_dir, sample_snapshot())
    replacement = sample_snapshot(
        from_date=date(2026, 2, 1),
        to_date=date(2026, 2, 28),
        outcome=RecordedRunOutcome.COMPLETED_WITH_ERRORS,
        coverage=(),
    )

    write_last_run_snapshot(output_dir, replacement)

    result = read_last_run_snapshot(output_dir)
    assert result.status is LastRunReadStatus.AVAILABLE
    assert result.snapshot == replacement
    assert [path.name for path in (output_dir / ".literature-monitor").iterdir()] == [
        "last-run.json"
    ]


def test_first_creation_failure_leaves_no_snapshot_or_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "workspace"

    def fail_link(source: object, destination: object) -> None:
        raise OSError("link failed")

    monkeypatch.setattr(safe_write_module.os, "link", fail_link)

    with pytest.raises(LastRunSnapshotWriteError, match="link failed"):
        write_last_run_snapshot(output_dir, sample_snapshot())

    metadata_dir = output_dir / ".literature-monitor"
    assert not last_run_snapshot_path(output_dir).exists()
    assert tuple(metadata_dir.glob(".last-run.json.*.tmp")) == ()


def test_replacement_failure_preserves_previous_snapshot_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "workspace"
    write_last_run_snapshot(output_dir, sample_snapshot())
    path = last_run_snapshot_path(output_dir)
    original = path.read_bytes()

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(safe_write_module.os, "replace", fail_replace)

    with pytest.raises(LastRunSnapshotWriteError, match="replace failed"):
        write_last_run_snapshot(
            output_dir,
            sample_snapshot(
                from_date=date(2026, 3, 1),
                to_date=date(2026, 3, 31),
            ),
        )

    assert path.read_bytes() == original
    assert tuple(path.parent.glob(".last-run.json.*.tmp")) == ()


def test_serialization_failure_preserves_previous_snapshot(tmp_path: Path) -> None:
    output_dir = tmp_path / "workspace"
    write_last_run_snapshot(output_dir, sample_snapshot())
    path = last_run_snapshot_path(output_dir)
    original = path.read_bytes()
    invalid = sample_snapshot(
        coverage=(
            CoverageUnit(
                provider="crossref",
                component=CoverageComponent.CROSSREF_SUPPLEMENT,
                status=CoverageStatus.PARTIAL,
                doi="10.5555/not-singleton",
            ),
        )
    )

    with pytest.raises(LastRunSnapshotWriteError, match="cannot be PARTIAL"):
        write_last_run_snapshot(output_dir, invalid)

    assert path.read_bytes() == original


def test_missing_snapshot_is_distinct_from_invalid_snapshot(tmp_path: Path) -> None:
    output_dir = tmp_path / "workspace"

    missing = read_last_run_snapshot(output_dir)

    assert missing.status is LastRunReadStatus.NOT_FOUND
    assert missing.snapshot is None
    assert missing.error is None

    path = last_run_snapshot_path(output_dir)
    path.parent.mkdir(parents=True)
    path.write_text("{not-json", encoding="utf-8")
    invalid = read_last_run_snapshot(output_dir)

    assert invalid.status is LastRunReadStatus.INVALID
    assert invalid.snapshot is None
    assert "invalid JSON" in (invalid.error or "")
    assert path.read_text(encoding="utf-8") == "{not-json"


def test_corrupt_snapshot_is_reported_without_rewrite(tmp_path: Path) -> None:
    output_dir = tmp_path / "workspace"
    path = last_run_snapshot_path(output_dir)
    path.parent.mkdir(parents=True)
    contents = b"{ definitely not json\n"
    path.write_bytes(contents)

    result = read_last_run_snapshot(output_dir)

    assert result.status is LastRunReadStatus.INVALID
    assert "invalid JSON" in (result.error or "")
    assert path.read_bytes() == contents


def test_unknown_schema_version_is_rejected(tmp_path: Path) -> None:
    output_dir = tmp_path / "workspace"
    payload = raw_payload()
    payload["schema_version"] = 3
    write_raw_snapshot(output_dir, payload)

    result = read_last_run_snapshot(output_dir)

    assert result.status is LastRunReadStatus.INVALID
    assert "unsupported schema_version 3" in (result.error or "")


def test_v1_remains_read_only_with_empty_reuse_and_historical_duplicate_coverage(tmp_path):
    payload = raw_payload()
    payload["coverage"] *= 2
    path = write_raw_snapshot(tmp_path, payload)
    before = path.read_bytes()
    result = read_last_run_snapshot(tmp_path)
    assert result.status is LastRunReadStatus.AVAILABLE
    assert result.snapshot.schema_version == 1 and result.snapshot.reused_units == ()
    assert path.read_bytes() == before


def test_v2_roundtrip_reuse_and_live_are_separate(tmp_path):
    from dataclasses import replace
    reused = (
        ProviderReuseUnit("openalex", CoverageComponent.OPENALEX_DISCOVERY, journal="Other journal"),
        ProviderReuseUnit("crossref", CoverageComponent.CROSSREF_DISCOVERY, journal="Biometrics", issn="1541-0420"),
        ProviderReuseUnit("crossref", CoverageComponent.CROSSREF_SUPPLEMENT, doi="10.5555/reused"),
    )
    snapshot = replace(sample_snapshot(), reused_units=reused)
    write_last_run_snapshot(tmp_path, snapshot)
    assert read_last_run_snapshot(tmp_path).snapshot == snapshot
    assert [summary.reused_units for summary in snapshot.reuse_summary] == [1, 1, 1]


@pytest.mark.parametrize("case", [
    "duplicate_live", "duplicate_reuse", "overlap", "reversed", "interleaved",
    "provider", "component", "journal", "issn", "doi", "noncanonical_doi", "status", "not_array",
])
def test_invalid_v2_reporting_is_rejected_without_rewriting(tmp_path, case):
    payload = raw_payload()
    payload["schema_version"] = 2
    reused = {"provider": "crossref", "component": "crossref_supplement",
              "journal": None, "issn": None, "doi": "10.5555/reused"}
    payload["reused_units"] = [reused]
    if case == "duplicate_live":
        payload["coverage"] *= 2
    elif case == "duplicate_reuse":
        payload["reused_units"] *= 2
    elif case == "overlap":
        payload["reused_units"] = [{k: v for k, v in payload["coverage"][0].items() if k != "status"}]
    elif case in {"reversed", "interleaved"}:
        oa = {"provider": "openalex", "component": "openalex_discovery", "journal": "Other", "issn": None, "doi": None}
        payload["reused_units"] = [reused, oa]
        if case == "interleaved":
            payload["reused_units"] = [oa, reused, dict(oa, journal="Third")]
    elif case == "noncanonical_doi":
        reused["doi"] = "https://doi.org/10.5555/reused"
    elif case == "status":
        reused["status"] = "COMPLETE"
    elif case == "not_array":
        payload["reused_units"] = {}
    else:
        reused[case] = {"provider": "openalex", "component": "wrong", "journal": "Unexpected",
                        "issn": "0006-341X", "doi": None}[case]
    path = write_raw_snapshot(tmp_path, payload)
    before = path.read_bytes()
    result = read_last_run_snapshot(tmp_path)
    assert result.status is LastRunReadStatus.INVALID and result.error
    assert path.read_bytes() == before


@pytest.mark.parametrize("existing", [False, True])
def test_v2_invalid_writer_preserves_existing_snapshot(tmp_path, existing):
    from dataclasses import replace
    valid = sample_snapshot()
    path = last_run_snapshot_path(tmp_path)
    if existing:
        write_last_run_snapshot(tmp_path, valid)
    before = path.read_bytes() if path.exists() else None
    duplicate = replace(valid, coverage=(*valid.coverage, valid.coverage[0]))
    with pytest.raises(LastRunSnapshotWriteError, match="duplicate"):
        write_last_run_snapshot(tmp_path, duplicate)
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("component", "semantic_scholar_discovery", "component is invalid"),
        ("status", "MAYBE", "status is invalid"),
    ),
)
def test_invalid_coverage_component_or_status_is_rejected(
    field: str,
    value: str,
    message: str,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "workspace"
    payload = raw_payload()
    coverage = payload["coverage"]
    assert isinstance(coverage, list)
    coverage[0][field] = value
    write_raw_snapshot(output_dir, payload)

    result = read_last_run_snapshot(output_dir)

    assert result.status is LastRunReadStatus.INVALID
    assert message in (result.error or "")


@pytest.mark.parametrize(
    "unit",
    (
        {
            "provider": "openalex",
            "component": "openalex_discovery",
            "status": "COMPLETE",
            "journal": None,
            "issn": None,
            "doi": None,
        },
        {
            "provider": "crossref",
            "component": "crossref_discovery",
            "status": "COMPLETE",
            "journal": "Biometrics",
            "issn": None,
            "doi": None,
        },
        {
            "provider": "crossref",
            "component": "crossref_supplement",
            "status": "COMPLETE",
            "journal": None,
            "issn": None,
            "doi": "HTTPS://DOI.ORG/10.5555/Mixed",
        },
    ),
)
def test_invalid_component_specific_identity_is_rejected(
    unit: dict[str, object],
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "workspace"
    payload = raw_payload()
    payload["coverage"] = [unit]
    write_raw_snapshot(output_dir, payload)

    result = read_last_run_snapshot(output_dir)

    assert result.status is LastRunReadStatus.INVALID


def test_invalid_date_is_rejected(tmp_path: Path) -> None:
    output_dir = tmp_path / "workspace"
    payload = raw_payload()
    resolved = payload["resolved_date_range"]
    assert isinstance(resolved, dict)
    resolved["from_date"] = "2026-02-31"
    write_raw_snapshot(output_dir, payload)

    result = read_last_run_snapshot(output_dir)

    assert result.status is LastRunReadStatus.INVALID
    assert "from_date must be YYYY-MM-DD" in (result.error or "")


def test_invalid_outcome_is_rejected(tmp_path: Path) -> None:
    output_dir = tmp_path / "workspace"
    payload = raw_payload()
    payload["outcome"] = "INVALID_CONFIGURATION"
    write_raw_snapshot(output_dir, payload)

    result = read_last_run_snapshot(output_dir)

    assert result.status is LastRunReadStatus.INVALID
    assert result.error == "outcome is invalid"


def test_filesystem_read_failure_is_structured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output_dir = tmp_path / "workspace"
    write_last_run_snapshot(output_dir, sample_snapshot())

    def fail_read(path: Path) -> str:
        raise OSError("read denied")

    monkeypatch.setattr(
        "literature_monitor.application.run_state.read_text_exact",
        fail_read,
    )

    result = read_last_run_snapshot(output_dir)

    assert result.status is LastRunReadStatus.INVALID
    assert result.error == "read denied"


def test_unsafe_metadata_directory_symlink_is_neither_read_nor_written(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "workspace"
    output_dir.mkdir()
    external = tmp_path / "external-metadata"
    external.mkdir()
    metadata_dir = output_dir / ".literature-monitor"
    metadata_dir.symlink_to(external, target_is_directory=True)

    read_result = read_last_run_snapshot(output_dir)

    assert read_result.status is LastRunReadStatus.INVALID
    assert "not a regular directory" in (read_result.error or "")

    with pytest.raises(LastRunSnapshotWriteError, match="not a regular directory"):
        write_last_run_snapshot(output_dir, sample_snapshot())

    assert metadata_dir.is_symlink()
    assert tuple(external.iterdir()) == ()


def test_unsafe_snapshot_symlink_is_neither_read_nor_replaced(tmp_path: Path) -> None:
    output_dir = tmp_path / "workspace"
    metadata_dir = output_dir / ".literature-monitor"
    metadata_dir.mkdir(parents=True)
    external = tmp_path / "external.json"
    external.write_text("user object", encoding="utf-8")
    path = last_run_snapshot_path(output_dir)
    path.symlink_to(external)

    read_result = read_last_run_snapshot(output_dir)

    assert read_result.status is LastRunReadStatus.INVALID
    assert "not a regular file" in (read_result.error or "")

    with pytest.raises(LastRunSnapshotWriteError, match="not a regular file"):
        write_last_run_snapshot(output_dir, sample_snapshot())

    assert path.is_symlink()
    assert external.read_text(encoding="utf-8") == "user object"
