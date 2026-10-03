"""Crossref-only revision-bound Provider state (§37.6) and safe publication."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile

from pydantic import TypeAdapter

from literature_monitor.application.runtime_metadata import metadata_directory
from literature_monitor.crossref import (
    CrossrefWorkRecord, parse_crossref_indexed_at, validate_normalized_crossref_record,
)
from literature_monitor.identifiers import normalize_doi
from literature_monitor.provider_revision import parse_revision_timestamp

SCHEMA_VERSION = 3
CROSSREF_SERIALIZATION_VERSION = 1
STATE_FILENAME = "provider-state.sqlite3"
_REVISION_TIMESTAMP = TypeAdapter(datetime | None)
_SCHEMA = (
    "CREATE TABLE schema_metadata (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), schema_version INTEGER NOT NULL)",
    "CREATE TABLE crossref_records (doi TEXT PRIMARY KEY NOT NULL, indexed_at TEXT NOT NULL, retrieved_at TEXT NOT NULL, semantic_hash TEXT NOT NULL, record_json TEXT NOT NULL)",
)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate serialized field")
        result[key] = value
    return result


def _payload(text: str, version: int, field: str) -> object:
    if not isinstance(text, str):
        raise ValueError("serialized payload must be text")
    value = json.loads(text, object_pairs_hook=_unique_object)
    if (not isinstance(value, dict) or set(value) != {"serialization_version", field}
            or type(value["serialization_version"]) is not int
            or value["serialization_version"] != version):
        raise ValueError("incompatible Provider-state serialization")
    return value[field]


def _canonical_time(value: datetime) -> datetime:
    if (not isinstance(value, datetime) or value.utcoffset() != timedelta(0)
            or parse_revision_timestamp(value) != value):
        raise ValueError("durable timestamp must already be timezone-aware UTC")
    return value


def _time_text(value: datetime) -> str:
    return _canonical_time(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _read_time(value: str) -> datetime:
    parsed = parse_revision_timestamp(value)
    if parsed is None or _time_text(parsed) != value:
        raise ValueError("noncanonical durable timestamp")
    return parsed


def _validate_record(record: CrossrefWorkRecord) -> None:
    validate_normalized_crossref_record(record)
    _canonical_time(record.provenance.retrieved_at)
    if record.indexed_at is not None:
        _canonical_time(parse_crossref_indexed_at(record.indexed_at))
    for relation in record.relations:
        if relation.id_type.casefold() == "doi" and normalize_doi(relation.identifier) != relation.identifier:
            raise ValueError("relation DOI must already be normalized")
    for author in record.authors:
        if author.openalex_id is not None and not re.fullmatch(r"https://openalex\.org/A\d+", author.openalex_id):
            raise ValueError("noncanonical OpenAlex author ID")


def _crossref_record_data(record: CrossrefWorkRecord) -> dict[str, object]:
    """Include revision only in state payloads, outside the legacy record surface."""
    data = record.model_dump(mode="json")
    data["indexed_at"] = _REVISION_TIMESTAMP.dump_python(record.indexed_at, mode="json")
    return data


def serialize_crossref_record(record: CrossrefWorkRecord) -> str:
    data = _crossref_record_data(record)
    # Domain validators may trim values; durable reads/writes must reject repairs.
    parsed = CrossrefWorkRecord.model_validate_json(_json(data), strict=True)
    if _crossref_record_data(parsed) != data:
        raise ValueError("noncanonical normalized Crossref record")
    _validate_record(parsed)
    return _json({"serialization_version": CROSSREF_SERIALIZATION_VERSION, "record": data})


def deserialize_crossref_record(text: str) -> CrossrefWorkRecord:
    data = _payload(text, CROSSREF_SERIALIZATION_VERSION, "record")
    record = CrossrefWorkRecord.model_validate_json(_json(data), strict=True)
    if _crossref_record_data(record) != data:
        raise ValueError("noncanonical normalized Crossref record")
    _validate_record(record)
    return record


def crossref_semantic_hash(record: CrossrefWorkRecord) -> str:
    fields = {
        "doi", "title", "journal", "abstract", "authors", "issns", "dates", "relations", "work_type",
    }
    data = record.model_dump(mode="json", include=fields)
    return hashlib.sha256(_json(data).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CrossrefRecordState:
    doi: str
    indexed_at: datetime
    retrieved_at: datetime
    semantic_hash: str
    record: CrossrefWorkRecord

    def __post_init__(self) -> None:
        if not isinstance(self.doi, str) or normalize_doi(self.doi) != self.doi:
            raise ValueError("state DOI must already be normalized")
        _canonical_time(self.indexed_at)
        _canonical_time(self.retrieved_at)
        serialize_crossref_record(self.record)
        if (self.record.doi != self.doi
                or parse_crossref_indexed_at(self.record.indexed_at) != parse_crossref_indexed_at(self.indexed_at)
                or self.record.provenance.retrieved_at != self.retrieved_at
                or self.semantic_hash != crossref_semantic_hash(self.record)):
            raise ValueError("Crossref state identity/revision/provenance/hash mismatch")

    @classmethod
    def from_record(cls, record: CrossrefWorkRecord) -> CrossrefRecordState:
        return cls(record.doi, record.indexed_at, record.provenance.retrieved_at,
                   crossref_semantic_hash(record), record)


@dataclass(frozen=True)
class ProviderState:
    crossref_records: tuple[CrossrefRecordState, ...] = ()

    def __post_init__(self) -> None:
        if len({row.doi for row in self.crossref_records}) != len(self.crossref_records):
            raise ValueError("duplicate Crossref DOI in Provider state")


class ProviderStateStatus(str, Enum):
    MISSING = "missing"
    AVAILABLE = "available"
    INVALID = "invalid"


@dataclass(frozen=True)
class ProviderStateReadResult:
    status: ProviderStateStatus
    state: ProviderState | None = None
    error: str | None = None
    replaceable: bool = False


def _regular_file(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(mode):
        raise OSError("Provider-state path is not a regular file")
    return True


def _connect(path: Path, *, readonly: bool) -> sqlite3.Connection:
    # Opening a WAL DB can create -shm even in read-only mode. Reject it before SQLite.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        header = stream.read(20)
    if header.startswith(b"SQLite format 3\x00") and 2 in header[18:20]:
        raise ValueError("unsupported Provider-state WAL format")
    return sqlite3.connect(path.absolute().as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
                           uri=True, timeout=0)


def _schema_version(connection: sqlite3.Connection) -> int:
    rows = connection.execute("SELECT singleton, schema_version FROM schema_metadata").fetchall()
    if rows != [(1, SCHEMA_VERSION)]:
        raise ValueError("unsupported Provider-state schema version")
    return rows[0][1]


def _read_connection(connection: sqlite3.Connection) -> ProviderState:
    if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise ValueError("corrupt Provider-state DB")
    if connection.execute("PRAGMA journal_mode").fetchone() != ("delete",):
        raise ValueError("unsupported Provider-state journal mode")
    schema = connection.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name").fetchall()
    if sorted(row[0] for row in schema) != sorted(_SCHEMA):
        raise ValueError("incompatible Provider-state schema")
    _schema_version(connection)
    crossref = []
    for doi, indexed, retrieved, digest, payload in connection.execute(
        "SELECT doi, indexed_at, retrieved_at, semantic_hash, record_json FROM crossref_records ORDER BY doi"
    ):
        record = deserialize_crossref_record(payload)
        crossref.append(CrossrefRecordState(
            doi, _read_time(indexed), _read_time(retrieved), digest, record,
        ))
    return ProviderState(tuple(crossref))


def read_provider_state(output_dir: Path) -> ProviderStateReadResult:
    try:
        directory = metadata_directory(output_dir, create=False)
        if directory is None or not _regular_file(directory / STATE_FILENAME):
            return ProviderStateReadResult(ProviderStateStatus.MISSING)
        with closing(_connect(directory / STATE_FILENAME, readonly=True)) as connection:
            connection.execute("BEGIN")
            state = _read_connection(connection)
        return ProviderStateReadResult(ProviderStateStatus.AVAILABLE, state)
    except (OSError, sqlite3.OperationalError) as error:
        # Busy/permission/path failures do not authorize replacement of a DB.
        return ProviderStateReadResult(ProviderStateStatus.INVALID, error=str(error))
    except (sqlite3.DatabaseError, ValueError, TypeError) as error:
        return ProviderStateReadResult(ProviderStateStatus.INVALID, error=str(error), replaceable=True)


def _upsert_changes(connection: sqlite3.Connection, state: ProviderState) -> None:
    connection.executemany(
        "INSERT INTO crossref_records VALUES (?, ?, ?, ?, ?) ON CONFLICT(doi) DO UPDATE SET indexed_at=excluded.indexed_at, retrieved_at=excluded.retrieved_at, semantic_hash=excluded.semantic_hash, record_json=excluded.record_json",
        [(row.doi, _time_text(row.indexed_at), _time_text(row.retrieved_at), row.semantic_hash,
          serialize_crossref_record(row.record)) for row in state.crossref_records],
    )


def _fresh_database(path: Path, state: ProviderState) -> None:
    with closing(_connect(path, readonly=False)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        for statement in _SCHEMA:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_metadata VALUES (1, ?)", (SCHEMA_VERSION,))
        _upsert_changes(connection, state)
        _read_connection(connection)


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_fresh(output_dir: Path, state: ProviderState, *, replace: bool) -> None:
    directory = metadata_directory(output_dir, create=True)
    path = directory / STATE_FILENAME
    existed = _regular_file(path)
    if any(os.path.lexists(str(path) + suffix) for suffix in ("-journal", "-wal", "-shm")):
        raise OSError("cannot publish over Provider state with existing SQLite journal files")
    if existed != replace:
        raise OSError("Provider-state path changed before publication")
    previous = path.lstat() if existed else None
    descriptor, name = tempfile.mkstemp(prefix=".provider-state-", suffix=".sqlite3", dir=directory)
    os.close(descriptor)
    temporary = Path(name)
    backup = temporary.with_suffix(".previous")
    preserve_backup = False
    try:
        _fresh_database(temporary, state)
        with closing(_connect(temporary, readonly=True)) as connection:
            _read_connection(connection)
        descriptor = os.open(temporary, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        metadata_directory(output_dir, create=False)
        if _regular_file(path) != existed or (previous is not None and path.lstat() != previous):
            raise OSError("Provider-state path changed before publication")
        if replace:
            os.link(path, backup, follow_symlinks=False)
            os.replace(temporary, path)
        else:
            os.link(temporary, path, follow_symlinks=False)
        try:
            _fsync_directory(directory)
        except OSError:
            # Restore the preceding file if publication durability cannot be confirmed.
            try:
                if replace:
                    os.replace(backup, path)
                else:
                    path.unlink()
            except OSError as error:
                preserve_backup = replace
                raise OSError(f"publication rollback failed; previous DB retained at {backup}") from error
            raise
    finally:
        temporary.unlink(missing_ok=True)
        if not preserve_backup:
            backup.unlink(missing_ok=True)


def update_provider_state(output_dir: Path, changes: ProviderState) -> None:
    """One transaction/upsert set; missing DB creation is published atomically."""
    directory = metadata_directory(output_dir, create=False)
    if directory is None or not _regular_file(directory / STATE_FILENAME):
        _publish_fresh(output_dir, changes, replace=False)
        return
    with closing(_connect(directory / STATE_FILENAME, readonly=False)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        _read_connection(connection)
        _upsert_changes(connection, changes)
        _read_connection(connection)


def replace_invalid_provider_state(output_dir: Path, fresh_state: ProviderState) -> None:
    """Publish complete fresh state only over an invalid regular DB; never unlink it first."""
    result = read_provider_state(output_dir)
    if result.status is not ProviderStateStatus.INVALID or not result.replaceable:
        raise OSError("Provider state is not an invalid replaceable regular DB")
    _publish_fresh(output_dir, fresh_state, replace=True)
