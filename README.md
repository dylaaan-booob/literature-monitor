# Literature Monitor

This repository contains the journal-monitoring workflow specified in
`SPEC.md`. The workflow uses OpenAlex for primary discovery and Crossref for
secondary discovery and bibliographic evidence, consolidates provider evidence
before local keyword filtering, canonicalizes retained DOI-bound papers,
safely updates durable Paper and Author Markdown, and exports kept papers.

## Release and development status

**v0.6.3 is RELEASED** as the current GitHub release.
[GitHub Release v0.6.3](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.6.3)
contains the verified Python wheel, sdist and separate Chrome/MV3 Connector ZIP
with corresponding AGPL source. Python package, Provider User-Agent and Connector
identities are `0.6.3`. The previous
[GitHub Release v0.6.2](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.6.2)
retains its own release artifacts and historical contract.
[SPEC §41](SPEC.md#41-v062-issn-l-venue-identity--journalspublishers-settings---development-contract)
defines the venue identity and Settings contract;
[SPEC §41.16](SPEC.md#4116-v062-release-and-documentation-closeout-2026-10-08)
records the permanent release target and asset verification.
The unchanged automatic capture contract remains in
[SPEC §40](SPEC.md#40-v061-automatic-zotero-connector-capture-current-contract).
[SPEC §40.16](SPEC.md#4016-v061-release-and-documentation-closeout-2026-10-07)
preserves v0.6.1 publication history; §§40.14 and 40.15 retain its live acceptance
and preparation evidence.

A7 recorded a successful toolbar-free automatic save in normal Chrome with
Zotero Desktop and an existing institutional session. Final authoritative
exact-DOI reconciliation confirmed the bibliographic parent and changed the
validation Paper to `in_zotero`; a PDF was observed without gating parent success.
The optional pre-existing-parent live case and 17 upstream ItemSaver tests remain
unverified, the latter because the required Puppeteer Chrome was unavailable.
These recorded §40.14 acceptance boundaries describe the historical v0.6.2 release.

The released v0.6.0 workflow uses the official Zotero Connector manually and
read-only exact-DOI reconciliation. The released v0.6.1 workflow adds automatic
Connector triggering under §40. Neither workflow requires a PDF attachment
for `in_zotero`; publisher authentication remains manual in the normal browser.
The retired Browser Companion/custom PDF acquisition path is historical only.

The v0.6.1 deliverables are the Python wheel, Python sdist and a
separate Chrome/MV3 Literature Monitor Connector ZIP with corresponding source.
The Python package
is MIT; the Connector has its own AGPLv3 license/provenance boundary and is
excluded from wheel/sdist and Python imports. See [connector/README.md](connector/README.md)
and [connector/PROVENANCE.md](connector/PROVENANCE.md) for build/install details
and provenance. Extract the ZIP and load its `chrome-mv3/` directory locally in Chrome.

[AGENTS.md](AGENTS.md#release-execution-and-verification-reuse) defines continuous
release execution and validation reuse. Detailed historical release and live
evidence remain in SPEC; historical validation does not establish success for
the current release beyond its recorded scope.

The legacy usage instructions below describe v0.6.2 under
[SPEC §41](SPEC.md#41-v062-issn-l-venue-identity--journalspublishers-settings---development-contract).
The [v0.6.1 README](https://github.com/dylaaan-booob/literature-monitor/blob/v0.6.1/README.md)
retains the previous configuration and Settings interface.
**A6_FINAL_REVIEW: PASSED; A7_RELEASE_PREPARATION: PREPARED;
A7_FINAL_RELEASE_CANDIDATE_REVIEW: PASSED.** The final review's sole top-status
finding was corrected and rechecked before publication.

A6_FIX_1 closed all five code findings (F1–F5): URL host normalization,
import identity, legacy Web import, damaged Journal-storage recovery and
reintroducing saved Journals. **CODE_FINDINGS: CLOSED; BROWSER_ACCEPTANCE:
PASSED (desktop Chrome and 550px responsive Chrome);
A6_FINAL_REVIEW: PASSED.** On 2026-10-08, real macOS Chrome acceptance
used an isolated `/settings` service and configuration, rendering 74 Journals,
20 Publishers and 19 Open links. The desktop view (1470px content width)
passed independent list scrolling and no-overflow checks; a 550px Chrome
window passed single-column layout and normal page scrolling. Real-browser
Preview/Apply/Save, URL and ISSN-L rejection, revision conflict, HTMX state
preservation and Remove → Add → Save also passed, with writes confined to
isolated test files. This responsive-window check is not a physical mobile
touch test.

An independent rerun of `tests/test_a6_fix1.py` passed 56 tests. The
previous Codex full `uv run pytest` result was 2720 passed with two existing
dependency deprecation warnings; the browser closeout did not rerun the
full suite. Physical mobile testing and a new live OpenAlex/Crossref
end-to-end Run remain unverified. See
[SPEC §41.14](SPEC.md#4114-a6-integration-audit-and-product-acceptance-2026-10-08)
for the historical BLOCKED findings and subsequent acceptance record.
A7 candidate validation and original SHA-256 values remain in
[SPEC §41.15](SPEC.md#4115-a7-release-preparation-2026-10-08).
[SPEC §41.16](SPEC.md#4116-v062-release-and-documentation-closeout-2026-10-08)
records the published artifacts and reused validation evidence.

## v0.6.3 workflow and acceptance

A0–A9 and the Reset Guard Lifecycle Fix implement [SPEC §42](SPEC.md#42-v063-keep-driven-batch-zotero-import--federated-access-preparation--development-contract).
Local release-candidate checks, final artifacts and publication verification are
recorded in §§42.18–42.19. The user reports completed real Chrome/Zotero parent
and PDF saves, serial imports across two Access Services, dedicated Workspace
Reset → explicit Run and all 17 upstream ItemSaver tests. Those live reports are
distinct from the executed automated checks; actual PDF completion still remains
`unverified` in the application without an attributable upstream attachment
receipt. The permanent release tag points to the verified v0.6.3 package inputs.

The v0.6.3 UI has exactly **Inbox / Kept / Settings**. Run creates
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
invocation to avoid duplicate parents. A0–A9 pre-simplification tests are
preserved in `tests/history/` alongside replacement regressions.

Settings accepts optional public institution/IdP identifiers, which establish
neither a session nor full-text access. There are currently **zero enabled
federation routes**; use Open DOI and manual institutional access. Workspace
Reset in Settings is explicit **permanent deletion of the entire configured
Workspace, with no undo/recovery**. This includes `.obsidian`, custom files and
old/damaged Papers. Configuration outside the Workspace remains; deletion
failures may leave partial results. Reset does not undo Zotero saves or make
import retry safe. A later explicit Run recreates the Workspace.
See [SPEC §42.10](SPEC.md#4210-verification-and-release-gates) for required manual
acceptance and [SPEC §42.15](SPEC.md#4215-a9-final-functional-closeout-2026-10-08)
for current evidence. The remaining usage sections describe released v0.6.2.

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

The GUI is local-only and binds to `127.0.0.1:8000`; v0.4.0 provides no LAN
serving mode. Missing or invalid monitor/journal configuration does not block
startup and Settings remains accessible. Save can repair a missing or damaged
Journals section from an explicit valid draft, preserving readable Publishers
and unrelated sections. Untrusted Journal metadata requires OpenAlex resolution;
unsafe or unreadable files remain blocked. GUI Run always
uses the persisted Monitor date policy and exposes no temporary date override.

Settings → **Journals & Publishers** is one editor with one **Save** for the
complete Monitor, Journals and Publishers draft. Journal name, ISSN-L and direct
Publisher ID are read-only; edit Group, **Remove** a row, or **Add** a new Journal
by ISSN-L. New rows are Pending until Save resolves canonical OpenAlex metadata.
To change identity, Remove and Add. Create, rename, delete and order Groups in
**Organize Groups**; deleting a Group moves its Journals to **Ungrouped**.
Removing and reintroducing a saved ISSN-L through Add or Import restores its
persisted canonical name and Publisher ID during offline Save, while retaining
the draft's Group and Access URL edits.

Publisher name and OpenAlex ID are read-only. **Access URL** is an optional,
visible, editable field: manually change it or clear it. **Open** is available
for an accepted URL and uses a new tab with `noopener noreferrer`. Access is a
manual institutional-login shortcut, not a login/status or entitlement check.
The URL contract rejects non-HTTP(S), credentials, localhost and non-public IP
literals after host Unicode/IDNA normalization, without DNS or reachability
requests. Public IDN domains remain supported. Publisher IDs alone define identity; names, hostnames
and corporate lineage do not merge Publishers.

Ordinary Save is offline, including Group, Access URL, keyword and date edits.
Identity-changing Save resolves all required metadata before any persistence
write. Resolution/identity failure writes neither settings file. Save compares
both original content revisions; a concurrent disk edit conflicts. A journal
write followed by a monitor-write failure is reported as a partial save.
Provider metadata does not overwrite a manually entered or cleared Access URL.
The old Web **Validate** button/route and separate Publisher projection are
removed; CLI `validate` remains available.

Desktop Journals and Publishers have separate bounded scroll areas, with Save,
Bulk Import and Group controls outside them. At widths ≤760px, the lists stack
in page flow. Automated HTMX checks cover preservation of draft values,
revisions, disclosure state and list scroll. Real desktop Chrome and 550px
responsive Chrome layout/interaction acceptance passed on 2026-10-08; physical
mobile touch behavior remains untested.

Bulk Import accepts UTF-8 CSV, TSV, pasted supported tables and Literature
Monitor `list.md`. Prefer `Journal,ISSN-L,Group` (Group is optional). **Preview**
shows changes; **Merge** keeps existing Journals and **Replace** previews
removals. **Apply to draft** changes only the unsaved draft and retains its
original revisions; Save is the sole persistence action. ISSN-L defines
Merge/Replace identity. Rows with the same ISSN-L and compatible Groups merge
despite different display hints; a genuine Group conflict blocks the whole
Apply. Existing canonical metadata is retained, and new identities obtain
canonical metadata from OpenAlex.

Legacy `Journal,ISSN/EISSN[,Group]` input requires unique Source reconciliation
and, where needed, independently confirmed ISSN-L, never name-based selection
or automatic canonical selection by Crossref. Local Preview shows legacy
identifiers, Groups and optional confirmed ISSN-L inputs. **Reconcile and Apply**
uses OpenAlex evidence for all rows and changes the draft only if every row
passes. Confirmation values are bound to the exact import source and rows;
failed reconciliation retains correctable inputs and diagnostics. Existing
legacy storage has separate confirmation inputs in its Settings migration flow: opening
Settings writes nothing; Save verifies all rows, accepts optional confirmed
ISSN-L targets, and preserves Groups. Migrate storage before adding/importing
or organizing Journals; unresolved rows block the whole Save.

`list.md` is the portable local-first source of truth for both tables:

```markdown
## Journals

| Journal | ISSN-L | Publisher ID | Group |
| --- | --- | --- | --- |
| Biometrics | 0006-341X | https://openalex.org/P4310311648 | Statistics |

## Publishers

| Publisher | OpenAlex ID | Access URL |
| --- | --- | --- |
| Oxford University Press | https://openalex.org/P4310311648 | http://global.oup.com/?cc=gb |
```

Names and Journal Publisher associations are machine-managed; Group and Access
URL are human-managed. No print/electronic alias columns or Publisher fields
are added to `monitor.yaml`. Only Journals drive discovery. Conferences and
unrelated Markdown sections retain their preservation boundary.

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

Kept Paper detail offers **Save to Zotero**, **Open DOI** and **Check Zotero** when the Paper is
`kept` and has a valid normalized DOI. The old Web Zotero export panel, Load
export, and textarea workflow remain removed; CLI `export-kept` remains
available and unchanged.

Paper Markdown remains the durable workflow state. The GUI does not add a
workflow/execution database, persistent run history, SSE, WebSocket, batch
queue, or another source of Paper decision state. Connector heartbeat and one
active capture attempt are transient process-local state.

### Zotero workflow (unchanged v0.6.1 capture protocol)

```text
Settings → Journals & Publishers → Publisher Access URL → Open
→ optionally open relevant journal/publisher sites and authenticate manually

Kept Paper → Save to Zotero
→ authoritative exact-DOI My Library preflight
→ if a unique parent exists: reconcile directly
→ only if explicitly absent: automatic Connector save in a dedicated DOI tab
→ after a confirmed or uncertain save: one final exact-DOI reconciliation
→ in_zotero only after a unique bibliographic parent is verified
```

**Open DOI** and **Check Zotero** remain the manual fallback and explicit
reconciliation paths. Automatic capture supports one active Paper at a time;
uncertain completion never automatically repeats a save.

#### Prerequisites

- **Zotero Desktop**, running with Local API access enabled.
- For automatic capture, the **Literature Monitor Connector** in Chrome:
  download and extract the published v0.6.2 ZIP, open
  `chrome://extensions/`, enable Developer Mode, choose **Load unpacked**, and
  select `chrome-mv3/`; see [Connector instructions](connector/README.md).
- Run the local Web UI bridge at `http://127.0.0.1:8000`; Settings shows the
  Connector readiness, which must be `connected` to start automatic capture.
- The official Zotero Connector can coexist for ordinary manual saves.
  Institutional authentication remains manual in the normal browser.

#### Publisher access

Use **Open** beside a Publisher Access URL in **Journals & Publishers**, then
complete institutional login manually in the normal browser. Literature Monitor
does not inspect cookies, identify your account or institution, determine
entitlement, detect login success, or track session expiry. These fields belong
to the shared Settings draft; opening a link does not save it or start a Run.

The separate read-only Publisher projection belonged to released v0.6.1.
The development branch replaces it with the durable Publisher table above;
`/settings/publisher-access` is removed. Zotero capture and exact-DOI
reconciliation remain unchanged under §40.

#### Open DOI and return behavior

**Open DOI** appears only for a `kept` Paper with a valid normalized DOI. The
server builds the DOI target and opens the normal external DOI page in a new
browser tab. Opening the DOI does not change Paper Markdown and starts no
Literature Monitor browser automation.

On the DOI/publisher page, use the official Zotero Connector manually. When you
return to Literature Monitor, the page makes at most one automatic reconciliation
attempt for that Open DOI action. There is no fixed wait, background polling,
attachment-ready check in this manual return path. If the Zotero parent is not yet
visible, the Paper remains `kept`; use **Check Zotero** later to retry explicitly.

#### Check Zotero and `in_zotero`

Automatic return reconciliation and **Check Zotero** use the same read-only
Zotero Desktop operation. Success requires:

- Zotero Desktop Local API is readable;
- My Library can be completely read and contains exactly one bibliographic
  parent whose normalized DOI exactly matches the Paper DOI;
- any existing non-null `zotero_key` already agrees with that verified parent.

On success, Literature Monitor records only:

```yaml
status: in_zotero
zotero_key: <verified parent key>
```

A PDF attachment is not required, a Snapshot is not required, and attachment
readiness is not checked. Here `in_zotero` means that the unique exact-DOI
bibliographic parent linkage has been verified. Zero matches, duplicate matches,
incomplete or unstable reads, and conflicting existing keys leave the Paper
unchanged.

#### v0.6.1 troubleshooting

| Result or symptom | Next step |
| --- | --- |
| Save to Zotero / Open DOI / Check Zotero absent | The Paper must be `kept` and have a valid DOI. |
| Connector unavailable | Load the Literature Monitor Connector in Chrome, start Zotero Desktop and use the bridge at `127.0.0.1:8000`; **Open DOI** remains available. |
| Save unconfirmed | Check the dedicated task tab and Zotero, then use **Check Zotero**; capture is not automatically repeated. |
| Zotero unavailable / Local API unreadable | Start Zotero Desktop and enable Local API access. |
| No exact DOI match | Finish the manual Zotero Connector save, then use **Check Zotero**. |
| Duplicate exact DOI parents | Resolve the duplicate bibliographic parents in My Library, then retry. |
| Existing `zotero_key` conflicts | Repair the Paper/Zotero linkage deliberately; Literature Monitor does not silently replace it. |
| Publisher Access URL empty | Enter a reviewed public URL in Settings, or continue with **Open DOI**. No login-status check is performed. |

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

## Run a persistent monitor

The normal persistent-monitor entry point is:

```bash
uv run literature-monitor run --config config.example.yaml
```

`run` reads the journal whitelist, keyword expression, output directory, date
policy, and log level from the monitor YAML. It then runs the complete production
path: OpenAlex primary discovery, Crossref secondary discovery and DOI
supplementation, evidence consolidation, local FTS5 filtering, canonicalization,
and Paper / Author / Inbox materialization.

On a TTY, `run` renders the five workflow stages, per-source Activities,
reliable counters, elapsed time, and each source's ETA when available. CLI
serializes concurrent progress callbacks; non-TTY progress remains intact plain
event lines on `stderr`. Machine/data output on `stdout` and exit codes are
unchanged.

Candidate membership remains based on the inclusive publication-date window.
Crossref `indexed` timestamps validate Crossref metadata revisions only; they
do not change candidate membership to an update-date window, recover
late-indexed records, provide watermark synchronization, or resume checkpoints.

Transport remains synchronous and pooled, with one client per Provider
execution. One OpenAlex and one Crossref discovery branch may run concurrently;
there is no async rewrite or journal/ISSN/DOI worker pool. Dependent Crossref DOI
supplementation waits for both discovery branches.

OpenAlex batches configured ISSN-L Source resolution with at most 100 identifiers
per batch, then uses multi-Source thin Works discovery. The resolved valid aliases
are transient evidence for bounded compatible Crossref queries, not new durable
Journal identities. `primary_location`, its `is_published` flag,
and Source/journal attribution remain discovery evidence. No retained-work
location requests or OpenAlex revision state are used.

OpenAlex Provider evidence eligibility is separate from `CanonicalPaper`
eligibility. A valid Work identity, trustworthy Source, and usable DOI or title
allow evidence to continue even with empty authors, missing abstract, or missing
publication date. Ordinary sparsity does not create ingestion-time missing-field
warnings. Normal Source absence is `UNAVAILABLE` with a warning, while
unrecovered remote request failures and Source/journal identity failures remain
`FAILED` with an error.

Crossref establishes current DOI membership through live manifests. Matching
current `indexed` revisions permit normalized metadata reuse; changed or new
records obtain current full metadata. DOI supplementation fills only Crossref
gaps anchored by current OpenAlex evidence. Alias lookups preserve requested-DOI
coverage while evidence and state use the resolved prime DOI.

DOI-anchored partial OpenAlex evidence can receive Crossref supplementation
even without an OpenAlex title or authors. Title-only evidence without a DOI
can proceed directly to local matching without an ingestion-time `missing_doi`
warning or a DOI-supplement request/coverage unit. After consolidation, an
eligible cluster with no usable title, author keywords, or abstract receives
an `unsearchable` warning and is excluded from matcher candidates, including for
pure `NOT` and complement expressions. This is a search-boundary warning,
not a Provider retrieval issue. Matched evidence needs a valid normalized DOI
for `CanonicalPaper`; pure no-DOI evidence is skipped with a canonicalization
`missing_doi` warning and creates no Paper. It also needs a title, journal, and
at least one author; unmet metadata eligibility produces `insufficient_metadata`.
Evidence grouping and representative selection remain in use without generic
field-by-field Provider synthesis.

Normalized valid DOI is the only supported canonical work identity: the same
DOI produces one Paper, and different DOIs remain separate regardless of title,
authors, other identifiers or relations. Raw Crossref relations remain provider
metadata and have no canonical grouping effect. Publication-date precedence is
`published-print → published-online → published → issued → representative publication_date`.
Only complete dates participate in date selection; missing month/day components
are never fabricated.

Candidate Eligibility is evaluated after current Crossref supplementation and
before evidence assembly and local matching. Exact Crossref `journal-article`
with compatible identifier evidence for the configured ISSN-L context is strong
eligible evidence; `journal-issue` is excluded
before matching. An explicit non-journal type without a target ISSN is
`INELIGIBLE`; conflicting type/venue evidence, `other`, missing type, or an
article with explicit non-target ISSNs is `SCOPE_DISPUTED` under SPEC §32.
OpenAlex `type` itself never vetoes a candidate.

When Crossref has no exact record, OpenAlex `primary_location.is_published`
provides the fallback: `UNAVAILABLE` and DOI-less evidence use true → eligible,
false → ineligible, unknown → disputed. `FAILED` uses true → eligible and
false/unknown → disputed, retaining the Provider execution error. Disputed
evidence continues to matching to protect recall. Unmatched or unsearchable
pure disputes remain diagnostics only; matched disputes warn unless the same
cluster has strong eligible evidence, in which case the dispute remains a
diagnostic. An empty mixed eligible/disputed cluster retains both its genuine
unsearchable warning and scope diagnostic.

Non-candidate exclusion and scope dispute are typed transient Run diagnostics.
CLI completion
shows their logical-group counts and context separately from warnings/errors;
Web technical details appear in Advanced & Diagnostics → Current run.
Diagnostics alone do not change exit codes or `RunOutcome` and are not written
to last-run, Paper/Author Markdown, monitor YAML, or Provider state.

After DOI grouping, compatible ordered author display forms may compare
equivalent while preserving author sequence and representative display names.
Abstract comparison uses deterministic HTML/JATS, entity, Unicode, whitespace,
punctuation, and structural-label normalization, preserving substantive text
and the selected raw abstract. These metadata comparisons do not create work
identity. No fuzzy, embedding, or LLM similarity is used.

OpenAlex Works discovery keeps cursor pagination at `per_page=100`. Crossref
uses bounded manifest retrieval, splitting ISSNs and date intervals as needed,
then traversing cursors for oversized single days, with `rows=1000`. A Crossref
client uses valid `X-Rate-Limit-Limit` and `X-Rate-Limit-Interval` response
metadata to pace later requests without rate-limit probes or count-only
requests. In the current source tree, pacing uses elapsed-aware, process-local
monotonic timing, with independent Provider-derived state for singleton DOI
and list/filter request classes. Network and processing time count toward the
interval; retry and pacing deadlines share the remaining wait instead of
adding duplicate full waits. Rate state is not persisted. HTTP 429, HTTP 5xx,
and supported timeout/transport failures receive at most three attempts. The
1-second/2-second retry fallback remains; elapsed time counts toward those
deadlines too, so actual waits can be shorter.
Endpoint-specific 404 handling is unchanged, and other HTTP 4xx responses are
not retried. Retry and pacing state remain process-local and transient.

Each production run also carries retrieval coverage for the work units it
actually executed: OpenAlex discovery per configured journal, Crossref discovery
per journal/queried ISSN, and Crossref DOI supplementation per lookup that
entered the pending set. Coverage distinguishes complete, partial, unavailable,
and failed execution without claiming that the bibliographic universe itself is
complete. CLI completion summaries and the GUI finished-run panel show compact
per-component counts. Completion reporting separately shows Provider-state
usage: Crossref metadata reused/refreshed/new. Reuse does not remove live
coverage units or reduce evidence
statistics. Usage counts are transient completion information, not durable
telemetry.

After a normal production `run` completes, the application attempts to
atomically replace one last-run snapshot. When replacement succeeds, that file
represents the latest successfully persisted completed production run and
contains its resolved date range, recorded outcome, and ordered coverage units.
It does not persist provider payloads, issue text, statistics, workflow
decisions, cursors, retry state, timestamps, or historical runs. Snapshot
persistence is diagnostic metadata only; a write failure is reported as a
warning after normal Paper materialization, does not roll back the workspace,
and leaves any previously valid snapshot in place.

The selected output directory contains:

```text
<output-dir>/
├── Inbox.base
├── Papers/
├── Authors/
└── .literature-monitor/
    ├── last-run.json
    └── provider-state.sqlite3
```

`.literature-monitor/last-run.json` is application-owned metadata for the latest
successfully persisted completed production run, not Paper/Author workflow state. Only one snapshot is retained; it is
not run history. Deleting it does not remove Papers, Authors, decisions, or
notes, and the next normal run remains valid. Materialization scanners do not
treat this hidden metadata directory as Paper data.

`.literature-monitor/provider-state.sqlite3` is application-owned,
reconstructible optimization state. It stores only normalized Crossref records,
their revisions and consumed-metadata hashes. It is not Paper/Author workflow
state, candidate membership, run history, or checkpoint/cursor state. Normal
production Run automatically uses it, but live Provider evidence always determines
current membership. Matching Crossref revisions permit metadata reuse; changed
revisions refresh live. Missing state causes an ordinary all-live Run.

Current Provider-state logical schema is **v3, Crossref-only**. Old v1/v2 state
is incompatible: Run uses all-live retrieval and only safely replaces it at the
normal persistence boundary with a successfully constructed fresh database.
There is no row-preserving migration or reuse of old rows.

Corrupt or incompatible regular state causes an all-live Run with a warning;
a successfully constructed fresh DB can safely replace that invalid regular
file. Unsafe objects such as symlinks or directories at the state path or its
metadata directory remain untouched. Provider-state persistence follows
Paper/Author materialization; a persistence failure preserves previous state
where possible and does not roll back workspace files. Provider failures remain
isolated so successful evidence from other requests or Providers stays usable.

For v0.4.2 compatibility, an old `provider-cache.json` may remain on disk
untouched. Neither v0.4.3 nor the current source tree reads, writes, deletes,
migrates, or modifies it. It has
no authority over current execution and needs no migration. The deprecated
`run --reuse-provider-cache` compatibility flag expired after v0.5.0 and is
removed in released v0.5.1; argparse now rejects it as an unknown option.
Normal Run continues to reuse revision-validated Provider state automatically.

New last-run snapshots remain schema v2 and write `reused_units: []`. Valid
historical v1/v2 snapshots remain readable, and `last-run` may still display
historical v2 cache-reuse identities and recorded outcomes without
reinterpretation under the current warning/error policy. Current Provider-state usage counts are
not persisted into last-run.

Paper Markdown remains the durable workflow state: UUIDs, review status, human
notes, unknown human-owned frontmatter, and unmanaged sections survive reruns
according to current-schema materialization rules. Sources remain provenance.
The retired pre-v0.5.2 schema used `versions`, `preferred_version` and managed
`## Versions`; current Papers have none of these. Old-schema frontmatter is
explicitly rejected, without migration or repair. `Inbox.base` is presentation
only. It is created when missing and an existing customized file is preserved.

Read the snapshot without contacting providers or modifying the workspace:

```bash
uv run literature-monitor last-run --config monitor.yaml
```

A valid snapshot prints its resolved date range, recorded outcome, and the same
compact per-component coverage counts used by run summaries. Missing or invalid
snapshot data exits `1`; invalid monitor configuration exits `2`.

**last-run snapshot ≠ Provider state ≠ checkpoint.** `last-run.json` alone
never authorizes request skipping, changes the discovery window, restores a
cursor, or alters canonicalization/materialization. Automatic state reuse
requires current live membership and matching revisions; it is not resume or
synchronization state.

The supported decision model is one monitor to one decision workspace. Paper
UUID stability, `candidate` / `rejected` / `kept` / `in_zotero`
decisions, human notes, and other durable Paper state are local to that
workspace. The same research work is not guaranteed to receive the same UUID in
another workspace, and Reject/Keep decisions are not inherited across monitors.
There is no global cross-monitor decision registry.

### Persistent monitor fields and defaults

A monitor accepts `name`, `venue_whitelist`, `keyword_expression`,
`output_dir`, `from_date`, `to_date`, `window_days`, and `log_level`.
`keyword_expression` is the only core field without a default and must not be
missing, null, or empty.

| Field | Default when omitted |
| --- | --- |
| `name` | monitor config filename stem |
| `venue_whitelist` | `<config-directory>/list.md` |
| `output_dir` | `<config-directory>/workspace` |
| date policy | rolling 14 days |
| `log_level` | `INFO` |

Relative `venue_whitelist` and `output_dir` paths are always resolved from
the monitor config directory, not the shell working directory. The loader does
not search parent directories or other locations for a replacement `list.md`.

For multiple monitors, use one monitor per config directory or configure a
different `output_dir` for each monitor. If two monitor YAML files live in the
same directory and both omit `output_dir`, the existing default rule resolves
both to the same `<config-directory>/workspace`. The program does not reject
that arrangement, but shared-workspace multi-monitor operation is unsupported /
undefined advanced usage and has no cross-monitor UUID or decision-semantics
guarantee.

A minimal monitor is therefore:

```yaml
keyword_expression: 'statist*'
```

This is usable only when a valid `list.md` exists beside the monitor file. Its
effective defaults are the config filename stem for `name`, `./workspace`
relative to the config file for output, a rolling 14-day window, and `INFO`
logging.

### Date policy and temporary CLI overrides

Persistent date policy supports five forms: no date fields, `window_days` only,
`from_date + to_date`, `from_date + window_days`, or
`to_date + window_days`. Date ranges are inclusive. For example, with
`window_days: 14` and today equal to 2026-09-21, the resolved range is
2026-09-08 through 2026-09-21 inclusive.

`from_date` alone, `to_date` alone, all three date fields together,
`window_days < 1`, and `from_date > to_date` are invalid.

`run` and the existing date-bearing diagnostic commands accept temporary
`--from-date`, `--to-date`, and `--window-days` overrides:

```bash
uv run literature-monitor run \
  --config monitor.yaml \
  --window-days 30

uv run literature-monitor run \
  --config monitor.yaml \
  --from-date 2026-01-01 \
  --to-date 2026-06-30
```

Once any CLI date field is supplied, the CLI fields form the complete date
policy for that invocation; missing pieces are not borrowed from the monitor
YAML. Thus config `window_days: 14` combined with CLI
`--to-date 2026-09-01` is invalid. CLI date overrides are ephemeral and are
never written back to the monitor file.

### Runtime environment and non-features

Optional provider credentials/contact information remain environment settings,
not monitor fields: `OPENALEX_API_KEY` and `CROSSREF_MAILTO`.

The workflow does not add monitor/workspace UUIDs, workspace ownership
markers, a global research-work or decision registry, last-successful-run
synchronization state, provider cursors/watermarks/checkpoints, incremental
delta / “What's New” state, scheduler or daemon durable state, notifications,
run history, or a workflow/execution database. The last-run snapshot is
observational metadata; Provider state is a reconstructible revision-validated
optimization, and every Run still establishes live membership.
Under the unchanged v0.6.1 capture protocol, Python Zotero integration remains read-only exact-DOI parent
reconciliation in My Library. The separate Literature Monitor Connector may
save automatically only after authoritative preflight confirms the parent is
absent. Custom PDF acquisition/staging, publisher-login automation and the
retired Browser Companion remain outside the current workflow.

## Validate configuration

`config.example.yaml` contains a syntax example, not a real research query.
Before making any provider request, `validate` loads the monitor and journal
whitelist, validates keyword parser syntax and date-policy form, runs the same
FTS5-dependent lexical semantic validation used by production local filtering,
and resolves the monitor's effective runtime date range. This catches
parser-valid but lexically invalid Prefix/Proximity operands, an unavailable
SQLite FTS5 backend, and date arithmetic outside the supported
`datetime.date` bounds before provider work begins.

```bash
uv run literature-monitor validate --config config.example.yaml
```

After local preflight succeeds, the command contacts OpenAlex only to resolve
every configured journal Source through the same batched resolver as production,
with up to 100 configured ISSN-L values per batch. Source kind, identity and
identifier membership checks apply, including conflicting-Source rejection.
Journal titles do not match or veto identity; each malformed alias is excluded
and diagnosed independently. OpenAlex supports anonymous Source
lookups; set `OPENALEX_API_KEY` in the environment to use an API key. Local
configuration or runtime-preflight failures exit with status `2`; OpenAlex
Source-resolution
errors exit with status `1`; warning-only and fully valid validation exit with
status `0`.

Normal Source absence yields `VALID_WITH_WARNINGS`; an unrecovered remote
Source request failure or identity-validation failure yields `SOURCE_ERRORS`.

TTY `validate` shows validation-specific progress, including reliable journal
Source-resolution counts, without presenting the five-stage Run model. Non-TTY
validation progress uses the same plain `stderr` event-line convention.

`validate` does not request OpenAlex Works, contact Crossref or Semantic
Scholar, read/write Provider state, check whether `output_dir` is writable,
create a workspace, or materialize Paper, Author, or Inbox files. OpenAlex Source
resolution is its only network/provider validation.

## Run tests

```bash
uv run pytest
```

## End-to-end validation

The default `uv run pytest` suite includes deterministic full-cycle CLI
regressions using local HTTP fixtures. The representative persistent-monitor
lifecycle enters through `run --config`, while explicit `materialize` coverage
remains for the diagnostic surface and an overlapping multi-journal
rerun. The CLI can also be used for manual smoke validation against the real
OpenAlex and Crossref providers, but those results depend on external service
availability and are not part of the deterministic default suite. Optional
credentials and contact details are supplied only through `OPENALEX_API_KEY`
and `CROSSREF_MAILTO` environment variables.

## Diagnostic and lower-level commands

`run` is the normal persistent-monitor entry point. The commands below remain
available as explicit diagnostic or lower-level surfaces for provider inspection,
search diagnostics, canonicalization diagnostics, and diagnostic materialization.

## Diagnose OpenAlex discovery

The OpenAlex discovery diagnostic resolves each configured ISSN-L,
retrieves works from the resolved OpenAlex Source in an inclusive date window,
and writes normalized records as NDJSON to stdout. Logs are written to stderr.
The NDJSON shape is a validation surface for OpenAlex discovery, not a stable
export format.

```bash
uv run literature-monitor openalex-discover \
  --config config.example.yaml \
  --issn-l "0006-341X" \
  --from-date 2026-01-01 \
  --to-date 2026-09-18
```

OpenAlex supports anonymous requests. For a larger run, set a free API key in
the environment without placing it in repository configuration:

```bash
OPENALEX_API_KEY=your-key uv run literature-monitor openalex-discover \
  --config config.example.yaml \
  --from-date 2026-01-01 \
  --to-date 2026-01-31
```

The OpenAlex discovery diagnostic does not perform keyword filtering, Crossref
enrichment, Markdown materialization, Zotero integration, conference monitoring,
or persistence.

## Diagnose Crossref discovery

The Crossref-only discovery diagnostic queries each configured ISSN-L
within the inclusive publication-date window and writes normalized provider
records as NDJSON. It does not construct an OpenAlex client or apply local
keyword filtering.

```bash
uv run literature-monitor crossref-discover \
  --config config.example.yaml \
  --issn-l "0006-341X" \
  --from-date 2026-01-20 \
  --to-date 2026-01-25
```

The output is diagnostic rather than a stable export. `CROSSREF_MAILTO` may be
set in the environment to use Crossref's polite API pool.

## Diagnose local keyword filtering

The local keyword filtering diagnostic runs venue-first OpenAlex discovery and
then evaluates the configured keyword expression with the local FTS5 matcher.
It writes only retained original OpenAlex records as NDJSON to stdout and
reports discovered, retained, and filtered-out counts on stderr. This NDJSON is
not a stable export format.

Use the configured expression:

```bash
uv run literature-monitor openalex-filter \
  --config config.example.yaml \
  --issn-l "0006-341X" \
  --from-date 2026-01-20 \
  --to-date 2026-01-25
```

For a one-run diagnostic expression, use an override. It is not written to the
configuration file:

```bash
uv run literature-monitor openalex-filter \
  --config config.example.yaml \
  --issn-l "0006-341X" \
  --from-date 2026-01-20 \
  --to-date 2026-01-25 \
  --keyword-expression '"multiview learning"'
```

Prefix and Proximity operands may be combined with the same Boolean grammar:

```bash
uv run literature-monitor openalex-filter \
  --config config.example.yaml \
  --issn-l "0006-341X" \
  --from-date 2026-01-20 \
  --to-date 2026-01-25 \
  --keyword-expression 'statist* AND "causal inference"~1'
```

The local keyword filtering diagnostic does not perform Crossref enrichment,
canonicalization, Markdown materialization, Zotero integration, conference
monitoring, or persistence.

In the current source tree, filtering uses `record.to_evidence()` and its Provider
searchable projection. Partial title-only or DOI-only records, empty authors,
and missing abstract/publication date are supported. Records with no title,
author keywords, or abstract are filtered out before the matcher, so an empty
projection cannot be retained through `NOT`. Searchable records keep the normal
FTS5 semantics and discovery order; output remains the original OpenAlex records.
Legacy `openalex-filter` and `crossref-enrich` do not apply the production
Candidate Eligibility boundary described above.

## Keyword matching semantics

Local filtering searches only Title, true author- or publisher-supplied Author
Keywords, and Abstract. Each title, abstract, and individual author keyword is
an independent searchable unit. Provider topics, fields of study, and inferred
topics are not searchable.

Expressions support Terms, quoted phrases, Prefix operands such as `statist*`,
Proximity operands such as `"causal inference"~1`, `AND`, `OR`, `NOT`, and
parentheses. Before matching, text is normalized with Unicode NFKC, Unicode
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
current Python SQLite runtime lacks FTS5, commands that require local filtering
report a clear error and exit without falling back to the previous substring
matcher. Local matching does not provide synonym expansion, stemming, arbitrary
user-facing FTS5 `NEAR(...)` syntax, wildcard forms beyond the defined trailing
`*` Prefix, `?` wildcard syntax, BM25 ranking, fuzzy search, or semantic
search.

## Diagnose Crossref DOI enrichment

This historical stage diagnostic runs venue-first OpenAlex discovery, applies
the keyword expression with the same local FTS5 semantics described above, and
performs Crossref DOI lookups only for retained records.
It preserves each original OpenAlex record and attaches normalized Crossref
provider evidence when available. Records without a DOI, and records that are
not present in Crossref, remain in the output with `crossref: null`.

Filtering uses the same Provider evidence projections and excludes empty
projections before matching, including for pure `NOT`. The historical order
remains discovery → local filtering → enrichment: a DOI-only record with no
searchable fields is filtered out before any Crossref lookup. Retained DOI-less
records may still produce the historical diagnostic `missing_doi` warning.
That warning belongs to this enrichment diagnostic; production Run does not
emit an unconditional missing-DOI warning. Counts include records filtered out
for lacking searchable fields.

```bash
uv run literature-monitor crossref-enrich \
  --config config.example.yaml \
  --issn-l "0006-341X" \
  --from-date 2026-01-20 \
  --to-date 2026-01-25 \
  --keyword-expression '"multiview learning"'
```

Crossref access is anonymous. An optional contact address can be supplied for
the polite API pool without changing repository configuration:

```bash
CROSSREF_MAILTO=you@example.com uv run literature-monitor crossref-enrich \
  --config config.example.yaml \
  --issn-l "0006-341X" \
  --from-date 2026-01-20 \
  --to-date 2026-01-25 \
  --keyword-expression '"multiview learning"'
```

stdout is diagnostic `EnrichedWorkRecord` NDJSON and is not a stable export
format. This historical diagnostic does not merge Crossref fields into canonical
metadata and does not implement canonical grouping, UUID
creation, Markdown materialization, Zotero integration, or persistence.

## Diagnose DOI-first canonicalization

The canonicalization diagnostic uses current live retrieval algorithms:
batched OpenAlex discovery, Crossref manifest/full retrieval and DOI
supplementation. It neither reads nor writes Provider state, modifies
`last-run.json`, nor touches old `provider-cache.json`. Persistent revision reuse
does not apply to diagnostics. All available evidence is consolidated before
local FTS5 filtering. Retained valid DOI groups become canonical papers; pure
no-DOI evidence is skipped. Different DOIs remain separate, and neither relations
nor title/author similarity establish identity.

```bash
uv run literature-monitor canonicalize \
  --config config.example.yaml \
  --issn-l "0006-341X" \
  --from-date 2026-01-20 \
  --to-date 2026-01-25 \
  --keyword-expression '"multiview learning"'
```

stdout is diagnostic `CanonicalPaper` NDJSON with DOI-bound identity, selected
bibliographic metadata and provider provenance; it is not a stable export format.
Date selection follows the precedence above without inventing partial dates.

The canonicalization diagnostic does not implement Markdown materialization,
persistent rerun state, stable UUID recovery across independent runs, or Zotero
export.

## Diagnostic materialization

`materialize` remains an explicit diagnostic entry point. It
runs the same OpenAlex/Crossref consolidation-before-filter production pipeline
as `run`, but it still requires an explicit CLI `--output-dir` and continues to
support the existing diagnostic `--issn-l` and `--keyword-expression`
overrides. The normal `run` command instead takes its output directory,
journal whitelist, and keyword expression from the monitor definition:

```bash
uv run literature-monitor materialize \
  --config config.example.yaml \
  --issn-l "0006-341X" \
  --from-date 2026-01-20 \
  --to-date 2026-01-25 \
  --keyword-expression '"multiview learning"' \
  --output-dir /path/to/obsidian-vault/literature-monitor
```

The command writes to:

```text
<output-dir>/
├── Inbox.base
├── Papers/
└── Authors/
```

This diagnostic `materialize` command uses the current live retrieval algorithms
without persistent revision reuse. It neither reads nor writes Provider state,
modifies `.literature-monitor/last-run.json`, nor touches old `provider-cache.json`.
Only normal production `run` writes Provider state and last-run metadata.

Incremental materialization matches existing current-schema Papers only through
normalized DOI. UUID is the durable workspace reference recovered after that
match; title, authors, source keys and other identifiers do not provide fallback
work identity. Matched Papers retain their UUID and path. Pure no-DOI input is
skipped. Sources remain provider provenance and accumulate across reruns.
Current-schema reruns apply incoming canonical metadata, retain existing values
when optional incoming data is missing, and report changes/conflicts as warnings.
Pre-v0.5.2 Paper schemas are unsupported, without migration or legacy repair;
identity-readable unsupported files block unsafe duplicate creation.

Updates preserve workflow status, discovery time, Zotero key, unknown
frontmatter, Notes, and unmanaged body sections. Managed frontmatter and the
title, Abstract, and Sources sections are rewritten atomically only
when their rendered bytes change. Existing opaque Author files remain reusable
at their deterministic paths and are never overwritten; parseable Author notes
may receive missing stable identifiers without being renamed.

Malformed but identity-readable Papers still block duplicate creation. Unsafe
or ambiguous matches are reported as errors and left unchanged, while
recoverable metadata conflicts are warnings. Incremental materialization does
not automatically merge, delete, or rename historical duplicates.

### Review candidates in Obsidian

Open `<output-dir>/Inbox.base`, review candidate papers in the **Inbox** view,
and change each Paper's `status` to `kept` or `rejected`. The Paper moves
automatically between the status-derived **Inbox**, **Kept**, **Rejected**, and
**In Zotero** views. Use `export-kept` for the Zotero handoff.

Paper Markdown remains the durable workflow state. `Inbox.base` contains only
presentation configuration, so there is no Inbox synchronization command. It
is user-customizable: columns, sorting, filters, formulas, and view layout may
all be adjusted. Literature Monitor creates `Inbox.base` only when it is
absent; an existing regular `Inbox.base` is never overwritten.

The Review Inbox uses Obsidian Bases and was validated with Obsidian Desktop
1.13.7. Bases is the only additional presentation capability used. No
community plugin, Dataview, custom CSS, or additional Python runtime dependency
is required.

## Export Kept Papers for Zotero

The export command reads the existing durable Paper Markdown without running
discovery, enrichment, canonicalization, or materialization:

```bash
uv run literature-monitor export-kept \
  --output-dir /path/to/obsidian-vault/literature-monitor
```

Only Papers with `status: kept` are written to stdout. Papers marked
`candidate`, `rejected`, or `in_zotero` are skipped. Each kept Paper produces
one line containing its normalized DOI. Current-schema Papers require a valid
DOI; DOI-less or unsupported old-schema files are reported as invalid rather
than exported through another identifier. Malformed Paper files are reported on
stderr without blocking other valid entries, and make the command exit with
status 1. The output can be redirected to a file:

```bash
uv run literature-monitor export-kept \
  --output-dir /path/to/obsidian-vault/literature-monitor \
  > kept-for-zotero.txt
```

The command does not call Zotero APIs or change Markdown state. After a
successful downstream Zotero import, use Web **Check Zotero** to perform the
same exact-DOI reconciliation and atomically record `in_zotero` with its
verified `zotero_key`.
