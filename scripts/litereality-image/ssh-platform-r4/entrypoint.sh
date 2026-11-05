#!/bin/bash
set -euo pipefail
source /opt/room/image/environment.sh
if (( $# == 0 )); then
    set -- /usr/local/bin/start-ssh.sh
fi
case "$1" in
    /usr/local/bin/start-ssh.sh|/usr/local/bin/room-sshd|/opt/room/image/sshd.sh|start-ssh.sh|room-sshd)
        exec "$@"
        ;;
esac
# Some platforms replace CMD with a shell or keepalive process but retain ENTRYPOINT.
if [[ ${ROOM_SSH_AUTOSTART:-1} == 1 && $(id -u) == 0 ]]; then
    /opt/room/image/ssh-prepare.sh
    unset ROOT_PASSWORD SSH_PASSWORD
    case "$1" in
        /usr/sbin/sshd|sshd) ;;
        *) /usr/sbin/sshd ;;
    esac
fi
unset ROOT_PASSWORD SSH_PASSWORD
# Application wrappers initialize their own runtime when actually invoked.
# SSH must remain reachable even if that application initialization fails.
exec "$@"
