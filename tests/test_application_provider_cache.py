from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import literature_monitor.safe_write as safe_write
from literature_monitor.application.provider_cache import (
    CachedCrossrefDiscovery,
    CachedCrossrefSupplement,
    CachedOpenAlexDiscovery,
    ProviderCacheReadStatus,
    ProviderCacheWriteError,
    ProviderResultCache,
    build_provider_cache,
    provider_cache_path,
    read_provider_cache,
    write_provider_cache,
)
from literature_monitor.config import JournalConfig
from literature_monitor.coverage import CoverageComponent, CoverageStatus, CoverageUnit
from literature_monitor.crossref import (
    CrossrefDiscoveryIssue,
    CrossrefDiscoveryResult,
    CrossrefDiscoveryUnitResult,
    EnrichmentIssue,
    EnrichmentIssueSeverity,
    normalize_crossref_work,
)
from literature_monitor.date_range import ResolvedDateRange
from literature_monitor.models import Author, CanonicalMetadata, ExternalIds, MetadataSource
from literature_monitor.openalex import (
    DiscoveryIssue,
    DiscoveryResult,
    IssueSeverity,
    OpenAlexDiscoveryUnitResult,
    OpenAlexWorkRecord,
    OpenAlexVersion,
    OpenAlexVersionHint,
    ResolvedSource,
)
from literature_monitor.retrieval import CrossrefSupplementUnitResult, EvidenceRetrievalResult


def execution_results():
    journal = JournalConfig(name="Biometrics", issn=("0006-341X",))
    source = ResolvedSource(
        journal=journal.name, configured_issns=journal.issn,
        resolved_issns=journal.issn, unresolved_issns=(),
        openalex_id="https://openalex.org/S8265502", display_name="Biometrics",
        issn_l=journal.issn[0], issn=journal.issn,
    )
    timestamp = datetime(2026, 1, 31, tzinfo=timezone.utc)
    oa_records = tuple(OpenAlexWorkRecord(
        metadata=CanonicalMetadata(title=f"Statistics {index}", journal="Biometrics",
                                   publication_date=date(2026, 1, index)),
        external_ids=ExternalIds(openalex=f"https://openalex.org/W{index}", doi=f"10.5555/{index}"),
        authors=(Author(name="Ada Author", orcid="https://orcid.org/0000-0001-0002-0003"),),
        source_id=source.openalex_id,
        provenance=MetadataSource(provider="openalex", record_id=f"https://openalex.org/W{index}",
                                  retrieved_at=timestamp),
        version_hints=(OpenAlexVersionHint(
            source="doi", identifier=f"10.5555/{index}",
            version=OpenAlexVersion.PUBLISHED, url=f"https://doi.org/10.5555/{index}",
        ),),
    ) for index in (2, 1))
    payload = json.loads((Path(__file__).parent / "fixtures/crossref/work_complete.json").read_text())
    payload["message"]["author"] = [{"given": "Ada", "family": "Author", "ORCID": "https://orcid.org/0000-0002-1825-0097"}]
    payload["message"]["ISSN"] = list(journal.issn)
    payload["message"]["relation"] = {
        "is-preprint-of": [{"id-type": "doi", "id": "10.5555/target", "asserted-by": "subject"}],
    }
    cr_record, _ = normalize_crossref_work(payload, payload["message"]["DOI"].lower(), timestamp)
    # Keep rich dates, relations, and provenance in the roundtrip fixture.
    oa_coverage = CoverageUnit("openalex", CoverageComponent.OPENALEX_DISCOVERY,
                               CoverageStatus.COMPLETE, journal=journal.name)
    cr_coverage = CoverageUnit("crossref", CoverageComponent.CROSSREF_DISCOVERY,
                               CoverageStatus.COMPLETE, journal=journal.name, issn=journal.issn[0])
    supplement_coverage = CoverageUnit("crossref", CoverageComponent.CROSSREF_SUPPLEMENT,
                                       CoverageStatus.COMPLETE, doi=cr_record.doi)
    return (
        DiscoveryResult((source,), oa_records, (), (oa_coverage,), (
            OpenAlexDiscoveryUnitResult(journal, oa_coverage, source, oa_records, ()),
        )),
        CrossrefDiscoveryResult((cr_record,), (), (cr_coverage,), (
            CrossrefDiscoveryUnitResult(journal, journal.issn[0], cr_coverage, (cr_record,), ()),
        )),
        EvidenceRetrievalResult((), (cr_record,), (), (supplement_coverage,), (
            CrossrefSupplementUnitResult(cr_record.doi, supplement_coverage, cr_record, ()),
        )),
    )


