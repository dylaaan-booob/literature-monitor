"""Read CARSI's public embedded SPList and build the §44 AI work file.

This module has no Settings UI, configuration write or persistent cache.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from literature_monitor.application.access_service_migration import _source_identity
from literature_monitor.application.full_markdown_import import (
    AI_MAPPING_PACK_MARKER, MAX_MARKDOWN_BYTES,
)
from literature_monitor.application.settings import SettingsIssueSource, _read_snapshot
from literature_monitor.config import ConfigurationError, parse_settings_list_text
from literature_monitor.safe_write import ContentChangedError


CARSI_DIRECTORY_URL = "https://www.carsi.edu.cn/SPlist.html"
AI_MAPPING_PACK_FILENAME = "literature-monitor-ai-mapping-pack.md"
MAX_CARSI_HTML_BYTES = 2 * 1024 * 1024
MAX_CARSI_RECORDS = 10_000
CARSI_TIMEOUT = httpx.Timeout(8.0, connect=4.0)
CARSI_TOTAL_SECONDS = 20.0

AI_EDITING_PROMPT_V1 = """### Editing contract (access-aware mapping)

You are editing the complete saved Literature Monitor list.md, using the official
CARSI resource records below as reference data. First output the final, complete,
importable list.md in ONE standalone Markdown code block tagged markdown. Choose
an enclosing fence longer than any backtick run inside list.md. The fences are not part of the importable file.
These code fences are response formatting only. After closing that block,
provide, IN ORDER: (2) Mapping changes summary, (3) Existing manual Services
review, (4) Unassigned Publishers research, and (5) Manual action items.
The first output is always part (1): the full list.md alone in its code block.
Do not return the AI Mapping Pack, its marker or the CARSI catalog appendix,
and never put research inside list.md.

- Edit ONLY rows in ## Access Services and the Access Service ID cell of
  EXISTING rows in ## Publishers. Preserve the full file and its original
  non-managed content, including all Journals, ISSN-L, Group assignments,
  Conferences, unknown Sections, human notes and other text.
- Preserve all Journal names and machine-managed Publisher relationships.
  Preserve each Publisher ID, official Publisher name and user-maintained
  Publisher URL. Never add, delete or rename a Journal or Publisher.
- Before ANY modification, inspect the complete list.md: ALL Journals and their
  direct Publisher links, ALL Publishers and their Access Service ID references,
  ALL existing Access Services and their Publisher members. Classify each
  Service as officially evidenced in this CARSI catalog or user-maintained
  without confirmed CARSI status; distinguish both from Unassigned Publishers.
  Absence from the catalog alone never makes a user-maintained Service invalid.
- Access Method is exactly carsi or manual (Manual Login). A legacy three-column
  Access Services table defaults to carsi. Preserve each existing Access Method;
  never overwrite a user's manual classification from CARSI directory evidence.
  New officially supported CARSI Services use carsi. Manual candidates remain
  advisory human actions; do not add them automatically.
- Default to preserving each existing Service ID, name, Access URL, Access Method, and every
  Publisher assignment, including non-CARSI manual Services. Never delete,
  rename, replace, detach or overwrite an existing manual Service solely because
  it is absent from CARSI. If evidence conflicts with a user decision or an
  existing Service field, report the conflict for manual review; do not
  silently change it. An institutional-login option on a manual Service does
  NOT establish compatibility with CARSI Portal Preparation.
- Before creating a Service, inspect existing Access Services and preferentially
  reuse any Service already corresponding to the same official CARSI resource.
  Preserve ALL existing Service IDs across name, URL or membership edits; never
  duplicate an existing CARSI resource or regenerate an existing Service ID.
- Only for genuinely new resources, independently generate a fresh, unique UUID
  and use Service ID svc_ followed by its 32 lowercase hexadecimal digits
  (svc_[0-9a-f]{32}). Do not derive an ID from names or URLs or reuse an ID.
