from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

from literature_monitor.canonicalize import (
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
    ExternalIds,
    MetadataSource,
    ProviderWorkEvidence,
    VersionKind,
)
from literature_monitor.openalex import (
    OpenAlexVersion,
    OpenAlexVersionHint,
    OpenAlexWorkRecord,
)


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
    doi: str | None = None,
    arxiv: str | None = None,
    authors: tuple[Author, ...] | None = None,
    publication_date: date | None = None,
    abstract: str | None = None,
    author_keywords: tuple[str, ...] = (),
    retrieved_at: datetime = NOW,
    external_ids: dict[str, str] | None = None,
    version_hints: tuple[OpenAlexVersionHint, ...] = (),
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
        version_hints=version_hints,
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
        external_ids=ExternalIds.model_validate({"independent": "work-1"}),
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
    assert any(issue.stage == "identifier_conflict" for issue in result.issues)


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


def test_title_fallback_does_not_use_evidence_without_authors() -> None:
    complete = openalex("W1", title="Shared title").to_evidence()
    partial = ProviderWorkEvidence(
        provenance=MetadataSource(
            provider="crossref",
            record_id="record-without-authors",
            retrieved_at=NOW,
        ),
        title="Shared title",
        journal="Biometrics",
        external_ids=ExternalIds(crossref="record-without-authors"),
    )

    result = canonicalize_records((complete, partial))

    assert len(result.papers) == 1
    assert result.papers[0].external_ids.crossref is None
    assert any(issue.stage == "insufficient_metadata" for issue in result.issues)


def test_exact_normalized_doi_merges_and_deduplicates_one_version() -> None:
    result = canonicalize(
        (
            enriched(openalex("W2", doi="https://doi.org/10.5555/EXAMPLE")),
            enriched(openalex("W1", doi="10.5555/example")),
        )
    )

    assert len(result.papers) == 1
    paper = result.papers[0]
    assert [(version.source, version.identifier) for version in paper.versions] == [
        ("doi", "10.5555/example")
    ]
    assert len(paper.sources) == 2


@pytest.mark.parametrize(
    ("relation_type", "subject_kind", "target_kind"),
    [
        ("is-preprint-of", VersionKind.PREPRINT, VersionKind.JOURNAL_FINAL),
        ("has-preprint", VersionKind.JOURNAL_FINAL, VersionKind.PREPRINT),
        (
            "is-manuscript-of",
            VersionKind.ACCEPTED_MANUSCRIPT,
            VersionKind.JOURNAL_FINAL,
        ),
        (
            "has-manuscript",
            VersionKind.JOURNAL_FINAL,
            VersionKind.ACCEPTED_MANUSCRIPT,
        ),
    ],
)
def test_directional_relation_roles_are_applied_to_subject_and_target(
    relation_type: str,
    subject_kind: VersionKind,
    target_kind: VersionKind,
) -> None:
    subject = openalex("W1", doi="10.5555/subject", title="Subject")
    target = openalex("W2", doi="10.5555/target", title="Target")
    result = canonicalize(
        (
            enriched(
                subject,
                crossref(
                    "10.5555/subject",
                    relations=(relation(relation_type, "10.5555/target"),),
                ),
            ),
            enriched(target),
        )
    )

    assert len(result.papers) == 1
    kinds = {version.identifier: version.kind for version in result.papers[0].versions}
    assert kinds == {
        "10.5555/subject": subject_kind,
        "10.5555/target": target_kind,
    }


@pytest.mark.parametrize("relation_type", ["is-version-of", "has-version"])
def test_generic_version_relations_merge_without_assigning_special_roles(
    relation_type: str,
) -> None:
    result = canonicalize(
        (
            enriched(
                openalex("W1", doi="10.5555/a", title="A"),
                crossref(
                    "10.5555/a",
                    relations=(relation(relation_type, "10.5555/b"),),
                ),
            ),
            enriched(openalex("W2", doi="10.5555/b", title="B")),
        )
    )

    assert len(result.papers) == 1
    assert {version.kind for version in result.papers[0].versions} == {
        VersionKind.JOURNAL_FINAL
    }


def test_is_identical_to_retains_different_discovered_version_keys() -> None:
    result = canonicalize(
        (
            enriched(
                openalex("W1", doi="10.5555/a", title="A"),
                crossref(
                    "10.5555/a",
                    relations=(relation("is-identical-to", "10.5555/b"),),
                ),
            ),
            enriched(openalex("W2", doi="10.5555/b", title="B")),
        )
    )

    assert len(result.papers) == 1
    assert {version.identifier for version in result.papers[0].versions} == {
        "10.5555/a",
        "10.5555/b",
    }


