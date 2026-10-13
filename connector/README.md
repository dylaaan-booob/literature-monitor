# Literature Monitor Connector

This directory defines the independent browser component for the v0.6.8 release,
following the v0.6.3 batch/Connector contract in SPEC §42. It is derived from the official
Zotero Connector source and is separate from the MIT Python
`literature_monitor` package. The tracked build entry point generates the
Chrome/MV3 artifact with version `0.6.8`.

v0.6.8 is the latest published stable release. The current build
includes read-only Paper Enrichment, optional Shibboleth consent and lifecycle
cleanup on the same pinned upstream. The Python server accepts the 0.6.8 CARSI
handshake alongside 0.6.4–0.6.7. See SPEC §54 for validation and publication evidence.

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
delta, runs the upstream debug build with release version `0.6.8`, verifies
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
[Connector ZIP](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.6.8)
and select its `chrome-mv3/` directory; it includes corresponding source and
license/build instructions. This is a local install path, not a Chrome Web Store release.
A7 recorded a successful toolbar-free automatic save in normal Chrome with
Zotero Desktop and an existing institutional session; that scoped live acceptance
does not constitute a Chrome Web Store release. Historical v0.6.1 publication and asset
verification are recorded in [SPEC §40.16](../SPEC.md#4016-v061-release-and-documentation-closeout-2026-10-07).
See [SPEC §§40.14 and 40.15](../SPEC.md#4014-a7-implementation-and-validation-evidence-2026-10-07)
for live validation and preparation evidence, and
[AGENTS.md](../AGENTS.md#release-execution-and-verification-reuse) for release
execution and validation reuse.

The v0.6.2 release uses the same upstream pins, patches and runtime overlay
as v0.6.1; only its manifest release identity changes. Candidate construction
and checks remain in [SPEC §41.15](../SPEC.md#4115-a7-release-preparation-2026-10-08).
Publication, final ZIP digest and the permanent release source are recorded in
[SPEC §41.16](../SPEC.md#4116-v062-release-and-documentation-closeout-2026-10-08).

## Runtime test

From the repository root:

```bash
./connector/test.sh
```

This runs the deterministic Literature Monitor-specific Node harness against
the real overlay runtime with mocked browser, Zotero and localhost bridge
boundaries. It requires no browser profile, live Zotero instance or network.
The v0.6.5 A4 tests additionally exercise the observed CARSI list/detail
selectors and attributable target opening with isolated DOM/browser fixtures. They
cannot substitute for live institutional Chrome acceptance.

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

The v0.6.3 hook observes successful local `saveItems` acceptance
for the matching bibliographic item before collection lookup and attachment
work. It binds the request, DOI target, save invocation, task tab and native
session, consumes one live dispatch permission and rejects expired commands.
Native HTTP `201 Created` is accepted with an empty body, or with one echoed
matching client-side parent ID; errors, missing responses and mismatches do not
prove acceptance. Only attributable native acceptance yields parent `CONFIRMED`. Explicit
pre-dispatch failures are no-effect (`FAILED` in the legacy adapter); ambiguous
errors after dispatch are uncertain (`UNCONFIRMED`). In v0.6.3,
matching confirmed parent acceptance atomically marks the Paper `exported`.
Import attempts are process-local, with no new persisted `export_attempt`.
Historical frontmatter fields remain untouched; an explicit later Import can
retry an unconfirmed or failed `kept` Paper after duplicate inspection. Native
save error, translator failure, missing acceptance and local Paper-write error
have distinct transient result semantics. An active Connector task and one-shot
dispatch authorization still prevent concurrent saves and duplicate delivery.
The task continues the upstream Zotero collection and attachment pipeline
before terminal delivery. After completion, the runtime returns focus to the
original verified Literature Monitor tab. It closes only an unchanged
project-created task tab on confirmed success, and retains failed or uncertain
tabs for inspection; ongoing manual login is not interrupted.
The legacy per-Paper completion and library-reconciliation workflow is retired.
The application aggregates all eligible Kept Papers and issues these commands
serially. User-reported v0.6.3 live acceptance and its evidence limits are recorded in SPEC §42.19.

PDF is independently `unverified`: links, Snapshots, metadata and progress do not
prove an actual saved PDF. Inspect Zotero Desktop for actual attachments, and
inspect the retained task tab on failures. Parent completion waits for the
existing native attachment pipeline to settle (or a bounded timeout), but
never repeats the parent save. See `PROVENANCE.md` for
the exact pinned-source findings and remaining live gates.

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

## v0.6.5 CARSI Preparation verification boundary

`literature-monitor-carsi.js` starts with the official resource list at
`https://ds.carsi.edu.cn/resource/resource.php` in a task-associated normal,
non-incognito Chrome tab. It verifies the owning window, completed tab load,
active top-level official document and resource-list/search/filter controls.
An already operable resource view proceeds directly; configuring an Institution
IdP is still required for automated execution.

Only an associated CARSI-domain non-resource page can trigger the configured
Shibboleth entry, at most once per Service's Portal observation. The entry encodes
the saved `Institution.idp_entity_id` as `entityID` and `/wxds` as the target;
it never navigates directly to the IdP identifier. Users handle external
institution login, passwords, MFA and challenges themselves. An exact
`https://ds.carsi.edu.cn/wxds` return navigates the associated tab back to the
resource list; execution continues only after official-page verification.
Waiting is reported after that conditional login entry, not inferred from an
arbitrary external redirect. There is no human Continue/Resume control or API.
Unverifiable pages use the existing bounded wait, authority-revocation and
Needs attention paths; this is not authentication detection or generic recovery.
Login waiting and Service deadlines are separate from the ten-second Visit
opening observation (SPEC §44.5–44.6).

For each Service, the Executor enables/rechecks `onlyMySchool`, overwrites the
search input and clicks the official search button. The query uses the saved
name, or its trimmed suffix after the last `&` when present; the full saved name
remains the exact selection identity. It observes result-list snapshots for at
most 15 seconds and requires a changed, subsequently stable list. Only one card
whose `.flex .t1` direct text matches the full name can be selected; nested badges
do not form part of that name. It does not paginate the list. Duplicate matches,
unchanged/empty results or unverified controls require manual attention rather
than an inferred resource-not-found result. The selected link must be an official
`resourceDetail.php` link, the new Detail tab must be attributed to the source
Portal, and its unique official `访问资源` control must be verified before clicking.

The normal-Chrome runtime uses independent Preparation claim/authorize/result
endpoints and browser arbitration. Opened requires a confirmed official Visit
click, attributable target opening within ten seconds and a single-use server
nonce; it does not check final external navigation or assert authentication or
entitlement. Accepted Results advance immediately without Preparation Settled.

**Preparation never automatically closes any tab or window.** Portal, resource
Detail, database Visit and institution-login pages stay open after every outcome,
Cancel, timeout and Service transition, and users close them manually. The same
Portal serves subsequent Services in one task; a new Prepare creates its own
Portal context without clearing earlier pages. There is no delayed tab cleanup,
URL/title-based closure rule or browser-close wait. Local cleanup only releases
listeners/timers and Executor references; cancellation revokes future operations
and retains the existing ten-second recovery isolation. Zotero Import tab cleanup
and native Parent Save settlement remain independent and unchanged.
The page selectors came from user-inspected Chrome DevTools evidence. Current
automated evidence is recorded in SPEC §44.10. The user reports that manual
real-browser acceptance has all passed; **Codex has not independently verified
that live acceptance**. v0.6.5 is **released**, with final audit/build checks in
SPEC §44.11 and verified tag/downloaded asset digests in §44.12. The ZIP includes
`chrome-mv3/` and `corresponding-source/` with pinned upstream source, the reviewed
delta, licenses and build instructions.

## Historical v0.6.3 DOI-based preparation design (not enabled)

This earlier design concerned an owned DOI tab's actual landing origin, not the
current CARSI Portal Preparation queue. No real federation route was verified
or enabled by it. Unknown Services used ordinary bibliographic capture and
Open DOI for manual access. Publisher metadata and login redirects were not authority to
identify a Service or prove access; IdP session, SP session, authentication and
full-text entitlement remained separate unknown observations. The design
required a challenge or failed/expired preparation to stop its task, with no
late save callback allowed to resume it.

Its proposed future rules had to preserve existing OpenAthens host associations
and satisfy origin/return allowlists and finite preparation limits. Deterministic fixtures
were not institution login evidence. See the historical
[SPEC §42.12](../SPEC.md#4212-a6-access-service-preparation-implementation-evidence)
and [PROVENANCE.md](PROVENANCE.md). Current CARSI operation is documented above
and in §44; this historical design is not an enabled access feature.

## Paper Enrichment

The development Connector advertises `paper-enrichment/v2`. Paper Detail offers
Enrich when Abstract, publication date or Authors are missing, without Zotero Desktop.
Preview shows only valid missing fields; Save fills them together, including
partial results (SPEC §§48, 50). Rebuild and reload this Connector to enable the
capability; older Connectors must be updated.

The result also forwards the original `creators` array. The server selects
author roles in order, handles personal/single-field names, and fills an empty
local author list only after explicit Save. It creates or reuses matching
Paper-local Author notes without guessing stable IDs or merging by name. If
Paper saving fails after Author creation, the created files remain and their
paths are reported. Preview and Cancel create no files.

The translator must run in the browser and return one literature Item matching
the Paper DOI. Original `abstractNote` and `date` are transported with date parts
from the pinned `Zotero.Date.strToDate()` in MV3 offscreen. Invalid, ambiguous or
partially unparsed dates yield no date candidate; a valid Abstract remains
usable. Only the server fills genuinely missing month/day with `01`, then
validates the calendar date. These compatibility values do not confirm the
actual publication day; sorting uses the completed date. No print/online choice
or Abstract quality repair is performed.

No Zotero item/attachment save, source history or persistent enrichment state is
created. After the server confirms Preview, the completed task tab closes only
if its document/window identity still matches. Cancellation, failure and user
navigation retain the page. No Abstract length limit is imposed.


## Optional Shibboleth attribute consent

SPEC §53 adds a default-off Institution preference and the optional
`shibboleth-consent/v1` heartbeat capability. Older Connectors continue ordinary
Preparation with manual consent and an update notice when this preference is on.
Only recognized same-origin consent forms in an active task's Portal login wait
are operated. Native global-consent/proceed clicks do not establish login success
or access rights, and never close tabs. Existing Manifest permissions are reused.

For this development change, the successful build replaces the existing Chrome-
loaded `connector/build/chrome-mv3` directory in place. Reload that extension in
Chrome; source VM tests are not real Chrome/IdP verification. Manual verification
belongs to the user. No version bump or publication is included.
