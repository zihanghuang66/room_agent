# Batched saved-scene review Implementation Plan

**Goal:** Rebuild and independently review the saved authored scene before making edits, then repair at most three small batches without invoking ingest/reconstruct/seed/author pipeline stages.

**Architecture:** A versioned standalone launcher reuses the pinned v0.3.1 application, copies the explicit source room into a new return_output/artifacts run, and retains immutable per-round source/build/evidence. An independent image selector examines contact sheets covering every captured RGB frame and nominates actual captured views for headboard, bedside cabinet, curtains, bedding, and ground contact. Global and focused evidence go to a fresh review session. Missing visibility remains unresolved. Each repair handles at most three concrete issues with lighting/large appearance prioritized over small fixtures. A terminated repair is always followed by a fresh build/check of saved changes when executable; session termination and quality verdict are separate fields.

**Tech Stack:** Python 3.11, existing Blender/Codex, pinned LiteReality modules, Pillow, pytest.

- [x] Add regression tests for timeout vs quality status, malformed review rejection, frame selection across all input frames, missing/unknown frame IDs, unchanged source, and sequence rebuild-review-before-repair.
- [x] Add batch_core.py for validated evidence selection, verdicts, checkpoint copying and event duration accounting (tool intervals vs unclassified non-tool time; never label residual as pure thinking).
- [x] Add worker.py for bounded build/render/geometry/reopen commands, independent selector/reviewer prompts and recorded sessions, immutable round checkpoints, limited repair batches and post-timeout assessment.
- [x] Add run_batches.py and launchers for explicit source selection, project lock, existing runtime/environment reuse, snapshot isolation, terminal logs and automatic return archive even on failure/interruption.
- [x] Test pure behavior, simulated orchestration, actual returned-room copy/identity, Blender source rebuild and export where local dependencies permit. State limits of local vs school end-to-end verification.
- [x] Package only scripts and pinned app, no model weights/CLI binaries; epoch timestamps; unique v0.1.0 name; verify unpacked tests and checksums. Supply foreground commands, no tmux/background.

Implementation proceeds inline under the user's approved scope. Original source run and upstream acceptance rules remain unchanged. No GitHub push.
