from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

from literature_monitor.canonicalize import (
    _normalize_abstract,
    _normalize_retrievals,
    _choose_evidence,
    CanonicalizationResult,
    canonicalize_records,
    consolidate_evidence,
)
from literature_monitor.crossref import (
    CrossrefPartialDate,
    CrossrefRelation,
    CrossrefWorkRecord,
    EnrichedWorkRecord,
)
from literature_monitor.models import (
    Author,
    CanonicalMetadata,
    EvidenceDate,
    EvidenceDateKind,
    ExternalIds,
    ProviderRecordRef,
    MetadataSource,
    ProviderTopic,
    ProviderWorkEvidence,
)
from literature_monitor.openalex import OpenAlexWorkRecord
from literature_monitor.search import build_searchable_projection


NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)


def author(
    name: str = "Ada Author",
    *,
    openalex_id: str | None = None,
    orcid: str | None = None,
) -> Author:
    return Author(name=name, openalex_id=openalex_id, orcid=orcid)


def openalex(
    identifier: str,
    *,
    title: str = "A Study",
    doi: str | None = "10.5555/example",
    arxiv: str | None = None,
    authors: tuple[Author, ...] | None = None,
    publication_date: date | None = None,
    abstract: str | None = None,
    author_keywords: tuple[str, ...] = (),
    retrieved_at: datetime = NOW,
    external_ids: dict[str, str] | None = None,
) -> OpenAlexWorkRecord:
    values: dict[str, Any] = {
        "openalex": f"https://openalex.org/{identifier}",
        "doi": doi,
        "arxiv": arxiv,
    }
    if external_ids:
        values.update(external_ids)
    return OpenAlexWorkRecord(
        metadata=CanonicalMetadata(
            title=title,
            journal="Biometrics",
            publication_date=publication_date,
            abstract=abstract,
            author_keywords=author_keywords,
        ),
        external_ids=ExternalIds.model_validate(values),
        authors=authors or (author(),),
        source_id="https://openalex.org/S8265502",
        provenance=MetadataSource(
            provider="openalex",
            record_id=f"https://openalex.org/{identifier}",
            retrieved_at=retrieved_at,
        ),
    )


def crossref(
    doi: str,
    *,
    title: str | None = None,
    journal: str | None = None,
    abstract: str | None = None,
    authors: tuple[Author, ...] = (),
    dates: tuple[CrossrefPartialDate, ...] = (),
    relations: tuple[CrossrefRelation, ...] = (),
    retrieved_at: datetime = NOW,
) -> CrossrefWorkRecord:
    return CrossrefWorkRecord(
        doi=doi,
        title=title,
        journal=journal,
        abstract=abstract,
        authors=authors,
        dates=dates,
        relations=relations,
        provenance=MetadataSource(
            provider="crossref",
            record_id=doi,
            retrieved_at=retrieved_at,
        ),
    )


def enriched(
    record: OpenAlexWorkRecord,
    evidence: CrossrefWorkRecord | None = None,
) -> tuple[ProviderWorkEvidence, ...]:
    return EnrichedWorkRecord(openalex=record, crossref=evidence).to_evidence()


def canonicalize(
    records: Sequence[tuple[ProviderWorkEvidence, ...]],
) -> CanonicalizationResult:
    return canonicalize_records(tuple(item for record in records for item in record))


def relation(
    relation_type: str,
    identifier: str,
    *,
    id_type: str = "doi",
) -> CrossrefRelation:
    return CrossrefRelation(
        relation_type=relation_type,
        id_type=id_type,
        identifier=identifier,
    )


def partial_date(
    kind: str,
    year: int,
    month: int | None = None,
    day: int | None = None,
) -> CrossrefPartialDate:
    return CrossrefPartialDate(kind=kind, year=year, month=month, day=day)


def paper_projection(paper: object) -> dict[str, Any]:
    payload = paper.model_dump(mode="json")  # type: ignore[attr-defined]
    payload.pop("id")
    payload.pop("workflow")
    return payload


def test_non_openalex_evidence_can_create_a_canonical_paper() -> None:
    evidence = ProviderWorkEvidence(
        provenance=MetadataSource(
            provider="independent",
            record_id="work-1",
            retrieved_at=NOW,
        ),
        title="Provider-neutral study",
        journal="Biometrics",
        publication_date=date(2026, 9, 1),
        authors=(author("Ada Author", orcid="0000-0001-2345-6789"),),
        external_ids=ExternalIds.model_validate({"doi": "10.5555/independent", "independent": "work-1"}),
    )

    result = canonicalize_records((evidence,))

    assert not result.issues
    assert len(result.papers) == 1
    paper = result.papers[0]
    assert paper.metadata.title == "Provider-neutral study"
    assert paper.external_ids.model_dump()["independent"] == "work-1"
    assert paper.sources == (evidence.provenance,)


def test_public_consolidation_groups_same_doi_deterministically() -> None:
    openalex_evidence = openalex("W1", doi="10.5555/same").to_evidence()
    crossref_evidence = crossref(
        "10.5555/same",
        title="Crossref title",
        journal="Biometrics",
        authors=(author(),),
    ).to_evidence()

    forward = consolidate_evidence((openalex_evidence, crossref_evidence))
    reverse = consolidate_evidence((crossref_evidence, openalex_evidence))

    assert len(forward.clusters) == 1
    assert [
        (record.provenance.provider, record.provenance.record_id)
        for record in forward.clusters[0].evidence
    ] == [
        (record.provenance.provider, record.provenance.record_id)
        for record in reverse.clusters[0].evidence
    ]
    assert forward.issues == reverse.issues


def test_public_consolidation_keeps_conflicting_dois_separate() -> None:
    first = openalex("W1", doi="10.5555/a").to_evidence()
    second = crossref(
        "10.5555/b",
        title="A Study",
        journal="Biometrics",
        authors=(author(),),
    ).to_evidence()

    result = consolidate_evidence((first, second))

    assert len(result.clusters) == 2
    assert not result.issues
    assert not result.diagnostics


