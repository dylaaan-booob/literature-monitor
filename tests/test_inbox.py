from __future__ import annotations

import re

import pytest
import yaml

from literature_monitor.inbox import render_default_inbox_base


VIEW_STATUSES = {
    "Inbox": "candidate",
    "Kept": "kept",
    "Rejected": "rejected",
    "In Zotero": "in_zotero",
}

EXPECTED_ORDER = [
    "formula.Paper",
    "journal",
    "publication_date",
    "authors",
    "author_keywords",
    "discovered_at",
    "status",
]

EXPECTED_SORT = [
    {"property": "discovered_at", "direction": "DESC"},
    {"property": "publication_date", "direction": "DESC"},
    {"property": "title", "direction": "ASC"},
]


def parsed_base() -> dict[str, object]:
    parsed = yaml.safe_load(render_default_inbox_base())
    assert isinstance(parsed, dict)
    return parsed


def test_renderer_is_deterministic_and_valid_yaml() -> None:
    first = render_default_inbox_base()

    assert first == render_default_inbox_base()
    assert first.endswith("\n")
    assert isinstance(yaml.safe_load(first), dict)


def global_filter() -> str:
    filters = parsed_base()["filters"]
    assert isinstance(filters, dict)
    expressions = filters["and"]
    assert isinstance(expressions, list)
    assert len(expressions) == 1
    expression = expressions[0]
    assert isinstance(expression, str)
    return expression


def resolved_papers_folder(expression: str, base_folder: str) -> str:
    match = re.search(
        r'file\.inFolder\(if\(this\.file\.folder == "(?P<root>[^"]+)", '
        r'"(?P<root_target>[^"]+)", this\.file\.folder \+ "(?P<suffix>[^"]+)"\)\)',
        expression,
    )
    assert match is not None
    if base_folder == match["root"]:
        return match["root_target"]
    return base_folder + match["suffix"]


def test_global_filter_preserves_paper_markdown_boundary() -> None:
    expression = global_filter()

    assert expression.startswith('file.ext == "md" && file.inFolder(')
    assert expression.endswith(') && type == "paper"')


@pytest.mark.parametrize(
    ("base_folder", "expected_folder"),
    [
        ("/", "Papers"),
        ("Research/LiteratureMonitor", "Research/LiteratureMonitor/Papers"),
    ],
)
def test_global_filter_targets_workspace_papers_folder(
    base_folder: str,
    expected_folder: str,
) -> None:
    target = resolved_papers_folder(global_filter(), base_folder)

    assert target == expected_folder
    assert not target.startswith("/")


def test_views_have_status_membership_and_default_order() -> None:
    views = parsed_base()["views"]
    assert isinstance(views, list)
    assert [view["name"] for view in views] == list(VIEW_STATUSES)

    for view in views:
        status = VIEW_STATUSES[view["name"]]
        assert view["type"] == "table"
        assert view["filters"] == {"and": [f'status == "{status}"']}


def test_views_use_required_columns_and_sort_precedence() -> None:
    data = parsed_base()
    assert data["properties"] == {
        "note.status": {"displayName": "Status"},
        "note.journal": {"displayName": "Journal"},
        "note.publication_date": {"displayName": "Publication Date"},
        "note.authors": {"displayName": "Authors"},
        "note.author_keywords": {"displayName": "Author Keywords"},
        "note.discovered_at": {"displayName": "Discovered At"},
    }

    for view in data["views"]:
        assert view["order"] == EXPECTED_ORDER
        assert view["sort"] == EXPECTED_SORT


def test_paper_formula_is_presentation_only_file_navigation() -> None:
    assert parsed_base()["formulas"] == {
        "Paper": "link(file.path, title)"
    }


def test_renderer_adds_no_durable_inbox_or_track_b_fields() -> None:
    rendered = render_default_inbox_base()
    excluded = {
        "inbox_title",
        "display_title",
        "inbox_seen",
        "inbox_order",
        "reviewed_at",
        "review_batch",
        "review_priority",
        "rejection_reason",
        "maybe",
        "deferred",
        "reviewing",
        "screened",
        "archived",
    }

    for field in excluded:
        assert field not in rendered
