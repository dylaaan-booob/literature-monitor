"""DOI-first candidate attribution and the sole journal-issue exclusion (§49)."""
from collections import defaultdict
from collections.abc import Sequence
import re

from literature_monitor.config import JournalConfig
from literature_monitor.identifiers import normalize_doi
from literature_monitor.models import ProviderWorkEvidence, ProviderRecordRef
from literature_monitor.openalex import DiscoveryResult
from literature_monitor.application.crossref_retrieval import CrossrefManifestResult, CrossrefRetrievalResult, CrossrefSupplementResult


def valid_doi(value: str | None) -> str | None:
    doi = normalize_doi(value)
    return doi if doi and re.fullmatch(r"10\.\d{4,9}/[^\s]+", doi) else None


def openalex_attribution(openalex: DiscoveryResult, journals: Sequence[JournalConfig]) -> dict[str, tuple[str, ...]]:
    configured = {journal.issn_l for journal in journals}
    sources = defaultdict(set)
    for source in openalex.sources:
        if source.configured_issn_l in configured:
            sources[source.openalex_id].add(source.configured_issn_l)
    attribution = defaultdict(set)
    for record in openalex.records:
        doi = valid_doi(record.external_ids.doi)
        if doi and len(sources[record.source_id]) == 1:
            attribution[doi].update(sources[record.source_id])
    return {doi: tuple(sorted(issns)) for doi, issns in attribution.items()}


def filter_candidate_evidence(
    openalex: DiscoveryResult, manifest: CrossrefManifestResult,
    discovery: CrossrefRetrievalResult, supplements: CrossrefSupplementResult,
    journals: Sequence[JournalConfig], excluded: set[str],
) -> tuple[ProviderWorkEvidence, ...]:
    oa_journals = openalex_attribution(openalex, journals)
    aliases = dict(supplements.aliases)
    skipped = dict(supplements.skipped_aliases)
    evidence = []
    anchors = defaultdict(list)
    attribution = defaultdict(set)
    plans = manifest.plans
    manifest_venues = manifest.journal_issns
    for record in openalex.records:
        requested = valid_doi(record.external_ids.doi)
        final = aliases.get(requested, requested)
        if (requested not in oa_journals or requested in skipped
                or requested in supplements.unresolved_dois or final in excluded):
            continue
        attribution[final].update(oa_journals[requested])
        item = record.to_evidence()
        anchors[final].append(ProviderRecordRef(provider="openalex", record_id=record.provenance.record_id))
        evidence.append(item.model_copy(update={
            "external_ids": item.external_ids.model_copy(update={"doi": final}),
            "monitor_journal_issns": oa_journals[requested],
        }))
    records = {record.doi: record for record in (*discovery.discovery.records, *supplements.records)}
    for record in records.values():
        if record.doi in excluded:
            continue
        own = {plan.journal.issn_l for plan in plans if set(record.issns).intersection(plan.query_aliases)}
        known = attribution[record.doi] | set(manifest_venues.get(record.doi, ()))
        if not known:
            continue
        item = record.to_evidence(supplements=tuple(anchors[record.doi]))
        evidence.append(item.model_copy(update={
            "journal": item.journal if own.intersection(known) or not record.issns else None,
            "monitor_journal_issns": tuple(sorted(known)),
        }))
    return tuple(evidence)
