"""Provider-neutral evidence canonicalization and version consolidation."""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from literature_monitor.identifiers import normalize_doi
from literature_monitor.models import (
    Author,
    CanonicalMetadata,
    CanonicalPaper,
    EvidenceDate,
    EvidenceDateKind,
    EvidenceVersionHint,
    EvidenceVersionRole,
    ExternalIds,
    MetadataSource,
    PaperVersion,
    ProviderWorkEvidence,
    VersionKind,
    VersionRef,
)


_VERSION_RELATIONS = {
    "is-preprint-of",
    "has-preprint",
    "is-manuscript-of",
    "has-manuscript",
    "is-version-of",
    "has-version",
    "is-identical-to",
}

_DATE_PRECEDENCE = {
    VersionKind.JOURNAL_FINAL: (
        EvidenceDateKind.PUBLISHED_PRINT,
        EvidenceDateKind.PUBLISHED,
        EvidenceDateKind.ISSUED,
        EvidenceDateKind.PUBLISHED_ONLINE,
    ),
    VersionKind.JOURNAL_ONLINE: (
        EvidenceDateKind.PUBLISHED_ONLINE,
        EvidenceDateKind.PUBLISHED,
        EvidenceDateKind.ISSUED,
        EvidenceDateKind.PUBLISHED_PRINT,
    ),
    VersionKind.ACCEPTED_MANUSCRIPT: (
        EvidenceDateKind.PUBLISHED_ONLINE,
        EvidenceDateKind.PUBLISHED,
        EvidenceDateKind.ISSUED,
        EvidenceDateKind.PUBLISHED_PRINT,
    ),
    VersionKind.PREPRINT: (
        EvidenceDateKind.PUBLISHED_ONLINE,
        EvidenceDateKind.PUBLISHED,
        EvidenceDateKind.ISSUED,
        EvidenceDateKind.PUBLISHED_PRINT,
    ),
    VersionKind.UNKNOWN: (
        EvidenceDateKind.PUBLISHED,
        EvidenceDateKind.ISSUED,
        EvidenceDateKind.PUBLISHED_ONLINE,
        EvidenceDateKind.PUBLISHED_PRINT,
    ),
}

_VERSION_PRIORITY = {
    VersionKind.JOURNAL_FINAL: 4,
    VersionKind.JOURNAL_ONLINE: 3,
    VersionKind.ACCEPTED_MANUSCRIPT: 2,
    VersionKind.PREPRINT: 1,
    VersionKind.UNKNOWN: 0,
}


@dataclass(frozen=True)
class CanonicalizationIssue:
    stage: str
    message: str
    record_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanonicalizationResult:
    papers: tuple[CanonicalPaper, ...]
    issues: tuple[CanonicalizationIssue, ...]


@dataclass(frozen=True)
class EvidenceCluster:
    evidence: tuple[ProviderWorkEvidence, ...]


@dataclass(frozen=True)
class EvidenceConsolidationResult:
    clusters: tuple[EvidenceCluster, ...]
    issues: tuple[CanonicalizationIssue, ...]


@dataclass(frozen=True)
class _BuiltVersion:
    version: PaperVersion
    representative: ProviderWorkEvidence
    base_evidence: tuple[ProviderWorkEvidence, ...]


