# Spatial Agent v0.5.5 — compact workspace

User-approved scope: reduce the sidebar's left whitespace, add an always-accessible sidebar toggle, keep stage progress permanently expanded, and remove the progress area's run ID / Open parent version controls.

- Removed the 1480px centered shell cap which created large outer gutters on wide screens. Sidebar is 228px, with 12px horizontal padding; task cards use 12px horizontal padding. Header uses a 16px horizontal inset (12px on small screens).
- Header toggle remains outside the hidden sidebar. Hiding the sidebar removes its grid column; the task area uses the full available width. The button supports keyboard activation, `aria-controls`, `aria-expanded` and dynamic Show/Hide labels. Browser-local preference survives refresh; storage denial is caught and never blocks the app.
- Progress uses a regular div, not an open/closed details widget. Polling and task switching cannot collapse it. Run ID and parent navigation are omitted from this region; internal task IDs, ancestry and saved outputs remain intact.
- Existing task sorting, editing, review outcomes, Snapshot approval, download and recovery behaviors remain unchanged. No backend Python changed; execution hashes and all existing compatibility entries remain those of v0.5.4.

Regression checks cover edge spacing, fixed progress, removed controls, sidebar open/close, restoring the setting, storage denial, task switching/polling, no API action from toggling, and existing recovery/download/version behavior. A static fixture is available under return_output/ui_preview_v055. Browser policy blocked local-file preview access, so no live-browser visual acceptance is claimed. DOM and JavaScript tests run locally.

Deployment is deferred until the user's active edit task ends. Installation does not resume/reconstruct any task. No GitHub push.
