from datetime import date
from pathlib import Path
import re

import pytest

from literature_monitor.config import (
    ConfigurationError,
    JournalConfig,
    load_config,
    parse_journal_whitelist,
    parse_journal_whitelist_text,
    render_journal_whitelist_text,
    validate_journal_configs,
    validate_journal_storage,
)
from literature_monitor.date_range import DateRangeSpec
from literature_monitor.keywords import And, Phrase, Term


JOURNAL_FIXTURE = '# Venues\n\n## Journals\n\n| Journal | ISSN-L | Publisher ID | Group |\n|---|---|---|---|\n| Biometrics | 0006-341X |  |  |\n| Annals of Applied Statistics | 1932-6157 |  |  |\n\n## Conferences\n\n| Journal | ISSN/EISSN |\n|---|---|\n| Fake Conference | 0378-5955 |\n'

GROUPED_JOURNAL_FIXTURE = '# Venues\n\n## Journals\n\n| Journal | ISSN-L | Publisher ID | Group |\n|---|---|---|---|\n| Biometrics | 0006-341X |  | Biostatistics |\n| Annals of Applied Statistics | 1932-6157 |  |  |\n\n## Conferences\n\nConferences remain outside Journal parsing.\n'


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


def test_target_journal_fixture_parses_single_issn_l_and_excludes_conferences(tmp_path: Path) -> None:
    path = write_whitelist(tmp_path)

    journals = parse_journal_whitelist(path)

    assert [journal.name for journal in journals] == [
        "Biometrics",
        "Annals of Applied Statistics",
    ]
    assert journals[1].issn_l == "1932-6157"
    assert all(journal.group is None for journal in journals)
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
        ('| Biometrics | 0006-3410 |  |  |', "checksum"),
        ("Biometrics | 0006-341X", "Markdown table row"),
    ],
)
def test_whitelist_errors_include_path_and_line(
    tmp_path: Path, replacement: str, message: str
) -> None:
    if "Journal Name" in replacement:
        contents = JOURNAL_FIXTURE.replace('| Journal | ISSN-L | Publisher ID | Group |', replacement, 1)
    else:
        contents = JOURNAL_FIXTURE.replace('| Biometrics | 0006-341X |  |  |', replacement)
    path = write_whitelist(tmp_path, contents)

    with pytest.raises(ConfigurationError) as captured:
        parse_journal_whitelist(path)

    assert str(path) in str(captured.value)
    assert re.search(r":\d+:", str(captured.value))
    assert message in str(captured.value)


def test_duplicate_issn_reports_value_and_first_location(tmp_path: Path) -> None:
    contents = JOURNAL_FIXTURE.replace("1932-6157", "0006-341X")
    path = write_whitelist(tmp_path, contents)

    with pytest.raises(ConfigurationError, match="duplicate ISSN-L '0006-341X'.*first seen"):
        parse_journal_whitelist(path)




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
    assert all(journal.issn_l for journal in journals)
    assert {journal.name for journal in journals}.isdisjoint(conference_names)


def test_grouped_whitelist_parses_groups_and_empty_cells(tmp_path: Path) -> None:
    journals = parse_journal_whitelist(write_whitelist(tmp_path, GROUPED_JOURNAL_FIXTURE))

    assert journals == (
        JournalConfig(name="Biometrics", issn_l="0006-341X", group="Biostatistics"),
        JournalConfig(name="Annals of Applied Statistics", issn_l="1932-6157"),
    )


@pytest.mark.parametrize("second_group", ["Biostatistics", "biostatistics", "Other"])
def test_shared_validation_preserves_group_names_and_journal_order(second_group: str) -> None:
    journals = (
        JournalConfig.model_construct(name=" Biometrics ", issn_l=" 0006-341x ", group=" Biostatistics "),
        JournalConfig(name="Annals of Applied Statistics", issn_l="1932-6157", group=second_group),
    )

    normalized = validate_journal_configs(journals)

    assert [journal.name for journal in normalized] == ["Biometrics", "Annals of Applied Statistics"]
    assert [journal.group for journal in normalized] == ["Biostatistics", second_group]
    assert (normalized[0].issn_l,) == ("0006-341X",)
    assert JournalConfig(name="Ungrouped", issn_l="0006-341X").group is None


