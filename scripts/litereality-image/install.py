"""Image build steps; never run against the user's host Python environment."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from download_artifacts import download

ROOT = Path('/opt/room')
HERE = ROOT / 'image'
PINS = json.loads((HERE / 'pins.json').read_text())
APP = ROOT / 'envs/app/bin/python'
GPU = ROOT / 'envs/gpu/bin/python'


def run(*args, **kw):
    print('+', ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True, **kw)


def core():
    download(PINS['litereality'], ROOT / 'scripts/litereality-agent-prepare/upstream-source.tar.gz')
    run(sys.executable, ROOT / 'scripts/litereality-codex/build_fork.py', ROOT)
    target = ROOT / 'scripts/vendor/litereality-agent-28ffe1f-room-codex-v0.1.2'
    (ROOT / 'app').symlink_to(target)
    env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(APP.parent.parent))
    run('uv', 'sync', '--frozen', '--no-dev', '--python', '/usr/bin/python3.10',
        '--default-index', 'https://pypi.org/simple', '--project', target, env=env)
    constraints = ROOT / 'build-info/app-baseline.txt'
    constraints.parent.mkdir(parents=True, exist_ok=True)
    run(APP, '-c', 'from importlib.metadata import distributions; import sys; '
        'from pathlib import Path; Path(sys.argv[1]).write_text("\\n".join(sorted('
        'd.metadata["Name"]+"=="+d.version for d in distributions()))+"\\n")', constraints)
    run('uv', 'pip', 'install', '--python', APP, '--only-binary', ':all:', '--require-hashes',
        '--index-url', 'https://pypi.org/simple', '--constraint', constraints,
        '--requirements', HERE / 'linux-compatibility.txt')
    run('uv', 'pip', 'check', '--python', APP)
    run(APP, '-c', 'import open3d, fcl, coacd, mujoco; import litereality_agent; print("APP_IMPORT_OK")')
    run(APP, '-m', 'litereality_agent.cli', '--help')
    with (ROOT / 'build-info/app-freeze.txt').open('w') as f:
        run('uv', 'pip', 'freeze', '--python', APP, stdout=f)
    (ROOT / 'scripts/litereality-agent-prepare/upstream-source.tar.gz').unlink()


def tools():
    for name in ('blender', 'codex', 'node'):
        archive = ROOT / ('download-' + name + ('.tar.xz' if name != 'codex' else '.tar.gz'))
        download(PINS[name], archive)
        dest = ROOT / 'tools' / name
        dest.mkdir(parents=True, exist_ok=True)
        cmd = ['tar', '-xf', archive, '-C', dest]
        if name != 'codex':
            cmd += ['--strip-components=1']
        run(*cmd)
        archive.unlink()
    binaries = {'blender': ROOT / 'tools/blender/blender',
                'codex': ROOT / 'tools/codex/codex-x86_64-unknown-linux-musl',
                'node': ROOT / 'tools/node/bin/node', 'npm': ROOT / 'tools/node/bin/npm',
                'npx': ROOT / 'tools/node/bin/npx'}
    # The school base may already supply Node or other CLI binaries. Keep our versions
    # in their own prefix instead of overwriting those preinstalled files.
    (ROOT / 'bin').mkdir(exist_ok=True)
    for name, source in binaries.items():
        if not source.is_file():
            raise RuntimeError(f'Expected binary missing from verified archive: {source}')
        (ROOT / 'bin' / name).symlink_to(source)
    run('blender', '--background', '--factory-startup', '--version')
    run('codex', '--version')
    run('node', '--version')


def gpu_python():
    run('uv', 'venv', '--python', '/usr/bin/python3.10', GPU.parent.parent)
    run('uv', 'pip', 'install', '--python', GPU, '--require-hashes', '--only-binary', ':all:',
        '--index-url', 'https://pypi.org/simple', '--extra-index-url', 'https://download.pytorch.org/whl/cu124',
        '--index-strategy', 'unsafe-best-match', '--requirements', HERE / 'gpu-requirements.lock')
    run('uv', 'pip', 'check', '--python', GPU)
    run(GPU, '-c', 'import torch, torchvision, transformers; '
        'from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor; '
        'assert torch.__version__ == "2.6.0+cu124"; print("GPU_ENV_CPU_IMPORT_OK")')


def clone(name, target):
    item = PINS['native'][name]
    run('git', 'init', target)
    run('git', '-C', target, 'remote', 'add', 'origin', 'https://github.com/' + item['repo'] + '.git')
    run('git', '-C', target, 'fetch', '--depth', '1', 'origin', item['revision'])
    run('git', '-C', target, 'checkout', '--detach', 'FETCH_HEAD')
    actual = subprocess.check_output(['git', '-C', str(target), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != item['revision']:
        raise RuntimeError(f'Git revision mismatch for {name}')
    run('git', '-C', target, 'submodule', 'update', '--init', '--recursive', '--depth', '1')
    with (ROOT / 'build-info' / (name + '-submodules.txt')).open('w') as f:
        run('git', '-C', target, 'submodule', 'status', '--recursive', stdout=f)


def native(name):
    # Compile for explicit architectures without querying a build-time GPU.
    # Link-only CUDA stubs are scoped to this build process, never LD_LIBRARY_PATH at runtime.
    env = dict(os.environ, PATH=str(GPU.parent) + ':' + os.environ['PATH'],
               CUDA_HOME=os.environ['CUDA_HOME'], BUILD_TARGET='cuda',
               LIBRARY_PATH=os.environ['CUDA_HOME'] + '/lib64/stubs:' + os.environ.get('LIBRARY_PATH', ''))
    target = ROOT / 'backends' / ('TRELLIS.2' if name == 'trellis' else name)
    clone(name, target)
    if name == 'trellis':
        return
    run('uv', 'pip', 'install', '--python', GPU, '--no-build-isolation', '--no-deps', target, env=env)
    shutil.rmtree(target / 'build', ignore_errors=True)


def ovoxel():
    env = dict(os.environ, PATH=str(GPU.parent) + ':' + os.environ['PATH'], BUILD_TARGET='cuda',
               LIBRARY_PATH=os.environ['CUDA_HOME'] + '/lib64/stubs:' + os.environ.get('LIBRARY_PATH', ''))
    run('uv', 'pip', 'install', '--python', GPU, '--no-build-isolation', '--no-deps',
        ROOT / 'backends/TRELLIS.2/o-voxel', env=env)
    run('uv', 'pip', 'check', '--python', GPU)
    with (ROOT / 'build-info/gpu-freeze.txt').open('w') as f:
        run('uv', 'pip', 'freeze', '--python', GPU, stdout=f)
    # GPU extension imports can require the host driver, absent on CPU image builders.
    # Verify actual compiled files here; run imports/kernels in room-check on the assigned GPU.
    run(GPU, HERE / 'check_extensions.py', '--files-only')


if __name__ == '__main__':
    if not Path('/.dockerenv').exists() and not Path('/opt/room/image/pins.json').exists():
        raise SystemExit('Image-only installer; do not run on the Mac host')
    actions = {'core': core, 'tools': tools, 'gpu-python': gpu_python, 'o-voxel': ovoxel}
    if sys.argv[1] == 'native':
        native(sys.argv[2])
    else:
        actions[sys.argv[1]]()
