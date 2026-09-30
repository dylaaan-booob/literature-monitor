"""Ordered, generic browser PDF acquisition; ends at a temporary file (§35.8–9)."""

from dataclasses import dataclass, field
from enum import Enum
import math
from pathlib import Path
import shutil
import tempfile
import time
from urllib.parse import urljoin, urlsplit

from platformdirs import user_data_path
from playwright.sync_api import TimeoutError as BrowserTimeout

from .institutional_browser import browser_session as _browser_session, institutional_profile
from .institutional_resolver import ResolverCandidate


class PdfAcquisitionOutcome(str, Enum):
    ACQUIRED = "acquired"
    NO_VALID_PDF = "no_valid_pdf"
    AUTH_REQUIRED = "auth_required"
    BROWSER_UNAVAILABLE = "browser_unavailable"
    API_FAILURE = "api_failure"
    INVALID_RESPONSE = "invalid_response"


class PdfSourceKind(str, Enum):
    DIRECT_RESPONSE = "direct_response"
    DOWNLOAD = "download"
    CITATION_PDF_URL = "citation_pdf_url"
    EXPLICIT_PDF_LINK = "explicit_pdf_link"


@dataclass(frozen=True)
class AcquiredPdf:
    """Caller owns this file after return; cleanup() or a with block removes it."""

    path: Path = field(repr=False)
    source_kind: PdfSourceKind
    candidate_position: int  # One-based position in the resolver input.
    category: str
    byte_count: int
    _directory: Path = field(repr=False)

    def cleanup(self) -> None:
        shutil.rmtree(self._directory, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cleanup()


@dataclass(frozen=True)
class PdfAcquisitionResult:
    outcome: PdfAcquisitionOutcome
    pdf: AcquiredPdf | None = None

    @property
    def message(self) -> str:
        return {
            PdfAcquisitionOutcome.ACQUIRED: "Validated temporary PDF acquired; caller must clean up after use.",
            PdfAcquisitionOutcome.NO_VALID_PDF: "No valid PDF found in the supplied resolver candidates.",
            PdfAcquisitionOutcome.AUTH_REQUIRED: "Complete login or human verification in the dedicated browser, then retry.",
            PdfAcquisitionOutcome.BROWSER_UNAVAILABLE: "Dedicated browser unavailable. Check Chrome installation and close any browser using this profile, then retry.",
            PdfAcquisitionOutcome.API_FAILURE: "PDF acquisition failed; retry later.",
            PdfAcquisitionOutcome.INVALID_RESPONSE: "Valid ordered resolver candidates are required.",
        }[self.outcome]


def _target(value, base: str = "") -> str | None:
    if not isinstance(value, str) or not value or "\\" in value or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        return None
    try:
        target = urljoin(base, value)
        parts = urlsplit(target)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None:
            return None
        _ = parts.port
        return target
    except ValueError:
        return None


def _valid_pdf(data) -> bool:
    # Content-Type, filename and status are discovery signals, never validators.
    return isinstance(data, bytes) and data.startswith(b"%PDF")


def _remaining(deadline: float) -> float:
    return max(1, (deadline - time.monotonic()) * 1000)


def _human_required(page, response) -> bool:
    if response is not None and response.status in (401, 403):
        return True
    return page.locator(
        'input[type="password"], input[autocomplete="one-time-code"], '
        'iframe[src*="captcha" i], iframe[src*="challenge" i], '
        'input[name*="captcha" i], #challenge-stage, .cf-turnstile, .g-recaptcha'
    ).count() > 0


@dataclass
class _VisitResult:
    pdf: AcquiredPdf | None = None
    auth_required: bool = False
    landing: bool = False


class GenericPdfAcquirer:
    """One bounded attempt in the A3 dedicated profile; no durable PDF state."""

    def __init__(self, *, project_dir: Path, workspace_dir: Path, config_path: Path,
                 timeout_seconds: float = 60.0):
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Acquisition timeout must be finite and positive.")
        self._protected = (Path(project_dir).resolve(), Path(workspace_dir).resolve(), Path(config_path).resolve().parent)
        self._timeout = timeout_seconds

    def _temporary_directory(self, profile: Path) -> Path:
        parent = Path(tempfile.gettempdir()).resolve()
        if any(parent.is_relative_to(root) for root in (*self._protected, profile)):
            raise ValueError("Temporary directory overlaps protected application state.")
        return Path(tempfile.mkdtemp(prefix="literature-monitor-pdf-", dir=parent))

    def _save(self, data: bytes, directory: Path, source: PdfSourceKind, position: int, category: str) -> AcquiredPdf | None:
        if not _valid_pdf(data):
            return None
        with tempfile.NamedTemporaryFile(dir=directory, suffix=".pdf", delete=False) as stream:
            stream.write(data)
            path = Path(stream.name)
        return AcquiredPdf(path, source, position, category, len(data), directory)

    def _received_pdf(self, response, downloads, directory, source, position, category) -> AcquiredPdf | None:
        if response is not None and 200 <= response.status < 300:
            try:
                pdf = self._save(response.body(), directory, source, position, category)
                if pdf is not None:
                    return pdf
            except Exception:
                pass  # A download response may not expose a body.
        while downloads:
            download = downloads.pop(0)
            path, keep = None, False
            try:
                with tempfile.NamedTemporaryFile(dir=directory, suffix=".pdf", delete=False) as stream:
                    path = Path(stream.name)
                # The suggested filename and final URL are never used.
                download.save_as(str(path))
                data = path.read_bytes()
                if _valid_pdf(data):
                    keep = True
                    kind = PdfSourceKind.DOWNLOAD if source is PdfSourceKind.DIRECT_RESPONSE else source
                    return AcquiredPdf(path, kind, position, category, len(data), directory)
            except Exception:
                pass
            finally:
                if path is not None and not keep:
                    path.unlink(missing_ok=True)
        return None

    def _visit(self, page, target: str, directory: Path, deadline: float,
               position: int, category: str, source: PdfSourceKind) -> _VisitResult:
        responses, downloads = [], []
        def on_response(response):
            try:
                request = response.request
                if request.is_navigation_request() and request.frame == page.main_frame:
                    responses.append(response)
            except Exception:
                pass  # Never let event-loop errors disclose a response target.
        def on_download(download):
            downloads.append(download)
        page.on("response", on_response)
        page.on("download", on_download)
        navigation_ok = False
        try:
            try:
                response = page.goto(target, wait_until="domcontentloaded", timeout=_remaining(deadline))
                navigation_ok = response is not None
                if response is not None and (not responses or response is not responses[-1]):
                    responses.append(response)
            except Exception:
                # PDF download navigation commonly aborts; its event remains usable.
                pass
            page.wait_for_timeout(0)  # Dispatch any queued navigation/download event.
            response = responses[-1] if responses else None
            pdf = self._received_pdf(response, downloads, directory, source, position, category)
            if pdf is not None:
                return _VisitResult(pdf=pdf)
            if not _human_required(page, response) and (response is None or 200 <= response.status < 300):
                # A page-triggered download can arrive just after DOMContentLoaded.
                # Observe it before moving to lower-priority metadata/link discovery.
                try:
                    page.wait_for_event("download", timeout=min(1000, _remaining(deadline)))
                except BrowserTimeout:
                    pass
                response = responses[-1] if responses else None
                pdf = self._received_pdf(response, downloads, directory, source, position, category)
                if pdf is not None:
                    return _VisitResult(pdf=pdf)
            if _human_required(page, response):
                while time.monotonic() < deadline and _human_required(page, response):
                    page.wait_for_timeout(min(250, _remaining(deadline)))
                    response = responses[-1] if responses else None
                if _human_required(page, response):
                    return _VisitResult(auth_required=True)
                navigation_ok = response is not None
                pdf = self._received_pdf(response, downloads, directory, source, position, category)
                if pdf is not None:
                    return _VisitResult(pdf=pdf)
            return _VisitResult(landing=navigation_ok and response is not None and 200 <= response.status < 300)
        finally:
            page.remove_listener("response", on_response)
            page.remove_listener("download", on_download)

    def _candidate(self, page, candidate, position, directory, deadline) -> _VisitResult:
        visit = self._visit(page, candidate.target, directory, deadline, position, candidate.category, PdfSourceKind.DIRECT_RESPONSE)
        if visit.pdf or visit.auth_required or not visit.landing:
            return visit
        base = page.url
        metadata = page.locator('meta[name="citation_pdf_url" i]').evaluate_all("nodes => nodes.map(n => n.getAttribute('content'))")
        # Capture only explicit signals on this landing before navigating away.
        try:
            links = page.locator('a[href], link[href]').evaluate_all(
                "nodes => nodes.map(n => ({href:n.getAttribute('href'), type:n.getAttribute('type')}))"
            )
        except Exception:
            links = []  # A lower-priority discovery failure cannot discard metadata.
        def explicit_targets():
            for link in links if isinstance(links, list) else []:
                if not isinstance(link, dict):
                    continue
                target = _target(link.get("href"), base)
                mime = link.get("type")
                if target and ((isinstance(mime, str) and mime.lower().split(";")[0].strip() == "application/pdf")
                               or urlsplit(target).path.lower().endswith(".pdf")):
                    yield target
        visited = {candidate.target}
        for values, source in ((metadata if isinstance(metadata, list) else [], PdfSourceKind.CITATION_PDF_URL),
                               (explicit_targets(), PdfSourceKind.EXPLICIT_PDF_LINK)):
            for value in values:
                target = _target(value, base)
                if target is None or target in visited or time.monotonic() >= deadline:
                    continue
                visited.add(target)
                visit = self._visit(page, target, directory, deadline, position, candidate.category, source)
                if visit.pdf or visit.auth_required:
                    return visit
        return _VisitResult()

    def acquire(self, candidates: tuple[ResolverCandidate, ...]) -> PdfAcquisitionResult:
        if not isinstance(candidates, tuple) or any(
            not isinstance(candidate, ResolverCandidate) or not isinstance(candidate.category, str)
            or candidate.category not in {"FullText", "SmartLinks"}
            or _target(candidate.target) is None for candidate in candidates
        ):
            return PdfAcquisitionResult(PdfAcquisitionOutcome.INVALID_RESPONSE)
        if not candidates:
            return PdfAcquisitionResult(PdfAcquisitionOutcome.NO_VALID_PDF)
        directory, acquired, launched, transferred = None, None, False, False
        deadline = time.monotonic() + self._timeout
        try:
            profile = institutional_profile(user_data_path("Literature Monitor", appauthor=False), self._protected)
            directory = self._temporary_directory(profile)
            with _browser_session() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(profile), channel="chrome", headless=False, accept_downloads=True,
                    downloads_path=str(directory), timeout=_remaining(deadline),
                )
                launched = True
                try:
                    context.set_default_timeout(_remaining(deadline))
                    page = context.new_page()
                    for position, candidate in enumerate(candidates, 1):
                        if time.monotonic() >= deadline:
                            break
                        try:
                            visit = self._candidate(page, candidate, position, directory, deadline)
                        except Exception:
                            continue  # A broken candidate must not discard later routes.
                        if visit.auth_required:
                            return PdfAcquisitionResult(PdfAcquisitionOutcome.AUTH_REQUIRED)
                        if visit.pdf:
                            acquired = visit.pdf
                            break
                finally:
                    context.close()
            if acquired is not None:
                # Browser-owned partial downloads must not survive with the handle.
                for path in directory.iterdir():
                    if path != acquired.path:
                        if path.is_dir():
                            shutil.rmtree(path, ignore_errors=True)
                        else:
                            path.unlink(missing_ok=True)
                if not acquired.path.is_file() or not _valid_pdf(acquired.path.read_bytes()):
                    raise ValueError("Validated temporary file unavailable.")
                transferred = True
                return PdfAcquisitionResult(PdfAcquisitionOutcome.ACQUIRED, acquired)
            return PdfAcquisitionResult(PdfAcquisitionOutcome.NO_VALID_PDF)
        except Exception:
            acquired = None
            return PdfAcquisitionResult(PdfAcquisitionOutcome.API_FAILURE if launched else PdfAcquisitionOutcome.BROWSER_UNAVAILABLE)
        finally:
            if directory is not None and not transferred:
                shutil.rmtree(directory, ignore_errors=True)
