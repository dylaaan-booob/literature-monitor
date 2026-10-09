# Literature Monitoring Workflow — Specification v1.4

**Status:** Active; v0.6.3 is in release preparation and is NOT YET PUBLISHED. Python package, Provider User-Agent and Connector candidate identities are `0.6.3`; v0.6.2 remains the latest verified published version until the v0.6.3 publication transaction completes. Permanent historical release evidence for v0.6.2 is in §41.16 and for v0.6.1 in §40.16.

**Stage:** v0.6.3 A0–A9 and Reset Guard Lifecycle Fix are implemented. Independent development review and manual Chrome/Zotero/Reset acceptance were reported complete before this release request; §42 records the automation evidence and prior limitations. v0.6.3 release identities, final artifacts and publication still require verification. Earlier v0.6.2 release and acceptance records remain historical (§41.16); release execution and evidence reuse follow [AGENTS.md](AGENTS.md#release-execution-and-verification-reuse).
**Scope:** Journal monitoring with CLI, durable Markdown workspace, Obsidian presentation, and a local Python Web UI adapter; conferences remain excluded

**Current contract:** §42 governs the v0.6.3 Paper workflow, batch Connector import, access preparation, UI and Workspace Reset. §41 governs unaffected ISSN-L Journal identity, retrieval and Settings; §37 governs unaffected DOI-first identity and Provider semantics. Unchanged §39/§40 Connector security boundaries continue to apply. Earlier version-specific contracts and release records retain their historical scope.

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

The completed implementation/release sequences below are historical records.
Their separate reviews, validation runs, commits and documentation closeouts
are not mandatory stages for later versions. Current release execution and
validation reuse follow [AGENTS.md](AGENTS.md#release-execution-and-verification-reuse).
Historical results retain their original provenance and verification scope.

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

### 24.20 v0.6.0 Zotero Connector Transition & Publisher Access — completed release closeout

The completed v0.6.0 implementation commit is `f6600dd6d955699c0ce0a066f8d16067533d89ce` (`Implement v0.6.0 Zotero Connector workflow`). It retires the Literature Monitor Browser Companion, custom browser/PDF acquisition and Zotero write path; adds Open DOI plus read-only exact-DOI Zotero parent reconciliation; adds the saved-Journal Publisher access projection and Settings UI; closes current-state documentation; and includes Final Audit Fix 1 so `resolve_identity()` verifies only exact-DOI top-level accepted bibliographic parents.

Release preparation was deliberately narrower than the implementation commit. It changed only package version identity `0.5.3` → `0.6.0`, OpenAlex/Crossref User-Agent identity `literature-monitor/0.5.3` → `literature-monitor/0.6.0`, directly coupled version assertions, the local editable package version in `uv.lock`, and current-state/release-preparation documentation. It did not change product functionality, dependency membership, Open DOI/reconciliation behavior, Publisher grouping/UI behavior, Local API behavior, CLI behavior, or the retired-path boundary.

The independently reviewed release-preparation diff was committed as `b2ee7fe34efff692e1e77fb612c24f3a05b83340` (`Prepare v0.6.0 release`), whose parent is the implementation commit `f6600dd6d955699c0ce0a066f8d16067533d89ce`. The reviewed scope was exactly **9 files / 43 insertions / 28 deletions**, limited to package version identity, OpenAlex/Crossref User-Agent identity, directly coupled assertions, the editable package version in `uv.lock`, and current-state documentation. No functionality, dependency membership, product behavior, or runtime contract changed in release preparation.

The release-preparation commit was followed by an independently reviewed documentation closeout, committed as RELEASE_HEAD `df3d805f173b1a4b8f48264821a105df5613f822` (`Close v0.6.0 release-prep documentation`). The completed sequence was implementation → independent reviews/final audit → implementation commit → release preparation → independent release-preparation review → release-preparation commit → documentation closeout before candidate final validation → exact RELEASE_HEAD clean-export validation → annotated tag → remote-main push → tag push → GitHub Release → this separate post-release documentation closeout. The final element is outside the permanent `v0.6.0` tag target and release artifacts.

Implementation/final-audit evidence remains distinct from later release validation. The completed final audit recorded focused **912 passed / 9 skipped / 2 warnings**, full pytest **2381 passed / 9 skipped / 2 warnings**, `uv lock --check` PASS and `git diff --check` PASS; Node was unavailable in that earlier independent AgentDock environment because its Core PATH did not yet expose the stable fnm alias. Initial release-preparation validation similarly recorded focused OpenAlex/Crossref pytest **419 passed**, full pytest **2381 passed / 9 skipped / 2 existing dependency warnings**, `uv lock --check` PASS, `git diff --check` PASS, repository-external wheel/sdist build and metadata/payload checks, and isolated installed-wheel smoke. After the AgentDock environment was corrected, post-commit source-tree verification used Node **v24.21.0** and npm **11.19.0**, executed the Open DOI JavaScript test (**PASS**) and selected Web/Settings executable tests (**8 passed**), and ran full pytest **2390 passed / 0 skipped / 2 warnings**.

Final exact-release-HEAD validation used a fresh Git archive of exactly `df3d805f173b1a4b8f48264821a105df5613f822` with **114 exported tracked files**. Frozen offline sync passed. Node **v24.21.0** was available; Open DOI executable JavaScript passed **1 test**, selected executable Web/Settings JavaScript passed **8 tests**, Provider tests passed **419**, focused v0.6 integration passed **921 / 2 warnings**, and full pytest passed **2390 / 0 skipped / 2 existing dependency warnings**. `uv lock --check` passed. Both final artifacts had correct `0.6.0` metadata; the complete **58-file** `literature_monitor` package payload matched the pristine build export byte-for-byte; isolated installed-wheel imports/resources/CLI smoke and `uv pip check` passed.

The immutable final artifacts are:

- `literature_monitor-0.6.0-py3-none-any.whl`: **198918 bytes**, SHA-256 `c38c50e7d813f8a2faa57096c7be527d1b9a7b6dc6d9f0180f171dafb7eb05ec`.
- `literature_monitor-0.6.0.tar.gz`: **188361 bytes**, SHA-256 `82d367a4975a48683810a66198a459da21b19053bc98f251e4c2ae491953b427`.

Annotated tag `v0.6.0` has tag object `a0e32576cc746029a3fb23ee03f6b43a2629d73c` and peels to permanent RELEASE_HEAD `df3d805f173b1a4b8f48264821a105df5613f822`. Remote `main` was fast-forwarded to that exact RELEASE_HEAD without moving the protected local `main`, and the annotated tag was pushed. [GitHub Release v0.6.0](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.6.0) is published, non-draft and non-prerelease, with exactly the wheel and sdist above. GitHub authoritative SHA-256 digest fields match both retained artifacts, and independently downloaded copies matched the retained files byte-for-byte.

v0.6.0 is RELEASED and is the latest released/completed baseline. No automated Zotero Connector triggering, live publisher-login automation, new Zotero write path, or PDF-attachment requirement is claimed by this release. Post-release documentation closeout is a separate later change outside the permanent `v0.6.0` tag target and release artifacts; the tag remains permanently on RELEASE_HEAD.

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

## 39. v0.6.0 Zotero Connector Transition & Publisher Access — Current Contract

### 39.1 Authority and transition

§39 is the current released behavior source of truth for v0.6.0. v0.6.0 is RELEASED and is the latest released/completed baseline. Implementation commit `f6600dd6d955699c0ce0a066f8d16067533d89ce` and independently reviewed release-preparation commit `b2ee7fe34efff692e1e77fb612c24f3a05b83340` are complete. Permanent RELEASE_HEAD and annotated `v0.6.0` tag target are `df3d805f173b1a4b8f48264821a105df5613f822`; package metadata and OpenAlex/Crossref Provider User-Agent identities are `0.6.0`. Final exact-release-HEAD validation, remote-main/tag pushes, GitHub Release, and authoritative release-asset digest verification are complete. This post-release documentation closeout is a separate later change outside the permanent tag target.

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

Material final-audit evidence before the implementation commit: focused audit **912 passed / 9 skipped / 2 warnings** and full pytest **2381 passed / 9 skipped / 2 warnings**; `uv lock --check` and `git diff --check` passed. Post-commit full pytest initially again passed **2381 / 9 skipped / 2 warnings** while Node remained unavailable to that AgentDock Core PATH. After the environment was corrected to expose the stable fnm alias, independent post-commit source-tree verification resolved Node **v24.21.0** and npm **11.19.0**, executed the Open DOI one-shot JavaScript test (**PASS**) and selected Node-dependent Web/Settings tests (**8 passed**), and ran full pytest **2390 passed / 0 skipped / 2 warnings**. These source-tree results are distinct from final release validation.

Final clean-export release validation separately used exact RELEASE_HEAD `df3d805f173b1a4b8f48264821a105df5613f822`. A fresh Git archive contained **114 tracked files**, `uv sync --frozen --offline` passed, Node **v24.21.0** executed the v0.6 JavaScript harnesses, and full pytest passed **2390 / 0 skipped / 2 existing dependency warnings**. `uv lock --check`, wheel/sdist metadata verification, complete **58-file** packaged `literature_monitor` payload comparison, isolated installed-wheel smoke, and `uv pip check` all passed. This final evidence belongs to the permanent v0.6.0 RELEASE_HEAD; no live publisher login, automated Zotero Connector operation, or Zotero write validation is claimed.

Final Audit Fix 1 closed the exact-parent proof gap: `ZoteroLocalClient.resolve_identity()` can return `VERIFIED` only for an exact normalized DOI match that is a top-level item and has an accepted bibliographic item type. Unknown exact-DOI item types and accepted bibliographic types carrying `parentItem` fail closed rather than authorizing `in_zotero` mutation.

---

## 40. v0.6.1 Automatic Zotero Connector Capture (Current Contract)

### 40.1 Authority, baseline and supersede boundary

v0.6.1 is RELEASED and is the latest released/completed baseline (§40.16). §39 remains the historical/released authority for v0.6.0, including its implementation, validation and release evidence. v0.6.1 development started from the post-release `origin/main` state after v0.6.0.

§40 is the current released contract for v0.6.1. It supersedes §39 only where §39 excludes automatic Zotero Connector triggering and the narrowly related Connector bridge, orchestration and completion observation authorized below. Unchanged §39 behavior continues to apply. §37 continues to govern unaffected DOI-first identity, Provider behavior, the current Paper schema, provenance, single-manifestation semantics and My Library bibliographic-parent identity.

The terminated v0.5.x Browser Companion/custom acquisition direction is not an implementation baseline for v0.6.1. Historical §§35 through 39 remain version-specific records and must not be rewritten to match this current contract.

### 40.2 Kept Paper workflow

For a Paper whose current `status` is `kept` and whose DOI is valid under the existing `normalize_doi()` contract, the primary action becomes:

```text
Save to Zotero
```

`Open DOI` and `Check Zotero` remain available.

The normal `Save to Zotero` flow is:

```text
Save to Zotero
→ authoritative existing exact-DOI reconciliation
→ if one unique exact-DOI My Library bibliographic parent already exists:
     use the existing reconciliation path and enter in_zotero
     do not start Connector capture
→ only an explicit exact-DOI NOT_FOUND result may create an automatic capture attempt
→ Connector claims the command
→ Connector creates a dedicated task tab in the user's normal Chrome profile/session
→ task tab opens the server-built DOI URL
→ Zotero Connector uses its existing translator/save machinery
→ observation reports CONFIRMED, UNCONFIRMED or FAILED
→ CONFIRMED or UNCONFIRMED performs exactly one follow-up exact-DOI reconciliation
→ only reconciliation finding one unique parent may enter in_zotero
→ otherwise the Paper remains kept
```

Connector completion is never Paper or bibliographic identity authority. The final durable transition remains controlled by exact-DOI My Library reconciliation.

### 40.3 Durable Paper state and process-local capture state

The durable Paper workflow schema remains:

```yaml
status: candidate | rejected | kept | in_zotero
zotero_key: <verified My Library parent key> | null
```

v0.6.1 must not add durable `capture_status`, `saving`, `connector_result`, `pdf_ready`, `attachment_state`, `publisher_login_state`, `capture_history` or equivalent capture history/state.

An automatic capture attempt is process-local only. It must represent at least:

- an opaque attempt/request identity;
- Paper identity;
- normalized DOI;
- the server-built DOI URL;
- stage;
- start time;
- claim/result state;
- terminal outcome.

The orchestration stages need cover only the actual process boundary, for example:

```text
WAITING_FOR_CONNECTOR
CONNECTOR_ACTIVE
FINISHED
```

Terminal outcomes must distinguish at least:

```text
CONFIRMED
UNCONFIRMED
FAILED
```

Attempt state must not be written to Paper Markdown, SQLite, Provider state, `last-run.json` or another durable store.

Process-local Connector presence must maintain the most recent heartbeat, Connector version and a Zotero-reachable flag solely for readiness and fast failure. Readiness is `connected` only when the heartbeat is sufficiently recent and the Zotero-reachable flag is true. Automatic browser capture may begin only while readiness is `connected`. A missing or stale heartbeat, or a false Zotero-reachable flag, must return `unavailable` promptly and must not create a long-lived `WAITING_FOR_CONNECTOR` attempt. In that case the Paper remains `kept`, no automatic browser save starts, and `Open DOI` remains the manual fallback. Presence state remains process-local transient state and must not become durable history, diagnostics or an event log.

### 40.4 Capture eligibility and authoritative preflight

Automatic capture may begin only when all of the following hold:

- the current Paper remains `kept`;
- its normalized DOI is valid;
- Zotero Desktop Local API/My Library is available for authoritative reconciliation;
- Connector presence satisfies §40.3's recent-heartbeat `connected` readiness gate;
- no other automatic capture attempt is active in the Literature Monitor process.

Before any browser save starts, Literature Monitor must invoke the existing authoritative exact-DOI reconciliation boundary.

Preflight results have these semantics:

- one unique exact-DOI bibliographic parent: complete the existing reconciliation success path and do not trigger Connector capture;
- duplicate exact-DOI bibliographic parents: fail closed and do not trigger Connector capture;
- unreadable, incomplete or unstable Zotero state: fail closed and do not trigger Connector capture;
- malformed or conflicting Paper state: fail closed;
- explicit `NOT_FOUND`: automatic Connector capture may start.

`candidate`, `rejected` and `in_zotero` Papers cannot start capture. A Paper with no DOI or an invalid DOI cannot start capture.

`reconcile_paper_with_zotero()` continues to own the final authoritative chain:

```text
safe Paper identity
→ current kept-state verification
→ normalized DOI
→ complete My Library enumeration
→ exact DOI uniqueness
→ existing zotero_key compatibility
→ atomic status + zotero_key compare-write
```

The capture coordinator must not duplicate, weaken or bypass that authority.

### 40.5 Localhost Connector bridge

The existing Literature Monitor Web process on `127.0.0.1:8000` also hosts the Connector bridge. v0.6.1 does not add a second long-running daemon.

The bridge has only the responsibilities needed for:

- Connector heartbeat;
- pending-command fetch/claim;
- terminal-result submission;
- Web UI capture snapshot/readiness observation.

A command payload carries only the minimum task information, such as:

- opaque request identity;
- server-authoritative DOI target.

The automatic DOI target must be derived from the server's current Paper and its normalized DOI using the same normalization and path-encoding safety boundary as §39.3. The only permitted external target form is:

```text
https://doi.org/<safely encoded normalized DOI path>
```

Reserved delimiters in the DOI must remain encoded path content and must not become a query string, fragment or second authority component. The browser or Connector must not submit, replace or override the target with an arbitrary external URL.

The command must not send Paper Markdown, a Paper/filesystem path, `zotero_key`, CSRF tokens, Workspace content, institutional credentials or collection configuration.

Connector-returned `item_key`, title, URL or success flags are observations, not bibliographic identity authority. The Connector result endpoint must not directly modify a Paper.

A stale request, previous-attempt result, malformed result, oversized result or unrelated result must not terminate the current attempt.

### 40.6 Browser and Zotero Connector boundary

v0.6.1 supports automatic capture only in Chrome with an MV3 Connector. Firefox, Safari and Edge automatic capture are outside this version.

The Literature Monitor Connector uses Zotero Connector upstream as the source of save capability. It must preserve upstream translators, page translation/save machinery, the user's normal browser cookies/session and normal Zotero Desktop connector-server interaction.

Literature Monitor-specific changes must remain concentrated in command polling, task-tab orchestration, save triggering and completion reporting. v0.6.1 does not reimplement publisher translators, add publisher-specific DOM/runtime adapters or automatically enter institutional credentials.

The official Zotero Connector and the Literature Monitor Connector may coexist in the same Chrome profile. They must not share Literature Monitor automatic command, attempt or completion state. The official Zotero Connector remains available for ordinary manual user operation.

Every automatic capture creates its own task tab. It must not borrow or navigate an already open user tab.

A confirmed safe terminal save may close the task-created tab. `FAILED` and `UNCONFIRMED` must leave the task-created tab open for inspection or manual recovery.

### 40.7 Completion and reconciliation semantics

Automatic capture does not require PDF attachment or Snapshot completion. Across the entire v0.6.1 automatic capture path, Literature Monitor must not call `ZoteroLocalClient.inspect_attachments()`, poll PDF attachments, poll Snapshots, check whether a PDF file has landed on disk, wait for attachment readiness, or add a fixed `sleep(5)` or equivalent fixed delay to guess save completion. It must not classify or gate on publisher PDF, OA PDF, arXiv PDF or any other attachment source/version.

The only durable success authority is reconciliation against My Library finding one unique exact-DOI bibliographic parent.

`CONFIRMED` completion performs exactly one follow-up exact-DOI reconciliation.

`UNCONFIRMED` completion also performs exactly one follow-up exact-DOI reconciliation. If that reconciliation still returns `NOT_FOUND`, Literature Monitor must not automatically retrigger Connector capture.

`FAILED` leaves the Paper `kept`, performs no automatic repeated save and preserves `Open DOI` and `Check Zotero` as manual recovery paths.

If the Local API still exposes no unique parent after a `CONFIRMED` result, the Paper remains `kept`. If Connector activity creates duplicate exact-DOI parents, reconciliation fails closed and must not select one arbitrarily.

If Zotero actually saves the item into a Group Library while My Library has no unique exact-DOI bibliographic parent, Literature Monitor reconciliation does not succeed. v0.6.1 must not add Group Library lookup or fallback to rescue that result.

### 40.8 Concurrency, timeout and restart

The Literature Monitor process permits at most one active automatic capture attempt at a time. v0.6.1 does not add a batch queue or concurrent multi-Paper capture.

If `Save to Zotero` is invoked while an attempt is active, the system must not create a second attempt.

An active attempt must have a finite timeout and cannot occupy the single capture slot indefinitely. Timeout ends only the process-local attempt lifecycle; it does not alter exact-DOI reconciliation authority.

If a command has not been claimed by the Connector when its waiting timeout expires, Literature Monitor must release the active capture slot, leave the Paper unchanged in `kept`, perform no automatic save, and create no durable capture state.

If the Connector has already claimed the command but the terminal result is lost or completion observation times out, Literature Monitor must not interpret that condition as ordinary `FAILED`. It follows `UNCONFIRMED`/uncertain-completion semantics: perform exactly one exact-DOI reconciliation, keep the Paper `kept` if no unique exact-DOI My Library parent is found, and do not automatically retrigger the Connector.

v0.6.1 adds no Cancel workflow. Finite timeout is the mechanism that releases a lost or disconnected process-local attempt.

Capture state exists only for the current Literature Monitor process. After restart:

- no attempt is restored;
- the system does not infer that an interrupted capture failed;
- no automatic retry occurs;
- no Paper is modified because of the lost attempt;
- `Check Zotero` is the recovery path for an uncertain result.

If a Paper is modified, moved, replaced or changes status during capture, any final mutation remains subject to the existing safe-read and compare-and-swap Paper boundary.

### 40.9 Web UI and Settings

For a valid `kept` Paper, the primary action is `Save to Zotero`. `Open DOI` and `Check Zotero` remain visible.

`Open DOI` retains the v0.6.0 manual-fallback semantics. `Check Zotero` retains explicit reconciliation semantics. Successful reconciliation continues to use the current refreshed-view neighbor navigation.

Settings adds only a compact Connector readiness indication:

```text
connected
unavailable
```

v0.6.1 does not add Connector diagnostics history, an event log, publisher diagnostics or a troubleshooting dashboard.

The v0.6.0 Publisher access behavior remains unchanged. Its purpose is to let the user open publisher pages early and establish a normal Chrome institutional session manually. v0.6.1 does not detect publisher login state.

### 40.10 License, source and packaging boundary

The Literature Monitor Connector is a separate AGPL-compatible browser component. Its source/build records must preserve:

- Zotero Connector upstream copyright and license notices;
- the exact upstream revision;
- the Literature Monitor-specific patch boundary;
- reproducible source provenance.

Zotero Connector-derived code must not be represented as MIT-licensed Python-main-program code. The Python `literature_monitor` package remains MIT.

The Connector is excluded from the Python wheel and Python runtime import graph. Connector build artifacts remain separate from the Python wheel/sdist.

The exact source layout and vendoring/submodule strategy are deferred to a later implementation task, which must choose a reproducible-build approach that preserves the license boundary. A0 does not freeze unnecessary implementation details.

### 40.11 Explicit v0.6.1 exclusions

v0.6.1 does not include:

- collection selection or automatic collection routing;
- tag routing;
- Group Library support;
- Zotero Web API write credentials;
- a Zotero Desktop plugin for collection routing;
- batch/queue capture;
- concurrent multi-Paper saves;
- durable capture history;
- restart recovery;
- PDF/Snapshot success gating;
- local PDF-file verification;
- PDF-source/version diagnostics;
- publisher session-expiry diagnostics;
- institutional-login automation;
- CARSI, Smart Gateway, WebVPN or XMU automatic login;
- credential storage;
- publisher-specific runtime adapters;
- Firefox, Safari or Edge automatic capture;
- automatic duplicate merge;
- CLI `export-kept` changes;
- DOI work-identity changes;
- Provider or canonicalization redesign.

Retired v0.5.x production modules must not be restored, including:

```text
application/acquisition.py
browser_acquisition.py
pdf_staging.py
web/acquisition_coordinator.py
web/browser_handoff.py
zotero_write.py
zotero_credentials.py
browser_companion/
```

Historical cleanup tests should continue to prove that these old production paths remain retired.

### 40.12 Verification contract

This A0 section defines the verification boundary required for the completed v0.6.1 implementation. It does not claim that implementation, builds, tests or live verification have already occurred.

Final implementation acceptance requires at least:

- network-independent Python tests for coordinator, bridge and orchestration behavior;
- executable Node/JavaScript Connector tests;
- command polling;
- heartbeat;
- the single-active-task boundary;
- safe DOI URL handling;
- save triggering;
- `CONFIRMED`, `UNCONFIRMED` and `FAILED` outcomes;
- stale-result rejection;
- existing v0.6.0 exact-DOI/Paper-safety regression tests;
- full pytest;
- applicable Zotero Connector upstream/build tests;
- `uv lock --check`;
- `git diff --check`;
- verification that Python wheel/sdist and Connector artifacts remain separate;
- live automatic save in normal Chrome with Zotero Desktop without a toolbar click;
- at least one real publisher DOI using an already established normal-browser institutional session;
- treatment of PDF/attachment results as observations rather than a release gate;
- a synthetic completion-unconfirmed case proving that no automatic retry occurs.

Evidence must be recorded only after the corresponding check has actually run. Historical v0.6.0 validation does not establish v0.6.1 implementation or live success.

### 40.13 Release boundary

A0 does not modify Python package version, Provider User-Agent version, Connector release version, tags, changelog/release assets or GitHub Release state.

The released Python package, OpenAlex/Crossref Provider User-Agent and generated
Connector artifact identities are `0.6.1`. Release-preparation commit and
permanent annotated `v0.6.1` tag target are
`22bd1efb6c3534110850dfaae5a6239f74b16731`, following implementation
`b5f97adefac4c16f46a3383c92d768fefa8ac22b`. Publication and verification
are complete (§40.16); this documentation closeout remains outside the tag.
The version identity change does not alter the §40 behavior contract.

Follow [AGENTS.md](AGENTS.md#release-execution-and-verification-reuse) for
continuous release execution, necessary artifact checks and evidence reuse.
The existing §40.14 implementation/live acceptance and §40.15 preparation
results may be reused where their inputs remain applicable. An actual input
change requires affected checks; a commit or publication step alone does not
require another full suite, independent audit or live capture. This changes
only the operating procedure, not §40.12 acceptance or historical evidence.

### 40.14 A7 implementation and validation evidence (2026-10-07)

A7 inspected the uncommitted A0 through A6 worktree on `v0.6.1-development`,
with HEAD and `origin/main` at `7a7ffb5b842c41a317dd609b6dc3bcae9e53b2c3`.
The runtime, bridge and orchestration are implemented. This evidence does not
change the behavior contract above or the historical records in §§35 through 39.
v0.6.1 remains unreleased. Required live acceptance passed and A7 is
**READY FOR COMMIT**, subject to the explicitly recorded upstream harness
environment limitation below; this is not release preparation or publication.

The fresh external Connector build passed with upstream revision
`876e41ad15139077f2e07b2f71a0fa94742e0b4a` and all five `upstream.lock`
submodule pins verified. The artifact is named `Literature Monitor Connector`,
uses Manifest V3 and `background-worker.js`, and includes the byte-identical
current runtime overlay, upstream `COPYING` and provenance marker. The
Literature Monitor runtime bridge authority is only `http://127.0.0.1:8000`.

A7 reproduced and corrected one completion-observation defect: an exception
after dispatching local `saveItems`, before receiving its response, could emit
`FAILED` despite an uncertain bibliographic save. The upstream delta now tracks
request dispatch separately from parent acceptance. Only a pre-dispatch error
emits the deterministic failure signal; a lost post-dispatch response follows
`UNCONFIRMED` observation semantics. The Connector regression
`lost saveItems response is UNCONFIRMED while pre-dispatch failure is FAILED`
failed on the previous delta (35/36) and passed after the correction (36/36).
This changes only the declared Connector patch boundary; Python production code
and package/Provider versions were not changed.

Deterministic validation passed: Connector Node **36/36**, the requested focused
Python suite **356 passed**, and full pytest **2505 passed**, with two existing
dependency deprecation warnings. Explicit Python cases included
`test_claimed_timeout_unconfirmed_uses_same_one_shot_reconciliation`,
`test_terminal_completion_reconciles_exactly_once`,
`test_claimed_timeout_unconfirmed_uses_same_poll_reconciliation`,
`test_failed_capture_has_no_completion_reconciliation`, and
`test_failed_capture_stops_polling_without_reconciliation_or_retry`.
Supplemental synthetic checks exercised explicit `UNCONFIRMED` and a claimed
command with a lost terminal result: each performed one final reconciliation,
kept an unchanged Paper after `NOT_FOUND`, and issued no replacement command.
Connector post-trigger timeout checks observed one task tab, one save trigger
and zero automatic save retries, including later polling. Parent confirmation
while the full attachment/save promise remained unresolved also passed.

A real `kept` Paper and its three Author files were copied read-only into the
repository-external workspace `/private/tmp/lm-a7-retest-hjcwgyga/validation-workspace`.
Authoritative My Library preflight for `10.1109/tit.2026.3702696` returned
explicit `NOT_FOUND`. Zotero Desktop 10.0.5 Local API and connector server were
reachable. The current Web process used the external config on `127.0.0.1:8000`.

Earlier attempts were blocked by Chrome GUI availability and an absent
institutional session. Following the user's environment recovery and manual
login, A7 rebuilt the corrected current source into the fresh external artifact
`/private/tmp/lm-a7-retest-hjcwgyga/output`. It was loaded and enabled as the
independent `Literature Monitor Connector` extension alongside the official
Zotero Connector in the existing normal, non-incognito Chrome profile. The
bridge reported `connected`, version `4.999.0` and `zotero_reachable=true`.
Before capture, the direct IEEE page visibly displayed
`Access provided by: Xiamen University`. No credentials, cookies, SSO or CAPTCHA
were read or automated.

One Web UI **Save to Zotero** click, with no Connector toolbar click, started
the capture after authoritative `NOT_FOUND` preflight. The Connector claimed
the command and opened a fresh normal task tab using the server-built
`https://doi.org/10.1109/tit.2026.3702696` target. It redirected to
`ieeexplore.ieee.org/document/11558502` and inherited the existing institutional
session. The upstream translator/save path produced `CONFIRMED`; Web completion
polling performed final exact-DOI reconciliation. The temporary Paper became
`in_zotero` with key `M5ZJP5CE`. An independent authoritative My Library lookup
verified the unique parent `M5ZJP5CE`; the Connector's returned item key was not
identity authority. Both required toolbar-free and institutional-session live
cases **PASS**. The original Paper remains `kept` and the new Zotero item is
retained.

Read-only attachment observation found an imported PDF child `4GLWIULE`
(`application/pdf`, `Full Text PDF`) and no Snapshot. Attachment readiness was
not awaited and did not gate bibliographic parent success.

The optional pre-existing-parent live case was prepared in a separate external
workspace but not executed: subsequent native Chrome navigation returned
`windowNotFoundAtPosition` and intermittent empty accessibility state. Existing
deterministic short-circuit regressions passed; this optional live case remains
**NOT VERIFIED**. The validation Web processes were stopped and the personal
`monitor.yaml` Web service was restored on port 8000 under the user's explicit
authorization.

The 17 applicable upstream ItemSaver tests remain **NOT VERIFIED**: the
Puppeteer global setup failed before test bodies ran because its required Chrome
`150.0.7871.24` was not installed. The harness and browser environment were not
changed to bypass that limitation.

Wheel/sdist builds and content inspection confirmed version `0.6.0`, MIT Python
metadata, current README metadata and exclusion of Connector source/artifacts.
`uv lock --check` and `git diff --check` passed. The protected historical
worktree was read-only throughout A7; no commit, push, tag, release preparation
or version bump was performed.

### 40.15 v0.6.1 release-preparation evidence (2026-10-07)

Release preparation is performed in the current worktree on top of implementation
commit `b5f97adefac4c16f46a3383c92d768fefa8ac22b`
(`Implement v0.6.1 automatic Zotero Connector capture`). A7 final audit and
live acceptance remain the implementation evidence in §40.14. This preparation
does not create a release-preparation commit and does not tag, push or publish
v0.6.1.

The prepared release identities are all `0.6.1`: the Python package, the
OpenAlex and Crossref Provider User-Agent strings, and the generated Literature
Monitor Connector manifest. The Connector build entry point keeps the reviewed
upstream debug-build mode and supplies `0.6.1` through upstream `build.sh -v`;
the pinned upstream revision and submodule set are unchanged.

Direct release-preparation validation passed: Provider/version and Connector
boundary pytest coverage **431 passed**, Connector Node **36/36**, the A7 focused
Python suite **357 passed**, and full pytest **2506 passed**, with the same two
existing dependency deprecation warnings. The focused/full totals are one test
higher than A7 because release preparation adds one static regression for the
tracked Connector version source. The applicable upstream ItemSaver E2E command
was attempted again, but Mocha global setup stopped before test bodies because
Puppeteer requires Chrome `150.0.7871.24`, which is not installed. Those 17
tests therefore remain **NOT VERIFIED / existing environment limitation**; no
browser was installed to bypass the limitation.

A fresh repository-external Python build produced
`literature_monitor-0.6.1-py3-none-any.whl` and
`literature_monitor-0.6.1.tar.gz`. Wheel metadata reports version `0.6.1`
and MIT licensing. Wheel and sdist both contain the current README and all 61
current `literature_monitor` payload files byte-identically. Connector
source/artifacts, AGPL `COPYING`, `monitor.yaml`, `src/.obsidian/` and
retired Browser Companion paths are absent. A fresh external virtual
environment installed the built wheel from site-packages; key application/Web
imports, current templates/static assets, `literature-monitor --help` and
`uv pip check` passed, with no Connector payload present.

A separate fresh repository-external Connector build reconstructed exact
upstream revision `876e41ad15139077f2e07b2f71a0fa94742e0b4a` and the five
tracked top-level submodule pins. The generated artifact is named
`Literature Monitor Connector`, uses Manifest V3, reports version `0.6.1`
and uses `background-worker.js`. The runtime overlay and `COPYING` are
byte-identical to their tracked sources; the A7 corrected pre-dispatch versus
parent-acceptance `saveItems` markers are present; the Literature Monitor
runtime authority remains only `http://127.0.0.1:8000`; no Python payload is
present.

The intended later v0.6.1 release artifact set consists of three separate
deliverables: the Python wheel, the Python sdist and the independent generated
Chrome/MV3 Literature Monitor Connector artifact. The Connector must remain
outside wheel/sdist. No new ZIP or packaging mechanism is introduced by release
preparation; any publication packaging belongs to the later reviewed release
transaction.

This release-preparation diff changes version/status identity and directly
coupled assertions only. It does not change §40 runtime behavior, A7 automatic
capture semantics, Provider retrieval behavior, dependency membership or the
Python/Connector licensing boundary. No renewed live Chrome save is required
for this version-only Connector change. v0.6.1 remains **UNRELEASED**, and
v0.6.0 remains the latest released baseline.

### 40.16 v0.6.1 release and documentation closeout (2026-10-07)

The authorized release completed continuously in one conversation. The existing
implementation commit is `b5f97adefac4c16f46a3383c92d768fefa8ac22b`.
The scoped preparation commit, permanent RELEASE_HEAD and annotated `v0.6.1`
tag target are `22bd1efb6c3534110850dfaae5a6239f74b16731`; the annotated
tag object is `8e5bc8d5ead58f502d80d73065fe7e46d7a8f157`. Normal remote-main
and tag pushes were verified against those exact objects before publication.

[GitHub Release v0.6.1](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.6.1)
was published at `2026-10-07T13:20:44Z` as the public latest stable release
(non-draft, non-prerelease). The API readback confirms its tag, title, notes
and exactly three uploaded assets. Their authoritative GitHub SHA-256 digests
match the validated local artifacts:

| Asset | SHA-256 |
| --- | --- |
| `literature-monitor-connector-0.6.1-chrome-mv3.zip` | `e88c5c8fd24cc2f3e608d3e8cb91903fd59c4fb07da12faa9c454bbf2594b754` |
| `literature_monitor-0.6.1-py3-none-any.whl` | `71a8a1b2e3f05d838c54336e9a3ed216e0dc1b531f5e00b6cf8de2c6c3fb531e` |
| `literature_monitor-0.6.1.tar.gz` | `c82f42aa4edcc5764deb42c15f069a7325d655632368cc8953186b1dec5d5ca8` |

Validation reuse: §40.15's full pytest **2506 passed**, Connector Node **36/36**
and directly coupled version/preparation checks remain applicable. Subsequent
changes to the tested preparation inputs are documentation only; the Python
runtime, Connector patches/overlay/build entry point, dependencies and version
assertions were inspected and unchanged. §40.14's required scoped normal-Chrome,
Zotero Desktop and established institutional-session automatic save remains
live acceptance. These results were reused, not rerun during publication.
The optional pre-existing-parent live case and 17 upstream ItemSaver tests
remain **NOT VERIFIED** with their previously recorded scope/environment
limitations. No live capture or blocked upstream setup was repeated.

Checks run for this release: `uv lock --check`, version/identity consistency,
complete preparation-diff review and `git diff --check` passed. An exact clean
Git export of RELEASE_HEAD was built with `uv build --no-sources`; the final
wheel/sdist contain all **61/61** application payload files byte-identically,
version `0.6.1`, the current release-HEAD README and MIT license. Connector
source/artifacts, AGPL `COPYING`, protected local paths, retired Browser
Companion paths and cache noise are excluded. A fresh external Python 3.12.14
virtual environment installed the wheel with frozen exported runtime requirements
offline; site-packages imports, all packaged files, installed HTTP/template
rendering, localhost state endpoint, `literature-monitor --help` and
`uv pip check` passed. The HTTP smoke is not a new visual/browser live acceptance.

The previously validated external Connector build was reused after proving
exact upstream revision `876e41ad15139077f2e07b2f71a0fa94742e0b4a`, all five
top-level submodule pins, all four patched source files, overlays, license and
all **308** generated runtime files match the tracked release inputs and
retained upstream build. Its manifest is MV3, version `0.6.1`, component name
`Literature Monitor Connector`, with `background-worker.js`. The inspected ZIP
contains `chrome-mv3/`, `INSTALL.txt` and **2006** corresponding-source files:
the patched upstream source, initialized pinned top-level submodules and the
Literature Monitor source locks, patches, overlays and build instructions.
Archive readback, required executable modes and license/provenance separation
passed. Local installation uses Chrome Developer Mode / **Load unpacked**;
this publication does not claim Chrome Web Store or PyPI distribution.

There are no configured repository CI workflows or required remote checks;
GitHub reports zero check runs/status contexts, not a CI success. The published
source, Python artifacts, independent Connector artifact and GitHub Release
are verified separately. Build/validation output stays repository-external.
`monitor.yaml`, `src/.obsidian/`, `workspace/` and unrelated local state are
preserved, unstaged and excluded from published artifacts. The subsequent
necessary documentation closeout uses affected content/diff checks only and
keeps the release tag and assets fixed; it requires no new functional suite
or package rebuild.

---

## 41. v0.6.2 ISSN-L Venue Identity & Journals/Publishers Settings - Development Contract

### 41.1 Authority, baseline and delivery boundary

§41 is the v0.6.2 development authority for the configured venue identity, Journal/Publisher configuration, retrieval adaptation and Settings behavior it explicitly changes. It supersedes conflicting earlier ISSN/EISSN and Journal-name identity, import, Settings storage and Publisher access projection requirements, including the affected parts of §§25, 32, 34 and 39.7. Unaffected requirements continue to apply.

§40 remains authoritative for automatic Zotero Connector capture, its bridge, orchestration, completion observation and reconciliation. Other §39/§40 Zotero/Connector behavior is unchanged. §37 retains authority for DOI-first Paper identity and unaffected Provider/Paper semantics. §§35 through 40 remain historical version contracts and evidence; their text must not be rewritten to imply v0.6.2 behavior.

Development began from verified `main@8ab0630237753e0d56d26a06953576894a7d1106` on the normal `v0.6.2-development` branch. v0.6.1 was the released baseline at development start; v0.6.2 publication is now verified in §41.16. A1 established only this contract and aligned project rules, without product implementation, data migration or publication. A1 through A6 implementation and accepted migrations are now present; §41.13 records implementation evidence, §41.14 preserves the original A6 blockers and their closure, and §41.15 records the prepared A7 candidate. README describes released v0.6.2 and links the historical v0.6.1 interface. v0.6.2 adds no institutional-login automation.

### 41.2 Configured identity, Provider evidence and Source reconciliation

Venue identity has three separate layers:

| Layer | Ownership and permitted use |
| --- | --- |
| Durable configured identity | `JournalConfig.issn_l` is exactly one normalized, syntax/checksum-valid ISSN-L, owned by the user/configuration once established. It is the sole duplicate key, Candidate Eligibility configured venue key and value used in new/current Paper `journal_issns`. No Provider may silently replace it. |
| Provider identifier evidence | Individually normalized OpenAlex/Crossref ISSNs are transient evidence for Source membership, retrieval expansion, venue corroboration, legacy migration analysis and diagnostics. A Provider's canonical field label does not confer durable identity authority. |
| Reconciliation | Compare independently obtained evidence with configured identity or a migration candidate. Outcomes are verified/compatible, insufficient/unproven or conflicting/ambiguous. Preserve disagreements and do not guess across conflict; no persistent reconciliation database, alias registry or second source of truth is authorized. |

The target `JournalConfig` semantics are:

| Field | Ownership and meaning |
| --- | --- |
| `issn_l` | Sole durable configured Journal identity, supplied or explicitly established under the migration proof rules. |
| `name` | Machine-managed canonical OpenAlex display metadata; not user-editable. |
| `publisher_id` | Direct OpenAlex Publisher ID or null. |
| `group` | Human-managed optional Group. |

Exact Python field names may follow project style; these semantics are normative. Duplicate detection uses ISSN-L only. Different ISSN-L values with the same display name are valid distinct Journals. Journal names, alternate titles, abbreviations, Publisher names and hostnames cannot substitute for identifiers or act as fallback identity, merge authority or Source-resolution veto. Names cannot participate in duplicate validation, Provider matching, Candidate Eligibility identity or migration merging.

For an already configured v0.6.2 Journal, verify the OpenAlex relationship by direct identifier membership:

```text
configured ISSN-L
→ local syntax/checksum normalization
→ OpenAlex Source lookup by identifier
→ usable individually validated Source ISSN evidence
→ unique journal Source membership
→ verified configured Source
```

Success requires a locally valid configured ISSN-L, exactly one supporting Source of type `journal`, that identifier in the Source's usable normalized `issn` membership evidence, a valid OpenAlex Source ID and usable canonical `display_name`. Direct unambiguous membership is sufficient for the configured Source relationship. A configured-name/Source-name mismatch must never veto it. One queried identifier supported by multiple distinct active Sources is an explicit identity conflict.

OpenAlex `Source.issn_l` is Provider evidence only. It can corroborate the relationship, disagree with it or supply a legacy migration candidate (§41.10). A different, missing or malformed value may generate diagnostics, but cannot by itself invalidate otherwise unambiguous direct membership or replace configured `issn_l`. A canonical external-ID label in OpenAlex documentation does not change local ownership.

Validate every Source alias independently: normalize and retain each valid ISSN, exclude and diagnose each malformed/checksum-invalid value, then deduplicate normalized values. One bad additional alias must not poison valid aliases or the useful Source entity. For M&SOM, retain `1523-4614` and `1526-5498`, exclude/diagnose `1526-5489` and permit membership through the valid identifiers. Never invent a typo correction.

Fail closed when the configured/queried identifier itself is invalid or absent from usable membership, the Source is not a journal, its ID or required display metadata is unusable, multiple Sources support the identifier, or response structure prevents determining which Source owns it. Per-identifier tolerance does not excuse indeterminate ownership. From a verified Source, canonical display metadata and direct `host_organization` supply Journal metadata and Publisher association; usable aliases remain transient, never multiple configured identities.

Design benchmarks are the public [OpenAlex sources registry](https://github.com/ourresearch/openalex-sources#readme), which separates feed evidence, normalized membership, direct identifier reconciliation, conflicts and ISSN→ISSN-L mapping, and [OpenCitations Meta identifier cleaning](https://github.com/opencitations/oc_meta/blob/master/oc_meta/core/curator.py#L704-L737), which retains individually valid identifiers. These are architectural references, not code to copy or runtime dependencies. Literature Monitor does not adopt OpenAlex's guarded name matching, first-alias fallback or persistent registry machinery. The existing Crossref `_normalize_issns()` is the local precedent for independent identifier validation.

### 41.3 Provider retrieval and venue evidence

OpenAlex remains the primary discovery Provider; Crossref remains the secondary discovery and bibliographic-evidence Provider. Configured identity remains ISSN-L throughout retrieval. Resolve the OpenAlex Source once and share its verified membership/alias evidence; do not add a redundant Source lookup solely for Crossref. Retrieve OpenAlex Works by that Source, and seed Crossref journal/date requests with configured ISSN-L plus its usable Source aliases. Deduplicate all alias results by normalized DOI.

Crossref records/manifests may contain additional ISSNs. Apply existing `_normalize_issns()` policy individually: retain normalized valid identifiers, warn/ignore malformed ones and preserve other valid evidence. Valid Crossref identifiers tied to the already verified configured venue may supplement/corroborate the current transient retrieval evidence. They are not persisted into Journal configuration. Contradictory identifiers are retained/reported as disagreement, not automatically promoted into the usable query set.

```text
durable identity:
  configured ISSN-L
transient evidence:
  valid OpenAlex Source aliases
  + compatible valid Crossref ISSNs tied to current venue evidence
retrieval:
  bounded union of usable evidence
  → Crossref journal/date requests
  → normalized-DOI deduplication
never:
  transient alias → silent Journal identity mutation
```

Do not introduce an unbounded recursive alias crawl. If a later implementation demonstrates a recall need, it may use a deterministic, bounded second query expansion within the same verified configured-venue context, with DOI deduplication and no promotion of Crossref-only ISSNs to durable identity.

Crossref can independently corroborate that current works/journal metadata carry identifiers compatible with configured ISSN-L and OpenAlex membership. Agreement strengthens venue evidence. Crossref does not provide authoritative `arbitrary Crossref ISSN → canonical ISSN-L` mapping and cannot select or rewrite configured `issn_l`. Both Providers can contain incorrect metadata; neither a canonical label nor a single erroneous field changes long-lived identity.

Crossref availability is not a mandatory gate for every configured Journal Source lookup or identity-changing Settings Save. Preserve valid, uniquely supported OpenAlex membership when Crossref corroboration is unavailable. Available contradictory evidence must be preserved/reported without identity mutation or automatic query expansion; migration or identity-changing persistence remains conservative until the conflict is resolved.

When Source resolution is unavailable during normal retrieval, Crossref may conservatively query only configured ISSN-L; Journal-name fallback must not expand scope. This retrieval fallback does not authorize identity-changing Settings persistence without verified metadata (§41.8). Crossref records lacking usable ISSN evidence cannot obtain strong configured-venue authority from matching `container-title` alone. Preserve conservative disputed/fail-closed eligibility for insufficient evidence, Provider-local failure isolation, consolidation before local keyword filtering and unaffected §37 DOI/Provider behavior. Implementation may choose the clearest internal API preserving these requirements.

### 41.4 Candidate attribution, Papers and Workspace mapping

Candidate Eligibility uses canonical configured ISSN-L as its authoritative venue key. Existing plural fields retain their names and responsibilities:

```text
ProviderWorkEvidence.monitor_journal_issns: transient configured attribution
CanonicalPaper.journal_issns: current configured attribution
Paper journal_issns: durable configured attribution
```

Current/new attribution contains configured canonical ISSN-L values, not raw Provider aliases or Group names. Preserve §34's current-context union, materialization update/preservation and human-content ownership rules where unaffected. Attribution remains separate from DOI identity, Paper UUID, duplicate matching and workflow status.

For Workspace Group mapping, valid non-empty `journal_issns` matches configured ISSN-L: one matching Journal maps to its Group or Ungrouped; zero or multiple matching Journals are Unmapped, with no name fallback. Existing bounded compatibility for legacy missing/empty attribution may remain, but cannot become current identity authority. Malformed attribution retains its conservative Unmapped behavior. Different Journals sharing a display name cannot gain identity from that compatibility path.

Do not eagerly rewrite historical Paper Markdown when venue identity changes. Before any proposed migration, establish canonical ISSN-L under §41.10 and compare current durable Paper attribution under legacy configuration with proposed canonical configuration. Detect changes to unique Journal, Group, mapped/unmapped status and ambiguity status. Block migration before configuration writes if a currently uniquely mapped Paper becomes unmapped, ambiguous or mapped to another Journal or Group. Journal-name fallback cannot conceal an incompatibility.

A canonical ISSN-L absent from the old configured identifier set triggers explicit independent identity confirmation and Paper/Group compatibility proof; it is not a permanent migration failure. If canonical identity is independently established and durable Paper/Group mapping remains unchanged, absence alone does not block migration. This proof does not authorize eager Paper rewriting or a persistent alias registry.

### 41.5 CLI and Bulk Import

Retire exact Journal-name diagnostic selection `--journal <name>` and replace it with `--issn-l <ISSN-L>`. CLI `validate` remains. Change the flag spelling only if repository conventions demonstrate a materially better equivalent with the same identity semantics.

Preferred Bulk Import identity is explicit ISSN-L. Merge/Replace compares ISSN-L only, never normalized Journal name. Legacy `Journal + ISSN/EISSN` input is migration input only; its Journal name is a display hint. Normalize each legacy identifier and reconcile membership against one unambiguous current Journal under §41.10. OpenAlex can establish Source membership and supply an ISSN-L candidate; available Crossref current identifier evidence can corroborate it. Neither Provider alone supplies authoritative canonical selection. Different or ambiguous Source relationships conflict; do not merge by name.

If reliable ISSN-L cannot be established under the migration proof rules, require explicit confirmation/correction before Apply/Save accepts the identity. Persist no invented identity and select no ISSN-L automatically from Crossref.

Import Preview may remain local syntax/plan preview. Provider membership reconciliation and required independent identity confirmation belong to Apply/Save before persistence. Any invalid row or unresolved identity conflict blocks the whole Apply; no partial draft application is allowed. Preserve explicit Replace/removal preview, applicable Group semantics and the draft's original monitor/list revisions. Apply changes only the unsaved Settings draft, and Save remains the sole persistence boundary; Apply must not create a second write path or refresh revisions to bypass conflicts.

### 41.6 Shared portable list.md storage

`list.md` is the portable local-first source of truth for configured Journals and Publishers. The target tables are:

```markdown
## Journals

| Journal | ISSN-L | Publisher ID | Group |
| --- | --- | --- | --- |

## Publishers

| Publisher | OpenAlex ID | Access URL |
| --- | --- | --- |
```

Journal display name and direct Publisher association are machine-managed; ISSN-L is Journal identity; Group is human-managed. Publisher display name is machine-managed; OpenAlex ID is direct Publisher identity; optional Access URL is human-managed. Persist no additional configured print/electronic aliases.

Only Journals drive literature discovery. Preserve `## Conferences` and all unrelated sections exactly under existing storage semantics; they must not become discovery input. Opening Settings or upgrading the application does not by itself rewrite legacy Journal storage. `monitor.yaml` gains no Publisher field, and no third persistence file is added.

### 41.7 Direct Publishers and safe manual Access URLs

Publisher identity comes from each verified Journal Source's direct `host_organization`. Merge by direct OpenAlex Publisher ID only, never by Publisher name, hostname or parent/imprint lineage. Do not collapse an imprint into its corporate parent. Several Journals with the same direct Publisher ID produce one Publisher row. Removing the last associated configured Journal may remove that Publisher from active configuration.

For a new Publisher, resolve canonical `display_name` and `homepage_url` from OpenAlex Publisher metadata, batching metadata lookup where practical. A safe valid homepage may seed its initial Access URL; missing homepage yields blank, and an unsafe homepage must not become an initial clickable URL.

After Access URL is saved, it is user-managed, including an intentionally blank value. The user may enter, replace or clear it. Later metadata resolution must not silently overwrite or clear that saved value.

Blank Access URL is valid. A nonblank value must be a safe public HTTP/HTTPS URL. Reject credentials/userinfo, localhost, loopback/private IP targets, control characters and malformed URLs. Render an Open link only for a valid safe URL, using established external-link safety including `noopener`, `noreferrer` and no-referrer behavior where applicable.

Access URL is only a manual institutional-login shortcut. Its presence or use establishes no login/session status, entitlement or successful authentication. Add no cookie inspection, credentials, checked timestamps, login history or URL polling.

### 41.8 Settings validation, resolution and persistence

Journals and Publishers share one Settings form, one Save action, one `list.md` content revision and the existing monitor/list persistence boundary (§25.6). Preserve local complete-draft validation, both original revision/CAS checks, safe writes, established two-file write order and truthful partial-save reporting.

The Save sequence is:

```text
edit Settings
→ local validation
→ determine whether Journal identity requires metadata resolution
→ resolve all required OpenAlex Journal/Publisher metadata before first disk write
→ reread both current files and check original content revisions
→ prepare complete list.md and monitor.yaml targets
→ preserve compare-before-replace/CAS checks at the write boundary
→ write list.md, then monitor.yaml, through existing persistence
→ reread actual disk state and report the result
```

For a new or changed v0.6.2 Journal, the user/configuration supplies the intended ISSN-L. Save verifies that value through local syntax/checksum validation, one unambiguous OpenAlex journal Source supporting it, usable membership containing it, valid Source ID and canonical display metadata (§41.2). OpenAlex `issn_l` never replaces the submitted value. Legacy identities follow §41.10 confirmation/proof before persistence. Available Crossref corroboration may strengthen diagnostics; do not add a mandatory Crossref Save dependency without a later implementation task demonstrating that need. Unresolved contradictory evidence remains a persistence conflict.

Metadata resolution is required only for identity-changing cases: legacy ISSN/EISSN-to-ISSN-L migration, a new Journal, a changed ISSN-L or legacy import canonicalization. Ordinary saves remain network-independent, including Group-only, Publisher Access URL-only, keyword-expression, date-policy, output-directory and other changes that leave Journal identity unchanged. Those saves perform no OpenAlex request and must not become a metadata refresh.

If OpenAlex fails or identity cannot be established during identity-changing Save/Apply, fail before any settings-file write; neither `list.md` nor `monitor.yaml` may be partially mutated. Once complete targets have been prepared and writes begin, preserve existing two-file partial-save semantics: a list write failure prevents the monitor write; a successful list write followed by monitor failure reports partial save and real disk state, never success. Do not add a third file, cross-file transaction framework or rollback subsystem.

### 41.9 Settings presentation and removed Web actions

The shared form presents `Journals & Publishers`. Journals rows expose read-only canonical Journal name, ISSN-L, Group and Remove. Publishers rows expose read-only canonical Publisher name, editable Access URL and Open link only for a valid safe URL. Bulk Import and Group organization remain within the Journals subsection.

Use consistent row/grid presentation for both lists. Desktop lists use bounded scrolling consistent with existing Journals behavior; mobile retains readable single-column rows and avoids inappropriate nested scrolling.

Remove the separate Publisher access panel and Web `/settings/publisher-access` projection route. Remove the Settings Validate button and Web `/settings/validate` route. Retain application `validate_settings()`, internal `save_settings()` validation and CLI `validate`. Preserve unaffected Settings/Web security and §40 Connector readiness/capture behavior.

### 41.10 Real tracked migration, reconciliation and evidence boundary

The pre-migration tracked `list.md` contained 74 legacy `Journal + ISSN/EISSN` rows whose identifiers were legacy evidence rather than explicit durable ISSN-L. A2_FIX_1 subsequently proved all 74 target identities SAFE; A3 persisted the Journal migration and A4 persisted 20 direct Publishers. The current tables and unchanged Paper/Group mappings were read-only rechecked in A6 (§41.13). Reviewed deterministic proof must use real verified metadata, not synthetic-only fixtures. The earlier supplied OpenAlex HTTP 429 planning evidence established neither resolved identities nor successful migration.

```text
legacy ISSN/EISSN set
→ normalize each identifier
→ OpenAlex Source membership + available Crossref valid current journal/work ISSNs
→ reconcile whether identifiers describe one current Journal
→ ISSN-L candidate/evidence
→ establish durable ISSN-L only under an authorized proof rule
```

OpenAlex `Source.issn_l` may supply a candidate and Crossref may corroborate membership. Neither alone supplies authoritative ISSN→ISSN-L mapping. All relevant valid legacy identifiers must be compatible with one unambiguous journal Source/current venue; indeterminate or contradictory evidence remains unproven/conflicting. Retain valid evidence while diagnosing invalid identifiers; never invent corrections or silently drop unresolved identity-bearing input to force success.

A narrow automatic migration may accept the OpenAlex candidate only when all of these hold:

- All relevant valid legacy identifiers resolve compatibly to one unambiguous journal Source.
- Candidate ISSN-L passes syntax/checksum validation, belongs to the Source's usable identifier set and is already in the legacy configured identifier set.
- No available Crossref evidence contradicts the venue relationship; unavailable corroboration is not fabricated as agreement.
- No other legacy row resolves to the same target identity.
- Historical Paper/Group compatibility is proven safe under §41.4.

This accepts a strongly corroborated existing identifier; it grants no global authority to OpenAlex `issn_l`. A known unresolved disagreement cannot enter this automatic safe case, even when the candidate is an old identifier.

A proposed ISSN-L absent from the old set must not be persisted solely because OpenAlex returns it. Require explicit independent confirmation: reviewed one-time authoritative ISSN Registry evidence may establish the tracked repository's canonical identity; generic user migration requires explicit confirmation/correction rather than guessing. Available Crossref current-work ISSNs can corroborate one venue but cannot choose canonical ISSN-L. Do not select canonical identity by Crossref frequency, print/electronic labels, Journal title, Provider ordering, first alias or OpenAlex `issn_l` alone. Independently established identities still require unique Source membership, no target collision, resolution of contradictory evidence and the Paper/Group compatibility gate. Absence alone is not a permanent failure after those proofs pass.

#### Historical A2 exceptional identity evidence (Revision 2)

The original A2 real-data probe exposed these four cases. At the Revision 2 documentation-only correction, the supplied Registry conclusions below had not been independently re-verified and required review before configuration writes. A2_FIX_1 later re-verified the exceptional decisions and produced the accepted proof recorded in §41.13. The historical table retains the original A2 evidence; title-history conflicts must be resolved by reviewed identifiers, never runtime name matching.

| Journal (presentation label) | Legacy identifiers | A2 OpenAlex evidence | Supplied independent Registry conclusion and implication |
| --- | --- | --- | --- |
| Optimization Methods & Software | `1055-6788` | Candidate `1026-7670` | ISSN-L `1026-7670`; a legitimate canonical identity can be absent from the old set and requires confirmation/proof. |
| INFORMS Journal on Computing | `1091-9856` | Candidate `0899-1499` | Current-title ISSN-L `1091-9856`; `0899-1499` belongs to predecessor ORSA Journal on Computing. Source aggregation across title history cannot choose the current configured identity. |
| Genome Biology | `1474-760X` | Candidate `1465-6906` | Current-title ISSN-L `1474-7596`; `1465-6906` is associated with earlier GenomeBiology.com. Apply the same title-history constraint and independent confirmation. |
| Manufacturing & Service Operations Management | `1523-4614`, `1526-5498` | Source also contains invalid `1526-5489` | ISSN-L `1523-4614`; diagnose/exclude the invalid alias while retaining valid membership evidence. |

#### Historical A2 Paper evidence and completion limit

Supplied A2 workspace evidence reports 30 Paper Markdown files, 30 valid `journal_issns` attribution states and 11 Papers with multiple ISSNs. The supplied read-only simulation excluded the malformed M&SOM alias while retaining valid Source evidence and produced 30 unique current mappings, 30 unique proposed mappings, 30 unchanged Journal/Group mappings and zero demonstrated mapping changes. This is simulation evidence, not final migration acceptance; it does not settle exceptional canonical ISSN-L choices. That Revision 2 documentation task ran neither the simulation nor a live Provider probe.

The preserved A2 implementation/probe's strict resolution rules remain distinct from that supplied simulation: its report classified 70 rows safe, three requiring compatibility treatment and one invalid Source, and left all 30 proposed Paper mappings unproven. Revision 2 changes the contract, not that implementation or report. Corrected parsing/reconciliation belongs to `V0_6_2_A2_ISSN_L_RESOLUTION_MIGRATION_PROOF_FIX_1` after independent review of this correction.

Before configuration writes, independently re-verify exceptional canonical decisions, reconcile every row, prove safe historical mapping and inspect the reviewed migration diff. Any unresolved identity conflict, target collision or unsafe Paper/Group mapping blocks the tracked migration. At A1/Revision 2 the 74-row migration was not complete, and read-only A2 evidence did not constitute accepted rewritten data. Subsequent A2_FIX_1, A3 and A4 completed proof and tracked migration; current evidence is in §41.13. These later results do not retroactively change the original blocked A2 report.

### 41.11 Explicit non-goals

v0.6.2 does not add:

- automatic institutional login, credential storage, cookie/session inspection, login/session status detection or entitlement checks;
- CARSI, Smart Gateway, WebVPN or XMU login automation; publisher-specific login DOM adapters or login-expiry diagnostics;
- Publisher URL reachability polling, Publisher login history or automatic Publisher merging by name/hostname/lineage;
- a persistent OpenAlex metadata database/cache, Journal identity SQLite database, reconciliation database, alias registry or global metadata refresh scheduler;
- ISSN Portal API, ISSN.org scraping, Registry credentials/subscriptions, a local 2.6M-row ISSN→ISSN-L mirror, periodic mapping refresh or any new persistent identifier registry;
- conference monitoring, PDF attachment-readiness changes, Zotero collection routing, changes to the v0.6.1 Connector capture protocol or new Paper workflow states;
- unrelated Provider/canonicalization refactoring or restoration of retired v0.5.x acquisition/session machinery.

Reviewed one-time authoritative Registry evidence for the tracked 74-row migration is release/migration verification data, not a new production Provider or mandatory runtime dependency. Production remains OpenAlex + Crossref.

### 41.12 Required implementation acceptance and release boundary

The implementation must demonstrate all of the following. These remain acceptance requirements, not A1 test results; §41.13 records implementation evidence and §41.14 identifies remaining gaps. Passing existing tests does not waive these requirements:

- Exactly one user/configuration-owned durable ISSN-L per Journal, ISSN-L-only duplicate detection, distinct Journals with duplicate display names allowed and read-only canonical names. Providers cannot silently replace configured identity. Regressions cover name mismatch with valid identity and duplicate display names with different ISSN-L values.
- One unambiguous journal Source directly supporting configured ISSN-L through usable membership, valid Source ID and display metadata. Independently validate/deduplicate aliases; unrelated malformed aliases and different/missing/malformed OpenAlex `issn_l` do not alone invalidate valid membership. Cover invalid queried IDs, absent membership, non-journal Sources, invalid Source IDs, structural ownership uncertainty and multiple-Source conflicts.
- Shared Source resolution, bounded transient OpenAlex/Crossref retrieval evidence, normalized-DOI deduplication and explicit compatible/unproven/conflicting reconciliation. Crossref corroborates membership but cannot select ISSN-L; contradictory identifiers are reported, not automatically queried. No unbounded alias crawl, durable alias registry or strong name-only Crossref venue authority. Resolution failure preserves the bounded configured-ISSN-L fallback, Provider failure isolation and conservative eligibility.
- ISSN-L Candidate Eligibility and new/current Paper attribution through the existing plural fields, conservative Workspace mapping and no eager historical Paper rewrite. Human notes, unknown frontmatter, status and Zotero linkage remain preserved.
- Reviewed real-metadata migration of all 74 tracked rows, with the narrow existing-identifier automatic rule, independent confirmation/correction for absent-set identities, re-verification of all four exceptional cases, no target collisions and historical Paper/Group compatibility. Canonical absence alone does not permanently block a proven safe migration; unresolved identity conflicts or unsafe mapping prevent writes. No eager Paper rewrite or external Registry runtime dependency.
- Publishers persisted in shared `list.md`, one row per direct Publisher ID, no name/hostname/lineage merging, safe homepage seeding and preservation of manually entered or cleared URLs. Regressions cover one Publisher shared by several Journals and missing homepage followed by manual URL entry.
- Safe URL acceptance/rejection and external-link behavior; Conferences and unrelated content preserved; monitor YAML schema unchanged.
- One shared Settings Save/revision/CAS boundary. Ordinary saves remain network-independent; identity-changing Save verifies submitted ISSN-L and resolves required metadata before the first persistence write, without silently substituting OpenAlex `issn_l` or imposing mandatory Crossref availability. Failed identity establishment or unresolved conflict causes zero settings mutation. Regressions cover network independence, fail-before-write, concurrent edits and existing two-file partial-save reporting.
- Explicit ISSN-L import and ISSN-L-only Merge/Replace; legacy import uses reconciled evidence and confirmation rules, with no name merging or Crossref-derived automatic ISSN-L selection. Whole-Apply conflict handling, original draft revisions and Group organization remain preserved.
- Unified desktop/mobile Settings lists; separate Publisher projection and Web Validate action/routes removed while application/internal validation and CLI `validate` remain.
- v0.6.1 Connector protocol, automatic capture, exact-DOI reconciliation and manual-session boundaries remain unchanged.
- Relevant tests cover migration, OpenAlex, Crossref, Candidate Eligibility, Workspace, Import, Settings and Web. Before release, run full pytest, applicable Node Settings/UI harnesses, `uv lock --check`, `git diff --check` and normal package/build verification. Preserve applicable §40 Connector acceptance and report actual verification scope.
- Package and Provider version identities change to `0.6.2` only during normal release preparation. A1 changes no package/Provider/Connector version, production code, tests, configuration data or protected local state, and runs no live Provider migration probe.

### 41.13 A1 through A5 implementation and migration evidence (2026-10-08)

Current development HEAD is `8ab0630237753e0d56d26a06953576894a7d1106` on `v0.6.2-development`; A1 through A5 changes remain uncommitted. This record separates real external evidence, deterministic replay, automated checks and current read-only data verification. It does not assert v0.6.2 publication or complete acceptance.

| Phase | Implemented or verified scope | Evidence boundary |
| --- | --- | --- |
| A1 | §41 identity, storage, migration and Settings contract; stable project rules. | Contract only at that phase, not implementation or migration. |
| Original A2 | Real Source probe under the original strict rules: 70 safe rows, three requiring compatibility treatment and one invalid Source; all 30 proposed Paper mappings unproven. | BLOCKED historical report. Revision 2 supplied simulation did not close it. |
| A2_FIX_1 | Real OpenAlex Source response and independently reviewed exceptional canonical decisions; 74 SAFE targets: 71 automatic existing-identifier cases and three independently confirmed cases. No collisions, conflicts or unresolved targets. All 30 Papers retain the same unique Journal/Group mapping; 11 have multiple stored ISSNs. | Accepted read-only proof (`report.json`). Crossref was NOT_QUERIED; no positive Crossref corroboration is claimed. Recorded-response replay (`final-code-replay.json`) reproduced the 74/30 result without new network calls. |
| A3 and A3_FIX_1 | Persisted 74 canonical Journal names, one durable ISSN-L each, direct Publisher associations and original Groups. Adapted Source membership, shared retrieval evidence, CLI selection, Candidate Eligibility, current Paper attribution and conservative Workspace mapping. | Migration/preservation evidence and network-independent regression coverage. Historical Papers were not proactively rewritten. |
| A4 | Persisted 20 direct Publisher IDs/names from a real OpenAlex Publisher batch, matching 73 Journal associations. Seeded 19 accepted initial homepage URLs; one Publisher has a blank URL. Shared local-first Settings resolution, offline ordinary saves, URL ownership and revision checks implemented. | Real Publisher probe (`publisher-probe.json`) and preservation checks. No hostname/name/lineage merge, login check or credential/session inspection. No new Crossref migration probe. |
| A5 | Unified Journals & Publishers editor, read-only canonical metadata, Pending new Journals, editable/clearable Access URLs, Group controls and one Save. Separate Publisher projection and Web Validate actions/routes removed; application/internal validation and CLI validate retained. | Python Web tests and Node Settings/UI harnesses passed. Real desktop/mobile browser layout and interactions were not verified. A6 found gaps below. |

A6 read-only comparison used the accepted A2_FIX_1 row tuples (canonical display name, durable ISSN-L, direct Publisher ID and Group) and A4 Publisher tuples (display name, direct ID and initial Access URL). All 74 Journal rows and 20 Publisher rows match. There are 74 unique Journal identities and 20 unique Publisher identities, 73 associations, 19 populated initial URLs and one blank. The Conferences/unrelated tail matches the pre-migration tracked content exactly. Workspace loads 30 Papers without issues; every Paper retains the accepted unique Journal/Group classification, including Ungrouped where appropriate. Paper bytes and protected state remain unchanged.

Retrieval review found no additional substantive defect in shared once-per-run Source resolution, independently validated aliases, bounded Crossref reconciliation/fallback, normalized-DOI consolidation, configured-ISSN-L Candidate Eligibility/current attribution or conservative historical mapping. This is code review plus existing automated coverage, not a new live multi-Provider Run. §40 Connector implementation/protocol and Connector source/artifacts are unchanged; relevant capture/reconciliation regressions remain covered by the reused suite. Existing §40 live evidence keeps its original scope and is not a fresh Chrome/Zotero acceptance run.

Validation reused from A5 (macOS, Python 3.12.14, Node v24.21.0): `uv run pytest` completed with **2665 passed**, including applicable Node Settings/UI harnesses, and two existing dependency deprecation warnings. A6 changed only README/SPEC; production source, test behavior, dependencies and Connector inputs are unchanged, so this result remains applicable under AGENTS evidence-reuse rules. A6 additionally ran offline adversarial probes against isolated temporary configurations; they exposed the five defects in §41.14 despite the passing existing suite. No real user configuration was written and no external Publisher URL was opened by these probes.

A6 ran `uv lock --check` successfully (`Resolved 27 packages`) and checked README commands against the actual root/subcommand CLI help, including `--issn-l` on the six Journal-selecting diagnostics and absence of `--journal`. `git diff --check` passed after documentation edits. Package and OpenAlex/Crossref User-Agent identities remain `0.6.1`; no version bump, build, tag, push or Release was performed. A7 must perform its own authorized release preparation and distribution verification after A6 blockers close.

### 41.14 A6 integration audit and product acceptance (2026-10-08)

**Original A6 result: STATE: BLOCKED. RELEASE_READY: no. NEXT: A6_FIX.** A6 inspected the full task-relevant A1 through A5 changes, current callers/tests and tracked migration evidence using AGENTS and the parent Desktop `REVIEW_WORKFLOW.md`. That audit changed documentation only and observed the following unfixed findings at its source snapshot. The table and probe evidence remain the historical audit record; subsequent A6_FIX_1 closure is recorded below. The acceptance/identity contract above is unchanged.

| Finding | Directly observed behavior and impact | Bounded correction and required regression |
| --- | --- | --- |
| F1 / P1: Unicode Access URL host bypass | `src/literature_monitor/url_safety.py:25–39` checks localhost/private IP before IDNA normalization. `http://127。0。0。1`, `http://192。168。1。1` and `http://１０。０。０。１` pass shared validation/PublisherConfig, while Node's WHATWG URL parser resolves them to loopback/private IPs. An isolated ordinary Save also persists a bypass URL and exposes Open. This violates §41.7's local/non-public target rejection. | Normalize the host before the existing localhost/IP/numeric-host rejection, retaining offline validation and legitimate public IDN support. Cover validator, PublisherConfig and Web Save/Open; add no DNS, reachability or login probe. |
| F4 / P1: damaged Journal storage cannot be repaired | `src/literature_monitor/application/settings.py:768` strictly parses persisted Journals before accepting a corrected draft. A recoverable Settings page for malformed/missing Journal table still rejects a valid replacement with “Journals table is missing or empty”; no Provider calls or writes occur. This regresses unaffected §25.8 recovery. | Permit explicit damaged-storage repair while preserving readable metadata authority, unrelated sections and original revisions/CAS. Test missing/malformed storage, successful repair and zero-write failure; do not ignore parser errors globally. |
| F2 / P2: display hint vetoes same-ISSN-L import | `src/literature_monitor/application/journal_import.py:230` treats differing imported names as conflicting row metadata. Two rows with `0006-341X`, the same Group and different display hints block the whole Apply. This violates ISSN-L-only duplicate handling (§§41.2, 41.5). | Remove the display-name veto while retaining genuine Group-conflict handling and authoritative existing metadata. Test same identity/different hints, distinct identities/same name and conflicting Groups. |
| F3 / P2: legacy Web Import has no completion path | `application/journal_import.py:253–256` returns legacy previews with `MIGRATION_REQUIRED` and `can_apply=false`; `web/templates/fragments/settings_editor.html:212` hides Apply, and `web/app.py:555` supplies no migration confirmations (all beneath `src/literature_monitor/`). Valid legacy import therefore cannot reach the backend migration resolver or confirmed identity Apply. | Connect legacy Preview/reconciliation/confirmation to whole-draft Apply using the existing migration rules. Keep ambiguous/conflicting input blocked, preserve original revisions and unsaved values, and retain Save as the only disk-write action. Cover automatic-safe and explicitly confirmed legacy input through Web endpoints. |
| F5 / P2: remove/reintroduce saved identity fails Save | Removing a saved Journal from the draft, then importing that same ISSN-L, produces a Pending row with a hint and empty Publisher ID. Apply succeeds, but Save rejects machine-managed metadata against the still-persisted row. Code inspection shows Add constructs the same Pending metadata shape; the persisted-metadata rejection is at `src/literature_monitor/application/settings.py:778–780`. | Restore persisted canonical metadata for a reintroduced saved identity while retaining metadata-tampering rejection and offline ordinary Save. Cover Remove → Import/Add → Save, retained Group/URL edits, revisions and zero-write failure. |

Primary offline probes used application import and isolated FastAPI TestClient requests with Provider calls disabled where ordinary Save must be offline. All four failure-path reproductions retained settings bytes; URL normalization was compared with Node's actual URL parser without requesting any target. These probes establish observable defects; they are not browser visual acceptance and do not amend the passing A5 test record.

Real desktop/mobile smoke is **UNVERIFIED**. The browser-control inventory call timed out after 10 seconds; prior A5 attempts were also unavailable. No write-based smoke was run on the user's real configuration. Automated CSS/template/DOM assertions cannot prove actual large-list independent scrolling, narrow-screen stacking, focus/interaction, HTMX visual restoration or rendered success/failure feedback. The requested isolated 74-Journal/20-Publisher desktop/mobile smoke remains a release gate.

The original A6 next step was a separately authorized bounded fix of all five findings, followed by real desktop/mobile acceptance. A6_FIX_1 below closes the code findings; browser acceptance remains required before recommending A7. v0.6.1 remains the latest RELEASED baseline; v0.6.2 preparation, publication and new live Connector verification have not been performed.

#### A6_FIX_1 closure and verification (2026-10-08)

**CODE_FINDINGS: CLOSED. BROWSER_ACCEPTANCE: UNVERIFIED. RELEASE_READY: NO. NEXT: BROWSER_ACCEPTANCE.** Authorized fixes retain the original A1 through A6 changes and the §41 identity/persistence contract. HEAD remains `8ab0630237753e0d56d26a06953576894a7d1106` on `v0.6.2-development`; all changes remain uncommitted and the index is empty.

| Finding | Closure and observable regression coverage |
| --- | --- |
| F1 / P1 | Shared server validation performs IDNA/Unicode host normalization before localhost, literal-IP and numeric-host checks. Unicode dots, fullwidth digits and other normalized non-public targets are rejected by the validator, PublisherConfig and Web Save/Open; legitimate public IDNs remain accepted. Python results are compared with Node's actual WHATWG URL parser. No DNS, reachability or login probe was added. |
| F4 / P1 | Settings distinguishes canonical, legacy, recoverable damaged and unsafe storage. An explicit valid repair draft can replace missing/malformed Journals while preserving readable Publishers and unrelated sections. Individually verifiable canonical rows retain metadata authority; untrusted rows require existing OpenAlex resolution. Unassociated persisted Publishers cannot be silently lost during repair. Provider/metadata failure, ambiguous duplicate sections, unsafe paths, symlinks, unreadable input and revision conflicts block writes. List-first CAS and partial-save semantics remain intact. |
| F2 / P2 | Duplicate import identity uses ISSN-L alone. Compatible Groups merge despite different display hints; the first hint is deterministic for a new identity, whose final metadata still comes from OpenAlex. Existing canonical metadata is retained. Genuine Group conflicts block the whole Apply, and different ISSN-L values with the same name coexist. MERGE/REPLACE and source-row diagnostics are covered. |
| F3 / P2 | Local legacy Preview exposes identifiers, Groups and confirmation inputs, with a Reconcile and Apply path into the existing migration engine. All rows must resolve automatically or pass independent explicit confirmation; Provider failures, conflicting/ambiguous evidence, invalid confirmations and target collisions block the whole Apply without writes. Import confirmation fields are separate from persisted-storage migration fields and bound to the exact source text and source-row sequence. Edited sources invalidate stale confirmations. HTMX retains unsaved fields, URL edits/clears, revisions and valid confirmation inputs after a failed attempt. Save remains the sole persistence action. |
| F5 / P2 | Save matches Pending reintroductions against persisted canonical ISSN-L rows and restores their canonical name and Publisher ID without Provider requests. Remove/Add and Remove/Import retain draft Group and Publisher URL edits/clears. Ordinary forged metadata and fake Publisher associations remain rejected; unfamiliar identities still require resolution, and revision conflicts remain blocked. |

`tests/test_a6_fix1.py` adds **56 finding-specific regression cases**, including actual Node URL parsing and the existing Settings DOM harness for edited import confirmations and HTMX restoration. Each finding's independent regression first reproduced the failure and then passed after its fix. Existing Import/Web regressions now test genuine Group conflicts rather than treating display-name differences as identity conflicts; invalid legacy inputs remain whole-Apply blocked.

Associated URL/Settings/Import/migration/A3/A4/A5/Workspace/Candidate/OpenAlex/Crossref coverage passed **1187 tests** before the final additional Node regression; that Node case also passed independently. Final `uv run pytest` passed **2720 tests**, with two existing dependency deprecation warnings, in 9.00 seconds on macOS/Python 3.12.14/Node v24.21.0. Applicable Node Settings/UI harnesses ran within pytest; these are automated assertions, not real browser acceptance. `uv lock --check` passed (`Resolved 27 packages`), and `git diff --check` passed after the final documentation edits. The complete FIX diff was inspected.

Read-only preservation checks matched all **74 Journal** tuples to accepted A2_FIX_1 evidence and all **20 Publisher** tuples to accepted A4 evidence. All **30 Papers** retained their unique Journal/Group mapping and exact bytes. The **177 protected files** under `monitor.yaml`, `src/.obsidian/` and `workspace/` matched the pre-fix snapshot. Tracked `list.md`, AGENTS stable rules, dependencies, §40 Connector source/artifacts and package/Provider User-Agent version `0.6.1` are unchanged. SPEC §§1 through 40, including §40.16, match the pre-fix text exactly. No real settings or workspace writes, commit, build, tag, push, Release or A7 operation occurred.

The FIX_1 real-browser inventory attempt, `cua.getState()`, timed out after 10 seconds. **Desktop and mobile acceptance remain UNVERIFIED**: no isolated 74-Journal/20-Publisher rendered smoke could be performed. Independent scrolling, mobile page flow, focus/interactions and rendered success/failure/conflict feedback still require real browser acceptance. This environment blocker does not reopen F1 through F5, but it prevents a release-ready claim.

Audit/rollback boundary: the original findings above, isolated before/after regressions and final verification record identify the changes. Rollback would remove only the FIX_1 named-file hunks while retaining prior A1 through A6 work; no real configuration/Paper rollback or data migration is needed because the fixes were exercised only on isolated fixtures. No unrelated refactor, new Provider, persistent alias registry, runtime ISSN Portal dependency or frontend framework was introduced.

#### A6 browser acceptance closeout (2026-10-08)

A subsequent acceptance session succeeded using the AgentDock desktop skill and real macOS Google Chrome, after the earlier A6_FIX_1 browser-control timeout. The original A6 **BLOCKED** findings and the FIX_1 **UNVERIFIED** record above remain accurate descriptions of their respective earlier checkpoints; this closeout supersedes only the current browser-gate status. F1–F5 code findings were already closed by A6_FIX_1. This session did not change the §41 identity or persistence contract.

```yaml
CODE_FINDINGS: CLOSED
BROWSER_DESKTOP: PASS
BROWSER_RESPONSIVE_550PX: PASS
PHYSICAL_MOBILE: NOT_TESTED
A6_BROWSER_GATE: CLOSED
A6_FINAL_REVIEW: PENDING
A7: NOT_STARTED
V0_6_2_RELEASED: NO
```

**Isolated configuration and real rendering.** Chrome visited `http://127.0.0.1:8767/settings` against the isolated test service. The service used `/private/tmp/literature-monitor-browser-repair-20261008/smoke/monitor.yaml` and `/private/tmp/literature-monitor-browser-repair-20261008/smoke/list.md`, never the user's active configuration. At **1470px content width**, the real page rendered **74 Journals**, **20 Publishers** and **19 Open links**. Journal name, ISSN-L, Group and Remove, plus Publisher name, Access URL and Open, were displayed and usable. The two lists had separate bounded scrolling and could scroll independently; the page had no horizontal overflow. **Desktop Chrome: PASS.**

At **550px macOS Chrome window width**, both Journal and Publisher rows stacked in a single column. Internal list scrolling was disabled in favor of normal page scrolling; long-list entries, Group, Remove, Access URL and Open remained readable and there was no horizontal overflow. **Responsive Chrome: PASS.** This was a resized desktop-browser window, **not** physical phone/touch acceptance.

**Real-browser interaction results (all PASS; all writes confined to the isolated files):**

1. **Unicode URL safety:** Entering `http://127。0。0。1` hid the Open link. Save rejected the value while preserving the draft input and showing an error; both isolated files remained byte-identical.
2. **Import Preview → Apply:** A Group change for the same ISSN-L `0090-5364` applied correctly to the unsaved draft. The Publisher Access URL draft and original revision survived Apply; no file write occurred before Save.
3. **Successful Save:** The Group became `Statistics · T0.5` and the Publisher Access URL became `https://a6-isolated.example/access`. Chrome showed success; both values persisted to the isolated `list.md`, and the dirty indicator cleared.
4. **Invalid new Journal:** Adding Pending Journal `NOT-ISSN` caused Save to reject the draft and retain the input/error feedback, with no file mutation.
5. **Revision conflict:** Two Chrome tabs simulated concurrent Settings edits. Save with the stale original revision was rejected, preserving the attempted draft and the newer disk content.
6. **HTMX Preview restoration:** The Journal list's `scrollTop=1630`, Publisher list's `scrollTop=680`, and the expanded Import and Groups sections survived the partial replacement.
7. **Remove → Add → Save:** Removing a persisted Journal and adding back ISSN-L `0090-5364` succeeded. Unique identity, canonical Journal name, Publisher ID and Group were restored correctly on Save, with no residual Pending state.

**Verification provenance and limits.** `tests/test_a6_fix1.py` was independently rerun with **56 passed**. The **2720 passed** full `uv run pytest` suite, including Node Settings/UI harnesses and two existing dependency deprecation warnings, is **prior Codex evidence** from A6_FIX_1, not a suite run repeated during this real-browser session or the documentation closeout. No new live OpenAlex/Crossref end-to-end Provider Run or physical mobile/touch test was performed. The §40 Connector live evidence retains its historical scope; this session does not claim new Connector acceptance.

The user's `monitor.yaml`, `src/.obsidian/`, `workspace/` and Paper Markdown were not used for browser writes; their protection checks remained unchanged. `list.md` retains 74 Journal and 20 Publisher rows. The document-only closeout preserves SPEC §§1–40 (including §40.16), the original A6 audit and FIX_1 timeout evidence, and package/Provider User-Agent version `0.6.1`. The Git index remains empty, and no commit, build, tag, push, GitHub Release or A7 preparation was performed. **A6_BROWSER_GATE is CLOSED; A6 is ready for final independent documentation/Git review, which is still PENDING.** v0.6.2 remains **UNRELEASED**; no v0.6.2 distribution artifact has been verified.


### 41.15 A7 release preparation (2026-10-08)

**A6_FINAL_REVIEW: PASSED. A7_RELEASE_PREPARATION: PREPARED. v0.6.2: UNRELEASED.**
The authorized A7 baseline confirms that the final independent A1–A6 review
passed. This current status supersedes the pending checkpoint in §41.14, while
preserving its original BLOCKED audit, FIX_1 browser timeout and subsequent
real-Chrome acceptance records. v0.6.1 remains the latest published release
until publication verification.

Preparation operates on accepted uncommitted A1–A6 inputs at
`8ab0630237753e0d56d26a06953576894a7d1106` on `v0.6.2-development`.
Python metadata, OpenAlex/Crossref User-Agent strings and the independent
Connector manifest target `0.6.2`. Upstream revision, all five submodule pins,
Connector patches/overlay and §40 protocol are unchanged. The Git index remains
empty; commit, tag, push and GitHub Release are outside this task.

Preflight verified local `main`, `origin/main` and development HEAD at that same
baseline; live `origin/main` also matched. The index was empty, no local/remote
`v0.6.2` tag existed, GitHub authentication was active, and the tag Release API
returned HTTP 404. The latest public stable Release was `v0.6.1`. There is no
branch/history/publication conflict. These were read-only remote checks.

**Direct A7 validation (macOS, Python 3.12.14, Node v24.21.0, uv 0.12.19).**
The isolated current-working-tree candidate includes the two accepted untracked
modules (`application/journal_migration.py`, `url_safety.py`) and all six accepted
untracked test files, rather than exporting the old HEAD alone. Version/header
and boundary tests passed across these focused commands:

- `uv run --locked pytest tests/test_crossref.py::test_client_uses_versioned_encoded_doi_endpoint_and_polite_headers tests/test_crossref.py -k 'versioned_encoded or redirect' tests/test_openalex.py::test_canonical_singleton_checks_only_configured_identity_with_bearer_key tests/test_connector_boundary.py`: **20 passed, 190 deselected**. The global `-k` selection excludes the OpenAlex and boundary cases, which the next command exercises.
- `uv run --locked pytest tests/test_openalex.py::test_canonical_singleton_checks_only_configured_identity_with_bearer_key tests/test_connector_boundary.py`: **13 passed**.
- `uv run --locked pytest tests/test_crossref.py::test_a5_manifest_and_doi_batches_use_repeated_filters_and_same_pool`: **1 passed**.
- `./connector/test.sh`: **36/36 passed**; `uv lock --check`: **Resolved 27 packages**; `git diff --check`: passed. Package metadata, lock project version and Provider/build/assertion references are consistently `0.6.2`.

A7 changes only release identities, their direct assertions and release documents.
Dependency membership/versions, packaging logic and A1–A6 runtime behavior remain
unchanged. §41.14's A6_FIX_1 full `uv run pytest` **2720 passed**, including Node
Settings/UI harnesses and two existing warnings, its independent **56 passed**
regression rerun, and real desktop/550px Chrome acceptance are **reused evidence**,
not new A7 runs. The accepted A6 source/test inputs were frozen at A7 preflight
and only the stated A7 changes followed. §40.14 Connector live acceptance retains
its original scope. Physical mobile, a new live multi-Provider Run, the optional
pre-existing-parent live case and the 17 upstream ItemSaver tests remain
unverified under their recorded boundaries. The unavailable Puppeteer Chrome
environment was unchanged; blocked upstream setup was not repeated.

**Python candidate.** `uv build --no-sources --out-dir <external>/artifacts`
built the wheel from the sdist using accepted current working-tree inputs.
Both artifacts report `0.6.2` and MIT, include current README/license metadata,
and contain all **61/61** application payload files byte-identically, including
new modules and Web templates/static. Complete file lists were inspected
(**75 wheel entries**, **66 sdist files**). Protected local paths, Connector/AGPL
payload, caches, temporary reports and secret markers are absent. A fresh
external Python 3.12.14 venv installed the wheel offline with frozen exported
runtime requirements; site-packages metadata, 11 imports, all installed payload
bytes, CLI help and `uv pip check` (**21 packages compatible**) passed. Installed
FastAPI HTTP/template/static smoke returned 200 for `/settings`, `/static/app.js`
and `/static/app.css`; it used a missing external config and wrote no settings.

**Connector candidate.** The external build reconstructed exact upstream
`876e41ad15139077f2e07b2f71a0fa94742e0b4a` and all five locked top-level submodules,
ran `npm ci --ignore-scripts --no-audit --no-fund` and upstream debug build
`./build.sh -d -v 0.6.2`. MV3 manifest version is `0.6.2`, name is
`Literature Monitor Connector`, and service worker is `background-worker.js`.
Runtime/provenance overlay and AGPL COPYING match tracked sources; source delta
matches all four declared patched paths. Against the digest-verified published
v0.6.1 ZIP, the Chrome payload path set is identical and **only manifest.version
changes**. The separate ZIP retains `chrome-mv3/`, `INSTALL.txt` and **2006**
corresponding-source files under `source/upstream/` and
`source/literature-monitor/connector/`. Its source path set matches the accepted
v0.6.1 format; unused Zotero Desktop dependency symlinks remain excluded as in
that archive. ZIP CRC, complete byte readback, executable build/reconstruction
modes, required licenses/locks/patches/overlays and absence of Python/personal
payload passed. A provenance wording correction required only ZIP repackaging
and readback, not a new upstream build.

All three candidate files are retained in:
`/private/tmp/literature-monitor-v0.6.2-a7-4hq2flbd/artifacts/`.

| Candidate | SHA-256 |
| --- | --- |
| `literature_monitor-0.6.2-py3-none-any.whl` | `1343c6d1af9fe9113640fb03eac1719522d5cce45466a980ea5650023a5d9d35` |
| `literature_monitor-0.6.2.tar.gz` | `2674ac7072099cbc891039e1d805177c876eca28b570013231ddc8fd58198555` |
| `literature-monitor-connector-0.6.2-chrome-mv3.zip` | `816c5e00ed391808e38110aff43a208709a3ac99ba1fba519175ba1da0afecea` |

**Scope and preservation.** A7 adds exactly 12 version/document/assertion file
changes; no unrelated refactor or runtime/dependency/build-logic change was
introduced. The complete A1–A7 candidate inventory and diffs are recorded beside
artifacts, including accepted untracked modules/tests and intended deletions.
The 74 Journal and 20 Publisher tuples match accepted A2_FIX_1/A4 evidence;
all 30 durable Papers retain their accepted unique Journal/Group mapping.
`list.md` is unchanged by A7, Conferences/unrelated tail matches HEAD, and all
177 protected files match A7 preflight bytes/modes/mtimes and the A6 byte hashes.
SPEC §§1–40, including §40.16 artifact digests, remain byte-identical. README,
current identities and final artifacts agree. The final index is empty and HEAD
is unchanged. No commit, tag, push or GitHub Release was performed.

**NEXT: V0_6_2_A7_FINAL_RELEASE_CANDIDATE_REVIEW.** PREPARED records local
candidate construction and verification; v0.6.2 remains UNRELEASED.


### 41.16 v0.6.2 release and documentation closeout (2026-10-08)

FIX_1 repaired the final candidate review's sole finding: stale Status/Stage
lines at the top of SPEC. Its bounded recheck confirmed only lines 3 and 5
changed, all section bodies (including §§1-40 and §41.15) were unchanged, README
was consistent, the external SPEC input hash was synchronized, the index was
empty, all 177 protected files were unchanged, and SPEC was absent from all
three candidate archives. Candidate hashes remained exactly those in §41.15.
No tests or artifact rebuilds were repeated for that document-only repair.
The final candidate review recheck passed. The user's subsequent explicit
release confirmation authorized the continuous commit/tag/push/publication
and necessary documentation closeout.

The scoped implementation/preparation commit and permanent **RELEASE_HEAD**
are `f6cf5a0a24248f321ac88410043610339d348ade`. It includes all **52** accepted A1-A7 paths, including two new
Python modules and six new test files, plus the FIX_1 top-status correction.
The annotated `v0.6.2` tag object is `6eec6fd6003e217eb3f04457ddd51ee694510486` and its peeled target is RELEASE_HEAD.
Normal remote-main and tag pushes were read back against these exact objects;
local `main` was fast-forwarded by compare-and-swap without switching the
`v0.6.2-development` checkout, rewriting history or touching protected data.

[GitHub Release v0.6.2](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.6.2)
was published at `2026-10-08T04:22:30Z` (2026-10-08 12:22:30 Asia/Shanghai).
GitHub API readback confirms latest stable, non-draft, non-prerelease status,
exact title/notes/target, exactly three uploaded assets, and all authoritative
SHA-256 digests matching the verified local files:

| Published asset | SHA-256 |
| --- | --- |
| `literature_monitor-0.6.2-py3-none-any.whl` | `1343c6d1af9fe9113640fb03eac1719522d5cce45466a980ea5650023a5d9d35` |
| `literature_monitor-0.6.2.tar.gz` | `2674ac7072099cbc891039e1d805177c876eca28b570013231ddc8fd58198555` |
| `literature-monitor-connector-0.6.2-chrome-mv3.zip` | `b779e3303efaef21187572191c1f1af25a3edaedcce565bc5873b30a69e87219` |

Final artifacts are retained in
`/private/tmp/literature-monitor-v0.6.2-publish-ssmdeu_6/dist/`.
The Python wheel/sdist reuse the unchanged §41.15 candidate bytes. Their
61/61 application payload files and embedded README were directly compared
against permanent RELEASE_HEAD. SPEC is outside their payload, so FIX_1 and
review-status changes do not invalidate the tested Python build/install inputs.
The final Connector ZIP reuses the verified Chrome build and all 2006
corresponding-source files. Only `INSTALL.txt` was repackaged to identify the
formal release source and exact RELEASE_HEAD instead of an uncommitted,
UNRELEASED candidate. Complete byte readback, ZIP CRC, source comparison against
RELEASE_HEAD and version checks passed. This explains its final ZIP digest's
difference from §41.15; the original three candidate files and digests are
retained unchanged. No upstream reconstruction or runtime rebuild was needed.

Validation reuse follows §41.15: A6 full pytest **2720 passed**, independent
**56 passed** regressions, applicable Node Settings/UI harnesses, real desktop
and responsive 550px Chrome acceptance; A7 affected version/boundary runs and
Connector Node **36/36**, Python metadata/payload/isolation smoke and Connector
pin/license/source/build verification. Only document status and ZIP installation
instructions changed afterward. Physical mobile, a new live multi-Provider Run,
optional pre-existing-parent live case and the 17 upstream ItemSaver tests retain
their recorded unverified scope. Functional tests/live browser acceptance were
not repeated for Git or publication phases. There are no repository CI workflows;
GitHub returns zero check runs and zero status contexts, not a CI success.

Publication checks additionally ran `git diff --check`, scoped staged-content
checks and artifact-source/digest verification. `monitor.yaml`, `src/.obsidian/`,
`workspace/`, all 30 Paper bytes/mappings and 74 Journal/20 Publisher rows remain
preserved. Protected paths were never staged or packaged. Historical §40.16
and §41.15 records remain unchanged. Current README/SPEC/Connector documents
now describe the verified v0.6.2 release. The necessary documentation closeout
is outside the permanent release tag and does not alter uploaded assets; it
uses affected document/diff checks only. No PyPI or Chrome Web Store publication
is claimed, and no tag, Release, asset or reviewed commit was overwritten.

---

## 42. v0.6.3 Keep-driven Batch Zotero Import & Federated Access Preparation — Development Contract

### 42.1 Authority and Baseline

§42 is the v0.6.3 development contract. Development starts from the verified v0.6.2 post-release baseline `578b26b2025ba3b62789d150478e292e83579877` on `v0.6.2-development`; §41.16 retains the v0.6.2 publication evidence. A0 establishes requirements only. It implements no feature and establishes no v0.6.3 test, live-acceptance or release result. v0.6.2 remains the released baseline until a later authorized publication is verified.

For v0.6.3, this section supersedes conflicting earlier requirements only for:

- Paper statuses, transitions and required Zotero linkage, including affected workflow/schema clauses in §§13-14, 19, 23, 37.5, 37.7, 39.5, 39.10 and 40.2-40.4;
- mandatory full-My-Library enumeration, exact-DOI uniqueness reconciliation and `NOT_FOUND` preflight as import/completion authority (§§39.4-39.6 and 40.2-40.5, 40.7);
- single-Paper user capture, process-only attempt safety, restart handling and export-completion criteria (§§39.2, 40.3, 40.8, 40.11 and conflicting earlier export clauses);
- blanket exclusion of federated access preparation and PDF outcome observation (§§39.8-39.11, 40.6-40.7, 40.9, 40.11 and 41.7, 41.11);
- conflicting main navigation, per-Paper save/reconciliation actions and Workspace Reset exclusions.

Unchanged DOI-first identity, workspace UUIDs, Provider behavior, configured ISSN-L and Journal/Publisher ownership remain under §§37 and 41. Preserve human Markdown, safe writes, Web authorization/CSRF boundaries, server-built DOI URLs, authenticated task/bridge attribution and the separate Connector license/provenance boundary (§40.10). §42 does not restore the retired v0.5.x Browser Companion, custom acquisition/staging, PDF writer or credential/session orchestration.

§§1-41 retain their original version-specific meaning, implementation evidence and release records. In particular, earlier `in_zotero`, `zotero_key`, reconciliation and single-capture requirements describe those versions; they are not v0.6.3 workflow requirements. A0 changes only this appended section and directly conflicting project rules in `AGENTS.md`. It changes no runtime, tests, README, package identity, user data or Git/publication state and performs no Reset or Zotero save.

### 42.2 Paper Workflow

The complete supported Paper status set is:

```yaml
status: candidate | kept | rejected | exported
```

Only these normal transitions are allowed:

| Transition | Authority and observable result |
| --- | --- |
| `candidate → kept` | Explicit Keep immediately persists the user's final import intent through the safe Paper-write boundary, independently of Zotero or browser availability. |
| `candidate → rejected` | Explicit Reject immediately persists the decision through the same boundary, independently of Zotero or browser availability. |
| `kept → exported` | An attributable Connector parent-save confirmation and the matching current Paper revision authorize one atomic completion write (§42.3). |

Repeated Keep/Reject requests cannot create another transition or erase a concurrent decision. A failed write must report failure and leave actual durable state intact. There is no Reject/Keep Undo. `rejected` and `exported` are long-term history: later Run/materialization may refresh permitted metadata but must not reset them to `candidate`, recreate a second candidate for the same DOI, or discard their UUID, notes or custom fields. Human-controlled state and content remain protected under the existing rerun rules.

`exported` means that historical Zotero parent export completed. It makes no assertion that Zotero currently contains an item, still has a PDF, or has a unique matching DOI. Deleting or editing an item in Zotero does not automatically change the Paper. New Papers contain no generated `zotero_key`; normal import requires neither an item key nor full My Library DOI enumeration, uniqueness reconciliation or a Zotero item mirror. A returned item key is not durable workflow identity.

Older `in_zotero`/key-bearing schemas are not automatically converted, reinterpreted as `exported`, stripped or repaired. Incompatible Papers cannot import; establishing a fresh supported workspace uses only the explicitly authorized Reset boundary (§42.8). This task does not authorize disposal of the existing workspace. File/BibTeX/Markdown export alone is not Connector parent confirmation and cannot mark a Paper `exported`.

### 42.3 Transient Import Attempts and Historical Metadata

Paper Markdown remains the sole durable source of workflow status. New v0.6.3
imports must not persist `export_attempt`, `pending`, `uncertain`, a batch
manifest, Zotero item keys or separate failed-import markers. Only the four
status values in §42.2 are durable workflow states. Each explicit Import
invocation constructs fresh process-local attempt identities; failures, timeouts,
missing responses and process exits leave unconfirmed Papers at `kept`.
A subsequent explicit invocation may retry them without a separate recovery
operation. No outcome automatically retries within the same active batch.

Existing `export_attempt` frontmatter is historical user data. Treat it as
non-authoritative for new import eligibility, whether syntactically valid,
`pending`, `uncertain`, malformed or inconsistent with the current Paper.
Preserve its original value and all unknown fields; never silently delete,
rewrite, auto-repair or reuse it as a new authority. A legacy field in another
Paper, note or unimportable document cannot globally block Kept import.
Incompatible `in_zotero`/`zotero_key` schemas retain their established
non-migration protections; an unsafe or ambiguous target still cannot save.

Maintain one Workspace operation owner across each active batch, including
across processes, and one Connector active capture. Within the owner, derive
an immutable plan of safe uniquely identified Kept Papers from one Workspace
inventory. Immediately before claim and native dispatch, reread the planned
target using safe path, inode, Workspace, UUID, normalized DOI, status and
exact-content checks. A changed target cannot dispatch or be committed.
No historical failed attempt establishes an ongoing live save slot.

The Connector command binds an unpredictable request/invocation identity,
DOI URL, current task tab and document/translator identity and native session.
Its one-shot dispatch grant prevents replay, duplicate claims and duplicate
saveItems calls. Old process callbacks cannot bind to a fresh coordinator.
The Connector serializes task execution even when an interrupted invocation
still has browser-side work; new commands must never induce concurrent saves
with an actually active capture. An ambiguous dispatched outcome stops further
automatic dispatch in that batch; the next explicit Import can retry once an
available Connector owns the new command. This cannot establish that Zotero
contains no pre-existing item; the interface must warn about duplicate risk.

Only attributable native Desktop parent acceptance authorizes `kept → exported`.
For automatic native `saveItems` only, the Connector preserves the HTTP status
alongside the body. A completed `201 Created` response with an empty body
confirms the parent; a `201` JSON response with one echoed client-side parent
ID is also valid when that ID matches the single requested item. The pinned
request must still match the reserved DOI, one-shot dispatch grant, native
session and task document/translator. Reject HTTP errors, a missing transport
response, non-201 status and a contradictory or malformed nonempty response.
The client-side item ID is not a Zotero library key.
The coordinator consumes the matching receipt once. The commit rereads and
compares the planned current Paper, then atomically writes `exported` while
preserving all other frontmatter, custom notes and body. No obsolete
`export_attempt` is automatically cleared as part of this write. If native
acceptance is not confirmed, keep the Paper unchanged. If the local completion
write fails or its durability is ambiguous, report that specific condition,
do not assert a definite failure of the upstream save and do not overwrite
concurrent human edits. A fresh user invocation may still attempt import after
manual duplicate inspection.

### 42.4 Batch Execution

`Import to Zotero` builds a single process-local plan of every safe, uniquely
identified `kept` Paper in the current Workspace, including earlier Runs.
The plan is frozen at invocation; a later Keep appears in the next plan.
Candidate/rejected/exported or unsafe/ambiguous Papers are omitted with a
truthful per-Paper reason. A malformed, unrelated or historical attempt field
alone is not an exclusion or global blocker. Do not rescan the entire Workspace
between saves; revalidate only the planned target and the required safe-write
identity at claim, dispatch and completion.

Explicit Import runs serially. At most one Connector capture is active for
this Workspace and a duplicate submission during an active batch coalesces
or rejects before a second dispatch. The application must isolate an individual
translator/navigation pre-dispatch failure, record the result, leave that
Paper kept and continue with other eligible Papers. An unconfirmed dispatched
save pauses the rest of the batch because the browser task may still be
finishing. A Connector/Zotero-wide outage may stop the batch with unsent
Papers kept. A new explicit invocation may retry every remaining Kept Paper.
Never resume a batch automatically after process restart and never accept
old command, dispatch or confirmation identifiers for a new invocation.
Native parent acceptance does not complete the browser task. Continue through
the existing Zotero collection/attachment pipeline, then deliver the terminal
result and release browser task ownership. If a confirmed parent outlives the
bounded observation wait, report its confirmed parent separately from
unverified attachments and retain the unresolved native save slot until the
original browser-side pipeline ends; do not close that tab or overlap saves.
An explicit, invocation-bound browser completion receipt is required to retire
an issued native grant. It follows verified completion of the original upstream
save pipeline, including any attachment continuation. Neither the parent receipt,
a timeout nor a newer batch outcome can establish this completion. Lost terminal
receipts leave Reset conservatively blocked without making the Paper durable
state uncertain.

The UI reports per-Paper parent acceptance, pre-dispatch failure, native-save
error without acceptance, missing confirmation, local-write failure,
skipped entries and separate PDF evidence. No failed or uncertain durable status
or permanent warning marker is written to Paper Markdown. A retry may duplicate
a parent already present in Zotero; normal import performs no library lookup.

### 42.5 Connector and PDF Outcomes

Continue to use the pinned official Zotero Connector translator and its native
`saveItems` request to Zotero Desktop. A successful page translation alone is
not native acceptance. Keep the existing task tab, document-generation,
challenge, one-shot dispatch and stale-callback checks. The existing bounded
heartbeat, command claim and dispatch endpoints are functional command
transport and permission guards; HTTP access-log volume alone is not a reason
to replace them with a larger messaging framework. Do not add a custom
bibliographic importer, PDF downloader, attachment writer or Zotero mirror.
After a terminal import, reactivate the original non-incognito Literature
Monitor tab if its exact application origin and window still match. Close
only the project-created task tab after successful native parent and
attachment-pipeline completion with an accepted result, provided its document
is still the saved document. Retain the task tab on native/attachment error,
missing confirmation, delivery failure or user navigation for inspection.
Never close user-owned tabs or steal focus during unfinished manual
authentication. PDF saving remains independently `unverified` unless the
upstream supplies attributable completion evidence.

| Outcome | Meaning and Paper effect |
| --- | --- |
| Parent confirmed | Matching native `saveItems` acceptance with verified parent identity; commit `exported` under safe Paper CAS, irrespective of PDF. |
| Translator/navigation failure | Before native dispatch, confirmed no-effect for this invocation; retain `kept` and continue. |
| Native save failure | Native save path reported an error, without confirmed acceptance. Upstream side effects may still have occurred; retain `kept` and report uncertainty. |
| Missing confirmation/timeout/interruption | Acceptance not established; retain `kept`, invalidate stale server commands, report duplicate risk on a new explicit Import. |
| Local write failure | A parent may be accepted by Zotero but Paper CAS/durability failed; do not claim either a durable export or a proven no-effect save. |
| PDF verified success/failure | Only attributable terminal actual-PDF save evidence may justify a corresponding PDF result; it never changes parent status. |
| PDF unverified | Links, Snapshot metadata, native parent acceptance and missing attachment callbacks do not prove actual PDF saving or failure. Report `unverified`. |

Batch results and access observations remain process-local. Treat provider
claims about translator/native stages as diagnostic labels; the coordinator's
own one-shot grant and invocation checks determine which outcomes can authorize
a durable transition. PDF observation must remain independent, finite and
non-blocking; manual Zotero inspection is the fallback wherever actual PDF
completion cannot be verified.

### 42.6 Access Service and Federation

An Access Service is the actual DOI full-text landing service/SP used by a Paper, separate from its configured bibliographic Publisher. Authentication preparation is derived only from the current Kept import plan; do not log in to every configured Publisher. Resolve service identity through the server-built DOI navigation, actual landing/redirect evidence and verified application rules. Publisher names, OpenAlex Publisher IDs, DOI prefixes and manually saved Publisher Access URLs cannot select an SP or invent its authentication endpoint.

The following observations have distinct meanings and remain process-local:

| Observation | Meaning and limitation |
| --- | --- |
| IdP session | The institution's identity provider may recognize the user; this does not prove an SP session. |
| SP session | The specific service may recognize an authenticated browser session; this does not establish access on another SP. |
| Authentication success | A verified trusted route completed for the intended service; this does not prove full-text entitlement. |
| Full-text authorization | Reliable service/page evidence establishes access to that particular resource; it cannot be inferred solely from any preceding session observation. |

Use existing normal-profile sessions naturally through browser navigation. Permit only verified trusted CARSI/Shibboleth/OpenAthens or equivalent federation routes. Each enabled rule needs evidence for its actual SP, institution/IdP selector, allowed destinations/redirects and usable return behavior. A0 names no verified route and supplies no executable WAYFless URL. Unknown SPs, missing trusted routes, ambiguous association or failed authentication fall back to the actual service page/manual institutional access; never guess a URL or claim access succeeded.

The user completes IdP credentials, MFA and CAPTCHA/challenges in ordinary Chrome. Do not enter credentials, bypass verification, inspect/extract Cookies or browser session stores, capture SAML assertions/sensitive query parameters, or persist credentials, sessions, login history or inferred expiry. Observe only the navigation and non-sensitive page/result evidence needed for the declared service outcome. Keep sensitive federation URLs/parameters out of Paper state, configuration, diagnostics and durable logs.

Prepare service access once per applicable process-local context, reusing what the normal profile actually supplies without promising a session lifetime. An IdP login may help several services, but each SP retains independent outcome/entitlement handling. A failing or waiting service must not block unrelated services; skip/defer its Papers for manual access when needed. Suspended navigation must have no authority to trigger a late save outside the serial slot.

Existing OpenAthens host associations and redirect behavior must coexist with other institutional routes and manual Publisher URLs. Do not overwrite profile/host associations or apply a catch-all redirect that forces unrelated hosts through one route. Verified rules must account for the actual associated host and preserve the intended DOI/service destination. Use finite redirect/hop and repeated-destination protection, with at most one automatic preparation attempt per service context in a batch; a detected loop stops orchestration and offers manual access. No cookie deletion, session reset or repeatedly forced login is a remedy.

Actual trusted route availability, host-association behavior and reliable authentication/entitlement indicators are engineering evidence gates (§42.10). Insufficient evidence disables automatic preparation for that service while retaining normal Connector/manual access. Federation preparation does not expand discovery, scrape Publishers or revive the retired acquisition architecture.

### 42.7 Settings and UI

The daily main navigation contains exactly:

```text
Inbox
Kept
Settings
```

Inbox contains candidates with immediate Keep/Reject decisions. Keep is the final import intent. Kept aggregates the whole Workspace and provides one `Import to Zotero` action, serial progress and separate parent/PDF results, including skipped/blocked/uncertain reasons and required manual access. It must not require a second per-Paper selection decision. The normal workflow no longer depends on per-Paper `Save to Zotero` or `Check Zotero` reconciliation. Safe manual DOI access remains available for fallback without changing status.

Rejected and Exported records remain durable history and are excluded from daily main navigation and the default import set. This does not authorize their deletion or add an Undo/history-management feature. Use current refreshed views after successful safe writes; a display must reflect actual persisted state, including a failed completion write.

Institution Settings save only necessary non-sensitive institution/IdP identifiers. They contain no passwords, Cookies, SAML payloads, browser session snapshots or guessed validity times. Integrate them with the existing Settings validation/revision/safe-persistence boundary; no third configuration store or workflow database is authorized. Trusted SP routes are verified application rules, not a generalized Publisher login table the user must maintain. A configured institution identifies a preparation context, not evidence of successful authentication or entitlement.

§41's configured ISSN-L, direct Publisher identity, human-managed Access URL/Group ownership and shared Journal/Publisher Settings rules remain applicable. Ordinary saves that do not change Journal identity remain network-independent. Manual Publisher Access URLs retain their manual role and do not become federation rules. Connector readiness remains a compact truthful current indication, without durable login/capture diagnostics history.

### 42.8 Workspace Reset

**User decision (2026-10-09):** After one explicit user confirmation, Reset
permanently deletes the **entire directory selected by the current Workspace
configuration**, equivalent in scope to `rm -rf Workspace`. The user accepts
this deletion. This contract replaces the earlier recognized-output inventory,
protected-internal-content, per-file object-custody and recovery requirements
recorded in §§42.14–42.15. Those records retain their historical evidence only.

The operation remains at Settings → Advanced & Diagnostics → Danger Zone.
Show the actual absolute Workspace path, warn that all its contents are included,
and require one typed `RESET` confirmation bound to that server-held target and
current configuration. There is no per-Paper inventory or format inspection.
Contents include `.obsidian`, custom Markdown, modified Inbox.base, .DS_Store,
unknown files/directories, old/damaged Papers and pending/uncertain export_attempt
markers. These do not cause refusal. Empty or already-absent Workspaces are valid
outcomes. Reset is never automatic and starts no Run or migration.

Only the current configuration-bound directory is a deletion target. Reject root,
Home, repository/configuration roots, mount roots and other obviously dangerous
locations. Within this repository only the dedicated `workspace/` area may be a
Reset target; source, tests, Connector, Git metadata, configuration and other
repository directories are refused, including case aliases. Never follow target/ancestor symlinks, redirect deletion through a
changed path, or traverse links inside the tree into outside data. Bind the
confirmation to the target directory identity and configuration revision, and use
directory-relative, symlink-safe recursive deletion; unavailable platform safety
causes refusal. Atomically capture the root into a private temporary directory and
verify its confirmed identity there before deletion. A competing root captured
instead remains intact with its retained location reported; a replacement at the
public Workspace path is not deleted and prevents SUCCESS. This root-only binding
does not inspect individual files or offer restoration. Workspace-external monitor.yaml/list.md, Git repository data,
Zotero and Chrome data are not deletion targets. Files intentionally placed
inside the Workspace, including copies named monitor.yaml/list.md, are included.

Reset shares necessary nonblocking cross-process exclusion with Run, Batch,
materialization and Paper/Settings writes. Separately, each authorized native-save
dispatch creates a process-local outstanding grant and holds a shared external
Reset guard for its Workspace. Reset must acquire this guard exclusively before
deletion. The stable guard inode lives outside the Workspace; deleting its internal
operation lock cannot bypass protection for an unresolved native grant. The guard
is released only after every outstanding grant has an attributable completion
receipt from its own browser pipeline; a newer successful Import cannot clear an
earlier uncertain grant. The Connector verifies original request/invocation,
DOI target, task tab, document and native session before reporting pipeline
completion. A terminal result, parent acceptance, time limit, browser loss or
manual assertion alone never supplies this receipt. Lost or unverifiable evidence
keeps guard ownership until a safe external process-lifecycle boundary; Reset
must refuse while it remains held. Other explicit Imports may acquire shared
guard ownership; old failed/unconfirmed Paper metadata never excludes them. This
shared guard is an empty lock, not durable workflow state. Historical attempt
fields alone do not block Reset. Reset errors distinguish active Run/Batch,
pending capture resolution, outstanding native grants and cross-process lock
contention rather than inferring activity from a displayed FINISHED result.
Reset does not revoke an already issued Zotero request, reverse external saves or
establish that another import is safe. The UI explicitly warns of possible old
browser/Zotero side effects; lost-process observations cannot prove revocation.

No Undo, Recovery, Rollback, restoration directory, disposal inventory or automatic
retry is provided. Deletion need not be atomic. Any deletion/validation error
returns failure and warns that some contents may already have been permanently
deleted; it cannot claim complete deletion or restoration. Success requires the
configured target absent after the operation (or safely confirmed already absent).
A subsequent **explicit Run** recreates the supported Workspace outputs; deleted
user files and `.obsidian` are not recovered. Reset is disposal, not data repair,
migration, Zotero cleanup, session reset or import retry.

### 42.9 Scope and Non-goals

v0.6.3 authorizes only the changed workflow, minimal attempt safety, serial Keep-driven import, separate outcome observation, trusted access preparation, stated UI and explicit safe Reset. It excludes:

- automatic institutional credential filling, MFA/CAPTCHA bypass and credential/session extraction or storage;
- full-Publisher login preparation, guessed federation endpoints, Publisher scraping and retired publisher-specific acquisition/login adapters;
- Zotero item synchronization, deduplication, deletion, merging, repair, a library mirror, Collection/Group/tag routing or custom bibliographic ingestion;
- custom PDF downloading, staging/upload, attachment creation/management or restoration of the v0.5.x PDF-write architecture;
- a full batch audit database, durable batch queue/navigation/authentication/PDF history or an independent workflow source of truth;
- old Paper schema migration/repair, automatic Workspace Reset, Reject/Keep Undo or new-version reminders;
- unrelated Provider, DOI identity, Journal canonicalization, configuration ownership or discovery redesign.

### 42.10 Verification and Release Gates

The following are acceptance requirements for later implementation, **not checks run or passed by A0**. Evidence must state the actual command/scenario, tested inputs, environment, outcome and coverage boundary. Existing v0.6.2 tests/live records retain their historical scope and cannot establish v0.6.3 success.

| Level | Required observable acceptance |
| --- | --- |
| Unit, network-independent | Offline Keep/Reject persistence and failed-write behavior; only the three normal transitions; reruns preserve rejected/exported, UUIDs, notes and custom fields; new Papers have no generated key; ordinary file exports never mark Zotero completion. |
| Unit, safety/fault injection | No new persistent export-attempt marker is created; interrupted/unconfirmed imports remain kept; a later explicit invocation can retry, with a duplicate warning and no hidden durable failure state. Malformed historical attempts neither block nor overwrite user data. Local-write failure must not be misreported as no-effect or confirmed durable export. |
| Unit, identity/concurrency | DOI, UUID, path/location, file object, status or revision changes reject dispatch/wrong writeback; confirmed-parent status writes preserve unrelated human content and old metadata; stale/duplicate callbacks and commands cannot complete or save another attempt; competing processes cannot actively save the same Paper. |
| Integration, application/Connector | Build the all-Kept plan once, including earlier Runs; perform targeted safe checks at each save. One active serial capture; duplicate active submissions/replayed triggers cause no second save. Translator pre-dispatch failure preserves unrelated progress; ambiguous native acceptance pauses that batch but a later explicit invocation can retry. Timeout/restart reject old callback authority; unavailable services leave unsent Papers untouched. |
| Integration, outcomes | Parent confirmation and actual PDF completion are separate; links/Snapshots/progress/parent-only confirmation yield no PDF success; prove verified success/failure attribution if supported, otherwise unverified; PDF failure leaves confirmed parent exported and does not trigger save/repair. No My Library enumeration, uniqueness or item key is needed for normal completion. |
| Integration, access/UI | Institution identifiers use existing safe Settings persistence; three main navigation entries, immediate decisions, one Kept import action and truthful progress; existing session/manual IdP/unknown-SP fallback; isolate service failure/wait; verified rule allowlists, OpenAthens association coexistence and bounded redirect-loop fallback; no sensitive durable state. |
| Integration, Reset/adversarial filesystem | Actual absolute path/configuration and RESET binding; whole-directory deletion including unknown/legacy/damaged content, `.obsidian` and historical pending/uncertain markers; empty/absent success; dangerous target and symlink/path-replacement safety; cross-process Run/Batch/write/live-save exclusion with a stable external lock even after the internal lock/tree disappears; injected deletion failure reports failure and possible partial deletion without recovery/retry; final absence and explicit Run recreation; configuration/Git/Zotero/Chrome outside the target untouched. |
| Live, real normal Chrome/Zotero | An explicitly authorized batch spans at least two actual Access Services and includes kept Papers beyond the latest Run. Observe serial toolbar-free Connector parent saves and guarded exported transitions; prove isolation/fallback for a failed/unknown service. Use both an existing institutional session and user-completed IdP authentication as applicable to verified routes, with no credential/session extraction. |
| Live, federation/PDF | Record actual DOI landing/SP identities and rule evidence; exercise OpenAthens host-association coexistence and safe loop handling in real Chrome, distinguishing controlled loop tests from genuine service behavior. Establish actual available parent/PDF completion evidence; report PDF unverified wherever reliable actual-save evidence is absent. |
| Live, preservation/Reset | On an isolated dedicated workspace, explicitly authorize and verify Reset followed by Run. Read-only before/after evidence must show existing Zotero items and normal Chrome session continuity unaffected, Workspace-external configuration/.obsidian untouched while internal `.obsidian` is deliberately deleted, and later Run retaining rejected/exported on a non-reset supported workspace. Automated assertions alone do not prove these real-app boundaries. |

Before enabling an SP rule, later work must verify its institution/service route, redirect/return behavior and non-sensitive observation capability. Unknown or unverified SPs retain manual fallback. Before reporting PDF verified success, later work must demonstrate reliable actual-save evidence with attempt/parent attribution; absent capability yields `unverified`, not a manufactured success. Before shipping batching/Reset, later work must prove cross-process exclusion, stale browser save invalidation, whole-Workspace permanent deletion boundaries and truthful failure/partial-deletion reporting under the relevant scenarios; historical attempt markers do not block Reset, and Reset does not prove import retry safe. Run must bind materialization, Provider state and LastRunSnapshot to the same locked Workspace even when configuration changes during execution. These unresolved engineering capabilities are gates, not assumed implementation facts.

Final integration also requires the applicable full pytest, executable Node/Connector/UI and upstream/build checks, `uv lock --check`, `git diff --check`, and package/install/artifact verification proportionate to changed inputs. Preserve MIT/AGPL separation, exact pinned upstream provenance, protected-path exclusions and unaffected DOI/ISSN-L/Provider/Settings regressions. Record unavailable checks explicitly; fix or resolve necessary evidence gaps before release.

Required real Chrome/Zotero acceptance cannot be replaced by mocks, synthetic callbacks, source inspection or historical single-Paper live evidence. No necessary live gate may remain unpassed when claiming v0.6.3 RELEASED. The absence of a proven PDF completion API does not require a downloader or claim of PDF success: the release must instead demonstrate truthful `unverified` behavior and its manual fallback. Version changes, commits, tags, pushes and publication belong to later explicitly authorized tasks. A0 ends with this contract and aligned project rules; A1 is not started.

### 42.10 A4 serial whole-Workspace batch implementation evidence (2026-10-08)

**A4: EXECUTED; review fix 2 implemented, independent re-review pending.
v0.6.3 remains unreleased.**
`V0_6_3_A4_SERIAL_ALL_KEPT_BATCH` was implemented against
`578b26b2025ba3b62789d150478e292e83579877`, preserving the existing uncommitted
A0–A3 inputs. This record establishes application/bridge automation evidence,
not real Chrome/Zotero acceptance or A5–A9 completion. Independent review requested
changes for a hidden unresolved-marker fence bypass (F1) and partial failures
reported as successful (F2). `V0_6_3_A4_REVIEW_FIX_1` addresses both against the
same HEAD while retaining the original uncommitted A4 work.
Further independent review found that changing a reserved Paper's suffix could
hide its marker from the safety scan (F3).
`V0_6_3_A4_REVIEW_FIX_2` removes this suffix-based safety bypass while retaining
Fix 1 and the existing A0–A4 work.

`application.batch_import.BatchImportService.start(workspace)` explicitly scans
all current eligible Kept Papers, including earlier Runs, and processes the
frozen plan in stable filename order. `snapshot()` returns immutable process-local
plan, phase, current Paper, compact results and separate parent/PDF statistics.
Overlapping starts on one service coalesce. Later Keeps enter the next explicit
invocation. Current status, DOI, exact content, location and file identity are
checked before reservation; UUID/DOI collisions and exclusion/blocking reasons
are exposed by `application.workspace.plan_workspace_import`. No batch queue,
manifest, history database or automatic restart/retry is persisted.

Safety inventory reads every direct entry in `Papers/` through the existing
no-follow regular-file boundary and parses frontmatter before filtering by suffix,
Paper type or metadata. Non-`.md` documents are never import targets. An excluded
document containing `export_attempt` blocks new save authority even
when its marker is malformed or lacks verifiable identity. Unsafe or unparseable
documents also fail closed, including non-`.md` entries, links and subdirectories
whose contents cannot be safely established by this scan. A safely parsed ordinary
non-Paper or non-`.md` document without a marker is normally excluded.
This scan never clears an old marker.
Exclusions expose a compact `kind` of `normal`, `error` or `blocked`.
`normal_excluded` counts non-target exclusions separately from `skipped_blocked`;
candidate/rejected/exported states are normal exclusions unless a safety blocker
applies. Invalid or otherwise unimportable Kept Papers count as errors. A completed
batch reports `successful` only when every planned target exported and there are
no error exclusions or safety blockers.

The existing empty Workspace operation-lock inode is held through the batch,
reservation, claim/dispatch and A3 guarded completion. Coordinator-only callers
also acquire the same owner; A1/A3 reuse its live lock instead of reacquiring
flock. Run/materialization and decisions already use this boundary; Settings
Workspace-path changes now acquire applicable old/new existing Workspace locks
and recheck them before writes. Workspace, Papers-directory and lock identities
remain checked at the relevant boundaries. Web creates the service beside the
existing coordinator, and old capture polling cannot consume a running batch's
completion. No new import UI or legacy per-Paper Save/Check action was added.

Safe continuation has two affirmative proofs: the exact owned terminal command
never granted dispatch, so late claims/permissions are rejected; or the pinned
one-shot native invocation received attributable parent acceptance, consuming
that command's parent-save permission. A3 persistence failure remains protected,
even when parent permission is already consumed. Retirement evidence binds the
complete frozen attempt and exists only in that coordinator's memory. It cannot
be rebound by copying an attempt ID to another Paper.

A grant without attributable acceptance may still reach the content script or
native save after the UI wait ends. The batch reports `paused`, retains pending/
uncertain protection and dispatches no next Paper. File-lock release or process
exit is not browser invalidation: unknown unresolved markers observable in this
scan fence the Workspace save slot on a fresh process. Known safely retired markers remain
excluded Papers but can permit unrelated continuation in their owning process.
Confirmed parents report PDF `unverified`; A4 adds no PDF evidence producer,
Zotero enumeration, item-key inference, download or attachment handling.

**Safety scope:** this is an inventory of currently observable direct `Papers/`
entries, not a global record of escaped browser save permissions. If an external
actor deletes the reserved file, moves it entirely outside `Papers/`, or removes
its marker, a fresh process cannot recover that lost evidence from the Paper-only
design. The implementation does not revoke an already granted browser permission,
search other locations, or persist global save tracking. Serial safety under such
external removal is not established; file/marker absence must not be described as
proof that an old native save stopped. Rename within `Papers/` now preserves the
fence irrespective of suffix; an uninspectable directory entry fails closed.

**Original A4 verification, before review fix 1:** `uv run pytest` completed with
**2776 passed** and two existing dependency deprecation warnings. Focused A4/A1/A3/
coordinator/Settings compatibility coverage completed with **311 passed**.
`node --test connector/tests/runtime.test.cjs` completed with **82/82** harness
cases, including the shipped native hook's delayed permission and consumed-save
replay cases. These are original A4 results, not a full-suite run of the review fix.

**Review fix 1 verification:** before production edits, the two focused F1/F2
regressions failed. An isolated copy of the actual pre-fix Python source reproduced
F1 with `blockers=0` and a granted B parent-dispatch permission after A's pending
marker was hidden by `type: note`. With the fix, `tests/test_batch_import.py`
completed with **60 passed**. The combined targeted command
`uv run pytest tests/test_batch_import.py tests/test_application_workspace.py tests/test_export_attempts.py tests/test_capture_coordinator.py tests/test_zotero_capture.py tests/test_web_zotero_capture.py tests/test_connector_outcomes.py tests/test_web_connector_bridge.py tests/test_web_app.py`
completed with **361 passed**, including those 60 batch tests, and two existing
dependency deprecation warnings. `./connector/test.sh` completed with **82/82**
harness cases; Connector source and dispatch contracts were unchanged by this fix.
The full suite was not repeated: its earlier evidence is reused only for unaffected
coverage. The review-fix diff was inspected and `git diff --check` passed.

New deterministic regressions cover hidden pending/uncertain markers, malformed
markers, missing/mismatched identity, invalid YAML/delimiters, different coordinators,
normal non-target exclusions, invalid Kept metadata/duplicate DOI and safely retired
attempts without automatic retries. Three fresh real spawned-process cases
(pending, uncertain, malformed) confirm a blocked batch, no exposed command and
denied dispatch permission for another valid Kept Paper. These run against temporary
Workspaces and the actual fixed application code, with no browser or native save.

**Review fix 2 verification:**
`uv run pytest tests/test_batch_import.py -k 'review_f3_real_old_dispatch' -s`
failed both cases before production edits and passed both afterward. Each case
uses two independent spawned processes: the old process obtains dispatch permission,
is terminated, and the actual Workspace flock is then acquired/released before
renaming A to `.txt` (pending) or `.bak` (uncertain). The marker bytes survive the
rename. The new process reports `NEW_PROCESS_COMMAND False` and
`NEW_PROCESS_DISPATCH_GRANTED False`, rejects the old invocation and leaves B
unchanged. This verifies application permissions and real OS process/lock behavior;
no actual Chrome/Zotero save is performed.

`uv run pytest tests/test_batch_import.py` completed with **77 passed**. The same
nine-file targeted command recorded for Fix 1 completed with **378 passed**,
including those 77 batch tests and all Fix 1/F2 regressions, with two existing
dependency deprecation warnings. Seventeen new cases cover suffix changes, damaged
or unverifiable markers, different coordinators, the two spawned-process sequences,
normal marker-free non-`.md` documents, malformed YAML/UTF-8 and unsafe link,
directory/FIFO entries. `./connector/test.sh` completed with **82/82** harness cases.
Connector code/contracts and `application/batch_import.py` are unchanged by Fix 2.
The full suite was not repeated; previous full-suite evidence applies only to
unaffected code. The Fix 2 diff was inspected and `git diff --check` passed.
The remaining `.md` filters in the read-only Workspace view, Paper locator and
A1 reservation schema scan grant no native dispatch authority; coordinator
start, claim and dispatch all revalidate through the corrected safety inventory.

Python coverage separates deterministic bridge/filesystem injection from actual
POSIX `multiprocessing` **spawn**: two competing processes permit only one grant,
leave a different Paper unsent, and process termination releases flock without
making stale browser authority safe. Deterministic coverage exercises multiple
materialized Runs, full default plans, changed Papers, duplicate starts/claims/
permissions/results, delayed positive permission replies, safe timeout
continuation versus unsafe pause, persistence conflicts, unavailable service,
shared operation exclusion and Workspace/Papers/lock replacements. Connector
worker restart and callbacks are simulated in Node; these are not actual Chrome
service-worker or Zotero observations.

**Remaining gates:** independent repository review, later A5–A9 work and §42.9's
explicitly authorized real ordinary Chrome/Zotero batch/federation/PDF/Reset and
applicable distribution acceptance. No live app or user Workspace was modified.
All **177** protected files under `monitor.yaml`, `src/.obsidian/` and `workspace/`
matched preflight bytes. Existing A0–A3 work, release version and Connector source/
provenance/license separation are preserved. No commit, push, tag, release or
branch switch occurred. A4 rollback removes only this task's source/test/document
hunks while retaining the pre-existing uncommitted A0–A3 changes; no user-data
rollback is needed.
Review fix 1 rollback removes only its hunks in `application/workspace.py`,
`application/batch_import.py`, `tests/test_batch_import.py` and this evidence section,
using the pre-fix snapshots while retaining the original A0–A4 uncommitted work.
Review fix 2 rollback removes only its hunks in `application/workspace.py`,
`tests/test_batch_import.py` and this evidence section using the pre-Fix-2 snapshots;
it preserves Fix 1 and all preceding uncommitted work.

### 42.11 A5 unified import UI implementation evidence (2026-10-08)

**A5: EXECUTED; independent review pending. v0.6.3 remains unreleased.**
`V0_6_3_A5_UNIFIED_IMPORT_UI` uses baseline
`578b26b2025ba3b62789d150478e292e83579877` and retains all uncommitted A0–A4
inputs, including the two review fixes. A1–A4 application/native-save mechanisms
and the Connector source/contracts are unchanged. A6–A9 are not implemented.

The primary navigation is exactly Inbox, Kept and Settings. Redundant Workspace
tabs were removed; Rejected/Exported Markdown and existing read-only historical
URLs remain available without a daily navigation entry. Inbox still derives
only candidates from current Markdown. Keep/Reject reuse the existing application
decision and reread path, including write-failure feedback and neighbor selection;
they need no browser/Connector/Zotero availability.

Kept displays the entire configured Workspace, including earlier Runs, with one
`Import to Zotero` action and no per-Paper selection or confirmation. The action
warns that normal import does not check My Library for matching DOIs and prior
manual saves can create duplicates. Safe Open DOI remains a manual link, with no
mutation or Retry authority. Pending/uncertain notices explain possible prior
saving, blocked automatic retry and the lack of timeout/restart revocation proof.

`POST /imports/start` verifies the rendered Workspace authorization against the
real configuration and calls only `BatchImportService.start()`. Extra form fields, including caller
Workspace paths, are rejected. Both import endpoints require the existing CSRF
token and retain TrustedHost restrictions. The form token is an HMAC over the
server-rendered configuration revision/file identity, resolved Workspace path,
Workspace/Papers directory identities and existing operation-lock identity.
A process-local nonce, consumed under a small submission mutex, rejects replay even after a
no-effect batch finishes; a new explicit form action is required for another
batch. Different pages share the same A4 service and its existing execution
exclusion. This token is presentation replay protection, not another batch state,
queue or database.

`POST /imports/poll` reconstructs Markdown views and presents only `snapshot()`;
it never consumes coordinator completion. HTMX polls only while the displayed
phase is running. Page load, Run completion and Settings refresh never start an
import. The panel distinguishes parent completion, errors/unresolved results,
safety blockage, paused stale-save authority and unavailable capture service.
It shows the current Paper, processed/planned counts, exclusion/blocker reasons,
exported/no-effect/uncertain/skipped-blocked/normal-excluded counts and the three
separate PDF counters. Parent completion does not assert PDF saving. HTTP refusal
and transport failure get visible feedback without inventing a save outcome.
Legacy per-Paper Save/Check remain CSRF-protected HTTP 410 entry points.

**Automated evidence:** `tests/test_unified_import_ui.py` contains **27 passing**
cases covering navigation, disconnected Keep/Reject and failing writes, cross-Run
Kept membership, real A4 full-plan execution through the Web adapter, sequential
and concurrent duplicate POSTs, completed no-effect replay, multiple-page polling
with pending worker-owned completion, request/path/config/CSRF/host refusal,
parent/failure phases, changed Markdown, normal history exclusions and truthful
PDF presentation. Progress is captured before reloading Markdown so a terminal
snapshot cannot stop polling with a stale Kept list; a deterministic regression
checks that ordering. The PDF verified-counter fixture is synthetic snapshot evidence,
not a PDF completion capability. Its Node DOM harness executes the shipped JS,
including conflict refresh and HTTP/transport-error feedback.

The targeted command
`uv run pytest tests/test_unified_import_ui.py tests/test_web_app.py tests/test_web_run_settings.py tests/test_a5_settings_ui.py tests/test_application_workspace.py tests/test_application_decisions.py tests/test_batch_import.py tests/test_export_attempts.py tests/test_capture_coordinator.py tests/test_zotero_capture.py tests/test_web_zotero_capture.py tests/test_web_connector_bridge.py tests/test_connector_outcomes.py tests/test_application_settings.py --tb=short`
completed with **661 passed**, including the A4 real spawned-process regressions,
Run/Settings behavior and existing Node harnesses, with two existing dependency
deprecation warnings. After the final JS error-feedback change,
`uv run pytest tests/test_unified_import_ui.py tests/test_a5_settings_ui.py tests/test_web_run_settings.py -k 'executable or unified_import' --tb=short`
completed with **33 passed, 160 deselected**. After the idle-heading and final
snapshot/Markdown ordering changes, the final affected Web command
`uv run pytest tests/test_unified_import_ui.py tests/test_web_app.py tests/test_web_run_settings.py tests/test_a5_settings_ui.py tests/test_web_zotero_capture.py tests/test_web_connector_bridge.py tests/test_batch_import.py --tb=short`
completed with **366 passed**, including the then-current 25 A5 cases and executable Node
harnesses. The earlier 661 result is reused for the application/Settings/save
mechanisms unaffected by those final presentation changes. `./connector/test.sh` completed with
**82/82** harness cases. No full pytest rerun was needed; original full-suite
evidence applies only to unchanged inputs.

The final Kept-list marker notice makes pending/uncertain protection visible
without selecting a Paper or starting import. Both marker states leave bytes
unchanged and the batch idle in the added regressions. The final
`uv run pytest tests/test_unified_import_ui.py tests/test_web_app.py --tb=short`
completed with **107 passed**, including all 27 A5 cases; prior unaffected evidence
is reused as above.

**Rendered evidence:** a separate headless WebKit test runtime, isolated outside
the repository with fresh temporary configuration/Papers, ran actual shipped
HTMX and JS against the local Web adapter. Desktop **1470px** and narrow
**550px/390px** views had no horizontal overflow; screenshots were inspected.
Unavailable/running/completed presentation, disabled active import, safe DOI
link, two synthetic parent callbacks with PDF `unverified`, real Markdown refresh,
Inbox membership and Settings rendering passed. This is synthetic browser/bridge
evidence, not user Chrome, Zotero, physical-mobile or live capture acceptance.

**Preservation and remaining gates:** the A5 diff and `git diff --check` passed;
all **177** protected files matched preflight bytes. Earlier SPEC sections and all
unrelated uncommitted work remain intact. No actual user Workspace, Chrome or
Zotero was operated; no commit, push, tag, release or branch switch occurred.
Independent review and later explicitly authorized live/integration/distribution
gates remain. The Paper-only marker observability limits in §42.10 still apply.
A5 rollback uses its external preflight snapshots and removes only its source,
template/style/JS, test and evidence hunks, preserving preceding A0–A4 work.

**A5 review fix 1: EXECUTED; independent review pending (2026-10-08).**
Independent review identified a cross-Workspace authorization gap missed by the
original A5 tests: a valid form rendered for A could start B after `output_dir`
changed. Before the production fix, the two new isolated regressions reproduced
HTTP 200 and a B command after both an external config edit and an actual Settings
Save. Both expected HTTP 409 assertions failed. The current target-bound token
closes this gap without a client path field or persistent form registry.

Rendering uses the same parsed configuration for the displayed Papers and token;
a changed configuration/identity during rendering issues no usable authorization.
Submission checks the target-bound signature and consumes the nonce under the
existing submission exclusion. The A4 `start()` API now accepts one optional
caller target precondition: after scanning under the actual Workspace operation
lock, it verifies that frozen authorization before publishing the plan or writing
any pending reservation. This small interface extension closes a change between
the Web check and worker startup, including a change during scanning. It leaves
A1–A4 marker, ownership, native attribution, completion and stale-authority
mechanisms intact. No new batch state, queue, durable token database or Connector
contract was added. Settings retains its existing shared Workspace exclusion.

All commands and reservations continue using the frozen authorized Workspace;
later external configuration writes cannot redirect them to a new Workspace.
This is target binding, not revocation of an already exposed browser command.
An uncooperative edit after authorization cannot prove that an old native save
ended; the existing marker and paused-authority rules and §42.10 observability
limits still apply. Requests rejected by the form check leave the batch idle;
startup target conflicts expose a blocked snapshot without a plan or pending
write. HTTP 409 returns a visible warning and freshly loaded current Markdown;
the existing executable HTMX/Node harness verifies that 409 workspace fragments
are swapped into view. No frontend source or template change was required.

`tests/test_unified_import_ui.py` now has **39 passing cases**, adding 12
regressions for external and Settings switches, fresh B authority and application
bridge dispatch permission, stale replay/concurrent requests, two-page concurrent fresh requests,
configuration content/file replacement, resolved symlink retargeting,
Workspace/Papers/lock replacement, and page-read/startup/plan timing changes.
Rejected requests preserve both Workspaces' Paper bytes and attempt markers.
Existing presentation tests that mocked configuration now provide a real
temporary config revision; the Settings delegation test still forbids Web
persistence and distinguishes readonly import identity types from write code.

The affected command
`uv run pytest tests/test_unified_import_ui.py tests/test_web_app.py tests/test_web_run_settings.py tests/test_a5_settings_ui.py tests/test_application_settings.py tests/test_batch_import.py tests/test_export_attempts.py tests/test_capture_coordinator.py tests/test_zotero_capture.py tests/test_web_zotero_capture.py tests/test_web_connector_bridge.py tests/test_connector_outcomes.py --tb=short`
passed **580 tests**, with the two existing dependency warnings. Its **77** A4
cases include deterministic safety regressions and actual spawned processes:
different-Paper competition, process exit without browser-authority revocation,
hidden type-changed markers, and renamed `.txt`/`.bak` markers after an old process
received dispatch permission. These are real process tests, not live browser
saves. After the annotation/enum/assertion-only edits,
`uv run pytest tests/test_unified_import_ui.py tests/test_web_app.py tests/test_web_run_settings.py --tb=short`
passed **269 tests**, including all 39 unified import UI cases; no full pytest run was repeated. The previous A5
`./connector/test.sh` **82/82** result is reused for unchanged Connector inputs;
the affected Python bridge tests ran in the 580-case command above.

A separate temporary-Workspace probe, using actual application code and HTTP
endpoints without test fixtures, produced `STALE_FORM_HTTP 409`,
`STALE_FORM_VISIBLE True`, `NEW_WORKSPACE_RENDERED True`,
`NEW_PROCESS_COMMAND False`, `NEW_PROCESS_DISPATCH_GRANTED False`, and
`PAPER_BYTES_UNCHANGED True`. A fresh B page then produced HTTP 200, DOI
`https://doi.org/10.5555/b` and an accepted dispatch; a synthetic attributable
parent callback completed B through A3. This is isolated application/bridge
evidence, not Chrome/Zotero live acceptance or an independent code review.

The Fix 1 diff and `git diff --check` were inspected. All **177** protected files
match the earlier A5 snapshot; all **321** other pre-fix Git-listed inputs remain
unchanged. Preceding SPEC sections and Git HEAD/index are preserved.
Rollback removes only these Fix 1 source/test/evidence hunks using the external
pre-fix snapshots, retaining all A0–A5 work. Independent review and explicitly
authorized Chrome/Zotero live acceptance remain gates; A6–A9, publication and
real-user Workspace operations remain outside this task.

### 42.12 A6 Access Service preparation implementation evidence

**A6: EXECUTED; independent review pending (2026-10-08). Unreleased.**
This evidence covers V0_6_3_A6_ACCESS_SERVICE_PREPARATION on HEAD
`578b26b2025ba3b62789d150478e292e83579877`, preserving the uncommitted A0–A5
implementation, A4 serial authority and A5 Workspace-target binding. It does
not implement Institution Settings, Reset or live acceptance (A7–A9).

**Identity and route gate:** the AGPL `literature-monitor-access.js` overlay
consumes actual main-frame commits and HTTP redirect events for the currently
owned DOI task tab. It separates the DOI resolver, observed landing origin and
rule-verified SP. No Publisher metadata, OpenAlex Publisher identity, DOI prefix,
manual Access URL or IdP origin selects a service. SP mapping requires exactly
one reviewed rule matching the observed landing. Missing/ambiguous rules retain
unknown identity and normal official bibliographic capture plus manual Open DOI.

The production verified-rule table is **empty**: zero verified SP mappings and
zero enabled federation routes. No actual CARSI/Shibboleth/OpenAthens/WAYFless URL
has been invented or enabled. Future static rules require distinct evidence
references for SP, institution selector, allowed destinations, usable return and
preserved host association; exact public HTTPS origins and a safe static entry
must pass validation. The `.example` rules in Node tests are synthetic fixtures,
not actual institutional evidence and not production configuration. No browser
payload or Settings field can install a rule.

**Bounded preparation and fallback:** a fresh opaque context shared by the
current batch retains service outcomes only in Connector worker memory. At most
one automatic preparation per identified service/context is allowed. Each task
has a monotonic 20-second preparation budget and eight observed main-frame
redirect/commit hops; commits and redirects are conservatively counted together.
Repeated destinations, hosts outside a verified allowlist, form-submitted
challenges, errors and expiry stop preparation. Same-origin repeated HTTP
redirects can conservatively fall back rather than retaining sensitive paths.
Only a reviewed static entry and the original server-built DOI can be issued in
the dedicated normal, non-incognito task tab. Existing Chrome profile sessions
are used naturally; neither Cookies nor credentials are inspected or cleared.

The existing `webNavigation` and `webRequest` permissions were verified in the
pinned source and built MV3 manifest; no permission was added. A read-only
main-frame redirect observer does not redirect unrelated hosts or request
headers/cookies. Chrome's documented `documentId`, main-frame and transition
semantics were checked against the official
[webNavigation reference](https://developer.chrome.com/docs/extensions/reference/api/webNavigation).
Only observed events establish evidence: events before task-tab binding can be
missed, and this implementation does not claim complete chain observation or
network cancellation. Actual event ordering and proxy behavior remain live gates.

**Session/privacy boundary:** IdP session, SP session, authentication and
resource-level entitlement are four independent fields. All remain **unknown**;
there are no verified positive indicators or real authentication observations.
Even a tested route return is only navigation preparation, never authentication
or PDF/full-text proof. The bridge accepts only immutable public HTTPS origins
and fixed reason values, rejects paths/queries/fragments/userinfo/private literal
hosts and extra credentials/Cookie/SAML fields, and rejects unproven positive
session assertions. Full sensitive URLs/parameters are not retained by the access
policy, sent in summaries or written into Papers/configuration/logs/databases.
Opaque document identifiers and bounded navigation state remain in worker memory.
No browser storage/session persistence or OpenAthens host-association access or
mutation was added; preservation is required by rule policy. Actual coexistence
is not yet live-verified.

**Save authority and presentation:** preparation reuses the current capture's
A1 reservation and A4 ownership. It cannot grant native permission or consume
completion. An optional opaque `access_context` on the server claim groups batch
preparation; invocation payloads still contain only the A2 native identity fields.
The new `/api/connector/access` JSON bridge accepts one matching live claimed
attempt/tab summary before permission and rejects replay, wrong capability,
late/finished attempts and restart callbacks. Failure/challenge reasons deny
native dispatch. A safe pre-trigger failure can complete no-effect through A3
and let unrelated Papers proceed; an escaped dispatch remains uncertain/paused.

Every deferred readiness/navigation operation rechecks task identity, terminal
state and deadline. A new main-frame navigation after translator readiness
invalidates it before save; navigation after the translator trigger terminates
as uncertain. Timeout, late navigation/permission/authentication callbacks and
worker restart cannot revive the retired task. This uses the existing A2 native
fence, not an assertion that browser navigation or an old native save was revoked.
The Paper-only marker observability limits in §42.10 still apply. A5 polling shows
sanitized current/final access reasons and keeps completion ownership in the batch
executor. Open DOI remains the manual entry. The official translator/saveItems
hooks are unchanged; PDF remains unverified, without a downloader or attachment
manager.

**Executed verification:**

- `uv run pytest tests/test_access_preparation.py tests/test_unified_import_ui.py tests/test_web_app.py tests/test_web_run_settings.py tests/test_a5_settings_ui.py tests/test_application_settings.py tests/test_batch_import.py tests/test_export_attempts.py tests/test_capture_coordinator.py tests/test_zotero_capture.py tests/test_web_zotero_capture.py tests/test_web_connector_bridge.py tests/test_connector_outcomes.py --tb=short`
  passed **607 tests**, with the two existing dependency warnings. This includes
  27 new A6 HTTP/application cases, all 39 A5 UI/target-binding cases and 77 A4
  batch cases. A4 cases include real spawned-process competition, process exit
  after dispatch and type/extension-hidden unresolved markers. The new A6 tests
  verify privacy rejection, readonly polling, tab/permission attribution,
  failed-service isolation, restart/timeout fences and process-local contexts.
- `./connector/test.sh` passed **98/98**, including 16 new A6 cases and unchanged
  executed native A2/A4 hooks. Coverage includes synthetic DOI/SP mapping,
  unverified rules, host/return allowlists, one preparation per service, finite
  hops/loops, four unknown session fields, frozen OpenAthens associations,
  forbidden Cookie/storage access, deferred navigation timeout, challenge/late
  return, readiness navigation race, restart and late dispatch permission.
  These are deterministic mocked browser/federation events, not Chrome, Zotero
  or real institutional login acceptance.
- `connector/build.sh` reconstructed the exact pinned source/submodules and
  built Chrome/MV3 in `/tmp/lm-a6-connector.Ab37Oz`. Its patch/delta checks, overlay
  wiring, manifest and verbatim AGPL COPYING checks passed. After the final
  runtime race regression, the overlay-copy phase was refreshed from current
  sources; byte comparison, `node --check`, import ordering, existing permissions
  and zero production rules passed against the final artifact. The unchanged
  upstream build was reused, not rebuilt a second time.
- `uv build --out-dir /tmp/lm-a6-connector.Ab37Oz/python-dist` built wheel/sdist.
  Archive inspection confirmed the access model and updated template in the MIT
  wheel and excluded Connector-derived code and protected user paths from both
  artifacts. This is build/payload evidence, not installed or live acceptance.

The affected Python/HTTP and Node runtime tests ran in this task. No full pytest
was repeated; earlier full-suite evidence is applicable only to unchanged
implementation/dependency inputs. Browser/federation mocks never substitute for
future explicitly authorized live gates. Enabling any actual rule requires real
SP/selector/destination/return/host-association evidence and independent review;
positive session/authentication/entitlement reporting additionally needs reliable
separate indicators. Real Chrome/Zotero parent/PDF and institution acceptance
remain outside A6.

**Preservation and rollback:** the A6-specific source/test/documentation diff and
`git diff --check` passed. All 177 protected files, preceding SPEC bytes and
unrelated preflight Git-listed inputs, HEAD and index remain unchanged. No real
user Workspace, Chrome or Zotero operation, commit, push, tag, release or branch
switch occurred. The external preflight copies and A6-only diff are retained at
`/var/folders/7c/v718694145388ns274tht_l40000gn/T/lm-a6-preflight-c3uyy6kz`;
rollback removes only A6 hunks/new files while retaining A0–A5 work. Completion
now stops for the requested independent review.


**A6 review fix 1: EXECUTED; independent review pending (2026-10-08).**
The review found that the previously tested committed-only success path omitted
HTTP redirects during resource re-resolution. A conforming synthetic rule now
exercises the actual `observeRedirect()` and `observe()` together: initial
DOI-to-SP redirect, landing commit, issued federation entry, IdP commit, verified
SP return, issued original DOI, resolver commit, second DOI-to-same-SP redirect
and final landing. Before the fix, `./connector/test.sh` passed **98/99**: the new
case failed with `redirect_loop` instead of the expected resource-stage reason.
This red evidence is retained in the external pre-fix snapshot directory.

The resource stage now permits one expected resolver redirect only after the
verified SP `returnPath` and actual issuance of the original task DOI. It requires
that exact source DOI and a landing origin allowed by the current rule, records
only that public origin, and binds the final committed landing to it. No history
is cleared. Repeated resolver returns, another DOI/query, an IdP destination,
untrusted hosts, other same-origin repeats and mismatched final landings cannot
borrow this exception. The federation-return exception also requires an actually
issued entry. Both origin-history checks were inspected: the committed resource
transition was already stage-specific, but could overwrite a preceding failure
reason/cache entry; both observers now ignore failed as well as closed journeys.
Late events therefore cannot turn fallback into a prepared service observation.
The native dispatch/reservation/completion contracts and runtime source are
unchanged by this fix.

The limits remain 20 seconds and eight observed redirect/commit hops. The mixed
success case completes at exactly eight hops. A longer, otherwise valid synthetic
flow with one additional HTTP redirect reaches a ninth hop and explicitly falls
back with `hop_limit`, without retry. Future real rules must demonstrate a usable
flow inside these limits; this fix does not claim arbitrary real federation
chains can complete. The production rule table remains empty, and all four
session/authentication/entitlement observations remain unknown.

Final `./connector/test.sh` passed **110/110** (12 new cases since the original
A6 evidence). In addition to the red-green path, tests cover wrong return paths,
unverified path policy, source/host/landing binding, one consumed exception,
retained loop history, ninth-hop fallback, expiry/closed journeys and once-per-
service reuse. The runtime coexistence success test now also combines actual
mocked main-frame redirect and committed events, including the second DOI
resolution, before its single official translator trigger. Existing failed-SP
isolation, late navigation, worker restart and executed A2/A4 one-shot native
hook regressions remain passing. These are synthetic browser/federation events,
not live authentication evidence.

`uv run pytest tests/test_access_preparation.py tests/test_batch_import.py tests/test_capture_coordinator.py tests/test_unified_import_ui.py tests/test_web_connector_bridge.py tests/test_connector_outcomes.py tests/test_web_zotero_capture.py --tb=short`
passed **197 tests**, with the two existing dependency warnings. It includes A4
actual spawned-process exclusion/stale-marker cases and A5 Workspace-binding
regressions. Python inputs are unchanged; the earlier 607-case command remains
applicable to its unchanged Settings and other Python coverage. No full pytest,
Python packaging rebuild or unchanged upstream Connector rebuild was repeated.

Only the access overlay build input changed. The earlier isolated MV3 artifact
was checked against its pre-fix overlay, its copy step refreshed with the fixed
source, and all declared overlay bytes compared with current source. Final
`node --check`, worker import ordering, empty rules, MV3, verbatim AGPL COPYING,
applied upstream delta paths and `git apply --reverse --check` for both unchanged
patches passed. The existing upstream build/manifest and Python wheel/sdist
checks are reused within that unchanged-input boundary.

The fix-specific diff and `git diff --check` passed. All 177 protected files,
327 unrelated pre-fix Git-listed inputs, preceding SPEC bytes, HEAD and index
are preserved. Only the access overlay, Node tests and this current evidence were
changed. No user Workspace import/write, real Chrome/Zotero, A7–A9, commit, push,
tag, release or branch switch occurred. Red/green logs, the old overlay/test/SPEC
copies and the fix-only diff are retained under
`/var/folders/7c/v718694145388ns274tht_l40000gn/T/lm-a6-fix1-u1ncp10p`.
Rollback removes only these fix hunks and refreshes the external overlay copy
from its preserved pre-fix bytes, without resetting any A0–A6 work. Real service
routes, event ordering, host associations, authentication/entitlement indicators
and normal Chrome/Zotero acceptance remain separate explicitly authorized live
gates. Work stops for the requested independent review.

### 42.13 A7 Institution Settings and remaining stage ownership (2026-10-08)

**A7: EXECUTED; independent review pending. v0.6.3 remains unreleased.**
`V0_6_3_A7_INSTITUTION_SETTINGS` uses baseline
`578b26b2025ba3b62789d150478e292e83579877` and retains A0–A6, including their
review fixes. Remaining work is **A7 Settings → A8 Workspace Reset → A9 final
functional closeout and user manual Live Acceptance → A10 Final Audit/Release**.
A7 completes only Settings; it starts no Reset, manual acceptance or publication.

The existing `monitor.yaml` schema now accepts optional `institution.name`
(up to 120 Unicode letters/numbers/name punctuation) and
`institution.idp_entity_id` (a public HTTPS entity ID without credentials,
query, fragment or encoded payload, or a public URN; at most 512 ASCII characters).
These are opaque public context, never navigable login actions or route authority.
Missing/null/empty institution, null fields and blank strings mean unconfigured;
Save omits empty context. Existing configurations need no migration. Strict
validation rejects unknown/sensitive institution keys, invalid types, control/
invisible characters, malicious markup and unsafe entity IDs. Submitted duplicate
institution fields and extra credential/session/login-route fields are refused
without projecting their values into the returned editor.

Production Load, editable draft, form projection, shared Validate, Save and Reload
carry the same context. The existing revision and safe-write transaction is reused.
An institution-only edit bypasses metadata resolution and list rendering/writing,
while retaining Publisher membership and both original revision checks; a final
list revision check precedes monitor CAS. Monitor failure is `WRITE_FAILED`, and
CAS conflict is `REVISION_CONFLICT`, with no fictitious partial Journal write.
Combined Settings edits retain the existing two-file/partial-save semantics.
Tests retain Journal/Publisher metadata, manual URLs, Groups, CRLF/list formatting,
list file identity and Paper bytes. No Provider request or implicit import occurs.

The new fieldset uses the existing responsive Settings grid and one Save action.
It explains unconfigured/manual access and **Configured institution ≠ authenticated
session ≠ full-text entitlement**, including no IdP/SP login or Zotero PDF proof.
Journals, Publishers, Bulk Import, Groups, date policy and daily navigation remain
unchanged. A5's complete config digest/file identity continues to bind import
authorization: a successful institution edit rejects old forms; a config change
between POST and locked startup refuses before reservation. No new token mechanism
is added. A6's production `VERIFIED_RULES` remains empty; no identifier is sent to
Connector, no route is enabled and session/authentication/entitlement remain
`unknown`. Existing OpenAthens associations and manual Publisher URLs are separate.

**Automated verification:**
`uv run pytest tests/test_institution_settings.py tests/test_config.py tests/test_application_settings.py tests/test_a4_publisher_settings.py tests/test_a5_settings_ui.py tests/test_web_run_settings.py tests/test_unified_import_ui.py tests/test_access_preparation.py tests/test_batch_import.py tests/test_capture_coordinator.py tests/test_web_connector_bridge.py tests/test_connector_outcomes.py tests/test_web_zotero_capture.py --tb=short`
passed **632 tests** (including **48 A7 cases**), with two existing dependency
deprecation warnings. Coverage uses temporary Settings/Workspaces, HTTP requests,
fault injection, actual process/lock regressions and synthetic bridge events.
The executed Node Settings DOM harness verifies institution editing, dirty state,
HTMX preview/swap retention and Save feedback. `./connector/test.sh` passed
**110/110**, retaining unknown/manual fallback and OpenAthens/redirect safety.
`node --check` passed for `web/static/app.js` and the access overlay;
`uv lock --check` reported **Resolved 27 packages**; `git diff --check` passed.
No lint/static type checker is configured. No new dependency, payload location or
Connector build input is added; A7 does not repeat packaging/upstream builds or
claim existing A6 artifacts contain A7. Final changed artifacts belong to A10.
HTTP/DOM simulation and inherited responsive CSS are checked; actual browser
rendering and real-app behavior are not claimed.

**Manual Live Acceptance ownership:** all real normal Chrome, Zotero, institution
authentication and user Workspace scenarios in §42.10 are performed manually by
the user at A9. Codex handles source/tests/simulation/build verification and does
not operate those real environments or collect browser session evidence. ChatGPT
reviews supplied results and their evidence boundaries. The necessary §42.10
acceptance scope is unchanged; record each actual result as PASS, FAIL or NOT TESTED.
No missing manual result becomes PASS, and required live gates still precede any
RELEASED claim. **MANUAL_LIVE: NOT_TESTED — USER_OWNED.** At A9, check Settings
save/reload and narrow-screen readability, stale import-form refusal and manual
service access without inferred login/full-text/PDF success; retain the broader
batch/federation/PDF/preservation/isolated-Reset scenarios required by §42.10.

**Preservation/rollback:** the A7-only diff was inspected. All 176 protected files
under `monitor.yaml`, `src/.obsidian/` and `workspace/`, other pre-existing inputs,
HEAD and index are unchanged. Only config/Settings/form/template, A7 tests and this
appended record change. Pre-A7 copies and the task diff are retained externally at
`/var/folders/7c/v718694145388ns274tht_l40000gn/T/lm-a7-preflight-g6nip3tk`;
rollback applies only A7 hunks/removes the new A7 test, preserving A0–A6. No commit,
push, tag or Release is created. Work stops for ChatGPT's AgentDock independent
review; A8, A9 and A10 remain pending.

**A7 review fix 1: EXECUTED; independent re-review pending (2026-10-08).**
`V0_6_3_A7_REVIEW_FIX_1` corrects an incomplete form blacklist: unknown IdP/auth
fields such as `idp_password` were discarded but their submissions still reported
`Settings saved.` and emitted `settingsSaved`. Tests were added before production
edits. `uv run pytest tests/test_institution_settings.py -k 'unknown_sensitive_save_fields or import_draft_rejects_sensitive' --tb=short`
failed **38/38** on the old implementation, then passed **38/38** after the fix.
They exercise ordinary and HTMX Save, plus Import Preview/Apply, using temporary
Settings/Workspaces and synthetic credential markers. Cases cover `idp_password`,
`idp_cookie`, `idp_session`, `idp_token`, `saml_assertion`, authentication/cookie/
session/credential/OAuth/Shibboleth/OpenAthens variants and case/dotted aliases.

The shared form adapter now accepts only the existing editor/import fields and
explicitly rejects unsupported inputs with a static error, without reflecting
their values. The two legitimate institution fields and duplicate-field check
remain. Validation-error HTML uses the existing HTTP 200 fragment behavior;
neither Save nor Apply reports success or emits its success event. Tests verify
unchanged monitor/list/Paper bytes, no credential echo in HTML/headers, and retained
public institution draft. An initial compatibility run found the existing rule
that forged `resulting_journals` and `plan_can_apply` browser hints are ignored;
these two inputs retain that inert behavior, while Apply still replans entirely
on the server. No browser plan gains authority. InstitutionConfig, persistence
types, routes, session handling and federation functionality are unchanged.

Final `uv run pytest tests/test_institution_settings.py tests/test_application_settings.py tests/test_a4_publisher_settings.py tests/test_a5_settings_ui.py tests/test_web_run_settings.py tests/test_unified_import_ui.py tests/test_access_preparation.py --tb=short`
passed **462 tests**, including **86 A7 cases**, with the two existing dependency
warnings. This includes normal save/clear/reload, CSRF, revision/storage failures,
HTMX/Node DOM execution, Journal Import draft retention, Publisher/Group/date and
legacy-import compatibility, stale A5 tokens and A6 unknown/manual fallback.
The earlier A7 Connector **110/110** evidence is reused only for unchanged
Connector source/test inputs; it is not a new fix run. No full suite, build or
real Chrome/Zotero/institution/User Workspace operation is performed.
**MANUAL_LIVE: NOT_TESTED — USER_OWNED.** A8–A10 remain pending.

The fix-only diff and `git diff --check` passed. All 176 protected files and other
pre-fix inputs outside the three changed files retain their exact bytes; HEAD and
index are unchanged. Pre-fix copies, red/green/compatibility logs and the fix-only
diff are retained under
`/var/folders/7c/v718694145388ns274tht_l40000gn/T/lm-a7-fix1-_xjvnx9n`.
Rollback removes only these fix hunks, preserving all A0–A7 work. No commit, push,
tag or Release is created. Work stops for ChatGPT's independent re-review.

### 42.14 A8 Safe Workspace Reset implementation evidence (2026-10-08)

**A8: EXECUTED; independent AgentDock review pending. v0.6.3 remains
unreleased.** This stage implements only §42.8. A9 human live acceptance and
A10 final audit/publication remain separate. No actual user Workspace, Chrome
session, Zotero data or institutional authentication was accessed by Reset;
all destructive test operands were independently created synthetic temporary
Workspaces. **MANUAL_LIVE: NOT_TESTED — USER_OWNED.**

**Explicit user boundary:** Settings → Advanced & Diagnostics → Danger Zone
renders the current resolved absolute Workspace path, each verified removal
entry (including file names beneath Papers/, Authors/ and
.literature-monitor/), protected exclusions, and the destructive-operation
warning. The server stores a one-use nonce with the full plan, and requires
both the existing CSRF token and the exact user-entered `RESET`. Failed
confirmation is consumed, duplicate/subsequent submissions cannot replay,
and a fresh Settings page replaces the prior authorization. The POST accepts
neither a caller-selected path nor caller-selected removal entries.
The plan captures the monitor and Journal Settings revisions, Workspace
identity, per-directory identities and per-file identity, size, timestamp
and content digest. Reset acquires the Workspace operation lock and builds
a fresh complete plan before comparing to the displayed plan; changes or
configuration swaps refuse without destructive action.

**Safe removal scope:** The target check rejects filesystem root, Home,
repository/configuration root or unsafe ancestors, paths within browser or
Zotero data locations, symbolic-link paths, unknown or unowned objects and
unsupported file types/hardlinks. The same traversal requires a real
application-owned inventory, not merely named directories or .md suffixes.
Papers must parse as current supported documents with matching UUID
filename and no export_attempt (including malformed, pending or uncertain
attempts); Authors must parse as recognizable application notes; Inbox.base
must match the default application-generated bytes; the only accepted
metadata objects are recognized and parseable last-run.json and
provider-state.sqlite3. Unrecognized entries, files or nested directories
refuse the entire operation. Protected `monitor.yaml`, `list.md`,
all `.obsidian/` content, the permanent empty operation-lock inode, browser
data and Zotero data are excluded. No schema conversion, session reset,
Connector invocation or implicit Run occurs.

**Historical implementation boundary:** The recovery/staging mechanism below
was the original A8 implementation. The user decision and Review Fix 1 recorded
at the end of this section supersede its recovery requirements and implementation.

**Exclusive ownership and recovery:** Run now retains the same cross-process
flock from the initial provider phase through metadata persistence; Web Run
reserves it before worker startup. Existing Paper mutation, Batch and native
capture retain their A4 exclusion; Settings saves now acquire the lock even
without a Workspace path change. Persistent unresolved attempt markers
prevent Reset, including after process restart or where browser save
authority cannot be proved ended. On the supported POSIX lock/no-follow
filesystem boundary, a verified plan stages only its four allowed top-level
application output names in an exclusive, unpredictable directory inside
the same Workspace. Every move uses directory file descriptors; all
contents are rechecked after staging. An interrupted stage restores
unchanged objects where safe using kernel-level atomic no-replace rename;
occupied or changed destinations are never overwritten. An incomplete
rollback or cleanup reports failure with the recovery location and remaining
staged top-level entries. No unbounded recursive deletion, second workflow
database or operation-lock unlink occurs. No-replace rename support is
required before destructive work; unavailable platforms fail closed.

**Executed verification:** `uv run pytest -q tests/test_workspace_reset.py
--tb=short` passed **37/37**, including actual process-held flock exclusion,
Run-before-worker ownership, real file inode/contents substitution,
configuration and Journal revision conflicts, symlink/directory swaps,
pending export attempts, wrong/replayed confirmation and CSRF, protected
content, rollback/cleanup fault injection, and subsequent explicit
materialization. The affected A1–A7 Run/Settings/Batch regressions passed,
as did `uv run pytest -q --tb=short` across the full repository
(with two existing third-party deprecation warnings).
`./connector/test.sh` passed the existing Connector runtime cases without
rebuilding the unchanged upstream; the existing Node Settings DOM execution
is included in the full pytest. `uv lock --check` and `git diff --check`
passed. No configured ruff/mypy/pyright lint or type-check target exists.
The full suite was run before the final atomic no-replace rollback change;
the complete Reset suite was rerun against that final change.
A prior assertion assumed same-Workspace Settings saves leave no lock inode;
it was updated to reflect the required durable empty lock inode. All
protected repository work and A0–A7 uncommitted changes remain preserved.

**Manual acceptance ownership:** Only the user performs A9 live tests on
a separate dedicated Workspace. Confirm the presented path and exact list,
protected configuration and Obsidian preservation, explicit Run regeneration,
and lack of changes to real Zotero data or the normal Chrome session. Do not
submit credentials, session data or a detailed operation log. Current
result: **MANUAL_LIVE: NOT_TESTED — USER_OWNED**. No commit, push,
tag, GitHub Release or A9/A10 work is authorized in A8.


#### A8 Review Fix 1 — user-revised permanent Reset and Run binding (2026-10-08)

**A8 Review Fix 1: EXECUTED; independent ChatGPT review pending.**
`V0_6_3_A8_REVIEW_FIX_1` uses the unchanged HEAD
`578b26b2025ba3b62789d150478e292e83579877` and preserves all pre-fix A0–A8 work.
The user explicitly defines Reset as permanent deletion with no Recovery,
Rollback, undo or recovery directory. This decision replaces the earlier
§42.8/§42.10 recovery contract and the historical mechanism above. F1 is no
longer a defect merely because partially deleted originals cannot be restored;
safe scope, error reporting and exclusion remain mandatory. F2, the Run lock
and output-target mismatch, remains a defect and is fixed here.

**Permanent deletion and partial failure:** `workspace_reset.execute_reset`
compares the complete current plan with the confirmed Workspace, monitor and
Journal revisions, object identities and removal inventory before deletion.
It opens the recognized folders without following links, retains the shared
cross-process lock and uses directory-descriptor-relative unlink/rmdir on the
fixed inventory only. Each mutation checks the target/revision, remaining
inventory, parent identity and file identity/content; final verification must
confirm every planned output is absent before SUCCESS. The staging directory,
move/restore logic, kernel no-replace rollback and `ResetResult.recovery` are
removed. No recursive scavenging, retry, new state database or automatic Run is
introduced. Protected configuration, `.obsidian/` and the permanent lock inode
are excluded; unknown objects, path/identity changes and unresolved attempts
continue to block or abort Reset.

`ResetResult` reports `deleted`, `remaining` and `unconfirmed` separately.
Completed deletion calls are recorded immediately. A failed call never counts
as confirmed deletion merely because its path is now absent. Remaining entries
must still match their original identities/content; missing, replaced or
unreadable entries and observed new/reappeared objects are unconfirmed.
Any deletion/validation error stops the operation and returns failure without
restoration or automatic retry. The Web route/template remove the recovery
location, disclose permanent deletion and display these three lists. The
necessary adjacent `app.js` change permits the Danger Zone's 400/409 HTMX
fragments to display; CSRF 403 and unexpected server errors keep their original
handling. HTTP and executable Node tests cover this actual reporting path.
Deletion is intentionally non-atomic, without a promise of recovery.

**F2 bound Run target:** The CLI Run engine loads one configuration snapshot
before acquiring its Workspace lock and passes it through preparation and
execution. Web Run verifies its pre-reserved lock against that same loaded
target. Materialization, Provider-state read/write and LastRunSnapshot use the
frozen output directory, with lock identity checks before persistence; later
configuration edits cannot redirect the current Run to an unlocked Workspace.
The next explicit Run loads the then-current configuration. Normal Provider
failure isolation, Run outcomes and CLI exit handling are preserved.

**Executed regressions and checks:** Before the F2 fix,
`uv run pytest tests/test_application_monitor.py -k
run_config_switch_at_preflight --tb=short` failed both CLI-engine and real Web
worker cases: a CHECKING_MONITOR callback changed the configured target from A
to B, and actual Provider/last-run persistence created `.literature-monitor/`
in unlocked B. After the fix both cases pass: B's personal file and inventory
remain unchanged, while real materialization, Provider SQLite and last-run
writes complete in locked A. Only remote Provider computation is mocked.
Before the HTMX correction, `uv run pytest tests/test_workspace_reset.py -k
reset_failure_htmx_swap_runtime --tb=short` failed because the actual app.js
handler did not swap a Danger Zone error fragment; after correction it passes.
A further fault-injection test introduced a personal top-level file after the
fresh inventory returned but before the first deletion. It failed against the
initial rewrite, which treated every extra root entry as protected. The fixed
preserved set contains only explicit protected names and the operation lock;
the same scenario now refuses before deleting anything and reports the new file
as unconfirmed. Red/green evidence uses `uv run pytest
tests/test_workspace_reset.py -k unknown_root_file_introduced --tb=short`.

The targeted Reset/Run/coordinator command
`uv run pytest tests/test_workspace_reset.py tests/test_application_monitor.py
tests/test_run_coordinator.py --tb=short` passed **114 tests** before the final
HTMX and post-inventory race regressions were added. Final
`uv run pytest --tb=short` passed **3014 tests**,
including all **46 Reset cases**, both F2 cases, executable Node Settings/Reset
handlers and unaffected Provider/CLI/Settings/Batch/Capture tests, with only the
two existing third-party deprecation warnings. The Reset cases use isolated
temporary Workspaces and exercise permanent deletion, unknown/dangerous/link
refusal, changed file/parent identity and revisions, mid-delete unlink/rmdir
faults, missing response, newly introduced/replaced/reappeared objects,
configuration switching, protected-content retention, pending export_attempts,
CSRF and one-use confirmation, and explicit Run regeneration. A real child
process paused inside Reset's destructive phase proves exclusion of CLI Run,
Web Run startup, Settings save, Batch startup and Capture attempt reservation;
none writes or publishes a save command while that lock is held.
`uv lock --check` passed. No configured lint/type-check target exists. Connector
inputs are byte-identical to the pre-fix inputs; the earlier §42.14 Connector
verification remains applicable and no Connector build is repeated.

**Scope and retained evidence:** Changes are limited to SPEC, Reset, the shared
Run engine, their tests and the necessary Web route/template/HTMX reporting
callers. All 177 protected files (`monitor.yaml`, repository `list.md`,
`src/.obsidian/`, `workspace/`) retain their pre-fix bytes. Other existing inputs,
HEAD and the index remain unchanged. Pre-fix source copies, F2/HTMX/inventory-race red/green
logs, full/targeted test logs and the fix-only diff are retained under
`/var/folders/7c/v718694145388ns274tht_l40000gn/T/lm-a8-fix1-0d2u_3rj`.
If a source correction is needed, revert only this fix's hunks against those
copies; this development source reversion provides no Reset recovery feature.
`git diff --check` and the fix-only whitespace check passed.
**MANUAL_LIVE: NOT_TESTED — USER_OWNED.** A9 manual acceptance and A10 final
audit/publication remain separate; no real Chrome, Zotero, institution or user
Workspace was operated. No commit, push, tag or Release was created. Work stops
for ChatGPT's independent review.


#### A8 Review Fix 2 — F3 deletion-object binding (2026-10-08)

**A8 Review Fix 2: EXECUTED; independent ChatGPT review pending.**
`V0_6_3_A8_REVIEW_FIX_2` preserves the unchanged HEAD
`578b26b2025ba3b62789d150478e292e83579877`, all existing A0–A8 work and the
user's permanent-deletion/no-Recovery/no-Rollback contract. F3 is confirmed:
Fix 1 verified an object and then unlinked/rmdir'd its public name. A writer
replacing that name after verification could cause deletion of an unverified
object and an incorrect success result. The direct public-path disposal branch
in the preceding Fix 1 record is superseded by the following safety boundary.

**Object custody before deletion:** Reset still validates the complete confirmed
plan, revisions, Workspace, file/parent identities and content under the shared
cross-process lock. It then creates an unpredictable, owner-only (`0700`)
operation-local deletion directory, opened without following links and bound
by its directory descriptor and identity. Each output is atomically moved into
that exclusive namespace without overwriting a destination, and only then
compared with the original inventory's inode/type/size/timestamp/content.
A substitute captured by the move fails validation and is never unlinked.
Both file unlink and empty output-directory rmdir operate only relative to the
isolation descriptor, not the replaceable public Workspace names. Papers,
Authors, Inbox.base and both runtime metadata files share these same branches.
Public-name reappearance or any remaining inventory/target mismatch returns
failure even where the verified original has already been deleted. Final
success requires all planned originals absent, no public substitutions and
removal of the empty operation-local directory.

The native claim requires macOS `renameatx_np(RENAME_EXCL)` or Linux
`renameat2(RENAME_NOREPLACE)`. There is no ordinary rename/copy fallback.
Missing platform support, unsupported filesystem behavior or an occupied
isolation destination stops before deleting a captured unknown object.
The executed filesystem evidence is macOS with Python 3.12 and independent
local temporary Workspaces; Linux's syscall branch is not a Linux runtime
acceptance claim. The private namespace is exclusively owned by this Reset,
unpublished while active, and excluded from other application writers. Its
location, inode, permissions and exact custody inventory are checked. The
regressions cover competing writers replacing public paths and intrusion at
the atomic-claim destination; they do not claim privilege isolation against
an adversary able to control the Reset process or its private descriptors.

**Permanent semantics and F1 reporting:** Isolation is deletion safety custody,
not an undo/recovery feature. Successful deletion is irreversible; there is no
restoration, rollback, automatic retry or expanded recursive cleanup. Normal
success removes the empty isolation directory. On failure, an undeleted or
unverified captured object remains intact at its reported isolation location;
only an empty verified isolation directory may be cleaned. Such leftovers are
not recovery snapshots or a persistent state database. A later Reset treats
them as unknown content and refuses rather than resuming disposal. Existing
`deleted`, `remaining` and `unconfirmed` lists continue to distinguish actual
outcomes; retained entries include their actual isolation path. Missing/lost
responses never count as confirmed deletion. Originals moved elsewhere by a
competing writer are unconfirmed; Reset does not search outside its target to
invent their location. Generic Web/HTMX rendering displays the updated labels
without new route, configuration or template changes.

**Red/green and executed checks:** Before production changes,
`uv run pytest tests/test_workspace_reset.py -k
public_path_replacement_at_final_delete --tb=short` failed **8/8** cases because
ordinary replacement files/empty directories were actually removed. After the
fix the same eight pass: every substitute remains, Reset returns failure and
its original-object outcome is reported truthfully. Eight further atomic-claim
substitution cases cover all five file categories and three directories;
identical valid application bytes on a new inode still fail authorization.
Additional cases cover captured symlinks, missing platform/filesystem support,
non-overwrite destination collision and a lost native-move response. Two real
child-process races replace the Paper before native claim or immediately before
unlink; both preserve the substitute and report failure. The scoped sibling
sweep confirms both Reset application-disposal sites use isolated descriptors;
the remaining rmdir is restricted to the empty operation-created container.

The targeted A8/Run/Settings/Web command
`uv run pytest tests/test_workspace_reset.py tests/test_application_monitor.py
tests/test_run_coordinator.py tests/test_application_settings.py
tests/test_web_run_settings.py tests/test_a5_settings_ui.py
tests/test_institution_settings.py --tb=short` passed **441 tests**.
Final `uv run pytest --tb=short` passed **3037 tests**, including all **69 Reset
cases**, F1 partial-deletion reporting, F2 frozen CLI/Web Run Workspace binding,
CSRF/RESET/revision/export_attempt protection, multi-process exclusion, explicit
Run regeneration and executable Node Settings/HTMX checks. Only the two existing
third-party deprecation warnings remain. `uv lock --check`, `git diff --check`
and fix-only whitespace checks passed. No configured lint/type-check target
exists. Connector inputs did not change; prior §42.14 Connector evidence is
reused without rebuilding or repeating real browser operations.

**Preservation and scope:** Only `application/workspace_reset.py`,
`tests/test_workspace_reset.py` and this appended §42.14 record changed. All
177 protected files and other pre-fix inputs retain their bytes; HEAD and index
remain unchanged. Pre-fix source copies, eight-case red/green logs, targeted/full
logs and the fix-only diff are retained under
`/var/folders/7c/v718694145388ns274tht_l40000gn/T/lm-a8-fix2-28igp8c_`.
Development source reversion can remove only these fix hunks against those
copies, preserving earlier work; it provides no restoration of Reset-deleted
content. **MANUAL_LIVE: NOT_TESTED — USER_OWNED.** No actual user Workspace,
Chrome, Zotero or institutional session was operated. No A9/A10, commit, push,
tag or Release was started. Work stops for independent ChatGPT review.


### 42.15 A9 final functional closeout (2026-10-08)

**A9: EXECUTED; independent ChatGPT review pending. v0.6.3 is NOT RELEASED.**
`V0_6_3_A9_FINAL_FUNCTIONAL_CLOSEOUT` keeps HEAD
`578b26b2025ba3b62789d150478e292e83579877` and all preceding uncommitted A0–A8
work. The current handoff reports A7 Fix 1 and A8 Fixes 1–2 independently
reviewed, with **A8 Fix 2: REVIEW_PASSED**; this is the user's supplied review
status, not a new independent verdict by this executor. Historical implementation
and pending-review records above remain unchanged. Stage ownership remains
**A7 Settings → A8 Reset → A9 functional closeout/user manual acceptance →
A10 Final Audit/Release**. A10 has not started.

**Integration review and one observed correction:** Source, callers, current
cumulative diff (including new application/HTTP tests) and existing evidence were
checked across Run → Inbox decisions → historical Kept → serial Connector batch
→ parent/PDF outcomes and guarded export completion. CLI and Web Run retain one
frozen, locked Workspace for materialization, Provider state and LastRunSnapshot.
Reruns retain UUIDs, rejected/exported states and human content. Serial native
permission, failure isolation and durable pending/uncertain markers remain in
force. Ordinary file exports cannot mark Zotero completion; retired per-Paper
Save/Check endpoints have no save/reconciliation authority. Settings retains
revision/CSRF/safe persistence and invalidates old import context tokens. The
three main navigation entries remain Inbox / Kept / Settings. Institution context
is not authentication; production `VERIFIED_RULES` is empty and all four access
observations remain unknown. Manual Publisher URLs and OpenAthens associations
retain their separate boundaries. Reset's verified inventory, atomic object
custody, shared cross-process lock, partial-failure reporting and explicit Run
regeneration are unchanged from reviewed A8 Fix 2.

The review found that Web Reset used `snapshot().attempt is not None` as an
active-save test. After normal CONFIRMED or proven NO_EFFECT completion,
`finish_resolution()` retires authority/releases ownership but intentionally
retains the FINISHED result for display. Reset therefore incorrectly refused
until application restart. `CaptureCoordinator.has_unresolved_save_authority`
now reads under the coordinator lock and checks actual active ownership,
undelivered/delivered/resolving completion, active stage and escaped-grant
uncertainty. Web Reset uses this property. No capture state machine, persistence,
deletion execution or retirement rule was changed. The sibling sweep found no
other Web permission gate using the removed snapshot-presence condition; remaining
FINISHED checks format display results. Internal security/architecture checks and
four read-only adversarial angles returned no additional concrete defect. These
checks do not replace the requested independent ChatGPT review.

**Red/green and current automated evidence:**

- Before production edits, `uv run pytest tests/test_a9_integration.py --tb=short`
  produced **2 failed / 7 passed**: both safely retired batch outcomes failed to
  reach the Reset executor. After the fix it produced **9 passed**. The seven
  refusal cases cover waiting, claimed, dispatched, pending, delivered, resolving
  and uncertain authority. All use an isolated temporary Workspace and replace
  `execute_reset` with a non-deleting stub; actual file bytes remain unchanged.
  HTMX failure output remains truthful and emits no success event.
- A8 Fix 2's final diff was reproduced byte-for-byte from its pre-fix snapshot;
  all other pre-A9 test/implementation/dependency inputs matched. Its recorded
  `uv run pytest --tb=short` **3037 passed**, including **69 Reset cases**, is
  reused for the unchanged Reset implementation and fault/concurrency coverage.
  This is pre-A9 evidence, not a full-suite run of the new coordinator property.
- Because the property/Web caller changed, current
  `uv run pytest --ignore=tests/test_workspace_reset.py --tb=short` passed
  **2977 tests**, including the nine new HTTP cases, executable Node UI tests,
  batch/attempt/capture/access/Settings/DOI/ISSN-L/Provider regressions and actual
  spawned-process tests. Only the two existing dependency deprecation warnings
  remain. The 69 disposal tests were deliberately not rerun: A9 forbids actual
  permanent deletion, their Reset implementation inputs are unchanged, and the
  changed Web permission boundary is covered by non-deleting HTTP regressions.
- `./connector/test.sh` passed **110/110** against current overlays and executed
  native hook fixtures. These mocked browser/Zotero/federation events establish
  serial/late-trigger/attribution/privacy behavior, never live authentication.
  The external A6 MV3 build is reused: current overlay bytes, pinned upstream and
  submodule revisions, four allowed upstream delta paths/reverse patch checks,
  worker import order, MV3 manifest, verbatim AGPL COPYING, Node syntax and zero
  production routes were checked. Connector executable build inputs did not
  change; its current-development README's obsolete “until A3” clause was fixed.
- The upstream ItemSaver **17 tests remain NOT_TESTED / unavailable**. A read-only
  Puppeteer path check still finds the required Chrome `150.0.7871.24` absent.
  No browser was downloaded/launched and the previously blocked suite was not
  repeated. This is an explicit unresolved verification boundary, not PASS or a
  waived release gate.
- `uv lock --check` and `git diff --check` passed. No configured lint/typecheck
  target exists. No `personal-dev-guard` entry point was discoverable in the
  available skills/tools, local skill/plugin inventory or Codex configuration;
  no claim of executing that guard is made. AGENTS, REVIEW_WORKFLOW and the
  task's scope/protected-state restrictions were applied; no guard override or
  approval rejection occurred.

**Packaging/install precheck, not A10 release artifacts:** An isolated external
copy of current tracked and application/test additions, excluding protected user
state, ran `uv build --offline --out-dir <external>/python-dist`. Wheel/sdist
built successfully with the deliberately unchanged **0.6.2** metadata. Both
archives include current modules/templates and MIT licensing, exclude Connector/
AGPL source and protected paths, and contain no user database or bytecode.
SPEC is outside the default sdist payload. Offline installation into a fresh
Python 3.12.14 venv and `uv pip check` passed (22 installed packages). From outside
the source checkout, installed CLI help, module import location, templates/static
HTTP requests and a synthetic materialize → Keep → institution Save/Reload →
serial mock HTTP parent-confirmed import → exported/PDF-unverified smoke passed.
It also verifies CSRF refusal, retired display authority and Reset preview only.
No Provider, real browser/Zotero or Reset execution occurred. A10 must build and
verify final versioned publication inputs; these preview packages are not published
v0.6.3 assets. README now separates current development use from unchanged v0.6.2
release/usage history, without a version bump or release claim.

**User-owned manual acceptance:** **NOT_TESTED — USER_OWNED** for every current
v0.6.3 live gate. The user reports each actual scenario as PASS / FAIL /
NOT_TESTED. ChatGPT reviews the results and evidence boundaries; Codex does not
operate real Chrome/Zotero, institutional credentials or a user Workspace.
The high-level directions are:

1. On a dedicated acceptance Workspace, check Run, immediate Keep/Reject and
   rerun/history preservation; verify only Inbox / Kept / Settings navigation.
2. Import historical and current Kept Papers across at least two actual Access
   Services; observe serial toolbar-free parent saves, separate PDF reporting,
   failed/unknown-service isolation and uncertainty/duplicate-save protection.
3. Check normal-profile institutional/manual access and OpenAthens coexistence,
   keeping configured context, session, authentication and resource entitlement
   distinct. No production federation route is enabled; no synthetic route or
   institution identifier may be counted as real authentication evidence.
4. Only on a dedicated disposable Workspace, manually confirm permanent Reset
   and later explicit Run; check protected configuration/.obsidian, existing
   Zotero items and Chrome session continuity, including non-reset history.

These directions preserve §42.10's required scope rather than replacing its
release gates. Unperformed federation/parent/PDF/session/preservation cases remain
NOT_TESTED; unavailable reliable PDF evidence remains unverified. No necessary
live gate may be bypassed to declare RELEASED. **READY_FOR_FINAL_AUDIT: no**:
user manual results and the unresolved upstream-test boundary remain outstanding,
as does this handoff's independent review. No new feature or A10 action is started.

**Scope, preservation and evidence:** A9 changes only README, Connector README,
this appended record, the coordinator property/Web Reset caller and the new
non-deleting HTTP regression file. All 177 protected files, other prior inputs,
HEAD and index retain their preflight state. Preflight copies, red/green and
current pytest logs, packaging source/previews/install smoke, preservation checks
and the A9-only diff are retained at
`/var/folders/7c/v718694145388ns274tht_l40000gn/T/lm-a9-closeout-x45xwk2j`.
Development source reversion would remove only these A9 hunks/new test against
the retained copies, preserving A0–A8; it does not restore Reset-deleted content.
No real Workspace mutation, actual Reset deletion, browser/Zotero/institution
operation, commit, push, tag or Release occurred. Work stops for the requested
independent ChatGPT review.

#### A9 user decision — whole-Workspace permanent deletion (2026-10-09)

**IMPLEMENTED / AUTOMATION-VERIFIED / UNRELEASED.**
`V0_6_3_A9_SIMPLIFY_WORKSPACE_RESET` applies the user's replacement contract:
one explicit confirmation permanently deletes the entire configured Workspace,
including `.obsidian`, custom/unknown files, modified Inbox.base and damaged or
old Papers. Historical pending/uncertain markers do not block Reset. §§42.8 and
42.10 now define this behavior; the earlier A8 inventory/object-custody contract,
its internal-file preservation promises and the preceding A9 Reset direction
retain historical status and are superseded. Import's durable pending/uncertain
duplicate-save protection remains in force. Reset cannot undo a Zotero save or
establish that another import is safe.

The Reset module shrank from 442 to 119 lines. Per-file business recognition,
inventory, content digests, isolated disposal and individual outcome lists are
removed. Confirmation retains configuration bytes and root directory identity.
Deletion uses the standard library's descriptor-based, symlink-safe `rmtree`;
unsafe targets, target/ancestor links and unavailable platform safety are refused.
Empty/absent targets succeed. Errors report failure and possible permanent partial
deletion, with no recovery or automatic retry. A later explicit Run recreates
application outputs, without restoring deleted user files or `.obsidian`.

The necessary shared lock now has an empty, private inode outside the Workspace;
its path hash names exclusion only, not a file inventory or workflow database.
Run/Batch/Paper writes use it through the existing operation lock. Settings also
locks absent targets, so removal of the Workspace/internal marker cannot split
exclusion. A still-live escaped browser grant retains the same external flock
after result resolution, even if the internal marker disappeared. Pure historical
markers carry no Reset authority. The UI shows the absolute path, whole-tree
warning and one typed RESET confirmation; CSRF, single-use server-held target and
HTMX handling remain. Adjacent Settings/capture/lock changes are required for this
shared exclusion; test form collectors were scoped to the Settings form because
Reset is now normally visible. No federation, credential or Connector inputs changed.

**Executed evidence:** All deletion scenarios use isolated synthetic temporary
Workspaces. New cross-process escaped-grant tests first reproduced deletion while
authority remained live, then passed after external ownership retention. The
internal-marker-loss variant independently failed before removing that dependency
from ownership retention, then passed. An unsafe external-lock error test first
raised an uncaught exception, then passed with a truthful Reset failure result.
Final `uv run pytest --tb=short` passed **3016 tests / 2 existing dependency
deprecation warnings / 18.90s**, including **39 Reset cases**, executable Node UI
tests and Import/Settings/Run/DOI/ISSN-L/Provider regressions. Coverage includes
unknown/legacy/uncertain content, internal `.obsidian`, empty/absent roots,
dangerous targets and symlink replacement, stale configuration/directory identity,
injected partial deletion and lost response, current Run/Batch/browser ownership,
competing writes after tree deletion, CSRF/HTMX and explicit Run regeneration.
`uv lock --check` and `git diff --check` passed. No configured lint/typecheck target
exists. The earlier Connector **110/110** result is reused for unchanged inputs;
upstream ItemSaver's unavailable Chrome-dependent tests remain NOT_TESTED.

README's unreleased development instructions match the replacement contract.
Earlier Python package/install previews no longer cover these changed sources or
embedded README; affected final artifacts must be rebuilt/checked in A10. No
release build, version bump, publication or A10 work was performed here.

**Preservation and handoff:** All 78 protected files from this task's current
preflight, unrelated accumulated A0–A9 changes, HEAD and index remain unchanged.
The user Workspace, monitor.yaml, list.md and src/.obsidian were never mutated.
Preflight copies, failure/passing logs, preservation evidence and the task-only
diff are retained at
`/var/folders/7c/v718694145388ns274tht_l40000gn/T/lm-a9-reset-simplify-kwm6o7eo`.
Source reversion can remove only these task hunks against those copies; there is
no recovery of Reset-deleted content. **MANUAL_LIVE: NOT_TESTED — USER_OWNED**.
Later manual acceptance should use only a dedicated disposable Workspace to
check the displayed path/whole-tree warning, deletion including `.obsidian`,
external configuration/Zotero/Chrome continuity and explicit Run recreation.
These directions do not waive §42.10's other live gates. Work stops for ChatGPT's
independent review; no commit, push, tag or Release was created.

#### A9 simplified Reset review fix 1 — dangerous targets and root binding (2026-10-09)

**IMPLEMENTED / AUTOMATION-VERIFIED / UNRELEASED.**
`V0_6_3_A9_SIMPLIFY_WORKSPACE_RESET_REVIEW_FIX_1` addresses independent review F1
(P0) and F2 (P1). Repository descendants outside the dedicated `workspace/` area
are refused, including `src`, tests, Connector, configuration, documentation and
Git metadata. Case aliases cannot bypass this repository boundary. A dedicated
Workspace inside the repository remains allowed. Whole-directory semantics,
unknown/damaged content and historical uncertain markers remain unchanged.

Before recursive deletion, the root is atomically moved into a fresh mode-0700
private sibling directory, then checked against the confirmed device/inode.
Only that captured confirmed root is passed to descriptor-relative `rmtree`.
A substituted root captured at rename is retained intact without calling rmtree;
its location is reported. A replacement at the public path is never a deletion
target and causes failure even when the confirmed root has already been deleted.
Partial deletion or uncertain errors may leave contents in the private binding
directory, whose location is reported; no automatic restoration, retry or recovery
feature exists. This adds root-level binding only, not per-file inventory, schema,
digest or object verification. External lock and Import/Run/capture behavior are
unchanged. §42.8 includes this safety boundary; the preceding simpler implementation
record retains historical status and does not establish these two missing checks.

**Red/green evidence:** Before implementation,
`uv run pytest tests/test_workspace_reset.py -k 'repository_non_workspace or repository_dedicated or public_root_replaced' --tb=short`
produced **7 failed / 2 passed**: six repository targets were accepted and a
replacement user directory was deleted. After the fix, the Reset suite passed
**51 tests**; the added case-alias test brought coverage to 52. Full
`uv run pytest --tb=short` passed **3029 tests / 2 existing dependency warnings /
19.11s**, including cross-process exclusion, Settings/Web/Node, Run regeneration
and Import pending/uncertain regressions. A final error-wording clarification and
an opened-file assertion proving actual deletion of the confirmed root's Paper
were checked by `uv run pytest tests/test_workspace_reset.py --tb=short`:
**52 passed / 0.97s**. Cases also cover directory/link/disappearance at atomic
capture, a link swap at recursive open and accurately reported retained partial
contents. All destructive tests used isolated temporary Workspaces.

`uv lock --check` and `git diff --check` passed. Connector inputs are unchanged;
their earlier 110/110 native-test evidence is reused. No new guard entry point
was available; the repository/task scope restrictions were applied without a
guard bypass. This task changes only workspace_reset.py, its test file and SPEC.
All 78 current-preflight protected files, unrelated A0–A9 work, HEAD and index
remain unchanged. Preflight source copies, red/green/full logs and task diff are at
`/var/folders/7c/v718694145388ns274tht_l40000gn/T/lm-a9-reset-review-fix-3rq526pu`.
Only these source hunks can be reverted against those copies; permanently deleted
Workspace content cannot be recovered. **MANUAL_LIVE: NOT_TESTED — USER_OWNED**.
No real Workspace, Chrome/Zotero, commit, push, tag, release or A10 action occurred.
Work stops for the requested independent ChatGPT review.

### 42.16 Connector challenge/readiness correction evidence (2026-10-09)

**Implementation requested and executed; uncommitted, unpublished.** Task:
`V0_6_3_CONNECTOR_CHALLENGE_READINESS_FIX`. Repository HEAD remains
`578b26b2025ba3b62789d150478e292e83579877` on `v0.6.2-development`.
This record appends to §42; earlier version and A-stage evidence retains its
historical scope. It establishes no v0.6.3 release readiness.

**Observed causes and engineering choice.** The previous runtime adopted
`getTabInfo(tabID).translators` without document attribution, treated navigation
after `translatorReady` as terminal before invoking saving, and A6 classified
repeated origins as loops even for ordinary DOI/challenge redirects. Pinned
`_updateInfoForTab()` resets the tab cache, including same-URL loads; asynchronous
`onTranslators()` reports can repopulate it and compete with iframe reports.
The top-frame `instanceID` is always zero. Pinned `PageSaving.onPageLoad()` assigns
its current document's translator array but exposes no detection-start provenance
and permits asynchronous completion. Its DOM-change hooks do not prove that a
late result began after challenge completion.

The implementation reads current top-frame `Zotero.PageSaving.translators` with
`browser.scripting.executeScript({target: {tabId, documentIds}, world: "ISOLATED"})`.
No new production detection API, translator framework, permission, dependency or
provider was introduced. A previously challenged document remains ineligible if
only its DOM/translator array changes; it must load a new document or reach safe
manual fallback. This is an intentional supported-capability boundary, not proof
that same-document verification completed.

**API feasibility, real isolated Chrome.** Chrome `154.0.8037.98` was launched in
fresh temporary profiles with local, non-sensitive test pages. The probe used the
pinned `PageSaving` source declaration with a synthetic translator array: it tests
isolated-world access, not real translator detection. Observations:

- Existing `webRequest`, `webNavigation`, `scripting` and host permissions allow
  a non-blocking main-frame `onHeadersReceived` listener with `responseHeaders`;
  the local response's `cf-mitigated: challenge` was visible.
- Top navigation `onBeforeRequest`, response headers, `onResponseStarted` and
  network completion lacked `documentId`; header/network completion preceded
  `onBeforeNavigate` in an observed run. Request IDs covered redirect segments.
- `onCommitted`, `getFrame`, injection results and runtime message senders exposed
  matching top-document IDs. A same-URL reload produced another ID; a redirect
  produced a final paper document ID. Targeting the removed ID was rejected.
- The runtime owns an `about:blank` tab before issuing the DOI navigation. Its
  latest main-frame request, pre-request document ID, navigation generation,
  network completion, active frame and repeated document checks prevent adopting
  an old active document or a late candidate. An uncorrelatable response or
  unavailable identity never grants readiness.

Primary API references: [Chrome webRequest](https://developer.chrome.com/docs/extensions/reference/api/webRequest),
[webNavigation event ordering](https://developer.chrome.com/docs/extensions/reference/api/webNavigation),
[scripting document targets](https://developer.chrome.com/docs/extensions/reference/api/scripting),
and [Cloudflare response marker](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/).
The requested [challenge.ts](https://github.com/chendefine/dsh-web-fetch-playwright/blob/main/src/challenge.ts)
and [provider.ts](https://github.com/chendefine/dsh-web-fetch-playwright/blob/main/src/provider.ts)
were inspected as design references for response evidence, JSD exclusion, bounded
waiting and rechecking after navigation. No code or dependency was copied.

**Implemented safety.** Response challenge evidence and exact DOM markers/title/
orchestrate structure forbid saving; ordinary JSD, standalone Turnstile and
article text do not qualify as full-page challenge evidence. No refresh, repeated
DOI request, credential entry, challenge interaction, Cookie inspection or secret
storage was added. The existing one 20-second monotonic AccessJourney preparation
budget covers all challenge, translator, online-check and access-report waits;
500 ms polls do not restart it. The existing post-save 75-second observation and
native stale-command protection remain separate from preparation.

Before saving, the runtime rechecks current normal task-tab identity, complete
navigation/document, DOM content/challenge, top-document translator, journey,
generation and deadline after asynchronous operations. Before save invocation,
navigation discards readiness and resumes the same task; after invocation it ends
uncertain and cannot invoke another save. The narrow added upstream change in
`src/browserExt/background.js` targets the automatic `translate` message at the
selected `documentId` and translator, bypassing the unbound background cache.
The manual branch and native Zotero `saveItems` implementation are unchanged.
Dispatch is consumed once and checks selected sender/frame/document and current
readiness before and after the bridge await. Parent acceptance retains matching
request/invocation/DOI/session/document attribution; unrelated later DOM or
translator changes do not replace its completion standard. PDF remains
`unverified`. Paper statuses, exact native DOI match, serial batching and durable
pending/uncertain semantics were not changed.

Ordinary DOI navigation compares origin/path fingerprints without retaining raw
paths or query values, allowing different same-origin paths and one same-URL
reload. Repeated no-progress documents, repeated redirect destinations, eight-hop
limits and timeout still fail. Verified federation keeps its origin allowlist,
fixed entry, SP return, original DOI return, one-time exception and single
preparation. `form_submit` still falls back conservatively: it does not reliably
prove human participation, and is not used to relax the authentication boundary.

**Executed verification.** Logs, local probe sources, baseline copies, task-only
diff and the independent MV3 artifact are under
`/tmp/lm-challenge-qaci43hh/` (external to the repository).

| Check | Actual result and coverage |
| --- | --- |
| Baseline `./connector/test.sh` | `110/110` passed before edits. |
| Final regressions against copied pre-fix overlays/patch | Exit 1, `108/132` passed; 20 of the 22 added readiness cases failed, demonstrating defects against actual pre-fix source. Original worktree was not reverted. |
| Final `./connector/test.sh` | `132/132` passed. Actual overlay/injected function and shipped dispatch/save hook executed in Node with per-document worlds, cache resets, request/header/navigation events and deferred callbacks. Save invocation, dispatch, native call counts and terminal bridge outcomes are asserted. |
| `uv run --locked pytest -q tests/test_connector_outcomes.py tests/test_a9_integration.py tests/test_web_connector_bridge.py tests/test_batch_import.py` | Exit 0; 117 passed, two existing deprecation warnings. Parent confirmation/Paper completion, attempt protection, bridge and serial batching covered. |
| `uv run --locked pytest -q` | Exit 0; 3029 passed, the same two deprecation warnings. These Python results remain applicable: subsequent edits affected only Connector JavaScript, tests and documentation. |
| `uv lock --check` | Exit 0; 27 packages resolved. |
| External `connector/build.sh` with `LM_CONNECTOR_WORK_DIR=/tmp/lm-challenge-qaci43hh/build-work` and `LM_CONNECTOR_OUTPUT_DIR=/tmp/lm-challenge-qaci43hh/chrome-mv3` | Exit 0; clean reconstruction of pinned upstream/submodules, ordered patch application, npm/build and MV3 output. Final overlay-only changes were restaged by the same declared overlay copy operation; upstream build inputs did not change. |
| Final artifact/source checks | Five declared upstream delta paths exactly match; reverse patch checks, all upstream/submodule pins, verbatim COPYING, unchanged manifest permissions, worker import order, final overlay bytes, JavaScript syntax and protected-path exclusions passed. Artifact retains version `0.6.2`; no version bump authorized. |
| Real Chrome final-artifact startup | Exact MV3 artifact loaded in a fresh temporary Chrome profile. An isolated proxy rejected all network traffic, including loopback; it observed rejected bridge and Zotero probes. Runtime/access overlays initialized, Chrome/MV3 flags were true, and no active save task existed. No real bridge command, personal Zotero data or user Chrome profile was accessed. This proves startup only. |
| `git diff --check` | Passed; current task diff/source and repository status inspected. No staging, repository commit, push, tag or release. |

The added cases cover header/DOM challenges, harmless JSD/Turnstile/prose,
new-document and same-URL passage, request/navigation disorder, early tab-creation
events, late DOM/online/access results, unsupported same-document redetection,
iframe/old cache rejection, finite waiting, missing identity/content, cancelled
and consecutive navigation, navigation after invocation/during dispatch, replay,
actual dispatch/native-save counts, and unchanged parent/PDF completion authority.
The same-shape sweep found readiness/navigation authority in the runtime and loop
policy in the access overlay; upstream cache/broadcast behavior is bypassed only
for automatic saves by the declared patch. No sibling application/provider
refactor was performed.

**Review correction: provisional navigation errors (2026-10-09).** Task
`V0_6_3_CONNECTOR_CHALLENGE_READINESS_REVIEW_FIX_1` remains uncommitted and
unpublished at the same HEAD. Independent review reproduced an old provisional
`onErrorOccurred` terminating preparation before a successful new document could
commit. Chrome documents multiple pending navigation sequences in one frame;
`processId` on `onBeforeNavigate` and `onErrorOccurred` is deprecated and `-1`.
Neither a shared frame/URL nor the error string identifies the current document.

Pre-save errors now retain pending-navigation invalidation and allow a new
verified commit within the unchanged 20-second preparation deadline. A late
error invalidates existing asynchronous candidates and can resume only the same
previously committed, active, error-free document after `getFrame()` revalidation
with matching task/generation. It cannot adopt another document, clear a newer
navigation, replace request/header evidence, or extend the deadline. Error event
document IDs remain in a task-local set: the errored document cannot gain
authority through an overlapping commit callback even before `getFrame()` reports
the error. Missing identity never grants a new document's authority. Current DOM,
translator and response qualification still runs before saving; challenged
documents and manual/authentication/federation restrictions remain unchanged.
Every post-invocation navigation error remains `UNCONFIRMED` with no second save.

Evidence is under
`/var/folders/7c/v718694145388ns274tht_l40000gn/T/lm-navigation-review-7f9roaul/`:

- Final `./connector/test.sh`: exit 0, **144/144** passed. Twelve new executable
  Runtime cases cover old provisional cancellation, different/same-URL new
  document IDs, delayed old DOM results/senders, late errors after/during commit,
  19-second recovery, original-deadline failure with zero saves, unavailable
  frames, late revalidation versus a newer navigation, missing error identity,
  error-event/frame disagreement, and post-save `UNCONFIRMED`. Existing header/DOM
  challenge, same-origin redirect, single dispatch, native save and parent
  confirmation regressions pass in the same suite.
- Final regressions against a copied pre-review Runtime: exit 1, **134/144**
  passed; ten new cases fail. The worktree was never reverted.
- Connector artifact: unchanged pinned upstream/submodule revisions and all
  applied patches revalidated, prior compiled upstream reused, declared overlays
  staged into a new external MV3 artifact. Its only changed build payload is
  `literature-monitor-runtime.js`; license, permissions, version `0.6.2`, worker
  order, syntax and protected-path exclusions pass. Chrome-generated ruleset
  metadata from the prior startup is excluded from the build-payload comparison.
- Exact final artifact startup: Chrome `154.0.8037.98`, fresh temporary profile,
  all network including loopback rejected by an isolated proxy, Runtime/access
  initialized and no active task. This is startup evidence only.
- The earlier **3029 passing Python tests** are reused: no Python source,
  dependency, template, configuration or native Zotero save input changed in this
  correction. `git diff --check` passes; no commit, push, tag or release.

Real Chrome provisional-error recovery during public DOI/Zotero import remains
**NOT_TESTED**, as do the genuine Cloudflare, parent/Paper and federation live
acceptance cases below. The absent upstream-required Chrome environment is
unchanged. This correction establishes no overall v0.6.3 release readiness.

**Live and review boundaries.** Real public DOI/Zotero import without a challenge,
actual naturally clearing Cloudflare, persistent/human challenge fallback,
post-invocation navigation in a real Zotero save, and real parent/Paper writeback
are each `NOT_TESTED`. The local header probe and executable synthetic regressions
are not Cloudflare Live PASS. Federation/CARSI remains independently `NOT_TESTED`;
zero production SP routes remain enabled. The upstream ItemSaver browser suite
was not repeated or declared passed; its required bundled Chrome 150.0.7871.24
was not available, and Chrome 154 API/startup probes do not replace that suite.
These gaps prohibit claiming complete v0.6.3 release readiness.

AGENTS and `/Users/adrian/Desktop/REVIEW_WORKFLOW.md` were read. AgentDock's
`skill://managed/personal-dev-guard/SKILL.md` was discovered and read during the
work; its readability, restrained design, Chinese core comments and complete
verification boundary were applied. It is an instruction skill, not a blocking
execution hook. Stop That Shit's unconfirmed Guard observation mode was not
claimed as enforcement. Existing unrelated changes, `monitor.yaml`,
`src/.obsidian/` and `workspace/` were not edited or staged.

Implementation is ready for the requested separate ChatGPT review through
AgentDock against the actual repository. No independent ChatGPT review has been
performed or represented by this execution report. Task-start source copies are
retained externally for a bounded rollback/review that preserves earlier dirty
work; do not reset the shared worktree to HEAD.

### 42.17 Reset guard lifecycle correction — development validation (2026-10-09)

**IMPLEMENTED / ISOLATED-TESTED / UNRELEASED.** Task
`V0_6_3_RESET_GUARD_LIFECYCLE_FIX` preserves the external shared/exclusive
Reset lock and replaces Workspace-level sticky uncertainty with separate
process-local native dispatch grants. A grant is retired only when the original
browser task reports actual completion of the upstream save pipeline, bound to
its original request, invocation, DOI, tab, document and native session.
This acknowledgment changes neither Paper state nor an old or current batch
result; repeated, missing, stale or mismatched receipts have no authority.
Until every grant for the Workspace is settled, the shared lock remains held
across later Imports and Reset still refuses. The bounded Connector delivery
attempts cannot manufacture a receipt when the bridge fails. Native request
errors, parent-only confirmation, observation timeout and a new successful
batch have no authority to release an old native grant. Successful confirmation
before the upstream attachment pipeline ends also retains ownership and prevents
the next batch dispatch. Once the correct receipt arrives, no historical
uncertain Paper result or retained FINISHED display blocks Reset.

**Pre-fix reproduction:** A new Web application was constructed on a temporary
synthetic Workspace and driven through dispatched UNCONFIRMED → explicit retry
CONFIRMED → COMPLETED batch. The final coordinator snapshot reported FINISHED /
CONFIRMED while Web Reset refused with HTTP 409 and left the test Workspace
intact. No request touched the user Workspace or running Web process.

**Current checks:** The isolated Python Web, coordinator, batch and Reset
regressions test the same sequence, guard-count ownership, mismatched/repeated
receipts, non-deleting HTTP Reset stubs, and actual cross-process flock exclusion
before/after a matching original receipt. The 11-module affected Python run
passed **374/374**, followed by **12/12** passing focused Reset/A9 regressions
after one additional error-message assertion. The pinned browser runtime's Node
harness passed **158/158**, covering late original-pipeline callbacks,
forged/stale sender rejection, native errors without clearance and finite
failed-delivery retries. `git diff --check` passes. All tests run without Zotero
or an ordinary Chrome profile; no test invokes Reset on the configured user
Workspace.

**Residual limits:** The on-disk change does not alter a Web process already
running an older coordinator or retroactively observe that process's past save
grant. If an original browser task disappears or an authoritative completion
receipt cannot be delivered, a running owner retains Reset exclusion; process
loss releases the operating system's flock without establishing native-save
completion. No local timeout, batch display or manual assertion repairs that
uncertainty. Real Chrome/Zotero and the existing Web process are not validated
or restarted by this task. Independent review and user-owned live acceptance
remain outstanding; no commit, publication or real Workspace Reset occurred.

### 42.18 v0.6.3 release preparation (2026-10-09)

**CANDIDATE PREPARED; PUBLICATION NOT YET VERIFIED.** This release preparation
updates Python package metadata and the root lock entry, OpenAlex/Crossref
User-Agent identities, the Connector build version and their version-specific
test assertions to 0.6.3. It preserves the A0–A9 source and Reset Guard Lifecycle
Fix, earlier version-specific contracts, user-managed Markdown and unrelated
local state. The remaining verification and publication actions are authorized
by the user's explicit release request.

**Current checks:** `uv lock --offline`, `uv lock --check` and the affected
OpenAlex, Crossref and Connector-boundary pytest run passed (**454 tests**).
`uv build --offline --no-sources` produced a 0.6.3 wheel and sdist;
both exclude the Connector and protected local paths. The wheel's MIT metadata
and version are correct. Offline installation into an isolated Python 3.12
environment passed `uv pip check` for **22 packages**, and the installed CLI
help executed. The Connector runtime Node harness passed on current sources;
`git diff --check` passed. Existing broader Python, Node and live evidence
is reused only where tested inputs remain applicable, without claiming a new
real-browser run.

**Connector construction:** The initial clean remote upstream fetch failed
with an empty HTTP response. An existing exact pinned upstream checkout with
the five locked submodules was copied outside the repository, its previous
source delta was reversed, and the current declared patches were applied.
Pinned revision, changed-path inventory, license equality and installed
Node dependencies were checked. The supported upstream 0.6.3 build succeeded;
the resulting MV3 manifest, worker overlay ordering, JavaScript syntax and
verbatim AGPL COPYING were verified against the current tracked overlays.
This was a fresh local build from verified cached source, not a fresh network
download.

**Live acceptance provenance:** The user reports completed independent
reviews, real Zotero parent/PDF save observations, batch import across two
Access Services, dedicated Workspace Reset → explicit Run and upstream ItemSaver
**17 tests**. These are user-reported or previously recorded evidence, not
newly executed live checks in this preparation. Application PDF reporting
remains `unverified` unless authoritative attachment completion is available;
the production verified federation rule table remains empty. No configured
Workspace Reset, normal Chrome/Zotero operation, commit, tag, push or Release
occurred during the preparation checks.
