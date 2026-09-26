"""Production-path reuse checks with real normalizers and counted fake APIs."""

from dataclasses import replace
from datetime import date, datetime, timezone, timedelta

import pytest

from literature_monitor.application import monitor
from literature_monitor.application.monitor import RunOutcome, run_monitor, _run_canonical_core
from literature_monitor.application.provider_cache import (
    read_provider_cache, write_provider_cache, provider_cache_path, cached_unit_key,
)
from literature_monitor.application.run_state import read_last_run_snapshot
from literature_monitor.config import JournalConfig
from literature_monitor.coverage import CoverageComponent, reporting_identity
from literature_monitor.crossref import CrossrefRequestError, discover_crossref_journals
from literature_monitor.openalex import discover_journals
from literature_monitor.date_range import DateRangeSpec
from literature_monitor.retrieval import assemble_provider_evidence
from test_e2e import source_payload, work_payload, crossref_payload, crossref_list_payload


JOURNALS = (
    JournalConfig(name="Biometrics", issn=("0006-341X", "1541-0420")),
    JournalConfig(name="Annals of Statistics", issn=("0090-5364",)),
)


class APIs:
    def __init__(self):
        self.calls = []
        self.fail_issn = None
        self.empty = False
        self.current_work_id = "W1"

    def get_source_by_issn(self, issn, **kwargs):
        self.calls.append(("source", issn))
        journal = next(j for j in JOURNALS if issn in j.issn)
        return source_payload("S1" if journal == JOURNALS[0] else "S2", journal.name, journal.issn[0], list(journal.issn))

    def iter_work_pages(self, source, *args, **kwargs):
        self.calls.append(("works", source))
        records = [] if self.empty or source.endswith("S2") else [
            work_payload(self.current_work_id, "10.5555/pending", "Statistics pending", "2026-01-15",
                         "S1", "Biometrics", "A1", "Current Author")
        ]
        yield {"results": records}

    def iter_journal_work_pages(self, issn, *args, **kwargs):
        self.calls.append(("journal", issn))
        if issn == self.fail_issn:
            raise CrossrefRequestError("isolated failure")
        messages = []
        if not self.empty and issn == "0006-341X":
            message = crossref_payload("10.5555/discovered", "Statistics discovery", "Biometrics", (2026, 1, 16))["message"]
            message["ISSN"] = ["0006-341X"]
            message["author"] = [{"given": "Current", "family": "Author"}]
            messages.append(message)
        yield crossref_list_payload(messages)

    def get_work_by_doi(self, doi, **kwargs):
        self.calls.append(("doi", doi))
        return crossref_payload(doi, "Statistics pending", "Biometrics", (2026, 1, 15))


@pytest.fixture
def invocation(tmp_path, monkeypatch):
    config = tmp_path / "monitor.yaml"
    (tmp_path / "list.md").write_text(
        "## Journals\n\n| Journal | ISSN/EISSN |\n|---|---|\n"
        "| Biometrics | 0006-341X / 1541-0420 |\n| Annals of Statistics | 0090-5364 |\n"
    )
    config.write_text("keyword_expression: statistics\noutput_dir: workspace\nfrom_date: 2026-01-01\nto_date: 2026-01-31\n")
    apis = APIs()
    monkeypatch.setattr(monitor, "OpenAlexClient", lambda **kwargs: apis)
    monkeypatch.setattr(monitor, "CrossrefClient", lambda **kwargs: apis)
    return config, tmp_path / "workspace", apis


def test_all_hits_repeated_reuse_then_default_live_preserves_workflow(invocation, monkeypatch):
    config, output, apis = invocation
    first = run_monitor(config)
    assert first.outcome is RunOutcome.COMPLETED, first.warnings
    assert len(apis.calls) == 9
    original = read_provider_cache(output).cache
    assert len(original.units) == 6
    workflow = {path: path.read_bytes() for path in output.rglob("*.md")}
    config_before = config.read_bytes()
    for _ in range(2):
        apis.calls.clear()
        events = []
        result = run_monitor(config, reuse_provider_cache=True, progress_callback=events.append)
        assert apis.calls == []
        assert result.coverage == ()
        assert [summary.reused_units for summary in result.reuse_summary] == [2, 3, 1]
        assert result.statistics.openalex_records == first.statistics.openalex_records == 1
        assert result.statistics.crossref_discovery_records == first.statistics.crossref_discovery_records == 1
        assert result.statistics.crossref_supplement_records == first.statistics.crossref_supplement_records == 1
        assert result.outcome is RunOutcome.COMPLETED
        assert read_provider_cache(output).cache == original
        snapshot = read_last_run_snapshot(output).snapshot
        assert snapshot.schema_version == 2 and snapshot.reused_units == result.reused_units
        assert snapshot.coverage == ()
        assert not any(event.activity and event.activity.source in {"openalex", "crossref"} for event in events)
        assert {path: path.read_bytes() for path in workflow} == workflow
        assert config.read_bytes() == config_before
    def forbidden(*args):
        raise AssertionError("default/diagnostic execution must not read provider cache")
    monkeypatch.setattr(monitor, "read_provider_cache", forbidden)
    run_monitor(config)
    assert len(apis.calls) == 9
    apis.calls.clear()
    _run_canonical_core(config)
    assert len(apis.calls) == 9


