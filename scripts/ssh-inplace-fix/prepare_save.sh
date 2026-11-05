#!/usr/bin/env bash
# Use the platform web Shell, after repair.sh succeeds, immediately before Save Image.
set -Eeuo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run as root in the web Shell.'; exit 1; }
grep -q ROOM_SSH_INPLACE_BOOTSTRAP_V1 /usr/sbin/sshd || {
    echo 'STOP: run repair.sh successfully before preparing the snapshot.'; exit 1;
}
python3 - <<'PY'
from pathlib import Path
# A foreground sshd supervised by PID 1 must not be stopped from this script.
pidfile=Path('/run/sshd.pid')
if pidfile.exists():
    pid=int(pidfile.read_text().strip())
    if pid==1:
        raise SystemExit('STOP: sshd is PID 1; do not stop it from this helper.')
    if Path('/proc/1/comm').read_text().strip()=='tini':
        args=Path('/proc/1/cmdline').read_bytes()
        if any(x in args for x in (b'room-sshd',b'start-ssh.sh',b'sshd.sh',b'/usr/sbin/sshd')):
            raise SystemExit('STOP: PID 1 supervises SSH; this helper is for the current manually started school service.')
PY
echo 'Stopping SSH for the snapshot. Keep this platform web Shell open.'
service ssh stop
python3 - <<'PY'
from pathlib import Path
# Remove only generated host identities, not account keys/config/passwords.
for p in Path('/etc/ssh').glob('ssh_host_*_key*'):
    if p.is_file() and not p.is_symlink() and (p.name.endswith('_key') or p.name.endswith('_key.pub')):
        p.unlink()
assert not list(Path('/etc/ssh').glob('ssh_host_*_key'))
print('HOST_KEYS_REMOVED_FOR_SNAPSHOT')
PY
echo 'SAVE_IMAGE_NOW: click the platform Save Image button; wait until it finishes.'
echo 'Do not restart SSH before the save finishes. Afterwards, service ssh start restores this container.'
