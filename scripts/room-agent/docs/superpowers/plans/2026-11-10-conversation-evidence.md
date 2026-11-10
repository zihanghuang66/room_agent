# Conversation evidence implementation plan

Goal: let the conversation inspect existing scene source and images before advising.
Architecture: UI-only EvidenceReader -> bounded LangGraph inspect loop -> cited answer/plan -> existing explicit confirmation. No worker or Skill migration.
Tech: Python, existing LangGraph, no-tool Codex CLI image inputs, existing HTML conversation.

- [x] Test then implement ui/scene_evidence.py: closed catalog, bounded reads/search/images, SHA256 records, verify-before-use.
- [x] Extend shared planner_contract and planner_cli: inspect request schema and image inputs; exercise real local CLI with fake model and no tools.
- [x] Wire conversation graph/context/progress and citation validation, max two inspections, preserve source binding; bind evidence at confirmation.
- [x] Add collapsed Evidence in conversation, progress text and adequate request timeout; preserve all current UI actions.
- [x] Regression: traversal/link/cross-run, malformed requests, no execute, bounded loop, stale evidence, source/image transport, legacy plans, stable session; full extracted package tests.
- [x] Deliver v0.9.0, deterministic archive, server commands and explicit acceptance limitations. No GitHub push.

Verification: final extracted executable code passed 445 tests in 43.67 s. Real local Codex 0.164.0 probe verified image inputs and zero tools against a local simulated model. Querying the returned CosmeticOrganiser source produced ten bounded source excerpts and one existing view without model calls. Live school semantic acceptance pending.
