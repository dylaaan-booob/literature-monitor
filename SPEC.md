# Literature Monitor — Current Technical Specification

This specification describes the current application and its safety contracts. It is not a release log. The
implementation and active tests determine observable behavior; changes to the contracts below require
corresponding implementation and verification. For user operations see [README](README.md); for contributor
rules see [AGENTS](AGENTS.md); for Connector provenance see [connector/PROVENANCE.md](connector/PROVENANCE.md).

## 1. System and Ownership

Literature Monitor is a local-first journal monitor and review workflow. The supported path is:

1. Configure Journals, publication months and a lexical expression.
2. Run OpenAlex/Crossref discovery; capture new DOI Papers into an Obsidian-compatible Workspace.
3. Review candidates in Inbox; Keep or Reject explicitly.
4. Optionally Enrich an individual Paper's missing bibliographic fields.
5. From Kept, optionally prepare institutional CARSI access and import eligible Papers into Zotero through native Connector saving.

There is no unattended scheduler, publication watermark, cross-run metadata synchronization, persistent review
database, automatic PDF acquisition, Zotero library mirror or server-managed authentication.

Data authority is deliberately divided:

| Authority | Durable content | Owner |
| --- | --- | --- |
| `monitor.yaml` | Monitor configuration, publication months, local paths, institution and Kept workflow settings | User, Settings safe writer |
| `list.md` | Journals, direct Publishers, Access Services, Group membership, manual URLs and unrelated Markdown sections | User, managed-section safe writer |
| `workspace/Papers/*.md` | DOI-bound Paper metadata, UUID, workflow status, user content | Paper Markdown; guarded narrow writers |
| `workspace/Authors/*.md` | Materialized Author identity and notes | Create-only Run/Enrich |
| `workspace/Inbox.base` | Optional Obsidian Bases presentation | Create only if absent |
| `journal-assets/` | Local Journal covers and manifest beside the list file | Cover operations; independent of list and Workspace |
| Provider SQLite state, latest-run snapshot | Reconstructible evidence and diagnostic state | Application; never Paper authority |
| Web/Connector coordinators | Current Run, Enrichment, Preparation, Import, windows and authorization | Process memory, not a durable workflow queue |
| Zotero Library and Chrome profiles | Bibliographic items, attachments, login/session state | External applications and the user |

Paper Markdown persists decisions across application restarts. Runtime sessions, browser tasks and diagnostic
snapshots cannot substitute for it.

## 2. Architecture and Module Boundaries

`src/literature_monitor/application/` owns Monitor, Settings, Workspace, decisions, imports, enrichment,
coverage and related application operations. The core `models.py`, `identifiers.py`, `canonicalize.py`,
`keywords.py`, `openalex.py`, `crossref.py`, `materialize.py`, `markdown_state.py` and `safe_write.py` provide
domain, Provider and filesystem primitives.

`web/app.py` composes one local FastAPI application. `web/ui_next.py` and `web/settings_api.py` adapt services
to the five-page frontend; `web/run_coordinator.py`, `web/kept_workflow.py`, `web/preparation_coordinator.py`,
`web/capture_coordinator.py` and `web/paper_enrichment.py` retain transient execution authority. The UI in
`web/static/ui_next/` has no independent business persistence. All routes use the same process and Workspace.

The command `literature-monitor gui --config <path>` starts Uvicorn on `127.0.0.1:8000` with one worker. CLI
subcommands for Run, validation, discovery and export have been retired. The application can start with missing
or malformed configuration so Settings can show errors; that does not authorize Run or a malformed Save.

The separately reconstructed Chrome/MV3 Connector derives from pinned Zotero Connector AGPLv3 source; the MIT
Python package does not bundle, import or rewrite its source. Connector uses upstream translator and native
Zotero save mechanisms; Python supplies localhost task coordination and guarded Paper writes. See
[connector/README](connector/README.md) and [provenance](connector/PROVENANCE.md). Browser control, native
Parent save, CARSI navigation and Google image download belong to the Connector, not Python's Provider retrieval
pipeline.

## 3. Configuration, Settings and Persistence

### 3.1 Configuration files

Relative `venue_whitelist` and `output_dir` paths resolve against the `monitor.yaml` directory. Monitor fields
include `name`, `venue_whitelist` (normally `list.md`), `keyword_expression`, `output_dir`, `from_month`,
`to_month` and `log_level`. The month fields are strict `YYYY-MM` values; `from_date`, `to_date` and
`window_days` are unsupported. `institution` stores optional public school/IdP settings and default-off
`automatic_attribute_release_consent`. `kept_workflow` stores `auto_import_after_preparation` (default false),
`transition_delay_seconds` (default 3) and optional `display_name`. Credentials, cookies and federation sessions
must not enter either configuration file.

