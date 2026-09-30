"""OS-only storage for Zotero Local API credentials (SPEC §35.5)."""

from contextlib import contextmanager
from contextvars import ContextVar
import logging
import sys

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