def test_crossref_only_sufficient_evidence_canonicalizes() -> None:
    evidence = crossref(
        "10.5555/crossref-only",
        title="Crossref-only study",
        journal="Biometrics",
        authors=(author("Ada Author", orcid="https://orcid.org/O1"),),
        dates=(partial_date("published-online", 2026, 3, 4),),
    ).to_evidence()

    result = canonicalize_records((evidence,))

    assert not [
        issue for issue in result.issues if issue.stage == "insufficient_metadata"
    ]
    assert len(result.papers) == 1
    paper = result.papers[0]
    assert paper.metadata.title == "Crossref-only study"
    assert paper.external_ids.doi == "10.5555/crossref-only"
    assert paper.authors[0].orcid == "https://orcid.org/O1"
    assert [source.provider for source in paper.sources] == ["crossref"]


def test_compatible_cross_provider_authors_merge_stable_ids() -> None:
    openalex_record = openalex(
        "W1",
        doi="10.5555/authors",
        authors=(author("Ada Author", openalex_id="https://openalex.org/A1"),),
    )
    crossref_record = crossref(
        "10.5555/authors",
        title="A Study",
        journal="Biometrics",
        abstract="Richer abstract",
        authors=(
            author(
                "Ada Author",
                orcid="https://orcid.org/0000-0002-1825-0097",
            ),
        ),
        dates=(partial_date("published-online", 2026, 3, 4),),
    )

    paper = canonicalize((enriched(openalex_record, crossref_record),)).papers[0]

    assert paper.authors == (
        author(
            "Ada Author",
            openalex_id="https://openalex.org/A1",
            orcid="https://orcid.org/0000-0002-1825-0097",
        ),
    )


def semantic_scholar_evidence(
    paper_id: str,
    *,
    doi: str | None = "10.5555/example",
    topics: tuple[ProviderTopic, ...] = (),
) -> ProviderWorkEvidence:
    return ProviderWorkEvidence(
        provenance=MetadataSource(
            provider="semantic_scholar",
            record_id=paper_id,
            retrieved_at=NOW,
        ),
        title="A Study",
        journal="Biometrics",
        abstract="Semantic Scholar abstract",
        authors=(
            author(
                "Ada Author",
                orcid="https://orcid.org/0000-0002-1825-0097",
            ),
        ),
        external_ids=ExternalIds.model_validate(
            {"semantic_scholar": paper_id, "doi": doi}
        ),
        provider_topics=topics,
        fields_of_study=("Medicine",) if topics else (),
    )


def test_three_provider_same_doi_is_one_order_independent_paper() -> None:
    openalex_evidence = openalex(
        "W1",
        doi="10.5555/three",
        authors=(author(openalex_id="https://openalex.org/A1"),),
    ).to_evidence()
    crossref_evidence = crossref(
        "10.5555/three",
        title="A Study",
        journal="Biometrics",
        authors=(author(),),
    ).to_evidence()
    semantic_evidence = semantic_scholar_evidence(
        "S2-1",
        doi="10.5555/three",
    )
    records = (openalex_evidence, crossref_evidence, semantic_evidence)

    forward = canonicalize_records(records)
    reverse = canonicalize_records(tuple(reversed(records)))

    assert len(forward.papers) == 1
    assert paper_projection(forward.papers[0]) == paper_projection(reverse.papers[0])
    paper = forward.papers[0]
    assert paper.external_ids.model_dump(exclude_none=True) == {
        "openalex": "https://openalex.org/W1",
        "doi": "10.5555/three",
        "crossref": "10.5555/three",
        "semantic_scholar": "S2-1",
    }
    assert {source.provider for source in paper.sources} == {
        "openalex",
        "crossref",
        "semantic_scholar",
    }
    assert paper.authors[0].openalex_id == "https://openalex.org/A1"
    assert paper.authors[0].orcid == "https://orcid.org/0000-0002-1825-0097"


def test_semantic_scholar_only_evidence_canonicalizes_and_taxonomy_has_no_authority() -> None:
    plain = semantic_scholar_evidence("S2-only")
    tagged = semantic_scholar_evidence(
        "S2-only",
        topics=(ProviderTopic(value="Statistics", source="s2-fos-model"),),
    )

    plain_result = canonicalize_records((plain,))
    tagged_result = canonicalize_records((tagged,))

    assert len(tagged_result.papers) == 1
    assert paper_projection(tagged_result.papers[0]) == paper_projection(
        plain_result.papers[0]
    )
    assert tagged_result.papers[0].external_ids.model_dump()[
        "semantic_scholar"
    ] == "S2-only"
    assert [source.provider for source in tagged_result.papers[0].sources] == [
        "semantic_scholar"
    ]


def test_taxonomy_does_not_affect_equal_snapshot_selection() -> None:
    base = semantic_scholar_evidence("S2-equal")
    ada = base.model_copy(
        update={"authors": (author("Ada Author"),)}
    )
    zoe = base.model_copy(
        update={"authors": (author("Zoe Author"),)}
    )
    alpha_topic = (ProviderTopic(value="Alpha", source="s2-fos-model"),)
    zulu_topic = (ProviderTopic(value="Zulu", source="s2-fos-model"),)

    first = canonicalize_records(
        (
            ada.model_copy(
                update={
                    "provider_topics": zulu_topic,
                    "fields_of_study": ("Zulu",),
                }
            ),
            zoe.model_copy(
                update={
                    "provider_topics": alpha_topic,
                    "fields_of_study": ("Alpha",),
                }
            ),
        )
    )
    swapped = canonicalize_records(
        (
            ada.model_copy(
                update={
                    "provider_topics": alpha_topic,
                    "fields_of_study": ("Alpha",),
                }
            ),
            zoe.model_copy(
                update={
                    "provider_topics": zulu_topic,
                    "fields_of_study": ("Zulu",),
                }
            ),
        )
    )

    assert paper_projection(first.papers[0]) == paper_projection(swapped.papers[0])
    assert [item.name for item in first.papers[0].authors] == ["Ada Author"]


