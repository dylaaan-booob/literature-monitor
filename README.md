# Literature Monitor

This repository contains the journal-monitoring workflow specified in
`SPEC.md`. The workflow uses OpenAlex for primary discovery and Crossref for
secondary discovery and bibliographic evidence, matches trusted OpenAlex text first, obtains Crossref metadata on demand,
and canonicalizes retained DOI-bound evidence,
captures new DOI Papers and necessary Author Markdown, skips already saved
Papers without rewriting them, and offers native Zotero Import for kept papers.

## Release and development status

**v0.6.8 is RELEASED and is the latest published stable release.** It includes natural-month
Journal Run, OpenAlex-first early matching, explicit Paper Enrichment, optional
Shibboleth consent, Connector lifecycle cleanup and core-regression simplification.
Current Python package, Provider and Connector build identities are `0.6.8`.
The latest published stable release is
[v0.6.8: Journal Run & Paper Enrichment](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.6.8).

Run creates new DOI Papers and necessary Authors, and preserves existing Paper
and Author bytes. New Papers may have empty Authors; publication dates retain
available ISO precision. See SPEC §§46, 49 and 52. The v0.6.7 baseline's
**3070 Python tests (2 existing warnings)** remain historical evidence in §46.6;
they do not describe the current suite. Cleanup, validation and publication evidence
is recorded in [SPEC §54](SPEC.md#54-v068-core-regression-cleanup-and-release-preparation).

Paper Detail offers **Enrich → Preview → Save / Cancel** when Abstract,
publication date or Authors are missing, in every status. The rebuilt Connector advertises
`paper-enrichment/v2`; Zotero Desktop is unnecessary. Save fills only missing
fields, including partial results. Publication dates preserve Translator precision:
`2024`, `2024-05` or `2024-05-16`; missing components are never invented.
Authors are filled from Translator creators only after Save; existing Author
files are preserved. If Paper saving fails after Author creation, new Author
files remain and their paths are reported. Run remains Capture-Only. See
[SPEC §50](SPEC.md#50-paper-enrichment-authors--development-contract) and
[SPEC §51](SPEC.md#51-paper-detail-and-publication-date-precision--development-contract).

Paper Detail focuses on bibliographic content with a clickable DOI in every status.
External IDs, Sources and Author Keywords remain in existing Markdown but are hidden
from the ordinary UI. Paper List hides Discovered At; internal sorting still uses it.
The default `Inbox.base` has the same compact columns; existing custom files are preserved.

Kept offers optional **Prepare CARSI Access** for CARSI Services related to all
current Kept Papers, deduplicated in saved configuration order. **Manual Login**
Services have separate saved-URL access. Preparation searches verified official
CARSI resource pages, clicks a uniquely matched Detail/Visit control, and reports
**Opened / Needs attention / Failed**. Opened confirms a click and attributable
target opening, without claiming authentication, entitlement or PDF availability.
Users complete institutional login/MFA and close all retained browser pages.

Settings → Institution offers **Automatic Attribute Release Consent**, off by
default. During an active CARSI login wait it can select Global Consent and submit
a recognized same-origin Shibboleth form in the task's Portal tab. Global Consent
may allow this IdP to release personal attributes to subsequent services, not just
this database; the school determines the actual release. Turning it off does not
revoke previously stored school consent. Save applies to the next Preparation;
Cancel stops current actions. URN or different-origin IdPs remain manual.
Credentials, login buttons, MFA and CAPTCHA remain user-operated. See SPEC §53.
Cancel revokes further actions; **Refresh Status** updates the UI manually while
preserving disclosure choices. Zotero Import remains a separate action sharing
browser exclusion and its existing native Parent/PDF safeguards.

Settings can export an **AI Mapping Pack** containing the complete saved list.md
and fresh public CARSI catalog for external AI editing. Review it before sharing;
it is a work file and cannot be imported. Apply a reviewed complete list.md only
through the existing Markdown Import/Preview/Confirm workflow.

The user reports all manual real-browser acceptance passed; this is separate from
Codex-executed automated verification and is not independent live verification.
The Python wheel/sdist remain MIT; the separate Chrome/MV3 Connector is AGPLv3.

### Historical releases

[v0.6.5 CARSI Preparation](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.6.5)
introduced the current optional Preparation action and AI Mapping Pack.
[SPEC §44.11](SPEC.md#4411-v065-release-preparation-and-final-audit-2026-10-11)
records its 3,085 Python tests, 163 Runtime tests, 25 CARSI Executor tests and
isolated installation checks; [§44.12](SPEC.md#4412-v065-publication-verification-and-closeout-2026-10-11)
preserves the fixed tag and verified asset digests.

[v0.6.4 Journals & Access](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.6.4)
introduced the shared Settings draft, Journal Groups, Access Mapping and full
Markdown Import/Export. [SPEC §43.11](SPEC.md#4311-v064-release-verification-and-closeout-2026-10-10)
preserves its fixed tag `c90651cdba98aa1a606a880ecae8b8891aed8bca` and asset
checksums. [§43.10](SPEC.md#4310-a7-acceptance-evidence-and-a8-review-follow-up-2026-10-10-unreleased)
retains the isolated-browser evidence and its NOT_VERIFIED limits.

[v0.6.3](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.6.3)
introduced the current transient Kept Import contract.
[v0.6.2](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.6.2)
and v0.6.1 used older Settings and Zotero workflows. Their detailed instructions
remain in the [v0.6.2 README](https://github.com/dylaaan-booob/literature-monitor/blob/v0.6.2/README.md)
and [v0.6.1 README](https://github.com/dylaaan-booob/literature-monitor/blob/v0.6.1/README.md).
My Library preflight, exact-DOI `in_zotero`/`zotero_key` reconciliation and
Journal Preview/Apply belong to those historical contracts, not current operations.

[SPEC §41.14](SPEC.md#4114-a6-integration-audit-and-product-acceptance-2026-10-08)
preserves the v0.6.2 review, isolated desktop/responsive Chrome acceptance and
remaining live-test gaps; [§41.15](SPEC.md#4115-a7-release-preparation-2026-10-08)
and [§41.16](SPEC.md#4116-v062-release-and-documentation-closeout-2026-10-08)
retain preparation, test counts and publication evidence.
[§40.14](SPEC.md#4014-a7-implementation-and-validation-evidence-2026-10-07)
and [§40.16](SPEC.md#4016-v061-release-and-documentation-closeout-2026-10-07)
preserve v0.6.1 live acceptance and release history. These records establish only
their tested versions and scopes.

See [Connector instructions](connector/README.md) and
[provenance](connector/PROVENANCE.md) for the separate AGPLv3 component;
it is excluded from the MIT Python wheel/sdist and runtime imports.
[AGENTS.md](AGENTS.md#release-execution-and-verification-reuse) defines release
execution and validation reuse.

## Current Kept and Zotero workflow

A0–A9 and the Reset Guard Lifecycle Fix implement [SPEC §42](SPEC.md#42-v063-keep-driven-batch-zotero-import--federated-access-preparation--development-contract).
Local release-candidate checks, final artifacts and publication verification are
recorded in §§42.18–42.19. The user reports completed real Chrome/Zotero parent
and PDF saves, serial imports across two Access Services, dedicated Workspace
Reset → explicit Run and all 17 upstream ItemSaver tests. Those live reports are
distinct from the executed automated checks; actual PDF completion still remains
`unverified` in the application without an attributable upstream attachment
receipt. The permanent release tag points to the verified v0.6.3 package inputs.

The current UI has exactly **Inbox / Kept / Settings**. Run creates
`candidate` Papers; Keep/Reject persists immediately. Kept aggregates eligible
Papers across Runs. Its single Import action processes the set serially through
the separate Connector. Attributable parent acceptance marks `exported`; PDF
remains independently `unverified` without reliable attachment evidence.
New imports create no persistent `export_attempt` marker. Failed, interrupted
or unconfirmed parents remain `kept`; a later explicit Import creates a fresh
plan and can retry them. Historical `export_attempt` frontmatter is preserved
without becoming a blocking authority. An active batch and concurrent Connector
save still exclude overlapping dispatches. Native parent acceptance alone can
mark `exported`; translator failure, native errors, absent confirmation and
local Paper-write errors remain distinct transient batch results. Normal import
does not enumerate Zotero My Library; inspect uncertain saves before another
invocation to avoid duplicate parents. Superseded persistent-attempt tests remain
available in Git history; active tests cover the current transient import contract.

Settings accepts optional public institution/IdP identifiers, which establish
neither a session nor full-text access. The earlier §42 DOI-origin automatic
federation routes remain disabled. §44 provides the separate, optional
**Prepare CARSI Access** action. Preparation does not verify the actual DOI
landing SP, login status, access rights or PDF availability. **Open DOI** and
manual institutional access remain alternatives. Workspace Reset in Settings
is explicit **permanent deletion of the entire configured
Workspace, with no undo/recovery**. This includes `.obsidian`, custom files and
old/damaged Papers. Configuration outside the Workspace remains; deletion
failures may leave partial results. Reset does not undo Zotero saves or make
import retry safe. A later explicit Run recreates the Workspace.
See [SPEC §42.10](SPEC.md#4210-verification-and-release-gates) for required manual
acceptance and [SPEC §42.15](SPEC.md#4215-a9-final-functional-closeout-2026-10-08)
for the historical v0.6.3 evidence.

## Current retrieval architecture

Each configured Journal has exactly one normalized, valid ISSN-L owned by the
user/configuration. Journal names are presentation metadata, never identity,
duplicate-detection or Provider-matching authority. OpenAlex `issn_l` is
Provider evidence and cannot replace the configured identity.

The workflow establishes live journal/date candidate membership on every Run.
Source resolution runs once; OpenAlex and Crossref retrieval share its evidence.
Valid Source aliases can support bounded transient Crossref queries; malformed
aliases are diagnosed individually and conflicting evidence cannot expand the
query set. Source-resolution failure falls back to the configured ISSN-L only.
Crossref can corroborate venue membership, but cannot select canonical ISSN-L.
Aliases are neither configured state nor a persistent identity registry.
OpenAlex provides primary discovery through batched Source resolution and thin
Works retrieval; it performs no retained-work location hydration. Crossref
provides secondary discovery and bibliographic evidence, using live manifests
and revisions to reuse or refresh Crossref-only Provider state. Both discovery
branches can run concurrently with independent per-source Activity/ETA.
The old explicit provider-cache reuse mode is retired. No checkpoint resume,
watermark, scheduler, or run history is added. Semantic Scholar remains excluded
from supported production retrieval; provider identifiers remain metadata/provenance.

## Setup

Install [uv](https://docs.astral.sh/uv/) and synchronize the locked environment:

```bash
brew install uv
uv sync
```

## Local Web UI

During development, launch the supported local Web UI with:

```bash
uv run literature-monitor gui --config monitor.yaml
```

From an installed distribution, use:

```bash
literature-monitor gui --config monitor.yaml
```

The GUI is local-only and binds to `127.0.0.1:8000`. Missing or invalid
configuration does not block startup; Settings remains accessible. Ordinary Save
rejects damaged Journal storage and legacy ISSN/EISSN storage rather than
implicitly repairing or upgrading it. Unsafe or unreadable files remain blocked.
GUI Run uses the saved publication months. Select and Save months in Settings before running.

### Journals & Access

Settings has one shared Draft and one **Save** for Monitor, Institution,
Journals, Publishers and Access Services. **Overview** reflects that Draft,
including unsaved edits. Switching views writes nothing.

Use **Organize · Journal Groups** to create, rename and order Groups. Drag a
Journal into a Group, or select Journals and use **Assign** with the destination
selector; each row also has a Group selector. **Ungrouped** means no Group.
Deleting a nonempty Group requires confirmation and moves its Journals to Ungrouped.
Empty Groups exist only in the Draft; only Groups assigned to Journals persist.

Journal name, ISSN-L and direct Publisher ID are machine-managed. Use **Add
journal** with an ISSN-L; a new identity stays pending until Save verifies
OpenAlex metadata. To change an identity, Remove and Add. Re-adding a saved
identity retains its saved canonical metadata. Journal names and Publisher
names do not establish identity.

Use **Organize · Access Mapping** to create or edit Services and explicitly
**Assign** selected Publishers to a Service or **Unassigned**. Journal Groups
and Publisher → Service mapping are independent. Publisher name and OpenAlex ID
are machine-managed; **Publisher URL** is a user-maintained manual shortcut,
not proof of the actual DOI landing platform, login or entitlement. Provider
metadata does not overwrite a manually entered or cleared Publisher URL.

An Access Service has a stable ID, editable name, **Access URL** and **Access
Method**: **CARSI** (`carsi`) or **Manual Login** (`manual`). IDs determine
relationships; names and URLs never infer them. Deleting a Service requires
confirmation and leaves its Publishers Unassigned. Removing the last Journal
associated with a Publisher also removes that Publisher's metadata, manual URL
and mapping; Services remain. Review the cascade and use **Confirm deletions
and Save** when requested.

URL checks reject non-HTTP(S), credentials, localhost and non-public IP literals
after Unicode/IDNA normalization, without DNS or reachability requests. Public
IDN domains remain supported. Opening a reviewed URL is manual access and does
not save the Draft or establish authentication.

Ordinary edits to saved identities, Groups, mapping, URLs, keywords and dates
save offline. Adding a new Journal requires OpenAlex verification before any
write; identity or Provider failure writes neither settings file. Save checks
the original `list.md` and `monitor.yaml` revisions, so concurrent disk changes
require a fresh Draft. A list write followed by a monitor-write failure is
reported as a partial save. Legacy Access Service schema has a separate explicit
**Preview one-time Access schema Upgrade** / **Confirm Upgrade** flow;
ordinary Save is not an implicit
upgrade. Configuration editing and Journal Run use the Web UI.

### Markdown configuration

`list.md` is the sole portable saved configuration for Journals, Publishers and
Access Services. This syntax example leaves its Publisher Unassigned; it does
not assert an Access Service relationship or a verified login:

```markdown
## Journals

| Journal | ISSN-L | Publisher ID | Group |
| --- | --- | --- | --- |
| Biometrics | 0006-341X | https://openalex.org/P4310311648 | Statistics |

## Publishers

| Publisher | OpenAlex ID | Publisher URL | Access Service ID |
| --- | --- | --- | --- |
| Oxford University Press | https://openalex.org/P4310311648 | http://global.oup.com/?cc=gb | |

## Access Services

| Service ID | Service | Access URL | Access Method |
| --- | --- | --- | --- |
```

Service IDs are stable opaque identifiers (`svc_` followed by 32 lowercase
hexadecimal characters). Keep the ID when renaming a Service. Access Method is
`carsi` or `manual`; historical three-column Access Services tables default to
`carsi`. New Journal and Publisher identities still require application
verification before persistence.

Group and Publisher URL are user-maintained. Notes, Conferences, unknown
frontmatter and non-managed Markdown remain user content; ordinary managed
Save preserves unrelated file content. Only Journals drive discovery.
`monitor.yaml` independently owns Monitor and Institution settings.

### Import, Export and Reload

Open **Import complete Markdown configuration**, select a complete UTF-8
`.md` file (up to 1 MiB), then choose **Preview Changes** and **Confirm Import**.
This replaces the entire saved `list.md`, including Conferences, unknown
sections and user-authored text. Review those whole-file changes; Import does
not merge a partial Journal table and does not import `monitor.yaml`.

New Journals and Publishers require canonical OpenAlex identity verification.
A new Publisher may be Unassigned or explicitly reference a Service ID that
was already saved before Preview and remains valid in the uploaded complete
configuration. It cannot reference a Service created only in that upload.
Renaming an existing Service does not change its identity; Preview uses its
uploaded name and stable ID. Names, URLs and OpenAlex metadata never infer
mapping. Preview shows the new Publisher's official name and ID, its explicit
Service name and ID or Unassigned, and the required OpenAlex verification.

Preview and **Cancel Import** write nothing. A changed selected source or
changed target content/file identity invalidates the old confirmation; Preview
again. With an unsaved shared Draft, **Save** first and Preview again, or choose
**Discard unsaved Draft and Confirm Import** explicitly. Discard includes
Monitor and Institution edits; Import itself writes only `list.md`.

Confirm rechecks the signed Preview and revisions, verifies new identities and
uses safe-path, compare-and-swap (CAS) and atomic-write safeguards. Provider or
validation failure cannot persist unverified metadata. **Export Markdown**
downloads the saved complete configuration, including user text, rather than
the unsaved Draft. If the Draft is dirty, Save first or explicitly Discard for
the export. **Reload from Disk** explicitly discards unsaved edits.

**Export AI Mapping Pack** creates a work file for external AI editing. It is
not importable configuration. Review the AI output and import only a complete
`list.md` through the same Preview/Confirm flow.

Current Run attribution writes configured ISSN-L values into the existing
plural `journal_issns` field, deduplicated when one DOI matches several Journal
contexts. Historical Papers are not eagerly rewritten. Workspace maps a Paper
only when its valid stored ISSNs identify exactly one configured Journal;
zero/multiple matches appear under **Unmapped journals**. Non-empty sections
follow saved Group order, then Ungrouped and Unmapped journals. Group changes
update organization without rewriting Papers.

During an active run, the GUI Run panel shows `Stage N of 5`, elapsed time,
and per-source Activities. OpenAlex and Crossref Activities may coexist, each
with independent counters, rate/ETA when enough reliable samples exist, and
activity age; one Provider's progress does not overwrite the other's. Run-level
inactivity follows real activity from any source, so a quiet Provider alone
does not trigger `No recent activity`. That warning remains advisory, and
retrying, waiting, and stopped-worker states remain distinct. Runtime progress
is process-local, with snapshot-only HTMX polling every 750 ms and no durable
progress or heartbeat. Web offers only normal **Run** / **Run again** actions.

At desktop widths >760px, the Paper list and detail panes have equal,
viewport-bounded height and scroll independently. The active selection is
visible and always belongs to the current view. Clicking a Paper replaces only
the detail and preserves list scroll. A successful decision reloads disk state:
if the Paper leaves the view, selection moves to its next neighbor, then the
previous neighbor, then empty detail. Whole-workspace refreshes preserve a still
valid transient selection and restore the list's review neighborhood; failures
show refreshed state without stepping to a neighbor. At mobile widths ≤760px,
list and detail stack in normal page flow without nested pane scrolling.

A healthy Workspace has no permanent Workspace issues panel. Non-empty issues
show a compact count and a link to Settings → **Advanced & Diagnostics →
Workspace health**, where the full details appear. The primary finished Run
keeps its result summary and warning/error counts; full issues, diagnostics,
coverage, and Provider-state usage appear in **Advanced & Diagnostics → Current
run**. These details are process-local and are not restored after server restart.

Kept offers **Import to Zotero** for eligible kept Papers with normalized DOIs.
It saves serially through the separate Connector and marks `exported` only after
attributable native parent acceptance. **Open DOI** opens a normal browser page
without changing Paper state.

Paper Markdown remains the durable workflow state. The GUI does not add a
workflow/execution database, persistent run history, SSE, WebSocket, batch
queue, or another source of Paper decision state. Connector heartbeat and one
active capture attempt are transient process-local state.

### Connector prerequisites

- Run **Zotero Desktop** and the local Web UI bridge at `http://127.0.0.1:8000`.
- Download and extract the published **v0.6.8 Connector ZIP**. In Chrome,
  open `chrome://extensions/`, enable Developer Mode, choose **Load unpacked**,
  and select `chrome-mv3/`; see [Connector instructions](connector/README.md).
- Settings must show the Connector as `connected` before Import. The official
  Zotero Connector can coexist for ordinary manual saves.
- Complete institutional authentication manually. Inspect uncertain saves in
  Zotero before another explicit Import; a retry can create duplicate parents.
  PDF completion is separate from parent success and is not custom downloading.

The former **Save to Zotero** / **Check Zotero** and My Library reconciliation
instructions are historical v0.6.1/v0.6.2 behavior, linked above. Current Import
uses transient batch results and `kept → exported` under §42.

### Historical v0.5.1 live verification

Automated checks and historical scoped live evidence remain distinct under SPEC
§36.14. These results do not establish v0.5.2 live verification.
Fresh Local API inspection verified registered child `LJ6UV83V` under parent
`ZHIST6EG` with an actual non-empty regular PDF file whose bytes equal the user
Chrome download. The earlier incomplete child is retained and does not count
as PDF success. Human-verification behavior remains required whenever a genuine
challenge appears; environmental non-occurrence is recorded as not
live-exercised under §36.14's conditional boundary. Further real writes or
re-registration require explicit user authorization.

## Journal Run

Use Settings to select **Start month** and **End month**, Save, then click **Run**.
Equal months select a single natural month; a range includes every day from the
first day of the start month to the last day of the end month. An unconfigured
Settings draft preselects the current month. Saved months remain fixed.

The supported command is the local Web UI launcher:

```bash
uv run literature-monitor gui --config monitor.yaml
```

Run, validation, discovery, canonicalization, materialization and export CLI
subcommands are retired. Journal Run and workflow actions are used through the UI.

### Monitor configuration

```yaml
name: Statistical ML
venue_whitelist: list.md
keyword_expression: 'statist*'
output_dir: ./workspace
from_month: '2026-10'
to_month: '2026-10'
log_level: INFO
```

`keyword_expression`, `from_month` and `to_month` are required. Relative paths
resolve from the configuration directory. Defaults remain the configuration
filename stem for `name`, `list.md` for the whitelist, `workspace` for output,
and `INFO` for logging. Institution settings remain independent.

Old `from_date`, `to_date` and `window_days` fields are no longer accepted.
Replace them with explicit `from_month` and `to_month` values. There is no legacy
configuration migration or special Save repair path. Later month changes use
Settings and Save.

### Discovery and capture

OpenAlex and Crossref discover in parallel using each Provider's native
Publication Date. OpenAlex Primary Source IDs and trusted Crossref ISSN-L/aliases
are independent discovery paths. A failure in one Provider does not discard
usable evidence from the other. There is no automatic backfill or watermark.

Run reads transient DOI/UUID identities from Paper Markdown before obtaining
Crossref full metadata. Reliable saved DOIs skip metadata retrieval, keyword
matching and canonicalization regardless of workflow status. Duplicate or
uncertain identities are reported rather than counted as Already Saved.
Explicit Crossref `journal-issue` records are excluded across DOI paths;
missing or conflicting types do not imply exclusion. Publication Stage and
`is_published` are not candidate rules.

New DOI candidates first match the Title and Abstract returned by trusted OpenAlex
Discovery. A match with enough metadata to Capture needs no extra Crossref full
metadata. A complete OpenAlex Title/Abstract that does not match excludes the DOI,
even if Crossref might have matched: this is an intentional retrieval limitation.
Missing OpenAlex search fields may be filled on demand from Crossref; valid
OpenAlex fields retain priority for matching. Crossref-only DOIs use Crossref
metadata. Failed retrieval is reported as a failure rather than a trusted nonmatch.
Required metadata uses the current-revision cache and shared invocation results.
OpenAlex-only matches still undergo DOI/Prime/Alias identity confirmation, using
thin evidence where possible. Previously rejected final DOIs are not revived by
later aliases or metadata.

When Crossref metadata was actually obtained, Title and ordered Authors retain
field priority with OpenAlex fallback. Abstract uses the longer cleaned available
Provider text (Crossref wins equal lengths); no request is made just to compare it.
Ordinary differences between Providers do not produce metadata warnings; identity
and same-Provider evidence problems still do. OpenAlex author names prefer
`raw_author_name`; compatible stable IDs may supplement the chosen ordered names.
Capture requires DOI, Title and Journal; Authors, Abstract and date may be absent.
Empty authors are stored as `authors: []` without placeholder notes.

Publication dates retain actual precision as quoted `YYYY`, `YYYY-MM` or
`YYYY-MM-DD` strings. Complete dates preserve print → online → published → issued
precedence and existing complete Provider fallback. Only when no complete date
exists do Crossref partial dates apply in the same source order, choosing higher
precision and then the earliest value within one source. Components are never
combined or invented. This affects new captures only; saved Paper dates are not
repaired or overwritten by Run.
Run creates new Paper/Author notes only. Existing metadata, workflow, Sources,
Enrichment state and human content remain unchanged. No new Paper needing Authors
means no Author scan. Existing operation locks and safe create-only writes remain.

### Run results and cache

**New Papers** counts actual successful creations. **Already Saved** counts
reliable distinct final DOIs discovered in the selected journals/months before
keyword matching; it does not mean those Papers were matched again.

Discovery Coverage describes manifest traversal. Crossref metadata retrieval
successes/failures are reported separately in Current run. Missing Indexed
revisions do not make an otherwise complete manifest incomplete or trigger date
splitting; they also never authorize stale cache reuse. Real paging, request and
identity failures remain visible. Zero-new runs still record the result and Coverage.

Provider SQLite schema v3 and Last Run Snapshot v2 are retained; historical v1/v2
snapshots keep their original day-level dates. No persistent DOI index, month
cache, metadata history or lifecycle state is added. Empty cache changes cause
no write transaction.

### Workspace compatibility

Normal existing Workspaces can be reused. If incompatible or conflicting data
must be discarded, use the existing Settings Workspace Reset and type `RESET`.
First Save the new month configuration. Reset permanently deletes the configured
Workspace and starts no Run; a later explicit Run recreates supported outputs.
External `list.md`, `monitor.yaml`, Zotero and Chrome data are outside Reset.
There is no data migration or Undo. Older application versions may reject Papers
with empty Authors; reverting code does not restore deleted data.

### Provider environment

`OPENALEX_API_KEY` and `CROSSREF_MAILTO` remain environment settings. This change
adds no credentials, Provider, automatic Enrichment, Zotero integration, browser
session management, scheduler or synchronization engine.

## Keyword matching semantics

Journal Run applies its source-selection policy to Title and Abstract before
local matching. The shared lexical engine also supports explicitly supplied Author
Keywords; Provider Discovery currently supplies none. Each title, abstract, and individual author keyword is
an independent searchable unit. Provider topics, fields of study, and inferred
topics are not searchable.

Expressions support Terms, quoted phrases, Prefix operands such as `statist*`,
Proximity operands such as `"causal inference"~1`, `AND`, `OR`, and
parentheses. The standalone `NOT` operator (case-insensitive) is rejected in
Settings and before Run; old expressions must be edited explicitly. Quoted `"not"`
remains ordinary search text. Before matching, text is normalized with Unicode NFKC, Unicode
casefold, and whitespace normalization, then tokenized with SQLite FTS5
`unicode61` semantics. Each Term's complete token sequence must occur
contiguously within one searchable unit. For example, `strasse` matches
`Straße`. Punctuation is a tokenizer boundary, so `high-dimensional` can
match both `high-dimensional` and `high dimensional`. This is lexical token
equivalence, not fuzzy matching.

A quoted phrase must occur as one complete contiguous token sequence inside a
single searchable unit. Given a title unit `deep` and an abstract unit
`learning`, `"deep learning"` is false, while `deep AND learning` is true.
Exact Phrase matching remains ordered and adjacent.

A Prefix uses exactly one trailing `*` and matches a lexical token prefix, not
an arbitrary substring. For example, `statist*` matches `statist`,
`statistic`, `statistics`, and `statistical`, but not `biostatistics`.
After normalization the Prefix base must contain at least three lexical
characters, contain only letters or digits, and produce one lexical token.
Leading wildcards, mid-word wildcards, and multiple `*` characters are not
supported. A quoted `"statist*"` remains a Phrase, and `?` is not a wildcard.

A Proximity operand uses a quoted lexical sequence followed by an integer
distance from 0 through 50. `"causal inference"~0` means unordered adjacency:
`causal inference` and `inference causal` match, while
`causal robust inference` does not. `"causal inference"~1` permits one
additional intervening token, so `causal robust inference` matches. The
quoted content must produce at least two real `unicode61` lexical tokens.
Prefix syntax inside quoted Proximity content has no Prefix meaning.

Each Term, Phrase, Prefix, or Proximity match must be satisfied wholly inside
one searchable unit. In particular, a single Proximity operand cannot span a
title and abstract, two author keywords, or records from different providers.
Boolean operators are evaluated for the consolidated work, so operands may
combine matches from different searchable units or provider evidence. For
example, `statist* AND "causal inference"~1` may match the Prefix in one
provider title and the Proximity operand in another provider abstract.

Each filtering stage builds a transient in-memory SQLite FTS5 index and discards
it after the run. The index is rebuilt as needed, writes no persistent search
database, and is not a second durable source of truth beside Markdown. If the
current Python SQLite runtime lacks FTS5, Run reports a clear error and stops without falling back to the previous substring
matcher. Local matching does not provide synonym expansion, stemming, arbitrary
user-facing FTS5 `NEAR(...)` syntax, wildcard forms beyond the defined trailing
`*` Prefix, `?` wildcard syntax, BM25 ranking, fuzzy search, or semantic
search.

### Review candidates in Obsidian

Open `<output-dir>/Inbox.base`, review candidate papers in the **Inbox** view,
and change each Paper's `status` to `kept` or `rejected`. The Paper moves
automatically between the status-derived **Inbox**, **Kept**, **Rejected**, and
**Exported** views. Use the Web UI Kept view for native Zotero Import.

Paper Markdown remains the durable workflow state. `Inbox.base` contains only
presentation configuration, so there is no Inbox synchronization command. It
is user-customizable: columns, sorting, filters, formulas, and view layout may
all be adjusted. Literature Monitor creates `Inbox.base` only when it is
absent; an existing regular `Inbox.base` is never overwritten.

The Review Inbox uses Obsidian Bases and was validated with Obsidian Desktop
1.13.7. Bases is the only additional presentation capability used. No
community plugin, Dataview, custom CSS, or additional Python runtime dependency
is required.
