from __future__ import annotations

from literature_monitor.application import publisher_access
from literature_monitor.application.publisher_access import (
    project_publisher_access,
    resolve_publisher_access,
)
from literature_monitor.config import JournalConfig
from literature_monitor.coverage import CoverageStatus
from literature_monitor.openalex import (
    DiscoveryIssue,
    IssueSeverity,
    ResolvedSource,
    SourceResolutionUnit,
    resolve_journal_source,
)


def journal(name: str, *issns: str) -> JournalConfig:
    return JournalConfig(name=name, issn=tuple(issns))


def resolved_source(
    saved_journal: JournalConfig,
    *,
    source_id: str = "S1",
    publisher_id: str | None = "P1",
    publisher_name: str | None = "Example Publisher",
    homepage_url: str | None = "https://journals.example.org/title",
    hostname: str | None = "journals.example.org",
) -> ResolvedSource:
    return ResolvedSource(
        journal=saved_journal.name,
        configured_issns=saved_journal.issn,
        resolved_issns=saved_journal.issn,
        unresolved_issns=(),
        openalex_id=f"https://openalex.org/{source_id}",
        display_name=saved_journal.name,
        issn_l=saved_journal.issn[0],
        issn=saved_journal.issn,
        host_organization=(
            f"https://openalex.org/{publisher_id}" if publisher_id is not None else None
        ),
        host_organization_name=publisher_name,
        homepage_url=homepage_url,
        homepage_hostname=hostname if homepage_url is not None else None,
    )


def unit(
    saved_journal: JournalConfig,
    source: ResolvedSource | None,
    *,
    status: CoverageStatus | None = None,
    issues: tuple[DiscoveryIssue, ...] = (),
) -> SourceResolutionUnit:
    return SourceResolutionUnit(saved_journal, source, issues, status)


def test_one_journal_projects_one_publisher_and_homepage() -> None:
    saved = journal("Journal One", "1111-1111")
    projection = project_publisher_access((unit(saved, resolved_source(saved)),))

    assert projection.unavailable_journals == ()
    assert len(projection.publishers) == 1
    group = projection.publishers[0]
    assert group.publisher_id == "https://openalex.org/P1"
    assert group.publisher_name == "Example Publisher"
    assert len(group.sites) == 1
    site = group.sites[0]
    assert site.homepage_url == "https://journals.example.org/title"
    assert site.hostname == "journals.example.org"
    assert site.source_ids == ("https://openalex.org/S1",)
    assert site.journals == (saved,)


def test_one_source_with_multiple_configured_issns_projects_once() -> None:
    saved = journal("Journal One", "1111-1111", "2222-2222")
    source = resolved_source(saved)

    projection = project_publisher_access((unit(saved, source),))

    group = projection.publishers[0]
    assert len(group.sites) == 1
    assert group.sites[0].source_ids == ("https://openalex.org/S1",)
    assert group.sites[0].journals == (saved,)


def test_same_publisher_preserves_multiple_distinct_real_homepages() -> None:
    first = journal("Journal One", "1111-1111")
    second = journal("Journal Two", "2222-2222")
    units = (
        unit(
            first,
            resolved_source(
                first,
                source_id="S1",
                homepage_url="https://journals.example.org/one",
            ),
        ),
        unit(
            second,
            resolved_source(
                second,
                source_id="S2",
                homepage_url="https://journals.example.org/two",
            ),
        ),
    )

    projection = project_publisher_access(units)

    assert len(projection.publishers) == 1
    assert [site.homepage_url for site in projection.publishers[0].sites] == [
        "https://journals.example.org/one",
        "https://journals.example.org/two",
    ]
    assert [site.hostname for site in projection.publishers[0].sites] == [
        "journals.example.org",
        "journals.example.org",
    ]


def test_same_publisher_exact_repeated_homepage_deduplicates_and_aggregates_journals() -> None:
    first = journal("Journal One", "1111-1111")
    second = journal("Journal Two", "2222-2222")
    homepage = "https://journals.example.org/shared"
    units = (
        unit(first, resolved_source(first, source_id="S1", homepage_url=homepage)),
        unit(second, resolved_source(second, source_id="S2", homepage_url=homepage)),
    )

    projection = project_publisher_access(units)

    group = projection.publishers[0]
    assert len(group.sites) == 1
    assert group.sites[0].source_ids == (
        "https://openalex.org/S1",
        "https://openalex.org/S2",
    )
    assert group.sites[0].journals == (first, second)


