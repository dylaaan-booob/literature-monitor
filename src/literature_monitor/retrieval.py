"""Provider-neutral evidence assembly for the multi-source pipeline."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from literature_monitor.crossref import (
    CrossrefClient,
    CrossrefNotFoundError,
    CrossrefRecordError,
    CrossrefRequestError,
    CrossrefWorkRecord,
    EnrichmentIssue,
    EnrichmentIssueSeverity,
    normalize_crossref_work,
)
from literature_monitor.identifiers import normalize_doi
from literature_monitor.models import ProviderRecordRef, ProviderWorkEvidence
from literature_monitor.openalex import OpenAlexWorkRecord
from literature_monitor.progress import (
    ActivityKind,
    ActivityUpdate,
    ProgressCallback,
    ProgressEvent,
)


@dataclass(frozen=True)
class EvidenceRetrievalResult:
    evidence: tuple[ProviderWorkEvidence, ...]
    supplement_records: tuple[CrossrefWorkRecord, ...]
    issues: tuple[EnrichmentIssue, ...]

    @property
    def has_errors(self) -> bool:
        return any(
            issue.severity is EnrichmentIssueSeverity.ERROR
            for issue in self.issues
        )


def _anchor(record: OpenAlexWorkRecord) -> ProviderRecordRef:
    return ProviderRecordRef(
        provider=record.provenance.provider,
        record_id=record.provenance.record_id,
    )


def _report_activity(
    callback: ProgressCallback | None,
    activity: ActivityUpdate,
) -> None:
    if callback is not None:
        callback(ProgressEvent(activity=activity))


def assemble_provider_evidence(
    client: CrossrefClient,
    openalex_records: Sequence[OpenAlexWorkRecord],
    discovered_crossref_records: Sequence[CrossrefWorkRecord],
    *,
    retrieved_at: datetime | None = None,
    progress_callback: ProgressCallback | None = None,
) -> EvidenceRetrievalResult:
    """Attach discovered Crossref evidence and supplement uncovered OA DOIs once."""

    timestamp = retrieved_at or datetime.now(timezone.utc)
    if timestamp.utcoffset() is None:
        raise ValueError("retrieved_at must include a timezone")
    timestamp = timestamp.astimezone(timezone.utc)

    openalex_by_doi: dict[str, list[OpenAlexWorkRecord]] = defaultdict(list)
    issues: list[EnrichmentIssue] = []
    evidence = [record.to_evidence() for record in openalex_records]
    for record in openalex_records:
        doi = normalize_doi(record.external_ids.doi)
        if doi is None:
            issues.append(
                EnrichmentIssue(
                    severity=EnrichmentIssueSeverity.WARNING,
                    stage="missing_doi",
                    record_id=record.provenance.record_id,
                    message="record has no DOI; Crossref lookup was skipped",
                )
            )
            continue
        openalex_by_doi[doi].append(record)

    discovered_dois = {record.doi for record in discovered_crossref_records}
    for record in discovered_crossref_records:
        anchors = tuple(
            _anchor(openalex)
            for openalex in sorted(
                openalex_by_doi.get(record.doi, ()),
                key=lambda item: item.provenance.record_id,
            )
        )
        evidence.append(record.to_evidence(supplements=anchors))

    supplements: list[CrossrefWorkRecord] = []
    pending_dois = tuple(sorted(set(openalex_by_doi) - discovered_dois))
    total_dois = len(pending_dois)
    for doi_index, doi in enumerate(pending_dois):
        matching = sorted(
            openalex_by_doi[doi],
            key=lambda item: item.provenance.record_id,
        )
        activity = ActivityUpdate(
            kind=ActivityKind.WORKING,
            source="crossref",
            operation="doi_supplement",
            label="Looking up Crossref DOI metadata",
            detail=f"DOI {doi}",
            current=doi_index,
            total=total_dois,
            unit="doi",
        )
        _report_activity(progress_callback, activity)
        try:
            if progress_callback is None:
                payload = client.get_work_by_doi(doi)
            else:
                payload = client.get_work_by_doi(
                    doi,
                    progress_callback=progress_callback,
                    activity=activity,
                )
            crossref_record, warnings = normalize_crossref_work(
                payload,
                doi,
                timestamp,
            )
        except CrossrefNotFoundError as error:
            issues.extend(
                EnrichmentIssue(
                    severity=EnrichmentIssueSeverity.WARNING,
                    stage="not_found",
                    record_id=record.provenance.record_id,
                    doi=doi,
                    message=str(error),
                )
                for record in matching
            )
        except CrossrefRequestError as error:
            issues.extend(
                EnrichmentIssue(
                    severity=EnrichmentIssueSeverity.ERROR,
                    stage="request_failure",
                    record_id=record.provenance.record_id,
                    doi=doi,
                    message=str(error),
                )
                for record in matching
            )
        except CrossrefRecordError as error:
            issues.extend(
                EnrichmentIssue(
                    severity=EnrichmentIssueSeverity.ERROR,
                    stage="record_normalization",
                    record_id=record.provenance.record_id,
                    doi=doi,
                    message=str(error),
                )
                for record in matching
            )
        else:
            supplements.append(crossref_record)
            anchors = tuple(_anchor(record) for record in matching)
            evidence.append(crossref_record.to_evidence(supplements=anchors))
            issues.extend(
                EnrichmentIssue(
                    severity=EnrichmentIssueSeverity.WARNING,
                    stage="field_normalization",
                    record_id=matching[0].provenance.record_id,
                    doi=doi,
                    message=warning,
                )
                for warning in warnings
            )
        # 成功、not-found、请求失败与记录错误都完成了一个 DOI work unit。
        _report_activity(
            progress_callback,
            replace(
                activity,
                label="Completed Crossref DOI lookup",
                current=doi_index + 1,
            ),
        )

    return EvidenceRetrievalResult(
        evidence=tuple(evidence),
        supplement_records=tuple(supplements),
        issues=tuple(issues),
    )
