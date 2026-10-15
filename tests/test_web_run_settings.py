from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

@pytest.fixture(autouse=True)
def unavailable_settings_metadata(monkeypatch):
    from literature_monitor.openalex import OpenAlexClient, OpenAlexRequestError
    def unavailable(*args, **kwargs):
        raise OpenAlexRequestError("requires metadata resolution: test Provider unavailable")
    monkeypatch.setattr(OpenAlexClient, "_request_json", unavailable)

from fastapi.testclient import TestClient
from starlette.datastructures import FormData

import literature_monitor.web.app as web_app
import literature_monitor.web.settings_form as settings_form
from literature_monitor.application.monitor import (
    MonitorIssue,
    MonitorIssueComponent,
    MonitorIssueSeverity,
    MonitorStatistics,
    ProgressStage,
    RunOutcome,
    RunResult,
)
from literature_monitor.application.settings import (
    ContentRevision,
    MonitorDraft,
    SettingsIssue,
    SettingsLoadResult,
    SettingsSaveOutcome,
    SettingsSaveResult,
    SettingsValidationOutcome,
    SettingsValidationResult,
)
from literature_monitor.config import JournalConfig, LogLevel, parse_monitor_definition
from literature_monitor.coverage import CoverageComponent, CoverageStatus, CoverageUnit
from literature_monitor.date_range import DateRangeSpec, ResolvedDateRange
from literature_monitor.progress import (
    PROGRESS_STAGES,
    ActivityKind,
    ActivitySnapshot,
)
from literature_monitor.web.app import create_app
from literature_monitor.web.run_presentation import build_run_presentation
from literature_monitor.web.run_coordinator import (
    CoordinatorSnapshot,
    CoordinatorStatus,
    StartOutcome,
    StartResult,
    UnexpectedRunError,
)


def revision(exists: bool, digest: str | None) -> ContentRevision:
    return ContentRevision(exists=exists, digest=digest)


def make_draft(
    *,
    name: str = "Web Monitor",
    keyword_expression: str = "causal",
    output_dir: Path = Path("workspace"),
    monitor_digest: str = "monitor-old",
    journal_digest: str = "journal-old",
    journals: tuple[JournalConfig, ...] | None = None,
) -> MonitorDraft:
    return MonitorDraft(
        name=name,
        keyword_expression=keyword_expression,
        journals=journals
        or (JournalConfig(name="Biometrics", issn_l="0006-341X"),),
        date_spec=DateRangeSpec("2026-01", "2026-01"),
        output_dir=output_dir,
        log_level=LogLevel.INFO,
        monitor_revision=revision(True, monitor_digest),
        journal_revision=revision(True, journal_digest),
    )


def make_settings_state(
    tmp_path: Path,
    *,
    draft: MonitorDraft | None = None,
    issues: tuple[SettingsIssue, ...] = (),
) -> SettingsLoadResult:
    return SettingsLoadResult(
        config_path=(tmp_path / "monitor.yaml").resolve(),
        journal_path=(tmp_path / "list.md").resolve(),
        venue_whitelist=Path("list.md"),
        draft=draft or make_draft(),
        issues=issues,
    )


def make_validation(
    *,
    outcome: SettingsValidationOutcome = SettingsValidationOutcome.VALID,
    issues: tuple[SettingsIssue, ...] = (),
) -> SettingsValidationResult:
    return SettingsValidationResult(
        outcome=outcome,
        issues=issues,
        config=None,
        resolved_date_range=(
            ResolvedDateRange(
                from_date=date(2026, 9, 9),
                to_date=date(2026, 9, 22),
            )
            if outcome is SettingsValidationOutcome.VALID
            else None
        ),
    )


