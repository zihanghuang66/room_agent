"""No-account check that Codex's complete runtime lives inside the image."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

TARGET = Path('/opt/room/tools/codex-complete-0.164.0')
LINKS = [Path('/opt/room/bin/codex'), Path('/usr/local/bin/codex'),
         Path('/opt/room/tools/codex-packaged/bin/codex')]


def image_layer(paths, mountinfo=Path('/proc/self/mountinfo')):
    mounts = []
    for line in mountinfo.read_text().splitlines():
        value = line.split()[4]
        for escaped, decoded in [('\\040', ' '), ('\\011', '\t'), ('\\012', '\n'), ('\\134', '\\')]:
            value = value.replace(escaped, decoded)
        mounts.append(Path(value))
    for path in paths:
        path = Path(path).resolve()
        candidates = [p for p in mounts if path == p or p in path.parents]
        if not candidates or max(candidates, key=lambda p: len(p.parts)) != Path('/'):
            raise RuntimeError('Target is on a separate mount; it may not enter the image: ' + str(path))


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def check():
    image_layer([TARGET, *[p.parent for p in LINKS]])
    manifest = json.loads(Path('/opt/room/build-info/codex-complete-v0.1.0.json').read_text())
    meta = json.loads((TARGET / 'codex-package.json').read_text())
    assert meta['version'] == '0.164.0' and meta['target'] == 'x86_64-unknown-linux-musl'
    for rel, expected in manifest['files'].items():
        path = TARGET / rel
        if path.is_symlink() or sha(path) != expected:
            raise RuntimeError('Installed runtime changed: ' + str(path))
    cli = TARGET / 'bin/codex'
    for link in LINKS:
        if link.resolve() != cli:
            raise RuntimeError('Wrong global Codex entrypoint: ' + str(link))
    results = {}
    for name, cmd in [('cli', [cli, '--version']), ('host', [TARGET / 'bin/codex-code-mode-host', '--help'])]:
        p = subprocess.run(list(map(str, cmd)), capture_output=True, text=True, timeout=30)
        if p.returncode:
            raise RuntimeError(name + ': ' + p.stderr[-1500:])
        results[name] = p.stdout.splitlines()[0]
    results.update(status='passed', cli_path=str(cli), independent_of_project_mount=True,
                   actual_model_execution_tested=False)
    return results


if __name__ == '__main__':
    print(json.dumps(check(), indent=2))