@pytest.mark.parametrize("fallback", ["missing", "invalid", "range_start", "range_end", "subset", "overlap"])
def test_explicit_fallbacks_are_live_with_only_invalid_warning(invocation, fallback):
    config, output, apis = invocation
    if fallback != "missing":
        run_monitor(config)
        cache = read_provider_cache(output).cache
        if fallback == "invalid":
            provider_cache_path(output).write_bytes(b"{invalid")
        else:
            ranges = {
                "range_start": (date(2025, 12, 31), date(2026, 1, 31)),
                "range_end": (date(2026, 1, 1), date(2026, 2, 1)),
                "subset": (date(2026, 1, 5), date(2026, 1, 20)),
                "overlap": (date(2026, 1, 15), date(2026, 2, 15)),
            }
            write_provider_cache(output, cache.model_copy(update={"resolved_date_range": replace(cache.resolved_date_range,
                from_date=ranges[fallback][0], to_date=ranges[fallback][1])}))
    apis.calls.clear()
    result = run_monitor(config, reuse_provider_cache=True)
    assert len(apis.calls) == 9 and not result.reused_units
    assert len(result.coverage) == 6
    warnings = [w for w in result.warnings if w.stage == "cache_read"]
    assert len(warnings) == (1 if fallback == "invalid" else 0)
    assert result.outcome is (RunOutcome.COMPLETED_WITH_WARNINGS if fallback == "invalid" else RunOutcome.COMPLETED)
    assert read_last_run_snapshot(output).snapshot.outcome.value == result.outcome.value


@pytest.mark.parametrize("failure", ["config", "date", "journal", "keyword", "backend"])
def test_invalid_preflight_never_reads_cache(invocation, monkeypatch, failure):
    config, output, apis = invocation
    def forbidden(*args):
        raise AssertionError("preflight must precede cache inspection")
    monkeypatch.setattr(monitor, "read_provider_cache", forbidden)
    args = {}
    if failure == "config":
        config.write_text("invalid: true")
    elif failure == "date":
        args["date_override"] = DateRangeSpec(from_date=date(2026, 2, 1), to_date=date(2026, 1, 1))
    elif failure == "journal":
        args["journal_name"] = "Not configured"
    elif failure == "keyword":
        args["keyword_expression"] = "AND"
    else:
        from literature_monitor.search import SearchBackendError
        def fail(*args):
            raise SearchBackendError("unavailable")
        monkeypatch.setattr(monitor, "validate_runtime_keyword", fail)
    result = _run_canonical_core(config, reuse_provider_cache=True, **args)
    assert result.outcome is RunOutcome.INVALID_CONFIGURATION and apis.calls == []
    assert not output.exists()


