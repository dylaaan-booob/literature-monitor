"""Execute the current provider plan with explicit, validated A5 unit reuse."""

from collections.abc import Sequence
from datetime import datetime, timezone

from literature_monitor.application.provider_cache import (
    CachedProviderUnit, CachedCrossrefSupplement, cached_unit_key, provider_cache_key,
)
from literature_monitor.config import JournalConfig
from literature_monitor.crossref import (
    CrossrefClient, CrossrefDiscoveryResult, CrossrefDiscoveryUnitResult,
    discover_crossref_journal_issn,
)
from literature_monitor.date_range import ResolvedDateRange
from literature_monitor.identifiers import normalize_doi
from literature_monitor.openalex import (
    OpenAlexClient, DiscoveryResult, OpenAlexDiscoveryUnitResult, discover_openalex_journal,
)
from literature_monitor.progress import ProgressCallback, ProgressEvent, ProgressStage
from literature_monitor.retrieval import EvidenceRetrievalResult, assemble_provider_evidence


def retrieve_with_reuse(
    openalex_client: OpenAlexClient,
    crossref_client: CrossrefClient,
    journals: Sequence[JournalConfig],
    date_range: ResolvedDateRange,
    cached_units: Sequence[CachedProviderUnit],
    *,
    progress_callback: ProgressCallback | None = None,
) -> tuple[DiscoveryResult, CrossrefDiscoveryResult, EvidenceRetrievalResult, tuple[CachedProviderUnit, ...]]:
    """Current order is authoritative; reused domain units have no live coverage."""

    available = {cached_unit_key(unit): unit for unit in cached_units}
    reused: list[CachedProviderUnit] = []
    oa_units: list[OpenAlexDiscoveryUnitResult] = []
    timestamp = datetime.now(timezone.utc)
    for index, journal in enumerate(journals):
        cached = available.get(provider_cache_key("openalex", "openalex_discovery", journal=journal))
        if cached is not None:
            reused.append(cached)
            oa_units.append(OpenAlexDiscoveryUnitResult(journal, None, cached.source, cached.records, ()))
        else:
            oa_units.append(discover_openalex_journal(
                openalex_client, journal, date_range.from_date, date_range.to_date,
                retrieved_at=timestamp, progress_callback=progress_callback, unit_index=index,
            ))
    openalex = DiscoveryResult(
        sources=tuple(unit.source for unit in oa_units if unit.source is not None),
        records=tuple(record for unit in oa_units for record in unit.records),
        issues=tuple(issue for unit in oa_units for issue in unit.issues),
        coverage=tuple(unit.coverage for unit in oa_units if unit.coverage is not None),
        units=tuple(oa_units),
    )
    cr_units: list[CrossrefDiscoveryUnitResult] = []
    timestamp = datetime.now(timezone.utc)
    for journal_index, journal in enumerate(journals):
        for issn_index, issn in enumerate(journal.issn):
            cached = available.get(provider_cache_key("crossref", "crossref_discovery", journal=journal, issn=issn))
            if cached is not None:
                reused.append(cached)
                cr_units.append(CrossrefDiscoveryUnitResult(journal, issn, None, cached.records, ()))
            else:
                cr_units.append(discover_crossref_journal_issn(
                    crossref_client, journal, issn, date_range.from_date, date_range.to_date,
                    retrieved_at=timestamp, progress_callback=progress_callback,
                    journal_index=journal_index, issn_index=issn_index,
                ))
    crossref = CrossrefDiscoveryResult(
        records=tuple(record for unit in cr_units for record in unit.records),
        issues=tuple(issue for unit in cr_units for issue in unit.issues),
        coverage=tuple(unit.coverage for unit in cr_units if unit.coverage is not None),
        units=tuple(cr_units),
    )
    # Pending DOIs and supplement anchors belong to this invocation, not the cache.
    pending = sorted({doi for record in openalex.records
                      if (doi := normalize_doi(record.external_ids.doi)) is not None}
                     - {record.doi for record in crossref.records})
    supplied = {}
    for doi in pending:
        cached = available.get(provider_cache_key("crossref", "crossref_supplement", doi=doi))
        if isinstance(cached, CachedCrossrefSupplement):
            reused.append(cached)
            supplied[doi] = cached.record
    if progress_callback is not None:
        progress_callback(ProgressEvent(stage=ProgressStage.COMBINING_METADATA))
    retrieval = assemble_provider_evidence(
        crossref_client, openalex.records, crossref.records,
        supplied_supplements=supplied, progress_callback=progress_callback,
    )
    return openalex, crossref, retrieval, tuple(reused)
