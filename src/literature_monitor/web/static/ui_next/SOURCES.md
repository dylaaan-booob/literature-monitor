# UI Next sources

UI Next ports the locally approved `prototypes/inbox-selected-view` interface.
The prototype is maintained separately; UI Next runs from these packaged assets
and real application APIs, without prototype snapshots, Demo controllers, remote
fonts, Three.js or recording tools at runtime.

- RhinE `aa92ac9aa8fd8c611f5121583ae7c2a406b52607`: archive sprites,
  inspection frames, projection/springs and the Kept layout. MIT, Copyright (c)
  2026 the Audio Archive authors; retained in `licenses/RhinE-MIT.txt`.
- Blog Theme `6f1e5b0c9a7e9b253db8b8a9b93a397c07d7ba93`: surface transitions,
  document decryption, Run video adaptation, Kept 2D scene and recorded loops.
  MIT, Copyright (c) 2026 LBEILC; retained in `licenses/Blog-Theme-MIT.txt`.
- RhineLabUI `12cc5e4013acb9408f753ff71de6b8492299b7c8`: prototype-derived Settings
  form layout and controls. MIT, Copyright (c) 2026 LBEILC; retained in
  `licenses/RhineLabUI-MIT.txt`.
- Geist Mono: bundled font files and the original SIL OFL 1.1 notice.
- MiSans: bundled font files, original notice and license PDF in `licenses/`.
- Journal covers are read through the existing saved-cover endpoint and are not
  bundled as personal snapshot assets. Code licenses do not grant rights to
  third-party covers, fonts or marks beyond their respective licenses.

Application changes replace Demo data/timers with Workspace, decision, Run,
Enrichment, Kept workflow and Settings APIs. Settings preserves the approved
prototype DOM/CSS and interaction structure; configuration identities and all
Save/FILES/Cover/System outcomes come from production services. No
`settings-data.js`, Demo catalog or scenario controller is packaged. Canvas geometry, inspection frames, font
choices, native video dimensions and player timing are retained. `kept.js` and
`kept/` are bundle inputs; `kept-app.js` / `kept-app.css` are generated together.

From the repository root, build after changing the Kept controller or scene:

```sh
npm ci --ignore-scripts --prefix tools
node tools/build-ui-next.mjs
```

This uses the pinned esbuild development dependency in `tools/package-lock.json`. Serving the
already-built UI requires only the existing Python Web service, with no Node or
npm dependency. Full original adaptation history remains in the prototype's
`SOURCES.md`.