def test_mixed_reuse_full_crossref_venue_current_anchors_and_rebuild(invocation, monkeypatch):
    config, output, apis = invocation
    run_monitor(config)
    cache = read_provider_cache(output).cache
    # Force live OA Biometrics and live its sibling Crossref ISSN only.
    kept = tuple(unit for unit in cache.units if not (
        unit.component == "openalex_discovery" and unit.journal.name == "Biometrics"
        or unit.component == "crossref_discovery" and unit.issn == "1541-0420"
    ))
    write_provider_cache(output, cache.model_copy(update={"units": kept}))
    apis.current_work_id = "W99"
    original_pages = apis.iter_journal_work_pages
    def sibling(issn, *args, **kwargs):
        if issn == "1541-0420":
            apis.calls.append(("journal", issn))
            # Matches the configured primary ISSN, not the queried sibling.
            message = crossref_payload("10.5555/sibling", "Statistics sibling", "Other name", (2026, 1, 17))["message"]
            message["ISSN"] = ["0006-341X"]
            message["author"] = [{"given": "Current", "family": "Author"}]
            yield crossref_list_payload([message])
        else:
            yield from original_pages(issn, *args, **kwargs)
    monkeypatch.setattr(apis, "iter_journal_work_pages", sibling)
    import literature_monitor.application.provider_reuse as reuse_module
    captured = []
    original_assemble = reuse_module.assemble_provider_evidence
    def capture(*args, **kwargs):
        result = original_assemble(*args, **kwargs)
        captured.extend(result.evidence)
        return result
    monkeypatch.setattr(reuse_module, "assemble_provider_evidence", capture)
    apis.calls.clear()
    result = run_monitor(config, reuse_provider_cache=True)
    assert apis.calls == [("source", "0006-341X"), ("source", "1541-0420"), ("works", "https://openalex.org/S1"), ("journal", "1541-0420")]
    assert result.outcome is RunOutcome.COMPLETED_WITH_WARNINGS
    assert all(warning.component is monitor.MonitorIssueComponent.MATERIALIZATION for warning in result.warnings)
    assert [summary.reused_units for summary in result.reuse_summary] == [1, 2, 1]
    assert len(result.coverage) == 2
    assert (result.statistics.openalex_records, result.statistics.crossref_discovery_records,
            result.statistics.crossref_supplement_records) == (1, 2, 1)
    assert not ({reporting_identity(unit) for unit in result.coverage} & {reporting_identity(unit) for unit in result.reused_units})
    supplemented = next(e for e in captured if e.provenance.provider == "crossref" and e.external_ids.doi == "10.5555/pending")
    assert [anchor.record_id for anchor in supplemented.supplements] == ["https://openalex.org/W99"]
    rebuilt = read_provider_cache(output).cache
    assert len(rebuilt.units) == 6
    assert [cached_unit_key(unit) for unit in rebuilt.units] == [cached_unit_key(unit) for unit in cache.units]
    assert rebuilt.units[-1] == cache.units[-1]


def test_live_failure_isolated_while_reusing_peer_and_dirty_unit_excluded(invocation):
    config, output, apis = invocation
    run_monitor(config)
    cache = read_provider_cache(output).cache
    kept = tuple(unit for unit in cache.units if not (unit.component == "crossref_discovery" and unit.issn == "1541-0420"))
    write_provider_cache(output, cache.model_copy(update={"units": kept}))
    apis.fail_issn = "1541-0420"
    apis.calls.clear()
    result = run_monitor(config, reuse_provider_cache=True)
    assert apis.calls == [("journal", "1541-0420")]
    assert result.outcome is RunOutcome.COMPLETED_WITH_ERRORS
    assert len(result.reused_units) == 5 and len(result.coverage) == 1
    assert len(read_provider_cache(output).cache.units) == 5


def test_current_order_and_removed_journals_not_historical_cache_order(invocation):
    config, output, apis = invocation
    run_monitor(config)
    whitelist = config.parent / "list.md"
    whitelist.write_text("## Journals\n\n| Journal | ISSN/EISSN |\n|---|---|\n| Annals of Statistics | 0090-5364 |\n| Biometrics | 0006-341X / 1541-0420 |\n")
    apis.calls.clear()
    result = run_monitor(config, reuse_provider_cache=True)
    assert apis.calls == []
    assert [unit.journal for unit in result.reused_units[:2]] == ["Annals of Statistics", "Biometrics"]
    whitelist.write_text("## Journals\n\n| Journal | ISSN/EISSN |\n|---|---|\n| Annals of Statistics | 0090-5364 |\n")
    result = run_monitor(config, reuse_provider_cache=True)
    assert len(result.reused_units) == 2 and apis.calls == []
    assert len(read_provider_cache(output).cache.units) == 2


def test_full_ordered_configured_identity_change_forces_live(invocation):
    config, output, apis = invocation
    run_monitor(config)
    whitelist = config.parent / "list.md"
    whitelist.write_text(whitelist.read_text().replace("0006-341X / 1541-0420", "1541-0420 / 0006-341X"))
    apis.calls.clear()
    result = run_monitor(config, reuse_provider_cache=True)
    assert ("journal", "0006-341X") in apis.calls and ("journal", "1541-0420") in apis.calls
    assert ("works", "https://openalex.org/S1") in apis.calls
    assert all(unit.journal != "Biometrics" for unit in result.reused_units)


