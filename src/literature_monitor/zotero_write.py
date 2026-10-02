"""Concrete Local API authorization and child-file upload boundary (SPEC §35).

A client is bound to one previously verified parent/instance. Operations are
synchronous and intended for serial use. It neither acquires/validates PDFs nor
changes Paper state. After partial/uncertain writes, the caller must inspect
actual Zotero state before starting another attempt; this module never replays
an uncertain create or claims rollback.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
import hashlib
import math
import os
from pathlib import Path
import re
import secrets
import stat
from typing import BinaryIO, Callable

import httpx

from .zotero_credentials import (
    ZoteroAuthorizationRuntime, ZoteroCredentialStore, ZoteroCredentialStoreError,
    _Credential, _without_secret_logs,
)
from .zotero_local import LOCAL_API_BASE, VerifiedZoteroItem

_KEY = re.compile(r"[A-Za-z0-9]{32}")
_ITEM_KEY = re.compile(r"[A-Z0-9]{8}")
_UPLOAD_KEY = re.compile(r"[A-Za-z0-9_-]+")
_CONTENT_TYPE = re.compile(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+")
_FILENAME = "article.pdf"


class ZoteroAuthorizationOutcome(str, Enum):
    AUTHORIZED = "authorized"
    REQUIRED = "required"
    DENIED = "denied"
    RATE_LIMITED = "rate_limited"
    API_FAILURE = "api_failure"
    INVALID_RESPONSE = "invalid_response"
    SERVER_ID_MISMATCH = "server_id_mismatch"
    SECURE_STORE_FAILURE = "secure_store_failure"


@dataclass(frozen=True)
class ZoteroAuthorizationResult:
    outcome: ZoteroAuthorizationOutcome
    server_id: str
    remembered: bool = False
    retry_after_seconds: float | None = None

    @property
    def retryable(self) -> bool:
        return self.outcome is ZoteroAuthorizationOutcome.RATE_LIMITED

    @property
    def message(self) -> str:
        return {
            ZoteroAuthorizationOutcome.AUTHORIZED: "Zotero write authorization granted.",
            ZoteroAuthorizationOutcome.REQUIRED: "Authorize Zotero in Settings → Advanced & Diagnostics → Zotero integration.",
            ZoteroAuthorizationOutcome.DENIED: "Zotero write authorization denied.",
            ZoteroAuthorizationOutcome.RATE_LIMITED: "Zotero authorization rate limited.",
            ZoteroAuthorizationOutcome.API_FAILURE: "Zotero authorization unavailable.",
            ZoteroAuthorizationOutcome.INVALID_RESPONSE: "Invalid Zotero authorization response.",
            ZoteroAuthorizationOutcome.SERVER_ID_MISMATCH: "Zotero instance changed; verify identity again.",
            ZoteroAuthorizationOutcome.SECURE_STORE_FAILURE: "OS credential store unavailable.",
        }[self.outcome]


class ZoteroUploadOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL_FAILURE = "partial_failure"
    AUTH_FAILURE = "auth_failure"
    SERVER_ID_MISMATCH = "server_id_mismatch"
    API_FAILURE = "api_failure"
    INVALID_RESPONSE = "invalid_response"
    GUARD_REJECTED = "guard_rejected"


class ZoteroUploadStage(str, Enum):
    NO_CONFIRMED_MUTATION = "no_confirmed_mutation"
    CHILD_CREATED = "child_created"
    BYTES_UPLOADED = "bytes_uploaded"
    REGISTERED = "registered"


class ZoteroWriteGuardPhase(str, Enum):
    BEFORE_WRITE = "before_write"
    BEFORE_AUTHORIZATION_RETRY = "before_authorization_retry"


@dataclass(frozen=True)
class ZoteroWriteGuardContext:
    phase: ZoteroWriteGuardPhase
    stage: ZoteroUploadStage
    attachment_key: str | None


@dataclass(frozen=True)
class ZoteroUploadResult:
    outcome: ZoteroUploadOutcome
    stage: ZoteroUploadStage
    parent_key: str
    server_id: str
    attachment_key: str | None = None
    failure: ZoteroUploadOutcome | None = None
    authorization: ZoteroAuthorizationResult | None = None
    mutation_uncertain: bool = False

    @property
    def message(self) -> str:
        if self.outcome is ZoteroUploadOutcome.SUCCEEDED:
            return "PDF attachment registered in Zotero."
        if self.mutation_uncertain:
            return "Zotero write result is uncertain; inspect actual attachment state before retrying."
        if self.stage is ZoteroUploadStage.BYTES_UPLOADED:
            return "File bytes uploaded, but attachment registration failed."
        if self.stage is ZoteroUploadStage.CHILD_CREATED:
            return "Child attachment created, but file upload did not complete."
        return "Zotero attachment upload did not start."


class _WriteFailure(Exception):
    def __init__(self, outcome: ZoteroUploadOutcome, *, authorization=None, uncertain=False):
        super().__init__(outcome.value)
        self.outcome = outcome
        self.authorization = authorization
        self.uncertain = uncertain


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        if re.fullmatch(r"[0-9]+", value):
            delay = float(value)
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            delay = max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
        return delay if math.isfinite(delay) and delay >= 0 else None
    except (ValueError, OverflowError, TypeError):
        return None


class ZoteroAuthorizationClient:
    def __init__(
        self, server_id: str, *,
        authorization_runtime: ZoteroAuthorizationRuntime | None = None,
        credential_store: ZoteroCredentialStore | None = None,
        timeout: float = 10.0, transport: httpx.BaseTransport | None = None,
    ):
        self.server_id = server_id
        self._runtime = (authorization_runtime if authorization_runtime is not None
                         else ZoteroAuthorizationRuntime(credential_store=credential_store))
        self._credential: _Credential | None = None
        self._instance_changed = False
        self._reauthorized = False
        self._http = httpx.Client(
            timeout=timeout, transport=transport, trust_env=False, follow_redirects=False,
            headers={"Zotero-API-Version": "3", "Accept": "application/json"},
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self) -> None:
        self._credential = None
        self._http.close()

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        with _without_secret_logs():
            # Neither a prior Set-Cookie nor ambient proxy/auth configuration
            # may accompany an upload-key-authorized request.
            self._http.cookies.clear()
            try:
                return self._http.request(method, url, **kwargs)
            except (httpx.HTTPError, OSError, ValueError):
                raise _WriteFailure(ZoteroUploadOutcome.API_FAILURE, uncertain=method == "POST") from None
            finally:
                self._http.cookies.clear()

    def _check_response_instance(self, response: httpx.Response) -> None:
        server_id = response.headers.get("Zotero-Server-ID")
        if not server_id or not server_id.strip():
            self._credential = None
            raise _WriteFailure(ZoteroUploadOutcome.INVALID_RESPONSE)
        if server_id != self.server_id:
            self._credential = None
            self._instance_changed = True
            self._runtime.observe_instance(server_id)
            raise _WriteFailure(ZoteroUploadOutcome.SERVER_ID_MISMATCH)

    def _check_server(self) -> None:
        if self._instance_changed:
            raise _WriteFailure(ZoteroUploadOutcome.SERVER_ID_MISMATCH)
        response = self._request(
            "GET", LOCAL_API_BASE + "/",
            headers={"Zotero-Server-ID": self.server_id},
        )
        self._check_response_instance(response)
        if response.status_code != 200:
            raise _WriteFailure(ZoteroUploadOutcome.API_FAILURE)
        self._runtime.observe_instance(self.server_id)

    @staticmethod
    def _json(response: httpx.Response):
        try:
            value = response.json()
        except (ValueError, UnicodeError):
            raise _WriteFailure(ZoteroUploadOutcome.INVALID_RESPONSE) from None
        if not isinstance(value, dict):
            raise _WriteFailure(ZoteroUploadOutcome.INVALID_RESPONSE)
        return value

    def authorize(self) -> ZoteroAuthorizationResult:
        """Request one dialog, returning only sanitized public authorization state."""
        self._credential = None
        try:
            self._check_server()
            delay = self._runtime.retry_boundary.remaining()
            if delay > 0:
                return ZoteroAuthorizationResult(ZoteroAuthorizationOutcome.RATE_LIMITED,
                    self.server_id, retry_after_seconds=delay)
            response = self._request(
                "POST", LOCAL_API_BASE + "/local/authorize",
                headers={"Zotero-Server-ID": self.server_id},
                json={"appName": "Literature Monitor"},
            )
            self._check_response_instance(response)
            if response.status_code == 403:
                outcome = ZoteroAuthorizationOutcome.DENIED
            elif response.status_code == 429:
                delay = _retry_after(response.headers.get("Retry-After"))
                if delay is not None:
                    self._runtime.retry_boundary.defer(delay)
                return ZoteroAuthorizationResult(ZoteroAuthorizationOutcome.RATE_LIMITED,
                    self.server_id, retry_after_seconds=delay)
            elif response.status_code != 200:
                outcome = ZoteroAuthorizationOutcome.API_FAILURE
            else:
                payload = self._json(response)
                key, remember = payload.get("key"), payload.get("remember")
                if not isinstance(key, str) or not _KEY.fullmatch(key) or type(remember) is not bool:
                    raise _WriteFailure(ZoteroUploadOutcome.INVALID_RESPONSE)
                self._runtime.establish(self.server_id, key, remembered=remember)
                self._credential = _Credential(key, remember)
                return ZoteroAuthorizationResult(
                    ZoteroAuthorizationOutcome.AUTHORIZED, self.server_id, remembered=remember,
                )
        except ZoteroCredentialStoreError:
            outcome = ZoteroAuthorizationOutcome.SECURE_STORE_FAILURE
        except _WriteFailure as failure:
            outcome = {
                ZoteroUploadOutcome.SERVER_ID_MISMATCH: ZoteroAuthorizationOutcome.SERVER_ID_MISMATCH,
                ZoteroUploadOutcome.INVALID_RESPONSE: ZoteroAuthorizationOutcome.INVALID_RESPONSE,
            }.get(failure.outcome, ZoteroAuthorizationOutcome.API_FAILURE)
        return ZoteroAuthorizationResult(outcome, self.server_id)

    def _require_authorization(self, result: ZoteroAuthorizationResult) -> None:
        if result.outcome is ZoteroAuthorizationOutcome.AUTHORIZED:
            return
        outcome = (
            ZoteroUploadOutcome.SERVER_ID_MISMATCH
            if result.outcome is ZoteroAuthorizationOutcome.SERVER_ID_MISMATCH
            else ZoteroUploadOutcome.AUTH_FAILURE
        )
        raise _WriteFailure(outcome, authorization=result)

    def _credential_for_write(self) -> _Credential:
        self._check_server()  # Check before accessing even this instance's keyring entry.
        try:
            credential = self._runtime.credential(self.server_id)
        except ZoteroCredentialStoreError:
            self._require_authorization(ZoteroAuthorizationResult(
                ZoteroAuthorizationOutcome.SECURE_STORE_FAILURE, self.server_id,
            ))
        if credential is None:
            self._require_authorization(ZoteroAuthorizationResult(
                ZoteroAuthorizationOutcome.REQUIRED, self.server_id,
            ))
        if not isinstance(credential.key, str) or not _KEY.fullmatch(credential.key):
            self._require_authorization(ZoteroAuthorizationResult(
                ZoteroAuthorizationOutcome.SECURE_STORE_FAILURE, self.server_id,
            ))
        self._credential = credential
        return self._credential

    def authorization_status(self) -> ZoteroAuthorizationResult:
        """Read current authorization availability without opening a dialog."""
        try:
            credential = self._credential_for_write()
            return ZoteroAuthorizationResult(ZoteroAuthorizationOutcome.AUTHORIZED,
                self.server_id, remembered=credential.remembered)
        except _WriteFailure as failure:
            return failure.authorization or ZoteroAuthorizationResult(
                ZoteroAuthorizationOutcome.SERVER_ID_MISMATCH if self._instance_changed
                else ZoteroAuthorizationOutcome.API_FAILURE, self.server_id)

    def _invalidate(self, credential: _Credential) -> None:
        self._credential = None
        try:
            self._runtime.invalidate(self.server_id, credential)
        except ZoteroCredentialStoreError:
            self._require_authorization(ZoteroAuthorizationResult(
                ZoteroAuthorizationOutcome.SECURE_STORE_FAILURE, self.server_id,
            ))


class ZoteroWriteClient(ZoteroAuthorizationClient):
    def __init__(self, parent: VerifiedZoteroItem, **kwargs):
        if not isinstance(parent, VerifiedZoteroItem):
            raise TypeError("A verified Zotero parent is required.")
        self.parent = parent
        super().__init__(parent.server_id, **kwargs)

    @staticmethod
    def _guard(guard, stage, attachment_key, phase=ZoteroWriteGuardPhase.BEFORE_WRITE) -> None:
        if guard is None:
            return
        try:
            accepted = guard(ZoteroWriteGuardContext(phase, stage, attachment_key)) is True
        except Exception:
            accepted = False
        if not accepted:
            raise _WriteFailure(ZoteroUploadOutcome.GUARD_REJECTED)

    def _write(self, path: str, *, headers=None, guard=None,
               stage=ZoteroUploadStage.NO_CONFIRMED_MUTATION, attachment_key=None, **kwargs) -> httpx.Response:
        self._guard(guard, stage, attachment_key)
        credential = self._credential_for_write()
        for attempt in range(2):
            # Authorization may require human interaction; check again afterwards.
            self._guard(guard, stage, attachment_key)
            try:
                response = self._request(
                    "POST", LOCAL_API_BASE + path,
                    headers={
                        **(headers or {}), "Zotero-Server-ID": self.server_id,
                        "Zotero-API-Key": credential.key,
                    }, **kwargs,
                )
            finally:
                # A key is consumed at authentication validation, even when a
                # later precondition/HTTP failure occurs. On uncertain transport,
                # discard it too; there is no safe basis for reusing it.
                if not credential.remembered:
                    self._runtime.consume(self.server_id, credential)
                    self._credential = None
            try:
                self._check_response_instance(response)
            except _WriteFailure as failure:
                failure.uncertain = response.status_code in (200, 204) or response.status_code >= 500
                raise
            if response.status_code != 401:
                return response
            self._invalidate(credential)
            if (attempt == 1 or not credential.remembered or self._reauthorized
                    or stage is not ZoteroUploadStage.NO_CONFIRMED_MUTATION):
                raise _WriteFailure(ZoteroUploadOutcome.AUTH_FAILURE)
            self._reauthorized = True
            # A confirmed 401 cannot have created an item. Only this case gets
            # one fresh authorization/retry; kwargs retain the same write token.
            self._guard(guard, stage, attachment_key, ZoteroWriteGuardPhase.BEFORE_AUTHORIZATION_RETRY)
            self._require_authorization(self.authorize())
            credential = self._credential
        raise AssertionError("Unreachable Zotero retry state.")

    def _created_key(self, response: httpx.Response) -> str:
        payload = self._json(response)
        successful = payload.get("successful")
        if (
            not isinstance(successful, dict) or set(successful) != {"0"}
            or payload.get("failed") != {} or payload.get("unchanged") != {}
        ):
            raise _WriteFailure(ZoteroUploadOutcome.INVALID_RESPONSE, uncertain=True)
        item = successful["0"]
        data = item.get("data") if isinstance(item, dict) else None
        key = item.get("key") if isinstance(item, dict) else None
        if (
            not isinstance(key, str) or not _ITEM_KEY.fullmatch(key) or key == self.parent.key
            or not isinstance(data, dict) or data.get("key") != key
            or data.get("itemType") != "attachment" or data.get("parentItem") != self.parent.key
            or data.get("linkMode") != "imported_file" or data.get("contentType") != "application/pdf"
        ):
            raise _WriteFailure(ZoteroUploadOutcome.INVALID_RESPONSE, uncertain=True)
        return key

    @staticmethod
    def _upload_authorization(payload: dict) -> tuple[str, str, str]:
        key, url, content_type = (payload.get(name) for name in ("uploadKey", "url", "contentType"))
        # Exact canonical URL comparison also rejects userinfo, encoded paths,
        # query strings, fragments, alternate ports/hosts and redirect targets.
        if (
            not isinstance(key, str) or not _UPLOAD_KEY.fullmatch(key)
            or url != LOCAL_API_BASE + "/local/uploads/" + key
            or not isinstance(content_type, str) or not _CONTENT_TYPE.fullmatch(content_type)
            or payload.get("prefix") != "" or payload.get("suffix") != ""
            or "exists" in payload
        ):
            raise _WriteFailure(ZoteroUploadOutcome.INVALID_RESPONSE)
        return key, url, content_type

    def upload_pdf(self, local_file: Path | str, *,
                   guard: Callable[[ZoteroWriteGuardContext], bool] | None = None) -> ZoteroUploadResult:
        """Upload caller-supplied bytes; caller owns file validity and lifecycle.

        This operation must follow current identity/attachment inspection. A2
        preserves the supplied file and performs no PDF discovery or parsing.
        An optional guard runs before authorization access, each write/replay,
        and fresh 401 authorization. Its stage/child key identifies this attempt's
        own partial attachment; rejection prevents further writes/dialogs.
        """
        stage = ZoteroUploadStage.NO_CONFIRMED_MUTATION
        self._reauthorized = False
        attachment_key = None
        try:
            with Path(local_file).open("rb") as file:
                file_stat = os.fstat(file.fileno())
                if not stat.S_ISREG(file_stat.st_mode) or not 0 < file_stat.st_size < 4 * 1024**3:
                    raise _WriteFailure(ZoteroUploadOutcome.INVALID_RESPONSE)
                digest = hashlib.file_digest(file, "md5").hexdigest()
                file.seek(0)
                response = self._write(
                    "/users/0/items", headers={"Zotero-Write-Token": secrets.token_hex(16)},
                    guard=guard, stage=stage, attachment_key=attachment_key,
                    json=[{
                        "itemType": "attachment", "linkMode": "imported_file",
                        "parentItem": self.parent.key, "contentType": "application/pdf",
                        "url": "https://doi.org/" + self.parent.normalized_doi,
                        "filename": _FILENAME, "title": "Full text PDF",
                        "tags": [], "collections": [], "relations": {},
                    }],
                )
                if response.status_code != 200:
                    raise _WriteFailure(
                        ZoteroUploadOutcome.API_FAILURE, uncertain=response.status_code >= 500,
                    )
                try:
                    attachment_key = self._created_key(response)
                except _WriteFailure as failure:
                    failure.uncertain = True
                    raise
                stage = ZoteroUploadStage.CHILD_CREATED
                endpoint = "/users/0/items/" + attachment_key + "/file"
                response = self._write(endpoint, headers={"If-None-Match": "*"}, data={
                    "md5": digest, "filename": _FILENAME, "filesize": str(file_stat.st_size),
                    "mtime": str(file_stat.st_mtime_ns // 1_000_000),
                }, guard=guard, stage=stage, attachment_key=attachment_key)
                if response.status_code != 200:
                    raise _WriteFailure(ZoteroUploadOutcome.API_FAILURE)
                payload = self._json(response)
                if payload == {"exists": 1} and type(payload["exists"]) is int:
                    stage = ZoteroUploadStage.REGISTERED
                else:
                    upload_key, url, content_type = self._upload_authorization(payload)
                    self._check_server()
                    self._guard(guard, stage, attachment_key)
                    response = self._request(
                        "POST", url,
                        headers={"Content-Type": content_type, "Zotero-Server-ID": self.server_id,
                                 "Content-Length": str(file_stat.st_size)},
                        content=self._file_chunks(file),
                    )
                    # The received bytes may already be temporary server state,
                    # even if a response is missing or belongs to another server.
                    try:
                        self._check_response_instance(response)
                    except _WriteFailure as failure:
                        failure.uncertain = True
                        raise
                    if response.status_code != 201:
                        raise _WriteFailure(
                            ZoteroUploadOutcome.API_FAILURE, uncertain=response.status_code >= 500,
                        )
                    stage = ZoteroUploadStage.BYTES_UPLOADED
                    response = self._write(endpoint, headers={"If-None-Match": "*"}, data={"upload": upload_key},
                                           guard=guard, stage=stage, attachment_key=attachment_key)
                    if response.status_code != 204:
                        raise _WriteFailure(ZoteroUploadOutcome.API_FAILURE)
                    stage = ZoteroUploadStage.REGISTERED
            return ZoteroUploadResult(
                ZoteroUploadOutcome.SUCCEEDED, stage, self.parent.key, self.server_id, attachment_key,
            )
        except (OSError, ValueError):
            failure = _WriteFailure(ZoteroUploadOutcome.API_FAILURE)
        except _WriteFailure as caught:
            failure = caught
        outcome = (
            ZoteroUploadOutcome.PARTIAL_FAILURE
            if stage is not ZoteroUploadStage.NO_CONFIRMED_MUTATION else failure.outcome
        )
        return ZoteroUploadResult(
            outcome, stage, self.parent.key, self.server_id, attachment_key,
            failure=failure.outcome, authorization=failure.authorization, mutation_uncertain=failure.uncertain,
        )

    @staticmethod
    def _file_chunks(file: BinaryIO):
        while chunk := file.read(1024 * 1024):
            yield chunk