def test_exact_normalized_doi_produces_one_paper() -> None:
    result = canonicalize(
        (
            enriched(openalex("W2", doi="https://doi.org/10.5555/EXAMPLE")),
            enriched(openalex("W1", doi="10.5555/example")),
        )
    )

    assert len(result.papers) == 1
    paper = result.papers[0]
    assert paper.doi == "10.5555/example"
    assert len(paper.sources) == 2


def test_shared_crossref_evidence_supplements_every_openalex_anchor() -> None:
    evidence = crossref(
        "10.5555/same",
        dates=(partial_date("published-online", 2026, 5, 1),),
    )
    records = (
        enriched(
            openalex(
                "W1",
                doi="10.5555/same",
                arxiv="2601.00001",
            ),
            evidence,
        ),
        enriched(openalex("W2", doi="10.5555/same"), evidence),
    )

    forward = canonicalize(records)
    reverse = canonicalize(tuple(reversed(records)))

    assert paper_projection(forward.papers[0]) == paper_projection(reverse.papers[0])
    assert forward.issues == reverse.issues
    assert len(forward.papers) == 1
    assert forward.papers[0].metadata.publication_date == date(2026, 5, 1)
    normalized, _ = _normalize_retrievals(tuple(item for group in records for item in group))
    crossref_record, = [item for item in normalized if item.provenance.provider == "crossref"]
    assert {anchor.record_id for anchor in crossref_record.supplements} == {
        "https://openalex.org/W1", "https://openalex.org/W2",
    }


def test_same_date_kind_conflict_chooses_earliest_and_reports_issue() -> None:
    record = openalex("W1", doi="10.5555/dates")
    evidence = crossref(
        "10.5555/dates",
        dates=(
            partial_date("published-print", 2026, 5, 2),
            partial_date("published-print", 2026, 5, 1),
        ),
    )

    result = canonicalize((enriched(record, evidence),))

    assert result.papers[0].metadata.publication_date == date(2026, 5, 1)
    assert any(issue.stage == "date_conflict" for issue in result.issues)


def test_crossref_fills_missing_abstract_but_does_not_overwrite_conflict() -> None:
    filled = canonicalize(
        (
            enriched(
                openalex("W1", doi="10.5555/fill"),
                crossref("10.5555/fill", abstract="Crossref abstract"),
            ),
        )
    )
    assert filled.papers[0].metadata.abstract == "Crossref abstract"
    assert not [issue for issue in filled.issues if issue.stage == "metadata_conflict"]

    conflict = canonicalize(
        (
            enriched(
                openalex("W2", doi="10.5555/conflict", abstract="OpenAlex abstract"),
                crossref("10.5555/conflict", abstract="Crossref abstract"),
            ),
        )
    )
    assert conflict.papers[0].metadata.abstract == "OpenAlex abstract"
    assert any(issue.stage == "metadata_conflict" for issue in conflict.issues)


def test_representative_uses_fixed_completeness_then_openalex_id() -> None:
    poorer = enriched(openalex("W1", doi="10.5555/same", title="Poorer"))
    richer = enriched(
        openalex("W2", doi="10.5555/same", title="Richer", abstract="Abstract")
    )

    paper = canonicalize((poorer, richer)).papers[0]

    assert paper.metadata.title == "Richer"
    assert paper.metadata.abstract == "Abstract"

    tied = canonicalize(
        (
            enriched(openalex("W2", doi="10.5555/tie", title="Second")),
            enriched(openalex("W1", doi="10.5555/tie", title="First")),
        )
    ).papers[0]
    assert tied.metadata.title == "First"


def test_same_doi_openalex_conflicts_are_visible_after_deterministic_selection() -> None:
    first = enriched(
        openalex(
            "W1",
            doi="10.5555/conflict",
            title="A title",
            authors=(author("Ada"),),
        )
    )
    second = enriched(
        openalex(
            "W2",
            doi="10.5555/conflict",
            title="B title",
            authors=(author("Grace"),),
        )
    )

    result = canonicalize((second, first))

    assert len(result.papers) == 1
    assert result.papers[0].metadata.title == "A title"
    assert [item.name for item in result.papers[0].authors] == ["Ada"]
    conflicts = [issue for issue in result.issues if issue.stage == "metadata_conflict"]
    assert {issue.message.rsplit(" ", maxsplit=1)[-1] for issue in conflicts} >= {
        "title",
        "authors",
    }
    assert not any("OpenAlex and OpenAlex" in issue.message for issue in conflicts)


def test_same_doi_crossref_conflict_is_visible_after_deterministic_selection() -> None:
    first = enriched(
        openalex("W1", doi="10.5555/conflict"),
        crossref("10.5555/conflict", abstract="Alpha abstract"),
    )
    second = enriched(
        openalex("W2", doi="10.5555/conflict"),
        crossref("10.5555/conflict", abstract="Beta abstract"),
    )

    result = canonicalize((second, first))

    assert result.papers[0].metadata.abstract == "Alpha abstract"
    assert any(
        issue.stage == "metadata_conflict"
        and issue.message.endswith("conflicting Crossref abstract")
        for issue in result.issues
    )


def test_duplicate_retrieval_uses_latest_snapshot_without_history_conflict() -> None:
    older = enriched(
        openalex(
            "W1",
            title="Old title",
            doi="10.5555/same",
            retrieved_at=NOW - timedelta(days=1),
        )
    )
    newer = enriched(
        openalex(
            "W1",
            title="New title",
            doi="10.5555/same",
            abstract="Improved",
            retrieved_at=NOW,
        )
    )

    result = canonicalize((older, newer))

    assert result.papers[0].metadata.title == "New title"
    assert result.papers[0].metadata.abstract == "Improved"
    assert not result.issues
    assert len(result.papers[0].sources) == 1


def test_equally_recent_missing_to_populated_uses_completeness_without_issue() -> None:
    missing = enriched(openalex("W1", doi="10.5555/same"))
    populated = enriched(
        openalex("W1", doi="10.5555/same", abstract="Improved")
    )

    result = canonicalize((missing, populated))

    assert result.papers[0].metadata.abstract == "Improved"
    assert not result.issues


