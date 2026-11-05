"""Apply a small SSH-only patch on the verified r3 image."""
from pathlib import Path
import shutil

source = Path('/opt/room/ssh-platform-r4')
for name in ('entrypoint.sh', 'sshd.sh', 'ssh-prepare.sh'):
    target = Path('/opt/room/image') / name
    shutil.copyfile(source / name, target)
    target.chmod(0o755)
shutil.copyfile(source / 'sshd_config', '/etc/ssh/sshd_config')
alias = Path('/usr/local/bin/start-ssh.sh')
alias.symlink_to('/opt/room/image/sshd.sh')
Path('/run/sshd').mkdir(mode=0o755, parents=True, exist_ok=True)

# SysV service must initialize keys even when the platform replaces ENTRYPOINT.
service = Path('/etc/init.d/ssh')
text = service.read_text()
anchor = 'case "$1" in\n  start)'
assert text.count(anchor) == 1
text = text.replace(anchor, '''case "$1" in
  start|restart|try-restart)
    /opt/room/image/ssh-prepare.sh
    unset ROOT_PASSWORD SSH_PASSWORD
    ;;
esac

case "$1" in
  start)''')
service.write_text(text)

# No SSH host private keys or credentials are added to the image.
assert not list(Path('/etc/ssh').glob('ssh_host_*_key'))