`list.md` has three managed Markdown tables:

~~~markdown
## Journals
| Journal | ISSN-L | Publisher ID | Group |
| --- | --- | --- | --- |

## Publishers
| Publisher | OpenAlex ID | Publisher URL | Access Service ID |
| --- | --- | --- | --- |

## Access Services
| Service ID | Service | Access URL | Access Method |
| --- | --- | --- | --- |
~~~

Only Journals drive discovery; `Conferences`, other headings and user-authored text are preserved by ordinary
managed Save, not interpreted as discovery scope. The saved Journal order controls grouping and CARSI Service
order when applicable. A Group is stored on each Journal row; an empty draft-only Group has no durable
standalone row.

### 3.2 Journal, Publisher and Service identities

Each Journal has exactly one checksum-valid normalized configured ISSN-L. Its canonical name and direct
Publisher ID are machine-managed and must not be silently replaced by imports, user-facing display names or
Provider suggestions. Its Group is user-managed. Each Publisher has a direct OpenAlex Publisher ID and canonical
name, a user-maintained `Publisher URL` manual shortcut and an optional `Access Service ID` foreign key. Its
saved URL must not be overwritten by Provider refresh, including when deliberately blank. The Publisher
association must not be inferred from name, host, DOI prefix or imprint parent.

Each Access Service has an immutable opaque ID, normally `svc_` plus 32 lowercase hexadecimal digits, and
mutable user-managed name, URL and access method `carsi` or `manual`. Older three-column Access Services tables
default to `carsi`. Publisher-to-Service is zero-or-one; a Service can have many Publishers or none. Group
assignment and Service assignment are independent. Missing Service mapping never blocks a normal Run or proves
which Service hosts a DOI.

Validate normalized unique IDs, names and foreign keys, direct Publisher relationships, machine-field
compatibility and public HTTP(S) URL safety. During full Markdown Import, a newly introduced Publisher may
reference only an Access Service ID that already existed in the saved file before Preview, or remain Unassigned;
a Service added by the same upload is not an eligible initial default for that new Publisher. Reject userinfo,
localhost/non-public IP and unsafe schemes; URL storage does not perform DNS, login or entitlement verification.
Changes which remove the last Journal of a Publisher can remove its Publisher URL and mapping; surface the
cascade before confirmed Save. Service deletion unassigns related Publishers and requires explicit confirmation.

### 3.3 Shared draft and safe writes

Journals, Access and Settings are views of one shared Settings draft. The overview reflects pending edits.
Organize supports Journal search, Groups, batch selection/assignment, and Service/Publisher mapping with both
drag and non-drag controls. Switching views or refreshing projections does not implicitly Save, Discard or
advance source revisions.

Ordinary Save validates both the draft and the original revisions of `list.md` and `monitor.yaml`, checks safe
paths and symlink/file identity, and writes only content that changed. Managed Markdown edits preserve unowned
sections and human text; pure Markdown edits never rewrite the monitor file, and monitor-only changes never
rewrite the list. No-op Save writes nothing. Compare-before-replace/CAS, atomic replacement and readback protect
every written target. If both files need changes, list is written before monitor; a second-file failure is
reported as actual partial success, without pretending an atomic two-file transaction. Concurrent edits cause a
revision conflict; there is no silent merge.

Malformed source files are surfaced as issues and require manual repair followed by Reload. Opening the UI or
loading Settings performs no implicit repair, upgrade or writes.

### 3.4 Full Markdown import, export and upgrade

The only configuration import is a complete UTF-8 `.md` replacement for `list.md`.
`Import → Preview Changes → Confirm Import` validates the whole file, managed relationships, new machine
identities, safe paths, source digest and unchanged target revision before one atomic replacement. Preview and
Cancel write nothing. Unlike ordinary Save, confirmed import replaces Conferences, unknown sections and all
other text exactly as reviewed; it never merges just one table. The AI Mapping Pack marker is explicitly
rejected.

Dirty draft content requires explicit Save (then a fresh Preview) or Discard before importing. Import itself
never saves independent Monitor/Institution edits. Export downloads the complete saved `list.md`, excluding
draft and `monitor.yaml`, and applies its own dirty-draft guard. Reload explicitly discards unsaved draft
values.

