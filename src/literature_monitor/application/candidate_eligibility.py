"""Transient candidate scope decisions, after supplementation and before assembly.

See SPEC.md §§32.2–32.3. Acquired Provider results remain untouched.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

from literature_monitor.application.crossref_retrieval import CrossrefSupplementResult
from literature_monitor.config import JournalConfig
from literature_monitor.coverage import CoverageStatus
from literature_monitor.crossref import CrossrefDiscoveryResult, CrossrefWorkRecord, CrossrefJournalQuery, plan_crossref_queries
from literature_monitor.diagnostics import RunDiagnostic, RunDiagnosticKind
from literature_monitor.identifiers import normalize_doi
from literature_monitor.models import ProviderRecordRef, ProviderWorkEvidence
from literature_monitor.openalex import DiscoveryResult, OpenAlexWorkRecord, ResolvedSource


class CandidateEligibility(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    SCOPE_DISPUTED = "SCOPE_DISPUTED"


@dataclass(frozen=True)
class EligibilityDecision:
    state: CandidateEligibility
    strong: bool = False


@dataclass(frozen=True)
class TargetVenue:
    configured_issn_l: str
    query_aliases: tuple[str, ...]
    source_id: str | None = None
    name: str = ""  # Diagnostic presentation only.
    excluded_aliases: tuple[str, ...] = ()


def target_venue(plan: CrossrefJournalQuery, sources: Sequence[ResolvedSource]) -> TargetVenue:
    journal = plan.journal
    resolved = tuple(source for source in sources if source.configured_issn_l == journal.issn_l)
    return TargetVenue(journal.issn_l, plan.query_aliases,
                       resolved[0].openalex_id if len(resolved) == 1 else None, journal.name,
                       plan.excluded_aliases)


def _conflicting_alias_only(record: CrossrefWorkRecord, target: TargetVenue) -> bool:
    return bool(set(record.issns).intersection(target.excluded_aliases)) and not bool(
        set(record.issns).intersection(target.query_aliases)
    )


def classify_crossref(record: CrossrefWorkRecord, target: TargetVenue) -> EligibilityDecision:
    """Classify exact type/venue evidence without OpenAlex type or name vetoes."""
    state = CandidateEligibility
    kind = record.work_type
    target_issn = bool(set(record.issns) & set(target.query_aliases))
    if kind == "journal-issue":
        return EligibilityDecision(state.INELIGIBLE)
    if kind is None or kind == "other":
        return EligibilityDecision(state.SCOPE_DISPUTED)
    if kind == "journal-article":
        if target_issn:
            return EligibilityDecision(state.ELIGIBLE, strong=True)
        if record.issns:
            return EligibilityDecision(state.SCOPE_DISPUTED)
        return EligibilityDecision(state.SCOPE_DISPUTED)
    return EligibilityDecision(state.SCOPE_DISPUTED if target_issn else state.INELIGIBLE)


def classify_publication(
    is_published: bool | None, *, has_doi: bool,
    supplement_status: CoverageStatus | None = None,
) -> EligibilityDecision:
    """Authoritative absence and request failure differ when the flag is false."""
    state = CandidateEligibility
    if has_doi and supplement_status not in (CoverageStatus.UNAVAILABLE, CoverageStatus.FAILED):
        return EligibilityDecision(state.SCOPE_DISPUTED)
    if is_published is True:
        return EligibilityDecision(state.ELIGIBLE)
    if is_published is False and (not has_doi or supplement_status is CoverageStatus.UNAVAILABLE):
        return EligibilityDecision(state.INELIGIBLE)
    return EligibilityDecision(state.SCOPE_DISPUTED)


def _ref(record: OpenAlexWorkRecord | CrossrefWorkRecord | ProviderWorkEvidence) -> ProviderRecordRef:
    return ProviderRecordRef(provider=record.provenance.provider, record_id=record.provenance.record_id)


@dataclass(frozen=True)
class ClusterEligibility:
    has_eligible: bool
    has_disputed: bool
    has_strong_eligible: bool


@dataclass(frozen=True)
class EligibleCandidates:
    openalex_records: tuple[OpenAlexWorkRecord, ...]
    crossref_records: tuple[CrossrefWorkRecord, ...]
    supplement_evidence: tuple[ProviderWorkEvidence, ...]
    attribution: Mapping[ProviderRecordRef, tuple[EligibilityDecision, ...]]
    diagnostics: tuple[RunDiagnostic, ...] = ()
    monitor_journal_issns: Mapping[ProviderRecordRef, tuple[str, ...]] = field(default_factory=dict)

    def cluster_eligibility(self, evidence: Sequence[ProviderWorkEvidence]) -> ClusterEligibility:
        decisions = tuple(decision for record in evidence for decision in self.attribution[_ref(record)])
        return ClusterEligibility(
            any(decision.state is CandidateEligibility.ELIGIBLE for decision in decisions),
            any(decision.state is CandidateEligibility.SCOPE_DISPUTED for decision in decisions),
            any(decision.strong for decision in decisions),
        )


def scope_dispute_diagnostic(evidence: Sequence[ProviderWorkEvidence]) -> RunDiagnostic:
    return RunDiagnostic(
        RunDiagnosticKind.SCOPE_DISPUTE,
        "candidate scope remains disputed without an eligibility warning",
        tuple(sorted({record.provenance.record_id for record in evidence})),
    )


def filter_candidate_evidence(
    openalex: DiscoveryResult, crossref: CrossrefDiscoveryResult,
    supplements: CrossrefSupplementResult, journals: Sequence[JournalConfig],
) -> EligibleCandidates:
    """Filter candidates per current venue/anchor while preserving retrieval results."""
    plans = plan_crossref_queries(journals, openalex.sources)
    targets = {plan.journal.issn_l: target_venue(plan, openalex.sources) for plan in plans}
    source_targets: dict[str, list[TargetVenue]] = {}
    for source in openalex.sources:
        if source.configured_issn_l in targets:
            venues = source_targets.setdefault(source.openalex_id, [])
            if targets[source.configured_issn_l] not in venues:
                venues.append(targets[source.configured_issn_l])
    discovered = {record.doi: record for record in crossref.records}
    by_anchor = {(unit.requested_doi, ref): unit for unit in supplements.units for ref in unit.anchors}
    attribution: dict[ProviderRecordRef, list[EligibilityDecision]] = {}
    monitor_journal_issns: dict[ProviderRecordRef, set[str]] = {}
    exclusions: dict[tuple[str | None, str, str], set[str]] = {}

    def attribute_venue(ref: ProviderRecordRef, journal: str | None) -> None:
        identities = monitor_journal_issns.setdefault(ref, set())
        if journal is not None:
            identities.add(targets[journal].configured_issn_l)

    def retain(
        ref: ProviderRecordRef, decision: EligibilityDecision, journal: str | None,
        *, doi: str | None = None, related_record_ids: tuple[str, ...] = (),
    ) -> bool:
        if decision.state is CandidateEligibility.INELIGIBLE:
            normalized_doi = normalize_doi(doi)
            key = ((journal, "doi", normalized_doi) if normalized_doi is not None
                   else (journal, ref.provider, ref.record_id))
            exclusions.setdefault(key, set()).update((ref.record_id, *related_record_ids))
            return False
        attribution.setdefault(ref, []).append(decision)
        attribute_venue(ref, journal)
        return True

    retained_crossref: dict[str, CrossrefWorkRecord] = {}
    seen_discovery: set[tuple[str, ProviderRecordRef]] = set()
    for unit in crossref.units:
        target = targets[unit.journal.issn_l]
        for record in unit.records:
            ref = _ref(record)
            key = (target.configured_issn_l, ref)
            if key in seen_discovery:
                continue  # Multiple queried ISSNs are one logical venue decision.
            seen_discovery.add(key)
            ambiguous = target.source_id is not None and len(source_targets.get(target.source_id, ())) > 1
            decision = (EligibilityDecision(CandidateEligibility.SCOPE_DISPUTED) if ambiguous
                        else classify_crossref(record, target))
            identity = None if ambiguous or _conflicting_alias_only(record, target) else target.configured_issn_l
            if retain(ref, decision, identity, doi=record.doi):
                retained_crossref.setdefault(record.doi, record)
    # Missing current venue context cannot establish eligibility or exclusion.
    seen_refs = {ref for _journal, ref in seen_discovery}
    for record in crossref.records:
        if _ref(record) not in seen_refs:
            retain(_ref(record), EligibilityDecision(CandidateEligibility.SCOPE_DISPUTED), None)
            retained_crossref.setdefault(record.doi, record)

    retained_openalex = []
    prime_records: dict[str, CrossrefWorkRecord] = {}
    prime_anchors: dict[str, list[ProviderRecordRef]] = {}
    for record in openalex.records:
        ref, doi = _ref(record), record.external_ids.doi
        unit = by_anchor.get((doi, ref))
        exact = discovered.get(doi) or (unit.record if unit is not None else None)
        venues = source_targets.get(record.source_id, ())
        if len(venues) > 1:
            venues = ()  # One Source shared by multiple configured targets cannot select identity.
        retained = False
        for target in venues:
            decision = (
                classify_crossref(exact, target) if exact is not None else
                classify_publication(record.is_published, has_doi=doi is not None,
                                     supplement_status=unit.coverage.status if unit is not None else None)
            )
            # Only the current requested-DOI/anchor relation authorizes prime grouping.
            if not retain(
                ref, decision, target.configured_issn_l,
                doi=exact.doi if exact is not None else doi,
                related_record_ids=(exact.provenance.record_id,) if exact is not None else (),
            ):
                continue
            retained = True
            if exact is not None and doi not in discovered:
                prime_records[exact.doi] = exact
                anchors = prime_anchors.setdefault(exact.doi, [])
                if ref not in anchors:
                    anchors.append(ref)
                attribution.setdefault(_ref(exact), []).append(decision)
                attribute_venue(_ref(exact), None if _conflicting_alias_only(exact, target) else target.configured_issn_l)
        if not venues:
            retained = retain(ref, EligibilityDecision(CandidateEligibility.SCOPE_DISPUTED), None)
        if retained:
            retained_openalex.append(record)

    return EligibleCandidates(
        tuple(retained_openalex), tuple(retained_crossref.values()),
        tuple(record.to_evidence(supplements=tuple(prime_anchors[doi]))
              for doi, record in prime_records.items()),
        {ref: tuple(decisions) for ref, decisions in attribution.items()},
        tuple(RunDiagnostic(
            RunDiagnosticKind.NON_CANDIDATE_EXCLUSION,
            "evidence excluded from the candidate pipeline", tuple(sorted(record_ids)), targets[journal].name if journal is not None else None,
        ) for (journal, _namespace, _identity), record_ids in exclusions.items()),
        {ref: tuple(sorted(issns)) for ref, issns in monitor_journal_issns.items()},
    )
