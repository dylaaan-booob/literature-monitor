"""Read-only Zotero My Library identity and PDF file boundary (§35)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
import stat
from urllib.parse import unquote, urlsplit

import httpx

from literature_monitor.identifiers import normalize_doi
from literature_monitor.zotero_credentials import _without_secret_logs


LOCAL_API_BASE = "http://localhost:23119/api"
_ITEM_KEY = re.compile(r"[A-Z0-9]{8}")
# Bibliographic types from https://api.zotero.org/itemTypes; used only by strict
# acquisition reads, so unknown item types cannot authorize parent mutation.
_BIBLIOGRAPHIC_TYPES = frozenset({
    "artwork", "audioRecording", "bill", "blogPost", "book", "bookSection", "case",
    "conferencePaper", "dataset", "dictionaryEntry", "document", "email",
    "encyclopediaArticle", "film", "forumPost", "hearing", "instantMessage",
    "interview", "journalArticle", "letter", "magazineArticle", "manuscript", "map",
    "newspaperArticle", "patent", "podcast", "preprint", "presentation",
    "radioBroadcast", "report", "computerProgram", "standard", "statute",
    "tvBroadcast", "thesis", "videoRecording", "webpage",
})


class ZoteroReadOutcome(str, Enum):
    VERIFIED = "VERIFIED"
    NOT_FOUND = "NOT_FOUND"
    DUPLICATE = "DUPLICATE"
    CHECKED = "CHECKED"
    API_FAILURE = "API_FAILURE"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    SERVER_ID_MISMATCH = "SERVER_ID_MISMATCH"
    INVALID_DOI = "INVALID_DOI"


@dataclass(frozen=True)
class VerifiedZoteroItem:
    key: str
    normalized_doi: str
    server_id: str

    def __post_init__(self) -> None:
        if (
            not _ITEM_KEY.fullmatch(self.key)
            or not self.server_id.strip()
            or normalize_doi(self.normalized_doi) != self.normalized_doi
            or not self.normalized_doi
        ):
            raise ValueError("Invalid verified Zotero identity")


@dataclass(frozen=True)
class ZoteroIdentityResult:
    outcome: ZoteroReadOutcome
    item: VerifiedZoteroItem | None
    message: str


@dataclass(frozen=True)
class ZoteroInstanceResult:
    outcome: ZoteroReadOutcome
    server_id: str | None
    message: str


@dataclass(frozen=True)
class ZoteroAttachmentResult:
    outcome: ZoteroReadOutcome
    has_pdf: bool | None
    message: str
    # Complete PDF child metadata, not evidence that file upload is registered.
    pdf_keys: tuple[str, ...] = ()
    pdf_file_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Item:
    key: str
    item_type: str
    doi: str | None
    parent_key: str | None
    content_type: str | None

    def matches_doi(self, doi: str) -> bool:
        return self.item_type not in {"attachment", "note", "annotation"} and self.doi == doi


class _ReadFailure(Exception):
    def __init__(self, outcome: ZoteroReadOutcome, message: str) -> None:
        self.outcome = outcome
        self.message = message
        super().__init__(message)


def _invalid(message: str) -> _ReadFailure:
    return _ReadFailure(ZoteroReadOutcome.INVALID_RESPONSE, message)


def _item(payload: object) -> _Item:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise _invalid("Zotero returned unreadable item metadata.")
    key = payload.get("key")
    data = payload["data"]
    item_type = data.get("itemType")
    if (
        not isinstance(key, str)
        or not _ITEM_KEY.fullmatch(key)
        or data.get("key", key) != key
        or not isinstance(item_type, str)
        or not item_type.strip()
    ):
        raise _invalid("Zotero returned invalid item identity metadata.")
    parent = data.get("parentItem")
    if parent is not None and (
        not isinstance(parent, str) or not _ITEM_KEY.fullmatch(parent)
    ):
        raise _invalid("Zotero returned invalid child-parent metadata.")
    content_type = data.get("contentType")
    try:
        doi = normalize_doi(data.get("DOI"))
    except ValueError:
        doi = None
    return _Item(key, item_type, doi, parent, content_type)


def _json(response: httpx.Response) -> object:
    try:
        return response.json()
    except (ValueError, UnicodeError):
        raise _invalid("Zotero returned unreadable JSON.") from None


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

    def _get(
        self, url: httpx.URL, server_id: str | None, *, allow_missing: bool = False,
        allow_file_redirect: bool = False,
    ) -> tuple[httpx.Response, str]:
        headers = {"Zotero-Server-ID": server_id} if server_id is not None else {}
        try:
            response = self._http.get(url, headers=headers)
        except httpx.HTTPError:
            raise _ReadFailure(
                ZoteroReadOutcome.API_FAILURE,
                "Zotero is unavailable. Check Zotero Desktop and its Local API setting.",
            ) from None
        current_id = response.headers.get("Zotero-Server-ID", "").strip()
        if not current_id:
            raise _invalid("Zotero response has no Server-ID; the instance cannot be verified.")
        if (server_id is not None and current_id != server_id) or response.status_code == 412:
            raise _ReadFailure(
                ZoteroReadOutcome.SERVER_ID_MISMATCH,
                "The Zotero instance changed. Verify the item again before continuing.",
            )
        if response.status_code == 403:
            raise _ReadFailure(
                ZoteroReadOutcome.API_FAILURE,
                "Zotero Local API access is disabled. Enable it in Zotero's Advanced settings.",
            )
        if (response.status_code != 200
                and not (allow_missing and response.status_code == 404)
                and not (allow_file_redirect and response.status_code == 302)):
            raise _ReadFailure(
                ZoteroReadOutcome.API_FAILURE,
                f"Zotero Local API read failed (HTTP {response.status_code}).",
            )
        return response, current_id

    def _items(
        self, path: str, server_id: str | None, *, bibliographic: bool = False,
    ) -> Iterator[tuple[_Item, str]]:
        params = {"format": "json", "include": "data", "limit": "100", "start": "0"}
        if bibliographic:
            params["itemType"] = "-attachment"
        url = httpx.URL(LOCAL_API_BASE + path, params=params)
        total: int | None = None
        keys: set[str] = set()
        seen_urls: set[httpx.URL] = set()
        version: str | None = None
        while True:
            if url in seen_urls:
                raise _invalid("Zotero pagination repeated a page; enumeration is incomplete.")
            seen_urls.add(url)
            response, server_id = self._get(url, server_id)
            count = response.headers.get("Total-Results", "")
            if not count.isascii() or not count.isdecimal():
                raise _invalid("Zotero pagination has no reliable total count.")
            current_version = response.headers.get("Last-Modified-Version", "")
            if not current_version.isascii() or not current_version.isdecimal():
                raise _invalid("Zotero pagination has no usable Last-Modified-Version.")
            if total is None:
                total = int(count)
                version = current_version
            if int(count) != total or current_version != version:
                raise _invalid("Zotero library changed during enumeration. Try again.")
            payload = _json(response)
            if not isinstance(payload, list):
                raise _invalid("Zotero returned an unreadable item list.")
            for raw in payload:
                item = _item(raw)
                if item.key in keys:
                    raise _invalid("Zotero pagination repeated an item; enumeration is incomplete.")
                keys.add(item.key)
                yield item, server_id
            next_link = response.links.get("next")
            if len(keys) > total or (next_link is not None and not payload):
                raise _invalid("Zotero pagination is inconsistent.")
            if next_link is None:
                if len(keys) != total:
                    raise _invalid("Zotero pagination ended before all items were read.")
                return
            try:
                next_url = url.join(next_link["url"])
                next_params = dict(next_url.params)
                next_start = next_params.pop("start")
            except (KeyError, httpx.InvalidURL):
                raise _invalid("Zotero returned an invalid pagination link.") from None
            expected_params = {k: v for k, v in params.items() if k != "start"}
            if (
                next_url.copy_with(query=None) != url.copy_with(query=None)
                or next_params != expected_params
                or next_start != str(len(keys))
                or len(keys) >= total
            ):
                raise _invalid("Zotero pagination did not continue the same My Library read.")
            url = next_url

    def current_instance(self) -> ZoteroInstanceResult:
        """Read the current Local API instance without authorization or item access."""
        try:
            with _without_secret_logs():
                _, server_id = self._get(httpx.URL(LOCAL_API_BASE + "/"), None)
            return ZoteroInstanceResult(ZoteroReadOutcome.VERIFIED, server_id, "Zotero Local API is reachable.")
        except _ReadFailure as failure:
            return ZoteroInstanceResult(failure.outcome, None, failure.message)

    def resolve_identity(
        self, doi: str, zotero_key: str | None = None,
    ) -> ZoteroIdentityResult:
        """Verify a key or exhaust exact DOI fallback before accepting one parent."""
        try:
            normalized = normalize_doi(doi)
        except ValueError:
            normalized = None
        if normalized is None:
            return ZoteroIdentityResult(ZoteroReadOutcome.INVALID_DOI, None, "A normalized DOI is required.")
        server_id = None
        try:
            if isinstance(zotero_key, str) and _ITEM_KEY.fullmatch(zotero_key):
                response, server_id = self._get(
                    httpx.URL(f"{LOCAL_API_BASE}/users/0/items/{zotero_key}"),
                    server_id,
                    allow_missing=True,
                )
                if response.status_code == 200:
                    item = _item(_json(response))
                    if item.key != zotero_key:
                        raise _invalid("Zotero returned a different item than the requested key.")
                    if item.matches_doi(normalized):
                        return ZoteroIdentityResult(
                            ZoteroReadOutcome.VERIFIED,
                            VerifiedZoteroItem(item.key, normalized, server_id),
                            "Existing Zotero item verified by exact DOI.",
                        )
            matches = []
            for item, server_id in self._items("/users/0/items", server_id, bibliographic=True):
                if item.matches_doi(normalized):
                    matches.append(item.key)
            if not matches:
                return ZoteroIdentityResult(ZoteroReadOutcome.NOT_FOUND, None, "No exact DOI match exists in Zotero My Library.")
            if len(matches) > 1:
                return ZoteroIdentityResult(ZoteroReadOutcome.DUPLICATE, None, "Multiple exact DOI matches exist. Resolve Zotero duplicates first.")
            assert server_id is not None
            return ZoteroIdentityResult(
                ZoteroReadOutcome.VERIFIED,
                VerifiedZoteroItem(matches[0], normalized, server_id),
                "Existing Zotero item verified by exact DOI.",
            )
        except _ReadFailure as failure:
            return ZoteroIdentityResult(failure.outcome, None, failure.message)

    def verify_parent_key(
        self, doi: str, zotero_key: str, *, server_id: str | None = None,
    ) -> ZoteroIdentityResult:
        """Verify exactly this My Library parent; never enumerate a fallback (§36.4)."""
        try:
            normalized = normalize_doi(doi)
        except ValueError:
            normalized = None
        if normalized is None:
            return ZoteroIdentityResult(ZoteroReadOutcome.INVALID_DOI, None, "A normalized DOI is required.")
        if not isinstance(zotero_key, str) or not _ITEM_KEY.fullmatch(zotero_key):
            return ZoteroIdentityResult(ZoteroReadOutcome.INVALID_RESPONSE, None, "A valid Zotero parent key is required.")
        try:
            response, current_id = self._get(
                httpx.URL(f"{LOCAL_API_BASE}/users/0/items/{zotero_key}"),
                server_id, allow_missing=True,
            )
            if response.status_code == 404:
                return ZoteroIdentityResult(ZoteroReadOutcome.NOT_FOUND, None, "The Zotero parent key no longer exists.")
            item = _item(_json(response))
            if (item.key != zotero_key or item.parent_key is not None
                    or item.item_type not in _BIBLIOGRAPHIC_TYPES or not item.matches_doi(normalized)):
                raise _invalid("The current Zotero parent does not match the frozen key and DOI.")
            return ZoteroIdentityResult(
                ZoteroReadOutcome.VERIFIED, VerifiedZoteroItem(item.key, normalized, current_id),
                "Exact Zotero parent key and DOI verified.",
            )
        except _ReadFailure as failure:
            return ZoteroIdentityResult(failure.outcome, None, failure.message)

    def _has_pdf_file(self, key: str, server_id: str) -> bool:
        # Never follow the HTTP redirect or let debug logging expose its path.
        # A file URL is only a location hint; inspect current storage below.
        with _without_secret_logs():
            try:
                response, _ = self._get(
                    httpx.URL(f"{LOCAL_API_BASE}/users/0/items/{key}/file"),
                    server_id, allow_missing=True, allow_file_redirect=True,
                )
            except httpx.InvalidURL:
                raise _invalid("Zotero returned an unreadable file redirect.") from None
        if response.status_code == 404:
            return False
        if response.status_code != 302:
            raise _invalid("Zotero returned an unexpected file response.")
        location = response.headers.get("Location", "")
        # Zotero's getLocalFileURL() returns false when no file path is set;
        # the file endpoint serializes this as Location: false.
        if location == "false":
            return False
        try:
            target = urlsplit(location)
            if (not location.startswith("file://") or target.scheme != "file" or target.netloc not in ("", "localhost")
                    or not target.path.startswith("/") or len(target.path) <= 1
                    or "?" in location or "#" in location
                    or re.search(r"%(?![0-9a-fA-F]{2})", target.path)
                    or any(ord(char) < 32 or ord(char) == 127 for char in location)):
                raise ValueError
            path = Path(unquote(target.path, errors="strict"))
            if not path.is_absolute() or any(ord(char) < 32 or ord(char) == 127 for char in str(path)):
                raise ValueError
        except ValueError:
            raise _invalid("Zotero returned an unreadable file redirect.") from None
        try:
            current = path.stat(follow_symlinks=False)
        except (FileNotFoundError, NotADirectoryError):
            return False
        except OSError:
            raise _ReadFailure(ZoteroReadOutcome.API_FAILURE, "Zotero attachment storage could not be inspected.") from None
        return stat.S_ISREG(current.st_mode) and current.st_size > 0

    def inspect_attachments(self, parent: VerifiedZoteroItem) -> ZoteroAttachmentResult:
        """Complete child enumeration, then verify files; failure means unknown."""
        if not _ITEM_KEY.fullmatch(parent.key) or not parent.server_id.strip():
            return ZoteroAttachmentResult(ZoteroReadOutcome.INVALID_RESPONSE, None, "A verified Zotero parent is required.")
        try:
            pdf_keys = []
            for child, _ in self._items(f"/users/0/items/{parent.key}/children", parent.server_id):
                if child.parent_key != parent.key:
                    raise _invalid("Zotero returned children for a different parent.")
                if child.item_type == "attachment":
                    if not isinstance(child.content_type, str):
                        raise _invalid("Zotero returned unreadable attachment metadata.")
                    if child.content_type.strip().casefold() == "application/pdf":
                        pdf_keys.append(child.key)
            pdf_file_keys = tuple(key for key in pdf_keys if self._has_pdf_file(key, parent.server_id))
            return ZoteroAttachmentResult(
                ZoteroReadOutcome.CHECKED, bool(pdf_file_keys),
                "Zotero child attachments and files checked.", tuple(pdf_keys), pdf_file_keys,
            )
        except _ReadFailure as failure:
            return ZoteroAttachmentResult(failure.outcome, None, failure.message)
