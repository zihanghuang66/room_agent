# Separate edit acceptance from scene quality

Approved direction: report requested-change verification independently, reduce repeated whole-room review, preserve existing results and quality thresholds.

## Design

New edit requests pin `edit_review_policy=impact-review/1`. The graph and its immutable stage receipts stay unchanged. Requests without this policy retain the original grouped-review implementation, including checkpoint reuse. No task is resumed or migrated during installation.

New review compares the actual saved baseline and child source inventories, compiled scene, and matched before/after renders in one independent session. It must account for every changed file, identify affected objects, verify the instruction and absence of unintended changes with cited evidence, score at least 8, and report zero edit issues. Geometry remains mandatory. Missing or ambiguous evidence fails acceptance; a timeout remains an execution error and cannot produce approval. No automatic repair loop.

The parent quality and findings are historical, not a fresh whole-room approval. Failed parent quality remains failed; otherwise the child's whole-room quality is unreviewed until separately reviewed. A successful edit is never promoted to whole-room quality passed. Unchanged findings are carried as baseline context without spending another model call reviewing every object. This first implementation scopes review for all explicit edits; it does not implement automatic selective whole-room re-certification.

Old completed tasks receive a read-only projection of `change_verified` only when saved group files match the review receipt and binding. This is weaker than the new `edit_status=passed` contract and is labelled accordingly. Saved requests, outputs, receipts and checkpoints are never rewritten.

The UI adds one compact edit-result line alongside existing review information. Accepted edit snapshots may appear as accepted edit-stage outputs, with whole-room status still explicit. Legacy or unapproved edits remain under More details. No new navigation or popup menu.

## Implementation and checks

- [x] Regression tests: old receipt projection, tampered evidence, scope/no-regression failures, score boundary, inherited failed quality, no-change handling, timeout/reuse, parent immutability, UI result separation.
- [x] Add small review-policy module; pin new request policy and baseline quality context; dispatch new reviews without altering legacy node outputs.
- [x] Add read-only legacy status projection, new status fields, and minimal UI presentation.
- [x] Run focused tests, full Python/Node suite, check old compatibility fingerprints and an old interrupted graph resume.
- [x] Build v0.5.4 source-only package, extract and verify manifest, run acceptance tests from the extracted package, document server commands.

External Codex, Blender and GPU work is not run locally. Real review quality and runtime reduction remain school-server acceptance items. No GitHub push is authorized.

Validation: extracted v0.5.4 package passed 308 Python/Node tests in 26.83 seconds. Manifest validation and shell syntax checks passed. The real returned task projected 15 verified legacy groups without executing a node.
