"""Evidence-based canonicalization and version consolidation for Task 5."""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from literature_monitor.crossref import (
    CrossrefDateKind,
    CrossrefPartialDate,
    CrossrefWorkRecord,
    EnrichedWorkRecord,
)
from literature_monitor.identifiers import normalize_doi
from literature_monitor.models import (
    Author,
    CanonicalMetadata,
    CanonicalPaper,
    ExternalIds,
    MetadataSource,
    PaperVersion,
    VersionKind,
    VersionRef,
)
from literature_monitor.openalex import OpenAlexWorkRecord


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
        CrossrefDateKind.PUBLISHED_PRINT,
        CrossrefDateKind.PUBLISHED,
        CrossrefDateKind.ISSUED,
        CrossrefDateKind.PUBLISHED_ONLINE,
    ),
    VersionKind.JOURNAL_ONLINE: (
        CrossrefDateKind.PUBLISHED_ONLINE,
        CrossrefDateKind.PUBLISHED,
        CrossrefDateKind.ISSUED,
        CrossrefDateKind.PUBLISHED_PRINT,
    ),
    VersionKind.ACCEPTED_MANUSCRIPT: (
        CrossrefDateKind.PUBLISHED_ONLINE,
        CrossrefDateKind.PUBLISHED,
        CrossrefDateKind.ISSUED,
        CrossrefDateKind.PUBLISHED_PRINT,
    ),
    VersionKind.PREPRINT: (
        CrossrefDateKind.PUBLISHED_ONLINE,
        CrossrefDateKind.PUBLISHED,
        CrossrefDateKind.ISSUED,
        CrossrefDateKind.PUBLISHED_PRINT,
    ),
    VersionKind.UNKNOWN: (
        CrossrefDateKind.PUBLISHED,
        CrossrefDateKind.ISSUED,
        CrossrefDateKind.PUBLISHED_ONLINE,
        CrossrefDateKind.PUBLISHED_PRINT,
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
class _Record:
    openalex: OpenAlexWorkRecord
    crossref: CrossrefWorkRecord | None

    @property
    def record_id(self) -> str:
        return self.openalex.provenance.record_id


@dataclass(frozen=True)
class _BuiltVersion:
    version: PaperVersion
    representative: _Record
    crossref: CrossrefWorkRecord | None


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


def _openalex_completeness(record: OpenAlexWorkRecord) -> tuple[int, ...]:
    return (
        int(record.metadata.abstract is not None),
        int(record.metadata.publication_date is not None),
        int(bool(record.metadata.author_keywords)),
        sum(author.openalex_id is not None for author in record.authors),
        sum(author.orcid is not None for author in record.authors),
    )


def _is_complete_date(value: CrossrefPartialDate) -> bool:
    return value.month is not None and value.day is not None


def _crossref_completeness(record: CrossrefWorkRecord) -> tuple[int, ...]:
    return (
        int(record.title is not None),
        int(record.journal is not None),
        int(record.abstract is not None),
        sum(_is_complete_date(item) for item in record.dates),
        len(record.relations),
    )


def _choose_openalex(records: Sequence[OpenAlexWorkRecord]) -> OpenAlexWorkRecord:
    latest = max(record.provenance.retrieved_at for record in records)
    candidates = [record for record in records if record.provenance.retrieved_at == latest]
    completeness = max(_openalex_completeness(record) for record in candidates)
    candidates = [
        record for record in candidates if _openalex_completeness(record) == completeness
    ]
    return min(candidates, key=lambda record: record.model_dump_json())


def _choose_crossref(records: Sequence[CrossrefWorkRecord]) -> CrossrefWorkRecord:
    latest = max(record.provenance.retrieved_at for record in records)
    candidates = [record for record in records if record.provenance.retrieved_at == latest]
    completeness = max(_crossref_completeness(record) for record in candidates)
    candidates = [
        record for record in candidates if _crossref_completeness(record) == completeness
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


def _author_evidence_conflicts(records: Sequence[OpenAlexWorkRecord]) -> bool:
    return any(
        _author_lists_conflict(left.authors, right.authors)
        for left_index, left in enumerate(records)
        for right in records[left_index + 1 :]
    )


def _keyword_signature(keywords: Sequence[str]) -> tuple[str, ...]:
    return tuple(_normalize_text(keyword) for keyword in keywords)


def _identifier_values(
    records: Sequence[OpenAlexWorkRecord],
    *,
    exclude: frozenset[str] = frozenset(),
) -> dict[str, set[str]]:
    values: dict[str, set[str]] = defaultdict(set)
    for record in records:
        for namespace, identifier in record.external_ids.model_dump(
            exclude_none=True
        ).items():
            normalized_namespace = namespace.strip().casefold()
            if normalized_namespace in exclude or not isinstance(identifier, str):
                continue
            if normalized_namespace == "doi":
                normalized_identifier = normalize_doi(identifier)
                if normalized_identifier is None:
                    continue
            else:
                normalized_identifier = identifier.strip()
            values[normalized_namespace].add(normalized_identifier)
    return values


def _snapshot_issues(
    record_id: str,
    openalex_records: Sequence[OpenAlexWorkRecord],
    crossref_records: Sequence[CrossrefWorkRecord],
) -> list[CanonicalizationIssue]:
    issues: list[CanonicalizationIssue] = []
    latest_openalex = max(record.provenance.retrieved_at for record in openalex_records)
    current_openalex = [
        record
        for record in openalex_records
        if record.provenance.retrieved_at == latest_openalex
    ]
    fields = {
        "title": [record.metadata.title for record in current_openalex],
        "journal": [record.metadata.journal for record in current_openalex],
        "abstract": [record.metadata.abstract for record in current_openalex],
    }
    for field, values in fields.items():
        normalizer = _normalize_abstract if field == "abstract" else _normalize_text
        if _nonempty_conflicts(values, normalizer):
            issues.append(
                CanonicalizationIssue(
                    stage="metadata_conflict",
                    record_ids=(record_id,),
                    message=f"equally recent OpenAlex snapshots conflict on {field}",
                )
            )
    if _author_evidence_conflicts(current_openalex):
        issues.append(
            CanonicalizationIssue(
                stage="metadata_conflict",
                record_ids=(record_id,),
                message="equally recent OpenAlex snapshots conflict on authors",
            )
        )
    publication_dates = {
        record.metadata.publication_date
        for record in current_openalex
        if record.metadata.publication_date is not None
    }
    if len(publication_dates) > 1:
        issues.append(
            CanonicalizationIssue(
                stage="date_conflict",
                record_ids=(record_id,),
                message="equally recent OpenAlex snapshots conflict on publication_date",
            )
        )
    keyword_sets = {
        _keyword_signature(record.metadata.author_keywords)
        for record in current_openalex
        if record.metadata.author_keywords
    }
    if len(keyword_sets) > 1:
        issues.append(
            CanonicalizationIssue(
                stage="metadata_conflict",
                record_ids=(record_id,),
                message="equally recent OpenAlex snapshots conflict on author_keywords",
            )
        )
    for namespace, values in _identifier_values(current_openalex).items():
        if len(values) > 1:
            issues.append(
                CanonicalizationIssue(
                    stage="identifier_conflict",
                    record_ids=(record_id,),
                    message=(
                        "equally recent OpenAlex snapshots conflict on "
                        f"{namespace} identifier"
                    ),
                )
            )

    if crossref_records:
        latest_crossref = max(record.provenance.retrieved_at for record in crossref_records)
        current_crossref = [
            record
            for record in crossref_records
            if record.provenance.retrieved_at == latest_crossref
        ]
        for field in ("title", "journal", "abstract"):
            values = [getattr(record, field) for record in current_crossref]
            normalizer = _normalize_abstract if field == "abstract" else _normalize_text
            if _nonempty_conflicts(values, normalizer):
                issues.append(
                    CanonicalizationIssue(
                        stage="metadata_conflict",
                        record_ids=(record_id,),
                        message=f"equally recent Crossref snapshots conflict on {field}",
                    )
                )
        date_values: dict[CrossrefDateKind, set[tuple[int, int | None, int | None]]] = (
            defaultdict(set)
        )
        for record in current_crossref:
            for item in record.dates:
                if item.month is not None and item.day is not None:
                    date_values[item.kind].add((item.year, item.month, item.day))
        for date_kind, values in date_values.items():
            if len(values) > 1:
                issues.append(
                    CanonicalizationIssue(
                        stage="date_conflict",
                        record_ids=(record_id,),
                        message=(
                            "equally recent Crossref snapshots conflict on "
                            f"{date_kind.value} date"
                        ),
                    )
                )
    return issues


def _normalize_retrievals(
    records: Sequence[EnrichedWorkRecord],
) -> tuple[list[_Record], list[CanonicalizationIssue]]:
    grouped: dict[str, list[EnrichedWorkRecord]] = defaultdict(list)
    for record in records:
        grouped[record.openalex.provenance.record_id].append(record)

    normalized: list[_Record] = []
    issues: list[CanonicalizationIssue] = []
    for record_id in sorted(grouped):
        snapshots = grouped[record_id]
        openalex_records = [snapshot.openalex for snapshot in snapshots]
        openalex = _choose_openalex(openalex_records)
        openalex_doi = normalize_doi(openalex.external_ids.doi)
        crossref_records = [
            snapshot.crossref
            for snapshot in snapshots
            if snapshot.crossref is not None
            and normalize_doi(snapshot.crossref.doi) == openalex_doi
        ]
        crossref = _choose_crossref(crossref_records) if crossref_records else None
        issues.extend(
            _snapshot_issues(record_id, openalex_records, crossref_records)
        )
        normalized.append(_Record(openalex=openalex, crossref=crossref))
    return normalized, issues


def _external_identifiers(record: _Record) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    for namespace, value in record.openalex.external_ids.model_dump(
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


def _relation_target_key(id_type: str, identifier: str) -> tuple[str, str] | None:
    namespace = id_type.strip().casefold()
    if namespace == "doi":
        value = normalize_doi(identifier)
        return (namespace, value) if value is not None else None
    value = identifier.strip()
    return (namespace, value) if namespace and value else None


def _authors_compatible(left: Sequence[Author], right: Sequence[Author]) -> bool:
    if len(left) != len(right):
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
    records: Sequence[_Record],
    root: int,
) -> set[str]:
    return {
        doi
        for index, record in enumerate(records)
        if union_find.find(index) == root
        if (doi := normalize_doi(record.openalex.external_ids.doi)) is not None
    }


def _group_records(
    records: Sequence[_Record],
    issues: list[CanonicalizationIssue],
) -> tuple[list[tuple[int, ...]], dict[int, set[str]]]:
    union_find = _UnionFind(len(records))
    roles: dict[int, set[str]] = defaultdict(set)

    dois: dict[str, list[int]] = defaultdict(list)
    identifier_index: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        for namespace, value in _external_identifiers(record).items():
            identifier_index[(namespace, value)].append(index)
        doi = normalize_doi(record.openalex.external_ids.doi)
        if doi is not None:
            dois[doi].append(index)
    for indexes in dois.values():
        for index in indexes[1:]:
            union_find.union(indexes[0], index)

    for subject_index, record in enumerate(records):
        if record.crossref is None:
            continue
        for relation in record.crossref.relations:
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
        title_groups[_normalize_text(record.openalex.metadata.title)].append(index)
    for indexes in title_groups.values():
        for left_position, left_index in enumerate(indexes):
            for right_index in indexes[left_position + 1 :]:
                left_root = union_find.find(left_index)
                right_root = union_find.find(right_index)
                if left_root == right_root:
                    continue
                record_ids = tuple(
                    sorted((records[left_index].record_id, records[right_index].record_id))
                )
                if not _authors_compatible(
                    records[left_index].openalex.authors,
                    records[right_index].openalex.authors,
                ):
                    issues.append(
                        CanonicalizationIssue(
                            stage="blocked_match",
                            record_ids=record_ids,
                            message="matching normalized titles lack compatible author evidence",
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
                            message="compatible title and authors were not merged across conflicting DOI components",
                        )
                    )
                    continue
                union_find.union(left_root, right_root)

    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(records)):
        components[union_find.find(index)].append(index)

    def component_key(indexes: Sequence[int]) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            sorted(
                (*_version_key(records[index]), records[index].record_id)
                for index in indexes
            )
        )

    ordered = [
        tuple(sorted(indexes, key=lambda index: records[index].record_id))
        for indexes in components.values()
    ]
    ordered.sort(key=component_key)
    return ordered, roles


