# Literature Monitor

This repository contains the journal-monitoring workflow specified in
`SPEC.md`. It currently provides the Task 1 project foundation, Task 2
OpenAlex venue-first discovery, Task 3 local keyword filtering, and Task 4
DOI-only Crossref enrichment.

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
