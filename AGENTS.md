# Literature Monitor — Repository Development Rules

These rules apply to the repository and its descendants. Read [SPEC](SPEC.md) for current behavior and
[README](README.md) for user operations; neither release history nor old staging notes override present code and
tests. Check more specific `AGENTS.md` / `AGENTS.override.md` before editing a subdirectory.

## Architecture and source authority

- Local-first FastAPI/Web workflow: `src/literature_monitor/application/` owns use cases; `src/literature_monitor/web/` owns guarded API and transient coordinators; `web/static/ui_next/` owns the five-page UI. `cli.py` launches GUI only.
- `list.md` is the portable durable source of Journals, direct Publishers and Access Services. `monitor.yaml` owns Monitor, months, Institution and Kept workflow configuration. Paper/Author Markdown owns Workspace state. Provider cache, last-run diagnostics and Web/Connector task state are reconstructible or process-local.
- The current frontend is served at `/`; `/ui-next` is an alias. Do not restore the retired Jinja/HTMX UI or independent Preparation entry. The supported screens are Inbox, Kept, Journals, Access and Settings.
- Respect the Python MIT boundary and independently pinned AGPLv3 Connector source, notices, corresponding-source obligations and declared delta; see [Connector provenance](connector/PROVENANCE.md). Do not vendor Connector-derived source into the Python wheel or import graph.

## Domain and persistence invariants

- Journal identity is a **user/configuration-owned checksum-valid ISSN-L**. OpenAlex Source IDs/aliases and Crossref ISSNs are Provider evidence, not authority to silently replace it. Names, titles and Publisher domains never establish Journal identity. Do not invent an alias registry or mandatory external identity Provider.
- One normalized **DOI** identifies one canonical Paper. A separate Workspace UUID guards durable references and updates. Do not merge distinct DOIs or introduce durable manifestation/version trees. Preserve valid historical Provider provenance without reviving retired Provider retrieval.
- Run is **capture-only**: it may create new DOI Papers and compatible missing Author notes, but must never rewrite an existing Paper or Author byte. Saved DOI identities are skipped before metadata work; conflicts fail closed. Missing Authors/Abstract/dates are valid when Title/Journal/DOI exist. Date precision remains `YYYY`, `YYYY-MM` or `YYYY-MM-DD` without fabricated components. Early matching uses trusted OpenAlex Title/Abstract, with Crossref on demand and isolated Provider failures.
- Human-managed Markdown, `status`, Notes, custom fields, URLs, Group assignments and unknown sections must survive ordinary operations. `candidate → kept/rejected` and `kept → exported` require independent guarded writes. Enrich is explicit, read-only Connector translation followed by an eligible narrow Paper write; no Preview/Save step and no Zotero item saving. Existing Authors are never rewritten.
- Settings uses one shared draft with base revisions. Ordinary Save modifies only changed files, preserves non-managed list sections, enforces identity/mapping validation and safe CAS/atomic writes; whole-file Markdown Import instead replaces all `list.md` content after Preview/Confirm and draft guards. Never silently migrate legacy schema or repair damaged data. Do not infer a Service from Publisher name or URL.
- Protect the real `monitor.yaml`, `list.md`, `workspace/`, `src/.obsidian/`, `journal-assets/`, Zotero Library and other user files. Tests use isolated fixtures. Workspace Reset is separately confirmed permanent deletion of the *entire* configured Workspace, with strict root/path guards; it does not start Run or remove external config, covers or Zotero data.

## Browser, CARSI and native Import

- Kept offers **PREPARE & IMPORT** and **IMPORT ONLY**, with one active serial Connector native save. Preparation uses related CARSI Services for *all* current Kept Papers; Manual Services provide URLs only. Access defaults are not DOI landing-SP, session, entitlement or PDF evidence. Browser tasks and single-use save grants have independent authority.
- CARSI uses verified official Shibboleth/Portal/Detail/Visit pages and exact saved Service names; `onlyMySchool` must be unchecked and verified. A normal per-Service timeout advances the queue; login waiting, Cancel or loss of authority terminate it. **Never auto-close Preparation pages** or handle school credentials/MFA. Optional same-origin Global Consent is explicit and default-off.
- Native Import may set `exported` only on attributable upstream parent acceptance followed by safe Paper state persistence. Parent and PDF outcomes are distinct; unverified PDF is never success. A stopped Import prevents new dispatch while escaped saves settle. Unknown outcomes remain `kept` and require user inspection before retry, not blind repeated saves. No custom Zotero ingestion, library mirror, collection routing, PDF acquisition or automatic authentication.
- Connector requests bind current task/request/document identities, one-shot grants and deadlines. Enrichment, Preparation, Import and cover search share browser exclusion. Browser window context is transient and cannot grant save or navigation authority. Preserve user-navigated tabs and never infer success from a redirect, animation or generic callback.

## Verification, scope and release

- Prefer the smallest coherent change with current implementation/tests as evidence. Validate relevant observable success and failure paths, file-content preservation, concurrent/stale revisions and security guards; do not substitute mocked browser acceptance for real Chrome/CARSI/Zotero verification.
- Check actual Git status, existing dirty diff, applicable rules and final diff. Keep unrelated user edits; do not reset, clean, silently stage or modify protected data. Tests only count as passed when actually executed; reuse applicable evidence with its precise scope.
- Commit, push, tag, release, deploy and remote changes require explicit user authorization. A request limited to docs, review or preparation does **not** authorize publication. After explicit `请提交并发布`, follow the reviewed target/version and applicable release procedure; never force-push, move published tags or overwrite Releases. Documentation-only changes require affected documentation checks, not automatic Python full-suite, Connector build or install validation.
- Do not add conference monitoring, global keyword discovery, ranking/LLM summarization, scheduler, independent workflow database, Obsidian plugin, publisher scraping or auth/session automation without a new product decision.
