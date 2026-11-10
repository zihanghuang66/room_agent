---
name: spatial-edit
description: Plan, inspect and perform bounded changes to an existing reconstructed scene using review evidence, explicit target bindings, and preserved current results. Use for scene edits and report-driven repairs, not new-scan reconstruction.
---

# Evidence-guided scene editing

Use the role and tools supplied by the caller. This Skill grants no tools or execution authority. A planning conversation drafts; the user confirms execution. Work stays in the same visible session and preserves the current accepted result until a changed candidate passes review.

## Ground the request

Distinguish the current retained scene, historical reviews, and unaccepted attempts. Historical findings suggest what to inspect; they do not establish a current defect. Preserve the report origin, finding ID, object IDs and cited views across planning and execution. Do not turn a precise report target into a vague description. When multiple objects are mentioned in a finding, select the requested subset explicitly; do not infer identity from prose alone.

For direct user edits, report-derived citations may be empty. Do not invent a report or object ID. Resolve user descriptions against actual scene data before editing. In follow-up turns preserve the previous target/evidence unless the user changes scope; use current supplied facts to validate them.

A useful plan states the intended change, target, evidence, preservation constraints and how the result will be checked. Keep the reply brief. Do not repeatedly recite historical failures or whole-room quality for a simple edit.

## Check prerequisites before spending compute

Separate three questions: can the target be uniquely identified; is the evidence sufficient to guide this change; can available editing tools change the requested parts while respecting the preservation constraints?

An opaque generated mesh is not a set of independently editable semantic parts. A whole-object bounding box does not establish local-part dimensions. Before proposing local shape changes, establish an editable representation or a permitted method; do not substitute whole-object scaling or silent regeneration. This applies to any object type.

If a prerequisite is missing, give a specific question or limitation, with candidate IDs and human-readable distinctions where supported. Do not guess, and do not continue building/rendering merely to reach a failure score. If visual evidence can be obtained by the normal matched-view stage and the target and editing method are already established, proceeding to that stage is reasonable; distinguish obtainable evidence from absent/insufficient evidence.

An already-satisfied request requires positive evidence for every requested change. Preservation means retaining the existing state; when no files change and no outputs are rebuilt, host hash equality supplies preservation evidence. Do not demand fresh renders to prove that nothing changed, or claim that no-op certifies whole-room quality. A relative action is not already satisfied merely because the object has current coordinates.

## Make one bounded edit

Use the confirmed target/evidence contract. Recheck actual scene identity rather than blindly trusting the report. Change only the requested scope, keep valid source after small steps, and report what was actually saved. Do not silently expand scope, lower acceptance criteria, regenerate neural assets, or call unavailable services.

If inspection reveals ambiguity, unsupported editing capability, or inadequate evidence, stop and report the missing prerequisite. If any partial edits were saved, say so; they remain a candidate and must not replace the current result. Never report an unperformed change as completed.

## Report outcomes honestly

- Already satisfied: positive requirement verified, files unchanged; retain current result.
- Needs clarification or blocked: state the specific missing target/evidence/capability; return to conversation.
- Changed: summarize actual changed files and objects; proceed to build and independent review.
- Review not passed: retain original and candidate; explain actual findings.
- Execution interrupted: preserve checkpoints; distinguish infrastructure interruption from scene quality.

The caller defines the output schema. Use it exactly. No source delta is not a zero-quality render. A completed model response is not proof that an edit occurred. The host verifies hashes, target/evidence references and promotion conditions; reviewers verify the change itself.
