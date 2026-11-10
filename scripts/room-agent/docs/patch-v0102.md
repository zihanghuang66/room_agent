# v0.10.2 — Scene-grounded independent review

## Confirmed failure

The latest school return completed independent review in 17m48s, with geometry passed and visual quality 5/10. Its selector and reviewer inherited a bedroom-only list from the legacy room_004 batch workflow. The current office layout contains 119 objects. Headboard/bedside/bedding became an irrelevant evidence issue.

## Fix

- Package-owned `skills/spatial-review/SKILL.md` is explicitly included in both selector and reviewer prompts; it is not merely an unused document or a separately installed global skill.
- Build a target contract from all current `room_layout.json` object IDs plus three scene-level checks: lighting, contacts/openings, and capture-visible missing objects. No room-name or bedroom heuristics.
- Select from every capture contact sheet, share complementary frames, validate exact target set and actual integer frame IDs. Preserve partial/absent/unobservable distinctions. A modeled object absent in capture remains unresolved, not silently removed.
- Keep quality threshold >=8, zero issues, all actual objects verified, and existing geometry/integrity checks. The model still determines visibility and visual defects; code cannot prove those semantic judgments.
- Persist target inventory + Skill SHA and selection. Saved-scene engine fingerprint includes the Skill. Old selection receipts are immutable; a fresh review drops foreign target claims from its prompt but retains rendered frame IDs and marks unselected current targets unresolved.
- Existing completed runs/reports are retained. Upgrade performs no scene operation. A newly confirmed independent review builds/renders the retained source and assesses it, without ingest/reconstruct/edit/repair. It does not rewrite the old 5/10 score.

## Scope and cost

This applies to the active saved-scene independent-review graph. The pinned legacy historical batch package and full-scan backend are untouched. Local edit acceptance already has its own explicit edit contract and is unchanged.

Selecting views for all actual objects may require more paired renders than the previous five-target selector. Frames are deduplicated, but there is no guarantee of equal runtime or complete coverage. Missing evidence still prevents acceptance; do not lower the quality gate to compensate.

## Validation

Regression tests use the returned 119-object inventory, office/bedroom variation, >150 camera IDs, foreign target rejection, absent/hidden records, source-mutation detection and actual selector session injection with a fake model. Existing graph/resume tests verify retained outputs and completed receipt reuse. Full package tests are recorded next to the release archive. Actual school GPU/model behavior remains a server acceptance step.
