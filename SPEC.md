# Literature Monitoring Workflow — MVP Specification v1.1

**Status:** Active; v0.1.0 delivered and multi-source retrieval evolution specified

**Stage:** Multi-source retrieval R0 specification alignment
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

1. a relatively stable journal whitelist;
2. a relatively stable keyword expression;
3. a rolling date range, such as the last 6 months, 1 year, or 3 years.

A normal run should:

1. validate the journal whitelist and resolve any provider-specific venue identifiers;
2. retrieve journal/date evidence independently from the configured providers;
3. consolidate records that have sufficient identity evidence into provider-neutral evidence clusters;
4. build a searchable projection from all available evidence in each cluster;
5. apply the configured keyword expression locally to decide inclusion;
6. merge each included cluster and its known versions into one canonical paper;
7. create one Markdown file per new candidate paper;
8. create/update author notes and author links;
9. preserve all prior human decisions and notes;
10. allow the user to mark papers as `rejected`, `kept`, or later `in_zotero`;
11. export identifiers for `kept` papers so existing Zotero DOI/identifier import functionality can be used.

---

## 4. MVP Scope

### 4.1 Included

MVP includes:

- journal whitelist configuration;
- ISSN/EISSN-based venue resolution;
- multi-source evidence retrieval within the journal/date boundary;
- OpenAlex journal/date discovery;
- Crossref journal/date discovery and DOI enrichment;
- Semantic Scholar metadata supplementation and supplemental discovery;
- provider-neutral identity/evidence consolidation;
- local keyword filtering after available evidence is consolidated;
- provider, journal, and request failure isolation;
- canonical paper identity;
- basic cross-source deduplication;
- version tracking and preferred-version selection;
- one-paper-one-Markdown materialization;
- full abstract storage when available;
- candidate workflow states;
- persistent rejected records;
- author wikilinks and author notes;
- safe incremental reruns;
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
- LLM relevance ranking;
- LLM semantic keyword expansion;
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
- Track B workflow/state expansion beyond the current Markdown lifecycle;
- search-engine parity features such as FTS5, stemming, wildcards, or proximity operators.

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

Venue validation must prefer ISSN/EISSN. Only when the provider record has no usable ISSN/EISSN may a strict normalized journal-name match be used as a fallback; ambiguous or mismatched venues must not enter the candidate universe.

### 6.4 Publisher fallback

Publisher fallback is deliberately excluded from MVP.

If the configured providers cannot provide a field such as abstract, the field remains missing and the failure is recorded. The system must not scrape publisher pages in the MVP.

Publisher-specific adapters may be considered only after real usage demonstrates persistent, high-value metadata gaps concentrated in specific journals or publishers.

---

## 7. Keyword Filtering

Keywords are a **filter inside the journal whitelist**, not a global discovery mechanism.

Filtering occurs only after records with sufficient identity evidence have been consolidated. The searchable projection for an identity cluster uses the eligible fields available across all provider evidence in that cluster, rather than only the fields from the first or otherwise preferred provider record.

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

### 7.2 Expression semantics

MVP supports:

- words;
- exact phrases;
- `AND`;
- `OR`;
- `NOT`;
- parentheses;
- case-insensitive matching.

MVP does not require:

- automatic synonym expansion;
- proximity/`NEAR` operators;
- LLM semantic matching;
- automatic topic inference;
- complex wildcard syntax.

The keyword expression must be configuration, not hard-coded logic.

---

## 8. Canonical Paper Identity

### 8.1 Stable internal primary key

Every canonical research work receives an **internal UUID**.

This UUID is the only permanent internal primary key.

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

MVP does not require SQLite or another mandatory database as a second source of truth.

An implementation may use disposable caches or indexes for speed, but they must be reconstructible from configuration, source APIs, and the Markdown corpus.

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

## 17. Incremental Update Semantics

Repeated runs over overlapping date windows are normal and must be safe.

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

## 18. Kept-Paper Export for Zotero

MVP deliberately reuses Zotero's existing identifier-import capabilities instead of reimplementing them.

