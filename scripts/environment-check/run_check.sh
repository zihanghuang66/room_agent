#!/usr/bin/env bash
set -eu
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if command -v python3 >/dev/null 2>&1; then
  exec python3 "$SCRIPT_DIR/inspect_environment.py" "$@"
fi
printf '%s\n' '未找到 python3。请将此提示和平台基础镜像名称发回；本脚本不会自动安装依赖。' >&2
exit 2