def _version_key(record: _Record) -> tuple[str, str]:
    external_ids = record.openalex.external_ids
    if external_ids.arxiv is not None:
        return "arxiv", external_ids.arxiv
    doi = normalize_doi(external_ids.doi)
    if doi is not None:
        return "doi", doi
    if external_ids.openalex is None:
        raise ValueError("OpenAlex record lacks an external OpenAlex identifier")
    return "openalex", external_ids.openalex


def _representative(records: Sequence[_Record]) -> _Record:
    completeness = max(_openalex_completeness(record.openalex) for record in records)
    candidates = [
        record
        for record in records
        if _openalex_completeness(record.openalex) == completeness
    ]
    return min(candidates, key=lambda record: record.record_id)


def _version_crossref(records: Sequence[_Record]) -> CrossrefWorkRecord | None:
    candidates = [record.crossref for record in records if record.crossref is not None]
    return _choose_crossref(candidates) if candidates else None


def _version_evidence_issues(
    key: tuple[str, str],
    records: Sequence[_Record],
) -> list[CanonicalizationIssue]:
    issues: list[CanonicalizationIssue] = []
    record_ids = tuple(sorted(record.record_id for record in records))
    openalex_records = [record.openalex for record in records]
    fields = {
        "title": [record.metadata.title for record in openalex_records],
        "journal": [record.metadata.journal for record in openalex_records],
        "abstract": [record.metadata.abstract for record in openalex_records],
    }
    for field, values in fields.items():
        normalizer = _normalize_abstract if field == "abstract" else _normalize_text
        if _nonempty_conflicts(values, normalizer):
            issues.append(
                CanonicalizationIssue(
                    stage="metadata_conflict",
                    record_ids=record_ids,
                    message=(
                        f"version {key[0]}:{key[1]} has conflicting OpenAlex {field}"
                    ),
                )
            )
    if _author_evidence_conflicts(openalex_records):
        issues.append(
            CanonicalizationIssue(
                stage="metadata_conflict",
                record_ids=record_ids,
                message=f"version {key[0]}:{key[1]} has conflicting OpenAlex authors",
            )
        )
    publication_dates = {
        record.metadata.publication_date
        for record in openalex_records
        if record.metadata.publication_date is not None
    }
    if len(publication_dates) > 1:
        issues.append(
            CanonicalizationIssue(
                stage="date_conflict",
                record_ids=record_ids,
                message=(
                    f"version {key[0]}:{key[1]} has conflicting OpenAlex "
                    "publication_date"
                ),
            )
        )
    keyword_sets = {
        _keyword_signature(record.metadata.author_keywords)
        for record in openalex_records
        if record.metadata.author_keywords
    }
    if len(keyword_sets) > 1:
        issues.append(
            CanonicalizationIssue(
                stage="metadata_conflict",
                record_ids=record_ids,
                message=(
                    f"version {key[0]}:{key[1]} has conflicting OpenAlex "
                    "author_keywords"
                ),
            )
        )
    for namespace, values in _identifier_values(
        openalex_records,
        exclude=frozenset({"openalex"}),
    ).items():
        if len(values) > 1:
            issues.append(
                CanonicalizationIssue(
                    stage="identifier_conflict",
                    record_ids=record_ids,
                    message=(
                        f"version {key[0]}:{key[1]} has conflicting OpenAlex "
                        f"{namespace} identifiers"
                    ),
                )
            )

    crossref_records = [
        record.crossref for record in records if record.crossref is not None
    ]
    for field in ("title", "journal", "abstract"):
        values = [getattr(record, field) for record in crossref_records]
        normalizer = _normalize_abstract if field == "abstract" else _normalize_text
        if _nonempty_conflicts(values, normalizer):
            issues.append(
                CanonicalizationIssue(
                    stage="metadata_conflict",
                    record_ids=record_ids,
                    message=(
                        f"version {key[0]}:{key[1]} has conflicting Crossref {field}"
                    ),
                )
            )
    crossref_dois = {normalize_doi(record.doi) for record in crossref_records}
    if len(crossref_dois) > 1:
        issues.append(
            CanonicalizationIssue(
                stage="identifier_conflict",
                record_ids=record_ids,
                message=f"version {key[0]}:{key[1]} has conflicting Crossref DOI",
            )
        )
    return issues


