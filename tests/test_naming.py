from uuid import UUID

import pytest

from literature_monitor.naming import create_internal_id, paper_filename, short_uuid, slugify_title


def test_internal_id_is_uuid4() -> None:
    assert create_internal_id().version == 4


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("  A Study: High-Dimensional Data!  ", "a-study-high-dimensional-data"),
        ("统计学习 / 方法", "统计学习-方法"),
        ("***", "paper"),
        ("ＡＢＣ", "abc"),
    ],
)
def test_slugify_title(title: str, expected: str) -> None:
    assert slugify_title(title) == expected


def test_slug_is_capped_without_trailing_separator() -> None:
    assert slugify_title("a" * 79 + " - b", max_length=80) == "a" * 79


def test_short_uuid_extends_only_when_prefix_is_occupied() -> None:
    paper_id = UUID("12345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    colliding = UUID("12345678-bbbb-4bbb-8bbb-bbbbbbbbbbbb")

    assert short_uuid(paper_id) == "12345678"
    assert short_uuid(paper_id, (colliding,)) == "12345678a"
    assert short_uuid(paper_id, (paper_id,)) == "12345678"


def test_short_uuid_and_filename_extend_past_multiple_prefix_collisions() -> None:
    paper_id = UUID("12345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    collisions = (
        UUID("12345678-abaa-4aaa-8aaa-aaaaaaaaaaaa"),
        UUID("12345678-aaba-4aaa-8aaa-aaaaaaaaaaaa"),
    )

    assert short_uuid(paper_id, collisions) == "12345678aaa"
    assert paper_filename("A Paper", paper_id, collisions) == "a-paper--12345678aaa.md"


def test_paper_filename_uses_slug_and_short_uuid() -> None:
    paper_id = UUID("12345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    assert paper_filename("A Paper", paper_id) == "a-paper--12345678.md"
