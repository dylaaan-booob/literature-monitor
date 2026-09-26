"""Durable latest-run retrieval coverage snapshot state."""

from __future__ import annotations

import json
import stat
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

from literature_monitor.coverage import (
    CoverageComponent,
    CoverageStatus,
    CoverageSummary,
    CoverageUnit,
    summarize_coverage,
)
from literature_monitor.date_range import DateRangeError, ResolvedDateRange
from literature_monitor.identifiers import normalize_doi
from literature_monitor.safe_write import (
    atomic_create_text,
    atomic_replace_text,
    read_text_exact,
)

LAST_RUN_SCHEMA_VERSION = 1
_METADATA_DIRECTORY_NAME = ".literature-monitor"
_LAST_RUN_FILENAME = "last-run.json"


class RecordedRunOutcome(str, Enum):
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"


class LastRunReadStatus(str, Enum):
    AVAILABLE = "available"
    NOT_FOUND = "not_found"
    INVALID = "invalid"


@dataclass(frozen=True)
class LastRunSnapshot:
    schema_version: int
    resolved_date_range: ResolvedDateRange
    outcome: RecordedRunOutcome
    coverage: tuple[CoverageUnit, ...]

    @property
    def coverage_summary(self) -> tuple[CoverageSummary, ...]:
        return summarize_coverage(self.coverage)


@dataclass(frozen=True)
class LastRunReadResult:
    status: LastRunReadStatus
    path: Path
    snapshot: LastRunSnapshot | None = None
    error: str | None = None


class LastRunSnapshotWriteError(RuntimeError):
    """The latest-run snapshot could not be persisted safely."""

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(message)
        self.path = path


def last_run_snapshot_path(output_dir: Path) -> Path:
    return output_dir / _METADATA_DIRECTORY_NAME / _LAST_RUN_FILENAME