@dataclass(frozen=True)
class _VersionEvidence:
    record_index: int
    hint: EvidenceVersionHint | None = None


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != item:
            parent = self.parent[item]
            self.parent[item] = root
            item = parent
        return root

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            self.parent[right_root] = left_root
        else:
            self.parent[left_root] = right_root


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _normalize_abstract(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _record_key(record: ProviderWorkEvidence) -> tuple[str, str]:
    return (
        record.provenance.provider.strip().casefold(),
        record.provenance.record_id,
    )


def _provider_label(provider: str) -> str:
    labels = {"openalex": "OpenAlex", "crossref": "Crossref"}
    normalized = provider.strip().casefold()
    return labels.get(normalized, provider.strip())


def _is_complete_date(value: EvidenceDate) -> bool:
    return value.month is not None and value.day is not None


def _completeness(record: ProviderWorkEvidence) -> tuple[int, ...]:
    return (
        int(record.title is not None),
        int(record.journal is not None),
        int(record.abstract is not None),
        int(record.publication_date is not None),
        int(bool(record.author_keywords)),
        sum(author.openalex_id is not None for author in record.authors),
        sum(author.orcid is not None for author in record.authors),
        sum(_is_complete_date(item) for item in record.dates),
        len(record.relations),
        len(record.version_hints),
    )


def _choose_evidence(
    records: Sequence[ProviderWorkEvidence],
) -> ProviderWorkEvidence:
    latest = max(record.provenance.retrieved_at for record in records)
    candidates = [
        record for record in records if record.provenance.retrieved_at == latest
    ]
    completeness = max(_completeness(record) for record in candidates)
    candidates = [
        record for record in candidates if _completeness(record) == completeness
    ]
    return min(candidates, key=lambda record: record.model_dump_json())


def _nonempty_conflicts(
    values: Iterable[str | None],
    normalizer: Callable[[str], str],
) -> bool:
    normalized = {
        normalizer(value)
        for value in values
        if value is not None
    }
    return len(normalized) > 1


def _author_lists_conflict(
    left: Sequence[Author],
    right: Sequence[Author],
) -> bool:
    if not left or not right:
        return False
    if len(left) != len(right):
        return True
    for left_author, right_author in zip(left, right, strict=True):
        if _normalize_text(left_author.name) != _normalize_text(right_author.name):
            return True
        if (
            left_author.openalex_id is not None
            and right_author.openalex_id is not None
            and left_author.openalex_id != right_author.openalex_id
        ):
            return True
        if (
            left_author.orcid is not None
            and right_author.orcid is not None
            and left_author.orcid != right_author.orcid
        ):
            return True
    return False


def _author_evidence_conflicts(
    records: Sequence[ProviderWorkEvidence],
) -> bool:
    return any(
        _author_lists_conflict(left.authors, right.authors)
        for left_index, left in enumerate(records)
        for right in records[left_index + 1 :]
    )


def _keyword_signature(keywords: Sequence[str]) -> tuple[str, ...]:
    return tuple(_normalize_text(keyword) for keyword in keywords)


def _external_identifiers(record: ProviderWorkEvidence) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    for namespace, value in record.external_ids.model_dump(
        exclude_none=True
    ).items():
        if not isinstance(value, str):
            continue
        normalized_namespace = namespace.strip().casefold()
        if normalized_namespace == "doi":
            normalized_value = normalize_doi(value)
            if normalized_value is not None:
                identifiers[normalized_namespace] = normalized_value
        else:
            identifiers[normalized_namespace] = value.strip()
    return identifiers


def _identifier_values(
    records: Sequence[ProviderWorkEvidence],
    *,
    exclude: frozenset[str] = frozenset(),
) -> dict[str, set[str]]:
    values: dict[str, set[str]] = defaultdict(set)
    for record in records:
        for namespace, identifier in _external_identifiers(record).items():
            if namespace not in exclude:
                values[namespace].add(identifier)
    return values


def _snapshot_issues(
    records: Sequence[ProviderWorkEvidence],
) -> list[CanonicalizationIssue]:
    current = [
        record
        for record in records
        if record.provenance.retrieved_at
        == max(item.provenance.retrieved_at for item in records)
    ]
    provider = _provider_label(current[0].provenance.provider)
    record_id = current[0].provenance.record_id
    issues: list[CanonicalizationIssue] = []

    for field in ("title", "journal", "abstract"):
        values = [getattr(record, field) for record in current]
        normalizer = _normalize_abstract if field == "abstract" else _normalize_text
        if _nonempty_conflicts(values, normalizer):
            issues.append(
                CanonicalizationIssue(
                    stage="metadata_conflict",
                    record_ids=(record_id,),
                    message=(
                        f"equally recent snapshots have conflicting "
                        f"{provider} {field}"
                    ),
                )
            )
    if _author_evidence_conflicts(current):
        issues.append(
            CanonicalizationIssue(
                stage="metadata_conflict",
                record_ids=(record_id,),
                message=f"equally recent {provider} snapshots conflict on authors",
            )
        )
    publication_dates = {
        record.publication_date
        for record in current
        if record.publication_date is not None
    }
    if len(publication_dates) > 1:
        issues.append(
            CanonicalizationIssue(
                stage="date_conflict",
                record_ids=(record_id,),
                message=(
                    f"equally recent {provider} snapshots conflict on "
                    "publication_date"
                ),
            )
        )
    keyword_sets = {
        _keyword_signature(record.author_keywords)
        for record in current
        if record.author_keywords
    }
    if len(keyword_sets) > 1:
        issues.append(
            CanonicalizationIssue(
                stage="metadata_conflict",
                record_ids=(record_id,),
                message=(
                    f"equally recent {provider} snapshots conflict on "
                    "author_keywords"
                ),
            )
        )
    for namespace, values in _identifier_values(current).items():
        if len(values) > 1:
            issues.append(
                CanonicalizationIssue(
                    stage="identifier_conflict",
                    record_ids=(record_id,),
                    message=(
                        f"equally recent {provider} snapshots conflict on "
                        f"{namespace} identifier"
                    ),
                )
            )

    date_values: dict[
        EvidenceDateKind, set[tuple[int, int | None, int | None]]
    ] = defaultdict(set)
    for record in current:
        for item in record.dates:
            if _is_complete_date(item):
                date_values[item.kind].add((item.year, item.month, item.day))
    for date_kind, values in date_values.items():
        if len(values) > 1:
            issues.append(
                CanonicalizationIssue(
                    stage="date_conflict",
                    record_ids=(record_id,),
                    message=(
                        f"equally recent {provider} snapshots conflict on "
                        f"{date_kind.value} date"
                    ),
                )
            )
    return issues


def _normalize_retrievals(
    records: Sequence[ProviderWorkEvidence],
) -> tuple[list[ProviderWorkEvidence], list[CanonicalizationIssue]]:
    grouped: dict[tuple[str, str], list[ProviderWorkEvidence]] = defaultdict(list)
    for record in records:
        grouped[_record_key(record)].append(record)

    normalized: list[ProviderWorkEvidence] = []
    issues: list[CanonicalizationIssue] = []
    for key in sorted(grouped):
        snapshots = grouped[key]
        issues.extend(_snapshot_issues(snapshots))
        selected = _choose_evidence(snapshots)
        supplements = {
            (reference.provider.strip().casefold(), reference.record_id): reference
            for snapshot in snapshots
            for reference in snapshot.supplements
        }
        normalized.append(
            selected.model_copy(
                update={
                    "supplements": tuple(
                        supplements[reference_key]
                        for reference_key in sorted(supplements)
                    )
                }
            )
        )
    return normalized, issues


def _relation_target_key(
    id_type: str,
    identifier: str,
) -> tuple[str, str] | None:
    namespace = id_type.strip().casefold()
    if namespace == "doi":
        value = normalize_doi(identifier)
        return (namespace, value) if value is not None else None
    value = identifier.strip()
    return (namespace, value) if namespace and value else None


def _authors_compatible(left: Sequence[Author], right: Sequence[Author]) -> bool:
    if not left or not right or len(left) != len(right):
        return False
    for left_author, right_author in zip(left, right, strict=True):
        if (
            left_author.openalex_id is not None
            and right_author.openalex_id is not None
            and left_author.openalex_id != right_author.openalex_id
        ):
            return False
        if (
            left_author.orcid is not None
            and right_author.orcid is not None
            and left_author.orcid != right_author.orcid
        ):
            return False
        shared_stable_id = (
            left_author.openalex_id is not None
            and left_author.openalex_id == right_author.openalex_id
        ) or (
            left_author.orcid is not None
            and left_author.orcid == right_author.orcid
        )
        if shared_stable_id:
            continue
        has_any_stable_id = any(
            (
                left_author.openalex_id,
                left_author.orcid,
                right_author.openalex_id,
                right_author.orcid,
            )
        )
        if has_any_stable_id or _normalize_text(left_author.name) != _normalize_text(
            right_author.name
        ):
            return False
    return True


def _component_dois(
    union_find: _UnionFind,
    records: Sequence[ProviderWorkEvidence],
    root: int,
) -> set[str]:
    return {
        doi
        for index, record in enumerate(records)
        if union_find.find(index) == root
        if (doi := _external_identifiers(record).get("doi")) is not None
    }


def _own_version_key(record: ProviderWorkEvidence) -> tuple[str, str]:
    identifiers = _external_identifiers(record)
    if (arxiv := identifiers.get("arxiv")) is not None:
        return "arxiv", arxiv
    if (doi := identifiers.get("doi")) is not None:
        return "doi", doi
    return _record_key(record)


def _version_keys(
    record: ProviderWorkEvidence,
    anchors: dict[tuple[str, str], ProviderWorkEvidence],
) -> tuple[tuple[str, str], ...]:
    keys: set[tuple[str, str]] = set()
    for reference in record.supplements:
        anchor_key = (
            reference.provider.strip().casefold(),
            reference.record_id,
        )
        anchor = anchors.get(anchor_key)
        if anchor is not None:
            keys.add(_own_version_key(anchor))
    if not keys:
        keys.add(_own_version_key(record))
    return tuple(sorted(keys))


def _group_records(
    records: Sequence[ProviderWorkEvidence],
    issues: list[CanonicalizationIssue],
) -> tuple[list[tuple[int, ...]], dict[int, set[str]]]:
    union_find = _UnionFind(len(records))
    roles: dict[int, set[str]] = defaultdict(set)

    dois: dict[str, list[int]] = defaultdict(list)
    identifier_index: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        identifiers = _external_identifiers(record)
        for namespace, value in identifiers.items():
            identifier_index[(namespace, value)].append(index)
        if (doi := identifiers.get("doi")) is not None:
            dois[doi].append(index)
    for indexes in dois.values():
        for index in indexes[1:]:
            union_find.union(indexes[0], index)

    for subject_index, record in enumerate(records):
        for relation in record.relations:
            relation_type = relation.relation_type.strip().casefold()
            if relation_type not in _VERSION_RELATIONS:
                continue
            if relation_type == "is-preprint-of":
                roles[subject_index].add("preprint")
            elif relation_type == "is-manuscript-of":
                roles[subject_index].add("manuscript")

            target_key = _relation_target_key(
                relation.id_type,
                relation.identifier,
            )
            if target_key is None:
                continue
            for target_index in identifier_index.get(target_key, ()):
                union_find.union(subject_index, target_index)
                if relation_type == "is-preprint-of":
                    roles[target_index].add("publication")
                elif relation_type == "has-preprint":
                    roles[target_index].add("preprint")
                elif relation_type == "is-manuscript-of":
                    roles[target_index].add("publication")
                elif relation_type == "has-manuscript":
                    roles[target_index].add("manuscript")

    title_groups: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        if record.title is not None and record.authors:
            title_groups[_normalize_text(record.title)].append(index)
    for indexes in title_groups.values():
        for left_position, left_index in enumerate(indexes):
            for right_index in indexes[left_position + 1 :]:
                left_root = union_find.find(left_index)
                right_root = union_find.find(right_index)
                if left_root == right_root:
                    continue
                record_ids = tuple(
                    sorted(
                        (
                            records[left_index].provenance.record_id,
                            records[right_index].provenance.record_id,
                        )
                    )
                )
                if not _authors_compatible(
                    records[left_index].authors,
                    records[right_index].authors,
                ):
                    issues.append(
                        CanonicalizationIssue(
                            stage="blocked_match",
                            record_ids=record_ids,
                            message=(
                                "matching normalized titles lack compatible "
                                "author evidence"
                            ),
                        )
                    )
                    continue
                left_dois = _component_dois(union_find, records, left_root)
                right_dois = _component_dois(union_find, records, right_root)
                if left_dois and right_dois and left_dois != right_dois:
                    issues.append(
                        CanonicalizationIssue(
                            stage="identifier_conflict",
                            record_ids=record_ids,
                            message=(
                                "compatible title and authors were not merged "
                                "across conflicting DOI components"
                            ),
                        )
                    )
                    continue
                union_find.union(left_root, right_root)

    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(records)):
        components[union_find.find(index)].append(index)

    anchors = {_record_key(record): record for record in records}

    def component_key(
        indexes: Sequence[int],
    ) -> tuple[tuple[str, str, str, str], ...]:
        return tuple(
            sorted(
                (
                    *version_key,
                    records[index].provenance.provider,
                    records[index].provenance.record_id,
                )
                for index in indexes
                for version_key in _version_keys(records[index], anchors)
            )
        )

    ordered = [
        tuple(sorted(indexes, key=lambda index: _record_key(records[index])))
        for indexes in components.values()
    ]
    ordered.sort(key=component_key)
    return ordered, roles


