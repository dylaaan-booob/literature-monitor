"""Crossref execution-local retrieval and pending state (§30.3); no filesystem access."""

from collections.abc import Sequence, Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone

from literature_monitor.application.provider_state import CrossrefRecordState
from literature_monitor.config import JournalConfig
from literature_monitor.coverage import CoverageComponent, CoverageStatus, CoverageUnit
from literature_monitor.crossref import (
    _CROSSREF_MANIFEST_BATCH_SIZE,
    _crossref_work_list,
    _merge_manifest_members,
    CrossrefClient,
    CrossrefDiscoveryIssue,
    CrossrefDiscoveryResult,
    CrossrefDiscoveryUnitResult,
    CrossrefDOIOutcome,
    CrossrefDOIOutcomeKind,
    CrossrefError,
    CrossrefHydration,
    CrossrefManifestMember,
    CrossrefManifestUnit,
    CrossrefJournalQuery,
    CrossrefWorkRecord,
    EnrichmentIssue,
    EnrichmentIssueSeverity,
    crossref_record_matches_journal,
    plan_crossref_queries,
    hydrate_crossref_records,
    normalize_crossref_manifest_member,
    normalize_crossref_work,
    parse_crossref_indexed_at,
    retrieve_crossref_manifests,
    validate_normalized_crossref_record,
)
from literature_monitor.models import ProviderRecordRef, ProviderWorkEvidence
from literature_monitor.openalex import OpenAlexWorkRecord, ResolvedSource
from literature_monitor.progress import ProgressCallback


@dataclass(frozen=True)
class CrossrefManifestResult:
    plans: tuple[CrossrefJournalQuery, ...]
    units: tuple[CrossrefManifestUnit, ...]
    members: tuple[CrossrefManifestMember, ...]

    @property
    def journal_issns(self) -> dict[str, tuple[str, ...]]:
        return {
            member.doi: tuple(plan.journal.issn_l for plan in self.plans
                              if set(member.issns).intersection(plan.query_aliases))
            for member in self.members
        }


@dataclass(frozen=True)
class CrossrefRetrievalResult:
    discovery: CrossrefDiscoveryResult
    reused_dois: tuple[str, ...]
    refreshed_dois: tuple[str, ...]
    new_dois: tuple[str, ...]
    pending_changes: tuple[CrossrefRecordState, ...]


@dataclass(frozen=True)
class CrossrefSupplementUnit:
    requested_doi: str
    anchors: tuple[ProviderRecordRef, ...]
    record: CrossrefWorkRecord | None
    coverage: CoverageUnit
    issues: tuple[EnrichmentIssue, ...]


@dataclass(frozen=True)
class CrossrefSupplementResult:
    units: tuple[CrossrefSupplementUnit, ...]
    records: tuple[CrossrefWorkRecord, ...]
    reused_dois: tuple[str, ...]
    refreshed_dois: tuple[str, ...]
    new_dois: tuple[str, ...]
    pending_changes: tuple[CrossrefRecordState, ...]
    skipped_aliases: tuple[tuple[str, str], ...] = ()
    aliases: tuple[tuple[str, str], ...] = ()
    unresolved_dois: tuple[str, ...] = ()

    @property
    def coverage(self) -> tuple[CoverageUnit, ...]:
        return tuple(unit.coverage for unit in self.units)

    @property
    def issues(self) -> tuple[EnrichmentIssue, ...]:
        return tuple(issue for unit in self.units for issue in unit.issues)

    @property
    def evidence(self) -> tuple[ProviderWorkEvidence, ...]:
        anchors: dict[str, list[ProviderRecordRef]] = {}
        for unit in self.units:
            if unit.record is not None:
                refs = anchors.setdefault(unit.record.doi, [])
                refs.extend(ref for ref in unit.anchors if ref not in refs)
        return tuple(record.to_evidence(supplements=tuple(anchors[record.doi])) for record in self.records)


