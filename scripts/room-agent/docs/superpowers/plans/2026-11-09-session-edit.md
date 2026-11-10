# Session edit dispatch implementation plan

Approved goal: one persistent Workspace task/conversation; confirmed edits use internal immutable runs and existing LangGraph/checkpoints, never new sidebar rows or reconstruction.

Architecture: ui/session_edits.py owns a durable per-session operation ledger and explicit confirmation/actions. ui/conversation.py supplies bound scene/status context; ui/server.py projects session state, current accepted presentation and internal operation progress. Existing execution Python remains unchanged. No auto-confirm from chat text. Legacy tasks become sessions lazily, with their original ID and history intact.

- [x] Backend contracts: latest plan/revision binding; same-plan replay; changed facts rejection; reserved internal run recovery; one unresolved operation/session; passed edit + technical export promotion; failed/unapproved leaves current result intact; explicit resume reuses ID; discard retains diagnostics.
- [x] Implement SessionEdits with locks and atomic JSON; internal requests include session_id, deterministic run ID and confirmed plan digest. Retain exact confirmed constraints in instruction. Ignore model-supplied execution/queue fields.
- [x] HTTP contracts: CSRF/Origin guard; internal runs hidden from Workspace/versions; root status reflects operation; source files keep explicit run IDs; existing non-session routes preserved; actions bind operation ID. Diagnostic tar includes conversation plus latest operation diagnostic tar.
- [x] UI: confirmation only on latest draft; selected root never changes; inline operation status, Resume/Keep current controls; accepted Snapshot remains current during work; candidate in More details; original Edit shortcut focuses conversation. Enter behavior unchanged. History below Summary.
- [x] Regression: real graph with fake expensive adapters, failure/resume checkpoint reuse, restart/replay/concurrent confirm, plan changes and target isolation, UI action routing, HTTP guards. Build v0.7.0 deterministic package, extracted regression + manifest/shell checks, detailed server acceptance commands. No GitHub push; no live reconstruction during local tests.

First increment deliberately requires a click on Confirm & run. Text such as “go ahead” alone cannot dispatch. Completed unapproved attempts can be retained and closed with Keep current result; interrupted execution resumes the same checkpoint. Planning remains available during execution, but confirmation is blocked until the previous operation is resolved; plans based on changing state must be refreshed.

Source full regression: 373 passed in 34.08s. All 29 execution Python files byte-identical to v0.6.3. Extracted full suite: 373 passed in 32.94s. Packaged verify_stage4b.sh: 73 passed in 6.31s; actual Codex local boundary probe passed. Shell syntax, manifest and zero tar timestamps passed. School-server real edit acceptance remains pending.
