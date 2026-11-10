# v0.5.2 — compact workspace and copy recovery

The approved layout replaces the global three-action toolbar with a room title, status and More menu. Accepted comparisons remain central. Editing belongs to the selected version; Blender and GLB links share a Download disclosure. Diagnostics and full archive downloads appear once in the More menu.

Details holds the full progress grid and task metadata. Activity & findings holds review details, saved candidates, other files and logs. Failed/interrupted tasks retain visible Resume; waiting tasks retain Finish/Repair without opening a disclosure. Candidate editing stays inside Saved candidate, and unapproved images never enter the accepted result panel. Task switches immediately hide stale contents and disallow editing until the new task status is known. Polling does not reset open disclosures or the selected camera.

Native disclosures provide keyboard operation. Escape closes a download menu and returns focus to its trigger; outside clicks dismiss it. Narrow-screen CSS stacks paired images and preserves control sizes. The layout fixture is separate from the delivered application and does not execute a scene.

## Recovery

Before a model edit starts, source files are copied to a temporary directory, verified and renamed into place. Incomplete pre-marker copies from older releases are retained separately and can recover. Existing edit markers still prevent repeated model edits. Completed review groups, stage receipts and immutable requests are not rewritten. Unknown asset links remain rejected; the v0.5.1 generated-preview policy remains intact.

Exact released v0.5.1 and earlier engine identities remain accepted. Tests include resuming a failed review from a v0.5.1-identity request, preserving the parent and calling the edit/build only once.

## Validation and limits

- Added layout contracts for collapsed details, single archive entries, version-bound editing, candidate isolation, recovery/decision actions, Escape/focus and stale-preview protection.
- Existing full local regression passed 276 tests before the extra release-identity parameter was added; the resulting revision test file passed all 12 cases.
- Extracted-package acceptance and final full test counts are recorded alongside the release archive in its validation JSON.
- The browser tool rejected the local `file://` preview. Visual inspection was not completed; automated DOM/behavior checks and responsive CSS checks must not be described as visual browser acceptance.
- Real school Blender/GPU/Codex execution was not run here. Local graph integration tests substitute external execution.
- Rendering still retries at stage granularity. The quality threshold remains unchanged. A successful small edit does not automatically approve pre-existing room defects.

## Upgrade

Wait until the previous server has no running or queued task, then stop that web server. Extract the new package and run `bash start_server.sh`. Startup verifies the package, runs local checks and uses the existing persistent state directory. It does not create a reconstruction task. Keep the same state root if previously customized.

Open existing tasks to inspect them. Completed tasks need no Resume. Use Resume only when intentionally continuing an unfinished task. Download diagnostics from the More menu when returning a report.
