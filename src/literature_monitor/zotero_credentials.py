"""OS-only storage for Zotero Local API credentials (SPEC §36.7)."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import logging
import sys
from threading import RLock
import time

SERVICE_NAME = "literature-monitor.zotero-local"
_sensitive_operation = ContextVar("zotero_sensitive_operation", default=False)


class _SensitiveLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _sensitive_operation.get()


# HTTP debug output can contain headers and the secret-bearing upload URL.
# Suppress these libraries only while this thread/context handles our secrets.
_log_filter = _SensitiveLogFilter()
for _logger_name in (
    "httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2",
    "httpcore.proxy", "httpcore.socks", "keyring", "keyring.backends.macOS",
    "keyring.backends.Windows", "keyring.backends.SecretService",
):
    logging.getLogger(_logger_name).addFilter(_log_filter)


@contextmanager
def _without_secret_logs():
    token = _sensitive_operation.set(True)
    try:
        yield
    finally:
        _sensitive_operation.reset(token)


class ZoteroCredentialStoreError(Exception):
    """A sanitized OS credential-store failure."""


def _os_backend():
    # Explicit OS backends prevent keyring configuration/plugins from selecting
    # a plaintext or chained fallback. No backend is accessed at import time.
    if sys.platform == "darwin":
        from keyring.backends.macOS import Keyring
        return Keyring()
    if sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring
        return WinVaultKeyring()
    if sys.platform.startswith("linux"):
        from keyring.backends.SecretService import Keyring
        return Keyring()
    raise ZoteroCredentialStoreError("OS credential store is unavailable.")


class ZoteroCredentialStore:
    """Partition remembered keys by the verified instance's exact Server-ID."""

    def load(self, server_id: str) -> str | None:
        with _without_secret_logs():
            try:
                return _os_backend().get_password(SERVICE_NAME, server_id)
            except Exception:
                raise ZoteroCredentialStoreError("Cannot read OS credential store.") from None

    def save(self, server_id: str, key: str) -> None:
        with _without_secret_logs():
            try:
                _os_backend().set_password(SERVICE_NAME, server_id, key)
            except Exception:
                raise ZoteroCredentialStoreError("Cannot save OS credential.") from None

    def delete(self, server_id: str) -> None:
        with _without_secret_logs():
            try:
                backend = _os_backend()
                if backend.get_password(SERVICE_NAME, server_id) is not None:
                    backend.delete_password(SERVICE_NAME, server_id)
            except Exception:
                raise ZoteroCredentialStoreError("Cannot remove OS credential.") from None


class AuthorizationRetryBoundary:
    """One runtime's transient authorization limit, independent of workspace."""

    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._not_before = 0.0

    def remaining(self) -> float:
        return max(0.0, self._not_before - self._clock())

    def defer(self, delay: float) -> None:
        self._not_before = max(self._not_before, self._clock() + delay)


@dataclass(frozen=True)
class _Credential:
    key: str = field(repr=False)
    remembered: bool


class ZoteroAuthorizationRuntime:
    """OS remembered keys plus one instance-bound process-only Allow (§36.7).

    Callers must verify the current Server-ID before accessing this holder.
    """

    def __init__(self, *, credential_store=None, retry_boundary=None):
        self.store = credential_store if credential_store is not None else ZoteroCredentialStore()
        self.retry_boundary = retry_boundary if retry_boundary is not None else AuthorizationRetryBoundary()
        self._server_id: str | None = None
        self._one_time: str | None = None
        self._blocked_remembered: set[str] = set()
        self._lock = RLock()

    def observe_instance(self, server_id: str) -> None:
        with self._lock:
            if self._server_id != server_id:
                self._one_time = None
                self._server_id = server_id

    def credential(self, server_id: str) -> _Credential | None:
        with self._lock:
            if self._server_id != server_id:
                raise ValueError("Current instance must be verified before credential access.")
            if self._one_time is not None:
                return _Credential(self._one_time, False)
            if server_id in self._blocked_remembered:
                raise ZoteroCredentialStoreError("Remembered authorization must be established again.")
            try:
                key = self.store.load(server_id)
            except Exception:
                raise ZoteroCredentialStoreError("Cannot read OS credential store.") from None
            return _Credential(key, True) if key is not None else None

    def establish(self, server_id: str, key: str, *, remembered: bool) -> None:
        with self._lock:
            self.observe_instance(server_id)
            self._one_time = None
            if remembered:
                try:
                    self.store.save(server_id, key)
                except Exception:
                    self._blocked_remembered.add(server_id)
                    raise ZoteroCredentialStoreError("Cannot save OS credential.") from None
                self._blocked_remembered.discard(server_id)
            else:
                self._one_time = key

    def consume(self, server_id: str, credential: _Credential) -> None:
        with self._lock:
            if (not credential.remembered and self._server_id == server_id
                    and self._one_time == credential.key):
                self._one_time = None

    def invalidate(self, server_id: str, credential: _Credential) -> None:
        with self._lock:
            self.consume(server_id, credential)
            if credential.remembered:
                self._blocked_remembered.add(server_id)
                try:
                    self.store.delete(server_id)
                except Exception:
                    raise ZoteroCredentialStoreError("Cannot remove OS credential.") from None
                self._blocked_remembered.discard(server_id)