The project should provide a simple export containing only papers whose state is:

```text
kept
```

and excluding:

```text
in_zotero
```

### 18.1 Preferred export identifier

Priority:

```text
DOI
→ other stable identifier if useful
→ title + basic metadata for manual handling when no suitable identifier exists
```

The exact export format may be a plain text file, Markdown list, or similarly simple artifact, but it must be easy to paste/use with Zotero's existing DOI/identifier import workflow.

### 18.2 Out of scope

The project does not:

- call Zotero write APIs;
- create Zotero items itself;
- download PDFs;
- manage Zotero attachments;
- guarantee PDF availability;
- automatically switch the Markdown status to `in_zotero`.

The user changes the status to `in_zotero` after successful downstream import.

---

## 19. CLI / Execution Surface

MVP may be implemented as a CLI application or equivalent scriptable command surface.

It must support at least these operations conceptually:

### 19.1 Validate configuration

- validate journal whitelist;
- resolve/report provider-specific venue/source identifiers where applicable;
- report unresolved or ambiguous venues per provider;
- validate keyword expression syntax.

### 19.2 Discover/update candidates

Input:

- journal whitelist;
- keyword expression;
- `from_date`;
- `to_date`.

Output:

- independently retrieved provider evidence;
- identity/evidence consolidation and a local-filtering summary;
- new candidate Markdown files;
- safe enrichment of existing files;
- author notes;
- run summary/log.

### 19.3 Export kept papers

Output identifiers for papers with:

```text
status = kept
```

while skipping `in_zotero`.

Exact command names are implementation details and need not be frozen in the specification.

---

## 20. Error Handling

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

## 21. Reuse and Prior-Art Boundary

### 21.1 Gian-Hacher/Paper_tracker

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

### 21.2 zcz718/PaperRadar

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

### 21.3 ansatzX/PaperTrack

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

### 21.4 Zotero

Reuse Zotero's existing DOI/identifier import workflow and existing Zotero plugins where useful.

Do not build a new Zotero ingestion subsystem in MVP.

---

## 22. Acceptance Criteria

MVP is complete only when the following are demonstrated.

### 22.1 Whitelist and venue validation

Given the supplied journal whitelist with conferences excluded:

- configured ISSNs can be validated;
- provider-specific venue/source identifiers are resolved where applicable, or an explicit unresolved/ambiguous error is reported;
- ISSN/EISSN is preferred for venue identity, with strict normalized journal-name fallback only when a provider record lacks usable ISSN/EISSN;
- no journal is silently dropped.

### 22.2 Journal/date multi-source retrieval

For a selected date window:

- OpenAlex and Crossref can each contribute journal/date candidates independently;
- Semantic Scholar can supplement metadata and contribute supplemental candidates that pass venue/date validation;
- a candidate is not required to exist in OpenAlex first;
- global keyword search is not used to define the candidate universe;
- Semantic Scholar broad positive queries do not decide final inclusion.

### 22.3 Evidence consolidation and keyword filtering

Given a test expression using `AND`, `OR`, `NOT`, phrases, and parentheses:

- records with the same normalized DOI consolidate into one identity cluster;
- available provider evidence is consolidated before local filtering;
- an abstract supplied by any provider in the cluster participates in filtering;
- matching is case-insensitive;
- Title + Author Keywords + Abstract are used when available;
- the matcher degrades safely when fields are missing;
- provider-derived keywords, topics, or fields of study are not silently substituted for author keywords.

### 22.4 Candidate creation

For each newly matched work:

- exactly one Paper Markdown is created;
- the paper receives a stable internal UUID;
- the filename follows `slug--short-uuid.md`;
- title, journal, authors, date, and available identifiers are stored;
- the full abstract is stored when available;
- missing abstract is explicitly represented as missing;
- authors appear as Obsidian wikilinks;
- required Author notes are created/reused.

### 22.5 Safe rerun

Run the same overlapping query twice:

- no duplicate paper is created;
- no duplicate Author note is created;
- internal UUIDs remain stable;
- human status is preserved;
- human notes are preserved;
- metadata enrichment may improve existing records.