Legacy pre-Access-Service list schema has a separate explicit one-time Upgrade Preview/Confirm. The migration
operates on the actual current file, preserves IDs, names, Groups, Publisher manual URLs and unowned sections,
adds missing mapping columns and an empty Service table, and refuses stale revisions or unsafe paths. No
background upgrade is allowed; a valid legacy list can be read for bounded Run compatibility before upgrade.
Damaged formats are not silently repaired.

## 4. Discovery, Journal Matching and Run

### 4.1 Run scope and Provider policy

The user saves inclusive start/end natural months in Settings, then explicitly starts Run from Inbox. Equal
months mean one month. Run does not use sliding day windows, automatic backfill or schedule. Reject invalid
`keyword_expression`, including standalone `NOT`, before Provider or Workspace work.

OpenAlex is the primary journal-first discovery Provider; Crossref supplies independent journal discovery and
bibliographic metadata. Semantic Scholar and publisher scraping are absent from production retrieval. Resolve
configured ISSN-L against OpenAlex Source IDs; Source aliases and `issn_l` are transient Provider evidence, not
authority to rewrite configured ISSN-L. Verify aliases individually; ambiguous evidence must not expand trusted
Journal membership. Crossref valid ISSNs may corroborate or broaden bounded queries but cannot redefine
configuration identity. Journal name, abbreviation, title and Publisher hostname have no identity or veto
authority.

Discovery branches can execute in parallel. Each Run establishes live journal/month membership rather than
treating cache rows as evidence of new membership. Source-resolution failure falls back conservatively to the
configured ISSN-L. Isolate request, journal and Provider errors; partial successful records remain usable and
produce truthful coverage/issue reporting. Source failure is not a legitimate negative match. Exclude explicit
Crossref `journal-issue` works; absence or disagreement about type is not sufficient to exclude. Publication
stage and `is_published` are not filters.

### 4.2 DOI-first early matching and evidence selection

Before full metadata retrieval, read durable Paper identities from existing Markdown. A reliable saved
normalized DOI is Already Saved and skips further metadata matching, canonicalization and Paper/Author writing
regardless of status. Conflicted or unreadable identities fail closed and are reported separately; they cannot
silently authorize a skip or overwrite.

For a new DOI, first evaluate available trusted OpenAlex Title/Abstract. A complete search projection that does
not match excludes the DOI without retrieving Crossref just to search again. If OpenAlex search fields are
missing, Crossref may fill those fields on demand; Crossref-only DOIs use its own metadata. Boolean operands may
be satisfied by retained Provider projections, but one lexical operand must match inside one searchable unit. A
metadata request failure is an issue, not evidence of nonmembership or nonmatch. Once a DOI is excluded, later
aliases and supplements cannot revive it.

Crossref full records are retrieved only when required for matching or new-Paper metadata, with
revision-validated transient reuse and shared work within an invocation. OpenAlex thin Works do not require
location hydration. Successful match requires a usable DOI, Title and Journal; Authors, Abstract and date can be
absent. A matched DOI can map to multiple configured Journals; persist unique matching ISSN-L values without
changing their configured identities.

When Crossref full metadata is obtained, its Title and ordered Authors take precedence over OpenAlex fallback.
For Abstract, select the longer cleaned available Provider text, with Crossref winning ties; do not force a
request merely to compare lengths. OpenAlex author display prefers raw author names and may supplement
compatible stable IDs. Do not fabricate authors or metadata. Ordinary Provider field differences alone do not
create spurious identity warnings.

Publication date uses truthful ISO precision `YYYY`, `YYYY-MM` or `YYYY-MM-DD`. Complete evidence uses print,
online, published, then issued source priority with established Provider fallback. When no complete date exists,
Crossref partial dates follow the same source order, selecting higher precision and earliest value within one
source. Never fill unknown month/day with `01` in Run or combine unrelated date components. Distinguish
month-scoped discovery membership from a Paper's actual bibliographic date.

### 4.3 Lexical expression

The matcher uses Unicode NFKC/casefold, whitespace normalization and SQLite FTS5 `unicode61` tokenization. It
supports terms, quoted ordered phrases, trailing-star token prefixes of at least three normalized lexical
characters (e.g. `statist*`), quoted unordered proximity with distance 0–50 (e.g. `"causal inference"~1`),
`AND`, `OR` and parentheses. Standalone `NOT` is forbidden; quoted `"not"` is ordinary text. No
leading/middle/multiple-star wildcard, `?`, stemming, synonym expansion, fuzzy search, BM25 ranking or arbitrary
FTS query syntax.

