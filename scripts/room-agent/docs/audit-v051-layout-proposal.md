# v0.5.1 audit and layout proposal — 2026-11-07

Status: local audit complete; one recovery defect fixed in development, layout proposed only. Existing v0.5.1 archive is unchanged. No school GPU/model run, reconstruction restart, commit or push was performed.

## Verified defect and local fix

An interrupted source copy before the editing session creates `stable/room` but leaves it incomplete. v0.5.1 sees that directory on Resume, skips copying, and raises `Unstarted edit source differs from snapshot` repeatedly. The model has not been called at this point, so rejecting every retry prevents useful recovery.

Two new tests reproduced this failure before the implementation changed:

- Interrupt the actual copy entry point before the model starts, then retry.
- Resume an incomplete `room` directory made by the previous release.

The fix copies to a unique staging directory, verifies the source inventory, and renames the completed directory into place. An old partial directory is retained as `room.copy-interrupted-*`. Once `edit_started.json` exists, this recovery path is not used: saved model edits remain authoritative and the model is not silently called again. Failed staging copies remain available for diagnosis; disk reclamation is not implemented by this patch.

Files changed: `src/room_agent/edit_driver.py`, `tests/test_edit_driver.py`, `engine_compatibility.json`. The compatibility policy records the exact released v0.5.1 hashes and the new development hashes, without changing stored task requests or review receipts. The policy is marked `0.5.2-dev`; this is not a new released package.

## Checks and practical limits

| Area | Evidence / result |
| --- | --- |
| Original v0.5.1 baseline | 268 tests passed, 26.64 seconds |
| New fault reproductions before fix | Both failed with the expected source-copy mismatch |
| Edit driver after fix | 9 tests passed, including interrupted edit at-most-once behavior |
| Full regression after fix | 270 tests passed, 26.33 seconds |
| Parent and source integrity | Existing tests cover parent mutation rejection, explicit parent selection, retained real assets and rejected unknown links |
| Patch upgrade recovery | Existing test covers completed edit reuse when the package installation path changes |
| Review recovery | Existing tests cover completed group reuse, report tampering, missing observations, fake image citations and required change verification |
| UI and queue | Existing local tests cover child creation, parent not restarted, before/after views, unapproved outputs hidden, Finish and queue behavior |
| Real Blender / CUDA / Codex | Not executed in the school environment in this audit; external execution is replaced in orchestration tests |

Passing regression tests is not proof of an error-free server run.

## Remaining gaps, not claimed fixed

1. **Rendering resumes at stage granularity.** `SceneExecutor._external` creates a fresh attempt directory. If rendering fails after producing some camera images, Resume re-executes that rendering stage rather than reusing those images. It does not restart ingest or reconstruct. Frame-level receipts would need to bind source/build identity, camera pose, resolution and render settings before safely reusing frames.
2. **A small edit still gets a whole-room quality review.** Groups contain up to eight objects; each group has a 600-second session limit and independently checks the requested change. Pre-existing defects can keep overall quality at `not_passed` even if the requested colour change succeeded. The current policy intentionally does not waive existing defects. The UI should distinguish saved/executed state from quality approval. Separate change-verification and overall-quality summaries are a future design improvement, not a relaxed pass threshold.
3. **View selection is coverage sampling, not guaranteed visibility.** The current maximum of 24 evenly spaced camera frames covers the time range, but can miss a small object or contact. Unobservable objects fail evidence checks. Adaptive selection would be a separate improvement.
4. **Interruption immediately after the edit marker is conservative.** A marker is recorded before invoking the model. If interrupted between those actions, retry does not invoke the editor again. This protects against duplicate paid/destructive work but may leave the change unapplied. The result must not be represented as successful editing merely because the graph later completes.

## Recommended layout — single page with progressive disclosure

Keep the white background, consistent typography, English labels and a restrained card layout. No additional permanent action-card row.

```text
Workspace       Room name                         Completed   ⋯
                Current step / short outcome       Details ▾

                ┌──────────────────────────────────────────┐
                │ Captured           Rendered              │
                │                                          │
                │                 ‹  1 / 12  ›              │
                │                                          │
                │ Edit this version            Download ▾  │
                └──────────────────────────────────────────┘

                ▸ Activity & findings
```

- **Header:** room name, one status and a `⋯` menu. UUID, paths, parent reference and timings move into Details.
- **Progress:** one current-step summary; Details expands the full step grid. Failures keep the failed step and the available Resume action visible. Waiting for a decision keeps the required actions visible. Do not hide the action needed to move forward.
- **Scene result:** matched images at equal size and numeric arrow navigation. No repeated status cards or separate stage image galleries.
- **Edit:** move beside the version being edited, below its preview, not into a global download toolbar. For an unapproved saved version, keep its edit action inside the explicit candidate/details area so it cannot be mistaken for an approved result.
- **Download:** the result card menu contains only that accepted version's Blender / GLB outputs. No per-object mesh listing by default.
- **`⋯` menu:** `Download diagnostics` and `Download full archive`. These are the only compact/full bundle download entries. The existing label is **Full bundle**, not Full Build: it downloads a complete archive and does not rebuild the scene.
- **Activity & findings:** collapsed logs, review details, candidate outputs and history. Unapproved images/files remain out of the main result area. No duplicate Diagnostics tab plus duplicate download buttons.
- **No accepted result:** a short status replaces the large empty preview card. Candidate details remain accessible in the collapsed area; failures still expose Resume. Do not silently show a different parent version as the current accepted result.
- **Responsive / accessibility:** action controls share height/type styles; menu items have explicit names; keyboard opening, Escape dismissal and focus return must work. Small screens stack full image panels rather than shrinking text or clipping buttons.

Alternative: an Overview / Activity two-tab layout separates information more strongly, but requires more switching and risks hiding recovery actions. The single-page proposal is recommended for the current scope.

## Next-release acceptance before packaging

- Re-run copy-interruption, legacy partial-copy, consumed-edit and review-group recovery tests.
- Retain exact old-task compatibility and immutable request hashes.
- Exercise completed-approved, completed-with-issues, failed, queued, running and waiting-for-decision UI states.
- Confirm there is one entry each for diagnostic/full downloads, no unapproved main results and no wrong-version edit action.
- Inspect wide and narrow layouts and keyboard menu behavior.
- Validate the extracted source-only release, not just the working tree. Do not label a local mocked run as school GPU/model acceptance.
