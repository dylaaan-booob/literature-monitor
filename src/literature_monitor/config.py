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
from literature_monitor.url_safety import normalize_public_http_url

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

    issn_l: NonEmptyStr
    name: NonEmptyStr
    publisher_id: str | None = None
    group: NonEmptyStr | None = None

    @field_validator("issn_l")
    @classmethod
    def normalize_identity(cls, value: str) -> str:
        return normalize_journal_issn(value)

    @field_validator("publisher_id")
    @classmethod
    def normalize_publisher(cls, value: str | None) -> str | None:
        return normalize_publisher_id(value) if value else None


class PublisherConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    publisher_id: str
    name: NonEmptyStr
    access_url: str | None = None

    @field_validator("publisher_id")
    @classmethod
    def normalize_identity(cls, value: str) -> str:
        return normalize_publisher_id(value)

    @field_validator("access_url")
    @classmethod
    def normalize_access_url(cls, value: str | None) -> str | None:
        return normalize_public_http_url(value)


class LegacyJournal(BaseModel):
    """Migration input only; never supplied to LoadedConfig or production retrieval."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    name: NonEmptyStr
    issn: tuple[NonEmptyStr, ...]
    group: NonEmptyStr | None = None


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


def parse_journal_table_cells(
    line: str,
    path: Path,
    line_number: int,
    columns: int | None = None,
) -> tuple[str, ...]:
    """Read cells from an explicit Journal table schema."""
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        raise ConfigurationError(
            f"{path}:{line_number}: expected a Markdown table row"
        )
    cells = tuple(cell.strip() for cell in stripped[1:-1].split("|"))
    if len(cells) not in ((2, 3, 4) if columns is None else (columns,)):
        expected = "2, 3 or 4" if columns is None else str(columns)
        raise ConfigurationError(
            f"{path}:{line_number}: expected {expected} table columns, found {len(cells)}"
        )
    return cells


def _valid_issn_checksum(issn: str) -> bool:
    digits = issn.replace("-", "")
    values = [10 if character == "X" else int(character) for character in digits]
    return sum(value * weight for value, weight in zip(values, range(8, 0, -1))) % 11 == 0


def normalize_journal_issn(raw: str) -> str:
    """Normalize one configured ISSN and validate its checksum."""
    issn = raw.strip().upper()
    if not _ISSN_PATTERN.fullmatch(issn):
        raise ValueError(f"invalid ISSN/EISSN {raw!r}; expected NNNN-NNNN")
    if not _valid_issn_checksum(issn):
        raise ValueError(f"invalid ISSN/EISSN checksum for {issn!r}")
    return issn


def normalize_publisher_id(raw: str) -> str:
    """Accept only a direct OpenAlex Publisher ID, stored as its canonical URL."""
    value = raw.strip()
    match = re.fullmatch(r"(?:https?://openalex\.org/)?(P[0-9]+)", value, re.IGNORECASE)
    if match is None:
        raise ValueError(f"invalid direct OpenAlex Publisher ID {raw!r}")
    return "https://openalex.org/" + match[1].upper()


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

    identities: dict[str, int | None] = {}
    normalized: list[JournalConfig] = []
    for index, journal in enumerate(journals):
        line_number = line_numbers[index] if line_numbers is not None else None
        try:
            item = JournalConfig.model_validate(journal.model_dump())
        except ValueError as error:
            raise _journal_error(str(error), path=path, line_number=line_number) from error
        if item.issn_l in identities:
            first = identities[item.issn_l]
            suffix = f"; first seen at line {first}" if first is not None else ""
            raise _journal_error(f"duplicate ISSN-L {item.issn_l!r}{suffix}",
                                 path=path, line_number=line_number)
        identities[item.issn_l] = line_number
        normalized.append(item)
    return tuple(normalized)


def journal_whitelist_table(
    contents: str,
    *,
    path: Path,
) -> tuple[int, tuple[tuple[int, str], ...]]:
    """Validate the Journals table structure and return numbered raw data rows."""

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
    header_cells = parse_journal_table_cells(header, path, header_line)
    if header_cells not in (
        ("Journal", "ISSN/EISSN"),
        ("Journal", "ISSN/EISSN", "Group"),
        ("Journal", "ISSN-L", "Publisher ID", "Group"),
        ("Journal", "ISSN-L"),
        ("Journal", "ISSN-L", "Group"),
    ):
        raise ConfigurationError(
            f"{path}:{header_line}: expected table header '| Journal | ISSN/EISSN |' "
            "or target '| Journal | ISSN-L | Publisher ID | Group |'"
        )
    columns = len(header_cells)

    separator_line, separator = section[1]
    separator_cells = parse_journal_table_cells(separator, path, separator_line, columns)
    if not all(_SEPARATOR_PATTERN.fullmatch(cell) for cell in separator_cells):
        raise ConfigurationError(
            f"{path}:{separator_line}: invalid Markdown table separator"
        )

    return columns, tuple(section[2:])


def _journal_header(contents: str) -> tuple[int, tuple[str, ...]]:
    lines = contents.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "## Journals")
    header = next(line for line in lines[start + 1:] if line.strip())
    cells = tuple(cell.strip() for cell in header.strip()[1:-1].split("|"))
    return len(cells), cells


def _journal_table_is_legacy(contents: str) -> bool:
    try:
        _, cells = _journal_header(contents)
        return cells[1] == "ISSN/EISSN"
    except (StopIteration, IndexError):
        return False


def parse_legacy_journal_whitelist_text(contents: str, *, path: Path) -> tuple[LegacyJournal, ...]:
    """Read v0.6.1 storage explicitly for migration; no canonical identity guessing."""
    columns, rows = journal_whitelist_table(contents, path=path)
    if not _journal_table_is_legacy(contents):
        raise _journal_error("expected legacy Journal + ISSN/EISSN migration input", path=path)
    journals = []
    for number, row in rows:
        cells = parse_journal_table_cells(row, path, number, columns)
        try:
            identifiers = tuple(normalize_journal_issn(value) for value in cells[1].split("/"))
            journals.append(LegacyJournal(name=cells[0], issn=identifiers,
                                           group=(cells[2] or None) if columns == 3 else None))
        except ValueError as error:
            raise _journal_error(str(error), path=path, line_number=number) from error
    return tuple(journals)


def parse_journal_whitelist_text(contents: str, *, path: Path) -> tuple[JournalConfig, ...]:
    """Load target storage only. Legacy tables require explicit migration."""
    columns, rows = journal_whitelist_table(contents, path=path)
    if _journal_table_is_legacy(contents):
        raise _journal_error("legacy Journals storage: migration required to configured ISSN-L", path=path)
    if columns != 4:
        raise _journal_error("target storage requires Journal, ISSN-L, Publisher ID, Group", path=path)
    journals, numbers = [], []
    for number, row in rows:
        name, issn_l, publisher, group = parse_journal_table_cells(row, path, number, columns)
        if not name:
            raise _journal_error("Journal must not be empty", path=path, line_number=number)
        if not issn_l:
            raise _journal_error("ISSN-L must not be empty", path=path, line_number=number)
        try:
            journals.append(JournalConfig(name=name, issn_l=issn_l,
                                           publisher_id=publisher or None, group=group or None))
        except ValueError as error:
            raise _journal_error(str(error), path=path, line_number=number) from error
        numbers.append(number)
    return validate_journal_configs(journals, path=path, line_numbers=numbers)


def parse_journal_whitelist(path: Path) -> tuple[JournalConfig, ...]:
    path = path.resolve()
    return parse_journal_whitelist_text(_read_text(path), path=path)


def validate_journal_storage(journals: Sequence[JournalConfig]) -> None:
    """Validate constraints of the current Markdown table storage adapter."""

    for journal in journals:
        for field, value in (("name", journal.name), ("group", journal.group)):
            if value is None:
                continue
            unsafe = any(character in value for character in ("|", "\n", "\r"))
            if field == "group" and value.splitlines() != [value]:
                unsafe = True
            if unsafe:
                raise ConfigurationError(
                    (
                        f"journal {field} {value!r} cannot be represented in the "
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
    lines = (existing_contents or "").splitlines(keepends=True)
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

    start = headings[0] if headings else len(lines)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].lstrip().startswith("## "):
            end = index
            break

    rows = ["## Journals", "", "| Journal | ISSN-L | Publisher ID | Group |", "| --- | --- | --- | --- |"]
    for journal in normalized:
        rows.append(f"| {journal.name} | {journal.issn_l} | {journal.publisher_id or ''} | {journal.group or ''} |")
    section = "\n".join(rows) + "\n\n"
    if existing_contents is None:
        return "# List\n\n" + section

    if not headings:
        prefix = existing_contents
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        if prefix and not prefix.endswith("\n\n"):
            prefix += "\n"
        return prefix + section

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


def validate_publisher_configs(publishers: Sequence[PublisherConfig]) -> tuple[PublisherConfig, ...]:
    normalized = []
    seen = set()
    for publisher in publishers:
        try:
            item = PublisherConfig.model_validate(publisher.model_dump())
        except ValueError as error:
            raise ConfigurationError(str(error), field="publishers") from error
        if item.publisher_id in seen:
            raise ConfigurationError(f"duplicate Publisher ID {item.publisher_id}", field="publishers")
        for value in (item.name, item.access_url):
            if value is not None and any(c == "|" or ord(c) < 32 or ord(c) == 127 for c in value):
                raise ConfigurationError("Publisher content cannot be represented in Markdown table", field="publishers")
        seen.add(item.publisher_id)
        normalized.append(item)
    return tuple(normalized)


def parse_publisher_whitelist_text(contents: str, *, path: Path) -> tuple[PublisherConfig, ...]:
    lines = contents.splitlines()
    headings = [i for i, line in enumerate(lines) if line.strip() == "## Publishers"]
    if not headings:
        return ()  # Pre-A4 documents are read without rewriting them.
    if len(headings) != 1:
        raise ConfigurationError("expected exactly one Publishers section", field="publishers", path=path)
    section = []
    for i in range(headings[0] + 1, len(lines)):
        if _HEADING_PATTERN.match(lines[i].strip()):
            break
        if lines[i].strip():
            section.append((i + 1, lines[i]))
    if len(section) < 2:
        raise ConfigurationError("Publishers table is missing", field="publishers", path=path)
    header = parse_journal_table_cells(section[0][1], path, section[0][0], 3)
    separator = parse_journal_table_cells(section[1][1], path, section[1][0], 3)
    if header != ("Publisher", "OpenAlex ID", "Access URL") or not all(_SEPARATOR_PATTERN.fullmatch(c) for c in separator):
        raise ConfigurationError("invalid Publishers table header/separator", field="publishers", path=path)
    result = []
    for number, line in section[2:]:
        name, identity, url = parse_journal_table_cells(line, path, number, 3)
        try:
            result.append(PublisherConfig(name=name, publisher_id=identity, access_url=url or None))
        except ValueError as error:
            raise ConfigurationError(f"{path}:{number}: {error}", field="publishers", path=path) from error
    return validate_publisher_configs(result)


def validate_publisher_membership(journals: Sequence[JournalConfig], publishers: Sequence[PublisherConfig]) -> None:
    if {p.publisher_id for p in publishers} != {j.publisher_id for j in journals if j.publisher_id is not None}:
        raise ConfigurationError("Publisher rows must exactly match active direct Journal Publisher IDs", field="publishers")


def render_settings_list_text(existing_contents: str | None, journals: Sequence[JournalConfig],
                              publishers: Sequence[PublisherConfig], *, path: Path) -> str:
    """Prepare one coherent list target; neither section has an independent write path."""
    publishers = validate_publisher_configs(publishers)
    validate_publisher_membership(journals, publishers)
    target = render_journal_whitelist_text(existing_contents, journals, path=path)
    lines = target.splitlines(keepends=True)
    headings = [i for i, line in enumerate(lines) if line.strip() == "## Publishers"]
    if len(headings) > 1:
        raise ConfigurationError("duplicate Publishers section", field="publishers", path=path)
    start = headings[0] if headings else next((i for i, line in enumerate(lines) if line.strip() == "## Conferences"), len(lines))
    end = start if not headings else next((i for i in range(start + 1, len(lines)) if _HEADING_PATTERN.match(lines[i].strip())), len(lines))
    rows = ["## Publishers", "", "| Publisher | OpenAlex ID | Access URL |", "| --- | --- | --- |"]
    rows.extend(f"| {p.name} | {p.publisher_id} | {p.access_url or ''} |" for p in publishers)
    section = "\n".join(rows) + "\n\n"
    prefix = "".join(lines[:start])
    if prefix and not prefix.endswith("\n\n"):
        prefix += "\n" if prefix.endswith("\n") else "\n\n"
    return prefix + section + "".join(lines[end:])