def test_duplicate_crossref_retrieval_uses_latest_snapshot() -> None:
    item = openalex("W1", doi="10.5555/same")
    older = enriched(
        item,
        crossref(
            "10.5555/same",
            abstract="Old abstract",
            retrieved_at=NOW - timedelta(days=1),
        ),
    )
    newer = enriched(
        item,
        crossref(
            "10.5555/same",
            abstract="New abstract",
            retrieved_at=NOW,
        ),
    )

    result = canonicalize((older, newer))

    assert result.papers[0].metadata.abstract == "New abstract"
    assert not result.issues
    crossref_source = next(
        source for source in result.papers[0].sources if source.provider == "crossref"
    )
    assert crossref_source.retrieved_at == NOW


def test_equally_recent_conflicting_snapshots_are_visible() -> None:
    first = enriched(openalex("W1", title="First", doi="10.5555/same"))
    second = enriched(openalex("W1", title="Second", doi="10.5555/same"))

    result = canonicalize((first, second))

    assert any(issue.stage == "metadata_conflict" for issue in result.issues)


def test_equally_recent_snapshot_author_conflict_is_visible() -> None:
    ada = enriched(openalex("W1", authors=(author("Ada"),)))
    grace = enriched(openalex("W1", authors=(author("Grace"),)))

    result = canonicalize((grace, ada))

    assert [item.name for item in result.papers[0].authors] == ["Ada"]
    assert any(
        issue.stage == "metadata_conflict"
        and issue.message.endswith("conflict on authors")
        for issue in result.issues
    )


def test_equally_recent_snapshot_author_id_enrichment_is_not_a_conflict() -> None:
    missing = enriched(openalex("W1", authors=(author("Ada"),)))
    populated = enriched(
        openalex(
            "W1",
            authors=(author("Ada", openalex_id="https://openalex.org/A1"),),
        )
    )

    result = canonicalize((missing, populated))

    assert result.papers[0].authors[0].openalex_id == "https://openalex.org/A1"
    assert not [issue for issue in result.issues if issue.stage == "metadata_conflict"]


def test_equally_recent_snapshot_arxiv_conflict_is_visible() -> None:
    later_identifier = enriched(openalex("W1", arxiv="2601.00002"))
    earlier_identifier = enriched(openalex("W1", arxiv="2601.00001"))

    result = canonicalize((later_identifier, earlier_identifier))

    assert result.papers[0].external_ids.arxiv == "2601.00001"
    assert any(
        issue.stage == "identifier_conflict"
        and issue.message.endswith("conflict on arxiv identifier")
        for issue in result.issues
    )


def test_partial_crossref_date_precision_is_not_a_conflict_or_fabricated() -> None:
    item = enriched(
        openalex("W1", doi="10.5555/partial"),
        crossref(
            "10.5555/partial",
            dates=(
                partial_date("issued", 2026),
                partial_date("issued", 2026, 5),
            ),
        ),
    )

    result = canonicalize((item,))

    assert result.papers[0].metadata.publication_date is None
    assert not [issue for issue in result.issues if issue.stage == "date_conflict"]


def test_crossref_date_and_openalex_date_difference_is_not_metadata_conflict() -> None:
    record = openalex(
        "W1",
        doi="10.5555/date",
        publication_date=date(2026, 1, 1),
    )
    evidence = crossref(
        "10.5555/date",
        dates=(partial_date("published-print", 2026, 2, 1),),
    )

    result = canonicalize((enriched(record, evidence),))

    assert result.papers[0].metadata.publication_date == date(2026, 2, 1)
    assert not [issue for issue in result.issues if issue.stage == "metadata_conflict"]


def test_repeated_evidence_deduplicates_papers_and_provenance() -> None:
    item = enriched(
        openalex("W1", doi="10.5555/repeat"),
        crossref("10.5555/repeat"),
    )

    paper = canonicalize((item, item, item)).papers[0]

    assert [(source.provider, source.record_id) for source in paper.sources] == [
        ("crossref", "10.5555/repeat"),
        ("openalex", "https://openalex.org/W1"),
    ]


def test_reversed_input_is_semantically_order_independent() -> None:
    records = (
        enriched(openalex("W3", title="Unrelated")),
        enriched(
            openalex("W2", doi="10.5555/final", title="Final"),
        ),
        enriched(
            openalex("W1", doi="10.5555/pre", arxiv="2601.00001", title="Preprint"),
            crossref(
                "10.5555/pre",
                relations=(relation("is-preprint-of", "10.5555/final"),),
            ),
        ),
    )

    forward = canonicalize(records)
    reverse = canonicalize(tuple(reversed(records)))

    assert [paper_projection(paper) for paper in forward.papers] == [
        paper_projection(paper) for paper in reverse.papers
    ]
    assert forward.issues == reverse.issues


_AUTHOR_EQUIVALENTS = [
    ('Ada Lovelace', 'A. Lovelace'),
    ('Ada Lovelace', 'Lovelace, Ada'),
    ('Ada B. Lovelace', 'Ada B Lovelace'),
    ('Ada  Lovelace', 'Ada Lovelace'),
    ('Ａｄａ Lovelace', 'Ada Lovelace'),
    ('José Smith', 'Jose\u0301 Smith'),
    ('Ada Smith‐Jones', 'Ada Smith-Jones'),
    ('Ada Smith‑Jones', 'Ada Smith-Jones'),
]


def established_pair(layer, *, authors_left=None, authors_right=None, abstract_left=None, abstract_right=None):
    # 用额外完整度固定 representative，验证比较不会改写选中的原文。
    first = openalex('W1', doi='10.5555/equivalent', authors=authors_left,
                     abstract=abstract_left, author_keywords=('statistics',)).to_evidence()
    if layer == 'cross_provider':
        second = crossref('10.5555/equivalent', title='A Study', journal='Biometrics',
                          authors=authors_right or (author(),), abstract=abstract_right).to_evidence()
    else:
        second = openalex('W1' if layer == 'snapshot' else 'W2', doi='10.5555/equivalent',
                          authors=authors_right, abstract=abstract_right).to_evidence()
    return first, second


