# Stage 3A finite closeout — v0.4.5

## Contract

Continue the existing full-scan graph and retained Author workspace. The scan archive, immutable request, completed stage receipts and pinned v0.3.1 backend are unchanged. The compatible upgrade is restricted to the released v0.4.0–v0.4.4 code fingerprints; arbitrary modified code is not allowed.

The current task 708e2cd3348a46ef814f0af7e2a96321 has reached acceptance. Its first Resume rebuilds saved Room.py and independently reviews it. It does not run ingest/reconstruct or the initial Author/refine/material sessions again. A durable phase-entry marker also prevents those passes repeating if the first new review fails before generating a report.

## State transitions

- Initial review passes: original publish/collision/bake/final review -> export check. Final publish quality rejection goes to technical draft export, then ends with issues; it does not enter another unbounded repair cycle.
- Initial review fails quality with actionable defects: `author_decision` waits. Choose **Repair one batch** or **Finish with issues**. Resume alone cannot authorize repair.
- Repair: up to three concrete issues; geometry first, then lighting, large appearance, details. 40 tool calls / 600 seconds. Rebuild and independent review follow even when the repair reaches either cap. Limits are per repair, not the entire review/build duration.
- At most three manually authorized batches across this closeout policy. Two consecutive non-improvements on the same camera set end with `no_progress`. Evidence-only or ambiguous reports without concrete targets end with an explicit reason. Mixed missing evidence and visible defects can still repair those defects, but cannot pass until evidence requirements are met.
- Normal unsuccessful end: execution completed, quality not_passed, full_run_completed false. This is not accepted publication. Main Results retains successfully published earlier stages; unapproved candidates and verified draft downloads stay in Diagnostics.
- Network/renderer/missing-validator/build failures remain execution errors. Resume retries the stopped phase. A repair batch is charged before the model call; retry reviews saved edits instead of repeating that model call. Stable operation IDs prevent duplicate repair if the process dies between report and graph checkpoint commits.

## Review standard and evidence

Original finite-surface support, real-mesh collision, articulation, instance-preservation and build fingerprint checks remain in use. Missing checks are execution errors, never passes. Visual acceptance still requires score >=8, zero issues, all objects reviewed. Object IDs are reviewed in groups of at most 20; every ID needs an observed result with valid cited view filenames. Unknown/invented IDs cannot become repair targets. Group aggregation uses the minimum score and requires every group to pass.

Camera selection covers the full available trajectory, including its endpoints. It starts with six existing paired cameras and expands to 12 then at most 24 if evidence is missing. Selection uses deterministic gap-filling in frame index order; it is NOT claimed to be visibility-optimal. The expanded set is retained for subsequent comparisons. If the scan never saw a surface, rendering more views cannot establish its true appearance; missing evidence remains unresolved.

Initial expansion can render 6+12+24 views; subsequent checks reuse the selected IDs but render the changed scene again. Grouped review and extra views increase GPU/model time. The original 600-second repair cap does not include these independent builds/reviews. This bounded evidence budget limits cost; it does not relax acceptance.

Progress is not prose issue-count reduction: it requires geometry improvement, or consistent-camera improvement in score/identified defect groups. Model assessments are still variable; the best pointer means best under this conservative comparison, not proven global optimum.

## Retained data

Under backend/scenes/<upstream_scene>/realism_authoring/closeout:
- control.json: charged batches, pending operation, stable operation receipts, camera IDs, history, best candidate.
- batch_NN.json: selected targets and evidence-only findings.
- reviews/attempt-*/: source snapshot, retained exports, geometry, grouped reviews, final assessment and paired images.

The mutable source remains room/Room.py. The best reviewed candidate is retained separately; no silent rollback of newer edits. Before Resume, the outer executor checkpoints the saved scene under work/failed. These snapshots cost disk space. The compact diagnostics archive is not a restore backup; keep the server state directory.

Unapproved export verification checks the exact reviewed source/build fingerprint before and after Blender reopen, packs textures and checks BLEND/GLB bounds with the existing verifier. This checks file usability, not visual correctness. Final accepted publication keeps the original gates.

## Server acceptance checklist

1. Open the existing task, Resume once. Earlier completed stages must remain completed without new starts.
2. First check uses the retained source; no initial reconstruction/authoring sessions.
3. Quality failure with concrete defects -> Your decision. Resume disabled; Repair one batch and Finish with issues available.
4. One repair click consumes one batch. Tool/time cap still leads to independent review; a stopped network call is a resumable execution error.
5. No progress twice / total three batches / no actionable evidence -> Completed with issues, no automatic loop and no accepted publication claim.
6. Accepted -> original Publish and Export check. File usability and quality states remain separate.
7. Results excludes rejected revisions; Diagnostics contains rejected candidates and the technically verified exports after closeout.
8. Send the compact diagnostic tar; keep the full bundle on the server.

Local offline testing exercises actual SQLite checkpoints, HTTP/CLI, subprocess lifecycle and filesystem receipts; GPU/Blender/model boundaries are substituted. It cannot certify this real room's quality, Linux GPU runtime or network availability. School validation is still required before calling Stage 3A fully accepted.
