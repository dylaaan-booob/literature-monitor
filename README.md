# Literature Monitor

This repository contains the journal-monitoring workflow specified in
`SPEC.md`. It currently provides the Task 1 project foundation and Task 2
OpenAlex venue-first discovery.

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