- For a newly created officially supported CARSI resource, AccessService.name
  (Service column) must reproduce the exact original CARSI production string;
  AccessService.access_url (Access URL column) must use that record's
  corresponding url value. For an existing Service, reuse its stable ID and
  preserve its user-maintained name and URL by default; flag any disagreement
  with official CARSI fields rather than forcibly overwriting them.
  vendor_name is reference evidence for matching, not the Service name.
  Respect existing safe public HTTP/HTTPS URL and Service uniqueness validation.
  Some official url values lack a scheme; preserve those catalog values as evidence,
  but never invent a scheme or insert an invalid URL in list.md. Leave an uncertain
  resource or Publisher association Unassigned rather than guess a usable URL.
- Several Publishers may map to one Service. Assign an Unassigned Publisher
  to an official CARSI Service only when reliable evidence connects its actual
  Journal/PDF access platform to that official resource. Never infer that
  connection from Publisher-name similarity alone; uncertainty means leave
  its Access Service ID empty (Unassigned). Do not disturb already assigned
  Publishers on weaker or conflicting evidence.
- A non-CARSI platform supporting institutional access is a MANUAL Service
  candidate only: recommend user creation/association outside list.md, never
  automatically create a Service row or assign its Publisher during AI mapping.
  Unassigned can be an accurate final configuration, not an error to eliminate.
- Never fabricate Publisher associations, alternative resource aliases,
  WAYFless URLs, institution/IdP login URLs or a federation login route.
- The first output block must contain ONLY the complete valid list.md, with
  unchanged managed schema and preserved user content, suitable for the existing
  full Markdown Import → Preview → Confirm flow. Do not embed this Pack marker,
  instructions, catalog, commentary or the output fences in the imported file.

### Mapping changes summary (outside list.md only)

State any newly created, officially evidenced CARSI Services, reused existing
CARSI Services, and Publisher assignments changed. Identify changes by name
and stable Service ID. If no edits were made, state exactly No mapping changes.

### Existing manual Services review (outside list.md only)

List each existing Service without confirmed CARSI catalog correspondence, its
current assigned Publishers, the evidence gap and any CARSI Preparation
compatibility limitation. Preserve user decisions; do not claim a manual
institutional platform is prepared by the current CARSI-only workflow.
Report unresolved potential mapping conflicts for manual review.

### Unassigned Publishers research (outside list.md only)

After completing the CARSI Access Mapping above, investigate EVERY Publisher
whose final Access Service ID remains empty (Unassigned). Research begins only
AFTER the list.md code block has been finalized: do not revise that block in
response to later findings. This is advisory research for the user's manual
decision, not another configuration edit. Unassigned is a legitimate result
when formal PDFs are free, access is conditional, or evidence is insufficient.

- Identify each Unassigned Publisher by its exact name and OpenAlex Publisher ID.
  Extract ALL Journals associated with it from the complete list.md, including
  each Journal name and ISSN-L; omit none. State explicitly if there are none.
- For EVERY associated Journal, investigate its actual official Journal website,
  official publishing and access policy, DOI landing pages and formal PDF host.
  Record the policy URL and classify the Journal: all formal published PDFs free;
  mixed free and subscription articles; delayed free access/embargo; public
  accepted manuscripts or preprints; primarily subscription; or unknown.
  Distinguish Free to Read from an Open Access license. Do not confuse the
  Version of Record (formal published PDF) with an accepted manuscript, preprint,
  abstract or supplemental material. A publicly available manuscript alone
  never establishes that formal published PDFs are free.
- For EACH associated Journal, locate 1–3 recent, genuine research articles if
  verifiable. Give article title, real DOI, the DOI's observed redirect landing
  URL and actual article/full-text hosting platform. Never invent DOI examples,
  infer landing pages from Publisher homepages, or substitute a Publisher site
  for an unverified host. A real article with no DOI may instead use its
  verified official article URL as supplemental evidence; explicitly state
  DOI unavailable and do not fabricate one or change the application's
  DOI-only Paper identity contract. If fewer than one article can be verified
  for a Journal, report that gap rather than making up samples.
