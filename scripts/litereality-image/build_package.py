"""Generate a standalone Dockerfile and a reproducible, inspectable source bundle."""
import base64
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
import tarfile

VERSION = '0.1.3'
HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
ROOT = SCRIPTS.parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


def tar_bytes(files):
    raw = io.BytesIO()
    with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as gz:
        with tarfile.open(mode='w', fileobj=gz, format=tarfile.PAX_FORMAT) as tar:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ''
                info.mode = 0o755 if name.endswith('.sh') else 0o644
                tar.addfile(info, io.BytesIO(data))
    return raw.getvalue()


def payload_files():
    names = ['install.py', 'pins.json', 'gpu-requirements.lock', 'runtime.py', 'entrypoint.sh',
             'check.sh', 'check.py', 'check_extensions.py', 'reports.py', 'fetch-models.sh',
             'fetch_models.py', 'room-cli.sh', 'trellis_launcher.py', 'sshd.sh', 'sshd_config',
             'tmux.sh', 'tmux.conf', 'ghostty.terminfo', 'cuda-toolkit.sh', 'download_artifacts.py']
    files = {'image/' + n: (HERE / n).read_bytes() for n in names}
    files['image/linux-compatibility.txt'] = (SCRIPTS / 'litereality-agent-prepare/linux-compatibility.txt').read_bytes()
    for name in ('build_fork.py', 'bridge.py', 'classifier.py'):
        files['scripts/litereality-codex/' + name] = (SCRIPTS / 'litereality-codex' / name).read_bytes()
    for name in ('blender_smoke.py', 'sandbox_probe.py', 'graphics_probe.py'):
        files['image/' + name] = (SCRIPTS / 'codex-integration-check' / name).read_bytes()
    # SSH services can discard Docker ENV. Wrappers restore defaults without replacing
    # explicit user configuration; derive from the same template to avoid path drift.
    runtime_env = (HERE / 'Dockerfile.template').read_text().split('ENV PATH=', 1)[1].split('\nRUN ', 1)[0]
    values = dict(token.split('=', 1) for token in shlex.split(('PATH=' + runtime_env).replace('\\\n', ' ')))
    lines = ['# Generated defaults; no secrets.']
    for name, value in values.items():
        if name in ('PATH', 'PYTHONPATH'):
            continue
        assert not any(c in value for c in '\"`$\\')
        lines.append('export ' + name + '="${' + name + ':-' + value + '}"')
    lines += ['export PATH="/opt/room/envs/app/bin:/opt/room/bin:/opt/room/bootstrap/bin:/usr/local/bin:/usr/local/cuda-12.4/bin:${PATH:-/usr/bin:/bin}"',
              'export CUDA_HOME="/usr/local/cuda-12.4"',
              'export PYTHONPATH="/opt/room/app/src${PYTHONPATH:+:$PYTHONPATH}"']
    files['image/environment.sh'] = ('\n'.join(lines) + '\n').encode()
    return files


def frozen_base_payload():
    previous = SCRIPTS / 'packages/room-litereality-agent-v0.1.2.Dockerfile'
    expected = previous.with_name(previous.name + '.sha256').read_text().split()[0]
    assert sha(previous.read_bytes()) == expected, 'Published base recipe changed'
    block = previous.read_text().split("RUN mkdir -p /opt/room && printf '%s'", 1)[1].split('| base64 -d', 1)[0]
    data = base64.b64decode(''.join(re.findall(r"'([A-Za-z0-9+/=]+)'", block)), validate=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        files = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    return data, files


def install_payload(data, archive_name='payload.tar.gz'):
    encoded = base64.b64encode(data).decode('ascii')
    chunks = [encoded[i:i + 120] for i in range(0, len(encoded), 120)]
    # Classic Docker syntax; no BuildKit-only heredocs, mounts, network flags or extra context.
    install = "RUN mkdir -p /opt/room && printf '%s' \\\n" + ''.join("    '" + chunk + "' \\\n" for chunk in chunks)
    install += f"    | base64 -d > /opt/room/{archive_name} && \\\n"
    install += f"    echo '{sha(data)}  /opt/room/{archive_name}' | sha256sum -c - && \\\n"
    install += f"    tar -xzf /opt/room/{archive_name} -C /opt/room && rm /opt/room/{archive_name}"
    return install


def generate():
    files = payload_files()
    data, base_files = frozen_base_payload()
    assert set(base_files) <= set(files), 'Late overlay cannot delete base files'
    patch_files = {name: content for name, content in files.items() if base_files.get(name) != content}
    patch_data = tar_bytes(patch_files)
    pins = json.loads((HERE / 'pins.json').read_text())
    base = pins['base']['tag']
    if pins['base'].get('amd64'):
        base += '@' + pins['base']['amd64']
    dockerfile = (HERE / 'Dockerfile.template').read_text().replace('@@BASE_IMAGE@@', base)
    dockerfile = dockerfile.replace('@@PAYLOAD_SHA256@@', sha(data)).replace('@@PAYLOAD_INSTALL@@', install_payload(data))
    dockerfile = dockerfile.replace('@@PATCH_INSTALL@@', install_payload(patch_data, 'patch-v' + VERSION + '.tar.gz'))
    assert '@@' not in dockerfile
    (HERE / 'Dockerfile').write_text(dockerfile)
    manifest = {'version': VERSION, 'image_built': False, 'gpu_runtime_tested': False,
                'payload_sha256': sha(data), 'dockerfile_sha256': sha(dockerfile.encode()),
                'base_payload_release': '0.1.2', 'patch_sha256': sha(patch_data),
                'patch_files': {name: sha(content) for name, content in sorted(patch_files.items())},
                'payload_files': {name: sha(content) for name, content in sorted(files.items())}}
    (HERE / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    out = SCRIPTS / 'packages'
    out.mkdir(exist_ok=True)
    standalone = out / f'room-litereality-agent-v{VERSION}.Dockerfile'
    standalone.write_text(dockerfile)
    package_files = {'Dockerfile': dockerfile.encode(), 'manifest.json': (HERE / 'manifest.json').read_bytes()}
    for name in ['使用说明.md', 'Dockerfile.template', 'gpu-requirements.in',
                 'run_after_start.sh', '.dockerignore']:
        package_files[name] = (HERE / name).read_bytes()
    package_files.update({'payload/' + name: content for name, content in files.items()})
    package_files.update({'base_payload/' + name: content for name, content in base_files.items()})
    bundle = out / f'room-litereality-image-v{VERSION}.tar.gz'
    bundle.write_bytes(tar_bytes({f'room-litereality-image-v{VERSION}/' + n: d for n, d in package_files.items()}))
    for path in (bundle, standalone):
        path.with_name(path.name + '.sha256').write_text(sha(path.read_bytes()) + '  ' + path.name + '\n')
        print(path)
    return manifest


if __name__ == '__main__':
    generate()
