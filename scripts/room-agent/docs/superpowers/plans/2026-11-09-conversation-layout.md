# Conversation-first layout v0.6.1

Approved: user accepted the three-column Spatial Agent mockup and requested implementation.

Goal: default-open central conversation with bottom composer, independent compact Workspace and Summary panels, existing task controls preserved.

- [x] Add DOM/behavior regression contracts for panel structure, independent toggles, preferences, current plan only, plan invalidation on clarification/error, task-switch isolation, action visibility and disclosure defaults. Update superseded v0.6.0 collapsed-conversation expectations.
- [x] Restructure dashboard HTML: stable global toggles, central Conversation, right-side accepted Snapshot / Draft plan / Steps / Review findings / Files / More details. Move controls, do not duplicate IDs or change execution endpoints. Keep a central status/action shortcut when Summary is hidden.
- [x] Implement responsive fixed-viewport grid, independent panel/history scrolling and bottom composer. Compact task list without UUID. All substantive content must remain reachable on smaller screens/zoom; no absolute-positioned download menus.
- [x] Project latest conversation plan into Summary only. Old plan links may open historical plans explicitly; a later message makes the old draft non-current. Opening panels, switching tasks and refreshing never submits work. Keep chat input, request idempotency and voice semantics intact.
- [x] Test full suite and extracted package, execution and conversation backend parity with v0.6.0, shell/manifest verification. Build source-only mtime-zero package; preserve existing state root and tasks. No GitHub push.

No conversational dispatch added. School browser visual acceptance remains necessary. Do not bypass the prior browser-tool URL policy block to obtain screenshots.

Verification: extracted release full suite 347 passed (31.52s); packaged verify_stage4b.sh 47 passed (2.82s), real installed Codex local boundary check passed. Browser visual acceptance remains pending.
