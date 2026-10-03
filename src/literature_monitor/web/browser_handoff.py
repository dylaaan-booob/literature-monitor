"""Process-local, single-claim browser authority (SPEC §36.9).

The launch URL is for a later browser opener, never an HTTP redirect. Events
authenticate an envelope only; they do not implement acquisition semantics.
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
import hashlib
import json
import re
import secrets
from threading import RLock
from uuid import UUID

MAX_MESSAGE_BYTES = 8192
MAX_PAYLOAD_BYTES = 4096
_CAPABILITY = re.compile(r"[A-Za-z0-9_-]{43}")
_TAB = re.compile(r"[A-Za-z0-9_-]{1,128}")
_EVENT_TYPE = re.compile(r"[a-z][a-z0-9_]{0,63}")


@dataclass(frozen=True)
class HandoffLaunch:
    task_id: UUID
    url: str = field(repr=False)


@dataclass(frozen=True)
class HandoffStatus:
    task_id: UUID
    claimed: bool


@dataclass(frozen=True)
class HandoffClaim:
    task_id: UUID
    event_capability: str = field(repr=False)


@dataclass(frozen=True)
class AuthenticatedBrowserEvent:
    task_id: UUID
    tab_binding: str = field(repr=False)
    event_type: str = field(repr=False)
    payload: dict = field(repr=False)
    command: dict | None = field(default=None, repr=False)


@dataclass
class _Authority:
    task_id: UUID
    initial_digest: bytes | None = field(repr=False)
    event_digest: bytes | None = field(default=None, repr=False)
    tab_binding: str | None = field(default=None, repr=False)


def _task_id(value: object) -> UUID | None:
    if not isinstance(value, str) or len(value) != 36:
        return None
    try:
        task_id = UUID(value)
    except ValueError:
        return None
    return task_id if str(task_id) == value else None


def _matches(pattern: re.Pattern, value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _digest(capability: str) -> bytes:
    return hashlib.sha256(capability.encode("ascii")).digest()


def _json_value(value: object, depth=0) -> bool:
    if depth > 8:
        return False
    if type(value) is dict:
        return all(isinstance(key, str) and _json_value(item, depth + 1)
                   for key, item in value.items())
    if type(value) is list:
        return all(_json_value(item, depth + 1) for item in value)
    return value is None or type(value) in (str, int, float, bool)


def _bounded_payload(payload: object) -> dict | None:
    if type(payload) is not dict or not _json_value(payload):
        return None
    try:
        encoded = json.dumps(payload, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_PAYLOAD_BYTES:
            return None
        # Detach the authenticated envelope from the caller's mutable input.
        return json.loads(encoded)
    except (ValueError, TypeError, RecursionError):
        return None


def parse_handoff_message(raw: bytes) -> dict | None:
    """Bounded JSON parsing without framework validation reflecting secrets."""
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Invalid handoff message.")
            result[key] = value
        return result

    if len(raw) > MAX_MESSAGE_BYTES:
        return None
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_fields)
        return value if type(value) is dict else None
    except (ValueError, UnicodeError, RecursionError):
        return None


def validate_command(task_id: UUID, value: object) -> dict | None:
    """Fixed app replies; no authority, credentials, tabs or arbitrary URLs."""
    command = _bounded_payload(value)
    if command is None or command.get('task_id') != str(task_id):
        return None
    kind = command.get('type')
    if not isinstance(kind, str):
        return None
    fields = {'task_id', 'type'}
    if kind == 'START':
        if set(command) != fields | {'plan'} or type(command['plan']) is not dict:
            return None
        plan = command['plan']
        if set(plan) != {'task_id', 'doi', 'direct_url'}:
            return None
        try:
            # Local imports avoid a dependency cycle with the A7 event parser.
            from literature_monitor.application.acquisition import AcquisitionTask
            from literature_monitor.browser_acquisition import navigation_plan
            task = AcquisitionTask(task_id, task_id, plan['doi'], '', '')
            return command if navigation_plan(task).message() == plan else None
        except (ValueError, TypeError, KeyError):
            return None
    if kind == 'CHOOSE':
        return command if set(command) == fields | {'choice_id'} and type(command['choice_id']) is int and 0 <= command['choice_id'] < 6 else None
    if kind in {'PUBLISHER_EXHAUSTED', 'DOWNLOAD_CURRENT'}:
        return command if set(command) == fields else None
    return None


class BrowserHandoffRegistry:
    """One active task, digest-only capabilities, no history or persistence."""

    def __init__(self, *, lock=None):
        self._active: _Authority | None = None
        self._lock = lock if lock is not None else RLock()

    @property
    def lock(self):
        return self._lock

    def issue(self, task_id: UUID, *, port: int) -> HandoffLaunch:
        if not isinstance(task_id, UUID) or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Invalid handoff launch parameters.")
        with self._lock:
            if self._active is not None:
                raise RuntimeError("A browser handoff is already active.")
            capability = secrets.token_urlsafe(32)
            self._active = _Authority(task_id, _digest(capability))
            return HandoffLaunch(task_id, f"http://localhost:{port}/browser-handoff/{task_id}#{capability}")

    def status(self, task_id: object) -> HandoffStatus | None:
        task_id = _task_id(task_id)
        with self._lock:
            active = self._active
            if active is None or active.task_id != task_id:
                return None
            return HandoffStatus(active.task_id, active.event_digest is not None)

    def claim(self, task_id: object, capability: object, tab_binding: object) -> HandoffClaim | None:
        task_id = _task_id(task_id)
        if task_id is None or not _matches(_CAPABILITY, capability) or not _matches(_TAB, tab_binding):
            return None
        with self._lock:
            active = self._active
            if (active is None or active.task_id != task_id or active.initial_digest is None
                    or not secrets.compare_digest(active.initial_digest, _digest(capability))):
                return None
            event_capability = secrets.token_urlsafe(32)
            active.initial_digest = None
            active.tab_binding = tab_binding
            active.event_digest = _digest(event_capability)
            return HandoffClaim(task_id, event_capability)

    def receive_event(
        self, task_id: object, capability: object, tab_binding: object,
        event_type: object, payload: object, *,
        on_event: Callable[[AuthenticatedBrowserEvent], dict | None] | None = None,
    ) -> AuthenticatedBrowserEvent | None:
        task_id = _task_id(task_id)
        if (task_id is None or not _matches(_CAPABILITY, capability)
                or not _matches(_TAB, tab_binding) or not _matches(_EVENT_TYPE, event_type)):
            return None
        payload = _bounded_payload(payload)
        if payload is None:
            return None
        with self._lock:
            active = self._active
            if (active is None or active.task_id != task_id or active.event_digest is None
                    or active.tab_binding != tab_binding
                    or not secrets.compare_digest(active.event_digest, _digest(capability))):
                return None
            event = AuthenticatedBrowserEvent(task_id, tab_binding, event_type, payload)
            # Serialize dispatch with invalidation: late events cannot invoke a
            # handler after its task authority has been destroyed. No queue.
            if on_event is not None:
                try:
                    command = on_event(event)
                    if command is not None:
                        command = validate_command(task_id, command)
                        if command is None:
                            raise ValueError('Invalid browser command.')
                        event = replace(event, command=command)
                except Exception:
                    raise RuntimeError("Browser handoff event could not be handled.") from None
            return event

    def invalidate(self, task_id: UUID) -> bool:
        with self._lock:
            if self._active is None or self._active.task_id != task_id:
                return False
            self._active = None
            return True
