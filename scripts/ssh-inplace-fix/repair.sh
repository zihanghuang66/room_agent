#!/usr/bin/env bash
# Run in the school's current r4 container through the web Shell.
set -Eeuo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run as root in the web Shell.'; exit 1; }
BASE=/yinxin/projects/room/return_output
NAME=room_ssh_inplace_$(date -u +%Y%m%dT%H%M%SZ)_$$
REPORT="$BASE/environment_reports/$NAME"
mkdir -p "$REPORT"
exec > >(tee "$REPORT/repair.log") 2>&1
finish() {
    code=$?
    trap - EXIT
    printf 'RESULT exit=%s\n' "$code"
    printf '%s\n' "$code" > "$REPORT/exit-code.txt"
    if tar --sort=name --mtime='@0' --owner=0 --group=0 --numeric-owner \
        -C "$BASE/environment_reports" -cf - "$NAME" | gzip -n > "$BASE/$NAME.tar.gz.part"; then
        mv "$BASE/$NAME.tar.gz.part" "$BASE/$NAME.tar.gz"
        printf 'RETURN_TAR: %s/%s.tar.gz\n' "$BASE" "$NAME"
    fi
    exit "$code"
}
trap finish EXIT
python3 - <<'PY'
from pathlib import Path
import shutil, subprocess

binary=Path('/usr/sbin/sshd')
real=Path('/usr/lib/openssh/sshd.room-real')
service=Path('/etc/init.d/ssh')
prepare=Path('/opt/room/image/ssh-prepare.sh')
if not prepare.is_file():
    raise SystemExit('STOP: expected r4 ssh-prepare.sh is missing; no files changed.')
original=service.read_text()
lines=original.splitlines()
for i,line in enumerate(lines):
    if 'start-stop-daemon' in line and '--exec /usr/sbin/sshd' in line:
        line=line.replace('--exec /usr/sbin/sshd','--exec '+str(real))
        if '--start ' in line:
            line=line.replace('--exec '+str(real),'--exec '+str(real)+' --startas /usr/sbin/sshd')
        lines[i]=line
    if 'status_of_proc' in line:
        lines[i]=line.replace('/usr/sbin/sshd',str(real))
updated='\n'.join(lines)+'\n'
if '--startas /usr/sbin/sshd' not in updated or '--exec '+str(real) not in updated:
    raise SystemExit('STOP: unexpected SSH service script; no files changed.')
wrapper='''#!/bin/bash
# ROOM_SSH_INPLACE_BOOTSTRAP_V1
set -euo pipefail
initialize=1
for argument in "$@"; do
    case "$argument" in -t|-T|-V|'-?') initialize=0 ;; esac
done
if (( initialize )) && [[ $(id -u) == 0 ]]; then
    /opt/room/image/ssh-prepare.sh
fi
unset ROOT_PASSWORD SSH_PASSWORD
exec /usr/lib/openssh/sshd.room-real "$@"
'''
subprocess.run(['bash','-n'],input=wrapper,text=True,check=True)
subprocess.run(['sh','-n'],input=updated,text=True,check=True)
divert=subprocess.check_output(['dpkg-divert','--truename',str(binary)],text=True).strip()
if divert==str(binary):
    with binary.open('rb') as f: magic=f.read(4)
    if magic!=b'\x7fELF' or real.exists():
        raise SystemExit('STOP: unexpected sshd installation; no files changed.')
elif divert!=str(real) or not real.is_file():
    raise SystemExit('STOP: another sshd diversion exists; no files changed.')
else:
    current=binary.read_text()
    if 'ROOM_SSH_INPLACE_BOOTSTRAP_V1' not in current and 'sshd.room-real' not in current:
        raise SystemExit('STOP: unrecognized existing sshd wrapper; no files changed.')
backup=Path('/opt/room/ssh-inplace-backup')
backup.mkdir(exist_ok=True)
if not (backup/'ssh.init.original').exists():
    shutil.copy2(service,backup/'ssh.init.original')
if divert==str(binary):
    subprocess.run(['dpkg-divert','--local','--rename','--add','--divert',str(real),str(binary)],check=True)
binary.write_text(wrapper);binary.chmod(0o755)
service.write_text(updated)
print('Installed SSH bootstrap; existing host keys and account settings retained.')
PY
# Initialize first, so validation also works if this container has no keys yet.
/opt/room/image/ssh-prepare.sh
/usr/sbin/sshd -t
service ssh restart
service ssh status
python3 - <<'PY'
import socket,time
for attempt in range(20):
    try:
        with socket.create_connection(('127.0.0.1',22),timeout=2) as s:
            s.settimeout(2);banner=s.recv(128).decode().strip()
            assert banner.startswith('SSH-2.0-'),banner
            print('LOCAL_SSH_OK',banner);break
    except OSError:
        if attempt==19:raise
        time.sleep(.2)
PY
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
echo 'REPAIR_OK. Test Mac SSH, then use prepare_save.sh in the web Shell before saving the image.'
