from pathlib import Path
import re

import pytest

from literature_monitor.config import ConfigurationError, load_config, parse_journal_whitelist
from literature_monitor.keywords import And, Phrase, Term


JOURNAL_FIXTURE = """\
# Venues

## Journals

| Journal | ISSN/EISSN |
|---|---|
| Biometrics | 0006-341X |
| Annals of Applied Statistics | 1932-6157 / 1941-7330 |

## Conferences

| Journal | ISSN/EISSN |
|---|---|
| Fake Conference | 0378-5955 |
"""


def write_whitelist(tmp_path: Path, contents: str = JOURNAL_FIXTURE) -> Path:
    path = tmp_path / "venues.md"
    path.write_text(contents, encoding="utf-8")
    return path


def write_config(tmp_path: Path, whitelist: str = "venues.md", **values: str) -> Path:
    expression = values.get("keyword_expression", '"high-dimensional" AND statistics')
    log_level = values.get("log_level", "INFO")
    path = tmp_path / "config.yaml"
    path.write_text(
        f"venue_whitelist: {whitelist}\n"
        f"keyword_expression: '{expression}'\n"
        f"log_level: {log_level}\n",
        encoding="utf-8",
    )
    return path


def test_journal_fixture_parses_multiple_issns_and_excludes_conferences(tmp_path: Path) -> None:
    path = write_whitelist(tmp_path)

    journals = parse_journal_whitelist(path)

    assert [journal.name for journal in journals] == [
        "Biometrics",
        "Annals of Applied Statistics",
    ]
    assert journals[1].issn == ("1932-6157", "1941-7330")
    assert "Fake Conference" not in {journal.name for journal in journals}


def test_load_config_resolves_relative_path_and_parses_keyword(tmp_path: Path) -> None:
    whitelist = write_whitelist(tmp_path)
    config_path = write_config(tmp_path, log_level="debug")

    config = load_config(config_path)

    assert config.venue_whitelist == whitelist.resolve()
    assert config.keyword_ast == And(Phrase("high-dimensional"), Term("statistics"))
    assert config.log_level.value == "DEBUG"


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("| Journal Name | ISSN/EISSN |", "expected table header"),
        ("| Biometrics | 0006-3410 |", "checksum"),
        ("Biometrics | 0006-341X", "two-column Markdown table row"),
    ],
)
def test_whitelist_errors_include_path_and_line(
    tmp_path: Path, replacement: str, message: str
) -> None:
    if "Journal Name" in replacement:
        contents = JOURNAL_FIXTURE.replace("| Journal | ISSN/EISSN |", replacement, 1)
    else:
        contents = JOURNAL_FIXTURE.replace("| Biometrics | 0006-341X |", replacement)
    path = write_whitelist(tmp_path, contents)

    with pytest.raises(ConfigurationError) as captured:
        parse_journal_whitelist(path)

    assert str(path) in str(captured.value)
    assert re.search(r":\d+:", str(captured.value))
    assert message in str(captured.value)


def test_duplicate_issn_reports_value_and_first_location(tmp_path: Path) -> None:
    contents = JOURNAL_FIXTURE.replace("1932-6157 / 1941-7330", "1932-6157 / 0006-341X")
    path = write_whitelist(tmp_path, contents)

    with pytest.raises(ConfigurationError, match="duplicate ISSN/EISSN '0006-341X'.*first seen"):
        parse_journal_whitelist(path)


@pytest.mark.parametrize(
    ("original", "replacement", "message"),
    [
        (
            "| Annals of Applied Statistics | 1932-6157 / 1941-7330 |",
            "| biometrics | 1932-6157 / 1941-7330 |",
            "duplicate Journal",
        ),
        ("| Biometrics | 0006-341X |", "|  | 0006-341X |", "Journal must not be empty"),
        ("| Biometrics | 0006-341X |", "| Biometrics |  |", "ISSN/EISSN must not be empty"),
        (
            "| Biometrics | 0006-341X |",
            "| Biometrics | 0006-341X /  |",
            "ISSN/EISSN contains an empty value",
        ),
        (
            "| Biometrics | 0006-341X |",
            "| Biometrics | ０００６-３４１X |",
            "invalid ISSN/EISSN",
        ),
    ],
)
def test_whitelist_rejects_invalid_names_and_issn_cells(
    tmp_path: Path, original: str, replacement: str, message: str
) -> None:
    path = write_whitelist(tmp_path, JOURNAL_FIXTURE.replace(original, replacement))

    with pytest.raises(ConfigurationError) as captured:
        parse_journal_whitelist(path)

    assert str(path) in str(captured.value)
    assert re.search(r":\d+:", str(captured.value))
    assert message in str(captured.value)


def test_config_errors_include_field_or_yaml_location(tmp_path: Path) -> None:
    write_whitelist(tmp_path)
    config_path = write_config(tmp_path, keyword_expression="alpha AND")
    with pytest.raises(ConfigurationError, match="field 'keyword_expression'.*column"):
        load_config(config_path)

    config_path.write_text("venue_whitelist: [\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match=r"config\.yaml:\d+:\d+: invalid YAML"):
        load_config(config_path)


def test_missing_whitelist_reports_resolved_path(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, whitelist="missing.md")
    with pytest.raises(ConfigurationError, match=str(tmp_path / "missing.md")):
        load_config(config_path)


def test_repository_list_smoke_parses_and_excludes_conference_names() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    list_path = repository_root / "list.md"
    journals = parse_journal_whitelist(list_path)

    contents = list_path.read_text(encoding="utf-8")
    conference_section = contents.split("## Conferences", maxsplit=1)[1]
    conference_names = {
        cells[0].strip()
        for line in conference_section.splitlines()
        if line.startswith("|")
        if len(cells := line.strip("|").split("|")) == 2
        and cells[0].strip() not in {"Abbreviation", "---"}
    }

    assert journals
    assert all(journal.issn for journal in journals)
    assert {journal.name for journal in journals}.isdisjoint(conference_names)
