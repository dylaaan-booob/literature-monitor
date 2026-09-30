"""XMU Full Text Finder browser boundary, limited to SPEC §35.7.

Protocol basis: the headed XMU page's POST /api/links was observed on
2026-09-30. It supplies customer s1215021/main/ftf, an OpenURL, and a flat
ordered links array. The provider's _app-f464f871d0f42ca8.js and
257-9d9382490581fa46.js establish proxyUrl precedence and baseUrl/queryString
fallback. We observe the page's request instead of recreating its private body.
No candidate navigation, downloads, storage-state export or Paper writes occur.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
import math
import os
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from platformdirs import user_data_path
from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout

from .identifiers import normalize_doi
from .institutional_browser import browser_session, institutional_profile

_ENTRY = "https://resolver.ebsco.com/c/45yels/result"
_ELIGIBLE = frozenset({"FullText", "SmartLinks"})


class ResolverOutcome(str, Enum):
    RESOLVED = "resolved"
    NO_ELIGIBLE_CANDIDATES = "no_eligible_candidates"
    AUTH_REQUIRED = "auth_required"
    BROWSER_UNAVAILABLE = "browser_unavailable"
    API_FAILURE = "api_failure"
    INVALID_RESPONSE = "invalid_response"
    INVALID_DOI = "invalid_doi"


@dataclass(frozen=True)
class ResolverCandidate:
    category: str
    target: str = field(repr=False)


@dataclass(frozen=True)
class ResolverResult:
    outcome: ResolverOutcome
    candidates: tuple[ResolverCandidate, ...] = ()

    @property
    def message(self) -> str:
        return {
            ResolverOutcome.RESOLVED: f"Resolver returned {len(self.candidates)} eligible candidates.",
            ResolverOutcome.NO_ELIGIBLE_CANDIDATES: "No eligible institutional full-text candidates.",
            ResolverOutcome.AUTH_REQUIRED: "Complete institutional login or human verification in the dedicated browser, then retry.",
            ResolverOutcome.BROWSER_UNAVAILABLE: "Dedicated browser unavailable. Check Chrome installation and close any browser using this profile, then retry.",
            ResolverOutcome.API_FAILURE: "Institutional resolver request failed; retry later.",
            ResolverOutcome.INVALID_RESPONSE: "Institutional resolver returned an invalid response.",
            ResolverOutcome.INVALID_DOI: "A DOI accepted by normalize_doi() is required.",
        }[self.outcome]


@contextmanager
def _browser_session():
    with browser_session(sync_playwright) as playwright:
        yield playwright


def _resolver_response(response) -> bool:
    try:
        url = urlsplit(response.url)
        return (
            url.scheme == "https" and url.hostname == "resolver.ebsco.com"
            and url.port in (None, 443) and url.path == "/api/links"
            and not url.query and not url.fragment and url.username is None
            and response.request.method == "POST" and response.request.redirected_from is None
        )
    except (ValueError, AttributeError):
        return False


def _request_identity(request, doi: str) -> ResolverOutcome | None:
    """Validate the actual page request; never forward or expose its body."""
    try:
        body = request.post_data_json
        customer = body["customer"]
        if not isinstance(customer, dict) or (
            customer.get("customerId"), customer.get("groupId"), customer.get("profileId")
        ) != ("s1215021", "main", "ftf"):
            return ResolverOutcome.INVALID_RESPONSE
        if customer.get("isAuthenticated") is False:
            return ResolverOutcome.AUTH_REQUIRED
        if customer.get("isAuthenticated") is not True:
            return ResolverOutcome.INVALID_RESPONSE
        open_url = body["openUrl"]
        if not isinstance(open_url, str):
            return ResolverOutcome.INVALID_RESPONSE
        query = parse_qs(urlsplit(open_url).query)
        references = query.get("rft_id")
        if not references or len(references) != 1 or not references[0].startswith("info:doi/"):
            return ResolverOutcome.INVALID_RESPONSE
        if normalize_doi(references[0][len("info:doi/"):]) != doi or query.get("x-opid") != ["45yels"]:
            return ResolverOutcome.INVALID_RESPONSE
    except (ValueError, TypeError, KeyError, AttributeError):
        return ResolverOutcome.INVALID_RESPONSE
    return None


def _navigation_target(link: dict) -> str:
    # This follows the observed provider helper/component, including its entity
    # encoding; it does not extract DOM anchors or interpret session parameters.
    base, proxy, query = (link.get(name) for name in ("baseUrl", "proxyUrl", "queryString"))
    if not all(isinstance(value, str) for value in (base, proxy, query)) or not base:
        raise ValueError("Invalid resolver candidate.")
    target = proxy or base + query
    for encoded, replacement in (
        ("%26amp%3Blt%3B", "%3C"), ("%26amp%3Bgt%3B", "%3E"), ("%26amp%3B", "%26"),
        ("&amp;amp;lt", "%3C"), ("&amp;amp;gt", "%3E"),
        ("&amp;lt", "%3C"), ("&amp;gt", "%3E"),
        ("&amp;", "%26"), ("&lt;", "%3C"), ("&gt;", "%3E"),
    ):
        target = target.replace(encoded, replacement)
    parts = urlsplit(target)
    if (
        parts.scheme not in {"https", "http"} or not parts.hostname or parts.username is not None
        or any(ord(char) <= 32 or ord(char) == 127 for char in target) or "\\" in target
    ):
        raise ValueError("Invalid resolver candidate.")
    # Accessing .port also detects malformed authorities without echoing URLs.
    _ = parts.port
    return target


def _parse_response(payload, doi: str) -> ResolverResult:
    try:
        if not isinstance(payload, dict) or payload.get("diagnostics") != []:
            raise ValueError
        links, facts = payload["links"], payload["facts"]
        if not isinstance(links, list) or not isinstance(facts, list) or not all(isinstance(f, dict) for f in facts):
            raise ValueError
        doi_facts = [normalize_doi(f.get("value")) for f in facts if f.get("key") == "doi"]
        if doi_facts != [doi]:
            raise ValueError
        candidates = []
        for link in links:
            if not isinstance(link, dict) or not isinstance(link.get("category"), str):
                raise ValueError
            if link["category"] not in _ELIGIBLE:
                continue
            if type(link.get("rank")) is not int or link["rank"] < 0:
                raise ValueError
            candidates.append(ResolverCandidate(link["category"], _navigation_target(link)))
        # The observed response is already a flat ordered array. Preserve it,
        # including equal ranks; do not apply UI label sorting or local ranking.
        return ResolverResult(
            ResolverOutcome.RESOLVED if candidates else ResolverOutcome.NO_ELIGIBLE_CANDIDATES,
            tuple(candidates),
        )
    except (ValueError, TypeError, KeyError):
        return ResolverResult(ResolverOutcome.INVALID_RESPONSE)


def _human_intervention(page) -> bool:
    # Read only authentication indicators, never candidate anchors/page contents.
    if urlsplit(page.url).hostname != "resolver.ebsco.com":
        return True
    return page.locator('input[type="password"], iframe[src*="captcha"], #challenge-stage').count() > 0


class XmuInstitutionalResolver:
    """One bounded synchronous lookup, using only the dedicated browser profile.

    The caller supplies protected locations so platformdirs overrides/symlinks
    cannot place session material in its project, workspace or config directory.
    Terminal return closes the browser; the persistent session remains on disk.
    """

    def __init__(self, *, project_dir: Path, workspace_dir: Path, config_path: Path,
                 timeout_seconds: float = 60.0):
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Resolver timeout must be finite and positive.")
        self._protected = (Path(project_dir).resolve(), Path(workspace_dir).resolve(), Path(config_path).resolve().parent)
        self._timeout_ms = timeout_seconds * 1000

    def _profile(self) -> Path:
        return institutional_profile(user_data_path("Literature Monitor", appauthor=False), self._protected)

    def resolve(self, doi: str | None) -> ResolverResult:
        try:
            normalized = normalize_doi(doi)
        except ValueError:
            normalized = None
        if normalized is None:
            return ResolverResult(ResolverOutcome.INVALID_DOI)
        launched = False
        try:
            profile = self._profile()
            with _browser_session() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(profile), channel="chrome", headless=False, accept_downloads=False,
                    timeout=self._timeout_ms,
                )
                launched = True
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    navigation = None
                    # A prior browser session may retain other pages. This module
                    # navigates only the one resolver page, never their anchors.
                    with page.expect_response(_resolver_response, timeout=self._timeout_ms) as pending:
                        navigation = page.goto(
                            _ENTRY + "?" + urlencode({"rft_id": "info:doi/" + normalized}),
                            wait_until="domcontentloaded", timeout=self._timeout_ms,
                        )
                    response = pending.value
                    if response.status in (401, 403):
                        return ResolverResult(ResolverOutcome.AUTH_REQUIRED)
                    if response.status != 200:
                        return ResolverResult(ResolverOutcome.API_FAILURE)
                    identity_error = _request_identity(response.request, normalized)
                    if identity_error is not None:
                        return ResolverResult(identity_error)
                    try:
                        payload = response.json()
                    except Exception:
                        return ResolverResult(ResolverOutcome.INVALID_RESPONSE)
                    return _parse_response(payload, normalized)
                except BrowserTimeout:
                    return ResolverResult(
                        ResolverOutcome.AUTH_REQUIRED
                        if (navigation is not None and navigation.status in (401, 403)) or _human_intervention(page)
                        else ResolverOutcome.API_FAILURE,
                    )
                finally:
                    context.close()
        except Exception:
            return ResolverResult(ResolverOutcome.API_FAILURE if launched else ResolverOutcome.BROWSER_UNAVAILABLE)
