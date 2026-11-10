# Download and no-change presentation

Implemented in v0.7.2. See patch-v072.md for behavior and validation limits.

- Offer existing portable.blend and GLB files; no ordinary blend fallback or rename. Preserve all underlying artifacts.
- A verified already-satisfied request ends before baseline build/render and does not change the current scene.
- Conversation displays one simple message; no extra decision is required.
