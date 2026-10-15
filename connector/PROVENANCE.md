# Connector source provenance and license boundary

The Literature Monitor Chrome/MV3 Connector is a derivative of Zotero Connector source. It is **separate** from
the MIT `literature_monitor` Python distribution and its runtime import graph. This file records the exact
upstream baseline, the changes owned by Literature Monitor and the corresponding-source obligations. Do not
remove original license headers, notices or upstream attribution when changing the build or publishing an
artifact.

## Pinned upstream authority

- Project: [zotero/zotero-connectors](https://github.com/zotero/zotero-connectors)
- Git repository: `https://github.com/zotero/zotero-connectors.git`
- Commit: `876e41ad15139077f2e07b2f71a0fa94742e0b4a`
- Commit date: 2026-09-17
- Commit subject: `Let users suppress the Safari all-websites recommendation (#639)`
- Governing upstream license: **GNU Affero General Public License, version 3**, as reproduced verbatim in [COPYING](COPYING). Individual source files and bundled third parties retain their own copyrights and notices.

[upstream.lock](upstream.lock) pins the upstream commit and all required top-level submodules:

| Upstream gitlink | Exact commit |
| --- | --- |
| `src/translate` | `485ac36edbd66459e2aa3bad4d4b36921be2c7c2` |
| `src/utilities` | `cccf1235a318c259345fc623d5e9d6770ba19df7` |
| `src/zotero` | `01ebeb183439bbd79797bbcd4a41e99aad099ab8` |
| `src/zotero-google-docs-integration` | `a46413151134ad699c351ac42ecbbe9c76c8e6cc` |
| `src/zotero-schema` | `b5b3f51217a99b3c41585d468ee1dc0837233d13` |

The pinned upstream README specifies these top-level submodules; recursive initialization is not required for
the normal build. The top-level Connector `COPYING` must remain byte-identical to the pinned upstream copy. The
current tracked Connector build passes version `0.7.0` to upstream's supported `-v` option without moving
upstream pins or altering its package metadata.

## Exact reconstruction and Literature Monitor delta

[reconstruct-upstream.sh](scripts/reconstruct-upstream.sh) starts from an empty checkout, fetches only the
pinned upstream commit, verifies its gitlink set, initializes the five exact submodules and verifies the license
copy. The [build script](build.sh) applies [patches](patches/README.md) in lexical order after
`git apply --check`, builds the original Chrome/MV3 product and adds Literature Monitor-owned `overlay/` files.
A mismatch of upstream commit, gitlink, license, patch context or [delta.lock](delta.lock) causes a failed
build. The generated source/build directories are not substitutes for the tracked inputs.

The reviewable delta consists of:

| Changed source boundary | Purpose |
| --- | --- |
| `0001-literature-monitor-component-name.patch` | Distinct Chrome/MV3 component identity |
| `0002-literature-monitor-automatic-capture-hooks.patch` | Worker wiring, document-bound automatic translator invocation, one-shot native `saveItems` authorization, attributable parent response observation and local-only save isolation |
| `0003-literature-monitor-paper-enrichment.patch` | Separate read-only translation message and MV3 offscreen handler; no native or Zotero-server save |
| `0004-literature-monitor-connector-lifecycle-cleanup.patch` | Tab-ID normalization, live-tab/offscreen cache cleanup with safe snapshot iteration |
| `overlay/literature-monitor-runtime.js` | Localhost heartbeat, Import and Enrichment tasks, browser owner, results and window context |
| `overlay/literature-monitor-access.js` | Conservative DOI landing-origin observation; no production verified SP auto-login rule |
| `overlay/literature-monitor-carsi.js` | Official CARSI Shibboleth Portal, Service search, Detail and Visit automation, optional recognized IdP consent |
| `overlay/literature-monitor-covers.js` | Google Images first-original extraction and Chrome-side bounded image fetch |
| `overlay/LITERATURE_MONITOR_PROVENANCE.txt` | Artifact provenance boundary and license pointers |

The authoritative list of **all modified upstream file paths and overlay additions** is `delta.lock`. The source
patch boundary includes background worker/manifest, `background.js`, PageSaving/ItemSaver, native Connector
transport, inject and offscreen translation files. No Connector-derived AGPL code or parsed Zotero date utility
is copied into the MIT Python package. The runtime uses the pinned upstream translator and
`Zotero.Date.strToDate()` under the Connector license; the Python application only validates returned data and
operates on local Markdown.

The independent [Chrome Connector README](README.md) records installation, permissions, current browser behavior
and developer verification commands. Review the patch README before changing a patched upstream location.
Upstream's existing copyright and embedded third-party licenses continue to apply; the independent frontend
asset notices are in [UI Next SOURCES](../src/literature_monitor/web/static/ui_next/SOURCES.md).

## Native Save attribution and scope

In the pinned `src/common/itemSaver.js` and bundled Zotero Desktop connector-source implementation, native
`saveItems` returns its parent acceptance before downstream collection/attachment completion. A successful local
HTTP 201 may have an empty body or a one-item response containing the same client-side parent identity. This
identity is **not** a Zotero Library key. If both request and response carry IDs, they must be valid
own-property scalar values (a nonblank string or finite nonzero number) with exact type-sensitive equality;
missing, malformed or mismatched responses never establish attributable parent acceptance. Any post-dispatch
transport error remains uncertain, not a proven no-effect failure.

The pinned attachment code can report progress and Snapshots without confirming a real saved PDF. The checked-in
Desktop source does not supply a proven actual-file completion receipt for this producer; the application
therefore keeps PDF `unverified` in the absence of stronger attributable evidence. Do not turn generic
attachment progress, response status, `snapshot=false`, native parent acceptance or an arbitrary timeout into
PDF success. Nor may a browser navigation or CARSI Visit grant Zotero native-save permission.

Automatic translation is bound to the current task DOI, committed Document, tab, invocation/native session,
finite deadline and single-use dispatch authorization. It disables upstream remote Zotero-server fallback for
this path only. Translator readiness uses current-document evidence; stale background caches or a challenged
page cannot authorize saving. Chrome main-frame challenge observation reads only relevant non-blocking metadata
and avoids credentials, Cookies and response bodies. The task may not reissue a native save after an uncertain
first dispatch. Manual Zotero saving remains upstream-owned.

## CARSI, Enrichment, Covers and browser ownership

CARSI automated Preparation navigates only official Shibboleth Portal → verified resource search → official
Detail/Visit paths. It accepts a full saved Service Name, verifies the `onlyMySchool` filter-off state and
enforces task/document/Visit-nonce checks. It reports observed navigation, not authentication or entitlement.
Preparation leaves every tab/window open; local cleanup releases listeners and stale authority. Optional
`shibboleth-consent/v1` handles only recognized same-origin native consent forms, never passwords, MFA, Cookies
or arbitrary login.

Read-only `paper-enrichment/v2` invokes the pinned Translate Framework in browser mode with saving disabled,
returning original Item data including creators and parsed date components. Python owns the subsequent narrow
Markdown save. Current date precision is preserved; legacy default-month/day padding and accepted-Preview page
cleanup no longer define the current contract.

`journal-covers/v2` obtains first-result original image URLs using recognized Google Images result data,
including regional domains. Chrome background fetches are bounded, serial and credential-free. Source inspection
was informed by [ViewImage](https://github.com/bijij/ViewImage) and
[Classic View for Google Images](https://github.com/hsr88/classic-view-for-google-images); **no code/dependency
from those projects is bundled**. Chrome network policy does not replicate an old Python DNS-pinned image
downloader.

`background-task-windows/v1` creates unfocused normal Chrome windows and reuses one window for a Kept
Preparation/Import workflow. Optional `window_context` is ephemeral routing metadata, never proof of identity,
login or a native-save grant. Enrich and Import may close only unchanged matching task documents under their
accepted lifecycle; CARSI pages are not closed. The shared browser exclusion and native save authorization
remain independent.

The Connector requests no new credentials, cookie extraction/transfer, browser-session registry, Zotero Library
inspection, background sync, generic PDF acquisition or institution-specific login routes. The automatically
observed DOI landing-origin access overlay has **no verified production SP rule**; synthetic fixtures are not
evidence of live federation.

## Corresponding source and intentional updates

Every distributed Connector binary/ZIP must retain an AGPL-compatible component identity, original/upstream
copyright and license notices, this pinned source relationship and access to **complete corresponding source**,
including required upstream and submodule revisions, local patches, overlays, lockfiles, scripts and build
instructions. The release ZIP's `chrome-mv3/` and `corresponding-source/` boundaries implement that obligation.
Neither an upstream link alone nor an opaque compiled bundle substitutes for the reviewed local delta and
corresponding-source information. Distribution requirements for separately licensed bundled dependencies also
continue to apply.

To intentionally update upstream: select and verify an exact upstream commit; inspect `.gitmodules`, gitlinks,
original `COPYING`, lockfile and build procedure; update `upstream.lock` with the exact commit set; retain the
verbatim license; review/rebase **every** patch; update `delta.lock` and provenance marker where necessary; then
reconstruct from an empty checkout and validate the separated artifacts, licenses, upstream tests and runtime
behavior. Never auto-accept failed patch contexts or silently change source pins. Publishing a new Connector
requires the normal separate user authorization.
