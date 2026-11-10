# v0.4.6 — independent review recovery

## Failure and recovery

The 708e2cd run completed 3 groups (60/119 objects) before the fourth reviewer hit its fixed 240-second budget. v0.4.5 wrote those reports but did not load them on Resume. This is distinct from a repair's 40-tool limit or a quality rejection.

v0.4.6 uses per-group atomic receipts and an active evaluation. Source/build/check hashes, all capture frame/camera hashes, reviewer identity, semantic policy and rendered image hashes bind each receipt. A changed input stops recovery with an explicit error; files remain available. No automatic rollback, ingest, reconstruction or scene editing is introduced by review recovery.

- New groups: 10 objects. A timeout splits that group into two 5-object groups; failed leaves stop the invocation. Resume retries unfinished leaves, retaining successful siblings.
- Completed calls (including low scores and evidence defects) are retained verbatim. An incomplete assessment has no overall pass or score.
- All objects, score >= 8, zero issues, instance preservation, original geometry/support checks remain required.
- Missing evidence still expands to 12/24 capture views according to the previous policy. New evidence requires a new assessment; this is not replaying an unchanged input.
- Repair budget remains 3 batches / 2 stalled batches. Network timeouts are technical interruptions and do not consume a repair batch again.
- Final Publish still invokes the original collision correction, quality checks, compile and material bake. Successful steps are journaled, and final review uses a stable directory. Retry after a review timeout reuses the compiled/baked export and completed groups.
- The upstream pinned backend is unchanged.

## Old report migration

A one-time v0.4.5 import requires exactly one unfinished candidate, matching original failed operation, matching copied source and renderer layout, valid strict report types and object IDs, all selected camera pairs, and matching model/provider. It preserves the completed 20-object groups; remaining objects form new 10-object groups. The migration records its evidence/provenance and new hashes.

v0.4.5 did not record per-group content hashes. Migration is a consistency check against its retained source, layout, original request and images, not a claim that historical hashes exist. Ambiguous or inconsistent cases fail closed without discarding files. Hashes protect all subsequent resumes.

A crash between evaluation creation and active-pointer update is recovered by the operation-bound orphan evaluation. A crash after a group receipt but before the progress write reuses the committed group.

## Timeout setting

Default per-review-call budget: 600 seconds. Optional file in the task's `realism_authoring/closeout/review_policy.json`:

```json
{"timeout_seconds": 900}
```

Allowed integer range 120..1800. Only the timeout can be configured here; no score/coverage/check override. Changing timeout retains completed semantic reviews. Total elapsed review time can exceed 600 seconds because each group has its own budget.

## Validation and limits

Tests use real file persistence and graph SQLite checkpoints, replacing GPU/model calls at their boundary. Covered: middle-group timeout, fresh-process resume, split sibling reuse, committed-report crash window, orphan activation recovery, malformed/stale reports, source/capture/layout/render changes, configurable SessionSpec budget, legacy 3x20 group migration, strict failed findings, UI progress, final Publish recovery and engine upgrade compatibility.

Local tests cannot validate live school GPU renders or current Codex service response time. The returned diagnostics archive includes only selected images, so it cannot substitute for all server-side evidence; final migration checks run on the server before any review call.

Recovery scope: Publish review timeout is resumable after successful compile/bake. If a process dies inside compile/bake after partially overwriting an export, identity validation stops recovery for inspection; the adapter does not silently trust or roll back a partial export.
