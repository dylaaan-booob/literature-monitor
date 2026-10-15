# Literature Monitor

Literature Monitor is a local-first journal discovery and review application. It monitors a configured journal
whitelist using OpenAlex and Crossref, creates Markdown Papers for new DOIs, supports explicit Keep/Reject and
Paper Enrichment, and sends selected Papers to Zotero through a separate Chrome Connector.

The **current development workspace** uses a five-page Web UI: **Inbox, Kept, Journals, Access, Settings**. `/`
is the default entry; `/ui-next` and `/ui-next/` are aliases. The retired Jinja/HTMX interface and independent
Preparation entry are no longer available.

The stable release is
[v0.7.0](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.7.0). The five-page Web UI
and Connector changes are included in this release. The Python package is MIT licensed; the separate
upstream-derived Chrome Connector is AGPLv3. See [Connector provenance](connector/PROVENANCE.md).

## Installation and startup

Install [uv](https://docs.astral.sh/uv/) and synchronize the project environment:

~~~bash
uv sync
uv run literature-monitor gui --config monitor.yaml
~~~

An installed package uses `literature-monitor gui --config monitor.yaml`. The application listens only on
`http://127.0.0.1:8000/`, with one Web worker. Missing or invalid configuration does not prevent opening
Settings; it prevents affected operations until corrected. Run and configuration editing are Web actions; the
previous Run/validation/export CLI subcommands do not exist.

Create `monitor.yaml` from [config.example.yaml](config.example.yaml), then adjust the month range, expression
and paths:

~~~yaml
name: Statistical ML
venue_whitelist: list.md
keyword_expression: 'statist* AND "high dimensional"~0'
output_dir: ./workspace
from_month: '2026-10'
to_month: '2026-10'
log_level: INFO
kept_workflow:
  auto_import_after_preparation: false
  transition_delay_seconds: 3
~~~

Relative paths resolve from the directory containing `monitor.yaml`. Saved `from_month` and `to_month` are
inclusive natural months; equal values select one month. Change them in Settings and Save before Run. There is
no day-window policy, scheduler or automatic backfill. Never put school credentials, Cookies or login-session
data in configuration.

## Five-page workflow

### Inbox: Run and review

Click **Run** to discover journal articles in the saved month range. OpenAlex is the primary journal Provider
and Crossref is the secondary discovery/metadata Provider. Discovery is journal-first and DOI-bound; Semantic
Scholar, conference monitoring, publisher scraping and keyword-first global search are excluded. Each Run
fetches live membership. Partial Provider failures can still produce valid Papers and visible warnings.

Search expressions operate on Title and Abstract with `AND`, `OR`, parentheses, terms, ordered phrases,
`statist*` prefixes and `"causal inference"~1` proximity. Prefixes match token beginnings, not arbitrary
substrings. Distinct operands can match separate fields. Standalone `NOT` is not supported. Unicode
normalization and SQLite FTS5 lexical tokenization apply; there is no fuzzy/semantic ranking.

**Run creates only new DOI Papers**. It leaves every existing Paper and Author byte unchanged, regardless of
candidate/rejected/kept/exported status, including existing metadata and notes. DOI conflicts are reported, not
overwritten. New Papers require Title, Journal and DOI; Abstract, Authors and publication date may be absent.
Dates preserve available `YYYY`, `YYYY-MM` or `YYYY-MM-DD` precision. An already saved DOI is counted separately
from actual new file creation.

Select a candidate and click **Keep** or **Reject**. The decision writes guarded Markdown state immediately.
**Inbox** shows candidates; **Kept** is independent of the Run that created them. DOI links open a normal
external page and never themselves change status. Current Run progress and detailed issues are session-local.

### Paper Detail: Enrich

When Abstract, Authors or publication date is missing, **Enrich** runs one browser-only, read-only Connector
translation for the Paper DOI. Zotero Desktop is not required. Valid retrieved fields save immediately under
identity/content checks; there is no Preview or separate Save. Results may be partial. A month-only date may be
improved to an exact complete date, while a complete date is not overwritten; unknown date components are not
invented.

Existing Author files are not rewritten. If new Authors were created but Paper saving fails, the files remain
and the error reports their paths. Cancel or leave Detail to stop retrieval; already saved fields remain. After
one successful save, Enrich is unavailable for that Paper until the local Web service restarts. Enrichment never
saves to Zotero or changes Run's capture-only rule.

### Kept: prepare access and import

Kept aggregates `kept` Papers from all Runs. Choose:

- **PREPARE & IMPORT**: prepare related CARSI Services, show outcomes/manual links and then continue to Import.
- **IMPORT ONLY**: skip CARSI Preparation and start eligible native Zotero imports.

There is no independent Prepare button or page. Preparation collects related Services from all current Kept
Papers via Journal → direct Publisher → default Access Service, then deduplicates them in saved Service order.
Only Services marked **CARSI** receive automated Portal tasks. **Manual Login** Services offer their saved URL
and require user interaction. Preparation outcomes **Opened / Needs attention / Failed** describe navigation
only; none proves login, entitlement, a PDF or access to an article.

The official CARSI Shibboleth entry uses the optional Institution IdP identifier; users complete login, MFA and
challenges themselves. An optional **Automatic Attribute Release Consent** setting is off by default. It
operates only on recognized same-origin Shibboleth consent forms during an active Portal login wait;
school-stored consent can affect other services and disabling the setting does not revoke earlier consent.
Preparation leaves all its Chrome tabs/windows open for manual inspection and closure. Unclear results require
manual access and explicit **Continue to Import**.

Native Import requires **Zotero Desktop running** and the Literature Monitor Chrome Connector connected to the
local Web service. It dispatches Papers serially and marks `kept → exported` only on attributable upstream
native **parent item** acceptance and a successful guarded local write. The PDF result is separate, and
`unverified` does not mean saved. Failure or uncertain parent confirmation leaves the Paper kept; **inspect
Zotero before retrying**, because an uncertain save may have created an item.

Ending during Import stops **future** dispatch and lets any already-authorized native save settle; it does not
undo Zotero writes. All terminal Kept outcomes remain on Kept. The workflow state survives page refresh within a
running Web process, not process restart. Animation playback does not determine success.

See [Connector installation and tests](connector/README.md) and
[technical Import/CARSI contracts](SPEC.md#7-kept-workflow-and-native-zotero-import).

### Journals: configure monitored venues and covers

Journals are identified by one normalized valid **ISSN-L** each. Display names and Provider aliases are not
durable identity keys. Create/assign Journal Groups with drag-and-drop or selection/Assign controls.
**Ungrouped** means blank Group; empty Groups are draft-only. Journal name, ISSN-L and direct Publisher ID are
machine-managed. Adding an ISSN-L requires OpenAlex verification at Save; identity conflicts require an explicit
correction, not a guessed rename.

Journal covers are local assets in `journal-assets/` beside `list.md`. With the Chrome Connector running, use
**Search & Download**, **Search & Replace** or **Find Missing Covers**; the first Google Images result is tried,
without relevance ranking, thumbnail fallback or automatic alternative. Inspect results. Chrome searches up to
three Journals together and downloads in saved order. **Cancel** retains completed covers, **Continue** resumes
remaining targets, **Retry** retries failed ones, and **Reset** deletes all local covers including uploads but
does not start another search. Manual upload accepts JPEG, PNG, WebP and GIF within validation limits. Missing
covers display a Generated fallback. Covers are not part of `list.md` and Workspace Reset does not remove them.

### Access: manage Publisher relationships and Services

A Journal references a **direct** OpenAlex Publisher ID. A Publisher's **Publisher URL** is a user-managed
manual shortcut; it is not evidence of the actual DOI landing platform. A Publisher may be assigned to zero or
one Access Service, independent of Journal Group. Services have stable `svc_...` IDs, editable names, public
HTTP(S) URLs and **CARSI** / **Manual Login** methods. Deleting a Service requires confirmation and unassigns
its Publishers; removing a Publisher's final Journal can also discard its Publisher URL and mapping.

**Export AI Mapping Pack** downloads the complete currently saved `list.md` together with a fresh public CARSI
service catalog and explicit external-AI editing instructions. The Pack is a work file, cannot be imported and
is not sent to AI by the application. Inspect its contents before sharing. Only a reviewed complete `list.md`
goes through the Markdown Import flow. Never infer a Service relationship, institutional entitlement or login
from Publisher names/URLs.

### Settings: shared draft, files and maintenance

Journals, Access and Settings use **one shared unsaved draft and one Save**. Changes to Monitor settings,
Journal Groups and Access Mapping remain pending across page navigation. Ordinary Save updates only files whose
contents change, validates original revisions and rejects external edit conflicts. Unsafe or malformed disk
files require manual repair and **Reload from Disk**, which explicitly discards draft values.

The portable `list.md` holds managed tables:

~~~markdown
## Journals
| Journal | ISSN-L | Publisher ID | Group |
| --- | --- | --- | --- |

## Publishers
| Publisher | OpenAlex ID | Publisher URL | Access Service ID |
| --- | --- | --- | --- |

## Access Services
| Service ID | Service | Access URL | Access Method |
| --- | --- | --- | --- |
~~~

Only Journals are discovery input. The saved file may also contain `Conferences` and user sections; ordinary
Save preserves them. Full **Import Markdown → Preview Changes → Confirm Import** instead replaces the **entire**
file after identity, revision and content checks; it does not import `monitor.yaml` or accept partial CSV/TSV
tables or AI Mapping Packs. New Journals/Publishers require OpenAlex identity verification; a newly introduced
Publisher must remain Unassigned or reference a Service ID present in the previously saved file, not a Service
created in the same import. A dirty draft requires explicit Save and a new Preview, or explicit Discard.
**Export Markdown** downloads the complete saved file, not draft data; **Reload** discards pending edits. Older
pre-Access-Service lists have a separately confirmed one-time Upgrade, never a silent conversion.

Settings contains Institution preferences, diagnostics for Current Run/Workspace health and **Workspace Reset**.
Reset requires fresh confirmation and typing `RESET`; it **permanently deletes the entire configured Workspace**
with no Undo, including user-created files inside it. It does not delete external configuration, covers, Chrome
data or the Zotero Library, and does not start Run. A later explicit Run recreates supported Workspace outputs.
Back up important data before Reset.

## Data storage and operational limits

Typical local layout:

~~~text
monitor.yaml                   # Monitor, months, Institution, Kept workflow
list.md                        # Journals, Publishers, Services, user sections
journal-assets/                # Cover manifest and images
workspace/
  Papers/                      # DOI Papers, frontmatter and notes
  Authors/                     # Author notes
  Inbox.base                   # Optional Obsidian Bases views
~~~

Paper `status` is the durable workflow authority: `candidate`, `rejected`, `kept` or `exported`. The local Paper
UUID and normalized DOI have distinct roles. Obsidian views and `Inbox.base` are optional and customizable; the
app creates the default base only when missing. Run does not modify existing Paper or Author files; Enrich,
decisions and confirmed native Import have separate narrowly guarded writes.

Current Run/Connector/browser queues are process-local; there is no permanent Import history, background
synchronization or automatic restart recovery. Actual Zotero Desktop items and school login sessions belong to
their external applications.

For complete technical contracts consult [SPEC](SPEC.md). For repository development rules consult
[AGENTS](AGENTS.md). For Chrome unpacked installation, independent build/tests and troubleshooting see
[Connector README](connector/README.md). Frontend asset licenses are documented in
[UI sources](src/literature_monitor/web/static/ui_next/SOURCES.md).
