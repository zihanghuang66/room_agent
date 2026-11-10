# Spatial Agent v0.5.3

## Changes

- Product header and browser title are Spatial Agent. Internal Python modules, archive prefix, script paths, state location and task identifiers keep their existing names for compatibility.
- Blender / GLB download links are directly visible in normal document flow. There is no floating file menu to clip against the result card. The page explains when files exist without a matching Snapshot.
- Review findings is a first-level disclosure with a finding count or review status. It is not nested under More details.
- Edit this version has one consistent location below Snapshot, including for completed versions with quality findings. Clicking it opens an instruction form already bound to that exact parent ID; it does not require task-type or version selection. New task retains the explicit generic version picker.
- Snapshot previews and the stage-progress details are expanded initially. User-collapsed progress stays collapsed during polling and resets to expanded when switching tasks. Unapproved candidate images remain under More details → Saved candidate.
- Resume and decision actions remain visible when needed. Editing an unavailable/running version stays disabled by eligibility checks. The earlier stale-preview guard and interrupted-copy recovery remain in place.

## Preservation

No execution-engine Python changed in this release. Engine hashes remain those of v0.5.2. No persisted requests, graph checkpoints, quality gates or scene files are migrated or changed. Existing released compatibility entries remain. Opening, switching and downloading tasks does not create a reconstruction job.

## Tests

UI tests cover the normal-flow file container, unique download links, review placement/counts, default expansion, polling preservation, completed-with-issues editing, direct parent binding despite an unrelated picker value, generic New task reset, stale-task protection, Finish/Resume and existing graph recovery. Extracted-package test counts are recorded in the adjacent release validation JSON.

No real school GPU/Blender/Codex reconstruction is executed locally. The browser's prior file-URL restriction prevents claiming visual browser acceptance. The specific clipping mechanism is removed structurally and checked in the HTML/CSS contract tests.

## Separate Codex routing timeout

The included `run_codex_network_check_v0.1.0.sh` is the existing read-only DNS/TLS diagnostic. It saves a compact report in `/yinxin/projects/room/return_output` and prints its path. It does not read authentication files, call a model, reinstall software or change DNS/proxy settings. HTTPS success is transport evidence only; it does not prove authenticated workspace routing works.

`codex login status` reports the active authentication method according to the [official authentication documentation](https://learn.chatgpt.com/docs/auth). A stored login is not proof that the current container can reach the routing service. A timeout alone does not establish DNS failure or invalid credentials. This release does not claim to repair that external connection failure.
