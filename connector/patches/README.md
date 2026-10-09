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
