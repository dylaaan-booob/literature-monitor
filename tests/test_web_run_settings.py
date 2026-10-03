from __future__ import annotations

import inspect
import json
import re
import shutil
import subprocess
from html import escape
from html.parser import HTMLParser
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
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
    SettingsIssueSource,
    SettingsLoadResult,
    SettingsSaveOutcome,
    SettingsSaveResult,
    SettingsValidationOutcome,
    SettingsValidationResult,
    load_settings,
)
from literature_monitor.config import JournalConfig, LogLevel, load_config, parse_monitor_definition
from literature_monitor.coverage import CoverageComponent, CoverageStatus, CoverageUnit
from literature_monitor.date_range import DateRangeSpec, ResolvedDateRange
from literature_monitor.diagnostics import RunDiagnostic, RunDiagnosticKind
from literature_monitor.progress import (
    PROGRESS_STAGES,
    ActivityKind,
    ActivitySnapshot,
    ActivityUpdate,
    ProgressEvent,
    ProgressState,
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


def csrf_from_html(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


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
        or (JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        date_spec=DateRangeSpec(window_days=14),
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
        "from_date": "",
        "to_date": "",
        "window_days": "21",
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
        from_date="",
        to_date="",
        window_days="14",
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
            "window_days": 14,
        },
    )

    assert issues == ()
    assert draft is not None
    assert draft.output_dir == runtime_definition.output_dir == expected


def test_settings_draft_form_round_trip_preserves_groups_and_revisions() -> None:
    draft = make_draft(journals=(
        JournalConfig(name="Biometrics", issn=("0006-341X",), group="统计 & <Models> \"B\""),
        JournalConfig(name="Annals of Applied Statistics", issn=("1932-6157", "1941-7330")),
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
    submission = list(valid_settings_form("unused").items()) + [
        ("journal_group", group_value),
        ("journal_name", "Annals of Applied Statistics"),
        ("journal_issns", "1932-6157, 1941-7330"),
        ("journal_group", ""),
    ]

    values = settings_form.settings_form_from_submission(FormData(submission))
    draft, issues = settings_form.settings_draft_from_form(values)

    assert issues == ()
    assert draft is not None
    assert draft.journals == (
        JournalConfig(name="Biometrics", issn=("0006-341X",), group=group_value.strip() or None),
        JournalConfig(name="Annals of Applied Statistics", issn=("1932-6157", "1941-7330")),
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
        updated_papers=2,
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


def test_run_post_requires_csrf_before_start(tmp_path: Path) -> None:
    app = create_app(tmp_path / "monitor.yaml")
    coordinator = StubCoordinator()
    app.state.run_coordinator = coordinator

    with TestClient(app, base_url="http://localhost") as client:
        response = client.post("/run")

    assert response.status_code == 403
    assert coordinator.start_calls == 0


def test_run_form_has_only_run_and_requires_no_mode_field(tmp_path):
    app = create_app(tmp_path / "monitor.yaml")
    coordinator = StubCoordinator()
    app.state.run_coordinator = coordinator
    with TestClient(app, base_url="http://localhost") as client:
        html = client.get("/fragments/run").text
        assert ">Run</button>" in html
        assert "reuse_provider_cache" not in html and "cache reuse" not in html
        response = client.post("/run", data={"csrf_token": csrf_from_html(html)})
    assert response.status_code == 200 and coordinator.start_calls == 1
    assert not (tmp_path / "monitor.yaml").exists()


def test_provider_state_and_coverage_are_only_in_current_run(tmp_path):
    from literature_monitor.application.monitor import ProviderStateUsage
    result = replace(make_run_result(), state_usage=ProviderStateUsage(1, 2, 3))
    app = create_app(tmp_path / "monitor.yaml")
    app.state.run_coordinator = StubCoordinator(snapshot=finished_snapshot(result=result))
    with TestClient(app, base_url="http://localhost") as client:
        primary = client.get("/fragments/run")
        response = client.get("/fragments/current-run")
    assert "Crossref metadata 1 reused · 2 refreshed · 3 new" in response.text
    assert "OpenAlex versions" not in response.text
    assert "Live OpenAlex coverage:" in response.text
    assert ">Run again</button>" in primary.text
    assert "Provider state:" not in primary.text
    assert "coverage:" not in primary.text
    assert "Cache reuse:" not in response.text and "reuse_provider_cache" not in response.text


@pytest.mark.parametrize(
    ("outcome", "snapshot", "text"),
    (
        (
            StartOutcome.STARTED,
            running_snapshot(ProgressStage.CHECKING_MONITOR),
            "Run in progress",
        ),
        (
            StartOutcome.ALREADY_RUNNING,
            running_snapshot(ProgressStage.DISCOVERING_PAPERS),
            "already active",
        ),
        (
            StartOutcome.START_FAILED,
            finished_snapshot(
                unexpected_error=UnexpectedRunError(
                    category="RuntimeError",
                    message="The monitor worker could not be started.",
                )
            ),
            "could not be started",
        ),
    ),
)
def test_run_post_start_outcomes_are_normal_html(
    outcome: StartOutcome,
    snapshot: CoordinatorSnapshot,
    text: str,
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "monitor.yaml")
    coordinator = StubCoordinator(
        start_result=StartResult(outcome),
        snapshot_after_start=snapshot,
    )
    app.state.run_coordinator = coordinator

    with TestClient(app, base_url="http://localhost") as client:
        run_fragment = client.get("/fragments/run")
        csrf = csrf_from_html(run_fragment.text)
        response = client.post(
            "/run",
            data={
                "csrf_token": csrf,
                "date_override": "2026-01-01",
                "provider": "unexpected",
            },
        )

    assert response.status_code == 200
    assert text in response.text
    assert coordinator.start_calls == 1
    assert "Traceback" not in response.text
    if outcome is StartOutcome.STARTED:
        assert response.headers["HX-Trigger"] == "runStarted"
    elif outcome is StartOutcome.START_FAILED:
        assert response.headers["HX-Trigger"] == "runStartFailed"
    else:
        assert "HX-Trigger" not in response.headers


def test_start_failed_refreshes_current_run_and_discards_previous_finished_details(tmp_path):
    diagnostic = RunDiagnostic(
        RunDiagnosticKind.SCOPE_DISPUTE, "Unique previous diagnostic", ("W-OLD",),
    )
    previous = replace(make_run_result(), diagnostics=(diagnostic,))
    failure = UnexpectedRunError(
        category="RuntimeError", message="The monitor worker could not be started.",
    )
    coordinator = StubCoordinator(
        snapshot=finished_snapshot(result=previous),
        start_result=StartResult(StartOutcome.START_FAILED),
        snapshot_after_start=finished_snapshot(unexpected_error=failure),
    )
    app = create_app(tmp_path / "monitor.yaml")
    app.state.run_coordinator = coordinator
    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/settings").text
        assert 'id="run-panel"' in page and 'id="current-run"' in page
        assert "Unique previous diagnostic" in page
        assert "one source warning" in page and "one source error" in page
        assert "coverage:" in page and "Provider state:" in page
        assert "runStartFailed from:body" in page
        response = client.post("/run", data={"csrf_token": csrf_from_html(page)})
        current = client.get("/fragments/current-run")

    assert response.headers["HX-Trigger"] == "runStartFailed"
    assert coordinator.current_snapshot.result is None
    assert "Run stopped" in current.text and failure.message in current.text
    assert failure.category in current.text
    for old_detail in (
        diagnostic.message, diagnostic.kind.value, "W-OLD", "one source warning",
        "one source error", "Diagnostics:", "Run issues", "coverage:", "Provider state:",
    ):
        assert old_detail not in current.text
    assert "Run in progress" not in current.text and "Traceback" not in current.text


def test_run_post_never_calls_run_monitor_directly() -> None:
    source = inspect.getsource(web_app)

    assert "run_monitor" not in source
    assert "date_override" not in inspect.getsource(web_app.create_app)


def test_idle_run_fragment_has_run_button_without_polling(tmp_path: Path) -> None:
    app = create_app(tmp_path / "monitor.yaml")
    app.state.run_coordinator = StubCoordinator(snapshot=idle_snapshot())

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/fragments/run")

    assert response.status_code == 200
    assert ">Run</button>" in response.text
    assert "every 750ms" not in response.text
    assert "HX-Trigger" not in response.headers


def test_running_run_fragment_renders_human_stage_and_htmx_polling(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "monitor.yaml")
    app.state.run_coordinator = StubCoordinator(
        snapshot=running_snapshot(ProgressStage.COMBINING_METADATA)
    )

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/fragments/run")

    assert response.status_code == 200
    assert "Stage 3 of 5" in response.text
    assert "Combining metadata" in response.text
    assert ProgressStage.COMBINING_METADATA.value not in response.text
    assert response.text.count('class="stage-segment') == 5
    assert 'aria-label="Workflow stage 3 of 5: Combining metadata"' in response.text
    assert 'hx-get="/fragments/run"' in response.text
    assert 'hx-trigger="every 750ms"' in response.text


def test_running_run_fragment_starts_without_blank_progress_panel(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "monitor.yaml")
    app.state.run_coordinator = StubCoordinator(
        snapshot=running_snapshot(None)
    )

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/fragments/run")

    assert response.status_code == 200
    assert "Run in progress" in response.text
    assert "Starting…" in response.text
    assert 'hx-trigger="every 750ms"' in response.text


def test_running_fragment_renders_determinate_activity_eta_and_timing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 22, 12, 1, 24, tzinfo=timezone.utc)
    monkeypatch.setattr(web_app, "_utc_now", lambda: now)
    activity = activity_snapshot()
    app = create_app(tmp_path / "monitor.yaml")
    app.state.run_coordinator = StubCoordinator(
        snapshot=running_snapshot(
            ProgressStage.COMBINING_METADATA,
            activity=activity,
            last_activity_at=activity.updated_at,
        )
    )

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/fragments/run")

    assert response.status_code == 200
    assert "Crossref" in response.text
    assert "Looking up Crossref DOI metadata" in response.text
    assert "DOI 10.5555/example" in response.text
    assert "18 / 47 DOI" in response.text
    assert 'value="18"' in response.text
    assert 'max="47"' in response.text
    assert 'aria-label="Activity progress: 18 / 47 DOI"' in response.text
    assert "Elapsed 1m 24s" in response.text
    assert "ETA 24s" in response.text
    assert "Last activity 2s ago" in response.text
    assert "doi_supplement" not in response.text


def test_running_fragment_handles_indeterminate_and_zero_work_safely(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "monitor.yaml")
    activity = activity_snapshot(
        current=3,
        total=None,
        unit="work",
        eta_seconds=None,
    )
    app.state.run_coordinator = StubCoordinator(
        snapshot=running_snapshot(
            activity=activity,
            last_activity_at=activity.updated_at,
        )
    )

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/fragments/run")

    assert "3 works processed" in response.text
    assert 'class="activity-progress"' not in response.text
    assert "3 /" not in response.text
    assert "ETA " not in response.text

    zero_activity = activity_snapshot(
        current=0,
        total=0,
        unit="file",
        eta_seconds=None,
    )
    app.state.run_coordinator = StubCoordinator(
        snapshot=running_snapshot(activity=zero_activity)
    )
    with TestClient(app, base_url="http://localhost") as client:
        zero_response = client.get("/fragments/run")

    assert "0 / 0 files" in zero_response.text
    assert 'class="activity-progress"' not in zero_response.text


def test_running_fragment_shows_estimating_for_working_activity(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "monitor.yaml")
    activity = activity_snapshot(current=1, total=4, eta_seconds=None)
    app.state.run_coordinator = StubCoordinator(
        snapshot=running_snapshot(activity=activity)
    )

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/fragments/run")

    assert "1 / 4 DOI" in response.text
    assert "Estimating…" in response.text
    assert "ETA " not in response.text


@pytest.mark.parametrize(
    ("kind", "state_text"),
    (
        (ActivityKind.RETRYING, "Retrying"),
        (ActivityKind.WAITING, "Waiting"),
    ),
)
def test_running_fragment_shows_non_working_state_without_stale_eta(
    kind: ActivityKind,
    state_text: str,
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "monitor.yaml")
    activity = activity_snapshot(kind=kind, eta_seconds=99.0)
    app.state.run_coordinator = StubCoordinator(
        snapshot=running_snapshot(activity=activity)
    )

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/fragments/run")

    assert state_text in response.text
    assert "ETA 1m" not in response.text
    assert "Estimating…" not in response.text


def test_inactivity_is_advisory_and_keeps_stage_and_activity_visible(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "monitor.yaml")
    activity = activity_snapshot(eta_seconds=24.0)
    app.state.run_coordinator = StubCoordinator(
        snapshot=running_snapshot(
            ProgressStage.DISCOVERING_PAPERS,
            activity=activity,
            inactivity_warning=True,
        )
    )

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/fragments/run")

    assert "Run in progress" in response.text
    assert "Discovering papers" in response.text
    assert activity.label in response.text
    assert "No recent activity" in response.text
    assert 'class="run-advisory warning"' in response.text
    assert 'class="run-advisory error"' not in response.text
    assert "ETA 24s" not in response.text


def test_running_snapshot_with_dead_worker_is_stopped_not_in_progress(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "monitor.yaml")
    app.state.run_coordinator = StubCoordinator(
        snapshot=running_snapshot(worker_alive=False)
    )

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/fragments/run")

    assert "Run stopped" in response.text
    assert "The monitor worker is no longer active." in response.text
    assert "Run in progress" not in response.text
    assert "No recent activity" not in response.text


def test_concurrent_provider_fragment_has_independent_rows_ages_and_eta(tmp_path, monkeypatch):
    now = datetime(2026, 9, 22, 12, 1, 30, tzinfo=timezone.utc)
    oa = replace(activity_snapshot(
        source="openalex", operation="works", label="Discovering OpenAlex works",
        current=20, total=100, unit="work", eta_seconds=8,
    ), updated_at=now - timedelta(seconds=70))
    cr = replace(activity_snapshot(
        source="crossref", operation="manifest", label="Retrieving Crossref manifest",
        current=35, total=200, unit="work", eta_seconds=None,
    ), updated_at=now - timedelta(seconds=5))
    snapshot = replace(running_snapshot(last_activity_at=cr.updated_at), activities=(oa, cr))
    app = create_app(tmp_path / "monitor.yaml")
    app.state.run_coordinator = StubCoordinator(snapshot=snapshot)
    monkeypatch.setattr(web_app, "_utc_now", lambda: now)
    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/fragments/run")
        repeated = client.get("/fragments/run")
    oa_card, cr_card = response.text.split('<div class="activity-card">')[1:]
    assert "OpenAlex" in oa_card and "Discovering OpenAlex works" in oa_card
    assert "20 / 100 works" in oa_card and "ETA 8s" in oa_card
    assert "Updated 1m 10s ago" in oa_card
    assert "Estimating…" not in oa_card
    assert "Crossref" in cr_card and "Retrieving Crossref manifest" in cr_card
    assert "35 / 200 works" in cr_card and "Estimating…" in cr_card
    assert "Updated 5s ago" in cr_card and "ETA 8s" not in cr_card
    assert response.text.count('<progress class="activity-progress"') == 2
    assert "Elapsed 1m 30s" in response.text and "Last activity 5s ago" in response.text
    assert "No recent activity" not in response.text
    assert 'hx-trigger="every 750ms"' in response.text
    assert repeated.text == response.text
    assert app.state.run_coordinator.snapshot().activities == snapshot.activities
    inactive = build_run_presentation(replace(snapshot, inactivity_warning=True), now=now)
    assert all(row["eta_text"] is None and not row["estimating"] for row in inactive["activities"])


def test_recovered_web_rows_keep_quiet_provider_age_without_stale_eta(tmp_path, monkeypatch):
    base = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
    state = ProgressState()
    state.apply(ProgressEvent(stage=ProgressStage.DISCOVERING_PAPERS), at=base)
    def report(source, current, seconds):
        state.apply(ProgressEvent(activity=ActivityUpdate(
            kind=ActivityKind.WORKING, source=source, operation="works",
            label=f"{source} records", current=current, total=10, unit="work",
        )), at=base + timedelta(seconds=seconds))
    for current in range(3):
        for source in ("openalex", "crossref"):
            report(source, current, current + 1)
    assert all(activity.eta_seconds is not None for activity in state.snapshot(
        at=base + timedelta(seconds=3), active=True,
    ).activities)
    inactive = state.snapshot(at=base + timedelta(seconds=63), active=True)
    assert inactive.inactivity_warning
    assert all(activity.eta_seconds is None for activity in inactive.activities)
    report("openalex", 3, 64)
    now = base + timedelta(seconds=64)
    recovered = state.snapshot(at=now, active=True)
    snapshot = replace(
        running_snapshot(started_at=base, last_activity_at=recovered.last_activity_at),
        activities=recovered.activities, inactivity_warning=recovered.inactivity_warning,
    )
    view = build_run_presentation(snapshot, now=now)
    assert len(view["activities"]) == 2 and not snapshot.inactivity_warning
    quiet = next(row for row in view["activities"] if row["activity"].source == "crossref")
    assert quiet["age_text"] == "1m 01s" and quiet["eta_text"] is None
    app = create_app(tmp_path / "monitor.yaml")
    app.state.run_coordinator = StubCoordinator(snapshot=snapshot)
    monkeypatch.setattr(web_app, "_utc_now", lambda: now)
    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/fragments/run")
    assert response.text.count('<div class="activity-card">') == 2
    assert "OpenAlex" in response.text and "Crossref" in response.text
    assert "Updated 1m 01s ago" in response.text and "Updated 0s ago" in response.text
    assert "ETA " not in response.text and "No recent activity" not in response.text
    assert "Last activity 0s ago" in response.text


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
            stage_index=4,
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


def test_run_fragment_polling_is_read_only_and_timer_changes_keep_announcement_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activity = activity_snapshot()
    snapshot = running_snapshot(
        activity=activity,
        last_activity_at=activity.updated_at,
    )
    coordinator = StubCoordinator(snapshot=snapshot)
    times = iter(
        (
            datetime(2026, 9, 22, 12, 1, 24, tzinfo=timezone.utc),
            datetime(2026, 9, 22, 12, 1, 25, tzinfo=timezone.utc),
        )
    )
    monkeypatch.setattr(web_app, "_utc_now", lambda: next(times))
    app = create_app(tmp_path / "monitor.yaml")
    app.state.run_coordinator = coordinator

    with TestClient(app, base_url="http://localhost") as client:
        first = client.get("/fragments/run")
        second = client.get("/fragments/run")

    key_pattern = r'data-run-announcement-key="([^"]+)"'
    first_key = re.search(key_pattern, first.text)
    second_key = re.search(key_pattern, second.text)
    assert first_key is not None
    assert second_key is not None
    assert first_key.group(1) == second_key.group(1)
    assert "Elapsed 1m 24s" in first.text
    assert "Elapsed 1m 25s" in second.text
    assert coordinator.snapshot_calls == 2
    assert coordinator.current_snapshot.last_activity_at == activity.updated_at
    assert coordinator.current_snapshot.worker_alive


def test_run_progress_accessibility_uses_persistent_semantic_announcer(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "monitor.yaml")
    app.state.run_coordinator = StubCoordinator(snapshot=running_snapshot())

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/")
        fragment = client.get("/fragments/run")

    assert 'id="run-live-announcer"' in page.text
    assert 'aria-live="polite"' in page.text
    assert 'aria-atomic="true"' in page.text
    assert 'class="run-status" aria-live=' not in fragment.text
    assert "data-run-announcement-key=" in fragment.text
    assert "data-run-announcement=" in fragment.text
    js_path = Path(web_app.__file__).resolve().parent / "static" / "app.js"
    javascript = js_path.read_text(encoding="utf-8")
    assert "lastRunAnnouncementKey" in javascript
    assert "key === lastRunAnnouncementKey" in javascript
    assert "panel.dataset.runAnnouncementKey" in javascript
    assert "announcer.textContent = message" in javascript


def test_finished_run_fragment_stops_polling_and_emits_completion_event(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "monitor.yaml")
    app.state.run_coordinator = StubCoordinator(
        snapshot=finished_snapshot(result=make_run_result())
    )

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/fragments/run")

    assert response.status_code == 200
    assert "every 750ms" not in response.text
    assert response.headers["HX-Trigger"] == "runCompleted"


def test_immediately_finished_started_run_emits_completion_event(tmp_path: Path) -> None:
    app = create_app(tmp_path / "monitor.yaml")
    app.state.run_coordinator = StubCoordinator(
        start_result=StartResult(StartOutcome.STARTED),
        snapshot_after_start=finished_snapshot(result=make_run_result()),
    )

    with TestClient(app, base_url="http://localhost") as client:
        csrf = csrf_from_html(client.get("/fragments/run").text)
        response = client.post("/run", data={"csrf_token": csrf})
        current = client.get("/fragments/current-run")

    assert response.headers["HX-Trigger"] == "runStarted, runCompleted"
    assert "one source warning" in current.text
    assert "Run in progress" not in current.text


def test_finished_primary_run_keeps_summary_and_counts_without_technical_details(tmp_path: Path) -> None:
    app = create_app(tmp_path / "monitor.yaml")
    app.state.run_coordinator = StubCoordinator(
        snapshot=finished_snapshot(result=make_run_result())
    )

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/fragments/run")

    assert "COMPLETED_WITH_ERRORS" in response.text
    assert "2026-09-01" in response.text
    assert "2026-09-22" in response.text
    assert "8 canonical" in response.text
    assert "3 created" in response.text
    assert "4 matched" in response.text
    assert "2 updated" in response.text
    assert "1 warning" in response.text
    assert "1 error" in response.text
    for technical_detail in (
        "Authors:", "one source warning", "one source error", "coverage:",
        "Provider state:", "Diagnostics:", "Run diagnostics", "Run issues",
    ):
        assert technical_detail not in response.text


def test_finished_current_run_keeps_full_issues_coverage_and_author_summary(tmp_path: Path) -> None:
    app = create_app(tmp_path / "monitor.yaml")
    app.state.run_coordinator = StubCoordinator(snapshot=finished_snapshot(result=make_run_result()))
    with TestClient(app, base_url="http://localhost") as client:
        text = client.get("/fragments/current-run").text

    for detail in (
        "Authors: 5 created · 6 existing", "one source warning", "one source error",
        "OpenAlex coverage: 1/2 complete · 1 failed",
        "Crossref discovery coverage: 0/1 complete · 1 unavailable",
        "Crossref supplement coverage: 1/1 complete",
    ):
        assert detail in text


@pytest.mark.parametrize("with_issues", [False, True])
def test_finished_run_diagnostics_are_separate_logical_groups_with_context(tmp_path, with_issues):
    diagnostics = (
        RunDiagnostic(RunDiagnosticKind.REPEATED_TITLE_SEPARATION, "Independent works retained", ("W1", "W2", "W3")),
        RunDiagnostic(RunDiagnosticKind.NON_CANDIDATE_EXCLUSION, "Issue volume excluded", ("W4", "10.5555/a", "W5"), "Biometrics"),
        RunDiagnostic(RunDiagnosticKind.SCOPE_DISPUTE, "Scope unresolved context", ("W6", "10.5555/b"), "Biometrics"),
        RunDiagnostic(RunDiagnosticKind.NON_CANDIDATE_EXCLUSION, "Another volume excluded", ("W7",)),
    )
    result = replace(make_run_result(), diagnostics=diagnostics)
    if not with_issues:
        result = replace(result, warnings=(), errors=(), outcome=RunOutcome.COMPLETED,
                         coverage=(CoverageUnit("openalex", CoverageComponent.OPENALEX_DISCOVERY,
                                                CoverageStatus.COMPLETE, journal="Biometrics"),))
    app = create_app(tmp_path / "monitor.yaml")
    app.state.run_coordinator = StubCoordinator(snapshot=finished_snapshot(result=result))
    with TestClient(app, base_url="http://localhost") as client:
        for _ in range(2):
            primary = client.get("/fragments/run").text
            response = client.get("/fragments/current-run")
            assert response.status_code == 200
            text = response.text
            assert f"Run finished · {result.outcome.value}" in primary
            assert "Diagnostics: 4 logical groups" in text
            labels = ("non_candidate_exclusion · 2 logical groups", "scope_dispute · 1 logical group",
                      "repeated_title_separation · 1 logical group")
            assert all(label in text for label in labels)
            assert text.index(labels[0]) < text.index(labels[1]) < text.index(labels[2])
            assert "conflicting_doi_separation" not in text
            details = re.search(r"<summary>Run diagnostics</summary>(.*?)</details>", text, re.S)
            assert details is not None
            diagnostic_context = details.group(1)
            assert "journal=Biometrics" in diagnostic_context
            assert "records=W4, 10.5555/a, W5" in diagnostic_context
            assert "Scope unresolved context" in diagnostic_context
            assert "Warning ·" not in diagnostic_context and "Error ·" not in diagnostic_context
            assert ("Run issues" in text) is with_issues
            assert ("<span>1 warning</span>" in primary) is with_issues
            assert ("<span>1 error</span>" in primary) is with_issues
            assert "Diagnostics:" not in primary and "Run diagnostics" not in primary
            assert "coverage:" not in primary and "Provider state:" not in primary
            for diagnostic in diagnostics:
                assert diagnostic.kind.value not in primary
                assert diagnostic.message not in primary
            assert "records=" not in primary and "journal=Biometrics" not in primary
            if with_issues:
                issues = re.search(r"<summary>Run issues</summary>(.*?)</details>", text, re.S).group(1)
                assert "one source warning" in issues and "one source error" in issues
                assert all(d.message not in issues and d.kind.value not in issues for d in diagnostics)
            else:
                assert "Warning ·" not in text and "Error ·" not in text
                assert "OpenAlex coverage: 1/1 complete" in text
    assert app.state.run_coordinator.current_snapshot.result is result


def test_run_diagnostic_context_uses_normal_template_escaping(tmp_path):
    diagnostic = RunDiagnostic(
        RunDiagnosticKind.CONFLICTING_DOI_SEPARATION,
        "<script>alert(1)</script>",
        ("<img src=x onerror=alert(1)>", "W<2>&3"),
        "<b>Biometrics</b> & Other",
    )
    result = replace(make_run_result(), warnings=(), errors=(), outcome=RunOutcome.COMPLETED,
                     diagnostics=(diagnostic,))
    app = create_app(tmp_path / "monitor.yaml")
    app.state.run_coordinator = StubCoordinator(snapshot=finished_snapshot(result=result))
    with TestClient(app, base_url="http://localhost") as client:
        text = client.get("/fragments/current-run").text
    assert "Diagnostics: 1 logical group" in text
    assert "conflicting_doi_separation · 1 logical group" in text
    for value in (diagnostic.message, diagnostic.journal, *diagnostic.record_ids):
        assert value not in text and escape(value) in text


@pytest.mark.parametrize(
    "outcome",
    (RunOutcome.COMPLETED, RunOutcome.COMPLETED_WITH_WARNINGS,
     RunOutcome.COMPLETED_WITH_ERRORS, RunOutcome.INVALID_CONFIGURATION),
)
def test_primary_run_outcome_presentation_is_independent_of_diagnostics(tmp_path, outcome):
    result = make_run_result()
    result = replace(
        result,
        outcome=outcome,
        warnings=result.warnings if outcome is not RunOutcome.COMPLETED else (),
        errors=result.errors if outcome in (RunOutcome.COMPLETED_WITH_ERRORS, RunOutcome.INVALID_CONFIGURATION) else (),
    )
    app = create_app(tmp_path / "monitor.yaml")
    coordinator = StubCoordinator(snapshot=finished_snapshot(result=result))
    app.state.run_coordinator = coordinator
    with TestClient(app, base_url="http://localhost") as client:
        before = client.get("/fragments/run").text
        diagnostic = RunDiagnostic(RunDiagnosticKind.SCOPE_DISPUTE, "Diagnostic context", ("W1",))
        coordinator.current_snapshot = finished_snapshot(result=replace(result, diagnostics=(diagnostic,)))
        after = client.get("/fragments/run").text

    assert after == before
    assert outcome.value in after
    assert ("1 warning" in after) is bool(result.warnings)
    assert ("1 error" in after) is bool(result.errors)
    assert "Diagnostic context" not in after
    if outcome is RunOutcome.INVALID_CONFIGURATION:
        assert "Configuration problem" in after
        assert 'class="run-message error"' in after
        assert "Run finished ·" not in after
    else:
        assert f"Run finished · {outcome.value}" in after


@pytest.mark.parametrize("status", (CoordinatorStatus.IDLE, CoordinatorStatus.RUNNING))
def test_current_run_empty_and_running_states_hide_all_finished_details(tmp_path, status):
    # Even an inconsistent stale-result snapshot cannot display old details while active/idle.
    snapshot = replace(
        running_snapshot(ProgressStage.DISCOVERING_PAPERS),
        status=status,
        result=make_run_result(),
    )
    app = create_app(tmp_path / "monitor.yaml")
    app.state.run_coordinator = StubCoordinator(snapshot=snapshot)
    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/fragments/current-run")

    expected = "Run in progress" if status is CoordinatorStatus.RUNNING else "No process-local run details."
    assert expected in response.text
    for detail in ("one source warning", "one source error", "coverage:", "Provider state:", "Stage ", "ETA", "activity-card"):
        assert detail not in response.text
    assert "HX-Trigger" not in response.headers
    assert "every " not in response.text


def test_current_run_settings_refresh_uses_the_same_coordinator_snapshot(tmp_path):
    diagnostic = RunDiagnostic(RunDiagnosticKind.SCOPE_DISPUTE, "Current diagnostic", ("W1",))
    result = replace(make_run_result(), diagnostics=(diagnostic,))
    coordinator = StubCoordinator(snapshot=finished_snapshot(result=result))
    app = create_app(tmp_path / "monitor.yaml")
    app.state.run_coordinator = coordinator
    with TestClient(app, base_url="http://localhost") as client:
        for surface in ("/settings", "/fragments/current-run", "/settings"):
            text = client.get(surface).text
            assert diagnostic.message in text
            assert "one source warning" in text and "one source error" in text
        page = client.get("/settings").text

    assert coordinator.snapshot_calls == 4
    assert coordinator.current_snapshot.result is result
    assert page.index('id="workspace-health"') < page.index('id="current-run"')
    assert 'hx-trigger="runStarted from:body, runCompleted from:body, runStartFailed from:body"' in page
    assert 'hx-sync="this:replace"' in page
    assert page.index('</form>') < page.index('id="advanced-diagnostics"') < page.index('id="current-run"')


def test_current_run_restart_never_restores_last_run_snapshot(health_config, monkeypatch):
    import literature_monitor.application.run_state as run_state

    result = make_run_result()
    output_dir = web_app.load_config(health_config).output_dir
    run_state.write_last_run_snapshot(output_dir, run_state.LastRunSnapshot(
        schema_version=2,
        resolved_date_range=result.resolved_date_range,
        outcome=run_state.RecordedRunOutcome.COMPLETED_WITH_ERRORS,
        coverage=result.coverage,
    ))
    snapshot_path = run_state.last_run_snapshot_path(output_dir)
    original = snapshot_path.read_bytes()

    def forbidden_read(*args, **kwargs):
        raise AssertionError("Web Current run must not read last-run.json")

    monkeypatch.setattr(run_state, "read_last_run_snapshot", forbidden_read)
    first = create_app(health_config)
    first.state.run_coordinator = StubCoordinator(snapshot=finished_snapshot(result=result))
    with TestClient(first, base_url="http://localhost") as client:
        assert "one source warning" in client.get("/settings").text
    restarted = create_app(health_config)
    with TestClient(restarted, base_url="http://localhost") as client:
        for surface in ("/settings", "/fragments/current-run"):
            text = client.get(surface).text
            assert "No process-local run details." in text
            assert "one source warning" not in text and "coverage:" not in text
    assert restarted.state.run_coordinator.snapshot().status is CoordinatorStatus.IDLE
    assert snapshot_path.read_bytes() == original


def test_current_run_start_and_completion_follow_real_coordinator(tmp_path, monkeypatch):
    import threading
    import literature_monitor.web.run_coordinator as coordinator_module

    entered, release = threading.Event(), threading.Event()
    workers = []
    previous = replace(make_run_result(), diagnostics=(
        RunDiagnostic(RunDiagnosticKind.SCOPE_DISPUTE, "Previous diagnostic details", ("W-OLD",)),
    ))
    current = replace(make_run_result(), diagnostics=(
        RunDiagnostic(RunDiagnosticKind.SCOPE_DISPUTE, "New diagnostic details", ("W-NEW",)),
    ))

    def runner(path, *, progress_callback):
        workers.append(threading.current_thread())
        if len(workers) == 1:
            return previous
        entered.set()
        assert release.wait(5)
        return current

    monkeypatch.setattr(coordinator_module, "run_monitor", runner)
    app = create_app(tmp_path / "monitor.yaml")
    coordinator = app.state.run_coordinator
    assert coordinator.start().outcome is StartOutcome.STARTED
    coordinator._worker.join(2)
    assert not coordinator._worker.is_alive()
    try:
        with TestClient(app, base_url="http://localhost") as client:
            csrf = csrf_from_html(client.get("/settings").text)
            assert "Previous diagnostic details" in client.get("/fragments/current-run").text
            started = client.post("/run", data={"csrf_token": csrf})
            assert started.headers["HX-Trigger"] == "runStarted"
            assert entered.wait(2)
            assert coordinator.snapshot().result is None
            active = client.get("/fragments/current-run").text
            assert "Run in progress" in active and "Previous diagnostic details" not in active
            repeated = client.post("/run", data={"csrf_token": csrf})
            assert "already active" in repeated.text and "HX-Trigger" not in repeated.headers
            release.set()
            workers[-1].join(2)
            assert not workers[-1].is_alive()
            completed = client.get("/fragments/run")
            assert completed.headers["HX-Trigger"] == "runCompleted"
            finished = client.get("/fragments/current-run").text
            assert "New diagnostic details" in finished
            assert "Previous diagnostic details" not in finished
    finally:
        release.set()
        for worker in workers:
            worker.join(2)


def test_unexpected_run_error_renders_only_safe_coordinator_text(tmp_path: Path) -> None:
    app = create_app(tmp_path / "monitor.yaml")
    app.state.run_coordinator = StubCoordinator(
        snapshot=finished_snapshot(
            unexpected_error=UnexpectedRunError(
                category="RuntimeError",
                message="The monitor run stopped because of an unexpected internal error.",
            )
        )
    )

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/fragments/run")
        current = client.get("/fragments/current-run")

    assert response.status_code == 200
    assert "unexpected internal error" in response.text
    assert "RuntimeError" in response.text
    assert "Traceback" not in response.text
    assert "unexpected internal error" in current.text
    assert "Traceback" not in current.text
    assert "Provider state:" not in current.text and "coverage:" not in current.text


def test_workspace_listens_for_run_completion_and_refreshes_through_workspace_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_calls: list[Path] = []
    monkeypatch.setattr(
        web_app,
        "load_config",
        lambda path: type("Config", (), {"output_dir": tmp_path / "workspace", "journals": ()})(),
    )
    monkeypatch.setattr(
        web_app,
        "load_workspace",
        lambda output_dir, *, journals: (
            load_calls.append(output_dir)
            or web_app.WorkspaceSnapshot(papers=(), issues=())
        ),
    )
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/")
        fragment = client.get("/fragments/workspace")

    assert 'hx-trigger="runCompleted from:body, settingsSaved from:body"' in page.text
    assert fragment.status_code == 200
    assert load_calls == [tmp_path / "workspace", tmp_path / "workspace"]


def test_settings_get_renders_structured_editor_and_exact_revisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = make_draft(
        journals=(
            JournalConfig(name="Biometrics", issn=("0006-341X",)),
            JournalConfig(
                name="Annals of Applied Statistics",
                issn=("1932-6157", "1941-7330"),
            ),
        )
    )
    state = make_settings_state(tmp_path, draft=draft)
    monkeypatch.setattr(web_app, "load_settings", lambda path: state)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/settings")

    assert response.status_code == 200
    assert 'id="settings-form"' in response.text
    assert 'value="Web Monitor"' in response.text
    assert 'value="causal"' in response.text
    assert 'value="workspace"' in response.text
    assert "Biometrics" in response.text
    assert "0006-341X" in response.text
    assert "Annals of Applied Statistics" in response.text
    assert "1932-6157, 1941-7330" in response.text
    assert 'name="monitor_revision_digest" value="monitor-old"' in response.text
    assert 'name="journal_revision_digest" value="journal-old"' in response.text
    assert "## Journals" not in response.text


@pytest.mark.parametrize("mode", ("missing", "malformed"))
def test_missing_or_malformed_monitor_remains_editable(
    mode: str,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "monitor.yaml"
    if mode == "malformed":
        config_path.write_text("keyword_expression: [\n", encoding="utf-8")
    (tmp_path / "list.md").write_text(
        """# List

## Journals

| Journal | ISSN/EISSN |
|---|---|
| Biometrics | 0006-341X |
""",
        encoding="utf-8",
    )
    app = create_app(config_path)

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/settings")

    assert response.status_code == 200
    assert 'id="settings-form"' in response.text
    assert "Configuration needs attention" in response.text


@pytest.mark.parametrize("mode", ("missing", "malformed"))
def test_missing_or_malformed_journal_remains_editable(
    mode: str,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "monitor.yaml"
    config_path.write_text(
        """name: Monitor
venue_whitelist: list.md
keyword_expression: causal
output_dir: workspace
window_days: 14
""",
        encoding="utf-8",
    )
    if mode == "malformed":
        (tmp_path / "list.md").write_text(
            "# List\n\n## Journals\n\nnot a table\n",
            encoding="utf-8",
        )
    app = create_app(config_path)

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/settings")

    assert response.status_code == 200
    assert 'id="settings-form"' in response.text
    assert "Configuration needs attention" in response.text


def test_settings_validate_requires_csrf_before_application_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def forbidden(*args: object, **kwargs: object) -> SettingsValidationResult:
        nonlocal calls
        calls += 1
        raise AssertionError("validation should not be called")

    monkeypatch.setattr(web_app, "validate_settings", forbidden)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.post(
            "/settings/validate",
            data=valid_settings_form("wrong"),
        )

    assert response.status_code == 403
    assert calls == 0


def test_validate_converts_current_unsaved_form_to_monitor_draft_without_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[MonitorDraft] = []
    save_calls = 0

    def fake_validate(
        config_path: Path,
        draft: MonitorDraft,
    ) -> SettingsValidationResult:
        captured.append(draft)
        return make_validation()

    def forbidden_save(*args: object, **kwargs: object) -> SettingsSaveResult:
        nonlocal save_calls
        save_calls += 1
        raise AssertionError("Validate must not save")

    monkeypatch.setattr(web_app, "validate_settings", fake_validate)
    monkeypatch.setattr(web_app, "save_settings", forbidden_save)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        csrf = csrf_from_html(client.get("/settings").text)
        response = client.post(
            "/settings/validate",
            data=valid_settings_form(csrf),
        )

    assert response.status_code == 200
    assert len(captured) == 1
    draft = captured[0]
    assert draft.name == "Unsaved Monitor"
    assert draft.keyword_expression == "causal AND inference"
    assert draft.journals == (
        JournalConfig(name="Biometrics", issn=("0006-341X",)),
    )
    assert draft.date_spec == DateRangeSpec(window_days=21)
    assert draft.output_dir == Path("edited-workspace")
    assert draft.log_level == "DEBUG"
    assert draft.monitor_revision == revision(True, "monitor-old")
    assert draft.journal_revision == revision(True, "journal-old")
    assert save_calls == 0
    assert "2026-09-09" in response.text
    assert "2026-09-22" in response.text
    assert "settingsSaved" not in response.headers.get("HX-Trigger", "")


@pytest.mark.parametrize("raw_output_dir", ("", "   "))
def test_validate_reaches_application_with_runtime_empty_path_semantics(
    raw_output_dir: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[MonitorDraft] = []

    def fake_validate(
        config_path: Path,
        draft: MonitorDraft,
    ) -> SettingsValidationResult:
        captured.append(draft)
        return make_validation()

    monkeypatch.setattr(web_app, "validate_settings", fake_validate)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        csrf = csrf_from_html(client.get("/settings").text)
        response = client.post(
            "/settings/validate",
            data=valid_settings_form(csrf, output_dir=raw_output_dir),
        )

    assert response.status_code == 200
    assert len(captured) == 1
    assert captured[0].output_dir == Path(".")
    assert "Output workspace must not be empty." not in response.text


def test_settings_form_has_no_gui_specific_output_path_validation() -> None:
    source = inspect.getsource(settings_form)

    assert "Output workspace must not be empty." not in source


def test_validate_failure_preserves_submitted_unsaved_values_and_revisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue = SettingsIssue(
        source=SettingsIssueSource.VALIDATION,
        field="keyword_expression",
        message="invalid keyword expression",
    )
    monkeypatch.setattr(
        web_app,
        "validate_settings",
        lambda config_path, draft: make_validation(
            outcome=SettingsValidationOutcome.INVALID,
            issues=(issue,),
        ),
    )
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        csrf = csrf_from_html(client.get("/settings").text)
        response = client.post(
            "/settings/validate",
            data=valid_settings_form(
                csrf,
                name="Still Unsaved",
                keyword_expression="alpha AND",
            ),
        )

    assert response.status_code == 200
    assert "invalid keyword expression" in response.text
    assert 'value="Still Unsaved"' in response.text
    assert 'value="alpha AND"' in response.text
    assert 'value="monitor-old"' in response.text
    assert 'value="journal-old"' in response.text


def test_invalid_form_syntax_is_normal_settings_state_without_validation_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def forbidden(*args: object, **kwargs: object) -> SettingsValidationResult:
        nonlocal calls
        calls += 1
        raise AssertionError("unconstructable draft must not reach validation")

    monkeypatch.setattr(web_app, "validate_settings", forbidden)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        csrf = csrf_from_html(client.get("/settings").text)
        response = client.post(
            "/settings/validate",
            data=valid_settings_form(
                csrf,
                from_date="not-a-date",
                window_days="many",
            ),
        )

    assert response.status_code == 200
    assert "valid ISO date" in response.text
    assert "must be an integer" in response.text
    assert 'value="not-a-date"' in response.text
    assert 'value="many"' in response.text
    assert calls == 0


def test_settings_save_requires_csrf_before_application_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def forbidden(*args: object, **kwargs: object) -> SettingsSaveResult:
        nonlocal calls
        calls += 1
        raise AssertionError("save should not be called")

    monkeypatch.setattr(web_app, "save_settings", forbidden)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.post(
            "/settings/save",
            data=valid_settings_form("wrong"),
        )

    assert response.status_code == 403
    assert calls == 0


def test_saved_result_uses_reread_state_new_revisions_and_saved_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved_draft = make_draft(
        name="Normalized Saved Monitor",
        monitor_digest="monitor-new",
        journal_digest="journal-new",
    )
    result = make_save_result(
        tmp_path,
        outcome=SettingsSaveOutcome.SAVED,
        state_draft=saved_draft,
        journal_written=True,
        monitor_written=True,
    )
    captured: list[MonitorDraft] = []
    monkeypatch.setattr(
        web_app,
        "save_settings",
        lambda config_path, draft: (captured.append(draft) or result),
    )
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        csrf = csrf_from_html(client.get("/settings").text)
        response = client.post(
            "/settings/save",
            data=valid_settings_form(csrf),
        )

    assert response.status_code == 200
    assert len(captured) == 1
    assert "Settings saved." in response.text
    assert 'value="Normalized Saved Monitor"' in response.text
    assert 'value="monitor-new"' in response.text
    assert 'value="journal-new"' in response.text
    assert response.headers["HX-Trigger"] == "settingsSaved"


def test_invalid_draft_save_preserves_submitted_values_and_old_revisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue = SettingsIssue(
        source=SettingsIssueSource.VALIDATION,
        field="keyword_expression",
        message="draft invalid",
    )
    result = make_save_result(
        tmp_path,
        outcome=SettingsSaveOutcome.INVALID_DRAFT,
        issues=(issue,),
    )
    monkeypatch.setattr(web_app, "save_settings", lambda config_path, draft: result)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        csrf = csrf_from_html(client.get("/settings").text)
        response = client.post(
            "/settings/save",
            data=valid_settings_form(
                csrf,
                name="Unsaved Invalid",
                keyword_expression="bad draft",
            ),
        )

    assert response.status_code == 200
    assert "invalid and was not saved" in response.text
    assert "draft invalid" in response.text
    assert 'value="Unsaved Invalid"' in response.text
    assert 'value="bad draft"' in response.text
    assert 'value="monitor-old"' in response.text
    assert "Settings saved." not in response.text


def test_revision_conflict_is_explicit_preserves_open_revisions_and_does_not_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue = SettingsIssue(
        source=SettingsIssueSource.STORAGE,
        message="Settings files changed after they were opened",
    )
    disk_draft = make_draft(
        name="External Disk Edit",
        monitor_digest="monitor-external",
        journal_digest="journal-external",
    )
    result = make_save_result(
        tmp_path,
        outcome=SettingsSaveOutcome.REVISION_CONFLICT,
        state_draft=disk_draft,
        issues=(issue,),
    )
    calls = 0

    def fake_save(config_path: Path, draft: MonitorDraft) -> SettingsSaveResult:
        nonlocal calls
        calls += 1
        return result

    monkeypatch.setattr(web_app, "save_settings", fake_save)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        csrf = csrf_from_html(client.get("/settings").text)
        response = client.post(
            "/settings/save",
            data=valid_settings_form(csrf, name="My Unsaved Edit"),
        )

    assert calls == 1
    assert "files changed since this editor was opened" in response.text
    assert 'value="My Unsaved Edit"' in response.text
    assert 'value="monitor-old"' in response.text
    assert "External Disk Edit" in response.text
    assert "monitor-external" in response.text
    assert "Settings saved." not in response.text


def test_write_failed_renders_failure_disk_state_and_attempted_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue = SettingsIssue(
        source=SettingsIssueSource.JOURNAL_DATA,
        message="unable to write journal data",
    )
    disk_draft = make_draft(
        name="Disk Monitor",
        monitor_digest="monitor-current",
        journal_digest="journal-current",
    )
    result = make_save_result(
        tmp_path,
        outcome=SettingsSaveOutcome.WRITE_FAILED,
        state_draft=disk_draft,
        issues=(issue,),
    )
    monkeypatch.setattr(web_app, "save_settings", lambda config_path, draft: result)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        csrf = csrf_from_html(client.get("/settings").text)
        response = client.post(
            "/settings/save",
            data=valid_settings_form(csrf, name="Attempted Monitor"),
        )

    assert "Settings could not be written." in response.text
    assert "unable to write journal data" in response.text
    assert "Disk Monitor" in response.text
    assert "Attempted Monitor" in response.text
    assert "Settings saved." not in response.text


def test_partial_save_is_warning_and_uses_returned_disk_state_revisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue = SettingsIssue(
        source=SettingsIssueSource.MONITOR_CONFIG,
        message="monitor write denied",
    )
    disk_draft = make_draft(
        name="Old Monitor From Disk",
        journals=(JournalConfig(name="Biometrics", issn=("0006-341X",)),),
        monitor_digest="monitor-after-partial",
        journal_digest="journal-after-partial",
    )
    result = make_save_result(
        tmp_path,
        outcome=SettingsSaveOutcome.PARTIAL_SAVE,
        state_draft=disk_draft,
        issues=(issue,),
        journal_written=True,
        monitor_written=False,
    )
    monkeypatch.setattr(web_app, "save_settings", lambda config_path, draft: result)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        csrf = csrf_from_html(client.get("/settings").text)
        response = client.post(
            "/settings/save",
            data=valid_settings_form(csrf, name="Attempted New Monitor"),
        )

    assert "partially saved" in response.text
    assert "Journal data written: yes" in response.text
    assert "Monitor config written: no" in response.text
    assert 'value="Old Monitor From Disk"' in response.text
    assert 'value="monitor-after-partial"' in response.text
    assert 'value="journal-after-partial"' in response.text
    assert "Attempted New Monitor" in response.text
    assert "Settings saved." not in response.text
    assert 'class="notice success"' not in response.text
    assert response.headers.get("HX-Trigger") != "settingsSaved"


def test_settings_route_delegates_save_once_and_contains_no_persistence_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = make_save_result(
        tmp_path,
        outcome=SettingsSaveOutcome.SAVED,
        state_draft=make_draft(monitor_digest="m-new", journal_digest="j-new"),
        journal_written=True,
        monitor_written=True,
    )
    calls = 0

    def fake_save(config_path: Path, draft: MonitorDraft) -> SettingsSaveResult:
        nonlocal calls
        calls += 1
        return result

    monkeypatch.setattr(web_app, "save_settings", fake_save)
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        csrf = csrf_from_html(client.get("/settings").text)
        response = client.post("/settings/save", data=valid_settings_form(csrf))

    source = inspect.getsource(web_app)
    assert response.status_code == 200
    assert calls == 1
    assert "safe_write" not in source
    assert "write_text" not in source
    assert "write_bytes" not in source
    assert "yaml.safe" not in source


def test_dirty_form_script_is_browser_only_and_save_event_driven(tmp_path: Path) -> None:
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/static/app.js")
        settings = client.get("/settings")

    assert response.status_code == 200
    script = response.text
    assert "beforeunload" in script
    assert 'addEventListener("input"' in script
    assert 'addEventListener("change"' in script
    assert 'addEventListener("settingsSaved"' in script
    assert "data-add-journal" in script
    assert "data-remove-journal" in script
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert 'hx-post="/settings/validate"' in settings.text
    assert 'hx-post="/settings/save"' in settings.text


@pytest.fixture
def health_config(tmp_path: Path) -> Path:
    (tmp_path / "list.md").write_text(
        "## Journals\n\n| Journal | ISSN/EISSN |\n|---|---|\n| Biometrics | 0006-341X |\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "monitor.yaml"
    config_path.write_text(
        "name: Health Monitor\nvenue_whitelist: list.md\nkeyword_expression: causal\n"
        "output_dir: saved-workspace\nwindow_days: 14\n",
        encoding="utf-8",
    )
    return config_path


def broken_health_paper(config_path: Path, workspace: str) -> Path:
    papers = config_path.parent / workspace / "Papers"
    papers.mkdir(parents=True)
    path = papers / f"{workspace}-broken.md"
    path.write_text("---\ntype: paper\n", encoding="utf-8")
    return path


def test_grouped_settings_web_validate_save_preserves_visible_assignment(health_config: Path) -> None:
    group = "统计 & <Models> \"B\""
    journal_path = health_config.parent / "list.md"
    journal_path.write_text(
        "## Journals\n\n| Journal | ISSN/EISSN | Group |\n|---|---|---|\n"
        f"| Biometrics | 0006-341X | {group} |\n",
        encoding="utf-8",
    )
    state = load_settings(health_config)
    before = (health_config.read_bytes(), journal_path.read_bytes())
    app = create_app(health_config)

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/settings")
        assert '<option value="统计 &amp; &lt;Models&gt; &#34;B&#34;" selected>' in page.text
        template = page.text.split("<template data-journal-template>", 1)[1].split("</template>", 1)[0]
        assert '<select name="journal_group"><option value="" selected>Ungrouped</option></select>' in template

        submitted = valid_settings_form(
            csrf_from_html(page.text),
            journal_group=group,
            monitor_revision_digest=state.draft.monitor_revision.digest,
            journal_revision_digest=state.draft.journal_revision.digest,
        )
        validated = client.post("/settings/validate", data=submitted)
        assert validated.status_code == 200
        assert "Settings draft is valid" in validated.text
        assert '<option value="统计 &amp; &lt;Models&gt; &#34;B&#34;" selected>' in validated.text
        assert (health_config.read_bytes(), journal_path.read_bytes()) == before

        saved = client.post("/settings/save", data=submitted)
        assert saved.status_code == 200
        assert saved.headers["HX-Trigger"] == "settingsSaved"
        assert "Settings saved." in saved.text
        assert '<option value="统计 &amp; &lt;Models&gt; &#34;B&#34;" selected>' in saved.text

    assert load_config(health_config).journals == (
        JournalConfig(name="Biometrics", issn=("0006-341X",), group=group),
    )


def test_advanced_is_collapsed_sibling_with_zotero_health_and_run(
    health_config: Path,
) -> None:
    app = create_app(health_config)
    with TestClient(app, base_url="http://localhost") as client:
        text = client.get("/settings").text

    assert "Advanced &amp; Diagnostics" in text
    assert "No issues detected." in text
    advanced = re.search(r'<details id="advanced-diagnostics"([^>]*)>(.*?)</details>', text, re.S)
    assert advanced is not None
    assert "open" not in advanced.group(1)
    assert re.search(r'</form>\s*</section>\s*<details id="advanced-diagnostics"', text)
    assert 'Zotero integration' in advanced.group(2)
    assert 'hx-get="/settings/zotero"' in advanced.group(2)
    assert 'Current run' in advanced.group(2)
    assert 'hx-get="/fragments/workspace-health"' in advanced.group(2)
    assert 'hx-trigger="settingsSaved from:body"' in advanced.group(2)


def test_unsaved_output_and_validate_do_not_change_saved_workspace_health(
    health_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved_issue = broken_health_paper(health_config, "saved-workspace")
    draft_issue = broken_health_paper(health_config, "draft-workspace")
    state = load_settings(health_config)
    unsaved_state = replace(state, draft=replace(state.draft, output_dir=Path("draft-workspace")))
    monkeypatch.setattr(web_app, "load_settings", lambda path: unsaved_state)
    app = create_app(health_config)

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/settings")
        assert 'name="output_dir" value="draft-workspace"' in page.text
        assert str(saved_issue) in page.text
        assert str(draft_issue) not in page.text
        validation = client.post(
            "/settings/validate",
            data=valid_settings_form(
                csrf_from_html(page.text),
                output_dir="draft-workspace",
                monitor_revision_digest=state.draft.monitor_revision.digest,
                journal_revision_digest=state.draft.journal_revision.digest,
            ),
        )
        health = client.get("/fragments/workspace-health")
        refreshed = client.get("/settings")

    assert validation.status_code == 200
    assert "Runtime date range" in validation.text
    assert 'value="draft-workspace"' in validation.text
    assert 'id="advanced-diagnostics"' not in validation.text
    assert "settingsSaved" not in validation.headers.get("HX-Trigger", "")
    for response in (health, refreshed):
        assert str(saved_issue) in response.text
        assert str(draft_issue) not in response.text


@pytest.mark.parametrize("mode", ("missing", "invalid", "unreadable"))
def test_workspace_health_unavailable_never_scans_recovery_draft(
    mode: str,
    health_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = load_settings(health_config)
    config_path = health_config.parent / "unavailable.yaml"
    if mode == "invalid":
        config_path.write_text("keyword_expression: [\n", encoding="utf-8")
    elif mode == "unreadable":
        config_path.mkdir()
    recovery = replace(state, draft=replace(state.draft, output_dir=Path("recovery-workspace")))
    monkeypatch.setattr(web_app, "load_settings", lambda path: recovery)

    def forbidden_scan(output_dir: Path) -> None:
        raise AssertionError("Invalid saved configuration must not scan any workspace")

    monkeypatch.setattr(web_app, "load_workspace", forbidden_scan)
    app = create_app(config_path)
    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/settings")
        health = client.get("/fragments/workspace-health")

    assert 'value="recovery-workspace"' in page.text
    for response in (page, health):
        assert response.status_code == 200
        assert "Workspace health unavailable" in response.text
        assert "No issues detected" not in response.text


def test_settings_saved_refresh_reads_new_target_from_actual_disk(
    health_config: Path,
) -> None:
    old_issue = broken_health_paper(health_config, "saved-workspace")
    new_issue = broken_health_paper(health_config, "new-workspace")
    state = load_settings(health_config)
    app = create_app(health_config)
    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/settings")
        saved = client.post(
            "/settings/save",
            data=valid_settings_form(
                csrf_from_html(page.text),
                output_dir="new-workspace",
                monitor_revision_digest=state.draft.monitor_revision.digest,
                journal_revision_digest=state.draft.journal_revision.digest,
            ),
        )
        health = client.get("/fragments/workspace-health")
        refreshed = client.get("/settings")

    assert saved.headers["HX-Trigger"] == "settingsSaved"
    assert 'id="advanced-diagnostics"' not in saved.text
    assert str(old_issue) in page.text
    for response in (health, refreshed):
        assert str(new_issue) in response.text
        assert str(old_issue) not in response.text
    assert web_app.load_config(health_config).output_dir == new_issue.parent.parent


def test_workspace_health_ignores_save_result_draft_when_disk_differs(
    health_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disk_issue = broken_health_paper(health_config, "saved-workspace")
    result = make_save_result(
        health_config.parent,
        outcome=SettingsSaveOutcome.SAVED,
        state_draft=make_draft(output_dir=Path("not-on-disk")),
    )
    monkeypatch.setattr(web_app, "save_settings", lambda path, draft: result)
    app = create_app(health_config)
    with TestClient(app, base_url="http://localhost") as client:
        csrf = csrf_from_html(client.get("/settings").text)
        saved = client.post("/settings/save", data=valid_settings_form(csrf))
        health = client.get("/fragments/workspace-health")

    assert 'value="not-on-disk"' in saved.text
    assert str(disk_issue) in health.text


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


def test_no_t9_gui_cli_or_uvicorn_launch_added() -> None:
    source = inspect.getsource(web_app)

    assert "uvicorn" not in source.lower()
    assert "literature-monitor gui" not in source


def test_group_form_projection_exact_identity_and_noop_order():
    groups = ("Statistics", None, "statistics", "Statistics", "Ungrouped", "Unmapped journals", '统计 & <x> "y"')
    identifiers = ("0006-341X", "0090-5364", "0162-1459", "0033-3123", "0092-5853", "1932-6157", "1941-7330")
    draft = make_draft(journals=tuple(
        JournalConfig(name=f"Journal {i}", issn=(issn,), group=group)
        for i, (issn, group) in enumerate(zip(identifiers, groups))
    ))
    values = settings_form.settings_form_from_draft(draft)
    assert values.groups == ("Statistics", "statistics", "Ungrouped", "Unmapped journals", '统计 & <x> "y"')
    assert settings_form.settings_draft_from_form(values) == (draft, ())
    # Empty presentation entries are not application state.
    assert settings_form.settings_draft_from_form(replace(values, groups=("Empty", *values.groups))) == (draft, ())


def test_submitted_empty_groups_order_duplicates_and_missing_assignment():
    pairs = list(valid_settings_form("unused").items()) + [
        ("settings_group", "Empty"), ("settings_group", "statistics"),
        ("settings_group", "Empty"), ("settings_group", "Statistics"),
        ("journal_group", '统计 & <x> "y"'),
    ]
    values = settings_form.settings_form_from_submission(FormData(pairs))
    assert values.groups == ("Empty", "statistics", "Statistics", '统计 & <x> "y"')
    draft, issues = settings_form.settings_draft_from_form(values)
    assert not issues and draft.journals[0].group == '统计 & <x> "y"'
    assert draft.monitor_revision == revision(True, "monitor-old")
    assert draft.journal_revision == revision(True, "journal-old")


@pytest.mark.parametrize("group", ["bad|group", "bad\ngroup", "bad\rgroup", "bad\u2028group"])
def test_unsafe_assigned_group_uses_existing_validation(health_config, group):
    path = health_config.parent / "list.md"
    before = (health_config.read_bytes(), path.read_bytes())
    with TestClient(create_app(health_config), base_url="http://localhost") as client:
        page = client.get("/settings")
        data = organization_submission(health_config, csrf_from_html(page.text), (group,), [("A", "0006-341X", group)])
        validated = client.post("/settings/validate", data=data)
        saved = client.post("/settings/save", data=data)
        assert "Settings draft needs correction" in validated.text
        assert "Settings draft is invalid" in saved.text
        assert "HX-Trigger" not in saved.headers
    assert (health_config.read_bytes(), path.read_bytes()) == before


class SettingsDOM(HTMLParser):
    """Expose rendered form nodes to the dependency-free Node handler harness."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = {"tag": "root", "attrs": {}, "children": []}
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "children": []}
        self.stack[-1]["children"].append(node)
        if tag not in {"input", "meta", "link", "br", "hr", "img"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1]["children"].append(data)


def organization_submission(config_path, csrf, groups, rows):
    values = settings_form.settings_form_from_draft(load_settings(config_path).draft)
    result = {key: getattr(values, key) for key in (
        "name", "keyword_expression", "from_date", "to_date", "window_days", "output_dir", "log_level",
        "monitor_revision_exists", "monitor_revision_digest", "journal_revision_exists", "journal_revision_digest",
    )}
    result.update(csrf_token=csrf, settings_group=list(groups),
                  journal_name=[r[0] for r in rows], journal_issns=[r[1] for r in rows], journal_group=[r[2] for r in rows])
    return result


@pytest.mark.parametrize("grouped", [False, True])
def test_organization_validate_noop_order_and_empty_group_save(health_config, grouped):
    path = health_config.parent / "list.md"
    rows = [("A", "0006-341X", "X" if grouped else ""), ("B", "0090-5364", ""),
            ("C", "0162-1459", "Y" if grouped else ""), ("D", "0033-3123", "X" if grouped else "")]
    header = "| Journal | ISSN/EISSN" + (" | Group" if grouped else "") + " |\n"
    path.write_text("## Journals\n" + header + ("|---|---|---|\n" if grouped else "|---|---|\n") + "".join(
        f"| {name} | {issn}" + (f" | {group}" if grouped else "") + " |\n" for name, issn, group in rows
    ))
    before = (health_config.read_bytes(), path.read_bytes())
    with TestClient(create_app(health_config), base_url="http://localhost") as client:
        page = client.get("/settings")
        assert (health_config.read_bytes(), path.read_bytes()) == before
        data = organization_submission(health_config, csrf_from_html(page.text), ("X", "Empty", "Y") if grouped else ("Empty",), rows)
        validated = client.post("/settings/validate", data=data)
        assert validated.status_code == 200 and "Settings draft is valid" in validated.text
        assert (health_config.read_bytes(), path.read_bytes()) == before
        assert 'name="settings_group" value="Empty"' in validated.text
        assert re.findall(r'name="journal_name" value="([^"]*)"', validated.text) == ["A", "B", "C", "D", ""]
        for revision_field in ("monitor_revision_digest", "journal_revision_digest"):
            assert f'name="{revision_field}" value="{data[revision_field]}"' in validated.text
        data["name"] = "Unrelated monitor change"
        saved = client.post("/settings/save", data=data)
        assert saved.headers["HX-Trigger"] == "settingsSaved"
        assert 'name="settings_group" value="Empty"' not in saved.text
    assert [(j.name, j.issn[0], j.group or "") for j in load_config(health_config).journals] == rows
    assert ("| Group |" in path.read_text()) is grouped


@pytest.mark.parametrize("operation", ["assignment", "rename", "delete", "reorder"])
def test_organization_save_through_real_boundary(health_config, operation):
    path = health_config.parent / "list.md"
    grouped = operation != "assignment"
    rows = [("A", "0006-341X", "X" if grouped else ""), ("B", "0090-5364", ""),
            ("C", "0162-1459", "Y" if grouped else ""), ("D", "0033-3123", "X" if grouped else "")]
    path.write_text("## Journals\n| Journal | ISSN/EISSN" + (" | Group" if grouped else "") + " |\n" +
                    ("|---|---|---|\n" if grouped else "|---|---|\n") + "".join(
                        f"| {n} | {i}" + (f" | {g}" if grouped else "") + " |\n" for n, i, g in rows))
    if operation == "assignment":
        rows[0] = (*rows[0][:2], "Ungrouped")
    elif operation == "rename":
        rows = [(n, i, "New" if g == "X" else g) for n, i, g in rows]
    elif operation == "delete":
        rows = [(n, i, "") for n, i, g in rows]
    else:
        rows = [rows[2], rows[0], rows[1], rows[3]]
    with TestClient(create_app(health_config), base_url="http://localhost") as client:
        page = client.get("/settings")
        data = organization_submission(health_config, csrf_from_html(page.text), ("Empty",), rows)
        response = client.post("/settings/save", data=data)
        assert response.headers["HX-Trigger"] == "settingsSaved"
        assert 'name="settings_group" value="Empty"' not in response.text
    assert [(j.name, j.issn[0], j.group or "") for j in load_config(health_config).journals] == rows
    assert "| Group |" in path.read_text()


def test_journal_organization_markup_and_responsive_scope(health_config):
    path = health_config.parent / "list.md"
    path.write_text('## Journals\n| Journal | ISSN/EISSN | Group |\n|---|---|---|\n| A | 0006-341X | 统计 & <x> "y" |\n')
    with TestClient(create_app(health_config), base_url="http://localhost") as client:
        page = client.get("/settings").text
        css = client.get("/static/app.css").text
        js = client.get("/static/app.js").text
    tree = SettingsDOM(page).root
    def visit(node, ancestors=()):
        if isinstance(node, str): return
        attrs = node["attrs"]
        if attrs.get("hx-post") == "/settings/validate" or (node["tag"] == "button" and attrs.get("type") == "submit"):
            assert not any("data-journal-viewport" in a for a in ancestors)
        if attrs.get("id") == "advanced-diagnostics":
            assert "open" not in attrs and not any(a.get("id") == "settings-form" for a in ancestors)
        for child in node["children"]: visit(child, (*ancestors, attrs))
    visit(tree)
    for marker in ("data-group-rows", "data-create-group", "data-rename-group", "data-delete-group", "data-group-up", "data-group-down", "data-journal-viewport"):
        assert marker in page
    assert '<select name="journal_group">' in page
    assert '<option value="统计 &amp; &lt;x&gt; &#34;y&#34;" selected>' in page
    assert 'type="hidden" name="journal_group"' not in page
    assert "draggable" not in page
    desktop = css.split("[data-journal-viewport] {", 1)[1].split("}", 1)[0]
    mobile = css.split("@media (max-width: 760px)", 1)[1]
    assert "max-height: 55vh" in desktop and "overflow-y: auto" in desktop
    assert "max-height: none" in mobile and "overflow: visible" in mobile
    assert "let workspaceScroll = null" in js and "let settingsJournalScroll = null" in js
    assert "innerHTML" not in js and "localStorage" not in js and "sessionStorage" not in js


SETTINGS_NODE_DOM = r'''
const vm = require("node:vm"), assert = require("node:assert/strict");
class Element {
  constructor(tag, attrs = {}) {
    this.tag = tag; this.attrs = {...attrs}; this.children = []; this.parentNode = null;
    this.dataset = {}; this.scrollTop = 0; this._value = attrs.value || ""; this.textContent = "";
    for (const [key, value] of Object.entries(attrs)) if (key.startsWith("data-")) {
      this.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value || "";
    }
  }
  get id() { return this.attrs.id; }
  get nextSibling() { return this.parentNode?.children[this.parentNode.children.indexOf(this) + 1] || null; }
  get options() { return this.children; }
  get value() { return this.tag === "select" ? (this.selected?.value || "") : this._value; }
  set value(value) { if (this.tag === "select") this.selected = this.children.find(n => n.value === value); else this._value = value; }
  hasAttribute(name) { return name in this.attrs; }
  matches(selector) {
    return selector.split(",").some(part => {
      part = part.trim();
      if (part.includes(" ")) return false;
      const tag = part.match(/^[a-z-]+/), id = part.match(/#([\w-]+)/), cls = part.match(/\.([\w-]+)/);
      if (tag && this.tag !== tag[0]) return false;
      if (id && this.id !== id[1]) return false;
      if (cls && !(this.attrs.class || "").split(" ").includes(cls[1])) return false;
      return [...part.matchAll(/\[([^=\]]+)(?:="([^"]*)")?\]/g)].every(m => this.hasAttribute(m[1]) && (m[2] === undefined || this.attrs[m[1]] === m[2]));
    });
  }
  closest(selector) { return this.matches(selector) ? this : this.parentNode?.closest(selector) || null; }
  querySelectorAll(selector) {
    const selectors = selector.split(",").map(s => s.trim().split(/\s+/));
    const found = [];
    const visit = parent => {
      for (const child of parent.children) {
        if (selectors.some(parts => {
          if (!child.matches(parts.at(-1))) return false;
          let ancestor = child.parentNode;
          for (let i = parts.length - 2; i >= 0; i--) {
            while (ancestor && !ancestor.matches(parts[i])) ancestor = ancestor.parentNode;
            if (!ancestor) return false;
            ancestor = ancestor.parentNode;
          }
          return true;
        })) found.push(child);
        if (!(child instanceof Template)) visit(child);
      }
    };
    visit(this); return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  append(child) {
    if (child.tag === "fragment") { for (const node of [...child.children]) this.append(node); return; }
    child.remove(); this.children.push(child); child.parentNode = this;
    if (this.tag === "select" && (!this.selected || child.hasAttribute("selected"))) this.selected = child;
  }
  insertBefore(child, reference) {
    child.remove();
    const index = reference ? this.children.indexOf(reference) : this.children.length;
    assert.ok(index >= 0); this.children.splice(index, 0, child); child.parentNode = this;
  }
  remove() {
    if (this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1);
    this.parentNode = null;
  }
  replaceChildren() { for (const child of this.children) child.parentNode = null; this.children = []; this.selected = null; }
  cloneNode() {
    const node = new Element(this.tag, this.attrs); node.value = this.value;
    for (const child of this.children) node.append(child.cloneNode(true)); return node;
  }
}
class Template extends Element { constructor(attrs) { super("template", attrs); this.content = new Element("fragment"); } }
function build(data) {
  const node = data.tag === "template" ? new Template(data.attrs) : new Element(data.tag, data.attrs);
  const parent = node instanceof Template ? node.content : node;
  for (const child of data.children) {
    if (typeof child === "string") node.textContent += child;
    else parent.append(build(child));
  }
  if (node.tag === "textarea") node.value = node.textContent;
  return node;
}
let dom = build(PAGE), handlers = {}, bodyHandlers = {}, windowHandlers = {};
const document = {
  documentElement: {dataset: {}},
  getElementById: id => dom.querySelector(`#${id}`),
  querySelector: selector => dom.querySelector(selector),
  createElement: tag => new Element(tag),
  addEventListener: (name, handler) => handlers[name] = handler,
  body: {addEventListener: (name, handler) => bodyHandlers[name] = handler},
};
vm.runInNewContext(SOURCE, {
  document, TextDecoder, Element, HTMLElement: Element, HTMLDetailsElement: Element, HTMLTemplateElement: Template,
  window: {location: {hash: ""}, addEventListener: (name, handler) => windowHandlers[name] = handler, innerWidth: 600},
  fetch: () => assert.fail("Organization must not request the server"),
});
'''


def test_executable_group_operations_and_validate_scroll(health_config):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable for the executable Settings DOM test")
    path = health_config.parent / "list.md"
    initial = [("A", "0006-341X", "X"), ("B", "0090-5364", ""), ("C", "0162-1459", "Y"),
               ("D", "0033-3123", "X"), ("E", "0092-5853", "x"),
               ("F", "1932-6157", "Ungrouped"), ("G", "1941-7330", "Unmapped journals")]
    # A genuinely long rendered editor, using checksum-valid unique fixture ISSNs.
    for i in range(30):
        digits = f"777{i:04d}"
        check = (11 - sum(int(d) * weight for d, weight in zip(digits, range(8, 1, -1))) % 11) % 11
        identifier = digits[:4] + "-" + digits[4:] + ("X" if check == 10 else str(check))
        initial.append((f"Extra {i}", identifier, "X"))
    path.write_text("## Journals\n| Journal | ISSN/EISSN | Group |\n|---|---|---|\n" + "".join(
        f"| {n} | {i} | {g} |\n" for n, i, g in initial))
    before = (health_config.read_bytes(), path.read_bytes())
    app = create_app(health_config)
    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/settings").text
        javascript = client.get("/static/app.js").text
    harness = SETTINGS_NODE_DOM + r'''
const editor = () => document.getElementById("settings-editor");
const groups = () => editor().querySelectorAll("[data-group-rows] [data-group-row]");
const group = name => groups().find(row => row.dataset.groupName === name);
const rows = () => editor().querySelectorAll("[data-journal-rows] .journal-row");
const name = row => row.querySelector('[name="journal_name"]').value;
const assignment = row => row.querySelector('[name="journal_group"]');
const row = n => rows().find(r => name(r) === n);
const firstOrder = () => [...new Set(rows().map(r => assignment(r).value).filter(Boolean))];
const represented = () => groups().map(g => g.dataset.groupName).filter(g => rows().some(r => assignment(r).value === g));
const click = target => { assert.ok(target); handlers.click({target}); };
const dirty = () => assert.equal(document.documentElement.dataset.settingsDirty, "true");
const clean = () => bodyHandlers.settingsSaved();
const content = () => rows().map(r => [name(r), r.querySelector('[name="journal_issns"]').value]).sort();
const create = value => { editor().querySelector("[data-new-group]").value = value; click(editor().querySelector("[data-create-group]")); };
const assign = (n, value) => { const target = assignment(row(n)); target.value = value; handlers.change({target}); dirty(); assert.deepEqual(firstOrder(), represented()); };
const rename = (old, value) => { const target = group(old).querySelector("[data-group-edit]"); target.value = value; click(group(old).querySelector("[data-rename-group]")); };
const original = content(), originalOrder = rows().map(name);
const revisions = ["monitor_revision_digest", "journal_revision_digest"].map(n => editor().querySelector(`[name="${n}"]`).value);
assert.deepEqual(firstOrder(), ["X", "Y", "x", "Ungrouped", "Unmapped journals"]);
assert.deepEqual(rows().map(name), originalOrder); // script initialization must never regroup
create("   "); assert.equal(groups().length, 5);
create("  Empty  "); dirty(); assert.ok(group("Empty")); assert.equal(firstOrder().length, 5);
create("Empty"); assert.equal(groups().length, 6);
// Enter in Group fields applies the browser operation, never submits Save.
let prevented = false;
editor().querySelector("[data-new-group]").value = "Keyboard";
handlers.keydown({target: editor().querySelector("[data-new-group]"), key: "Enter", preventDefault: () => prevented = true});
assert.ok(prevented && group("Keyboard"));
let edit = group("Keyboard").querySelector("[data-group-edit]"); edit.value = "Keyboard renamed";
handlers.keydown({target: edit, key: "Enter", preventDefault: () => {}});
assert.ok(group("Keyboard renamed")); click(group("Keyboard renamed").querySelector("[data-delete-group]"));
const safe = '统计 & <x> "y"'; create(safe); dirty();
assert.ok(assignment(row("A")).options.some(o => o.value === safe && o.textContent === safe));
rename("X", "New"); dirty(); assert.equal(assignment(row("A")).value, "New"); assert.equal(assignment(row("D")).value, "New");
assert.equal(assignment(row("E")).value, "x");
rename("New", "   "); assert.ok(group("New"));
rename("New", "X");
const xMembers = rows().filter(r => assignment(r).value === "X").map(name);
clean(); click(group("Y").querySelector("[data-group-up]")); dirty();
assert.deepEqual(firstOrder(), ["Y", "X", "x", "Ungrouped", "Unmapped journals"]);
assert.deepEqual(content(), original); assert.deepEqual(rows().filter(r => assignment(r).value === "X").map(name), xMembers);
clean(); click(group("Y").querySelector("[data-group-down]")); dirty();
assert.deepEqual(firstOrder(), ["X", "Y", "x", "Ungrouped", "Unmapped journals"]);
assert.deepEqual(content(), original); assert.deepEqual(rows().filter(r => assignment(r).value === "X").map(name), xMembers);
// Move an empty Group to first, then give it its first member.
while (groups()[0] !== group("Empty")) click(group("Empty").querySelector("[data-group-up]"));
const assignmentsBefore = new Map(rows().map(r => [name(r), assignment(r).value]));
assign("B", "Empty"); assert.equal(firstOrder()[0], "Empty");
for (const r of rows()) if (name(r) !== "B") assert.equal(assignment(r).value, assignmentsBefore.get(name(r)));
assign("B", ""); assert.ok(group("Empty"));
assign("B", "Ungrouped"); assert.equal(assignment(row("F")).value, "Ungrouped"); assign("B", "");
// Coalesce an exact existing target, but preserve case-distinct identities.
rename("X", "Y"); assert.equal(groups().filter(g => g.dataset.groupName === "Y").length, 1);
assert.equal(assignment(row("A")).value, "Y"); assert.equal(assignment(row("E")).value, "x");
assert.deepEqual(firstOrder(), represented());
clean(); click(group("Y").querySelector("[data-delete-group]")); dirty();
assert.equal(group("Y"), undefined); assert.equal(assignment(row("A")).value, ""); assert.equal(assignment(row("D")).value, "");
assert.deepEqual(content(), original);
clean(); click(row("E").querySelector("[data-remove-journal]")); dirty(); assert.ok(group("x")); assert.equal(row("E"), undefined);
clean(); click(editor().querySelector("[data-add-journal]")); dirty();
const added = rows().at(-1); assert.equal(name(added), ""); assert.equal(added.querySelector('[name="journal_issns"]').value, "");
assert.equal(assignment(added).value, ""); assert.ok(assignment(added).options.some(o => o.value === safe));
click(added.querySelector("[data-remove-journal]")); assert.equal(rows().length, original.length - 1);
assert.deepEqual(["monitor_revision_digest", "journal_revision_digest"].map(n => editor().querySelector(`[name="${n}"]`).value), revisions);
const draftRows = rows().map(r => [name(r), r.querySelector('[name="journal_issns"]').value, assignment(r).value]);
const draftGroups = groups().map(g => g.dataset.groupName);
if (!VALIDATED) {
  const fields = {};
  for (const input of editor().querySelectorAll("input, select")) {
    const field = input.attrs.name;
    if (field) (fields[field] ||= []).push(input.value);
  }
  console.log(JSON.stringify(fields));
  process.exit(0);
}
// Actual Validate response replaces the entire editor. Scroll and dirty state survive.
let viewport = editor().querySelector("[data-journal-viewport]"); viewport.scrollTop = 487;
bodyHandlers["htmx:beforeSwap"]({detail: {target: editor()}});
dom = build(VALIDATED);
bodyHandlers["htmx:afterSwap"]({detail: {target: editor()}});
assert.equal(editor().querySelector("[data-journal-viewport]").scrollTop, 487); dirty(); assert.ok(group("Empty"));
assert.deepEqual(rows().map(r => [name(r), r.querySelector('[name="journal_issns"]').value, assignment(r).value]), draftRows);
assert.deepEqual(groups().map(g => g.dataset.groupName), draftGroups);
// Workspace scroll retains separate storage while Settings is pending.
const workspace = new Element("section", {id: "workspace-root", "data-active-view": "inbox"});
const list = new Element("div", {id: "paper-list"}); workspace.append(list); dom.append(workspace);
list.scrollTop = 173; viewport = editor().querySelector("[data-journal-viewport]"); viewport.scrollTop = 291;
bodyHandlers["htmx:beforeSwap"]({detail: {target: editor()}});
bodyHandlers["htmx:beforeSwap"]({detail: {target: workspace, xhr: {status: 400}}});
list.scrollTop = 0; bodyHandlers["htmx:afterSwap"]({detail: {target: workspace}}); assert.equal(list.scrollTop, 173);
viewport.scrollTop = 0; bodyHandlers["htmx:afterSwap"]({detail: {target: editor()}}); assert.equal(viewport.scrollTop, 291);
clean(); assert.equal(document.documentElement.dataset.settingsDirty, "false");
'''
    preamble = "const SOURCE = " + json.dumps(javascript) + "; const PAGE = " + json.dumps(SettingsDOM(page).root) + ";\n"
    draft_result = subprocess.run([node], input=preamble + "const VALIDATED = null;\n" + harness, text=True, capture_output=True)
    assert draft_result.returncode == 0, draft_result.stdout + draft_result.stderr
    data = json.loads(draft_result.stdout)
    # Submit precisely the browser DOM produced by the operations above.
    with TestClient(app, base_url="http://localhost") as client:
        validated = client.post("/settings/validate", data=data)
        assert "Settings draft is valid" in validated.text and "HX-Trigger" not in validated.headers
        assert (health_config.read_bytes(), path.read_bytes()) == before
        assert 'name="settings_group" value="Empty"' in validated.text
        for field in ("monitor_revision_digest", "journal_revision_digest"):
            assert f'name="{field}" value="{data[field][0]}"' in validated.text
    result = subprocess.run([node], input=preamble + "const VALIDATED = " + json.dumps(SettingsDOM(validated.text).root) + ";\n" + harness,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("failure", ["revision_conflict", "partial_save"])
def test_organized_save_keeps_existing_failure_semantics(health_config, monkeypatch, failure):
    import literature_monitor.application.settings as application_settings

    path = health_config.parent / "list.md"
    monitor_before = health_config.read_bytes()
    with TestClient(create_app(health_config), base_url="http://localhost") as client:
        page = client.get("/settings")
        data = organization_submission(health_config, csrf_from_html(page.text), ("Assigned", "Empty"), [("Biometrics", "0006-341X", "Assigned")])
        if failure == "revision_conflict":
            path.write_bytes(path.read_bytes() + b"\n")
            journal_before = path.read_bytes()
        else:
            original_write = application_settings._write_snapshot_target
            calls = []
            def fail_monitor(target, contents, snapshot):
                calls.append(target)
                if target == health_config:
                    raise OSError("monitor write denied")
                original_write(target, contents, snapshot)
            monkeypatch.setattr(application_settings, "_write_snapshot_target", fail_monitor)
        response = client.post("/settings/save", data=data)
        assert response.status_code == 200 and "HX-Trigger" not in response.headers
        assert health_config.read_bytes() == monitor_before
        if failure == "revision_conflict":
            assert "files changed since this editor was opened" in response.text
            assert path.read_bytes() == journal_before
            assert 'name="settings_group" value="Empty"' in response.text
            assert f'name="journal_revision_digest" value="{data["journal_revision_digest"]}"' in response.text
        else:
            assert calls == [path, health_config]
            assert "Settings were partially saved" in response.text
            assert 'name="settings_group" value="Assigned"' in response.text
            assert 'name="settings_group" value="Empty"' not in response.text
            state = load_settings(health_config)
            assert state.draft.journals[0].group == "Assigned"
            assert f'name="journal_revision_digest" value="{state.draft.journal_revision.digest}"' in response.text


def browser_settings_submission(html):
    """Successful controls from the rendered editor, excluding inert templates."""
    result = {}
    def walk(node):
        if isinstance(node, str) or node["tag"] == "template": return
        attrs, tag = node["attrs"], node["tag"]
        if attrs.get("name") and tag in {"input", "select", "textarea"}:
            value = attrs.get("value", "")
            if tag == "textarea":
                value = "".join(child for child in node["children"] if isinstance(child, str))
            elif tag == "select":
                options = [child for child in node["children"] if isinstance(child, dict) and child["tag"] == "option"]
                selected = next((o for o in options if "selected" in o["attrs"]), options[0])
                value = selected["attrs"]["value"]
            result.setdefault(attrs["name"], []).append(value)
        for child in node["children"]: walk(child)
    walk(SettingsDOM(html).root)
    return result


def browser_settings_values(html):
    return settings_form.settings_form_from_submission(FormData([
        (name, value) for name, values in browser_settings_submission(html).items() for value in values
    ]))


def assert_import_does_not_write(config_path, before):
    assert (config_path.read_bytes(), (config_path.parent / "list.md").read_bytes()) == before
    assert sorted(p.name for p in config_path.parent.iterdir()) == ["list.md", "monitor.yaml"]


def test_bulk_import_markup_defaults_and_security(health_config):
    app = create_app(health_config)
    with TestClient(app, base_url="http://localhost") as client:
        html = client.get("/settings").text
    assert "Bulk import Journals" in html and "data-transient-import" in html
    assert 'type="file" data-journal-import-file' in html
    assert 'accept=".csv,.tsv,.md,text/csv,text/tab-separated-values,text/markdown,text/plain"' in html
    assert 'name="journal_import_text"' in html
    assert '<option value="MERGE" selected>Merge</option>' in html
    assert '<option value="REPLACE" >Replace</option>' in html
    assert 'hx-post="/settings/import/preview"' in html and 'data-import-apply' not in html
    assert 'hx-post="/settings/validate"' in html and 'hx-post="/settings/save"' in html
    assert html.split('<form id="settings-form"', 1)[1].split("</form>", 1)[0].count('type="submit"') == 1
    assert app.openapi_url is None and app.docs_url is None


@pytest.mark.parametrize("route", ["preview", "apply"])
@pytest.mark.parametrize("csrf", [None, "wrong", "不是 token"])
def test_import_csrf_precedes_core_and_has_no_side_effects(health_config, monkeypatch, route, csrf):
    def forbidden(*args, **kwargs): pytest.fail("CSRF failure must not invoke the import core")
    monkeypatch.setattr(web_app, "preview_journal_import", forbidden)
    monkeypatch.setattr(web_app, "apply_journal_import", forbidden)
    before = (health_config.read_bytes(), (health_config.parent / "list.md").read_bytes())
    with TestClient(create_app(health_config), base_url="http://localhost") as client:
        data = valid_settings_form(csrf or "unused", journal_import_text="Journal,ISSN/EISSN\nA,0006-341X\n")
        if csrf is None: data.pop("csrf_token")
        response = client.post(f"/settings/import/{route}", data=data)
        assert response.status_code == 403 and "HX-Trigger" not in response.headers
        assert client.post(f"/settings/import/{route}", data=data, headers={"Host": "evil.example"}).status_code == 400
    assert_import_does_not_write(health_config, before)


@pytest.mark.parametrize("route", ["preview", "apply"])
@pytest.mark.parametrize("mode", ["REPLAC", "", "replace", '<script>bad</script>'])
def test_invalid_import_mode_preserves_current_values_without_core(health_config, monkeypatch, route, mode):
    app = create_app(health_config)
    before = (health_config.read_bytes(), (health_config.parent / "list.md").read_bytes())
    with TestClient(app, base_url="http://localhost") as client:
        data = browser_settings_submission(client.get("/settings").text)
        data.update(journal_import_mode=mode, journal_import_text='</textarea><script>bad</script>', settings_group=["Empty"], name="Unsaved")
        def forbidden(*args, **kwargs): pytest.fail("Malformed mode must not invoke import")
        monkeypatch.setattr(web_app, "preview_journal_import", forbidden)
        monkeypatch.setattr(web_app, "apply_journal_import", forbidden)
        response = client.post(f"/settings/import/{route}", data=data)
    values = browser_settings_values(response.text)
    assert values.name == "Unsaved" and values.groups == ("Empty",)
    assert "Choose Merge or Replace" in response.text and "HX-Trigger" not in response.headers
    assert 'data-import-apply' not in response.text and '<script>bad</script>' not in response.text
    assert browser_settings_submission(response.text)["journal_import_mode"] == [mode]
    assert browser_settings_submission(response.text)["journal_import_text"] == ['</textarea><script>bad</script>']
    assert_import_does_not_write(health_config, before)


@pytest.mark.parametrize("route", ["preview", "apply"])
@pytest.mark.parametrize("field,invalid", [("window_days", "abc"), ("from_date", "bad"), ("monitor_revision_exists", "bad"), ("journal_name", "")])
def test_unconstructable_current_draft_is_not_substituted(health_config, monkeypatch, route, field, invalid):
    app = create_app(health_config)
    before = (health_config.read_bytes(), (health_config.parent / "list.md").read_bytes())
    with TestClient(app, base_url="http://localhost") as client:
        data = browser_settings_submission(client.get("/settings").text)
        data.update(journal_import_text="Journal,ISSN/EISSN\nNew,0090-5364\n", settings_group=["Empty"], **{field: invalid})
        def forbidden(*args, **kwargs): pytest.fail("Invalid current form must not invoke import or reread disk")
        for function in ("preview_journal_import", "apply_journal_import", "load_settings", "load_config"):
            monkeypatch.setattr(web_app, function, forbidden)
        response = client.post(f"/settings/import/{route}", data=data)
    assert browser_settings_submission(response.text)[field] == [invalid]
    assert browser_settings_values(response.text).groups == ("Empty",)
    assert "Settings issues" in response.text and "HX-Trigger" not in response.headers
    assert 'data-import-apply' not in response.text
    assert_import_does_not_write(health_config, before)


def test_merge_preview_apply_validate_save_uses_current_unsaved_draft(health_config, monkeypatch):
    before = (health_config.read_bytes(), (health_config.parent / "list.md").read_bytes())
    app = create_app(health_config)
    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/settings")
        data = browser_settings_submission(page.text)
        original_revisions = {f: data[f] for f in ("monitor_revision_digest", "journal_revision_digest")}
        data.update(name="Current unsaved name", keyword_expression="causal AND inference", window_days="21",
                    journal_name=["Draft B", "Biometrics"], journal_issns=["0090-5364", "0006-341X"],
                    journal_group=["Other", "DraftGroup"], settings_group=["Other", "DraftGroup", "Empty"],
                    journal_import_text="Journal,ISSN/EISSN,Group\nbiometrics,0006-341X,\nDraft B,0090-5364/0162-1459,Moved\nNew,0033-3123,NewGroup\n")
        data.pop("journal_import_mode")  # Server default must remain Merge.
        # Import endpoints must not fetch disk state, validate/save, or run retrieval.
        with monkeypatch.context() as patch:
            def forbidden(*args, **kwargs): pytest.fail("Preview/Apply must use only the submitted draft and pure core")
            for function in ("load_settings", "load_config", "validate_settings", "save_settings"):
                patch.setattr(web_app, function, forbidden)
            patch.setattr(app.state.run_coordinator, "start", forbidden)
            preview = client.post("/settings/import/preview", data=data)
            assert preview.status_code == 200 and "HX-Trigger" not in preview.headers
            assert set(re.findall(r'data-import-change="([^"]+)"', preview.text)) == {"NO_OP_DUPLICATE", "ISSN_MERGE", "GROUP_MOVE", "ADD"}
            assert "Source rows: 2" in preview.text and "Added ISSNs: 0162-1459" in preview.text
            assert "Group: Other → Moved" in preview.text and 'data-import-apply' in preview.text
            assert browser_settings_values(preview.text).journals == (
                settings_form.SettingsJournalRow("Draft B", "0090-5364", "Other"),
                settings_form.SettingsJournalRow("Biometrics", "0006-341X", "DraftGroup"),
            )
            applied = client.post("/settings/import/apply", data=browser_settings_submission(preview.text))
        assert applied.headers["HX-Trigger"] == "settingsDraftChanged"
        assert "unsaved Settings draft" in applied.text
        current = browser_settings_submission(applied.text)
        values = browser_settings_values(applied.text)
        assert values.groups == ("Moved", "DraftGroup", "NewGroup", "Empty")
        assert values.journals == (
            settings_form.SettingsJournalRow("Draft B", "0090-5364, 0162-1459", "Moved"),
            settings_form.SettingsJournalRow("Biometrics", "0006-341X", "DraftGroup"),
            settings_form.SettingsJournalRow("New", "0033-3123", "NewGroup"),
        )
        assert values.name == "Current unsaved name" and values.keyword_expression == "causal AND inference"
        assert values.window_days == "21"
        for f, revision_value in original_revisions.items(): assert current[f] == revision_value
        assert_import_does_not_write(health_config, before)
        validated = client.post("/settings/validate", data=current)
        assert "Settings draft is valid" in validated.text and "HX-Trigger" not in validated.headers
        assert browser_settings_values(validated.text) == values
        assert_import_does_not_write(health_config, before)
        saved = client.post("/settings/save", data=browser_settings_submission(validated.text))
        assert saved.headers["HX-Trigger"] == "settingsSaved" and "Settings saved." in saved.text
        assert "Empty" not in browser_settings_values(saved.text).groups
    assert load_config(health_config).journals == (
        JournalConfig(name="Draft B", issn=("0090-5364", "0162-1459"), group="Moved"),
        JournalConfig(name="Biometrics", issn=("0006-341X",), group="DraftGroup"),
        JournalConfig(name="New", issn=("0033-3123",), group="NewGroup"),
    )


def test_replace_preview_exposes_all_removals_and_order_changes(health_config):
    path = health_config.parent / "list.md"
    path.write_text("## Journals\n| Journal | ISSN/EISSN | Group |\n|---|---|---|\n"
                    "| A | 0006-341X / 0090-5364 | X |\n| B | 0162-1459 | Y |\n| C | 0033-3123 | RemovedGroup |\n")
    before = (health_config.read_bytes(), path.read_bytes())
    with TestClient(create_app(health_config), base_url="http://localhost") as client:
        data = browser_settings_submission(client.get("/settings").text)
        data.update(settings_group=["X", "Y", "RemovedGroup", "Empty"], journal_import_mode="REPLACE",
                    journal_import_text="Journal,ISSN/EISSN,Group\nB,0162-1459,\nA,0090-5364,Changed\n")
        preview = client.post("/settings/import/preview", data=data)
        assert set(re.findall(r'data-import-change="([^"]+)"', preview.text)) == {"ORDER_CHANGE", "ISSN_REMOVE", "GROUP_MOVE", "REMOVE"}
        assert "Removed ISSNs: 0006-341X" in preview.text and "Removed ISSNs: 0033-3123" in preview.text
        assert "Position: 2 → 1" in preview.text and "Position: 1 → 2" in preview.text
        assert "ISSN order: 0006-341X, 0090-5364 → 0090-5364" in preview.text
        applied = client.post("/settings/import/apply", data=browser_settings_submission(preview.text))
        values = browser_settings_values(applied.text)
        assert applied.headers["HX-Trigger"] == "settingsDraftChanged"
        assert values.groups == ("Y", "Changed", "Empty")  # RemovedGroup is not resurrected as empty.
        assert values.journals == (settings_form.SettingsJournalRow("B", "0162-1459", "Y"), settings_form.SettingsJournalRow("A", "0090-5364", "Changed"))
        assert_import_does_not_write(health_config, before)
        saved = client.post("/settings/save", data=browser_settings_submission(applied.text))
        assert saved.headers["HX-Trigger"] == "settingsSaved"
    assert load_config(health_config).journals == (
        JournalConfig(name="B", issn=("0162-1459",), group="Y"), JournalConfig(name="A", issn=("0090-5364",), group="Changed"),
    )


@pytest.mark.parametrize("contents,kind", [
    ("This is arbitrary prose, not a supported table.", "FORMAT_ERROR"),
    ("Journal,ISSN/EISSN\nNew,not-an-issn\nPeer,0090-5364\n", "INVALID_ROW"),
    ("Journal,ISSN/EISSN\nFirst,0090-5364\nOther,0090-5364\nPeer,0033-3123\n", "CONFLICT"),
    ("Journal,ISSN/EISSN\nOther name,0006-341X\nPeer,0033-3123\n", "CONFLICT"),
    ("Journal,ISSN/EISSN,Group\nNew,0090-5364,X\nNew,0162-1459,Y\nPeer,0033-3123,\n", "CONFLICT"),
    ("Journal,ISSN/EISSN\nNew,0090-5364,unexpected\n", "INVALID_ROW"),
])
def test_import_blocking_is_whole_apply_and_preserves_exact_form(health_config, contents, kind):
    before = (health_config.read_bytes(), (health_config.parent / "list.md").read_bytes())
    with TestClient(create_app(health_config), base_url="http://localhost") as client:
        data = browser_settings_submission(client.get("/settings").text)
        data.update(settings_group=["Empty"], journal_import_text=contents, name=" Unsaved name ", output_dir=" output ")
        expected_values = settings_form.settings_form_from_submission(FormData([
            (n, v) for n, values in data.items() for v in (values if isinstance(values, list) else [values])
        ]))
        preview = client.post("/settings/import/preview", data=data)
        assert f'data-import-change="{kind}"' in preview.text
        assert "Apply blocked" in preview.text and 'data-import-apply' not in preview.text
        applied = client.post("/settings/import/apply", data=browser_settings_submission(preview.text))
        for response in (preview, applied):
            assert browser_settings_values(response.text) == expected_values
            assert browser_settings_submission(response.text)["journal_import_text"] == [contents]
            assert "HX-Trigger" not in response.headers
            assert 'data-import-apply' not in response.text
        if "conflicting non-empty Groups" in preview.text:
            assert "Group: X → Y" in preview.text and "Source rows: 2, 3" in preview.text
    assert_import_does_not_write(health_config, before)


@pytest.mark.parametrize("source", ["csv", "tsv", "markdown"])
def test_web_import_formats_and_safe_unicode_roundtrip(health_config, source):
    group = '统计 & <x> "y"'
    if source == "csv":
        contents = '\ufeffJournal,ISSN/EISSN,Group\nBiometrics,0006-341X,"统计 & <x> ""y"""\n'
    elif source == "tsv":
        contents = f'Journal\tISSN/EISSN\tGroup\nBiometrics\t0006-341X\t"统计 & <x> ""y"""\n'
    else:
        contents = f"## Journals\n| Journal | ISSN/EISSN | Group |\n|---|---|---|\n| Biometrics | 0006-341X | {group} |\n\n## Conferences\nThis is ignored.\n"
    before = (health_config.read_bytes(), (health_config.parent / "list.md").read_bytes())
    with TestClient(create_app(health_config), base_url="http://localhost") as client:
        data = browser_settings_submission(client.get("/settings").text)
        data["journal_import_text"] = contents
        preview = client.post("/settings/import/preview", data=data)
        assert "Ready to Apply" in preview.text and 'data-import-change="GROUP_MOVE"' in preview.text
        assert browser_settings_submission(preview.text)["journal_import_text"] == [contents]
        applied = client.post("/settings/import/apply", data=browser_settings_submission(preview.text))
    assert applied.headers["HX-Trigger"] == "settingsDraftChanged"
    assert browser_settings_values(applied.text).journals[0].group == group
    assert 'value="统计 &amp; &lt;x&gt; &#34;y&#34;" selected' in applied.text
    assert_import_does_not_write(health_config, before)


def test_apply_replans_changed_draft_and_ignores_browser_plan(health_config):
    before = (health_config.read_bytes(), (health_config.parent / "list.md").read_bytes())
    with TestClient(create_app(health_config), base_url="http://localhost") as client:
        data = browser_settings_submission(client.get("/settings").text)
        data["journal_import_text"] = "Journal,ISSN/EISSN\nNew,0090-5364\n"
        preview = client.post("/settings/import/preview", data=data)
        assert "Ready to Apply" in preview.text
        changed = browser_settings_submission(preview.text)
        changed.update(journal_name=["Biometrics", "Draft owner"], journal_issns=["0006-341X", "0090-5364"],
                       journal_group=["", "DraftGroup"], settings_group=["DraftGroup", "Empty"],
                       resulting_journals='[{"name":"Trusted?","issn":["0033-3123"]}]', plan_can_apply="true")
        applied = client.post("/settings/import/apply", data=changed)
        assert "Apply blocked" in applied.text and 'data-import-change="CONFLICT"' in applied.text
        assert "Draft owner" in applied.text and "Trusted?" not in applied.text
        assert "HX-Trigger" not in applied.headers
        assert browser_settings_values(applied.text).journals == (
            settings_form.SettingsJournalRow("Biometrics", "0006-341X", ""),
            settings_form.SettingsJournalRow("Draft owner", "0090-5364", "DraftGroup"),
        )
        assert browser_settings_values(applied.text).groups == ("DraftGroup", "Empty")
    assert_import_does_not_write(health_config, before)


def test_import_apply_preserves_open_revision_for_external_change_conflict(health_config):
    path = health_config.parent / "list.md"
    with TestClient(create_app(health_config), base_url="http://localhost") as client:
        data = browser_settings_submission(client.get("/settings").text)
        revision_before = data["journal_revision_digest"]
        data["journal_import_text"] = "Journal,ISSN/EISSN,Group\nNew,0090-5364,Imported\n"
        preview = client.post("/settings/import/preview", data=data)
        applied = client.post("/settings/import/apply", data=browser_settings_submission(preview.text))
        assert browser_settings_submission(applied.text)["journal_revision_digest"] == revision_before
        path.write_bytes(path.read_bytes() + b"\n# External user edit\n")
        external_bytes = path.read_bytes()
        monitor_before = health_config.read_bytes()
        saved = client.post("/settings/save", data=browser_settings_submission(applied.text))
        assert "files changed since this editor was opened" in saved.text and "HX-Trigger" not in saved.headers
        assert browser_settings_submission(saved.text)["journal_revision_digest"] == revision_before
        assert browser_settings_values(saved.text).journals == browser_settings_values(applied.text).journals
    assert path.read_bytes() == external_bytes and health_config.read_bytes() == monitor_before


@pytest.mark.parametrize("file_kind", ["csv", "tsv", "md", "plain", "invalid_utf8", "binary", "read_failure"])
def test_executable_import_file_dirty_events_and_fragment_scroll(health_config, file_kind):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable for executable import file/DOM tests")
    if file_kind == "tsv":
        text = "Journal\tISSN/EISSN\tGroup\nNew\t0090-5364\tImported\n"
    elif file_kind == "md":
        text = "## Journals\n| Journal | ISSN/EISSN | Group |\n|---|---|---|\n| New | 0090-5364 | Imported |\n"
    else:
        text = "\ufeffJournal,ISSN/EISSN,Group\nNew,0090-5364,Imported\n"
    journal_path = health_config.parent / "list.md"
    existing = [("Biometrics", "0006-341X")]
    for i in range(30):
        digits = f"777{i:04d}"
        check = (11 - sum(int(d) * weight for d, weight in zip(digits, range(8, 1, -1))) % 11) % 11
        existing.append((f"Existing {i}", digits[:4] + "-" + digits[4:] + ("X" if check == 10 else str(check))))
    journal_path.write_text("## Journals\n| Journal | ISSN/EISSN |\n|---|---|\n" + "".join(f"| {n} | {i} |\n" for n, i in existing))
    app = create_app(health_config)
    before = (health_config.read_bytes(), (health_config.parent / "list.md").read_bytes())
    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/settings").text
        javascript = client.get("/static/app.js").text
    harness = SETTINGS_NODE_DOM + r'''
const editor = () => document.getElementById("settings-editor");
const clean = () => assert.equal(document.documentElement.dataset.settingsDirty, "false");
const dirty = () => assert.equal(document.documentElement.dataset.settingsDirty, "true");
const control = selector => editor().querySelector(selector);
const swap = (tree, top = 419, event = null) => {
  control("[data-journal-viewport]").scrollTop = top;
  bodyHandlers["htmx:beforeSwap"]({detail: {target: editor()}});
  if (event) bodyHandlers[event]();
  dom = build(tree);
  bodyHandlers["htmx:afterSwap"]({detail: {target: editor()}});
  assert.equal(control("[data-journal-viewport]").scrollTop, top);
};
(async () => {
  clean();
  const textarea = control("[data-journal-import-text]"), input = control("[data-journal-import-file]");
  assert.equal(input.attrs.name, undefined);
  let bytes = Buffer.from(TEXT, "utf8");
  if (KIND === "invalid_utf8") bytes = Buffer.from([0xff, 0xfe, 0x00]);
  const file = {
    name: KIND === "binary" ? "journals.xlsx" : KIND === "plain" ? "journals.txt" : `journals.${KIND}`,
    type: KIND === "binary" ? "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" : "text/plain",
    arrayBuffer: async () => {
      if (KIND === "read_failure") throw new Error("Read failed");
      return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    },
  };
  input.files = [file];
  textarea.value = "Old source must not survive invalid input";
  handlers.change({target: input});
  clean();
  await new Promise(resolve => setImmediate(resolve));
  const failed = ["invalid_utf8", "binary", "read_failure"].includes(KIND);
  if (failed) {
    assert.equal(textarea.value, ""); assert.equal(control("[data-import-file-error]").hidden, false);
    assert.ok(control("[data-import-file-error]").textContent.includes("UTF-8"));
    assert.equal(control("[data-import-preview]").disabled, true); clean();
    // Pasting a supported source recovers without changing Settings dirty state.
    textarea.value = TEXT; handlers.input({target: textarea});
  }
  assert.equal(textarea.value, TEXT); assert.equal(textarea.disabled, false); clean();
  assert.equal(control("[data-import-preview]").disabled, false);
  const mode = control('[name="journal_import_mode"]');
  mode.value = "REPLACE"; handlers.change({target: mode}); clean();
  mode.value = "MERGE"; handlers.change({target: mode}); clean();
  handlers.input({target: textarea}); clean();
  handlers.click({target: control("[data-import-preview]")}); clean();
  if (!RESPONSES) {
    const fields = {};
    for (const field of editor().querySelectorAll("input, select, textarea")) {
      if (field.attrs.name) (fields[field.attrs.name] ||= []).push(field.value);
    }
    assert.equal(fields.journal_import_text[0], TEXT);
    assert.ok(!Object.keys(fields).some(key => /file|path/i.test(key)));
    console.log(JSON.stringify(fields)); return;
  }
  swap(RESPONSES.preview); clean();
  assert.equal(control("[data-journal-import-text]").value, TEXT);
  assert.ok(control("[data-import-apply]"));
  // Preview and blocked Apply must also preserve an already-dirty editor.
  handlers.input({target: control('[name="name"]')}); dirty();
  swap(RESPONSES.preview, 321); dirty(); swap(RESPONSES.blocked, 325); dirty();
  bodyHandlers.settingsSaved(); clean(); swap(RESPONSES.blocked, 327); clean();
  assert.equal(control("[data-import-apply]"), null);
  swap(RESPONSES.applied, 421, "settingsDraftChanged"); dirty();
  const journals = () => editor().querySelectorAll("[data-journal-rows] .journal-row");
  assert.equal(journals().length, ROWCOUNT + 1);
  const newRow = journals().find(row => row.querySelector('[name="journal_name"]').value === "New");
  assert.equal(newRow.querySelector('[name="journal_group"]').value, "Imported");
  // The imported editor is still the ordinary A6 editor.
  let group = editor().querySelectorAll("[data-group-rows] [data-group-row]").find(g => g.dataset.groupName === "Imported");
  group.querySelector("[data-group-edit]").value = "Renamed";
  handlers.click({target: group.querySelector("[data-rename-group]")}); dirty();
  assert.equal(newRow.querySelector('[name="journal_group"]').value, "Renamed");
  control("[data-new-group]").value = "Empty";
  handlers.click({target: control("[data-create-group]")});
  group = editor().querySelectorAll("[data-group-rows] [data-group-row]").find(g => g.dataset.groupName === "Empty");
  handlers.click({target: group.querySelector("[data-group-up]")});
  handlers.click({target: group.querySelector("[data-group-down]")});
  handlers.click({target: group.querySelector("[data-delete-group]")}); dirty();
  assert.equal(newRow.querySelector('[name="journal_group"]').value, "Renamed");
  swap(RESPONSES.validated, 431); dirty();
  // Save clears dirty only through its existing event.
  swap(RESPONSES.saved, 439, "settingsSaved"); clean();
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    preamble = ("const SOURCE = " + json.dumps(javascript) + "; const PAGE = " + json.dumps(SettingsDOM(page).root) +
                "; const TEXT = " + json.dumps(text) + "; const KIND = " + json.dumps(file_kind) + "; const ROWCOUNT = " + str(len(existing)) + ";\n")
    initial = subprocess.run([node], input=preamble + "const RESPONSES = null;\n" + harness, text=True, capture_output=True)
    assert initial.returncode == 0, initial.stdout + initial.stderr
    data = json.loads(initial.stdout)
    assert data["journal_import_text"] == [text]
    with TestClient(app, base_url="http://localhost") as client:
        preview = client.post("/settings/import/preview", data=data)
        assert "HX-Trigger" not in preview.headers and "Ready to Apply" in preview.text
        blocked_data = browser_settings_submission(preview.text)
        blocked_data["journal_import_text"] = "unsupported text"
        blocked = client.post("/settings/import/apply", data=blocked_data)
        assert "HX-Trigger" not in blocked.headers and "Apply blocked" in blocked.text
        applied = client.post("/settings/import/apply", data=browser_settings_submission(preview.text))
        assert applied.headers["HX-Trigger"] == "settingsDraftChanged"
        validated = client.post("/settings/validate", data=browser_settings_submission(applied.text))
        assert "HX-Trigger" not in validated.headers and "Settings draft is valid" in validated.text
        assert_import_does_not_write(health_config, before)
        saved = client.post("/settings/save", data=browser_settings_submission(validated.text))
        assert saved.headers["HX-Trigger"] == "settingsSaved"
    responses = {key: SettingsDOM(response.text).root for key, response in (
        ("preview", preview), ("blocked", blocked), ("applied", applied), ("validated", validated), ("saved", saved),
    )}
    result = subprocess.run([node], input=preamble + "const RESPONSES = " + json.dumps(responses) + ";\n" + harness,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def zotero_settings(health_config, monkeypatch):
    """Real HTTP clients/runtime, synthetic Local API and fake OS backend only."""
    from types import SimpleNamespace
    import httpx
    import logging
    from literature_monitor import zotero_credentials as credentials
    from literature_monitor.zotero_local import ZoteroLocalClient
    from literature_monitor.zotero_write import ZoteroAuthorizationClient

    s = SimpleNamespace(server='settings-instance-A', remember=False, status=200,
                        requests=[], values={}, calls=[], fail=None, response_server=None, probe_status=200,
                        secret='SETTINGSSENTINELSECRET'.ljust(32, '0'))
    class Backend:
        def record(self, action, service, server):
            s.calls.append((action, service, server))
            logging.getLogger("keyring").warning("backend key %s", s.secret)
            if s.fail == action:
                raise RuntimeError(s.secret + ' private backend details')
        def get_password(self, service, server):
            self.record('get', service, server)
            return s.values.get((service, server))
        def set_password(self, service, server, key):
            self.record('set', service, server)
            s.values[service, server] = key
        def delete_password(self, service, server):
            self.record('delete', service, server)
            s.values.pop((service, server), None)
    def respond(request):
        s.requests.append(request)
        logging.getLogger("httpx").warning("private Local API payload %s", s.secret)
        headers = {'Zotero-Server-ID': s.server}
        assert request.url.host == 'localhost' and request.url.port == 23119
        assert 'Cookie' not in request.headers and 'Authorization' not in request.headers
        if request.method == 'GET':
            assert request.url.path == '/api/'
            return httpx.Response(s.probe_status, headers=headers, json={'private': s.secret})
        assert request.method == 'POST' and request.url.path == '/api/local/authorize'
        assert request.headers['Zotero-Server-ID'] == s.server
        assert 'Zotero-API-Key' not in request.headers
        assert json.loads(request.content) == {'appName': 'Literature Monitor'}
        headers.update({'Retry-After': '30'})
        if s.response_server is not None:
            headers['Zotero-Server-ID'] = s.response_server
        return httpx.Response(s.status, headers=headers, json={'key': s.secret, 'remember': s.remember})
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(credentials, '_os_backend', Backend)
    monkeypatch.setattr(web_app, 'ZoteroLocalClient', lambda: ZoteroLocalClient(transport=transport))
    monkeypatch.setattr(web_app, 'ZoteroAuthorizationClient', lambda server, **kwargs:
                        ZoteroAuthorizationClient(server, transport=transport, **kwargs))
    s.app = create_app(health_config)
    s.config = health_config
    s.post = lambda client, **data: client.post('/settings/zotero/authorize',
        data={'csrf_token': s.app.state.csrf_token, **data})
    return s


def test_zotero_settings_get_and_status_are_read_only(zotero_settings):
    s = zotero_settings
    with TestClient(s.app, base_url='http://localhost') as client:
        page = client.get('/settings')
        assert 'Zotero integration' in page.text and not s.requests
        status = client.get('/settings/zotero')
    assert status.status_code == 200 and s.server in status.text
    assert 'Authorize Zotero writes' in status.text
    assert 'name="csrf_token"' in status.text
    assert 'Settings → Advanced &amp; Diagnostics → Zotero integration' in status.text
    assert all(r.method == 'GET' for r in s.requests)
    assert s.secret not in page.text + status.text + repr(status.headers)


@pytest.mark.parametrize('mode', ['missing_csrf', 'wrong_csrf', 'get', 'host'])
def test_zotero_settings_authorization_requires_post_csrf_and_local_host(zotero_settings, mode):
    s = zotero_settings
    with TestClient(s.app, base_url='http://localhost') as client:
        if mode == 'get':
            result = client.get('/settings/zotero/authorize')
            assert result.status_code == 405
        else:
            data = {} if mode == 'missing_csrf' else {'csrf_token': 'wrong'}
            headers = {}
            if mode == 'host':
                data = {'csrf_token': s.app.state.csrf_token}
                headers = {'Host': 'untrusted.example'}
            result = client.post('/settings/zotero/authorize', data=data, headers=headers)
            assert result.status_code == (400 if mode == 'host' else 403)
    assert not s.requests and not s.calls


@pytest.mark.parametrize('remember', [False, True])
def test_zotero_settings_explicit_action_uses_verified_instance_and_runtime_only(zotero_settings, remember, caplog):
    from literature_monitor.zotero_credentials import SERVICE_NAME
    s = zotero_settings; s.remember = remember
    before = {p: p.read_bytes() for p in s.config.parent.rglob('*') if p.is_file()}
    with TestClient(s.app, base_url='http://localhost') as client:
        authorized = s.post(client, server_id='forged-instance')
        status = client.get('/settings/zotero')
        s.app = create_app(s.config)
        with TestClient(s.app, base_url='http://localhost') as fresh:
            restarted = fresh.get('/settings/zotero')
    assert authorized.status_code == 200
    label = 'Remembered write authorization is available' if remember else 'One-time Allow is ready'
    assert label in authorized.text and label in status.text
    assert ('Remembered write authorization is available' in restarted.text) is remember
    if not remember:
        assert 'Authorize Zotero writes' in restarted.text and 'One-time Allow is ready' not in restarted.text
    assert len([r for r in s.requests if r.method == 'POST']) == 1
    assert s.values == ({(SERVICE_NAME, s.server): s.secret} if remember else {})
    assert all(service == SERVICE_NAME and server == s.server for _, service, server in s.calls)
    assert sum(action == 'set' for action, _, _ in s.calls) == int(remember)
    assert before == {p: p.read_bytes() for p in s.config.parent.rglob('*') if p.is_file()}
    assert s.secret not in authorized.text + status.text + restarted.text + caplog.text + repr(authorized.headers)


@pytest.mark.parametrize('remember', [False, True])
def test_zotero_settings_instance_change_never_reuses_prior_authorization(zotero_settings, remember):
    s = zotero_settings; s.remember = remember
    with TestClient(s.app, base_url='http://localhost') as client:
        s.post(client)
        s.server = 'settings-instance-B'; s.calls.clear()
        changed = client.get('/settings/zotero')
        assert s.server in changed.text
        assert 'One-time Allow is ready' not in changed.text
        assert 'Remembered write authorization is available' not in changed.text
        assert all(server == s.server for _, _, server in s.calls)
        if not remember:
            s.server = 'settings-instance-A'
            assert 'One-time Allow is ready' not in client.get('/settings/zotero').text


@pytest.mark.parametrize('action', ['get', 'set'])
def test_zotero_settings_secure_store_failure_is_sanitized_without_fallback(zotero_settings, action, caplog):
    s = zotero_settings; s.fail = action; s.remember = True
    before = {p: p.read_bytes() for p in s.config.parent.rglob('*') if p.is_file()}
    with TestClient(s.app, base_url='http://localhost') as client:
        result = s.post(client) if action == 'set' else client.get('/settings/zotero')
        again = client.get('/settings/zotero')
    assert 'OS credential store' in result.text and 'OS credential store' in again.text
    assert 'Remembered write authorization is available' not in result.text + again.text
    assert 'One-time Allow is ready' not in result.text + again.text
    assert s.values == {}
    assert s.secret not in result.text + again.text + caplog.text
    assert 'private backend details' not in result.text + again.text
    assert before == {p: p.read_bytes() for p in s.config.parent.rglob('*') if p.is_file()}


@pytest.mark.parametrize('status,label', [(403, 'denied'), (429, 'rate limited')])
def test_zotero_settings_denial_and_rate_limit_do_not_loop(zotero_settings, status, label):
    s = zotero_settings; s.status = status
    with TestClient(s.app, base_url='http://localhost') as client:
        result = s.post(client)
        assert label in result.text
        if status == 429:
            repeated = s.post(client)
            assert 'disabled' in repeated.text and 'rate limited' in repeated.text
    assert not s.values
    assert len([r for r in s.requests if r.method == 'POST']) == 1
    assert s.secret not in result.text


def test_zotero_settings_changed_instance_during_dialog_saves_nothing(zotero_settings):
    s = zotero_settings; s.remember = True; s.response_server = 'settings-instance-B'
    with TestClient(s.app, base_url='http://localhost') as client:
        result = s.post(client)
    assert 'instance changed' in result.text
    assert not s.values and not s.calls
    assert s.secret not in result.text


@pytest.mark.parametrize('status', [403, 500])
def test_zotero_settings_unavailable_cannot_read_credentials_or_authorize(zotero_settings, status):
    s = zotero_settings; s.probe_status = status
    with TestClient(s.app, base_url='http://localhost') as client:
        observed = client.get('/settings/zotero')
        action = s.post(client)
    assert 'enable its Local API' in observed.text + action.text
    assert 'Authorize Zotero writes' not in observed.text + action.text
    assert all(r.method == 'GET' for r in s.requests)
    assert not s.calls and not s.values
    assert s.secret not in observed.text + action.text