### 22.6 Rejected persistence

After manually changing a paper to:

```yaml
status: rejected
```

rerunning discovery must not create a new candidate or revert its status.

### 22.7 Version consolidation

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

### 22.8 Provider-neutral canonicalization

Given valid evidence from multiple providers or from Crossref/Semantic Scholar without OpenAlex:

- the work can produce a canonical paper without an OpenAlex record;
- records sharing the same normalized DOI produce exactly one canonical work;
- contributing external identifiers and provenance are retained;
- first-seen provider order does not determine canonical authority;
- no missing metadata is invented.

### 22.9 Provider failure isolation

When one provider, journal, or request fails:

- successful evidence from other retrievals remains usable;
- successful evidence can still be consolidated, filtered, canonicalized, and materialized;
- the failure is reported without invalidating unrelated results;
- rerunning after recovery does not create duplicate canonical papers.

### 22.10 Low-confidence dedup safety

Two papers with similar titles but insufficient identifier/author evidence must not be silently merged.

### 22.11 Kept export

After manually setting:

```yaml
status: kept
```

- the paper appears in the Zotero identifier export;
- a paper marked `in_zotero` does not appear in the export;
- DOI is preferred when present;
- lack of DOI does not delete or invalidate the paper record.

### 22.12 User-edit safety

After adding arbitrary human notes to a Paper Markdown, running the updater again must not erase or replace those notes.

---

## 23. Suggested Implementation Sequence

### 23.1 Completed v0.1.0 history

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

### 23.2 Multi-source retrieval evolution

Proceed as separately planned, implemented, and reviewed tasks:

```text
R0 Specification alignment
→ R1 Provider-neutral evidence boundary
→ R2 Crossref independent discovery
→ R3 Semantic Scholar integration
→ later Search-engine parity
```

- R0 changes only the product specification and repository engineering constraints.
- R1 introduces a provider-neutral transient evidence representation, adapts the existing OpenAlex and Crossref records to that boundary, and removes canonicalization's structural dependency on an OpenAlex record while preserving the current user-visible OpenAlex → local filter → Crossref enrichment behavior.
- R2 adds independent Crossref journal/date discovery, unions OpenAlex and Crossref evidence, performs identity/evidence consolidation, constructs the multi-provider searchable projection, and moves final local keyword filtering after available evidence consolidation.
- R3 adds Semantic Scholar supplementation and venue/date-constrained supplemental discovery.
- Search-engine parity remains later work and must not be pulled into R0–R3.

R0–R3 must remain separate implementation tasks with their own plans, diffs, verification, and review. Completing this specification alignment does not start R1.

---

## 24. Non-blocking Implementation Details

The following do not block implementation and may be decided locally as long as the specification's observable behavior is preserved:

- programming language/package layout, provided the chosen stack can reuse relevant prior art cleanly;
- exact short-UUID length, provided collisions are checked;
- exact CLI command names;
- exact cache format;
- exact Markdown section ordering;
- exact format of the kept-paper export;
- exact retry/backoff library;
- whether provenance/version structures live entirely in YAML or partly in machine-managed Markdown sections.

These details should not change the core workflow or introduce additional scope.

---

## 25. Definition of MVP Done

The MVP is done when a user can take the real journal whitelist, a keyword expression, and a date window and reliably perform this cycle:

```text
run discovery
→ review new candidate Markdown files in Obsidian
→ mark some rejected and some kept
→ rerun without losing decisions or notes
→ export kept identifiers
→ import them with existing Zotero functionality
→ manually mark imported papers in_zotero
```

No additional infrastructure is required for MVP completion.

---

## 26. Next Project Step

After R0 is reviewed, plan R1 as a separate bounded task. Do not implement the provider-neutral evidence boundary, Crossref independent discovery, Semantic Scholar integration, or search-engine parity as part of specification alignment.

For each later task, inspect the repository before editing, keep the task boundary explicit, and review the actual diff and relevant verification output before proceeding to the next task.