@pytest.mark.parametrize('layer', ['snapshot', 'same_doi', 'cross_provider'])
@pytest.mark.parametrize(('left', 'right'), _AUTHOR_EQUIVALENTS)
def test_established_identity_author_representation_equivalence(layer, left, right):
    records = established_pair(layer, authors_left=(author(left),), authors_right=(author(right),))
    result = canonicalize_records(records)
    assert not result.issues and not result.diagnostics
    assert len(result.papers) == 1
    assert result.papers[0].authors[0].name == left


def test_equivalent_author_representation_enriches_ids_without_display_churn():
    representative = openalex('W1', doi='10.5555/equivalent',
                             authors=(author('Ada Lovelace', openalex_id='A1'),)).to_evidence()
    additional = crossref('10.5555/equivalent', title='A Study', journal='Biometrics',
                         authors=(author('Lovelace, A.', orcid='O1'),)).to_evidence()
    result = canonicalize_records((representative, additional))
    assert not result.issues
    assert result.papers[0].authors == (author('Ada Lovelace', openalex_id='A1', orcid='O1'),)


@pytest.mark.parametrize('layer', ['snapshot', 'same_doi', 'cross_provider'])
@pytest.mark.parametrize(('left', 'right'), [
    ((author('Ada Lovelace'),), (author('Grace Hopper'),)),
    ((author('Ada Lovelace'),), (author('Ada Lovelace'), author('Grace Hopper'))),
    ((author('Ada Lovelace'), author('Grace Hopper')), (author('Grace Hopper'), author('Ada Lovelace'))),
    ((author('Ada Lovelace', openalex_id='A1'),), (author('A. Lovelace', openalex_id='A2'),)),
    ((author('Ada Lovelace', orcid='O1'),), (author('A. Lovelace', orcid='O2'),)),
])
def test_established_identity_genuine_author_conflicts_remain_issues(layer, left, right):
    records = established_pair(layer, authors_left=left, authors_right=right)
    result = canonicalize_records(records)
    assert any(i.stage == 'metadata_conflict' and 'authors' in i.message for i in result.issues)
    assert not result.diagnostics
    assert result.papers[0].authors == left


_ABSTRACT_EQUIVALENTS = [
    ('Methods\nWe test.', 'We test.'),
    ('Methods:\nWe test.', 'We test.'),
    ('We test.\n\nResults:\nWe find 3 effects.', 'We test.\nWe find 3 effects.'),
    ('We test.\nResults:\nWe find 3 effects.', 'We test.\nWe find 3 effects.'),
    ('BACKGROUND\nWe test.', 'We test.'),
    ('Background\nWe test.\nMethods\nWe find.', 'We test.\nWe find.'),
    ('We test.\nResults\nWe find 3 effects.', 'We test.\nWe find 3 effects.'),
    ('We test an intervention\n\nResults\nWe find 3 effects.',
     'We test an intervention\nWe find 3 effects.'),
    ('<p>We <i>find</i> 3 effects.</p>', 'We find 3 effects.'),
    ('We <i>find</i> 3 effects.', 'We find 3 effects.'),
    ('x<sub>1</sub>', 'x1'),
    ('<jats:italic>text</jats:italic>', 'text'),
    ('We<br/>find 3 effects.', 'We find 3 effects.'),
    ('We<jats:break/>find 3 effects.', 'We find 3 effects.'),
    ('<p>Use <i> as the imaginary unit.</p>', 'Use <i> as the imaginary unit.'),
    ('<p>We <em>find</em> 3 effects.</p>', 'We find 3 effects.'),
    ('<jats:p>We <jats:italic>find</jats:italic> 3 effects.</jats:p>', 'We find 3 effects.'),
    ('<p>p < 0.05 and q > 0.1</p>', 'p < 0.05 and q > 0.1'),
    ('<p>Use <T> as the statistic.</p>', 'Use <T> as the statistic.'),
    ('Use &lt;T&gt; as the statistic.', 'Use <T> as the statistic.'),
    ('<jats:abstract><jats:p>We find 3 effects.</jats:p></jats:abstract>', 'We find 3 effects.'),
    ('Effect A &amp; B is &lt; 3.', 'Effect A & B is < 3.'),
    ('We\n find\t 3 effects.', 'We find 3 effects.'),
    ('Ｆｉｎｄ café effects.', 'Find cafe\u0301 effects.'),
    ('“We find” 1–3 effects…', '"We find" 1-3 effects...'),
    ('<abstract><sec><title>Background</title><p>We test.</p></sec>'
     '<sec><title>Methods</title><p>We find 3 effects.</p></sec></abstract>',
     'We test. We find 3 effects.'),
    ('Background: We test.\nMethods: We find 3 effects.', 'We test. We find 3 effects.'),
    ('<h2>Results</h2><p>We find 3 effects.</p>', 'We find 3 effects.'),
    ('<jats:sec><jats:title>Methods</jats:title><jats:p>We test.</jats:p>'
     '<jats:p>We find 3 effects.</jats:p></jats:sec>', 'We test. We find 3 effects.'),
]


@pytest.mark.parametrize('layer', ['snapshot', 'same_doi', 'cross_provider'])
@pytest.mark.parametrize(('left', 'right'), _ABSTRACT_EQUIVALENTS)
def test_established_identity_abstract_representation_equivalence(layer, left, right):
    records = established_pair(layer, abstract_left=left, abstract_right=right)
    result = canonicalize_records(records)
    assert not result.issues and not result.diagnostics
    assert result.papers[0].metadata.abstract == left


