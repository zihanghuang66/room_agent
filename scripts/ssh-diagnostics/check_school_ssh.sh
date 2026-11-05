#!/usr/bin/env bash
# Read-only service diagnostics; writes only its own report under return_output.
set -uo pipefail
ROOT=/yinxin/projects/room
STAMP=$(date -u +%Y%m%dT%H%M%SZ)_$$
NAME=room_ssh_diag_$STAMP
DIR="$ROOT/return_output/environment_reports/$NAME"
mkdir -p "$DIR" || exit 1
probe() {
    printf '\n--- %s ---\n' "$1"
    shift
    "$@" 2>&1
    printf '[exit=%s]\n' "$?"
}
{
    printf 'School SSH diagnostics: %s\n' "$STAMP"
    printf 'External endpoint supplied by user: 10.27.130.23:30403 (not tested from inside container)\n'
    probe identity id
    probe hostname hostname
    probe pid1_name cat /proc/1/comm
    probe pid1_executable readlink /proc/1/exe
    printf '\n--- Relevant process names (no command arguments) ---\n'
    ps -eo pid,ppid,user,comm | awk 'NR==1 || $4 ~ /^(sshd|tini|bash|sh|sleep|python|jupyter)/'
    probe ssh_paths ls -ld /run/sshd /etc/ssh /usr/sbin/sshd /usr/local/bin/start-ssh.sh
    printf '\n--- Host key filenames and permissions only (no key contents) ---\n'
    find /etc/ssh -maxdepth 1 -type f -name 'ssh_host_*_key' -printf '%f mode=%m owner=%u\n' 2>&1
    probe sshd_version /usr/sbin/sshd -V
    probe sshd_config_validation /usr/sbin/sshd -t
    printf '\n--- Effective SSH settings (allowlisted fields only) ---\n'
    /usr/sbin/sshd -T 2> "$DIR/sshd-config-error.log" |
        awk '$1 ~ /^(port|listenaddress|passwordauthentication|pubkeyauthentication|permitrootlogin|permitemptypasswords|authorizedkeysfile|pidfile)$/ {print}'
    printf '[pipeline_exit=%s]\n' "$?"
    cat "$DIR/sshd-config-error.log"
    printf '\n--- Listening sockets and actual local SSH banners ---\n'
    python3 - <<'PY'
import ipaddress, json, os, socket, struct, subprocess
from pathlib import Path

# Startup command may contain passwords. Record executable/known markers only.
try:
    args=Path('/proc/1/cmdline').read_bytes().split(b'\0')
    joined=b' '.join(args)
    markers=['sshd','service ssh','start-ssh.sh','entrypoint.sh','runtime.py','sleep','jupyter','tini']
    print('PID1_MARKERS', json.dumps({m:m.encode() in joined for m in markers}))
except OSError as exc: print('PID1_MARKERS_ERROR',str(exc))
for filename,family in [('/proc/net/tcp',socket.AF_INET),('/proc/net/tcp6',socket.AF_INET6)]:
    try:
        for line in Path(filename).read_text().splitlines()[1:]:
            row=line.split()
            if row[3]!='0A':continue
            address,port=row[1].split(':')
            if family==socket.AF_INET:
                packed=struct.pack('<I',int(address,16))
            else:
                packed=b''.join(struct.pack('<I',int(address[i:i+8],16)) for i in range(0,32,8))
            print('LISTEN',socket.inet_ntop(family,packed),int(port,16))
    except OSError as exc: print('SOCKET_TABLE_ERROR',filename,str(exc))
hosts=['127.0.0.1','::1']
try:
    p=subprocess.run(['hostname','-I'],capture_output=True,text=True,timeout=3)
    for value in p.stdout.split()[:4]:
        ipaddress.ip_address(value)
        if value not in hosts:hosts.append(value)
except (OSError,ValueError,subprocess.TimeoutExpired):pass
ports=[22]
value=os.environ.get('ROOM_SSH_PORT','')
if value.isdigit() and 0<int(value)<=65535 and int(value)!=22:ports.append(int(value))
ready=False
for host in hosts:
    for port in ports:
        try:
            with socket.create_connection((host,port),timeout=2) as s:
                s.settimeout(2)
                banner=s.recv(128).decode(errors='replace').strip()
                is_ssh=banner.startswith('SSH-2.0-')
                ready|=is_ssh
                print('BANNER',host,port,json.dumps(banner),'SSH_OK' if is_ssh else 'NOT_SSH')
        except OSError as exc:print('CONNECT_FAILED',host,port,str(exc))
print('LOCAL_SSH_BANNER_FOUND',ready)
print('NEXT: inspect bind addresses and platform port mapping if SSH is reachable locally; otherwise inspect sshd/config/key errors above.')
PY
} 2>&1 | tee "$DIR/diagnostics.log"
ARCHIVE="$ROOT/return_output/$NAME.tar.gz"
if tar --sort=name --mtime='@0' --owner=0 --group=0 --numeric-owner \
    -C "$(dirname "$DIR")" -cf - "$NAME" | gzip -n > "$ARCHIVE.part"; then
    mv "$ARCHIVE.part" "$ARCHIVE"
    printf '\nRETURN_TAR: %s\nREPORT: %s/diagnostics.log\n' "$ARCHIVE" "$DIR"
else
    printf '\nArchive failed; text report remains at %s/diagnostics.log\n' "$DIR" >&2
    exit 1
fi