def _require_exact_keys(
    value: object,
    *,
    label: str,
    expected: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise ValueError(f"{label} has invalid fields: {'; '.join(details)}")
    return value


def _require_optional_string(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string or null")
    return value


def _validate_coverage_identity(unit: CoverageUnit) -> None:
    if not isinstance(unit.provider, str) or not unit.provider:
        raise ValueError("coverage provider must be a non-empty string")
    journal = _require_optional_string(unit.journal, field="coverage journal")
    issn = _require_optional_string(unit.issn, field="coverage issn")
    doi = _require_optional_string(unit.doi, field="coverage doi")

    if unit.component is CoverageComponent.OPENALEX_DISCOVERY:
        if unit.provider != "openalex" or journal is None or issn is not None or doi is not None:
            raise ValueError(
                "OpenAlex discovery coverage requires provider=openalex and journal only"
            )
        return

    if unit.component is CoverageComponent.CROSSREF_DISCOVERY:
        if unit.provider != "crossref" or journal is None or issn is None or doi is not None:
            raise ValueError(
                "Crossref discovery coverage requires provider=crossref, journal, and issn"
            )
        return

    if unit.component is CoverageComponent.CROSSREF_SUPPLEMENT:
        if unit.provider != "crossref" or journal is not None or issn is not None or doi is None:
            raise ValueError(
                "Crossref supplement coverage requires provider=crossref and doi only"
            )
        try:
            normalized = normalize_doi(doi)
        except ValueError as error:
            raise ValueError("Crossref supplement coverage DOI is invalid") from error
        if normalized != doi:
            raise ValueError("Crossref supplement coverage DOI must already be normalized")
        if unit.status is CoverageStatus.PARTIAL:
            raise ValueError("Crossref supplement coverage cannot be PARTIAL")
        return

    raise ValueError(f"unsupported coverage component {unit.component!r}")


def _serialize_snapshot(snapshot: LastRunSnapshot) -> str:
    if snapshot.schema_version != LAST_RUN_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version {snapshot.schema_version!r}"
        )
    if not isinstance(snapshot.outcome, RecordedRunOutcome):
        raise ValueError("snapshot outcome is invalid")

    coverage: list[dict[str, object]] = []
    for unit in snapshot.coverage:
        if not isinstance(unit.component, CoverageComponent):
            raise ValueError("coverage component is invalid")
        if not isinstance(unit.status, CoverageStatus):
            raise ValueError("coverage status is invalid")
        _validate_coverage_identity(unit)
        coverage.append(
            {
                "provider": unit.provider,
                "component": unit.component.value,
                "status": unit.status.value,
                "journal": unit.journal,
                "issn": unit.issn,
                "doi": unit.doi,
            }
        )

    payload = {
        "schema_version": LAST_RUN_SCHEMA_VERSION,
        "resolved_date_range": {
            "from_date": snapshot.resolved_date_range.from_date.isoformat(),
            "to_date": snapshot.resolved_date_range.to_date.isoformat(),
        },
        "outcome": snapshot.outcome.value,
        "coverage": coverage,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _parse_iso_date(value: object, *, field: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO date string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field} must be YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{field} must be YYYY-MM-DD")
    return parsed


def _parse_coverage_unit(value: object, *, index: int) -> CoverageUnit:
    raw = _require_exact_keys(
        value,
        label=f"coverage[{index}]",
        expected={"provider", "component", "status", "journal", "issn", "doi"},
    )
    provider = raw["provider"]
    if not isinstance(provider, str) or not provider:
        raise ValueError(f"coverage[{index}].provider must be a non-empty string")
    try:
        component = CoverageComponent(raw["component"])
    except (TypeError, ValueError) as error:
        raise ValueError(f"coverage[{index}].component is invalid") from error
    try:
        status = CoverageStatus(raw["status"])
    except (TypeError, ValueError) as error:
        raise ValueError(f"coverage[{index}].status is invalid") from error

    unit = CoverageUnit(
        provider=provider,
        component=component,
        status=status,
        journal=_require_optional_string(
            raw["journal"],
            field=f"coverage[{index}].journal",
        ),
        issn=_require_optional_string(
            raw["issn"],
            field=f"coverage[{index}].issn",
        ),
        doi=_require_optional_string(
            raw["doi"],
            field=f"coverage[{index}].doi",
        ),
    )
    _validate_coverage_identity(unit)
    return unit


def _parse_snapshot(contents: str) -> LastRunSnapshot:
    try:
        value = json.loads(contents)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON: {error.msg}") from error
    raw = _require_exact_keys(
        value,
        label="snapshot",
        expected={"schema_version", "resolved_date_range", "outcome", "coverage"},
    )

    schema_version = raw["schema_version"]
    if type(schema_version) is not int or schema_version != LAST_RUN_SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {schema_version!r}")

    raw_range = _require_exact_keys(
        raw["resolved_date_range"],
        label="resolved_date_range",
        expected={"from_date", "to_date"},
    )
    try:
        resolved_date_range = ResolvedDateRange(
            from_date=_parse_iso_date(raw_range["from_date"], field="from_date"),
            to_date=_parse_iso_date(raw_range["to_date"], field="to_date"),
        )
    except DateRangeError as error:
        raise ValueError(str(error)) from error

    try:
        outcome = RecordedRunOutcome(raw["outcome"])
    except (TypeError, ValueError) as error:
        raise ValueError("outcome is invalid") from error

    raw_coverage = raw["coverage"]
    if not isinstance(raw_coverage, list):
        raise ValueError("coverage must be an array")
    coverage = tuple(
        _parse_coverage_unit(item, index=index)
        for index, item in enumerate(raw_coverage)
    )

    return LastRunSnapshot(
        schema_version=LAST_RUN_SCHEMA_VERSION,
        resolved_date_range=resolved_date_range,
        outcome=outcome,
        coverage=coverage,
    )


def _metadata_directory(
    output_dir: Path,
    *,
    create: bool,
) -> Path | None:
    path = output_dir / _METADATA_DIRECTORY_NAME
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        if not create:
            return None
        try:
            path.mkdir(parents=True)
        except FileExistsError:
            pass
        except OSError as error:
            raise OSError(f"cannot create runtime metadata directory: {error}") from error
        try:
            mode = path.lstat().st_mode
        except OSError as error:
            raise OSError(f"cannot inspect runtime metadata directory: {error}") from error
    except OSError as error:
        raise OSError(f"cannot inspect runtime metadata directory: {error}") from error

    if not stat.S_ISDIR(mode):
        raise OSError("runtime metadata path is not a regular directory")
    return path


def write_last_run_snapshot(output_dir: Path, snapshot: LastRunSnapshot) -> None:
    path = last_run_snapshot_path(output_dir)
    try:
        contents = _serialize_snapshot(snapshot)
        metadata_dir = _metadata_directory(output_dir, create=True)
        assert metadata_dir is not None

        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            atomic_create_text(path, contents)
        except OSError as error:
            raise OSError(f"cannot inspect snapshot path: {error}") from error
        else:
            if not stat.S_ISREG(mode):
                raise OSError("snapshot path is not a regular file")
            atomic_replace_text(path, contents)
    except (OSError, UnicodeError, ValueError) as error:
        raise LastRunSnapshotWriteError(path, str(error)) from error


def read_last_run_snapshot(output_dir: Path) -> LastRunReadResult:
    path = last_run_snapshot_path(output_dir)
    try:
        metadata_dir = _metadata_directory(output_dir, create=False)
    except OSError as error:
        return LastRunReadResult(
            status=LastRunReadStatus.INVALID,
            path=path,
            error=str(error),
        )
    if metadata_dir is None:
        return LastRunReadResult(
            status=LastRunReadStatus.NOT_FOUND,
            path=path,
        )

    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return LastRunReadResult(
            status=LastRunReadStatus.NOT_FOUND,
            path=path,
        )
    except OSError as error:
        return LastRunReadResult(
            status=LastRunReadStatus.INVALID,
            path=path,
            error=f"cannot inspect snapshot path: {error}",
        )
    if not stat.S_ISREG(mode):
        return LastRunReadResult(
            status=LastRunReadStatus.INVALID,
            path=path,
            error="snapshot path is not a regular file",
        )

    try:
        contents = read_text_exact(path)
        snapshot = _parse_snapshot(contents)
    except (OSError, UnicodeError, ValueError) as error:
        return LastRunReadResult(
            status=LastRunReadStatus.INVALID,
            path=path,
            error=str(error),
        )
    return LastRunReadResult(
        status=LastRunReadStatus.AVAILABLE,
        path=path,
        snapshot=snapshot,
    )
