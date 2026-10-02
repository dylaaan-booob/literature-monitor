"""One current event-driven acquisition, with bounded workers (SPEC §36.12)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
import re
import threading
from uuid import UUID, uuid4

from literature_monitor.application.acquisition import (
    AcquisitionClass, AcquisitionOutcome, AcquisitionRecovery, AcquisitionResult,
    AcquisitionService, AcquisitionStage, PreparedAcquisition,
)
from literature_monitor.browser_acquisition import download_evidence, navigation_plan
from literature_monitor.pdf_staging import stage_download
from literature_monitor.version_qualification import qualify_pdf
from literature_monitor.zotero_write import ZoteroAuthorizationOutcome
from .browser_handoff import BrowserHandoffRegistry, HandoffLaunch
from .chrome_launcher import launch_normal_chrome


class AcquisitionCoordinatorStatus(str, Enum):
    IDLE = 'IDLE'
    RUNNING = 'RUNNING'
    FINISHED = 'FINISHED'


class AcquisitionStartOutcome(str, Enum):
    STARTED = 'STARTED'
    ALREADY_RUNNING = 'ALREADY_RUNNING'
    START_FAILED = 'START_FAILED'


class AcquisitionActionOutcome(str, Enum):
    ACCEPTED = 'ACCEPTED'
    TOO_LATE = 'TOO_LATE'
    UNAVAILABLE = 'UNAVAILABLE'


@dataclass(frozen=True)
class AcquisitionStartResult:
    outcome: AcquisitionStartOutcome


@dataclass(frozen=True)
class UnexpectedAcquisitionError:
    category: str
    message: str


@dataclass(frozen=True)
class ResolverChoice:
    id: int
    category: str
    label: str = field(repr=False)


@dataclass(frozen=True)
class AcquisitionSnapshot:
    status: AcquisitionCoordinatorStatus
    paper_id: UUID | None
    stage: AcquisitionStage | None
    result: AcquisitionResult | None
    unexpected_error: UnexpectedAcquisitionError | None
    attempt_id: UUID | None = None
    task_id: UUID | None = None
    cancel_available: bool = False
    resume_available: bool = False
    open_retry_available: bool = False
    choices: tuple[ResolverChoice, ...] = field(default=(), repr=False)
    browser_identity: tuple[str, str] | None = None


@dataclass(repr=False)
class _Attempt:
    paper_id: UUID
    service: AcquisitionService
    port: int
    staging_options: dict
    attempt_id: UUID = field(default_factory=uuid4)
    task: object = None
    plan: object = None
    linked: bool = False
    launch: HandoffLaunch | None = None
    tab_binding: str | None = None
    qualified: object = None
    stage: AcquisitionStage = AcquisitionStage.LOCATING_ZOTERO
    worker_active: bool = False
    gate_entered: bool = False
    open_failed: bool = False
    route: str = 'direct'
    choices: tuple[ResolverChoice, ...] = ()
    browser_identity: tuple[str, str] | None = None


_BROWSER_STAGES = {AcquisitionStage.OPENING_CHROME, AcquisitionStage.HANDOFF,
    AcquisitionStage.BROWSER_ACTION, AcquisitionStage.RESOLVING,
    AcquisitionStage.WAITING_FOR_INSTITUTION_AUTH, AcquisitionStage.RESOLVER_CHOICE}


def _category(error):
    return type(error).__name__ if type(error) in (RuntimeError, ValueError, OSError, KeyboardInterrupt, SystemExit) else 'InternalError'


class AcquisitionCoordinator:
    def __init__(self, service=None, *, registry=None, launcher=launch_normal_chrome):
        self.registry = registry if registry is not None else BrowserHandoffRegistry()
        # Registry dispatch, terminal invalidation, Cancel and gate all use one RLock.
        self._lock = self.registry.lock
        self._service = service
        self._launcher = launcher
        self._active: _Attempt | None = None
        self._snapshot = AcquisitionSnapshot(AcquisitionCoordinatorStatus.IDLE, None, None, None, None)

    def snapshot(self):
        with self._lock:
            return self._snapshot

    def _publish(self, attempt):
        self._snapshot = AcquisitionSnapshot(AcquisitionCoordinatorStatus.RUNNING,
            attempt.paper_id, attempt.stage, None, None, attempt.attempt_id,
            attempt.task.task_id if attempt.task is not None else None,
            not attempt.gate_entered,
            attempt.stage is AcquisitionStage.WAITING_FOR_ZOTERO_AUTH and not attempt.worker_active,
            attempt.open_failed and not attempt.worker_active,
            attempt.choices, attempt.browser_identity)

    def start(self, paper_id: UUID, *, service_factory: Callable | None = None,
              port=8765, staging_options=None):
        if not isinstance(paper_id, UUID):
            raise TypeError('A Paper UUID is required.')
        with self._lock:
            if self._active is not None:
                return AcquisitionStartResult(AcquisitionStartOutcome.ALREADY_RUNNING)
            try:
                service = service_factory() if service_factory is not None else self._service
                if service is None or type(port) is not int or not 1 <= port <= 65535:
                    raise ValueError('An acquisition service and local port are required.')
                attempt = _Attempt(paper_id, service, port, dict(staging_options or {}))
                self._active = attempt
                self._publish(attempt)
                self._spawn(attempt, self._prepare, 'preflight')
            except BaseException as error:
                if self._active is not None:
                    self._finish(self._active, error=error)
                else:
                    self._snapshot = AcquisitionSnapshot(AcquisitionCoordinatorStatus.FINISHED,
                        paper_id, AcquisitionStage.FAILED, None,
                        UnexpectedAcquisitionError(_category(error), 'The acquisition worker could not be started.'))
                return AcquisitionStartResult(AcquisitionStartOutcome.START_FAILED)
            return AcquisitionStartResult(AcquisitionStartOutcome.STARTED)

    def _spawn(self, attempt, operation, name):
        if attempt.worker_active:
            raise RuntimeError('A bounded acquisition operation is already active.')
        attempt.worker_active = True
        self._publish(attempt)
        threading.Thread(target=self._worker, args=(attempt, operation), daemon=True,
                         name='literature-monitor-acquisition-' + name).start()

    def _worker(self, attempt, operation):
        try:
            operation(attempt)
        except BaseException as error:
            with self._lock:
                self._finish(attempt, error=error)
        finally:
            with self._lock:
                attempt.worker_active = False
                if self._active is attempt:
                    self._publish(attempt)

    def _stage(self, attempt, stage):
        with self._lock:
            if self._active is attempt:
                attempt.stage = stage
                self._publish(attempt)

    def _prepare(self, attempt):
        prepared = attempt.service.prepare(attempt.paper_id, stage_callback=lambda value: self._stage(attempt, value))
        with self._lock:
            if self._active is not attempt:
                return
            if isinstance(prepared, AcquisitionResult):
                self._finish(attempt, prepared)
                return
            if not isinstance(prepared, PreparedAcquisition) or prepared.task.paper_id != attempt.paper_id:
                raise ValueError('Invalid acquisition preflight result.')
            attempt.task, attempt.linked = prepared.task, prepared.linkage_completed
            attempt.plan = navigation_plan(attempt.task)
            attempt.launch = self.registry.issue(attempt.task.task_id, port=attempt.port)
            attempt.stage = AcquisitionStage.OPENING_CHROME
            self._publish(attempt)
        self._open(attempt)

    def _open(self, attempt):
        try:
            self._launcher(attempt.launch)
            failed = False
        except Exception:
            failed = True
        with self._lock:
            if self._active is attempt and attempt.tab_binding is None:
                attempt.open_failed = failed
                attempt.stage = AcquisitionStage.HANDOFF
                self._publish(attempt)

    def _matching(self, paper_id, attempt_id):
        active = self._active
        return active if active is not None and active.paper_id == paper_id and active.attempt_id == attempt_id else None

    def cancel(self, paper_id, attempt_id):
        with self._lock:
            attempt = self._matching(paper_id, attempt_id)
            if attempt is None:
                return AcquisitionActionOutcome.UNAVAILABLE
            if attempt.gate_entered:
                return AcquisitionActionOutcome.TOO_LATE
            self._finish(attempt, AcquisitionResult(paper_id, AcquisitionOutcome.CANCELLED,
                         linkage_completed=attempt.linked))
            return AcquisitionActionOutcome.ACCEPTED

    def reopen(self, paper_id, attempt_id):
        with self._lock:
            attempt = self._matching(paper_id, attempt_id)
            if attempt is None or not attempt.open_failed or attempt.worker_active or attempt.launch is None:
                return AcquisitionActionOutcome.UNAVAILABLE
            status = self.registry.status(str(attempt.task.task_id))
            if status is None or status.claimed:
                return AcquisitionActionOutcome.UNAVAILABLE
            attempt.open_failed = False
            attempt.stage = AcquisitionStage.OPENING_CHROME
            try:
                self._spawn(attempt, self._open, 'open')
            except BaseException as error:
                self._finish(attempt, error=error)
                return AcquisitionActionOutcome.UNAVAILABLE
            return AcquisitionActionOutcome.ACCEPTED

    def resume(self, paper_id, attempt_id):
        with self._lock:
            attempt = self._matching(paper_id, attempt_id)
            if attempt is None or attempt.stage is not AcquisitionStage.WAITING_FOR_ZOTERO_AUTH or attempt.worker_active:
                return AcquisitionActionOutcome.UNAVAILABLE
            try:
                self._spawn(attempt, self._ready_to_commit, 'commit')
            except BaseException as error:
                self._finish(attempt, error=error)
                return AcquisitionActionOutcome.UNAVAILABLE
            return AcquisitionActionOutcome.ACCEPTED

    def _finish(self, attempt, result=None, *, error=None):
        if self._active is not attempt:
            return
        if result is not None and (not isinstance(result, AcquisitionResult) or result.paper_id != attempt.paper_id):
            error, result = ValueError('Invalid acquisition result.'), None
        if error is not None:
            result = AcquisitionResult(attempt.paper_id, AcquisitionOutcome.INTERNAL_FAILURE,
                AcquisitionRecovery.CHECK_ZOTERO, linkage_completed=attempt.linked,
                mutation_uncertain=attempt.gate_entered)
        stage = (AcquisitionStage.CANCELLED if result.outcome is AcquisitionOutcome.CANCELLED
                 else AcquisitionStage.SUCCEEDED if result.outcome in (AcquisitionOutcome.SUCCEEDED, AcquisitionOutcome.PDF_ALREADY_ATTACHED)
                 else AcquisitionStage.FAILED)
        self._snapshot = AcquisitionSnapshot(AcquisitionCoordinatorStatus.FINISHED, attempt.paper_id,
            stage, result, UnexpectedAcquisitionError(_category(error), 'Acquisition stopped because of an unexpected internal error.') if error else None,
            attempt.attempt_id, attempt.task.task_id if attempt.task is not None else None)
        if attempt.task is not None:
            self.registry.invalidate(attempt.task.task_id)
        self._active = None
        if attempt.qualified is not None:
            try:
                attempt.qualified.artifact.cleanup()
            except Exception:
                pass  # Best effort, without logging artifact/source paths.
            attempt.qualified = None
        attempt.launch = None

    def _failure(self, attempt, outcome=AcquisitionOutcome.NO_VALID_PDF, recovery=AcquisitionRecovery.RETRY):
        self._finish(attempt, AcquisitionResult(attempt.paper_id, outcome, recovery, linkage_completed=attempt.linked))

    def handle_event(self, event):
        with self._lock:
            attempt = self._active
            if attempt is None or attempt.task is None or event.task_id != attempt.task.task_id:
                return None
            if attempt.tab_binding is not None and event.tab_binding != attempt.tab_binding:
                return None
            if attempt.stage not in _BROWSER_STAGES:
                return None
            payload = event.payload
            def command(kind, **fields):
                return {'task_id': str(attempt.task.task_id), 'type': kind, **fields}
            if event.event_type == 'tab_ready':
                if payload or not re.fullmatch(r'tab-\d{1,16}', event.tab_binding):
                    return None
                if attempt.tab_binding is None:
                    attempt.tab_binding = event.tab_binding
                    attempt.open_failed = False
                    attempt.stage = AcquisitionStage.BROWSER_ACTION
                    self._publish(attempt)
                # Lost reply recovery sends the same frozen plan; companion START
                # is idempotent for an already-running identical task.
                return command('START', plan=attempt.plan.message())
            if attempt.tab_binding is None:
                return None
            kind = event.event_type
            if kind == 'navigation_state':
                identity = payload.get('identity')
                if (set(payload) == {'identity', 'route'} and payload['route'] == attempt.route and type(identity) is dict
                        and set(identity) == {'scheme', 'host'} and isinstance(identity['scheme'], str) and identity['scheme'] in {'http:', 'https:'}
                        and isinstance(identity['host'], str) and re.fullmatch(r'[A-Za-z0-9.-]{1,253}', identity['host'])):
                    attempt.browser_identity = identity['scheme'], identity['host']
                    attempt.stage = AcquisitionStage.BROWSER_ACTION
            elif kind == 'human_action_needed' and payload == {'route': attempt.route}:
                attempt.stage = AcquisitionStage.WAITING_FOR_INSTITUTION_AUTH
            elif kind == 'publisher_state' and set(payload) == {'state'} and isinstance(payload['state'], str) and payload['state'] in {'accessible', 'human_required', 'exhausted'}:
                attempt.stage = AcquisitionStage.WAITING_FOR_INSTITUTION_AUTH if payload['state'] == 'human_required' else AcquisitionStage.BROWSER_ACTION
            elif kind == 'publisher_fallback_request' and payload == {}:
                if attempt.task.acquisition_class is not AcquisitionClass.PUBLISHED or attempt.route != 'direct':
                    return None
                attempt.route = 'xmu'
                attempt.stage = AcquisitionStage.RESOLVING
                self._publish(attempt)
                return command('PUBLISHER_EXHAUSTED')
            elif kind == 'resolver_choices' and attempt.route == 'xmu':
                choices = payload.get('choices')
                if set(payload) != {'choices'} or type(choices) is not list or not 2 <= len(choices) <= 6:
                    return None
                valid = all(type(c) is dict and set(c) == {'id', 'category', 'label'}
                    and type(c['id']) is int and c['id'] == index and isinstance(c['category'], str) and c['category'] in {'FullText', 'SmartLinks'}
                    and isinstance(c['label'], str) and 0 < len(c['label']) <= 80 and '://' not in c['label']
                    and not any(ord(char) < 32 for char in c['label']) for index, c in enumerate(choices))
                if not valid:
                    return None
                attempt.choices = tuple(ResolverChoice(**c) for c in choices)
                attempt.stage = AcquisitionStage.RESOLVER_CHOICE
            elif kind == 'resolver_choice_request':
                if (set(payload) != {'choice_id'} or type(payload['choice_id']) is not int
                        or attempt.stage is not AcquisitionStage.RESOLVER_CHOICE
                        or payload['choice_id'] not in {c.id for c in attempt.choices}):
                    return None
                attempt.choices = ()
                attempt.stage = AcquisitionStage.RESOLVING
                self._publish(attempt)
                return command('CHOOSE', choice_id=payload['choice_id'])
            elif kind == 'user_download_request' and payload == {}:
                return command('DOWNLOAD_CURRENT')
            elif kind == 'browser_path_failure':
                if (set(payload) == {'reason'} and isinstance(payload['reason'], str) and payload['reason'] in {
                    'navigation_failed', 'exact_manifestation_unavailable', 'resolver_not_ready',
                    'resolver_choice_overflow', 'no_visible_eligible_choices', 'ambiguous_download_ownership', 'download_unavailable'}):
                    outcome = (AcquisitionOutcome.NO_ELIGIBLE_CANDIDATES if payload['reason'] == 'no_visible_eligible_choices'
                               else AcquisitionOutcome.RESOLVER_FAILURE if attempt.route == 'xmu' else AcquisitionOutcome.NO_VALID_PDF)
                    self._failure(attempt, outcome, AcquisitionRecovery.CHECK_BROWSER)
                return None
            elif kind == 'download_candidate':
                try:
                    evidence = download_evidence(event, attempt.task, attempt.tab_binding)
                except ValueError:
                    self._failure(attempt)
                    return None
                if attempt.worker_active or evidence.route != attempt.route:
                    self._failure(attempt)
                    return None
                attempt.stage = AcquisitionStage.VALIDATING_PDF
                try:
                    self._spawn(attempt, lambda active: self._stage_download(active, evidence), 'staging')
                except BaseException as error:
                    self._finish(attempt, error=error)
                return None
            self._publish(attempt)
            return None

    def _stage_download(self, attempt, evidence):
        artifact = None
        try:
            artifact = stage_download(evidence, **attempt.staging_options)
            qualified = qualify_pdf(attempt.task, evidence, artifact, claimed_tab_binding=attempt.tab_binding)
            with self._lock:
                if self._active is not attempt:
                    return
                attempt.qualified = qualified
                artifact = None  # The current task now owns cleanup.
            self._ready_to_commit(attempt)
        except ValueError:
            with self._lock:
                self._failure(attempt)
        finally:
            if artifact is not None:
                artifact.cleanup()

    def _enter_gate(self, attempt):
        with self._lock:
            if self._active is not attempt or attempt.gate_entered or attempt.qualified is None:
                return False
            attempt.gate_entered = True
            attempt.stage = AcquisitionStage.ATTACHING
            self._publish(attempt)
            return True

    def _mutation_allowed(self, attempt):
        with self._lock:
            return self._active is attempt and attempt.gate_entered

    def _ready_to_commit(self, attempt):
        qualified = attempt.qualified
        if qualified is None or not qualified.artifact.validate():
            with self._lock:
                self._failure(attempt)
            return
        authorization = attempt.service.authorization_status(attempt.task)
        with self._lock:
            if self._active is not attempt:
                return
            if authorization.outcome is ZoteroAuthorizationOutcome.SERVER_ID_MISMATCH:
                self._failure(attempt, AcquisitionOutcome.CONFLICT, AcquisitionRecovery.CHECK_ZOTERO)
                return
            if authorization.outcome is not ZoteroAuthorizationOutcome.AUTHORIZED:
                attempt.stage = AcquisitionStage.WAITING_FOR_ZOTERO_AUTH
                self._publish(attempt)
                return
        if not self._enter_gate(attempt):
            return
        result = attempt.service.commit(attempt.task, qualified, linkage_completed=attempt.linked,
                                       mutation_allowed=lambda: self._mutation_allowed(attempt))
        with self._lock:
            self._finish(attempt, result)
