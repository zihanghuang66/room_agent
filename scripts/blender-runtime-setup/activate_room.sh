#!/usr/bin/env bash
# Source this again in a newly created container. No credentials are stored here.
export ROOM_PROJECT_ROOT="${ROOM_PROJECT_ROOT:-/yinxin/projects/room}"
export BLENDER_PATH="$ROOM_PROJECT_ROOT/return_output/tools/blender-5.1.2-linux-x64"
export LITEREALITY_BLENDER="$BLENDER_PATH"
export PATH="$BLENDER_PATH:$HOME/.local/bin:$PATH"
