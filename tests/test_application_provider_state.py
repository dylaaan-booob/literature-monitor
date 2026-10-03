"""Strict revision-bound state, transaction isolation, and safe binary publication."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3

import pytest

from literature_monitor.application import provider_state as ps
from literature_monitor.application.run_state import LAST_RUN_SCHEMA_VERSION
from literature_monitor.crossref import normalize_crossref_discovered_work

NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


def crossref(doi: str = "10.5555/a") -> ps.CrossrefRecordState:
    record, warnings = normalize_crossref_discovered_work({
        "DOI": doi, "title": ["Study"], "container-title": ["Biometrics"],
        "abstract": "<p>Abstract</p>", "ISSN": ["0006-341X"],
        "author": [{"given": "Ada", "family": "Author", "ORCID": "0000-0002-1825-0097"}],
        "published": {"date-parts": [[2026, 1, 1]]},
        "relation": {"is-version-of": [{"id-type": "doi", "id": "10.5555/parent"}]},
        "indexed": {"date-time": "2026-09-26T00:00:00Z"}, "type": "journal-article",
    }, NOW)
    assert not warnings
    return ps.CrossrefRecordState.from_record(record)


def changes() -> ps.ProviderState:
    return ps.ProviderState((crossref(),))


def state_path(output_dir: Path) -> Path:
    return output_dir / ".literature-monitor" / ps.STATE_FILENAME


def connect(output_dir: Path) -> sqlite3.Connection:
    return sqlite3.connect(state_path(output_dir))


def durable_rows(output_dir: Path) -> tuple:
    with connect(output_dir) as connection:
        rows = tuple(connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                     for table in ("schema_metadata", "crossref_records"))
    connection.close()
    return rows


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("legacy_table", [False, True])
def test_old_schemas_are_not_reused_converted_or_updated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: int, legacy_table: bool) -> None:
    ps.update_provider_state(tmp_path, changes())
    with connect(tmp_path) as connection:
        connection.execute("UPDATE schema_metadata SET schema_version=?", (version,))
        if legacy_table:
            connection.execute("CREATE TABLE openalex_versions (work_id TEXT PRIMARY KEY NOT NULL, hydrated_against_updated_at TEXT NOT NULL, retrieved_at TEXT NOT NULL, versions_json TEXT NOT NULL)")
    connection.close()
    before = state_path(tmp_path).read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("incompatible state must not deserialize historical records for reuse")

    monkeypatch.setattr(ps, "deserialize_crossref_record", forbidden)
    result = ps.read_provider_state(tmp_path)
    assert result.status is ps.ProviderStateStatus.INVALID
    assert result.state is None and result.replaceable
    assert "schema" in result.error
    with pytest.raises(ValueError, match="schema"):
        ps.update_provider_state(tmp_path, changes())
    assert state_path(tmp_path).read_bytes() == before
    assert list(state_path(tmp_path).parent.iterdir()) == [state_path(tmp_path)]


def test_independent_schema_and_serialization_versions() -> None:
    assert ps.SCHEMA_VERSION == 3
    assert ps.CROSSREF_SERIALIZATION_VERSION == 1
    assert LAST_RUN_SCHEMA_VERSION == 2


def test_complete_roundtrip_schema_and_no_wal(tmp_path: Path) -> None:
    expected = changes()
    ps.update_provider_state(tmp_path, expected)
    result = ps.read_provider_state(tmp_path)
    assert result.status is ps.ProviderStateStatus.AVAILABLE
    assert result.state == expected
    with connect(tmp_path) as connection:
        assert connection.execute("SELECT * FROM schema_metadata").fetchall() == [(1, 3)]
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"schema_metadata", "crossref_records"}
        assert [row[1] for row in connection.execute("PRAGMA table_info(crossref_records)")] == [
            "doi", "indexed_at", "retrieved_at", "semantic_hash", "record_json",
        ]
    connection.close()
    assert sorted(path.name for path in state_path(tmp_path).parent.iterdir()) == [ps.STATE_FILENAME]


@pytest.mark.parametrize("metadata_exists", [False, True])
def test_missing_read_creates_nothing(tmp_path: Path, metadata_exists: bool) -> None:
    if metadata_exists:
        state_path(tmp_path).parent.mkdir()
    before = list(tmp_path.rglob("*"))
    result = ps.read_provider_state(tmp_path)
    assert result.status is ps.ProviderStateStatus.MISSING
    assert result.state is None
    assert list(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("field", ["doi", "title", "journal", "abstract", "authors", "issns", "dates", "relations", "work_type"])
def test_hash_changes_for_consumed_semantics(field: str) -> None:
    record = crossref().record
    updates = {
        "doi": "10.5555/b", "title": "Other", "journal": "Other", "abstract": "Other",
        "authors": (record.authors[0].model_copy(update={"name": "Other"}),),
        "issns": (), "dates": (), "relations": (), "work_type": "book",
    }
    assert ps.crossref_semantic_hash(record) != ps.crossref_semantic_hash(record.model_copy(update={field: updates[field]}))


def test_hash_ignores_revision_and_provenance() -> None:
    record = crossref().record
    digest = ps.crossref_semantic_hash(record)
    for update in (
        {"indexed_at": NOW + timedelta(days=1)},
        {"provenance": record.provenance.model_copy(update={"retrieved_at": NOW + timedelta(days=1)})},
    ):
        assert ps.crossref_semantic_hash(record.model_copy(update=update)) == digest
    assert ps.crossref_semantic_hash(ps.deserialize_crossref_record(ps.serialize_crossref_record(record))) == digest


@pytest.mark.parametrize("field,value", [
    ("doi", " 10.5555/a"), ("doi", "10.5555/A"), ("title", " Study "),
    ("issns", ["0006-341x"]), ("indexed_at", "2026-09-26T08:00:00+08:00"),
    ("indexed_at", "2026-09-26T00:00:00"), ("extra", "value"),
])
def test_strict_crossref_payload_rejects_repairs(field: str, value: object) -> None:
    payload = json.loads(ps.serialize_crossref_record(crossref().record))
    payload["record"][field] = value
    with pytest.raises(ValueError):
        ps.deserialize_crossref_record(json.dumps(payload))


@pytest.mark.parametrize("field,value", [
    ("provider", "openalex"), ("record_id", "10.5555/b"),
    ("retrieved_at", "2026-09-26T08:00:00+08:00"),
])
def test_strict_crossref_provenance(field: str, value: str) -> None:
    payload = json.loads(ps.serialize_crossref_record(crossref().record))
    payload["record"]["provenance"][field] = value
    with pytest.raises(ValueError):
        ps.deserialize_crossref_record(json.dumps(payload))


def test_strict_author_relation_and_duplicate_fields() -> None:
    payload = json.loads(ps.serialize_crossref_record(crossref().record))
    payload["record"]["authors"][0]["orcid"] = "0000-0002-1825-0097"
    with pytest.raises(ValueError):
        ps.deserialize_crossref_record(json.dumps(payload))
    payload = json.loads(ps.serialize_crossref_record(crossref().record))
    payload["record"]["relations"][0]["identifier"] = "10.5555/PARENT"
    with pytest.raises(ValueError):
        ps.deserialize_crossref_record(json.dumps(payload))
    with pytest.raises(ValueError, match="duplicate"):
        ps.deserialize_crossref_record('{"serialization_version":1,"serialization_version":1,"record":{}}')


@pytest.mark.parametrize("version", [2, True, "1"])
def test_crossref_serialization_version_is_independent_and_strict(version) -> None:
    envelope = json.loads(ps.serialize_crossref_record(crossref().record))
    envelope["serialization_version"] = version
    with pytest.raises(ValueError):
        ps.deserialize_crossref_record(json.dumps(envelope))


def test_missing_revision_remains_transient_but_not_durable() -> None:
    record = crossref().record.model_copy(update={"indexed_at": None})
    assert record.to_evidence().title == "Study"
    with pytest.raises(ValueError):
        ps.CrossrefRecordState.from_record(record)


@pytest.mark.parametrize("update", [
    {"doi": "10.5555/A"}, {"doi": "10.5555/b"}, {"indexed_at": NOW + timedelta(days=1)},
    {"retrieved_at": NOW + timedelta(days=1)}, {"retrieved_at": NOW.replace(tzinfo=None)},
    {"retrieved_at": NOW.astimezone(timezone(timedelta(hours=8)))}, {"semantic_hash": "bad"},
])
def test_crossref_state_invariants(update) -> None:
    with pytest.raises(ValueError):
        replace(crossref(), **update)


@pytest.mark.parametrize("damage", [
    "UPDATE schema_metadata SET schema_version=999", "DELETE FROM schema_metadata",
    "UPDATE schema_metadata SET singleton=0, schema_version=1",
    "ALTER TABLE crossref_records ADD COLUMN extra TEXT", "CREATE TABLE membership (id TEXT)",
    "DROP TABLE crossref_records", "CREATE TABLE openalex_versions (work_id TEXT)", "UPDATE crossref_records SET semantic_hash='bad'",
    "UPDATE crossref_records SET record_json='{}'", "UPDATE crossref_records SET indexed_at='2026-09-26T00:00:00Z'",
    "UPDATE crossref_records SET doi='10.5555/b'",
    "UPDATE crossref_records SET indexed_at='2026-09-27T00:00:00.000000Z'",
    "UPDATE crossref_records SET retrieved_at='2026-09-27T00:00:00.000000Z'",
    "UPDATE crossref_records SET record_json=json_set(record_json, '$.serialization_version', 2)",
    "UPDATE crossref_records SET record_json=json_set(record_json, '$.record.title', ' Study ')",
    "UPDATE crossref_records SET record_json=json_set(record_json, '$.record.provenance.record_id', '10.5555/b')",
])
def test_invalid_state_read_is_untrusted_and_nonmutating(tmp_path: Path, damage: str) -> None:
    ps.update_provider_state(tmp_path, changes())
    with connect(tmp_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(damage)
    connection.close()
    before = state_path(tmp_path).read_bytes()
    result = ps.read_provider_state(tmp_path)
    assert result.status is ps.ProviderStateStatus.INVALID
    assert result.state is None
    assert state_path(tmp_path).read_bytes() == before
    assert list(state_path(tmp_path).parent.iterdir()) == [state_path(tmp_path)]
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        ps.update_provider_state(tmp_path, changes())
    assert state_path(tmp_path).read_bytes() == before


def test_v3_upsert_preserves_existing_rows_without_rehash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ps.update_provider_state(tmp_path, changes())
    before = durable_rows(tmp_path)
    statements = []
    original = ps._connect

    def trace(*args, **kwargs):
        connection = original(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(ps, "_connect", trace)
    ps.update_provider_state(tmp_path, ps.ProviderState((crossref("10.5555/new"),)))
    assert not any(sql.startswith(("UPDATE crossref_records", "UPDATE schema_metadata")) for sql in statements)
    after = durable_rows(tmp_path)
    assert before[1][0] in after[1] and before[0] == after[0]


@pytest.mark.parametrize("phase", ["pending", "validation"])
def test_transaction_failure_rolls_back_pending_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str) -> None:
    initial = ps.ProviderState((crossref(), crossref("10.5555/history")))
    ps.update_provider_state(tmp_path, initial)
    before = durable_rows(tmp_path)
    original_upsert, original_read = ps._upsert_changes, ps._read_connection
    reads = 0

    def fault_upsert(connection, pending):
        original_upsert(connection, pending)
        if phase == "pending":
            raise sqlite3.IntegrityError("after pending upsert")

    def fault_read(connection):
        nonlocal reads
        result = original_read(connection)
        reads += 1
        if phase == "validation" and reads == 2:
            raise ValueError("after final state validation")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(ps, "_upsert_changes", fault_upsert)
        patch.setattr(ps, "_read_connection", fault_read)
        with pytest.raises((sqlite3.IntegrityError, ValueError)):
            ps.update_provider_state(tmp_path, ps.ProviderState((crossref("10.5555/new"),)))
    assert durable_rows(tmp_path) == before
    assert ps.read_provider_state(tmp_path).state == initial
    assert list(state_path(tmp_path).parent.iterdir()) == [state_path(tmp_path)]


def test_transaction_upsert_retains_history(tmp_path: Path) -> None:
    initial = ps.ProviderState((crossref(), crossref("10.5555/history")))
    ps.update_provider_state(tmp_path, initial)
    record = crossref().record.model_copy(update={"title": "Updated", "indexed_at": NOW + timedelta(days=1)})
    updated = ps.CrossrefRecordState.from_record(record)
    ps.update_provider_state(tmp_path, ps.ProviderState((updated,)))
    assert ps.read_provider_state(tmp_path).state == ps.ProviderState((updated, initial.crossref_records[1]))


@pytest.mark.parametrize("conflicting", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_duplicate_state_identity_is_rejected_before_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    conflicting: bool, existing: bool,
) -> None:
    initial = changes()
    if existing:
        ps.update_provider_state(tmp_path, initial)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    first = crossref()
    second = ps.CrossrefRecordState.from_record(first.record.model_copy(update={"title": "Other"})) if conflicting else first
    inputs = {"crossref_records": (first, second)}
    message = "duplicate Crossref DOI"

    def unexpected_persistence(*args, **kwargs):
        pytest.fail("duplicate state reached persistence")

    with monkeypatch.context() as patch:
        patch.setattr(ps, "_connect", unexpected_persistence)
        patch.setattr(ps, "_publish_fresh", unexpected_persistence)
        with pytest.raises(ValueError, match=message):
            ps.update_provider_state(tmp_path, ps.ProviderState(**inputs))
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
    if existing:
        assert ps.read_provider_state(tmp_path).state == initial
    else:
        assert not list(tmp_path.iterdir())


def test_transaction_rollback_and_connections_close(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ps.update_provider_state(tmp_path, changes())
    opened = []
    original_connect, original_upsert = ps._connect, ps._upsert_changes

    def tracking_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    def fail_after_upsert(connection, state):
        original_upsert(connection, ps.ProviderState(state.crossref_records))
        raise sqlite3.IntegrityError("simulated failure after upsert")

    monkeypatch.setattr(ps, "_connect", tracking_connect)
    monkeypatch.setattr(ps, "_upsert_changes", fail_after_upsert)
    with pytest.raises(sqlite3.IntegrityError):
        ps.update_provider_state(tmp_path, ps.ProviderState((crossref("10.5555/new"),)))
    assert ps.read_provider_state(tmp_path).state == changes()
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_busy_state_is_not_replaced_or_retried(tmp_path: Path) -> None:
    ps.update_provider_state(tmp_path, changes())
    with connect(tmp_path) as locked:
        locked.execute("BEGIN EXCLUSIVE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            ps.update_provider_state(tmp_path, changes())
        result = ps.read_provider_state(tmp_path)
        assert result.status is ps.ProviderStateStatus.INVALID
        assert not result.replaceable
        with pytest.raises(OSError):
            ps.replace_invalid_provider_state(tmp_path, changes())
    locked.close()
    assert ps.read_provider_state(tmp_path).state == changes()


def corrupt_db(tmp_path: Path) -> bytes:
    path = state_path(tmp_path)
    path.parent.mkdir()
    content = b"invalid sqlite file\x00preserve these bytes"
    path.write_bytes(content)
    return content


def test_safe_fresh_replacement_validates_before_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prior = corrupt_db(tmp_path)
    assert ps.read_provider_state(tmp_path).status is ps.ProviderStateStatus.INVALID
    assert state_path(tmp_path).read_bytes() == prior
    original = os.replace
    calls = []

    def publish(source, target):
        assert state_path(tmp_path).read_bytes() == prior
        with sqlite3.connect(source) as complete:
            assert complete.execute("PRAGMA quick_check").fetchone() == ("ok",)
            assert complete.execute("SELECT COUNT(*) FROM crossref_records").fetchone() == (1,)
        complete.close()
        calls.append((source, target))
        original(source, target)

    monkeypatch.setattr(ps.os, "replace", publish)
    ps.replace_invalid_provider_state(tmp_path, changes())
    assert len(calls) == 1
    assert ps.read_provider_state(tmp_path).state == changes()
    assert list(state_path(tmp_path).parent.iterdir()) == [state_path(tmp_path)]


@pytest.mark.parametrize("failure_at", ["construction", "validation", "fsync", "replace", "directory_fsync"])
def test_failed_replacement_preserves_previous_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_at: str) -> None:
    prior = corrupt_db(tmp_path)

    def fail(*args, **kwargs):
        raise OSError("simulated replacement failure")

    if failure_at == "construction":
        monkeypatch.setattr(ps, "_fresh_database", fail)
    elif failure_at == "validation":
        original = ps._read_connection

        def validate(connection):
            filename = connection.execute("PRAGMA database_list").fetchone()[2]
            if Path(filename).name.startswith(".provider-state-"):
                raise ValueError("invalid replacement")
            return original(connection)

        monkeypatch.setattr(ps, "_read_connection", validate)
    elif failure_at == "fsync":
        monkeypatch.setattr(ps.os, "fsync", fail)
    elif failure_at == "replace":
        monkeypatch.setattr(ps.os, "replace", fail)
    else:
        monkeypatch.setattr(ps, "_fsync_directory", fail)
    with pytest.raises((OSError, ValueError)):
        ps.replace_invalid_provider_state(tmp_path, changes())
    assert state_path(tmp_path).read_bytes() == prior
    assert list(state_path(tmp_path).parent.iterdir()) == [state_path(tmp_path)]


@pytest.mark.parametrize("at_metadata_directory,object_type", [
    (False, "symlink"), (False, "directory"), (False, "fifo"),
    (True, "symlink"), (True, "file"), (True, "fifo"),
])
def test_unsafe_objects_remain_untouched(tmp_path: Path, object_type: str, at_metadata_directory: bool) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"preserve")
    path = state_path(tmp_path).parent if at_metadata_directory else state_path(tmp_path)
    if not at_metadata_directory:
        path.parent.mkdir()
    if object_type == "symlink":
        path.symlink_to(outside if at_metadata_directory else sentinel)
    elif object_type == "directory":
        path.mkdir()
    elif object_type == "file":
        path.write_bytes(b"preserve metadata object")
    else:
        os.mkfifo(path)
    mode = path.lstat().st_mode
    result = ps.read_provider_state(tmp_path)
    assert result.status is ps.ProviderStateStatus.INVALID
    assert not result.replaceable
    with pytest.raises(OSError):
        ps.update_provider_state(tmp_path, changes())
    with pytest.raises(OSError):
        ps.replace_invalid_provider_state(tmp_path, changes())
    assert path.lstat().st_mode == mode
    assert sentinel.read_bytes() == b"preserve"


def test_other_runtime_files_and_workspace_are_preserved(tmp_path: Path) -> None:
    for name in (".literature-monitor/provider-cache.json", ".literature-monitor/last-run.json", "Papers/paper.md", "Authors/author.md", "Inbox.base", "monitor.yaml"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"human state")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    ps.update_provider_state(tmp_path, changes())
    assert all(path.read_bytes() == content for path, content in before.items())


def test_wal_read_creates_nothing_and_closed_wal_db_can_be_replaced(tmp_path: Path) -> None:
    ps.update_provider_state(tmp_path, changes())
    with connect(tmp_path) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    connection.close()
    before = {path.name: path.read_bytes() for path in state_path(tmp_path).parent.iterdir()}
    result = ps.read_provider_state(tmp_path)
    assert result.status is ps.ProviderStateStatus.INVALID
    assert {path.name: path.read_bytes() for path in state_path(tmp_path).parent.iterdir()} == before
    ps.replace_invalid_provider_state(tmp_path, changes())
    assert ps.read_provider_state(tmp_path).state == changes()
    assert list(state_path(tmp_path).parent.iterdir()) == [state_path(tmp_path)]


def test_publication_rollback_failure_keeps_prior_bytes_for_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prior = corrupt_db(tmp_path)
    original_replace = os.replace

    def fail_sync(directory):
        raise OSError("publication fsync failed")

    def fail_rollback(source, target):
        if str(source).endswith(".previous"):
            raise OSError("rollback failed")
        original_replace(source, target)

    monkeypatch.setattr(ps, "_fsync_directory", fail_sync)
    monkeypatch.setattr(ps.os, "replace", fail_rollback)
    with pytest.raises(OSError, match="previous DB retained"):
        ps.replace_invalid_provider_state(tmp_path, changes())
    backup, = state_path(tmp_path).parent.glob("*.previous")
    assert backup.read_bytes() == prior


def test_state_specific_record_serialization_preserves_excluded_revision(tmp_path: Path) -> None:
    record = crossref().record
    revised = record.model_copy(update={"indexed_at": NOW + timedelta(microseconds=123456)})
    assert revised.model_dump() == record.model_dump()
    payload = ps.serialize_crossref_record(revised)
    assert json.loads(payload)["record"]["indexed_at"] == "2026-09-26T00:00:00.123456Z"
    restored = ps.deserialize_crossref_record(payload)
    assert restored == revised
    assert restored.indexed_at == revised.indexed_at
    assert ps.crossref_semantic_hash(restored) == ps.crossref_semantic_hash(record)
    state = ps.CrossrefRecordState.from_record(restored)
    ps.update_provider_state(tmp_path, ps.ProviderState((state,)))
    assert ps.read_provider_state(tmp_path).state.crossref_records == (state,)