def make_save_result(
    tmp_path: Path,
    *,
    outcome: SettingsSaveOutcome,
    state_draft: MonitorDraft | None = None,
    issues: tuple[SettingsIssue, ...] = (),
    journal_written: bool = False,
    monitor_written: bool = False,
) -> SettingsSaveResult:
    state = make_settings_state(tmp_path, draft=state_draft or make_draft())
    validation = make_validation(
        outcome=(
            SettingsValidationOutcome.INVALID
            if outcome is SettingsSaveOutcome.INVALID_DRAFT
            else SettingsValidationOutcome.VALID
        ),
        issues=issues if outcome is SettingsSaveOutcome.INVALID_DRAFT else (),
    )
    return SettingsSaveResult(
        outcome=outcome,
        validation=validation,
        state=state,
        issues=issues,
        journal_written=journal_written,
        monitor_written=monitor_written,
        monitor_revision=state.draft.monitor_revision,
        journal_revision=state.draft.journal_revision,
    )


def valid_settings_form(csrf_token: str, **overrides: str) -> dict[str, str]:
    values = {
        "csrf_token": csrf_token,
        "name": "Unsaved Monitor",
        "keyword_expression": "causal AND inference",
        "journal_name": "Biometrics",
        "journal_issns": "0006-341X",
        "journal_group": "",
        "journal_publisher_id": "",
        "journal_pending": "0",
        "from_month": "2026-01",
        "to_month": "2026-01",
        "output_dir": "edited-workspace",
        "log_level": "DEBUG",
        "monitor_revision_exists": "1",
        "monitor_revision_digest": "monitor-old",
        "journal_revision_exists": "1",
        "journal_revision_digest": "journal-old",
    }
    values.update(overrides)
    return values


def settings_form_values(output_dir: str) -> settings_form.SettingsFormValues:
    return settings_form.SettingsFormValues(
        name="Web Monitor",
        keyword_expression="causal",
        journals=(
            settings_form.SettingsJournalRow(
                name="Biometrics",
                issns="0006-341X",
            ),
        ),
        from_month="2026-01",
        to_month="2026-01",
        output_dir=output_dir,
        log_level="INFO",
        monitor_revision_exists="1",
        monitor_revision_digest="monitor-old",
        journal_revision_exists="1",
        journal_revision_digest="journal-old",
    )


@pytest.mark.parametrize(
    ("raw_output_dir", "expected"),
    (
        ("", Path(".")),
        ("   ", Path(".")),
        ("  workspace  ", Path("workspace")),
        ("./workspace ", Path("workspace")),
        (" /tmp/demo ", Path("/tmp/demo")),
    ),
)
def test_settings_form_output_path_matches_runtime_config_semantics(
    raw_output_dir: str,
    expected: Path,
    tmp_path: Path,
) -> None:
    draft, issues = settings_form.settings_draft_from_form(
        settings_form_values(raw_output_dir)
    )
    runtime_definition = parse_monitor_definition(
        tmp_path / "monitor.yaml",
        {
            "keyword_expression": "causal",
            "output_dir": raw_output_dir,
            "from_month": "2026-01",
            "to_month": "2026-01",
        },
    )

    assert issues == ()
    assert draft is not None
    assert draft.output_dir == runtime_definition.output_dir == expected


def test_settings_draft_form_round_trip_preserves_groups_and_revisions() -> None:
    draft = make_draft(journals=(
        JournalConfig(name="Biometrics", issn_l="0006-341X", group="统计 & <Models> \"B\""),
        JournalConfig(name="Annals of Applied Statistics", issn_l="1932-6157"),
    ))

    values = settings_form.settings_form_from_draft(draft)
    restored, issues = settings_form.settings_draft_from_form(values)

    assert issues == ()
    assert restored == draft
    assert [row.group for row in values.journals] == ["统计 & <Models> \"B\"", ""]


@pytest.mark.parametrize("group_value", ["", "   ", " Biostatistics "])
def test_settings_submission_preserves_per_row_groups_and_new_ungrouped_row(
    group_value: str,
) -> None:
    submission = list(valid_settings_form("unused", journal_group=group_value).items()) + [
        ("journal_name", "Annals of Applied Statistics"),
        ("journal_issns", "1932-6157"),
        ("journal_group", ""),
        ("journal_publisher_id", ""),
        ("journal_pending", "0"),
    ]

    values = settings_form.settings_form_from_submission(FormData(submission))
    draft, issues = settings_form.settings_draft_from_form(values)

    assert issues == ()
    assert draft is not None
    assert draft.journals == (
        JournalConfig(name="Biometrics", issn_l="0006-341X", group=group_value.strip() or None),
        JournalConfig(name="Annals of Applied Statistics", issn_l="1932-6157"),
    )
    assert draft.monitor_revision == revision(True, "monitor-old")
    assert draft.journal_revision == revision(True, "journal-old")


