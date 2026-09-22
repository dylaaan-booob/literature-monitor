# Literature Monitoring Workflow — MVP Specification v1.2

**Status:** Active; v0.3.2 is the latest released version

**Stage:** v0.3.3 release preparation and closeout
**Scope:** Journal monitoring only; conferences are excluded from MVP

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
Review Inbox presentation in Obsidian Bases
    ↓
Human triage in Obsidian
    ↓
rejected / kept
    ↓
Export kept identifiers for Zotero import
    ↓
Human confirmation → in_zotero
```

MVP success means this complete workflow works reliably on the journal whitelist without requiring a separate GUI, Obsidian plugin, publisher scraper framework, or custom Zotero ingestion implementation.

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
- OpenAlex journal/date discovery;
- Crossref journal/date discovery and DOI enrichment;
- Semantic Scholar metadata supplementation and supplemental discovery;
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
- safe overlapping reruns;
- export of `kept` paper identifiers for Zotero;
- logs sufficient to inspect unresolved journals, failed enrichment, and partial metadata.

### 4.2 Explicitly excluded

MVP does **not** include:

- conference monitoring;
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
- GUI;
- web application;
- Obsidian plugin;
- recommendation ranking;
- citation-count ranking;
- journal ranking;
- durable database or another mandatory source of truth;
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
- persisted last-used date ranges, provider cursors, provider watermarks, late-index recovery cursors, checkpoints, run history, delta / What's New state, notification state, scheduler state, or automatic retry state;
- a persistent execution database;
- scheduler, daemon, or cron management;
- notifications;
- multi-monitor dashboard;
- Zotero API integration;
- Paper Markdown schema changes in v0.3.3;
- Track B workflow/state expansion beyond the current Markdown lifecycle;

SQLite FTS5 is permitted only as transient, reconstructible runtime state. It must not become durable workflow state or a mandatory second source of truth.

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

All retrieval remains journal-whitelist-first and date-bounded. OpenAlex, Crossref, and Semantic Scholar may each contribute evidence to the candidate universe. No provider has implicit canonical authority merely because its record was retrieved first.

Discovery date membership remains publication-date-only. Literature Monitor does not use Crossref update-date, created-date, index-date, provider update timestamps, or persisted synchronization metadata to expand a date window. v0.3.3 therefore does not provide late-index recovery, provider watermarks, checkpoints, or incremental-sync semantics. Semantic Scholar year-only records use the provider-specific filtering interpretation defined in §6.3 without fabricating a bibliographic publication date.

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

### 6.3 Semantic Scholar

Responsibilities:

- supplement metadata by DOI or batch lookup;
- perform venue/date-constrained supplemental discovery;
- contribute title, authorship, abstract, external identifiers, topics/fields of study, and provenance when available.

A broad positive query may be used only to expand provider coverage. Every returned record must still pass the configured date boundary and venue validation, and final inclusion is always decided by the unified local keyword expression.

When the local expression is projected into a Semantic Scholar supplemental-discovery query, Prefix operands retain their single trailing `*`. Proximity operands are not sent using provider-specific proximity syntax; each Proximity operand is reduced to a broad `AND` query containing all of its positive lexical terms. As with the existing broad-positive-query behavior, `NOT` subtrees are removed from the provider query rather than used to exclude provider results. This projection affects discovery recall only and never changes final local Boolean evaluation. OpenAlex and Crossref remain venue/date retrieval paths and do not acquire keyword-query responsibilities from this projection.

Venue validation must prefer ISSN/EISSN. Only when the provider record has no usable ISSN/EISSN may a strict normalized journal-name match be used as a fallback; ambiguous or mismatched venues must not enter the candidate universe.

Supplemental-discovery date validation follows Semantic Scholar's `publicationDateOrYear` contract. When a normalized record has an exact `publication_date`, that exact date alone determines inclusive membership in the configured date window, even when `publication_year` is also present. When the exact date is absent and only `publication_year = Y` is available, local provider-result validation interprets the filtering date as `Y-01-01`; the record is accepted only when that date falls inside the inclusive query window. When both date and year are absent, the record cannot prove date-window membership and is excluded.

`Y-01-01` is only a filtering interpretation for Semantic Scholar supplemental discovery. It is not a bibliographic publication date and must not be written to `SemanticScholarWorkRecord.publication_date`, `ProviderWorkEvidence.publication_date`, canonical metadata, or Paper Markdown. A valid year-only record does not produce a warning merely because this filtering rule was used.

### 6.4 Publisher fallback

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

All except the internal UUID are optional.

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

The purpose is to preserve enough provenance to understand where metadata came from.

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

Provider evidence may exist independently before consolidation. A Crossref-only work or a Semantic-Scholar-only supplemental work is valid input when it satisfies the journal/date boundary; canonicalization must not require an OpenAlex record.

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

SQLite FTS5 may be used only for the transient runtime index described in §7; no SQLite or other durable database is a mandatory second source of truth.

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
- Semantic Scholar ID;
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

State transitions in MVP are human-controlled:

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

## 18. Review Inbox Presentation

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

The following remain excluded unless a future independent Track B proposal changes scope:

- Maybe / Deferred states, rejection reasons, review labels, reviewer identity, review batches, review sessions, reviewed timestamps, bulk screening workflow, and priority state;
- BM25, semantic, citation, or LLM ranking;
- automatic summaries, relevance explanations, and recommendations;
- PDF preview or download;
- Zotero API integration;
- an Obsidian plugin or custom GUI;
- a persistent Inbox database.

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

The persistent policy and the resolved runtime range are distinct concepts. Defaults belong to the config/domain contract; CLI commands, provider adapters, materialization code, and any future GUI must not define competing default semantics. A future GUI, if separately brought into scope, must obey this same two-degrees-of-freedom date model.

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

`literature-monitor run --config monitor.yaml` is the normal persistent-monitor execution path. It obtains the following from the monitor definition after defaults and path/date resolution:

```text
journals
keyword expression
output directory
date policy
log level
```

Its production flow is:

```text
load monitor
→ resolve journals
→ resolve effective date range
→ retrieve provider evidence
→ consolidate evidence
→ local FTS5 filtering
→ canonicalize
→ materialize Papers / Authors / Inbox
```

`run` must reuse the existing complete materialization production pipeline. v0.3.2 must not create a second implementation of retrieval, Semantic Scholar supplementation, filtering, canonicalization, or materialization.

Existing diagnostic commands remain available. `materialize` remains an explicit legacy / diagnostic-style execution entry. Its existing explicit `--output-dir` behavior is preserved rather than redesigned here.

### 20.5 Validate configuration

Running:

```bash
literature-monitor validate --config monitor.yaml
```

has a strict boundary:

```text
load config / whitelist
→ deterministic local runtime preflight
→ OpenAlex Source resolution
```

Before any OpenAlex Source-resolution network request, validation must complete all deterministic checks required by a normal persistent `run`: strict monitor-field and whitelist loading, keyword parser syntax validation, date-policy form validation, FTS5-dependent lexical semantic validation of the configured keyword expression, and effective runtime date-range resolution from the monitor's `date_spec`.

Parser-valid expressions may still fail local semantic validation after SQLite FTS5 `unicode61` tokenization. Invalid Prefix or Proximity operands, including a Proximity operand that produces only one lexical token, are local validation errors. A Python SQLite runtime without FTS5 is a local runtime-preflight failure. Runtime date resolution that exceeds the supported `datetime.date` bounds is also a local runtime-preflight failure.

Configuration and deterministic local preflight failures exit with code `2` and must occur before any provider path is constructed or called. OpenAlex Source-resolution errors retain exit code `1`. Warning-only OpenAlex validation and fully valid validation both exit with code `0`.

`validate` performs no OpenAlex Works discovery, Crossref connectivity check, Semantic Scholar connectivity check, `output_dir` writability check, workspace creation, or Paper / Author / Inbox materialization. Its only provider/network validation is OpenAlex Source resolution.

### 20.6 Existing behavior preserved

The v0.3.3 hardening preserves the existing observable behavior of:

- OpenAlex retrieval;
- Crossref retrieval;
- Semantic Scholar DOI/batch supplementation;
- provider evidence consolidation;
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
- OpenAlex and Crossref can each contribute journal/date candidates independently;
- Semantic Scholar can supplement metadata and contribute supplemental candidates that pass venue/date validation;
- Semantic Scholar records with an exact publication date use that date even when a year is also present;
- a Semantic Scholar record with no exact date and only year `Y` uses `Y-01-01` solely for supplemental-discovery filtering membership; the interpretation is not stored as bibliographic metadata and does not itself produce a warning;
- a Semantic Scholar record with neither publication date nor year is excluded because date-window membership cannot be proven;
- a candidate is not required to exist in OpenAlex first;
- global keyword search is not used to define the candidate universe;
- Semantic Scholar broad positive queries do not decide final inclusion.

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
- Semantic Scholar supplemental discovery preserves the trailing `*` of valid Prefix operands, lowers each Proximity operand to a broad `AND` query over all of its positive lexical terms, and removes `NOT` subtrees from the broad positive provider query;
- Semantic Scholar query projection affects discovery recall only: provider evidence is still consolidated before local filtering, and provider-neutral final inclusion is determined only by the complete local Boolean expression;
- OpenAlex and Crossref retain their existing venue/date retrieval responsibilities and do not use the Semantic Scholar query projection;
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

Given valid evidence from multiple providers or from Crossref/Semantic Scholar without OpenAlex:

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
- `validate` does not perform OpenAlex Works discovery, Crossref or Semantic Scholar connectivity checks, output-directory writability checks, workspace creation, or Paper / Author / Inbox materialization;
- `literature-monitor run --config monitor.yaml` is the normal persistent-monitor execution entry and obtains journals, keyword expression, output directory, date policy, and log level from that monitor;
- all date-bearing commands expose `--from-date`, `--to-date`, and `--window-days` with the same date-resolution semantics;
- when no CLI date argument is supplied, the monitor date policy is used;
- once any CLI date argument is supplied, CLI date arguments completely replace the monitor date policy for that invocation and may not borrow missing components from config;
- `--window-days N`, `--from-date X --to-date Y`, `--from-date X --window-days N`, and `--to-date Y --window-days N` are valid complete CLI overrides;
- CLI `--from-date X` alone, `--to-date Y` alone, all three CLI date fields, zero/negative window sizes, and reversed ranges are rejected;
- the legacy `openalex-discover --config ... --from-date ... --to-date ...` form preserves its observable from/to behavior;
- CLI date overrides remain ephemeral and create no config mutation, last-used range, checkpoint, or other durable run state;
- `run` reuses the existing retrieval, Semantic Scholar supplementation, evidence consolidation, local filtering, canonicalization, and materialization production path rather than implementing a second pipeline;
- `materialize` remains an explicit legacy / diagnostic-style entry and its existing explicit `--output-dir` behavior is preserved;
- one monitor to one decision workspace is the supported product relationship; Paper UUIDs, statuses, notes, and other durable Paper state are workspace-local;
- different workspaces are not promised the same UUID for the same research work and do not inherit Reject/Keep decisions from one another;
- one monitor per config directory, or explicitly distinct `output_dir` values, is the recommended multi-monitor layout;
- two monitor YAML files in the same directory that both omit `output_dir` continue to resolve to the same `<config-directory>/workspace`; this is not rejected, but shared-workspace multi-monitor operation is unsupported / undefined advanced usage with no cross-monitor decision guarantee;
- no monitor UUID, workspace UUID, ownership marker, workspace registry, global research-work registry, cross-monitor Paper UUID, per-monitor decision object, monitor membership state, decision inheritance, last-run semantics, run history, scheduler/notification state, provider cursor/watermark/checkpoint persistence, or persistent execution database is introduced;
- Paper Markdown remains the durable workflow state, with no Paper Markdown schema change in v0.3.3 and no regression to workspace-local UUID stability, statuses, or human-note preservation.

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

---

## 25. Non-blocking Implementation Details

The following do not block implementation and may be decided locally as long as the specification's observable behavior is preserved:

- programming language/package layout, provided the chosen stack can reuse relevant prior art cleanly;
- exact short-UUID length, provided collisions are checked;
- exact cache format;
- exact Markdown section ordering;
- exact format of the kept-paper export;
- exact retry/backoff library;
- whether provenance/version structures live entirely in YAML or partly in machine-managed Markdown sections.

These details should not change the core workflow or introduce additional scope.

---

## 26. Definition of MVP Done

The MVP is done when a user can take a monitor definition and reliably perform this cycle:

```text
literature-monitor run --config monitor.yaml
→ review new candidate Markdown files through Inbox.base in Obsidian
→ mark some rejected and some kept
→ rerun without losing decisions or notes
→ export kept identifiers
→ import them with existing Zotero functionality
→ manually mark imported papers in_zotero
```

No additional infrastructure is required for MVP completion.

For v0.3.0 Track A, this also means:

- `materialize` initializes a missing default Review Inbox even when no candidate is produced;
- Inbox, Kept, Rejected, and In Zotero are projections of the existing Paper Markdown statuses, so status edits need no synchronization step and rediscovery does not reset prior decisions;
- a nested workspace observes only its own `Papers/` files with `type == paper`;
- Paper Markdown remains the only durable user-facing workflow state;
- existing `Inbox.base` content and user customization are preserved byte-for-byte on rerun;
- Inbox creation failures are reported as materialization issues, produce a non-zero command result, and do not roll back successful Paper or Author writes;
- no Track B state, second source of truth, external service, community plugin, Python runtime dependency, or `export-kept` regression is introduced.

---

## 27. Next Project Step

R0–R3, v0.2.1 lexical search, v0.3.0 Track A Review Inbox, v0.3.1 Prefix / Proximity search, and v0.3.2 Persistent Monitor Definition are completed release history. v0.3.2 is released and remains the latest released version.

The v0.3.3 Pre-GUI Correctness Hardening feature implementation and reviewed feature commit are complete. The current stage is release preparation / closeout. The remaining release transaction is:

```text
release-preparation commit
→ tag
→ push main
→ push tag
→ GitHub Release
```

This status does not mark v0.3.3 as released. Until that transaction completes, v0.3.2 remains the latest released version.

Future Track B remains outside the current v0.3.3 scope and requires a separate proposal before entering scope.
