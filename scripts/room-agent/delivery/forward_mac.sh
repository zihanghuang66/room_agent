#!/usr/bin/env bash
set -euo pipefail
read -r -p '学校当前 SSH 主机地址（例如 10.27.130.23）: ' SERVER
read -r -p '学校当前 SSH 端口: ' PORT
read -r -p 'SSH 用户名 [root]: ' USERNAME
USERNAME="${USERNAME:-root}"
if [[ ! "$SERVER" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] || [[ ! "$PORT" =~ ^[0-9]+$ ]] || [[ ! "$USERNAME" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]]; then
  echo '主机、端口或用户名格式不正确。' >&2; exit 2
fi
echo '连接成功后，在 Mac 浏览器打开 http://127.0.0.1:8765；此终端保持运行。'
exec ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:8765:127.0.0.1:8765 -p "$PORT" "$USERNAME@$SERVER"
