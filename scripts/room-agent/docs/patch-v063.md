# v0.6.3 — conversation input polish

User has accepted the seven v0.6.2 planning/interface checks. This patch adds an inline SVG house before the assistant name in all chat turns, removes the visible Status when sent block (facts retained in persisted conversation/diagnostics), changes plain Enter to send and Shift+Enter to newline, and removes the browser speech UI and SpeechRecognition implementation.

Composition state, event.isComposing, legacy keyCode 229, and key-repeat guards prevent common IME/held-key submissions. Existing sending locks, pending planning checks and request-id replay remain unchanged. Browser-specific Chinese input behavior still requires user verification. No Python backend or persisted-state changes; no reconstruction or GitHub push.
