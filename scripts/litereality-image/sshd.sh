#!/bin/bash
# Optional foreground server for platforms that do not supply their own SSH service.
set -euo pipefail
if [[ $(id -u) != 0 ]]; then
    echo 'ERROR: room-sshd must run as root to initialize OpenSSH.' >&2
    exit 1
fi
port=${ROOM_SSH_PORT:-22}
if [[ ! $port =~ ^[0-9]{1,5}$ ]] || ((10#$port < 1 || 10#$port > 65535)); then
    echo 'ERROR: ROOM_SSH_PORT must be an integer from 1 to 65535.' >&2
    exit 1
fi
mkdir -p /run/sshd
# Generated per container, never baked into an image shared by users.
/usr/bin/ssh-keygen -A
/usr/sbin/sshd -t -f /opt/room/image/sshd_config -p "$port"
echo "Starting SSH on container port $port; public-key authentication only."
echo 'The school platform must expose this port and provision authorized_keys.'
exec /usr/sbin/sshd -D -e -f /opt/room/image/sshd_config -p "$port"
