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
from literature_monitor.openalex import OpenAlexVersion, OpenAlexVersionHint

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


def openalex(work_id: str = "https://openalex.org/W1") -> ps.OpenAlexVersionState:
    return ps.OpenAlexVersionState(work_id, NOW, NOW, (
        OpenAlexVersionHint(source="doi", identifier="10.5555/a", version=OpenAlexVersion.PUBLISHED,
                            url="https://doi.org/10.5555/a"),
    ))


def changes() -> ps.ProviderState:
    return ps.ProviderState((crossref(),), (openalex(),))


def state_path(output_dir: Path) -> Path:
    return output_dir / ".literature-monitor" / ps.STATE_FILENAME


def connect(output_dir: Path) -> sqlite3.Connection:
    return sqlite3.connect(state_path(output_dir))


def write_v1(output_dir: Path, state: ps.ProviderState) -> None:
    state_path(output_dir).parent.mkdir(parents=True)
    with connect(output_dir) as connection:
        for statement in ps._SCHEMA:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_metadata VALUES (1, 1)")
        connection.executemany("INSERT INTO crossref_records VALUES (?, ?, ?, ?, ?)", [
            (row.doi, ps._time_text(row.indexed_at), ps._time_text(row.retrieved_at),
             ps._crossref_semantic_hash_v1(row.record),
             json.dumps(json.loads(ps.serialize_crossref_record(row.record)), indent=2))
            for row in state.crossref_records
        ])
        connection.executemany("INSERT INTO openalex_versions VALUES (?, ?, ?, ?)", [
            (row.work_id, ps._time_text(row.hydrated_against_updated_at), ps._time_text(row.retrieved_at),
             ps.serialize_openalex_versions(row.version_hints)) for row in state.openalex_versions
        ])
    connection.close()


def durable_rows(output_dir: Path) -> tuple:
    with connect(output_dir) as connection:
        rows = tuple(connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                     for table in ("schema_metadata", "crossref_records", "openalex_versions"))
    connection.close()
    return rows


def test_independent_schema_and_serialization_versions() -> None:
    assert ps.SCHEMA_VERSION == 2
    assert ps.CROSSREF_SERIALIZATION_VERSION == ps.OPENALEX_SERIALIZATION_VERSION == 1
    assert LAST_RUN_SCHEMA_VERSION == 2


