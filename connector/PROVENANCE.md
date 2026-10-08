# Literature Monitor Connector provenance

## Upstream authority

- Upstream project: `zotero/zotero-connectors`
- Canonical repository: `https://github.com/zotero/zotero-connectors.git`
- Pinned revision: `876e41ad15139077f2e07b2f71a0fa94742e0b4a`
- Upstream commit date: 2026-09-17
- Upstream commit subject: `Let users suppress the Safari all-websites recommendation (#639)`
- License: GNU Affero General Public License version 3 as stated in the
  pinned upstream `COPYING`; individual upstream files retain their own
  copyright/license notices.

`upstream.lock` also records every top-level gitlink required by this exact
revision:

| Path | Pinned revision |
| --- | --- |
| `src/translate` | `485ac36edbd66459e2aa3bad4d4b36921be2c7c2` |
| `src/utilities` | `cccf1235a318c259345fc623d5e9d6770ba19df7` |
| `src/zotero` | `01ebeb183439bbd79797bbcd4a41e99aad099ab8` |
| `src/zotero-google-docs-integration` | `a46413151134ad699c351ac42ecbbe9c76c8e6cc` |
| `src/zotero-schema` | `b5b3f51217a99b3c41585d468ee1dc0837233d13` |

The pinned upstream README states that normal Connector builds require only
these top-level submodules; recursive submodule initialization is not required.

## Reconstruction and delta

`scripts/reconstruct-upstream.sh` initializes an empty checkout, fetches only
the exact upstream revision, verifies that its complete gitlink set matches
`upstream.lock`, initializes those exact top-level submodule commits, and
checks that this directory's `COPYING` remains byte-identical to the pinned
upstream license file.

Literature Monitor-owned changes are not mixed into `src/literature_monitor`.
They are represented only by:

- `patches/*.patch`: source patches applied in lexical order after
  `git apply --check`;
- `overlay/`: files copied into the generated Chrome/MV3 artifact after the
  upstream build;
- `delta.lock`: the expected upstream paths and overlay files changed by
  Literature Monitor.

The released v0.6.1 delta keeps the A1 Chrome/MV3 component-name
and provenance changes and the A5 automatic-capture runtime. The tracked
Literature Monitor build entry point supplies candidate version `0.6.2` to the pinned
upstream build through its supported `-v` option; the upstream revision,
submodule pins and upstream package metadata remain unchanged. v0.6.2 remains
UNRELEASED. The A5 source delta is
limited to:

- wiring a Literature Monitor-owned background runtime into the MV3 worker;
- propagating an automatic/local-only save marker through the existing
  page-saving and ItemSaver path;
- disabling Zotero-server fallback only for that automatic path;
- surfacing a tab-correlated top-level local `saveItems` acceptance signal
  before collection lookup and attachment work, plus deterministic pre-parent
  failure signaling.

The runtime itself remains an overlay file. It communicates only with
`http://127.0.0.1:8000`, creates one dedicated normal task tab per accepted
command, and reuses the pinned upstream translator machinery. It does not
introduce collection/tag routing, Group Library fallback, attachment-completion
gating, credential automation or a Python import/runtime dependency.

## Intentional upstream updates

An upstream update is a review event, not a floating build:

1. resolve the intended `zotero/zotero-connectors` commit explicitly;
2. inspect its `.gitmodules`, gitlink revisions, `COPYING`, lockfile and
   build instructions;
3. update `upstream.lock` with the new full commit SHA and exact top-level
   gitlink SHAs;
4. replace `COPYING` only with the verbatim file from that pinned revision;
5. rebase/review every patch and update `delta.lock` if the Literature
   Monitor delta changes;
6. update the artifact provenance marker;
7. reconstruct from an empty directory and rerun the Connector build,
   packaging-isolation checks and applicable upstream tests.

The build fails when the upstream revision, gitlink set, submodule commit,
license copy, patch context or declared delta does not match the tracked locks.
