"""Retained-only versions: revision binding, failure isolation, no durable access."""

from datetime import date, datetime, timedelta, timezone
import sqlite3

import httpx
import pytest

from literature_monitor.application import provider_state as ps
from literature_monitor.application.openalex_retrieval import hydrate_retained_openalex_versions
from literature_monitor.models import Author, CanonicalMetadata, ExternalIds, MetadataSource
from literature_monitor.openalex import (
    OpenAlexClient, OpenAlexVersion, OpenAlexVersionHint, OpenAlexWorkRecord,
)

NOW = datetime(2026, 1, 31, tzinfo=timezone.utc)


def record(work="W1", revision=NOW):
    return OpenAlexWorkRecord(
        metadata=CanonicalMetadata(title="Study", journal="Biometrics", publication_date=date(2026, 1, 1)),
        authors=(Author(name="Ada"),), external_ids=ExternalIds(openalex=f"https://openalex.org/{work}", doi="10.5555/study"),
        source_id="https://openalex.org/S1",
        provenance=MetadataSource(provider="openalex", record_id=f"https://openalex.org/{work}", retrieved_at=NOW),
        updated_at=revision,
    )


def location(identifier="doi:10.5555/STUDY", version="publishedVersion", url="https://doi.org/10.5555/study"):
    return {"id": identifier, "version": version, "landing_page_url": url}


def payload(*works):
    return {"meta": {"count": len(works)}, "results": list(works)}


def hydrated(work="W1", locations=None):
    return {"id": f"https://openalex.org/{work}", "locations": [location()] if locations is None else locations}


class Transport(httpx.MockTransport):
    def __init__(self, *outcomes):
        self.outcomes, self.requests = list(outcomes), []
        self.closed = False
        super().__init__(self.respond)

    def respond(self, request):
        self.requests.append(request)
        assert self.outcomes, f"unexpected request {request.url}"
        outcome = self.outcomes.pop(0)
        return outcome if isinstance(outcome, httpx.Response) else httpx.Response(200, json=outcome)

    def close(self):
        self.closed = True
        super().close()


def client_for(*outcomes):
    transport = Transport(*outcomes)
    return OpenAlexClient(transport=transport, sleep=lambda _: None), transport


def state(work="W1", revision=NOW):
    return ps.OpenAlexVersionState(f"https://openalex.org/{work}", revision, NOW, (
        OpenAlexVersionHint(source="doi", identifier="10.5555/old", version=OpenAlexVersion.ACCEPTED),
    ))


def test_matching_revision_reuses_once_without_http_or_historical_candidates():
    live = (record(), record())
    old = state()
    client, transport = client_for()
    with client:
        result = hydrate_retained_openalex_versions(client, live, version_state=(old, state("W99")), retrieved_at=NOW)
    assert len(result.records) == 2
    assert result.records[0].version_hints == old.version_hints
    assert result.reused_work_ids == (live[0].external_ids.openalex,)
    assert not result.hydrated_work_ids and not result.pending_changes and not result.issues
    assert not transport.requests and transport.closed


@pytest.mark.parametrize("revision", [NOW, None])
def test_changed_or_missing_revision_hydrates_retained_ids_once_and_binds_pending_state(revision):
    live = (record(revision=revision), record(revision=revision))
    client, transport = client_for(payload(hydrated()))
    with client:
        result = hydrate_retained_openalex_versions(client, live, version_state=(state(revision=NOW - timedelta(days=1)), state("W99")), retrieved_at=NOW)
    assert len(transport.requests) == 1
    params = dict(transport.requests[0].url.params)
    assert params == {"filter": "ids.openalex:W1", "select": "id,locations", "per_page": "100"}
    assert result.hydrated_work_ids == ("https://openalex.org/W1",)
    assert not result.reused_work_ids
    assert len(result.pending_changes) == (0 if revision is None else 1)
    if revision is not None:
        assert result.pending_changes[0].hydrated_against_updated_at == revision
        assert result.pending_changes[0].retrieved_at == NOW
    assert len(result.records) == len(live)
    for old, new in zip(live, result.records, strict=True):
        assert old.metadata == new.metadata
        assert old.external_ids == new.external_ids
        assert old.authors == new.authors
        assert old.provenance == new.provenance
        assert new.version_hints[0].identifier == "10.5555/study"