@pytest.mark.parametrize("group", ["", "   "])
def test_journal_domain_rejects_empty_string_group(group: str) -> None:
    with pytest.raises(ValueError, match="group"):
        JournalConfig(name="Biometrics", issn_l="0006-341X", group=group)






def test_groups_do_not_change_global_issn_uniqueness() -> None:
    with pytest.raises(ConfigurationError, match="duplicate ISSN"):
        validate_journal_configs((
            JournalConfig(name="One", issn_l="0006-341X", group="First"),
            JournalConfig(name="Two", issn_l="0006-341X", group="Second"),
        ))


@pytest.mark.parametrize(
    "group", ["Stats|Methods", "Stats\nMethods", "Stats\rMethods", "Stats\u2028Methods"],
)
def test_group_storage_rejects_table_breaking_values(group: str, tmp_path: Path) -> None:
    journals = (JournalConfig(name="Biometrics", issn_l="0006-341X", group=group),)
    with pytest.raises(ConfigurationError) as captured:
        validate_journal_storage(journals)
    assert captured.value.field == "journals"
    assert "journal group" in str(captured.value)
    assert repr(group) in str(captured.value)
    with pytest.raises(ConfigurationError, match="journal group"):
        render_journal_whitelist_text(None, journals, path=tmp_path / "list.md")


@pytest.mark.parametrize(
    "existing_contents",
    [None, JOURNAL_FIXTURE, GROUPED_JOURNAL_FIXTURE, "# Custom List\n\n## Conferences\n\nKeep me.\n"],
    ids=["new-document", "legacy-table", "grouped-table", "missing-journals-section"],
)
@pytest.mark.parametrize("group", [None, "统计 & Methods <A> \"B\""])
def test_renderer_preserves_storage_shape_and_other_sections(
    tmp_path: Path, existing_contents: str | None, group: str | None,
) -> None:
    journals = (
        JournalConfig(name="Biometrics", issn_l="0006-341X", group=group),
        JournalConfig(name="Annals of Applied Statistics", issn_l="1932-6157"),
    )
    path = tmp_path / "list.md"
    rendered = render_journal_whitelist_text(existing_contents, journals, path=path)

    grouped = group is not None or existing_contents == GROUPED_JOURNAL_FIXTURE
    journal_section = rendered.split("## Journals", 1)[1].split("## Conferences", 1)[0]
    assert '| Journal | ISSN-L | Publisher ID | Group |' in journal_section
    assert parse_journal_whitelist_text(rendered, path=path) == journals
    assert render_journal_whitelist_text(rendered, journals, path=path) == rendered
    if existing_contents is not None:
        prefix = existing_contents.split("## Journals", 1)[0]
        assert rendered.startswith(prefix)
        if "## Journals" in existing_contents and "## Conferences" in existing_contents:
            assert rendered.split("## Conferences", 1)[1] == existing_contents.split("## Conferences", 1)[1]


def test_renderer_preserves_crlf_surroundings_with_target_schema(tmp_path: Path) -> None:
    prefix = "# List\r\n\r\nUnowned introduction.\r\n\r\n"
    suffix = "## Conferences\r\n\r\nUntouched conference content.\r\n"
    contents = prefix + '## Journals\r\n\r\n| Journal | ISSN-L | Publisher ID | Group |\r\n|---|---|---|---|\r\n| Biometrics | 0006-341X |  | Stats |\r\n\r\n' + suffix
    journals = (JournalConfig(name="Biometrics", issn_l="0006-341X"),)

    rendered = render_journal_whitelist_text(contents, journals, path=tmp_path / "list.md")

    assert '| Journal | ISSN-L | Publisher ID | Group |' in rendered
    assert rendered.startswith(prefix)
    assert rendered.endswith(suffix)
    assert parse_journal_whitelist_text(rendered, path=tmp_path / "list.md") == journals