- For each DOI sample, inspect the actual landing/PDF access path, where
  accessible, and report TWO INDEPENDENT dimensions for that DOI:
  (1) Actual PDF hosting platform: Publisher-operated, third-party platform
  (name the platform), or unknown. Do not infer a PDF host from the DOI landing page.
  (2) PDF access status: publicly downloadable PDF (verified without login);
  restricted PDF requiring institutional authentication; personal-account or
  payment-only PDF; no PDF found; or cannot verify (undetermined).
  A PDF on a third-party platform may be public or restricted: PDF hosting and
  access status are not mutually exclusive. Identify the actual PDF URL and
  unauthenticated retrieval outcome when verifiable. A PDF button or
  open-access metadata alone never proves a downloadable PDF. Also identify
  the PDF version separately: formal Version of Record, accepted manuscript,
  preprint, supplemental material, or unknown. Do not call an abstract or
  supplement a full formal PDF. A retrieval timeout, anti-bot block, redirect
  error or download-tool failure is cannot verify (undetermined), NEVER proof
  of a paywall or institutional-authentication requirement.
- For restricted PDFs, check the actual full-text platform's documented
  institutional login option and any evidenced CARSI directory Service,
  especially a reusable Access Service already present in list.md. Do not
  presume an institution's entitlement, a Publisher-to-Service relationship,
  or a login URL. Name unconfirmed candidates for manual verification and
  KEEP ambiguous Publishers Unassigned; research must not silently remap them.
  For a non-CARSI institutional platform, recommend Manual Service Candidate
  rather than inventing a CARSI Service, even if login is supported.
- For each Publisher, assess DOI-to-PDF value ONLY for the checked DOI samples
  and state the sample coverage and limits. Report separately any public PDF
  without CARSI, institutional PDF worth checking a Service, restricted PDF
  without identified institutional access, and PDF hosted elsewhere (prioritize
  that platform); these findings may coexist. If no obtainable PDF was confirmed,
  describe limited Publisher-site utility for THESE samples or insufficient
  evidence. Never generalize from a few articles to a Publisher-wide claim
  that it offers no PDFs. Recommend existing-Service reuse only when supported,
  otherwise manual investigation or limited utility.
- Provide working evidence links to actual DOI records, observed article landing
  URLs, verified PDF URLs and/or official access guidance. Separate observed
  facts, plausible hypotheses and unknowns. If browsing, redirect resolution,
  unauthenticated PDF retrieval or sites are unavailable, disclose precisely
  what could not be checked; never simulate network results or access rights.
  Samples constrain conclusions about the checked DOIs; use documented Journal
  policy to support any broader claim and identify conflicting evidence.
- Give exactly ONE primary recommendation per remaining Unassigned Publisher:
  No Service Needed only when official policy and sample evidence support free
  access to relevant formal published PDFs without institutional login;
  Keep Unassigned — Conditional Access for embargoes, hybrid access, distinct
  document versions or partial subscription; Reuse Existing CARSI Service only
  if the actual platform and official CARSI record are demonstrably linked,
  as a MANUAL follow-up rather than an edit to the finalized block;
  Manual Service Candidate for an institution-enabled non-CARSI platform
  requiring user-managed creation/association; or Needs Verification where
  evidence is incomplete. Explain differing Journal policies under one
  Publisher. Do not equate free articles with Publisher-wide free access.
  If the current Publisher URL is less helpful than the verified Journal
  website, suggest a manual Publisher URL change with evidence, but NEVER
  change Publisher URL in the importable list.md. Do not recommend Journal
  deletion simply to reduce Unassigned; monitoring scope belongs to the user.

Place each Publisher's findings AFTER the list.md code block, with its name,
OpenAlex ID, ALL associated Journals/ISSN-L, DOI samples and landing hosts,
separate PDF hosting platform and access status for EACH DOI sample, evidence,
sample-limited value assessment and Service/manual recommendation.
Do not create an additional report file, delete or rename Journal/Publisher
records, alter protected machine fields or automatically delete Access Services.
Research is not permission to change the already completed Access Mapping.

### Manual action items (outside list.md only)

