"""DOI-first provider-neutral evidence consolidation and canonicalization."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser

from literature_monitor.diagnostics import RunDiagnostic
from literature_monitor.identifiers import normalize_doi
from literature_monitor.models import (
    Author,
    CanonicalMetadata,
    CanonicalPaper,
    EvidenceDate,
    EvidenceDateKind,
    ExternalIds,
    MetadataSource,
    ProviderWorkEvidence,
)


_DATE_PRECEDENCE = (
    EvidenceDateKind.PUBLISHED_PRINT,
    EvidenceDateKind.PUBLISHED_ONLINE,
    EvidenceDateKind.PUBLISHED,
    EvidenceDateKind.ISSUED,
)


@dataclass(frozen=True)
class CanonicalizationIssue:
    stage: str
    message: str
    record_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanonicalizationResult:
    papers: tuple[CanonicalPaper, ...]
    issues: tuple[CanonicalizationIssue, ...]
    diagnostics: tuple[RunDiagnostic, ...] = ()


@dataclass(frozen=True)
class EvidenceCluster:
    evidence: tuple[ProviderWorkEvidence, ...]


@dataclass(frozen=True)
class EvidenceConsolidationResult:
    clusters: tuple[EvidenceCluster, ...]
    issues: tuple[CanonicalizationIssue, ...]
    diagnostics: tuple[RunDiagnostic, ...] = ()


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


_REPRESENTATIONAL_PUNCTUATION = str.maketrans({
    **{character: "-" for character in "‐‑‒–—―"},
    "‘": "'", "’": "'", "“": '"', "”": '"', "…": "...",
})
_SECTION_LABELS = "background|objective|objectives|methods|results|conclusion|conclusions"
_STRUCTURAL_LABEL = re.compile(
    rf"^\s*(?:{_SECTION_LABELS})\s*:\s*", re.IGNORECASE,
)
_LABEL_TEXT = re.compile(rf"(?:{_SECTION_LABELS})\s*:?", re.IGNORECASE)
_HTML_ABSTRACT_TAGS = frozenset({
    "a", "abbr", "b", "bdi", "bdo", "blockquote", "br", "cite", "code",
    "dd", "del", "div", "dl", "dt", "em", "h1", "h2", "h3", "h4", "h5", "h6",
    "hr", "i", "ins", "kbd", "li", "mark", "ol", "p", "pre", "q", "s", "samp",
    "small", "span", "strong", "sub", "sup", "time", "u", "ul", "var", "wbr",
})
_JATS_ABSTRACT_TAGS = frozenset({
    "abstract", "sec", "title", "label", "p", "italic", "bold", "underline",
    "overline", "strike", "monospace", "sc", "sub", "sup", "ext-link", "xref",
    "named-content", "styled-content", "list", "list-item", "break",
})


def _abstract_markup_tag(tag: str) -> str | None:
    # 只接受明确的 HTML/JATS 标签；未知名称或命名空间可能是科学正文。
    if tag.startswith("jats:"):
        local = tag.removeprefix("jats:")
        return local if local in _JATS_ABSTRACT_TAGS else None
    return tag if tag in _HTML_ABSTRACT_TAGS | _JATS_ABSTRACT_TAGS else None


class _AbstractText(HTMLParser):
    """只去除已确认的 markup，保留模糊尖括号正文和明确的段落边界。"""

    _blocks = {
        "abstract", "sec", "title", "label", "p", "div", "blockquote", "dd", "dl",
        "dt", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ol", "pre", "ul",
        "list", "list-item",
    }
    _heading_tags = {"title", "label", "h1", "h2", "h3", "h4", "h5", "h6"}
    _void_tags = {"br", "break", "hr", "wbr"}

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self.line_offsets = [0, *(match.end() for match in re.finditer("\n", source))]
        self.parts: list[str] = []
        self.open_tags: list[tuple[str, int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        markup = _abstract_markup_tag(tag)
        if markup is None:
            self.parts.append(self.get_starttag_text())
            return
        if markup in self._void_tags:
            if markup != "wbr":
                self.parts.append("\n")
            return
        # 合法标签名也可能是科研 token；等对应 end 到达后才确认 markup。
        self.open_tags.append((markup, len(self.parts)))
        self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag: str) -> None:
        markup = _abstract_markup_tag(tag)
        matched = next((
            index for index in range(len(self.open_tags) - 1, -1, -1)
            if self.open_tags[index][0] == markup
        ), None)
        if markup is None or matched is None:
            # HTMLParser 会折叠标签大小写，按原位置保留未知结束 token。
            line, column = self.getpos()
            start = self.line_offsets[line - 1] + column
            self.parts.append(self.source[start:self.source.index(">", start) + 1])
            return
        _, start = self.open_tags[matched]
        del self.open_tags[matched:]
        self.parts[start] = "\n" if markup in self._blocks else ""
        if markup in self._heading_tags:
            heading = unicodedata.normalize("NFKC", "".join(self.parts[start + 1:])).strip()
            if _LABEL_TEXT.fullmatch(heading):
                del self.parts[start + 1:]
        if markup in self._blocks:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        markup = _abstract_markup_tag(tag)
        if markup is None:
            self.parts.append(self.get_starttag_text())
        elif markup in self._blocks or markup in self._void_tags - {"wbr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _normalize_abstract(value: str) -> str:
    parser = _AbstractText(value)
    parser.feed(value)
    parser.close()
    text = unicodedata.normalize("NFKC", "".join(parser.parts)).translate(
        _REPRESENTATIONAL_PUNCTUATION
    )
    # 独立 label 还需开头、空行或句末边界，避免删除换行拆开的正文词。
    raw_lines = text.splitlines()
    lines: list[str] = []
    previous_body = ""
    for index, raw_line in enumerate(raw_lines):
        line = raw_line.strip()
        if _LABEL_TEXT.fullmatch(line):
            boundary = (
                not previous_body
                or not raw_lines[index - 1].strip()
                or previous_body.rstrip("\"')]} ").endswith((".", "!", "?", "。"))
            )
            has_body = any(
                following.strip() and not _LABEL_TEXT.fullmatch(following.strip())
                for following in raw_lines[index + 1:]
            )
            if boundary and has_body:
                continue
        else:
            # colon-only label 不能绕过边界判断；此处只处理同一行带正文的形式。
            line = _STRUCTURAL_LABEL.sub("", line)
        lines.append(line)
        if line:
            previous_body = line
    return " ".join(" ".join(lines).split())


def _record_key(record: ProviderWorkEvidence) -> tuple[str, str]:
    return (
        record.provenance.provider.strip().casefold(),
        record.provenance.record_id,
    )


def _provider_label(provider: str) -> str:
    labels = {
        "openalex": "OpenAlex",
        "crossref": "Crossref",
        "semantic_scholar": "Semantic Scholar",
    }
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
    return min(
        candidates,
        key=lambda record: record.model_copy(
            update={"provider_topics": (), "fields_of_study": (),
                    "monitor_journal_issns": ()}
        ).model_dump_json(),
    )


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


def _author_ids_conflict(left: Author, right: Author) -> bool:
    return any(
        getattr(left, namespace) is not None
        and getattr(right, namespace) is not None
        and getattr(left, namespace) != getattr(right, namespace)
        for namespace in ("openalex_id", "orcid")
    )


def _author_name_tokens(name: str) -> tuple[str, ...]:
    value = _normalize_text(name).translate(_REPRESENTATIONAL_PUNCTUATION)
    if value.count(",") == 1:
        family, given = value.split(",")
        value = f"{given.strip()} {family.strip()}"
    return tuple(token for token in re.split(r"[.\s]+", value) if token)


def _author_names_equivalent(left: str, right: str) -> bool:
    """仅比较同一 DOI 内作者姓名的兼容表示。"""
    left_tokens = _author_name_tokens(left)
    right_tokens = _author_name_tokens(right)
    if not left_tokens or not right_tokens or len(left_tokens) != len(right_tokens):
        return False
    if left_tokens[-1] != right_tokens[-1]:
        return False
    return all(
        a == b
        or (len(a) == 1 and a.isalpha() and b.isalpha() and b.startswith(a))
        or (len(b) == 1 and b.isalpha() and a.isalpha() and a.startswith(b))
        for a, b in zip(left_tokens[:-1], right_tokens[:-1], strict=True)
    )


def _author_lists_conflict(
    left: Sequence[Author],
    right: Sequence[Author],
) -> bool:
    if not left or not right:
        return False
    if len(left) != len(right):
        return True
    for left_author, right_author in zip(left, right, strict=True):
        if _author_ids_conflict(left_author, right_author) or not _author_names_equivalent(
            left_author.name, right_author.name
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
    grouped: dict[tuple[str, str, str], list[ProviderWorkEvidence]] = defaultdict(list)
    for record in records:
        # A provider record ID can occur with distinct DOIs; never discard either work.
        grouped[(*_record_key(record), _record_doi(record) or "")].append(record)

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
                    ),
                    "monitor_journal_issns": tuple(sorted({
                        issn for snapshot in snapshots for issn in snapshot.monitor_journal_issns
                    })),
                }
            )
        )
    return normalized, issues


def _record_doi(record: ProviderWorkEvidence) -> str | None:
    doi = normalize_doi(record.external_ids.doi)
    return doi if doi is not None and re.fullmatch(r"10\.\d{4,9}/[^\s]+", doi) else None


def _group_records(
    records: Sequence[ProviderWorkEvidence],
) -> list[tuple[int, ...]]:
    dois: dict[str, list[int]] = defaultdict(list)
    singletons: list[tuple[int, ...]] = []
    for index, record in enumerate(records):
        doi = _record_doi(record)
        if doi is None:
            singletons.append((index,))
        else:
            dois[doi].append(index)
    return [tuple(dois[doi]) for doi in sorted(dois)] + singletons


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


def _doi_evidence_issues(
    doi: str,
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
                            f"DOI {doi} has conflicting "
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
                        f"DOI {doi} has conflicting "
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
                        f"DOI {doi} has conflicting "
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
                        f"DOI {doi} has conflicting "
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
                            f"DOI {doi} has conflicting "
                            f"{provider} {namespace} identifiers"
                        ),
                    )
                )
    return issues


def _publication_date(
    records: Sequence[ProviderWorkEvidence],
    representative: ProviderWorkEvidence,
    issues: list[CanonicalizationIssue],
) -> date | None:
    by_kind: dict[EvidenceDateKind, set[date]] = defaultdict(set)
    for record in records:
        for item in record.dates:
            if item.month is not None and item.day is not None:
                by_kind[item.kind].add(date(item.year, item.month, item.day))

    record_ids = tuple(sorted({record.provenance.record_id for record in records}))
    for kind in _DATE_PRECEDENCE:
        values = by_kind[kind]
        if len(values) > 1:
            selected = min(values)
            rendered = ", ".join(value.isoformat() for value in sorted(values))
            issues.append(CanonicalizationIssue(
                stage="date_conflict",
                record_ids=record_ids,
                message=(f"{kind.value} has multiple complete dates ({rendered}); "
                         f"selected {selected.isoformat()}"),
            ))
    for kind in _DATE_PRECEDENCE:
        if by_kind[kind]:
            return min(by_kind[kind])
    return representative.publication_date


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
    issues: list[CanonicalizationIssue],
) -> CanonicalPaper | None:
    evidence = tuple(records[index] for index in component)
    record_ids = tuple(sorted({record.provenance.record_id for record in evidence}))
    doi = _record_doi(evidence[0])
    if doi is None:
        issues.append(CanonicalizationIssue(
            stage="missing_doi",
            record_ids=record_ids,
            message="provider evidence has no valid DOI; no canonical Paper created",
        ))
        return None
    representative = _representative(evidence)
    if not _canonical_eligible(representative):
        issues.append(CanonicalizationIssue(
            stage="insufficient_metadata",
            record_ids=record_ids,
            message=("provider evidence lacks title, journal, or author data "
                     "required for a canonical paper"),
        ))
        return None

    issues.extend(_doi_evidence_issues(doi, evidence))
    issues.extend(_metadata_issues(representative, evidence))
    return CanonicalPaper(
        metadata=CanonicalMetadata(
            title=representative.title,
            journal=representative.journal,
            publication_date=_publication_date(evidence, representative, issues),
            abstract=_fallback_abstract(representative, evidence),
            author_keywords=representative.author_keywords,
        ),
        external_ids=_merged_external_ids(representative, evidence).model_copy(
            update={"doi": doi},
        ),
        authors=_merged_authors(representative, evidence),
        sources=_deduplicate_sources(evidence),
        journal_issns=tuple(sorted({
            issn for record in evidence for issn in record.monitor_journal_issns
        })),
    )


def canonicalize_records(
    records: Sequence[ProviderWorkEvidence],
) -> CanonicalizationResult:
    """Consolidate provider-neutral evidence into canonical papers."""

    normalized, components, issues = _consolidation_parts(records)
    papers = tuple(
        paper
        for component in components
        if (paper := _build_paper(component, normalized, issues)) is not None
    )
    unique_issues = sorted(
        set(issues),
        key=lambda issue: (issue.stage, issue.record_ids, issue.message),
    )
    return CanonicalizationResult(
        papers=papers, issues=tuple(unique_issues),
    )


def _consolidation_parts(
    records: Sequence[ProviderWorkEvidence],
) -> tuple[
    list[ProviderWorkEvidence],
    list[tuple[int, ...]],
    list[CanonicalizationIssue],
]:
    normalized, issues = _normalize_retrievals(records)
    return normalized, _group_records(normalized), issues


def consolidate_evidence(
    records: Sequence[ProviderWorkEvidence],
) -> EvidenceConsolidationResult:
    """Normalize snapshots and group by DOI, keeping DOI-less candidates independent."""

    normalized, components, issues = _consolidation_parts(records)
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
