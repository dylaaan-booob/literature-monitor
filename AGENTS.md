# Project Instructions — Literature Monitoring Workflow

## Applicability and instruction sources

- This file applies only to this repository and its descendants.
- Global `AGENTS.md` instructions still govern general communication, autonomy, safety, and engineering practice. This file supplements them with project-specific constraints; it does not replace or restate them.
- `SPEC.md` is the source of truth for product behavior, MVP scope, acceptance criteria, and the boundary between system-managed and human-managed data. v0.5.3 is RELEASED and remains the latest released/completed baseline, with package/Provider identities `0.5.3`; its reviewed implementation and release evidence remain historical facts. v0.6.0 is the current development target, and §39 is the current development authority. §37 remains authoritative for unaffected DOI-first identity, Provider behavior, current Paper schema, single-manifestation and Zotero parent identity. §§35–38 are historical released contracts: preserve their release/live/verification records, especially §§24, 36.14, 37.10 and 38.13, without rewriting them to match v0.6.0. Conflicting historical Browser Companion, custom PDF acquisition, staging, institutional credential orchestration and Zotero write requirements do not constrain v0.6.0. If code, documentation, plans, or assumptions conflict with `SPEC.md`, follow `SPEC.md` unless the user explicitly changes the specification.
- Do not duplicate the full specification in code comments or secondary planning documents. Reference the relevant `SPEC.md` section instead.

## Scope and product constraints

- Implement the MVP one bounded task at a time and preserve the end-to-end behavior defined in `SPEC.md`.
- `list.md` is the source of truth for the supplied venue whitelist. For the MVP, only its Journals section is in scope; the Conferences section must not be implemented or treated as discovery input.
- Keep discovery/retrieval journal-whitelist-first, with ISSN/EISSN as the preferred venue identity. OpenAlex is the primary discovery provider; Crossref is the secondary discovery and bibliographic-evidence provider. Semantic Scholar is not part of the supported production retrieval pipeline. Global keyword-first discovery and publisher scraping remain excluded.
- Isolate provider failures so successful evidence from other providers, journals, or requests remains usable. Consolidate available provider evidence before local keyword filtering decides inclusion.
- Use normalized DOI as the only supported canonical work identity under `SPEC.md` §37. Provider records contribute metadata/provenance to one Paper per DOI; do not merge different DOIs or maintain persistent manifestation/version state. Preserve workspace-local UUIDs for durable Paper references and never invent missing metadata.
- Treat Paper Markdown as durable user-facing workflow state. Preserve human-controlled status, unknown frontmatter fields, notes, and other human-authored sections during reruns.
- Keep disposable caches and indexes reconstructible. Do not introduce a mandatory second source of truth for workflow state.
- Do not add features outside the current `SPEC.md` scope, including conference monitoring, publisher scraping, global keyword-first discovery, LLM ranking or summarization, an Obsidian plugin, or custom bibliographic Zotero ingestion. For v0.6.0, follow §39's narrowed downstream workflow: the user opens the DOI and operates the official Zotero Connector manually; Literature Monitor performs only read-only exact-DOI reconciliation against Zotero Desktop My Library and records the verified parent link. Publisher access is a read-only projection from saved Journals for opening real journal/platform sites. Browser Companion, custom PDF acquisition/staging, Zotero writes, institutional credential/session orchestration and publisher-specific runtime adapters are not v0.6.0 production responsibilities. Automatic Connector triggering, Connector forks/bridges, completion signaling and collection routing remain future v0.6.x work unless a later contract explicitly authorizes them.
- Do not freeze programming language, package layout, CLI names, cache format, export format, or other non-blocking implementation details before the task that requires that decision.

## Engineering constraints

- Prefer the smallest clear implementation that satisfies the current `SPEC.md` acceptance criteria. Add dependencies, abstractions, retries, persistence, or compatibility layers only when a current requirement or observed failure justifies them.
- Follow §37's disposable-data boundary for older Paper and Provider-state schemas; do not add Paper schema migration/legacy repair or row-preserving Provider-state v1/v2 migration.
- Make metadata updates idempotent and isolate partial source failures so successful records remain usable.
- Keep machine-managed writes narrowly targeted and explicitly test preservation of user-managed Markdown content whenever that behavior is touched.
- Respect upstream licenses and notices. Do not copy code whose license is absent or incompatible with the repository's chosen licensing obligations.
- Do not perform unrelated refactors, opportunistic cleanup, or unrequested scope expansion. Refactor only when necessary for the current change, and keep it within that change's boundary.

## Verification and completion

- For behavior changes, add or update tests that exercise observable results and the relevant `SPEC.md` acceptance criteria. Use network-independent tests where practical; do not substitute mocks for the required end-to-end validation when the specification explicitly requires live behavior.
- Before declaring work complete, inspect the actual implementation, the complete repository diff, and the relevant test command output. Confirm that the diff stays within the requested task and that no human-managed state or MVP boundary was violated.
- Report checks that actually ran and any remaining unverified behavior. A written plan, created file, passing syntax check, or successful isolated unit test is not evidence that the full workflow works.
