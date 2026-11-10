# v0.7.1 — clear file types and discoverable diagnostics

UI-only patch; Python, session dispatch, execution graph, checkpoints and source selection unchanged from v0.7.0.

- Download Blender (.blend) and Download portable Blender (.portable.blend) explicitly identify the filename variant; hover shows the basename. The UI neither repacks nor certifies texture completeness, and never substitutes a portable file from another revision.
- Download diagnostics stays visible in Files. During queued/running/finalizing state it has aria-disabled, no actionable download URL, a click guard and a reason. When idle/failed/completed it restores the exact session diagnostics URL. Server busy guards remain unchanged.
- Remove Other files & reports and candidate ancillary report lists from the UI. Accepted room files, candidate room files/views, Review findings and diagnostic archives remain. No source report or artifact is deleted.

Tests cover filename/ownership, active-to-idle download states, blocked busy click, absence of ancillary report lists, and existing layout/dispatch regression. Browser visual acceptance remains user-side.
