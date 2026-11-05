#!/bin/bash
set -euo pipefail
/opt/room/image/ssh-prepare.sh
unset ROOT_PASSWORD SSH_PASSWORD
echo 'Starting SSH using /etc/ssh/sshd_config; runtime keys and platform credentials.'
exec /usr/sbin/sshd -D -e "$@"
