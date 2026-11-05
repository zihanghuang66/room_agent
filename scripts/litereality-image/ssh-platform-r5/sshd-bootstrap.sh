#!/bin/bash
# Preserve the standard sshd path even when a platform replaces ENTRYPOINT/CMD.
set -euo pipefail
real=/usr/lib/openssh/sshd.room-real
initialize=1
for argument in "$@"; do
    case "$argument" in
        -t|-T|-V|'-?') initialize=0 ;;
    esac
done
if (( initialize )) && [[ $(id -u) == 0 ]]; then
    /opt/room/image/ssh-prepare.sh
fi
unset ROOT_PASSWORD SSH_PASSWORD
# OpenSSH re-exec uses the real absolute binary path, not this shell wrapper.
exec "$real" "$@"
