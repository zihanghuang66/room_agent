# Evidence-guided edit implementation plan

Goal: fix planning-to-execution information loss and unnecessary work after blocked/no-change edits using a reusable Skill plus small deterministic gates.
Architecture: the packaged spatial-edit Skill guides planner, inspector and editor; structured evidence/target bindings travel in immutable edit_contract. Code validates binding and routes on prerequisites and actual hash deltas. Legacy requests retain original graph paths and receipts.
Tech stack: existing Python/LangGraph/Codex subprocess/HTML UI; no new dependency or reconstruction backend.

User has approved the three repairs and requested this hybrid implementation now. Execute inline; no GitHub push.

- [x] Add self-contained spatial-edit/SKILL.md and a loader; bind Skill digest to new plans/requests. Keep role/tool limits intact.
- [x] Replace truncated report strings as the planning basis with bounded complete finding records, stable IDs, origin and object IDs. Validate selected records and preserve them in the editor instruction and immutable request. Tests reject fabricated IDs and preserve cross-turn provenance.
- [x] Add an opt-in source-check/3 readiness result. Ambiguity/evidence/capability gaps produce a concise question and end before baseline build/render. Positive no-op still requires independent evidence. Clarification is not quality failure.
- [x] Add edit-stage structured outcome and host source delta gate. No changes => return to conversation before build/review; partial changes with a blocking report remain an unpromoted candidate. No synthetic score0. Real completed edits use unchanged acceptance standards.
- [x] Resolve clarification operations without changing current pointer; expose reason/questions in Conversation and planning facts. New answer makes a new confirmed attempt in the same visible session; old files are preserved.
- [x] Test multiple object categories, unknown/ambiguous targets, citations, unchanged positive requirements, zero/partial edits, stop-after-edit, resume, duplicate confirmation, old request compatibility, and download regression.
- [x] Build v0.8.0; verify manifest, timestamps, shell and full extracted tests. Document actual model behavior as server acceptance, not locally proven.

Verification: extracted package regression: 421 passed in 41.80 seconds. Skill validator, release manifest, zero archive timestamps and shell syntax passed. Real school Codex/GPU acceptance remains pending.