@pytest.mark.parametrize('layer', ['snapshot', 'same_doi', 'cross_provider'])
@pytest.mark.parametrize(('left', 'right'), [
    ('We find 3 effects.', 'We find 4 effects.'),
    ('We do not find effects.', 'We do find effects.'),
    ('We find beneficial effects.', 'We find harmful effects.'),
    ('We compare Methods with Results.', 'We compare with.'),
    ('We compare\nMethods\nwith Results.', 'We compare with Results.'),
    ('A + B < 3.', 'A - B < 3.'),
    ('We test. We succeed.', 'We succeed. We test.'),
    ('Use <T> as the statistic.', 'Use as the statistic.'),
    ('Use <i> as the imaginary unit.', 'Use as the imaginary unit.'),
    ('Let <sub> denote the subgroup.', 'Let denote the subgroup.'),
    ('We compare\nMethods:\nwith Results.', 'We compare with Results.'),
    ('The <beta> coefficient changed.', 'The coefficient changed.'),
    ('Use <T>value</T> as the statistic.', 'Use value as the statistic.'),
    ('Use <beta/> as the coefficient.', 'Use as the coefficient.'),
    ('Use <science:p> as the statistic.', 'Use as the statistic.'),
])
def test_established_identity_real_abstract_differences_remain_issues(layer, left, right):
    records = established_pair(layer, abstract_left=left, abstract_right=right)
    result = canonicalize_records(records)
    assert any(i.stage == 'metadata_conflict' and 'abstract' in i.message for i in result.issues)
    assert not result.diagnostics
    assert result.papers[0].metadata.abstract == left


@pytest.mark.parametrize('raw', [
    'Use <T> as the statistic.',
    'Use <i> as the imaginary unit.',
    'Let <sub> denote the subgroup.',
    'Let </sub> denote the subgroup.',
    'The <beta> coefficient changed.',
    'Use <T>value</T> as the statistic.',
    'Use <beta/> as the coefficient.',
    'Use <science:p> as the statistic.',
    'Use\n<T>value</T> as the statistic.',
    'p < 0.05 and q > 0.1',
    'We compare\nMethods\nwith Results.',
    'We compare\nMethods:\nwith Results.',
    'We compare Methods with Results.',
    'Methods',
    'Methods:',
    'We compare\nMethods',
])
def test_abstract_comparison_preserves_scientific_angle_tokens_and_operators(raw):
    assert _normalize_abstract(raw) == ' '.join(raw.split())


def test_snapshot_attribution_union_preserves_freshness_and_creates_no_conflict():
    old = openalex("W1", title="Old title").to_evidence().model_copy(update={
        "monitor_journal_issns": ("0090-5364", "0006-341X"),
    })
    new = openalex("W1", title="Current title", retrieved_at=NOW + timedelta(days=1)).to_evidence().model_copy(update={
        "monitor_journal_issns": ("1541-0420", "0006-341X"),
    })
    for snapshots in ((old, new), (new, old)):
        normalized, issues = _normalize_retrievals(snapshots)
        selected, = normalized
        assert selected.title == "Current title" and selected.provenance == new.provenance
        assert selected.monitor_journal_issns == ("0006-341X", "0090-5364", "1541-0420")
        assert not issues
        result = canonicalize_records(snapshots)
        assert result.papers[0].journal_issns == selected.monitor_journal_issns
        assert not result.issues and not result.diagnostics


@pytest.mark.parametrize("different_metadata", [False, True])
def test_snapshot_tie_break_ignores_swapped_attribution(different_metadata):
    left = openalex("W1", title="Left title").to_evidence()
    right = left.model_copy(update={
        "title": "Right title" if different_metadata else left.title,
    })
    baseline = _choose_evidence((left, right))
    for identities in (("0006-341X", "0090-5364"), ("0090-5364", "0006-341X")):
        snapshots = tuple(r.model_copy(update={"monitor_journal_issns": (issn,)})
                          for r, issn in zip((left, right), identities, strict=True))
        selected = _choose_evidence(snapshots)
        assert selected.model_dump(exclude={"monitor_journal_issns"}) == baseline.model_dump(exclude={"monitor_journal_issns"})
        _, issues = _normalize_retrievals(snapshots)
        _, baseline_issues = _normalize_retrievals((left, right))
        assert issues == baseline_issues


def test_attribution_disagreement_alone_has_no_snapshot_or_metadata_diagnostics():
    record = openalex("W1").to_evidence()
    snapshots = tuple(record.model_copy(update={"monitor_journal_issns": ids})
                      for ids in (("0090-5364",), ("0006-341X",), ()))
    result = canonicalize_records(snapshots)
    assert result.papers[0].journal_issns == ("0006-341X", "0090-5364")
    assert not result.issues and not result.diagnostics


@pytest.mark.parametrize("case", [
    "single", "doi", "external_id", "title_author", "raw_relation", "conflicting_doi", "conflicting_authors",
])
def test_attribution_is_component_union_and_does_not_change_domain_decisions(case):
    first = openalex("W1", doi="10.5555/first").to_evidence()
    second = crossref("10.5555/first", title="A Study", journal="Biometrics", authors=(author(),)).to_evidence()
    if case == "single":
        records = (first,)
    elif case == "external_id":
        records = (first.model_copy(update={"external_ids": ExternalIds(arxiv="2601.00001")}),
                   second.model_copy(update={"external_ids": ExternalIds(arxiv="2601.00001")}))
    elif case == "title_author":
        records = (first.model_copy(update={"external_ids": ExternalIds(openalex="W1")}),
                   second.model_copy(update={"external_ids": ExternalIds()}))
    elif case == "raw_relation":
        records = (
            first,
            crossref("10.5555/pre", title="Preprint title", journal="Biometrics", authors=(author(),),
                     relations=(relation("is-preprint-of", "10.5555/first"),)).to_evidence(),
        )
    elif case == "conflicting_doi":
        records = (first, crossref("10.5555/other", title="A Study", journal="Biometrics", authors=(author(),)).to_evidence())
    elif case == "conflicting_authors":
        records = (first.model_copy(update={"external_ids": ExternalIds()}),
                   second.model_copy(update={"external_ids": ExternalIds(), "authors": (author("Other Author"),)}))
    else:
        records = (first, second)

    baseline = canonicalize_records(records)
    baseline_clusters = consolidate_evidence(records)
    tagged = tuple(record.model_copy(update={"monitor_journal_issns": ids})
                   for record, ids in zip(records, (("0006-341X", "0006-341X"), ("0090-5364", "0006-341X"))))
    for inputs in (tagged, tuple(reversed(tagged))):
        clusters = consolidate_evidence(inputs)
        assert len(clusters.clusters) == len(baseline_clusters.clusters)
        assert clusters.issues == baseline_clusters.issues and clusters.diagnostics == baseline_clusters.diagnostics
        for cluster, original in zip(clusters.clusters, baseline_clusters.clusters, strict=True):
            assert [e.model_dump(exclude={"monitor_journal_issns"}) for e in cluster.evidence] == [
                e.model_dump(exclude={"monitor_journal_issns"}) for e in original.evidence
            ]
            assert build_searchable_projection(cluster.evidence) == build_searchable_projection(original.evidence)
        result = canonicalize_records(inputs)
        assert result.issues == baseline.issues and result.diagnostics == baseline.diagnostics
        doi_clusters = [cluster for cluster in clusters.clusters if cluster.evidence[0].external_ids.doi]
        for paper, original, cluster in zip(result.papers, baseline.papers, doi_clusters, strict=True):
            assert paper.model_dump(exclude={"id", "workflow", "journal_issns"}) == original.model_dump(exclude={"id", "workflow", "journal_issns"})
            assert paper.workflow.status == original.workflow.status
            assert paper.journal_issns == tuple(sorted({issn for e in cluster.evidence for issn in e.monitor_journal_issns}))
        if case == "doi":
            assert len(result.papers) == 1
            assert result.papers[0].journal_issns == ("0006-341X", "0090-5364")
        elif case in {"external_id", "title_author", "conflicting_authors"}:
            assert not result.papers
            assert all(issue.stage == "missing_doi" for issue in result.issues)
        elif case in {"raw_relation", "conflicting_doi"}:
            assert len(result.papers) == 2
    assert "journal_issns" not in CanonicalMetadata.model_fields
    assert "monitor_journal_issns" not in CanonicalMetadata.model_fields


