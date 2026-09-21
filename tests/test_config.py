from datetime import date
from pathlib import Path
import re

import pytest

from literature_monitor.config import ConfigurationError, load_config, parse_journal_whitelist
from literature_monitor.date_range import DateRangeSpec
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


def write_whitelist(
    tmp_path: Path,
    contents: str = JOURNAL_FIXTURE,
    filename: str = "venues.md",
) -> Path:
    path = tmp_path / filename
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


def write_monitor_config(
    tmp_path: Path,
    contents: str,
    filename: str = "monitor.yaml",
) -> Path:
    path = tmp_path / filename
    path.write_text(contents, encoding="utf-8")
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


def test_minimal_monitor_uses_config_defaults(tmp_path: Path) -> None:
    whitelist = write_whitelist(tmp_path, filename="list.md")
    config_path = write_monitor_config(
        tmp_path,
        "keyword_expression: causal\n",
        filename="my-monitor.yaml",
    )

    config = load_config(config_path)

    assert config.name == "my-monitor"
    assert config.venue_whitelist == whitelist.resolve()
    assert config.keyword_expression == "causal"
    assert config.keyword_ast == Term("causal")
    assert config.output_dir == (tmp_path / "workspace").resolve()
    assert config.date_spec == DateRangeSpec(window_days=14)
    assert config.log_level.value == "INFO"
    assert [journal.name for journal in config.journals] == [
        "Biometrics",
        "Annals of Applied Statistics",
    ]
    assert not config.output_dir.exists()


def test_monitor_defaults_are_independent_of_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    cwd_dir = tmp_path / "elsewhere"
    cwd_dir.mkdir()

    config_list = JOURNAL_FIXTURE.replace("Biometrics", "Config Journal", 1)
    cwd_list = JOURNAL_FIXTURE.replace("Biometrics", "Cwd Journal", 1)
    whitelist = write_whitelist(research_dir, config_list, filename="list.md")
    write_whitelist(cwd_dir, cwd_list, filename="list.md")
    config_path = write_monitor_config(
        research_dir,
        "keyword_expression: causal\n",
    )

    monkeypatch.chdir(cwd_dir)
    config = load_config(config_path)

    assert config.venue_whitelist == whitelist.resolve()
    assert config.output_dir == (research_dir / "workspace").resolve()
    assert config.journals[0].name == "Config Journal"


def test_explicit_monitor_paths_are_config_relative_or_absolute(tmp_path: Path) -> None:
    config_dir = tmp_path / "research"
    config_dir.mkdir()
    relative_dir = config_dir / "config"
    relative_dir.mkdir()
    whitelist = write_whitelist(relative_dir)
    absolute_output = (tmp_path / "absolute-workspace").resolve()
    config_path = write_monitor_config(
        config_dir,
        "venue_whitelist: config/venues.md\n"
        "keyword_expression: causal\n"
        f"output_dir: {absolute_output}\n",
    )

    config = load_config(config_path)

    assert config.venue_whitelist == whitelist.resolve()
    assert config.output_dir == absolute_output


def test_absolute_whitelist_and_relative_output_dir_are_supported(tmp_path: Path) -> None:
    config_dir = tmp_path / "research"
    config_dir.mkdir()
    whitelist = write_whitelist(tmp_path).resolve()
    config_path = write_monitor_config(
        config_dir,
        f"venue_whitelist: {whitelist}\n"
        "keyword_expression: causal\n"
        "output_dir: ./monitor-workspace\n",
    )

    config = load_config(config_path)

    assert config.venue_whitelist == whitelist
    assert config.output_dir == (config_dir / "monitor-workspace").resolve()


def test_empty_name_is_rejected(tmp_path: Path) -> None:
    write_whitelist(tmp_path, filename="list.md")
    config_path = write_monitor_config(
        tmp_path,
        "keyword_expression: causal\nname: ''\n",
    )

    with pytest.raises(ConfigurationError, match="field 'name'"):
        load_config(config_path)


@pytest.mark.parametrize(
    "contents",
    [
        "log_level: INFO\n",
        "keyword_expression: ''\n",
        "keyword_expression: null\n",
        "keyword_expression: '   '\n",
    ],
)
def test_keyword_expression_is_required_and_nonempty(
    tmp_path: Path,
    contents: str,
) -> None:
    write_whitelist(tmp_path, filename="list.md")
    config_path = write_monitor_config(tmp_path, contents)

    with pytest.raises(ConfigurationError, match="keyword_expression"):
        load_config(config_path)


