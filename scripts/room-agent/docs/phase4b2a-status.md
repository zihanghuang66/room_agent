# 4B-2A — confirmed editing in one session, v0.7.0

User approved one Workspace task and conversation for repeated edits. v0.6.2 seven-step planning checks and v0.6.3 input polish were reported accepted by the user. This increment adds explicit confirmation and session dispatch; school-server end-to-end acceptance remains pending.

## Design and persistence

ui/session_edits.py manages conversation/session.json, reserved deterministic internal run IDs, confirmed source/plan bindings, operation receipts, current accepted source pointer, and status/presentation projection. The immutable per-operation request includes session_id and confirmed_plan_id. Existing execution Python and engine hashes are unchanged; no legacy inputs or checkpoints are migrated.

Confirm checks the latest persisted turn/revision, plan identity, session, structured status binding, and fresh source/capture hashes. Confirmation and planning share the conversation lock; operation creation/promotion/actions use the session lock; preparing an immutable request uses the room lock. A durable reservation precedes publication/enqueue. Replay uses the reserved request and ID; if enqueue succeeded but receipt was lost it adopts the existing queued/running/completed record rather than replaying work. Execution failures resume only by explicit action. At most one unresolved edit per session.

Promotion requires completed + edit_status passed + technical export verified + worker finalization finished. An unapproved operation never replaces the current pointer. Accepted original output and unapproved candidate links explicitly carry their own artifact run IDs. Discard retains the candidate and returns planning to the current accepted source.

## Presentation

Workspace filters only new requests carrying session_id, preserving old visible tasks. Root status projects the latest operation while run_id/Conversation stay stable. Old independent edit creation is blocked in the web adapter; Edit this version focuses Conversation. Confirmation button binds the latest plan. Progress and explicit Resume edit / Keep current result / Cancel queued edit are inside the conversation. History is under Summary. Session diagnostics contain root conversation plus latest internal operation evidence.

## Verification scope

Tests use actual Service, LangGraph and filesystem/checkpoint code with simulated costly model/render adapters. They cover stale source/plan, concurrent locks, duplicate confirmation, reserve/publish/enqueue interruption, explicit resume without repeating edit, unapproved/pending-finalization no-promotion, accepted chaining, stable Workspace/root, CSRF, artifact ownership, preserved originals, and no ingest/reconstruct. Browser appearance, school Codex/Blender/GPU runtime and actual edit quality still need user acceptance. No reconstruction was launched locally and no GitHub push.

## Deliberate first-increment limits

A message alone never confirms. Failed execution can resume its checkpoint; completed but unapproved edits are retained and can be closed with Keep current result, then a new plan starts from the last accepted source. Editing an unapproved candidate as a new baseline, automatic repair loops, rollback selection, new-scan conversational routing, and multi-room scheduling are not part of this increment. Current session history retains all internal files; storage cleanup/retention policy remains a later concern. Raw object descriptions are not verified until execution.

## Local release verification

373 extracted-package tests passed; 73 packaged targeted checks passed. Installed Codex 0.164.0 local simulated-response boundary probe passed. All 29 execution Python files match v0.6.3 byte-for-byte. Server execution and browser visual acceptance pending.