def test_location_normalization_reuses_existing_identity_roles_and_preference():
    locations = [location(url=None), location(),
                 location("pmh:oai:arxiv.org:2401.12345", "submittedVersion", "https://arxiv.org/abs/2401.12345"),
                 location("pmh:repository:123", "acceptedVersion", "https://example.org/123"),
                 {"version": "unknown"}, "bad"]
    client, _ = client_for(payload(hydrated(locations=locations)))
    with client:
        result = hydrate_retained_openalex_versions(client, (record(),), retrieved_at=NOW)
    hints = result.records[0].version_hints
    assert [(hint.source, hint.identifier) for hint in hints] == [
        ("arxiv", "2401.12345"), ("doi", "10.5555/study"), ("openalex_location", "pmh:repository:123"),
    ]
    assert hints[1].url == "https://doi.org/10.5555/study"
    assert len(result.issues) == 2
    assert all(issue.stage == "version_hydration" and issue.journal == "Biometrics" for issue in result.issues)
    assert result.pending_changes[0].version_hints == hints


def test_failed_work_hydration_preserves_candidate_and_successful_peer():
    live = (record(), record("W2"))
    client, transport = client_for(payload(hydrated()), httpx.Response(404))
    with client:
        result = hydrate_retained_openalex_versions(client, live, version_state=(state("W2", NOW - timedelta(days=1)),), retrieved_at=NOW)
    # W2 state deliberately changed so its old hint cannot leak after failure.
    assert len(result.records) == 2
    assert result.records[0].version_hints
    assert result.records[1].version_hints == ()
    assert result.hydrated_work_ids == ("https://openalex.org/W1",)
    assert [row.work_id for row in result.pending_changes] == ["https://openalex.org/W1"]
    assert result.issues[0].record_id == "https://openalex.org/W2"
    assert len(transport.requests) == 2


@pytest.mark.parametrize("failure", ["request", "missing", "malformed", "duplicate"])
def test_recoverable_batch_hydration_splits_only_unresolved_works(failure):
    initial = payload(hydrated())
    if failure == "request":
        initial = httpx.Response(500)
        outcomes = [initial, initial, initial, payload(hydrated()), payload(hydrated("W2"))]
    else:
        if failure == "malformed":
            initial["results"].append({"id": "W2", "locations": None})
        if failure == "duplicate":
            initial = payload(hydrated(), hydrated(), hydrated("W2"))
        fallback = hydrated() if failure == "duplicate" else hydrated("W2")
        outcomes = [initial, payload(fallback)]
    client, transport = client_for(*outcomes)
    with client:
        result = hydrate_retained_openalex_versions(client, (record(), record("W2")), retrieved_at=NOW)
    assert len(result.hydrated_work_ids) == 2
    assert len(result.pending_changes) == 2
    assert not result.issues
    assert len(transport.requests) == (5 if failure == "request" else 2)


@pytest.mark.parametrize("status,requests", [(401, 1), (403, 1), (429, 3)])
def test_terminal_version_failure_prevents_fallback_storm(status, requests):
    client, transport = client_for(*[httpx.Response(status)] * requests)
    with client:
        result = hydrate_retained_openalex_versions(client, (record(), record("W2")), retrieved_at=NOW)
    assert len(transport.requests) == requests
    assert len(result.records) == 2
    assert all(not record.version_hints for record in result.records)
    assert not result.pending_changes and not result.hydrated_work_ids
    assert len(result.issues) == 2


@pytest.mark.parametrize("locations", [[], None, "bad"])
def test_empty_locations_succeed_but_missing_or_malformed_locations_fail(locations):
    raw = {"id": "W1", "locations": locations}
    client, _ = client_for(payload(raw))
    with client:
        result = hydrate_retained_openalex_versions(client, (record(),), retrieved_at=NOW)
    assert result.records[0].version_hints == ()
    assert bool(result.pending_changes) is isinstance(locations, list)
    assert bool(result.issues) is not isinstance(locations, list)


