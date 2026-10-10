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
Literature Monitor build entry point supplies release version `0.6.4` to the pinned
upstream build through its supported `-v` option; the upstream revision,
submodule pins and upstream package metadata remain unchanged. Published v0.6.2
source and artifact verification are recorded in SPEC §41.16. The A5 source delta is
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

## v0.6.3 A2 development outcome evidence

This section records source inspection and deterministic tests, not live
Chrome/Zotero acceptance. The upstream and every gitlink above are unchanged.
The A2 delta remains confined to the already declared worker, PageSaving,
ItemSaver and overlay paths in `delta.lock`.

### Evidence from the exact pinned source

| Source | Observed behavior | Outcome authority |
| --- | --- | --- |
| `src/common/itemSaver.js`, `_saveToZotero()` | Native `saveItems` resolves before `getSelectedCollection` and `saveAttachmentsToZotero`. | Parent acceptance is independent of attachment work. |
| `src/common/connector.js`, `callMethod()` | Transport, parse and HTTP errors reject; a lost response cannot establish whether a save occurred. | Errors after dispatch are uncertain, never no-effect proof. |
| Pinned `src/zotero/chrome/content/zotero/xpcom/connector/server_connector.js`, `SaveItems.init()` / `saveItems()` | The bundled Desktop source returns `201` with parent item IDs from `onTopLevelItemsDone`; attachment work can continue after that response. | A matching native parent response supports acceptance, not PDF existence. This bundled source is not a probe of the user's installed Desktop. |
| `src/common/itemSaver.js`, `_saveAttachmentsToZotero()` | `snapshot === false` links also receive progress `100`; primary types include PDF and EPUB; resolver fallback may continue after a failed attachment attempt. | Generic progress/failure is not proof of this parent having an actual saved PDF or a final PDF failure. |
| `src/common/itemSaver.js`, `_executeSingleFile()` | Snapshot callbacks use the same `0`, `100`, and `false` progress vocabulary. | Snapshot success is not PDF success. |
| `src/common/itemSaver_background.js`, `saveAttachmentToZotero()` | The native download/upload path forwards attachment metadata and returns the connector-server response. The pinned bundled Desktop source has no `saveAttachment` endpoint implementation to inspect. | No proven actual-file receipt or terminal PDF evidence is available from this source bundle. No installed Desktop/file inspection was performed. |

### Producer and bridge contract

The claim supplies unpredictable request and invocation capabilities plus the
server-built DOI target. The runtime freezes these values and a finite expiry
before calling the native translator. PageSaving passes an invocation-local
object to ItemSaver; subsequent changes to shared `sessionDetails` cannot
change that object's identity or dispatch state.

Immediately before native `saveItems`, the automatic hook requires one
bibliographic item whose DOI matches the target and requests one live dispatch
permission. The runtime verifies its own extension sender, task tab, request,
invocation and DOI; it binds the native session, frame and document. The bridge
consumes the permission once, binds tab/session and revalidates any A1 durable
reservation. Neither permission loss, worker/application restart nor timeout
reissues it. Content also checks a monotonic finite deadline before and after
permission receipt, so a paused/late reply cannot revive the command.

Only a matching native parent acceptance response emits `CONFIRMED`. Explicit
pre-dispatch failures map to parent `NO_EFFECT` (legacy `FAILED`); ambiguous
transport/permission errors or observation expiry map to `UNCERTAIN` (legacy
`UNCONFIRMED`). Ordinary failure after dispatch is never no-effect proof.
No bridge outcome clears an A1 marker. The existing guarded legacy completion
reconciliation remains in place until A3; this task adds no `exported` transition.

Parent delivery does not wait for the full native save promise or attachments.
PDF is separately `unverified` in snapshots and completion handoffs. The contract
names `verified success` and `verified failure`, but this producer has no proven
evidence for either; the bridge rejects those assertions. No PDF event producer,
attachment registry, downloader, library scan or fixed-delay success inference
is introduced. Late, duplicate or unbound callbacks cannot change the parent.
The manual recovery path is inspection of the retained task tab and the saved
parent in Zotero Desktop; lack of PDF evidence does not imply PDF failure.

A2 verification uses temporary Workspaces, mocked browser/server boundaries and
executed shipped hook code. Release gates still require real ordinary Chrome
and Zotero acceptance of parent observations and truthful PDF `unverified`
behavior. Verified PDF reporting requires a separately established actual-save
or final-failure evidence source with invocation/parent attribution.

### A2 review fix: native parent identity

At the same pinned revision, `ItemSaver._saveToZotero()` preserves a supplied
truthy `item.id` or fills it with `Zotero.Utilities.randomString(8)` before
copying the native request. The pinned utility uses ASCII digits and letters.
The bundled Desktop `SaveItems.saveItems()` preserves supplied IDs and can
assign numeric IDs when absent; its `onTopLevelItemsDone` response copies
`item.id` into `items[].id` and returns `201`. This is a client correlation ID,
not a Zotero library item key. Neither an eight-character-only nor a
string-only acceptance rule represents that protocol.

