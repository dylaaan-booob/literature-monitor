# Literature Monitor Connector patches

Files in this directory are the reviewable Literature Monitor source delta
against the exact Zotero Connector revision in `../upstream.lock`.

`../build.sh` applies `*.patch` files in lexical order with
`git apply --check` before applying them. A context mismatch is a hard build
failure; the build never silently accepts upstream drift.

The tracked patches currently contain:

- `0001-literature-monitor-component-name.patch`: the A1 Chrome/MV3 component
  name change;
- `0002-literature-monitor-automatic-capture-hooks.patch`: the A5 MV3 worker
  wiring and the smallest upstream save-lifecycle hooks required for
  Literature Monitor automatic capture.

The A5 hook is deliberately narrow. It propagates an automatic/local-only
marker, prevents that path from falling back to Zotero's remote server, reports
deterministic pre-parent failure, and signals local top-level `saveItems`
acceptance before collection/attachment work. The bridge/task orchestration
itself lives in the Literature Monitor-owned overlay runtime rather than in a
vendored upstream tree.

Every changed upstream path remains declared in `../delta.lock`.

The unreleased v0.6.3 A2 changes extend `0002` on the same pinned revision:
invocation-local identity and dispatch state replace shared-session reads,
a one-shot live permission guards native `saveItems`, and the acceptance signal
carries the matching invocation/native session. Native parent DOI, one-parent ID and native HTTP 201 checks apply only
to automatic saves. `src/common/connector.js` exposes status and body for that
one endpoint without altering other Connector callers. Both empty-body 201
and matching single-item JSON 201 responses are supported; errors and missing
transport acknowledgments remain unconfirmed. The parent hook leaves upstream
collection and attachment saving intact; a separate pipeline-finished signal
allows the runtime to close only the completed project task tab. A monotonic
deadline rejects expired commands, including delayed permission replies.
No attachment hook claims PDF completion; see `../PROVENANCE.md` for the
inspected evidence boundary.

The unreleased v0.6.3 A6 addition to `0002` only imports the AGPL access overlay
before the runtime. It adds no upstream translator, `saveItems`, attachment or
proxy-association modifications. The empty production rule table and bounded
navigation/observation policy live in `../overlay/literature-monitor-access.js`.

The unreleased v0.6.3 challenge/readiness correction also changes
`src/browserExt/background.js`: automatic translation bypasses the unbound
background tab cache and uses `tabs.sendMessage` with the selected document ID
and current top-frame translator. Manual saves and native Zotero saving remain
unchanged. Current-document readiness, response/DOM challenge observation and
pre-trigger navigation recovery live in the overlay; see SPEC §42.16.

`0003-literature-monitor-paper-enrichment.patch` adds a separate read-only
content-script message and MV3 offscreen handler (SPEC §§47–48). It uses the pinned
Translate Framework with `libraryID:false` and `saveAttachments:false`, guards
the actual loaded translator against Desktop/Server execution, and never
enters PageSaving's native-save path. Upstream pins and permissions are unchanged.

The same read-only handler transports Abstract and publication date. It calls
upstream `Zotero.Date.strToDate()` and preserves absent components, rejecting
unparsed remainder and explicit numeric-month correction. The Python server
alone applies missing-component `01` defaults and calendar validation.

SPEC §50 extends the same Item projection with original `creators`; it adds no
translator adapter or saving path. Creator interpretation and explicit local
Author/Paper writes remain outside the AGPL Connector.

`0004-literature-monitor-connector-lifecycle-cleanup.patch` follows `0003` and
fixes only `background.js`, `background/offscreenManager.js` and
`offscreen/offscreenTranslate.js`. Cleanup compares normalized tab IDs against
all live tabs, including loading pages. Both periodic paths snapshot candidate
IDs before querying tabs, so records created during the query survive that pass.
The background sweep covers tab info and injection caches; offscreen cleanup
returns the IDs actually removed and uses working JSON diagnostics. Existing
tab-close listeners, fifteen-minute intervals, native saving and attachment
processing remain unchanged. No cleanup closes a tab or the offscreen document.

After `./connector/build.sh`, run
`node --test connector/tests/lifecycle-cleanup.test.cjs` from the repository root.
This deterministic VM regression reads the applied source in `.work/upstream`;
it needs no Chrome profile or Zotero Library and does not measure memory use.
