from __future__ import annotations

import inspect
import re
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

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
        current_activity=activity,
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
    later = replace(first, current_activity=later_activity)
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
            current_activity=replace(
                later_activity,
                kind=ActivityKind.RETRYING,
                eta_seconds=None,
            ),
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
            current_activity=activity_snapshot(
                operation="materialize_write",
                label="Writing workspace",
                source="workspace",
                current=0,
                total=3,
                unit="file",
                eta_seconds=None,
            ),
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

    assert response.headers["HX-Trigger"] == "runCompleted"


def test_finished_run_result_renders_summary_and_issues(tmp_path: Path) -> None:
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
    assert "5 created" in response.text
    assert "6 existing" in response.text
    assert "one source warning" in response.text
    assert "one source error" in response.text
    assert "OpenAlex coverage: 1/2 complete · 1 failed" in response.text
    assert (
        "Crossref discovery coverage: 0/1 complete · 1 unavailable"
        in response.text
    )
    assert "Crossref supplement coverage: 1/1 complete" in response.text


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

    assert response.status_code == 200
    assert "unexpected internal error" in response.text
    assert "RuntimeError" in response.text
    assert "Traceback" not in response.text


def test_workspace_listens_for_run_completion_and_refreshes_through_workspace_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_calls: list[Path] = []
    monkeypatch.setattr(
        web_app,
        "load_config",
        lambda path: type("Config", (), {"output_dir": tmp_path / "workspace"})(),
    )
    monkeypatch.setattr(
        web_app,
        "load_workspace",
        lambda output_dir: (
            load_calls.append(output_dir)
            or web_app.WorkspaceSnapshot(papers=(), issues=())
        ),
    )
    app = create_app(tmp_path / "monitor.yaml")

    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/")
        fragment = client.get("/fragments/workspace")

    assert 'hx-trigger="runCompleted from:body"' in page.text
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