def sample_cache() -> ProviderResultCache:
    return build_provider_cache(
        ResolvedDateRange(date(2026, 1, 1), date(2026, 1, 31)), *execution_results(),
    )


def test_cache_roundtrip_preserves_rich_domain_models_and_execution_order(tmp_path: Path) -> None:
    cache = sample_cache()
    write_provider_cache(tmp_path, cache)
    result = read_provider_cache(tmp_path)
    assert result.status is ProviderCacheReadStatus.AVAILABLE
    assert result.cache == cache
    assert [type(unit) for unit in result.cache.units] == [
        CachedOpenAlexDiscovery, CachedCrossrefDiscovery, CachedCrossrefSupplement,
    ]
    assert [record.external_ids.openalex for record in result.cache.units[0].records] == [
        "https://openalex.org/W2", "https://openalex.org/W1",
    ]
    raw = json.loads(provider_cache_path(tmp_path).read_text())
    assert set(raw) == {"schema_version", "resolved_date_range", "units"}
    assert set(raw["units"][0]) == {"provider", "component", "journal", "source", "records"}
    assert set(raw["units"][1]) == {"provider", "component", "journal", "issn", "records"}
    assert set(raw["units"][2]) == {"provider", "component", "doi", "record"}
    assert "retrieved_at" in raw["units"][2]["record"]["provenance"]


@pytest.mark.parametrize("component", range(3))
@pytest.mark.parametrize("status,with_issue,reusable", [
    (CoverageStatus.COMPLETE, False, True),
    (CoverageStatus.COMPLETE, True, False),
    (CoverageStatus.PARTIAL, False, False),
    (CoverageStatus.FAILED, False, False),
    (CoverageStatus.UNAVAILABLE, False, False),
])
def test_selection_requires_complete_without_unit_local_issues(component, status, with_issue, reusable):
    results = list(execution_results())
    result = results[component]
    unit = result.units[0]
    issues = ()
    if with_issue:
        issues = ((
            DiscoveryIssue(IssueSeverity.WARNING, "normalization", "Biometrics", "warning"),
            CrossrefDiscoveryIssue(EnrichmentIssueSeverity.WARNING, "normalization", "Biometrics", "0006-341X", "warning"),
            EnrichmentIssue(EnrichmentIssueSeverity.WARNING, "normalization", "warning", doi=unit.coverage.doi),
        )[component],)
    results[component] = replace(result, units=(replace(
        unit, coverage=replace(unit.coverage, status=status), issues=issues,
    ),))
    cache = build_provider_cache(sample_cache().resolved_date_range, *results)
    assert len(cache.units) == (3 if reusable else 2)
    assert any(entry.component == unit.coverage.component.value for entry in cache.units) is reusable


@pytest.mark.parametrize("component", [0, 1])
def test_clean_zero_result_discovery_is_reusable(component):
    results = list(execution_results())
    result = results[component]
    results[component] = replace(result, records=(), units=(replace(result.units[0], records=()),))
    cache = build_provider_cache(sample_cache().resolved_date_range, *results)
    assert len(cache.units) == 3
    assert cache.units[component].records == ()


def test_flat_records_never_infer_cache_membership():
    results = tuple(replace(result, units=()) for result in execution_results())
    assert build_provider_cache(sample_cache().resolved_date_range, *results).units == ()


def test_issue_on_one_unit_does_not_exclude_clean_peer_with_same_identity():
    oa, cr, retrieval = execution_results()
    warning = DiscoveryIssue(IssueSeverity.WARNING, "source_resolution", "Biometrics", "alternate ISSN")
    dirty = replace(oa.units[0], issues=(warning,))
    oa = replace(oa, units=(dirty, oa.units[0]), issues=(warning,))
    cache = build_provider_cache(sample_cache().resolved_date_range, oa, cr, retrieval)
    assert len(cache.units) == 3


