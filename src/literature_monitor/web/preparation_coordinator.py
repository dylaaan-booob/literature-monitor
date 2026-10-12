"""Process-local CARSI Preparation and independent Connector bridge (§44.4, §44.7).

Lock order: Preparation._lock -> CaptureCoordinator._lock. Import's
BatchImport._mutex -> CaptureCoordinator._lock. Never reverse either order;
never hold CaptureCoordinator._lock while acquiring Workspace/import locks.
Production accepts the bounded A4 capability only from the Connector runtime.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from urllib.parse import urlencode

from pydantic import ValidationError

from literature_monitor.application.access_service_migration import _source_identity
from literature_monitor.application.full_markdown_import import MAX_MARKDOWN_BYTES
from literature_monitor.application.settings import SettingsIssueSource, _read_snapshot, load_settings
from literature_monitor.application.workspace import journal_for_paper, load_workspace
from literature_monitor.config import (
    AccessService, ConfigurationError, InstitutionConfig, load_config, parse_settings_list_text,
    resolve_config_path, validate_access_mapping, validate_publisher_membership,
)
from literature_monitor.safe_write import ContentChangedError
from literature_monitor.web.capture_coordinator import CaptureCoordinator

CONNECTOR_STALE_SECONDS = 20.0
SERVICE_WAIT_SECONDS = 90.0  # Bounded official operation / missing Result.
LOGIN_WAIT_SECONDS = 600.0  # Human-controlled IdP/MFA is not subject to Visit's 10-second deadline.
RELEASE_GRACE_SECONDS = 10.0  # Bounds authority recovery after a lost Connector callback.
EXECUTOR_CAPABILITY = "carsi-preparation/a4-verified"
COMPATIBLE_VERSIONS = frozenset({"0.6.4", "0.6.5", "0.6.6", "0.6.7"})
CARSI_LOGIN_URL = "https://ds.carsi.edu.cn/Shibboleth.sso/Login"
CARSI_PORTAL_URL = "https://ds.carsi.edu.cn/wxds"
REASONS = frozenset({
    "resource_not_found", "ambiguous_resource", "unverified_official_page",
    "navigation_failed", "navigation_incomplete", "timeout", "manual_action_required",
    "connector_error", "connector_disconnected", "unsupported", "unknown",
})


@dataclass(frozen=True)
class VisitEvidence:
    nonce: str
    visit_action_observed: bool
    source_tab_id: int
    target_tab_id: int
    created_target_tab_id: int | None
    navigation_observed: bool

class PreparationPhase(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    STOPPED = "stopped"
    BLOCKED = "blocked"


class PreparationOutcome(str, Enum):
    OPENED = "Opened"
    NEEDS_ATTENTION = "Needs attention"
    FAILED = "Failed"


@dataclass(frozen=True)
class PreparationResult:
    service_id: str
    outcome: PreparationOutcome
    reason: str


@dataclass(frozen=True)
class PreparationSnapshot:
    phase: PreparationPhase
    task_id: str | None
    generation: int
    services: tuple[AccessService, ...]
    current_service_id: str | None
    results: tuple[PreparationResult, ...]
    message: str
    authorization_pending: bool = False
    waiting_for_user: bool = False
    manual_services: tuple[AccessService, ...] = ()


@dataclass(frozen=True)
class PreparationCommand:
    task_id: str
    generation: int
    request_id: str
    service_id: str
    name: str
    access_url: str | None
    kind: str = "carsi-preparation/v1"
    login_url: str | None = None
    last_service: bool = False


@dataclass
class _Task:
    task_id: str
    generation: int
    services: tuple[AccessService, ...]
    owner: str
    connector_instance: str
    manual_services: tuple[AccessService, ...] = ()
    login_url: str | None = None
    phase: PreparationPhase = PreparationPhase.RUNNING
    index: int = 0
    results: list[PreparationResult] = field(default_factory=list)
    message: str = "Awaiting CARSI preparation command claim"
    command: PreparationCommand | None = None
    claimed_at: float | None = None
    started_item_at: float = field(default_factory=time.monotonic)
    # 最近一次结果的身份与内容仅用于幂等响应，不能授予新的操作权限。
    # Bounded to one receipt per frozen Service, for idempotent delayed retries.
    result_receipts: dict[str, tuple[dict[str, object], PreparationOutcome, str, VisitEvidence | None]] = field(default_factory=dict)
    revoke: bool = False
    release_after: float | None = None
    visit_nonce: str | None = None
    visit_source_tab: int | None = None
    waiting_for_user: bool = False


def institution_login_url(entity_id: str | None) -> str | None:
    """Construct the official CARSI entry only from a schema-valid IdP entity ID."""
    try:
        validated = InstitutionConfig(idp_entity_id=entity_id).idp_entity_id
    except (ValidationError, ValueError) as error:
        raise ConfigurationError("Institution.idp_entity_id is invalid") from error
    if validated is None:
        return None
    return f"{CARSI_LOGIN_URL}?{urlencode({'entityID': validated, 'target': CARSI_PORTAL_URL})}"


def saved_institution_login_url(config_path: Path) -> str | None:
    """Read validated saved Institution configuration, never draft/browser state."""
    config = load_config(config_path)
    return institution_login_url(config.institution.idp_entity_id if config.institution else None)


def read_saved_preparation_services(config_path: Path) -> tuple[AccessService, ...]:
    """Snapshot Services needed by current Kept Papers, using saved configuration only."""
    state = load_settings(config_path)
    path = state.journal_path.absolute()
    try:
        identity = _source_identity(path)
        if path.stat().st_size > MAX_MARKDOWN_BYTES:
            raise ConfigurationError("Saved list.md exceeds size boundary")
        snapshot = _read_snapshot(path, source=SettingsIssueSource.JOURNAL_DATA)
        if snapshot.issue or snapshot.contents is None:
            raise ConfigurationError("Saved list.md is missing or unsafe")
        journals, publishers, services = parse_settings_list_text(snapshot.contents, path=path)
        validate_publisher_membership(journals, publishers)
        validate_access_mapping(publishers, services)
        workspace = load_workspace(resolve_config_path(config_path, state.draft.output_dir, "workspace"), journals)
        if workspace.issues:
            raise ConfigurationError("Workspace Paper data cannot safely be read")
        by_publisher = {publisher.publisher_id: publisher for publisher in publishers}
        needed = set()
        # 复用 Workspace 的归属语义，不猜测 Publisher 或 DOI 落地平台。
        for paper in workspace.kept:
            journal = journal_for_paper(paper, journals)
            publisher = by_publisher.get(journal.publisher_id) if journal else None
            if publisher is not None and publisher.access_service_id is not None:
                needed.add(publisher.access_service_id)
        if _source_identity(path) != identity:
            raise ContentChangedError("list.md identity changed while reading")
        again = _read_snapshot(path, source=SettingsIssueSource.JOURNAL_DATA)
        if (again.issue or again.revision != snapshot.revision
                or again.contents != snapshot.contents
                or _source_identity(path) != identity):
            raise ContentChangedError("list.md changed while preparing snapshot")
    except (OSError, ConfigurationError, ContentChangedError) as error:
        raise ConfigurationError("Saved current-schema list.md cannot safely start Preparation") from error
    return tuple(service for service in services if service.id in needed)


class PreparationCoordinator:
    def __init__(self, capture: CaptureCoordinator, *, execution_enabled: bool = False):
        self._lock = threading.Lock()
        self._capture = capture
        self._execution_enabled = execution_enabled
        self._connector_instance: str | None = None
        self._last_heartbeat: float | None = None
        self._capability_valid = False
        self._generation = 0
        self._task: _Task | None = None
        self._last = PreparationSnapshot(PreparationPhase.IDLE, None, 0, (), None, (), "Not started")

    def _ready_locked(self, now: float) -> bool:
        return (self._execution_enabled and self._capability_valid
                and self._connector_instance is not None
                and self._last_heartbeat is not None
                and now - self._last_heartbeat < CONNECTOR_STALE_SECONDS)

    def ready(self) -> bool:
        with self._lock:
            return self._ready_locked(time.monotonic())

    def heartbeat(self, instance_id: str, *, capability: str | None = None,
                  version: str | None = None) -> bool:
        if not isinstance(instance_id, str) or not 16 <= len(instance_id) <= 128:
            return False
        now = time.monotonic()
        with self._lock:
            if self._connector_instance is not None and self._connector_instance != instance_id:
                self._stop_locked("Connector instance changed; previous task revoked")
            compatible = capability == EXECUTOR_CAPABILITY and version in COMPATIBLE_VERSIONS
            if not compatible:
                self._stop_locked("Connector preparation executor capability or version unavailable")
            self._connector_instance = instance_id
            self._last_heartbeat = now
            self._capability_valid = compatible
            return self._ready_locked(now)

    def _release_locked(self, task: _Task) -> None:
        self._capture.release_browser_task("preparation", task.owner)

    def _stop_locked(self, message: str) -> None:
        task = self._task
        if task is None or task.phase is not PreparationPhase.RUNNING:
            return
        # 先撤销授权；至多保留短暂隔离期，不能由外站页面无限占有浏览器。
        task.revoke = True
        if task.index < len(task.services):
            task.results.append(PreparationResult(
                task.services[task.index].id, PreparationOutcome.NEEDS_ATTENTION,
                "connector_disconnected",
            ))
            task.index += 1
        task.phase = PreparationPhase.BLOCKED
        task.message = message
        task.release_after = time.monotonic() + RELEASE_GRACE_SECONDS if task.claimed_at else None
        if task.claimed_at is None:
            self._release_locked(task)

    def _expire_locked(self, now: float) -> None:
        task = self._task
        if task is None:
            return
        if task.release_after is not None and now >= task.release_after:
            self._release_locked(task)
            task.claimed_at = None
            task.command = None
            task.release_after = None
        if task.phase is not PreparationPhase.RUNNING:
            return
        if not self._ready_locked(now) or task.connector_instance != self._connector_instance:
            self._stop_locked("Connector disconnected; browser authority revoked")
            return
        if now - task.started_item_at > (LOGIN_WAIT_SECONDS if task.waiting_for_user else SERVICE_WAIT_SECONDS):
            # 没有 Connector Result 时也产生确定的人工处理结果。
            self._advance_locked(PreparationResult(
                task.services[task.index].id, PreparationOutcome.NEEDS_ATTENTION,
                "timeout",
            ), revoke=True)

    def start(self, config_path: Path) -> PreparationSnapshot:
        # Read outside locks; a later user edit cannot mutate the frozen tuple.
        relevant = read_saved_preparation_services(config_path)
        services = tuple(service for service in relevant if service.access_method == "carsi")
        manual = tuple(service for service in relevant if service.access_method == "manual")
        login_url = saved_institution_login_url(config_path) if services else None
        with self._lock:
            now = time.monotonic()
            self._expire_locked(now)
            if self._task is not None and (
                self._task.phase is PreparationPhase.RUNNING
                or self._task.claimed_at is not None
            ):
                return replace(self._snapshot_locked(), phase=PreparationPhase.BLOCKED,
                               message="Previous Preparation still owns the browser task")
            if not services:
                # 纯人工访问不创建 Task、Connector command 或浏览器所有权。
                self._task = None
                self._last = PreparationSnapshot(
                    PreparationPhase.COMPLETED, None, self._generation, (), None, (),
                    "No related CARSI Services; use Manual Service Access when available",
                    manual_services=manual,
                )
                return self._last
            if not self._ready_locked(now):
                self._task = None
                self._last = PreparationSnapshot(
                    PreparationPhase.BLOCKED, None, self._generation, services,
                    None, (), "CARSI browser executor not verified or Connector unavailable",
                    manual_services=manual,
                )
                return self._last
            owner = self._capture.reserve_browser_task("preparation")
            if owner is None:
                self._task = None
                self._last = PreparationSnapshot(
                    PreparationPhase.BLOCKED, None, self._generation, services, None, (),
                    "Another browser task or native-save pipeline still owns the browser",
                    manual_services=manual,
                )
                return self._last
            self._generation += 1
            self._task = _Task(
                secrets.token_urlsafe(32), self._generation, services, owner,
                self._connector_instance, login_url=login_url,
                manual_services=manual, started_item_at=now,
            )
            return self._snapshot_locked()

    def _snapshot_locked(self) -> PreparationSnapshot:
        task = self._task
        if task is None:
            return self._last
        return PreparationSnapshot(
            phase=task.phase, task_id=task.task_id, generation=task.generation,
            services=task.services,
            current_service_id=(task.services[task.index].id if task.index < len(task.services)
                                and (task.phase is PreparationPhase.RUNNING
                                     or task.claimed_at is not None) else None),
            results=tuple(task.results), message=task.message,
            authorization_pending=task.claimed_at is not None,
            waiting_for_user=(task.phase is PreparationPhase.RUNNING
                              and task.waiting_for_user and not task.revoke),
            manual_services=task.manual_services,
        )

    def snapshot(self) -> PreparationSnapshot:
        with self._lock:
            self._expire_locked(time.monotonic())
            return self._snapshot_locked()

    def claim(self, instance_id: str) -> PreparationCommand | None:
        with self._lock:
            self._expire_locked(time.monotonic())
            task = self._task
            if (task is None or task.phase is not PreparationPhase.RUNNING
                    or task.connector_instance != instance_id or task.revoke
                    or task.claimed_at is not None or task.index >= len(task.services)):
                return None
            service = task.services[task.index]
            task.command = PreparationCommand(
                task.task_id, task.generation, secrets.token_urlsafe(32),
                service.id, service.name, service.access_url,
                login_url=task.login_url,
                last_service=task.index == len(task.services) - 1,
            )
            task.claimed_at = time.monotonic()
            return task.command

    def _matches_locked(self, instance_id: str, task_id: str, generation: int,
                        request_id: str, service_id: str) -> bool:
        task = self._task
        command = task.command if task else None
        return (task is not None and command is not None
                and instance_id == task.connector_instance
                and task_id == command.task_id and generation == command.generation
                and request_id == command.request_id and service_id == command.service_id
                and task.claimed_at is not None)

    def authorize(self, **identity: object) -> bool:
        """A4 must check this immediately before every future browser action."""
        with self._lock:
            self._expire_locked(time.monotonic())
            return (self._matches_locked(**identity) and self._task is not None
                    and self._task.phase is PreparationPhase.RUNNING
                    and not self._task.revoke)

    def report_waiting(self, *, waiting: bool, **identity: object) -> bool:
        """Connector-observed login wait only; never authentication evidence."""
        with self._lock:
            self._expire_locked(time.monotonic())
            task = self._task
            if (not self._matches_locked(**identity) or task is None
                    or task.phase is not PreparationPhase.RUNNING or task.revoke
                    or not isinstance(waiting, bool)):
                return False
            if task.waiting_for_user != waiting:
                task.started_item_at = time.monotonic()
            task.waiting_for_user = waiting
            task.message = ("Waiting for user to finish institutional login in normal Chrome"
                            if waiting else "Official CARSI resource view detected; preparing Service")
            return True

    def authorize_visit(self, *, source_tab_id: int, **identity: object) -> str | None:
        """Single-use grant, bound to the claimed Service and official detail tab."""
        with self._lock:
            self._expire_locked(time.monotonic())
            task = self._task
            if (not self._matches_locked(**identity) or task is None
                    or task.phase is not PreparationPhase.RUNNING or task.revoke
                    or task.visit_nonce is not None
                    or not isinstance(source_tab_id, int) or isinstance(source_tab_id, bool)
                    or source_tab_id < 0):
                return None
            task.visit_nonce = secrets.token_urlsafe(32)
            task.visit_source_tab = source_tab_id
            return task.visit_nonce

    def result(self, *, outcome: PreparationOutcome, reason: str,
               evidence: VisitEvidence | None = None, **identity: object) -> bool:
        with self._lock:
            self._expire_locked(time.monotonic())
            task = self._task
            if task is None:
                return False
            receipt = task.result_receipts.get(identity.get("service_id", ""))
            if receipt is not None:
                return receipt[0] == identity and receipt[1:] == (outcome, reason, evidence)
            if (not self._matches_locked(**identity) or task.phase is not PreparationPhase.RUNNING
                    or task.revoke):
                return False
            if outcome is PreparationOutcome.OPENED:
                if (reason != "target_opened" or evidence is None or task.visit_nonce is None
                        or not secrets.compare_digest(evidence.nonce, task.visit_nonce)
                        or not evidence.visit_action_observed or not evidence.navigation_observed
                        or evidence.source_tab_id != task.visit_source_tab
                        or evidence.target_tab_id < 0
                        or (evidence.created_target_tab_id is not None and (
                            evidence.created_target_tab_id != evidence.target_tab_id
                            or evidence.target_tab_id == evidence.source_tab_id))):
                    return False
            elif reason not in REASONS or evidence is not None:
                return False
            task.result_receipts[task.services[task.index].id] = (dict(identity), outcome, reason, evidence)
            self._advance_locked(PreparationResult(task.services[task.index].id, outcome, reason))
            return True

    def _advance_locked(self, outcome: PreparationResult, *, revoke: bool = False) -> None:
        task = self._task
        assert task is not None
        claimed = task.claimed_at is not None
        task.results.append(outcome)
        task.index += 1
        task.command = None
        task.claimed_at = None
        task.visit_nonce = None
        task.visit_source_tab = None
        task.waiting_for_user = False
        task.started_item_at = time.monotonic()
        if revoke:
            task.revoke = True
            task.phase = PreparationPhase.BLOCKED
            task.message = "Service deadline exceeded; remaining Preparation canceled"
            # 迟到操作不得重新获得授权。旧浏览器调用只持有有限的隔离期。
            if claimed:
                task.claimed_at = task.started_item_at
                task.release_after = task.started_item_at + RELEASE_GRACE_SECONDS
            else:
                self._release_locked(task)
        elif task.index >= len(task.services):
            task.phase = PreparationPhase.COMPLETED
            task.message = "Preparation queue finished; results do not confirm login or access"
            self._release_locked(task)
        else:
            task.message = "Awaiting next Service command"

    def cancel(self, task_id: str, generation: int) -> bool:
        with self._lock:
            self._expire_locked(time.monotonic())
            task = self._task
            if (task is None or task.task_id != task_id or task.generation != generation
                    or task.phase is not PreparationPhase.RUNNING):
                return False
            task.revoke = True
            if task.claimed_at is not None and task.index < len(task.services):
                task.results.append(PreparationResult(
                    task.services[task.index].id, PreparationOutcome.NEEDS_ATTENTION, "canceled",
                ))
                task.index += 1
            task.phase = PreparationPhase.STOPPED
            task.message = "Canceled; no further browser actions authorized"
            if task.claimed_at is not None:
                task.release_after = time.monotonic() + RELEASE_GRACE_SECONDS
            else:
                self._release_locked(task)
            return True
