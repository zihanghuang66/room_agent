# White English dashboard integration — v0.3.1

Approved by user: integrate the white card-based English preview with the running application.

1. Keep existing API contracts, source/scan validation, queue and repair gating. Add behavioral tests for task dialog, English state labels and empty inputs.
2. Replace dashboard markup and styles with white cards, task sidebar, progress, real preview/report cards, review/actions and collapsed logs. Use a task dialog for saved review and experimental scan; no sample image or synthetic run state.
3. Translate built-in UI labels and guidance. Preserve original report, exception and log content. Maintain quality vs completion vs timeout distinctions and candidate export labels.
4. Run all local tests, inspect actual browser for empty state and task modal; exercise real HTTP/worker flow with test fixtures without GPU/model calls.
5. Build a new v0.3.1 source-only tar, run extracted-package checks, verify manifest and zero timestamps. Keep environment/state directories stable. No GitHub push or Ubuntu testing.

## Verification

- All 104 local tests passed on Python3.10, including real HTTP/worker tests and dashboard state/action tests.
- Actual browser: created a prepare-only task using a real 59-frame-pair scan; queue submission and completion displayed correctly. The first local attempt reported the missing Linux interpreter, then a new task with the correct local interpreter completed. No GPU/model calls were made.
- English chrome preserves original report/log text and candidate output labels. No synthetic scene data is bundled.
- Final delivery uses v0.3.1 with stable environment and state paths. School GPU and Linux execution were not tested in this UI change.
