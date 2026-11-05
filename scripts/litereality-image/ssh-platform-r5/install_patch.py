"""Register a package-aware SSH bootstrap without baking shared host keys."""
from pathlib import Path
import shutil
import subprocess

real = '/usr/lib/openssh/sshd.room-real'
subprocess.run(['dpkg-divert', '--local', '--rename', '--add', '--divert', real,
                '/usr/sbin/sshd'], check=True)
shutil.copyfile('/opt/room/ssh-platform-r5/sshd-bootstrap.sh', '/usr/sbin/sshd')
Path('/usr/sbin/sshd').chmod(0o755)

# Match the real running executable when stopping/status-checking the service.
# For starts, use the bootstrap as the launch path and the real binary for matching.
service = Path('/etc/init.d/ssh')
lines = service.read_text().splitlines()
changed = 0
for i, line in enumerate(lines):
    if 'start-stop-daemon' in line and '--exec /usr/sbin/sshd' in line:
        line = line.replace('--exec /usr/sbin/sshd', '--exec '+real)
        if '--start ' in line:
            line = line.replace('--exec '+real, '--exec '+real+' --startas /usr/sbin/sshd')
        lines[i] = line
        changed += 1
    if 'status_of_proc' in line:
        lines[i] = line.replace('/usr/sbin/sshd', real)
assert changed >= 6, changed
service.write_text('\n'.join(lines)+'\n')
assert not list(Path('/etc/ssh').glob('ssh_host_*_key'))
