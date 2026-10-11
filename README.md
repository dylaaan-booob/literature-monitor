# Literature Monitor

This repository contains the journal-monitoring workflow specified in
`SPEC.md`. The workflow uses OpenAlex for primary discovery and Crossref for
secondary discovery and bibliographic evidence, consolidates provider evidence
before local keyword filtering, canonicalizes retained DOI-bound papers,
safely updates durable Paper and Author Markdown, and exports kept papers.

## Release and development status

**v0.6.6 Codebase & Documentation Simplification is RELEASED** as the latest
verified [GitHub Release](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.6.6).
T1 through T4 have completed independent review. ChatGPT's independent T5
offline integration audit passed **3040 Python tests (2 warnings), 163 Runtime
tests and 25 CARSI Executor tests**; see [SPEC §45.7](SPEC.md#457-t5-final-integration-audit-evidence).
Version finalization passed **528 targeted Python tests**, including the 0.6.6
CARSI handshake. The wheel, sdist and separate Connector ZIP passed content
checks. A fresh wheel installation passed CLI and HTTP smoke checks; all three
downloads matched the inspected builds.
[SPEC §45.8](SPEC.md#458-v066-release-preparation-and-validation-reuse) records
build evidence; [§45.9](SPEC.md#459-v066-publication-verification-and-closeout)
records the fixed tag and asset SHA-256 values. Embedded artifact documentation
retains the verified release-preparation snapshot.

Kept offers optional **Prepare CARSI Access** for CARSI Services related to all
current Kept Papers, deduplicated in saved configuration order. **Manual Login**
Services have separate saved-URL access. Preparation searches verified official
CARSI resource pages, clicks a uniquely matched Detail/Visit control, and reports
**Opened / Needs attention / Failed**. Opened confirms a click and attributable
target opening, without claiming authentication, entitlement or PDF availability.
Users complete institutional login/MFA and close all retained browser pages.
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
GUI Run uses the persisted Monitor date policy and exposes no temporary date override.

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
upgrade. The CLI `validate` command remains available.

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
without changing Paper state. CLI `export-kept` remains a separate read-only handoff.

Paper Markdown remains the durable workflow state. The GUI does not add a
workflow/execution database, persistent run history, SSE, WebSocket, batch
queue, or another source of Paper decision state. Connector heartbeat and one
active capture attempt are transient process-local state.

### Connector prerequisites

- Run **Zotero Desktop** and the local Web UI bridge at `http://127.0.0.1:8000`.
- Download and extract the published **v0.6.6 Connector ZIP**. In Chrome,
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
UUID stability, `candidate` / `rejected` / `kept` / `exported`
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
Current Zotero Import uses the separate Connector's native parent-save
confirmation and transient batch state under §42. It does not require My Library
preflight or exact-DOI parent reconciliation. Custom PDF acquisition/staging,
publisher-login automation and the retired Browser Companion remain outside
the current workflow.

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
**Exported** views. Use `export-kept` for the Zotero handoff.

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

Only Papers with `status: kept` are written to stdout. All other statuses
are skipped. Each kept Paper produces
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

The command does not call Zotero APIs or change Markdown state. Manual downstream
use of this CLI output does not automatically reconcile Paper state. Web
**Import to Zotero** uses the separate native acceptance flow described above
to persist `kept → exported`.