def _hint_version_key(hint: EvidenceVersionHint) -> tuple[str, str]:
    source = hint.source.strip().casefold()
    if source == "doi":
        doi = normalize_doi(hint.identifier)
        if doi is None:
            raise ValueError("DOI version hint is empty")
        return source, doi
    return source, hint.identifier.strip()


def _canonical_eligible(record: ProviderWorkEvidence) -> bool:
    return (
        record.title is not None
        and record.journal is not None
        and bool(record.authors)
    )


def _representative(
    records: Sequence[ProviderWorkEvidence],
) -> ProviderWorkEvidence:
    eligible = [record for record in records if _canonical_eligible(record)]
    candidates = eligible or list(records)
    completeness = max(_completeness(record) for record in candidates)
    candidates = [
        record for record in candidates if _completeness(record) == completeness
    ]
    return min(candidates, key=_record_key)


def _version_evidence_issues(
    key: tuple[str, str],
    records: Sequence[ProviderWorkEvidence],
) -> list[CanonicalizationIssue]:
    issues: list[CanonicalizationIssue] = []
    record_ids = tuple(
        sorted({record.provenance.record_id for record in records})
    )
    by_provider: dict[str, list[ProviderWorkEvidence]] = defaultdict(list)
    for record in records:
        by_provider[record.provenance.provider.strip().casefold()].append(record)

    for provider_key, provider_records in sorted(by_provider.items()):
        provider = _provider_label(provider_records[0].provenance.provider)
        for field in ("title", "journal", "abstract"):
            values = [getattr(record, field) for record in provider_records]
            normalizer = (
                _normalize_abstract if field == "abstract" else _normalize_text
            )
            if _nonempty_conflicts(values, normalizer):
                issues.append(
                    CanonicalizationIssue(
                        stage="metadata_conflict",
                        record_ids=record_ids,
                        message=(
                            f"version {key[0]}:{key[1]} has conflicting "
                            f"{provider} {field}"
                        ),
                    )
                )
        if _author_evidence_conflicts(provider_records):
            issues.append(
                CanonicalizationIssue(
                    stage="metadata_conflict",
                    record_ids=record_ids,
                    message=(
                        f"version {key[0]}:{key[1]} has conflicting "
                        f"{provider} authors"
                    ),
                )
            )
        publication_dates = {
            record.publication_date
            for record in provider_records
            if record.publication_date is not None
        }
        if len(publication_dates) > 1:
            issues.append(
                CanonicalizationIssue(
                    stage="date_conflict",
                    record_ids=record_ids,
                    message=(
                        f"version {key[0]}:{key[1]} has conflicting "
                        f"{provider} publication_date"
                    ),
                )
            )
        keyword_sets = {
            _keyword_signature(record.author_keywords)
            for record in provider_records
            if record.author_keywords
        }
        if len(keyword_sets) > 1:
            issues.append(
                CanonicalizationIssue(
                    stage="metadata_conflict",
                    record_ids=record_ids,
                    message=(
                        f"version {key[0]}:{key[1]} has conflicting "
                        f"{provider} author_keywords"
                    ),
                )
            )
        for namespace, values in _identifier_values(
            provider_records,
            exclude=frozenset({provider_key}),
        ).items():
            if len(values) > 1:
                issues.append(
                    CanonicalizationIssue(
                        stage="identifier_conflict",
                        record_ids=record_ids,
                        message=(
                            f"version {key[0]}:{key[1]} has conflicting "
                            f"{provider} {namespace} identifiers"
                        ),
                    )
                )
    return issues


