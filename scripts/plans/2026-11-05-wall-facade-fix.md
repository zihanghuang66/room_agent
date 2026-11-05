# General wall/facade consistency — design and implementation plan

Goal: handle flush built-in wardrobe facades without scene IDs, contact exemptions, fabricated recesses, or bypassing validation. Continue a failed v0.3.0 ingest using preserved extraction/placement.

Root evidence: raw Wall1 bbox thickness 0.0001m; loader replaces it with 0.16m; facade near face approximately coincident with captured plane, centre 0.03719m into the room. Existing renderer grows walls outward to 0.10m, preserving interior face. Layout and render disagree.

Chosen approach: preserve positive finite measured thickness; share outward wall-volume policy between layout and Blender. Test complete footprint intersecting bounded physical wall solid (5mm geometric tolerance), not centre in an artificially inflated slab. Check all furniture categories already checked by upstream, not wardrobe-only. Real penetrations remain errors; no confidence-based override. The depth of hidden wardrobes is not inferred.

Alternatives rejected: per-object allowlist would hide collisions; carving a recess from model prose invents unobserved wall geometry; shifting wardrobe to clear a fabricated 16cm wall damages captured position.

- [x] Create v0.3.1 copy and regression tests failing on v0.3.0.
- [x] Preserve raw thickness and implement shared wall geometry + footprint penetration, retain other checks and official quality prompts.
- [x] Replay actual scene data in an isolated copy; check no Storage position/size mutation, positive/negative synthetic cases including rotation and IDs changed.
- [x] Check real Blender wall mesh matches collision volume and attached facade stays clear, deliberately penetrating box detected.
- [x] Guarded migration for exact prior app hash/version and unfinished ingest only; backup inputs/results/layout pickles, no deletion, no unrelated run discovery.
- [x] Fresh tar extraction: all prior regressions plus wall/resume tests, hashes/timestamps, foreground launcher, reproducible package and report.

No new network/model calls in local regression. Real Linux GPU/full reconstruction remains server-side. Known residual: actual recesses with observed wall penetration need structural evidence and are not automatically approved.
