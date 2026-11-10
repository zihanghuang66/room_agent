# v0.4.6: resumable independent review

- Preserve the existing task, source, immutable stage snapshots, and strict score >= 8 / all objects / zero issues gate.
- Record an active evaluation bound to source/build/check hashes, capture hashes, model/provider and review policy. Resume reuses its build and per-camera renders only if identities still match.
- Commit each group report atomically. Default new groups contain 10 objects. A timed-out group splits once into groups of at least 5; a leaf error stops the invocation and preserves successful siblings. Manual Resume retries only unfinished leaves.
- Reviewer calls receive 600 seconds (configurable local closeout/review_policy.json, 120..1800). Timeout changes do not invalidate completed semantic reviews.
- Migrate the existing v0.4.5 incomplete review only when the original failed operation, copied sources, render layout, captures and complete report coverage match. Preserve the three completed 20-object groups; never infer a passing score from incomplete reports.
- Show group progress / Review incomplete separately from quality failure. Keep unapproved output under Diagnostics.
- Test restart, timeout/splitting, source/input/image/policy invalidation, malformed reports, legacy migration and graph compatibility. Test extracted package startup; report unavailable GPU/model validation honestly.
