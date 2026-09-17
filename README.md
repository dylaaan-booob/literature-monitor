# Literature Monitor

This repository contains the journal-monitoring workflow specified in
`SPEC.md`. Task 1 provides only the offline project foundation: validated data
models, journal and keyword configuration loading, filename generation,
logging, and a configuration-validation CLI.

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