@pytest.mark.parametrize("path,value", [
    (("schema_version",), 2),
    (("schema_version",), True),
    (("schema_version",), "1"),
    (("extra",), "unrecognized"),
    (("resolved_date_range", "from_date"), "not a date"),
    (("resolved_date_range", "from_date"), "2026-02-01"),
    (("resolved_date_range", "extra"), 1),
    (("units",), {}),
    (("units", 0, "component"), "wrong"),
    (("units", 0, "provider"), "crossref"),
    (("units", 0, "status"), "PARTIAL"),
    (("units", 0, "issues"), []),
    (("units", 0, "journal", "extra"), "wrong"),
    (("units", 0, "source"), None),
    (("units", 0, "source", "extra"), "wrong"),
    (("units", 0, "source", "openalex_id"), "wrong"),
    (("units", 0, "source", "resolved_issns"), []),
    (("units", 0, "source", "issn"), [13]),
    (("units", 0, "records", 0, "metadata", "title"), None),
    (("units", 0, "records", 0, "metadata", "extra"), 1),
    (("units", 0, "records", 0, "source_id"), "https://openalex.org/S99"),
    (("units", 0, "records", 0, "provenance", "retrieved_at"), "2026-01-31T00:00:00"),
    (("units", 1, "issn"), "1541-0420"),
    (("units", 1, "records", 0, "doi"), "10.5555/UPPER"),
    (("units", 1, "records", 0, "dates", 0, "year"), "2026"),
    (("units", 1, "records", 0, "extra"), 1),
    (("units", 2, "doi"), "https://doi.org/10.5555/other"),
    (("units", 2, "doi"), " 10.5555/other "),
    (("units", 1, "records", 0, "doi"), " 10.5555/other "),
    (("units", 2, "record"), None),
    (("units", 2, "record", "provenance", "provider"), "openalex"),
])
def test_strict_reader_rejects_invalid_schema_and_records_without_rewriting(tmp_path, path, value):
    write_provider_cache(tmp_path, sample_cache())
    cache_path = provider_cache_path(tmp_path)
    payload = json.loads(cache_path.read_text())
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    cache_path.write_text(json.dumps(payload))
    before = cache_path.read_bytes()
    result = read_provider_cache(tmp_path)
    assert result.status is ProviderCacheReadStatus.INVALID
    assert result.cache is None and result.error
    assert cache_path.read_bytes() == before


@pytest.mark.parametrize("path", [
    ("units", 0, "source", "issn"), ("units", 0, "records"),
    ("units", 1, "issn"), ("units", 2, "record"),
])
def test_reader_requires_component_fields(tmp_path, path):
    write_provider_cache(tmp_path, sample_cache())
    cache_path = provider_cache_path(tmp_path)
    payload = json.loads(cache_path.read_text())
    target = payload
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    cache_path.write_text(json.dumps(payload))
    assert read_provider_cache(tmp_path).status is ProviderCacheReadStatus.INVALID


def test_missing_and_corrupt_cache_are_distinct_and_reader_does_not_create_files(tmp_path):
    assert read_provider_cache(tmp_path).status is ProviderCacheReadStatus.NOT_FOUND
    assert tuple(tmp_path.iterdir()) == ()
    cache_path = provider_cache_path(tmp_path)
    cache_path.parent.mkdir()
    assert read_provider_cache(tmp_path).status is ProviderCacheReadStatus.NOT_FOUND
    cache_path.write_bytes(b"{broken json")
    assert read_provider_cache(tmp_path).status is ProviderCacheReadStatus.INVALID
    assert cache_path.read_bytes() == b"{broken json"


@pytest.mark.parametrize("case,error", [
    ("crossref_mismatched_venue", "configured journal"),
    ("openalex_missing_resolved_issn", "clean resolution"),
    ("openalex_unresolved_issn", "clean resolution"),
    ("openalex_reordered_resolved_issns", "clean resolution"),
    ("crossref_before_openalex", "phase order"),
    ("supplement_before_discovery", "phase order"),
    ("interleaved_phases", "phase order"),
])
def test_semantically_invalid_mutated_cache_is_invalid_and_not_rewritten(tmp_path, case, error):
    cache = sample_cache()
    write_provider_cache(tmp_path, cache)
    path = provider_cache_path(tmp_path)
    payload = json.loads(path.read_text())
    units = payload["units"]
    if case == "crossref_mismatched_venue":
        units[1]["records"][0].update(journal="Other Journal", issns=["0028-0836"])
    elif case.startswith("openalex_"):
        source = units[0]["source"]
        units[0]["journal"]["issn"] = ["0006-341X", "1541-0420"]
        source["configured_issns"] = ["0006-341X", "1541-0420"]
        source["issn"] = ["0006-341X", "1541-0420"]
        if case == "openalex_unresolved_issn":
            source["unresolved_issns"] = ["1541-0420"]
        elif case == "openalex_reordered_resolved_issns":
            source["resolved_issns"] = ["1541-0420", "0006-341X"]
    elif case == "crossref_before_openalex":
        payload["units"] = [units[1], units[0]]
    elif case == "supplement_before_discovery":
        payload["units"] = [units[2], units[1]]
    else:
        payload["units"] = [units[0], units[2], units[1]]
    path.write_text(json.dumps(payload))
    before = path.read_bytes()
    result = read_provider_cache(tmp_path)
    assert result.status is ProviderCacheReadStatus.INVALID
    assert result.cache is None and error in result.error
    assert path.read_bytes() == before