Summarize only actionable user decisions: a verified candidate for a manual
Service, an existing official CARSI Service to consider reusing, an institution
subscription to check, a useful Publisher URL suggestion, or a justified
decision to leave a Publisher Unassigned. Every suggestion is advisory; no
additional changes to the already generated list.md and no separate report
file.
"""


class MappingPackError(ValueError):
    """The public catalog or saved Markdown cannot safely produce a Pack."""


@dataclass(frozen=True)
class CarsiResource:
    production: str
    vendor_name: str
    url: str


@dataclass(frozen=True)
class AIMappingPack:
    filename: str
    markdown: str


class _Scripts(HTMLParser):
    """Collect HTML script text without evaluating JavaScript or fetching assets."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._active = False
        self._parts: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self._active = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._active:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._active:
            self.scripts.append("".join(self._parts))
            self._active = False


def parse_carsi_directory(html: str) -> tuple[CarsiResource, ...]:
    """Parse the verified inline `var SPList=[...];` in official SPlist.html."""
    if len(html.encode("utf-8")) > MAX_CARSI_HTML_BYTES:
        raise MappingPackError("CARSI directory response exceeds size limit")
    scripts = _Scripts()
    scripts.feed(html)
    declarations = []
    for script in scripts.scripts:
        match = re.match(r"\s*var\s+SPList\s*=\s*", script)
        if match is not None:
            declarations.append(script[match.end():])
    if len(declarations) != 1:
        raise MappingPackError("CARSI directory has no unique official SPList data")
    try:
        records, position = json.JSONDecoder().raw_decode(declarations[0])
    except (ValueError, TypeError) as error:
        raise MappingPackError("CARSI SPList is not valid JSON data") from error
    if not declarations[0][position:].lstrip().startswith(";"):
        raise MappingPackError("CARSI SPList declaration is incomplete")
    if not isinstance(records, list) or not 0 < len(records) <= MAX_CARSI_RECORDS:
        raise MappingPackError("CARSI SPList must contain a bounded, nonempty resource array")

    resources = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict) or any(
            not isinstance(record.get(field), str) or not record[field].strip()
            for field in ("production", "vendor_name", "url")
        ):
            raise MappingPackError(f"CARSI SPList resource {index} lacks required fields")
        url = record["url"]
        try:
            parsed = urlsplit(url)
        except ValueError as error:
            raise MappingPackError(f"CARSI SPList resource {index} has an invalid URL") from error
        # The live official SPList contains scheme-less "www..." URL fields.
        # Preserve these verbatim as reference data; NEVER invent https://.
        if parsed.scheme:
            acceptable = (parsed.scheme in ("https", "http") and parsed.hostname
                          and parsed.username is None and parsed.password is None)
        else:
            acceptable = (url.startswith("www.") and not re.search(r"\s|[<>]", url)
                          and "@" not in url)
        if not acceptable:
            raise MappingPackError(f"CARSI SPList resource {index} has an invalid URL")
        resources.append(CarsiResource(
            production=record["production"],
            vendor_name=record["vendor_name"],
            url=url,
        ))
    return tuple(resources)


def fetch_carsi_directory(*, transport: httpx.BaseTransport | None = None) -> tuple[CarsiResource, ...]:
    """One uncached public GET, no redirect following, with response/elapsed bounds."""
    started = time.monotonic()
    try:
        with httpx.Client(
            transport=transport, timeout=CARSI_TIMEOUT, follow_redirects=False,
        ) as client:
            with client.stream("GET", CARSI_DIRECTORY_URL, headers={
                "Accept": "text/html",
                "User-Agent": "literature-monitor/0.6.8 (public CARSI catalog export)",
            }) as response:
                if response.is_redirect:
                    raise MappingPackError("CARSI directory redirected; refusing unexpected destination")
                if response.status_code != 200:
                    raise MappingPackError(f"CARSI directory returned HTTP {response.status_code}")
                if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "text/html":
                    raise MappingPackError("CARSI directory has an unexpected content type")
                declared = response.headers.get("content-length")
                if declared is not None:
                    try:
                        size = int(declared)
                    except ValueError as error:
                        raise MappingPackError("CARSI directory has invalid Content-Length") from error
                    if size < 0 or size > MAX_CARSI_HTML_BYTES:
                        raise MappingPackError("CARSI directory exceeds size limit")
                data = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    data.extend(chunk)
                    if len(data) > MAX_CARSI_HTML_BYTES:
                        raise MappingPackError("CARSI directory exceeds size limit")
                    if time.monotonic() - started > CARSI_TOTAL_SECONDS:
                        raise MappingPackError("CARSI directory request exceeded total time limit")
    except httpx.TimeoutException as error:
        raise MappingPackError("CARSI directory request timed out") from error
    except httpx.RequestError as error:
        raise MappingPackError("CARSI directory request failed") from error
    try:
        return parse_carsi_directory(data.decode("utf-8"))
    except UnicodeError as error:
        raise MappingPackError("CARSI directory is not valid UTF-8") from error


