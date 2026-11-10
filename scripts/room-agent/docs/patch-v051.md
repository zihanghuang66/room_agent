# v0.5.1 — source preview classification and toolbar

Root cause: new revision admission reused a source inventory excluding only `room_preview` and `repair_preview`. The real completed task also had `revision_preview/Object/ChairCluster0.glb`, a generated preview link. Admission rejected it before publishing a child request.

Fix: new edit requests record `source_preview_policy=generated-preview-v2`. Inventory, initial copying and later parent verification use this explicit policy, pruning only those three known top-level preview directories. Real asset links and unknown nested links remain rejected. Existing requests without the new field retain their original source inventory semantics. No source files, links, old checkpoints or scene outputs are deleted or rewritten.

Upgrade audit also reproduced a stable edit checkpoint mismatch when extracting a new package directory: the installation path was included as scene input. Verified engine compatibility now governs code identity independently of installation location. Cached completed edits remain reusable across package paths; changed request/state identities still fail.

UI: one shared component for semantic buttons and download links. Equal columns, 44px height, 13px system font, weight 500, 20px line-height, no internal wrapping. One accent primary action, two neutral secondary actions. At <=600px the whole group becomes aligned equal-width rows. `hidden` remains explicit so running-state controls cannot be exposed by the component display style.

Design reference: [Apple buttons](https://developer.apple.com/design/human-interface-guidelines/buttons), consistent adjacent controls and a single emphasized preferred action. This is the existing Room visual style with consistent controls, not an imitation of a platform screen.

Real browser checks on a static fixture built from the production dashboard: desktop buttons 144.66px wide and 44px tall, font13/weight500/line20, same top coordinate; 375px viewport buttons 328px wide and 44px tall, aligned vertically. Temporary viewport override reset. Fixture ran no reconstruction/model calls.

Regression fixtures reproduce the exact preview link path class, verify required asset retention and parent immutability, reject unknown asset links, retain old request inventories, reuse completed edit checkpoints across package locations, and reject changed scene input. Real school data execution remains pending.

Local full regression: 268 tests passed in 25.75 seconds. No existing user task or real GPU/model reconstruction executed.
