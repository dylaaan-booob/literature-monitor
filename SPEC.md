# Literature Monitoring Workflow — Specification v1.4

**Status:** Active; v0.5.3 remains RELEASED and the latest released/completed baseline. v0.6.0 implementation is complete and the current source is in release preparation, but v0.6.0 is not released or tagged. Prepared package and OpenAlex/Crossref Provider identities are `0.6.0`. §§35–38 retain their released historical meaning and verification boundaries.

**Stage:** v0.6.0 A1–A6 implementation, Final Audit Fix 1, final independent audit, implementation commit `f6600dd6d955699c0ce0a066f8d16067533d89ce` (`Implement v0.6.0 Zotero Connector workflow`), and post-commit automated verification are complete. Release preparation is now in progress; the prepared source has not been independently release-prep reviewed or committed, and no v0.6.0 tag, push, GitHub Release, or release transaction is complete. The unreleased v0.5.4 Automatic Institutional Access Orchestration + Candidate-Bound PDF Acquisition direction was terminated after product validation and is not production authority.
**Scope:** Journal monitoring with CLI, durable Markdown workspace, Obsidian presentation, and a local Python Web UI adapter; conferences remain excluded

**Current contract:** §39 is the authoritative v0.6.0 current development contract. §37 remains authoritative for unaffected DOI-first identity, Provider behavior, current Paper schema, single-manifestation and Zotero parent identity. Conflicting Browser Companion, custom PDF acquisition, staging, institutional credential orchestration, and Zotero write requirements in historical §§35–38 do not constrain v0.6.0 implementation. Their release, live-verification, and maintenance records remain immutable version-specific evidence.

---

## 1. Problem

The current literature workflow is fragmented across discovery, screening, Zotero ingestion, and later reading notes. It is also too idea-driven: literature is often searched only after an idea has already formed, which makes it difficult to build a current view of a field and to recognize whether a proposed idea has already been extensively studied.

The project should support a different workflow:

> Continuously monitor a fixed set of high-quality journals, filter recent papers with relatively stable keywords, create a low-cost Markdown candidate pool for human screening, and only send papers worth keeping to the downstream Zotero workflow.

The system should help accumulate recent literature and author relationships. It should not replace human academic judgment.

---

## 2. Goal

Build a lightweight, maintainable journal-monitoring workflow with the following end-to-end path:

```text
Journal whitelist
    ↓
Provider-specific journal/date retrieval
    ↓
Provider evidence
    ↓
Identity/evidence consolidation
    ↓
Searchable projection
    ↓
Local keyword filtering
    ↓
CanonicalPaper / version consolidation
    ↓
One candidate paper → one Markdown file
    ↓
Author wikilinks + author notes
    ↓
Workspace presentation adapters
    ├─ local Python Web UI
    └─ Obsidian Bases Review Inbox
    ↓
Human-directed triage recorded in Paper Markdown
    ↓
rejected / kept
    ↓
Export kept identifiers for Zotero import
    ↓
Human confirmation → in_zotero
```

The v0.3.3 journal-monitoring model remains the core. v0.4.0 adds a local Web application as another adapter over the same configuration, canonical production pipeline, Markdown workspace, and decision state. It does not replace the CLI, require an Obsidian plugin, introduce publisher scraping, or implement custom Zotero ingestion.

---

## 3. User Scenario

The user maintains:

1. one local YAML monitor definition;
2. a relatively stable journal whitelist referenced by that monitor;
3. a relatively stable keyword expression;
4. a persistent date policy or an ephemeral CLI date override.

A normal run uses:

```bash
literature-monitor run --config monitor.yaml
```

and should:

1. validate the journal whitelist and resolve any provider-specific venue identifiers;
2. retrieve journal/date evidence independently from the configured providers;
3. consolidate records that have sufficient identity evidence into provider-neutral evidence clusters;
4. build a searchable projection from all available evidence in each cluster;
5. apply the configured keyword expression locally to decide inclusion;
6. merge each included cluster and its known versions into one canonical paper;
7. create one Markdown file per new candidate paper;
8. create/update author notes and author links;
9. create the default Review Inbox presentation when it is absent;
10. preserve all prior human decisions, notes, and an existing user-customized Review Inbox;
11. allow the user to mark papers as `rejected`, `kept`, or later `in_zotero`;
12. export identifiers for `kept` papers so existing Zotero DOI/identifier import functionality can be used.

---

## 4. MVP Scope

### 4.1 Included

MVP includes:

- a local-first persistent monitor definition stored as one YAML file;
- journal whitelist configuration;
- ISSN/EISSN-based venue resolution;
- multi-source evidence retrieval within the journal/date boundary;
- OpenAlex primary journal/date discovery;
- Crossref secondary journal/date discovery, DOI enrichment, and bibliographic evidence;
- provider-neutral identity/evidence consolidation;
- local keyword filtering after available evidence is consolidated;
- a transient, reconstructible SQLite FTS5 runtime index for local lexical filtering;
- Prefix/truncation and Proximity operands within local lexical filtering;
- provider, journal, and request failure isolation;
- canonical paper identity;
- basic cross-source deduplication;
- version tracking and preferred-version selection;
- one-paper-one-Markdown materialization;
- full abstract storage when available;
- candidate workflow states;
- persistent rejected records;
- author wikilinks and author notes;
- an Obsidian Bases Review Inbox derived from Paper Markdown state;
- a local Python Web UI adapter implemented with FastAPI, Jinja2, and vendored HTMX;
- stable application-layer boundaries shared by the CLI and Web application;
- transient process-local run coordination with no persistent run history;
- one application-owned last-run retrieval-coverage snapshot representing the latest successfully persisted completed production run, with no execution-control or history semantics;
- automatic revision-validated reuse of reconstructible Provider metadata/version state, with live candidate membership on every production Run, under §30;
- safe overlapping reruns;
- export of `kept` paper identifiers for Zotero;
- logs sufficient to inspect unresolved journals, failed enrichment, and partial metadata.

### 4.2 Explicitly excluded

MVP does **not** include:

- conference monitoring;
- systematic-review collaboration or multi-reviewer screening workflow;
- Google Scholar scraping;
- global keyword-first literature search;
- proactive arXiv-wide monitoring;
- publisher-specific scraping/adapters;
- automatic topic classification;
- Topic Graph construction;
- citation graph construction;
- automatic research-thread detection;
- automatic synonym expansion;
- stemming;
- Porter tokenizer;
- trigram or fuzzy matching;
- semantic search;
- LLM query expansion;
- relevance ranking, including BM25 or LLM-based ranking;
- field-specific query syntax;
- arbitrary user-facing FTS5 `NEAR(...)` syntax beyond the defined `"a b"~N` Proximity operand;
- wildcard forms other than the defined single trailing-`*` Prefix operand;
- automatic idea generation;
- automatic paper summarization;
- automatic PDF download for candidates;
- custom DOI-to-Zotero ingestion code;
- custom Zotero attachment handling;
- Obsidian plugin;
- LAN/server deployment of the v0.4.0 Web UI;
- login/password or multi-user account systems;
- desktop application wrappers;
- recommendation ranking;
- citation-count ranking;
- journal ranking;
- a durable workflow database or another mandatory source of truth; the narrowly defined reconstructible Provider-state DB in §30.4 is permitted;
- persistent search database;
- inline journal definitions in monitor YAML;
- monitor UUIDs or monitor versioning;
- workspace UUIDs, workspace ownership markers, or a workspace registry;
- a global research-work registry or cross-workspace Paper UUID identity;
- per-monitor decision objects, monitor membership state, or decision inheritance across monitors;
- defined rejected-paper resurfacing semantics across different workspaces;
- new workflow statuses beyond `candidate`, `rejected`, `kept`, and `in_zotero`;
- query versioning;
- Crossref update-date, created-date, index-date, or another provider update timestamp as a discovery date;
- persisted last-run or last-successful-run timestamps;
- persisted date ranges used as synchronization or discovery-window authority, provider cursors, provider watermarks, late-index recovery cursors, checkpoints, run history, delta / What's New state, notification state, scheduler state, or automatic retry state;
- a persistent execution database;
- scheduler, daemon, or cron management;
- notifications;
- multi-monitor dashboard;
- Zotero API integration;
- Paper Markdown schema changes in v0.3.3;
- Track B workflow/state expansion beyond the current Markdown lifecycle;

SQLite FTS5 remains transient, reconstructible runtime state. The separate Provider-state SQLite DB is limited to §30.4; neither is durable workflow state or a mandatory second source of truth. Additional v0.4.3 exclusions are defined in §30.11; v0.4.4 exclusions are defined in §31.8.

---

## 5. Journal Whitelist

The initial whitelist is the supplied journal list. Conferences in that source list are excluded from MVP.

Each journal configuration entry should support at least:

```yaml
name: Journal Name
issn:
  - 0000-0000
  - 1111-1111   # optional second ISSN/EISSN
```

Optional cached fields may include:

```yaml
openalex_source_id:
publisher:
content_policy:
```

Requirements:

- ISSN/EISSN are the primary venue identifiers.
- Journal names are display metadata, not the sole identity key.
- Source resolution failures must be reported explicitly and must not be silently skipped.
- Cached OpenAlex source IDs may be stored after successful resolution.
- MVP does not require fine-grained publisher-specific article-type filtering.

No global `research_article_only` assumption should be hard-coded because the whitelist contains journals whose legitimate content includes reviews, expository work, software papers, or other nonstandard article categories.

---

## 6. Data Source Policy

All retrieval remains journal-whitelist-first and date-bounded. OpenAlex is the sole primary discovery provider. Crossref is the secondary provider and may independently contribute journal/date discovery records plus DOI and bibliographic evidence. Evidence consolidation remains provider-neutral; retrieval order does not grant canonical authority.

Discovery date membership remains publication-date-only. Literature Monitor does not use Crossref update-date, created-date, index-date, provider update timestamps, or persisted synchronization metadata to expand a date window. The current production pipeline does not provide late-index recovery, provider watermarks, checkpoints, or incremental-sync semantics.

### 6.1 OpenAlex

Responsibilities:

- ISSN/EISSN → OpenAlex Source resolution;
- retrieve works by source and date window;
- title;
- authorship data;
- OpenAlex author IDs;
- ORCID when available;
- DOI when available;
- publication date;
- abstract when available;
- venue/source metadata;
- OpenAlex work ID;
- useful relation or version hints when available.

OpenAlex retrieval must be venue-first:

```text
journal whitelist
→ OpenAlex source
→ works in time window
→ provider evidence
```

It must **not** use OpenAlex global keyword search as the main discovery path.

### 6.2 Crossref

Responsibilities:

- retrieve works independently by ISSN and date window;
- enrich records by DOI and related bibliographic identifiers;
- contribute title, authorship, publication dates, abstract, and venue metadata when present;
- provide relation metadata when available;
- contribute DOI, journal, and provenance evidence.

A valid Crossref journal/date result may contribute a candidate even when no corresponding OpenAlex record exists.

Crossref `type = journal-article` must not be interpreted as proof that a record is a research article.

### 6.3 Retired providers and durable compatibility

Semantic Scholar is not a supported production retrieval provider. The production pipeline must not construct a Semantic Scholar client, read `SEMANTIC_SCHOLAR_API_KEY`, perform Semantic Scholar discovery or supplementation, or expose Semantic Scholar-specific runtime statistics, issues, or progress activity.

Provider retirement does not narrow the durable data model. Existing Paper Markdown may continue to contain `semantic_scholar` or other provider-specific external identifiers, and historical provenance may continue to name retired providers. `ExternalIds` remains open to additional provider-specific identifiers, while canonicalization and Markdown merge preserve valid historical identifiers and provenance rather than deleting them during reruns.

### 6.4 Provider request reliability

Production Provider clients remain synchronous and use the pooled transport/lifecycle contract in §30.9. Stage 2 concurrency is limited to the two Provider branches in §30.10, with no journal/ISSN worker pools or async rewrite. Retry, pacing, pagination, and batching remain Provider-local transient behavior, with no durable request history, cursor/checkpoint, resume, or scheduler state. The latest-run snapshot in §6.6 is observational metadata, and the DB in §30.4 contains only revision-validated normalized state.

The v0.4.3 batched Source/Works, Crossref manifest planner, and DOI probe/hydration rules in §§30.2–30.3 supersede the released singleton-only supplementation and cursor-only Crossref discovery paths. Pagination retains repeated-cursor and malformed-response protection; optimizations must preserve the coverage invariants in §30.7.

Provider requests use at most three attempts by default. Retryable transient failures are limited to HTTP 429, HTTP 5xx, and supported transport or timeout failures. Endpoint-specific HTTP 404 handling retains its existing not-found semantics. Other HTTP 4xx responses fail the request immediately and are not automatically retried. When no provider pacing information is available, retry delays retain the existing exponential fallback of 1 second and then 2 seconds.

Crossref request pacing uses process-local monotonic time, never wall clock or persisted timing state. State is separated by actual request class, at least singleton DOI requests and list/filter requests. Each class retains its own latest valid `minimum_interval` and `next_allowed_at`; one class's response must not overwrite another class's state. Before each request, proactive pacing waits only `max(0, next_allowed_at - monotonic_now)`. The next deadline is based on the preceding request's start time and the applicable minimum interval, so normal response/network latency counts toward request spacing rather than being followed by a full interval sleep.

Rate metadata already present on normal API responses updates only the corresponding request class. When both `X-Rate-Limit-Limit` and `X-Rate-Limit-Interval` are valid, `interval_seconds / limit` defines that class's minimum interval and updates its deadline against the request start time. Missing or malformed headers preserve that class's latest valid state, do not invalidate an otherwise successful response, and do not trigger a separate probe, count-only request, or other pacing-only provider request. The implementation must not freeze current public, polite, or plus pool limits into long-lived product constants.

Retry backoff must satisfy any known pacing requirement for the same request class. The retry may start only after both the retry-backoff deadline and the class's `next_allowed_at`; elapsed latency and elapsed waiting count toward those deadlines. Do not add a full pacing sleep to a retry sleep that already satisfies the same spacing requirement. The retry range, attempt bound, and 1-second/2-second fallback above remain unchanged.

Provider pacing remains serial and transient, without a new worker pool, async limiter, token bucket, or generic rate-limit framework. If a proactive pacing delay actually occurs and a progress callback is present, it is reported as `WAITING` with the existing transient Activity model. `RETRYING` remains reserved for retry after a real failure and is reported before its corresponding sleep, preserving the request's source, operation, unit, current, and total identity. These Activities keep liveness and inactivity reporting tied to the worker state. Provider pagination, pacing, and retry state do not add or change any of the five `ProgressStage` values.

A failed provider request remains isolated according to the existing retrieval rules. Evidence already obtained successfully from another provider, journal, page, or request remains usable and must not be discarded because a later request fails.

### 6.5 Run coverage state

Each production run records transient, process-local retrieval coverage for the provider work units it actually executes. Coverage describes retrieval execution completeness for this run, not bibliographic field completeness; it does not claim that a provider or the bibliographic universe is globally complete. A provider may successfully return zero records and still have `COMPLETE` coverage.

Coverage is separate from provider issues. Coverage states whether a retrieval work unit completed reliably, while issues continue to carry concrete warnings, errors, and diagnostics. Coverage must not change `RunOutcome`, CLI exit codes, provider failure isolation, canonicalization, or materialization behavior.

The provider-neutral coverage model has these components:

- `OPENALEX_DISCOVERY`;
- `CROSSREF_DISCOVERY`;
- `CROSSREF_SUPPLEMENT`.

Each work unit has one of these statuses:

- `COMPLETE`;
- `PARTIAL`;
- `UNAVAILABLE`;
- `FAILED`.

A coverage unit identifies work performed by one run. OpenAlex discovery is identified by provider/component plus configured journal. Crossref discovery is identified by provider/component plus configured journal and queried ISSN. Crossref supplementation is identified by provider/component plus normalized requested DOI. Coverage order is deterministic. These fields do not become a durable execution identity when copied into the A4 latest-run snapshot: coverage must not add UUIDs, database IDs, persisted cursors, checkpoints, watermarks, resume state, scheduler state, run history, or another execution database.

OpenAlex discovery coverage is:

- `COMPLETE` when a journal Source is established and Works traversal reaches its natural end without unresolved structural or traversal failure. Zero works is valid `COMPLETE`. Tolerable sparsity in title, authors, abstract, DOI, publication date, author IDs, ORCID, or `updated_date` does not lower successful traversal coverage. A record with neither valid DOI nor usable title falls below the usable evidence floor (§31.3), but excluding it for that reason alone is not an execution `ERROR` and does not lower coverage. An unresolved alternate configured ISSN does not by itself lower coverage when another ISSN reliably resolves to the same usable Source and Works retrieval completes.
- `PARTIAL` when some trustworthy Works retrieval succeeds but pagination, request, count, cursor, structural Work identity, or unrecoverable Source-attribution failure leaves retrieval incomplete or untrustworthy in part. Duplicate traversal and other existing conservative checks that genuinely indicate traversal incompleteness remain applicable; ordinary bibliographic sparsity is not such a failure.
- `UNAVAILABLE` with `WARNING` for genuine normal Source absence, such as when all relevant Source lookups are not found. `validate` encountering only normal Source absence produces `VALID_WITH_WARNINGS`, not `SOURCE_ERRORS`.
- `FAILED` with `ERROR` when remote request failure, provider response validation failure, conflicting Source identity, journal identity validation failure, or another provider error prevents trustworthy Source coverage, or when Works retrieval fails before any page completes. Structural/traversal failures without usable retrieval remain failures; tolerable missing fields must not be promoted to this category.

Crossref discovery coverage is one work unit per configured journal plus queried ISSN:

- `COMPLETE` when the live manifest is verified complete and every member has the required current full metadata under §30.3, without losing a returned work item. Zero records is valid `COMPLETE`. Venue-mismatch exclusion and field-normalization warnings that retain the record do not lower coverage.
- `PARTIAL` when at least one page completes and a later request or pagination failure occurs, or when a returned work item is dropped by record normalization.
- `UNAVAILABLE` for the endpoint-specific journal/ISSN not-found case.
- `FAILED` when request, response-envelope, or cursor failure occurs before any page completes.

Required full-hydration failures for manifest-confirmed current members follow §30.7: affected discovery units must be `PARTIAL` or `FAILED`, never `COMPLETE` or `UNAVAILABLE`.

Crossref supplementation coverage is one work unit for each normalized requested DOI in the current pending lookup set that is actually processed. Successful revision-validated evidence acquisition is `COMPLETE`; authoritative not-found is `UNAVAILABLE`; request, invalid-response, or record-normalization failure is `FAILED`. Supplementation does not use `PARTIAL`. Batch processing or alias resolution does not change that requested-DOI identity; a DOI that never enters the pending set must not create a supplementation coverage unit. §30.7 governs conservative coverage across batching and state reuse.

Coverage is produced at provider execution boundaries and carried with the existing provider results; application orchestration combines those units into the canonical production result in deterministic order. Preflight or invalid-configuration failures that execute no providers have empty coverage and must not invent failed provider units.

`RunResult` exposes the current run's coverage and a compact derived per-component summary containing total units plus counts for complete, partial, unavailable, and failed states. Coverage is not duplicated into `MonitorStatistics`.

The `run`, `canonicalize`, and `materialize` CLI completion summaries display compact structured coverage without printing every successful unit. Under v0.4.6 §33.4, Local Web coverage is shown only in Settings `Advanced & Diagnostics → Current run`, using the finished process-local `RunResult`; the primary finished Run view does not show coverage detail. A refresh in the same server process can show the retained result again. A server restart cannot reconstruct full Run diagnostics from `last-run.json` or any other durable data. No browser-side coverage persistence, run history, or coverage API/database is introduced. The separate read-only `last-run` CLI diagnostic continues to read the durable snapshot defined below; it does not restore a discarded Web `RunResult`.

Coverage is retrieval execution metadata, not Paper workflow state. Only the single latest-run snapshot at `<output_dir>/.literature-monitor/last-run.json` persists coverage. Provider state in §30.4 must not persist coverage or membership. Coverage must not be written to Paper Markdown, Author Markdown, Inbox, monitor YAML, `.obsidian`, SQLite FTS indexes, or other persistent Web state, or affect canonical identity, keyword filtering, Paper status, human notes, materialization merge, or Zotero export.

### 6.6 Durable latest-run coverage snapshot

A4 may persist exactly one application-owned last-run coverage snapshot at `<output_dir>/.literature-monitor/last-run.json`. A valid file represents the latest successfully persisted completed production run. The hidden `.literature-monitor/` directory is runtime metadata, not Paper/Author workflow state, and materialization scanners must not treat it as Paper data. A4 defines no historical run files.

The snapshot is a durable observation of the latest successfully persisted completed normal production `run_monitor()`. A snapshot write failure deliberately preserves the previous valid file, so the snapshot need not describe the chronologically most recent completed execution. It is not a durable provider result cache, resume checkpoint, synchronization cursor, or authority for future provider execution. A later run must not use it to skip provider requests, change the discovery window, retry automatically, restore a cursor, infer late-index coverage, change canonicalization/materialization, or determine resume eligibility. Deleting the snapshot must not damage Paper/Author workflow state or prevent a future normal run.

A4 originally wrote schema v1. The snapshot remains schema v2 in v0.4.3, with `reused_units=[]` on every new Run; valid v1 and historical v2 (including non-empty historical `reused_units`) remain readable without migration. The common fields are:

- the resolved inclusive `from_date` / `to_date` used by that production run;
- the recorded outcome: `COMPLETED`, `COMPLETED_WITH_WARNINGS`, or `COMPLETED_WITH_ERRORS`;
- the ordered A3 coverage units, preserving provider, component, status, journal, ISSN, and DOI identity fields.

It must not persist derived coverage summaries, provider raw responses, canonical papers, issue text, `MonitorStatistics`, workflow decisions, UUIDs, provider cursors, config fingerprints, retry state, timestamps, or historical runs. A consumer must derive compact per-component summaries again from the ordered coverage units.

Only a normal production `run_monitor()` that reaches a non-`INVALID_CONFIGURATION` `RunResult` may replace the snapshot. This includes clean, warning, and error completions because partial/failed coverage remains diagnostically useful. `validate`, provider diagnostics, `canonicalize`, legacy/diagnostic `materialize`, and `export-kept` must not write it. GUI Run needs no second persistence path because it already executes production `run_monitor()` through the existing `RunCoordinator`.

Invalid configuration, deterministic preflight failure, an unexpected exception before a normal `RunResult`, and snapshot serialization/write failure must preserve any previous valid snapshot. Snapshot creation and replacement use complete-file filesystem semantics: replacement is atomic, a failed replacement preserves the prior complete file when possible, temporary files are cleaned up, and an unexpected symlink or incompatible filesystem object at the metadata directory or snapshot path must be reported rather than replaced or destroyed. Cross-process coordination for simultaneous runs against one workspace remains outside A4.

Snapshot persistence failure occurs after normal Paper materialization and must not roll back those writes. It is an application-level warning distinct from provider and materialization issues. A3 coverage status itself still does not change `RunOutcome`; an A4 filesystem warning follows the existing result invariant, so an otherwise clean completion becomes `COMPLETED_WITH_WARNINGS` while CLI warning-only exit semantics remain non-fatal.

The application-level reader is fail-soft and read-only. Missing snapshot, valid snapshot, and invalid/unreadable snapshot are distinguishable. Malformed JSON, unknown schema version, invalid date/outcome/component/status, invalid component-specific coverage identity, and filesystem read failure are structured read failures. The reader must not delete or rewrite a corrupt file, contact providers, repair unknown schemas, or treat corruption as an empty successful snapshot.

Persisted coverage identity is validated as follows:

- OpenAlex discovery requires journal and forbids ISSN/DOI;
- Crossref discovery requires journal and ISSN and forbids DOI;
- Crossref supplementation requires an already-normalized DOI and forbids journal/ISSN.

`literature-monitor last-run --config monitor.yaml` loads the configured `output_dir` and reads this snapshot only. It performs no provider request, canonicalization, materialization, or write. A valid snapshot exits `0` and reports the resolved date range, recorded outcome, and the same compact summary logic used for A3 coverage. Missing or invalid/unreadable snapshot exits `1`; invalid monitor configuration exits `2`. The command has no date, journal, or keyword overrides and is a human-readable diagnostic rather than a frozen machine API.

V2 also stores `reused_units` reporting identities, never records. The identity validator applies to coverage and historical reuse: OpenAlex requires provider=openalex and journal only; Crossref discovery requires provider=crossref, journal and ISSN; supplement requires provider=crossref and normalized requested DOI only. V2 requires unique live identities, unique reused identities, disjoint live/reused sets, and monotonic reused component phases. Valid v1 reads with empty reuse, without retroactive v2 uniqueness constraints. Unknown future versions are INVALID. `last-run` displays a separate historical reuse summary when non-empty. The transient v0.4.3 Provider-state usage summary in §30.8 is not serialized here. The snapshot provides no resume or freshness authority.

### 6.7 Current Provider retrieval and state contract

§37 is the current v0.5.2 development contract for DOI-first consolidation, removal of retained OpenAlex location/version hydration, and the Crossref-only Provider-state schema v3 without v1/v2 row-preserving migration. Unaffected live membership, revision checks, transport, concurrency, progress, failure isolation and coverage requirements continue under §§30–32. Those sections retain the historical v0.4.3–v0.4.5 contracts, including the pacing/partial-evidence changes in §31 and eligibility/metadata-equivalence changes in §32; their conflicting version-state, grouping and migration clauses are superseded by §37. Publication-date discovery, local filtering and Markdown human ownership remain preserved.

### 6.8 Released v0.4.2 cache history

In released v0.4.2, A5 persisted `provider-cache.json` schema v1 with the exact resolved date range and normalized results of clean COMPLETE units. A6 permitted explicit exact-range opt-in reuse through CLI `--reuse-provider-cache` and Web “Run with cache reuse”; default runs stayed live. Reused units were reported separately from live coverage, and last-run schema v2 added their identities. This is release history, not current production behavior. The v0.4.3 migration and compatibility rules are in §30.6; the completed release record remains in §24.10.

### 6.9 Publisher fallback

Publisher fallback is deliberately excluded from MVP.

If the configured providers cannot provide a field such as abstract, the field remains missing and the failure is recorded. The system must not scrape publisher pages in the MVP.

Publisher-specific adapters may be considered only after real usage demonstrates persistent, high-value metadata gaps concentrated in specific journals or publishers.

---

## 7. Keyword Filtering

Keywords are a **filter inside the journal whitelist**, not a global discovery mechanism.

Filtering occurs only after records with sufficient identity evidence have been consolidated. The searchable projection for an identity cluster uses the eligible fields available across all provider evidence in that cluster, rather than only the fields from the first or otherwise preferred provider record.

The required order is:

```text
journal/date retrieval
→ provider evidence
→ evidence consolidation
→ searchable projection
→ local keyword filtering
```

The complete Boolean expression must not be applied provider-record-by-provider-record before evidence consolidation.

### 7.1 Search fields

MVP follows a 篇关摘-style search scope:

```text
Title + Author Keywords + Abstract
```

Field availability may degrade gracefully:

```text
Title + Author Keywords + Abstract
→ Title + Abstract
→ Title
```

A missing abstract or missing author keywords must not automatically exclude a paper.

If one provider lacks an abstract and another provider in the same identity cluster supplies one, the available abstract must participate in filtering.

Only author/publisher-supplied keywords that can be identified as such should be treated as `Author Keywords`.

Provider-derived keywords, topics, or fields of study must remain distinct from `Author Keywords`. They may be retained as provider evidence, but they must not be silently mapped into the `Author Keywords` search field.

Each concrete field value contributed by available evidence is an independent **searchable unit**. Searchable units therefore include, for example:

- a provider A title;
- a provider A abstract;
- a provider B title;
- a provider B abstract;
- each individual author keyword.

Field and provider boundaries are retained in the searchable projection even though Boolean evaluation occurs at the consolidated research-work identity.

### 7.2 Expression semantics

MVP supports:

- Terms as defined by the existing keyword grammar, including punctuation-containing Terms such as `x/y`, `a-b`, `p>>n`, and `high-dimensional`;
- quoted phrases;
- Prefix operands using `term*`;
- Proximity operands using `"a b"~N`;
- `AND`;
- `OR`;
- `NOT`;
- parentheses;
- case-insensitive matching.

Before lexical matching, every searchable unit and the lexical content of every atomic Term, Phrase, Prefix, or Proximity operand are normalized in this order:

```text
Unicode NFKC
→ Unicode casefold
→ whitespace normalization
```

Whitespace normalization strips leading and trailing whitespace and collapses each internal whitespace run to one space. SQLite FTS5 `unicode61` lexical tokenization occurs only after this normalization. The implementation must apply Unicode casefold before tokenization rather than relying on `unicode61` case handling alone; for example, `strasse` must match `Straße`.

The existing Term boundary rules remain unchanged. Each normalized Term is tokenized with `unicode61`. If it produces one or more lexical tokens, that complete ordered token sequence must occur contiguously within one searchable unit for the Term to match. A Term cannot span searchable units. If a Term produces no lexical tokens, that atomic operand does not match any research work. Term content must be treated as lexical input and must not be interpolated verbatim into SQLite FTS5 query syntax.

A quoted phrase represents one contiguous lexical token sequence and must occur completely within one searchable unit. A phrase must not span title and abstract, two author keywords, or records from different providers. For example:

```text
title unit: deep
abstract unit: learning

"deep learning" → false
deep AND learning → true
```

A Prefix operand uses exactly one trailing `*`, as in `statist*`. The Prefix base is normalized with NFKC and Unicode casefold before validation and tokenization. After normalization, the base must contain at least three lexical characters, consist only of Unicode letters or digits, and produce exactly one `unicode61` lexical token. Leading wildcards, mid-word wildcards, and multiple `*` characters are invalid Prefix syntax. `?` has no wildcard meaning. An `*` inside a quoted operand has no Prefix meaning.

Prefix matching is lexical token-prefix matching, not substring matching. For example:

```text
statist* → statist, statistic, statistics, statistical
statist* → does not match biostatistics
```

A Proximity operand uses a quoted lexical sequence followed by an explicit distance, as in `"causal inference"~0`. Whitespace is permitted between the closing quote and `~N`. `N` must be an integer in the inclusive range `0 <= N <= 50`. After normalization and `unicode61` tokenization, the quoted content must contain at least two lexical tokens; a single-token Proximity operand is invalid and does not provide fuzzy-search semantics.

Proximity token order is not significant. The user distance `N` counts the additional intervening tokens permitted beyond the most compact arrangement of the operand tokens. Therefore `"causal inference"~0` means unordered adjacency, while the exact Phrase `"causal inference"` remains ordered adjacency. For a Proximity operand containing `k` lexical tokens, the equivalent FTS5 NEAR distance is:

```text
fts_near_distance = user_distance + k - 2
```

Every Prefix or Proximity match must be satisfied wholly inside one searchable unit. In particular, Proximity cannot span title and abstract, two author keyword values, or records from different providers.

Boolean operators combine atomic operand truth values at the consolidated work level, so `A AND B` may match different searchable units or evidence supplied by different providers regardless of whether those operands are Terms, Phrases, Prefixes, or Proximity expressions. `NOT` retains these work-level Boolean semantics: it negates whether its operand matches the work, not merely whether it matches the same searchable unit as another operand.

Punctuation is handled as a `unicode61` tokenizer boundary; character-for-character punctuation-sensitive substring equivalence is not required. Consequently, the Term `high-dimensional` matches both `high-dimensional` and `high dimensional` when they produce the same contiguous lexical token sequence. A Term such as `p>>n` is handled by its resulting `unicode61` token sequence in the same way. This remains lexical token matching rather than fuzzy matching, and punctuation-containing Terms remain valid user syntax.

MVP does not require:

- automatic synonym expansion;
- stemming or the Porter tokenizer;
- trigram or fuzzy matching;
- semantic search or LLM query expansion;
- relevance or BM25 ranking;
- field-specific query syntax;
- arbitrary user-facing FTS5 `NEAR(...)` syntax beyond the defined `"a b"~N` Proximity operand;
- wildcard forms other than the defined single trailing-`*` Prefix operand;
- automatic topic inference;

SQLite FTS5 may implement this contract only through a transient, reconstructible runtime index. User input must be parsed and compiled from the defined expression grammar; no Term, Phrase, Prefix, Proximity, distance, Boolean subtree, or other user-supplied text may be inserted as arbitrary FTS5 `MATCH` syntax. The index is disposable and must not become a persistent search database, durable workflow state, or another mandatory source of truth. If the current Python SQLite runtime lacks FTS5, commands that require local filtering must continue to use the existing explicit error path rather than silently changing matching semantics.

The keyword expression must be configuration, not hard-coded logic.
---

## 8. Canonical Paper Identity

### 8.1 Stable internal primary key

Every canonical research work materialized in an output workspace receives an **internal UUID**.

This UUID is the permanent internal primary key within that workspace. Its stability contract is workspace-local: Literature Monitor does not promise that the same research work materialized independently in two different workspaces will receive the same UUID.

External identifiers such as DOI, OpenAlex ID, arXiv ID, Crossref ID, or publisher IDs are attributes and matching evidence; they are not the internal primary key.

### 8.2 Markdown filename

Paper Markdown filenames use:

```text
<readable-slug>--<short-uuid>.md
```

Requirements:

- the full internal UUID remains stored in frontmatter;
- the short UUID is derived from the internal UUID and collision-checked;
- title changes must not change the paper identity;
- filenames may remain unchanged after creation even if title metadata later improves;
- renaming files during routine metadata enrichment should be avoided.

---

## 9. Canonical Paper Model

Conceptual structure:

```text
CanonicalPaper
├── identity
├── canonical_metadata
├── external_ids
├── authors
├── versions[]
├── sources[]
└── workflow
```

### 9.1 Identity

```yaml
id: <internal UUID>
```

Required.

### 9.2 Canonical metadata

```yaml
title:
journal:
publication_date:
abstract:
author_keywords:
doi:
```

Rules:

- `title` is required;
- `journal` is required;
- at least one author is required for normal candidate creation;
- `publication_date` should be present when available;
- `abstract` is optional but should contain the full abstract when available;
- `author_keywords` is optional;
- `doi` is optional.

No missing abstract may be replaced with a generated summary.

### 9.3 External identifiers

```yaml
external_ids:
  openalex:
  doi:
  arxiv:
  crossref:
  semantic_scholar:
```

All except the internal UUID are optional. The `semantic_scholar` example is retained for historical durable-data compatibility; current production retrieval does not populate it.

The schema should allow future additional identifiers without migration of the identity model.

### 9.4 Authors

Canonical author data should be structured before Markdown rendering:

```yaml
authors:
  - name:
    openalex_id:
    orcid:
```

Rules:

- `name` is required;
- `openalex_id` is preferred when available;
- `orcid` is optional;
- display names are not sufficient as the sole identity signal when a stable OpenAlex author ID exists.

### 9.5 Sources / provenance

```yaml
sources:
  - provider: openalex
    record_id:
    retrieved_at:
  - provider: crossref
    record_id:
    retrieved_at:
  - provider: semantic_scholar
    record_id:
    retrieved_at:
```

The purpose is to preserve enough provenance to understand where metadata came from. The `semantic_scholar` example represents historical provenance that remains readable and must not be deleted during reruns; it does not define a current retrieval provider.

MVP does not need to archive full raw API responses in every Markdown file.

### 9.6 Versions

```yaml
versions:
  - kind:
    source:
    identifier:
    url:
    date:
```

Supported `kind` values:

```text
journal_final
journal_online
accepted_manuscript
preprint
unknown
```

The system records only versions it actually discovers. It must not invent an unseen review/version history.

---

## 10. Canonicalization and Deduplication

The system should determine whether two source records represent the same research work using evidence in descending reliability.

Provider evidence may exist independently before consolidation. Crossref-only current production evidence is valid input when it satisfies the journal/date boundary, and canonicalization must not require an OpenAlex record. Provider-neutral canonicalization and data structures may also accept and preserve valid historical evidence, provenance, and identifiers from retired providers, consistent with §6.3.

### 10.1 Match priority

```text
1. Exact DOI match
2. arXiv external DOI / explicit preprint-to-publication relation
3. Explicit external-database relation
4. Normalized title + compatible author evidence
```

Rules:

- high-confidence identifier matches may merge automatically;
- title-only matching is insufficient for silent merge;
- title + author fallback must be conservative;
- low-confidence cases should remain separate rather than risk a false merge;
- canonicalization must not depend on first-seen-wins behavior.

### 10.2 Metadata merge policy

Metadata enrichment must not use unconditional last-write-wins.

General rules:

- field selection uses explicit, deterministic, provider-neutral normalization and completeness rules;
- normalized DOI and other high-confidence identifiers may join records across providers;
- the first-seen provider has no implicit canonical authority;
- an existing nonempty canonical value should not be silently overwritten by a conflicting enrichment value unless an explicit normalization rule allows it;
- conflicting values should remain inspectable through provenance/logging;
- all contributing external identifiers and provenance must be retained;
- no provider value may be invented to fill missing metadata;
- consolidation and enrichment must be deterministic and idempotent.

---

## 11. Preferred Version Policy

If multiple records are confirmed to represent the same research work, the system stores all discovered version metadata but selects one preferred version.

Priority:

```text
journal final
    > journal online
    > accepted manuscript
    > latest preprint
```

Within the same version class, prefer the latest dated version.

Important distinction:

> Selecting a preferred version does not delete the other version records.

Example:

```text
arXiv v1
arXiv v2
journal online
journal final
```

All may remain in `versions[]`, while `preferred_version` points to `journal_final`.

MVP does not compare PDF/text differences between versions.

---

## 12. Markdown as Durable Workflow State

Markdown files are the durable user-facing state for candidate decisions.

SQLite FTS5 may be used only for the transient runtime index described in §7. The reconstructible Provider-state DB in §30.4 is permitted, but no SQLite or other durable database is a mandatory second source of truth for workflow state.

An implementation may use disposable caches or indexes for speed, but they must be reconstructible from configuration, source APIs, and the Markdown corpus.

The supported product relationship is:

```text
Monitor definition
→ discovery/query configuration

one monitor
→ one decision workspace

Workspace
→ durable research corpus + decision context

Paper Markdown
→ workspace-local Paper identity
→ candidate / rejected / kept / in_zotero
→ human notes and other durable Paper state
```

Paper UUID stability, workflow decisions, human-authored notes, and other durable Paper state are scoped to the workspace. The product does not promise a shared UUID for the same research work across different workspaces, does not inherit Reject/Keep decisions from one monitor's workspace into another, and has no cross-monitor global decision registry.

The long-term sources of truth remain:

```text
monitor.yaml
→ Monitor configuration

journal data file
→ Journal configuration

workspace/Papers/*.md
→ Paper identity, bibliographic metadata, and workflow decisions

workspace/Authors/*.md
→ Author durable materialization
```

The local Web UI must derive its state from these files and the current run process. It must not add a GUI database, persisted Inbox membership, run history, generic job records, frontend workflow state, or a second durable Paper decision state. The concrete journal data-file syntax remains a storage/parser concern and is not frozen by v0.4.0.

---

## 13. Paper Markdown Schema

One canonical paper corresponds to one Markdown file.

Recommended initial structure:

```yaml
---
type: paper
id: <internal UUID>
title:
authors:
  - "[[Author A]]"
  - "[[Author B]]"
journal:
publication_date:
doi:
openalex_id:
arxiv_id:
author_keywords:
status: candidate
discovered_at:
preferred_version:
zotero_key:
---
```

Structured `versions` and provenance may be stored in frontmatter or another machine-readable block, provided reruns can update them safely without overwriting human-authored prose.

Suggested body:

```markdown
# <Title>

## Abstract

<full abstract, or an explicit missing marker>

## Versions

<machine-maintained version summary>

## Sources

<machine-maintained provenance summary>

## Notes

<human-authored content>
```

### 13.1 Missing abstract

If unavailable:

```text
Abstract unavailable from current MVP data sources.
```

Do not generate or infer an abstract.

---

## 14. Ownership of Markdown Content

This boundary is frozen.

### 14.1 System-managed content

The program may maintain bibliographic/system metadata such as:

- title;
- journal;
- publication dates;
- DOI;
- OpenAlex ID;
- provider-specific external identifiers, including historical Semantic Scholar IDs;
- arXiv ID;
- author identities;
- author keywords;
- abstract;
- versions;
- sources/provenance;
- preferred version;
- discovered timestamps;
- exported identifiers;
- Zotero key if the user later records one.

### 14.2 Human-managed content

The user owns:

- `status`;
- free-form notes;
- reading notes;
- questions;
- any other human-authored body sections.

The program must not silently revert:

```text
rejected → candidate
kept → candidate
in_zotero → kept/candidate
```

The program must preserve unknown frontmatter fields and human-created body sections whenever reasonably possible.

---

## 15. Workflow State Machine

Allowed states:

```text
candidate
rejected
kept
in_zotero
```

State transitions are human-directed. They may be recorded by editing Paper Markdown directly, by the Obsidian workflow, or by the v0.4.0 decision application boundary, but the durable state remains the Paper Markdown `status` field:

```text
candidate ──human──> rejected
candidate ──human──> kept
kept      ──human──> in_zotero
```

Review Inbox does not change this state machine. It must not add Track B states or durable workflow fields, including `maybe`, `deferred`, `reviewing`, `screened`, `archived`, `reviewed_at`, `review_batch`, `review_priority`, `rejection_reason`, `inbox_seen`, or `inbox_order`.

### 15.1 candidate

The paper matched the configured journal/date/keyword rules and has a Markdown record awaiting review.

### 15.2 rejected

The user reviewed the paper and does not want to keep it.

Requirements:

- Markdown remains permanently stored;
- future runs must recognize it;
- it must not re-enter the candidate inbox merely because it is rediscovered;
- metadata may still be enriched safely if the record is encountered again.

### 15.3 kept

The user wants to keep the paper and send it to the downstream Zotero workflow.

The project does not ingest it into Zotero directly in MVP.

### 15.4 in_zotero

The user confirms that the item has entered Zotero.

MVP does not require that a PDF attachment exists for this state.

---

## 16. Author Entity Materialization

Every discovered author should be linkable from Paper Markdown.

Paper frontmatter should render authors as Obsidian wikilinks:

```yaml
authors:
  - "[[Author A]]"
  - "[[Author B]]"
```

Each author note should minimally contain:

```yaml
---
type: author
name:
openalex_id:
orcid:
---
```

Requirements:

- reuse the same Author note when a stable OpenAlex author ID matches;
- do not create duplicate author notes on repeated runs;
- name-only identity should be treated cautiously when no stable identifier exists;
- author notes remain deliberately minimal in MVP;
- no automatic author ranking, biography generation, topic inference, or research-profile summarization.

The purpose is to allow Obsidian Graph/Local Graph to reveal repeated authors and coauthor structure naturally as the paper corpus grows.

---

## 17. Safe Rerun Semantics

Repeated runs over overlapping date windows are normal and must be safe.

These are idempotent overlapping-rerun semantics, not provider incremental synchronization. Literature Monitor does not persist a cursor, watermark, last-successful-run timestamp, last-seen timestamp, or other state that changes the publication-date discovery window.

Requirements:

- existing canonical papers are updated, not recreated;
- existing internal UUIDs never change;
- rejected papers remain rejected;
- kept papers remain kept;
- in_zotero papers remain in_zotero;
- author notes are reused;
- newly discovered versions append/merge into the same canonical paper when matching confidence is sufficient;
- system-managed metadata may improve;
- human-authored content must survive reruns;
- the same candidate must not be repeatedly surfaced as new.

The durable state unit is the **paper**, not the journal issue.

---

## 18. Obsidian Review Inbox Presentation

### 18.1 Product role and workspace boundary

Review Inbox is an Obsidian Bases presentation artifact:

```text
Papers/*.md
→ Inbox.base
→ Obsidian review interface
```

Paper Markdown remains the only durable user-facing workflow state. `Inbox.base` stores presentation configuration such as filters, views, sorting, columns, presentation formulas, and layout; it must not become a second workflow-state store or membership database.

The Inbox observes only Paper Markdown files in the `Papers/` directory belonging to the same Literature Monitor output workspace as `Inbox.base`. Its scope is semantically equivalent to all of the following:

- the item is a Markdown file;
- the file belongs to `<output-dir>/Papers/`;
- the file has `type == paper`.

`output-dir` may be nested inside an Obsidian vault. For example:

```text
Vault/
└── Research/
    └── LiteratureMonitor/
        ├── Inbox.base
        ├── Papers/
        └── Authors/
```

In this example, the Inbox observes only `Research/LiteratureMonitor/Papers/`. It must not observe another `Papers/` directory at the vault root, another Literature Monitor workspace, `Authors/`, ordinary notes, README files, `.base` files, or attachments.

This specification freezes the observable workspace boundary, not the concrete Obsidian `.base` serialization.

### 18.2 Default views and presentation

The default `Inbox.base` provides these views and opens `Inbox` by default:

| View | Membership derived from Paper Markdown |
| --- | --- |
| Inbox | `status == candidate` |
| Kept | `status == kept` |
| Rejected | `status == rejected` |
| In Zotero | `status == in_zotero` |

Literature Monitor stores no separate Inbox membership. After a user edits a Paper Markdown `status`, Obsidian recalculates view membership without a synchronization command or synchronization state.

The default presentation exposes these labels using the existing Paper properties:

| Presentation label | Paper property |
| --- | --- |
| Paper | `title` |
| Journal | `journal` |
| Publication Date | `publication_date` |
| Authors | `authors` |
| Author Keywords | `author_keywords` |
| Discovered At | `discovered_at` |
| Status | `status` |

The Paper column is presentation-only navigation: its display text prefers the existing `title` and opens the corresponding Paper Markdown. A `.base` file may use formula or display configuration for this behavior, but materialization must not add durable properties such as `inbox_title` or `display_title`.

Default sorting has this precedence:

```text
discovered_at DESC
publication_date DESC
title ASC
```

The concrete `.base` YAML representation remains an A1 implementation detail and must be based on the format generated by the then-current Obsidian Desktop, not guessed from unofficial examples.

### 18.3 Ownership, creation, and idempotency

`Inbox.base` follows creation-only ownership:

```text
missing
→ create the default Inbox.base exactly once

existing regular file
→ preserve byte-for-byte

concurrent creation race
→ preserve the winner or otherwise report safely

existing non-regular or unsafe filesystem target
→ report MaterializationIssue; do not replace or delete it
```

For an existing regular file, materialization does not read it to judge validity, merge it, modify it, restore defaults, or inspect/rebuild the user's layout. User-customized views, filters, sorting, layout, and formulas survive every rerun.

Creation uses exclusive-create semantics and the existing materialization pattern rather than a new persistence framework. It must not create alternative files such as `Inbox (1).base`, `Inbox-2.base`, or `Inbox.default.base`.

The Inbox lifecycle belongs to a successfully initialized, valid output workspace rather than to the current result count. A zero-candidate materialization still creates a missing `Inbox.base`. After the first successful creation, later runs preserve the existing file byte-for-byte and neither create duplicates nor reset presentation configuration.

### 18.4 Failure semantics

Inbox creation is isolated from Paper and Author writes. If Papers and Authors succeed but Inbox creation fails:

- successful Paper and Author writes remain;
- a `MaterializationIssue` is recorded;
- the `materialize` command exits non-zero.

An existing regular `Inbox.base` requires no read or write and is not an error.

### 18.5 Architectural boundary and Track B exclusions

Review Inbox occurs only at the presentation end of the existing pipeline:

```text
retrieval
→ canonicalization
→ Papers / Authors materialization
→ ensure default Inbox presentation
```

It does not participate in provider retrieval, search filtering, evidence consolidation, `CanonicalPaper`, canonicalization, identity matching, version consolidation, candidate inclusion, or workflow-state semantics. As of v0.3.2, the normal persistent-monitor entry point is `literature-monitor run --config monitor.yaml`; `materialize` remains an explicit legacy / diagnostic-style execution entry. v0.3.0 Track A itself added no `inbox-sync`, `review`, `rebuild-inbox`, or other CLI workflow.

The following remain excluded from the workflow-state model:

- Maybe / Deferred states, rejection reasons, review labels, reviewer identity, review batches, review sessions, reviewed timestamps, bulk screening workflow, and priority state;
- BM25, semantic, citation, or LLM ranking;
- automatic summaries, relevance explanations, and recommendations;
- PDF preview or download;
- Zotero API integration;
- an Obsidian plugin;
- a persistent Inbox database.

The v0.4.0 local Web UI is a separate presentation/application adapter over the same Paper Markdown state. It does not alter these Obsidian-specific creation-only semantics or create another Inbox membership store.

---

## 19. Kept-Paper Export for Zotero

MVP deliberately reuses Zotero's existing identifier-import capabilities instead of reimplementing them.

The project should provide a simple export containing only papers whose state is:

```text
kept
```

and excluding:

```text
in_zotero
```

### 19.1 Preferred export identifier

Priority:

```text
DOI
→ other stable identifier if useful
→ title + basic metadata for manual handling when no suitable identifier exists
```

The exact export format may be a plain text file, Markdown list, or similarly simple artifact, but it must be easy to paste/use with Zotero's existing DOI/identifier import workflow.

### 19.2 Out of scope

The project does not:

- call Zotero write APIs;
- create Zotero items itself;
- download PDFs;
- manage Zotero attachments;
- guarantee PDF availability;
- automatically switch the Markdown status to `in_zotero`.

The user changes the status to `in_zotero` after successful downstream import.

---

## 20. CLI / Execution Surface

The normal user entry point is:

```bash
literature-monitor run --config monitor.yaml
```

Existing diagnostic commands remain available. `materialize` remains an explicit legacy / diagnostic-style execution entry, and its existing explicit `--output-dir` behavior is unchanged.

### 20.1 Persistent monitor definition

A monitor is defined by one YAML file. The supported monitor fields are:

```yaml
name:
venue_whitelist:
keyword_expression:
output_dir:
from_date:
to_date:
window_days:
log_level:
```

`keyword_expression` is the only core field without a default and therefore the only field required in a minimal monitor definition.

The monitor definition is local-first configuration: it is intended to be movable, copyable, and suitable for version control. The configuration file does not contain execution identity or durable run state. In particular, the current product does not introduce:

```text
monitor_id
monitor_version
workspace_id
workspace_owner
last_successful_run
last_run_at
last_seen_at
last_used_range
cursor
watermark
checkpoint
run_history
delta state
notification state
scheduler state
persistent execution database
```

Paper Markdown remains the durable workflow state and retains ownership of workspace-local stable Paper UUIDs, `rejected` / `kept` / `in_zotero` status, and human-authored notes.

Monitor configuration uses strict validation. Unknown fields are errors. For example, `window_day: 14` must fail rather than silently falling back to the `window_days` default.

Missing fields and explicit YAML `null` are distinct. Defaults apply only when the corresponding field or date-policy fields are absent. Explicit `null` is invalid for every monitor field; for date fields it does not count as absence and must not activate the default rolling window.

The configuration defaults are:

- `name`: the monitor config filename stem. For `/research/causal-inference.yaml`, the default name is `causal-inference`. The name is used only for display, logging, and future UI. It does not define monitor identity, workspace paths, Paper identity, or date calculation. An explicit empty string or `null` is invalid.
- `venue_whitelist`: `<config-directory>/list.md`. A relative explicit path is also resolved from the monitor config directory. The loader must not search parent directories, shell cwd, a global list, or other Markdown files. If the default or explicit resolved file does not exist, configuration loading fails and reports the resolved path. Explicit `null` is invalid.
- `keyword_expression`: no default. Missing, `null`, or an empty string is invalid and must fail before any provider network request. It must never be interpreted as match-all.
- `output_dir`: `<config-directory>/workspace`. A relative explicit path is resolved from the monitor config directory. It must not default to shell cwd, HOME, an Obsidian vault, or another machine-specific fixed path. Explicit `null` is invalid. A missing output directory is initialized by the existing materialization behavior when materialization occurs.
- date policy: when `from_date`, `to_date`, and `window_days` are all absent, the config/domain layer applies `window_days = 14`. This default is resolved in memory and is not written back to YAML.
- `log_level`: `INFO`. Existing case normalization remains in force. An invalid value or explicit `null` is an error.

Absolute paths remain valid. Relative `venue_whitelist` and `output_dir` semantics depend only on `config_path.parent`, never on the shell cwd.

The supported decision model is one monitor per decision workspace. The recommended layout is one monitor config directory per monitor, which naturally gives each monitor its own default `<config-directory>/workspace`, or an explicitly distinct `output_dir` for every monitor.

The existing path rule is not reinterpreted: if two monitor YAML files are in the same directory and both omit `output_dir`, both resolve to that directory's same `workspace` path. The current version does not reject this arrangement and does not add owner validation, a monitor marker, or a workspace registry. Explicitly or implicitly sharing one workspace across multiple monitors is unsupported / undefined advanced usage; no cross-monitor Paper-UUID, membership, Reject/Keep inheritance, or other decision-semantics guarantee is provided.

`--journal` remains part of existing diagnostic behavior only and does not enter the persistent monitor definition. API keys, Crossref mailto, and similar runtime/environment settings also remain outside monitor YAML. Existing keyword-expression CLI override behavior is not redesigned by v0.3.2.

### 20.2 Date policy and resolved runtime range

The persistent date policy has exactly two degrees of freedom. Date ranges are inclusive:

```text
window_days = (to_date - from_date).days + 1
from_date = to_date - (window_days - 1 days)
to_date = from_date + (window_days - 1 days)
```

Legal persistent forms are:

```text
no date fields
window_days
from_date + to_date
from_date + window_days
to_date + window_days
```

No date fields means the default rolling `window_days = 14`.

The following forms are invalid:

```text
from_date only
to_date only
from_date + to_date + window_days
window_days < 1
from_date > to_date
```

All three fields must be rejected even when they are mathematically consistent. There is no precedence rule among three simultaneously configured date fields.

A rolling policy resolves at runtime as:

```text
window_days: N
→ to_date = today
→ from_date = today - (N - 1 days)
```

Date arithmetic uses standard `datetime.date` semantics. This includes `window_days = 1`, leap days, month boundaries, and year boundaries. Resolved runtime dates are ephemeral and must not be written back to the monitor config.

The persistent policy and the resolved runtime range are distinct concepts. Defaults belong to the config/domain contract; CLI commands, provider adapters, materialization code, and the v0.4.0 Web UI must not define competing default semantics. GUI Run always uses the persisted Monitor date policy and offers no temporary date override.

### 20.3 CLI date override contract

The date-bearing commands are:

```text
run
openalex-discover
crossref-discover
openalex-filter
crossref-enrich
canonicalize
materialize
```

`validate` and `export-kept` are not date-bearing commands.

Every date-bearing command supports:

```text
--from-date
--to-date
--window-days
```

Date precedence has only two layers:

```text
no CLI date args
→ use the monitor config date policy

at least one CLI date arg
→ the CLI date args form a complete override
→ all config date fields are ignored for this invocation
```

CLI and config date fields must never be merged field-by-field. For example:

```text
config: window_days = 14
CLI: --to-date 2026-09-01
→ error
```

The CLI may not borrow `window_days` or another missing date component from the config once any CLI date argument is present.

Legal CLI overrides are:

```text
--window-days N
--from-date X --to-date Y
--from-date X --window-days N
--to-date Y --window-days N
```

Invalid CLI overrides are:

```text
--from-date X
--to-date Y
all three date fields
--window-days 0
from_date > to_date
```

All three CLI date fields are invalid even when they are mathematically consistent.

The legacy form:

```bash
literature-monitor openalex-discover \
  --config monitor.yaml \
  --from-date 2026-01-01 \
  --to-date 2026-01-31
```

must retain the same observable behavior. CLI overrides are ephemeral runtime input: they are not written to YAML and do not create a last-used range or any other override state.

All date-bearing commands must share the same observable date-resolution semantics; internal resolver names, class names, and file layout are not product contracts.

### 20.4 Normal run and production-pipeline reuse

`literature-monitor run --config monitor.yaml` remains the normal CLI persistent-monitor execution path. In v0.4.0, both CLI Run and GUI Run call the stable `application.monitor.run_monitor(...) -> RunResult` boundary. It obtains the following from the monitor definition after defaults and path/date resolution:

```text
journals
keyword expression
output directory
date policy
log level
```

The shared canonical production core is:

```text
load monitor
→ resolve journals
→ resolve effective date range
→ retrieval
→ provider evidence consolidation
→ local matching
→ canonicalization
```

CLI `run`, GUI Run, CLI `canonicalize`, and CLI `materialize` must reuse this common core rather than copying its provider retrieval, evidence consolidation, local matching, or canonicalization orchestration.

Production Run additionally uses the revision-validated state boundary in §30; diagnostics, `canonicalize`, and legacy/diagnostic `materialize` do not read or write persistent Provider state. They share the core algorithms without copying orchestration. The retained OpenAlex version-hydration step in §30.2 sits after local matching and before canonicalization, and affects version construction only.

The output/materialization boundaries diverge after canonicalization:

```text
CLI canonicalize
→ stop after canonicalization
→ output canonical papers

CLI materialize
→ take canonical papers from the shared core
→ invoke the formal materialization path

CLI run / GUI Run
→ take canonical papers from the shared core
→ invoke the same formal materialization path
```

`run_monitor()` remains the formal common entry point for CLI `run` and GUI Run. This split must not change the existing observable behavior of `canonicalize` or `materialize`; `canonicalize` remains a read-only diagnostic command, and `materialize` retains its explicit `--output-dir` behavior. Lower-level diagnostic commands may continue to call existing lower-level modules when their diagnostic purpose requires it.

### 20.5 Validate configuration

Running:

```bash
literature-monitor validate --config monitor.yaml
```

and the v0.4.0 application boundary `application.monitor.validate_monitor(...) -> ValidationResult` share the same validation path and semantics. Validation has a strict boundary:

```text
load config / whitelist
→ deterministic local runtime preflight
→ OpenAlex Source resolution
```

Before any OpenAlex Source-resolution network request, validation must complete all deterministic checks required by a normal persistent `run`: strict monitor-field and whitelist loading, keyword parser syntax validation, date-policy form validation, FTS5-dependent lexical semantic validation of the configured keyword expression, and effective runtime date-range resolution from the monitor's `date_spec`.

Parser-valid expressions may still fail local semantic validation after SQLite FTS5 `unicode61` tokenization. Invalid Prefix or Proximity operands, including a Proximity operand that produces only one lexical token, are local validation errors. A Python SQLite runtime without FTS5 is a local runtime-preflight failure. Runtime date resolution that exceeds the supported `datetime.date` bounds is also a local runtime-preflight failure.

Configuration and deterministic local preflight failures exit with code `2` and must occur before any provider path is constructed or called. OpenAlex Source-resolution errors retain exit code `1`. Warning-only OpenAlex validation and fully valid validation both exit with code `0`. More generally, CLI compatibility remains: completed or valid results exit `0`; provider or materialization errors exit `1`; local configuration or deterministic preflight errors exit `2`.

`validate` performs no OpenAlex Works discovery, Crossref connectivity check, retired-provider connectivity check, `output_dir` writability check, workspace creation, or Paper / Author / Inbox materialization. Its only provider/network validation is OpenAlex Source resolution, using the same batched resolver as production Run (§30.2). It neither reads nor writes persistent Provider state.

### 20.6 Existing behavior preserved

The v0.3.3 hardening preserves the existing observable behavior of:

- OpenAlex retrieval;
- Crossref retrieval and DOI supplementation;
- provider-neutral evidence consolidation and durable historical-provider compatibility;
- local FTS5 lexical filtering;
- v0.3.1 Prefix semantics;
- v0.3.1 Proximity semantics;
- canonicalization;
- Paper / Author materialization;
- Review Inbox behavior;
- `export-kept`;
- Paper Markdown durable-state ownership;
- workspace-local Paper UUID stability;
- persistence of `rejected`, `kept`, and `in_zotero`;
- preservation of human notes.

### 20.7 Export kept papers

Output identifiers for papers with:

```text
status = kept
```

while skipping `in_zotero`.

---

## 21. Error Handling

A partial failure must not invalidate the whole run.

The system must tolerate:

- one unresolved journal;
- one provider being unavailable;
- one failed provider page/request;
- one provider lacking a record or field that another provider supplies;
- missing DOI;
- missing abstract;
- missing author keywords;
- incomplete ORCID data;
- a paper with no usable Zotero identifier.

Requirements:

- failures are logged;
- unresolved venues are reported prominently;
- no missing field is replaced with invented content;
- provider, journal, and request failures are isolated from one another;
- successful evidence continues through consolidation, local filtering, canonicalization, and materialization even when another retrieval or enrichment operation fails;
- retries should not create duplicates.

---

## 22. Reuse and Prior-Art Boundary

### 22.1 Gian-Hacher/Paper_tracker

Borrow design ideas for:

- venue whitelist configuration;
- OpenAlex source resolution;
- source + date retrieval;
- local keyword filtering.

Do not reuse as the core data model/state layer:

- its paper model;
- dedup logic;
- SQLite schema;
- daily/weekly Markdown renderer.

If its repository lacks a clear license, implementation should be independently written rather than copied.

### 22.2 zcz718/PaperRadar

MIT-licensed components may be reused where appropriate, preserving required license notices.

Highest-value reusable areas:

- source-adapter pattern;
- OpenAlex/Crossref parsing helpers;
- per-paper Markdown materialization ideas/code;
- defensive HTTP/config helpers.

Do not adopt its orchestration assumptions:

- keyword-first global discovery;
- recommendation scoring;
- LLM reranking;
- automatic Zotero ingestion.

Its Zotero write/attachment code is not needed in MVP because Zotero ingestion has been removed from project scope.

### 22.3 ansatzX/PaperTrack

Borrow design ideas for:

- source-authority/fallback reasoning;
- Crossref ISSN querying;
- incremental-state thinking;
- arXiv ↔ publisher DOI matching concepts.

Do not adopt:

- provider-specific `if` architecture;
- issue-level state;
- issue-level Markdown rendering;
- GPL code unless the project intentionally accepts the relevant licensing consequences.

### 22.4 Zotero

Reuse Zotero's existing DOI/identifier import workflow and existing Zotero plugins where useful.

Do not build a new Zotero ingestion subsystem in MVP.

---

## 23. Acceptance Criteria

MVP is complete only when the following are demonstrated.

### 23.1 Whitelist and venue validation

Given the supplied journal whitelist with conferences excluded:

- configured ISSNs can be validated;
- provider-specific venue/source identifiers are resolved where applicable, or an explicit unresolved/ambiguous error is reported;
- ISSN/EISSN is preferred for venue identity, with strict normalized journal-name fallback only when a provider record lacks usable ISSN/EISSN;
- no journal is silently dropped.

### 23.2 Journal/date multi-source retrieval

For a selected date window:

- discovery membership uses publication date only; Crossref update-date, created-date, index-date, provider update timestamps, and persisted sync state do not extend the window;
- OpenAlex is the sole primary discovery provider;
- Crossref independently contributes secondary journal/date candidates and may supply DOI/bibliographic evidence;
- a candidate is not required to exist in OpenAlex first when Crossref provides valid journal/date evidence;
- global keyword search is not used to define the candidate universe;
- Semantic Scholar is not called by the production retrieval pipeline.

### 23.3 Evidence consolidation and keyword filtering

Given test expressions using Terms, Phrases, Prefix, Proximity, `AND`, `OR`, `NOT`, and parentheses:

- records with the same normalized DOI consolidate into one identity cluster;
- available provider evidence is consolidated before local filtering;
- only Title + Author Keywords + Abstract are searchable;
- an eligible field supplied by any provider in the cluster participates in filtering as its own searchable unit;
- a missing abstract or author keywords degrades safely and does not by itself exclude the work;
- provider-derived keywords, topics, or fields of study remain non-searchable and are not silently substituted for author keywords;
- Boolean operands, including Prefix and Proximity operands, compose at the consolidated-work level and may match across searchable units and across providers when the Boolean structure permits it;
- the existing grammar continues to accept punctuation-containing Terms, and Term content is never interpreted verbatim as SQLite FTS5 query syntax;
- after normalization, a Term's complete nonempty `unicode61` token sequence must occur contiguously within one searchable unit and cannot span units;
- a Term that produces no lexical tokens does not match any research work;
- a quoted phrase matches only when its full token sequence occurs within one searchable unit and cannot span fields, keywords, or provider records;
- lexical normalization applies NFKC, then Unicode casefold, then whitespace normalization before `unicode61` tokenization, preserving matches such as `strasse` against `Straße`;
- punctuation follows SQLite FTS5 `unicode61` token semantics, including lexical equivalence between token sequences such as `high-dimensional` and `high dimensional`;
- Prefix accepts exactly one trailing `*`; after NFKC and casefold its base contains at least three lexical characters, contains only Unicode letters or digits, and tokenizes to exactly one `unicode61` token;
- leading wildcards, mid-word wildcards, multiple `*` characters, and invalid Prefix bases are rejected; `?` has no wildcard meaning and quoted `*` has no Prefix meaning;
- Prefix matches a lexical token prefix rather than an arbitrary substring, so `statist*` matches `statist`, `statistic`, `statistics`, and `statistical` but not `biostatistics`;
- Proximity accepts `"a b"~N` with optional whitespace between the closing quote and `~N`, requires an explicit integer `N` in `0 <= N <= 50`, and requires at least two lexical tokens after normalization and tokenization;
- Proximity is unordered and interprets `N` as additional intervening tokens beyond the most compact arrangement, so `"causal inference"~0` is unordered adjacency while the exact Phrase `"causal inference"` remains ordered adjacency;
- a `k`-token Proximity operand compiles with `fts_near_distance = user_distance + k - 2`;
- each Prefix or Proximity match is satisfied within one searchable unit, and Proximity cannot cross title/abstract boundaries, author keyword values, or provider records;
- a single-token Proximity operand is invalid and is not treated as fuzzy search;
- generated FTS5 queries are compiled from parsed operands and never accept user input as arbitrary `MATCH` or `NEAR(...)` syntax;
- OpenAlex and Crossref retrieval remain journal/date-bounded; neither provider uses a keyword-query projection to decide candidate inclusion;
- if the current Python SQLite runtime lacks FTS5, commands that require local filtering use the existing explicit error path rather than silently changing matching semantics;
- the local search index is transient, reconstructible runtime state and is not a durable or mandatory source of truth.

### 23.4 Candidate creation

For each newly matched work:

- exactly one Paper Markdown is created;
- the paper receives a stable internal UUID;
- the filename follows `slug--short-uuid.md`;
- title, journal, authors, date, and available identifiers are stored;
- the full abstract is stored when available;
- missing abstract is explicitly represented as missing;
- authors appear as Obsidian wikilinks;
- required Author notes are created/reused.

### 23.5 Safe rerun

Run the same overlapping query twice:

- no duplicate paper is created;
- no duplicate Author note is created;
- internal UUIDs remain stable;
- human status is preserved;
- human notes are preserved;
- metadata enrichment may improve existing records.

### 23.6 Rejected persistence

After manually changing a paper to:

```yaml
status: rejected
```

rerunning discovery must not create a new candidate or revert its status.

### 23.7 Version consolidation

Given two records known to represent the same work:

- high-confidence matching consolidates them into one canonical paper;
- both discovered versions remain represented in `versions[]`;
- preferred-version priority follows:

```text
journal final
> journal online
> accepted manuscript
> latest preprint
```

- only one Paper Markdown exists.

### 23.8 Provider-neutral canonicalization

Given valid evidence from multiple providers, from Crossref without OpenAlex, or from persisted historical provider evidence:

- the work can produce a canonical paper without an OpenAlex record;
- records sharing the same normalized DOI produce exactly one canonical work;
- contributing external identifiers and provenance are retained;
- first-seen provider order does not determine canonical authority;
- no missing metadata is invented.

### 23.9 Provider failure isolation

When one provider, journal, or request fails:

- successful evidence from other retrievals remains usable;
- successful evidence can still be consolidated, filtered, canonicalized, and materialized;
- the failure is reported without invalidating unrelated results;
- rerunning after recovery does not create duplicate canonical papers.

### 23.10 Low-confidence dedup safety

Two papers with similar titles but insufficient identifier/author evidence must not be silently merged.

### 23.11 Kept export

After manually setting:

```yaml
status: kept
```

- the paper appears in the Zotero identifier export;
- a paper marked `in_zotero` does not appear in the export;
- DOI is preferred when present;
- lack of DOI does not delete or invalidate the paper record.

### 23.12 User-edit safety

After adding arbitrary human notes to a Paper Markdown, running the updater again must not erase or replace those notes.

### 23.13 Review Inbox

Given a valid Literature Monitor output workspace:

- `materialize` creates `Inbox.base` when it is absent, including on a zero-candidate run;
- the default Inbox view shows only Paper Markdown with `status == candidate` from that workspace's own `Papers/` directory;
- Kept, Rejected, and In Zotero membership is derived respectively from the existing `kept`, `rejected`, and `in_zotero` statuses;
- editing a Paper Markdown `status` changes view membership without an additional synchronization command or state;
- rediscovered `rejected` or `kept` papers do not re-enter Inbox;
- an existing regular `Inbox.base` remains byte-for-byte unchanged, preserving all user customization on rerun;
- a nested `output-dir` observes only its own `Papers/` and not similarly named directories elsewhere in the vault;
- Inbox creation failure preserves successful Paper and Author writes, records a `MaterializationIssue`, and makes `materialize` exit non-zero;
- the feature adds no workflow states, durable Inbox membership, second source of truth, external service, Obsidian community plugin, or Python runtime dependency;
- `export-kept` behavior remains unchanged.

### 23.14 Persistent monitor definition, validation, and workspace boundary

Given the v0.3.2 persistent-monitor model with the v0.3.3 correctness hardening:

- a minimal monitor requires only a valid `keyword_expression` as an explicit YAML field; omitted fields use their defined defaults, subject to the resolved default `list.md` existing;
- a missing `name` resolves to the config filename stem, while explicit empty string or `null` is rejected;
- a missing `venue_whitelist` resolves exactly to `<config-directory>/list.md`, and loading fails with the resolved path when that file does not exist;
- a missing `output_dir` resolves exactly to `<config-directory>/workspace`;
- relative `venue_whitelist` and `output_dir` paths resolve from `config_path.parent` and produce the same result regardless of shell cwd;
- a missing, `null`, or empty `keyword_expression` fails before any provider network request and never becomes match-all;
- with all date fields missing, the effective persistent policy is rolling 14 days without mutating the YAML;
- a missing `log_level` resolves to `INFO`, existing case normalization remains valid, and an invalid or explicit-null level is rejected;
- explicit `null` never activates a missing-field default, including the default rolling date policy;
- unknown monitor fields are rejected, including a misspelled `window_day`;
- all legal persistent date forms are accepted: no date fields, `window_days`, `from_date + to_date`, `from_date + window_days`, and `to_date + window_days`;
- `from_date` alone, `to_date` alone, all three date fields together, `window_days < 1`, and `from_date > to_date` are rejected;
- date arithmetic is inclusive and satisfies `window_days = (to_date - from_date).days + 1`, including one-day windows and ranges crossing leap days, month boundaries, or year boundaries;
- a rolling `window_days: N` resolves to `to_date = today` and `from_date = today - (N - 1 days)`;
- resolved runtime dates are not written back to the monitor YAML;
- `validate --config` performs FTS5 lexical semantic validation and effective runtime date-range resolution before constructing or calling the OpenAlex Source-resolution path;
- local configuration, FTS5 semantic/backend, and runtime date-resolution failures from `validate` exit `2`; OpenAlex Source-resolution errors exit `1`; warning-only and fully valid validation exit `0`;
- `validate` does not perform OpenAlex Works discovery, Crossref or retired-provider connectivity checks, output-directory writability checks, workspace creation, or Paper / Author / Inbox materialization;
- `literature-monitor run --config monitor.yaml` is the normal persistent-monitor execution entry and obtains journals, keyword expression, output directory, date policy, and log level from that monitor;
- all date-bearing commands expose `--from-date`, `--to-date`, and `--window-days` with the same date-resolution semantics;
- when no CLI date argument is supplied, the monitor date policy is used;
- once any CLI date argument is supplied, CLI date arguments completely replace the monitor date policy for that invocation and may not borrow missing components from config;
- `--window-days N`, `--from-date X --to-date Y`, `--from-date X --window-days N`, and `--to-date Y --window-days N` are valid complete CLI overrides;
- CLI `--from-date X` alone, `--to-date Y` alone, all three CLI date fields, zero/negative window sizes, and reversed ranges are rejected;
- the legacy `openalex-discover --config ... --from-date ... --to-date ...` form preserves its observable from/to behavior;
- CLI date overrides remain ephemeral and create no config mutation, last-used range, checkpoint, or other durable run state;
- `run` reuses the OpenAlex/Crossref retrieval, evidence consolidation, local filtering, canonicalization, and materialization production path rather than implementing a second pipeline;
- `materialize` remains an explicit legacy / diagnostic-style entry and its existing explicit `--output-dir` behavior is preserved;
- one monitor to one decision workspace is the supported product relationship; Paper UUIDs, statuses, notes, and other durable Paper state are workspace-local;
- different workspaces are not promised the same UUID for the same research work and do not inherit Reject/Keep decisions from one another;
- one monitor per config directory, or explicitly distinct `output_dir` values, is the recommended multi-monitor layout;
- two monitor YAML files in the same directory that both omit `output_dir` continue to resolve to the same `<config-directory>/workspace`; this is not rejected, but shared-workspace multi-monitor operation is unsupported / undefined advanced usage with no cross-monitor decision guarantee;
- no monitor UUID, workspace UUID, ownership marker, workspace registry, global research-work registry, cross-monitor Paper UUID, per-monitor decision object, monitor membership state, decision inheritance, last-run execution-control semantics, run history, scheduler/notification state, provider cursor/watermark/checkpoint persistence, or persistent execution database is introduced; the observational snapshot (§6.6) and reconstructible Provider-state DB (§30.4) are narrowly defined exceptions to blanket metadata-persistence exclusions;
- Paper Markdown remains the durable workflow state, with no Paper Markdown schema change in v0.3.3 and no regression to workspace-local UUID stability, statuses, or human-note preservation.

### 23.15 v0.4.0 Local Web UI and application boundaries

The v0.4.0 implementation is accepted only when all of the following hold:

- a provider can fail while other evidence still canonicalizes and materializes successfully; the application result reports `COMPLETED_WITH_ERRORS`, preserves the successful writes, and the CLI retains exit code `1`;
- a corrupt or unreliably parsed Paper is isolated from Inbox/Kept/Rejected/In Zotero views and appears in workspace issues without preventing valid Papers from loading;
- decision writes preserve `Notes`, every human-managed body section, and unknown frontmatter fields while changing only `status`;
- a decision whose expected current status no longer matches disk state returns a state conflict and does not overwrite the newer state;
- compare-before-replace detects a file change between read and replace and refuses to overwrite it silently;
- an unknown Paper UUID, duplicate/ambiguous UUID location, or unsafe/non-regular target produces a safe decision failure with no mutation;
- an invalid workflow transition is rejected and no arbitrary status mutation surface exists;
- Settings Save aborts before writing when either file revision has changed since Settings open;
- if the journal file write succeeds and the monitor config write then fails, Settings reports a partial save, rereads real disk state, and does not report success;
- an invalid or missing monitor config still allows the Web application to start, and `GET /` provides a recoverable HTML state with access to Settings rather than requiring a generic HTTP 500;
- a missing or invalid journal data file, or a workspace that cannot form a normal view, still permits application startup and a recoverable `GET /` HTML state;
- expected decision, Settings, and run-already-active failures produce understandable application HTML states rather than generic HTTP 500 responses;
- `POST /settings/validate` validates the current unsaved `MonitorDraft` with shared deterministic rules and performs no config write, journal-data write, or provider request;
- only one GUI run may be active in the process at a time, repeated starts do not create a second production run, and no run history is persisted;
- RunCoordinator state reads use a small lock to obtain immutable snapshots, release the lock before HTML rendering or other slow work, and the HTTP server remains responsive while synchronous `run_monitor()` executes in its dedicated worker;
- GUI Run exposes no temporary date override and uses only the persisted Monitor date policy;
- CLI completed/valid, provider/materialization-error, and local-configuration/preflight outcomes retain exit codes `0`, `1`, and `2` respectively;
- CLI `run`, CLI `canonicalize`, CLI `materialize`, and GUI Run demonstrably reuse the same canonical production core through canonicalization; `canonicalize` stops there, while `materialize`, CLI `run`, and GUI Run pass the resulting canonical papers into the formal materialization path;
- Run, Settings Save, and all Paper decision mutations reject requests without the valid process-start CSRF token;
- the Web server binds only to `127.0.0.1`, rejects unintended Host values, and does not enable broad CORS or LAN serving;
- package templates, vendored HTMX, and other required static assets are present in both wheel and sdist;
- a smoke test from an installed wheel/sdist can create the GUI application and render the required local UI without relying on repository-relative template/static paths or a Node toolchain.

### 23.16 v0.4.3 retrieval efficiency and revision-validated evidence

v0.4.3 is accepted only when §30 is implemented and the following observable scenarios are demonstrated. A1–A8 implementation and independent stage reviews are complete; these scenarios have been demonstrated and passed the final independent audit. These acceptance requirements remain in force except for the narrowly superseded behavior identified in §31.1; v0.4.4 acceptance is defined in §23.17:

- With equivalent current Provider evidence, all-live and revision-validated reuse Runs produce equivalent retained FTS5 clusters and canonical results, preserving UUIDs, publication-date membership, Markdown ownership, decisions, and Zotero behavior. Term, Phrase, Prefix, Proximity, and Boolean expressions remain local; inspected OpenAlex/Crossref requests contain no `keyword_expression` or keyword projection.
- Production Run and `validate` resolve the same Source identities through batches of at most 100 ISSNs. Partial resolution exercises singleton recovery, strict journal/title/ISSN consistency, and conflicting-Source rejection. Terminal auth/quota/circuit failures do not trigger fallback storms.
- Multi-source OpenAlex Works thin requests select Provider field `updated_date`, not `updated_at`, and exclude `locations`. OpenAlex-specific normalization converts `updated_date` to the internal timezone-aware UTC `updated_at` revision marker; values without an explicit timezone are interpreted as UTC because OpenAlex defines this field as UTC. Missing or malformed `updated_date` leaves no reusable revision binding without removing the candidate. This UTC exception does not relax generic revision parsing. Incomplete pages, malformed items, and evidence that cannot map to a configured Source cannot yield false per-journal COMPLETE coverage. Successful safe fallback restores coverage without an optimization warning.
- After local retention, duplicate retained W IDs hydrate once. Historical version reuse compares internal `updated_at` with `hydrated_against_updated_at`; matching revisions reuse versions, while changed or missing revisions force live hydration. Missing-revision hydration is not persisted unbound; hydration failure warns without fabricated hints or discovery-coverage reduction. Version hints cannot change research-work grouping.
- Crossref manifests use repeated ISSN and publication-date filters and expose DOI, ISSN, and canonically parsed `indexed_at`. Manifest and full hydrated record revisions use one shared canonical `indexed_at` parser/normalization rule; state revision equality compares values produced by that same parser, with no path-specific normalization. The bounded manifest planner's batch size remains an internal implementation constant, absent from monitor YAML, CLI configuration, application API configuration, and persisted Provider state. Bounded oversized retrieval splits ISSNs, then non-overlapping date intervals, then uses cursors for oversized single days. Cursor tests preserve the full original query, use the latest `next-cursor`, deduplicate DOI, and reconcile obtained results with `total-results`, including incomplete/malformed traversal.
- Crossref revision matches reuse full normalized state; absent state or changed revision hydrates every consumed field, including relation metadata. Venue mismatch still fails `crossref_record_matches_journal`; historical rows without current live anchors create no candidates. Duplicate DOI across ISSNs hydrates once and can serve multiple current coverage units. When a DOI is confirmed as a current live manifest member but required current full evidence cannot be obtained, every actually affected per-journal/per-ISSN discovery unit is `PARTIAL` or `FAILED` according to the amount/trustworthiness of successfully obtained evidence, never `COMPLETE` or `UNAVAILABLE`; a complete manifest alone cannot hide the failure.
- Recoverable Crossref batch failures split/fall back and preserve successful evidence. OpenAlex-only supplementation tests batched DOI/indexed probes and absent-probe singleton 200, 301/308 prime-DOI, and 404 cases. Alias evidence/state use prime DOI while coverage uses requested DOI; alias mappings are not persisted.
- Semantic-hash tests change consumed fields, revision/retrieval timestamps, and unconsumed fields independently. Only consumed semantic changes alter the hash; a revision-only change still updates revision/retrieval/provenance, and hash equality never bypasses live revision validation.
- The durable DB contains only the §30.4 logical schema with strict/versioned normalized serialization. A successful transaction upserts current changes and retains out-of-window rows; missing DB retrieval is all-live and successful production can create it. No WAL, membership, execution history, or workspace lock is introduced.
- Corrupt/schema-incompatible regular DBs fall back all-live with a PROVIDER_STATE warning. Replacement is a complete fresh app-owned DB followed by atomic replacement, preserving the old file on construction/replacement failure. Symlink, directory, and incompatible objects remain untouched. Locked/busy or other valid-state write failures roll back and preserve previous state; persistence failure leaves materialized Papers/Authors intact.
- Every Provider execution uses at most one pooled synchronous client per Provider, without per-request sessions. Normal, validation, diagnostic, and exception paths close clients. Alias redirects remain observable rather than automatically followed away.
- Stage 2 runs only one OpenAlex and one Crossref branch concurrently. Branches return in-memory results/pending changes, connections do not cross worker threads, and durable writes occur after branch completion at the production boundary. No journal/ISSN pool or async rewrite appears.
- Interleaved Provider callbacks preserve independent source Activities, estimators, and activity ages; one source's progress does not reset the other's ETA. Run-level last activity is the latest real activity from any source. A quiet source cannot alone trigger whole-worker inactivity. Coordinator and CLI serialize updates; non-TTY lines remain intact. The five stages and snapshot-only 750 ms HTMX polling remain unchanged.
- Normal CLI/Web Run automatically uses state; no application cache-mode parameter or Web reuse action remains. The deprecated CLI flag is accepted until v0.5.0, ignored, and emits the exact §30.6 stderr/logging notice without a RunResult warning or outcome change. The obsolete application cache/reuse modules are removed once their real calls disappear.
- Existing `provider-cache.json` bytes remain identical across normal Runs, compatibility-flag Runs, failure paths, and diagnostics, including malformed legacy files. Production performs no read, write, deletion, or migration of it.
- New last-run files remain schema v2 with `reused_units=[]`. Valid v1 and historical v2 with non-empty reuse remain readable. CLI/Web completion separately display the typed transient usage summary; it is absent from Paper Markdown and last-run JSON. Record statistics count evidence used, including reused records.
- Diagnostics, `validate`, `canonicalize`, and legacy/diagnostic `materialize` neither read nor write Provider state; existing state bytes remain unchanged and an absent DB remains absent. State validation/persistence issues use `MonitorIssueComponent.PROVIDER_STATE`.

### 23.17 v0.4.4 elapsed-aware pacing and partial Provider evidence

The §31 implementation is complete. A1–A5 implementation and independent stage reviews are complete; the following observable scenarios have been demonstrated and passed the final independent audit. These acceptance requirements remain in force. v0.4.4 is released:

- Deterministic monotonic-clock checks show that Crossref response latency shorter than the known interval leaves only the remainder to wait, while latency equal to or longer than that interval leaves no proactive wait. Wall-clock changes have no effect. Interleaved singleton DOI and list/filter requests retain independent header-derived intervals/deadlines; missing or malformed headers preserve the corresponding last valid state without a pacing-only probe or successful-response failure.
- Retry checks cover the existing maximum of three attempts, HTTP 429/5xx and supported transport/timeout failures, immediate failure for other HTTP 4xx, endpoint-specific 404 behavior, and the 1-second/2-second fallback. A known class interval is satisfied without adding redundant full pacing and retry sleeps. Actual proactive waits report `WAITING`; retries after real failures report `RETRYING` with unchanged Activity identity and stage values.
- OpenAlex normalization retains DOI-only and title-only records, empty/unusable authors as empty authors, malformed/missing abstract as missing, and invalid DOI with usable title as DOI-less evidence. Sparse publication date, author IDs, ORCID, and `updated_date` do not independently discard otherwise usable evidence or create ingestion-time missing-field RunIssues. Missing revision disables the corresponding version-state reuse without a Run error; invalid/missing Work ID remains a structural failure without synthetic identity.
- Complete OpenAlex traversal remains `COMPLETE` despite bibliographic sparsity, including exclusion of a validly attributed Work with neither DOI nor title. Structural identity, unrecovered attribution, request/pagination/count/cursor, and genuine duplicate-traversal failures retain conservative `PARTIAL`/`FAILED` behavior. Normal Source absence produces `UNAVAILABLE` plus `WARNING` and warning-only `validate`; remote failure, conflicting Source identity, and journal identity validation failure remain `FAILED` plus `ERROR`.
- Ambiguous multi-Source Works batches split/fall back; after narrowing to one Source, absent nested Source uses the explicit request scope. An explicit conflicting Source identity still produces a scope-integrity `ERROR` and is never overwritten by request context.
- A retained DOI-anchored OpenAlex record lacking title/authors reaches Crossref supplementation. Successful supplementation can produce normal searchable/canonical evidence without warnings merely describing the original OpenAlex title/author gaps. A usable title-only record reaches local matching without an unconditional production `missing_doi` warning.
- After consolidation, clusters with no usable title, author keywords, or abstract produce an unsearchable warning and are absent from the normal matcher candidate set, including for pure `NOT`/complement expressions. Searchable documents retain existing Term/Phrase/Prefix/Proximity/Boolean/NOT behavior. Matched clusters that still lack canonical title, journal, or an author produce the existing `insufficient_metadata` warning.
- Tolerable ingestion-time sparsity alone emits no missing-field RunIssues; genuine warnings/errors, including the post-consolidation unsearchable and `insufficient_metadata` warnings above, follow the existing `RunOutcome` precedence without Provider-specific exceptions. Historical `openalex-filter` and `crossref-enrich` diagnostics accept partial OpenAlex evidence/search projections while preserving their historical stage order and diagnostic role.
- Compatibility checks preserve Provider-state schema and persistence, Crossref revision reuse/manifest/alias/semantic-hash behavior, Paper/Author Markdown and monitor YAML schemas, the five ProgressStages, and inert `provider-cache.json` bytes. New last-run files remain schema v2; historical v1/v2 snapshots display their recorded outcomes without reinterpretation under the new issue-severity policy. Existing workflow preservation and unaffected §23.16 acceptance remain applicable.

### 23.18 v0.4.5 Candidate Eligibility, Warning Semantics & Scrollable Master-Detail Workspace

v0.4.5 §32 implementation is complete. A1–A6 and their independent stage reviews are complete; the following acceptance scenarios have been demonstrated and the final independent integration audit passed. The full automated suite passed (1877 tests, with two known dependency deprecation warnings). The desktop/mobile manual Web smoke passed during A6, and the final independent integration audit separately verified the corresponding desktop/mobile browser behavior. v0.4.5 is released and was the current/latest released and completed baseline at its closeout.

- A Crossref `journal-issue` is `INELIGIBLE` before topic matching, including with a target ISSN or an empty searchable projection. It creates neither a candidate nor an `unsearchable` warning. OpenAlex `paratext` or `book-chapter` misclassification does not veto exact Crossref `journal-article` evidence with a target ISSN.
- An explicit non-journal Crossref type without a target ISSN is excluded even when its container name equals the configured journal name. `other` or missing Crossref type is `SCOPE_DISPUTED`; `journal-article` with explicit non-target ISSNs is also disputed. A `journal-article` without ISSNs may use strict configured/resolved journal-name equality as weak venue support, never as an override for nonmatching ISSNs.
- Crossref supplement `UNAVAILABLE` is authoritative Crossref absence and uses OpenAlex `primary_location.is_published`: true → `ELIGIBLE`, false → `INELIGIBLE`, missing/invalid/unknown → `SCOPE_DISPUTED`, without unconditional exclusion. Crossref `FAILED` is an execution failure: true → `ELIGIBLE`, false → `SCOPE_DISPUTED`, missing/invalid/unknown → `SCOPE_DISPUTED`; `is_published=false` alone cannot exclude the candidate, and the existing Provider execution error remains. DOI-less OpenAlex evidence independently uses true → `ELIGIBLE`, false → `INELIGIBLE`, missing/invalid/unknown → `SCOPE_DISPUTED`, without manufacturing Crossref coverage. These eligibility decisions do not change the existing supplementation coverage/status meanings.
- Alias supplementation evaluates the exact prime record against the OpenAlex anchor through `CrossrefSupplementUnit.requested_doi` / `anchors`, preserving requested-DOI coverage and prime-DOI evidence/state identity. The prime record's eligibility applies to the requesting anchor even when the two DOIs differ.
- `INELIGIBLE` evidence never enters `assemble_live_provider_evidence`, consolidation, matching, canonicalization, or retained version hydration. Exclusion leaves retrieval coverage, Provider-state usage reporting, and otherwise valid acquired-record persistence unchanged.
- Unmatched disputed evidence produces only a diagnostic. A matched disputed cluster with no strong eligible evidence produces a Candidate Eligibility warning; a matching cluster with strong eligible evidence retains the dispute as a diagnostic without that warning. Unsearchable eligible evidence and insufficient matched canonical metadata retain their genuine warnings.
- Author identity fallback distinguishes `MATCH`, `INCONCLUSIVE`, and `CONFLICT`. Same normalized names, count, and sequence with a one-sided ORCID or OpenAlex author ID do not automatically conflict; missing/inadequate evidence stays inconclusive and contradictory comparable IDs conflict. Title fallback merges only on `MATCH`.
- Blocked title fallback and refusal to merge conflicting DOI components leave the work identities separate and produce typed consolidation diagnostics counted once per logical group, rather than pairwise Run warnings. Genuine snapshot identifier conflicts and material metadata conflicts remain warnings/errors.
- Once DOI or equivalent high-confidence evidence establishes same-work identity, initials/full given names, name display order, period/whitespace, and Unicode hyphen differences can compare equivalent without changing author sequence or migrating representative display names. Nonconflicting identifiers can enrich the representative; conflicting identifiers cannot be silently replaced.
- Deterministic abstract normalization treats equivalent HTML/JATS, Unicode, whitespace, dash/punctuation, and structural section-label representations equally. A real body-content difference still warns; no fuzzy, embedding, or LLM similarity is used.
- An otherwise clean Run containing only diagnostics returns `COMPLETED`. Real warnings/errors retain `ERROR → COMPLETED_WITH_ERRORS`, else `WARNING → COMPLETED_WITH_WARNINGS`, else `COMPLETED`, and existing CLI exit semantics. CLI/Web display diagnostics separately; diagnostics are absent from `last-run.json`, Paper/Author Markdown, monitor YAML, and durable Provider state.
- A valid logical schema-v1 Provider-state DB is validated with the v1 semantic-hash algorithm, converted to current v2 state in memory, and immediately reusable after live revision validation in that same Run; it does not force a cold start or mutate the DB during reading.
- At the normal production persistence boundary, one transaction recomputes every historical Crossref semantic hash with `work_type`, updates logical schema metadata to v2, and writes current pending state. Out-of-window Crossref and OpenAlex rows survive, and unchanged `record_json` need not be rewritten. SQLite layout and both Provider serialization versions remain unchanged.
- A migration/write failure rolls back the whole transaction, leaving the original v1 state readable and reusable by a later Run. Completed Paper/Author materialization survives and a Provider-state persistence warning is reported. Warning/error production completions reaching the boundary can migrate; `INVALID_CONFIGURATION`, diagnostics, `validate`, `canonicalize`, and legacy/diagnostic `materialize` do not migrate or acquire new state access.
- At desktop width, the list and detail panes have equal, viewport-bounded height and independent vertical scrolling. Selection has a visible active state; clicking another Paper updates detail and preserves list scroll. Workspace replacement restores the list's current scroll neighborhood without durable frontend state.
- After a successful decision removes the selected Paper from the active view, selection prefers the next Paper at its former position, then the preceding Paper; an empty view shows empty detail. A Paper that remains in the view stays selected. Every rendered selection belongs to the current active view, including after view switches or refreshes.
- A failed decision, state conflict, or I/O failure displays refreshed real disk state and the failure, without reporting successful neighbor navigation. Out-of-range navigation positions are clamped and never influence decision authorization, UUID validation, or expected-status checks.
- At viewport width ≤760px, the workspace returns to ordinary single-column document flow, without desktop pane-height constraints or nested pane scrolling. Both list and detail remain reachable through page scrolling.
- Existing CSRF, compare-before-replace, Markdown ownership/preservation, UUID identity, Zotero, retrieval coverage, revision reuse/alias behavior, five-stage progress, inert legacy cache, and historical last-run compatibility continue to hold within §32.1's narrow supersede boundary.
- Final acceptance requires the full automated suite plus manual desktop and mobile Web smoke demonstrating scrolling, active selection, view membership, successful decisions, refreshed failure states, empty views, and scroll-neighborhood restoration. The required desktop/mobile manual Web smoke passed during A6. The final independent integration audit separately verified the corresponding desktop/mobile browser behavior, and the full automated suite passed.

---

### 23.19 v0.4.6 GUI Cleanup — Advanced & Diagnostics + Kept Copy DOI

v0.4.6 A0 specification alignment, A1–A3 implementation, and A0–A3 independent stage reviews are complete. The A2 `START_FAILED` Current run lifecycle finding was fixed and re-reviewed; the final independent integration audit passed. The following §33 acceptance scenarios have been demonstrated at the verification levels recorded here. Final release validation passed, and release preparation, the release transaction, and closeout are complete (§24.14). v0.4.6 is released and was the current/latest released and completed baseline at its closeout.

The implementation environment reported 1910 tests passing. The independent final AgentDock audit ran 1907 tests successfully and skipped three Node-dependent Clipboard harness cases because no JavaScript runtime was available; those three cases passed in the implementation environment. Both environments reported two known dependency deprecation warnings. The independent audit also inspected the retained real-browser `Copied` / `Copy failed` artifacts and the server-normalized DOI → Clipboard API chain.

Final release validation passed with 1910 tests passing, zero skipped, and two known dependency deprecation warnings; all three Clipboard JavaScript harness cases executed. `uv lock --check` and `git diff --check` also passed. This final release validation is distinct from the independent AgentDock audit recorded above.

- A healthy Workspace shows neither a standalone `Workspace issues` panel nor `No workspace issues.`. Non-empty `WorkspaceSnapshot.issues` produces only a lightweight issue indicator/count and a details entry point; full issue paths/messages appear only in Settings `Advanced & Diagnostics → Workspace health`. Invalid Papers are isolated while all valid Papers remain available in their workflow views.
- Settings has a read-only, initially collapsed `Advanced & Diagnostics` area outside the Settings form. Opening, closing, or reading it does not make the form dirty and it does not participate in Validate or Save.
- Workspace health resolves its target from the currently saved, valid monitor configuration. Editing or validating an unsaved draft, including its output path, does not change that target. A successful Save makes the newly saved target applicable; a missing/invalid/unloadable saved configuration shows Workspace health unavailable and never uses a recovery or unsaved draft to guess a workspace.
- A finished primary Run shows outcome, Paper result summary, and warning/error counts, without diagnostics summary/details, coverage detail, Provider-state usage, or a full warning/error issue list. A diagnostics-only `COMPLETED` result with zero warnings/errors has the normal completed presentation; `INVALID_CONFIGURATION` remains an explicit configuration problem. Diagnostics do not alter success/error presentation.
- `Advanced & Diagnostics → Current run` shows full warnings/errors, typed diagnostics with kinds/logical-group counts/context, coverage, and Provider-state usage from the existing `RunCoordinator.snapshot().result`. Once a new Run successfully starts, prior finished details disappear immediately; while it runs this area shows only `Run in progress`.
- Refresh in the same server process can show the current finished details again. Restarting the server does not restore them from `last-run.json` or other persistent data. No snapshot schema change or diagnostics/history persistence occurs; CLI diagnostics, coverage, and Provider-state usage behavior remain unchanged.
- Only a `kept` Paper whose DOI is accepted by the existing Python `normalize_doi()` shows `Copy DOI`. A DOI URL representation copies the normalized bare DOI, for example `10.1234/example`, without `https://doi.org/`. Candidate, rejected, and in_zotero Papers never show the action, even when they have valid DOIs.
- A kept Paper with a missing or non-normalizable DOI shows no button, including no disabled fallback. An available arXiv ID, title, citation, or other identifier does not substitute. Browser JavaScript consumes the Python-normalized value and does not implement a DOI parser.
- Copy uses only the browser Clipboard API. Success temporarily displays `Copied`, then returns to `Copy DOI`; clipboard failure displays lightweight `Copy failed`, without an alert or notification system. Both paths perform no mutation POST, Workspace reload, Markdown write, workflow/status or `zotero_key` change, or automatic `Mark in Zotero` action.
- Workspace has no `Zotero export` panel, `Load export` action, or export textarea workflow. CLI `export-kept` and `kept_export.py` retain the existing batch DOI/arXiv/MANUAL behavior; `Mark in Zotero` remains an independent manual confirmation.
- Existing desktop/mobile master-detail, selection/scroll preservation, Run progress/polling, Settings validation/save, decision safety, and Markdown ownership behavior continue to hold. Copy feedback is transient browser presentation only; no domain localStorage, diagnostics/workspace-health/run history, or new durable state is introduced.

---

### 23.20 v0.4.7 Journals Organization & Bulk Import — demonstrated acceptance

A0–A7 implementation and all A0–A7 independent stage reviews are complete. `V0_4_7_FINAL_INTEGRATION_AUDIT` passed with APPROVED status, demonstrating the following acceptance requirements for the implemented §34 contract. The verification levels recorded below remain distinct. Final release validation passed; release preparation, the release transaction, and closeout are complete (§24.15). v0.4.7 is released and is the current/latest released and completed baseline, with package metadata `0.4.7`.

- Journal configuration remains an ordered flat `tuple[JournalConfig, ...]`, with at most one optional Group per Journal. Group order derives from Journal configuration order; Ungrouped uses `group=None`. Create, rename, delete, Up / Down, and Journal assignment work without nested Groups, multi-Group membership, independent positions, or durable empty Groups. Deleting a Group moves its Journals to Ungrouped without deleting them.
- Both `Journal | ISSN/EISSN` and `Journal | ISSN/EISSN | Group` tables load. A legacy ungrouped two-column file stays two-column, including on unrelated Settings Save. Actual Group use saves canonical three-column storage; a file already using three columns retains that shape after all Groups are removed. Upgrade and Settings open do not rewrite the file. Existing name, checksum, and globally unique configured ISSN validation remains applicable, with safe Markdown-table Group storage validation.
- UTF-8 CSV, TSV, pasted delimited tables, and Literature Monitor `list.md` use only explicitly supported formats/headers. Parse and normalize produce a preview before Apply; no inference, Provider requests, metadata completion, AI importer, or XLSX dependency occurs.
- Merge is the default; Replace requires explicit selection and previews removals. Preview distinguishes Add, no-op duplicate, ISSN merge, Group move, Remove (Replace only), conflict, and invalid row. Any invalid row or identity/name conflict blocks the entire Apply and leaves the draft unchanged.
- Merge can add new ISSNs to the same-name configured Journal, but an ISSN belonging to another Journal or paired with another name conflicts. An imported blank Group preserves an existing Journal's Group; an explicit different non-empty Group previews and applies a move.
- Apply changes only the current unsaved browser Settings draft and marks it dirty. It preserves `monitor_revision` and `journal_revision`; existing complete-draft Validate / Save and Save revision-conflict/partial-save behavior remain the sole persistence path. An intervening disk edit still causes a normal Save conflict after import.
- Desktop Journals use a viewport-bounded scroll area with Validate / Save outside it and always accessible; mobile uses normal page flow without nested Journals scrolling. Settings Validate fragment replacement restores Journals scroll position.
- Candidate Eligibility alone supplies configured Journal attribution as transient `ProviderWorkEvidence.monitor_journal_issns`. Provider raw/Source ISSNs are not copied into Paper attribution. Same-provider/record snapshots from different execution contexts retain the union even when `_choose_evidence()` selects one representative; a canonical work carries the union in `CanonicalPaper.journal_issns`.
- Attribution does not alter evidence clustering, UUID/canonical identity, representative/version selection, searchable projection/keyword matching, duplicate matching, status, or metadata-conflict diagnostics. `CanonicalMetadata` remains unchanged, and Provider-state storage/revision/cache semantics remain unchanged apart from the transient attribution flow.
- New materialized Papers write valid available attribution as managed `journal_issns: list[str]`. For an existing matched Paper, non-empty incoming attribution replaces durable attribution; empty incoming attribution preserves it. Legacy Papers without the field remain valid. Parsing exposes missing/empty, valid, and malformed attribution separately from identity and update safety; malformed attribution neither blocks normal materialization/repair nor Keep / Reject / In Zotero.
- Each Inbox / Kept / Rejected / In Zotero view shows non-empty sections in saved Group order, then Ungrouped, then Unmapped journals. Within each section, existing `discovered_at DESC → publication_date DESC → title ASC` precedence remains. No second Group-tab row or workflow/status system is introduced.
- Valid non-empty Paper attribution maps only when exactly one current saved Journal matches; zero or multiple matches are Unmapped without name fallback. Missing/empty legacy attribution may use conservative normalized Journal-name equality only when unique. Malformed attribution is Unmapped without creating a Workspace issue solely for that field.
- Successful Settings Save immediately changes Workspace presentation for Group rename/reorder/assignment, without rewriting Papers. Removing a configured Journal may make historical Papers Unmapped without modifying them; Provider display-name changes cannot disrupt a unique valid ISSN mapping.
- Preservation checks retain human notes, unknown/custom frontmatter, workflow status, `zotero_key`, preferred versions, Copy DOI, CLI `export-kept`, provider resolution, and existing Web safety/selection/scroll behavior outside the narrow §34 supersede boundary. No eager corpus migration, Journal UUID/database, Paper Group tags, persistent workflow DB, or external integration is introduced.

Actual final integration audit evidence:

- Implementation environment: **2209 passed, 0 skipped, 2 known dependency warnings**. Node **v24.21.0** was available, and executable DOM/browser harnesses passed.
- Independent AgentDock full suite: **2198 passed, 11 skipped, 2 known dependency warnings**; the skips were Node-dependent tests because Node was unavailable in that environment. Its independent cross-stage suite recorded **986 passed, 8 Node-dependent skips**, and independent `git diff --check` passed. The implementation environment's Node harness evidence was not independently rerun by AgentDock.
- Independent temporary end-to-end integration: **Import → Apply → Validate → Save → Workspace reprojection PASS**, with existing Paper bytes unchanged.

Release preparation updated only package/User-Agent version identity, matching test expectations, and current-state documentation without functional changes. Its independently reviewed diff was committed before annotated tag creation and the release transaction. The release-preparation commit, annotated tag, push main, push tag, GitHub Release, and post-release closeout are complete (§24.15).

Release-preparation validation in the implementation environment passed: targeted OpenAlex/Crossref tests **436 passed**; full suite **2209 passed, 0 skipped, 2 known dependency warnings**. Node **v24.21.0** was available and all executable DOM/browser harnesses ran. The two warnings were the existing Starlette TestClient/httpx and anyio BlockingPortal alias deprecations. `uv lock --check` and `git diff --check` passed. This release-preparation validation is distinct from the completed independent diff review and final release validation.

Final release validation separately passed with **2209 passed, 0 skipped, 2 known dependency deprecation warnings**. Node **v24.21.0** was available and all executable DOM/browser harnesses ran. `uv lock --check` and `git diff --check` passed. Wheel/sdist metadata and required package contents were verified against the reviewed release HEAD, including `journal_import.py` and required Web assets; user workspace material was excluded. Wheel import smoke passed.

---

### 23.21 v0.5.0 Institutional PDF Acquisition to Existing Zotero Item — demonstrated acceptance

A0–A7 implementation and all independent stage reviews are complete. `V0_5_0_FINAL_AUDIT_COMMIT_REVIEW` passed with READY_FOR_COMMIT status. Implementation commit `a06474ae2aadd5631be74697809f3bff95f3ce14` (`Add institutional PDF acquisition to Zotero`) was created and independently verified as the exact reviewed 25-file implementation. The implemented §35 contract has the following demonstrated acceptance at the distinct verification levels below. Final release validation passed; release preparation, the release transaction, and closeout are complete (§24.16). v0.5.0 is released and is the current/latest released and completed baseline, with package metadata `0.5.0`.

- Add PDF to Zotero is available only for a current-disk `in_zotero` Paper with a valid normalized DOI. Exact-DOI verification locates an existing My Library parent; it never creates a bibliographic parent. Only null/missing `zotero_key` can be safely linked, preserving workflow status, notes, custom frontmatter, and all other Paper content. Concurrent linkage/content changes stop the attempt.
- Complete Zotero enumeration requires a stable `Last-Modified-Version`, current Server-ID, and complete pagination/count checks for both parent identity and child attachments. An actual existing PDF file short-circuits acquisition; metadata-only partial attachments do not count as success or prevent a later explicit retry.
- Local API writes use `/users/0`, API version 3, and the verified current Server-ID. One-time credentials remain process-only; remembered credentials use the OS credential store with no plaintext fallback. Same-Server-ID HTTP 412 is an operation failure, not an instance change. The single fresh-authorization 401 retry revalidates Paper, parent, Server-ID, and attachments; the process-wide authorization 429 boundary survives workspace changes.
- The dedicated persistent Google Chrome profile stays outside project/workspace, independently of shell cwd. Authenticated XMU resolver candidates are filtered to `FullText` / `SmartLinks` without changing resolver order or adding publisher routing. Generic PDF discovery validates actual `%PDF` bytes; temporary files are isolated and cleaned, and durable attachment source metadata uses the canonical DOI URL.
- One independent process-local `AcquisitionCoordinator` permits a single active attempt without a queue. Web execution remains responsive, HTMX polling is observational, and current-attempt results expose no credentials or sensitive URLs. No new workflow status, durable PDF state, acquisition history, or changes to existing Run, Settings, decisions, Copy DOI, export, or materialization behavior are introduced.

Network-independent final audit evidence:

- Independent full suite: **2876 passed, 11 skipped, 2 known dependency warnings, 2887 collected**. The skipped cases were Node-dependent harnesses in the independent environment. The local implementation audit separately ran **2887 passed, 0 skipped, 2 known dependency warnings, 2887 collected**; these are distinct environment results.
- `uv lock --check` and `git diff --check`: **PASS**.
- Source-tree wheel/sdist build and isolated wheel installation, CLI help, acquisition-module imports, and Web acquisition-template loading/compilation: **PASS**.

Final release validation separately ran from a clean export of release HEAD `413b2505b8cab4d74bd5e6e43f4c9ec5aee6815c`: **2887 passed, 0 skipped, 2 known dependency warnings, 2887 collected**. Node **v24.21.0** was available and Node-powered Web harnesses executed. `uv lock --check` and `git diff --check` passed. Wheel/sdist metadata and required acquisition package contents were verified against that exact HEAD; isolated installed-wheel CLI/import/template smoke passed. The committed grouped `list.md` release source used `Journal | ISSN/EISSN | Group` and passed parsing/storage validation with **74 Journals**. It is repository release source, not part of the Python wheel/sdist payload.

Previously established live evidence: headed real Google Chrome launched; authenticated XMU Full Text Finder was observed; a real `POST https://resolver.ebsco.com/api/links` returned **HTTP 200** with **4 ordered eligible FullText candidates**. This evidence demonstrates authenticated resolver access and ordered candidates only.

Successful institutional PDF retrieval, actual Zotero authorization dialog/authorization, actual Zotero child attachment creation, actual PDF upload/registration, and full live end-to-end acquisition are **not demonstrated live**. Mocked failure, retry, and upload tests do not substitute for those live operations. Release preparation and final release validation performed no new live acquisition or Zotero mutation.

---

### 23.22 v0.5.1 Version-Qualified Institutional PDF Acquisition via Normal Chrome — demonstrated acceptance

A0–A9 implementation, independent final-audit Fix 1–3 review, and implementation acceptance/final audit are complete under §36.14. Implementation commit `3720dc375672a492717ae334856dfe73c9343dcf` (`Implement v0.5.1 PDF acquisition via normal Chrome`) contains the independently reviewed 60-file implementation. §36 remains the authoritative behavior contract.

The independent implementation validation recorded 3356 passed / 0 skipped / 2 existing dependency warnings with Node runtime, 174 shipped worker simulation cases, 101 shipped content-adapter cases, passing lock/diff checks, and an offline wheel/sdist build. These implementation checks are distinct from prepared-tree validation and final clean-export release-commit validation in §24.17.

Scoped live evidence in §36.14 includes normal Chrome companion/handoff, publisher-first navigation, explicit XMU fallback, SmartLinks/Research, delayed task-bound blob attribution, private staging, `%PDF` validation, PUBLISHED qualification, and Settings-owned remembered authorization. Actual Zotero child creation/upload/registration succeeded for parent `ZHIST6EG` and child `LJ6UV83V`; fresh complete Local API inspection verified a non-empty regular PDF file and bytes equal to the user Chrome download. Add PDF did not mutate Paper content.

A separate scoped institutional-path run retained its task/tab/frozen version but naturally presented no genuine human-verification challenge. `HUMAN_VERIFICATION_NOT_PRESENT` records this branch as not live-exercised under the conditional §36.14 boundary; no challenge was manufactured or verification bypass used. Pre-mutation cancellation preserved parent `YLF7MWNU` without a PDF child. Meaningful executable human-wait/continuation coverage passed. Historical v0.5.0 live evidence in §23.21 remains unchanged.

v0.5.1 is the latest released and completed baseline, with package metadata `0.5.1`. Release-preparation review/commit, final clean-export release validation, annotated tag creation, main/tag pushes, and the GitHub Release are complete (§24.17). Product/release closeout completed with `125bfb7`, outside the release tag target. Later current-main maintenance and its separate verification are recorded in §36.14.

---

## 24. Suggested Implementation Sequence

### 24.1 Completed v0.1.0 history

The original MVP tasks are completed history, not pending implementation steps:

1. repository foundation;
2. OpenAlex venue-first discovery;
3. local keyword expression engine;
4. DOI-based Crossref enrichment;
5. canonicalization and version consolidation;
6. Obsidian materialization;
7. incremental update semantics;
8. kept-paper export;
9. end-to-end validation.

This list records the shipped v0.1.0 sequence. It does not give OpenAlex or the original post-filter Crossref enrichment path authority over the revised multi-source candidate universe.

### 24.2 Completed multi-source retrieval evolution

R0–R3 are completed, separately reviewed history:

```text
R0 Specification alignment
→ R1 Provider-neutral evidence boundary
→ R2 Crossref independent discovery
→ R3 Semantic Scholar integration
```

- R0 aligned the product specification and repository engineering constraints.
- R1 introduced a provider-neutral transient evidence representation, adapted the existing OpenAlex and Crossref records to that boundary, and removed canonicalization's structural dependency on an OpenAlex record while preserving the then-current user-visible OpenAlex → local filter → Crossref enrichment behavior.
- R2 added independent Crossref journal/date discovery, unioned OpenAlex and Crossref evidence, performed identity/evidence consolidation, constructed the multi-provider searchable projection, and moved final local keyword filtering after available evidence consolidation.
- R3 added Semantic Scholar supplementation and venue/date-constrained supplemental discovery.

Each R0–R3 task was implemented and reviewed as a separate bounded change. This sequence is retained as project history, not as a pending implementation plan.

### 24.3 Completed v0.2.1 lexical-search evolution

The bounded v0.2.1 lexical-search evolution is implemented:

1. the observable lexical-search contract was aligned with §7 and §23.3;
2. the transient SQLite FTS5 batch backend was implemented;
3. all local filtering entry points were integrated with that backend after the appropriate metadata or evidence consolidation stage.

This work does not retroactively alter the completed R0–R3 history or bring other SQLite FTS5 capabilities into product scope.

### 24.4 Completed v0.3.0 Review Inbox evolution

The bounded v0.3.0 Track A Review Inbox evolution is completed and reviewed history:

```text
A0 Specification alignment
→ A1 Minimal Base artifact
→ A2 Materialization integration
→ A3 Documentation / real Obsidian validation
```

This sequence added the creation-only Review Inbox presentation without changing Paper Markdown's ownership of durable workflow state or entering Track B scope.

### 24.5 Completed v0.3.1 Prefix / Proximity search evolution

The bounded v0.3.1 Prefix / Proximity search evolution is completed history:

```text
S0 Specification alignment
→ S1 Grammar / AST / provider query projection
→ S2 Local FTS5 matching / validation integration
→ S3 Documentation / final feature audit
```

This sequence added explicit Prefix and Proximity grammar/AST nodes, safe
Semantic Scholar broad-positive query projection, transient FTS5 token-prefix
matching, and unordered Proximity matching with the defined distance semantics.
Repeated Proximity tokens preserve occurrence multiplicity using transient FTS5
position evidence. Local-filter commands validate lexical constraints before
provider work, while searchable-unit boundaries, provider-neutral final local
matching, and reconstructible in-memory search state remain unchanged. README
and specification history were synchronized as the final bounded step.

### 24.6 Completed v0.3.2 Persistent Monitor Definition evolution

The completed v0.3.2 sequence is:

```text
P0 Specification alignment
→ A1 Monitor config / date domain
→ A2 Unified CLI date overrides
→ A3 Persistent run entrypoint / production-pipeline reuse
→ A4 Documentation / E2E / final feature audit
```

This evolution added one local YAML monitor definition with config-relative
paths, a shared inclusive date-policy model, unified ephemeral CLI date
overrides, and `literature-monitor run --config monitor.yaml` as the normal
entry point. `run` reuses the existing production retrieval, evidence,
filtering, canonicalization, and materialization pipeline rather than creating a
second orchestration path. No durable execution state, monitor identity, run
history, provider cursor persistence, scheduler state, or second workflow source
of truth was added.

### 24.7 v0.3.3 Pre-GUI Correctness Hardening

The completed bounded hardening work freezes three pre-GUI contracts without adding a GUI or durable execution state:

```text
A1 Semantic Scholar year-only date-membership semantics
→ A2 validate deterministic runtime preflight
→ A3 contract documentation
```

Discovery remains publication-date-only. Semantic Scholar year-only records use `Y-01-01` only for provider filtering membership and do not acquire a fabricated bibliographic date. `validate --config` now exercises the deterministic local FTS5 and runtime date checks required before provider work. The supported durable-state boundary is one monitor to one decision workspace, with workspace-local Paper UUIDs, statuses, and human notes.

### 24.8 v0.4.0 Python Local Web UI implementation — completed

v0.4.0 is released and is the current completed baseline. Its application, workspace, decision, settings, run-coordination, Web, security, GUI CLI, packaging, and installed-distribution contracts in §25 were implemented, independently audited, release-prepared, tagged, pushed, and released.

The completed implementation remains an adapter over the existing durable Markdown model and canonical production pipeline. It does not introduce a second workflow source of truth, persistent execution database, new workflow status, Zotero API ingestion, or a parallel GUI-specific retrieval/canonicalization/materialization pipeline.

The release closeout sequence is:

```text
v0.4.0 feature implementation complete
→ final audit complete
→ release-preparation commit
→ tag
→ push main
→ push tag
→ GitHub Release
```

The full closeout sequence is completed release history.

### 24.9 v0.4.1 Runtime Progress, Activity, ETA, and Inactivity Feedback

The v0.4.1 work extends the existing transient runtime reporting contract without changing the canonical production pipeline, durable workspace model, single-active-run semantics, or provider retrieval policy. Specification alignment, the separately reviewed A1–A5 implementation tasks, and the final independent audit are complete.

The completed bounded result includes:

- a shared process-local five-stage `ProgressStage` plus transient `Activity` contract;
- reliable current-Activity counters and ETA, real `last_activity_at` / `worker_alive`, and one inactivity advisory;
- OpenAlex and Crossref request/retry/response/page instrumentation without progress-only provider requests;
- coarse local processing plus natural materialization and validation progress;
- CLI TTY/non-TTY runtime presentation and GUI HTMX progress/accessibility presentation;
- no durable runtime state and no duplicated CLI/Web production orchestration.

The release closeout sequence is:

```text
v0.4.1 feature implementation complete
→ final audit complete
→ release-preparation commit
→ tag
→ push main
→ push tag
→ GitHub Release
```

The full closeout sequence is complete. v0.4.1 is released and is the current released and completed baseline.

### 24.10 v0.4.2 Provider Reliability, Coverage, and Explicit Cache Reuse

The separately reviewed A1–A6 feature implementation and the final independent audit are complete. The v0.4.2 release transaction and closeout are complete.

The completed bounded result includes:

- Semantic Scholar retired from supported production retrieval while historical durable identifiers and provenance remain readable;
- OpenAlex/Crossref request reliability improved with bounded retry and Crossref rate-aware pacing;
- transient per-work-unit provider coverage;
- one atomic latest-run coverage/reuse diagnostic snapshot;
- one replaceable normalized clean-COMPLETE provider-result cache;
- explicit exact-range opt-in provider cache reuse for CLI and Web, while default runs remain live;
- no checkpoint resume, watermark, late-index recovery, scheduler, notification, run history, or execution database.

The release closeout sequence is:

```text
v0.4.2 feature implementation complete
→ final audit complete
→ release-preparation commit
→ tag
→ push main
→ push tag
→ GitHub Release
```

The full closeout sequence is complete. v0.4.2 is released and is the current released and completed baseline.

### 24.11 v0.4.3 Retrieval Efficiency & Revision-Validated Provider Evidence

The separately reviewed A1–A8 implementation and the final independent audit are complete. The v0.4.3 release transaction and closeout are complete.

The completed bounded result includes:

- live candidate membership established on every production Run;
- automatic revision-validated Provider-state reuse;
- batched OpenAlex Source/Works retrieval and retained-only location/version hydration;
- live Crossref manifests with revision-aware metadata reuse/refresh and current-evidence-anchored DOI supplementation;
- narrow reconstructible Provider-state SQLite for Crossref metadata and retained OpenAlex version hints;
- Provider-level OpenAlex/Crossref discovery concurrency and independent per-source progress/ETA;
- legacy explicit provider-cache reuse retired, with old cache bytes remaining inert and untouched;
- no checkpoint resume, watermark, run history, or scheduler semantics.

The release closeout sequence is:

```text
v0.4.3 feature implementation complete
→ final audit complete
→ release-preparation commit
→ tag
→ push main
→ push tag
→ GitHub Release
```

The full closeout sequence is complete. v0.4.3 is released and is the current released and completed baseline.

### 24.12 v0.4.4 Crossref Elapsed-Aware Pacing & Partial Provider Evidence Semantics

The A1–A5 implementation, independent stage reviews, final independent audit, v0.4.4 release transaction, and closeout are complete.

The completed bounded result includes:

- elapsed-aware process-local monotonic Crossref pacing, with network and processing elapsed time counting toward request spacing;
- independent Provider-derived pacing state for singleton DOI and list/filter requests;
- composition of retry and pacing deadlines without redundant full waits;
- partial OpenAlex Provider evidence admission using valid Work identity, trustworthy Source attribution, and DOI-or-title;
- Source attribution and severity corrections, preserving normal absence as `UNAVAILABLE` plus `WARNING` and genuine remote/identity failures as `FAILED` plus `ERROR`;
- DOI-anchored Crossref supplementation of partial OpenAlex evidence and direct local matching for title-only evidence without an unconditional production missing-DOI warning;
- post-consolidation unsearchable warnings and candidate exclusion protecting `NOT`/complement matching;
- historical diagnostic compatibility with partial Provider evidence while preserving discovery → local filtering → enrichment order;
- unchanged durable schemas, five workflow stages, canonical eligibility, and Paper/Author workflow ownership, without generic Provider field synthesis.

The release closeout sequence is:

```text
v0.4.4 feature implementation complete
→ final audit complete
→ release-preparation commit
→ tag
→ push main
→ push tag
→ GitHub Release
```

The full closeout sequence is complete. v0.4.4 is released and is the current released and completed baseline.

---

### 24.13 v0.4.5 Candidate Eligibility, Warning Semantics & Scrollable Master-Detail Workspace

A1–A6 implementation, independent stage reviews, and the final independent integration audit are complete. The §23.18 acceptance scenarios and full automated suite passed. The A6 desktop/mobile manual Web smoke passed, and separate independent desktop/mobile browser verification passed during the final integration audit. Release preparation, annotated tag creation, push main, push tag, GitHub Release, and closeout are complete. v0.4.5 is released.

The completed bounded result includes:

- Candidate Eligibility tri-state decisions and pre-matching exclusion, with distinct Crossref absence/failure and DOI-less OpenAlex publication fallback semantics;
- typed transient Run diagnostics that do not change outcomes, with logical-group counts and context shown separately in CLI/Web;
- conservative author/title identity decisions and deterministic author/abstract metadata representation equivalence;
- Provider-state logical schema v2, Crossref `work_type` semantic-hash consumption, immediate valid-v1 reuse, and transactional migration of historical v1 hashes with rollback;
- viewport-bounded equal-height desktop master-detail panes, independent scrolling, current-view selection, success-only neighbor navigation, and mobile normal page flow;
- preserved Markdown ownership, workflow decisions, retrieval/coverage, revision reuse, alias, progress, and security boundaries.

The release closeout sequence is:

```text
v0.4.5 feature implementation complete
→ final audit complete
→ release-preparation commit
→ tag
→ push main
→ push tag
→ GitHub Release
```

The full closeout sequence is complete. v0.4.5 is released and was the current/latest released and completed baseline at its closeout.

---

### 24.14 v0.4.6 GUI Cleanup — Advanced & Diagnostics + Kept Copy DOI

A0 specification alignment, A1–A3 implementation, A0–A3 independent stage reviews, and the final independent integration audit are complete. The §23.19 acceptance scenarios have been demonstrated at the recorded automated/browser verification levels, including the fixed and re-reviewed A2 `START_FAILED` lifecycle finding. Final release validation, the release-preparation commit, annotated tag creation, push main, push tag, GitHub Release, and closeout are complete. v0.4.6 is released, with package metadata `0.4.6`.

The completed bounded result includes:

- read-only Settings `Advanced & Diagnostics → Workspace health` over the saved configuration, with compact Workspace issue presentation;
- primary finished Run cleanup and process-local Current run technical details, including refresh after a failed worker start;
- kept-Paper Copy DOI using the server-normalized bare DOI and transient Clipboard feedback;
- removal of the Web batch Zotero export UI while preserving CLI `export-kept` and independent manual `Mark in Zotero`;
- preserved workflow/domain boundaries, without new durable run, diagnostic, or domain state.

The release closeout sequence is:

```text
v0.4.6 feature implementation complete
→ final audit complete
→ release-preparation commit
→ tag
→ push main
→ push tag
→ GitHub Release
```

Release preparation updated package/User-Agent metadata and current-state documentation without functional changes; its reviewed diff was committed before the annotated tag and release transaction. The full release closeout sequence is complete. v0.4.6 is released and was the current/latest released and completed baseline at its closeout.

---

### 24.15 v0.4.7 Journals Organization & Bulk Import — completed release closeout

A0–A7 implementation, all independent stage reviews, and `V0_4_7_FINAL_INTEGRATION_AUDIT` are complete; the final audit is APPROVED. §23.20 records demonstrated acceptance and distinct verification provenance, including passing final release validation. Release preparation, its independently reviewed commit, annotated tag creation, push main, push tag, GitHub Release, and closeout are complete.

The completed release closeout sequence is:

```text
A0–A7 implementation
→ independent stage reviews
→ final integration audit
→ release preparation
→ release-preparation commit
→ annotated tag
→ push main
→ push tag
→ GitHub Release
→ closeout
```

Release commit and local/remote annotated tag `v0.4.7` target `c9f6091e840f6cef9ab005bbaed7b69e51450682`; main and origin/main synchronized at that release HEAD. The [GitHub Release v0.4.7](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.4.7) is published, non-draft, and non-prerelease, with the verified wheel and sdist uploaded.

§34 remains the implemented authoritative behavior contract. v0.4.7 is released and is the current/latest released and completed baseline, with package metadata `0.4.7`. The unaffected released contracts and historical release facts remain applicable.

---

### 24.16 v0.5.0 Institutional PDF Acquisition to Existing Zotero Item — completed release closeout

A0–A7 implementation, independent stage reviews, final audit, and the independently verified implementation commit `a06474ae2aadd5631be74697809f3bff95f3ce14` are complete. §23.21 records demonstrated acceptance and the actual network-independent/live verification boundaries, including passing final release validation. Release preparation, its independent review and commit, the grouped Journal list commit, annotated tag creation, main/tag pushes, GitHub Release, and documentation closeout are complete. v0.5.0 is released, with package metadata `0.5.0`, and is the current/latest released and completed baseline.

The completed release closeout sequence is:

```text
A0–A7 implementation
→ independent stage reviews
→ final audit
→ implementation commit
→ release preparation
→ independent release-prep review
→ release-preparation commit
→ grouped Journal list commit
→ annotated tag
→ push main
→ push tag
→ GitHub Release
→ closeout
```

The independently reviewed release-preparation commit is `18f3793f1cf3952fa36912ce909dd197f47faeda` (`Prepare v0.5.0 release`). It changed only package/Provider User-Agent identity, matching tests, current-state documentation, and the credential module's §35.6 comment reference; acquisition behavior and dependencies were preserved, and `list.md` was not modified by that commit. The user-approved current grouped `list.md` was then committed without rewriting its bytes as `413b2505b8cab4d74bd5e6e43f4c9ec5aee6815c` (`Update grouped journal list`). The v0.5.0 tag includes this 74-Journal repository configuration.

Annotated tag `v0.5.0` has tag object `11d86e72535e5ea7e757dc51afe604e69973f5a8` and peeled release target `413b2505b8cab4d74bd5e6e43f4c9ec5aee6815c`. Remote main synchronized at that release HEAD. The [GitHub Release v0.5.0](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.5.0) is published, non-draft, and non-prerelease, with exactly the verified wheel and sdist uploaded. GitHub asset digests matched the local SHA-256 values:

- `literature_monitor-0.5.0-py3-none-any.whl`: `b9600a7ab59a444051175a46c84a0fcc021a286925c58e064d0c2a177b37c9a2`.
- `literature_monitor-0.5.0.tar.gz`: `164c2bcefd759435ed1b04ef1b24efbffdf61ad2744492cc71182141eef7ace0`.

§35 remains the authoritative v0.5.0 behavior contract. Historical release facts and the live validation boundaries in §23.21 remain distinct. `monitor.yaml`, `src/.obsidian/`, and `workspace/` remained untouched and local-only. This documentation closeout follows the release tag; its subsequent documentation commit is not part of the v0.5.0 tag target.

---

### 24.17 v0.5.1 Version-Qualified Institutional PDF Acquisition via Normal Chrome — completed release closeout

The reviewed implementation commit is `3720dc375672a492717ae334856dfe73c9343dcf` (`Implement v0.5.1 PDF acquisition via normal Chrome`). A0–A9 implementation, independent Fix 1–3 review, and implementation acceptance/final audit are complete (§§23.22, 36.14). Release preparation updated package/Provider User-Agent identity to `0.5.1`, expired the `--reuse-provider-cache` compatibility flag as already required by §30.6, updated affected tests/current-state documentation, and corrected the credential module's authorization reference to §36.7. Automatic revision-validated Provider-state reuse and legacy-cache immutability remain unchanged; acquisition behavior, companion behavior/version, credential behavior, and dependencies were preserved. Its independently reviewed 13-file diff was committed as `e623f7576d614516a952216c9c198a2b685ed1c1` (`Prepare v0.5.1 release`), the release HEAD.

Prepared-tree release-preparation validation passed on 2026-10-02: focused CLI/normal-Run/legacy-cache checks 4 passed; Provider tests 436 passed; affected E2E/state-migration tests 77 passed; full pytest with Node v24.21.0 3356 passed / 0 skipped / 2 existing dependency warnings, including 174 shipped worker and 101 shipped content-adapter cases. `uv lock --check` and `git diff --check` passed. An offline build from an isolated export of the implementation commit plus this prepared diff produced `literature_monitor-0.5.1-py3-none-any.whl` and `literature_monitor-0.5.1.tar.gz`; both metadata versions and package contents were verified. Isolated installed-wheel CLI/import/template smoke passed with 26 installed distributions matching the lock. The standalone companion remains outside the Python artifacts, which contain no local monitor/workspace/Obsidian material.

Final release-commit validation separately passed on 2026-10-02 from a clean Git export of exactly `e623f7576d614516a952216c9c198a2b685ed1c1`: `uv sync --frozen --offline`, full `uv run pytest` with Node v24.21.0 (3356 passed / 0 skipped / 2 existing dependency deprecation warnings), `uv lock --check`, and direct `node tests/browser_companion_runtime.cjs` (174 shipped worker simulation cases / 101 shipped content-adapter cases). The offline wheel/sdist build passed; both artifact metadata versions are `0.5.1`, required acquisition modules/templates are present, and `browser_companion/`, `monitor.yaml`, `workspace/`, and `src/.obsidian/` are excluded. An isolated installed-wheel smoke passed with 26 distributions matching the lock, acquisition-module imports, CLI help, and argparse exit 2 for the expired flag. These final clean-export checks are distinct from the earlier implementation checks, prepared-tree checks above, and scoped live evidence in §§23.22 and 36.14.

The release closeout sequence is:

```text
A0–A9 implementation
→ independent reviews / final audit
→ implementation commit
→ release preparation
→ independent release-preparation review
→ release-preparation commit
→ final release-commit validation
→ annotated tag
→ push main
→ push tag
→ GitHub Release
→ documentation closeout
```

Annotated tag `v0.5.1` has tag object `db5cb9d691632bee5d298ace4792fa8b32edf57b` and peeled release target `e623f7576d614516a952216c9c198a2b685ed1c1`. Normal main and tag pushes completed; remote main synchronized at that release HEAD, and the remote annotated tag peels to the same commit. The [GitHub Release v0.5.1](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.5.1) is published, non-draft, and non-prerelease, with exactly the independently built wheel and sdist. GitHub authoritative asset SHA-256 digests match the retained local release artifacts:

- `literature_monitor-0.5.1-py3-none-any.whl`: `7705531fc372f9aa78e642f8b0890d8345acec94b6db4aadccd637e77895a15d`.
- `literature_monitor-0.5.1.tar.gz`: `2ab404d9153d1c1a86800e44426b530936b6654e4b1a0dd6e451f707a83c931d`.

v0.5.1 is RELEASED and the latest released and completed baseline, with package metadata `0.5.1`. The release transaction and independent post-release verification are complete. The initial documentation-only closeout was committed as `125bfb7e85a5e73aede7deb8c77e6522e74cdd74` after the release tag and is not part of the v0.5.1 tag target. Current main's §36 remains the authoritative source behavior contract; subsequent source maintenance is recorded separately below. Historical v0.5.0 and earlier release facts remain unchanged. The scoped live registration and `HUMAN_VERIFICATION_NOT_PRESENT` record remain distinct: real CAPTCHA/login/MFA continuation was not live-exercised, no challenge was manufactured, and no bypass, cookie copying, or session manipulation occurred. The standalone companion remains distributed from the source repository. No new acquisition or Zotero write is performed by release preparation, the release transaction, or this documentation closeout; protected local objects remain local-only and excluded.

After that completed release/closeout, `0b69d4be6fe9f783d37b051d088e67f6e3483c05` (`Simplify PDF acquisition guard and cancellation ownership`) added independently reviewed main-branch maintenance while preserving v0.5.1 user-visible acquisition semantics. It is outside the annotated `v0.5.1` tag target and the published wheel/sdist and GitHub Release artifacts. The tag object/target, asset hashes, release validation and live evidence above remain historical release facts. No new version, tag or release artifacts were created; §36.14 records the separate post-release engineering verification.


### 24.18 v0.5.2 DOI-first Single-Manifestation Simplification — completed release closeout

The reviewed implementation commit is `f48574869969534daec589e124c6979011b5cbb5` (`Implement v0.5.2 DOI-first workflow`), whose parent is `73409eaf77db1e57b41ca2aa3be7539257955d16`. A0–A5 implementation, independent stage reviews and final independent integration audit are complete (§37.10). Release preparation changes only Python package/local lockfile and Provider User-Agent identity from `0.5.1` to `0.5.2`, directly coupled tests and current-status documentation. Dependencies and product/runtime behavior are unchanged; the standalone companion manifest remains `0.1.0`.

Implementation-agent prepared-tree validation passed on 2026-10-03: focused OpenAlex/Crossref/companion pytest **473 passed / 0 skipped / 0 warnings**; full pytest **3220 passed / 0 skipped / 2 existing dependency warnings**. Node **v24.21.0** was available; direct shipped worker simulation **175 cases** and content-adapter suite **104 cases** passed (synthetic Chrome API/DOM, no live browser/network). `uv lock --check` and `git diff --check` passed. The only lockfile change is the local `literature-monitor` version; dependencies are unchanged.

An isolated Git export of exactly the implementation commit plus the release-preparation diff passed `uv sync --frozen --offline` and an offline wheel/sdist build. Artifacts `literature_monitor-0.5.2-py3-none-any.whl` and `literature_monitor-0.5.2.tar.gz` have metadata version `0.5.2`; all 67 expected package files, including templates/static assets, were verified against the prepared source. Neither artifact contains `browser_companion/`, `monitor.yaml`, `workspace/` or `src/.obsidian/`. Isolated installed-wheel smoke passed: 26 installed distributions match the lock, 11 key package/module imports resolve inside the isolated environment, CLI `--help` succeeds and installed GUI templates are available. Temporary exports, environments and artifacts stayed outside the repository and were cleaned after validation; no artifacts were published.

These are implementation-agent release-preparation results, not independent review. The independent implementation/final-audit evidence remains separately recorded in §37.10. Release-preparation review is complete; the reviewed 10-file preparation diff was committed as `d180c18aa853e4483a7d110d39bdfb8aca6860f8` (`Prepare v0.5.2 release`), the release HEAD. The user explicitly skipped the separate final clean-export full validation stage. No additional full pytest/release-validation suite was run during the release transaction, and that skipped stage is not claimed as completed.

Release-integrity checks used a clean Git export of exactly `d180c18aa853e4483a7d110d39bdfb8aca6860f8`, without a full test rerun. An offline build produced the two 0.5.2 artifacts below. Project identity and metadata, application modules/templates/static assets, absence of retired `version_qualification.py` / `application/openalex_retrieval.py`, and exclusion of companion/local monitor/workspace/Obsidian objects were verified. These exact artifacts were retained for upload; this is artifact-integrity evidence, not the skipped final clean-export full validation.

Annotated tag `v0.5.2` has tag object `19f3d3910c842a2102d58f05b6ae0bf3d4c39c9d` and peeled target `d180c18aa853e4483a7d110d39bdfb8aca6860f8`. Main was pushed first and verified remotely at that release HEAD before closeout; the annotated tag was then pushed and its remote object/peeled target verified. The [GitHub Release v0.5.2](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.5.2) is published, non-draft and non-prerelease, and is the latest release. It contains exactly the intended wheel and sdist; GitHub authoritative asset SHA-256 digests match the local artifacts:

- `literature_monitor-0.5.2-py3-none-any.whl`: `53f23175fa657c3bac57eb24ee56154617da1d7907b6b6525254944a89137437`.
- `literature_monitor-0.5.2.tar.gz`: `086242d7810ca9d2b404622ed0930a52b8dae46df28089dcb1922a580104ba84`.

v0.5.2 is RELEASED and the latest released/completed baseline, with package metadata `0.5.2`. This documentation closeout follows the release transaction in a separate later commit outside the v0.5.2 tag target; the tag remains on the release HEAD. No new v0.5.2 live browser/Zotero mutation verification is claimed. Historical v0.5.1 live evidence remains historical; protected local objects remain untouched, untracked and excluded.

### 24.19 v0.5.3 Browser Companion v2 — release transaction complete, post-release documentation closeout

The reviewed implementation commit is `21547260157f121a1efd2a5e8f930fad5f26959f` (`Implement v0.5.3 Browser Companion v2`). Implementation, A0–A5 independent stage reviews, A6 automated integration/acceptance, the final code/behavior audit, closed-claim storage-fault and fresh-handoff activation fix reviews, and documentation closeout review are complete (§38.13); no known substantive implementation defect remains.

Release preparation changes only Python package version `0.5.2` → `0.5.3`, OpenAlex/Crossref User-Agent identity `literature-monitor/0.5.2` → `literature-monitor/0.5.3`, directly coupled version/documentation tests, the local editable package version in `uv.lock`, and current-state documentation. Product behavior and dependencies are unchanged. The independent companion manifest remains `0.1.0`, with unchanged permissions. Proactive/automatic institutional authentication remains future scope; current v0.5.3 authentication is manual in the same claimed normal-Chrome task tab.

Scoped v0.5.3 live evidence remains exactly the boundary in §38.13: normal Chrome `154.0.8037.93` with companion `0.1.0` verified fresh automatic handoff after the activation fix, the same claimed tab, committed publisher navigation, `BROWSER_ACTION`, current popup operations, earlier natural Retry recovery, pre-freeze task-tab close/slot release, and a later acquisition start. Correct direct publisher navigation and TEST_FORCED_XMU resolver entry were exercised. The two authorized paths provided no target PDF: no actual v0.5.3 target DownloadItem, private staging or Zotero registration occurred. EBSCO trusted final PDF action, successful XMU → eligible provider routing and post-freeze close were not live-exercised. No genuine challenge appeared: `HUMAN_VERIFICATION_NOT_PRESENT`; manual challenge continuation remains not live-exercised. Automated ownership/provider/staging/writer coverage does not establish live success; historical v0.5.1/v0.5.2 evidence remains historical.

Implementation-agent prepared-tree validation passed on **2026-10-04** with actual Node **v24.21.0**: all **8** shipped Browser Companion JS syntax checks passed; direct worker simulation **461 cases passed** (including activation source/current-tab **21**) and direct content adapters **120 cases passed**. Focused OpenAlex/Crossref/companion pytest: **474 passed / 0 skipped / 0 warnings**. Full pytest with required Node PATH: **3280 passed / 0 skipped / 2 existing dependency warnings**. `uv lock --check` and `git diff --check`: PASS. The dependency graph is identical; only the local editable package version changed in `uv.lock`. These tests are automated/synthetic evidence and do not add live browser/staging/Zotero evidence.

An isolated Git export of implementation commit `21547260157f121a1efd2a5e8f930fad5f26959f` plus the release-preparation diff contains only the **145 tracked files** and passed `uv sync --frozen --offline`. An offline wheel/sdist build produced `literature_monitor-0.5.3-py3-none-any.whl` and `literature_monitor-0.5.3.tar.gz`, both with metadata version `0.5.3`. All **67 expected package files**, including modules, GUI templates and static assets, match the prepared source; README/LICENSE and semantic sdist TOML were verified. Neither artifact includes the standalone `browser_companion/`, protected monitor/workspace/Obsidian objects, tests, local caches or browser data. Isolated installed-wheel smoke passed: **31 installed distributions** (including the frozen development group) match the frozen lock/environment, **11 module imports** resolve inside the isolated environment, `literature-monitor --help` succeeds, and **13 GUI templates / 4 static assets** are available. `uv pip check` passed. Temporary exports, build/install environments and artifacts stayed outside the repository and were cleaned after validation; no artifacts were published.

These are prepared-tree results from this task, not an independent release-preparation review or a separate final clean-export full release-validation audit.

Independent release-preparation review passed before this release task. The exact reviewed **11-file** diff (**52 insertions / 22 deletions**) was committed as `4a2595e56bfcfb7d845216c5161e978628d3a71a` (`Prepare v0.5.3 release`), whose parent is implementation commit `21547260157f121a1efd2a5e8f930fad5f26959f`. This commit is the permanent **RELEASE_HEAD** and tag target.

Final exact-release-HEAD clean-export validation passed on **2026-10-04** from a fresh Git archive of `4a2595e56bfcfb7d845216c5161e978628d3a71a`: all **145 tracked source files** remained byte-identical after validation, with no local/protected objects exported. Actual Node **v24.21.0** passed all **8 shipped JS syntax checks**, direct worker simulation **461 cases** (including activation source/current-tab **21**) and content adapters **120 cases**. Full pytest in that clean export passed **3280 / 0 skipped / 2 existing dependency warnings / 0 failed**. `uv lock --check` passed. This final release validation belongs to the exact committed RELEASE_HEAD and is distinct from the preceding prepared-tree results and independent reviews.

Frozen offline sync and the final offline wheel/sdist build passed from that same exact export. The final artifacts below were verified and retained as one set for upload; no replacement set was rebuilt. Both metadata versions are `0.5.3`; all **67 package files**, modules/templates/static assets, README/LICENSE and semantic sdist TOML match release source. Companion/protected/local/test/cache/browser objects are absent. Isolated installation of the exact final wheel passed **31 frozen distributions**, **11 representative imports**, CLI `--help`, **13 GUI templates / 4 static assets**, and `uv pip check`.

- `literature_monitor-0.5.3-py3-none-any.whl`: **230048 bytes**, SHA-256 `0e84fc037e5c77d070f209d3bded62cfe665d2badecf453e5da0d35f30506b12`.
- `literature_monitor-0.5.3.tar.gz`: **216240 bytes**, SHA-256 `8ab3e02aca64a8ce81c2549fe377e53baf3c9179d761040f1f0edbae15437092`.

The pre-release remote guard verified repository `dylaaan-booob/literature-monitor`, GitHub auth, clean tracked/staged state, unchanged remote main `493e5b69b99c58e0a2fb7eee49f409c1b067cc8d`, its ancestor relationship, the exact two local commits ahead, and absence of v0.5.3 tag/Release. Annotated tag `v0.5.3` has object `1b300575bd1c1fe688db6266c677a576cbbc7aa3` and peeled target `4a2595e56bfcfb7d845216c5161e978628d3a71a`. Main was pushed normally and verified at RELEASE_HEAD before the tag push; the remote annotated tag object and peeled target then matched the local tag. No force push or history rewrite occurred.

[GitHub Release v0.5.3](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.5.3) is published, non-draft and non-prerelease, with exactly the final wheel and sdist above. Independent GitHub API queries verified the annotated tag target and authoritative asset SHA-256 digests matching both local final artifacts. Release notes retain §38.13's partial live boundary and manual same-task-tab institutional authentication; no new live PDF/staging/Zotero/EBSCO/XMU/human-verification success is claimed.

v0.5.3 is RELEASED and the latest released/completed baseline. This post-release documentation closeout is a separate change after the release tag, outside the v0.5.3 tag target and release artifacts. The annotated tag remains permanently on RELEASE_HEAD. The closeout changes only current-state documentation and its directly coupled static status assertion; release artifacts, package/runtime behavior, live-evidence boundaries and historical evidence are unchanged. Protected local objects remain untouched, untracked and excluded. Historical v0.5.1/v0.5.2 evidence retains its original baseline meaning.

### 24.20 v0.6.0 Zotero Connector Transition & Publisher Access — release preparation

The completed v0.6.0 implementation commit is `f6600dd6d955699c0ce0a066f8d16067533d89ce` (`Implement v0.6.0 Zotero Connector workflow`). It retires the Literature Monitor Browser Companion, custom browser/PDF acquisition and Zotero write path; adds Open DOI plus read-only exact-DOI Zotero parent reconciliation; adds the saved-Journal Publisher access projection and Settings UI; closes current-state documentation; and includes Final Audit Fix 1 so `resolve_identity()` verifies only exact-DOI top-level accepted bibliographic parents.

Release preparation is deliberately narrower than the implementation commit. It changes only package version identity `0.5.3` → `0.6.0`, OpenAlex/Crossref User-Agent identity `literature-monitor/0.5.3` → `literature-monitor/0.6.0`, directly coupled version assertions, the local editable package version in `uv.lock`, and current-state/release-preparation documentation. It must not change product functionality, dependency membership, Open DOI/reconciliation behavior, Publisher grouping/UI behavior, Local API behavior, CLI behavior, or the retired-path boundary.

Implementation/final-audit evidence is distinct from release-preparation validation. The completed final audit recorded focused **912 passed / 9 skipped / 2 warnings**, full pytest **2381 passed / 9 skipped / 2 warnings**, `uv lock --check` PASS and `git diff --check` PASS; Node was unavailable in the independent AgentDock environment and therefore no executable JavaScript PASS is claimed. Post-commit full pytest again recorded **2381 passed / 9 skipped / 2 warnings**. No live v0.6.0 browser, publisher-login, or Zotero Connector automation validation is claimed, and historical v0.5.x browser/PDF live evidence remains historical.

Current release-preparation validation against this prepared source passed: focused OpenAlex/Crossref pytest **419 passed**, full pytest **2381 passed / 9 skipped / 2 existing dependency warnings**, `uv lock --check` PASS, and `git diff --check` PASS. Node remains unavailable in this AgentDock environment, so the Node-dependent skipped JavaScript test is not reported as executable JS PASS. A repository-external build produced `literature_monitor-0.6.0-py3-none-any.whl` and `literature_monitor-0.6.0.tar.gz`; both metadata versions are `0.6.0`, required Literature Monitor modules/templates/static assets are present, and Browser Companion plus protected `monitor.yaml`/`workspace/`/`.obsidian` objects are absent. Isolated installed-wheel smoke passed representative imports, Web app/template availability, `literature-monitor --help`, and `uv pip check`. Build/install artifacts remained outside the repository and were not published. These are release-preparation results, not an independent release-preparation review or a release transaction.

Release state at this point: v0.5.3 remains the latest RELEASED baseline; v0.6.0 is prepared/unreleased. The release-preparation diff still requires independent review and has not been committed. No v0.6.0 tag, push, GitHub Release, or release asset publication has occurred.

---

## 25. v0.4.0 Python Local Web UI Application Contract

v0.4.0 adds a local Web application as an adapter over the existing monitor, journal configuration, canonical production pipeline, Markdown workspace, and kept-export boundary. It does not redefine the literature-monitoring domain model.

### 25.1 Runtime architecture and delivery boundary

The Web application uses:

```text
FastAPI
+ Jinja2
+ HTMX
+ minimal browser-only JavaScript
```

Frontend delivery is Python-only. HTMX is vendored inside package static assets. The project does not require Node, npm, Vite, a SPA build pipeline, React, Vue, or another JavaScript application framework.

The application runs as one local process with one server worker and binds only to `127.0.0.1`. v0.4.0 does not provide LAN/server deployment, remote multi-user operation, login/password authentication, or an account system.

The durable-state contract from §12 remains authoritative. The Web application must not introduce:

- a GUI database;
- persisted Inbox membership;
- persistent run history;
- generic job records;
- frontend workflow state;
- a second Paper decision state.

### 25.2 Stable application boundaries

The stable application boundaries are:

```text
application.monitor
application.settings
application.workspace
application.decisions
```

Both CLI and Web entry points call these application boundaries. Current v0.4.3 Provider-state and reporting changes are defined once in §30 and supplement this released v0.4.0 application contract. Web routes, HTMX handlers, and Jinja templates must not directly implement provider orchestration, evidence consolidation, canonicalization, materialization, YAML mutation, frontmatter mutation, or other domain/business rules.

The application layer may call the existing lower-level provider, parsing, canonicalization, materialization, and export modules. It must not duplicate those implementations behind Web-specific code paths.

### 25.3 Monitor application contract

The public monitor operations are:

```python
run_monitor(...) -> RunResult
validate_monitor(...) -> ValidationResult
```

`run_monitor` is the common formal entry point for CLI `run` and GUI Run. Its application API has no `reuse_provider_cache` behavior switch; automatic reuse and transient usage reporting follow §§30.6 and 30.8. The common canonical production core shared by CLI `run`, GUI Run, CLI `canonicalize`, and CLI `materialize` stops at canonicalization:

```text
retrieval
→ provider evidence consolidation
→ local matching
→ canonicalization
```

The four entry points must not duplicate orchestration inside that core. After canonicalization, CLI `canonicalize` stops and emits canonical papers without materialization. CLI `materialize` consumes the core's canonical papers and invokes the formal materialization path while preserving its explicit `--output-dir`. CLI `run` and GUI Run consume the same canonical papers and invoke that same formal materialization path. These boundaries preserve the existing observable behavior of `canonicalize` and `materialize`.

Lower-level diagnostic commands may continue to use existing lower-level modules where their diagnostic behavior requires it.

GUI Run has no temporary date controls. It always uses the persisted Monitor date policy. CLI date overrides retain §20.3 semantics: once any CLI date argument is supplied, the CLI fields form the complete ephemeral override and must not be merged with configured date fields.

The public workflow-progress contract has exactly these stages:

```text
CHECKING_MONITOR
DISCOVERING_PAPERS
COMBINING_METADATA
MATCHING_LITERATURE
UPDATING_WORKSPACE
```

These five `ProgressStage` values remain the stable workflow-location contract. v0.4.1 extends runtime feedback with the independent transient `Activity` layer defined in §29. Provider pagination, journal counts, ISSN counts, DOI lookup counts, retry state, and other operation-level details may populate Activity, counters, or timing data, but they must not become additional `ProgressStage` values.

The progress callback or semantically equivalent application-level reporting boundary remains synchronous and independent of FastAPI, HTMX, asyncio, or a particular thread type. It may report stage transitions and current Activity. Its public semantics must not make provider pagination or provider request counts part of the stage model.

`RunResult` must at least express:

- resolved date range;
- canonical paper count;
- created papers;
- matched-existing papers;
- updated papers;
- created authors;
- existing authors;
- warnings;
- errors;
- outcome;
- typed transient Provider-state usage summary (§30.8), separate from live coverage.

The outcome set includes at least:

```text
COMPLETED
COMPLETED_WITH_WARNINGS
COMPLETED_WITH_ERRORS
INVALID_CONFIGURATION
```

A provider error does not imply that the entire run failed. When other evidence can still produce Papers and materialization completes, the run may return `COMPLETED_WITH_ERRORS`. Uncaught programming errors and system exceptions must propagate to the execution boundary and must not be disguised as ordinary business `RunResult` values.

CLI exit compatibility is preserved:

```text
completed / valid
→ exit 0

provider or materialization error
→ exit 1

local configuration or deterministic preflight error
→ exit 2
```

Accordingly, a CLI run that completes materialization while reporting provider errors still exits `1`, even though its application outcome can be `COMPLETED_WITH_ERRORS`.

`validate_monitor` preserves the current validation semantics:

```text
configuration loading
→ keyword lexical / FTS5 validation
→ runtime date resolution
→ configured OpenAlex Source resolution
```

It must not retrieve OpenAlex Works, call Crossref Works, call Semantic Scholar, create a workspace, or materialize Papers, Authors, or Inbox presentation artifacts.

### 25.4 Workspace application contract

The public workspace operation is:

```python
load_workspace(output_dir) -> WorkspaceSnapshot
```

It must reuse the existing `parse_paper_state()` parser. Valid Papers and workspace issues are separate outputs. A corrupt Paper or a Paper that cannot be parsed reliably does not enter workflow views, but its problem appears in workspace issues.

Workspace membership is derived on every load from the current Paper Markdown `status`:

```text
candidate  → Inbox
kept       → Kept
rejected   → Rejected
in_zotero  → In Zotero
```

Membership is neither persisted nor cached as workflow state. Default ordering remains:

```text
discovered_at DESC
publication_date DESC
title ASC
```

`WorkspaceSnapshot` is independent of `RunResult`. The workspace always reflects current disk contents, regardless of what the latest run reported.

### 25.5 Decision application contract and safe filesystem writes

The public decision actions are exactly:

```python
keep_paper(...)
reject_paper(...)
mark_paper_in_zotero(...)
```

There is no public arbitrary `set_status` action or generic status mutation route.

v0.4.0 permits only:

```text
candidate → kept
candidate → rejected
kept      → in_zotero
```

A decision request identifies the Paper by its stable Paper UUID plus the expected current status. A browser-supplied or server-generated filesystem path is not an authoritative Paper identifier.

Before mutation, the decision boundary must:

1. rescan or otherwise relocate the Paper by UUID from the current workspace;
2. reject missing UUIDs, duplicate/ambiguous UUID locations, non-regular/unsafe targets, and other unsafe location results;
3. reread the complete current file bytes;
4. call `parse_paper_state()` again on the current content;
5. verify the Paper UUID;
6. verify that the Paper is updateable;
7. verify the expected current status;
8. verify that the requested transition is allowed;
9. prepare a change that modifies only `status`;
10. preserve unknown frontmatter fields;
11. preserve `Notes` and every human-managed body section;
12. perform compare-before-replace so a concurrent change cannot be silently overwritten.

`DecisionResult` must distinguish at least:

```text
updated
not found
invalid paper
state conflict
invalid transition
I/O failure
```

`safe_write.py` contains only general filesystem semantics such as exact UTF-8 reads, atomic replace, and compare-before-replace. It does not understand Paper, Monitor, Journal, workflow states, or frontmatter. `materialize.py`, `application.decisions`, and `application.settings` may reuse it. v0.4.0 must not turn `materialize.py` into a generic persistence framework.

### 25.6 Settings application contract

The Settings application boundary edits the same persistent monitor and journal configuration used by the runtime. It does not create GUI-specific configuration or validation rules.

`MonitorDraft` expresses at least:

- name;
- keyword expression;
- journals;
- date policy;
- output workspace;
- log level;
- current monitor-config file revision;
- current journal-data file revision.

Runtime configuration loading and GUI draft validation reuse the same deterministic config/domain validation path. Settings-specific handlers must not reimplement keyword, date-policy, path, or journal validation. The provider-resolving stage of `validate_monitor` remains separate; Settings open, validate, and save do not contact providers.

Concrete journal storage syntax is isolated at the storage/parser boundary. Routes, templates, `MonitorDraft`, monitor execution, and decisions operate on:

```python
tuple[JournalConfig, ...]
```

The current `list.md` format may remain the initial journal storage representation, but v0.4.0 does not freeze a future journal data-file format.

When Settings opens, it computes a content revision from the exact bytes of both the monitor config file and the journal data file.

Settings Save follows this sequence:

```text
validate the complete draft
→ reread both current files
→ compare both content revisions
→ abort on revision conflict
→ prepare both complete target contents
→ write journal data file
→ write monitor config file
→ reread actual disk state
→ report the resulting state
```

No write begins until both complete target contents have been prepared. If the journal write fails, the monitor file is not written. If the journal write succeeds and the monitor write fails, the result reports a partial save, rereads the real disk state, and must not display or report Save successful.

This two-file operation is deliberately not an atomic cross-file transaction. v0.4.0 does not add SQLite transactions, a rollback framework, or another complex persistence subsystem to simulate one.

### 25.7 Transient RunCoordinator

The process-local run coordinator lives at:

```text
web/run_coordinator.py
```

It owns only transient process state:

- at most one active run;
- one dedicated in-process worker that calls synchronous `run_monitor()`;
- current public progress stage;
- relevant transient run timestamps;
- the current session's last `RunResult`;
- an unexpected-error record suitable for safe UI reporting and server logging.

Coordinator state is:

```text
IDLE → RUNNING → FINISHED
          ↑         │
          └─────────┘ next start
```

A later start may move from `FINISHED` back to `RUNNING`. Run history is not persisted.

A small coordinator lock protects at least:

- coordinator status;
- current progress stage;
- relevant transient run timestamps;
- current `RunResult`;
- unexpected error state.

HTTP polling and other read paths acquire the lock only long enough to copy an immutable coordinator snapshot, then release it immediately. HTML rendering, template work, workspace loading, or other comparatively slow work must occur after the lock is released.

The synchronous `run_monitor()` call executes in the dedicated worker. The HTTP server must remain responsive while the monitor pipeline is running. Repeated start requests while the coordinator is already `RUNNING` must not create a second concurrent production run. `RunCoordinator.start` receives no cache mode (§30.6); concurrent Provider progress updates retain coordinator serialization and follow §30.10.

Provider clients remain synchronous. These concurrency semantics do not require an asyncio provider rewrite, a generic queue, Celery, Redis, RabbitMQ, WebSocket, SSE, or a more elaborate threading framework.

### 25.8 Web application behavior

This section describes the current shared Web adapter behavior. The implemented v0.4.6 presentation contract is authoritative in §33; unaffected v0.4.0 application boundaries remain applicable.

The application factory is:

```python
create_app(config_path: Path) -> FastAPI
```

or a semantically equivalent interface.

Application creation does not require the current monitor configuration to be valid. A missing or invalid monitor file, or a missing/invalid journal data file, must not prevent the server from starting; the UI must remain able to enter Settings and repair the configuration.

The full-page routes are:

```text
GET /           → Workspace, default Inbox
GET /settings   → Monitor Editor
```

When Monitor configuration, Journal configuration, or the workspace cannot form a normal Workspace view, `GET /` returns a normal recoverable HTML empty/error state with an entry point to Settings. The UI may guide or redirect the user to Settings, but redirect is not the only valid behavior. Expected configuration or workspace failures must not become a generic HTTP 500, and application startup must remain successful.

Paper mutation routes are exactly:

```text
POST /papers/{paper_id}/keep
POST /papers/{paper_id}/reject
POST /papers/{paper_id}/mark-in-zotero
```

There is no generic arbitrary-status route.

Run routes are:

```text
POST /run
GET  /fragments/run
```

Settings routes are:

```text
GET  /settings
POST /settings/validate
POST /settings/save
```

`POST /settings/validate` validates the browser's current unsaved `MonitorDraft`. It does not require the draft to be saved first, does not write the monitor config, does not write the journal data file, does not call any provider, and uses the same deterministic validation rules as runtime configuration.

Expected application failures are represented as normal, understandable HTML error/state responses rather than being collapsed into generic HTTP 500 responses. This includes at least decision not found, invalid Paper, state conflict, invalid transition, decision I/O failure, Settings validation failure, Settings revision conflict, and run-already-active.

Unexpected programming or system exceptions remain distinct from those expected failures. The adapter or coordinator outer boundary records the traceback in server logging, while ordinary HTML UI responses do not display the traceback.

HTMX fragment responses cover at least:

- the workspace composite snapshot;
- Paper list;
- Paper detail;
- run status/result;
- the lightweight workspace issue indicator/count and details entry point.

Healthy Workspace renders no standalone `Workspace issues` panel or `No workspace issues.` message. Full issue paths/messages belong only in `Advanced & Diagnostics → Workspace health`, while valid Papers continue loading despite invalid Papers. Advanced is initially collapsed, outside the Settings form, and excluded from dirty state, Validate, and Save. Its Workspace health target comes only from the currently saved, valid monitor configuration; an unsaved/recovery draft cannot supply that target (§33.2–§33.3).

The primary finished Run shows outcome, Paper result summary, and warning/error counts. Full warnings/errors, diagnostics, coverage, and Provider-state usage are presented only in `Advanced & Diagnostics → Current run`, using the current process-local coordinator result. Successfully starting a new Run removes the previous finished details from Current run; while active, Advanced shows only `Run in progress` for that area (§33.4).

The Web UI provides kept-Paper `Copy DOI` under §33.5 and removes the Workspace `Zotero export` panel and `Load export` / textarea workflow. No Web export fragment/route is required; unused Web-only fragment/route/context glue may be removed during implementation. CLI `export-kept` and `kept_export.py` remain unchanged (§33.6).

After a decision completes, the server reloads the workspace from disk. The browser does not infer the next workflow state or maintain a domain-state store.

Run completion does not make `RunResult` authoritative for workspace contents. The server rescans the workspace after completion. While a run is active, run-status polling occurs at approximately 0.5–1 second intervals. A finished run fragment stops polling and emits a completion event that triggers workspace refresh.

Browser JavaScript is limited to browser-only interaction such as K/R keyboard shortcuts, clipboard handling, and focus management. It does not own workflow state, provider orchestration, persistence, or canonicalization.

### 25.9 Web security, packaging, and dependency policy

Every Web mutation, including Run, Settings Save, and Paper decisions, requires a process-start random CSRF token. The same protection may be required for other browser POST endpoints such as Settings Validate. Tokens are process-local and are not durable application state.

The Web server must:

- restrict accepted Host values to the intended localhost/loopback host set;
- bind to `127.0.0.1` only;
- avoid broad CORS configuration;
- expose no LAN binding mode in v0.4.0;
- provide no account/password system;
- send unexpected tracebacks only to server logging;
- keep ordinary HTML error responses free of tracebacks.

Allowed v0.4.0 runtime dependencies are limited to those actually needed for this design, including:

- FastAPI;
- Uvicorn;
- Jinja2;
- the selected form-parsing dependency if form handling actually requires one.

HTMX is vendored as a package static asset rather than installed with a Node toolchain. Templates and static assets must be included in both wheel and sdist so the GUI works from an installed package.

The following remain outside the v0.4.0 architecture:

- Node, npm, Vite, or another frontend build toolchain;
- React or Vue;
- Redis or Celery;
- SQLAlchemy;
- a WebSocket framework;
- desktop wrappers;
- generic Repository/Service/Manager/Interface hierarchies;
- event bus or command bus infrastructure;
- a DI container.

### 25.10 Zotero boundary

v0.4.0 does not implement Zotero API integration. The GUI reuses the existing read-only kept-paper export boundary. It may present or copy the export result, but it does not ingest items into Zotero or infer `in_zotero` from Zotero state.

The preceding GUI export presentation records the v0.4.0 behavior. For v0.4.6, §33.5–§33.6 supersede it with kept-Paper Copy DOI and removal of the Web export panel; the CLI batch export and manual `Mark in Zotero` boundary remain applicable.

### 25.11 Testing responsibility boundary

v0.4.0 regression coverage follows the same application boundaries as production code.

Primary production-orchestration regression tests belong at the application layer, for example `test_application_monitor.py` or a semantically equivalent location. They cover the monitor behavior that must remain shared across CLI and Web, including validation, date resolution, provider partial failures, canonical production-core reuse, `RunResult` outcomes, materialization handoff, and progress reporting.

CLI tests primarily cover:

- argument parsing and CLI-only date-override handling;
- adapter wiring into the application layer;
- logging and user-visible output;
- exit-code compatibility.

CLI and application tests must not maintain duplicate copies of the same complete provider-orchestration fixture matrix.

Application-level regression coverage also includes the important workspace, decision, Settings, and RunCoordinator contracts defined in §25: corrupt-Paper isolation, disk-derived membership, decision transitions and conflicts, human-content preservation, compare-before-replace outcomes, Settings validation/revision/partial-save behavior, one-active-run semantics, coordinator snapshots, and unexpected-error separation.

Route tests verify the adapter boundary:

```text
HTTP input
→ correct application action
→ server-authoritative HTML state
```

They do not re-test Markdown merge algorithms, safe-write internals, canonicalization internals, or provider orchestration that is already covered at the corresponding application/domain layer.

Browser-level tests remain few and targeted to behavior that requires a real browser, such as HTMX polling and swap termination, completion-triggered refresh, keyboard shortcuts, and dirty-form behavior. Domain and persistence semantics remain below the browser layer.

Testability must not drive production-only hooks, exported symbols, setters/getters, widened visibility, or artificial interfaces that would not otherwise belong in the production design.

---

## 26. Non-blocking Implementation Details

The following do not block implementation and may be decided locally as long as the specification's observable behavior is preserved:

- internal Python package/file layout outside the stable application boundaries and the specified `web/run_coordinator.py` location;
- exact short-UUID length, provided collisions are checked;
- physical Provider-state layout within the fixed logical schema and strict/versioned serialization contract in §30.4;
- exact Markdown section ordering;
- exact format of the kept-paper export;
- exact retry/backoff library;
- whether provenance/version structures live entirely in YAML or partly in machine-managed Markdown sections.

These details should not change the core workflow or introduce additional scope.

---

## 27. Definition of Current Version Done

The v0.3.3 baseline remains complete when a user can reliably run the journal-monitoring pipeline, review the durable Paper Markdown corpus through the Obsidian presentation, preserve decisions and notes across reruns, export kept identifiers, and manually confirm `in_zotero` after downstream Zotero import.

v0.4.0 is released and remains complete: the same durable workflow is operable through the local Python Web UI defined in §25, and the acceptance criteria in §23.15 have been demonstrated. In particular:

- GUI Run and CLI `run` share `run_monitor()`, use the same canonical production core plus formal materialization path, and preserve the existing CLI date/exit-code contracts;
- Workspace views are reconstructed from current Paper Markdown and isolate corrupt records as issues;
- GUI decisions perform conflict-safe, status-only mutations while preserving unknown frontmatter and human-authored content;
- Settings uses shared validation, revision checks, and the specified non-transactional two-file save semantics;
- only one transient local run is active at a time and no run history or second durable workflow state is introduced;
- CSRF, Host restriction, localhost-only binding, traceback handling, and package asset inclusion are verified;
- the installed wheel/sdist GUI smoke test succeeds without Node/npm/Vite or repository-relative assets.

Obsidian remains a supported presentation of the same Markdown workspace; it is no longer the only daily workflow presentation.

v0.4.1 is released and complete. Its feature implementation, final independent audit, release preparation, and release transaction are complete, and the runtime progress/activity contract and acceptance criteria in §29 were implemented and verified without changing the durable workflow model or canonical production path.

v0.4.3 is released and complete. Its A1–A8 implementation, independent stage reviews, final independent audit, release preparation, release transaction, and closeout are complete (§24.11). The §30 contract and §23.16 acceptance requirements remain in force along with the preserved workflow acceptance criteria, subject only to the v0.4.4 supersede boundary in §31.1.

v0.4.4 is released and complete. Its §31 implementation and §23.17 acceptance are complete; A1–A5 independent stage reviews, final independent audit, release preparation, release transaction, and closeout are complete (§24.12). The preserved workflow and compatibility contracts remain in force.

v0.4.5 is released and complete. Its §32 implementation and A1–A6 independent stage reviews are complete; §23.18 acceptance has been demonstrated, including the full automated suite and A6 desktop/mobile manual Web smoke. The final independent integration audit passed and separately verified the corresponding desktop/mobile browser behavior. Release preparation, release transaction, and closeout are complete (§24.13).

v0.4.6 is released and complete. Its §33 implementation and A0–A3 independent stage reviews are complete; §23.19 acceptance has been demonstrated at its recorded automated/browser verification levels. The final independent integration audit and final release validation passed. Release preparation, release transaction, and closeout are complete (§24.14), with package metadata `0.4.6`. v0.4.6 was the current/latest released and completed baseline at its closeout.

---

## 28. Current Project Stage

R0–R3, v0.2.1 lexical search, v0.3.0 Review Inbox, v0.3.1 Prefix / Proximity search, v0.3.2 Persistent Monitor Definition, v0.3.3 Pre-GUI Correctness Hardening, v0.4.0 Python Local Web UI, v0.4.1 Runtime Progress, Activity, ETA, and Inactivity Feedback, v0.4.2 Provider Reliability, Coverage, and Explicit Cache Reuse, v0.4.3 Retrieval Efficiency & Revision-Validated Provider Evidence, v0.4.4 Crossref Elapsed-Aware Pacing & Partial Provider Evidence Semantics, v0.4.5 Candidate Eligibility, Warning Semantics & Scrollable Master-Detail Workspace, v0.4.6 GUI Cleanup — Advanced & Diagnostics + Kept Copy DOI, v0.4.7 Journals Organization & Bulk Import, and v0.5.0 Institutional PDF Acquisition to Existing Zotero Item are completed release history. v0.5.0 was the current released and completed baseline at its closeout.

v0.5.1 Version-Qualified Institutional PDF Acquisition via Normal Chrome is RELEASED and remains the previous completed baseline. A0–A9 implementation, independent final-audit Fix 1–3 review, full automated validation, scoped normal-Chrome/XMU acquisition, and actual Zotero child file registration verification are complete. Implementation acceptance and final audit are complete under §36.14's conditional live-verification boundary. A scoped real run explicitly sought human verification, but no genuine challenge naturally appeared; `HUMAN_VERIFICATION_NOT_PRESENT` records that environmental non-occurrence, not a live continuation pass or an implementation failure. No challenge was manufactured. Release preparation, final clean-export release validation, annotated tag creation, main/tag pushes, the GitHub Release and initial documentation closeout are complete (§24.17), with verified release assets and package metadata `0.5.1`. Current main additionally contains independently reviewed post-release PDF guard/cancellation ownership maintenance in `0b69d4b`, with unchanged user-visible acquisition semantics and no new version/tag/release artifact. §36 records that baseline contract and maintenance; its maintenance wording is not a claim that the post-release commit or updated specification was included in the v0.5.1 tag/artifacts. Historical release and acceptance facts remain unchanged.

The current released baseline is v0.5.2 DOI-first Single-Manifestation Simplification, governed by §37. A0–A5 implementation, independent stage reviews, final integration audit and release-preparation review are complete. Implementation commit is `f48574869969534daec589e124c6979011b5cbb5`; release HEAD is `d180c18aa853e4483a7d110d39bdfb8aca6860f8`. Annotated tag/main push/tag push/GitHub Release are complete (§24.18), with package metadata and Provider User-Agent identities `0.5.2`. The separate final clean-export full validation was explicitly skipped by user choice. No new v0.5.2 live browser/Zotero verification is claimed; historical v0.5.1 evidence remains historical.

The v0.4.4 release and closeout are complete, following §24.12. Its §31 implementation, independent stage reviews, and final independent audit are complete; the §23.17 acceptance scenarios have been demonstrated.

v0.4.5 Candidate Eligibility, Warning Semantics & Scrollable Master-Detail Workspace implementation, A1–A6 independent stage reviews, §23.18 acceptance, and full automated verification are complete. The A6 desktop/mobile manual Web smoke passed, and separate independent desktop/mobile browser verification passed during the final integration audit. §32 remains its historical authoritative behavior contract, subject to the narrow v0.4.6 Web presentation supersede boundary in §33.1. Release preparation, release transaction, and closeout are complete (§24.13); its released package metadata is `0.4.5`.

v0.4.6 GUI Cleanup — Advanced & Diagnostics + Kept Copy DOI implementation, A0–A3 independent stage reviews, and final independent integration audit are complete. §33 remains its authoritative behavior contract; §23.19 records demonstrated acceptance and the actual verification levels, including final release validation. Release preparation, release transaction, and closeout are complete (§24.14); its closeout stage was v0.4.6 released / closeout complete, with package metadata `0.4.6`. No subsequent product-development stage was established by that closeout.

v0.4.7 Journals Organization & Bulk Import implementation, A0–A7 independent stage reviews, and final independent integration audit are complete. §34 remains the authoritative behavior contract; §23.20 records demonstrated acceptance and actual verification levels, including final release validation. Release preparation, the release transaction, and closeout are complete (§24.15). v0.4.7 is released / closeout complete, with package metadata `0.4.7`, and was the current/latest released and completed baseline at its closeout. No subsequent product-development stage was established by that closeout.

v0.5.0 Institutional PDF Acquisition to Existing Zotero Item implementation, A0–A7 independent reviews, final audit, and the reviewed implementation commit are complete. §35 is its implemented and released behavior contract and §23.21 records the actual verification levels, including passing final release validation. Release preparation, its independent review and commit, the grouped Journal list commit, release transaction, and closeout are complete (§24.16). v0.5.0 is released / closeout complete, with package metadata `0.5.0`, and was the current/latest released and completed baseline at its closeout.

OpenAlex remains the primary discovery provider, Crossref the secondary discovery/bibliographic provider, and Semantic Scholar remains excluded. Automatic revision-validated Provider-state reuse is in scope for v0.4.3. Checkpoint resume, provider cursors/watermarks, late-index recovery, run history, scheduling/notification, and persistent execution databases remain excluded; the reconstructible Provider-state DB is not execution state.

---

## 29. v0.4.1 Runtime Progress, Activity, ETA, and Inactivity Feedback Contract

This section records the released v0.4.1 contract. The current provider policy in §6 supersedes historical Semantic Scholar runtime statements. §30.10 supersedes the single `current_activity` model with per-source Activity and estimators for v0.4.3; the remaining stage, ETA, liveness, accessibility, and transient-state rules continue to apply.

v0.4.1 gives GUI and CLI runtime feedback a shared transient contract. It extends the v0.4.0 application and `RunCoordinator` boundaries without changing the canonical production pipeline, Paper Markdown ownership, one-active-run rule, provider retrieval policy, or CLI result semantics.

### 29.1 Workflow stage and Activity model

`run_monitor()` remains the only formal public production entry point shared by CLI `run` and GUI Run. Its workflow location is still expressed by exactly these five `ProgressStage` values:

```text
CHECKING_MONITOR
DISCOVERING_PAPERS
COMBINING_METADATA
MATCHING_LITERATURE
UPDATING_WORKSPACE
```

The GUI presents this as `Stage N of 5`. The stage indicator represents discrete workflow position; it must not claim that the five stages consume equal time or that `N / 5` is a real elapsed-time completion percentage.

Operation-level runtime feedback is a separate transient `Activity`. The minimum Activity semantics are:

- `kind`: `WORKING`, `WAITING`, or `RETRYING`;
- `source`: optional provider or subsystem identity;
- `operation`: stable internal operation identity;
- `label`: human-readable current action;
- `detail`: optional supporting text;
- `current`: optional integer completed/processed count;
- `total`: optional integer denominator;
- `unit`: optional unit identity;
- `started_at`;
- `updated_at`;
- `rate`: optional smoothed current rate;
- `eta_seconds`: optional current-Activity ETA.

Activity represents only the current transient operation. It does not store an event history and must not be written to Monitor YAML, Paper Markdown, workspace files, a run database, or any other durable state.

The coordinator status model remains:

```text
IDLE → RUNNING → FINISHED
          ↑         │
          └─────────┘ next start
```

`WAITING` and `RETRYING` are Activity kinds inside `RUNNING`; they are not new `CoordinatorStatus` values. v0.4.1 must not add `WAITING`, `RETRYING`, `STALLED`, or another top-level coordinator state.

Determinate Activity progress is allowed only when the denominator is reliable. Examples include:

```text
3 / 8 journals
5 / 11 ISSNs
18 / 47 DOI lookups
7 / 14 files
```

When no reliable denominator exists, the UI and CLI may show indeterminate activity, a completed count, or ordinary activity text. They must not synthesize a percentage from an unknown or unstable denominator.

### 29.2 ETA and elapsed-time contract

Elapsed runtime may always be shown. ETA applies only to the current Activity; v0.4.1 does not define a whole-run ETA.

An Activity ETA is available only when all of the following are true:

- `total` is known and reliable;
- `current` has advanced through real completed work;
- at least two valid work units have completed;
- enough nonzero elapsed-time sampling exists to calculate a meaningful rate.

Before those conditions are met, the presentation uses `Estimating…` or an equivalent unavailable/initializing state. A lightweight smoothed rate is sufficient; an EWMA with smoothing around `0.3` is an acceptable model. v0.4.1 must not add a complex forecasting subsystem.

The estimator resets whenever Activity identity changes. Identity distinguishes at least:

- `stage`;
- `source`;
- `operation`;
- `unit`.

An invalid ETA is `None` or otherwise explicitly unavailable; an ETA from a previous Activity must never be carried forward. When an inactivity warning becomes active, the current ETA immediately becomes unavailable. When real activity resumes, ETA sampling restarts for the active identity rather than reusing stale samples.

### 29.3 CoordinatorSnapshot, timestamps, and liveness

The process-local coordinator snapshot must express at least:

- `status`;
- `progress_stage`;
- `stage_index`;
- `stage_total`;
- `current_activity`;
- `started_at`;
- `stage_started_at`;
- `last_activity_at`;
- `worker_alive`;
- `result`;
- `unexpected_error`.

All runtime progress, Activity, rate, ETA, liveness, and timing data remain process-local. `stage_total` is `5` for normal `run_monitor()` execution, and a stage change updates `stage_started_at`.

`last_activity_at` records real worker activity, not observation of coordinator state. Real activity includes:

- stage change;
- provider request-attempt start;
- provider response completion;
- page completion;
- journal or ISSN completion;
- DOI lookup completion;
- retry/backoff start;
- a major local operation start or completion;
- materialization paper/write completion.

The following must not update `last_activity_at`:

- `GET /fragments/run`;
- HTMX polling;
- coordinator snapshot/read operations;
- Jinja rendering;
- CLI spinner or progress-widget refresh;
- elapsed-time refresh;
- a UI redraw that does not correspond to worker activity.

No heartbeat thread is added. `worker_alive` reflects the real liveness of the current local worker thread. If coordinator status remains `RUNNING` after that worker is no longer alive, the GUI must not continue to present a normal Run in progress state. It must render an explicit stopped/error state while preserving the distinction between an unexpected execution failure and an ordinary inactivity advisory.

### 29.4 Inactivity advisory

Inactivity is advisory and is not a run failure.

When OpenAlex/Crossref request attempts, retry/backoff starts, and response completions are observable, `60` seconds without real activity activates a single `No recent activity` advisory. While that advisory is active:

- `CoordinatorStatus` remains `RUNNING`;
- the worker is not terminated;
- no `RunResult` is created or modified because of inactivity;
- the whole run is not automatically retried;
- the main progress indicator does not become an error indicator;
- current ETA is unavailable.

When real activity resumes, the advisory clears automatically and ETA sampling restarts.

If an implementation can observe only the start and end of an entire provider-client call, but cannot observe request/retry boundaries within that call, a `60`-second threshold is not safe. That implementation must use at least approximately `120` seconds and document why the coarser observation boundary requires the higher threshold.

v0.4.1 defines only one inactivity threshold. It does not add second-level or third-level stale thresholds, automatic cancellation, or a `STALLED` coordinator state.

### 29.5 Provider instrumentation

Provider instrumentation must use information already available during normal retrieval. It must not add provider API requests solely to improve progress reporting.

For OpenAlex:

- existing response pagination/meta information is reused;
- after the first page, a reliable record total may provide determinate progress for the current journal;
- request-attempt start, retry/backoff start, response completion, and natural page/journal completion are real Activity boundaries;
- no count-only or other progress-specific API request is added.

For Crossref discovery:

- normal response result/pagination metadata is reused when it provides a reliable denominator;
- request-attempt start, retry/backoff start, response completion, and natural page/journal completion are real Activity boundaries;
- no extra count request is added.

For Crossref supplementation, `assemble_provider_evidence()` already knows the exact DOI set before per-DOI lookup. That exact total should drive `current / total` DOI lookup progress and is one of the primary determinate Activities suitable for ETA.

The progress architecture does not depend on Semantic Scholar. Absence of a Semantic Scholar API key must not weaken OpenAlex, Crossref, matching, or materialization progress. When Semantic Scholar runs, basic Activity reporting is sufficient; an existing natural batch count may be used, but SDK-internal retry observability and precise Semantic Scholar ETA are not blocking v0.4.1 requirements.

### 29.6 Local processing instrumentation

`MATCHING_LITERATURE` keeps the existing FTS5 batch-processing design. Progress instrumentation must not convert matching into one FTS5 query per Paper.

Canonicalization does not gain a per-record callback. Coarse Activity at natural operation boundaries is sufficient, for example:

```text
Matching literature · N candidate works
Canonicalizing literature · N matched clusters
```

Materialization likewise uses existing natural boundaries rather than restructuring its core behavior. Meaningful Activities may include:

```text
Reading workspace
Preparing workspace updates · x/y papers
Writing workspace · x/y files
```

Progress instrumentation must not require a redesign of materialization semantics.

### 29.7 GUI presentation and accessibility

The GUI continues to use the existing HTMX polling model. v0.4.1 adds no SSE, WebSocket, external worker, queue, or background service. Poll frequency is a presentation concern and must not participate in liveness or inactivity calculations.

While a run is active, the GUI presents at least:

- a human-readable stage label;
- a primary progress bar/indicator labeled `Stage N of 5`;
- current Activity;
- a reliable counter when available;
- elapsed time;
- optional current-Activity ETA;
- last-activity information;
- the inactivity advisory when active.

A secondary determinate Activity indicator is allowed when a reliable denominator exists. Without such a denominator, the Activity is indeterminate or textual.

`No recent activity` uses warning/advisory semantics rather than failure/error semantics.

Elapsed time, ETA countdowns, last-activity age, polling, and redraws must not continuously trigger screen-reader live announcements. `aria-live` is reserved for semantically meaningful changes such as:

- stage change;
- Activity change;
- retry;
- inactivity advisory activation/recovery;
- completion;
- failure.

### 29.8 CLI runtime feedback

CLI `run` and `validate` both provide runtime feedback without changing their production behavior.

For a TTY, the display continuously updates current progress and Activity:

- `run` may present the five-stage workflow model;
- `validate` presents validation-specific progress and is not forced into the five-stage run model; journal `x/y` Source resolution is an example of a valid determinate validation Activity.

For a non-TTY:

- output consists of plain progress lines only on real Activity or state changes;
- no ANSI spinner/control characters are emitted;
- elapsed-time refresh alone does not produce repeated log lines.

All CLI progress output goes to `stderr`. Existing machine/data output on `stdout` remains compatible, and the existing exit-code semantics in §20.5 and §25.3 remain unchanged.

### 29.9 Explicitly out of scope for v0.4.1

The following remain outside v0.4.1:

- persistent run history;
- persistent runtime metrics;
- whole-run historical ETA;
- SSE;
- WebSocket;
- Celery;
- Redis;
- an external worker process;
- a generic job queue;
- a heartbeat thread;
- inactivity-triggered cancellation;
- a `STALLED` `CoordinatorStatus`;
- multiple inactivity thresholds;
- provider timeout-policy redesign;
- provider API requests added only for progress;
- per-Paper FTS5 execution;
- per-record canonicalization instrumentation;
- Semantic Scholar retry observability as a blocking requirement;
- Semantic Scholar retrieval-policy redesign;
- a public machine-readable `--progress=json` protocol;
- unrelated GUI redesign.

### 29.10 v0.4.1 acceptance and regression coverage

The v0.4.1 implementation is accepted only when all of the following hold:

- `run_monitor()` remains the sole formal public production entry used by CLI `run` and GUI Run;
- the five existing `ProgressStage` values retain their v0.4.0 semantics and provider/request counters do not become new stages;
- the GUI primary progress bar/indicator shows `Stage N of 5` without claiming that stage position is a real time percentage;
- determinate Activity is used only with a reliable denominator, while unknown denominators remain indeterminate or textual;
- elapsed, current-Activity ETA, and last-activity presentation follow §§29.2–29.4;
- ETA does not initialize until real progress has supplied the required work/time samples;
- Activity identity changes reset ETA sampling;
- an inactivity advisory makes ETA unavailable, and real activity recovery clears the advisory and restarts sampling;
- when request/retry boundaries are observable, `60` seconds without real activity activates the single inactivity advisory; if only whole-provider-call boundaries are observable, the threshold is at least approximately `120` seconds and the implementation documents the reason;
- `last_activity_at` changes only because of real worker activity;
- snapshot reads, HTMX polling, rendering, spinner refresh, elapsed refresh, and redraws do not manufacture activity;
- `stage_started_at` changes on stage transition;
- `worker_alive` reflects the real local worker thread state;
- a dead worker cannot leave a `RUNNING` coordinator rendered indefinitely as a normal active run;
- observable OpenAlex/Crossref request attempts, retry/backoff starts, and response completions can emit real Activity;
- provider instrumentation adds no API request solely for progress;
- Crossref supplementation uses the exact DOI total already known before per-DOI lookup;
- FTS5 matching remains batched and canonicalization does not become per-record solely for progress;
- materialization exposes progress only at natural, meaningful boundaries;
- missing Semantic Scholar credentials do not degrade the primary OpenAlex/Crossref/local progress path;
- TTY `run` exposes live workflow and Activity feedback;
- TTY `validate` exposes validation-specific progress;
- non-TTY operation emits plain event lines only on real state/Activity changes and no ANSI control stream;
- all CLI progress is written to `stderr`;
- existing CLI `stdout` and exit-code compatibility is preserved;
- GUI live-region behavior avoids repeated announcements from timer-only refreshes;
- runtime progress/activity/timing data is not persisted to Monitor YAML, Paper Markdown, workspace files, or another durable store;
- existing single-active-run semantics remain unchanged;
- unexpected exceptions remain distinct from an ordinary inactivity advisory;
- the existing test suite continues to pass.

Regression coverage must include at least:

- Activity snapshot update;
- `last_activity_at` updating only on real activity;
- `stage_started_at`;
- `worker_alive`;
- ETA initialization;
- ETA identity reset;
- ETA unavailable after inactivity;
- inactivity advisory activation;
- Activity recovery;
- provider retry Activity;
- GUI determinate and indeterminate Activity presentation;
- GUI inactivity presentation;
- CLI TTY progress;
- CLI non-TTY progress;
- `validate` progress;
- polling that does not fabricate activity.

---

## 30. v0.4.3 Retrieval Efficiency & Revision-Validated Provider Evidence Contract

This is the authoritative v0.4.3 retrieval contract. It supersedes conflicting released v0.4.2 retrieval/cache behavior; §24.10 and §6.8 retain that release history. A1–A8 implementation, independent stage reviews, final independent audit, release preparation, release transaction, and closeout are complete, with acceptance defined in §23.16. v0.4.3 is released.

### 30.1 Live membership and preserved domain semantics

A normal production Run remains the only user action. Every Run obtains live Provider evidence sufficient to establish the current candidate universe; expensive normalized metadata may be reused only after validating the current upstream revision. `keyword_expression` remains entirely local and must never be pushed to OpenAlex or Crossref.

Publication-date membership (§6), FTS5 semantics (§7), canonical identity/grouping (§§8–11), Paper/Author Markdown ownership and workflow state (§§12–17), and Zotero boundaries (§19) remain unchanged. Revision timestamps validate freshness of current evidence; they are not publication dates, discovery-window authority, update-date/watermark synchronization, or late-index recovery. Historical state alone cannot create candidates, even when the current window overlaps an earlier Run.

### 30.2 OpenAlex batching and retained version hydration

Source resolution is batched, with at most 100 ISSNs per batch. Production Run and `validate` share this resolver. Existing journal-name/title/ISSN consistency and conflicting-Source validation remain mandatory. Incomplete batch resolution may fall back to singleton resolution, but terminal auth/quota/circuit failures must not cause fallback request storms.

Works discovery uses multi-source OR filtering and thin fields, selecting the Provider's `updated_date` field rather than `updated_at` and excluding `locations`. OpenAlex-specific normalization maps raw `updated_date` to the internal timezone-aware UTC `OpenAlexWorkRecord.updated_at` revision marker. Because OpenAlex defines `updated_date` as UTC, values without an explicit timezone are interpreted as UTC at this Provider boundary; explicit UTC/offset values normalize to UTC. Missing or malformed `updated_date` yields no reusable revision binding and warns without removing the candidate. Generic revision parsing remains strict about explicit timezones. Incomplete pagination or malformed/unassignable batch evidence cannot create false per-journal COMPLETE coverage. Safe Provider-specific fallback may recover coverage without an optimization-layer user warning; unrecovered incompleteness remains conservatively reported under §30.7.

After local FTS5 retention is known, retained OpenAlex W IDs are batch hydrated for locations/version hints. Duplicate retained W IDs hydrate only once. Historical version state is reusable only when the resulting internal `updated_at` equals `hydrated_against_updated_at`. Missing revision means old version state is untrusted: retained records require live hydration, and hydration without a revision binding must not be persisted.

Version hydration affects version construction only and must not participate in research-work grouping. Hydration failure warns and continues canonicalization without fabricated version hints or reduced discovery coverage.

### 30.3 Crossref live manifests, full records, and DOI supplementation

Current membership is always established by a live manifest for the current publication-date window. Each manifest member includes at least DOI, ISSN, and one canonically parsed `indexed_at`. Retrieval uses repeated ISSN filters plus publication-date filters and a bounded Provider-specific planner; completeness must be verified rather than inferred from a successful HTTP response.

Manifest `indexed_at`, full hydrated Crossref record `indexed_at`, and state revision comparison all use one shared canonical `indexed_at` parser/normalization rule. Revision equality compares values produced by that same parser; separate path-specific timestamp normalization rules are not permitted.

The Crossref manifest planner's batch size is an internal implementation constant. It must not become monitor YAML configuration, CLI configuration, application API configuration, or persisted Provider state.

Oversized batches split ISSNs first, then non-overlapping publication-date intervals, then use cursor traversal only when a single day remains too large. Cursor traversal preserves the complete original query, uses the latest `next-cursor`, deduplicates DOI, and reconciles obtained results with `total-results`. Recoverable batch failure must split/fall back to reduce the failure domain and preserve successful evidence.

Full normalized Crossref state identity is normalized prime DOI. A matching live `indexed_at` permits normalized-state reuse. Missing state or changed revision requires current full hydration before that record can contribute current evidence. Full hydration includes every field already consumed by FTS5, consolidation, and canonicalization, including relation metadata. Historical state without a current live manifest anchor never creates a candidate. `crossref_record_matches_journal` venue validation remains mandatory. Duplicate DOI across ISSNs hydrates only once; one hydrated record may serve multiple current ISSN coverage units.

OpenAlex-only DOI supplementation uses batched Crossref DOI/indexed probing followed by revision-aware reuse or batch hydration. A requested DOI absent from the batch probe receives singleton fallback:

- 200: normal response handling;
- 301/308: resolve prime DOI;
- 404: UNAVAILABLE.

The alias prime DOI is the Crossref evidence/state identity; normalized requested DOI remains the supplement coverage identity. Alias mapping itself is not persisted in v0.4.3. Supplement evidence remains anchored to current OpenAlex evidence, never historical membership.

### 30.4 Narrow reconstructible Provider-state schema

Production introduces `<output_dir>/.literature-monitor/provider-state.sqlite3`. Its logical durable schema contains only:

| State | Identity and stored fields |
| --- | --- |
| Schema metadata | Singleton schema version |
| CrossrefRecordState | Normalized prime DOI primary key; `indexed_at`; `retrieved_at`; `semantic_hash`; strict/versioned normalized Crossref record serialization |
| OpenAlexVersionState | Canonical OpenAlex W ID primary key; `hydrated_against_updated_at`; `retrieved_at`; strict/versioned normalized version-hints serialization |

State must not persist membership, Paper, Author, workflow, decisions, monitor definition, run history, request history, cursors, watermarks, scheduler state, or checkpoints. Historical rows may remain indefinitely but cannot independently manufacture candidates. The DB is reconstructible from live APIs and cannot become a second source of truth for workflow state.

Valid DB updates use one transaction/upsert and retain historical rows absent from the current window. Do not enable WAL. Provider branches return pending changes in memory; durable writes occur only at the production persistence boundary after branch completion. SQLite connections must not cross Provider worker threads.

### 30.5 State failure and persistence boundary

Missing DB means all-live retrieval; a successful production Run may create it. A corrupt or schema-incompatible regular DB is untrusted for that Run: retrieval continues all-live with a warning. After successful fresh-state construction, replacement of an invalid regular DB uses complete fresh app-owned state followed by atomic replacement, never deletion of the old DB first.

Symlinks, directories, or incompatible filesystem objects at the state path or metadata directory must not be deleted or overwritten. Valid-state write failure rolls back the transaction and preserves previous valid state. Locked/busy DB is a persistence failure; v0.4.3 introduces no inter-process workspace lock and no WAL.

Persistence failure does not roll back Paper/Author materialization. State validation and persistence issues use `MonitorIssueComponent.PROVIDER_STATE`, not PROVIDER_CACHE, and follow existing warning/outcome semantics without changing live coverage. Diagnostics, `validate`, `canonicalize`, and legacy/diagnostic `materialize` do not read or write persistent Provider state.

### 30.6 Legacy cache and API migration

Production automatically uses revision-validated Provider state. The application API no longer exposes a `reuse_provider_cache` behavior switch. `RunCoordinator.start` receives no cache mode; existing single-active-run and ALREADY_RUNNING behavior remains in force. Web removes “Run with cache reuse”; only normal “Run” / “Run again” remains, with existing CSRF protection.

CLI `--reuse-provider-cache` remains accepted on `run` until v0.5.0 as a deprecated compatibility flag. It is ignored and emits the exact notice at CLI stderr/logging level:

```text
Provider-state reuse is now automatic.
```

The notice is not a RunResult warning and must not change RunOutcome. `provider-cache.json` is completely ignored by v0.4.3 production execution: no read, write, deletion, migration, or byte modification. Once all real calls disappear, `application/provider_cache.py` and `application/provider_reuse.py` are deleted rather than retained as unused abstractions.

### 30.7 Live coverage invariants

Batching, state reuse, splitting, fallback, and parallel execution are implementation details. Existing `CoverageComponent` values and per-journal/per-ISSN/per-requested-DOI identities (§6.5) remain stable. Every production Run establishes live membership; state reuse therefore does not remove a currently processed unit from live coverage or synthesize a REUSED status.

Batch evidence that cannot reliably map to configured Source/ISSN identity must fall back, split, or report conservative incomplete coverage. When a DOI is confirmed as a current member by the live Crossref manifest but required current full evidence cannot be obtained, every current Crossref discovery coverage unit actually affected by that member must be `PARTIAL` or `FAILED`, according to the existing amount/trustworthiness of successfully obtained evidence. It must never remain `COMPLETE` or become `UNAVAILABLE`, because live membership has already been established; a complete manifest alone cannot hide the hydration failure. Per-journal/per-ISSN coverage identity remains unchanged. OpenAlex version-hydration failure follows §30.2 and does not reduce discovery coverage. Provider failure isolation and deterministic coverage reporting order remain in force despite branch concurrency.

### 30.8 Semantic hash and transient usage reporting

Crossref `semantic_hash` covers only normalized semantic fields Literature Monitor actually consumes. It excludes `indexed_at`, `retrieved_at`, and unconsumed fields. A revision change with unchanged semantic hash still updates current revision/retrieval/provenance state. The hash creates no durable change history and never substitutes for a live revision check.

RunResult gains a typed transient Provider-state usage summary expressing at least:

- Crossref metadata reused;
- Crossref metadata refreshed;
- Crossref metadata new;
- OpenAlex versions reused;
- OpenAlex versions hydrated.

CLI and Web completion views display usage separately from live coverage. Usage is not written to Paper Markdown or `last-run.json`; the snapshot compatibility/writer contract remains in §6.6. Existing `MonitorStatistics` count evidence actually used by the pipeline, including reused records; state reuse must not artificially reduce record counts.

### 30.9 Pooled synchronous transport and client lifecycle

Production Provider transport uses pooled synchronous `httpx.Client`; `httpx` becomes a runtime dependency. One Provider execution creates at most one OpenAlex client and one Crossref client. No request path creates a fresh client/session per request. Clients close on normal, validation, diagnostic, and exception paths.

Crossref alias singleton handling must observe 301/308 rather than losing alias identity through automatic redirect following. Provider pacing/retry/pagination/batching stays inside each Provider module, retaining the bounded reliability policy in §6.4.

### 30.10 Provider-only parallelism and per-source progress

Stage 2 parallelism exists only at Provider level: one OpenAlex branch and one Crossref branch. There is no journal-level or ISSN-level generic worker pool and no async rewrite. Each Provider returns in-memory records, issues, coverage, and pending state changes. Durable Provider-state writes occur after branch completion at the production persistence boundary (§30.4).

ProgressState changes from one `current_activity` to per-source Activity state. Existing sources, including application/openalex/crossref/workspace, remain supported. Each source maintains an independent estimator, applying §29's Activity identity, sample, ETA-reset, and transient-state rules independently. Progress from one source must not overwrite another source's Activity or estimator.

Run-level `last_activity_at` is the most recent real activity from any source. Inactivity warning occurs only when the entire worker has no real activity; a quiet Provider alone cannot trigger it. Provider rows display their own activity age. Existing §29 inactivity and liveness rules otherwise remain applicable.

RunCoordinator retains progress-update serialization. CLI adds equivalent serialization so simultaneous Provider callbacks cannot corrupt progress state or interleave one logical output line. The existing five top-level ProgressStage values and their ordering remain unchanged. HTMX 750 ms polling remains snapshot-only and never manufactures activity.

### 30.11 Explicit v0.4.3 exclusions

The scope exclusions in §4.2 remain in force. v0.4.3 additionally makes these boundaries explicit:

- Provider-side keyword search;
- OpenAlex paid updated-date synchronization;
- Crossref watermark synchronization;
- background synchronization, scheduler, or daemon;
- persistent cursor/checkpoint;
- generic cache/state/repository/hydration/batch frameworks;
- journal/ISSN worker pools or async Provider rewrite;
- Semantic Scholar;
- research-work identity redesign;
- Paper decision/workflow redesign;
- Zotero behavior changes;
- run history or Provider change history;
- workspace-wide inter-process locking.

---

## 31. v0.4.4 Crossref Elapsed-Aware Pacing & Partial Provider Evidence Semantics Contract

This is the authoritative v0.4.4 contract. Its A1–A5 implementation and independent stage reviews are complete, the §23.17 acceptance scenarios have been demonstrated, and the final independent audit passed. Release preparation, release transaction, and closeout are complete (§24.12). Acceptance remains defined once in §23.17. v0.4.4 is released.

### 31.1 Authority and preserved release contracts

§30 is retained unchanged as the historical v0.4.3 release contract. v0.4.4 supersedes only older clauses that conflict with the following behavior:

- Crossref pacing (§6.4 and the reliability policy referenced by §30.9);
- OpenAlex normalization and partial Provider evidence admission, including missing-field diagnostics and the missing-revision warning clause in §30.2;
- OpenAlex retrieval coverage and issue severity (§6.5 and conflicting OpenAlex interpretations of §30.7 and §23.16).

All other v0.4.3 Provider-state, revision reuse, manifest planner, alias handling, semantic hash, transport/concurrency, progress, and persistence contracts continue to apply. In particular, §§30.3–30.6 and §§30.8–30.10 remain effective except for the pacing change above. The existing Source batching, thin Works selection, OpenAlex UTC revision normalization, retained version hydration, and version-state binding rules in §30.2 remain effective; missing/unusable revision only disables the corresponding version-state reuse as specified in §31.3. The structural and traversal integrity rules in §30.7 remain effective under the metadata-versus-execution distinction in §6.5.

Publication-date discovery membership, live membership on every Run, local keyword filtering, conservative evidence grouping, internal UUID identity, Markdown ownership, human decisions/notes, and Zotero behavior remain unchanged. Provider evidence eligibility is distinct from `CanonicalPaper` eligibility; §9.2 still requires canonical title, journal, and at least one author.

### 31.2 Crossref elapsed-aware pacing

§6.4 defines the current elapsed-aware, per-request-class monotonic pacing and bounded retry policy. This policy applies to the actual Crossref requests issued by the existing manifest, probe, hydration, singleton fallback, and alias paths; operation names do not substitute for actual request class. Header updates and deadline calculation remain inside the synchronous Crossref Provider client, without persisted timing state or a new limiter framework.

`WAITING` represents actual proactive pacing; `RETRYING` represents retry after a real failure. Existing per-source Activity reporting and the five top-level stages remain unchanged. Revision-reuse progress redesign is outside v0.4.4.

### 31.3 OpenAlex partial Provider evidence envelope

The minimum usable OpenAlex Provider evidence envelope consists of all three:

- a valid OpenAlex Work identity;
- trustworthy attribution to a configured/resolved monitor Source under §31.4;
- at least one valid DOI or usable title.

Provider evidence may be incomplete and must not require strict `CanonicalMetadata` before supplementation, consolidation, or projection. Admission is separate from the canonical title + journal + at least one author invariant; retaining partial evidence does not manufacture canonical fields or relax candidate creation.

Normalization follows these rules:

| OpenAlex evidence | Required behavior |
| --- | --- |
| Valid DOI, missing/unusable title | Retain and allow DOI-anchored Crossref supplementation. |
| Missing DOI, usable title | Retain and allow local keyword matching; production Run emits no unconditional `missing_doi` warning. |
| Neither valid DOI nor usable title | Exclude from the usable evidence pipeline; a `WARNING` is permitted, but this fact alone is not an `ERROR` and does not lower successful traversal coverage. |
| Missing/unusable authors | Retain otherwise usable evidence with empty authors. |
| Missing/malformed abstract | Retain otherwise usable evidence with missing abstract. |
| Invalid DOI, usable title | Treat as DOI-less evidence and continue; do not delete the record. |
| Missing/unusable publication date, author IDs, ORCID, or `updated_date` | Degrade the affected field safely without independently discarding otherwise usable evidence. |
| Invalid/missing Work ID | Structural Provider-record failure; do not generate a synthetic ID. |

No metadata may be invented. Safe field degradation preserves available trustworthy fields, author order, identifiers, and provenance. Sparse publication-date metadata does not authorize any change to the existing publication-date request window or membership policy.

Missing or malformed `updated_date` leaves no reusable revision binding and disables reuse of the corresponding OpenAlex version state. Existing live retained-version hydration and non-persistence of unbound hydration remain in force. Revision absence alone must not produce a user-visible Run error or an ingestion-time missing-field RunIssue; genuine hydration failure retains its existing warning semantics.

Ordinary tolerable missing-field diagnostics must not first create RunIssues at ingestion and then depend on later supplementation to delete them. Genuine structural, scope-integrity, traversal, and request failures remain reportable. Coverage follows retrieval execution completeness in §6.5, independently of bibliographic completeness and canonical eligibility.

### 31.4 Source attribution and integrity

Multi-Source Works batch evidence must be assigned reliably to a configured/resolved Source. If attribution cannot be determined, continue the existing split/fallback behavior rather than guessing a Source or claiming complete per-journal coverage.

After narrowing to an explicit single-Source request scope, missing nested Source metadata may use that queried/resolved Source as the attribution fallback. This is a bounded attribution rule for absent nested identity, not authority to overwrite conflicting Provider evidence. If the Provider explicitly returns an identity that conflicts with the queried/resolved Source, request context must not override it; the conflict remains a scope-integrity `ERROR` with conservative coverage.

Source-resolution identity validation remains mandatory. Genuine normal Source absence is `UNAVAILABLE` plus `WARNING`; pure normal absence yields `VALID_WITH_WARNINGS` from `validate`, not `SOURCE_ERRORS`. Remote request failure, conflicting Source identity, and journal identity validation failure remain `FAILED` plus `ERROR` under §6.5.

### 31.5 Production supplementation, matching, and outcomes

Crossref supplementation continues to be driven by retained current OpenAlex provenance plus a DOI anchor, following §30.3. Partial OpenAlex title/authors must not block DOI supplementation. Successful supplementation must not leave warnings that merely describe the original OpenAlex missing title/author fields. Title-only usable OpenAlex evidence can proceed to local matching without an unconditional production missing-DOI warning.

After evidence consolidation and before keyword matching, identify any cluster whose projection has no usable title, author keywords, or abstract across its available evidence. Such a cluster is unsearchable: emit a warning and omit it from the normal matcher candidate set. In particular, an empty projection must not become a false-positive match through `NOT` or complement semantics. Existing Boolean/NOT semantics for normal searchable documents in §7 remain unchanged.

Search projection may use existing eligible fields across consolidated evidence. v0.4.4 does not introduce generic cross-provider field-level synthetic merging. A matched cluster that still cannot satisfy `CanonicalPaper` eligibility continues to produce the existing `insufficient_metadata` warning rather than an invented title, journal, or author.

`RunOutcome` and its existing completion precedence remain unchanged:

```text
ERROR present   → COMPLETED_WITH_ERRORS
else WARNING    → COMPLETED_WITH_WARNINGS
else            → COMPLETED
```

`INVALID_CONFIGURATION` retains its preflight/configuration meaning. No OpenAlex-specific or Crossref-specific outcome exception is introduced; coverage status itself remains separate from outcome. The intended change is to report genuine failures/warnings at the correct boundary, not to suppress them by special-casing final outcomes.

### 31.6 Compatibility and state

The v0.4.3 state and persistence contracts remain in force:

- `provider-state.sqlite3` retains the §30.4 schema; no partial-evidence/completion tables or durable records are added.
- Crossref revision-state, manifest planner, alias handling, semantic hash, Provider-state failure isolation, and persistence follow §§30.3–30.8.
- `last-run.json` remains schema v2 under §6.6, with `reused_units=[]` on new Runs. Historical v1/v2 snapshots are read using their recorded values, including recorded outcomes; new severity rules must not retroactively reinterpret historical outcomes or rewrite snapshots.
- Paper Markdown, Author Markdown, and monitor YAML schemas remain unchanged, preserving unknown frontmatter, human-controlled status, and human-authored content.
- `provider-cache.json` remains inert and untouched: no read, write, deletion, migration, or byte modification.
- The five existing `ProgressStage` values and their order remain unchanged: `CHECKING_MONITOR`, `DISCOVERING_PAPERS`, `COMBINING_METADATA`, `MATCHING_LITERATURE`, and `UPDATING_WORKSPACE`. Per-source progress remains governed by §30.10; revision-reuse progress redesign is excluded.

Partial evidence remains transient Provider/pipeline data, not a new persistent completion authority or second source of truth. Existing diagnostic/validation state-access restrictions and production persistence boundaries remain unchanged.

### 31.7 Historical diagnostic compatibility

Historical commands such as `openalex-filter` and `crossref-enrich` must accept the partial OpenAlex representation. Diagnostic filtering depends on eligible Provider evidence and its searchable projection, without requiring every OpenAlex record to hold strict `CanonicalMetadata`.

Adapt representation consumers while retaining these diagnostics' historical stage order and product role. They do not become alternative production pipelines, add a completion stage, or acquire persistent Provider-state access. Production issue policy in §31.5 does not authorize an unrelated redesign of Crossref optional-field diagnostics.

### 31.8 Explicit v0.4.4 exclusions

The existing §4.2 and §30.11 scope exclusions remain in force. v0.4.4 additionally excludes:

- a new metadata-completion Provider or completion Stage;
- generic cross-provider field synthesis;
- new persistent partial-evidence/completion state;
- research-work identity redesign;
- revision-reuse progress redesign or any new `ProgressStage`;
- scheduler, notification, or background sync;
- watermarks, checkpoints, or late-index recovery;
- Crossref optional-field warning redesign unrelated to this contract;
- Zotero behavior changes;
- last-run schema or Provider-state schema upgrades;
- release transaction, tag creation, or push as part of this development contract;
- release version or User-Agent bumps during implementation.

---

## 32. v0.4.5 Candidate Eligibility, Warning Semantics & Scrollable Master-Detail Workspace Contract

This is the authoritative v0.4.5 behavior contract. A1–A6 implementation and independent stage reviews are complete, §23.18 acceptance has been demonstrated, and the full automated suite passed. The desktop/mobile manual Web smoke passed during A6, and the final independent integration audit passed with separate verification of the corresponding desktop/mobile browser behavior. Release preparation, release transaction, and closeout are complete (§24.13). v0.4.5 is implemented and released; §23.18 remains its acceptance reference.

### 32.1 Authority and supersede boundary

§§30–31 remain intact as historical release contracts. v0.4.5 supersedes only conflicting clauses concerning:

- candidate eligibility, warning classification, and Provider-state semantics in §§31.5–31.6;
- the Crossref semantic-hash contract in §30.8, for consumption of `work_type` and logical schema-v2 migration;
- older conservative title-fallback behavior that emits Run warnings merely because a merge is refused;
- Web acceptance/UI behavior that conflicts with the desktop master-detail, selection, scrolling, and mobile flow specified in §32.6.

All unaffected v0.4.4/v0.4.3 contracts continue to apply: journal/publication-date retrieval, coverage and provider failure isolation, elapsed-aware pacing/retries, live revision validation and reuse, alias identities, Provider transport/concurrency, Markdown ownership, human decisions/notes, internal UUIDs, Zotero, five-stage/per-source progress, and historical last-run compatibility. Historical version-specific exclusions describe those versions; only the explicitly scoped Provider-state logical schema upgrade is introduced here. There is no general permission to expand retrieval, taxonomy, workflow, or persistence scope.

### 32.2 Candidate eligibility boundary and evidence

Candidate eligibility is transient pipeline state with exactly three values: `ELIGIBLE`, `INELIGIBLE`, and `SCOPE_DISPUTED`. It is evaluated after current Crossref supplementation completes and before `assemble_live_provider_evidence` / evidence consolidation. It is separate from Provider retrieval validity, publication-date membership, and the canonical title + journal + at least one author invariant in §9.2.

Downstream eligibility filtering does not rewrite retrieval coverage, turn successful retrieval into failure, reduce Provider-state usage counts for acquired/reused evidence, or prevent otherwise valid acquired Provider records from being persisted under the existing production boundary. An ineligible current record may be stored as reconstructible Provider evidence, but historical state alone still cannot create candidates.

OpenAlex `type` has no veto and is not an allow/deny list. Normalization additionally carries `primary_location.is_published` as true, false, or unknown; missing, malformed, or non-boolean values are unknown, without truthiness coercion. This evidence is transient and explicitly excluded from durable Provider state, `ProviderWorkEvidence`, Paper/Author Markdown, and existing diagnostic JSON surfaces. It must remain available to the eligibility boundary without extending those serialized representations. Current Source attribution and partial-evidence validation in §§31.3–31.4 remain mandatory.

The exact current Crossref record controls type/venue classification. Target ISSN means an explicitly matching normalized ISSN/EISSN for the configured/resolved journal associated with the current anchor; matching an unrelated whitelist journal is not sufficient. A target match among supplied ISSNs is support; nonempty ISSNs with no target match are explicit non-target evidence. A configured/resolved journal-name fallback uses strict normalized equality and is available only where specified below.

| Exact Crossref evidence | Eligibility |
| --- | --- |
| `journal-issue`, regardless of ISSN, name, or OpenAlex type/publication flag | `INELIGIBLE`. An issue/container is not an article candidate. |
| `journal-article` with a target ISSN | `ELIGIBLE`, with strong eligible evidence. This overrides an OpenAlex `paratext` / `book-chapter` label or contradictory publication flag for this anchor. |
| `journal-article` with explicit ISSNs but none matching the target | `SCOPE_DISPUTED`; container-name equality cannot override the ISSNs. |
| `journal-article` without usable ISSNs, with configured/resolved journal-name equality | `ELIGIBLE`, with weak venue support. |
| `journal-article` without usable ISSNs and without that name support | `SCOPE_DISPUTED`. |
| `other` or missing/unusable type, with or without venue support | `SCOPE_DISPUTED`. |
| Any other explicit non-journal type without a target ISSN | `INELIGIBLE`; a matching journal/container name cannot rescue it. |
| Any other explicit non-journal type with a target ISSN | `SCOPE_DISPUTED` because type and venue evidence disagree. |

This is a bounded product rule, not a publication-taxonomy framework. Crossref discovery still uses the existing live journal/date manifest without a `type:journal-article` filter; retrieval must retain the evidence needed for this downstream decision.

### 32.3 Supplement absence, publication fallback, and disputed matching

For DOI-anchored OpenAlex evidence, successful supplementation applies §32.2 to the exact returned record. When no exact record is available, distinguish authoritative Crossref absence, execution failure, and DOI-less evidence as follows. A missing/unresolved supplement verdict without exact evidence must not be fabricated as `UNAVAILABLE`; absent applicable publication evidence leaves scope disputed.

Crossref supplement `UNAVAILABLE` is authoritative Crossref absence, not an execution failure or unconditional exclusion. Use OpenAlex publication evidence:

| OpenAlex `primary_location.is_published` | Eligibility |
| --- | --- |
| true | `ELIGIBLE` under the already validated target Source attribution. |
| false | `INELIGIBLE`. |
| missing / invalid / unknown | `SCOPE_DISPUTED`. |

Crossref supplement `FAILED` is execution failure and establishes neither authoritative absence nor a trustworthy type/venue verdict. Retain the existing Provider execution error; a failed lookup cannot exclude a candidate solely because OpenAlex reports `is_published=false`:

| OpenAlex `primary_location.is_published` | Eligibility |
| --- | --- |
| true | `ELIGIBLE` under the already validated target Source attribution. |
| false | `SCOPE_DISPUTED`. |
| missing / invalid / unknown | `SCOPE_DISPUTED`. |

DOI-less OpenAlex evidence has no Crossref DOI lookup and independently uses its original publication mapping:

| OpenAlex `primary_location.is_published` | Eligibility |
| --- | --- |
| true | `ELIGIBLE` under the already validated target Source attribution. |
| false | `INELIGIBLE`. |
| missing / invalid / unknown | `SCOPE_DISPUTED`. |

A DOI-less record must not invent a supplement coverage unit. These eligibility decisions do not change the existing supplementation coverage/status meanings of `UNAVAILABLE` or `FAILED`. A `FAILED` lookup remains an error even if independent OpenAlex evidence is eligible; candidate fallback never masks execution failure. Publication fallback is eligible evidence but does not supply exact Crossref journal-article/target-ISSN confirmation.

Alias eligibility follows `CrossrefSupplementUnit.requested_doi` / `anchors`: the requested DOI identifies the current OpenAlex anchor and supplementation coverage, while the resolved prime DOI identifies the exact Crossref evidence and state. Apply the prime record's verdict to its requesting anchor rather than joining only on equal requested/prime DOI strings. Do not persist alias mappings or detach supplement evidence from current anchors.

`INELIGIBLE` records and their excluded supplement contributions do not enter consolidation, searchable projection, topic matching, canonicalization, or retained OpenAlex version hydration. They cannot produce `unsearchable`, including with empty fields or pure `NOT` expressions. These exclusions operate per anchored candidate and do not suppress independent valid candidates from another current anchor.

`SCOPE_DISPUTED` evidence continues through consolidation and local matching to protect recall. Its scope diagnostic remains inspectable even if no topic matches. Only a truly topic-matched cluster containing unresolved disputed evidence and lacking strong eligible evidence emits a Candidate Eligibility warning. Strong eligible evidence means exact current Crossref `journal-article` with target ISSN for that same identity cluster; a coincidentally matching title, weak name-only venue support, or OpenAlex publication fallback is insufficient to clear the dispute. Strong support suppresses only the eligibility warning, not contradictory metadata/identifier warnings or Provider execution errors. Unmatched disputes remain diagnostics only. A disputed-only cluster with an empty projection stays diagnostic-only and is omitted from matching, including pure `NOT` expressions; it cannot manufacture a match or an eligibility/unsearchable warning. Genuinely eligible unsearchable clusters and matched insufficient canonical metadata retain their warnings.

### 32.4 Identity, consolidation, and metadata equivalence

Author identity for conservative title fallback has three results:

- `MATCH`: sufficient author evidence agrees in count and sequence, with compatible normalized names/identities and no conflicting comparable stable identifier.
- `INCONCLUSIVE`: evidence is missing or insufficient to establish either agreement or contradiction; absence of an ID or author list is not itself a conflict.
- `CONFLICT`: actual contradictory comparable ORCID/OpenAlex author IDs or incompatible author names/count/sequence establish disagreement.

One-sided ORCID or OpenAlex author IDs must not automatically yield `CONFLICT`. If normalized names, author count, and sequence agree with adequate evidence, a missing corresponding ID on one side permits `MATCH`; different ID namespaces are not directly contradictory values. Title fallback merges only on `MATCH`; both other results preserve independent work identities. Distinct DOI components continue to forbid silent title-based merging, even with otherwise compatible authors. This does not authorize conference-to-journal or preprint-to-publication lifecycle grouping.

Blocked title fallback and refusal to merge conflicting DOI components become typed consolidation diagnostics. Each logical fallback/conflict group is counted once per diagnostic kind using deterministic group membership, rather than emitting an issue for every pairwise comparison. These diagnostics explain preserved separation without manufacturing a Run warning. Genuine snapshot identifier conflicts, such as contradictory stable IDs inside an already established identity, remain warning/error conditions.

Same-work author metadata comparison is a separate operation after DOI or equivalent high-confidence identity has been established. It tolerates deterministic representation equivalence for initials versus full given names, given/family display order, periods/whitespace, Unicode normalization, and Unicode hyphens. Name display order does not authorize reordering the author sequence; substantive author/count/order differences and incompatible comparable IDs remain conflicts. Preserve the current representative display name and merge only nonconflicting identifiers. Do not migrate existing author display names or silently replace conflicting identifiers.

Abstract comparison uses only deterministic normalization: extract text from HTML/JATS and decode entities, normalize Unicode and whitespace, normalize representational dash/punctuation variants, and disregard structural section-label differences when the body content is unchanged. Structural labels may be removed only as labels, not by deleting matching words throughout the prose. Normalization must preserve substantive text and order, including meaningful numbers, symbols, and negation. Equivalent normalized text does not warn; a genuine body-content difference remains a metadata warning. No fuzzy threshold, embedding, semantic similarity, or LLM comparison is permitted.

### 32.5 Run diagnostics and Provider-state v2

Run diagnostics are typed transient observations separate from warnings/errors. CLI and Web completion views display their kinds, logical-group counts, and useful context separately from real issues and coverage. Diagnostics do not change `RunOutcome`, CLI exit codes, or coverage and are not serialized into `last-run.json`, Paper/Author Markdown, monitor YAML, Provider-state tables, or any new durable history. An otherwise clean diagnostics-only Run is `COMPLETED`. Genuine metadata conflicts, snapshot identifier conflicts, eligible unsearchable clusters, matched unresolved scope disputes, request failures, and materialization/persistence failures retain warning/error handling and the existing precedence in §31.5.

From v0.4.5, normalized Crossref `work_type` is a consumed semantic-hash field: changing only `work_type` changes the current hash; revision/retrieval timestamps and unconsumed fields remain excluded. Logical Provider-state schema advances from v1 to v2. SQLite table layout and Crossref/OpenAlex normalized serialization versions remain unchanged; no new column, table, completion record, diagnostic history, or Paper/workflow state is introduced.

A valid v1 DB is supported input. Validate its schema, strict normalized records, and stored semantic hashes with the original v1 hash algorithm before conversion. Do not validate a stored v1 hash with the v2 algorithm or treat that expected difference as corruption. Once valid, convert to current v2 hashes/state in memory without writing the DB. That state is immediately available for the current Run's normal live-revision-validated reuse, without a cold start. Invalid v1 or v2 state retains the existing warning/all-live and safe-replacement behavior in §30.5; validation failure must not be disguised as successful migration.

Only the existing normal production persistence boundary performs durable v1→v2 migration. In the same transaction:

1. Recompute/update semantic hashes for every historical Crossref row with the v2 algorithm, including rows outside the current discovery window.
2. Update singleton logical schema metadata to v2.
3. Apply the current Run's pending Provider-state changes.

Commit all three together or roll them all back. Preserve historical out-of-window Crossref/OpenAlex rows; unchanged `record_json` does not require rewriting. OpenAlex version-state serialization/binding remains unchanged. New DBs use logical schema v2; valid v2 DBs continue the normal transactional pending-state update path. No WAL or cross-thread connection sharing is introduced.

Migration failure is a Provider-state persistence failure: roll back to the original still-readable v1 logical state, preserve its rows/schema/hashes, and emit a `MonitorIssueComponent.PROVIDER_STATE` persistence warning. Already completed Paper/Author materialization is not rolled back. The warning participates in the ordinary completion precedence; existing errors remain errors. A normal production Run reaching the persistence boundary may migrate even when completion contains warnings/errors; no clean-outcome-only migration gate is introduced.

`INVALID_CONFIGURATION`, read-only diagnostics, `validate`, `canonicalize`, legacy/diagnostic `materialize`, and other non-production paths must not trigger migration or gain Provider-state access. Reads/conversion alone do not migrate. Existing unsafe-filesystem and rollback protections remain in force, and legacy `provider-cache.json` remains inert and untouched.

`last-run.json` remains schema v2, with `reused_units=[]` for new Runs. Historical v1/v2 snapshots and their recorded outcomes remain readable without reinterpretation, migration, or rewriting. Provider-state logical schema v2 is independent of last-run schema v2. Paper/Author Markdown and monitor YAML schemas remain unchanged.

### 32.6 Scrollable master-detail Web workspace

At desktop viewport widths greater than 760px, the workspace is a viewport-bounded, equal-height two-column master-detail layout. Determine pane height from the available viewport/layout space rather than one fixed pixel height. The Paper list and detail pane each use `overflow-y: auto` and scroll independently; a long detail must not expand the list pane or force desktop review into an unbounded document.

The selected Paper has a visible active state in the list. Clicking a Paper changes selection/detail without replacing or resetting list scroll. A selected Paper must belong to the current active workflow view loaded from real disk state (§25.4). View switches and refreshes must revalidate selection against that view; an out-of-view UUID cannot leave stale detail visible. An empty view renders empty detail with no active Paper.

After a successful decision, reload the workspace from disk. If the Paper still belongs to the active view, retain it as selected. If it leaves the view, prefer the item now at the Paper's former list position (its next neighbor), otherwise the preceding item, otherwise empty detail. Workspace replacement restores the current list scroll neighborhood so the adjacent review context remains visible rather than jumping to the top. Scroll/selection/navigation hints are transient presentation data, not durable frontend state or authoritative workflow data.

Decision failure, state conflict, or I/O failure also reloads real disk state and displays the failure. Keep the previous selection only if still in the active view; otherwise render a valid refreshed selection/empty detail without claiming that the attempted decision succeeded or that a successful next-Paper step occurred. Neighbor navigation associated with success must not be fabricated from a submitted position when the decision failed.

Navigation index/position is only a presentation hint. Validate and clamp it to the refreshed view's bounds; it must not authorize workflow mutations, identify filesystem targets, bypass UUID lookup, or replace expected-status/transition checks. Existing decision actions, CSRF, compare-before-replace, and status-only Markdown preservation remain unchanged.

At widths ≤760px, remove desktop pane-height constraints and nested pane scrolling. List and detail stack in normal single-column document flow with ordinary page scrolling. Do not add virtualization, keyboard screening, collapsible detail state, or a frontend database. Existing local Web startup, Settings, RunCoordinator, run progress/polling, security, and Zotero behavior remain applicable.

### 32.7 Explicit exclusions and delivery boundary

The unaffected exclusions in §4.2 and the preserved release contracts remain in force. v0.4.5 excludes:

- Crossref manifest `type:journal-article` filtering and OpenAlex type allow/deny lists;
- a publication-taxonomy framework;
- conference→journal or preprint→publication lifecycle merging;
- persistent global research-work identity;
- fuzzy/AI author disambiguation or semantic abstract similarity;
- author display-name migration;
- durable diagnostic history/tables;
- `last-run.json` schema changes or Paper/Author Markdown schema changes;
- checkpoint/resume, scheduler, notifications, or persistent execution state;
- list virtualization, keyboard screening, collapsible detail state, or a frontend database;
- unrelated retrieval/performance refactors;
- release, tag, push, version bump, or User-Agent bump within this development contract.

The initial contract task changed only `SPEC.md`; the subsequent bounded A1–A6 implementation and independent reviews are complete. §23.18 acceptance and the full automated suite passed. The desktop/mobile manual Web smoke passed during A6, and the final independent integration audit passed with separate verification of the corresponding desktop/mobile browser behavior. Release preparation, the release transaction, and closeout are complete (§24.13), without changing this behavior contract. v0.4.5 is released; §23.18 remains its acceptance reference.

---

## 33. v0.4.6 GUI Cleanup — Advanced & Diagnostics + Kept Copy DOI Contract

This is the authoritative v0.4.6 behavior contract. A0 specification alignment, A1–A3 implementation, and A0–A3 independent stage reviews are complete, §23.19 acceptance has been demonstrated at its recorded automated/browser verification levels, and the final independent integration audit passed. Final release validation, release preparation, release transaction, and closeout are complete (§24.14). v0.4.6 is implemented and released, with package metadata `0.4.6`; §23.19 remains its acceptance reference.

### 33.1 Authority and supersede boundary

§§25 and 29–32 retain the released v0.4.0–v0.4.5 contracts and their completed historical facts. v0.4.6 supersedes only conflicting Web presentation clauses concerning:

- Local Web finished-run coverage placement in §6.5;
- Workspace issues, Settings Advanced, primary Run/Current run, and Web export fragment/panel behavior in §25.8;
- GUI presentation/copying of batch kept-export results in §25.10;
- Web completion Provider-state usage placement in §30.8 and its historical acceptance clause in §23.16;
- Web completion diagnostics presentation in §32.5 and its historical acceptance clause in §23.18;
- references to preserved Zotero presentation in §32.6, solely for the Web export-panel replacement.

In particular, §32.5 records that released v0.4.5 directly displayed diagnostics in CLI and Web completion views; this historical text is not rewritten to imply v0.4.6 behavior existed then. §33.4 now moves Web details into Current run while preserving the CLI diagnostics contract. The completed release records in §§24.7–24.13 and the unaffected v0.4.0–v0.4.5 acceptance/domain behavior remain intact.

This is a Web presentation change over existing application/domain data, not a retrieval or domain redesign. Candidate Eligibility, journal/date discovery, evidence consolidation, local filtering, canonical identity, Author behavior, Provider-state schema/revision reuse/alias semantics, coverage meaning, `RunOutcome`/CLI exits, durable Markdown ownership, decision safety, Settings save rules, progress, and Web security remain governed by their existing contracts. The v0.4.5 desktop/mobile master-detail behavior in §32.6 continues to apply.

### 33.2 Workspace hierarchy and issue isolation

A healthy Workspace main view displays neither a separate `Workspace issues` panel nor `No workspace issues.`. When `WorkspaceSnapshot.issues` (`workspace.issues`) is non-empty, the main view shows only a lightweight issue indicator/count and an entry point to view details.

Full issue paths/messages appear only in Settings `Advanced & Diagnostics → Workspace health`. This relocation does not hide issues from the data model or weaken invalid-Paper isolation: malformed/unreliably parsed Papers stay out of workflow views, and all other valid Papers continue to enter Inbox, Kept, Rejected, and In Zotero according to their current Markdown status (§25.4).

### 33.3 Settings Advanced and Workspace health target

Settings adds a read-only `Advanced & Diagnostics` area, initially collapsed and located outside the Settings form. It does not participate in form dirty state, Validate, or Save. Its content uses existing application reads rather than a second configuration or validation path.

`Workspace health` always resolves the output workspace from the currently saved, valid monitor configuration through the existing loading rules. Unsaved Settings edits, Validate results, and recovery drafts cannot change its target. After Save, use actual saved disk configuration, including the existing reread/partial-save semantics; do not assume that a submitted draft became authoritative. If the saved monitor configuration is missing, invalid, or cannot be loaded, show Workspace health unavailable rather than guessing a workspace from a recovery/unsaved draft.

When a valid saved target exists, load its current `WorkspaceSnapshot.issues` and show the complete issue paths/messages in Workspace health. No issue acknowledgment, dismissal, repair action, or health history is added.

### 33.4 Primary Run and Current run details

The finished primary Run continues to show:

- outcome;
- Paper result summary;
- warning/error counts.

It no longer directly shows diagnostics summary/details, coverage detail, Provider-state usage, or the full warning/error issue list. Diagnostics must not change primary success/error presentation: a diagnostics-only `COMPLETED` result with zero warnings/errors has the normal completed presentation. `INVALID_CONFIGURATION` continues to show a clear configuration problem; warnings/errors and unexpected failures keep their existing outcome/error semantics.

Settings `Advanced & Diagnostics → Current run` contains the full warning/error lists, typed diagnostics with kinds/logical-group counts/useful context, coverage, and Provider-state usage. It reads the existing process-local `RunCoordinator.snapshot().result` rather than a saved last-run snapshot or a new diagnostics store. Coordinator reads retain the immutable snapshot/small-lock boundary in §25.7; rendering occurs outside the lock.

After a new Run successfully starts, the previous finished details immediately cease to be Current run. While a Run is active, this Advanced area shows only `Run in progress`; the primary Run retains its existing live progress/polling behavior. Once finished, Current run displays the details available from that current result. Without a current process-local result, there are no finished details to show; an empty coordinator or unexpected failure must not resurrect an older result.

A page refresh within the same server process can show the retained finished details again. A server restart discards them; neither `last-run.json` nor any other persistent data may reconstruct full Run diagnostics. `last-run.json` schema and its existing CLI reader/writer behavior remain unchanged (§6.6, §32.5). CLI diagnostics, coverage, Provider-state usage, outcomes, and exit codes are unchanged.

### 33.5 Kept-Paper Copy DOI

Show `Copy DOI` only for a Paper with status=`kept` whose DOI in `WorkspacePaper.external_ids` is accepted by the existing Python/domain `normalize_doi()`. The canonical copy value is its normalized bare DOI, for example `10.1234/example`, never a value prefixed with `https://doi.org/`.

Candidate, rejected, and in_zotero Papers do not show Copy DOI. A kept Paper with no DOI or a DOI that cannot normalize shows no button at all, including no disabled fallback. There is no fallback to arXiv, title, citation, or another identifier. Python/domain normalization remains authoritative; browser JavaScript receives the normalized value and does not reimplement a DOI parser.

Clicking Copy DOI only calls the browser Clipboard API. It performs no mutation POST, Workspace reload, Paper Markdown write, or durable-state change. It changes neither workflow status nor `zotero_key` and never invokes `Mark in Zotero` automatically.

Clipboard success temporarily changes the presentation to `Copied`, then restores `Copy DOI`. Clipboard failure shows lightweight `Copy failed` feedback. Do not use an alert or introduce a notification system. These labels are transient browser presentation state only. `Mark in Zotero` remains a separate, user-directed manual confirmation after downstream import.

### 33.6 Web export removal and CLI preservation

Remove the Workspace `Zotero export` panel. Web UI no longer offers `Load export` or the export textarea workflow. The implementation may remove Web-only export fragments, routes, and context glue that are no longer needed.

Preserve CLI `export-kept` and `kept_export.py` with their existing batch DOI/arXiv/MANUAL behavior (§19, §20.7). Per-Paper Copy DOI is a Web convenience and does not replace or redesign that batch export. No Zotero API, authentication, automatic import, or inferred `in_zotero` state is added.

### 33.7 State and persistence boundary

No new durable state is introduced. Existing responsibilities remain:

- `WorkspaceSnapshot.issues`: read-only issues from the current disk-derived workspace load;
- `WorkspacePaper.external_ids`: existing Paper identifiers used for Python-normalized DOI presentation;
- `RunCoordinator`: current process-local execution/snapshot ownership;
- `RunResult`: existing outcome, Paper result summary, issues, diagnostics, coverage, and Provider-state usage.

`Copied` / `Copy failed` are ephemeral browser presentation only. Do not add diagnostics history, workspace-health history, run history, frontend domain persistence, or browser localStorage domain state. No diagnostic data is added to `last-run.json`, Provider-state, Paper/Author Markdown, or monitor YAML; their schemas and existing ownership remain unchanged. The existing observational coverage snapshot and reconstructible Provider-state DB retain their current narrow roles.

### 33.8 Explicit exclusions and delivery boundary

The unaffected MVP and historical release exclusions remain in force. v0.4.6 excludes:

- Creator Representation / collective authorship, `Author.kind`, and Author repair;
- Provider-state schema v3;
- Run history, diagnostics persistence, and workspace-health history;
- issue acknowledge/dismiss and repair actions;
- Provider inspector / debug bundle;
- Zotero API, authentication, automatic import, and PDF retrieval;
- Copy arXiv, Copy citation, and batch Copy DOI;
- CLI `export-kept` redesign;
- workflow status model changes;
- unrelated retrieval, canonicalization, Provider-state, or Author behavior changes;
- a notification system or frontend domain storage for this cleanup.

The initial A0 contract-alignment task changed only `SPEC.md`. It did not implement A1/A2/A3, change Python/HTML/JS/CSS/tests/README/lockfiles, alter package version or Provider User-Agent, create commits/tags/releases, or push. It did not read/modify/delete/stage the local untracked `monitor.yaml`, `src/.obsidian/`, or `workspace/` objects.

The subsequent bounded A1–A3 implementation and independent reviews are complete, including the fixed and re-reviewed A2 `START_FAILED` lifecycle finding. §23.19 remains the acceptance reference and distinguishes implementation-environment tests, independent automated/browser evidence, and final release validation; the final independent integration audit and final release validation passed. Release preparation, the release transaction, and closeout are complete (§24.14), without changing this behavior contract. v0.4.6 is released.

---

## 34. v0.4.7 Journals Organization & Bulk Import Contract

This is the implemented authoritative v0.4.7 behavior contract. A0–A7 implementation and all independent stage reviews are complete, and `V0_4_7_FINAL_INTEGRATION_AUDIT` passed with APPROVED status. §23.20 records demonstrated acceptance and its verification provenance, including passing final release validation. Release preparation, the release transaction, and closeout are complete (§24.15). v0.4.7 is implemented and released, with package metadata `0.4.7`, and is the current/latest released and completed baseline.

### 34.1 Authority and supersede boundary

§§23.19, 24.14, and 33 retain the completed v0.4.6 acceptance, release, and behavior history. Earlier version-specific contracts and exclusions remain historical facts. For v0.4.7, §34 supersedes only conflicting clauses concerning:

- Journal configuration/storage without Groups or restricted to a two-column Journal table; §§5, 12, and 25.6 retain their configuration ownership and ordered flat runtime boundary, with the specific compatibility rules in §§34.2–34.3;
- Paper schema remaining unchanged in prior versions, including §§31.6, 32.5, 32.7, and 33.7; §§13–14 gain only the optional managed `journal_issns` attribution described in §34.7, without adding Group names or changing Author Markdown or monitor YAML schemas;
- flat Workspace presentation and view-wide ordering in §§25.4 and 25.8; §34.8 adds sections inside the existing workflow views and retains the existing ordering precedence within each section;
- Settings Journals presentation where it conflicts with Group organization, import preview/Apply, and desktop scrolling in §§34.4–34.5.

The Candidate Eligibility verdicts, source validation, and fallback rules in §§32.2–32.3 remain authoritative. §34.6 adds retention of the configured Journal attribution established at that boundary, not a new eligibility rule. No historical text is rewritten to imply that v0.4.7 behavior existed in a released version. Unaffected contracts, including §33 Advanced & Diagnostics and Copy DOI, remain in force.

### 34.2 Journal Group state and ordering

`JournalConfig` gains optional `group`; the runtime/configuration boundary remains an ordered flat `tuple[JournalConfig, ...]`. Each Journal belongs to at most one Group; `group=None` means Ungrouped. Nested Groups and multi-Group membership are unsupported.

Group membership and order belong only to the current Journal configuration. Group order is derived from the order of Journals in that configuration, by first occurrence of each Group; no independent `position` field or Group identity store is introduced. Group reordering changes that configuration order. An empty Group has no durable state: a draft create operation may precede assignment, but an empty Group is not persisted independently of Journals.

Settings supports Group create, rename, delete, Up / Down reorder, and Journal assignment. Deleting a Group sets its member Journals to Ungrouped and never deletes those Journals. The first version uses Up / Down controls, without drag-and-drop. Group names are configuration/presentation data and must never be written to Paper Markdown, including as tags.

### 34.3 Journal Markdown storage compatibility

The Journal storage parser accepts both supported table headers in the Journals section of Literature Monitor `list.md`:

```text
Journal | ISSN/EISSN
Journal | ISSN/EISSN | Group
```

The two-column form supplies `group=None`; an empty Group cell in the three-column form also means Ungrouped. Only the Journals section is discovery/import input; the Conferences section remains excluded.

Storage shape follows these compatibility rules:

- A legacy two-column file stays two-column while all Journals are Ungrouped.
- Once Groups are actually used, a successful Save writes canonical three-column storage.
- A file already using three columns stays three-column even when all Journals later become Ungrouped.
- Application upgrade and opening Settings do not rewrite Journal files.
- An unrelated Settings Save must not migrate a legacy ungrouped two-column file to three columns.

Column shape is a storage-adapter concern; it does not replace or nest the flat Journal domain boundary. Existing deterministic Journal validation remains shared: non-empty Journal names, valid ISSN/EISSN checksums, globally unique configured ISSNs, and existing name-conflict rules. The storage adapter additionally validates that Group values can be represented safely in a Markdown table without corrupting rows/cells or losing their value. No new Journal UUID, database, or eager format migration is introduced.

### 34.4 Bulk import parsing, preview and Apply semantics

Bulk import supports UTF-8 CSV, TSV, pasted delimited tables, and Literature Monitor `list.md`. Parsing recognizes only these supported formats and the `Journal`, `ISSN/EISSN`, and optional `Group` column headers in §34.3. It must not infer Journals from arbitrary text or invent missing metadata. Normalization uses the existing deterministic Journal validation/identity rules; Group values also obey §34.3 storage validation. Import never calls OpenAlex, Crossref, or any other Provider and has no AI importer, metadata-completion step, or XLSX dependency.

The sequence is fixed:

```text
parse
→ normalize
→ preview
→ Apply to current unsaved Settings draft
→ existing Validate
→ existing Save
```

Preview compares the normalized import against the current unsaved Settings draft, not a substituted saved configuration. Merge is the default mode. Replace must be explicitly selected and preview which current Journals would be removed; no implicit replacement is permitted.

Preview distinguishes at least:

| Preview category | Observable meaning |
| --- | --- |
| Add | A new Journal would enter the draft. |
| no-op duplicate | The imported row makes no effective change. |
| ISSN merge | New ISSNs would join a same-name existing Journal. |
| Group move | An existing Journal would receive an explicitly different non-empty Group. |
| Remove | A current Journal would be removed; Replace only. |
| conflict | Journal identity/name evidence disagrees. |
| invalid row | The row cannot satisfy supported parsing or validation rules. |

Any invalid row or identity/name conflict blocks the entire Apply. The draft remains unchanged on a blocked Apply; valid rows must not be partially applied. Conflict checks cover both the imported rows and their relationship to the current draft.

Merge semantics are:

- New ISSNs may merge into an existing Journal with the same name under the existing deterministic name normalization.
- An ISSN already assigned to another configured Journal is a conflict; the importer cannot silently transfer it.
- The same ISSN paired with different Journal names is a conflict, including within one import.
- An imported blank Group on an existing Journal preserves its existing Group.
- An explicitly different non-empty imported Group previews and applies a Group move.

Replace proposes replacing the current draft Journal list with the normalized imported list and previews its removals; it remains subject to the same whole-Apply invalid-row and identity/name-conflict gate. Removing a Journal from a draft/configuration does not delete its historical Papers.

Import itself never writes a Journal file. Successful Apply changes only the current browser's unsaved Settings draft and marks it dirty. It retains the draft's existing `monitor_revision` and `journal_revision`; it must not refresh them to bypass a concurrent edit. Existing Validate / Save remains the only path for persisting that draft. Save validates the complete draft and checks both original revisions against current disk contents, with the same revision-conflict and partial-save behavior in §25.6. No second persistence path or transaction framework is added.

### 34.5 Settings Journal organization and scrolling

Settings presents the Group operations in §34.2 and the parse/preview/Apply flow in §34.4 as edits to the existing draft. Organization and import do not create independent saved configuration or bypass normal dirty state, Validate, Save, or revision checks.

On desktop, the Journals list uses a viewport-bounded scroll area. Validate / Save are outside that area and remain accessible even with a large Journal list. Settings Validate fragment replacement restores the Journals scroll position so validation feedback does not discard the user's list context. On mobile, Journals use normal document flow without nested Journals scrolling.

§33.3 Advanced & Diagnostics remains read-only, initially collapsed, and outside the Settings form. Its saved-configuration authority and exclusion from dirty state, Validate, and Save are unchanged. Existing Web security and shared configuration validation remain applicable.

### 34.6 Configured venue attribution

Candidate Eligibility is the sole authority for Provider record → configured Journal attribution. Once that boundary confirms the configured Journal, it passes that Journal's configured ISSN identity into provider-neutral evidence. Provider raw ISSNs and OpenAlex Source ISSNs may support the existing eligibility/source checks, but must not be copied directly into Paper `journal_issns` or treated as configured attribution independently of Candidate Eligibility.

The attribution fields are:

```python
ProviderWorkEvidence.monitor_journal_issns: tuple[str, ...] = ()  # transient
CanonicalPaper.journal_issns: tuple[str, ...] = ()
```

`CanonicalMetadata` does not gain `journal_issns`. Attribution is separate from bibliographic Journal display metadata and is not a Group name.

Multiple snapshots with the same provider / record ID may come from different execution contexts and carry different configured Journal attributions. Canonical retrieval normalization must union their `monitor_journal_issns` even when `_choose_evidence()` chooses only one snapshot for representative evidence. Representative choice must not discard other confirmed attribution. A canonical work may accumulate multiple configured Journal attributions; `CanonicalPaper.journal_issns` retains their union.

Neither field participates in evidence clustering, canonical identity/UUID, representative selection, version selection, searchable projection/keyword matching, duplicate matching, workflow status, or metadata-conflict diagnostics. Differences in execution-context attribution are not bibliographic metadata conflicts. Attribution accompanies the existing pipeline without changing its discovery, eligibility, consolidation, or filtering decisions.

`monitor_journal_issns` remains transient current-context data, including when Provider records are reused under normal live revision validation. It is not added to Provider-state serialization, semantic hashes, cache records, or revision identities. Historical Provider state cannot establish current configured venue attribution on its own. Runtime Provider resolution otherwise remains unchanged.

### 34.7 Durable Paper journal_issns

Paper managed frontmatter gains optional `journal_issns: list[str]`, containing valid configured ISSN identities supplied by §34.6. A new materialized Paper writes valid attribution when available; old Papers without the field remain legal and require no eager migration.

For an existing matched Paper:

- Non-empty current incoming attribution replaces durable `journal_issns` with that current attribution.
- Empty current incoming attribution preserves durable `journal_issns` rather than erasing it.

The union requirement in §34.6 applies within the current canonical work; it does not turn this replace rule into indefinite union with historical durable attribution.

`PaperMarkdownState` exposes venue-attribution parsing state so consumers distinguish missing/empty legacy attribution, valid non-empty attribution, and malformed attribution. A wrong field shape or invalid ISSN value makes attribution unavailable/damaged. Missing or invalid attribution must never enter UUID, external-ID, version, or provider-source identity.

Paper matching remains UUID → external ID → version → provider source → existing title-author fallback, with the existing conservative matching constraints. `journal_issns` never participates in duplicate matching.

Malformed `journal_issns` alone does not make a Paper unsafe or update-blocked, exclude it from its workflow view, or prevent Keep / Reject / In Zotero. Workspace presents it as Unmapped journals without creating an issue solely for that field. Other existing safety/identity/status failures retain their normal handling. Normal subsequent materialization may repair malformed attribution when valid non-empty incoming attribution is available, under the same safe write and ownership rules.

Materialization and decisions preserve human notes, unknown/custom frontmatter, workflow status, and `zotero_key` under their existing contracts. Decisions remain status-only writes; venue parsing does not authorize them to rewrite attribution.

### 34.8 Workspace Group projection

Inbox / Kept / Rejected / In Zotero remain the only top-level workflow views, derived from Paper Markdown status. Grouping is presentation inside each view, not another workflow/status system or a second row of Group tabs.

Each active view displays only sections containing Papers, in this order:

1. saved Journal Groups, in current Journal configuration order (§34.2);
2. Ungrouped;
3. Unmapped journals.

Within each section, preserve the existing `discovered_at DESC → publication_date DESC → title ASC` precedence. Grouping does not alter view membership, duplicate Papers into multiple sections, or change decision semantics.

Mapping uses only the current saved `JournalConfig` list, not an unsaved Settings draft:

| Paper attribution | Mapping behavior |
| --- | --- |
| Valid non-empty `journal_issns` | Match the supplied ISSNs against current configured Journal ISSNs. Exactly one matching Journal maps to that Journal's current Group, or Ungrouped if `group=None`. Zero or multiple matching Journals map to Unmapped journals, without Journal display-name fallback. |
| Missing or empty `journal_issns` | Conservative normalized Journal-name equality may map a legacy Paper only when exactly one current Journal matches; otherwise use Unmapped journals. |
| Malformed `journal_issns` | Attribution is unavailable/damaged; use Unmapped journals without name fallback or a Workspace issue solely for attribution damage. |

All matching ISSNs belonging to the same configured Journal count as one Journal match. Attribution spanning multiple current Journals is ambiguous even if those Journals share a Group; no arbitrary selection or multi-Group duplication is allowed.

After successful Settings Save, Group rename, Group reorder, and Journal assignment immediately affect Workspace presentation using the newly saved configuration. These operations do not rewrite Paper Markdown. Removing a configured Journal may make historical Papers Unmapped, but never modifies or deletes them. A Provider Journal display-name change cannot affect a Paper that still maps uniquely by valid `journal_issns`.

The existing desktop/mobile master-detail, disk reload, active selection, decision-failure handling, and scroll-neighborhood preservation in §32.6 remain applicable to these sectioned views. Group projection introduces no durable membership cache or frontend domain store.

### 34.9 Preservation requirements

The following behavior remains unchanged outside the explicitly scoped additions above:

- OpenAlex / Crossref are never called during Journal import; runtime Provider resolution changes only to retain confirmed configured venue attribution.
- Inbox / Kept / Rejected / In Zotero workflow semantics, status transitions, decision safety, and `zotero_key` behavior remain unchanged.
- `CanonicalPaper` UUID/canonical identity, evidence clustering/deduplication, representative selection, and preferred-version behavior remain unchanged.
- Paper human notes and unknown/custom frontmatter remain preserved; Group names are never Paper state. Author Markdown and monitor YAML schemas remain unchanged.
- Copy DOI and CLI `export-kept` retain their existing eligibility, output, and non-mutating behavior.
- Provider-state/cache/revision behavior, schemas, semantic hashes, coverage, diagnostics, and historical snapshot compatibility remain unchanged apart from transient attribution flow.
- Existing Settings validation, both revision checks, two-file write order, and truthful partial-save reporting remain unchanged. Upgrade/opening Settings never performs Journal format migration.

There is no repository-wide eager Paper migration, Journal UUID/database, Paper Group tag storage, persistent workflow DB, or new external integration. Paper Markdown remains the sole durable user-facing workflow state; current Journal configuration owns organization, and Workspace sections are derived presentation.

### 34.10 Explicit exclusions and delivery boundary

The unaffected MVP and released-contract exclusions remain in force. v0.4.7 excludes nested Groups, multi-Group membership, independent Group positions or durable empty Groups, drag-and-drop, arbitrary text inference, AI import, Provider-backed import/metadata completion, XLSX dependencies, and partial Apply. It adds no global keyword-first discovery, conference monitoring, publisher scraping, PDF acquisition, custom Zotero ingestion, persistent execution/workflow state, or external integration.

The completed A0 specification-alignment task modified only `SPEC.md`; subsequent A1–A7 implementation and independent reviews are complete. The final independent integration audit and final release validation passed, with actual verification levels recorded in §23.20. Release preparation updated only package/User-Agent version identity, matching test expectations, and current-state documentation without changing functionality, dependencies, `list.md`, user state, or performing a Paper migration. Its independently reviewed release commit was created; annotated tag creation, main/tag pushes, GitHub Release, and closeout are complete (§24.15), without changing this behavior contract. v0.4.7 is released.

---

## 35. v0.5.0 Institutional PDF Acquisition to Existing Zotero Item Contract

This is the implemented authoritative behavior contract for the first version of v0.5.0 Institutional PDF Acquisition to Existing Zotero Item. A0–A7 implementation and independent stage reviews are complete; the final audit and final release validation passed. §23.21 records demonstrated acceptance and its network-independent/live provenance. Release preparation, the release transaction, and closeout are complete (§24.16). v0.5.0 is implemented and released, with package metadata `0.5.0`, and is the current/latest released and completed baseline.

### 35.1 Authority and limited supersede boundary

Historical version-specific text, acceptance evidence, and release closeouts remain facts about their corresponding versions. For v0.5.0 only, §35 supersedes conflicting PDF acquisition, Zotero API/write/attachment, authentication, and external-integration exclusions in §§2, 4.2, 18.5, 19.2, 22.2, 22.4, 25.10, 33.6, 33.8, 34.9, and 34.10 solely to permit the user-triggered acquisition action defined here for an existing Zotero My Library bibliographic item. This does not add PDF acquisition to Obsidian Review Inbox, kept-paper export, candidate discovery, or the production monitor pipeline. Automatic bibliographic ingestion and automatic workflow decisions remain excluded.

The existing ownership and persistence clauses in §§12–14, 25.5, 33.7, and 34.7 gain only the independently verified null/missing `zotero_key` linkage in §35.4 and the machine-local browser/credential state in §§35.6–35.7. No historical section is rewritten to imply that an earlier release had this capability. All unaffected contracts remain in force, including journal-whitelist-first discovery, OpenAlex/Crossref retrieval, Candidate Eligibility, local keyword filtering, conservative canonicalization, UUIDs, Journal Groups/attribution, human decisions/notes, CLI export, Copy DOI, Settings, Web security, Run progress, Provider state, and coverage.

Literature Monitor owns acquisition orchestration. Zotero owns the bibliographic item and PDF attachment/storage. The institutional resolver owns holdings and routing authority. No provider routing or holdings authority is transferred into Literature Monitor.

### 35.2 Product eligibility and workflow preservation

The only domain workflow statuses remain:

```text
candidate
rejected
kept
in_zotero
```

`Mark in Zotero` remains a user-directed, status-only `kept → in_zotero` decision under §25.5. It neither creates a Zotero item nor starts acquisition; acquisition must never invoke it or infer a workflow transition from Zotero/PDF state. `in_zotero` continues to express the user's confirmation and does not require a PDF attachment.

`Copy DOI` retains all §33.5 behavior, including its kept-only eligibility, Python normalization, clipboard presentation, and non-mutating semantics. CLI `export-kept` remains unchanged.

The Paper detail action `Add PDF to Zotero` is shown only when `status == in_zotero` and the Paper DOI is accepted by the existing Python/domain `normalize_doi()`. Missing or invalid DOI means no action at all, including no disabled fallback. Candidate, kept, and rejected Papers never expose acquisition. There is no title / author / year / fuzzy matching, arXiv fallback, or browser-side replacement DOI parser for this action or Zotero identity.

An execution request identifies the Paper by its workspace-local UUID. The server must independently locate and reread the Paper and enforce the same status/normalized-DOI eligibility; a visible button, submitted path, or stale browser value is not authorization to acquire for an ineligible Paper. Revalidate UUID, status, and the attempt's normalized DOI before linkage and immediately before upload, including an authorization retry. A missing or changed Paper identity/eligibility returns conflict and must not authorize a Zotero write.

### 35.3 Verified Zotero identity

The first version supports only Zotero Desktop Local API and My Library, using API version 3 at `http://localhost:23119/api/`, with the My Library prefix `/users/0`. Zotero Web API, OAuth, Group Libraries, direct SQLite access, a Zotero plugin, and automatic bibliographic item creation are excluded. Creating the PDF child attachment under a verified existing parent is the only permitted Zotero content write for this feature; parent bibliographic metadata must not be edited.

Use the Paper normalized DOI as the identity authority. Prefer an existing `zotero_key`, but retrieve its actual My Library item and verify that it is a non-attachment bibliographic item whose DOI, normalized by the same `normalize_doi()`, exactly equals the Paper normalized DOI. A key alone is never proof of identity. A missing key, stale/not-found key, or wrong-DOI key falls back to paginated enumeration of non-attachment items at `/api/users/0/items`. Matching must inspect item DOI metadata, not a search-result title or arbitrary text. Incomplete pagination, an unavailable API, or an unreadable response cannot establish uniqueness and must fail without mutation.

Count exact normalized DOI matches across the complete fallback enumeration:

| Exact matches | Required result |
| --- | --- |
| 0 | Failure: no existing Zotero item; no Zotero or Paper mutation. |
| 1 | This is the verified My Library parent item. |
| >1 | Failure: require the user to resolve the Zotero duplicate first; no automatic selection and no Zotero or Paper mutation. |

The verified parent and current Zotero Server-ID belong to the current attempt. A stale/wrong key is a recoverable identity lookup condition only if fallback establishes one exact match; otherwise report the corresponding identity failure. No fuzzy identity or automatic bibliographic creation may rescue a failed lookup.

### 35.4 Independent safe Paper linkage

The only newly permitted durable Paper mutation is:

```text
zotero_key: null/missing → verified Zotero My Library item key
```

This is an independent linkage write, not a workflow decision. It must not change `status`, bibliographic metadata, `journal_issns`, versions, provenance, human notes, unknown/custom frontmatter, or any other Paper content. An existing non-null key, including a stale/wrong-DOI key, is preserved rather than overwritten; fallback may use the newly verified parent within the current attempt without persisting a replacement key. A completed safe linkage may remain if a later acquisition step fails; this does not imply PDF success or change workflow state. Normal materialization reruns must continue to preserve `zotero_key`.

Linkage requires safety equivalent to the existing §25.5 decision boundary:

1. Locate the current Paper by UUID within the workspace; reject missing/ambiguous UUIDs and unsafe/non-regular targets.
2. Read the complete current file, parse it again, and verify UUID, updateability, `status == in_zotero`, and the same normalized DOI used to verify the Zotero parent.
3. Verify that the durable field is actually null or missing; parser failure or malformed non-null data must not be treated as an empty field.
4. Prepare a change limited to `zotero_key`, preserving all other frontmatter and Paper body content.
5. Compare the complete current content against the read used to prepare the write before atomic replacement.

A Paper that disappears or changes UUID, status, DOI, linkage, or any content during this locate/read/parse/compare-before-replace sequence returns conflict. Do not recreate it, silently reread-and-overwrite new content, or continue to Zotero upload after a linkage conflict. This feature must not weaken decision safety or turn decision actions into general metadata writers.

### 35.5 Existing PDF short circuit

After verifying the Zotero parent, query its child attachments before any institutional acquisition. Zotero is the sole durable source of truth for PDF attachment existence. If a PDF attachment already exists, return `PDF already attached`, perform no download, and create no duplicate upload; Paper workflow state remains unchanged. Failure to read child attachment state is a failure, not proof that the item has no PDF.

Every new attempt, including a retry after restart or uncertain upload completion, repeats parent verification and the child-attachment check. Recheck attachments before upload so a PDF attached while institutional resolution was in progress also short-circuits. Do not add `pdf_status`, `pdf_path`, acquisition history, or an attachment-existence cache to Paper Markdown, monitor YAML, workspace state, or any other durable application state.

### 35.6 Local API write authorization and credentials

Use the official `POST /api/local/authorize` flow. Read the current `Zotero-Server-ID` and supply it with every Local API write; a missing ID must not be guessed. Identity verification, authorization, credential selection, and the target parent must belong to the same current Server-ID. Detecting a Server-ID change invalidates the attempt's credential/linkage assumptions: do not reuse an old credential or upload to a previously verified parent, and require fresh linkage verification against the current Zotero instance before retry.

Non-remembered local API keys exist only in the current process and must never be persisted to a file. A successful write is allowed to consume/invalidate such a key; subsequent writes must not assume it remains authorized.

A remembered key is a credential and must be stored in the OS credential store, associated with the Zotero Server-ID. Python `keyring` is the recommended implementation option, not an A0 dependency addition. The key must never be stored in the project, workspace, monitor YAML, ordinary app-data files, logs, or Paper state. An unavailable secure credential store must not cause fallback to plaintext persistence. A Server-ID change must never select the previous instance's credential.

Authorization denial is a normal action failure. If a write using remembered authorization returns 401, the current attempt may request fresh authorization once and retry the failed write after rechecking Server-ID, parent identity, and attachments. Further denial/401 ends the attempt; no prompt loop is permitted. If `/api/local/authorize` returns 429, honor its retry boundary, including `Retry-After` when supplied: no repeated authorization request or new authorization window before retry is permitted. A 429 must not trigger automatic prompt repetition; expose a retryable failure and allow a new user-triggered attempt only after the boundary. Do not invent an unbounded authorization retry policy.

### 35.7 Institutional browser and resolver authority

Use a Literature Monitor-dedicated persistent real-browser profile. Browser profile/session is machine-local application state outside both workspace and project; `platformdirs` is recommended for selecting the application-data path. This profile is not the user's ordinary browser profile and is not an acquisition-history store. Do not store institution passwords. Institution login/session cookies may remain only in that protected browser profile and must not be copied into logs, Paper state, or project configuration.

CAPTCHA, MFA, Cloudflare, and institutional verification must be completed manually by the user in the persistent browser. No bypass is permitted. Expired authentication must produce an actionable authentication result or `WAITING_FOR_INSTITUTION_AUTH` presentation, with continuation only after the required human step succeeds.

The supplied confirmed Xiamen University Full Text Finder configuration is:

```text
UI OPID: 45yels
customer/profile: s1215021.main.ftf
```

Public LinkIQ guest access must not be a dependency. Obtain resolver candidates through `POST https://resolver.ebsco.com/api/links` within the authenticated resolver browser context, using the Paper normalized DOI and this institutional context. Only `FullText` and `SmartLinks` categories are eligible for automatic acquisition.

The following are forbidden automatic acquisition sources, even if reachable in the browser:

- `/api/get-research-tool-links`;
- generic page anchors used as a substitute for resolver candidates;
- chat integrations;
- `SearchEngines`, `Other`, and `DocumentDelivery` categories;
- Sci-Hub / research-tool links.

Try eligible candidates in resolver-provided precedence/rank order; filtering must preserve that order rather than re-sort by provider or link label. Do not write special branches for names such as `EBSCOhost SmartLinks`, or journal→provider, publisher→provider, or DOI-prefix routing tables. Candidate failures must not discard remaining eligible candidates. The resolver response remains the holdings/routing authority; no holdings cache or general institution/resolver plugin framework is introduced.

### 35.8 Generic PDF discovery and byte validation

Within each eligible resolver candidate, use this discovery priority:

1. The response/navigation itself already provides a PDF.
2. A browser download event supplies the candidate file.
3. `citation_pdf_url` metadata supplies the PDF URL.
4. An explicit PDF / `application/pdf` link supplies the PDF URL.

These are generic discovery methods within the selected candidate, not permission to harvest arbitrary page anchors as resolver sources. The first version adds no publisher-specific adapters. An HTML landing page may provide the explicit metadata/link above, but HTML/login/error bytes themselves must never be uploaded as a PDF.

Success requires validation of the actual downloaded bytes, with `%PDF` mandatory. Content-Type, a `.pdf` filename, link text, a browser navigation result, or a successful HTTP status cannot independently prove success. Candidate PDF discovery or byte validation failure must continue to the next eligible resolver candidate in order when one remains; exhaustion returns an acquisition failure. User verification requirements retain the human boundary in §35.7 rather than becoming automated bypass steps.

### 35.9 Temporary files, upload provenance, and sensitive data

PDF temporary files must be outside the workspace and project. Successful Zotero upload deletes the temporary file; failure performs best-effort cleanup of attempt-owned temporary files. Literature Monitor does not retain a PDF store. An interrupted process may lose its attempt and leave an orphan temporary file; such a file is never authoritative acquisition state or proof of success, and restart must recheck Zotero before retry (§35.5).

Upload only validated PDF bytes as a child attachment of the verified existing My Library parent. Prefer the canonical DOI URL (`https://doi.org/<normalized DOI>`) for stable attachment source metadata. Do not use temporary authenticated resolver URLs as durable source metadata.

Auth tokens, cookies, Zotero write keys, signed resolver URLs, proxy-session URLs, and resolver URL components containing proxy/session/auth/signature information must never enter Paper Markdown, provenance, logs, monitor YAML, workspace state, or durable acquisition state. The dedicated browser profile and OS credential store are only the narrowly authorized session/credential locations in §§35.6–35.7. Sanitize action errors and server logs, including exception messages, so failure reporting cannot leak these values.

### 35.10 Independent single-active execution

Introduce a separate, narrow `AcquisitionCoordinator` for one Paper acquisition at a time within the running application. The active slot is shared across Web requests; a concurrent start must return a busy action result without starting a second attempt, queueing it, or creating another worker acquisition. Release the slot on success, normal failure, or unexpected failure so later user-triggered attempts remain possible.

Acquisition state and current-attempt presentation are process-local and may be lost on restart. Do not extend or reuse `RunCoordinator` to execute acquisition, alter monitor Run state, create a generic job framework, batch/queue acquisition, or introduce a durable acquisition database/history. Existing `RunCoordinator` responsibilities and production pipeline behavior remain unchanged.

Suggested presentation stages are:

```text
LOCATING_ZOTERO
CHECKING_ATTACHMENT
RESOLVING
WAITING_FOR_INSTITUTION_AUTH
DISCOVERING_PDF
ATTACHING
SUCCEEDED
FAILED
```

These describe the current acquisition attempt only; they are not domain workflow statuses and must not be written into Paper state. `PDF already attached` is a terminal successful no-op action result. Starting/executing acquisition, waiting for human verification, browser operations, Local API calls, and PDF upload must not block the FastAPI Web server; the existing UI and current-attempt polling must remain responsive.

### 35.11 Failure and restart semantics

Acquisition failures are action results. They are neither `WorkspaceIssue` entries nor monitor Run diagnostics, and must not alter workflow status on success or failure. Report useful failure/retry/human-action information without introducing durable history, timing telemetry, or sensitive values. A previously completed independent safe linkage is the only possible Paper change (§35.4).

| Condition | Required action/state behavior |
| --- | --- |
| Paper is no longer `in_zotero`, or DOI is missing/invalid | Refuse acquisition after current-disk validation; no workflow transition or Zotero write. |
| Paper disappears or changes before linkage replacement | Conflict; do not overwrite/recreate the Paper or upload after conflict. |
| Zotero unavailable / Local API disabled, or identity/attachments cannot be fully read | Failure with a useful local-API recovery action; no speculative parent choice or upload. |
| Missing / stale / wrong-DOI `zotero_key` | Complete exact-DOI fallback; preserve a non-null key and never trust it without verification. |
| Zero / multiple exact DOI matches | Failure without Zotero/Paper mutation; multiple matches require manual Zotero duplicate resolution. |
| Server-ID changes | Invalidate current verification/credential assumptions; no old-key reuse or write under the old linkage. |
| Authorization denied | Normal failure; no prompt loop. |
| Remembered authorization revoked / write returns 401 | At most one fresh authorization and guarded write retry under §35.6; further failure terminates. |
| Authorization endpoint returns 429 | Respect the retry boundary; no repeated authorization window. |
| Existing PDF attachment | `PDF already attached`; no download or duplicate upload. |
| Zero eligible `FullText` / `SmartLinks` candidates | Failure; do not substitute forbidden sources. |
| Expired institutional authentication / human verification | Show required manual action in the dedicated browser; no password storage or verification bypass. |
| Candidate HTML/login/error response or invalid PDF bytes | Do not upload those bytes; continue ordered remaining candidates or fail on exhaustion. |
| Zotero upload failure | Failure, best-effort temporary cleanup, and no workflow change; a retry begins by checking actual Zotero attachment state. |
| Restart during attempt | Current attempt is lost and must not be presented as completed; a new user-triggered attempt re-verifies Zotero identity and attachments before any acquisition. |

### 35.12 Web action and presentation scope

The local Web adapter supports the eligible `in_zotero` detail action, one-Paper-at-a-time execution, and HTMX/polling presentation of the current attempt. Polling reads process-local snapshots and never starts acquisition or performs a workflow/Paper mutation. No current-attempt history, frontend domain persistence, batch controls, acquisition ETA, or timing telemetry is introduced.

Acquisition starts and linkage writes retain the existing loopback/Host/CSRF boundaries in §25.9 and UUID/current-disk validation in §§35.2–35.4. A stale detail or concurrent start cannot bypass status eligibility or the single-active coordinator. Useful acquisition failures may offer a manual `Open Full Text Finder` escape hatch for the institutional resolver, without embedding an authenticated/signed/proxy-session URL in the Web response or stored provenance. Opening it does not authorize automatic source substitution, imply success, or change workflow state.

### 35.13 Explicit exclusions

v0.5.0 does not include:

- automatic Zotero bibliographic item creation or parent metadata updates;
- automatic `kept → in_zotero` or any PDF workflow status;
- acquisition for candidate / kept / rejected Papers or invalid/missing DOI;
- batch acquisition / queue or a generic job framework;
- Zotero Group Libraries, Zotero Web API/OAuth, direct SQLite, or a Zotero plugin;
- workspace/project PDF storage or durable acquisition database/history;
- publisher-specific adapters, journal/publisher/DOI-prefix routing tables, or holdings cache;
- acquisition ETA/timing telemetry;
- OCR, PDF parsing/reader/preview/annotation;
- Sci-Hub/research-tool acquisition, chat acquisition, or forbidden resolver categories;
- paywall/CAPTCHA/MFA/Cloudflare/institutional-verification bypass;
- institution password storage or plaintext remembered credentials;
- a general institution/resolver plugin framework;
- unrelated discovery, Provider, canonicalization, Journal organization, decision, or Obsidian changes.

### 35.14 Demonstrated acceptance and release closeout

The implemented contract retains the following observable behavior and preservation requirements. §23.21 records demonstrated acceptance and distinguishes network-independent tests from previously established live resolver evidence and the live operations not yet demonstrated:

| Acceptance area | Required evidence |
| --- | --- |
| Workflow and existing actions | Exactly the original four workflow statuses; existing Keep / Reject / Mark in Zotero, Copy DOI, and CLI export retain their behavior. Acquisition success/failure never changes `status`. |
| Detail eligibility and server validation | Only `in_zotero` + DOI accepted by `normalize_doi()` exposes acquisition; missing/invalid DOI has no disabled fallback. Crafted/stale requests for other statuses are rejected. |
| Exact Zotero identity | A valid key is retrieved and DOI-verified; missing/stale/wrong keys use complete paginated non-attachment My Library enumeration. Zero/one/multiple exact-match cases and incomplete enumeration are covered; duplicates never cause arbitrary selection. |
| Independent linkage safety | Only null/missing `zotero_key` may become the verified key. Notes, custom frontmatter, all other content/status, and non-null keys are preserved. Locate/read/parse/compare-write conflicts, disappearance, UUID/status/DOI/content changes, malformed data, and materialization-rerun preservation are covered. |
| Existing attachments and safe retry | Child attachment checks precede institutional acquisition and upload; `PDF already attached` short-circuits download/duplicate upload. Restart and uncertain upload retry recheck Zotero instead of relying on process state. |
| Server-ID and authorization | API version 3, current Server-ID on every write, identity/credential isolation across Server-ID mismatch, official authorization, denial, non-remembered key lifecycle, revoked remembered authorization/401 with one fresh authorization, and 429 retry/prompt boundaries are covered. |
| Secure remembered credentials | Remembered credentials use the OS credential store, isolated by Server-ID; unavailable storage has no plaintext fallback. No key enters project/workspace/YAML/app-data/logs. |
| Independent coordinator and responsiveness | A narrow `AcquisitionCoordinator` allows exactly one active Paper, rejects concurrent starts without queueing, releases its slot on all terminal paths, and does not reuse/alter `RunCoordinator`. FastAPI and HTMX polling remain responsive during browser/API/upload/human-wait work. |
| Authenticated institutional resolution | Real authenticated XMU browser evidence uses OPID `45yels`, profile `s1215021.main.ftf`, and `POST https://resolver.ebsco.com/api/links`; guest LinkIQ is not a dependency. Expired login/human verification follows the manual boundary. |
| Resolver filtering and ordering | Only `FullText` / `SmartLinks` are attempted in resolver precedence/rank order. Forbidden endpoints/categories, generic anchors as resolver sources, research-tool links, and link-name/provider routing branches are absent. Zero candidates and continuation after candidate failure are covered. |
| Generic discovery and PDF validation | Response/navigation → download event → `citation_pdf_url` → explicit PDF / `application/pdf` link priority is exercised without publisher adapters. Actual bytes require `%PDF`; misleading Content-Type and HTML/login/error/invalid bytes are rejected and remaining candidates continue in order. |
| Temporary isolation and upload | Attempt temporary PDFs are outside project/workspace, uploaded only under the verified existing parent, deleted after successful upload, and best-effort cleaned after failure. Upload failure and attachment recheck before retry are covered. |
| Provenance and sensitive-data preservation | Stable attachment source metadata prefers canonical DOI URL. Auth tokens, cookies, keys, signed/proxy-session/authenticated resolver URLs do not leak through Paper content, provenance, Web errors, exception logs, or project/workspace state. |
| Action-result separation | Failures/busy/authentication/conflict results are useful current-attempt presentation, not `WorkspaceIssue`, Run diagnostics, or durable state. No `pdf_status`, `pdf_path`, history, ETA, or new workflow transition appears. |
| Web recovery | Current-attempt polling is observational; a useful failure can offer manual `Open Full Text Finder` without sensitive URL leakage or implied acquisition success. |

Network-independent behavior tests cover the relevant failure and concurrency cases. Mocks alone do not demonstrate authenticated institutional browser resolution, actual Local API authorization, or PDF upload/storage. The actual live validation levels are reported separately in §23.21: authenticated XMU resolver access and ordered candidates were observed; successful PDF retrieval, actual Zotero authorization/child creation/upload/registration, and full live end-to-end acquisition remain undemonstrated.

A0 originally changed only `SPEC.md`. A1–A7 implementation and independent reviews are complete; the final audit and final release validation passed. The independently reviewed release-preparation commit changed only package/Provider User-Agent identity, matching tests, current-state documentation, and the known §35.6 credential comment reference; it did not modify `list.md`. Afterward, the user-approved grouped `list.md` was committed unchanged as `413b2505b8cab4d74bd5e6e43f4c9ec5aee6815c` (`Update grouped journal list`) and included in the v0.5.0 release tag. It is repository configuration, not an acquisition runtime change or Python package payload. `monitor.yaml`, `src/.obsidian/`, and `workspace/` remained untouched and local-only. The release-preparation commit, annotated tag creation, main/tag pushes, GitHub Release, and documentation closeout are complete (§24.16), without changing this behavior contract. Historical v0.4.7 text remains accurate. v0.5.0 is released.

---

## 36. v0.5.1 Version-Qualified Institutional PDF Acquisition via Normal Chrome Contract

This records the authoritative v0.5.1 normal-Chrome PDF acquisition contract and its post-release maintenance baseline; §37 now governs v0.5.2 development within its explicit supersede boundary. v0.5.1 remains the latest released and completed baseline with package metadata `0.5.1`; current main additionally includes post-release PDF guard/cancellation ownership hardening in `0b69d4b`, preserving its user-visible acquisition semantics. The updated §36 wording and that maintenance commit are outside the v0.5.1 tag/artifacts. A0–A9 implementation and independent final-audit Fix 1–3 review are complete; §36.14 separates the historical implementation acceptance/final audit, scoped live evidence and post-release engineering verification. Release preparation, final clean-export release validation, annotated tag creation, main/tag pushes, the GitHub Release, authoritative release-asset digest verification and initial documentation closeout are complete (§24.17); those release facts are unchanged.

The completed scoped live audit exercised normal Chrome, publisher→XMU, SmartLinks/Research, task-bound delayed blob download attribution, private staging, `%PDF` validation, PUBLISHED qualification, and Settings-owned remembered authorization. An explicitly authorized fresh task successfully created, uploaded, and registered child `LJ6UV83V` under verified parent `ZHIST6EG`; a fresh complete Local API inspection confirmed a real non-empty regular PDF file whose bytes equal the user Chrome download. The earlier incomplete child remains untouched and is not counted as actual PDF success. The separate human-verification attempt and its environmental boundary are recorded in §36.14. Further real writes or re-registration require explicit user authorization.

### 36.1 Authority and limited supersede boundary

For v0.5.1, §36 supersedes only conflicting current acquisition, `Mark in Zotero`, browser, authorization, resolver, staging, cancellation, and acquisition-coordinator behavior in §35 and earlier clauses. In particular, this replaces status-only Mark in Zotero (§§25.5, 34.7, 35.2), stale/wrong non-null key fallback (§§35.3–35.4), repeated acquisition Paper rereads/full-state writer guards (§§35.2, 35.4, 35.6), dedicated persistent Playwright/Chrome acquisition (§35.7), private EBSCO `/api/links` resolution (§35.7), temporary-download ownership (§35.9), and lifetime thread-oriented acquisition waiting (§35.10). §36.5 adds only monotonic version-kind enrichment for an existing normalized version identity; it changes neither version identity nor canonicalization policy.

§35 and all earlier version-specific acceptance and release/closeout text remain historically accurate facts about those versions, including §§23.21 and 24.16. They do not assert that v0.5.1 behavior existed in v0.5.0. For released v0.5.1, a conflicting historical statement does not override §36. The narrow existing-parent PDF-write permission in §35.1 continues under the replacement contract here; broader integration exclusions remain applicable.

All unrelated discovery, retrieval, Provider, canonicalization, Journal, Group, Obsidian, Run, workflow, and export contracts remain unchanged. Keep/Reject retain their existing safe status-only decisions. Copy DOI, CLI `export-kept`, Markdown ownership/preservation, Web loopback/Host/CSRF security, and the monitor pipeline remain unchanged except for the expressly permitted Mark/linkage and version-kind enrichment below. Acquisition remains a separate user action, outside the discovery pipeline and Obsidian presentation.

### 36.2 Durable workflow and verified Mark in Zotero

The durable workflow statuses remain exactly:

```text
candidate
rejected
kept
in_zotero
```

`Mark in Zotero` remains a user-triggered `kept → in_zotero` transition after downstream import. It must perform a complete exact normalized DOI lookup in Zotero Desktop **My Library**, even when the Paper already has a key. Use the existing domain DOI normalization for both Paper and actual Zotero item DOI metadata; no title/fuzzy match or arbitrary search-text match establishes identity. Uniqueness requires complete readable enumeration with the stable library revision, Server-ID, pagination, and count guarantees retained from v0.5.0.

Locate and safely read the requested Paper by workspace-local UUID using the decision safety boundary. Missing/ambiguous/duplicate UUIDs, unreadable candidates that prevent safe location, unsafe symlinks/non-regular files, malformed/non-updateable Paper state, invalid DOI, or a status other than `kept` cause zero Paper mutation. Zero DOI matches, multiple matches, unavailable/unreadable/incomplete Zotero state, or an unverifiable Server-ID also cause zero Paper mutation. Never select an arbitrary duplicate or create a bibliographic item to rescue the action.

Exactly one verified non-attachment bibliographic DOI match permits **one compare-and-replace Paper update**, atomically writing both:

```yaml
status: in_zotero
zotero_key: <verified key>
```

A missing/null key or an existing key equal to the verified key is compatible. A non-null wrong, stale, malformed, or conflicting key causes failure with zero Paper mutation; DOI lookup must not silently replace it. Preserve every unrelated frontmatter field and the complete body, including notes and unknown fields. Compare the complete current file against the safe read used to prepare this single update; concurrent modification or disappearance returns conflict, with neither field partially written and no reread-and-overwrite retry. This action performs no Zotero content write and does not start Add PDF. `in_zotero` remains the user's confirmed linkage state and does not require a PDF.

### 36.3 One safe action read and immutable acquisition identity

`Add PDF to Zotero` remains available only for an `in_zotero` Paper with a valid normalized DOI and a qualified preferred version. A request supplies the Paper UUID, not an authoritative filesystem path or browser-supplied metadata. Start from **one safe fail-closed Paper action read**: locate uniquely within the workspace and read/parse a complete authoritative snapshot. Duplicate UUIDs, unreadable candidate files that prevent establishing unique safe identity, unsafe symlinks/non-regular files, malformed requested Paper, invalid DOI, or invalid preferred-version structure refuse acquisition before browser work or Zotero content mutation.

`preferred_version` must resolve to exactly one normalized entry in `versions`. Missing, zero-match, ambiguous/multiple-match, or structurally invalid preferred/version data fails closed. Retain the complete resolved `PaperVersion` (kind, source, identifier, URL, date, and any other version evidence carried by that entry), rather than reducing qualification to the Paper DOI. Use the existing normalized version identity rules; DOI equality alone does not establish the expected version.

Freeze one immutable process-local task from this action snapshot and the Zotero preflight/linkage result. It contains at least:

| Task field | Required meaning |
| --- | --- |
| `task_id` | Unique identity of this attempt. |
| `paper_id` | Workspace-local Paper UUID from the safe action read. |
| normalized DOI | Frozen domain-normalized Paper DOI. |
| `zotero_key` | Verified current My Library parent key, after any permitted legacy linkage. |
| complete target `PaperVersion` | Exactly the resolved preferred entry, with its full evidence. |
| derived acquisition class | Qualification derived from that entry, not DOI alone. |
| bound Zotero Server-ID | Instance verified by preflight; cannot switch during this task. |
| stage | Current process-local stage. |
| handoff token/digest | Current one-time handoff capability or its validation digest, never durable state. |
| task-owned browser tab identity | Bound on claim; unset before handoff is claimed. |
| browser evidence | Observations attributable to that task/tab. |
| staged PDF artifact | Validated application-owned artifact, unset until staging succeeds. |
| terminal result | Unset until completion, failure, or cancellation. |

The qualified identity and expected version remain immutable. Event transitions replace immutable task snapshots to advance stage/evidence/artifact/result fields for the **same** `task_id`; they do not select a new Paper or version. Unavailable fields begin unset rather than being invented. A legacy linkage compare checks the original action bytes (§36.4); this is not a second semantic Paper action read. After construction, browser continuations, authorization waits/replay, and the writer do not relocate, reparse, or rebuild full Paper state. The final commit check is Zotero-only (§36.6). Later Paper edits do not silently retarget the frozen attempt; a different target requires a new user-triggered action.

`JOURNAL_FINAL` and `JOURNAL_ONLINE` (durable `journal_final` / `journal_online`) derive the `PUBLISHED` acquisition class. Accepted-manuscript and preprint targets, when explicitly selected by the existing preferred-version policy, retain their own version qualification. `UNKNOWN` / `unknown` is not automatically eligible and cannot be treated as published based on DOI presence. There is no automatic lower-version fallback if the frozen target is unobtainable.

### 36.4 Legacy missing-key compatibility

An existing `in_zotero` Paper with a genuinely missing/null `zotero_key` may receive one exact-DOI compatibility linkage during its first Add PDF attempt. Complete My Library enumeration must establish exactly one DOI-matched bibliographic parent under the current Server-ID. Unreadable/incomplete state, zero/multiple matches, or malformed/non-null key data does not authorize linkage.

Prepare only `zotero_key: null/missing → verified key` from the original safe action read and perform one complete-content compare-and-replace before freezing the linked task. Preserve status, all unrelated frontmatter, versions, and the body. A comparison/location conflict aborts the attempt without overwrite or Zotero content mutation. A successfully completed linkage may remain if acquisition later fails or is cancelled; it records identity, not PDF success. Once the key is durable, subsequent attempts verify it rather than repeating compatibility linkage.

A non-null conflicting, stale/not-found, wrong-DOI, or malformed key is never auto-repaired or bypassed through DOI fallback. Every acquisition, including a legacy-linked attempt, verifies the **current key plus actual parent DOI** against Zotero before browser work. Manual repair is required for a non-null conflict.

### 36.5 Durable version-kind lifecycle

Evidence for the same existing normalized version identity may monotonically enrich its durable kind, for example `JOURNAL_ONLINE → JOURNAL_FINAL`. The existing preferred-version priority supplies the kind ordering; lower incoming kind evidence must not downgrade an already durable higher kind. Unknown/weaker evidence must not erase a known higher kind. Preserve other discovered versions, provenance, and user-managed Paper content, and keep reruns idempotent.

This does not redefine normalized version identity, merge distinct versions merely because they share a DOI, invent an unseen version, or add migration-only durable state. A task already frozen from an earlier action keeps its expected version; later kind enrichment applies to future actions.

### 36.6 Zotero preflight and final commit check

Retain Zotero Desktop Local API, API version 3, `/users/0` My Library, complete readable enumeration, stable `Last-Modified-Version`, pagination/count validation, and actual-file attachment inspection. Verify the current parent key, actual normalized parent DOI, and Server-ID, then inspect its **actual current PDF attachments before opening Chrome**, including the handoff page. An actual PDF produces terminal successful `PDF_ALREADY_ATTACHED` without Chrome navigation, download, or duplicate upload. Metadata-only incomplete attachment records are not actual PDF success. Failed/incomplete inspection is not evidence of absence.

Immediately before the first Zotero content mutation, repeat a **Zotero-only final commit check** against the task's frozen key/DOI/Server-ID and current actual PDF attachments. Do not substitute a full Paper-state writer guard or repeated Paper action read. A parent identity mismatch, instance change, or unreadable/incomplete Zotero state prevents mutation. A PDF added by another actor during browser/authentication work returns `PDF_ALREADY_ATTACHED` and suppresses upload. A Server-ID change ends the attempt; a new explicit attempt must bind the new instance.

A qualified staged artifact that passes §36.11 and the commit-time artifact freshness check may enter `ZoteroWriteClient` for local object/file preparation. The writer's application guard owns the final Zotero commit check immediately before the first content POST; no Zotero content POST may precede that check. Before the first content mutation, both current staged artifact validity and current Zotero final state must pass. Each later mutation boundary still checks current Zotero identity and attachments, including the attempt's own child and competing actual PDFs. Recheck staged artifact freshness while the writer is in `NO_CONFIRMED_MUTATION` or `CHILD_CREATED`, when subsequent mutation still needs its bytes; after confirmed byte upload reaches `BYTES_UPLOADED`, registration does not read or revalidate the local artifact. Retain current Server-ID on every write, writer write-token/revision guards, and the existing same-Server-ID HTTP 412 operation-failure distinction. Parent bibliographic metadata is never changed. A retry after restart or uncertain completion starts with fresh current Zotero preflight; browser/download leftovers cannot prove success.

### 36.7 Settings-owned Zotero write authorization

Initial write authorization belongs in **Settings → Advanced & Diagnostics → Zotero integration**, using the official Local API authorization flow. Add PDF uses authorization established there; missing authorization offers that Settings action before mutation. Mark in Zotero needs readable verified identity, not PDF-write authorization. Authorization and browser institutional authentication remain separate.

Offer the existing one-time **Allow** and remembered authorization modes. A one-time Allow credential lives only in current process memory, bound to the current Server-ID, and must not be assumed reusable after a write consumes/invalidates it. A remembered credential lives only in the OS credential store, partitioned by Zotero Server-ID. No plaintext fallback is permitted; unavailable secure storage must not persist the credential elsewhere. No credential enters project/workspace files, monitor YAML, Paper state, app-data files, logs, browser handoff, or companion state.

A **confirmed remembered-credential 401 before mutation** may cause at most one fresh authorization and at most one guarded replay. Before replay, repeat the Zotero-only final commit check in §36.6; a newly existing PDF suppresses replay, and an identity/Server-ID mismatch aborts it. Further denial/401 terminates without a prompt loop. This exception does not move initial authorization back into acquisition or permit replay after a partial/uncertain mutation. Retain process-wide authorization 429/`Retry-After` limits across workspace changes, Server-ID isolation, write-token guards, partial-write handling, and mutation-uncertainty reporting.

### 36.8 Normal Chrome and browser companion ownership

Use the user's **normal Chrome profile/session**. Do not start a dedicated Playwright browser or profile. Cloudflare, CAPTCHA, institution login, and MFA stay in normal Chrome and require the user's action, with continuation on the same task/tab after the human step. No bypass is permitted. Literature Monitor must not request, inspect, copy, export, or persist browser cookies or store institution passwords.

The browser companion is a standalone repository artifact **outside the Python package**. It owns only browser handoff, navigation, observation, and download coordination. Version policy, task qualification, Zotero identity, Zotero authorization, and all Zotero writes remain application responsibilities. The companion receives no Zotero write credential. Initial distribution may use documented unpacked-extension installation; extension internals and other packaging choices are not frozen here.

Zotero Connector remains independent and may supply its normal proxy behavior in Chrome. Literature Monitor must not call, fork, modify, or control it.

### 36.9 Secure one-time browser handoff

Open a loopback handoff page under the existing local Web security boundary. A high-entropy one-time task secret appears **only in the URL fragment**, never in the path or query. The fragment is excluded from the handoff-page HTTP request; the secret must not enter HTTP request/access logs, error logs, or durable state. Never forward the secret to DOI/publisher/resolver destinations. The authorized claim exchange must not log the capability.

Each task may be claimed once, binding it to one task-owned Chrome tab. Successful claim consumes/invalidates the initial secret; subsequent events must be authenticated and attributable to the claimed task/tab without reusing the handoff secret. Repeated claims and unrelated tabs, navigations, or downloads cannot advance, supply evidence/artifacts to, or complete the task. Terminal completion, cancellation, and application restart invalidate any outstanding secret and task/event authority. Late events for invalidated tasks are ignored.

If the companion is absent, leave the handoff page visible with setup guidance and retain the current task until claim, cancellation, or termination. Do not fall back to Playwright. Endpoint layout, event transport, extension internals, and capability representation beyond these security properties remain implementation choices.

### 36.10 Publisher-first navigation and narrow XMU fallback

Attempt direct DOI/publisher access first, in the task-owned normal Chrome tab. Only when a `PUBLISHED` target is unobtainable there may acquisition fall back to **Xiamen University Full Text Finder**, using the existing XMU institutional context (`45yels`, `s1215021.main.ftf`). Login/verification waits do not themselves prove that the target is unobtainable; offer human continuation on the same task.

Replace private `/api/links` interception with a narrow adapter for normal-browser **Full Text / SmartLink** choices. The visible institutional resolver remains holdings/routing authority. Preserve resolver ordering for unambiguous eligible choices; ambiguous choices permit explicit user selection in the same browser task. Do not infer an authoritative candidate from arbitrary anchors or substitute research-tool, SearchEngines, Other, or DocumentDelivery links. Candidate failure may continue eligible choices without changing the expected version.

No private EBSCO `/api/links` interception/call is part of v0.5.1 acquisition. This is neither general EBSCO integration nor generic publisher scraping, a publisher routing table, DOI-prefix routing, or a holdings cache. The resolver cannot authorize an arbitrary lower-version fallback, and an exhausted path returns a useful current-task failure.

### 36.11 Download ownership, private staging, and version validation

`chrome.downloads.download()` is an optimization. User-initiated publisher/PDF-viewer downloads are a supported first-class fallback. Both paths must be attributable to the claimed task-owned tab and converge on the same validation/staging boundary; unrelated downloads cannot be adopted. Literature Monitor never deletes user-owned Chrome downloads, including after success, failure, or cancellation.

Treat browser-reported local paths as untrusted. Verify task/download ownership and safe source identity when reading; reject path substitution, symlinks, non-regular files, unavailable/incomplete downloads, and invalid or oversized artifacts. Apply a finite enforced size limit before accepting bytes; the numeric limit remains an implementation choice. Copy accepted bytes into an **application-owned private staging artifact outside project/workspace**, with race-safe source handling. Validate the staged bytes themselves, including mandatory `%PDF`; filenames, Content-Type, browser success, and HTTP 200 are insufficient. HTML/login/error responses or invalid/non-PDF artifacts must not reach the writer. Staging must not create a second durable PDF store or acquisition history.

Before any Zotero content mutation, validate the staged artifact and its task-bound browser evidence against the **complete frozen expected PaperVersion and derived acquisition class**. Identity and byte-format validation alone do not prove version qualification. Missing, ambiguous, or conflicting evidence must fail closed rather than attach an unqualified file. A `PUBLISHED` target with explicit accepted-manuscript/AAM or preprint evidence must fail version validation, even if DOI matches and bytes are a PDF. Do not silently attach a lower version or expose an `Attach anyway` control.

Acceptance example: for DOI `10.5705/ss.202024.0215`, if the frozen target is `JOURNAL_FINAL`/`PUBLISHED` and the browser/PDF evidence explicitly identifies the available file as an accepted author manuscript, reject that file before Zotero mutation. The same rule applies to any explicit-AAM case; this example establishes neither live retrieval evidence nor a DOI-specific production exception.

Only the staged, byte-validated, version-qualified artifact may be passed to `ZoteroWriteClient`. Use canonical DOI source metadata and preserve the existing sensitive-URL/provenance restrictions. Clean up task-owned staging after terminal outcomes, with best-effort cleanup after failure/interruption; never delete the user's source download. Orphan staging/download files after restart are not recoverable task authority.

### 36.12 Event-driven coordinator, cancellation, and truthful outcomes

Retain exactly **one active acquisition per process**, independent of the monitor `RunCoordinator`; a concurrent start returns busy without queueing. No queue, batch, durable acquisition history, or generic background-job framework is introduced. Browser events advance immutable snapshots of the same process-local task. Browser/user/authentication waits must not hold a worker thread blocked for the lifetime of the attempt. Bounded API/file work may use ordinary execution resources while the Web UI and observational polling remain responsive.

Stages may describe preflight, handoff, browser/authentication wait, staging/validation, `ATTACHING`, and terminal results. They are current-task presentation only, never durable workflow statuses. An application restart loses the task and invalidates its capabilities; do not reconstruct it from browser tabs, downloads, staging leftovers, or persisted history. All terminal paths release the active slot and invalidate task event authority.

**Cancel is available only before the first Zotero content mutation.** Entering `ATTACHING` / the mutation gate disables Cancel. Gate entry and Cancel compete atomically under the same coordinator lock: Cancel winning returns `ACCEPTED`, prevents all Zotero writes, cleans task-owned staging/capabilities, and ignores late browser/download events; gate entry winning fixes cancellation authority for that attempt, and subsequent Cancel returns `TOO_LATE`, even before the first content POST starts. The gate is the cancellation authority boundary, not a Zotero content mutation. Downstream application/writer code does not poll coordinator cancellation state again; after-gate Cancel cannot imply rollback or stop the authorized mutation flow. Artifact qualification establishes initial validity; the coordinator retains the same qualified artifact across authorization waits without repeating freshness validation, and `AcquisitionService.commit()` owns commit-time freshness before any content POST (§36.6). A successful earlier legacy Paper linkage may remain (§36.4). Cancellation leaves user downloads intact.

After mutation begins, retain truthful `CHILD_CREATED` / `BYTES_UPLOADED` partial-failure reporting and existing mutation-uncertainty guarantees. Do not report rollback, clean failure with zero writes, or automatically replay when child creation/upload/registration may have occurred. A later explicit retry must inspect actual Zotero state first. Failures and human-action results remain acquisition action results, separate from Workspace issues and Run diagnostics, without workflow/status changes or durable PDF status/path/history.

### 36.13 Replacement cleanup and explicit exclusions

After the replacement path is implemented and complete, remove the obsolete Playwright acquisition/resolver implementation instead of retaining it as a fallback. Remove `playwright` and `platformdirs` dependencies only if no remaining production consumer exists. A0 specifies this cleanup only; it does not remove code/dependencies, add a companion, alter package version, or implement later work.

v0.5.1 excludes:

- automatic bibliographic Zotero item creation, parent metadata updates, Group Libraries, Zotero Web API/OAuth, direct SQLite, and a Zotero plugin;
- Zotero Connector fork/modification/calls/control;
- cookie requests/inspection/copying/export/persistence, institution password storage, and plaintext remembered credentials;
- Cloudflare/CAPTCHA/MFA/institutional-verification or paywall bypass;
- generic publisher scraping, publisher/DOI-prefix routing tables, general EBSCO integration, and private `/api/links` acquisition;
- arbitrary lower-version fallback, `Attach anyway`, and a PDF reader/version manager;
- durable PDF status/path/history, new workflow states, queue/batch/history, and a generic background-job framework;
- automatic Mark in Zotero, acquisition for candidate/kept/rejected Papers, and candidate-pipeline PDF acquisition;
- unrelated Provider/discovery/retrieval/canonicalization/Journal/Group/Obsidian/Run/export refactors.

### 36.14 Required acceptance and verification boundary

The following remain **required acceptance criteria** for v0.5.1. The verification boundary and current acceptance record below distinguish executable tests from scoped live evidence; they do not assert that these behaviors existed in v0.5.0:

| Area | Required observable acceptance |
| --- | --- |
| Atomic Mark in Zotero | One complete exact-DOI My Library match atomically writes `status` and verified `zotero_key` in one compare-and-replace; unrelated frontmatter/body remain intact. Zero/multiple matches, unreadable/incomplete Zotero, invalid DOI, unsafe Paper/UUID/location, and wrong status produce zero Paper writes and no Zotero content writes. |
| Conflicting keys and compare-write | Non-null wrong/stale/conflicting/malformed keys remain unchanged for both Mark and Add PDF. Concurrent Paper edits/disappearance during Mark or compatibility linkage return conflict with no partial write, overwrite, or continued upload. |
| Legacy compatibility | One exact-DOI missing/null-key linkage is allowed for an existing `in_zotero` Paper; it preserves all other content. Later attempts verify the durable key plus DOI. No non-null fallback repair occurs. |
| Version lifecycle | Same normalized identity upgrades `JOURNAL_ONLINE → JOURNAL_FINAL`; lower/unknown incoming evidence cannot downgrade it. Other versions/identity rules and human content remain intact, with no migration-only state. |
| One-read frozen task | One safe Paper action read supplies immutable UUID/DOI/full target version; duplicate UUIDs, unreadable candidates, unsafe files, malformed Paper/DOI/versions fail closed. Continuations/final writer checks do not reread/reparse full Paper state or retarget after edits. |
| Preferred-version qualification | Preferred resolves to exactly one normalized `versions` entry; zero/ambiguous/malformed matches fail. Both journal kinds derive `PUBLISHED`; UNKNOWN is ineligible and no lower-version fallback occurs. |
| Zotero preflight | Current key/actual DOI/Server-ID and actual complete attachment state are verified before Chrome. Existing actual PDF returns successful `PDF_ALREADY_ATTACHED` with no handoff/Chrome/download/upload; metadata-only partial children do not count. |
| Handoff security | A high-entropy fragment-only secret is absent from the handoff-page HTTP request, HTTP request/access logs, and destination URLs. Exactly one claim binds one task/tab and consumes the secret; duplicate claims and unrelated tab/download events cannot affect the task. Terminal/cancel/restart invalidates capability/event authority. |
| Normal Chrome and missing companion | The user's normal Chrome session is used; no dedicated Playwright browser/profile starts. Missing companion leaves setup guidance visible without fallback. Companion owns only handoff/navigation/observation/download and receives no Zotero write credential. |
| Navigation/resolver | Direct DOI/publisher precedes XMU fallback, available only for an unobtainable PUBLISHED target. Narrow normal-browser Full Text/SmartLink choices support explicit ambiguous-choice selection; no private `/api/links` interception/call, generic scraping, routing tables, or Connector control occurs. |
| Human verification continuation | Whenever a genuine Cloudflare/CAPTCHA/institution-login/MFA or equivalent challenge naturally appears, the user completes it manually in normal Chrome; successful continuation keeps the same acquisition task, claimed tab, and frozen expected version. No bypass or cookie inspection/copying occurs. This product behavior remains required; live exercise follows the conditional boundary below. |
| Downloads and staging | Automatic download optimization and user-initiated publisher/PDF-viewer downloads converge on task-bound private staging. User-owned source downloads survive success/failure/cancel. Unrelated downloads, path substitution, symlinks/non-regular files, incomplete/oversized/non-PDF artifacts are rejected; staged `%PDF` validation precedes writer access. |
| Published versus lower version | Valid PDF bytes with matching DOI and explicit AAM/preprint evidence fail for a PUBLISHED task before mutation, including the §36.11 example. Ambiguous qualification fails closed; no `Attach anyway` or DOI-specific production rule exists. |
| Final duplicate suppression | A PDF appearing during browser/auth waits suppresses upload at the Zotero-only final check. Parent/DOI/Server-ID mismatch and incomplete inspection prevent writes without a full-state Paper guard. |
| Settings authorization modes | Initial authorization is in Settings Advanced & Diagnostics Zotero integration. One-time Allow is process-only; remembered credentials use only the OS store partitioned by Server-ID; unavailable storage has no plaintext fallback. Instance/429 limits remain enforced. |
| Guarded 401 replay | Confirmed remembered-credential 401 before mutation permits one fresh authorization and one replay after repeated Zotero-only final check; newly attached PDF suppresses replay. Further denial/401, changed instance/identity, and partial/uncertain mutation cannot trigger replay. |
| Cancellation and writer truth | Accepted pre-gate Cancel prevents all Zotero writes; racing gate entry has a single truthful outcome. ATTACHING disables Cancel; after-gate cancellation cannot imply rollback. Server-ID/write-token/412 guards, CHILD_CREATED/BYTES_UPLOADED partial failures, and mutation uncertainty remain truthful. |
| Coordinator and persistence | Only one active attempt per process; events advance the same task while browser/user waits hold no lifetime-blocked worker. UI/polling remain responsive; terminal paths free the slot. Restart loses the task without reconstruction, durable PDF state, queue, batch, or history. |
| Replacement cleanup | Completed replacement removes obsolete Playwright acquisition/resolver code, with no fallback. `playwright`/`platformdirs` are removed when unused by remaining production consumers and retained only when such a consumer exists. |
| Regression and live verification | Run the full automated suite for implementation, including meaningful network-independent tests of identity/version/staging/cancellation/concurrency/writer failures and existing workflow preservation. Separately perform and report explicitly scoped live normal-Chrome companion/handoff, publisher-first/XMU fallback, human-verification continuation subject to the conditional boundary below, download/staging, Settings Local API authorization, and actual Zotero child creation/upload/registration verification. Report each exercised path, environment, outcome, and unverified boundary; mocks or historical v0.5.0 browser evidence do not establish those live results. |

Live exercise of human-verification continuation is conditional on a genuine challenge naturally appearing during a scoped run. If one appears, successful manual completion and continuation on the same task/claimed tab/frozen expected version are required; inability to continue is an acceptance failure. The no-bypass, no-cookie-access, and authentication boundaries in §§36.8–36.10 remain mandatory.

If a scoped real run reaches the relevant institutional browser path without a genuine challenge, record `HUMAN_VERIFICATION_NOT_PRESENT` and report this branch as **not live-exercised**, never as a live pass. Do not manufacture a challenge by clearing cookies, forcing logout, changing credentials, or manipulating session state. External challenge non-occurrence is not by itself a release blocker, provided meaningful network-independent executable tests cover human-required detection/reporting, `WAITING_FOR_INSTITUTION_AUTH` presentation, authenticated same-task/tab event authority, waits without a lifetime-blocked worker, later browser observations continuing under that authority, rejection of invalid task/tab events, and cancellation before the mutation gate. This conditional live boundary does not relax the required product behavior.

**Historical v0.5.1 implementation acceptance record (2026-10-02):** A0–A9 implementation and independent final-audit Fix 1–3 review are complete. Independent automated validation passed: shipped worker simulation 174 cases, shipped content adapters 101 cases, full pytest with Node runtime 3356 passed / 0 skipped / 2 existing warnings, `uv lock --check`, `git diff --check`, and offline build. Scoped normal-Chrome/XMU/download/staging/qualification and actual Zotero registration verification are complete: parent `ZHIST6EG`, registered child `LJ6UV83V`, fresh complete Local API `has_pdf=true` with that child in `pdf_file_keys`, an actual non-empty regular PDF file, and registered bytes equal to the user Chrome download. Add PDF did not mutate Paper content, and live verification did not mutate source files.

The separate human-verification run used Paper `38f516c6-885d-4882-8c00-bd2c207c078d`, DOI `10.1287/ijoc.2024.0765.cd`, and verified parent `YLF7MWNU`. Normal Mark in Zotero changed only the owned status/key fields. Fresh task `20fa032b-0ada-40ad-b392-ca74a9b1b66c`, claimed tab `522718311`, and frozen journal-online/PUBLISHED version survived publisher-first navigation and explicit XMU → SmartLinks → EBSCO Research navigation. No genuine login/CAPTCHA/Cloudflare/MFA challenge naturally appeared: `HUMAN_VERIFICATION_NOT_PRESENT`; real human-step continuation was not live-exercised. No challenge was manufactured, cookies inspected/copied, sessions manipulated, or verification bypass used. Normal cancellation occurred before mutation; parent metadata remained unchanged, no child was created, and actual PDF remained absent. Together with the executable human-wait/continuation coverage, this satisfies the conditional acceptance boundary. Implementation acceptance and final audit are complete. Prepared-tree validation and the separate final clean-export release validation passed; release preparation, annotated tag creation, main/tag pushes, the GitHub Release, and authoritative asset digest verification are complete (§24.17). v0.5.1 is the latest released and completed baseline, with package metadata `0.5.1`; the initial documentation closeout at `125bfb7` followed the release tag. These release checks do not change the scoped live human-verification boundary above.

**Current-main post-release maintenance record (2026-10-02):** Implementation commit `0b69d4be6fe9f783d37b051d088e67f6e3483c05` (`Simplify PDF acquisition guard and cancellation ownership`) completed independently reviewed A1–A3 guard/cancellation ownership hardening after the v0.5.1 release. Add PDF workflow, result and recovery semantics remain unchanged. Each normal content POST has one application guard; a confirmed remembered-credential 401 before mutation still checks before fresh authorization and again before replay. The first content POST's guard owns the final Zotero commit check. Service commit owns commit-time artifact freshness; later freshness checks apply only at `NO_CONFIRMED_MUTATION` / `CHILD_CREATED`, while bytes are still needed. Registration at `BYTES_UPLOADED` proceeds even if staging was subsequently modified/deleted. Cancel and gate entry compete atomically under the coordinator lock; gate entry fixes cancellation authority, with no downstream `mutation_allowed` callback/polling. Parent/Server-ID checks, actual-PDF duplicate suppression, own-child validation, write tokens, credential lifecycle, truthful partial stages and mutation uncertainty remain enforced.

Independent post-release automated verification recorded **3376 passed, 13 skipped, 2 existing dependency warnings, 0 failed**, plus passing `git diff --check`. That verification environment lacked Node.js; the 13 skips were existing executable browser/DOM integration cases. These are the independent maintenance validation results, separate from the historical Node-enabled release validation above. Network-independent synthetic normal Add PDF → full Zotero upload reached `REGISTERED`, using real reader/staging/qualification/service/writer code with mock HTTP transports: Local API GET 18, POST 4, `verify_parent_key()` 5, `inspect_attachments()` 5, child `/file` probes 3, `StagedPdf.validate()` 5 and application guards 4. The four guards precede create (`NO_CONFIRMED_MUTATION`), preparation and byte upload (both `CHILD_CREATED`), and registration (`BYTES_UPLOADED`). These measured counts are engineering verification/regression evidence, not stable product-domain or API contracts. This maintenance performed no new live acquisition/Zotero mutation and does not extend the historical live evidence or human-verification boundary. No package version, tag, GitHub Release or published wheel/sdist was changed; this commit is not part of the v0.5.1 tag/artifacts.

The browser companion's standalone installation/use documentation, replacement cleanup, and regression tests are present in the released v0.5.1 source. The initial A0 task changed only this specification. Package names, class names beyond the already required writer boundary, endpoint layout, transport, extension internals, staging size limit, and other nonessential implementation choices remain unfrozen.

---

## 37. v0.5.2 DOI-first Single-Manifestation Simplification — Current Contract

### 37.1 Authority, supersede boundary and historical preservation

§37 is the authoritative current contract for v0.5.2 and the source of truth for A1–A5 implementation. It supersedes conflicting earlier normative descriptions and acceptance requirements only for:

- canonical work identity, DOI grouping, no-DOI admission, the version/domain model and publication-date selection (§§8–11 and 23.7, with related pipeline/model references in §§2–4, 17 and 23.8);
- Paper version fields, preferred-version selection, managed Versions content and support for older Paper schemas (§§9, 13–14, 17, 19.1, 23.11 and version-preservation clauses in §34);
- retained OpenAlex location/version hydration, OpenAlex version state and Provider-state v1/v2 migration (§§6.1, 6.4, 20.4, 30.2, 30.4, 30.7–30.8, 31.6 and 32.5);
- acquisition target-version/class fields, browser version transport/evidence, version-based PDF qualification and the PUBLISHED-only XMU fallback gate (§§36.3, 36.5, 36.8, 36.10–36.11 and corresponding version-related acceptance rows in §36.14).

An earlier requirement to preserve discovered versions or preferred versions does not require those fields in v0.5.2. An earlier allowance for DOI-less canonical Papers, relation-based cross-DOI grouping or title-based work identity does not override §37.2. Unaffected journal-whitelist/date discovery, local FTS5 filtering, Provider failure isolation, metadata equivalence/conflict reporting, author materialization, workflow, human Markdown ownership, Web security, export and acquisition safety remain applicable.

All v0.5.0/v0.5.1 release contracts, demonstrated acceptance, live verification, release closeouts and post-release maintenance evidence remain historical facts, particularly §§23.21–23.22, 24.16–24.17, 35 and 36.14. Historical version terminology and the v0.5.1 explicit-AAM rejection example remain valid records of those baselines; they do not prescribe v0.5.2 behavior or establish v0.5.2 acceptance. v0.5.1 remains a historical released baseline; v0.5.2 is the latest released baseline (§24.18). At A0, this contract established requirements without changing runtime code, package version, data, tags or publication state, and without implementation, test, live-verification or release-completion claims for v0.5.2. The later implementation acceptance and final independent integration audit are recorded separately in §37.10.

### 37.2 DOI-first identity and provider evidence

The existing domain-normalized, valid DOI is the **only supported canonical work identity**. The same normalized DOI means the same research work and yields one canonical Paper; provider records for that DOI supply metadata and `MetadataSource`/Sources provenance, not separate manifestations. OpenAlex-only or Crossref-only evidence may establish the DOI work when the preserved journal/date, eligibility and required-metadata rules permit it. No provider gains canonical authority from retrieval order.

The workspace-local UUID remains the stable internal key for an already materialized Paper, its filename, decisions and action references. It does not supply an alternative work-grouping identity or a global work registry. Repeated evidence for the same DOI preserves that Paper's UUID/path and human state. A different DOI must not silently retarget an existing Paper or inherit its decisions.

Different nonempty normalized DOIs **must remain different work components**. Crossref relations, version relations, shared provider IDs, title/author similarity and other manifestation metadata cannot merge them, alias one DOI to another, or select a prime DOI that erases the distinction. A DOI-less record must not form a transitive bridge between DOI components.

Pure no-DOI components do not produce canonical or materialized Papers and are unavailable to Mark in Zotero and Add PDF. Sparse no-DOI evidence may remain valid retrieval evidence under the existing Provider admission/coverage rules; this alone does not make a canonical candidate or an execution error. Such evidence may supplement metadata/provenance only when existing conservative evidence uniquely associates it with **one explicit DOI component**, without conflicting comparable identifiers or ambiguous DOI ownership. Title alone is insufficient. If association is ambiguous or would join distinct DOIs, retain their separation and do not use that record as shared identity authority. No DOI is inferred or fabricated from similarity.

### 37.3 Canonical/domain model without manifestation state

The target Canonical/domain model no longer contains `VersionKind`, `VersionRef`, `PaperVersion`, `CanonicalPaper.versions`, `CanonicalPaper.preferred_version`, `EvidenceVersionRole`, `EvidenceVersionHint` or `ProviderWorkEvidence.version_hints`. It has no persistent journal-final, journal-online, accepted-manuscript or preprint lifecycle, preferred manifestation, or version-kind enrichment.

Remove `EvidenceRelation` if removal of version consumers leaves it without an independent current responsibility; do not retain it as a speculative extension point. Provider-specific raw relations in `CrossrefWorkRecord` may continue to exist as Provider metadata. Their presence does not authorize cross-DOI grouping, a generalized identity layer or reconstruction of the removed version model. Preserve contributing `MetadataSource`/provenance and applicable nonconflicting external identifiers; missing metadata remains missing.

### 37.4 Publication-date selection

`publication_date` is selected for the single DOI work independently of `VersionKind`. Use this descending field priority across its attributable evidence:

```text
published-print → published-online → published → issued → representative publication_date
```

Use the first tier with usable normalized date evidence. Preserve the existing partial-date precision rules: missing month/day components are never invented. Within a tier, prefer more complete evidence; among equally complete candidates, choose the earliest normalized date deterministically, independent of Provider/input order. Duplicate/equivalent representations are one value. Multiple distinct complete dates in the same tier are a real metadata conflict: keep the deterministic selected date and report the conflicting values through existing metadata-conflict/provenance handling. Compatible partial representations are not contradictions, and the intentional distinction between print and online dates is not itself a conflict. Revision, retrieval and Provider update timestamps remain ineligible as publication dates or discovery-window authority.

### 37.5 Paper Markdown and disposable older schemas

The new Paper Markdown schema contains no `versions`, `preferred_version` or system-managed `## Versions` section. It retains the workspace UUID, normalized DOI, canonical bibliographic fields, Sources/provenance and the existing workflow fields. Routine reruns preserve human-controlled `status`, `zotero_key`, unknown/custom frontmatter, human Notes and other human-authored sections under the existing safe-write and idempotency boundaries. Abstracts remain source-derived; the removed Versions section is not replaced with generated content.

Existing pre-v0.5.2 workspace Papers and Provider-state are disposable test data. v0.5.2 provides **no old Paper schema migration, legacy repair or compatibility path**: do not add conversion, version stripping, schema repair or migration-only state to make old Papers usable. Preservation guarantees apply to supported current-schema Papers; they do not require supporting the superseded version schema. Disposal/reset of existing data is not an action authorized by this A0 task.

### 37.6 Production Run and Provider-state schema v3

Production Run no longer executes retained-work OpenAlex location/version hydration, including revision probes or state reuse whose sole purpose is version hints. OpenAlex remains primary journal/date discovery; Crossref remains secondary discovery/bibliographic evidence. Consolidate attributable available metadata before local keyword filtering. Preserve live membership, Crossref revision-validated metadata reuse, Provider-local partial failures, coverage and publication-date boundaries; historical state alone still cannot create candidates.

The new current logical Provider-state schema is **v3**, distinct from v1/v2. It contains only `schema_metadata` and `crossref_records`, retaining the existing normalized Crossref-record/revision/provenance and consumed-metadata hash responsibilities. It contains no OpenAlex version table or version-hint serialization, work membership, Paper/workflow state, diagnostics history or execution state. Current valid v3 updates retain the existing transaction/upsert, no-WAL, branch-completion persistence and no-cross-thread-connection boundaries. Provider-state schema versioning does not bump or migrate the separate `last-run.json` schema.

Do not implement row-preserving Provider-state v1/v2 migration or in-memory conversion for reuse. Old v1/v2 state follows the existing invalid-state boundary in §30.5: report it as incompatible/untrusted and run all-live; only the normal production persistence boundary may safely replace an invalid **regular** DB after constructing valid fresh state, using atomic replacement without deleting the old file first. Do not recover historical rows into v3. Unsafe symlinks/directories/path objects remain protected, valid-state write failure preserves the previous state, and persistence failure does not roll back completed Paper/Author materialization. Diagnostics and non-production commands do not gain Provider-state writes or reset/migration behavior. Legacy `provider-cache.json` remains inert.

### 37.7 DOI-bound Mark in Zotero and acquisition task

Mark in Zotero continues to require a valid normalized DOI, a safe current-schema Paper in `kept`, complete exact-DOI My Library enumeration and one verified bibliographic parent. Its single compare-and-replace update of `status` plus verified `zotero_key` retains §36.2's conflict and human-content protection. No-DOI Papers cannot be marked; no title/fuzzy lookup or automatic parent creation rescues them.

Add PDF continues to require `in_zotero`, a valid normalized DOI and the existing parent/key/actual DOI/Server-ID safety. Start from one safe uniquely located Paper action read and freeze the same task/Paper UUID, normalized DOI, verified Zotero parent key and Server-ID. `AcquisitionTask` no longer contains `target_version` or `acquisition_class`; no `PaperVersion`, preferred-version resolution or version-kind eligibility check is required. Browser continuations and writer checks do not reparse full Paper state or silently retarget the attempt after later edits.

The existing exact-DOI missing/null-key linkage safety in §36.4 applies only to an otherwise valid current-schema Paper, preserving all unrelated fields/body. It is an identity-only linkage, not a migration/repair path for old Paper schemas. Wrong, stale, malformed or conflicting non-null keys still fail closed without auto-repair, overwrite or DOI-lookup bypass. Zotero preflight and the final commit check remain Zotero-only checks against the frozen parent/key/DOI/Server-ID and actual attachment state (§36.6).

### 37.8 Browser, PDF evidence and XMU fallback

The browser **START** plan carries the DOI-bound task/tab navigation authority and starts direct acquisition at `https://doi.org/<normalized-doi>`. It transports no version class, `acquisition_class`, `target_version` or `PaperVersion`. The companion remains responsible only for authenticated task/tab handoff, navigation, observation and downloads; qualification of DOI identity/bytes and all Zotero operations stay application responsibilities.

Browser download evidence contains no `version_labels` or `manifestation`. Preprint, AAM/accepted manuscript, online first and final labels **cannot by themselves reject a PDF**. Neither missing version evidence nor ambiguity between such labels is a qualification failure. Observed DOI evidence, if present, must normalize to the frozen task DOI; a mismatch still fails closed before Zotero mutation. Absence of an observed DOI is not by itself a mismatch or grounds to manufacture one; all existing task attribution, download ownership and byte-validation requirements still apply. This does not add PDF content/version comparison or permission to adopt an unrelated file.

Direct DOI/publisher access precedes the existing DOI-based **XMU Full Text Finder** fallback. Fallback is available only after the publisher path is explicitly exhausted; there is no PUBLISHED-class gate. Login/verification waits alone do not establish exhaustion. Retain the existing institutional context, normal-browser Full Text/SmartLink ordering, explicit selection for ambiguous resolver choices, and exclusion of arbitrary anchors, research-tool/SearchEngines/Other/DocumentDelivery links and private `/api/links` acquisition.

The existing **EBSCO task/tab/record/DOI/explicit-PDF-action/timing attribution** remains mandatory, including task-bound delayed/blob download attribution. A matching label or DOI cannot substitute for that authority. Preserve normal Chrome/user verification, one-time claim/event authentication, invalidation of late/unrelated events and independence from Zotero Connector; no cookie access or verification bypass is introduced.

### 37.9 Preserved write safety, exclusions and implementation acceptance

All acquisition safety unrelated to manifestation qualification continues under §§35–36 and the §36.14 post-release maintenance record: download ownership, preservation of user-owned downloads, application-owned private staging outside project/workspace, finite size limits, staged `%PDF` byte validation, symlink/path-substitution/path-race protection, commit-time artifact freshness, cleanup and rejection of orphan artifacts as task authority. Only a task-attributed, DOI-compatible, byte-validated staged artifact may reach `ZoteroWriteClient`; removing version qualification does not relax these conditions.

Retain actual-file Zotero duplicate suppression before Chrome and at the final commit check, stable readable library enumeration, parent/key/DOI/Server-ID checks, authorization and OS credential-store boundaries, write-token/revision/HTTP 412 guards, guarded pre-mutation remembered-credential 401 replay, truthful partial-write and mutation-uncertainty semantics, and the existing atomic cancellation gate. Freshness checks still apply while bytes are needed (`NO_CONFIRMED_MUTATION` / `CHILD_CREATED`), and confirmed `BYTES_UPLOADED` registration does not revalidate local staging. Keep the independent event-driven single-active acquisition coordinator, responsive UI, restart invalidation and acquisition-result separation from Run/Workspace diagnostics. Parent metadata and human workflow state are not PDF-write targets.

v0.5.2 does not add `Attach anyway`, PDF history, persistent acquisition history, queues/batches, a generalized ResearchWork identity abstraction, PubMed, arXiv-only identity, preprint→publication lifecycle, PDF content/version comparison, automatic bibliographic Zotero ingestion or any other scope expansion excluded by the unaffected contracts.

Implementation acceptance requires meaningful tests demonstrating the changed observable behavior and preserving the applicable workflow/safety coverage. Required cases include same-DOI multi-Provider evidence yielding one Paper with provenance; different-DOI relations/similarity and ambiguous DOI-less bridges remaining separate; pure no-DOI components producing no Paper/action; deterministic date selection with genuine conflict reporting; current-schema reruns preserving human state without version fields; no retained OpenAlex hydration; Crossref-only v3 state with safe all-live handling/replacement of old state; a DOI-only START plan; acceptance of otherwise valid task-bound PDFs regardless of version labels; observed DOI mismatch rejection; and explicitly exhausted publisher access permitting DOI-based XMU fallback without a class gate. Applicable parent/attachment, handoff/EBSCO attribution, staging, freshness, authorization, cancellation and partial-write/uncertainty regressions remain required. These are normative implementation acceptance requirements, established by A0 without executed checks. A1–A5 implementation and the final independent integration audit now satisfy network-independent implementation acceptance at the verified scope recorded in §37.10. Required live browser/Zotero behavior must be verified and reported at its actual scope; historical v0.5.1 live evidence does not establish v0.5.2 live success.

### 37.10 v0.5.2 implementation acceptance and final independent integration audit record

A0–A5 implementation and independent stage reviews are complete. The final independent integration audit of the complete uncommitted tree is complete, with no remaining runtime/behavioral finding. A1–A5 satisfy §37.9's network-independent implementation acceptance at the independently verified scope:

- Targeted integration suite: **1283 passed, 2 skipped**.
- Full pytest: **3207 passed, 13 skipped, 2 existing dependency warnings**.
- `uv lock --check`: **PASS**.
- `git diff --check`: **PASS**.
- Node was unavailable in the reviewer environment; shipped JS runtime/content suites were **not independently executed** by this audit.

No v0.5.2 live browser/Zotero mutation verification is claimed. Historical v0.5.1 live evidence remains historical and does not establish v0.5.2 live success. Required live behavior remains subject to verification and reporting at its actual scope; the automated audit above does not close that boundary.

Implementation commit `f48574869969534daec589e124c6979011b5cbb5` (`Implement v0.5.2 DOI-first workflow`) contains the reviewed implementation and audit closeout. Release-preparation review/commit and the release transaction are complete at release HEAD `d180c18aa853e4483a7d110d39bdfb8aca6860f8` (§24.18). v0.5.2 is RELEASED and the latest released baseline, with package metadata and Provider User-Agent identities `0.5.2`. Prepared-tree evidence, this independent implementation audit and exact-HEAD artifact-integrity checks remain distinct. The separate final clean-export full validation was explicitly skipped by user choice and is not claimed as completed; no new v0.5.2 live browser/Zotero verification is claimed.

---

## 38. v0.5.3 Browser Companion v2 — Current Contract

### 38.1 Authority, preserved boundaries and release status

§38 is the authoritative current v0.5.3 browser/acquisition lifecycle contract. It supersedes conflicting earlier Browser Companion lifecycle, navigation confirmation, download ownership, popup and browser-evidence requirements, including related clauses and acceptance requirements in §§36.8–36.14 and 37.8–37.9. It does not supersede §37's DOI-first identity, Provider behavior, current Paper schema, single-manifestation model or Zotero parent identity. Unaffected §36 staging, authorization, writer safety, cancellation and partial-write requirements continue to apply.

v0.5.3 is RELEASED and the latest released/completed baseline, with package/Provider identities `0.5.3` and standalone companion manifest `0.1.0`. Implementation, A0–A6 reviews/automated acceptance and the final code/behavior audit are complete, with no known substantive implementation defect. Reviewed implementation commit is `21547260157f121a1efd2a5e8f930fad5f26959f`; independently reviewed preparation commit/release HEAD is `4a2595e56bfcfb7d845216c5161e978628d3a71a`. Final exact-release-HEAD validation, annotated tag, main/tag pushes, GitHub Release and asset digest verification are complete (§24.19). Post-release documentation closeout is a separate change after the tag. Scoped normal-Chrome handoff/navigation/termination passed at its actually exercised boundary (§38.13); actual target-PDF staging, Zotero registration and the listed environment-dependent live branches remain unverified. Historical §§24, 36.14 and 37.10 retain their original baseline meaning; they are not v0.5.3 evidence.

Retain the user's normal Chrome profile/session and the narrow MV3 Browser Companion. The companion owns only handoff, navigation, observation and download coordination. The application owns acquisition task identity, normalized DOI identity, Zotero parent/key/Server-ID authority, staging qualification, authorization and all Zotero writes. BrowserHandoffRegistry retains one-time fragment capability, separate event capability, exact task/tab binding and terminal invalidation. No browser authority may retarget the frozen application identity.

Preserve private staging, path/symlink/race protection, PDF byte validation, frozen Zotero identity, duplicate suppression, authorization boundaries, the atomic mutation gate, writer guards and truthful partial-write semantics. This contract does not redesign `pdf_staging.py` or Zotero writer safety; unaffected discovery/monitor and human-managed Markdown behavior remain unchanged.

### 38.2 Two-phase handoff and capability secrecy

The required handoff sequence is:

```text
claim → fragment scrub → activation
```

The initial fragment capability is usable only for the one-time claim. After successful claim, remove the URL fragment before the content script sends an independent activation. Claim and activation must not remain implicitly coupled by the same asynchronous continuation. Session authority must be successfully saved in `chrome.storage.session` before activation/START can proceed.

The initial capability must not appear in activation, publisher/resolver URLs or subsequent browser events; subsequent events use the separate event authority. Preserve its exclusion from handoff-page HTTP requests, HTTP request/access logs, error logs and durable state; the authorized claim exchange must not log it. If the server consumes the claim but the session-authority save fails, still clear the fragment, execute no START and never reuse the consumed capability.

### 38.3 Idempotent activation and START recovery

Duplicate activation and duplicate `tab_ready` are safe for the same active task/claimed tab. For every legitimate `tab_ready`, the application may return the same frozen START plan. A lost START reply is recoverable through popup `Retry companion connection`, which sends activation again without reclaiming the capability, rebinding the tab, reinitializing acquisition context or overwriting download/navigation state. Repeated START handling must preserve that same context and state.

After MV3 worker restart, existing `storage.session` authority permits recovery of the same task. Application restart still terminates the process-local acquisition and invalidates its authority; extension state cannot reconstruct it.

### 38.4 Navigation lifecycle and application stage

Commanded navigation follows `pending → committed`. Persist the intended pending navigation in `chrome.storage.session` **before** calling `chrome.tabs.update()`. Neither a successful update Promise nor a successful START command proves commitment. The claimed task tab's top-level `webNavigation.onCommitted` is the authoritative document-transition confirmation. Navigation failure, including a rejected `tabs.update()`, revokes the corresponding pending state.

Pending navigation must survive worker suspend/restart through session state. Stale committed events cannot overwrite a newer navigation epoch. User navigation in the claimed task tab updates the current epoch from the actual committed top-level navigation; an epoch change invalidates old generic arms and provider actions. Unrelated-tab navigation never changes the task. Do not infer commitment from command/reply timing or allow an immediate onCommitted event to escape the pending/commit lifecycle.

Claim, activation, `tab_ready`, START and pending navigation all leave the application in `HANDOFF`. Only the first legitimate committed task-tab navigation received by the application advances it to `BROWSER_ACTION`.

### 38.5 Browser task authority and termination

The claimed task tab is the sole browser task authority; never migrate authority automatically to another tab. Closing it before a compatible download candidate has been frozen immediately terminates browser acquisition and releases the application's single-acquisition slot. After candidate freeze, task-tab closure does not cancel that exact download ID's completion, staging or commit.

Extension reload/disable, Chrome crash and other failures unable to report a terminal event converge through an application-side **finite inactivity lease**, targeting approximately 30 minutes. This lease is abnormal-recovery control, not durable/product workflow state. Do not rely on a lifetime worker, permanently waiting thread, service-worker keep-alive hack, alarm-driven acquisition runner or generic job framework. Application restart reconstructs no acquisition from tabs, downloads, session state or staging orphans. Terminal paths invalidate authority and release the active slot subject to the preserved mutation-gate/partial-write semantics (§38.12).

### 38.6 Transient download ownership state

Companion runtime must represent at least the following in transient `chrome.storage.session` state:

| State | Required meaning |
| --- | --- |
| Task owner | Exact `taskId`, claimed `tabId`, application origin and event capability. |
| Navigation | Committed navigation, optional pending navigation and current navigation epoch/time. |
| Download expectation | None, a generic user arm, or a trusted provider action. |
| Observations | Bounded exact-ID observed downloads eligible for later reconciliation. |
| Candidate | At most one frozen compatible candidate. |
| Ambiguity | Only after multiple actual compatible download candidates. |

Do not introduce a durable acquisition DB, history or queue, or move active acquisition/history into `storage.local`. Exact state encoding and other nonessential implementation choices remain open.

### 38.7 Generic publisher download attribution

Retain a short-lived explicit generic arm, such as popup `Capture next PDF download`, scoped to the current task and navigation epoch. Chrome DownloadItem has no reliable initiating-tab identity; an empty referrer or detached file URL does not authorize adoption of arbitrary downloads. Generic HTTP/blob downloads without sufficient attribution evidence continue to fail closed.

An unrelated download neither consumes the arm nor becomes a candidate. The arm ends only on timeout, navigation-epoch change, compatible candidate claim or real ambiguity. Other tabs on the same or a related origin are not ambiguity: remove the same-origin competitor-tab rule. Ambiguity requires at least two actual download IDs satisfying current-task attribution conditions; the second compatible candidate deterministically fails ambiguous rather than selecting either file automatically.

### 38.8 Exact-ID download reconciliation

When `downloads.onCreated` metadata is incomplete, the companion may register a bounded transient exact download ID. Subsequent `downloads.onChanged` must reread DownloadItem and reevaluate ownership only for that observed exact ID. An observation may be removed once its ID is demonstrably incompatible with the current task. Do not scan Downloads history without bounds or use a latest/recent-download heuristic.

Only a compatible task-attributed candidate may freeze and proceed through completion to staging. Observed DOI, if present, must match the frozen normalized task DOI; mismatch is rejected before staging or Zotero mutation. Missing observed DOI does not manufacture identity or relax attribution. Delete `Check current download` once reliable onChanged reconciliation is established.

### 38.9 EBSCO provider-action ownership

Retain the existing narrow EBSCO Research content adapter. A trusted user final PDF **Download** click establishes a provider-action expectation itself, without a preceding generic arm. Bind it to the exact active task, claimed tab, exact current Research record, task DOI, visible PDF selection, trusted final click and current navigation epoch/time.

Provider evidence retains `action_time` and `attribution = ebsco_pdf_action`; no independent `arm_time` is required. Keep the existing approximately 120-second provider preparation window finite, including delayed/blob downloads. Wrong task/tab/record/DOI, non-PDF actions, stale actions and ambiguous candidates cannot enter staging. A matching DOI or label alone cannot establish provider-action authority.

### 38.10 Popup operations and manual verification

The popup is the sole human Browser Companion operation entry point. Remove production dependence on `chrome.action.onClicked`, because the manifest uses `default_popup`. Retain only operations with actual task semantics: `Retry companion connection`, generic `Capture next PDF download`, an optional current-URL Chrome download optimization if still required by the existing safety model, explicit `Publisher path exhausted — try XMU`, and resolver choice.

Remove `Check current download` under §38.8, the manual `Login or verification needed` declaration, old version/selected-version wording and redundant operations recoverable through automatic lifecycle events. Institution login, CAPTCHA and MFA remain direct user actions in the same normal Chrome task tab, without bypass or cookie access. A login/verification wait does not establish publisher exhaustion.

### 38.11 Permission model and exclusions

Preserve the current narrow manifest permission model and transient `chrome.storage.session`. Do not add `<all_urls>`, optional broad host permissions, `cookies`, `scripting`, `webRequest`, DNR or a generic publisher content script.

Do not add publisher scraping, DOI-prefix routing tables, a publisher adapter framework, Zotero Connector integration, Playwright/headless/dedicated Chrome fallback, durable acquisition history, batch/queue acquisition, automatic bibliographic Zotero item creation or PDF content/version comparison. Provider, canonicalization, Paper identity and monitor redesign are outside this contract.

### 38.12 XMU, staging and Zotero preservation

Direct DOI/publisher access remains first. XMU fallback requires **explicit publisher exhaustion**; login waits never trigger fallback automatically. Preserve existing institutional resolver eligibility/order and require explicit user choice among multiple eligible resolver providers. Zotero Connector remains independent.

The existing private staged artifact → guarded Zotero commit contract remains unchanged, including duplicate suppression, frozen parent/key/DOI/Server-ID, authorization and writer guards. Preserve candidate-staging/Cancel races and the atomic Cancel/mutation-gate competition. Once the mutation gate is entered, task-tab closure, lease expiry or Cancel cannot imply rollback or bypass the authorized write flow; report confirmed partial writes and mutation uncertainty truthfully. Literature Monitor never deletes the user's Chrome download, on any browser terminal, success, failure or cancellation path.

### 38.13 Required implementation acceptance and verification boundary

The following are **v0.5.3 implementation acceptance requirements**. Current implementation and verification evidence is recorded separately below:

| Area | Required observable acceptance |
| --- | --- |
| Fresh handoff and secrecy | Claim → scrub → activation → `tab_ready` → START → pending → committed → `BROWSER_ACTION`; initial capability secrecy, separate event authority and no START/reuse after consumed-claim session-save failure. |
| MV3 and reply recovery | Worker restart between claim/activation and pending/commit; duplicate activation/`tab_ready` and lost START reply recovery preserve the same task/tab, authority and navigation/download context. |
| Navigation | Pending persistence before `tabs.update()`, immediate onCommitted race, HANDOFF until valid commit, failed-navigation rollback, pending recovery, stale-event rejection, user-navigation epoch updates and unrelated-tab isolation. |
| Termination | Task-tab close before freeze terminates/releases the slot; close after freeze preserves exact-ID completion/staging/commit. Finite inactivity lease converges unreported browser failures; application restart reconstructs no acquisition. |
| Popup and permissions | Unchanged narrow manifest permissions, no production `chrome.action.onClicked`, recovery through popup, removal of redundant/manual-login/version operations and `Check current download` after reconciliation. |
| Generic arm | Task/epoch-scoped finite arm; unrelated downloads preserve the arm and user file; epoch change invalidates expectations; same-origin tabs alone are not ambiguous; second actual compatible candidate deterministically fails ambiguous. |
| Reconciliation and rejection | Incomplete onCreated metadata reconciles through exact-ID onChanged; bounded observations, no latest-download/history scanning, rejection of insufficient generic HTTP/blob attribution and observed DOI mismatch before staging/mutation. |
| EBSCO | Trusted final PDF click works without a generic arm; exact task/tab/Research record/DOI, visible PDF selection, trusted click, epoch and `action_time` evidence are required without `arm_time`. Delayed/blob downloads within the finite preparation window are attributable; wrong/stale/non-PDF/ambiguous actions fail before staging. |
| XMU and human verification | Publisher-first and explicit exhaustion, no fallback from login wait, unchanged resolver semantics/explicit provider choice, and normal manual login/CAPTCHA/MFA continuation on the same claimed tab. |
| Staging and writer safety | Actual private staging boundary, PDF bytes and path/symlink/race protection; unchanged duplicate suppression, frozen identity, authorization, writer guards, cancellation competition and truthful partial-write/uncertainty behavior. Browser terminal paths never delete user downloads or imply post-gate rollback. |

Final v0.5.3 verification must report distinct evidence for Python automated tests, JavaScript runtime/content tests **executed with an actual JS runtime**, synthetic evidence, scoped live normal-Chrome handoff/navigation, generic manual-download ownership where feasible, XMU/EBSCO where feasible, the actual staging boundary, and actual Zotero registration. Report the exercised environment, path and outcome for each, with any unverified live boundary explicitly identified, especially absent actual Zotero registration. Synthetic evidence or Python-only checks do not establish JavaScript execution or live browser/staging/Zotero success.

Keep §36.14's conditional live human-verification boundary: if a genuine challenge appears, verify manual same-task/tab continuation; if none appears, report `HUMAN_VERIFICATION_NOT_PRESENT` and the branch as not live-exercised, without manufacturing a challenge. Historical v0.5.1/v0.5.2 automated, live, staging or Zotero records cannot be relabeled as v0.5.3 verification.

**Current v0.5.3 implementation and verification record (released; scoped live boundary unchanged)**

Implementation and A0–A5 independent reviews are complete; A6 automated integration/acceptance and the final code/behavior audit passed. The closed-claim storage-fault fix and live-found fresh-handoff activation fix also passed independent review. No known substantive implementation defect remains; documentation closeout, the final audit's sole remaining commit-readiness finding, was independently reviewed and included in the implementation commit.

| Evidence category | Executed environment, path and result |
| --- | --- |
| Automated | Actual Node v24.21.0 executed all 8 shipped JS syntax checks, worker simulation **461 passed** (including activation source/current-tab **21**) and content adapters **120 passed**. Targeted browser pytest: **419 passed**; full pytest with required Node PATH: **3280 passed**, with 2 existing dependency deprecation warnings. `uv lock --check` and `git diff --check`: PASS. JS Chrome/DOM simulations and Python tests remain automated/synthetic evidence. |
| Scoped live normal Chrome | Normal Chrome **154.0.8037.93**, current unpacked companion **0.1.0**: fresh automatic claim → scrub → activation after the fix passed without Retry, followed by same claimed tab continuation, committed publisher navigation and application `BROWSER_ACTION`. Current popup operations/removals passed. An earlier naturally stalled handoff exercised popup Retry recovery. Pre-freeze task-tab close released the single slot and a later acquisition started. |
| Direct publisher and TEST_FORCED_XMU | Authorized DOI `10.1093/jrsssb/qkag124` reached the correct Oxford article with no current full-text access; `10.1080/01621459.2026.2731119` reached the correct Taylor & Francis article showing Get Access. TEST_FORCED_XMU reached the real resolver for both, but returned no eligible FullText/SmartLinks candidate and no EBSCO Research route. Resolver entry does not establish genuine publisher exhaustion or successful full-text retrieval. |
| Automated only | MV3/reply/storage/lease races, exact-ID generic ownership/reconciliation/rejection, EBSCO provider-action ownership, post-freeze task-tab close and staging/writer safety passed executable tests; these paths were not live-exercised in this v0.5.3 run. |
| Residual live boundaries | No target PDF was available from the two authorized current test paths: no actual target-PDF DownloadItem, v0.5.3 private staging or Zotero PDF-child registration occurred. EBSCO trusted final PDF action, successful XMU → eligible provider routing and post-freeze close were not live-exercised. No genuine challenge appeared: `HUMAN_VERIFICATION_NOT_PRESENT`; manual challenge continuation remains not live-exercised. |

These are explicit residual verification boundaries accepted for this final audit; they do not convert synthetic checks or unexercised branches into live PASS claims. Historical v0.5.1/v0.5.2 staging/Zotero success remains evidence only for those baselines.

Current scope note: v0.5.3 retains manual institutional login/CAPTCHA/MFA in the same claimed normal-Chrome task tab; login waits do not establish publisher exhaustion. Proactive/automatic institutional authentication is deferred to a future version.

v0.5.3 is RELEASED and the latest RELEASED/completed baseline. Implementation commit `21547260157f121a1efd2a5e8f930fad5f26959f` and independently reviewed release-preparation commit/RELEASE_HEAD `4a2595e56bfcfb7d845216c5161e978628d3a71a` are complete. Package/Provider identities are `0.5.3`; companion manifest remains `0.1.0`. Final exact-release-HEAD validation, annotated tag, main/tag pushes, GitHub Release and authoritative asset digest verification are complete (§24.19). Post-release documentation closeout is separate from the permanent release tag target. The live residual boundaries above are unchanged; proactive/automatic institutional authentication remains future scope.

---

## 39. v0.6.0 Zotero Connector Transition & Publisher Access Preparation — Current Development Contract

### 39.1 Authority and transition

§39 is the current implementation source of truth for v0.6.0. v0.5.3 remains RELEASED and the latest released/completed baseline; v0.6.0 implementation commit `f6600dd6d955699c0ce0a066f8d16067533d89ce` is complete and the current source is in release preparation, but v0.6.0 is not released. Prepared package metadata and OpenAlex/Crossref Provider User-Agent identities are `0.6.0`; no v0.6.0 tag, push, GitHub Release, or release transaction is complete.

The unreleased v0.5.4 Automatic Institutional Access Orchestration + Candidate-Bound PDF Acquisition direction was terminated after product validation. Experimental or dirty v0.5.4 work is research evidence only and does not define v0.6.0 production behavior.

§§35–38 remain immutable historical contracts for their released baselines, including their Browser Companion, acquisition, staging, Zotero-write, release, live-verification, and residual-boundary records. They must not be rewritten to look as though those versions followed v0.6.0 behavior. Where their acquisition, browser, staging, credential, or Zotero-write requirements conflict with this section, §39 governs v0.6.0. §37 continues to govern unaffected DOI-first identity, Provider behavior, current Paper schema, single-manifestation semantics, provenance, and Zotero parent identity.

### 39.2 Kept Paper capture workflow

The v0.6.0 downstream workflow is:

```text
kept
→ Open DOI
→ user manually operates the official Zotero Connector in normal Chrome
→ return to Literature Monitor
→ one automatic reconciliation attempt
→ optional Check Zotero retry
→ in_zotero
```

`Open DOI` and `Check Zotero` are visible only when the current Paper has `status: kept` and its DOI is valid under the existing `normalize_doi()` contract. v0.6.0 exposes no user action named `Copy DOI`, `Mark in Zotero`, or `Add PDF to Zotero`.

The official Zotero Connector remains a user-operated external product. Literature Monitor neither triggers the Connector nor treats Connector attachment progress as workflow state.

### 39.3 Open DOI boundary

For `Open DOI`, the server is authoritative for Paper identity and normalized DOI. It must derive the external target from the current Paper's server-side normalized DOI and construct only:

```text
https://doi.org/<safely encoded normalized DOI path>
```

Encode the normalized DOI as path content so reserved delimiters cannot become a query string, fragment, or second authority component. The browser cannot supply an arbitrary external target URL.

The external DOI URL must not carry Paper UUID, filesystem path, `zotero_key`, CSRF token, internal capability, or other local authority. Open the target in an ordinary new browser tab. Do not start Browser Companion, call a Zotero Connector API, or mutate durable Paper state merely because the DOI was opened.

### 39.4 Return reconciliation intent

The page may hold only process/page-local transient intent indicating that a particular `Open DOI` action is awaiting one return reconciliation. Each Open DOI action may cause at most one automatic reconciliation attempt after the user returns to the Literature Monitor page. Repeated focus or visibility events must coalesce rather than create a request storm.

The return path uses no fixed sleep, polling loop, attachment-readiness wait, Snapshot wait, or Full Text wait. If the Zotero parent is not yet visible, the Paper remains `kept`; the user may later invoke `Check Zotero`.

This return intent may disappear on page refresh. Do not persist it in `localStorage`, `sessionStorage`, cookies, Paper Markdown, SQLite, runtime JSON, or another durable store.

### 39.5 Exact-DOI Zotero reconciliation

Automatic return reconciliation and explicit `Check Zotero` invoke the same application operation. That operation succeeds only when all of the following hold at the guarded mutation point:

- the current Paper is still `kept`;
- its DOI remains valid after `normalize_doi()`;
- Zotero Desktop My Library can be completely enumerated;
- exactly one bibliographic parent in My Library has the same normalized DOI;
- the current Paper's `zotero_key` is absent/null or already equals that exact parent key.

Preserve complete pagination and stable Zotero Server-ID/library-revision safety. Zero exact-DOI parents, duplicate exact-DOI parents, unreadable or incomplete enumeration, unstable enumeration, malformed/conflicting existing `zotero_key`, or concurrent Paper edit/path/location substitution all fail closed without changing the Paper.

On success, atomically change only:

```yaml
status: in_zotero
zotero_key: <verified parent key>
```

Preserve Notes, Sources/provenance, journal attribution, unknown/custom frontmatter, and all other human-authored body content. Reconciliation must use the existing safe Paper-write/concurrency boundary and, after a successful write, retain the existing refreshed-view neighbor-navigation behavior.

The bibliographic parent itself is sufficient. A PDF child need not exist; a Snapshot need not be complete; Connector attachment saving may still be in progress. Reconciliation does not call attachment inspection. `in_zotero` means only that Literature Monitor verified a unique exact-DOI Zotero parent linkage.

### 39.6 Zotero integration boundary

For v0.6.0, Zotero Desktop Local API use is read-only and limited to My Library identity reconciliation. Literature Monitor may enumerate bibliographic parents and read the state required for complete-pagination/revision safety. It does not require Zotero write authorization, create bibliographic items, create PDF attachments, or upload attachment files.

`ZoteroLocalClient.inspect_attachments()` may remain as an unused future read boundary if other cleanup does not require its removal, but the v0.6.0 production workflow must not call it.

Group Libraries and Zotero Web API writes are outside v0.6.0.

### 39.7 Publisher access projection

Settings adds a read-only Publisher access projection derived only from saved Journals:

```text
saved Journals
→ ISSNs
→ existing OpenAlex Source resolution
→ publisher identity + source homepage
→ publisher/site presentation
```

Publisher access is a normal standalone Settings section/panel outside `Advanced & Diagnostics`. It is not part of the Settings form transaction and uses an independent read-only fragment refresh. The page may fetch that fragment independently on load and must fetch it again after a successful `settingsSaved` event. Unsaved Settings draft values and Validate do not alter the projection; Validate must not access OpenAlex, and the Settings Save transaction must not depend on Publisher resolution.

OpenAlex or Publisher access resolution failure affects only the Publisher access panel. It must not create a Workspace issue/diagnostic or Run issue/diagnostic, write `last-run.json`, Provider-state, or other durable diagnostic/history state, or change the result of Settings Validate or Save.

Publisher resolution writes nothing back to Journal durable state, adds no Publisher column to `list.md`, and introduces no database table, cache schema, or durable projection history.

Request only OpenAlex Source fields needed for this projection, such as `host_organization`, `host_organization_name`, `homepage_url`, and, when needed for reliable identity presentation, `host_organization_lineage`.

Group primarily by stable OpenAlex host-organization identity. Do not infer publisher identity from journal names, DOI prefixes, or hand-maintained string heuristics. Deduplicate the same Source reached through multiple ISSNs and deduplicate repeated publisher/site entries. When one publisher has distinct real journal/platform homepage hosts, preserve those distinct sites rather than collapsing them into a corporate homepage. When reliable publisher identity or homepage data is unavailable, present it as unavailable. Partial resolution retains successful results and compactly identifies saved Journals that could not be mapped.

### 39.8 Publisher authentication boundary

Publisher access helps the user open the real journal or publisher platform early and complete institutional authentication manually. Literature Monitor does not determine whether login succeeded, inspect cookies, inspect DOM login indicators, read account or institution identity, determine entitlement, persist login status or `checked_at`, diagnose session expiration, or maintain publisher-specific runtime adapters.

v0.6.0 performs no automatic CARSI, Smart Gateway, WebVPN, XMU credential submission, credential exchange, or credential-store orchestration.

### 39.9 Retired production responsibilities

The following are not v0.6.0 production responsibilities:

- Literature Monitor Browser Companion;
- publisher PDF discovery or browser download attribution;
- PDF staging or staged-artifact ownership;
- Zotero write authorization;
- bibliographic-item creation or PDF attachment create/upload;
- institutional credential store or exchange;
- Smart Gateway orchestration;
- XMU IdP automatic login;
- publisher-family runtime adapters.

Removing these responsibilities does not alter the historical truth of §§35–38. Their old implementation and verification records remain version-specific evidence.

Automatic Zotero Connector triggering, Connector forks, localhost Connector command bridges, completion/heartbeat signals, target collection routing, and related automation are possible future v0.6.x work only. They are not authorized by the v0.6.0 contract.

### 39.10 Durable workflow state

The durable workflow schema remains:

```yaml
status: candidate | rejected | kept | in_zotero
zotero_key: <Zotero item key> | null
```

Do not add `pending_capture`, `saving`, `pdf_ready`, publisher-login state, capture history, or another durable workflow status/database. Existing historical `in_zotero` Papers are not migrated and are not rechecked for attachments merely because v0.6.0 changes the capture workflow.

### 39.11 Explicit v0.6.0 exclusions

v0.6.0 does not include:

- automatic Zotero Connector triggering;
- a Connector fork or localhost Connector command bridge;
- Connector heartbeat or save-completion observation;
- collection/tag routing;
- PDF/Snapshot readiness validation;
- attachment polling or sleep-based save-completion logic;
- publisher login validation or credential/session/cookie automation;
- PDF-source provenance or fallback diagnostics;
- Group Library support;
- Zotero Web API writes;
- CLI `export-kept` redesign;
- a new workflow status;
- unrelated retrieval, canonicalization, or Provider redesign.

### 39.12 Implementation acceptance and evidence

The following v0.6.0 implementation acceptance requirements retain the semantics established by A1. A2–A6 plus Final Audit Fix 1 implemented them as applicable and the completed implementation was independently reviewed:

- removal of the old Browser Companion/custom acquisition/staging/Zotero-write production path without leaving retired production imports or active entry points;
- action visibility limited to valid `kept` Papers with valid normalized DOI, with obsolete capture/write actions absent;
- server-authoritative, safely encoded DOI navigation with no arbitrary external URL or leaked local authority;
- one-shot return reconciliation with focus/visibility deduplication and no polling/sleep dependency;
- explicit `Check Zotero` retry using the same application reconciliation operation;
- exact-DOI parent-only success, with attachment inspection/readiness unnecessary;
- complete-pagination and stable-revision safety, zero/duplicate/incomplete/unstable cases failing without mutation;
- atomic two-field Paper update preserving human content and rejecting concurrent Paper/path substitution;
- preserved refreshed-view next-neighbor behavior after successful reconciliation;
- Publisher access derivation from saved Journals, stable publisher grouping, distinct site-host preservation, partial-failure presentation, and no Journal-state writeback;
- Publisher access presented as normal Settings content outside `Advanced & Diagnostics`, using independent read-only fragment loading on page load and refresh after `settingsSaved`;
- Settings Validate/Save isolation from Publisher resolution, with unsaved drafts and Validate leaving the projection unchanged;
- Publisher access/OpenAlex resolution failure isolated to its panel, with no Workspace or Run diagnostic and no durable diagnostic/history write;
- read-only Zotero integration with no production Zotero write authorization or create/upload path;
- removal of keyring integration if no production caller remains after retired credential functionality is removed;
- unchanged CLI `export-kept` behavior;
- no new durable workflow state or capture/login history;
- relevant Python and executable JavaScript tests for changed behavior;
- full `pytest` at final integration scope;
- Node-executed JavaScript tests where such tests remain applicable;
- `uv lock --check`;
- `git diff --check`.

Material final-audit evidence before the implementation commit: focused audit **912 passed / 9 skipped / 2 warnings** and full pytest **2381 passed / 9 skipped / 2 warnings**; `uv lock --check` and `git diff --check` passed. Post-commit full pytest again passed **2381 / 9 skipped / 2 warnings**. Node was unavailable in the independent AgentDock environment, so Node-dependent executable JavaScript coverage remains an explicit validation gap and is not claimed as a JS PASS. No live browser/Zotero Connector validation is claimed for v0.6.0, and historical v0.5.x browser/PDF live evidence does not establish v0.6.0 behavior.

Final Audit Fix 1 closed the exact-parent proof gap: `ZoteroLocalClient.resolve_identity()` can return `VERIFIED` only for an exact normalized DOI match that is a top-level item and has an accepted bibliographic item type. Unknown exact-DOI item types and accepted bibliographic types carrying `parentItem` fail closed rather than authorizing `in_zotero` mutation.