def _version_kind(
    key: tuple[str, str],
    records: Sequence[ProviderWorkEvidence],
    roles: dict[int, set[str]],
    indexes: Sequence[int],
    hints: Sequence[EvidenceVersionHint],
    issues: list[CanonicalizationIssue],
) -> VersionKind:
    evidence = set().union(*(roles.get(index, set()) for index in indexes))
    evidence.update(hint.role.value for hint in hints)
    if key[0] == "arxiv":
        evidence.add(EvidenceVersionRole.PREPRINT.value)
    explicit_roles = evidence & {"preprint", "manuscript", "publication"}
    if len(explicit_roles) > 1:
        issues.append(
            CanonicalizationIssue(
                stage="version_role_conflict",
                record_ids=tuple(
                    sorted(record.provenance.record_id for record in records)
                ),
                message=(
                    f"version {key[0]}:{key[1]} has conflicting explicit roles: "
                    f"{', '.join(sorted(explicit_roles))}"
                ),
            )
        )
        return VersionKind.UNKNOWN
    if "preprint" in evidence:
        return VersionKind.PREPRINT
    if "manuscript" in evidence:
        return VersionKind.ACCEPTED_MANUSCRIPT

    date_kinds = {item.kind for record in records for item in record.dates}
    if EvidenceDateKind.PUBLISHED_PRINT in date_kinds:
        return VersionKind.JOURNAL_FINAL
    if EvidenceDateKind.PUBLISHED_ONLINE in date_kinds:
        return VersionKind.JOURNAL_ONLINE
    return VersionKind.JOURNAL_FINAL


