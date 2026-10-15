# Connector upstream patches

The files in this directory are the entire reviewable upstream-source patch portion of Literature Monitor's
AGPLv3 Chrome Connector delta. They apply **only** to the exact Zotero Connector revision and top-level
submodule pins in [upstream.lock](../upstream.lock). New files owned by Literature Monitor live separately in
[overlay](../overlay/); [delta.lock](../delta.lock) enumerates every modified upstream path and overlay target.

## Current patch set

Patches apply in filename lexical order:

| Patch | Current purpose |
| --- | --- |
| `0001-literature-monitor-component-name.patch` | Distinguish the Literature Monitor Chrome/MV3 extension name from the official Connector. |
| `0002-literature-monitor-automatic-capture-hooks.patch` | Wire local runtime into the background worker; enforce current-document automatic translation; pass invocation-local identity and one-shot dispatch permission through native ItemSaver; prevent Zotero-server fallback on the automatic path; capture attributable top-level native parent acceptance before attachment work. |
| `0003-literature-monitor-paper-enrichment.patch` | Add separate browser-only, read-only translation message via inject/PageSaving/MV3 offscreen. The pinned Translate Framework runs with saving disabled and returns the matching Item's Abstract, date and creators without sending an Item to Zotero. |
| `0004-literature-monitor-connector-lifecycle-cleanup.patch` | Normalize tab IDs and safely sweep orphaned tab-info, injection and offscreen translation caches, including loading tabs and candidates added during an asynchronous sweep. No tab/document is closed by this patch. |

The automatic parent hook handles empty-body HTTP 201 or a single matching echoed client-side parent ID.
Incompatible, missing and post-dispatch ambiguous replies remain uncertain; they cannot trigger a second native
save or declare a PDF saved. Collection and attachment processing remain upstream-owned. Author interpretation,
date precision and Paper Markdown saving belong to the Python server, not the AGPL patch.

The read-only enrichment patch contains the pinned `Zotero.Date.strToDate()` call in AGPL MV3 code. Do not copy
its implementation into the MIT Python package. The lifecycle patch cleans references only; it neither cancels
user work nor proves a measured memory benefit.

## Application and maintenance

`../build.sh` reconstructs the pinned upstream source into its generated work directory. It checks every patch
with `git apply --check` before applying it in lexical order and validates the resulting upstream-file delta
against `delta.lock`. Patch/context drift is a **build failure**; do not use fuzzy application, silently skip a
patch or alter the upstream pin to bypass review. Overlay files are copied separately after the upstream build.

If changing an upstream file, inspect the exact pinned implementation and downstream callers, edit the relevant
numbered patch, update `delta.lock` when its affected paths change, and verify source/delta/license
preservation. Use an isolated Connector build only when authorized. For deterministic lifecycle regression after
reconstruction:

~~~bash
node --test connector/tests/lifecycle-cleanup.test.cjs
~~~

Other runtime/CARSI/Enrichment tests are invoked by `./connector/test.sh`. A successful mocked Node test, patch
application or Chrome build does not prove real Zotero parent/PDF outcomes or CARSI login.

Exact upstream provenance, AGPL notices, source-submodule pins, corresponding-source distribution and
intentional-update procedure are documented in [PROVENANCE](../PROVENANCE.md). No patch may change native
saving, browser permissions or user data ownership outside the reviewed delta.