@pytest.mark.parametrize("bad, message", [
    ("|  | 0006-341X |  |  |", "Journal must not be empty"),
    ("| Biometrics |  |  |  |", "ISSN-L must not be empty"),
    ("| Biometrics | 0006-341X / 1541-0420 |  |  |", "invalid ISSN"),
    ("| Biometrics | 0006-3410 |  |  |", "checksum"),
    ("| Biometrics | 0006-341X | I1 |  |", "Publisher ID"),
    ("| Biometrics | 0006-341X |", "expected 4 table columns"),
])
def test_target_rows_reject_invalid_identity_or_metadata(tmp_path, bad, message):
    path = write_whitelist(tmp_path, JOURNAL_FIXTURE.replace('| Biometrics | 0006-341X |  |  |', bad))
    with pytest.raises(ConfigurationError, match=message):
        parse_journal_whitelist(path)


def test_same_display_name_is_distinct_by_issn_l(tmp_path):
    text = JOURNAL_FIXTURE.replace('Annals of Applied Statistics', 'Biometrics')
    journals = parse_journal_whitelist(write_whitelist(tmp_path, text))
    assert len(journals) == 2 and journals[0].name == journals[1].name
    assert journals[0].issn_l != journals[1].issn_l


@pytest.mark.parametrize('publisher', ['', 'P123', 'https://openalex.org/P123'])
def test_publisher_identity_round_trip(tmp_path, publisher):
    journal = JournalConfig(name='Biometrics', issn_l='0006-341x', publisher_id=publisher or None)
    expected = 'https://openalex.org/P123' if publisher else None
    assert journal.publisher_id == expected
    text = render_journal_whitelist_text(None, (journal,), path=tmp_path / 'list.md')
    assert parse_journal_whitelist_text(text, path=tmp_path / 'list.md') == (journal,)


@pytest.mark.parametrize('identifier', ['I123', 'S123', 'https://example.org/P123', 'P1/extra'])
def test_publisher_requires_direct_openalex_id(identifier):
    with pytest.raises(ValueError, match='Publisher ID'):
        JournalConfig(name='Bio', issn_l='0006-341X', publisher_id=identifier)


def test_legacy_storage_is_explicit_migration_input_without_first_issn_guess(tmp_path):
    from literature_monitor.config import parse_legacy_journal_whitelist_text, LegacyJournal
    legacy = '## Journals\n| Journal | ISSN/EISSN | Group |\n|---|---|---|\n| Old | 1541-0420 / 0006-341X | Stats |\n'
    rows = parse_legacy_journal_whitelist_text(legacy, path=tmp_path / 'list.md')
    assert rows == (LegacyJournal(name='Old', issn=('1541-0420', '0006-341X'), group='Stats'),)
    path = write_whitelist(tmp_path, legacy)
    with pytest.raises(ConfigurationError, match='migration required'):
        parse_journal_whitelist(path)
    config = write_config(tmp_path)
    with pytest.raises(ConfigurationError, match='migration required'):
        load_config(config)


@pytest.mark.parametrize('bad', ['| Journal | ISSN-L | Group |', '|---|---|bad|---|', '|---|---|---|'])
def test_target_structure_reports_location(tmp_path, bad):
    old = '| Journal | ISSN-L | Publisher ID | Group |' if 'Journal' in bad else '|---|---|---|---|'
    path = write_whitelist(tmp_path, JOURNAL_FIXTURE.replace(old, bad))
    with pytest.raises(ConfigurationError) as caught:
        parse_journal_whitelist(path)
    assert str(path) in str(caught.value)
