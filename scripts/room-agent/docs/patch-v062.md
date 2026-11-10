# v0.6.2 — resizable Summary and brand alignment

User-requested incremental UI change on the approved three-column layout. The Summary separator supports leftward expansion, rightward contraction, pointer capture, cancellation, keyboard arrows/Home/End, and double-click/Enter reset. Width preference persists in localStorage, independently of visibility. It is clamped for the current viewport without overwriting the original preference; desktop minimum 300px, maximum 900px, and 420px reserved for the conversation. Overlay layouts ≤1100 CSS px do not resize.

The logo is a fixed 32px green rounded square with a centered 20px white SVG house. This replaces the font-dependent glyph and transparent background. Snapshot height follows Summary width within bounds; existing accepted-only image selection is unchanged.

Four new regression tests cover pointer direction, bounds, persistence, resize/reopen, cancellation, responsive constraints, accessible markup, and no task submissions. Existing literal DOM-reference checks cover the new separator. Source and extracted-package full suite and checksum checks are required before delivery. Browser drag feel and visual alignment remain user acceptance items; no browser visual validation claimed.

No Python backend changes, state migration, reconstruction rerun, model change, or GitHub push. Conversations remain planning-only. See delivery 验收步骤.md for the exact acceptance sequence and prompts.
