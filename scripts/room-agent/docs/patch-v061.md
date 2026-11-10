# v0.6.1 — conversation-first workspace

Implements the approved three-column mockup. This is a presentation change; execution and conversation Python, source versions, state root, engine identities and queue semantics remain unchanged.

- Always-open central Conversation, independently scrolling history and bottom composer. Header provides permanent Workspace / Summary toggles, remembered separately.
- Compact recent task list; status/timestamps retained, run UUID omitted from list labels. Active context remains the selected task, not a recent conversation from another room.
- Summary contains accepted Snapshot, latest draft, task actions, collapsed Steps / Review findings / Files / More details. Files are inline, never an absolute popup clipped by the panel.
- Summary-hidden task status and action shortcut remain visible centrally. Controls are the original controls, moved rather than recreated. Failed/running/decision state rules are unchanged.
- Draft details shown once. History has a summary link. Later messages invalidate the displayed current draft, including failed/pending planning. Explicit historical views are labelled non-current. Late events cannot present an old-room plan.
- Snapshot Expand opens the original accepted comparison gallery in a larger dialog; it never promotes candidate images. Switching task closes it, avoiding stale target/image links.
- Default responsive breakpoints: Summary overlays below 1100px; Workspace overlays below 760px. Small-screen initial state is collapsed unless the user has saved a choice. Header controls remain available. Opening a panel on a phone closes the other overlay for that viewport.

Tests cover static DOM ancestry/unique IDs, independent toggles/preferences, plan freshness, history behavior, state/action visibility, snapshot/candidate separation, polling without model calls, task switch and original regression contracts. Actual school browser / microphone and layout at user's screen/zoom still need user acceptance; no live-browser screenshot is claimed.

No GitHub push. No reconstruction or review rerun needed for this UI upgrade. Stop the old server only when tasks and conversation calls are idle. Use start_server.sh from the new version, existing persistent state remains in agent_state_stage2_v020.
