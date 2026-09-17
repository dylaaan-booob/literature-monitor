"""Project configuration and journal whitelist loading."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError, field_validator

from literature_monitor.keywords import KeywordExpression, KeywordSyntaxError, parse_keyword_expression

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_ISSN_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{3}[0-9X]$")
_HEADING_PATTERN = re.compile(r"^##\s+")
_SEPARATOR_PATTERN = re.compile(r"^:?-{3,}:?$")


class ConfigurationError(ValueError):
    """A user-facing configuration error with file and field context."""


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

    venue_whitelist: Path
    keyword_expression: NonEmptyStr
    log_level: LogLevel = LogLevel.INFO

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value


@dataclass(frozen=True)
class LoadedConfig:
    venue_whitelist: Path
    keyword_expression: str
    keyword_ast: KeywordExpression
    log_level: LogLevel
    journals: tuple[JournalConfig, ...]


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


def _normalize_issn(raw: str, path: Path, line_number: int) -> str:
    issn = raw.strip().upper()
    if not _ISSN_PATTERN.fullmatch(issn):
        raise ConfigurationError(
            f"{path}:{line_number}: invalid ISSN/EISSN {raw!r}; expected NNNN-NNNN"
        )
    if not _valid_issn_checksum(issn):
        raise ConfigurationError(
            f"{path}:{line_number}: invalid ISSN/EISSN checksum for {issn!r}"
        )
    return issn


def parse_journal_whitelist(path: Path) -> tuple[JournalConfig, ...]:
    path = path.resolve()
    lines = _read_text(path).splitlines()
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
    names: dict[str, int] = {}
    issns: dict[str, int] = {}
    for line_number, row in section[2:]:
        name, raw_issns = _table_cells(row, path, line_number)
        if not name:
            raise ConfigurationError(f"{path}:{line_number}: Journal must not be empty")
        normalized_name = name.casefold()
        if normalized_name in names:
            raise ConfigurationError(
                f"{path}:{line_number}: duplicate Journal {name!r}; first seen at line {names[normalized_name]}"
            )
        if not raw_issns:
            raise ConfigurationError(f"{path}:{line_number}: ISSN/EISSN must not be empty")

        journal_issns: list[str] = []
        for raw_issn in raw_issns.split("/"):
            if not raw_issn.strip():
                raise ConfigurationError(
                    f"{path}:{line_number}: ISSN/EISSN contains an empty value"
                )
            issn = _normalize_issn(raw_issn, path, line_number)
            if issn in issns:
                raise ConfigurationError(
                    f"{path}:{line_number}: duplicate ISSN/EISSN {issn!r}; first seen at line {issns[issn]}"
                )
            issns[issn] = line_number
            journal_issns.append(issn)

        names[normalized_name] = line_number
        journals.append(JournalConfig(name=name, issn=tuple(journal_issns)))

    if not journals:
        raise ConfigurationError(f"{path}: Journals section contains no entries")
    return tuple(journals)


def _format_validation_error(path: Path, error: ValidationError) -> ConfigurationError:
    detail = error.errors()[0]
    field = ".".join(str(part) for part in detail["loc"]) or "configuration"
    return ConfigurationError(f"{path}: field {field!r}: {detail['msg']}")


def load_config(path: Path) -> LoadedConfig:
    path = path.resolve()
    contents = _read_text(path)
    try:
        raw = yaml.safe_load(contents)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        location = f":{mark.line + 1}:{mark.column + 1}" if mark is not None else ""
        raise ConfigurationError(f"{path}{location}: invalid YAML: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{path}: configuration must be a YAML mapping")

    try:
        settings = _Settings.model_validate(raw)
    except ValidationError as error:
        raise _format_validation_error(path, error) from error

    whitelist = settings.venue_whitelist
    if not whitelist.is_absolute():
        whitelist = path.parent / whitelist
    whitelist = whitelist.resolve()

    try:
        keyword_ast = parse_keyword_expression(settings.keyword_expression)
    except KeywordSyntaxError as error:
        raise ConfigurationError(
            f"{path}: field 'keyword_expression': {error}"
        ) from error

    journals = parse_journal_whitelist(whitelist)
    return LoadedConfig(
        venue_whitelist=whitelist,
        keyword_expression=settings.keyword_expression,
        keyword_ast=keyword_ast,
        log_level=settings.log_level,
        journals=journals,
    )
