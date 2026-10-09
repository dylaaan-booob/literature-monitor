"""Transient Connector coordination with Workspace-wide save-slot exclusion."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from literature_monitor.application.export_attempts import (
    ExportAttemptError, ExportReservation, validate_export_reservation,
)
from literature_monitor.safe_write import (
    CompareReadError, ContentChangedError, NATIVE_SAVE_RESET_GUARD,
    WorkspaceOperationLock, workspace_operation_lock, workspace_path_lock,
)
from literature_monitor.application.access import AccessObservation


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


class ParentOutcome(str, Enum):
    CONFIRMED = "CONFIRMED"
    NO_EFFECT = "NO_EFFECT"
    UNCERTAIN = "UNCERTAIN"


class PdfOutcome(str, Enum):
    VERIFIED_SUCCESS = "verified success"
    VERIFIED_FAILURE = "verified failure"
    UNVERIFIED = "unverified"


class CaptureFailureStage(str, Enum):
    PRE_DISPATCH = "pre_dispatch"
    TRANSLATOR = "translator"
    NATIVE_SAVE = "native_save"
    MISSING_CONFIRMATION = "missing_confirmation"


@dataclass(frozen=True)
class SaveInvocation:
    request_id: str
    invocation_id: str
    doi_url: str
    tab_id: int
    session_id: str


class CaptureStartOutcome(str, Enum):
    STARTED = "STARTED"
    CONNECTOR_UNAVAILABLE = "CONNECTOR_UNAVAILABLE"
    ALREADY_ACTIVE = "ALREADY_ACTIVE"
    COMPLETION_PENDING = "COMPLETION_PENDING"
    INVALID_DOI = "INVALID_DOI"
    WORKSPACE_BLOCKED = "WORKSPACE_BLOCKED"


@dataclass(frozen=True)
class CaptureCommand:
    request_id: str
    doi_url: str
    invocation_id: str
    access_context: str | None = None


@dataclass(frozen=True)
class CaptureCompletion:
    request_id: str
    paper_id: UUID
    normalized_doi: str
    outcome: CaptureOutcome
    finished_at: datetime
    export_reservation: ExportReservation
    parent_outcome: ParentOutcome = ParentOutcome.UNCERTAIN
    pdf_outcome: PdfOutcome = PdfOutcome.UNVERIFIED
    save_invocation: SaveInvocation | None = None
    access: AccessObservation | None = None
    failure_stage: CaptureFailureStage | None = None


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
    parent_outcome: ParentOutcome | None
    pdf_outcome: PdfOutcome
    access: AccessObservation | None = None


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
    doi_url: str
    stage: CaptureStage
    started_at: datetime
    started_monotonic: float
    invocation_id: str
    export_reservation: ExportReservation
    claimed_at: datetime | None = None
    claimed_monotonic: float | None = None
    finished_at: datetime | None = None
    terminal_outcome: CaptureOutcome | None = None
    save_invocation: SaveInvocation | None = None
    parent_outcome: ParentOutcome | None = None
    access_context: str | None = None
    access: AccessObservation | None = None
    access_tab_id: int | None = None
    failure_stage: CaptureFailureStage | None = None


def _doi_target(normalized_doi: str) -> str:
    return f"https://doi.org/{quote(normalized_doi, safe='/')}"


class CaptureCoordinator:
    """Own transient Connector presence, capture state, and completion handoff."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._changed = threading.Event()
        self._operation_lock: WorkspaceOperationLock | None = None
        self._operation_context = None
        # Each escaped native grant requires its own matching upstream pipeline
        # completion. A later Import or parent acceptance cannot clear an old grant.
        self._native_save_grants: dict[SaveInvocation, Path] = {}
        self._reset_guard_contexts: dict[Path, object] = {}
        self._last_heartbeat_monotonic: float | None = None
        self._last_heartbeat_at: datetime | None = None
        self._connector_version: str | None = None
        self._zotero_reachable = False
        self._attempt: _Attempt | None = None
        self._completion: CaptureCompletion | None = None
        self._delivered_completion: CaptureCompletion | None = None
        self._resolved_completion: CaptureCompletion | None = None

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

    @property
    def operation_lock(self) -> WorkspaceOperationLock | None:
        return self._operation_lock

    @property
    def reset_blocker(self) -> str | None:
        """Describe live ownership, not historical batch or Paper outcomes."""
        with self._lock:
            self._expire_locked(_monotonic(), _utc_now())
            if (self._operation_lock is not None or self._attempt is not None
                    and self._attempt.stage is not CaptureStage.FINISHED):
                return "A Connector capture or its Workspace save slot is active; Reset refused."
            if (self._completion is not None or self._delivered_completion is not None
                    or self._resolved_completion is not None):
                return "A Connector completion is awaiting local resolution; Reset refused."
            if self._native_save_grants:
                count = len(self._native_save_grants)
                return (f"{count} native-save dispatch grant(s) lack matching upstream pipeline-complete evidence; "
                        "Reset refused. A later import or confirmed parent does not settle an older grant.")
            return None

    @property
    def has_unresolved_save_authority(self) -> bool:
        return self.reset_blocker is not None

    def save_authority_ended(self, completion: CaptureCompletion) -> bool:
        """A settled native grant or an attempt without a grant can continue a batch."""
        with self._lock:
            return (completion.save_invocation is None
                    or completion.save_invocation not in self._native_save_grants)

    def report_native_save_settled(self, invocation: SaveInvocation) -> bool:
        """Retire only a grant whose original browser pipeline explicitly finished.

        The Connector authenticates the upstream pipeline callback against its
        task, native session and top-level document before sending this receipt.
        The receipt does not change a Paper, old result or current capture.
        """
        if (not isinstance(invocation, SaveInvocation)
                or type(invocation.tab_id) is not int or invocation.tab_id < 0
                or not all(isinstance(value, str) and 0 < len(value) <= REQUEST_ID_MAX_LENGTH
                           for value in (invocation.request_id, invocation.invocation_id, invocation.session_id))
                or not isinstance(invocation.doi_url, str)):
            return False
        with self._lock:
            workspace = self._native_save_grants.pop(invocation, None)
            if workspace is None:
                return False
            if workspace not in self._native_save_grants.values():
                guard = self._reset_guard_contexts.pop(workspace)
                guard.__exit__(None, None, None)
            return True

    def _validate_slot(self, reservation: ExportReservation) -> None:
        # Initial all-Paper uniqueness is established once by the batch plan.
        # Here only the frozen target requires a fresh safe identity/CAS read.
        validate_export_reservation(reservation, operation_lock=self._operation_lock)

    def start_capture(self, export_reservation: ExportReservation, *,
                      operation_lock: WorkspaceOperationLock | None = None,
                      access_context: str | None = None) -> CaptureStartResult:
        """Bind an invocation-local reservation and hold cross-process save exclusion."""
        if not isinstance(export_reservation, ExportReservation):
            return CaptureStartResult(CaptureStartOutcome.INVALID_DOI)
        with self._lock:
            now_monotonic, now = _monotonic(), _utc_now()
            self._expire_locked(now_monotonic, now)
            if self._attempt is not None and self._attempt.stage is not CaptureStage.FINISHED:
                return CaptureStartResult(CaptureStartOutcome.ALREADY_ACTIVE)
            if self._completion is not None or self._delivered_completion is not None or self._resolved_completion is not None:
                return CaptureStartResult(CaptureStartOutcome.COMPLETION_PENDING)
            context = None
            try:
                if operation_lock is None:
                    context = workspace_operation_lock(Path(export_reservation.attempt.workspace_path))
                    operation_lock = context.__enter__()
                self._operation_lock = operation_lock
                self._validate_slot(export_reservation)
            except (ExportAttemptError, ContentChangedError, CompareReadError, OSError):
                self._operation_lock = None
                if context is not None:
                    context.__exit__(None, None, None)
                return CaptureStartResult(CaptureStartOutcome.INVALID_DOI)
            if self._readiness_locked(now_monotonic) is not ConnectorReadiness.CONNECTED:
                self._operation_lock = None
                if context is not None:
                    context.__exit__(None, None, None)
                return CaptureStartResult(CaptureStartOutcome.CONNECTOR_UNAVAILABLE)
            self._operation_context = context
            request_id = secrets.token_urlsafe(32)
            normalized = export_reservation.attempt.doi
            self._attempt = _Attempt(
                request_id=request_id, paper_id=export_reservation.attempt.paper_id,
                normalized_doi=normalized, doi_url=_doi_target(normalized),
                stage=CaptureStage.WAITING_FOR_CONNECTOR, started_at=now,
                started_monotonic=now_monotonic, invocation_id=secrets.token_urlsafe(32),
                export_reservation=export_reservation,
                access_context=access_context,
            )
            self._changed.clear()
            return CaptureStartResult(CaptureStartOutcome.STARTED, request_id)

    def wait_for_completion(self) -> CaptureCompletion:
        """Wait finitely for this command, independent of UI polls."""
        while True:
            with self._lock:
                self._expire_locked(_monotonic(), _utc_now())
                if self._completion is not None:
                    completion = self._completion
                    self._completion = None
                    self._delivered_completion = completion
                    return completion
                attempt = self._attempt
                if attempt is None or attempt.stage is CaptureStage.FINISHED:
                    raise RuntimeError("Capture completion was consumed outside its batch owner")
                start = attempt.claimed_monotonic if attempt.claimed_monotonic is not None else attempt.started_monotonic
                duration = ACTIVE_TIMEOUT_SECONDS if attempt.claimed_monotonic is not None else WAITING_TIMEOUT_SECONDS
                remaining = max(0, start + duration - _monotonic())
                self._changed.clear()
            self._changed.wait(remaining)

    def finish_resolution(self, completion: CaptureCompletion) -> None:
        """Release the finished invocation; stale commands remain invalidated.

        An uncertain native result stops the current batch. A later explicit
        invocation can retry, while the Connector serializes its active task.
        """
        with self._lock:
            if self._resolved_completion is None or completion is not self._resolved_completion or self._attempt is None:
                return
            self._resolved_completion = None
            if completion.export_reservation is not self._attempt.export_reservation:
                return
            # Reset guard custody is independent of completion display and
            # local Paper writes. Only an attributable pipeline receipt retires it.
            if self._operation_context is not None:
                self._operation_context.__exit__(None, None, None)
                self._operation_context = None
            self._operation_lock = None

    def claim_command(self) -> CaptureCommand | None:
        """Atomically claim the current command, if its Paper still matches."""

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
            try:
                self._validate_slot(attempt.export_reservation)
            except (ExportAttemptError, ContentChangedError, CompareReadError, OSError):
                # No payload escapes. A later explicit batch rebuilds its plan.
                return None
            attempt.stage = CaptureStage.CONNECTOR_ACTIVE
            attempt.claimed_at = now
            attempt.claimed_monotonic = now_monotonic
            self._changed.set()
            return CaptureCommand(attempt.request_id, attempt.doi_url, attempt.invocation_id, attempt.access_context)

    def record_access(self, *, request_id: str, invocation_id: str, doi_url: str,
                      tab_id: int, observation: AccessObservation) -> bool:
        """Read-only workflow evidence, with no completion or dispatch authority."""
        with self._lock:
            self._expire_locked(_monotonic(), _utc_now())
            attempt = self._attempt
            if (attempt is None or attempt.stage is not CaptureStage.CONNECTOR_ACTIVE
                    or attempt.save_invocation is not None or attempt.access is not None
                    or request_id != attempt.request_id or invocation_id != attempt.invocation_id
                    or doi_url != attempt.doi_url or type(tab_id) is not int or tab_id < 0):
                return False
            attempt.access, attempt.access_tab_id = observation, tab_id
            return True

    def authorize_parent_dispatch(self, invocation: SaveInvocation) -> bool:
        """Consume one live dispatch permission immediately before native saveItems.

        A lost permission response is ambiguous; it never permits another dispatch.
        Only the claimed command knows the unpredictable invocation capability.
        """
        with self._lock:
            self._expire_locked(_monotonic(), _utc_now())
            attempt = self._attempt
            if not self._matches_command(attempt, invocation):
                return False
            assert attempt is not None
            if attempt.save_invocation is not None:
                return False
            if attempt.access_tab_id is not None and invocation.tab_id != attempt.access_tab_id:
                return False
            if attempt.access is not None and attempt.access.reason not in {"unknown_service", "no_verified_route", "prepared"}:
                return False
            try:
                self._validate_slot(attempt.export_reservation)
            except (ExportAttemptError, ContentChangedError, CompareReadError, OSError):
                return False
            # Establish cross-process Reset exclusion before native permission
            # escapes. The guard is independent of new explicit Import locks.
            workspace = Path(attempt.export_reservation.attempt.workspace_path)
            if workspace not in self._reset_guard_contexts:
                guard = workspace_path_lock(workspace / NATIVE_SAVE_RESET_GUARD, shared=True)
                try:
                    guard.__enter__()
                except (ContentChangedError, CompareReadError, OSError):
                    return False
                self._reset_guard_contexts[workspace] = guard
            self._native_save_grants[invocation] = workspace
            attempt.save_invocation = invocation
            return True

    @staticmethod
    def _matches_command(attempt: _Attempt | None, invocation: SaveInvocation) -> bool:
        return (
            isinstance(invocation, SaveInvocation)
            and attempt is not None
            and attempt.stage is CaptureStage.CONNECTOR_ACTIVE
            and invocation.request_id == attempt.request_id
            and invocation.invocation_id == attempt.invocation_id
            and invocation.doi_url == attempt.doi_url
            and type(invocation.tab_id) is int and invocation.tab_id >= 0
            and isinstance(invocation.session_id, str)
            and 0 < len(invocation.session_id) <= REQUEST_ID_MAX_LENGTH
        )

    def submit_connector_result(
        self, *, request_id: str, invocation_id: str, doi_url: str,
        tab_id: int | None, session_id: str | None, outcome: CaptureOutcome,
        pdf_outcome: PdfOutcome, failure_stage: CaptureFailureStage | None = None,
    ) -> bool:
        """Accept attributable parent evidence; the pinned producer cannot verify PDF.

        CONFIRMED is emitted only after the native saveItems acceptance hook.
        FAILED is a no-dispatch result, never a generic Connector error/timeout.
        A3 derives clearance only from the owned terminal no-dispatch receipt.
        """
        with self._lock:
            now = _utc_now()
            self._expire_locked(_monotonic(), now)
            attempt = self._attempt
            if (
                attempt is None or attempt.stage is not CaptureStage.CONNECTOR_ACTIVE
                or request_id != attempt.request_id or invocation_id != attempt.invocation_id
                or doi_url != attempt.doi_url or not isinstance(outcome, CaptureOutcome)
                or pdf_outcome is not PdfOutcome.UNVERIFIED
                or (failure_stage is not None and not isinstance(failure_stage, CaptureFailureStage))
            ):
                return False
            dispatched = attempt.save_invocation
            if dispatched is not None and (tab_id, session_id) != (
                dispatched.tab_id, dispatched.session_id,
            ):
                return False
            if outcome is CaptureOutcome.CONFIRMED and dispatched is None:
                return False
            if outcome is CaptureOutcome.FAILED and dispatched is not None:
                return False
            if (outcome is CaptureOutcome.CONFIRMED and failure_stage is not None
                    or outcome is CaptureOutcome.FAILED and failure_stage not in {
                        None, CaptureFailureStage.PRE_DISPATCH, CaptureFailureStage.TRANSLATOR,
                    }
                    or outcome is CaptureOutcome.UNCONFIRMED and failure_stage not in {
                        None, CaptureFailureStage.MISSING_CONFIRMATION, CaptureFailureStage.NATIVE_SAVE,
                    }
                    or failure_stage is CaptureFailureStage.NATIVE_SAVE and dispatched is None):
                return False
            attempt.failure_stage = failure_stage
            self._finish_locked(attempt, outcome, now)
            return True

    def consume_completion(self) -> CaptureCompletion | None:
        """Deliver one owned result for guarded durable resolution."""

        now_monotonic = _monotonic()
        now = _utc_now()
        with self._lock:
            self._expire_locked(now_monotonic, now)
            completion = self._completion
            self._completion = None
            if completion is not None:
                self._delivered_completion = completion
            return completion

    def owns_completion(self, completion: CaptureCompletion) -> bool:
        with self._lock:
            return completion is not None and completion is self._delivered_completion

    def claim_completion(self, completion: CaptureCompletion) -> bool:
        """Consume this exact issued result once, excluding substituted/late receipts.

        The producer already matched the unpredictable command, native invocation,
        task tab and session. Reconstructed or dataclass-replaced results have no
        authority. Consume before persistence; failure must never replay a save.
        """
        with self._lock:
            attempt = self._attempt
            if (completion is None or completion is not self._delivered_completion or attempt is None
                    or attempt.stage is not CaptureStage.FINISHED
                    or completion.request_id != attempt.request_id
                    or completion.paper_id != attempt.paper_id
                    or completion.normalized_doi != attempt.normalized_doi
                    or completion.export_reservation is not attempt.export_reservation
                    or completion.save_invocation != attempt.save_invocation
                    or completion.parent_outcome is not attempt.parent_outcome):
                return False
            self._delivered_completion = None
            self._resolved_completion = completion
            return True

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
                    parent_outcome=attempt.parent_outcome,
                    pdf_outcome=PdfOutcome.UNVERIFIED,
                    access=attempt.access,
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
            # Timeout alone is not no-effect proof, even before claim.
            self._finish_locked(attempt, CaptureOutcome.UNCONFIRMED, now)
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
        attempt.parent_outcome = {
            CaptureOutcome.CONFIRMED: ParentOutcome.CONFIRMED,
            CaptureOutcome.FAILED: ParentOutcome.NO_EFFECT,
            CaptureOutcome.UNCONFIRMED: ParentOutcome.UNCERTAIN,
        }[outcome]
        self._changed.set()
        self._completion = CaptureCompletion(
            request_id=attempt.request_id,
            paper_id=attempt.paper_id,
            normalized_doi=attempt.normalized_doi,
            outcome=outcome,
            finished_at=now,
            export_reservation=attempt.export_reservation,
            parent_outcome=attempt.parent_outcome,
            pdf_outcome=PdfOutcome.UNVERIFIED,
            save_invocation=attempt.save_invocation,
            access=attempt.access,
            failure_stage=attempt.failure_stage or (
                CaptureFailureStage.MISSING_CONFIRMATION if outcome is CaptureOutcome.UNCONFIRMED
                else CaptureFailureStage.PRE_DISPATCH if outcome is CaptureOutcome.FAILED else None
            ),
        )
