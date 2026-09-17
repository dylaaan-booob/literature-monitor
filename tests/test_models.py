from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from literature_monitor.models import (
    Author,
    CanonicalMetadata,
    CanonicalPaper,
    ExternalIds,
    MetadataSource,
    PaperVersion,
    VersionKind,
    VersionRef,
    WorkflowStatus,
)


def make_paper(**overrides: object) -> CanonicalPaper:
    values: dict[str, object] = {
        "metadata": CanonicalMetadata(title="A Paper", journal="A Journal"),
        "authors": (Author(name="Ada Author"),),
    }
    values.update(overrides)
    return CanonicalPaper(**values)


def test_minimal_paper_generates_uuid4_and_defaults() -> None:
    paper = make_paper()

    assert paper.id.version == 4
    assert paper.workflow.status is WorkflowStatus.CANDIDATE
    assert paper.workflow.discovered_at.utcoffset() is not None
    assert paper.metadata.abstract is None


def test_existing_uuid_and_nested_data_survive_json_round_trip() -> None:
    paper_id = uuid4()
    version = PaperVersion(
        kind=VersionKind.PREPRINT,
        source="arxiv",
        identifier="2401.00001",
        url="https://arxiv.org/abs/2401.00001",
        date=date(2024, 1, 1),
    )
    paper = make_paper(
        id=paper_id,
        external_ids=ExternalIds(doi="10.1000/example", semantic_scholar="abc"),
        versions=(version,),
        preferred_version=VersionRef(source="arxiv", identifier="2401.00001"),
        sources=(
            MetadataSource(
                provider="openalex",
                record_id="W1",
                retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
        ),
    )

    restored = CanonicalPaper.model_validate_json(paper.model_dump_json())

    assert restored == paper
    assert restored.id == paper_id
    assert restored.doi == "10.1000/example"
    assert restored.external_ids.model_extra == {"semantic_scholar": "abc"}


@pytest.mark.parametrize(
    ("metadata", "authors"),
    [
        ({"title": "", "journal": "Journal"}, ({"name": "Author"},)),
        ({"title": "Title", "journal": ""}, ({"name": "Author"},)),
        ({"title": "Title", "journal": "Journal"}, ()),
    ],
)
def test_required_canonical_fields_are_enforced(
    metadata: dict[str, str], authors: tuple[dict[str, str], ...]
) -> None:
    with pytest.raises(ValidationError):
        CanonicalPaper(metadata=metadata, authors=authors)


def test_existing_non_uuid4_identity_is_preserved() -> None:
    paper_id = UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

    assert make_paper(id=paper_id).id == paper_id


def test_duplicate_versions_are_rejected() -> None:
    version = PaperVersion(kind="preprint", source="arxiv", identifier="1")
    with pytest.raises(ValidationError, match="duplicate"):
        make_paper(versions=(version, version))


def test_preferred_version_must_reference_existing_version() -> None:
    with pytest.raises(ValidationError, match="existing version"):
        make_paper(preferred_version=VersionRef(source="arxiv", identifier="missing"))


def test_provenance_and_workflow_datetimes_require_timezones() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        MetadataSource(provider="openalex", record_id="W1", retrieved_at=datetime(2026, 1, 1))

    with pytest.raises(ValidationError, match="timezone"):
        make_paper(workflow={"discovered_at": datetime(2026, 1, 1)})


@pytest.mark.parametrize(
    "identifiers",
    [
        {"doi": " "},
        {"custom": None},
        {"custom": 42},
        {"": "value"},
        {" ": "value"},
    ],
)
def test_external_identifier_names_and_values_must_be_nonempty_strings(
    identifiers: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="non-empty"):
        ExternalIds.model_validate(identifiers)


def test_external_identifier_names_and_values_are_trimmed() -> None:
    identifiers = ExternalIds.model_validate({" custom ": " value "})

    assert identifiers.model_extra == {"custom": "value"}
