#!/bin/bash
# SSH initialization must not depend on Python, model paths or mounted project data.
set -euo pipefail
if [[ $(id -u) != 0 ]]; then
    echo 'ERROR: SSH server initialization requires root.' >&2
    exit 1
fi
install -d -m 0755 /run/sshd
/usr/bin/ssh-keygen -A

# Same platform inputs as the known-working sdmap image. No default password.
if [[ -n ${ROOT_PASSWORD:-} || -n ${SSH_PASSWORD:-} ]]; then
    if [[ -n ${ROOT_PASSWORD:-} ]]; then
        password=$ROOT_PASSWORD
    else
        password=$SSH_PASSWORD
    fi
    if [[ $password == *$'\n'* || $password == *$'\r'* ]]; then
        echo 'ERROR: injected SSH password cannot contain a newline.' >&2
        exit 1
    fi
    printf 'root:%s\n' "$password" | /usr/sbin/chpasswd
    unset password
fi

# Keep platform-managed system sshd_config effective; only explicit port overrides win.
if [[ -n ${ROOM_SSH_PORT:-} ]]; then
    if [[ ! $ROOM_SSH_PORT =~ ^[0-9]{1,5}$ ]] || ((10#$ROOM_SSH_PORT < 1 || 10#$ROOM_SSH_PORT > 65535)); then
        echo 'ERROR: ROOM_SSH_PORT must be an integer from 1 to 65535.' >&2
        exit 1
    fi
    printf 'Port %s\n' "$ROOM_SSH_PORT" > /run/room-ssh-port.conf
fi
unset ROOT_PASSWORD SSH_PASSWORD
/usr/sbin/sshd -t