def test_aggregate_live_phases_share_normalized_timestamps(monkeypatch):
    apis = APIs()
    def works(source, *args, **kwargs):
        journal = JOURNALS[0] if source.endswith("S1") else JOURNALS[1]
        suffix = source[-1]
        yield {"results": [work_payload(f"W{suffix}", f"10.5555/pending{suffix}", "Statistics",
            "2026-01-15", f"S{suffix}", journal.name, "A1", "Current Author")]}
    def pages(issn, *args, **kwargs):
        journal = next(j for j in JOURNALS if issn in j.issn)
        message = crossref_payload(f"10.5555/discovered-{issn}", "Statistics", journal.name, (2026, 1, 15))["message"]
        message["ISSN"] = [issn]
        yield crossref_list_payload([message])
    monkeypatch.setattr(apis, "iter_work_pages", works)
    monkeypatch.setattr(apis, "iter_journal_work_pages", pages)
    timestamp = datetime(2026, 1, 31, tzinfo=timezone(timedelta(hours=8)))
    args = (JOURNALS, date(2026, 1, 1), date(2026, 1, 31))
    oa = discover_journals(apis, *args, retrieved_at=timestamp)
    cr = discover_crossref_journals(apis, *args, retrieved_at=timestamp)
    retrieval = assemble_provider_evidence(apis, oa.records, cr.records, retrieved_at=timestamp)
    assert (len(oa.records), len(cr.records), len(retrieval.supplement_records)) == (2, 3, 2)
    assert all(record.provenance.retrieved_at == timestamp.astimezone(timezone.utc)
               for record in (*oa.records, *cr.records, *retrieval.supplement_records))


def test_missing_supplement_runs_live_and_is_retained_in_rebuilt_cache(invocation):
    config, output, apis = invocation
    run_monitor(config)
    cache = read_provider_cache(output).cache
    write_provider_cache(output, cache.model_copy(update={"units": cache.units[:-1]}))
    apis.calls.clear()
    result = run_monitor(config, reuse_provider_cache=True)
    assert apis.calls == [("doi", "10.5555/pending")]
    assert len(result.coverage) == 1 and len(result.reused_units) == 5
    assert len(read_provider_cache(output).cache.units) == 6


def test_cached_supplement_no_longer_pending_is_ignored(invocation, monkeypatch):
    config, output, apis = invocation
    run_monitor(config)
    cache = read_provider_cache(output).cache
    write_provider_cache(output, cache.model_copy(update={"units": tuple(
        unit for unit in cache.units if not (unit.component == "crossref_discovery" and unit.issn == "1541-0420")
    )}))
    def discovery(issn, *args, **kwargs):
        apis.calls.append(("journal", issn))
        message = crossref_payload("10.5555/pending", "Statistics pending", "Biometrics", (2026, 1, 15))["message"]
        message["ISSN"] = ["0006-341X"]
        yield crossref_list_payload([message])
    monkeypatch.setattr(apis, "iter_journal_work_pages", discovery)
    apis.calls.clear()
    result = run_monitor(config, reuse_provider_cache=True)
    assert apis.calls == [("journal", "1541-0420")]
    assert result.statistics.crossref_supplement_records == 0
    assert all(unit.component is not CoverageComponent.CROSSREF_SUPPLEMENT for unit in result.reused_units)
    assert all(unit.component != "crossref_supplement" for unit in read_provider_cache(output).cache.units)


def test_reuse_read_is_read_only_until_materialization_then_cache_then_snapshot(invocation, monkeypatch):
    config, output, apis = invocation
    run_monitor(config)
    events = []
    for name, label in (("read_provider_cache", "read"), ("materialize_papers", "materialize"),
                        ("write_provider_cache", "cache"), ("write_last_run_snapshot", "snapshot")):
        original = getattr(monitor, name)
        def capture(*args, _original=original, _label=label, **kwargs):
            events.append(_label)
            return _original(*args, **kwargs)
        monkeypatch.setattr(monitor, name, capture)
    run_monitor(config, reuse_provider_cache=True)
    assert events == ["read", "materialize", "cache", "snapshot"]


def test_invalid_unsafe_cache_can_report_read_and_persistence_independently(invocation):
    config, output, apis = invocation
    path = provider_cache_path(output)
    path.parent.mkdir(parents=True)
    outside = config.parent / "unrelated-cache"
    outside.write_bytes(b"preserve outside bytes")
    path.symlink_to(outside)
    result = run_monitor(config, reuse_provider_cache=True)
    assert len(apis.calls) == 9
    assert [issue.stage for issue in result.warnings] == ["cache_read", "persistence"]
    assert outside.read_bytes() == b"preserve outside bytes" and path.is_symlink()
    assert read_last_run_snapshot(output).snapshot.outcome.value == "COMPLETED_WITH_WARNINGS"
