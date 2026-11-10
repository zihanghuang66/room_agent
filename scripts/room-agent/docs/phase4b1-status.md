> Historical v0.6.0 record. The user accepted planning and UI through v0.6.3 on 2026-11-09. Conversation is now default-open and voice removed; see phase4b2a-status.md for current confirmation/session behavior.

# 4B-1: Conversation and planning — v0.6.0

Implementation complete; school-server and browser acceptance pending.

## Delivered

- Default-collapsed Conversation on the selected version, history above a bottom composer, Chinese conversation / English UI.
- A separate LangGraph: context → interpret → validate. Context is bounded recorded metadata, status and selected findings, not scene geometry or imagery.
- Actual packaged Codex CLI 0.164.0 (gpt-6-astra, low) is the interpreter. Existing CLI login is reused. No API-key extraction, auth or DNS changes.
- Structured answer, clarification or draft scene-edit plan. All plans are non-executable. No chat path to Service.create_edit, create_full, execute or TaskQueue. Confirmation/dispatch belongs to 4B-2.
- Durable per-version conversation/history.json; request IDs, revision conflict checks, serial planning lock, no replay on refresh/restart. Failed/interrupted turns stay visible. Existing compact diagnostics include conversation history under ordinary size limits.
- Browser dictation is optional capability-detected SpeechRecognition. It transcribes into text, never auto-sends. It may rely on the browser vendor's external speech service; OS dictation is the fallback. No custom audio backend or claim of Codex native voice.

## Capability boundary evidence

Inspected installed CLI help/features and generated protocol schemas. Existing execution adapter was not reused. The planning adapter uses --ignore-user-config, --ignore-rules, strict config, ephemeral session, read-only permissions, never approvals, no web, disabled shell/unified-exec/code-mode host/apps/plugins/hooks/multi-agent/browser/image tools. No project directory is supplied as its working directory.

Before real inference, a local fake Responses service with an isolated empty Codex home verifies the actual runtime. Request tool lists are empty. Unsolicited exec_command and shell calls return unsupported; apply_patch returns aborted under read-only permissions and does not write the marker. An unknown version or failed contract disables planning. This is a version-specific tested tool boundary, not a generic claim about all Codex versions. Runtime metadata/session bookkeeping is distinct from scene writes.

Existing auth and transport settings remain subject to the school's environment. A successful local contract does not test external model access. Planning has a 120-second timeout; CLI transport reconnection can occur within a single call. The app does not auto-restart failed turns.

## Validation

- Source and extracted-package regression, HTTP security, persistent transcripts, concurrency, duplicate replay, stale revision, interruption, invalid response, target mismatch, no task dispatch, DOM/text escaping, per-version drafts, dictation/no-auto-send, stale UI responses and lost-send recovery are tested.
- Actual local authenticated Codex calls with synthetic metadata: ambiguous cabinet movement produced three clarifying questions (object/direction/distance); exact floor roughness change produced a validated non-executable plan; status question distinguished edit passed/9 from whole-room not passed. Structured plan and factual answer took 12.39 s / 6.44 s in those individual local samples; not server latency promises.
- Existing 29 execution Python modules byte-identical to v0.5.6; engine identities and task inputs/checkpoints unchanged.
- Package member timestamps are zero. Source only, no model files, credentials or scene data.
- No live browser visual or microphone acceptance claimed. Browser URL policy blocked local previews in the earlier session; no alternate route used. DOM contracts do not substitute for school-browser acceptance.

## User acceptance gate

Follow delivery 使用说明.md. Select an existing version and only use Conversation. Ask status, give an ambiguous request, clarify it, revise the plan, switch/refresh versions. Inspect that task count and queue remain unchanged. Test optional dictation. Return Download diagnostics; first use start_server/verify_stage4b terminal checks if the runtime fails. Do not re-run reconstruction for this acceptance.

4B-2 remains unimplemented: exact plan confirmation, durable plan-to-child binding, dispatch deduplication and source revalidation. 4B-3 broader operation routing also remains unimplemented.