A term, phrase, prefix or proximity match is contained within one Title or Abstract field
(or one explicitly supplied Author Keyword, when supported by the lexical engine). Distinct operands may match
different units. Provider topics and fields of study are not searchable. Run currently supplies Title/Abstract,
not Provider-derived Author Keywords. Temporary FTS indexes are in memory; missing FTS5 causes a visible error,
not a silent alternative matcher.

### 4.4 Results and reconstructible state

`New Papers` counts actual successful file creations. `Already Saved` counts reliable distinct saved DOIs
encountered in the month's discovered venues before keyword matching. Coverage is per retrieval path; metadata
retrieval outcomes, per-source progress/activity, elapsed time and inactivity notices have separate meaning.
Provider inactivity is advisory, not automatic termination. The current snapshot and progress are process-local
where applicable; persisted last-run coverage and Provider state are disposable diagnostics/retrieval
optimizations, not a durable queue or Paper authority. Cache errors cannot rewrite Paper state. No watermark,
mandatory run history, persistent DOI index or cross-run enrichment is created.

Environment options `OPENALEX_API_KEY` and `CROSSREF_MAILTO` are Provider settings, not school credentials.

## 5. Paper Identity, Canonicalization and Markdown

### 5.1 Identity and provenance

Normalized DOI is the sole canonical work identity. Distinct DOIs never merge on title, date, author or venue
similarity. A local Paper UUID identifies the durable Workspace file and guards subsequent decisions; it is not
a second bibliographic deduplication key. A current Crossref-only Paper can canonicalize without an OpenAlex
record. Provider-neutral evidence structures may still read or preserve historical identifiers and provenance
from retired Providers, without making them active discovery inputs.

Combine retained matching DOI evidence conservatively into one Paper. Metadata Source records identify Provider,
record ID and retrieval time; external IDs may include OpenAlex, Crossref and other historical identifiers. A
Provider's display name, version hint, URL or unverified journal alias never overrides DOI identity. No
persistent version/manifestation graph, separate preferred-version lifecycle or automatic multiple-DOI
clustering exists.

### 5.2 Durable Paper schema and human ownership

New Papers are Markdown with YAML frontmatter and bibliographic body. Principal frontmatter includes a local
`id` UUID, `type`, `title`, `journal`, optional quoted `publication_date`, normalized DOI and external
identifiers, `authors` (Obsidian links or empty array), optional `author_keywords`, `journal_issns`, `status`,
`discovered_at` and provenance. Body includes Abstract/Versions/Sources/Notes where represented by current
renderer. Unknown keys, human notes and user-authored sections remain meaningful user data; older frontmatter is
not rewritten merely to conform to a newer renderer.

Statuses are `candidate`, `rejected`, `kept` and `exported`. The former `in_zotero` state, custom Zotero item
key, My Library enumeration and active `export_attempt` reservations are not part of current normal Import.
Historical `export_attempt` content, if present, is preserved without becoming a new retry lock.

Run is capture-only: it creates new DOI Papers and necessary Author notes, and **never edits bytes of an
existing Paper or Author**, including machine fields, Sources, Journal memberships, statuses or notes.
Create-only writes and identity-index checks must preserve existing files across races and damaged/conflicting
records. Existing Authors can be reused on compatible stable identity without metadata enrichment. New Papers
may have `authors: []` and omit empty Author Keywords. Author notes with conflicts or ambiguous stable IDs must
not be merged by name guesswork.

### 5.3 Decisions and Workspace projection

Keep/Reject are explicit, guarded transitions from candidate; they persist immediately as narrow Paper writes
with identity/status/content checks and preserve unrelated bytes. `kept` and `exported` reflect separate
subsequent workflow authority, never Run side effects. Malformed Paper Markdown, duplicate UUID/DOI,
symlink/path anomalies or inconsistent identity block unsafe actions, without automatic repair. A failed
decision must not be reported as saved.

Workspace groups current Papers by configured Journal attribution using valid `journal_issns`. Exactly one
configured match maps to that Journal; absent, invalid or multiple mapping belongs to Unmapped journals. Group
order and Ungrouped follow saved configuration. A limited historical exact-name fallback is allowed only where
implemented; no fuzzy identification or Paper rewrite occurs during grouping. Existing `Inbox.base`
customization is user-owned, with only a create-if-absent default. Obsidian views are optional; the Web Inbox
and Kept work without an Obsidian plugin.

## 6. Inbox, Paper Detail and Enrichment

Inbox shows current candidates, DOI-linked Paper Detail, Keep/Reject and an explicit Run; Kept shows retained or
imported results. Paper Detail exposes useful bibliographic fields, preserving technical external IDs, Sources
and Author Keywords in Markdown without cluttering ordinary presentation. Selection, list scroll, run progress
and header notices are UI state, not durable Paper modifications.