def _fence_for(*contents: str) -> str:
    longest = max((len(match.group()) for item in contents
                   for match in re.finditer(r"`+", item)), default=0)
    return "`" * max(4, longest + 1)


def build_ai_mapping_pack(saved_list: str, resources: tuple[CarsiResource, ...]) -> AIMappingPack:
    """Embed the exact UTF-8 source with size/hash for lossless extraction."""
    if not resources:
        raise MappingPackError("Cannot build Pack without official CARSI resources")
    raw = saved_list.encode("utf-8")
    if not raw or len(raw) > MAX_MARKDOWN_BYTES:
        raise MappingPackError("Saved list.md exceeds the supported size boundary")
    directory = json.dumps([{
        "production": item.production, "vendor_name": item.vendor_name, "url": item.url,
    } for item in resources], ensure_ascii=False, indent=2)
    fence = _fence_for(saved_list, directory)
    # The single separator newline after a non-LF-terminated source is NOT part
    # of the source: the recorded byte length and SHA-256 identify it exactly.
    closing_line = "" if saved_list.endswith("\n") else "\n"
    markdown = (
        f"{AI_MAPPING_PACK_MARKER}\n"
        "# Literature Monitor AI Mapping Pack — v1 (work file; do not import)\n\n"
        "## Fixed AI editing instructions\n\n"
        f"{AI_EDITING_PROMPT_V1}\n"
        "## Complete saved list.md — byte-exact UTF-8 source\n\n"
        f"UTF-8 byte length: {len(raw)}\n"
        f"SHA-256: {hashlib.sha256(raw).hexdigest()}\n\n"
        f"{fence}markdown\n{saved_list}{closing_line}{fence}\n\n"
        "## CARSI official public resource directory (reference only)\n\n"
        f"Source: {CARSI_DIRECTORY_URL}\n\n"
        f"{fence}json\n{directory}\n{fence}\n"
    )
    return AIMappingPack(filename=AI_MAPPING_PACK_FILENAME, markdown=markdown)


def generate_ai_mapping_pack(
    saved_list_path: Path, *, transport: httpx.BaseTransport | None = None,
) -> AIMappingPack:
    """Read only validated disk configuration, fetch fresh official data, generate."""
    path = saved_list_path.absolute()
    try:
        identity = _source_identity(path)
        if path.stat().st_size > MAX_MARKDOWN_BYTES:
            raise MappingPackError("Saved list.md exceeds 1 MiB")
        snapshot = _read_snapshot(path, source=SettingsIssueSource.JOURNAL_DATA)
        if snapshot.issue or snapshot.contents is None:
            raise MappingPackError("Saved list.md cannot be safely read as UTF-8")
        parse_settings_list_text(snapshot.contents, path=path)
        if _source_identity(path) != identity:
            raise MappingPackError("Saved list.md changed during reading")
    except (OSError, ConfigurationError, ContentChangedError) as error:
        raise MappingPackError("Saved list.md is missing, unsafe or not a valid current configuration") from error
    resources = fetch_carsi_directory(transport=transport)
    current = _read_snapshot(path, source=SettingsIssueSource.JOURNAL_DATA)
    try:
        unchanged = _source_identity(path) == identity
    except (OSError, ContentChangedError):
        unchanged = False
    if (not unchanged or current.issue or current.contents != snapshot.contents
            or current.revision != snapshot.revision):
        raise MappingPackError("Saved list.md changed during CARSI retrieval; retry export")
    return build_ai_mapping_pack(snapshot.contents, resources)