def test_relation_target_uses_matching_external_identifier_namespace() -> None:
    subject = enriched(
        openalex("W1", doi="10.5555/a", title="A"),
        crossref(
            "10.5555/a",
            relations=(relation("is-version-of", "123", id_type="pmid"),),
        ),
    )
    pmid_target = enriched(
        openalex("W2", doi="10.5555/b", title="B", external_ids={"pmid": "123"})
    )
    other_namespace = enriched(
        openalex("W3", title="C", arxiv="123")
    )

    result = canonicalize((subject, pmid_target, other_namespace))

    assert len(result.papers) == 2
    assert {len(paper.versions) for paper in result.papers} == {1, 2}


def test_nonversion_and_dangling_relations_do_not_invent_versions() -> None:
    references = enriched(
        openalex("W1", doi="10.5555/a", title="A"),
        crossref(
            "10.5555/a",
            relations=(relation("references", "10.5555/b"),),
        ),
    )
    unrelated = enriched(openalex("W2", doi="10.5555/b", title="B"))
    dangling = enriched(
        openalex("W3", doi="10.5555/c", title="C"),
        crossref(
            "10.5555/c",
            relations=(relation("is-preprint-of", "10.5555/missing"),),
        ),
    )

    result = canonicalize((references, unrelated, dangling))

    assert len(result.papers) == 3
    dangling_paper = next(
        paper for paper in result.papers if paper.external_ids.openalex.endswith("W3")
    )
    assert len(dangling_paper.versions) == 1
    assert dangling_paper.versions[0].kind is VersionKind.PREPRINT


def test_title_and_name_fallback_merges_only_when_no_stable_ids_exist() -> None:
    result = canonicalize(
        (
            enriched(openalex("W2", title="  The  Study ", authors=(author("Ada  Author"),))),
            enriched(openalex("W1", title="the study", authors=(author("ada author"),))),
        )
    )

    assert len(result.papers) == 1


def test_matching_stable_author_ids_are_compatible() -> None:
    stable = "https://openalex.org/A1"
    result = canonicalize(
        (
            enriched(openalex("W1", authors=(author("Ada", openalex_id=stable),))),
            enriched(openalex("W2", authors=(author("A. Author", openalex_id=stable),))),
        )
    )

    assert len(result.papers) == 1


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (author("Ada", openalex_id="A1"), author("Ada")),
        (author("Ada", openalex_id="A1"), author("Ada", openalex_id="A2")),
        (author("Ada", openalex_id="A1"), author("Ada", orcid="O1")),
        (author("Ada", orcid="O1"), author("Ada", orcid="O2")),
    ],
)
def test_insufficient_or_conflicting_stable_author_evidence_blocks_fallback(
    left: Author,
    right: Author,
) -> None:
    result = canonicalize(
        (
            enriched(openalex("W1", authors=(left,))),
            enriched(openalex("W2", authors=(right,))),
        )
    )

    assert len(result.papers) == 2
    assert [issue.stage for issue in result.issues] == ["blocked_match"]


def test_different_author_counts_block_title_fallback() -> None:
    result = canonicalize(
        (
            enriched(openalex("W1", authors=(author("Ada"),))),
            enriched(openalex("W2", authors=(author("Ada"), author("Grace")))),
        )
    )

    assert len(result.papers) == 2
    assert [issue.stage for issue in result.issues] == ["blocked_match"]


def test_different_titles_do_not_create_dedup_issue() -> None:
    result = canonicalize(
        (enriched(openalex("W1", title="One")), enriched(openalex("W2", title="Two")))
    )

    assert len(result.papers) == 2
    assert not result.issues


def test_conflicting_doi_components_are_not_fallback_merged() -> None:
    result = canonicalize(
        (
            enriched(openalex("W1", doi="10.5555/a")),
            enriched(openalex("W2", doi="10.5555/b")),
        )
    )

    assert len(result.papers) == 2
    assert [issue.stage for issue in result.issues] == ["identifier_conflict"]


def test_doi_free_bridge_cannot_transitively_merge_conflicting_dois() -> None:
    result = canonicalize(
        (
            enriched(openalex("W1", doi="10.5555/a")),
            enriched(openalex("W2")),
            enriched(openalex("W3", doi="10.5555/c")),
        )
    )

    assert len(result.papers) == 2
    assert any(issue.stage == "identifier_conflict" for issue in result.issues)