When Abstract, Authors or date are missing, explicit `Enrich` requests one matching DOI literature Item via
Connector browser-only, read-only translator processing; Zotero Desktop is not needed. A `YYYY-MM` date also
qualifies for replacement **only by a valid complete `YYYY-MM-DD` result**. Complete dates never change, and a
present year-only date is not eligible for date replacement. Missing dates accept a valid Translator date at its
actual year/month/day precision. An enrichment can save eligible Abstract and Authors even when no usable date
candidate exists. Do not invent date components, parse incomplete raw strings as confirmed dates or rewrite
already present Abstract/Authors.

Enrich immediately saves eligible validated fields after retrieval; there is no Preview or separate Save.
Translate returns DOI, bibliographic Item, original abstract/date, parsed components and creators; the server
checks the Paper's original DOI, UUID, status/content identity and target fields again before one narrow atomic
Markdown write. Accepted partial results are valid. Author creation uses creator roles and compatible stable
identity; existing Author files are never rewritten. A new Author file may remain if the final Paper CAS fails;
report the orphaned path explicitly, without destructive rollback or a fabricated successful save. Cancel or
leaving Detail cancels retrieval; a completed write remains. The Connector closes only its unchanged matching
task page after the backend accepts a Completed receipt; user-navigated and uncertain pages remain. Within one
Web process, a Paper with one successful Enrichment save is not offered Enrich again until service restart.
There is no periodic auto-enrichment, persistent enrichment job or native Zotero write in this operation.

## 7. Kept Workflow and Native Zotero Import

### 7.1 Modes and durable status

Eligible Kept Papers must have a reliable DOI and live in the configured Workspace, including Papers captured by
earlier Runs. The two supported Kept entry choices are `PREPARE & IMPORT` and `IMPORT ONLY`. There is **no
separate Preparation page or action**. Import Only skips CARSI Preparation. Both use the same serial native
Zotero Connector import service and single-use Web authorization. `Open DOI` is a non-mutating manual
alternative.

A new explicit invocation snapshots eligible targets and starts transient coordination. One native bibliographic
parent save is in flight at a time; an active task cannot be duplicated by another click, worker or competing
browser operation. There is no persisted batch queue, new attempt marker or automatic post-restart resume.
Failure, cancellation, timeout or missing confirmation leaves a Paper `kept` unless an attributable native
parent acceptance has been safely committed.

### 7.2 Parent/PDF evidence and stopping

The upstream translator requires a usable single-item DOI match. The Connector restricts automatic task
navigation to exact HTTPS `doi.org`, verifies Desktop connectivity before dispatch, uses one-shot native-save
grants and correlates command, task tab/document, native invocation/session, DOI and parent client ID. Native
`saveItems` HTTP 201 acceptance with an empty response, or one exactly matching parent client ID where echoed,
may confirm the parent. Distinguish unequivocal no-dispatch failure from post-dispatch uncertainty;
missing/malformed/mismatching replies never justify a retry within the invocation or `exported`. On attributable
native parent success, safely update only the matching `kept → exported` Paper, preserving unrelated text. A
failed local Paper write is not silently reported as durable Export.

Parent acceptance does **not** verify a PDF. PDF `unverified`, a specifically observed PDF failure and actual
parent acceptance are separate outcomes; links, Snapshots and ordinary attachment progress are not PDF
confirmation. Upstream attachment processing may continue and settle after parent saving. No custom Python PDF
download, staging/upload, attachment-management or library synchronization is authorized. Uncertain parent
outcome warrants inspection in Zotero before another explicit import: duplicate items are possible. Do not
enumerate My Library or assume reliable attachment state from unsupported APIs.

Preparation results and Manual Service links are reviewed before Import when necessary. Automatic continuation
applies only following a deliberate PREPARE & IMPORT start, with configured transition delay, and only where
preparation outcomes permit; it never substitutes for user-completed login/MFA. Continue is an explicit
alternative.

Completing, failing or ending any Kept workflow stays in/returns to **Kept**, even when no Papers remain. An
End/Esc during Import enters `stopping`: block further Paper dispatch but allow an escaped native save to settle
and keep its attributable result. Revoke not-yet-escaped grants under the coordinator lock; do not reverse an
escaped save or release browser/Workspace exclusion prematurely. All other waiting tasks are cancelled with
identity/generation checks. Browser task state survives page refresh within a running process, but not a Web
service restart; user-visible success cannot depend on animation timing or navigation.

