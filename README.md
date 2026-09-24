# Literature Monitor

This repository contains the journal-monitoring workflow specified in
`SPEC.md`. The workflow uses OpenAlex for primary discovery and Crossref for
secondary discovery and bibliographic evidence, consolidates provider evidence
before local keyword filtering, canonicalizes retained papers and versions,
safely updates durable Paper and Author Markdown, and exports kept papers.

v0.4.1 is the latest released baseline. It adds shared transient runtime
progress, Activity, current-Activity ETA, inactivity feedback, and corresponding
CLI/Web presentation while preserving the existing production and durable-state
boundaries.

## Setup

Install [uv](https://docs.astral.sh/uv/) and synchronize the locked environment:

```bash
brew install uv
uv sync
```

## Local Web UI

During development, launch the supported local Web UI with:

```bash
uv run literature-monitor gui --config monitor.yaml
```

From an installed distribution, use:

```bash
literature-monitor gui --config monitor.yaml
```

The GUI is local-only and binds to `127.0.0.1:8000`; v0.4.0 provides no LAN
serving mode. Missing or invalid monitor/journal configuration does not block
startup, so it can be repaired through Settings. GUI Run always uses the
persisted Monitor date policy and exposes no temporary date override.

During an active run, the GUI Run panel shows the humanized `Stage N of 5`,
current Activity, reliable `current / total` counters when available, elapsed
time, current-Activity ETA when enough reliable samples exist, and last-activity
age. `No recent activity` is an advisory rather than a failure, and retrying,
waiting, or stopped-worker states remain distinct. Runtime state stays
process-local in the existing coordinator and is presented through HTMX polling.

Paper Markdown remains the durable workflow state. The GUI does not add a
workflow/execution database, persistent run history, heartbeat, SSE, WebSocket,
queue, or another source of Paper decision state.

## Run a persistent monitor

The normal persistent-monitor entry point is:

```bash
uv run literature-monitor run --config config.example.yaml
```

`run` reads the journal whitelist, keyword expression, output directory, date
policy, and log level from the monitor YAML. It then runs the complete production
path: OpenAlex primary discovery, Crossref secondary discovery and DOI
supplementation, evidence consolidation, local FTS5 filtering, canonicalization,
and Paper / Author / Inbox materialization.

On a TTY, `run` renders the workflow stage, current Activity, reliable counters,
elapsed time, and current-Activity ETA when available. On a non-TTY, progress is
plain event lines on `stderr`; existing machine/data output on `stdout` and the
existing exit-code contract are unchanged.

Discovery windows are publication-date-only. The monitor does not use Crossref
update-date, created-date, index-date, provider update timestamps, or persisted
cursor/watermark/checkpoint state to recover late-indexed records.

Provider clients remain synchronous and serial. OpenAlex Works discovery keeps
cursor pagination at `per_page=100`; Crossref journal discovery keeps cursor
pagination and defaults to `rows=1000`. A Crossref client uses valid
`X-Rate-Limit-Limit` and `X-Rate-Limit-Interval` response metadata to pace later
requests without rate-limit probes or count-only requests. HTTP 429, HTTP 5xx,
and supported timeout/transport failures receive at most three attempts; when no
provider pacing is known, retry waits remain 1 second and then 2 seconds.
Endpoint-specific 404 handling is unchanged, and other HTTP 4xx responses are
not retried. Retry and pacing state remain process-local and transient.

The selected output directory contains:

```text
<output-dir>/
├── Inbox.base
├── Papers/
└── Authors/
```

Paper Markdown is the durable workflow state: UUIDs, review status, human notes,
unknown human-owned frontmatter, and unmanaged sections survive reruns according
to the existing materialization rules. `Inbox.base` is presentation only. It is
created when missing and an existing customized file is preserved.

The supported decision model is one monitor to one decision workspace. Paper
UUID stability, `candidate` / `rejected` / `kept` / `in_zotero`
decisions, human notes, and other durable Paper state are local to that
workspace. The same research work is not guaranteed to receive the same UUID in
another workspace, and Reject/Keep decisions are not inherited across monitors.
There is no global cross-monitor decision registry.

### Persistent monitor fields and defaults

A monitor accepts `name`, `venue_whitelist`, `keyword_expression`,
`output_dir`, `from_date`, `to_date`, `window_days`, and `log_level`.
`keyword_expression` is the only core field without a default and must not be
missing, null, or empty.

| Field | Default when omitted |
| --- | --- |
| `name` | monitor config filename stem |
| `venue_whitelist` | `<config-directory>/list.md` |
| `output_dir` | `<config-directory>/workspace` |
| date policy | rolling 14 days |
| `log_level` | `INFO` |

Relative `venue_whitelist` and `output_dir` paths are always resolved from
the monitor config directory, not the shell working directory. The loader does
not search parent directories or other locations for a replacement `list.md`.

For multiple monitors, use one monitor per config directory or configure a
different `output_dir` for each monitor. If two monitor YAML files live in the
same directory and both omit `output_dir`, the existing default rule resolves
both to the same `<config-directory>/workspace`. The program does not reject
that arrangement, but shared-workspace multi-monitor operation is unsupported /
undefined advanced usage and has no cross-monitor UUID or decision-semantics
guarantee.

A minimal monitor is therefore:

```yaml
keyword_expression: 'statist*'
```

This is usable only when a valid `list.md` exists beside the monitor file. Its
effective defaults are the config filename stem for `name`, `./workspace`
relative to the config file for output, a rolling 14-day window, and `INFO`
logging.

### Date policy and temporary CLI overrides

Persistent date policy supports five forms: no date fields, `window_days` only,
`from_date + to_date`, `from_date + window_days`, or
`to_date + window_days`. Date ranges are inclusive. For example, with
`window_days: 14` and today equal to 2026-09-21, the resolved range is
2026-09-08 through 2026-09-21 inclusive.

`from_date` alone, `to_date` alone, all three date fields together,
`window_days < 1`, and `from_date > to_date` are invalid.

`run` and the existing date-bearing diagnostic commands accept temporary
`--from-date`, `--to-date`, and `--window-days` overrides:

```bash
uv run literature-monitor run \
  --config monitor.yaml \
  --window-days 30

uv run literature-monitor run \
  --config monitor.yaml \
  --from-date 2026-01-01 \
  --to-date 2026-06-30
```

Once any CLI date field is supplied, the CLI fields form the complete date
policy for that invocation; missing pieces are not borrowed from the monitor
YAML. Thus config `window_days: 14` combined with CLI
`--to-date 2026-09-01` is invalid. CLI date overrides are ephemeral and are
never written back to the monitor file.

### Runtime environment and non-features

Optional provider credentials/contact information remain environment settings,
not monitor fields: `OPENALEX_API_KEY` and `CROSSREF_MAILTO`.

The workflow does not add monitor/workspace UUIDs, workspace ownership
markers, a global research-work or decision registry, last-successful-run state,
provider cursors/watermarks/checkpoints, incremental delta / “What's New” state,
scheduler or daemon durable state, notifications, or a workflow/execution
database. The local GUI does not change those boundaries and does not write
directly to the Zotero API.

## Validate configuration

`config.example.yaml` contains a syntax example, not a real research query.
Before making any provider request, `validate` loads the monitor and journal
whitelist, validates keyword parser syntax and date-policy form, runs the same
FTS5-dependent lexical semantic validation used by production local filtering,
and resolves the monitor's effective runtime date range. This catches
parser-valid but lexically invalid Prefix/Proximity operands, an unavailable
SQLite FTS5 backend, and date arithmetic outside the supported
`datetime.date` bounds before provider work begins.

```bash
uv run literature-monitor validate --config config.example.yaml
```

After local preflight succeeds, the command contacts OpenAlex only to resolve
every configured journal Source. OpenAlex supports anonymous Source lookups; set
`OPENALEX_API_KEY` in the environment to use an API key. Local configuration
or runtime-preflight failures exit with status `2`; OpenAlex Source-resolution
errors exit with status `1`; warning-only and fully valid validation exit with
status `0`.

TTY `validate` shows validation-specific progress, including reliable journal
Source-resolution counts, without presenting the five-stage Run model. Non-TTY
validation progress uses the same plain `stderr` event-line convention.

`validate` does not request OpenAlex Works, contact Crossref or Semantic
Scholar, check whether `output_dir` is writable, create a workspace, or
materialize Paper, Author, or Inbox files. OpenAlex Source resolution is its
only network/provider validation.

## Run tests

```bash
uv run pytest
```

## End-to-end validation

The default `uv run pytest` suite includes deterministic full-cycle CLI
regressions using local HTTP fixtures. The representative persistent-monitor
lifecycle enters through `run --config`, while explicit `materialize` coverage
remains for the legacy / diagnostic surface and an overlapping multi-journal
rerun. The CLI can also be used for manual smoke validation against the real
OpenAlex and Crossref providers, but those results depend on external service
availability and are not part of the deterministic default suite. Optional
credentials and contact details are supplied only through `OPENALEX_API_KEY`
and `CROSSREF_MAILTO` environment variables.

## Diagnostic and lower-level commands

`run` is the normal persistent-monitor entry point. The commands below remain
available as explicit diagnostic or lower-level surfaces for provider inspection,
search diagnostics, canonicalization diagnostics, and legacy materialization.

## Diagnose OpenAlex discovery

The OpenAlex discovery diagnostic resolves each configured ISSN independently,
retrieves works from the resolved OpenAlex Source in an inclusive date window,
and writes normalized records as NDJSON to stdout. Logs are written to stderr.
The NDJSON shape is a validation surface for OpenAlex discovery, not a stable
export format.

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

The OpenAlex discovery diagnostic does not perform keyword filtering, Crossref
enrichment, Markdown materialization, Zotero integration, conference monitoring,
or persistence.

## Diagnose Crossref discovery

The Crossref discovery diagnostic queries every configured ISSN independently
within the inclusive publication-date window and writes normalized provider
records as NDJSON. It does not construct an OpenAlex client or apply local
keyword filtering.

```bash
uv run literature-monitor crossref-discover \
  --config config.example.yaml \
  --journal "Biometrics" \
  --from-date 2026-01-20 \
  --to-date 2026-01-25
```

The output is diagnostic rather than a stable export. `CROSSREF_MAILTO` may be
set in the environment to use Crossref's polite API pool.

## Diagnose local keyword filtering

The local keyword filtering diagnostic runs venue-first OpenAlex discovery and
then evaluates the configured keyword expression with the local FTS5 matcher.
It writes only retained original OpenAlex records as NDJSON to stdout and
reports discovered, retained, and filtered-out counts on stderr. This NDJSON is
not a stable export format.

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

Prefix and Proximity operands may be combined with the same Boolean grammar:

```bash
uv run literature-monitor openalex-filter \
  --config config.example.yaml \
  --journal "Biometrics" \
  --from-date 2026-01-20 \
  --to-date 2026-01-25 \
  --keyword-expression 'statist* AND "causal inference"~1'
```

The local keyword filtering diagnostic does not perform Crossref enrichment,
canonicalization, Markdown materialization, Zotero integration, conference
monitoring, or persistence.

## Keyword matching semantics

Local filtering searches only Title, true author- or publisher-supplied Author
Keywords, and Abstract. Each title, abstract, and individual author keyword is
an independent searchable unit. Provider topics, fields of study, and inferred
topics are not searchable.

Expressions support Terms, quoted phrases, Prefix operands such as `statist*`,
Proximity operands such as `"causal inference"~1`, `AND`, `OR`, `NOT`, and
parentheses. Before matching, text is normalized with Unicode NFKC, Unicode
casefold, and whitespace normalization, then tokenized with SQLite FTS5
`unicode61` semantics. Each Term's complete token sequence must occur
contiguously within one searchable unit. For example, `strasse` matches
`Straße`. Punctuation is a tokenizer boundary, so `high-dimensional` can
match both `high-dimensional` and `high dimensional`. This is lexical token
equivalence, not fuzzy matching.

A quoted phrase must occur as one complete contiguous token sequence inside a
single searchable unit. Given a title unit `deep` and an abstract unit
`learning`, `"deep learning"` is false, while `deep AND learning` is true.
Exact Phrase matching remains ordered and adjacent.

A Prefix uses exactly one trailing `*` and matches a lexical token prefix, not
an arbitrary substring. For example, `statist*` matches `statist`,
`statistic`, `statistics`, and `statistical`, but not `biostatistics`.
After normalization the Prefix base must contain at least three lexical
characters, contain only letters or digits, and produce one lexical token.
Leading wildcards, mid-word wildcards, and multiple `*` characters are not
supported. A quoted `"statist*"` remains a Phrase, and `?` is not a wildcard.

A Proximity operand uses a quoted lexical sequence followed by an integer
distance from 0 through 50. `"causal inference"~0` means unordered adjacency:
`causal inference` and `inference causal` match, while
`causal robust inference` does not. `"causal inference"~1` permits one
additional intervening token, so `causal robust inference` matches. The
quoted content must produce at least two real `unicode61` lexical tokens.
Prefix syntax inside quoted Proximity content has no Prefix meaning.

Each Term, Phrase, Prefix, or Proximity match must be satisfied wholly inside
one searchable unit. In particular, a single Proximity operand cannot span a
title and abstract, two author keywords, or records from different providers.
Boolean operators are evaluated for the consolidated work, so operands may
combine matches from different searchable units or provider evidence. For
example, `statist* AND "causal inference"~1` may match the Prefix in one
provider title and the Proximity operand in another provider abstract.

Each filtering stage builds a transient in-memory SQLite FTS5 index and discards
it after the run. The index is rebuilt as needed, writes no persistent search
database, and is not a second durable source of truth beside Markdown. If the
current Python SQLite runtime lacks FTS5, commands that require local filtering
report a clear error and exit without falling back to the previous substring
matcher. Local matching does not provide synonym expansion, stemming, arbitrary
user-facing FTS5 `NEAR(...)` syntax, wildcard forms beyond the defined trailing
`*` Prefix, `?` wildcard syntax, BM25 ranking, fuzzy search, or semantic
search.

## Diagnose Crossref DOI enrichment

This historical stage diagnostic runs venue-first OpenAlex discovery, applies
the keyword expression with the same local FTS5 semantics described above, and
performs Crossref DOI lookups only for retained records.
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
format. This historical diagnostic does not merge Crossref fields into canonical
metadata and does not implement deduplication, version consolidation, UUID
creation, Markdown materialization, Zotero integration, or persistence.

## Diagnose canonicalization and versions

The canonicalization diagnostic retrieves journal/date evidence from OpenAlex
and Crossref and performs Crossref DOI supplementation. All available evidence
is consolidated before the local FTS5 filter evaluates the searchable
projection using the rules above. Retained clusters become canonical papers.
Matching is conservative and evidence-based:
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

The canonicalization diagnostic does not implement Markdown materialization,
persistent rerun state, stable UUID recovery across independent runs, or Zotero
export.

## Legacy / diagnostic materialization

`materialize` remains an explicit legacy / diagnostic-style entry point. It
runs the same OpenAlex/Crossref consolidation-before-filter production pipeline
as `run`, but it still requires an explicit CLI `--output-dir` and continues to
support the existing diagnostic `--journal` and `--keyword-expression`
overrides. The normal `run` command instead takes its output directory,
journal whitelist, and keyword expression from the monitor definition:

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
<output-dir>/
├── Inbox.base
├── Papers/
└── Authors/
```

Incremental materialization scans existing Markdown and recovers Paper identity
from UUIDs, external identifiers, version keys, and source keys before using the
conservative title-and-ordered-author fallback. Matched Papers retain their
durable UUID and path. Versions and sources accumulate across runs, and the
preferred version is recomputed before bibliographic metadata is updated. A
lower-priority incoming manifestation cannot overwrite the snapshot belonging
to the effective preferred version.

Updates preserve workflow status, discovery time, Zotero key, unknown
frontmatter, Notes, and unmanaged body sections. Managed frontmatter and the
title, Abstract, Versions, and Sources sections are rewritten atomically only
when their rendered bytes change. Existing opaque Author files remain reusable
at their deterministic paths and are never overwritten; parseable Author notes
may receive missing stable identifiers without being renamed.

Malformed but identity-readable Papers still block duplicate creation. Unsafe
or ambiguous matches are reported as errors and left unchanged, while
recoverable metadata conflicts are warnings. Incremental materialization does
not automatically merge, delete, or rename historical duplicates.

### Review candidates in Obsidian

Open `<output-dir>/Inbox.base`, review candidate papers in the **Inbox** view,
and change each Paper's `status` to `kept` or `rejected`. The Paper moves
automatically between the status-derived **Inbox**, **Kept**, **Rejected**, and
**In Zotero** views. Use `export-kept` for the Zotero handoff.

Paper Markdown remains the durable workflow state. `Inbox.base` contains only
presentation configuration, so there is no Inbox synchronization command. It
is user-customizable: columns, sorting, filters, formulas, and view layout may
all be adjusted. Literature Monitor creates `Inbox.base` only when it is
absent; an existing regular `Inbox.base` is never overwritten.

The Review Inbox uses Obsidian Bases and was validated with Obsidian Desktop
1.13.7. Bases is the only additional presentation capability used. No
community plugin, Dataview, custom CSS, or additional Python runtime dependency
is required.

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