def test_complete_roundtrip_schema_and_no_wal(tmp_path: Path) -> None:
    expected = changes()
    ps.update_provider_state(tmp_path, expected)
    result = ps.read_provider_state(tmp_path)
    assert result.status is ps.ProviderStateStatus.AVAILABLE
    assert result.state == expected
    with connect(tmp_path) as connection:
        assert connection.execute("SELECT * FROM schema_metadata").fetchall() == [(1, 2)]
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"schema_metadata", "crossref_records", "openalex_versions"}
        assert [row[1] for row in connection.execute("PRAGMA table_info(crossref_records)")] == [
            "doi", "indexed_at", "retrieved_at", "semantic_hash", "record_json",
        ]
        assert [row[1] for row in connection.execute("PRAGMA table_info(openalex_versions)")] == [
            "work_id", "hydrated_against_updated_at", "retrieved_at", "versions_json",
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


def test_v1_hash_preserves_original_algorithm_and_ignores_work_type() -> None:
    record = crossref().record
    expected = "a8015ec157e4ecc1c146385aa4cb4da3679455a7f10273abc103d9eb55698bfc"
    assert ps._crossref_semantic_hash_v1(record) == expected
    assert ps._crossref_semantic_hash_v1(record.model_copy(update={"work_type": "book"})) == expected
    assert ps.crossref_semantic_hash(record) != expected


def test_v1_read_converts_in_memory_without_durable_changes(tmp_path: Path) -> None:
    expected = changes()
    write_v1(tmp_path, expected)
    before, before_bytes = durable_rows(tmp_path), state_path(tmp_path).read_bytes()
    result = ps.read_provider_state(tmp_path)
    assert result.status is ps.ProviderStateStatus.AVAILABLE
    assert result.state == expected
    assert result.state.crossref_records[0].semantic_hash != before[1][0][3]
    assert durable_rows(tmp_path) == before
    assert state_path(tmp_path).read_bytes() == before_bytes
    assert list(state_path(tmp_path).parent.iterdir()) == [state_path(tmp_path)]


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


@pytest.mark.parametrize("field,value", [
    ("source", "unknown"), ("identifier", "10.5555/A"), ("identifier", " bad id"),
    ("url", "file:///tmp/paper"), ("url", "https://"), ("url", "https://example.org:bad"),
    ("version", "unknown"), ("extra", "value"),
])
def test_strict_version_payload(field: str, value: str) -> None:
    payload = json.loads(ps.serialize_openalex_versions(openalex().version_hints))
    payload["version_hints"][0][field] = value
    with pytest.raises(ValueError):
        ps.deserialize_openalex_versions(json.dumps(payload))


@pytest.mark.parametrize("serializer,deserializer,payload", [
    (ps.serialize_crossref_record, ps.deserialize_crossref_record, "record"),
    (ps.serialize_openalex_versions, ps.deserialize_openalex_versions, "versions"),
])
@pytest.mark.parametrize("version", [2, True, "1"])
def test_serialization_versions_are_independent_and_strict(serializer, deserializer, payload, version) -> None:
    value = crossref().record if payload == "record" else openalex().version_hints
    envelope = json.loads(serializer(value))
    envelope["serialization_version"] = version
    with pytest.raises(ValueError):
        deserializer(json.dumps(envelope))


def test_missing_revision_remains_transient_but_not_durable() -> None:
    record = crossref().record.model_copy(update={"indexed_at": None})
    assert record.to_evidence().title == "Study"
    with pytest.raises(ValueError):
        ps.CrossrefRecordState.from_record(record)
    with pytest.raises(ValueError):
        replace(openalex(), hydrated_against_updated_at=None)


@pytest.mark.parametrize("update", [
    {"doi": "10.5555/A"}, {"doi": "10.5555/b"}, {"indexed_at": NOW + timedelta(days=1)},
    {"retrieved_at": NOW + timedelta(days=1)}, {"retrieved_at": NOW.replace(tzinfo=None)},
    {"retrieved_at": NOW.astimezone(timezone(timedelta(hours=8)))}, {"semantic_hash": "bad"},
])
def test_crossref_state_invariants(update) -> None:
    with pytest.raises(ValueError):
        replace(crossref(), **update)


@pytest.mark.parametrize("work_id", ["W1", "https://openalex.org/w1", "https://openalex.org/S1", " https://openalex.org/W1"])
def test_openalex_state_identity(work_id: str) -> None:
    with pytest.raises(ValueError):
        openalex(work_id)


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("damage", [
    "UPDATE schema_metadata SET schema_version=999", "DELETE FROM schema_metadata",
    "UPDATE schema_metadata SET singleton=0, schema_version=1",
    "ALTER TABLE crossref_records ADD COLUMN extra TEXT", "CREATE TABLE membership (id TEXT)",
    "DROP TABLE openalex_versions", "UPDATE crossref_records SET semantic_hash='bad'",
    "UPDATE crossref_records SET record_json='{}'", "UPDATE crossref_records SET indexed_at='2026-09-26T00:00:00Z'",
    "UPDATE crossref_records SET doi='10.5555/b'",
    "UPDATE crossref_records SET indexed_at='2026-09-27T00:00:00.000000Z'",
    "UPDATE crossref_records SET retrieved_at='2026-09-27T00:00:00.000000Z'",
    "UPDATE crossref_records SET record_json=json_set(record_json, '$.serialization_version', 2)",
    "UPDATE crossref_records SET record_json=json_set(record_json, '$.record.title', ' Study ')",
    "UPDATE crossref_records SET record_json=json_set(record_json, '$.record.provenance.record_id', '10.5555/b')",
    "UPDATE openalex_versions SET work_id='W1'",
    "UPDATE openalex_versions SET versions_json='{}'",
])
def test_invalid_state_read_is_untrusted_and_nonmutating(tmp_path: Path, damage: str, version: int) -> None:
    if version == 1:
        write_v1(tmp_path, changes())
    else:
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


@pytest.mark.parametrize("version", [1, 2])
def test_persisted_hash_must_use_durable_logical_version(tmp_path: Path, version: int) -> None:
    write_v1(tmp_path, changes())
    wrong_hash = (ps.crossref_semantic_hash if version == 1 else ps._crossref_semantic_hash_v1)(crossref().record)
    with connect(tmp_path) as connection:
        connection.execute("UPDATE schema_metadata SET schema_version=?", (version,))
        connection.execute("UPDATE crossref_records SET semantic_hash=?", (wrong_hash,))
    connection.close()
    assert ps.read_provider_state(tmp_path).status is ps.ProviderStateStatus.INVALID


def test_v1_migration_rehashes_all_history_and_preserves_untouched_payloads(tmp_path: Path) -> None:
    initial = ps.ProviderState((crossref(), crossref("10.5555/history")), (openalex("https://openalex.org/W999"),))
    write_v1(tmp_path, initial)
    before = durable_rows(tmp_path)
    revised = ps.CrossrefRecordState.from_record(crossref().record.model_copy(update={"work_type": "journal-issue"}))
    pending = ps.ProviderState((revised, crossref("10.5555/new")), (openalex(),))
    ps.update_provider_state(tmp_path, pending)
    metadata, rows, oa = durable_rows(tmp_path)
    assert metadata == [(1, 2)]
    assert {row[0] for row in rows} == {"10.5555/a", "10.5555/history", "10.5555/new"}
    for row in rows:
        assert row[3] == ps.crossref_semantic_hash(ps.deserialize_crossref_record(row[4]))
    historical = next(row for row in rows if row[0] == "10.5555/history")
    assert historical[:3] == before[1][1][:3] and historical[4] == before[1][1][4]
    assert before[2][0] in oa
    state = ps.read_provider_state(tmp_path).state
    assert revised in state.crossref_records and pending.openalex_versions[0] in state.openalex_versions
    assert list(state_path(tmp_path).parent.iterdir()) == [state_path(tmp_path)]


def test_v2_upsert_does_not_repeat_legacy_rehash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert before[1][0] in after[1] and before[2] == after[2]


@pytest.mark.parametrize("phase", ["hashes", "metadata", "pending", "validation"])
def test_v1_migration_failure_rolls_back_every_phase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str) -> None:
    initial = ps.ProviderState((crossref(), crossref("10.5555/history")), (openalex(),))
    write_v1(tmp_path, initial)
    before = durable_rows(tmp_path)
    original_connect, original_upsert, original_read = ps._connect, ps._upsert_changes, ps._read_connection

    class FaultConnection(sqlite3.Connection):
        def executemany(self, sql, parameters):
            result = super().executemany(sql, parameters)
            if phase == "hashes" and sql.startswith("UPDATE crossref_records"):
                raise sqlite3.IntegrityError("after historical hashes")
            return result

        def execute(self, sql, parameters=()):
            result = super().execute(sql, parameters)
            if phase == "metadata" and sql.startswith("UPDATE schema_metadata"):
                raise sqlite3.IntegrityError("after schema metadata")
            return result

    def fault_connect(path, *, readonly):
        return original_connect(path, readonly=True) if readonly else sqlite3.connect(path, factory=FaultConnection)

    def fault_upsert(connection, pending):
        original_upsert(connection, pending)
        if phase == "pending":
            raise sqlite3.IntegrityError("after pending upsert")

    def fault_read(connection):
        result = original_read(connection)
        if phase == "validation" and ps._schema_version(connection) == 2:
            raise ValueError("after final v2 validation")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(ps, "_connect", fault_connect)
        patch.setattr(ps, "_upsert_changes", fault_upsert)
        patch.setattr(ps, "_read_connection", fault_read)
        with pytest.raises((sqlite3.IntegrityError, ValueError)):
            ps.update_provider_state(tmp_path, ps.ProviderState((crossref("10.5555/new"),),
                                                             (openalex("https://openalex.org/W3"),)))
    assert durable_rows(tmp_path) == before
    assert ps.read_provider_state(tmp_path).state == initial
    assert list(state_path(tmp_path).parent.iterdir()) == [state_path(tmp_path)]