## 8. Institutional Access, CARSI and AI Mapping

### 8.1 Service selection and safe access meaning

PREPARE & IMPORT snapshots Services from **all current kept Papers**, using Paper Journal attribution →
configured Journal direct Publisher ID → Publisher `access_service_id`. Deduplicate by stable Service ID in
`list.md` order. Only related `carsi` Services enter the frozen browser queue. Related `manual` Services retain
saved-URL links and no automatic execution statistics. Unmapped, unassigned and unrelated Services create no
task. A CARSI Service can have a blank saved Access URL and still use official Portal navigation.

Publisher defaults and official catalog names are organizational inputs. They cannot establish the actual DOI
landing Service/SP, login/session, entitlement, full-text rights or PDF availability. Do not guess WAYFless
routes, institution-specific SP aliases or login forms. Preparation does not authorize Zotero saving or alter
any Paper.

### 8.2 CARSI execution and timeout

Automated Preparation requires the saved institution IdP entity identifier and compatible Connector capability.
Start in a normal Chrome task window with the official CARSI Shibboleth entry targeting `/wxds`. Never navigate
directly to an unverified IdP URL. The user completes login, MFA and challenges; a verified official directory
return and owned Portal document, rather than observed credentials or a redirect alone, permits continued
automation. The human login wait has a bounded 600-second limit and distinct timeout authority.

For each Service, ensure the official `onlyMySchool` checkbox is **unchecked**; if checked, click once and
verify, if already unchecked leave it alone. Fail safely if its state or other required controls cannot be
verified. Search using the trimmed portion following the last `&` in the saved name (when present), but match
results **only by the entire saved Service name**, using trimmed direct text of `.item a .flex .t1`, excluding
nested badges. Poll for at most 15 seconds; do not require a changed/stable result list. Exactly one matching
result is required; duplicate or missing matches, unverifiable filters and unsupported pages yield Needs
attention, not invented absence.

Extract and validate the unique official `resourceDetail.php` link, bind it to the current Portal document and
create an inactive Detail tab in the owned task window. Validate the official Visit control and task authority
before each operation. Opened requires one verified Visit click, an attributable target opening within ten
seconds and a consumed nonce; it claims navigation only. Possible outcomes are **Opened**, **Needs attention**
and **Failed**, never authenticated, access granted or PDF saved.

An ordinary Service has a 90-second deadline starting at its **claim**, not while queued behind an earlier
Executor. Ordinary Service timeout records one Needs attention result, invalidates its nonce and advances to the
next Service; the last such result still completes the queue. Human-login expiry, Cancel, disconnection or
whole-task authority loss terminate the remaining queue. A revoked Executor cannot retain navigation authority;
its serial execution slot is released only after already-started browser calls return, not merely when listeners
are removed. Late receipts never revive ownership or replace a timed-out outcome.

**Preparation never closes tabs or windows**, including after success, errors, Cancel or timeout. Preserve
Portal, Detail, institutional login and database target pages for manual inspection/closure. Local cleanup
releases only listeners, timers and Executor references. Preparation uses the same browser-task exclusion as
Enrichment, Import and cover search.

### 8.3 Optional Shibboleth consent and manual fallback

`automatic_attribute_release_consent` is opt-in, default off. Only a recognized same-origin native Shibboleth
consent form in an active CARSI Portal login wait may select Global Consent and proceed once for that owned
document. Other IdPs, passwords, login buttons, MFA, CAPTCHA and attribute payloads remain user-controlled.
School-stored Global Consent may have effects outside this task and turning off the option does not revoke
earlier school consent. Consent automation never establishes login success, ownership of a new SP or
entitlement. Incomplete/failed CARSI outcomes and Manual Services retain explicit saved-URL access and a
user-controlled Continue step.

### 8.4 AI Mapping Pack

Settings can export `literature-monitor-ai-mapping-pack.md`, marked with document type `ai-mapping-pack/v1`.
Each export reads the **entire saved list.md** and freshly retrieves the public CARSI catalog from
`https://www.carsi.edu.cn/SPlist.html`; failure aborts without a partial file or configuration write. The Pack
contains fixed AI editing instructions and official `production`, `vendor_name` and `url` records. It excludes
`monitor.yaml`, browser state and credentials, but the list's own human-authored text may be sensitive; inspect
before externally sharing.