def _resolved_version_date(
    kind: VersionKind,
    records: Sequence[ProviderWorkEvidence],
    representative: ProviderWorkEvidence,
    issues: list[CanonicalizationIssue],
) -> date | None:
    if not records:
        return None
    by_kind: dict[EvidenceDateKind, set[date]] = defaultdict(set)
    for record in records:
        for item in record.dates:
            if _is_complete_date(item):
                by_kind[item.kind].add(
                    date(item.year, item.month or 1, item.day or 1)
                )

    selected_by_kind: dict[EvidenceDateKind, date] = {}
    record_ids = tuple(
        sorted({record.provenance.record_id for record in records})
    )
    for date_kind, values in by_kind.items():
        selected = min(values)
        selected_by_kind[date_kind] = selected
        if len(values) > 1:
            rendered = ", ".join(value.isoformat() for value in sorted(values))
            issues.append(
                CanonicalizationIssue(
                    stage="date_conflict",
                    record_ids=record_ids,
                    message=(
                        f"{date_kind.value} has multiple complete dates "
                        f"({rendered}); selected {selected.isoformat()}"
                    ),
                )
            )
    for date_kind in _DATE_PRECEDENCE[kind]:
        if date_kind in selected_by_kind:
            return selected_by_kind[date_kind]
    return representative.publication_date


