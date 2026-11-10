# Conversation review and resume implementation plan

**Goal:** Confirm a review of the retained scene, or resume an interrupted operation, in the existing conversation. Preserve reconstruction and edit checkpoints and never automatically repair.
**Architecture:** Extend the shared planner schema with explicit operation and operation_run_id. SessionEdits remains the durable dispatch ledger; saved_scene_review(max_batches=0) uses selected_source rather than source_info. Resume uses the existing run and graph, with persisted queue submission identities. Reports are associated with the reviewed source; review never promotes an edited scene.
**Tech stack:** Python, LangGraph/SQLite, existing TaskQueue, vanilla browser UI.

## 1. Contract and availability
- [x] Add tests for scene_review/resume_operation, blocked/stale/incorrect targets, legacy edit schema compatibility.
- [x] Extend planner_contract and validation; expose explicit available actions and exact resumable run in context. Resume plans may be made for unresolved operations; only explicit UI confirmation submits.

## 2. Review bridge
- [x] Test real saved-scene graph under simulated external work: reviewed child source, zero repair, no ingest/reconstruct, same root Workspace, preserved source pointer, fresh verdict projection.
- [x] Build request from selected_source, workflow saved-scene-review/1, max_batches=0; reserve/publish/enqueue using existing session ledger. Completed review is resolved regardless of quality. Show report without treating it as an accepted edit.

## 3. Resume dispatch
- [x] Test retry after queue acceptance and lost response, queue/server restart, fast re-failure, changed run identity and no repeated completed nodes.
- [x] Add optional TaskQueue submission_id and durable remembered submission IDs. Session confirmation reserves command before enqueue and reuses identity after restart. Resume exact unresolved child or interrupted root, never create a new reconstruction.

## 4. UI and integration
- [x] Test confirmation availability for resume, clear review/resume plan labels, progress/report completion and unchanged scene downloads; existing edit interactions continue.
- [x] Update session UI, report projection and diagnosis as needed without adding new sidebar tasks or automatic model calls.

## 5. Delivery
- [x] Full extracted-package regression including HTTP and actual graph checkpoint tests; inspect changed engine files and explicitly allow prior hashes only after compatibility tests.
- [x] Package v0.10.0 source only, zero tar timestamps, checksum/manifest; document server commands and stepwise acceptance including no artificial interruption of valuable jobs.

User approved implementation. Work remains in the current workspace; no GitHub push. Tests use simulated costly rendering/model calls, server acceptance remains separate.

## Local completion

Final extracted package: 465 tests passed in 52.71s. Actual Codex 0.164.0 local fake-provider probe verified zero tools, image input and rejection of unsolicited commands/patch. Independent review findings were addressed with source/capture freshness checks, baseline provenance, retained-scene report projection after discard and successful-review image projection. Real school GPU/model acceptance remains pending.