class CrossrefRetrieval:
    """One Provider execution, sharing DOI results across discovery and supplements.

    The caller owns the pooled client and supplies already validated in-memory state.
    A6 owns production invocation and persistence; this object returns pending rows.
    """

    def __init__(
        self, client: CrossrefClient, *, record_state: Sequence[CrossrefRecordState] = (),
        retrieved_at: datetime | None = None, progress_callback: ProgressCallback | None = None,
    ) -> None:
        self.client = client
        self.state = {row.doi: row for row in record_state}
        if len(self.state) != len(record_state):
            raise ValueError("duplicate Crossref state DOI")
        self.timestamp = retrieved_at or datetime.now(timezone.utc)
        if self.timestamp.utcoffset() is None:
            raise ValueError("retrieved_at must include a timezone")
        self.timestamp = self.timestamp.astimezone(timezone.utc)
        self.progress_callback = progress_callback
        self._resolved: dict[str, CrossrefHydration] = {}
        self._revisions: dict[str, datetime | None] = {}
        self._classification: dict[str, str] = {}
        self._pending: dict[str, CrossrefRecordState] = {}
        self._used: dict[str, None] = {}
        self._singletons: dict[str, CrossrefDOIOutcome | CrossrefError] = {}
        self._discovered_dois: set[str] = set()
        self._types: dict[str, CrossrefManifestMember] = {}

    def _observe_types(self, members: Sequence[CrossrefManifestMember]) -> None:
        merged, _ = _merge_manifest_members((*self._types.values(), *members))
        self._types = {member.doi: member for member in merged}

    @property
    def excluded_dois(self) -> set[str]:
        return {doi for doi, member in self._types.items()
                if member.work_type == "journal-issue" and not member.type_conflict}

    @property
    def metadata_counts(self) -> tuple[int, int]:
        # A later skip/exclusion does not erase a metadata attempt already performed.
        results = tuple(self._resolved.values())
        return sum(value.record is not None for value in results), sum(value.record is None for value in results)


    def _usage(self, records: Sequence[CrossrefWorkRecord]):
        dois = tuple(dict.fromkeys(record.doi for record in records))
        self._used.update(dict.fromkeys(dois))
        return (*(tuple(doi for doi in dois if self._classification.get(doi) == kind)
                  for kind in ("reused", "refreshed", "new")),
                tuple(self._pending[doi] for doi in dois if doi in self._pending))

    @property
    def pending_changes(self) -> tuple[CrossrefRecordState, ...]:
        """Deduplicated rows actually used across this execution's retrieval calls."""
        return tuple(self._pending[doi] for doi in self._used if doi in self._pending)

    def _resolve(
        self, members: Sequence[CrossrefManifestMember],
        supplied: dict[str, CrossrefHydration] | None = None,
    ) -> None:
        live, _ = _merge_manifest_members(members)
        supplied = supplied or {}
        requested = []
        for member in live:
            doi, revision = member.doi, parse_crossref_indexed_at(member.indexed_at)
            if doi in self._resolved:
                current = self._resolved[doi]
                replacement = supplied.get(doi)
                if (replacement is not None and replacement.record is not None
                        and (current.record is None or self._revisions[doi] != revision)):
                    # Current full evidence supersedes a failed attempt or older observation.
                    self._resolved[doi] = replacement
                    self._classification.pop(doi, None)
                    self._pending.pop(doi, None)
                elif self._revisions[doi] != revision:
                    # A second live observation is ambiguous; never reuse an earlier revision.
                    self._resolved[doi] = CrossrefHydration(doi, None, ("live indexed revision changed within execution",))
                    self._classification.pop(doi, None)
                    self._pending.pop(doi, None)
                continue
            self._revisions[doi] = revision
            state = self.state.get(doi)
            if state is not None and revision is not None and revision == parse_crossref_indexed_at(state.indexed_at):
                self._resolved[doi] = CrossrefHydration(doi, state.record)
                self._classification[doi] = "reused"
            elif doi in supplied:
                self._resolved[doi] = supplied[doi]
            else:
                requested.append(doi)
        for result in hydrate_crossref_records(self.client, requested, retrieved_at=self.timestamp,
                                               progress_callback=self.progress_callback):
            self._resolved[result.doi] = result
        for member in live:
            doi = member.doi
            result = self._resolved[doi]
            if result.record is None or doi in self._classification:
                continue
            # Full hydration can observe a revision newer than its manifest/probe.
            self._revisions[doi] = parse_crossref_indexed_at(result.record.indexed_at)
            self._classification[doi] = "refreshed" if doi in self.state else "new"
            if result.record.indexed_at is not None:
                try:
                    self._pending[doi] = CrossrefRecordState.from_record(result.record)
                except ValueError as error:
                    self._resolved[doi] = CrossrefHydration(doi, result.record,
                                                          (*result.issues, f"record cannot be persisted: {error}"))

    def discover_manifest(
        self, journals: Sequence[JournalConfig], from_date: date, to_date: date, *,
        resolved_sources: Sequence[ResolvedSource] = (),
    ) -> CrossrefManifestResult:
        plans = plan_crossref_queries(journals, resolved_sources)
        ordered = tuple(dict.fromkeys(issn for plan in plans for issn in plan.query_aliases))
        units = retrieve_crossref_manifests(self.client, ordered, from_date, to_date,
                                           progress_callback=self.progress_callback)
        members, _ = _merge_manifest_members(tuple(member for unit in units for member in unit.members))
        self._observe_types(members)
        return CrossrefManifestResult(plans, units, members)

    def hydrate_manifest(
        self, manifest: CrossrefManifestResult, dois: Sequence[str],
    ) -> CrossrefRetrievalResult:
        selected = set(dois) - self.excluded_dois
        self._resolve(tuple(member for member in manifest.members if member.doi in selected))
        # A failed Full Hydration is still a discovered DOI; do not repeat it as a supplement.
        self._discovered_dois.update(doi for doi, journals in manifest.journal_issns.items() if journals)
        by_issn = {unit.issn: unit for unit in manifest.units}
        units, flat = [], {}
        for plan in manifest.plans:
            for issn in plan.query_aliases:
                raw = by_issn[issn]
                severity = EnrichmentIssueSeverity.WARNING if raw.members else EnrichmentIssueSeverity.ERROR
                issues = [CrossrefDiscoveryIssue(severity, "manifest", plan.journal.name, issn, message)
                          for message in raw.issues]
                records = []
                for member in raw.members:
                    if member.doi not in selected:
                        continue
                    result = self._resolved[member.doi]
                    record = result.record
                    self._observe_types((CrossrefManifestMember(member.doi, member.issns,
                        member.indexed_at, record.work_type if record else None),))
                    for message in result.issues:
                        issues.append(CrossrefDiscoveryIssue(
                            EnrichmentIssueSeverity.WARNING if record else EnrichmentIssueSeverity.ERROR,
                            "full_hydration", plan.journal.name, issn, message, doi=member.doi))
                    if record is not None:
                        if record.issns and not crossref_record_matches_journal(record, plan.journal, query_aliases=plan.query_aliases):
                            issues.append(CrossrefDiscoveryIssue(EnrichmentIssueSeverity.WARNING,
                                "venue_validation", plan.journal.name, issn,
                                "full record lacks usable verified venue identifiers", doi=member.doi))
                        records.append(record)
                        flat.setdefault(record.doi, record)
                status = CoverageStatus.COMPLETE if raw.complete else (
                    CoverageStatus.PARTIAL if raw.members else CoverageStatus.FAILED)
                coverage = CoverageUnit("crossref", CoverageComponent.CROSSREF_DISCOVERY, status,
                                        journal=plan.journal.issn_l, issn=issn)
                units.append(CrossrefDiscoveryUnitResult(plan.journal, issn, coverage, tuple(records), tuple(issues)))
        discovery = CrossrefDiscoveryResult(tuple(flat.values()),
            (*tuple(issue for plan in manifest.plans for issue in plan.diagnostics),
             *tuple(issue for unit in units for issue in unit.issues)),
            tuple(unit.coverage for unit in units), tuple(units))
        return CrossrefRetrievalResult(discovery, *self._usage(discovery.records))

    def _probe(self, dois: Sequence[str]):
        members, diagnostics = {}, {}
        ordered = tuple(dict.fromkeys(dois))
        members.update({doi: replace(self._types[doi], indexed_at=self._revisions[doi])
                        for doi in ordered if doi in self._resolved})
        ordered = tuple(doi for doi in ordered if doi not in members)
        for offset in range(0, len(ordered), _CROSSREF_MANIFEST_BATCH_SIZE):
            group = ordered[offset:offset + _CROSSREF_MANIFEST_BATCH_SIZE]
            try:
                items, total = _crossref_work_list(self.client.get_doi_batch(group, thin=True,
                                                            progress_callback=self.progress_callback))
                found = []
                for item in items:
                    try:
                        member, warnings = normalize_crossref_manifest_member(item)
                    except CrossrefError as error:
                        for doi in group:
                            diagnostics.setdefault(doi, []).append(str(error))
                        continue
                    if member.doi in group:
                        found.append(member)
                        diagnostics.setdefault(member.doi, []).extend(warnings)
                merged, conflicts = _merge_manifest_members(found)
                members.update((member.doi, member) for member in merged)
                if conflicts or len(merged) != total:
                    for doi in group:
                        diagnostics.setdefault(doi, []).extend((*conflicts, "DOI probe count does not reconcile"))
            except CrossrefError as error:
                for doi in group:
                    diagnostics.setdefault(doi, []).append(str(error))
        return members, diagnostics

    def _singleton(self, doi):
        if doi not in self._singletons:
            try:
                self._singletons[doi] = self.client.get_doi_outcome(doi, progress_callback=self.progress_callback)
            except CrossrefError as error:
                self._singletons[doi] = error
        return self._singletons[doi]

    def _singleton_record(self, doi, outcome):
        record, warnings = normalize_crossref_work(outcome.payload, doi, self.timestamp)
        validate_normalized_crossref_record(record)
        return CrossrefManifestMember(doi, record.issns, record.indexed_at, record.work_type), CrossrefHydration(doi, record, warnings)

    def supplement(self, openalex_records: Sequence[OpenAlexWorkRecord], *,
                   should_process: Callable[[str], bool] = lambda doi: True,
                   hydrate: bool = True) -> CrossrefSupplementResult:
        """Requested DOI identities and anchors come solely from current live OpenAlex records."""
        anchors: dict[str, list[ProviderRecordRef]] = {}
        for record in openalex_records:
            if record.provenance.provider != "openalex":
                raise ValueError("supplement requires an OpenAlex evidence anchor")
            doi = record.external_ids.doi
            if doi is not None and doi not in self._discovered_dois:
                refs = anchors.setdefault(doi, [])
                ref = ProviderRecordRef(provider="openalex", record_id=record.provenance.record_id)
                if ref not in refs:
                    refs.append(ref)
        probes, diagnostics = self._probe(tuple(anchors))
        self._observe_types(tuple(probes.values()))
        targets, supplied, unavailable = {}, {}, set()
        aliases = {}
        for doi in anchors:
            if doi in probes:
                targets[doi] = doi
                continue
            outcome = self._singleton(doi)
            try:
                if isinstance(outcome, CrossrefError):
                    raise outcome
                if outcome.kind is CrossrefDOIOutcomeKind.NOT_FOUND:
                    unavailable.add(doi)
                elif outcome.kind is CrossrefDOIOutcomeKind.PRIME_REDIRECT:
                    aliases[doi] = outcome.prime_doi
                    targets[doi] = outcome.prime_doi
                else:
                    probes[doi], supplied[doi] = self._singleton_record(doi, outcome)
                    targets[doi] = doi
            except (CrossrefError, ValueError) as error:
                diagnostics.setdefault(doi, []).append(str(error))
        skipped = tuple((requested, target) for requested, target in targets.items()
                        if not should_process(target))
        for prime in aliases.values():
            if prime in self._resolved:
                # Prefer the actual hydrated revision when it is newer than the Manifest.
                probes[prime] = replace(self._types[prime], indexed_at=self._revisions[prime])
            elif prime in self._discovered_dois:
                probes[prime] = self._types[prime]
        prime_ids = tuple(dict.fromkeys(prime for prime in aliases.values()
                                      if prime not in probes and prime not in self.excluded_dois and should_process(prime)))
        prime_probes, prime_diagnostics = self._probe(prime_ids)
        probes.update(prime_probes)
        self._observe_types(tuple(prime_probes.values()))
        for prime in prime_ids:
            if prime in probes:
                continue
            outcome = self._singleton(prime)
            try:
                if isinstance(outcome, CrossrefError):
                    raise outcome
                if outcome.kind is not CrossrefDOIOutcomeKind.RECORD:
                    raise ValueError("alias prime DOI lacks current record/revision evidence")
                probes[prime], supplied[prime] = self._singleton_record(prime, outcome)
            except (CrossrefError, ValueError) as error:
                prime_diagnostics.setdefault(prime, []).append(str(error))
        self._observe_types(tuple(probes.values()))
        self._resolve(tuple(probes[target] for target in dict.fromkeys(targets.values())
                            if target in probes and should_process(target) and target not in self.excluded_dois
                            and (hydrate or target in supplied or target in self._resolved)), supplied)
        units, flat, unresolved = [], {}, []
        for doi, refs in anchors.items():
            target = targets.get(doi)
            if (doi, target) in skipped or target in self.excluded_dois:
                continue
            result = self._resolved.get(target) if target in probes else None
            record = result.record if result else None
            messages = [*diagnostics.get(doi, ()), *prime_diagnostics.get(target, ())]
            if result:
                messages.extend(result.issues)
            if record is not None:
                flat.setdefault(record.doi, record)
                status = CoverageStatus.COMPLETE
            elif not hydrate and target in probes:
                status = CoverageStatus.COMPLETE
            elif doi in unavailable:
                status = CoverageStatus.UNAVAILABLE
            else:
                status = CoverageStatus.FAILED
                if target not in probes:
                    unresolved.append(doi)
                if not messages:
                    messages.append("required current Crossref supplement evidence is unavailable")
            severity = EnrichmentIssueSeverity.ERROR if status is CoverageStatus.FAILED else EnrichmentIssueSeverity.WARNING
            issues = tuple(EnrichmentIssue(severity, "doi_supplement", message, doi=doi)
                           for message in messages)
            units.append(CrossrefSupplementUnit(doi, tuple(refs), record,
                CoverageUnit("crossref", CoverageComponent.CROSSREF_SUPPLEMENT, status, doi=doi), issues))
        records = tuple(flat.values())
        for record in records:
            self._observe_types((CrossrefManifestMember(record.doi, record.issns,
                                                        record.indexed_at, record.work_type),))
        return CrossrefSupplementResult(tuple(units), records, *self._usage(records), skipped, tuple(aliases.items()), tuple(unresolved))