The automatic hook now freezes the request's single parent ID before the native
call. Both identities must be own fields on item objects and valid JSON scalar
IDs: a nonblank string or a finite nonzero number. Response structure must
contain its own single-item `items` array. IDs must match without type coercion.
Missing IDs, null/empty IDs, booleans, object/array IDs, non-finite numbers,
inherited fields, damaged response structure and mismatches do not establish
acceptance, even if HTTP succeeded. They yield legacy `UNCONFIRMED` / parent
`UNCERTAIN`, never a no-effect claim or a second native save. The bridge leaves
the A1 attempt marker unresolved; the existing legacy completion adapter is
unchanged. PDF remains `unverified`.

Regression tests execute the shipped patch fragment with the actual overlay
listener, including valid native string/numeric identities and invalid equal
values. A temporary-Workspace Python test verifies that receiving this uncertain
outcome preserves the marker and prevents another reservation. These are
source/deterministic checks, not real Desktop response acceptance evidence.

### Unreleased v0.6.3 A6: bounded Access Service preparation

`literature-monitor-access.js` is an AGPL overlay loaded before the runtime.
The upstream save hooks, pin and existing MV3 permissions are unchanged.
Only actual main-frame commits/redirect events in the current dedicated DOI tab
contribute landing origins. A bibliographic Publisher, DOI prefix, manual Access
URL and IdP hostname are never SP identity authority. An origin becomes a known
SP only through an unambiguous, reviewed static identity rule.

The production rule table is empty: **no verified SP/federation routes** are
currently enabled. Rules need separate evidence references for SP identity,
institution selector, allowed destinations, return behavior and preservation of
existing host associations. Entries cannot contain sensitive query/fragment
parameters. Synthetic `.example` fixtures demonstrate the policy, not real
institution acceptance. Unknown services retain ordinary official bibliographic
capture and offer Open DOI for manual institutional access.

One opaque batch context retains service preparation outcomes only in worker
memory. Each service gets at most one automatic preparation per batch; each task
has a monotonic 20-second preparation budget and at most eight observed main-frame
redirect/commit hops. Repeated destinations, untrusted hosts, challenge form
submissions and expiry stop preparation. Redirect observation uses existing
webRequest permission without headers, cookies or blocking redirects; navigation
is restricted to a reviewed static entry and the original server-built DOI in
the owned task tab. Same-host repetition is conservatively treated as a loop.
Missing events cannot be advertised as complete redirect-chain evidence.

Only public HTTPS origins and a fixed reason reach the process-local bridge.
IdP session, SP session, authentication and resource entitlement are four distinct
unknown observations; navigation never proves any of them. No credentials,
Cookies, SAML assertions, sensitive URLs, browser storage or Paper/configuration
observations are persisted. Existing OpenAthens associations are neither read
nor changed by this overlay; rules require preservation, never overwrite.

Preparation cannot grant parent-save permission. Every asynchronous readiness or
navigation callback rechecks live task identity and deadline. Terminal tasks
cannot resume saving. Navigation after the translator trigger ends observation
as uncertain; an escaped A4 dispatch remains unresolved. The official translator,
one-shot native dispatch/acceptance hooks and PDF `unverified` boundary remain.
Actual service rules, real federation behavior and Chrome/Zotero acceptance are
future explicitly authorized live gates; see SPEC §42.12 for current evidence.


## Unreleased v0.6.3 challenge/readiness correction (2026-10-09)

This correction supersedes A6's conservative same-origin-loop behavior for
ordinary DOI navigation only. Such navigation compares path fingerprints without
retaining paths/query values, permits one same-URL reload, and retains the eight
hop limit and original 20-second deadline. Verified federation retains its
origin allowlist, SP/resource return checks and single-use return exception.
`form_submit` still falls back: Chrome does not identify who submitted the form.

The runtime now observes only the task tab's main-frame response header
`cf-mitigated: challenge` through non-blocking `webRequest`, using existing
permissions. Latest request IDs, the pre-request document, `getFrame` and
navigation generations prevent stale response/candidate reuse; no Cookie,
response body or sensitive query value is retained. Exact challenge DOM markers
and orchestrate scripts supplement the header; JSD and standalone Turnstile do
not establish a full-page challenge. No refresh or challenge interaction occurs.

Current top-frame `Zotero.PageSaving.translators` is read in the extension's
isolated world with `scripting.executeScript(target.documentIds)`. Background
translator caches and top-frame `instanceID = 0` cannot establish document
identity. Pinned `onPageLoad()` has no detection-start provenance; a challenged
document remains ineligible after same-document DOM changes or array replacement.
A new document must pass fresh checks, otherwise the task falls back at deadline.

`0002` additionally patches `src/browserExt/background.js` (declared in
`delta.lock`) only to target an automatic `translate` message at the selected
`documentId` and translator. The manual path and native `saveItems` implementation
are unchanged. Preparation navigation discards candidates and resumes under the
same deadline. After the first `saveWithTranslator()` invocation, navigation is
uncertain and cannot trigger another invocation. Native dispatch validates the
selected document before and after its bridge await; accepted-parent attribution
continues to be the completion authority, independently of PDF evidence.

Real Chrome 154.0.8037.98 API probes used temporary profiles and local test pages,
not the user's Chrome profile or Zotero data. They observed response headers
without a document ID, header-before-navigation ordering, distinct document IDs
for same-URL reload, isolated access to pinned PageSaving and stale-document
injection rejection. These probes establish API feasibility, not actual
Cloudflare passage, translator detection or Zotero parent/Paper completion.
See SPEC §42.16 for executable regression/build evidence and live gaps.