def _version_kind(
    key: tuple[str, str],
    records: Sequence[_Record],
    roles: dict[int, set[str]],
    indexes: Sequence[int],
    issues: list[CanonicalizationIssue],
) -> VersionKind:
    evidence = set().union(*(roles.get(index, set()) for index in indexes))
    if key[0] == "arxiv":
        evidence.add("preprint")
    explicit_roles = evidence & {"preprint", "manuscript", "publication"}
    if len(explicit_roles) > 1:
        issues.append(
            CanonicalizationIssue(
                stage="version_role_conflict",
                record_ids=tuple(sorted(record.record_id for record in records)),
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

    date_kinds = {
        item.kind
        for record in records
        if record.crossref is not None
        for item in record.crossref.dates
    }
    if CrossrefDateKind.PUBLISHED_PRINT in date_kinds:
        return VersionKind.JOURNAL_FINAL
    if CrossrefDateKind.PUBLISHED_ONLINE in date_kinds:
        return VersionKind.JOURNAL_ONLINE
    return VersionKind.JOURNAL_FINAL


def _resolved_version_date(
    kind: VersionKind,
    records: Sequence[_Record],
    representative: _Record,
    issues: list[CanonicalizationIssue],
) -> date | None:
    by_kind: dict[CrossrefDateKind, set[date]] = defaultdict(set)
    for record in records:
        if record.crossref is None:
            continue
        for item in record.crossref.dates:
            if item.month is not None and item.day is not None:
                by_kind[item.kind].add(date(item.year, item.month, item.day))

    selected_by_kind: dict[CrossrefDateKind, date] = {}
    record_ids = tuple(sorted(record.record_id for record in records))
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
    return representative.openalex.metadata.publication_date


def _build_versions(
    component: Sequence[int],
    records: Sequence[_Record],
    roles: dict[int, set[str]],
    issues: list[CanonicalizationIssue],
) -> list[_BuiltVersion]:
    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index in component:
        grouped[_version_key(records[index])].append(index)

    built: list[_BuiltVersion] = []
    for key in sorted(grouped):
        indexes = grouped[key]
        version_records = [records[index] for index in indexes]
        issues.extend(_version_evidence_issues(key, version_records))
        representative = _representative(version_records)
        kind = _version_kind(key, version_records, roles, indexes, issues)
        version_date = _resolved_version_date(
            kind,
            version_records,
            representative,
            issues,
        )
        built.append(
            _BuiltVersion(
                version=PaperVersion(
                    kind=kind,
                    source=key[0],
                    identifier=key[1],
                    date=version_date,
                ),
                representative=representative,
                crossref=_version_crossref(version_records),
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
    openalex: OpenAlexWorkRecord,
    crossref: CrossrefWorkRecord | None,
) -> list[CanonicalizationIssue]:
    if crossref is None:
        return []
    issues: list[CanonicalizationIssue] = []
    comparisons = (
        ("title", openalex.metadata.title, crossref.title, _normalize_text),
        ("journal", openalex.metadata.journal, crossref.journal, _normalize_text),
        ("abstract", openalex.metadata.abstract, crossref.abstract, _normalize_abstract),
    )
    for field, openalex_value, crossref_value, normalizer in comparisons:
        if (
            openalex_value is not None
            and crossref_value is not None
            and normalizer(openalex_value) != normalizer(crossref_value)
        ):
            issues.append(
                CanonicalizationIssue(
                    stage="metadata_conflict",
                    record_ids=(openalex.provenance.record_id,),
                    message=f"OpenAlex and Crossref conflict on nonempty {field}; kept OpenAlex",
                )
            )
    return issues


def _deduplicate_sources(records: Iterable[_Record]) -> tuple[MetadataSource, ...]:
    sources: dict[tuple[str, str], MetadataSource] = {}
    for record in records:
        candidates = [record.openalex.provenance]
        if record.crossref is not None:
            candidates.append(record.crossref.provenance)
        for source in candidates:
            key = (source.provider, source.record_id)
            existing = sources.get(key)
            if existing is None or source.retrieved_at > existing.retrieved_at:
                sources[key] = source
    return tuple(sources[key] for key in sorted(sources))


def _build_paper(
    component: Sequence[int],
    records: Sequence[_Record],
    roles: dict[int, set[str]],
    issues: list[CanonicalizationIssue],
) -> CanonicalPaper:
    built_versions = _build_versions(component, records, roles, issues)
    preferred = _preferred_version(built_versions)
    openalex = preferred.representative.openalex
    crossref = preferred.crossref
    issues.extend(_metadata_issues(openalex, crossref))

    abstract = openalex.metadata.abstract
    if abstract is None and crossref is not None:
        abstract = crossref.abstract
    metadata = CanonicalMetadata(
        title=openalex.metadata.title,
        journal=openalex.metadata.journal,
        publication_date=preferred.version.date,
        abstract=abstract,
        author_keywords=openalex.metadata.author_keywords,
    )
    external_ids = openalex.external_ids.model_dump(exclude_none=False)
    if crossref is not None:
        external_ids["crossref"] = crossref.provenance.record_id

    return CanonicalPaper(
        metadata=metadata,
        external_ids=ExternalIds.model_validate(external_ids),
        authors=openalex.authors,
        versions=tuple(built.version for built in built_versions),
        sources=_deduplicate_sources(records[index] for index in component),
        preferred_version=VersionRef(
            source=preferred.version.source,
            identifier=preferred.version.identifier,
        ),
    )


def canonicalize_records(records: Sequence[EnrichedWorkRecord]) -> CanonicalizationResult:
    """Consolidate enriched provider evidence into canonical papers."""

    normalized, issues = _normalize_retrievals(records)
    for record in normalized:
        issues.extend(_metadata_issues(record.openalex, record.crossref))
    components, roles = _group_records(normalized, issues)
    papers = tuple(
        _build_paper(component, normalized, roles, issues)
        for component in components
    )
    unique_issues = sorted(
        set(issues),
        key=lambda issue: (issue.stage, issue.record_ids, issue.message),
    )
    return CanonicalizationResult(papers=papers, issues=tuple(unique_issues))