@pytest.mark.parametrize("component", range(3))
def test_duplicate_work_unit_identity_is_invalid_in_reader_and_writer(tmp_path, component):
    cache = sample_cache()
    writer_dir = tmp_path / "writer"
    write_provider_cache(writer_dir, cache)
    writer_path = provider_cache_path(writer_dir)
    original = writer_path.read_bytes()
    duplicate_units = (*cache.units[:component + 1], cache.units[component], *cache.units[component + 1:])
    duplicate = cache.model_copy(update={"units": duplicate_units})

    reader_dir = tmp_path / "reader"
    reader_path = provider_cache_path(reader_dir)
    reader_path.parent.mkdir(parents=True)
    reader_path.write_text(duplicate.model_dump_json())
    before = reader_path.read_bytes()
    result = read_provider_cache(reader_dir)
    assert result.status is ProviderCacheReadStatus.INVALID
    assert "duplicate work-unit identity" in result.error
    assert reader_path.read_bytes() == before

    with pytest.raises(ProviderCacheWriteError, match="duplicate work-unit identity"):
        write_provider_cache(writer_dir, duplicate)
    assert writer_path.read_bytes() == original
    assert tuple(writer_path.parent.glob(".provider-cache.json.*.tmp")) == ()
    new_dir = tmp_path / "new"
    with pytest.raises(ProviderCacheWriteError, match="duplicate work-unit identity"):
        write_provider_cache(new_dir, duplicate)
    assert not provider_cache_path(new_dir).exists()


def test_multiple_unique_units_in_monotonic_phases_and_empty_cache_remain_valid(tmp_path):
    raw = sample_cache().model_dump(mode="json")
    oa, cr, supplement = raw["units"]
    second_oa = deepcopy(oa)
    second_oa["journal"] = {"name": "Annals of Statistics", "issn": ["0090-5364"]}
    second_oa["source"].update(
        journal="Annals of Statistics", configured_issns=["0090-5364"],
        resolved_issns=["0090-5364"], issn=["0090-5364"], issn_l="0090-5364",
        display_name="Annals of Statistics", openalex_id="https://openalex.org/S2",
    )
    second_oa["records"] = []
    # One configured Crossref journal can execute several ISSN queries.
    cr["journal"]["issn"] = ["0006-341X", "1541-0420"]
    second_cr = deepcopy(cr)
    second_cr["issn"] = "1541-0420"
    raw["units"] = [oa, second_oa, cr, second_cr, supplement]
    cache = ProviderResultCache.model_validate_json(json.dumps(raw), strict=True)
    write_provider_cache(tmp_path, cache)
    assert read_provider_cache(tmp_path).cache == cache
    assert len(cache.units) == 5
    within_phase_reorder = cache.model_copy(update={"units": (
        cache.units[1], cache.units[0], cache.units[3], cache.units[2], cache.units[4],
    )})
    write_provider_cache(tmp_path, within_phase_reorder)
    assert read_provider_cache(tmp_path).cache == within_phase_reorder
    empty = cache.model_copy(update={"units": ()})
    write_provider_cache(tmp_path, empty)
    assert read_provider_cache(tmp_path).cache == empty


