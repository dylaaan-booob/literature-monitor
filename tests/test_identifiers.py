import pytest

from literature_monitor.identifiers import normalize_doi


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (" 10.1234/ABC ", "10.1234/abc"),
        ("HTTPS://DOI.ORG/10.1234/ABC", "10.1234/abc"),
        ("https://doi.org/   ", None),
        ("not-a-doi", "not-a-doi"),
    ],
)
def test_normalize_doi_preserves_existing_openalex_semantics(
    value: object, expected: str | None
) -> None:
    assert normalize_doi(value) == expected


@pytest.mark.parametrize("value", ["", "   ", 123, object()])
def test_normalize_doi_rejects_existing_invalid_inputs(value: object) -> None:
    with pytest.raises(ValueError, match="invalid DOI"):
        normalize_doi(value)