def test_malformed_location_url_does_not_destroy_successful_peer():
    client, _ = client_for(payload(hydrated(locations=[location(url="https://[bad")]), hydrated("W2")), httpx.Response(404))
    with client:
        result = hydrate_retained_openalex_versions(client, (record(), record("W2")), retrieved_at=NOW)
    assert not result.records[0].version_hints
    assert result.records[1].version_hints
    assert result.hydrated_work_ids == ("https://openalex.org/W2",)


def test_empty_retention_does_not_request_or_return_historical_work():
    client, transport = client_for()
    with client:
        result = hydrate_retained_openalex_versions(client, (), version_state=(state(),))
    assert not result.records and not result.pending_changes and not result.reused_work_ids
    assert not transport.requests


def test_inconsistent_duplicate_live_revisions_hydrate_once_without_state_binding():
    client, transport = client_for(payload(hydrated()))
    with client:
        result = hydrate_retained_openalex_versions(client, (record(), record(revision=None)), version_state=(state(),))
    assert len(transport.requests) == 1
    assert len(result.records) == 2
    assert not result.reused_work_ids and not result.pending_changes


def test_versions_do_not_modify_retention_grouping_inputs_or_discovery_coverage():
    from literature_monitor.coverage import CoverageComponent, CoverageStatus, CoverageUnit
    from literature_monitor.canonicalize import canonicalize_records
    live = (record(), record("W2"))
    coverage = CoverageUnit("openalex", CoverageComponent.OPENALEX_DISCOVERY, CoverageStatus.COMPLETE, journal="Biometrics")
    client, _ = client_for(payload(hydrated(), hydrated("W2")))
    with client:
        result = hydrate_retained_openalex_versions(client, live, retrieved_at=NOW)
    assert [r.external_ids.openalex for r in result.records] == [r.external_ids.openalex for r in live]
    assert coverage.status is CoverageStatus.COMPLETE
    assert len(canonicalize_records(tuple(r.to_evidence() for r in live)).papers) == len(canonicalize_records(tuple(r.to_evidence() for r in result.records)).papers)


def test_a4_uses_only_memory_state_and_never_creates_or_reads_db(tmp_path, monkeypatch):
    directory = tmp_path / ".literature-monitor"
    directory.mkdir()
    path = directory / ps.STATE_FILENAME
    path.write_bytes(b"untouched invalid sentinel")
    monkeypatch.chdir(tmp_path)

    def unexpected_access(*args, **kwargs):
        pytest.fail("A4 accessed persistent Provider state")

    for name in ("read_provider_state", "update_provider_state", "replace_invalid_provider_state"):
        monkeypatch.setattr(ps, name, unexpected_access)
    monkeypatch.setattr(sqlite3, "connect", unexpected_access)
    client, _ = client_for(payload(hydrated("W2")))
    with client:
        result = hydrate_retained_openalex_versions(client, (record(), record("W2")), version_state=(state(),), retrieved_at=NOW)
    assert len(result.pending_changes) == 1
    assert path.read_bytes() == b"untouched invalid sentinel"
    assert list(directory.iterdir()) == [path]
    path.unlink()
    client, _ = client_for(payload(hydrated()))
    with client:
        hydrate_retained_openalex_versions(client, (record(),), retrieved_at=NOW)
    assert not list(directory.iterdir())


def test_version_batches_are_bounded_and_do_not_fetch_full_metadata():
    records = tuple(record(f"W{i}") for i in range(101))
    client, transport = client_for(payload(*(hydrated(f"W{i}") for i in range(100))), payload(hydrated("W100")))
    with client:
        result = hydrate_retained_openalex_versions(client, records, retrieved_at=NOW)
    assert len(result.pending_changes) == 101
    assert len(transport.requests) == 2
    assert [len(request.url.params["filter"].split("|")) for request in transport.requests] == [100, 1]
    assert all(request.url.params["select"] == "id,locations" for request in transport.requests)