The Pack is an **external editing aid**, never directly importable `list.md`. The AI is instructed to preserve
current Journal and Publisher machine fields, Groups, Publisher URLs, other Markdown sections and existing
Service IDs/mappings by default; only evidence-backed CARSI Service additions and existing Publisher Service
foreign keys may be proposed. Preserve manual methods and existing non-CARSI Services; uncertain membership
stays Unassigned. Any external research about article/PDF access is advisory and must not silently revise the
importable configuration. The user reviews the complete result, then uses ordinary full Markdown
Preview/Confirm; the application does not invoke AI, scrape publishers or auto-import mappings.

## 9. Connector Protocol and Browser Tasks

The Connector bridge is fixed to loopback `http://127.0.0.1:8000`. Capability-bearing heartbeat gates task
classes including native capture, `paper-enrichment/v2`, `journal-covers/v2`, optional Shibboleth consent and
background task windows. An old/missing capability never authorizes newer command fields or side effects. Claim,
dispatch/authorize, receipt and finish endpoints bind ephemeral task identity, generation/request ID, DOI or
Service/Paper target, one-shot grants and timeouts. No browser-storage persistence of active commands or
credentials is permitted. Arbitrary page callbacks, old generations or late receipts cannot confirm current
work.

Browser tasks run in normal, non-incognito Chrome windows created unfocused. Each Enrich has its own task
window; PREPARE & IMPORT shares a task window between Preparation and serial Import while it exists; Import Only
may keep a verified blank anchor. Import task tabs are inactive. Closing a window can cause a later owned window
to be created, but does not grant any stale task authority. Optional `window_context` associates one active
workflow only; it is never a save grant or persistent session record. Do not steal focus, activate an
application tab, sweep unrelated pages or automatically close Preparation pages. Enrichment/Import may close
their **unchanged matching** task page only under their own accepted cleanup rules, preserving user-navigated
pages.

The Web process has a single browser-task arbitration boundary for Connector Import, Preparation, Enrichment and
covers. Native save grants are an independent stricter authority even after browser ownership is obtained.
Cancellation, document navigation, expiration and service restart invalidate authority; an already dispatched
native call must still settle safely.

The source boundary, permissions, upstream pins, patch application, license notices and reconstruction procedure
are specified in [connector/PROVENANCE](connector/PROVENANCE.md) and
[connector/patches/README](connector/patches/README.md).

## 10. Journal Covers and Assets

Saved Journals have optional local cover assets alongside `list.md` in `journal-assets/`, not a configured Cover
URL. UI distinguishes Downloaded, Uploaded and Generated placeholder. Search & Download/Replace uses the saved
Journal name in Google Images from normal Chrome, classifies regional domains and obtains the first main-result
original HTTP(S) image URL. It does not execute embedded result code, choose later/thumbnail results, auto-rank
image relevance or automate anti-bot challenges. First-result relevance is a user inspection matter.

Up to three Journal searches run concurrently per group; after the group settles, Chrome background downloads
originals **serially in saved order**, with credentials omitted, normal redirects, a 20-second bound and
streaming 5 MiB limit. Chrome proxy/network policies apply; there is no Python DNS-pinned cover fetcher. Submit
bounded Base64 bytes with matching request/ISSN/source identity to Python for strict decode, image validation,
normalization and atomic save. No image result modifies `list.md`, draft, Papers, Authors or Zotero.

Cancel preserves completed assets; Continue resumes remaining batch targets and counters; Retry starts a new
batch for failures only; Find Missing Covers scans saved Journals without manifest entries. Progress survives
refresh while the service remains running, not restart. Reset removes local cover assets and manifest, including
uploads, without starting a search. Upload accepts common JPEG/PNG/WebP/GIF images with size and pixel limits
and stores an appropriate normalized still image. Workspace Reset and Journal deletion do not clear covers.
Unavailable results show the Generated fallback.

## 11. Web UI, API and Safety

### 11.1 Routes and screens

`GET /` serves UI Next, and `GET /ui-next` plus `GET /ui-next/` are aliases. Five top-level pages are **Inbox,
Kept, Journals, Access, Settings**. The old Jinja/HTMX UI and its template assets/routes are retired. All pages
share production services and the Settings draft. The UI supports mobile and reduced-motion presentation without
making timing, media or animations business authorities.

Principal JSON routes include:

