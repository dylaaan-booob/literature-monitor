# Literature Monitoring Workflow — MVP Specification v1.0

**Status:** Frozen for MVP implementation  
**Stage:** Specification → ready for repository setup and Codex implementation planning  
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
OpenAlex venue resolution
    ↓
Source + date-window retrieval
    ↓
Local keyword filtering
    ↓
Crossref enrichment
    ↓
Canonicalization / version consolidation
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

1. resolve journals to OpenAlex sources;
2. retrieve papers in the requested time window;
3. filter them locally using the configured search expression;
4. enrich metadata with Crossref when possible;
5. merge duplicate records and known versions into one canonical paper;
6. create one Markdown file per new candidate paper;
7. create/update author notes and author links;
8. preserve all prior human decisions and notes;
9. allow the user to mark papers as `rejected`, `kept`, or later `in_zotero`;
10. export identifiers for `kept` papers so existing Zotero DOI/identifier import functionality can be used.

---

## 4. MVP Scope

### 4.1 Included

MVP includes:

- journal whitelist configuration;
- ISSN/EISSN-based venue resolution;
- OpenAlex venue-first discovery;
- rolling date-window retrieval;
- local keyword filtering;
- Crossref metadata enrichment;
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
- journal ranking.

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

### 6.1 OpenAlex — primary discovery source

OpenAlex is the only primary discovery source in MVP.

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

Discovery must be venue-first:

```text
journal whitelist
→ OpenAlex source
→ works in time window
→ local keyword filter
```

It must **not** use OpenAlex global keyword search as the main discovery path.

### 6.2 Crossref — enrichment source

Crossref does not define the candidate universe.

Responsibilities:

- enrich records by DOI and related bibliographic identifiers;
- supplement publication dates;
- supplement abstract when present;
- provide relation metadata when available;
- corroborate DOI and journal metadata.

Crossref `type = journal-article` must not be interpreted as proof that a record is a research article.

### 6.3 Publisher fallback

Publisher fallback is deliberately excluded from MVP.

If OpenAlex and Crossref cannot provide a field such as abstract, the field remains missing and the failure is recorded. The system must not scrape publisher pages in v1.0.

Publisher-specific adapters may be considered only after real usage demonstrates persistent, high-value metadata gaps concentrated in specific journals or publishers.

---

## 7. Keyword Filtering

Keywords are a **filter inside the journal whitelist**, not a global discovery mechanism.

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

Only author/publisher-supplied keywords that can be identified as such should be treated as `Author Keywords`.

OpenAlex-generated `keywords` or `topics` must not be silently treated as author keywords for filtering.

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

- OpenAlex discovery data initializes the canonical record;
- Crossref fills missing fields and supplements richer date/relation metadata;
- an existing nonempty canonical value should not be silently overwritten by a conflicting enrichment value unless an explicit normalization rule allows it;
- conflicting values should remain inspectable through provenance/logging;
- enrichment must be idempotent.

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
- resolve/report OpenAlex sources;
- report unresolved or ambiguous venues;
- validate keyword expression syntax.

### 19.2 Discover/update candidates

Input:

- journal whitelist;
- keyword expression;
- `from_date`;
- `to_date`.

Output:

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
- one failed OpenAlex page/request;
- Crossref unavailable for a record;
- missing DOI;
- missing abstract;
- missing author keywords;
- incomplete ORCID data;
- a paper with no usable Zotero identifier.

Requirements:

- failures are logged;
- unresolved venues are reported prominently;
- no missing field is replaced with invented content;
- successfully processed papers remain usable even when enrichment fails elsewhere;
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

### 22.1 Whitelist resolution

Given the supplied journal whitelist with conferences excluded:

- configured ISSNs can be validated;
- OpenAlex source IDs are resolved or an explicit unresolved/ambiguous error is reported;
- no journal is silently dropped.

### 22.2 Venue-first discovery

For a selected date window:

- works are retrieved from the configured OpenAlex sources;
- global keyword search is not used to define the candidate universe;
- local filtering occurs only after venue/date retrieval.

### 22.3 Keyword filtering

Given a test expression using `AND`, `OR`, `NOT`, phrases, and parentheses:

- matching is case-insensitive;
- Title + Author Keywords + Abstract are used when available;
- the matcher degrades safely when fields are missing;
- OpenAlex-generated topic/keyword inference is not silently substituted for author keywords.

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

### 22.8 Low-confidence dedup safety

Two papers with similar titles but insufficient identifier/author evidence must not be silently merged.

### 22.9 Kept export

After manually setting:

```yaml
status: kept
```

- the paper appears in the Zotero identifier export;
- a paper marked `in_zotero` does not appear in the export;
- DOI is preferred when present;
- lack of DOI does not delete or invalidate the paper record.

### 22.10 User-edit safety

After adding arbitrary human notes to a Paper Markdown, running the updater again must not erase or replace those notes.

---

## 23. Suggested Implementation Sequence

### Task 1 — Repository foundation

Goal:

- package/project layout;
- configuration loading;
- logging;
- UUID identity model;
- Canonical Paper model;
- journal and keyword validation;
- test framework.

Acceptance:

- models/config parse correctly;
- tests run locally;
- no network-dependent behavior required yet.

### Task 2 — OpenAlex venue-first discovery

Goal:

```text
ISSN
→ OpenAlex Source
→ date-window Works
→ normalized source records
```

Acceptance:

- representative whitelist journals resolve;
- unresolved journals are explicit;
- works can be retrieved without keyword-first global search.

### Task 3 — Keyword expression engine

Goal:

- Title + Author Keywords + Abstract filtering;
- `AND`, `OR`, `NOT`, phrases, parentheses;
- case-insensitive matching;
- safe missing-field behavior.

### Task 4 — Crossref enrichment

Goal:

- DOI-based enrichment;
- supplementary dates;
- abstract when available;
- relations/provenance;
- failure isolation.

### Task 5 — Canonicalization and versions

Goal:

- exact DOI match;
- explicit relation matching;
- conservative title + author fallback;
- version list;
- preferred-version policy;
- idempotent metadata merge.

### Task 6 — Obsidian materialization

Goal:

- one paper → one Markdown;
- stable UUID + `slug--short-uuid` filename;
- full abstract;
- status initialization;
- Author wikilinks;
- Author notes;
- preserve human content.

### Task 7 — Incremental update semantics

Goal:

- parse existing Markdown state;
- preserve rejected/kept/in_zotero;
- update bibliographic metadata only;
- avoid duplicate papers/authors;
- append newly discovered versions safely.

### Task 8 — Kept-paper export

Goal:

- export only `kept` records;
- prefer DOI;
- skip `in_zotero`;
- provide usable fallback metadata for records without DOI.

### Task 9 — End-to-end validation

Use a representative subset of journals from different publishers/fields and an overlapping rerun scenario.

Validate the complete flow:

```text
whitelist
→ OpenAlex
→ filter
→ Crossref
→ canonicalize
→ candidate Markdown
→ manual status edit
→ rerun
→ kept export
```

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

After this specification is placed in the repository:

1. create the target repository;
2. add `SPEC.md`;
3. add a concise repository-level `AGENTS.md` describing engineering constraints and the role of the specification;
4. let Codex inspect the repository before editing;
5. implement one task at a time;
6. after each task, review the actual diff and test output against the acceptance criteria before proceeding.

At this point the project may move from **Specification** to **Implementation Planning**.