def test_different_publisher_ids_remain_separate_groups() -> None:
    first = journal("Journal One", "1111-1111")
    second = journal("Journal Two", "2222-2222")

    projection = project_publisher_access((
        unit(first, resolved_source(first, source_id="S1", publisher_id="P1")),
        unit(second, resolved_source(second, source_id="S2", publisher_id="P2")),
    ))

    assert [group.publisher_id for group in projection.publishers] == [
        "https://openalex.org/P1",
        "https://openalex.org/P2",
    ]


def test_same_publisher_name_with_different_ids_does_not_merge() -> None:
    first = journal("Journal One", "1111-1111")
    second = journal("Journal Two", "2222-2222")

    projection = project_publisher_access((
        unit(
            first,
            resolved_source(
                first,
                source_id="S1",
                publisher_id="P1",
                publisher_name="Shared Brand",
            ),
        ),
        unit(
            second,
            resolved_source(
                second,
                source_id="S2",
                publisher_id="P2",
                publisher_name="Shared Brand",
            ),
        ),
    ))

    assert len(projection.publishers) == 2


def test_same_hostname_with_different_publisher_ids_does_not_merge() -> None:
    first = journal("Journal One", "1111-1111")
    second = journal("Journal Two", "2222-2222")

    projection = project_publisher_access((
        unit(
            first,
            resolved_source(
                first,
                source_id="S1",
                publisher_id="P1",
                homepage_url="https://journals.example.org/one",
            ),
        ),
        unit(
            second,
            resolved_source(
                second,
                source_id="S2",
                publisher_id="P2",
                homepage_url="https://journals.example.org/two",
            ),
        ),
    ))

    assert len(projection.publishers) == 2
    assert [group.sites[0].hostname for group in projection.publishers] == [
        "journals.example.org",
        "journals.example.org",
    ]


def test_missing_publisher_id_keeps_source_ungrouped_without_losing_homepage() -> None:
    first = journal("Journal One", "1111-1111")
    second = journal("Journal Two", "2222-2222")
    units = (
        unit(
            first,
            resolved_source(
                first,
                source_id="S1",
                publisher_id=None,
                publisher_name="Shared Brand",
                homepage_url="https://journals.example.org/shared",
            ),
        ),
        unit(
            second,
            resolved_source(
                second,
                source_id="S2",
                publisher_id=None,
                publisher_name="Shared Brand",
                homepage_url="https://journals.example.org/shared",
            ),
        ),
    )

    projection = project_publisher_access(units)

    assert len(projection.publishers) == 2
    assert all(group.publisher_id is None for group in projection.publishers)
    assert all(
        group.sites[0].homepage_url == "https://journals.example.org/shared"
        for group in projection.publishers
    )


def test_missing_publisher_name_preserves_stable_identity() -> None:
    saved = journal("Journal One", "1111-1111")

    projection = project_publisher_access((
        unit(saved, resolved_source(saved, publisher_name=None)),
    ))

    group = projection.publishers[0]
    assert group.publisher_id == "https://openalex.org/P1"
    assert group.publisher_name is None


def test_missing_homepage_is_explicitly_unavailable_without_dropping_source() -> None:
    saved = journal("Journal One", "1111-1111")

    projection = project_publisher_access((
        unit(saved, resolved_source(saved, homepage_url=None, hostname=None)),
    ))

    site = projection.publishers[0].sites[0]
    assert site.homepage_url is None
    assert site.hostname is None
    assert site.source_ids == ("https://openalex.org/S1",)
    assert site.journals == (saved,)


def test_unsafe_openalex_homepage_becomes_unavailable_before_projection() -> None:
    saved = journal("Journal One", "1111-1111")
    payload = {
        "id": "https://openalex.org/S1",
        "display_name": saved.name,
        "issn_l": saved.issn[0],
        "issn": list(saved.issn),
        "type": "journal",
        "alternate_titles": [],
        "abbreviated_title": None,
        "host_organization": "https://openalex.org/P1",
        "host_organization_name": "Example Publisher",
        "homepage_url": "https://user:secret@journals.example.org/private",
    }

    class FakeClient:
        def get_source_by_issn(self, issn: str, **kwargs):
            assert issn == saved.issn[0]
            return payload

    source, issues = resolve_journal_source(FakeClient(), saved)
    assert source is not None and not issues
    projection = project_publisher_access((unit(saved, source),))

    site = projection.publishers[0].sites[0]
    assert site.homepage_url is None
    assert site.hostname is None