| Purpose | Routes |
| --- | --- |
| Workspace, decisions, Run | `GET /api/ui-next/workspace`; `POST /api/ui-next/papers/{paper_id}/{keep|reject}`; `GET/POST /api/ui-next/run` |
| Settings and diagnostics | `GET /api/ui-next/settings`; `GET /api/ui-next/settings/diagnostics/{kind}`; `GET /api/ui-next/settings/reset-preview` |
| Settings actions | `POST /settings/save`, `/settings/reload`, `/settings/import/{preview|confirm}`, `/settings/export`, `/settings/export-ai-mapping-pack`, `/settings/upgrade/{preview|confirm}`, `/settings/workspace-reset` |
| Covers | `GET /settings/covers/search`; `POST /settings/covers/search`, `/settings/covers/search/{cancel|continue|retry}`, `/settings/covers/reset`, `/settings/covers/{issn_l}/upload`; `GET /settings/covers/{issn_l}/image` |
| Enrichment | `GET /api/papers/{paper_id}/enrichment/state`; `POST /api/papers/{paper_id}/enrichment/{action}`; Connector `/api/connector/enrichment/{claim|authorize|result}` |
| Kept workflow | `GET /api/kept-workflow/state`; `POST /api/kept-workflow/start`; `POST /api/kept-workflow/{continue|cancel|acknowledge}` |
| Connector capture | `POST /api/connector/heartbeat`, `/api/connector/claim`, `/api/connector/dispatch`, `/api/connector/native-save-settled`, `/api/connector/access`, `/api/connector/result`; `GET /api/connector/state` |
| Connector Preparation | `POST /api/connector/preparation/{heartbeat|claim|authorize|waiting|visit-authorize|result}` |
| Connector cover tasks | `POST /api/connector/covers/{claim|authorize|result|finish}`; `POST /api/connector/import-browser-idle` |

HTTP methods, CSRF tokens, signed target identities, current revisions and state/generation preconditions remain
mandatory; the table is a route index, not permission to call the endpoints outside their guarded workflows.
Settings JSON responses preserve actual Save/Preview result semantics. Settings can load independently of
Workspace corruption; invalid configuration does not become a fabricated healthy Workspace.

### 11.2 Recovery, filesystem safety and concurrency

Web and Connector accept loopback-only authority with trusted-host/CSRF and path validation, rather than
exposing authenticated internet endpoints. Mutable actions must recheck target identity and revision at commit
time, not merely when rendered. Filesystem operations defend against symlinks, path substitution, unsafe
ownership, duplicate IDs, damaged frontmatter, partial writes and concurrent user edits. Preserve unowned
Markdown bytes and report content conflicts; never guess that a successful browser navigation implies a
successful filesystem or Zotero write.

Workspace Reset is an explicitly confirmed **permanent deletion of the entire configured Workspace**, including
Obsidian content and other files inside it. It has no Undo or automatic recovery. A typed `RESET` confirmation,
fresh target-bound authorization and safe-root guards prevent stale preview, alias, symlink and
path-substitution deletion. Reset creates no replacement Workspace and starts no Run. It never targets the
external `list.md`, `monitor.yaml`, `journal-assets/`, Chrome profile or Zotero Library. Filesystem errors may
leave partial deletion; report that honestly. Later explicit Run may recreate default Workspace files. Reset
does not undo native Zotero saves or make uncertain Import retries safe.

An active Run, Import, Preparation, Enrichment, cover task or reset must not race in ways that defeat Workspace
mutation guards or browser ownership. Work remains process-local with finite timeouts, cancellation and
stale-result rejection. Recovery from interruption may require a new explicit action; do not silently rerun
Provider retrieval, institutional login or native save.

## 12. Testing and Maintenance Contracts

Tests and fixture work must use temporary Workspace/configuration, not the real user `monitor.yaml`, `list.md`,
`workspace/`, `src/.obsidian/`, `journal-assets/` or Zotero Library. Test failure paths as carefully as
successful paths: duplicate DOI/UUID, malformed Markdown, stale revisions, symlink substitutions, partial
Provider evidence, source alias conflicts, unrecoverable native-save uncertainty, late browser callbacks and
cancellation.

A change to Run must preserve capture-only existing-file immutability and correct already-saved accounting; a
Settings change must preserve draft revisions and whole-file versus managed-section semantics; a Kept or
Connector change must preserve single native save, Parent/PDF evidence separation and explicit access-login
ownership. CARSI and browser-mock tests do not prove institutional authentication or user-visible Chrome focus
behavior. Attribute consent must remain opt-in. Provenance and corresponding-source obligations apply to every
Connector artifact, independent of Python packaging.

Run the smallest tests relevant to a change; use broader checks only for real regression risk. Report what ran,
what was reused and what remains unverified. Protect the dirty worktree, review the actual diff, and do not
infer product success from successful syntax checking, a UI asset build or tests of mocked browser APIs. Commit,
push, tag, deployment and Release require explicit authorization; current product contracts contain no implicit
release instruction.
