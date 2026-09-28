# Literature Monitoring Workflow — Specification v1.4

**Status:** Active; v0.4.5 is the current/latest released and completed baseline

**Stage:** v0.4.5 released / closeout complete
**Scope:** Journal monitoring with CLI, durable Markdown workspace, Obsidian presentation, and a local Python Web UI adapter; conferences remain excluded

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

The `run`, `canonicalize`, and `materialize` CLI completion summaries display compact structured coverage without printing every successful unit. The Local Web finished-run view displays compact coverage only while the current process still holds the finished `RunResult`; it must not persist browser-side coverage, add run history, add a coverage API/database, or restore discarded process results after refresh. A4 adds the separate read-only `last-run` CLI diagnostic over the durable snapshot defined below; it does not restore a discarded Web `RunResult`.

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

§31 is the authoritative v0.4.4 development contract for Crossref Elapsed-Aware Pacing & Partial Provider Evidence Semantics. §30 remains the historical v0.4.3 release contract and continues to govern live membership, revision checks, normalized state, transport, concurrency, progress, and compatibility except for the conflicting Crossref pacing, OpenAlex normalization/partial evidence admission, and OpenAlex coverage/issue-severity clauses superseded by §31. Publication-date discovery, local filtering, canonical identity, and Markdown ownership remain preserved.

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

v0.4.5 §32 implementation is complete. A1–A6 and their independent stage reviews are complete; the following acceptance scenarios have been demonstrated and the final independent integration audit passed. The full automated suite passed (1877 tests, with two known dependency deprecation warnings). The desktop/mobile manual Web smoke passed during A6, and the final independent integration audit separately verified the corresponding desktop/mobile browser behavior. v0.4.5 is released and is the current/latest released and completed baseline.

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

The full closeout sequence is complete. v0.4.5 is released and is the current/latest released and completed baseline.

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
- workspace issues;
- Zotero export.

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

v0.4.5 is released and complete. Its §32 implementation and A1–A6 independent stage reviews are complete; §23.18 acceptance has been demonstrated, including the full automated suite and A6 desktop/mobile manual Web smoke. The final independent integration audit passed and separately verified the corresponding desktop/mobile browser behavior. Release preparation, release transaction, and closeout are complete (§24.13). v0.4.5 is the current/latest released and completed baseline.

---

## 28. Current Project Stage

R0–R3, v0.2.1 lexical search, v0.3.0 Review Inbox, v0.3.1 Prefix / Proximity search, v0.3.2 Persistent Monitor Definition, v0.3.3 Pre-GUI Correctness Hardening, v0.4.0 Python Local Web UI, v0.4.1 Runtime Progress, Activity, ETA, and Inactivity Feedback, v0.4.2 Provider Reliability, Coverage, and Explicit Cache Reuse, v0.4.3 Retrieval Efficiency & Revision-Validated Provider Evidence, v0.4.4 Crossref Elapsed-Aware Pacing & Partial Provider Evidence Semantics, and v0.4.5 Candidate Eligibility, Warning Semantics & Scrollable Master-Detail Workspace are completed release history. v0.4.5 is the current released and completed baseline.

The v0.4.4 release and closeout are complete, following §24.12. Its §31 implementation, independent stage reviews, and final independent audit are complete; the §23.17 acceptance scenarios have been demonstrated.

v0.4.5 Candidate Eligibility, Warning Semantics & Scrollable Master-Detail Workspace implementation, A1–A6 independent stage reviews, §23.18 acceptance, and full automated verification are complete. The A6 desktop/mobile manual Web smoke passed, and separate independent desktop/mobile browser verification passed during the final integration audit. §32 remains its authoritative behavior contract. Release preparation, release transaction, and closeout are complete (§24.13); the current stage is v0.4.5 released / closeout complete, with package metadata `0.4.5`. No subsequent product-development stage is established by this closeout.

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
