"""Project configuration and journal whitelist loading."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError, field_validator

from literature_monitor.date_range import (
    DEFAULT_WINDOW_DAYS,
    DateRangeError,
    DateRangeSpec,
    ResolvedDateRange,
    resolve_date_range,
    validate_date_range_spec,
)
from literature_monitor.keywords import KeywordExpression, KeywordSyntaxError, parse_keyword_expression
from literature_monitor.search import validate_search_expression

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_ISSN_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{3}[0-9X]$")
_HEADING_PATTERN = re.compile(r"^##\s+")
_SEPARATOR_PATTERN = re.compile(r"^:?-{3,}:?$")


class ConfigurationError(ValueError):
    """A user-facing configuration error with file and field context."""

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        path: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.field = field
        self.path = path


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class JournalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: NonEmptyStr
    issn: tuple[NonEmptyStr, ...]

    @field_validator("issn")
    @classmethod
    def require_issn(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("at least one ISSN is required")
        return value


class _Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: NonEmptyStr | None = None
    venue_whitelist: Path | None = None
    keyword_expression: NonEmptyStr
    output_dir: Path | None = None
    from_date: date | None = None
    to_date: date | None = None
    window_days: int | None = None
    log_level: LogLevel = LogLevel.INFO

    @field_validator(
        "name",
        "venue_whitelist",
        "keyword_expression",
        "output_dir",
        "from_date",
        "to_date",
        "window_days",
        "log_level",
        mode="before",
    )
    @classmethod
    def reject_explicit_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("must not be null")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value


@dataclass(frozen=True)
class LoadedConfig:
    name: str
    venue_whitelist: Path
    keyword_expression: str
    keyword_ast: KeywordExpression
    output_dir: Path
    date_spec: DateRangeSpec
    log_level: LogLevel
    journals: tuple[JournalConfig, ...]


@dataclass(frozen=True)
class MonitorDefinition:
    """Validated monitor fields before config-relative paths are resolved."""

    name: str
    venue_whitelist: Path
    keyword_expression: str
    keyword_ast: KeywordExpression
    output_dir: Path
    date_spec: DateRangeSpec
    log_level: LogLevel


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigurationError(f"{path}: unable to read file: {error}") from error


def _table_cells(line: str, path: Path, line_number: int) -> tuple[str, str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        raise ConfigurationError(
            f"{path}:{line_number}: expected a two-column Markdown table row"
        )
    cells = tuple(cell.strip() for cell in stripped[1:-1].split("|"))
    if len(cells) != 2:
        raise ConfigurationError(
            f"{path}:{line_number}: expected 2 table columns, found {len(cells)}"
        )
    return cells[0], cells[1]


def _valid_issn_checksum(issn: str) -> bool:
    digits = issn.replace("-", "")
    values = [10 if character == "X" else int(character) for character in digits]
    return sum(value * weight for value, weight in zip(values, range(8, 0, -1))) % 11 == 0


def _normalize_issn_value(raw: str) -> str:
    issn = raw.strip().upper()
    if not _ISSN_PATTERN.fullmatch(issn):
        raise ValueError(f"invalid ISSN/EISSN {raw!r}; expected NNNN-NNNN")
    if not _valid_issn_checksum(issn):
        raise ValueError(f"invalid ISSN/EISSN checksum for {issn!r}")
    return issn


def _journal_error(
    message: str,
    *,
    path: Path | None = None,
    line_number: int | None = None,
) -> ConfigurationError:
    if path is not None and line_number is not None:
        rendered = f"{path}:{line_number}: {message}"
    elif path is not None:
        rendered = f"{path}: {message}"
    else:
        rendered = message
    return ConfigurationError(rendered, field="journals", path=path)


def validate_journal_configs(
    journals: Sequence[JournalConfig],
    *,
    path: Path | None = None,
    line_numbers: Sequence[int] | None = None,
) -> tuple[JournalConfig, ...]:
    """Validate and normalize the shared journal-domain constraints."""

    if not journals:
        raise _journal_error("Journals section contains no entries", path=path)
    if line_numbers is not None and len(line_numbers) != len(journals):
        raise ValueError("line_numbers must correspond to journals")

    names: dict[str, int | None] = {}
    issns: dict[str, int | None] = {}
    normalized: list[JournalConfig] = []
    for index, journal in enumerate(journals):
        line_number = line_numbers[index] if line_numbers is not None else None
        name = journal.name.strip()
        if not name:
            raise _journal_error(
                "Journal must not be empty",
                path=path,
                line_number=line_number,
            )
        normalized_name = name.casefold()
        if normalized_name in names:
            first_line = names[normalized_name]
            suffix = (
                f"; first seen at line {first_line}"
                if first_line is not None
                else ""
            )
            raise _journal_error(
                f"duplicate Journal {name!r}{suffix}",
                path=path,
                line_number=line_number,
            )

        if not journal.issn:
            raise _journal_error(
                "ISSN/EISSN must not be empty",
                path=path,
                line_number=line_number,
            )
        journal_issns: list[str] = []
        for raw_issn in journal.issn:
            if not raw_issn.strip():
                raise _journal_error(
                    "ISSN/EISSN contains an empty value",
                    path=path,
                    line_number=line_number,
                )
            try:
                issn = _normalize_issn_value(raw_issn)
            except ValueError as error:
                raise _journal_error(
                    str(error),
                    path=path,
                    line_number=line_number,
                ) from error
            if issn in issns:
                first_line = issns[issn]
                suffix = (
                    f"; first seen at line {first_line}"
                    if first_line is not None
                    else ""
                )
                raise _journal_error(
                    f"duplicate ISSN/EISSN {issn!r}{suffix}",
                    path=path,
                    line_number=line_number,
                )
            issns[issn] = line_number
            journal_issns.append(issn)

        names[normalized_name] = line_number
        normalized.append(JournalConfig(name=name, issn=tuple(journal_issns)))
    return tuple(normalized)


def parse_journal_whitelist_text(
    contents: str,
    *,
    path: Path,
) -> tuple[JournalConfig, ...]:
    """Parse the current Markdown Journals section into structured journals."""

    lines = contents.splitlines()
    journal_headings = [
        index for index, line in enumerate(lines) if line.strip() == "## Journals"
    ]
    if len(journal_headings) != 1:
        raise ConfigurationError(
            f"{path}: expected exactly one '## Journals' section, found {len(journal_headings)}"
        )

    start = journal_headings[0] + 1
    section: list[tuple[int, str]] = []
    for index in range(start, len(lines)):
        line = lines[index]
        if _HEADING_PATTERN.match(line.strip()):
            break
        if line.strip():
            section.append((index + 1, line))

    if len(section) < 3:
        raise ConfigurationError(f"{path}:{start + 1}: Journals table is missing or empty")

    header_line, header = section[0]
    if _table_cells(header, path, header_line) != ("Journal", "ISSN/EISSN"):
        raise ConfigurationError(
            f"{path}:{header_line}: expected table header '| Journal | ISSN/EISSN |'"
        )

    separator_line, separator = section[1]
    separator_cells = _table_cells(separator, path, separator_line)
    if not all(_SEPARATOR_PATTERN.fullmatch(cell) for cell in separator_cells):
        raise ConfigurationError(
            f"{path}:{separator_line}: invalid Markdown table separator"
        )

    journals: list[JournalConfig] = []
    line_numbers: list[int] = []
    for line_number, row in section[2:]:
        name, raw_issns = _table_cells(row, path, line_number)
        if not name:
            raise ConfigurationError(f"{path}:{line_number}: Journal must not be empty")
        if not raw_issns:
            raise ConfigurationError(f"{path}:{line_number}: ISSN/EISSN must not be empty")

        journal_issns: list[str] = []
        for raw_issn in raw_issns.split("/"):
            if not raw_issn.strip():
                raise ConfigurationError(
                    f"{path}:{line_number}: ISSN/EISSN contains an empty value"
                )
            journal_issns.append(raw_issn.strip())

        journals.append(JournalConfig(name=name, issn=tuple(journal_issns)))
        line_numbers.append(line_number)

    return validate_journal_configs(
        journals,
        path=path,
        line_numbers=line_numbers,
    )


def parse_journal_whitelist(path: Path) -> tuple[JournalConfig, ...]:
    path = path.resolve()
    return parse_journal_whitelist_text(_read_text(path), path=path)


def validate_journal_storage(journals: Sequence[JournalConfig]) -> None:
    """Validate constraints of the current Markdown table storage adapter."""

    for journal in journals:
        if any(character in journal.name for character in ("|", "\n", "\r")):
            raise ConfigurationError(
                (
                    f"journal name {journal.name!r} cannot be represented in the "
                    "current Markdown journal table"
                ),
                field="journals",
            )


def render_journal_whitelist_text(
    existing_contents: str | None,
    journals: Sequence[JournalConfig],
    *,
    path: Path,
) -> str:
    """Render only the current Journals section while preserving other content."""

    normalized = validate_journal_configs(journals)
    validate_journal_storage(normalized)
    rows = [
        "## Journals",
        "",
        "| Journal | ISSN/EISSN |",
        "|---|---|",
    ]
    rows.extend(
        f"| {journal.name} | {' / '.join(journal.issn)} |"
        for journal in normalized
    )
    section = "\n".join(rows) + "\n\n"
    if existing_contents is None:
        return "# List\n\n" + section

    lines = existing_contents.splitlines(keepends=True)
    headings = [
        index
        for index, line in enumerate(lines)
        if line.rstrip("\r\n").strip() == "## Journals"
    ]
    if len(headings) > 1:
        raise ConfigurationError(
            f"{path}: expected at most one '## Journals' section, found {len(headings)}",
            field="journals",
            path=path,
        )
    if not headings:
        prefix = existing_contents
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        if prefix and not prefix.endswith("\n\n"):
            prefix += "\n"
        return prefix + section

    start = headings[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].lstrip().startswith("## "):
            end = index
            break
    return "".join(lines[:start]) + section + "".join(lines[end:])


def _format_validation_error(path: Path, error: ValidationError) -> ConfigurationError:
    detail = error.errors()[0]
    field = ".".join(str(part) for part in detail["loc"]) or "configuration"
    return ConfigurationError(
        f"{path}: field {field!r}: {detail['msg']}",
        field=field,
        path=path,
    )


def resolve_config_path(config_path: Path, value: Path | None, default_name: str) -> Path:
    candidate = value if value is not None else Path(default_name)
    if not candidate.is_absolute():
        candidate = config_path.parent / candidate
    return candidate.resolve()


def parse_monitor_definition(path: Path, raw: Any) -> MonitorDefinition:
    """Validate monitor fields and apply config-file defaults without I/O."""

    path = path.resolve()
    if not isinstance(raw, dict):
        raise ConfigurationError(
            f"{path}: configuration must be a YAML mapping",
            path=path,
        )

    try:
        settings = _Settings.model_validate(raw)
    except ValidationError as error:
        raise _format_validation_error(path, error) from error

    date_spec = DateRangeSpec(
        from_date=settings.from_date,
        to_date=settings.to_date,
        window_days=settings.window_days,
    )
    if (
        date_spec.from_date is None
        and date_spec.to_date is None
        and date_spec.window_days is None
    ):
        date_spec = DateRangeSpec(window_days=DEFAULT_WINDOW_DAYS)
    try:
        validate_date_range_spec(date_spec)
    except DateRangeError as error:
        raise ConfigurationError(
            f"{path}: field {error.field!r}: {error}",
            field=error.field,
            path=path,
        ) from error

    try:
        keyword_ast = parse_keyword_expression(settings.keyword_expression)
    except KeywordSyntaxError as error:
        raise ConfigurationError(
            f"{path}: field 'keyword_expression': {error}",
            field="keyword_expression",
            path=path,
        ) from error

    return MonitorDefinition(
        name=settings.name if settings.name is not None else path.stem,
        venue_whitelist=(
            settings.venue_whitelist
            if settings.venue_whitelist is not None
            else Path("list.md")
        ),
        keyword_expression=settings.keyword_expression,
        keyword_ast=keyword_ast,
        output_dir=(
            settings.output_dir
            if settings.output_dir is not None
            else Path("workspace")
        ),
        date_spec=date_spec,
        log_level=settings.log_level,
    )


def build_loaded_config(
    path: Path,
    definition: MonitorDefinition,
    journals: Sequence[JournalConfig],
) -> LoadedConfig:
    """Resolve paths and journal-domain rules into the runtime config model."""

    path = path.resolve()
    normalized_journals = validate_journal_configs(journals)
    return LoadedConfig(
        name=definition.name,
        venue_whitelist=resolve_config_path(
            path,
            definition.venue_whitelist,
            "list.md",
        ),
        keyword_expression=definition.keyword_expression,
        keyword_ast=definition.keyword_ast,
        output_dir=resolve_config_path(path, definition.output_dir, "workspace"),
        date_spec=definition.date_spec,
        log_level=definition.log_level,
        journals=normalized_journals,
    )


def validate_runtime_keyword(config: LoadedConfig) -> None:
    """Validate configured keyword semantics using the production FTS5 backend."""

    validate_search_expression(config.keyword_ast)


def resolve_runtime_date(
    config: LoadedConfig,
    *,
    today: date,
    date_spec: DateRangeSpec | None = None,
) -> ResolvedDateRange:
    """Resolve the effective runtime date policy with production semantics."""

    return resolve_date_range(
        date_spec if date_spec is not None else config.date_spec,
        today=today,
    )


def validate_runtime_config(
    config: LoadedConfig,
    *,
    today: date,
) -> ResolvedDateRange:
    """Run deterministic runtime checks shared by Monitor and Settings."""

    validate_runtime_keyword(config)
    return resolve_runtime_date(config, today=today)


def load_config(path: Path) -> LoadedConfig:
    path = path.resolve()
    contents = _read_text(path)
    try:
        raw = yaml.safe_load(contents)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        location = f":{mark.line + 1}:{mark.column + 1}" if mark is not None else ""
        raise ConfigurationError(
            f"{path}{location}: invalid YAML: {error}",
            path=path,
        ) from error

    definition = parse_monitor_definition(path, raw)
    whitelist = resolve_config_path(path, definition.venue_whitelist, "list.md")
    try:
        journals = parse_journal_whitelist(whitelist)
    except ConfigurationError as error:
        raise ConfigurationError(
            f"{path}: field 'venue_whitelist': {error}",
            field="venue_whitelist",
            path=path,
        ) from error
    return build_loaded_config(path, definition, journals)