def _build_versions(
    component: Sequence[int],
    records: Sequence[ProviderWorkEvidence],
    roles: dict[int, set[str]],
    issues: list[CanonicalizationIssue],
) -> list[_BuiltVersion]:
    anchors = {_record_key(record): record for record in records}
    grouped: dict[tuple[str, str], list[_VersionEvidence]] = defaultdict(list)
    for index in component:
        for key in _version_keys(records[index], anchors):
            grouped[key].append(_VersionEvidence(index))
        for hint in records[index].version_hints:
            grouped[_hint_version_key(hint)].append(
                _VersionEvidence(index, hint)
            )

    built: list[_BuiltVersion] = []
    for key in sorted(grouped):
        evidence = grouped[key]
        indexes = sorted({item.record_index for item in evidence})
        base_indexes = sorted(
            {
                item.record_index
                for item in evidence
                if item.hint is None
            }
        )
        hints = tuple(item.hint for item in evidence if item.hint is not None)
        version_records = [records[index] for index in indexes]
        base_records = tuple(records[index] for index in base_indexes)
        issues.extend(_version_evidence_issues(key, version_records))
        representative = _representative(version_records)
        kind = _version_kind(
            key,
            version_records,
            roles,
            base_indexes,
            hints,
            issues,
        )
        version_date = _resolved_version_date(
            kind,
            base_records,
            representative,
            issues,
        )
        hint_urls = sorted(
            {hint.url for hint in hints if hint.url is not None}
        )
        built.append(
            _BuiltVersion(
                version=PaperVersion(
                    kind=kind,
                    source=key[0],
                    identifier=key[1],
                    url=hint_urls[0] if hint_urls else None,
                    date=version_date,
                ),
                representative=representative,
                base_evidence=base_records,
            )
        )
    return built


