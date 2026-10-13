"""Read-only Zotero Desktop instance health; no Library or attachment access."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
import logging

import httpx


LOCAL_API_BASE = "http://localhost:23119/api"
_sensitive_local_read = ContextVar("zotero_sensitive_local_read", default=False)


class _SensitiveReadLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _sensitive_local_read.get()


_log_filter = _SensitiveReadLogFilter()
for _logger_name in (
    "httpx",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
):
    logging.getLogger(_logger_name).addFilter(_log_filter)


@contextmanager
def _without_local_read_logs():
    token = _sensitive_local_read.set(True)
    try:
        yield
    finally:
        _sensitive_local_read.reset(token)


class ZoteroReadOutcome(str, Enum):
    VERIFIED = "VERIFIED"
    API_FAILURE = "API_FAILURE"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    SERVER_ID_MISMATCH = "SERVER_ID_MISMATCH"


@dataclass(frozen=True)
class ZoteroInstanceResult:
    outcome: ZoteroReadOutcome
    server_id: str | None
    message: str


class _ReadFailure(Exception):
    def __init__(self, outcome: ZoteroReadOutcome, message: str) -> None:
        self.outcome = outcome
        self.message = message
        super().__init__(message)


def _invalid(message: str) -> _ReadFailure:
    return _ReadFailure(ZoteroReadOutcome.INVALID_RESPONSE, message)


class ZoteroLocalClient:
    """Own a synchronous Local API read session; never authorize or write."""

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = httpx.Client(
            headers={"Zotero-API-Version": "3", "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ZoteroLocalClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _get(self, url: httpx.URL) -> tuple[httpx.Response, str]:
        try:
            response = self._http.get(url)
        except httpx.HTTPError:
            raise _ReadFailure(
                ZoteroReadOutcome.API_FAILURE,
                "Zotero is unavailable. Check Zotero Desktop and its Local API setting.",
            ) from None
        current_id = response.headers.get("Zotero-Server-ID", "").strip()
        if not current_id:
            raise _invalid("Zotero response has no Server-ID; the instance cannot be verified.")
        if response.status_code == 412:
            raise _ReadFailure(
                ZoteroReadOutcome.SERVER_ID_MISMATCH,
                "The Zotero instance changed. Verify the item again before continuing.",
            )
        if response.status_code == 403:
            raise _ReadFailure(
                ZoteroReadOutcome.API_FAILURE,
                "Zotero Local API access is disabled. Enable it in Zotero's Advanced settings.",
            )
        if response.status_code != 200:
            raise _ReadFailure(
                ZoteroReadOutcome.API_FAILURE,
                f"Zotero Local API read failed (HTTP {response.status_code}).",
            )
        return response, current_id


    def current_instance(self) -> ZoteroInstanceResult:
        """Read the current Local API instance without authorization or item access."""
        try:
            with _without_local_read_logs():
                _, server_id = self._get(httpx.URL(LOCAL_API_BASE + "/"))
            return ZoteroInstanceResult(ZoteroReadOutcome.VERIFIED, server_id, "Zotero Local API is reachable.")
        except _ReadFailure as failure:
            return ZoteroInstanceResult(failure.outcome, None, failure.message)
