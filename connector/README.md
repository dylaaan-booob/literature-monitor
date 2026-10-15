# Literature Monitor Chrome Connector

This is a separate Chrome Manifest V3 component derived from pinned
[Zotero Connector](https://github.com/zotero/zotero-connectors) AGPLv3 source. It supplies native Zotero saves,
read-only Paper Enrichment, CARSI Portal Preparation, Journal cover search and browser-task execution for the
local Python application. The Python wheel/sdist remains MIT and does **not** contain the Connector.

The generated Connector manifest version is `0.7.0`, matching the tracked `build.sh` setting. The
stable release is
[v0.7.0](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.7.0). Exact source revisions,
submodules, patches, overlays, copyrights and corresponding-source obligations are in
[PROVENANCE.md](PROVENANCE.md).

## Installation and local operation

Install the released Connector by extracting its separate release ZIP and opening `chrome://extensions/` in
Chrome. Enable Developer Mode, select **Load unpacked**, and choose the ZIP's `chrome-mv3/` directory. For local
development use the generated `connector/build/chrome-mv3/` directory instead. Reload the unpacked extension
after changes to its JavaScript or build output, restart the local Web service when Python endpoints have
changed, and refresh the five-page Web UI.

Run the local Web app with `uv run literature-monitor gui --config monitor.yaml`; the Connector communicates
only with `http://127.0.0.1:8000`. Verify that Settings reports the Connector connected before Import,
Enrichment or automated CARSI/Cover tasks. **Native Zotero Import requires Zotero Desktop running**.
Browser-only Enrichment, CARSI Preparation and cover search do not require Desktop. The official Zotero
Connector can coexist for ordinary manual saves.

The Connector does not manage school passwords, MFA, Cookies or institutional sessions. Users complete such
steps themselves in the normal Chrome task window and manually close retained CARSI pages. Saved
Publisher/Service URLs are manual access shortcuts, not trusted actual article-host or entitlement evidence.

## Current runtime contract

The Connector advertises supported task capabilities in loopback heartbeat. Requests are claimed, authorized and
completed under transient request, document, tab/window, target and generation identities. At most one
conflicting browser task or native save is active. The host enforces additional one-shot native-save grants. A
stale callback, expired task, ambiguous native save or browser navigation cannot acquire permission merely from
a heartbeat or open tab. Task execution is process-local; restarting the app/extension does not resume an
unfinished job.

**Import.** `PREPARE & IMPORT` and `IMPORT ONLY` are the Kept page entry points. The native Zotero Connector
translator must find a matching single literature Item for the DOI in the owned HTTPS `doi.org` task. The
upstream `saveItems` parent acceptance is correlated to the live invocation. Only attributable acceptance
followed by a guarded Paper write moves `kept → exported`. Native failure before dispatch, uncertain responses
after dispatch and failed local Markdown writes remain distinct results. PDF processing is independent; an
unknown attachment is `unverified` even when the parent was saved. Inspect Zotero before retrying an uncertain
parent to avoid duplicates. No My Library enumeration, collection/tag routing, custom PDF downloads, server-save
fallback or library synchronization is added.

**CARSI Preparation.** PREPARE & IMPORT collects only related CARSI Services from the current Kept Workspace;
Manual Login Services retain their saved URLs. The task enters the official CARSI Shibboleth Portal with the
configured public Institution IdP identifier. The user completes institutional login/MFA. The Executor checks
that `onlyMySchool` is unchecked, searches by the name suffix after the last `&`, and selects exactly one result
matching the **full saved Service Name**. It validates an official Detail URL and Visit control, then reports
Opened only after an attributable Visit click and target opening. Ordinary per-Service timeouts advance the
queue; login-wait expiry, cancellation or disconnection end it. These outcomes never establish authentication,
entitlement or PDF access. **Preparation never auto-closes any tab/window**, including Portal, Detail, target
and IdP pages.

**Enrichment.** `paper-enrichment/v2` runs a browser-only, read-only translation of the Paper DOI, returning
eligible Abstract, precision-preserving publication date and creators to Python. The server validates and
immediately saves only missing eligible fields; there is no Preview or separate Save. Existing Author files are
not modified; a Paper-save conflict can leave newly created Author files with a reported path. Cancel does not
reverse a completed write. No Zotero item or attachment is saved.

**Cover search.** `journal-covers/v2` uses normal Chrome Google Images pages and attempts the first main-result
original image. Up to three searches run together; downloads are serial, with bounded time, size and no
credentials. Python validates and atomically saves normalized local image bytes in `journal-assets/`. Failures
do not trigger thumbnails, automatic alternative results or configuration changes.

**Browser windows.** `background-task-windows/v1` creates unfocused normal task windows. One Kept workflow
reuses its Preparation/Import window while present. Enrich owns a separate window per request; Import Only can
retain a verified blank anchor. Only unchanged project-owned Enrich/Import task pages may close under their own
accepted cleanup rules. User-navigated pages are retained, and there is no automatic foreground focus
restoration. A transient window context is not native-save or navigation authority.

**Optional consent.** `shibboleth-consent/v1` applies a default-off Institution preference only to recognized
same-origin Shibboleth Global Consent forms during active CARSI login waiting. It never handles credentials,
MFA, unrelated IdP pages or user consent outside that narrow step. School-stored consent may persist after the
application setting is disabled.

See [SPEC §§7–10](../SPEC.md#7-kept-workflow-and-native-zotero-import) for app-side persistence, browser
arbitration, deadlines and protected states.

## Build and tests

The pinned upstream source is reconstructed into a generated Git-ignored work directory; patches apply only
after context checks and overlays are then copied. Required tools: Git with access to the pinned
upstream/submodule repositories, Node.js/npm, Bash, `rsync`, `jq`, `perl`, `awk`, `sort`, `cmp` and `mktemp`.
The Connector build runs the upstream npm dependencies with install scripts disabled and does not require a
personal Chrome profile or user Zotero Library.

From the repository root, for an explicitly authorized Connector build or runtime test:

~~~bash
./connector/build.sh
./connector/test.sh
~~~

The default generated directories are `connector/.work/` and `connector/build/chrome-mv3/`. Build overrides
`LM_CONNECTOR_WORK_DIR` and `LM_CONNECTOR_OUTPUT_DIR` allow **external** isolated paths; the script rejects
unsafe existing targets and symlink replacements. Consult the script's current guards before using overrides. No
build or test is a substitute for real Chrome/CARSI/Zotero acceptance.

`test.sh` runs the local Node runtime/CARSI/related deterministic harnesses using mocked browser, upstream and
loopback boundaries. After an authorized upstream reconstruction,
`node --test connector/tests/lifecycle-cleanup.test.cjs` also checks applied-source cache cleanup.
Browser-focused tests do not prove actual institutional authorization, visible Chrome focus, file access or
Zotero PDF completion.

## Source layout and maintenance

| Path | Role |
| --- | --- |
| `upstream.lock` | Exact upstream and five required top-level submodule commits |
| `COPYING` | Verbatim pinned upstream AGPLv3 license/copyright |
| `patches/` | Reviewable, context-checked changes to upstream source |
| `overlay/` | Literature Monitor-owned runtime/CARSI/Access/Cover code and provenance notice |
| `delta.lock` | Expected patched upstream and added overlay paths |
| `scripts/reconstruct-upstream.sh` | Fail-closed reproducible checkout |
| `build.sh` / `test.sh` / `tests/` | Independent build, verification and fixture sources |

The upstream project and submodule pins do not float. An intentional update requires re-review of upstream
`COPYING`, gitlinks, lockfile, every patch, declared delta, generated manifest and shipped corresponding source.
Keep license headers/notices intact. See [patch maintenance](patches/README.md) and [provenance](PROVENANCE.md);
do not change source pins only to make a failing patch apply.