@pytest.mark.parametrize(
    "field",
    [
        "name",
        "venue_whitelist",
        "output_dir",
        "from_date",
        "to_date",
        "window_days",
        "log_level",
    ],
)
def test_explicit_null_never_activates_monitor_defaults(
    tmp_path: Path,
    field: str,
) -> None:
    write_whitelist(tmp_path, filename="list.md")
    config_path = write_monitor_config(
        tmp_path,
        f"keyword_expression: causal\n{field}: null\n",
    )

    with pytest.raises(ConfigurationError) as captured:
        load_config(config_path)

    assert str(config_path.resolve()) in str(captured.value)
    assert f"field '{field}'" in str(captured.value)
    assert "must not be null" in str(captured.value)


def test_unknown_monitor_field_is_rejected_before_defaults(tmp_path: Path) -> None:
    write_whitelist(tmp_path, filename="list.md")
    config_path = write_monitor_config(
        tmp_path,
        "keyword_expression: causal\nwindow_day: 14\n",
    )

    with pytest.raises(ConfigurationError, match="field 'window_day'.*Extra inputs"):
        load_config(config_path)


@pytest.mark.parametrize(
    ("date_fields", "expected"),
    [
        ("", DateRangeSpec(window_days=14)),
        ("window_days: 14\n", DateRangeSpec(window_days=14)),
        (
            "from_date: 2026-09-01\nto_date: 2026-09-21\n",
            DateRangeSpec(
                from_date=date(2026, 9, 1),
                to_date=date(2026, 9, 21),
            ),
        ),
        (
            "from_date: 2026-09-01\nwindow_days: 21\n",
            DateRangeSpec(
                from_date=date(2026, 9, 1),
                window_days=21,
            ),
        ),
        (
            "to_date: 2026-09-21\nwindow_days: 14\n",
            DateRangeSpec(
                to_date=date(2026, 9, 21),
                window_days=14,
            ),
        ),
    ],
)
def test_config_accepts_all_legal_date_policy_forms(
    tmp_path: Path,
    date_fields: str,
    expected: DateRangeSpec,
) -> None:
    write_whitelist(tmp_path, filename="list.md")
    config_path = write_monitor_config(
        tmp_path,
        f"keyword_expression: causal\n{date_fields}",
    )

    assert load_config(config_path).date_spec == expected


@pytest.mark.parametrize(
    ("date_fields", "field"),
    [
        ("from_date: 2026-09-01\n", "from_date"),
        ("to_date: 2026-09-21\n", "to_date"),
        (
            "from_date: 2026-09-01\nto_date: 2026-09-21\nwindow_days: 21\n",
            "date_range",
        ),
        ("window_days: 0\n", "window_days"),
        ("window_days: -1\n", "window_days"),
        (
            "from_date: 2026-09-22\nto_date: 2026-09-21\n",
            "from_date",
        ),
    ],
)
def test_config_rejects_all_illegal_date_policy_forms(
    tmp_path: Path,
    date_fields: str,
    field: str,
) -> None:
    write_whitelist(tmp_path, filename="list.md")
    config_path = write_monitor_config(
        tmp_path,
        f"keyword_expression: causal\n{date_fields}",
    )

    with pytest.raises(ConfigurationError) as captured:
        load_config(config_path)

    assert str(config_path.resolve()) in str(captured.value)
    assert f"field '{field}'" in str(captured.value)


def test_invalid_date_value_keeps_config_path_and_field_context(tmp_path: Path) -> None:
    write_whitelist(tmp_path, filename="list.md")
    config_path = write_monitor_config(
        tmp_path,
        "keyword_expression: causal\n"
        "from_date: not-a-date\n"
        "window_days: 14\n",
    )

    with pytest.raises(ConfigurationError) as captured:
        load_config(config_path)

    assert str(config_path.resolve()) in str(captured.value)
    assert "field 'from_date'" in str(captured.value)


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


def test_missing_default_whitelist_reports_resolved_path(tmp_path: Path) -> None:
    config_path = write_monitor_config(tmp_path, "keyword_expression: causal\n")

    with pytest.raises(ConfigurationError) as captured:
        load_config(config_path)

    assert "field 'venue_whitelist'" in str(captured.value)
    assert str((tmp_path / "list.md").resolve()) in str(captured.value)


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
