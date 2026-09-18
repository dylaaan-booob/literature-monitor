# Literature Monitor

This repository contains the journal-monitoring workflow specified in
`SPEC.md`. It currently provides the Task 1 project foundation through Task 6
Obsidian materialization: venue-first OpenAlex discovery, local keyword
filtering, DOI-only Crossref enrichment, canonicalization and version
consolidation, and creation-only Paper and Author Markdown output.

## Setup

Install [uv](https://docs.astral.sh/uv/) and synchronize the locked environment:

```bash
brew install uv
uv sync
```

## Validate configuration

`config.example.yaml` contains a syntax example, not a real research query.
The command reads only the `Journals` section of `list.md`.

```bash
uv run literature-monitor validate --config config.example.yaml
```

## Run tests

```bash
uv run pytest
```

Task 1 performs no network requests and does not implement discovery,
Markdown materialization, Zotero integration, conference monitoring, or a
database.

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

The Task 6 command runs the complete journal-first pipeline and creates Paper
and Author notes under an Obsidian-compatible output directory:

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

Task 6 is creation-only. Existing Paper and Author files are reused without
changing any bytes, so human status, notes, unknown frontmatter, and custom body
sections remain untouched. A missing Paper is created only after all of its
required Author notes were created successfully or already exist as files.

This is not yet the complete overlapping-rerun behavior from `SPEC.md`. Reusing
the same Paper path in Task 6 assumes the same `CanonicalPaper` title and UUID
and the same creation-time UUID collision context. A different batch can change
the short-UUID length, a changed title can change the slug, and a separate
canonicalization run can assign a new UUID. Reading existing Markdown to recover
identity, retaining an old filename when metadata improves, and safely updating
machine-managed metadata are Task 7 responsibilities.