def idle_snapshot() -> CoordinatorSnapshot:
    return CoordinatorSnapshot(
        status=CoordinatorStatus.IDLE,
        progress_stage=None,
        started_at=None,
        finished_at=None,
        result=None,
        unexpected_error=None,
    )


def running_snapshot(
    stage: ProgressStage | None = ProgressStage.DISCOVERING_PAPERS,
    *,
    activity: ActivitySnapshot | None = None,
    started_at: datetime | None = None,
    last_activity_at: datetime | None = None,
    inactivity_warning: bool = False,
    worker_alive: bool = True,
) -> CoordinatorSnapshot:
    actual_started_at = started_at or datetime(
        2026,
        9,
        22,
        12,
        0,
        tzinfo=timezone.utc,
    )
    return CoordinatorSnapshot(
        status=CoordinatorStatus.RUNNING,
        progress_stage=stage,
        started_at=actual_started_at,
        finished_at=None,
        result=None,
        unexpected_error=None,
        stage_index=(
            PROGRESS_STAGES.index(stage) + 1 if stage is not None else None
        ),
        stage_total=len(PROGRESS_STAGES),
        activities=(activity,) if activity is not None else (),
        stage_started_at=actual_started_at if stage is not None else None,
        last_activity_at=last_activity_at,
        worker_alive=worker_alive,
        inactivity_warning=inactivity_warning,
    )


def activity_snapshot(
    *,
    kind: ActivityKind = ActivityKind.WORKING,
    operation: str = "doi_supplement",
    label: str = "Looking up Crossref DOI metadata",
    source: str | None = "crossref",
    detail: str | None = "DOI 10.5555/example",
    current: int | None = 18,
    total: int | None = 47,
    unit: str | None = "doi",
    eta_seconds: float | None = 24.0,
) -> ActivitySnapshot:
    started_at = datetime(2026, 9, 22, 12, 1, tzinfo=timezone.utc)
    return ActivitySnapshot(
        kind=kind,
        operation=operation,
        label=label,
        source=source,
        detail=detail,
        current=current,
        total=total,
        unit=unit,
        started_at=started_at,
        updated_at=datetime(2026, 9, 22, 12, 1, 22, tzinfo=timezone.utc),
        rate=1.0 if kind is ActivityKind.WORKING else None,
        eta_seconds=eta_seconds,
    )


def make_run_result() -> RunResult:
    warning = MonitorIssue(
        severity=MonitorIssueSeverity.WARNING,
        component=MonitorIssueComponent.OPENALEX,
        stage="discovery",
        message="one source warning",
    )
    error = MonitorIssue(
        severity=MonitorIssueSeverity.ERROR,
        component=MonitorIssueComponent.CROSSREF_DISCOVERY,
        stage="discovery",
        message="one source error",
    )
    return RunResult(
        resolved_date_range=ResolvedDateRange(
            from_date=date(2026, 9, 1),
            to_date=date(2026, 9, 22),
        ),
        canonical_paper_count=8,
        created_papers=3,
        matched_existing_papers=4,
        created_authors=5,
        existing_authors=6,
        warnings=(warning,),
        errors=(error,),
        outcome=RunOutcome.COMPLETED_WITH_ERRORS,
        statistics=MonitorStatistics(),
        coverage=(
            CoverageUnit(
                provider="openalex",
                component=CoverageComponent.OPENALEX_DISCOVERY,
                status=CoverageStatus.COMPLETE,
                journal="Biometrics",
            ),
            CoverageUnit(
                provider="openalex",
                component=CoverageComponent.OPENALEX_DISCOVERY,
                status=CoverageStatus.FAILED,
                journal="Annals of Statistics",
            ),
            CoverageUnit(
                provider="crossref",
                component=CoverageComponent.CROSSREF_DISCOVERY,
                status=CoverageStatus.UNAVAILABLE,
                journal="Biometrics",
                issn="0006-341X",
            ),
            CoverageUnit(
                provider="crossref",
                component=CoverageComponent.CROSSREF_SUPPLEMENT,
                status=CoverageStatus.COMPLETE,
                doi="10.5555/paper",
            ),
        ),
    )


