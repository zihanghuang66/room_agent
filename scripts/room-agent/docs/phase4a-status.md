# Stage 4A — explicit scene revisions (v0.5.0)

Implemented as a new `scene-edit/1` graph. Existing full-scan and saved-review graphs, task directories, requests, review groups and pinned backend remain unchanged. No migration or execution of old tasks occurs when starting the server.

## Delivered behavior

1. Choose an explicit completed scene version, including a completed 3A full scan with verified exports. A `Completed with issues` source is selectable and retains that quality status.
2. Enter one concrete instruction. The server creates a new run ID, binds parent/room/source/seed/capture/model identities and saves a separate snapshot. The parent is not reopened or edited.
3. Graph nodes: Snapshot → Environment → Before build → Evidence → Before render → Apply change → Build → Geometry → Render → Review → Complete.
4. Codex performs one source-edit session, bounded to 900 seconds and 45 tools. A durable marker is recorded before invocation. Interrupted editing is not invoked again by Resume; retained source proceeds to validation. Invalid saved code can still fail Build; Resume alone cannot repair invalid source. Keep that attempt and create a new instructed version from a known usable parent if needed.
5. Baseline and child use the same up-to-24 evenly spaced original camera IDs across the entire capture. This is deterministic coverage sampling, not an optimal-view guarantee. Missing object visibility remains a quality failure. No scene-specific frame numbers or furniture IDs.
6. Geometry and export reopen checks reuse the existing validation. Independent visual review uses groups of 8 objects, durable hashes and strict score >=8 / all objects / cited existing views / zero issues. The reviewer must also verify the requested change. Intentional design changes are judged against the instruction; unchanged scene content against the original capture. No automatic quality repair loop.
7. Before/After is the main edit comparison; Captured/Rendered is collapsed beneath it. Arrows and numeric counters navigate matched cameras. Quality-passed completed versions appear in Results; other versions remain in Diagnostics.
8. Open parent / Open selected version only navigates. Editing an older version branches a new child and retains all later versions.
9. Logs and compact diagnostics remain available; the full bundle is generated on demand.

## Cost and limits

This rebuilds saved scene code and renders two versions. It does not run ingest, reconstruction, reference image generation or TRELLIS. Copies require extra disk and renders/review can still take substantial time. Parent captures and assets must remain in the persistent project directory. Free-form conversation and automatic intent routing belong to 4B and are not part of this release.

Local validation exercises the real LangGraph, SQLite checkpoints, source receipts, HTTP queue and dashboard JavaScript, replacing only expensive external model/GPU services. Actual Codex editing and Blender rendering in the school image remain user acceptance work. Local tests do not establish visual quality or GPU compatibility.

## School acceptance

Use the existing state directory (default `return_output/agent_state_stage2_v020`). Stop the previous web service only when idle. Launch the new package; do not press Start Reconstruction.

Open the completed task → Edit this version → enter a small verifiable change → Create version & run. Suggested first change: change one explicitly named piece's leg material to black while retaining its dimensions and position. Use the exact object name where available; avoid an ambiguous “right” direction.

Check: new run ID with parent link; parent task unchanged; no Ingest/Reconstruct stages; matching Before/After cameras; geometry/quality statuses remain separate; Diagnostics retains failed-quality output. After a network interruption, Resume continues the unfinished stage, with one Apply change invocation and retained completed review groups. Switch back to the parent and verify that both versions remain available.

Return Download diagnostics after the child stops. Do not need to send the full scene bundle unless inspecting geometry/assets requires it.

## Local validation record

2026-11-07: 262 regression tests passed (25.03 s workspace, 24.86 s extracted release). The packaged `verify_stage4a.sh` passed 25 tests in 4.17 s using an isolated temporary project. Python syntax and shell syntax checks passed. The exact v0.4.7 engine identities remain compatible; the pinned full reconstruction backend has no changes. Actual school Codex/Blender run is not claimed.

## School validation update — 2026-11-09

Run 28066a0667094a1f97f8b2e580ceb6a3 completed a source-only floor roughness edit, independent impact review (9/10), geometry/export checks and successful resume of the interrupted Review stage. Whole-room failed quality was retained independently. Successful-stage time was 39m 02s; calendar elapsed included an overnight interruption. See acceptance-28066a.md for evidence and limits. The screenshot exposed a preview crop bug fixed in v0.5.6; no scene replay is needed. Earlier grouped-review and parent-link descriptions above are historical; new requests use v0.5.4 impact-review/1 and v0.5.5 omits the progress-area parent link.
