"""Local artifact/diagnostic checks, explicitly not a Docker or GPU build test."""
import ast
import base64
import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
OUT = ROOT / 'return_output/artifacts/litereality_image_v013_verification'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    result = {'docker_build_executed': False, 'gpu_test_executed': False, 'checks': []}
    builder = module('image_builder', HERE / 'build_package.py')
    manifest = json.loads((HERE / 'manifest.json').read_text())
    dockerfile = (HERE / 'Dockerfile').read_text()
    assert hashlib.sha256(dockerfile.encode()).hexdigest() == manifest['dockerfile_sha256']
    assert not re.search(r'^(COPY|ADD)\s', dockerfile, re.M)
    assert '@@' not in dockerfile
    pins = json.loads((HERE / 'pins.json').read_text())
    assert 'ARG BASE_IMAGE=10.27.130.24:5000/pytorch/pytorch-admin:24.06-py3\n' in dockerfile
    assert not pins['base'].get('amd64'), 'An unverified digest must not be reused for the private image'
    assert 'FROM ${BASE_IMAGE}' in dockerfile and '\nUSER root\n' in dockerfile
    assert 'CUDA_HOME=/usr/local/cuda-12.4' in dockerfile
    toolkit = (HERE / 'cuda-toolkit.sh').read_text()
    assert pins['cuda_toolkit']['keyring']['sha256'] in toolkit
    assert pins['cuda_toolkit']['package'] + '=' + pins['cuda_toolkit']['version'] in toolkit
    result['checks'].append('exact school registry reference, no foreign digest, explicit matching CUDA toolkit')
    payload_block = dockerfile.split("RUN mkdir -p /opt/room && printf '%s'", 1)[1].split('| base64 -d', 1)[0]
    payload = base64.b64decode(''.join(re.findall(r"'([A-Za-z0-9+/=]+)'", payload_block)), validate=True)
    assert hashlib.sha256(payload).hexdigest() == manifest['payload_sha256']
    with tarfile.open(fileobj=io.BytesIO(payload)) as tar:
        actual = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
        assert all(m.mtime == 0 and m.isfile() for m in tar.getmembers())
    base_actual = dict(actual)
    patch_block = dockerfile.split("RUN mkdir -p /opt/room && printf '%s'", 2)[2].split('| base64 -d', 1)[0]
    patch_payload = base64.b64decode(''.join(re.findall(r"'([A-Za-z0-9+/=]+)'", patch_block)), validate=True)
    assert hashlib.sha256(patch_payload).hexdigest() == manifest['patch_sha256']
    with tarfile.open(fileobj=io.BytesIO(patch_payload)) as tar:
        patch_files = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
    assert set(patch_files) == {'image/download_artifacts.py', 'image/install.py', 'image/pins.json', 'image/check.py'}
    actual.update(patch_files)
    assert actual == builder.payload_files()
    previous = (ROOT / 'scripts/packages/room-litereality-agent-v0.1.2.Dockerfile').read_text()
    def successful_layers(text):
        return [s.strip() for s in text.split('RUN python3.10 /opt/room/image/install.py core', 1)[0].splitlines()
                if s.strip() and not s.lstrip().startswith('#')]
    assert successful_layers(previous) == successful_layers(dockerfile)
    result['checks'].append('base plus late overlay match current sources; previously successful build instructions unchanged')
    assert int.from_bytes(payload[4:8], 'little') == 0
    for name, content in actual.items():
        if name.endswith('.py'):
            ast.parse(content.decode(), filename=name, feature_version=(3, 10))
        if name.endswith('.sh'):
            subprocess.run(['bash', '-n'], input=content, check=True)
    for path in HERE.glob('*.py'):
        ast.parse(path.read_text(), filename=str(path), feature_version=(3, 10))
    # Check shell syntax of all real RUN instructions, including the large payload decode.
    lines = dockerfile.replace('\\\n', '').splitlines()
    for line in lines:
        if line.startswith('RUN '):
            subprocess.run(['bash', '-n'], input=line[4:].encode(), check=True)
    result['checks'].append('Python 3.10 grammar and all shell/RUN syntax')
    with tempfile.TemporaryDirectory(dir=OUT, prefix='payload-') as temp:
        temp = Path(temp)
        env_file = temp / 'environment.sh'
        env_file.write_bytes(actual['image/environment.sh'])
        # Simulate an SSH-created shell with almost no Docker environment.
        done = subprocess.run(['bash', '-eu', '-c', 'source "$1"; printf "%s\\n" "$LR_REPO_ROOT" "$PYTHONPATH"', '_', str(env_file)],
                              env={'PATH': '/usr/bin:/bin', 'LR_CODEX_MODEL': 'user-selected-model'},
                              capture_output=True, text=True, check=True)
        assert done.stdout.splitlines() == ['/yinxin/projects/room/return_output/litereality_workspace', '/opt/room/app/src']
        done = subprocess.run(['bash', '-eu', '-c', 'source "$1"; printf "%s" "$LR_CODEX_MODEL"', '_', str(env_file)],
                              env={'PATH': '/usr/bin:/bin', 'LR_CODEX_MODEL': 'user-selected-model'},
                              capture_output=True, text=True, check=True)
        assert done.stdout == 'user-selected-model'
        done = subprocess.run(['bash', '-eu', '-c', 'source "$1"; printf "%s\\n" "$CUDA_HOME" "$PATH"', '_', str(env_file)],
                              env={'PATH': '/usr/bin:/bin'}, capture_output=True, text=True, check=True)
        assert done.stdout.splitlines()[0] == '/usr/local/cuda-12.4'
        assert done.stdout.splitlines()[1].startswith('/opt/room/envs/app/bin:/opt/room/bin:')
    result['checks'].append('SSH environment restoration preserves explicit overrides')
    with tempfile.TemporaryDirectory(prefix='room-terminal-') as temp:
        temp = Path(temp)
        termdb = temp / 'terminfo'
        termdb.mkdir()
        subprocess.run(['tic', '-x', '-o', str(termdb), str(HERE / 'ghostty.terminfo')], check=True)
        terminal = subprocess.check_output(['infocmp', '-x', '-A', str(termdb), 'xterm-ghostty'], text=True)
        assert 'xterm-ghostty' in terminal and 'colors#' in terminal
        key = temp / 'test_host_ed25519'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
        # Local OpenSSH parser validation; never start a listening daemon.
        config = temp / 'sshd_config'
        original = (HERE / 'sshd_config').read_text()
        config.write_text('\n'.join(line for line in original.splitlines() if not line.startswith('HostKey '))
                          + '\nHostKey ' + str(key) + '\n')
        subprocess.run(['/usr/sbin/sshd', '-t', '-f', str(config)], check=True)
        effective = subprocess.check_output(['/usr/sbin/sshd', '-T', '-f', str(config)], text=True)
        assert 'passwordauthentication no' in effective and 'pubkeyauthentication yes' in effective
        assert 'permitrootlogin without-password' in effective or 'permitrootlogin prohibit-password' in effective
    result['checks'].append('Ghostty terminfo compile/read round trip and local OpenSSH config validation (no listener)')
    package = ROOT / 'scripts/packages/room-litereality-image-v0.1.3.tar.gz'
    with tarfile.open(package) as tar:
        members = tar.getmembers()
        assert all(m.mtime == 0 and m.isfile() and not Path(m.name).is_absolute() and '..' not in Path(m.name).parts for m in members)
        bundled = tar.extractfile('room-litereality-image-v0.1.3/Dockerfile').read()
        assert bundled == dockerfile.encode()
        for name, content in actual.items():
            assert tar.extractfile('room-litereality-image-v0.1.3/payload/' + name).read() == content
    assert int.from_bytes(package.read_bytes()[4:8], 'little') == 0
    assert builder.tar_bytes(base_actual) == payload
    assert builder.tar_bytes(patch_files) == patch_payload
    result['checks'].append('reproducible archive metadata and readable payload equivalence')
    # Apply the real Codex fork to the real pinned LiteReality archive in isolation.
    with tempfile.TemporaryDirectory(dir=OUT, prefix='fork-') as temp:
        temp = Path(temp)
        archive = temp / 'scripts/litereality-agent-prepare/upstream-source.tar.gz'
        archive.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'scripts/litereality-agent-prepare/upstream-source.tar.gz', archive)
        fork_builder = module('fork_builder', ROOT / 'scripts/litereality-codex/build_fork.py')
        fork = fork_builder.build(temp)
        generated = json.loads((fork / 'room_codex_patch.json').read_text())
        existing = json.loads((ROOT / 'scripts/vendor' / fork_builder.FORK / 'room_codex_patch.json').read_text())
        assert generated == existing
    result['checks'].append('real pinned upstream patched and compiled with identical v0.1.2 manifest')
    sys.path.insert(0, str(HERE))
    import reports
    import check
    with tempfile.TemporaryDirectory(dir=OUT, prefix='failed-run-') as temp:
        # Failed initialization must still print the error and produce a valid return tar.
        with patch.dict(os.environ, {'ROOM_RETURN_OUTPUT': temp, 'FAKE_API_KEY': 'test-secret-123456'}), \
             patch.object(check, 'initialize', side_effect=RuntimeError('test-secret-123456 failure fixture')):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = check.main()
            assert code == 1 and 'RETURN TAR:' in output.getvalue()
            assert 'test-secret-123456' not in output.getvalue()
            report_tar = next(Path(temp).glob('room_image_check_*.tar.gz'))
            with tarfile.open(report_tar) as tar:
                for m in tar.getmembers():
                    assert m.mtime == 0
                    assert b'test-secret-123456' not in tar.extractfile(m).read()
        # Reports must not follow a symlink to an unrelated file.
        with patch.dict(os.environ, {'ROOM_RETURN_OUTPUT': temp}):
            folder = reports.create('test_symlink_exclusion')
            (folder / 'unrelated').symlink_to(HERE / 'pins.json')
            reports.save(folder / 'result.json', {'ok': True})
            report_tar = reports.archive(folder)
            with tarfile.open(report_tar) as tar:
                assert not any(m.name.endswith('unrelated') for m in tar.getmembers())
    result['checks'].append('fatal error printed, redacted, nonzero exit and valid return tar; symlinks excluded')
    result['status'] = 'local_checks_passed_image_build_pending'
    (OUT / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