def finished_snapshot(
    *,
    result: RunResult | None = None,
    unexpected_error: UnexpectedRunError | None = None,
) -> CoordinatorSnapshot:
    return CoordinatorSnapshot(
        status=CoordinatorStatus.FINISHED,
        progress_stage=ProgressStage.UPDATING_WORKSPACE,
        started_at=datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 9, 22, 12, 1, tzinfo=timezone.utc),
        result=result,
        unexpected_error=unexpected_error,
    )


class StubCoordinator:
    def __init__(
        self,
        *,
        snapshot: CoordinatorSnapshot | None = None,
        start_result: StartResult | None = None,
        snapshot_after_start: CoordinatorSnapshot | None = None,
    ) -> None:
        self.current_snapshot = snapshot or idle_snapshot()
        self.start_result = start_result or StartResult(StartOutcome.STARTED)
        self.snapshot_after_start = snapshot_after_start
        self.start_calls = 0
        self.snapshot_calls = 0

    def start(self) -> StartResult:
        self.start_calls += 1
        if self.snapshot_after_start is not None:
            self.current_snapshot = self.snapshot_after_start
        return self.start_result

    def snapshot(self) -> CoordinatorSnapshot:
        self.snapshot_calls += 1
        return self.current_snapshot


def test_app_constructs_exactly_one_coordinator_with_resolved_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Path] = []

    class CountingCoordinator(StubCoordinator):
        def __init__(self, config_path: Path) -> None:
            created.append(config_path)
            super().__init__()

    monkeypatch.setattr(web_app, "RunCoordinator", CountingCoordinator)

    app = create_app(tmp_path / "nested" / ".." / "monitor.yaml")

    assert created == [(tmp_path / "monitor.yaml").resolve()]
    assert isinstance(app.state.run_coordinator, CountingCoordinator)


@pytest.mark.parametrize("change", ["counter", "age", "operation", "retry", "add", "remove", "inactive"])
def test_announcement_key_uses_all_sources_but_excludes_counters_and_ages(change):
    now = datetime(2026, 9, 22, 12, 1, 30, tzinfo=timezone.utc)
    oa = activity_snapshot(source="openalex", operation="works", label="Discovering works")
    cr = activity_snapshot(source="crossref", operation="manifest", label="Retrieving manifest")
    first = replace(running_snapshot(), activities=(oa, cr))
    if change == "counter":
        updated = replace(first, activities=(replace(oa, current=19, eta_seconds=2), cr))
    elif change == "age":
        updated = replace(first, activities=(replace(oa, updated_at=now), replace(cr, updated_at=now)))
    elif change == "operation":
        updated = replace(first, activities=(replace(oa, operation="next_work_batch"), cr))
    elif change == "retry":
        updated = replace(first, activities=(oa, replace(cr, kind=ActivityKind.RETRYING)))
    elif change == "add":
        updated = replace(first, activities=(oa, cr, activity_snapshot(source="custom")))
    elif change == "remove":
        updated = replace(first, activities=(cr,))
    else:
        updated = replace(first, inactivity_warning=True)
    before = build_run_presentation(first, now=now)
    after = build_run_presentation(updated, now=now)
    if change in {"counter", "age"}:
        assert before["announcement_key"] == after["announcement_key"]
    else:
        assert before["announcement_key"] != after["announcement_key"]
    assert "OpenAlex." in before["announcement_message"] and "Crossref." in before["announcement_message"]
    assert build_run_presentation(first, now=now + timedelta(seconds=1))["announcement_key"] == before["announcement_key"]
    if change == "inactive":
        recovered = build_run_presentation(replace(updated, inactivity_warning=False), now=now)
        assert recovered["announcement_key"] == before["announcement_key"]