def test_sparse_same_doi_evidence_contributes_without_being_representative():
    complete = openalex("W-complete", doi="10.5555/sparse").to_evidence()
    sparse = ProviderWorkEvidence(
        provenance=MetadataSource(provider="crossref", record_id="10.5555/sparse", retrieved_at=NOW),
        external_ids=ExternalIds(doi="HTTPS://DOI.ORG/10.5555/SPARSE", crossref="10.5555/sparse"),
        abstract="Sparse source abstract",
        dates=(EvidenceDate(kind="published-print", year=2026, month=8, day=4),),
        monitor_journal_issns=("0006-341X",),
    )
    for inputs in ((complete, sparse), (sparse, complete)):
        result = canonicalize_records(inputs)
        assert not result.issues and not result.diagnostics
        paper, = result.papers
        assert paper.doi == "10.5555/sparse"
        assert paper.metadata.title == complete.title
        assert paper.metadata.abstract == sparse.abstract
        assert paper.metadata.publication_date == date(2026, 8, 4)
        assert paper.external_ids.crossref == "10.5555/sparse"
        assert {source.provider for source in paper.sources} == {"openalex", "crossref"}
        assert paper.journal_issns == ("0006-341X",)


@pytest.mark.parametrize("relation_type", [
    "is-preprint-of", "has-preprint", "is-manuscript-of", "has-manuscript",
    "is-version-of", "has-version", "is-identical-to", "references",
])
def test_raw_crossref_relations_never_merge_or_cross_enrich_dois(relation_type):
    left = crossref(
        "10.5555/left", title="A Study", journal="Biometrics", authors=(author(),),
        relations=(relation(relation_type, "10.5555/right"),),
    )
    right = crossref(
        "10.5555/right", title="A Study", journal="Biometrics",
        abstract="Right DOI abstract", authors=(author(openalex_id="A-right"),),
        dates=(partial_date("published-print", 2026, 6, 1),),
    )
    baseline = left.model_copy(update={"relations": ()}).to_evidence()
    assert left.relations and left.to_evidence() == baseline
    for inputs in ((left.to_evidence(), right.to_evidence()), (right.to_evidence(), left.to_evidence())):
        result = canonicalize_records(inputs)
        assert not result.issues and not result.diagnostics
        assert [paper.doi for paper in result.papers] == ["10.5555/left", "10.5555/right"]
        first, second = result.papers
        assert first.metadata.abstract is None and first.metadata.publication_date is None
        assert first.authors[0].openalex_id is None
        assert second.metadata.abstract == "Right DOI abstract"
        assert second.metadata.publication_date == date(2026, 6, 1)
        assert {s.record_id for s in first.sources} == {"10.5555/left"}


@pytest.mark.parametrize("same_provider_record", [False, True])
def test_shared_non_doi_identifiers_and_supplements_cannot_join_distinct_dois(same_provider_record):
    first = openalex("W1", doi="10.5555/first", arxiv="2601.00001").to_evidence()
    second = first.model_copy(update={
        "external_ids": first.external_ids.model_copy(update={"doi": "10.5555/second"}),
        "provenance": first.provenance if same_provider_record else MetadataSource(
            provider="crossref", record_id="shared-crossref", retrieved_at=NOW,
        ),
        "supplements": (ProviderRecordRef(provider="openalex", record_id=first.provenance.record_id),),
    })
    for inputs in ((first, second), (second, first)):
        clusters = consolidate_evidence(inputs)
        assert len(clusters.clusters) == 2
        result = canonicalize_records(inputs)
        assert [paper.doi for paper in result.papers] == ["10.5555/first", "10.5555/second"]
        assert not result.issues and not result.diagnostics


@pytest.mark.parametrize("doi", [None, "not-a-doi", "https://doi.org/"])
def test_doi_less_and_invalid_identity_cannot_create_papers_even_with_arxiv(doi):
    records = tuple(openalex(f"W{i}", doi=doi, arxiv="2601.00001").to_evidence() for i in range(2))
    forward = consolidate_evidence(records)
    assert len(forward.clusters) == 2
    assert all(len(cluster.evidence) == 1 for cluster in forward.clusters)
    assert not forward.issues and not forward.diagnostics
    baseline = canonicalize_records(records)
    reversed_result = canonicalize_records(tuple(reversed(records)))
    assert not baseline.papers and baseline.issues == reversed_result.issues
    assert len(baseline.issues) == 2 and all(issue.stage == "missing_doi" for issue in baseline.issues)


