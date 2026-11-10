# v0.4.7 — Finish queue handoff

Verified code defect: graph reaches waiting_for_input before the CLI's automatic full archive and the presentation worker's diagnostic collection finish. Queue remains running but UI permits Finish. The user archive confirms graph waiting and later successful CLI Finish, but contains no scheduler record proving the precise duration of the blocked queue.

Scope:
- Web workers stop automatically building the full archive. Keep on-demand Full bundle and automatic bounded diagnostics; foreground CLI behavior remains compatible.
- Expose post-work finalizing state; disable decision/download controls only for that run until worker/diagnostics finish. Polling settles a finished worker before returning status.
- Bounded log drain after direct worker exit prevents inherited stdout from indefinitely retaining queue ownership. Never release a genuinely live worker or delete resource locks.
- Revalidate queued commands at dispatch; completed tasks cannot be restarted by stale queue entries.
- Include scheduler/error information in diagnostics to make recurrence diagnosable.
- Test slow finalization, duplicate clicks, genuine live worker, inherited stdout, restart/stale queue, real HTTP Finish with LangGraph and preserved artifacts. Supply isolated server acceptance script; never reopen the user's completed task or reconstruct it.