def test_run_presentation_semantic_key_ignores_timer_and_counter_only_changes() -> None:
    first_activity = activity_snapshot(current=18, total=47)
    first = running_snapshot(
        ProgressStage.COMBINING_METADATA,
        activity=first_activity,
        last_activity_at=first_activity.updated_at,
    )
    later_activity = replace(
        first_activity,
        current=19,
        eta_seconds=20.0,
    )
    later = replace(first, activities=(later_activity,))
    now = datetime(2026, 9, 22, 12, 1, 24, tzinfo=timezone.utc)

    first_view = build_run_presentation(first, now=now)
    later_view = build_run_presentation(later, now=now)
    timer_view = build_run_presentation(
        first,
        now=now.replace(second=25),
    )

    assert first_view["announcement_key"] == later_view["announcement_key"]
    assert first_view["announcement_key"] == timer_view["announcement_key"]
    assert first_view["elapsed_text"] != timer_view["elapsed_text"]
    assert first_view["last_activity_text"] != timer_view["last_activity_text"]

    inactive_view = build_run_presentation(
        replace(later, inactivity_warning=True),
        now=now,
    )
    retry_view = build_run_presentation(
        replace(
            later,
            activities=(replace(
                later_activity,
                kind=ActivityKind.RETRYING,
                eta_seconds=None,
            ),),
        ),
        now=now,
    )
    next_stage_view = build_run_presentation(
        replace(
            later,
            progress_stage=ProgressStage.MATCHING_LITERATURE,
            stage_index=3,
        ),
        now=now,
    )
    changed_activity_view = build_run_presentation(
        replace(
            later,
            activities=(activity_snapshot(
                operation="materialize_write",
                label="Writing workspace",
                source="workspace",
                current=0,
                total=3,
                unit="file",
                eta_seconds=None,
            ),),
        ),
        now=now,
    )
    finished_view = build_run_presentation(
        finished_snapshot(result=make_run_result()),
        now=now,
    )

    assert inactive_view["announcement_key"] != later_view["announcement_key"]
    assert retry_view["announcement_key"] != later_view["announcement_key"]
    assert next_stage_view["announcement_key"] != later_view["announcement_key"]
    assert changed_activity_view["announcement_key"] != later_view["announcement_key"]
    assert finished_view["announcement_key"] != later_view["announcement_key"]
    assert "No recent activity." in inactive_view["announcement_message"]
    assert "Retrying." in retry_view["announcement_message"]
    assert "Run finished:" in finished_view["announcement_message"]


_EMPTY_CURRENT_TABLES = (
    "\n## Publishers\n\n| Publisher | OpenAlex ID | Publisher URL | Access Service ID |\n"
    "| --- | --- | --- | --- |\n\n"
    "## Access Services\n\n| Service ID | Service | Access URL |\n"
    "| --- | --- | --- |\n"
)