def test_partial_resolution_keeps_successful_groups_and_local_unavailable_journal() -> None:
    complete = journal("Complete Journal", "1111-1111")
    no_publisher = journal("No Publisher Journal", "2222-2222")
    failed = journal("Failed Journal", "3333-3333")

    projection = project_publisher_access((
        unit(complete, resolved_source(complete, source_id="S1")),
        unit(
            no_publisher,
            resolved_source(
                no_publisher,
                source_id="S2",
                publisher_id=None,
                publisher_name=None,
            ),
        ),
        unit(failed, None, status=CoverageStatus.FAILED),
    ))

    assert len(projection.publishers) == 2
    assert projection.publishers[0].sites[0].journals == (complete,)
    assert projection.publishers[1].publisher_id is None
    assert projection.publishers[1].sites[0].journals == (no_publisher,)
    assert [entry.journal for entry in projection.unavailable_journals] == [failed]


def test_failed_source_unit_exposes_only_projection_local_status_not_provider_issue() -> None:
    failed = journal("Failed Journal", "3333-3333")
    issue = DiscoveryIssue(
        severity=IssueSeverity.ERROR,
        stage="source_resolution",
        journal=failed.name,
        message="private provider diagnostic",
    )

    projection = project_publisher_access((
        unit(failed, None, status=CoverageStatus.FAILED, issues=(issue,)),
    ))

    assert projection.publishers == ()
    assert projection.unavailable_journals[0].journal == failed
    assert "private provider diagnostic" not in repr(projection)


def test_projection_order_is_stable_and_follows_saved_journal_sequence() -> None:
    first = journal("First", "1111-1111")
    second = journal("Second", "2222-2222")
    third = journal("Third", "3333-3333")
    fourth = journal("Fourth", "4444-4444")
    units = (
        unit(
            first,
            resolved_source(
                first,
                source_id="S1",
                publisher_id="P2",
                homepage_url="https://p2.example.org/first",
                hostname="p2.example.org",
            ),
        ),
        unit(
            second,
            resolved_source(
                second,
                source_id="S2",
                publisher_id="P1",
                homepage_url="https://p1.example.org/shared",
                hostname="p1.example.org",
            ),
        ),
        unit(
            third,
            resolved_source(
                third,
                source_id="S3",
                publisher_id="P1",
                homepage_url="https://p1.example.org/shared",
                hostname="p1.example.org",
            ),
        ),
        unit(fourth, None, status=CoverageStatus.UNAVAILABLE),
    )

    first_projection = project_publisher_access(units)
    second_projection = project_publisher_access(tuple(units))

    assert first_projection == second_projection
    assert [group.publisher_id for group in first_projection.publishers] == [
        "https://openalex.org/P2",
        "https://openalex.org/P1",
    ]
    assert first_projection.publishers[1].sites[0].journals == (second, third)
    assert first_projection.unavailable_journals[0].journal == fourth


def test_resolve_publisher_access_reuses_existing_batched_source_resolver(monkeypatch) -> None:
    first = journal("First", "1111-1111")
    second = journal("Second", "2222-2222")
    units = (
        unit(first, resolved_source(first, source_id="S1")),
        unit(second, None, status=CoverageStatus.UNAVAILABLE),
    )
    calls = []
    progress = object()

    def fake_resolver(client, journals, *, progress_callback=None):
        calls.append((client, journals, progress_callback))
        return units

    monkeypatch.setattr(
        publisher_access,
        "resolve_journal_sources_batched",
        fake_resolver,
    )
    client = object()

    projection = resolve_publisher_access(
        client,
        (first, second),
        progress_callback=progress,
    )

    assert calls == [(client, (first, second), progress)]
    assert projection == project_publisher_access(units)


def test_projection_module_has_no_publisher_guess_table() -> None:
    guess_tables = {
        name
        for name, value in vars(publisher_access).items()
        if name.isupper() and isinstance(value, dict)
    }
    assert guess_tables == set()