def test_journal_origin_without_crossref_is_final() -> None:
    paper = canonicalize((enriched(openalex("W1")),)).papers[0]

    assert paper.versions[0].kind is VersionKind.JOURNAL_FINAL


def test_journal_without_crossref_is_preferred_over_related_preprint() -> None:
    preprint = enriched(
        openalex("W1", doi="10.5555/pre", arxiv="2601.00001", title="Preprint"),
        crossref(
            "10.5555/pre",
            relations=(relation("is-preprint-of", "10.5555/final"),),
        ),
    )
    journal = enriched(openalex("W2", doi="10.5555/final", title="Final"))

    paper = canonicalize((preprint, journal)).papers[0]

    assert paper.preferred_version is not None
    assert paper.preferred_version.identifier == "10.5555/final"
    assert paper.metadata.title == "Final"


def test_nonpreferred_version_cross_provider_conflict_remains_visible() -> None:
    preprint = enriched(
        openalex(
            "W1",
            doi="10.5555/pre",
            arxiv="2601.00001",
            title="OpenAlex preprint",
        ),
        crossref(
            "10.5555/pre",
            title="Crossref preprint",
            relations=(relation("is-preprint-of", "10.5555/final"),),
        ),
    )
    final = enriched(
        openalex("W2", doi="10.5555/final", title="Final title")
    )

    result = canonicalize((preprint, final))

    assert result.papers[0].metadata.title == "Final title"
    assert any(
        issue.stage == "metadata_conflict"
        and "OpenAlex and Crossref conflict on nonempty title" in issue.message
        for issue in result.issues
    )


def test_online_evidence_outweighs_generic_published_for_kind() -> None:
    record = openalex("W1", doi="10.5555/online")
    evidence = crossref(
        "10.5555/online",
        dates=(
            partial_date("published-online", 2026, 4, 2),
            partial_date("published", 2026, 4, 8),
        ),
    )

    version = canonicalize((enriched(record, evidence),)).papers[0].versions[0]

    assert version.kind is VersionKind.JOURNAL_ONLINE
    assert version.date == date(2026, 4, 2)


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
    versions = {
        (version.source, version.identifier): version
        for version in forward.papers[0].versions
    }
    assert versions[("arxiv", "2601.00001")].kind is VersionKind.PREPRINT
    assert versions[("arxiv", "2601.00001")].date == date(2026, 5, 1)
    assert versions[("doi", "10.5555/same")].kind is VersionKind.JOURNAL_ONLINE
    assert versions[("doi", "10.5555/same")].date == date(2026, 5, 1)


def test_one_openalex_work_builds_distinct_inline_location_versions() -> None:
    record = openalex(
        "W1",
        doi="https://doi.org/10.5555/FINAL",
        publication_date=date(2026, 9, 1),
        version_hints=(
            OpenAlexVersionHint(
                source="doi",
                identifier="10.5555/final",
                version=OpenAlexVersion.PUBLISHED,
                url="https://doi.org/10.5555/final",
            ),
            OpenAlexVersionHint(
                source="arxiv",
                identifier="2601.01234",
                version=OpenAlexVersion.SUBMITTED,
                url="https://arxiv.org/abs/2601.01234",
            ),
            OpenAlexVersionHint(
                source="openalex_location",
                identifier="pmh:oai:repository.example:item-1",
                version=OpenAlexVersion.ACCEPTED,
                url="https://repository.example/item-1",
            ),
        ),
    )
    evidence = crossref(
        "10.5555/final",
        dates=(partial_date("published-online", 2026, 8, 15),),
    )

    paper = canonicalize((enriched(record, evidence),)).papers[0]

    versions = {
        (version.source, version.identifier): version for version in paper.versions
    }
    assert set(versions) == {
        ("arxiv", "2601.01234"),
        ("doi", "10.5555/final"),
        ("openalex_location", "pmh:oai:repository.example:item-1"),
    }
    assert versions[("doi", "10.5555/final")].kind is VersionKind.JOURNAL_ONLINE
    assert versions[("doi", "10.5555/final")].date == date(2026, 8, 15)
    assert versions[("arxiv", "2601.01234")].kind is VersionKind.PREPRINT
    assert versions[("arxiv", "2601.01234")].date is None
    assert (
        versions[("openalex_location", "pmh:oai:repository.example:item-1")].kind
        is VersionKind.ACCEPTED_MANUSCRIPT
    )
    assert (
        versions[("openalex_location", "pmh:oai:repository.example:item-1")].date
        is None
    )
    assert paper.external_ids.doi == "https://doi.org/10.5555/FINAL"
    assert paper.external_ids.arxiv is None
    assert paper.preferred_version is not None
    assert (paper.preferred_version.source, paper.preferred_version.identifier) == (
        "doi",
        "10.5555/final",
    )


