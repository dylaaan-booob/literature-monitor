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
