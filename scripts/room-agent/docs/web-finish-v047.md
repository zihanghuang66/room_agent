# v0.4.7 — web Finish handoff

The graph's waiting state is committed before its worker finishes post-processing. Previously the UI offered Repair/Finish while the queue still owned that worker; automatic full-scene compression extended the conflict. A separate possible indefinite wait was inherited stdout remaining open after the direct CLI process exited. The returned server archive did not contain scheduler state, so the exact server-side blocking interval cannot be reconstructed retrospectively.

Changes:
- Web-origin run/resume no longer automatically packs the full scene. Automatic bounded diagnostics remain; Full bundle remains available on demand. Explicit foreground CLI preserves its old full-return behavior.
- Finalizing is separate from reconstruction and quality. Buttons/downloads wait until that task's worker and diagnostic collection end. Polling settles completed workers before returning queue status.
- Log reader drains for at most one second after direct CLI exit, rather than waiting forever for inherited stdout. It preserves bytes already drained; trailing output from surviving descendants after this bound is not guaranteed. Live CLI processes and execution locks are never bypassed.
- Queue dispatch revalidates graph state. A stale queued action cannot restart a task completed through CLI.
- Compact diagnostics include a persisted queue snapshot and scheduler error, when available.

Existing task 708e2cd3348a46ef814f0af7e2a96321 is already completed/user_finished, quality not_passed. Installing the release must not reopen it, repeat Finish, reset its queue by deleting files, or rerun reconstruction. Original backend, reviewer/repair policy, inputs and checkpoints are unchanged.

`verify_web_finish.sh` runs isolated tests using temporary data, actual HTTP/queue/LangGraph persistence, and mocked expensive reconstruction/model boundaries. The UI behavior is checked using the same shipped JavaScript in a Node DOM harness. No real scene or GPU/model service is used. Its real HTTP test executes Repair -> waiting/finalizing -> Finish -> draft_export -> completed exactly once, with unchanged immutable request and no repeated ingest/reconstruct. This is not a claim that a human browser click has been tested against the school's live reconstruction service.

Manual final check after starting the new server: the existing task should show Completed with issues, 4.5, and download controls; no Resume or new Finish is required for this already completed task. Future waiting tasks briefly show Finalizing, then decision buttons become available automatically.
