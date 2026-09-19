# Literature Monitor

This repository contains the journal-monitoring workflow specified in
`SPEC.md`. Tasks 1 through 8 implement the complete MVP workflow: venue-first
OpenAlex discovery, local keyword filtering, DOI-only Crossref enrichment,
canonicalization and version consolidation, durable Paper and Author Markdown
updates, and kept-paper export. Task 9 validates that workflow end to end.

## Setup

Install [uv](https://docs.astral.sh/uv/) and synchronize the locked environment:

```bash
brew install uv
uv sync
```

## Validate configuration

`config.example.yaml` contains a syntax example, not a real research query.
The command validates the local configuration and the `Journals` section of
`list.md`, then contacts OpenAlex to resolve every configured journal Source.
It does not request Works or run discovery. OpenAlex supports anonymous Source
lookups; set `OPENALEX_API_KEY` in the environment to use an API key.

```bash
uv run literature-monitor validate --config config.example.yaml
```

## Run tests

```bash
uv run pytest
```

Validation performs only OpenAlex Source resolution; it does not perform Works
discovery, Markdown materialization, Zotero integration, conference monitoring,
or database operations.

## End-to-end validation

The default `uv run pytest` suite includes a deterministic full-cycle CLI
regression using local HTTP fixtures. The Task 9 acceptance coverage includes
a representative multi-journal full cycle and a partially overlapping rerun.
The normal CLI commands can also be used for manual smoke validation against
the real OpenAlex and Crossref providers, but those results depend on external
service availability and are not part of the deterministic default suite.
Optional credentials and contact details are supplied only through
`OPENALEX_API_KEY` and `CROSSREF_MAILTO` environment variables.

## Diagnose OpenAlex discovery

The Task 2 diagnostic command resolves each configured ISSN independently,
retrieves works from the resolved OpenAlex Source in an inclusive date window,
and writes normalized records as NDJSON to stdout. Logs are written to stderr.
The NDJSON shape is a validation surface for Task 2, not a stable export format.

```bash
uv run literature-monitor openalex-discover \
  --config config.example.yaml \
  --journal "Biometrics" \
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

Task 2 does not perform keyword filtering, Crossref enrichment, Markdown
materialization, Zotero integration, conference monitoring, or persistence.

## Diagnose local keyword filtering

The Task 3 diagnostic runs the same venue-first OpenAlex discovery and then
evaluates the configured keyword expression locally. It writes only retained
original OpenAlex records as NDJSON to stdout and reports discovered, retained,
and filtered-out counts on stderr. This NDJSON is not a stable export format.

Use the configured expression:

```bash
uv run literature-monitor openalex-filter \
  --config config.example.yaml \
  --journal "Biometrics" \
  --from-date 2026-01-20 \
  --to-date 2026-01-25
```

For a one-run diagnostic expression, use an override. It is not written to the
configuration file:

```bash
uv run literature-monitor openalex-filter \
  --config config.example.yaml \
  --journal "Biometrics" \
  --from-date 2026-01-20 \
  --to-date 2026-01-25 \
  --keyword-expression '"multiview learning"'
```

Task 3 does not perform Crossref enrichment, canonicalization, Markdown
materialization, Zotero integration, conference monitoring, or persistence.

## Diagnose Crossref enrichment

The Task 4 diagnostic runs venue-first OpenAlex discovery, applies the keyword
expression locally, and performs Crossref DOI lookups only for retained records.
It preserves each original OpenAlex record and attaches normalized Crossref
provider evidence when available. Records without a DOI, and records that are
not present in Crossref, remain in the output with `crossref: null`.

```bash
uv run literature-monitor crossref-enrich \
  --config config.example.yaml \
  --journal "Biometrics" \
  --from-date 2026-01-20 \
  --to-date 2026-01-25 \
  --keyword-expression '"multiview learning"'
```

Crossref access is anonymous. An optional contact address can be supplied for
the polite API pool without changing repository configuration:

```bash
CROSSREF_MAILTO=you@example.com uv run literature-monitor crossref-enrich \
  --config config.example.yaml \
  --journal "Biometrics" \
  --from-date 2026-01-20 \
  --to-date 2026-01-25 \
  --keyword-expression '"multiview learning"'
```

stdout is diagnostic `EnrichedWorkRecord` NDJSON and is not a stable export
format. Task 4 does not merge Crossref fields into canonical metadata and does
not implement deduplication, version consolidation, UUID creation, Markdown
materialization, Zotero integration, or persistence.

## Diagnose canonicalization and versions

The Task 5 diagnostic runs the complete journal-first discovery, local keyword
filter, and Crossref enrichment pipeline before consolidating the retained
evidence into canonical papers. Matching is conservative and evidence-based:
exact identifiers and explicit version relations take priority, while the
title-and-author fallback requires compatible ordered author identities.

```bash
uv run literature-monitor canonicalize \
  --config config.example.yaml \
  --journal "Biometrics" \
  --from-date 2026-01-20 \
  --to-date 2026-01-25 \
  --keyword-expression '"multiview learning"'
```

All actually discovered versions remain in `versions`. The preferred version
uses `journal_final > journal_online > accepted_manuscript > latest preprint`,
with `unknown` as the final fallback. stdout is diagnostic `CanonicalPaper`
NDJSON and is not a stable export format.

Task 5 does not implement Markdown materialization, persistent rerun state,
stable UUID recovery across independent runs, or Zotero export.

## Materialize Obsidian Markdown

The materialize command runs the complete journal-first pipeline and creates or
incrementally updates Paper and Author notes under an Obsidian-compatible output
directory:

```bash
uv run literature-monitor materialize \
  --config config.example.yaml \
  --journal "Biometrics" \
  --from-date 2026-01-20 \
  --to-date 2026-01-25 \
  --keyword-expression '"multiview learning"' \
  --output-dir /path/to/obsidian-vault/literature-monitor
```

The command writes to:

```text
<output-dir>/Papers
<output-dir>/Authors
```

Task 7 scans existing Markdown and recovers Paper identity from UUIDs, external
identifiers, version keys, and source keys before using the conservative
title-and-ordered-author fallback. Matched Papers retain their durable UUID and
path. Versions and sources accumulate across runs, and the preferred version is
recomputed before bibliographic metadata is updated. A lower-priority incoming
manifestation cannot overwrite the snapshot belonging to the effective
preferred version.

Updates preserve workflow status, discovery time, Zotero key, unknown
frontmatter, Notes, and unmanaged body sections. Managed frontmatter and the
title, Abstract, Versions, and Sources sections are rewritten atomically only
when their rendered bytes change. Existing opaque Author files remain reusable
at their deterministic paths and are never overwritten; parseable Author notes
may receive missing stable identifiers without being renamed.

Malformed but identity-readable Papers still block duplicate creation. Unsafe
or ambiguous matches are reported as errors and left unchanged, while
recoverable metadata conflicts are warnings. Task 7 does not automatically
merge, delete, or rename historical duplicates.

## Export Kept Papers for Zotero

The export command reads the existing durable Paper Markdown without running
discovery, enrichment, canonicalization, or materialization:

```bash
uv run literature-monitor export-kept \
  --output-dir /path/to/obsidian-vault/literature-monitor
```

Only Papers with `status: kept` are written to stdout. Papers marked
`candidate`, `rejected`, or `in_zotero` are skipped. Each kept Paper produces
one line using DOI first, then `arXiv:<id>`, or a tab-separated `MANUAL` entry
with title, journal, and publication date when neither identifier is available;
a missing date is written as `unknown`. Malformed Paper files are reported on
stderr without blocking other valid entries, and make the command exit with
status 1. The output can be redirected to a file:

```bash
uv run literature-monitor export-kept \
  --output-dir /path/to/obsidian-vault/literature-monitor \
  > kept-for-zotero.txt
```

The command does not call Zotero APIs or change Markdown state. After a
successful downstream Zotero import, change the Paper status to `in_zotero`
manually.
