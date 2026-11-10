# 4B-1 Conversation Implementation Plan

Approved scope: phase4b-plan.md and user confirmation on 2026-11-09.
Goal: collapsed conversation, text / optional browser dictation, Codex clarification and draft plans, no dispatch.
Architecture: UI extension owns transcripts. A small LangGraph captures bounded status context, calls a separate Codex planner, validates a structured answer. Existing reconstruction graphs and hashes remain unchanged. No scene writer or queue is exposed to the planner.

- [x] Verify actual Codex 0.164.0 tool surface with isolated local Responses fixture; assert zero tools and deny unsolicited exec/apply_patch calls. Fail closed for unsupported runtimes. Test subprocess timeout and malformed final output.
- [x] Add tests first for persistent version-isolated turns, request replay, concurrent submissions, interrupted/failed turn recovery, structured plans, target validation, and zero reconstruction task creation. Implement ui/planner_cli.py and ui/conversation.py; no source engine edits.
- [x] Extend authenticated HTTP handler with conversation GET/POST. POST validates JSON size/token/origin, target and expected revision. Store pending before call; no automatic model retry after failure/restart. GET is polling only.
- [x] Add collapsed Conversation with bounded scrollable history and bottom composer. Text is rendered with textContent. Optional speech recognition transcribes into composer only; show service/network limitations and require Send. Stop recognition on target changes. Keep drafts per version and guard stale async responses.
- [x] Run new tests, full suite and real-CLI local fixture. Bump version to 0.6.0, package source only with mtime=0; verify extracted package and engine parity. Document server checklist and limits. Do not push GitHub.

Release gate: this is planning only. No Confirm & run button. Real authenticated model quality, browser dictation and school network require user acceptance. Voice has OS dictation fallback and is not custom audio uploaded to the server.
