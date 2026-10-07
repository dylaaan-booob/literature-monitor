"""Process-local coordination for one automatic Zotero Connector capture."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from urllib.parse import quote
from uuid import UUID

from literature_monitor.application.decisions import ReconciliationGuard
from literature_monitor.identifiers import normalize_doi


HEARTBEAT_STALE_SECONDS = 15.0
WAITING_TIMEOUT_SECONDS = 30.0
ACTIVE_TIMEOUT_SECONDS = 120.0
CONNECTOR_VERSION_MAX_LENGTH = 64
REQUEST_ID_MAX_LENGTH = 128

_monotonic = time.monotonic


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ConnectorReadiness(str, Enum):
    CONNECTED = "connected"
    UNAVAILABLE = "unavailable"


class CaptureStage(str, Enum):
    WAITING_FOR_CONNECTOR = "WAITING_FOR_CONNECTOR"
    CONNECTOR_ACTIVE = "CONNECTOR_ACTIVE"
    FINISHED = "FINISHED"


class CaptureOutcome(str, Enum):
    CONFIRMED = "CONFIRMED"
    UNCONFIRMED = "UNCONFIRMED"
    FAILED = "FAILED"


class CaptureStartOutcome(str, Enum):
    STARTED = "STARTED"
    CONNECTOR_UNAVAILABLE = "CONNECTOR_UNAVAILABLE"
    ALREADY_ACTIVE = "ALREADY_ACTIVE"
    COMPLETION_PENDING = "COMPLETION_PENDING"
    INVALID_DOI = "INVALID_DOI"


@dataclass(frozen=True)
class CaptureCommand:
    request_id: str
    doi_url: str


@dataclass(frozen=True)
class CaptureCompletion:
    request_id: str
    paper_id: UUID
    normalized_doi: str
    capture_guard: ReconciliationGuard
    outcome: CaptureOutcome
    finished_at: datetime


@dataclass(frozen=True)
class CaptureAttemptSnapshot:
    request_id: str
    paper_id: UUID
    normalized_doi: str
    doi_url: str
    stage: CaptureStage
    started_at: datetime
    claimed_at: datetime | None
    finished_at: datetime | None
    terminal_outcome: CaptureOutcome | None


@dataclass(frozen=True)
class CaptureSnapshot:
    readiness: ConnectorReadiness
    connector_version: str | None
    zotero_reachable: bool
    last_heartbeat_at: datetime | None
    attempt: CaptureAttemptSnapshot | None
    completion_pending: bool


@dataclass(frozen=True)
class CaptureStartResult:
    outcome: CaptureStartOutcome
    request_id: str | None = None


@dataclass
class _Attempt:
    request_id: str
    paper_id: UUID
    normalized_doi: str
    capture_guard: ReconciliationGuard
    doi_url: str
    stage: CaptureStage
    started_at: datetime
    started_monotonic: float
    claimed_at: datetime | None = None
    claimed_monotonic: float | None = None
    finished_at: datetime | None = None
    terminal_outcome: CaptureOutcome | None = None


def _doi_target(normalized_doi: str) -> str:
    return f"https://doi.org/{quote(normalized_doi, safe='/')}"


class CaptureCoordinator:
    """Own transient Connector presence, capture state, and completion handoff."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_heartbeat_monotonic: float | None = None
        self._last_heartbeat_at: datetime | None = None
        self._connector_version: str | None = None
        self._zotero_reachable = False
        self._attempt: _Attempt | None = None
        self._completion: CaptureCompletion | None = None

    def heartbeat(
        self,
        *,
        connector_version: str,
        zotero_reachable: bool,
    ) -> ConnectorReadiness:
        """Record bounded process-local Connector presence."""

        version = connector_version.strip() if isinstance(connector_version, str) else ""
        if not version or len(version) > CONNECTOR_VERSION_MAX_LENGTH:
            raise ValueError("invalid Connector version")
        if not isinstance(zotero_reachable, bool):
            raise ValueError("invalid Zotero reachability")

        now_monotonic = _monotonic()
        now = _utc_now()
        with self._lock:
            self._expire_locked(now_monotonic, now)
            self._last_heartbeat_monotonic = now_monotonic
            self._last_heartbeat_at = now
            self._connector_version = version
            self._zotero_reachable = zotero_reachable
            return self._readiness_locked(now_monotonic)

    def start_capture(
        self,
        capture_guard: ReconciliationGuard,
    ) -> CaptureStartResult:
        """Start one transient capture after A3 has completed its own preflight."""

        if not isinstance(capture_guard, ReconciliationGuard):
            return CaptureStartResult(CaptureStartOutcome.INVALID_DOI)
        paper_id = capture_guard.paper_id
        normalized_doi = capture_guard.normalized_doi
        try:
            normalized = normalize_doi(normalized_doi)
        except ValueError:
            normalized = None
        if normalized is None or normalized != normalized_doi:
            return CaptureStartResult(CaptureStartOutcome.INVALID_DOI)

        now_monotonic = _monotonic()
        now = _utc_now()
        with self._lock:
            self._expire_locked(now_monotonic, now)
            if self._attempt is not None and self._attempt.stage is not CaptureStage.FINISHED:
                return CaptureStartResult(CaptureStartOutcome.ALREADY_ACTIVE)
            if self._completion is not None:
                return CaptureStartResult(CaptureStartOutcome.COMPLETION_PENDING)
            if self._readiness_locked(now_monotonic) is not ConnectorReadiness.CONNECTED:
                return CaptureStartResult(CaptureStartOutcome.CONNECTOR_UNAVAILABLE)

            request_id = secrets.token_urlsafe(32)
            self._attempt = _Attempt(
                request_id=request_id,
                paper_id=paper_id,
                normalized_doi=normalized,
                capture_guard=capture_guard,
                doi_url=_doi_target(normalized),
                stage=CaptureStage.WAITING_FOR_CONNECTOR,
                started_at=now,
                started_monotonic=now_monotonic,
            )
            return CaptureStartResult(CaptureStartOutcome.STARTED, request_id)

    def claim_command(self) -> CaptureCommand | None:
        """Atomically claim the current pending command, if it is still eligible."""

        now_monotonic = _monotonic()
        now = _utc_now()
        with self._lock:
            self._expire_locked(now_monotonic, now)
            attempt = self._attempt
            if (
                attempt is None
                or attempt.stage is not CaptureStage.WAITING_FOR_CONNECTOR
                or self._readiness_locked(now_monotonic) is not ConnectorReadiness.CONNECTED
            ):
                return None

            # Claim 与 command payload 在同一把锁内完成，避免 fetch/claim 双阶段竞争。
            attempt.stage = CaptureStage.CONNECTOR_ACTIVE
            attempt.claimed_at = now
            attempt.claimed_monotonic = now_monotonic
            return CaptureCommand(attempt.request_id, attempt.doi_url)

    def submit_result(self, request_id: str, outcome: CaptureOutcome) -> bool:
        """Accept one terminal result only for the current claimed attempt."""

        if (
            not isinstance(request_id, str)
            or not request_id
            or len(request_id) > REQUEST_ID_MAX_LENGTH
            or not isinstance(outcome, CaptureOutcome)
        ):
            return False

        now_monotonic = _monotonic()
        now = _utc_now()
        with self._lock:
            self._expire_locked(now_monotonic, now)
            attempt = self._attempt
            if (
                attempt is None
                or attempt.stage is not CaptureStage.CONNECTOR_ACTIVE
                or attempt.request_id != request_id
            ):
                return False

            self._finish_locked(attempt, outcome, now)
            return True

    def consume_completion(self) -> CaptureCompletion | None:
        """Return one A3 reconciliation handoff at most once."""

        now_monotonic = _monotonic()
        now = _utc_now()
        with self._lock:
            self._expire_locked(now_monotonic, now)
            completion = self._completion
            self._completion = None
            return completion

    def snapshot(self) -> CaptureSnapshot:
        """Copy current process-local state after applying lazy expiry."""

        now_monotonic = _monotonic()
        now = _utc_now()
        with self._lock:
            self._expire_locked(now_monotonic, now)
            attempt = self._attempt
            attempt_snapshot = (
                None
                if attempt is None
                else CaptureAttemptSnapshot(
                    request_id=attempt.request_id,
                    paper_id=attempt.paper_id,
                    normalized_doi=attempt.normalized_doi,
                    doi_url=attempt.doi_url,
                    stage=attempt.stage,
                    started_at=attempt.started_at,
                    claimed_at=attempt.claimed_at,
                    finished_at=attempt.finished_at,
                    terminal_outcome=attempt.terminal_outcome,
                )
            )
            return CaptureSnapshot(
                readiness=self._readiness_locked(now_monotonic),
                connector_version=self._connector_version,
                zotero_reachable=self._zotero_reachable,
                last_heartbeat_at=self._last_heartbeat_at,
                attempt=attempt_snapshot,
                completion_pending=self._completion is not None,
            )

    def _readiness_locked(self, now_monotonic: float) -> ConnectorReadiness:
        heartbeat = self._last_heartbeat_monotonic
        if (
            heartbeat is None
            or now_monotonic - heartbeat > HEARTBEAT_STALE_SECONDS
            or not self._zotero_reachable
        ):
            return ConnectorReadiness.UNAVAILABLE
        return ConnectorReadiness.CONNECTED

    def _expire_locked(self, now_monotonic: float, now: datetime) -> None:
        attempt = self._attempt
        if attempt is None or attempt.stage is CaptureStage.FINISHED:
            return

        if (
            attempt.stage is CaptureStage.WAITING_FOR_CONNECTOR
            and now_monotonic - attempt.started_monotonic >= WAITING_TIMEOUT_SECONDS
        ):
            # 未 claim 表示没有自动保存发生；释放 slot，但不制造 A3 reconciliation handoff。
            attempt.stage = CaptureStage.FINISHED
            attempt.finished_at = now
            attempt.terminal_outcome = None
            return

        if (
            attempt.stage is CaptureStage.CONNECTOR_ACTIVE
            and attempt.claimed_monotonic is not None
            and now_monotonic - attempt.claimed_monotonic >= ACTIVE_TIMEOUT_SECONDS
        ):
            # claim 后失去 terminal observation 属于不确定完成，不能降级成普通 FAILED。
            self._finish_locked(attempt, CaptureOutcome.UNCONFIRMED, now)

    def _finish_locked(
        self,
        attempt: _Attempt,
        outcome: CaptureOutcome,
        now: datetime,
    ) -> None:
        attempt.stage = CaptureStage.FINISHED
        attempt.finished_at = now
        attempt.terminal_outcome = outcome
        if outcome in {CaptureOutcome.CONFIRMED, CaptureOutcome.UNCONFIRMED}:
            self._completion = CaptureCompletion(
                request_id=attempt.request_id,
                paper_id=attempt.paper_id,
                normalized_doi=attempt.normalized_doi,
                capture_guard=attempt.capture_guard,
                outcome=outcome,
                finished_at=now,
            )