def test_transaction_upsert_retains_history(tmp_path: Path) -> None:
    initial = ps.ProviderState((crossref(), crossref("10.5555/history")), (openalex(), openalex("https://openalex.org/W2")))
    ps.update_provider_state(tmp_path, initial)
    record = crossref().record.model_copy(update={"title": "Updated", "indexed_at": NOW + timedelta(days=1)})
    updated = ps.CrossrefRecordState.from_record(record)
    oa = replace(openalex(), hydrated_against_updated_at=NOW + timedelta(days=1), version_hints=())
    ps.update_provider_state(tmp_path, ps.ProviderState((updated,), (oa,)))
    assert ps.read_provider_state(tmp_path).state == ps.ProviderState((updated, initial.crossref_records[1]), (oa, initial.openalex_versions[1]))


@pytest.mark.parametrize("provider", ["crossref", "openalex"])
@pytest.mark.parametrize("conflicting", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_duplicate_state_identity_is_rejected_before_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    provider: str, conflicting: bool, existing: bool,
) -> None:
    initial = changes()
    if existing:
        ps.update_provider_state(tmp_path, initial)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    if provider == "crossref":
        first = crossref()
        second = ps.CrossrefRecordState.from_record(first.record.model_copy(update={"title": "Other"})) if conflicting else first
        inputs = {"crossref_records": (first, second)}
        message = "duplicate Crossref DOI"
    else:
        first = openalex()
        second = replace(first, hydrated_against_updated_at=NOW + timedelta(days=1)) if conflicting else first
        inputs = {"openalex_versions": (first, second)}
        message = "duplicate OpenAlex Work ID"

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

    def fail_after_first_table(connection, state):
        original_upsert(connection, ps.ProviderState(state.crossref_records))
        raise sqlite3.IntegrityError("simulated second-table failure")

    monkeypatch.setattr(ps, "_connect", tracking_connect)
    monkeypatch.setattr(ps, "_upsert_changes", fail_after_first_table)
    with pytest.raises(sqlite3.IntegrityError):
        ps.update_provider_state(tmp_path, ps.ProviderState((crossref("10.5555/new"),), (openalex("https://openalex.org/W3"),)))
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
