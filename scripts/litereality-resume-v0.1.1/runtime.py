"""Install verified full runtime beside this package, without replacing global Codex/auth."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def install_archive(archive, target, expected):
    archive, target = Path(archive), Path(target)
    if digest(archive) != expected:
        raise RuntimeError('Payload SHA256 mismatch: ' + str(archive))
    if target.is_symlink():
        raise RuntimeError('Refusing symlink installation target: ' + str(target))
    with tarfile.open(archive) as t:
        members = t.getmembers()
        for m in members:
            p = PurePosixPath(m.name)
            if p.is_absolute() or '..' in p.parts or not (m.isdir() or m.isfile()):
                raise RuntimeError('Unsafe archive member: ' + m.name)
        if not target.exists():
            stage = Path(tempfile.mkdtemp(prefix=target.name + '-install-', dir=target.parent))
            try:
                for m in members:
                    out = stage / m.name
                    if m.isdir():
                        out.mkdir(parents=True, exist_ok=True)
                    else:
                        out.parent.mkdir(parents=True, exist_ok=True)
                        with t.extractfile(m) as src, out.open('wb') as dest:
                            shutil.copyfileobj(src, dest)
                        out.chmod(0o755 if m.mode & 0o111 else 0o644)
                stage.rename(target)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        for m in members:
            p = target / m.name
            relative = p.relative_to(target)
            if any(target.joinpath(*relative.parts[:i]).is_symlink() for i in range(len(relative.parts) + 1)):
                raise RuntimeError('Symlink in installed payload: ' + str(p))
            if m.isfile():
                with t.extractfile(m) as f:
                    h = hashlib.sha256()
                    for block in iter(lambda: f.read(1024 * 1024), b''):
                        h.update(block)
                if not p.is_file() or digest(p) != h.hexdigest():
                    raise RuntimeError('Installed payload changed: ' + str(p))
                if m.mode & 0o111 and not os.access(p, os.X_OK):
                    raise RuntimeError('Installed executable lacks execute permission: ' + str(p))
    return target


def prepare(env, report):
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
        raise RuntimeError('This repair runtime requires Linux x86_64')
    pins = json.loads((HERE / 'repair_pins.json').read_text())
    runtime = install_archive(HERE / 'codex-runtime.tar.gz', HERE / 'runtime', pins['runtime_sha256'])
    meta = json.loads((runtime / 'codex-package.json').read_text())
    if meta['version'] != pins['runtime_version'] or meta['target'] != pins['runtime_target']:
        raise RuntimeError('Unexpected Codex package version/platform')
    cli, host = runtime / 'bin/codex', runtime / 'bin/codex-code-mode-host'
    env['ROOM_CODEX'] = str(cli)
    env['PATH'] = str(cli.parent) + os.pathsep + env['PATH']
    results = {}
    for name, cmd in [('version', [cli, '--version']), ('code_mode_host', [host, '--help'])]:
        proc = subprocess.run(list(map(str, cmd)), env=env, capture_output=True, text=True, timeout=30)
        results[name] = dict(returncode=proc.returncode, stdout=proc.stdout[-2000:], stderr=proc.stderr[-2000:])
        if proc.returncode:
            raise RuntimeError(name + ' failed: ' + proc.stderr[-2000:])
    results.update(status='passed', cli=str(cli), host=str(host), archive_sha256=pins['runtime_sha256'],
                   cli_sha256=digest(cli), host_sha256=digest(host), actual_agent_execution_tested=False)
    Path(report).parent.mkdir(parents=True, exist_ok=True)
    Path(report).write_text(json.dumps(results, indent=2))
    print('CODEX_RUNTIME_PASS ' + str(cli), flush=True)
    return cli


def prepare_app():
    pins = json.loads((HERE / 'repair_pins.json').read_text())
    return install_archive(HERE / 'app.tar.gz', HERE / 'app', pins['app_sha256'])