def write_current_journals(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Build a canonical §43 file for legacy UI regression fixtures."""
    assert "## Publishers" not in content and "## Access Services" not in content
    path.write_text(content.rstrip("\n") + "\n" + _EMPTY_CURRENT_TABLES, encoding=encoding)


@pytest.fixture
def health_config(tmp_path: Path) -> Path:
    write_current_journals(tmp_path / "list.md",
        '## Journals\n\n| Journal | ISSN-L | Publisher ID | Group |\n|---|---|---|---|\n| Biometrics | 0006-341X |  |  |\n',
    )
    config_path = tmp_path / "monitor.yaml"
    config_path.write_text(
        "name: Health Monitor\nvenue_whitelist: list.md\nkeyword_expression: causal\n"
        "output_dir: saved-workspace\nfrom_month: 2026-01\nto_month: 2026-01\n",
        encoding="utf-8",
    )
    return config_path


def broken_health_paper(config_path: Path, workspace: str) -> Path:
    papers = config_path.parent / workspace / "Papers"
    papers.mkdir(parents=True)
    path = papers / f"{workspace}-broken.md"
    path.write_text("---\ntype: paper\n", encoding="utf-8")
    return path


def test_trusted_hosts_remain_local_only(tmp_path: Path) -> None:
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        localhost = client.get("/", headers={"host": "localhost:8000"})
        loopback = client.get("/", headers={"host": "127.0.0.1:8000"})
        testserver = client.get("/", headers={"host": "testserver"})
        external = client.get("/", headers={"host": "external.example"})

    assert localhost.status_code == 200
    assert loopback.status_code == 200
    assert testserver.status_code == 400
    assert external.status_code == 400


def test_group_form_projection_exact_identity_and_noop_order():
    groups = ("Statistics", None, "statistics", "Statistics", "Ungrouped", "Unmapped journals", '统计 & <x> "y"')
    identifiers = ("0006-341X", "0090-5364", "0162-1459", "0033-3123", "0092-5853", "1932-6157", "1941-7330")
    draft = make_draft(journals=tuple(
        JournalConfig(name=f"Journal {i}", issn_l=issn, group=group)
        for i, (issn, group) in enumerate(zip(identifiers, groups))
    ))
    values = settings_form.settings_form_from_draft(draft)
    assert values.groups == ("Statistics", "statistics", "Ungrouped", "Unmapped journals", '统计 & <x> "y"')
    assert settings_form.settings_draft_from_form(values) == (draft, ())
    # Empty presentation entries are not application state.
    assert settings_form.settings_draft_from_form(replace(values, groups=("Empty", *values.groups))) == (draft, ())


def test_submitted_empty_groups_order_duplicates_and_missing_assignment():
    pairs = list(valid_settings_form("unused", journal_group='统计 & <x> "y"').items()) + [
        ("settings_group", "Empty"), ("settings_group", "statistics"),
        ("settings_group", "Empty"), ("settings_group", "Statistics"),
    ]
    values = settings_form.settings_form_from_submission(FormData(pairs))
    assert values.groups == ("Empty", "statistics", "Statistics", '统计 & <x> "y"')
    draft, issues = settings_form.settings_draft_from_form(values)
    assert not issues and draft.journals[0].group == '统计 & <x> "y"'
    assert draft.monitor_revision == revision(True, "monitor-old")
    assert draft.journal_revision == revision(True, "journal-old")


def assert_import_does_not_write(config_path, before):
    assert (config_path.read_bytes(), (config_path.parent / "list.md").read_bytes()) == before
    assert sorted(p.name for p in config_path.parent.iterdir()) == ["list.md", "monitor.yaml"]


@pytest.mark.parametrize("route", ["preview", "confirm"])
@pytest.mark.parametrize("csrf", [None, "wrong", "不是 token"])
def test_import_csrf_precedes_core_and_has_no_side_effects(health_config, monkeypatch, route, csrf):
    def forbidden(*args, **kwargs): pytest.fail("CSRF failure must not invoke the import core")
    monkeypatch.setattr(web_app, "preview_full_markdown", forbidden)
    monkeypatch.setattr(web_app, "confirm_full_markdown", forbidden)
    before = (health_config.read_bytes(), (health_config.parent / "list.md").read_bytes())
    with TestClient(create_app(health_config), base_url="http://localhost") as client:
        data = valid_settings_form(csrf or "unused")
        if csrf is None: data.pop("csrf_token")
        response = client.post(f"/settings/import/{route}", data=data)
        assert response.status_code == 403 and "CSRF" in response.json()["message"]
        assert client.post(f"/settings/import/{route}", data=data, headers={"Host": "evil.example"}).status_code == 400
    assert_import_does_not_write(health_config, before)


@pytest.fixture
def zotero_settings(health_config, monkeypatch):
    """Synthetic read-only Zotero Local API status boundary."""
    from types import SimpleNamespace
    import httpx
    from literature_monitor.zotero_local import ZoteroLocalClient

    state = SimpleNamespace(
        server="settings-instance-A",
        probe_status=200,
        requests=[],
        secret="SETTINGSSENTINELSECRET",
    )

    def respond(request):
        state.requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "localhost" and request.url.port == 23119
        assert request.url.path == "/api/"
        assert "Cookie" not in request.headers
        assert "Authorization" not in request.headers
        assert "Zotero-API-Key" not in request.headers
        return httpx.Response(
            state.probe_status,
            headers={"Zotero-Server-ID": state.server},
            json={"private": state.secret},
        )

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(
        web_app,
        "ZoteroLocalClient",
        lambda: ZoteroLocalClient(transport=transport),
    )
    state.app = create_app(health_config)
    return state
