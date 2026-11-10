# Stage 3A: full-scan graph and stage previews

Approved scope: replace full-scan scheduling with LangGraph while reusing pinned LiteReality stage implementations. No dialogue, quality threshold change, GitHub push, or migration of unfinished legacy runs.

Architecture: new full-scan workflow version; prepare → preflight → ingest → reconstruct → seed → author → publish → export_check. Prepare-only ends after prepare. Graph checkpoints own routing. The driver directly calls stage.run(context, options), never PipelineRunner. Compatibility pipeline.json is a derived report only. Existing legacy runs retain their versioned adapter; new default tasks use the graph.

Recovery: each completed stage publishes an immutable scene snapshot, summary and preview index before its receipt. Stable working scene paths preserve embedded references. Before a failed stage is retried, preserve its working directory for diagnosis and restore the preceding published snapshot. A receipt committed before checkpoint failure is verified and reused. No exactly-once promise for a model call interrupted before receipt publication; unfinished stages can repeat calls. Scene snapshots cost extra disk; check available capacity, do not duplicate raw captures/model caches. Validate symlinks without traversing into external files; authorized capture links remain tied to raw integrity.

Files/tasks:
- [x] tests/test_scan_graph.py: real SQLite graph, failed-stage resume, receipt-before-checkpoint reuse, prepare-only, tamper, code version, stage previews while running, legacy compatibility.
- [x] scan_graph.py / scan_executor.py / scan_artifacts.py: graph, isolated stage calls, immutable receipts/snapshots, safe output collection and restore.
- [x] scan_driver.py: prepare/preflight/direct LiteReality stage dispatch, seed capture repair/camera refresh, final reopen checks; stage output metadata. Keep pinned backend unchanged.
- [x] service.py: route new graph requests, common locks/events, status/log/preview projection, package results; keep old adapter for old requests.
- [x] dashboard.html + tests: English stage labels, stage-grouped previews and logs; no preview must never imply completed render.
- [x] delivery and docs: v0.4.0 source-only package, foreground start, zero timestamps, full commands and explicit school acceptance procedure.

Verification: tests first for node and UI behavior; actual Python3.10 full suite; pinned runner regression; extracted-package manifest and startup --check-only; true offline scan prepare when local data available. GPU/Codex complete pipeline must be marked pending school verification, never represented by mocks as passed. Existing engine fingerprints remain strict; unfinished older saved-scene tasks use old release.

Implementation and local verification complete. School GPU/Codex acceptance remains pending; source-only delivery v0.4.0. No commits or push.
