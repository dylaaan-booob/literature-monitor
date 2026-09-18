"""Shared external-identifier normalization."""

from __future__ import annotations

from typing import Any


def normalize_doi(value: Any) -> str | None:
    """Normalize a DOI without imposing syntax beyond the existing provider contract."""

    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid DOI")
    doi = value.strip()
    prefix = "https://doi.org/"
    if doi.casefold().startswith(prefix):
        doi = doi[len(prefix) :].strip()
    if not doi:
        return None
    return doi.casefold()