@pytest.mark.parametrize("journal,issns,valid", [
    ("Other Journal", ["0006-341X"], True),
    ("Biometrics", ["1234-5678"], False),
    ("  ＢＩＯＭＥＴＲＩＣＳ  ", [], True),
    ("Other Journal", [], False),
])
def test_cached_crossref_venue_uses_production_issn_priority_and_name_fallback(tmp_path, journal, issns, valid):
    cache = sample_cache()
    write_provider_cache(tmp_path, cache)
    path = provider_cache_path(tmp_path)
    raw = json.loads(path.read_text())
    raw["units"][1]["records"][0].update(journal=journal, issns=issns)
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    result = read_provider_cache(tmp_path)
    assert result.status is (ProviderCacheReadStatus.AVAILABLE if valid else ProviderCacheReadStatus.INVALID)
    assert path.read_bytes() == before


@pytest.mark.parametrize("component", [0, 1])
@pytest.mark.parametrize("issns", [
    ["not-an-issn"], ["0006-3410"], ["0006-341x"], [" 0006-341X "],
    ["0006-341X", "0006-341X"],
])
def test_invalid_configured_journal_issns_reject_reader_and_writer(tmp_path, component, issns):
    cache = sample_cache()
    raw = cache.model_dump(mode="json")
    unit = raw["units"][component]
    unit["journal"]["issn"] = issns
    if component == 0:
        unit["source"].update(configured_issns=issns, resolved_issns=issns, issn=issns, issn_l=issns[0])
    else:
        unit["issn"] = issns[0]
        unit["records"][0]["issns"] = issns
    assert_invalid_reader_and_writer_candidate(tmp_path, cache, raw)


@pytest.mark.parametrize("issn", ["not-an-issn", "0006-3410", "0006-341x", " 0006-341X "])
def test_invalid_crossref_queried_issn_rejects_reader_and_writer(tmp_path, issn):
    cache = sample_cache()
    raw = cache.model_dump(mode="json")
    raw["units"][1]["issn"] = issn
    assert_invalid_reader_and_writer_candidate(tmp_path, cache, raw)


@pytest.mark.parametrize("component", [1, 2])
@pytest.mark.parametrize("field,value", [
    ("issns", ["not-an-issn"]),
    ("issns", ["0006-3410"]),
    ("issns", ["0006-341x"]),
    ("issns", [" 0006-341X "]),
    ("issns", ["0006-341X", "0006-341X"]),
    ("issns", ["1541-0420", "0006-341X"]),
    ("orcid", "not-an-orcid"),
    ("orcid", "https://orcid.org/0000-0002-1825-0090"),
    ("orcid", "0000-0002-1825-0097"),
    ("orcid", "http://orcid.org/0000-0002-1825-0097"),
    ("orcid", " https://orcid.org/0000-0002-1825-0097 "),
])
def test_non_normalized_crossref_record_rejects_reader_and_writer(tmp_path, component, field, value):
    cache = sample_cache()
    raw = cache.model_dump(mode="json")
    record = raw["units"][component]["records"][0] if component == 1 else raw["units"][component]["record"]
    if field == "issns":
        record["issns"] = value
    else:
        record["authors"][0]["orcid"] = value
    assert_invalid_reader_and_writer_candidate(tmp_path, cache, raw)


def assert_invalid_reader_and_writer_candidate(tmp_path, valid_cache, raw):
    path = provider_cache_path(tmp_path / "reader")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    result = read_provider_cache(tmp_path / "reader")
    assert result.status is ProviderCacheReadStatus.INVALID
    assert result.cache is None and result.error
    assert path.read_bytes() == before

    # Construct a candidate without schema validation, as orchestration does;
    # normal durable writes must validate it before creating/replacing a file.
    units = []
    def mutated_record(original, entry):
        return original.model_copy(update={
            "issns": tuple(entry["issns"]),
            "authors": tuple(author.model_copy(update={"orcid": raw_author["orcid"]})
                             for author, raw_author in zip(original.authors, entry["authors"], strict=True)),
        })

    for original, entry in zip(valid_cache.units, raw["units"], strict=True):
        if isinstance(original, CachedOpenAlexDiscovery):
            units.append(original.model_copy(update={
                "journal": original.journal.model_copy(update={"issn": tuple(entry["journal"]["issn"])}),
                "source": replace(original.source,
                                  configured_issns=tuple(entry["source"]["configured_issns"]),
                                  resolved_issns=tuple(entry["source"]["resolved_issns"]),
                                  issn=tuple(entry["source"]["issn"]), issn_l=entry["source"]["issn_l"]),
            }))
        elif isinstance(original, CachedCrossrefDiscovery):
            units.append(original.model_copy(update={
                "journal": original.journal.model_copy(update={"issn": tuple(entry["journal"]["issn"])}),
                "issn": entry["issn"],
                "records": tuple(mutated_record(record, raw_record)
                                 for record, raw_record in zip(original.records, entry["records"], strict=True)),
            }))
        else:
            units.append(original.model_copy(update={"record": mutated_record(original.record, entry["record"])}))
    candidate = ProviderResultCache.model_construct(
        schema_version=1, resolved_date_range=valid_cache.resolved_date_range, units=tuple(units),
    )
    for existing in (False, True):
        output_dir = tmp_path / ("existing" if existing else "new")
        cache_path = provider_cache_path(output_dir)
        previous = None
        if existing:
            write_provider_cache(output_dir, valid_cache)
            previous = cache_path.read_bytes()
        with pytest.raises(ProviderCacheWriteError):
            write_provider_cache(output_dir, candidate)
        assert (cache_path.read_bytes() if cache_path.exists() else None) == previous
        assert tuple(cache_path.parent.glob(".provider-cache.json.*.tmp")) == ()


