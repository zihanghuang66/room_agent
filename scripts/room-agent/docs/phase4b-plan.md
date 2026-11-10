# Stage 4B — conversation, explicit plans, confirmed dispatch

Approved 2026-11-09. 4B-1 implemented in v0.6.0; school-server acceptance pending (see phase4b1-status.md). 4B-2 / 4B-3 remain planned.

## Objective and existing components

Users discuss a selected room/version, inspect an explicit operation plan, and confirm that exact plan before an expensive operation is dispatched. Preserve existing tasks, source versions, checkpoints, independent edit acceptance and whole-room quality. Use the existing Service / TaskQueue / edit and scan graphs; do not replace the reconstruction backend or add a second execution state machine.

Source inspection confirms `Service.create_edit`, `create_full`, `create_review`, `execute` and the persistent serial `TaskQueue`. Saved-scene review currently expects a legacy source-run path, so reviewing an arbitrary selected child version needs an explicit bridge; it cannot be assumed to work by passing a child ID to that method.

The existing Codex provider is an execution adapter. Its `read_only` option does not establish tool isolation: the provider still launches an execution-capable CLI. A planning adapter must prove that command/file-edit/reconstruction capabilities are unavailable or denied before action. Prompt text alone is insufficient. Verify the installed CLI/protocol's capability controls first; if unavailable, report the limitation and retain a disabled planning/dispatch gate rather than shipping a pretend read-only mode. Existing execution authentication and DNS remain unchanged.

## Boundaries

- Conversation: saved turns and selected room/version context.
- Planner: bounded context from verified metadata, status and findings; returns structured intent, questions or a plan. It does not call execution APIs or edit scene files.
- Plan validator: supported operation allowlist, explicit target resolution, required parameters, source identity, current task eligibility and unresolved questions. Unknown operations and ambiguous directions/objects cannot become executable plans.
- Confirmation/dispatch: accepts a specific plan revision/hash. Bind a stable dispatch/task identifier durably so double-click, HTTP retry, disconnect or restart cannot create a second task. Revalidate source/state before execution. A stale plan needs revision and renewed confirmation.
- Existing execution graphs: sole owner of scene-stage execution and recovery. Conversation shows linked task events/results, not a competing pipeline state.

Read-only questions do not require confirmation. New plans, substantive plan revisions and renewed model/GPU execution after interruption require explicit action. Clarification never silently creates a run. No unbounded autonomous repair. Completion, edit acceptance and scene quality remain separate.

## Delivery 4B-1 — conversation and plans, no dispatch

Scope: a minimal conversation entry associated with the selected version; factual status questions, clarification and a proposed scene-edit plan. Chinese input/replies are supported; interface labels remain English. Current selection is shown explicitly, and cross-room context is isolated. A plan card presents Target version, Change, Preserve, Steps, Expected outputs and resource use. Time estimates, if shown, are ranges backed by samples or labelled unknown.

Planner boundary tests are a prerequisite. Read-only planning may call Codex and consume model time but never starts Blender, creates an execution task, writes a scene or enters the execution queue. Persist messages/plans in the existing persistent state area without changing old task inputs.

Server acceptance:
1. Ask what happened in the latest edit: answer from actual recorded status, not a fabricated score or current running state.
2. Ask to change a named material: produce a plan, with no new execution task.
3. Ask to move “that cabinet to the right”: ask for object/direction if not resolved by actual scene context.
4. Switch room/version: targets must remain explicit; no accidental use of the prior room.
5. Refresh/restart: conversation and latest plan survive; no model execution is auto-started.
6. Inject an instruction to run a command through chat or log content: planning has no command/file-edit capability. Invalid model output or network failure is an error, not an executable plan.

## Delivery 4B-2 — confirm and execute one edit

Scope: Confirm & run / Revise / Discard. Only scene_edit dispatch initially. On confirmation validate the plan revision, target source hashes and task eligibility, then use existing create_edit and queue semantics to produce one child task. Plan-to-task binding survives each publication boundary, including task creation before queue insertion. Two same-source plans remain explicit sibling branches; never silently rebase onto the newest child.

The planner does not implement exact-once model execution. Dispatch idempotency covers duplicate user/network submissions; existing durable stage receipts control node reuse, with external in-flight side effects retaining their documented recovery limits.

Server acceptance:
1. Plan creation/revision does not execute; one confirmation creates exactly one child.
2. Double-click, replay a request and reconnect: no duplicate child or queue entry.
3. Old plan revision, modified source, mismatched selected version or invalid action: reject before execution and explain the required correction.
4. Existing reconstruction is not rerun; parent scene stays preserved.
5. Progress, Before/After, edit acceptance, whole-room quality and downloads appear in the existing workspace.
6. Inject interruptions around confirmation/task creation/enqueue in isolated tests; restart recovers the same dispatch identity.

## Delivery 4B-3 — broader workflow routing and feedback

Scope: scan reconstruction from explicit discovered archives, supported saved-scene review, incomplete-task resume, and repair/finish only where current allowed_actions permits them. Build and test the selected-child-to-review bridge before exposing that operation. “Finish” must not be mapped to killing a running task; cancellation remains limited to queued entries unless separately designed.

Show a concise reason when the requested action is unavailable. Queue submissions remain serial; no GPU scheduling or parallel inference is introduced. Completion feedback is evidence-backed and links to the existing results/reports. Conversations may revise a plan through multiple turns, but each new execution plan requires confirmation.

Server acceptance: correct operation routing, no duplicate reconstruction, allowed-action enforcement, clear queued/running/interrupted/completed states, no automatic repair after failed quality, preserved completed stages after resume, and persistent conversations/tasks across restart.

## Packaging and stage gates

Each delivery gets a distinct archive name, launcher, complete extraction/run commands, local test report and short server acceptance checklist. Scripts remain under scripts; returned logs and compact diagnostics under return_output. Foreground launch; no automatic tmux/background. Upgrade only when the service is idle. No GitHub push without permission.

Development passes local tests first, then user verifies one package on the school server. A phase is not marked server-accepted from local mocks. Inspect returned diagnostics and fix that phase before enabling the next phase's new execution surface. Preserve existing tasks and avoid scene reruns for UI/plan-only acceptance.

First milestone is 4B-1, delivered for server acceptance. Conversational execution remains unimplemented. Its version-specific no-tool runtime contract is documented separately; no guarantee is made that future scene edits will pass quality.
