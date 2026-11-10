# Spatial Agent v0.5.4 — edit acceptance

The returned floor edit completed successfully but the 15 whole-room review groups rejected inherited reconstruction defects. All groups verified the requested change; this evidence was lost from the top-level status. Successful-stage time was 6,659.87 seconds, including 5,038.21 seconds of review. These are observations from run 94d9c3971cd44577998f3170b42b6ef0, not a performance forecast.

New requests pin `impact-review/1`. One independent session reviews the actual file delta, compiled baseline/child scenes, and before/after views. Every changed file must be accounted for, affected object IDs and valid before/after citations are required, the requested change and absence of unintended changes must both be supported, the score must be at least 8, and there must be no edit or geometry issues. Unknown impact, no saved change, hidden affected objects, invented citations, low scores, collisions and out-of-scope changes cannot be auto-approved. Execution timeouts still raise an execution error and retain the saved edit.

This is an impact review, not selective whole-room recertification. Untouched historical findings are retained without another model review; negative baseline quality remains negative, and other baseline states do not become fresh whole-room passes. The displayed edit score is explicitly separate from the historical scene score. A reviewer is still a model, so the actual quality and timing require server validation.

`edit_status`, `edit_score`, `edit_issues`, `change_verified`, `change_evidence`, `scope_evidence`, `affected_objects`, `whole_room_review` and `baseline_quality` persist in new stage results and summary projections. Source/build, baseline, capture and image bindings plus a hashed review receipt support reuse of a completed review. An interrupted session itself may be retried; completed edits are never replayed.

Old requests keep their original grouped review policy and graph topology. Old completed results are projected read-only from hashed group receipts bound to the instruction and build. They can show `Change verified · legacy review`, but cannot be upgraded to the stronger `Edit passed` contract. Parent inputs, graph checkpoints and artifacts are not migrated or rewritten.

Accepted edit-stage snapshots and exports appear in the main Snapshot only after edit acceptance and technical export verification. Whole-room status remains visible in Review findings. Unapproved and legacy-only candidates retain the existing More details placement. There is no automatic full-room repair or silent quality approval.

Validation includes actual returned legacy evidence (15 groups), new review gates, tampered evidence, baseline identity mismatch, timeout behavior, completed-review reuse, old task projection without node execution/checkpoint mutation, UI separation, immutable parent versions and the existing full suite. Local external sessions are test doubles: no real Codex/GPU/Blender execution is claimed.

Release commands are in the bundled 使用说明.md. Server state path, credentials and DNS are unchanged. No GitHub push.
