#!/usr/bin/env bash
export ROOM_PROJECT_ROOT="${ROOM_PROJECT_ROOT:-/yinxin/projects/room}"
export LR_REPO_ROOT="$ROOM_PROJECT_ROOT/scripts/vendor/litereality-agent-28ffe1f-room-codex-v0.1.2"
export PYTHONPATH="$LR_REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$HOME/.local/bin:$PATH"
export LR_AGENT_PROVIDER=codex LR_AUTHOR_PROVIDER=codex LR_MATERIALS_PROVIDER=codex
export LR_QUALITY_PROVIDER=codex LR_REFINE_PROVIDER=codex LR_PROCEDURAL_PROVIDER=codex
export LR_CLASSIFY_PROVIDER=codex HARNESS_VLM=codex
export LR_CODEX_MODEL=gpt-6-astra LR_CODEX_EFFORT=high
export HARNESS_MODEL=gpt-6-astra LR_PROCEDURAL_MODEL=gpt-6-astra LR_CHAIR_JUDGE_MODEL=gpt-6-astra
export HARNESS_VLM_CONCURRENCY=1
export PYTHONDONTWRITEBYTECODE=1