def _preferred_version(versions: Sequence[_BuiltVersion]) -> _BuiltVersion:
    return min(
        versions,
        key=lambda built: (
            -_VERSION_PRIORITY[built.version.kind],
            built.version.date is None,
            -(built.version.date.toordinal() if built.version.date else 0),
            built.version.source,
            built.version.identifier,
        ),
    )


def _metadata_issues(
    representative: ProviderWorkEvidence,
    records: Sequence[ProviderWorkEvidence],
) -> list[CanonicalizationIssue]:
    issues: list[CanonicalizationIssue] = []
    left_provider = _provider_label(representative.provenance.provider)
    left_provider_key = representative.provenance.provider.strip().casefold()
    for record in records:
        if (
            record is representative
            or record.provenance.provider.strip().casefold() == left_provider_key
        ):
            continue
        right_provider = _provider_label(record.provenance.provider)
        comparisons = (
            ("title", representative.title, record.title, _normalize_text),
            ("journal", representative.journal, record.journal, _normalize_text),
            (
                "abstract",
                representative.abstract,
                record.abstract,
                _normalize_abstract,
            ),
        )
        if _author_lists_conflict(representative.authors, record.authors):
            issues.append(
                CanonicalizationIssue(
                    stage="metadata_conflict",
                    record_ids=tuple(
                        sorted(
                            {
                                representative.provenance.record_id,
                                record.provenance.record_id,
                            }
                        )
                    ),
                    message=(
                        f"{left_provider} and {right_provider} conflict on "
                        f"nonempty authors; kept {left_provider}"
                    ),
                )
            )
        for field, left_value, right_value, normalizer in comparisons:
            if (
                left_value is not None
                and right_value is not None
                and normalizer(left_value) != normalizer(right_value)
            ):
                issues.append(
                    CanonicalizationIssue(
                        stage="metadata_conflict",
                        record_ids=(representative.provenance.record_id,),
                        message=(
                            f"{left_provider} and {right_provider} conflict on "
                            f"nonempty {field}; kept {left_provider}"
                        ),
                    )
                )
    return issues


def _deduplicate_sources(
    records: Iterable[ProviderWorkEvidence],
) -> tuple[MetadataSource, ...]:
    sources: dict[tuple[str, str], MetadataSource] = {}
    for record in records:
        source = record.provenance
        key = (source.provider, source.record_id)
        existing = sources.get(key)
        if existing is None or source.retrieved_at > existing.retrieved_at:
            sources[key] = source
    return tuple(sources[key] for key in sorted(sources))


def _fallback_abstract(
    representative: ProviderWorkEvidence,
    records: Sequence[ProviderWorkEvidence],
) -> str | None:
    if representative.abstract is not None:
        return representative.abstract
    candidates = [record for record in records if record.abstract is not None]
    if not candidates:
        return None
    return _choose_evidence(candidates).abstract


def _merged_external_ids(
    representative: ProviderWorkEvidence,
    records: Sequence[ProviderWorkEvidence],
) -> ExternalIds:
    values = representative.external_ids.model_dump(exclude_none=False)
    for record in sorted(records, key=_record_key):
        for namespace, identifier in record.external_ids.model_dump(
            exclude_none=True
        ).items():
            if values.get(namespace) is None:
                values[namespace] = identifier
    return ExternalIds.model_validate(values)


