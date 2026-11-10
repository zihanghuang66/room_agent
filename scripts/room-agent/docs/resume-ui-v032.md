# v0.3.2 — Repair resume and output presentation

Source: room_agent_stage3_setup_20261106T111902Z_65.tar.gz.
Run: 6b7f34a1e34145c787773155fb412463.

Confirmed: round 0 review completed; user requested repair; repair, round 1 build and geometry completed; round 1 evidence failed after Codex reconnect timeouts and WebSocket-to-HTTPS fallback. No second evidence invocation appears in the supplied server log. Setup exit=130 indicates interrupted service; exact timing relative to the user's browser Resume request is unrecorded. Browser “Failed to fetch” (if that was the displayed text) is a distinct browser-to-web-service failure, not proof of a graph/checkpoint failure.

Reproduction: real LangGraph/SQLite and real HTTP + persistent queue tests both resume round 1 evidence → render → review, without repeating repair/build/geometry. Model stages in these tests are controlled substitutes. No claim that actual Codex connectivity is repaired.

Presentation defects addressed: status used to wait for logs/events; network errors and action responses were only at the page top; stage flex widths varied and status/duration shared a wrapping line; all build rounds were flattened into identical filenames; mixed report/download link text did not indicate file type.

Changes limited to HTML/JS/CSS, tests, delivery metadata and docs. No execution-module edits: engine hash remains 5243482a5eee584c83d5ae1343fef41bea3bc2445b30224d00fa4d8006cbedf2. Keep old request/checkpoint integrity checks intact.

Browser fixture inspection: nine cards each 88px high, equal column widths within browser subpixel rounding; statuses use nowrap. Current round files visible; round 0 hidden under history. Intentional local server shutdown produced explicit connection-lost guidance and disabled run actions. Fixture data was visibly labeled as not a live task.