def test_canonical_crossref_identifiers_and_null_orcid_remain_available(tmp_path):
    raw = sample_cache().model_dump(mode="json")
    for record in (raw["units"][1]["records"][0], raw["units"][2]["record"]):
        record["issns"] = ["0006-341X", "1541-0420"]
        record["authors"].append({"name": "Author without ORCID", "orcid": None, "openalex_id": None})
    cache = ProviderResultCache.model_validate_json(json.dumps(raw), strict=True)
    write_provider_cache(tmp_path, cache)
    assert read_provider_cache(tmp_path).cache == cache


@pytest.mark.parametrize("location", ["directory", "file"])
@pytest.mark.parametrize("kind", ["symlink", "wrong_type"])
def test_filesystem_conflicts_are_preserved(tmp_path, location, kind):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = provider_cache_path(workspace)
    if location == "directory":
        path = path.parent
    else:
        path.parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"untouched")
    if kind == "symlink":
        path.symlink_to(outside if location == "directory" else sentinel)
    elif location == "directory":
        path.write_bytes(b"not a directory")
    else:
        path.mkdir()
    assert read_provider_cache(workspace).status is ProviderCacheReadStatus.INVALID
    with pytest.raises(ProviderCacheWriteError):
        write_provider_cache(workspace, sample_cache())
    assert sentinel.read_bytes() == b"untouched"
    assert path.is_symlink() if kind == "symlink" else path.exists()


def test_creation_and_replacement_publish_only_complete_files(tmp_path, monkeypatch):
    path = provider_cache_path(tmp_path)
    original_link = safe_write.os.link
    original_replace = safe_write.os.replace
    published = []

    def inspect_link(source, destination):
        assert not path.exists()
        parsed = ProviderResultCache.model_validate_json(Path(source).read_text(), strict=True)
        published.append(parsed)
        original_link(source, destination)

    def inspect_replace(source, destination):
        assert read_provider_cache(tmp_path).cache == published[0]
        parsed = ProviderResultCache.model_validate_json(Path(source).read_text(), strict=True)
        published.append(parsed)
        original_replace(source, destination)

    monkeypatch.setattr(safe_write.os, "link", inspect_link)
    monkeypatch.setattr(safe_write.os, "replace", inspect_replace)
    first = sample_cache()
    second = first.model_copy(update={"units": ()})
    write_provider_cache(tmp_path, first)
    write_provider_cache(tmp_path, second)
    assert published == [first, second]
    assert read_provider_cache(tmp_path).cache == second
    assert [item.name for item in path.parent.iterdir()] == ["provider-cache.json"]


@pytest.mark.parametrize("existing", [False, True])
def test_atomic_write_failure_preserves_prior_complete_cache_and_cleans_temp(tmp_path, monkeypatch, existing):
    cache = sample_cache()
    path = provider_cache_path(tmp_path)
    if existing:
        write_provider_cache(tmp_path, cache)
    before = path.read_bytes() if existing else None

    def fail(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(safe_write.os, "replace" if existing else "link", fail)
    with pytest.raises(ProviderCacheWriteError, match="disk failure"):
        write_provider_cache(tmp_path, cache.model_copy(update={"units": ()}))
    assert (path.read_bytes() if path.exists() else None) == before
    assert tuple(path.parent.glob(".provider-cache.json.*.tmp")) == ()