def test_doi_less_title_author_and_identifier_matches_never_bridge_or_enrich():
    first = openalex("W1", doi="10.5555/first", arxiv="shared").to_evidence()
    second = openalex("W2", doi="10.5555/second", arxiv="shared").to_evidence()
    bridge = openalex("W3", doi=None, arxiv="shared", abstract="Unsupported enrichment").to_evidence()
    for inputs in ((first, bridge, second), (second, bridge, first)):
        consolidated = consolidate_evidence(inputs)
        assert len(consolidated.clusters) == 3 and not consolidated.diagnostics
        result = canonicalize_records(inputs)
        assert [paper.doi for paper in result.papers] == ["10.5555/first", "10.5555/second"]
        assert all(paper.metadata.abstract is None for paper in result.papers)
        issue, = result.issues
        assert issue.stage == "missing_doi" and issue.record_ids == (bridge.provenance.record_id,)


@pytest.mark.parametrize(("higher", "lower"), [
    ("published-print", "published-online"),
    ("published-online", "published"),
    ("published", "issued"),
    ("issued", None),
])
def test_direct_doi_date_precedence_is_independent_of_input_order(higher, lower):
    representative = openalex("W1", doi="10.5555/date", publication_date=date(2026, 12, 1)).to_evidence()
    preferred = representative.model_copy(update={
        "provenance": MetadataSource(provider="crossref", record_id="preferred", retrieved_at=NOW),
        "dates": (EvidenceDate(kind=higher, year=2026, month=8, day=2),),
    })
    dates = () if lower is None else (EvidenceDate(kind=lower, year=2025, month=1, day=1),)
    other = representative.model_copy(update={"dates": dates})
    for inputs in ((preferred, other), (other, preferred)):
        result = canonicalize_records(inputs)
        assert result.papers[0].metadata.publication_date == date(2026, 8, 2)
        assert not result.issues


@pytest.mark.parametrize("precision", [None, 4])
def test_partial_higher_date_tier_allows_next_complete_tier_without_invention(precision):
    record = openalex("W1", doi="10.5555/date", publication_date=date(2026, 12, 1)).to_evidence()
    record = record.model_copy(update={"dates": (
        EvidenceDate(kind="published-print", year=2026, month=precision),
        EvidenceDate(kind="published-online", year=2026, month=5, day=2),
    )})
    result = canonicalize_records((record,))
    assert result.papers[0].metadata.publication_date == date(2026, 5, 2)
    assert not result.issues


@pytest.mark.parametrize("kind", list(EvidenceDateKind))
def test_complete_date_wins_compatible_partial_and_equivalent_duplicates(kind):
    record = openalex("W1", doi="10.5555/date").to_evidence().model_copy(update={"dates": (
        EvidenceDate(kind=kind, year=2026),
        EvidenceDate(kind=kind, year=2026, month=5),
        EvidenceDate(kind=kind, year=2026, month=5, day=2),
        EvidenceDate(kind=kind, year=2026, month=5, day=2),
    )})
    result = canonicalize_records((record,))
    assert result.papers[0].metadata.publication_date == date(2026, 5, 2)
    assert not result.issues


@pytest.mark.parametrize("kind", list(EvidenceDateKind))
def test_distinct_complete_same_tier_dates_choose_earliest_and_report_deterministically(kind):
    records = tuple(
        openalex(f"W{day}", doi="10.5555/date").to_evidence().model_copy(update={
            "dates": (EvidenceDate(kind=kind, year=2026, month=5, day=day),),
        }) for day in (9, 2, 9)
    )
    forward = canonicalize_records(records)
    reverse = canonicalize_records(tuple(reversed(records)))
    assert paper_projection(forward.papers[0]) == paper_projection(reverse.papers[0])
    assert forward.papers[0].metadata.publication_date == date(2026, 5, 2)
    assert forward.issues == reverse.issues
    issue, = forward.issues
    assert issue.stage == "date_conflict" and kind.value in issue.message
    assert "2026-05-02, 2026-05-09" in issue.message


@pytest.mark.parametrize("fallback", [None, date(2026, 9, 1)])
def test_only_partial_evidence_uses_representative_date_never_retrieval_or_revision(fallback):
    raw = crossref(
        "10.5555/date", dates=tuple(partial_date(kind.value, 2025) for kind in EvidenceDateKind),
        retrieved_at=NOW + timedelta(days=1),
    ).model_copy(update={"indexed_at": NOW + timedelta(days=2)})
    representative = openalex("W1", doi="10.5555/date", publication_date=fallback).to_evidence()
    for inputs in ((representative, raw.to_evidence()), (raw.to_evidence(), representative)):
        result = canonicalize_records(inputs)
        assert result.papers[0].metadata.publication_date == fallback
        assert not result.issues


def test_issued_full_date_is_used_after_all_higher_tiers_only_supply_partial_evidence():
    record = openalex("W1", doi="10.5555/date", publication_date=date(2026, 12, 1)).to_evidence()
    record = record.model_copy(update={"dates": (
        EvidenceDate(kind="published-print", year=2026),
        EvidenceDate(kind="published-online", year=2026, month=5),
        EvidenceDate(kind="published", year=2026),
        EvidenceDate(kind="issued", year=2026, month=6, day=3),
    )})
    result = canonicalize_records((record,))
    assert result.papers[0].metadata.publication_date == date(2026, 6, 3)
    assert not result.issues


@pytest.mark.parametrize("missing", ["title", "journal", "authors"])
def test_doi_component_still_requires_a_bibliographically_eligible_representative(missing):
    record = openalex("W1", doi="10.5555/incomplete").to_evidence()
    record = record.model_copy(update={missing: () if missing == "authors" else None})
    result = canonicalize_records((record,))
    assert not result.papers
    issue, = result.issues
    assert issue.stage == "insufficient_metadata"
    assert issue.record_ids == (record.provenance.record_id,)