def _merged_authors(
    representative: ProviderWorkEvidence,
    records: Sequence[ProviderWorkEvidence],
) -> tuple[Author, ...]:
    authors = list(representative.authors)
    for record in sorted(records, key=_record_key):
        if not record.authors or _author_lists_conflict(authors, record.authors):
            continue
        authors = [
            Author(
                name=current.name,
                openalex_id=current.openalex_id or additional.openalex_id,
                orcid=current.orcid or additional.orcid,
            )
            for current, additional in zip(authors, record.authors, strict=True)
        ]
    return tuple(authors)


def _build_paper(
    component: Sequence[int],
    records: Sequence[ProviderWorkEvidence],
    roles: dict[int, set[str]],
    issues: list[CanonicalizationIssue],
) -> CanonicalPaper | None:
    built_versions = _build_versions(component, records, roles, issues)
    eligible_versions = [
        built
        for built in built_versions
        if _canonical_eligible(built.representative)
    ]
    if not eligible_versions:
        issues.append(
            CanonicalizationIssue(
                stage="insufficient_metadata",
                record_ids=tuple(
                    sorted(
                        {
                            records[index].provenance.record_id
                            for index in component
                        }
                    )
                ),
                message=(
                    "provider evidence lacks title, journal, or author data "
                    "required for a canonical paper"
                ),
            )
        )
        return None

    preferred = _preferred_version(eligible_versions)
    representative = preferred.representative
    for built in built_versions:
        issues.extend(_metadata_issues(built.representative, built.base_evidence))
    metadata = CanonicalMetadata(
        title=representative.title,
        journal=representative.journal,
        publication_date=preferred.version.date,
        abstract=_fallback_abstract(
            representative,
            preferred.base_evidence,
        ),
        author_keywords=representative.author_keywords,
    )

    return CanonicalPaper(
        metadata=metadata,
        external_ids=_merged_external_ids(
            representative,
            preferred.base_evidence,
        ),
        authors=_merged_authors(representative, preferred.base_evidence),
        versions=tuple(built.version for built in built_versions),
        sources=_deduplicate_sources(records[index] for index in component),
        preferred_version=VersionRef(
            source=preferred.version.source,
            identifier=preferred.version.identifier,
        ),
    )


def canonicalize_records(
    records: Sequence[ProviderWorkEvidence],
) -> CanonicalizationResult:
    """Consolidate provider-neutral evidence into canonical papers."""

    normalized, components, roles, issues = _consolidation_parts(records)
    papers = tuple(
        paper
        for component in components
        if (paper := _build_paper(component, normalized, roles, issues)) is not None
    )
    unique_issues = sorted(
        set(issues),
        key=lambda issue: (issue.stage, issue.record_ids, issue.message),
    )
    return CanonicalizationResult(papers=papers, issues=tuple(unique_issues))


def _consolidation_parts(
    records: Sequence[ProviderWorkEvidence],
) -> tuple[
    list[ProviderWorkEvidence],
    list[tuple[int, ...]],
    dict[int, set[str]],
    list[CanonicalizationIssue],
]:
    normalized, issues = _normalize_retrievals(records)
    components, roles = _group_records(normalized, issues)
    return normalized, components, roles, issues


def consolidate_evidence(
    records: Sequence[ProviderWorkEvidence],
) -> EvidenceConsolidationResult:
    """Normalize snapshots and group provider evidence by conservative identity."""

    normalized, components, _roles, issues = _consolidation_parts(records)
    clusters = tuple(
        EvidenceCluster(
            evidence=tuple(normalized[index] for index in component)
        )
        for component in components
    )
    unique_issues = sorted(
        set(issues),
        key=lambda issue: (issue.stage, issue.record_ids, issue.message),
    )
    return EvidenceConsolidationResult(
        clusters=clusters,
        issues=tuple(unique_issues),
    )