def test_partial_online_date_classifies_without_fabricating_version_date() -> None:
    record = openalex("W1", doi="10.5555/online")
    evidence = crossref(
        "10.5555/online",
        dates=(partial_date("published-online", 2026, 4),),
    )

    version = canonicalize((enriched(record, evidence),)).papers[0].versions[0]

    assert version.kind is VersionKind.JOURNAL_ONLINE
    assert version.date is None


def test_print_evidence_outweighs_online_for_kind_and_date() -> None:
    record = openalex("W1", doi="10.5555/final")
    evidence = crossref(
        "10.5555/final",
        dates=(
            partial_date("published-online", 2026, 3, 1),
            partial_date("published-print", 2026, 5, 1),
        ),
    )

    version = canonicalize((enriched(record, evidence),)).papers[0].versions[0]

    assert version.kind is VersionKind.JOURNAL_FINAL
    assert version.date == date(2026, 5, 1)


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

    assert result.papers[0].versions[0].date == date(2026, 5, 1)
    assert any(issue.stage == "date_conflict" for issue in result.issues)


def test_same_kind_prefers_latest_dated_version_then_stable_identifier() -> None:
    first = enriched(
        openalex("W1", doi="10.5555/a", arxiv="2601.00001", publication_date=date(2026, 1, 1)),
        crossref(
            "10.5555/a",
            relations=(relation("is-identical-to", "10.5555/b"),),
        ),
    )
    second = enriched(
        openalex("W2", doi="10.5555/b", arxiv="2602.00001", publication_date=date(2026, 2, 1))
    )

    paper = canonicalize((first, second)).papers[0]

    assert paper.preferred_version is not None
    assert paper.preferred_version.identifier == "2602.00001"

    tied = canonicalize(
        (
            enriched(
                openalex("W3", doi="10.5555/c", arxiv="2603.00002"),
                crossref(
                    "10.5555/c",
                    relations=(relation("is-identical-to", "10.5555/d"),),
                ),
            ),
            enriched(openalex("W4", doi="10.5555/d", arxiv="2603.00001")),
        )
    ).papers[0]
    assert tied.preferred_version is not None
    assert tied.preferred_version.identifier == "2603.00001"


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


def test_preferred_manifestation_supplies_metadata_authors_and_external_ids() -> None:
    preprint = enriched(
        openalex(
            "W1",
            title="Preprint title",
            doi="10.5555/pre",
            arxiv="2601.00001",
            authors=(author("Preprint Author"),),
        ),
        crossref(
            "10.5555/pre",
            relations=(relation("is-preprint-of", "10.5555/final"),),
        ),
    )
    final = enriched(
        openalex(
            "W2",
            title="Final title",
            doi="10.5555/final",
            authors=(author("Final Author"),),
        )
    )

    paper = canonicalize((preprint, final)).papers[0]

    assert paper.metadata.title == "Final title"
    assert [item.name for item in paper.authors] == ["Final Author"]
    assert paper.external_ids.doi == "10.5555/final"
    assert {version.identifier for version in paper.versions} == {
        "2601.00001",
        "10.5555/final",
    }


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


def test_same_version_openalex_conflicts_are_visible_after_deterministic_selection() -> None:
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
    assert len(result.papers[0].versions) == 1
    assert result.papers[0].metadata.title == "A title"
    assert [item.name for item in result.papers[0].authors] == ["Ada"]
    conflicts = [issue for issue in result.issues if issue.stage == "metadata_conflict"]
    assert {issue.message.rsplit(" ", maxsplit=1)[-1] for issue in conflicts} >= {
        "title",
        "authors",
    }
    assert not any("OpenAlex and OpenAlex" in issue.message for issue in conflicts)


def test_same_version_crossref_conflict_is_visible_after_deterministic_selection() -> None:
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
    assert len(result.papers[0].versions) == 1
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

    assert result.papers[0].versions[0].identifier == "2601.00001"
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

    assert result.papers[0].versions[0].date is None
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


def test_repeated_evidence_deduplicates_versions_and_provenance() -> None:
    item = enriched(
        openalex("W1", doi="10.5555/repeat"),
        crossref("10.5555/repeat"),
    )

    paper = canonicalize((item, item, item)).papers[0]

    assert len(paper.versions) == 1
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
