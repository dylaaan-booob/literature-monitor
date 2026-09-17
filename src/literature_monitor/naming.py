"""Stable internal identity and creation-time Markdown filename helpers."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from uuid import UUID, uuid4


def create_internal_id() -> UUID:
    return uuid4()


def slugify_title(title: str, max_length: int = 80) -> str:
    if max_length < 1:
        raise ValueError("max_length must be positive")

    normalized = unicodedata.normalize("NFKC", title).casefold()
    characters: list[str] = []
    pending_separator = False
    for character in normalized:
        if character.isalnum():
            if pending_separator and characters:
                characters.append("-")
            characters.append(character)
            pending_separator = False
        else:
            pending_separator = True

    slug = "".join(characters)[:max_length].rstrip("-")
    return slug or "paper"


def short_uuid(
    paper_id: UUID,
    occupied_ids: Iterable[UUID] = (),
    min_length: int = 8,
) -> str:
    if not 1 <= min_length <= 32:
        raise ValueError("min_length must be between 1 and 32")

    other_hex_ids = {value.hex for value in occupied_ids if value != paper_id}
    for length in range(min_length, 33):
        prefix = paper_id.hex[:length]
        if not any(value.startswith(prefix) for value in other_hex_ids):
            return prefix
    raise ValueError("cannot disambiguate identical UUID values")


def paper_filename(
    title: str,
    paper_id: UUID,
    occupied_ids: Iterable[UUID] = (),
) -> str:
    return f"{slugify_title(title)}--{short_uuid(paper_id, occupied_ids)}.md"
