# Literature Monitor Connector

This directory defines the independent browser component prepared for
v0.6.2 (UNRELEASED), retaining the v0.6.1 protocol under SPEC §40. It is derived from the official
Zotero Connector source and is separate from the MIT Python
`literature_monitor` package. The tracked build entry point generates the
Chrome/MV3 artifact with version `0.6.2`.

A1 establishes source provenance, licensing, reconstruction and Chrome/MV3
build boundaries. A5 adds the independent Literature Monitor automatic-capture
runtime within that AGPL component: localhost heartbeat/claim/result transport,
one dedicated DOI task tab, reuse of the pinned upstream translator save
machinery, and a top-level bibliographic-save observation hook that reports
without waiting for PDF/Snapshot attachment completion. Collection/tag routing,
Group Library fallback, credential automation and attachment readiness are not
part of this component.

## License boundary

The repository root Python application remains MIT-licensed under the root
`LICENSE`. Upstream-derived Connector source and the Literature Monitor
derivative delta are governed by the upstream AGPLv3 boundary recorded here.
`COPYING` is the verbatim license/copyright file from the pinned Zotero
Connector revision. Upstream source-file headers and third-party notices remain
authoritative for their respective files.

See `PROVENANCE.md` for the exact upstream revision, submodule pins,
reconstruction method and update procedure.

## Build prerequisites

The build uses the upstream Node/npm toolchain directly and requires:

- Git with network access to the repositories named in `upstream.lock`;
- Node.js and npm;
- Bash;
- `rsync`, `jq`, `perl`, `awk`, `sort`, `cmp` and `mktemp`.

The upstream npm lockfile is authoritative for JavaScript dependencies.
`npm ci --ignore-scripts` is used for the Connector build boundary so
unrelated package install scripts, including browser downloads used by upstream
E2E tests, are not part of the Connector build input.

## Build

From the repository root:

```bash
./connector/build.sh
```

The script reconstructs the pinned upstream source into
`connector/.work/`, verifies and applies the tracked Literature Monitor
delta, runs the upstream debug build with release version `0.6.2`, verifies
a Manifest V3 Chrome artifact, and copies the result to:

```text
connector/build/chrome-mv3/
```

Both directories are generated and ignored by Git. For isolated validation,
set `LM_CONNECTOR_WORK_DIR` and `LM_CONNECTOR_OUTPUT_DIR` to
repository-external temporary paths. Only the repository-managed default
generated paths are recursively replaced. With external overrides, the output
directory must not already exist, and `<LM_CONNECTOR_WORK_DIR>/upstream` must
not already exist; an existing ordinary work-root directory itself is allowed.
External symlink targets are rejected. This fail-closed rule keeps caller-owned
directories from being treated as disposable Connector build state.

For local installation or verification, open `chrome://extensions/`, enable
Developer Mode, choose **Load unpacked**, and select the generated `chrome-mv3`
directory. Alternatively, extract the published
[Connector ZIP](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.6.1)
and select its `chrome-mv3/` directory; it includes corresponding source and
license/build instructions. This is a local install path, not a Chrome Web Store release.
A7 recorded a successful toolbar-free automatic save in normal Chrome with
Zotero Desktop and an existing institutional session; that scoped live acceptance
does not constitute a Chrome Web Store release. Publication and asset
verification are recorded in [SPEC §40.16](../SPEC.md#4016-v061-release-and-documentation-closeout-2026-10-07).
See [SPEC §§40.14 and 40.15](../SPEC.md#4014-a7-implementation-and-validation-evidence-2026-10-07)
for live validation and preparation evidence, and
[AGENTS.md](../AGENTS.md#release-execution-and-verification-reuse) for release
execution and validation reuse.

The v0.6.2 candidate uses the same upstream pins, patches and runtime overlay
as v0.6.1; only its manifest release identity changes. Candidate construction
and checks are recorded in [SPEC §41.15](../SPEC.md#4115-a7-release-preparation-2026-10-08).
The v0.6.1 link above remains the latest published artifact.

## Runtime test

From the repository root:

```bash
./connector/test.sh
```

This runs the deterministic Literature Monitor-specific Node harness against
the real overlay runtime with mocked browser, Zotero and localhost bridge
boundaries. It requires no browser profile, live Zotero instance or network.

## Automatic capture boundary

The runtime starts only after the pinned upstream background initialization is
ready. It keeps the MV3 worker alive through the upstream keep-alive mechanism,
heartbeats the fixed `http://127.0.0.1:8000` bridge, and claims at most one
automatic task at a time. Claimed targets must be HTTPS URLs at exact authority
`doi.org`; task state is service-worker process-local and is not stored in
browser storage.

Each accepted command creates a fresh normal Chrome task tab. A usable
single-item upstream translator is required; multiple-item translators are not
auto-selected, and the automatic path disables generic webpage fallback and
Zotero-server fallback. Immediately before the translator save is triggered,
Zotero Desktop reachability is checked again.

The A5 upstream hook observes successful local `saveItems` acceptance for the
top-level bibliographic item before collection lookup and attachment work.
That signal is the only automatic `CONFIRMED` evidence. Deterministic
pre-parent failures report `FAILED`; ambiguity after the save trigger,
including observation timeout or task-tab closure, reports `UNCONFIRMED`.
No outcome waits for PDF/Snapshot completion. Task tabs are left open for
inspection/recovery.

## Source layout

- `upstream.lock`: exact upstream and top-level submodule revisions;
- `COPYING`: verbatim upstream license/copyright file;
- `.gitattributes`: preserves that verbatim upstream file without treating its existing trailing whitespace as a Literature Monitor formatting defect;
- `PROVENANCE.md`: source relationship and intentional-update procedure;
- `patches/`: reviewable modifications to pinned upstream source;
- `overlay/`: Literature Monitor-owned non-upstream files added to artifacts;
- `delta.lock`: explicit Literature Monitor delta boundary;
- `scripts/reconstruct-upstream.sh`: fail-closed source reconstruction;
- `build.sh`: independent Connector build entry point;
- `test.sh` and `tests/`: deterministic Literature Monitor runtime tests.

No Connector-derived source belongs under `src/literature_monitor`, and the
Python wheel/sdist must not contain this component or its generated artifacts.
