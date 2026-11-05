"""Shared stdlib utilities; importing this module never installs or runs a model."""
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tarfile
import tempfile

REVISION = '48b23c8ce72c9a8fcf987f0d7f43d8e4870759f6'
SOURCE_SHA256 = '7ee9d87cb0ab0efc42d9092332d3e0809ef379bafe2c7c293354378a5657793c'
PACKAGE = Path(__file__).resolve().parent


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def new_report(root, category, prefix):
    parent = root / 'return_output' / category
    parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return Path(tempfile.mkdtemp(prefix=prefix + stamp + '_', dir=parent))


def clean_env(root):
    env = os.environ.copy()
    for key in list(env):
        if key.startswith('PIP_') or key in ('PYTHONPATH', 'PYTHONHOME'):
            env.pop(key)
    cache = root / 'return_output' / 'cache' / 'vggt-omega'
    for key, subdir in [('XDG_CACHE_HOME', 'xdg'), ('MPLCONFIGDIR', 'matplotlib'),
                        ('HF_HOME', 'huggingface'), ('TORCH_HOME', 'torch'), ('PIP_CACHE_DIR', 'pip')]:
        path = cache / subdir
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    env.update(PIP_CONFIG_FILE=os.devnull, PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
               OMP_NUM_THREADS='4', OPENBLAS_NUM_THREADS='4', MPLBACKEND='Agg')
    # Keep the scheduler's CUDA_VISIBLE_DEVICES exactly as supplied.
    return env


def run_step(name, command, report, env, timeout=1800):
    log = report / (name + '.log')
    print('Running ' + name + '; log: ' + str(log), flush=True)
    with log.open('w') as output:
        child = subprocess.Popen([str(x) for x in command], stdout=output, stderr=subprocess.STDOUT,
                                 env=env, cwd=report, start_new_session=True)
        try:
            code = child.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
            raise RuntimeError(name + ' interrupted or timed out; see ' + str(log))
    if code:
        raise RuntimeError(name + ' exited ' + str(code) + '; see ' + str(log))


def verify_frames(root, manifest):
    expected = manifest.get('expected_frame_count', len(manifest['selected_frames']))
    if type(expected) is not int or expected < 1 or expected != len(manifest['selected_frames']):
        raise ValueError('Input manifest frame count mismatch')
    frames, seen = [], set()
    for item in manifest['selected_frames']:
        name = item['file']
        if Path(name).name != name or name in ('.', '..') or name in seen:
            raise ValueError('Unsafe or duplicate frame filename: ' + name)
        seen.add(name)
        path = root / name
        if not path.is_file() or path.is_symlink() or sha256(path) != item['sha256']:
            raise ValueError('Missing or modified input frame: ' + str(path))
        frames.append(path)
    if not frames:
        raise ValueError('Empty input manifest')
    return frames


def pack_report(report, return_dir):
    files = sorted(p for p in report.rglob('*') if p.is_file())
    if any(p.is_symlink() for p in report.rglob('*')):
        raise ValueError('Report must not contain symlinks')
    save_json(report / 'manifest_sha256.json', {str(p.relative_to(report)): sha256(p)
              for p in files if p.name != 'manifest_sha256.json'})
    archive = return_dir / (report.name + '.tar.gz')
    temp = archive.with_suffix('.tmp')
    with temp.open('wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as tar:
                for path in sorted(p for p in report.rglob('*') if p.is_file()):
                    info = tarfile.TarInfo(report.name + '/' + path.relative_to(report).as_posix())
                    info.size, info.mode, info.mtime = path.stat().st_size, 0o644, 0
                    with path.open('rb') as stream:
                        tar.addfile(info, stream)
    temp.replace(archive)
    archive.with_name(archive.name + '.sha256').write_text(sha256(archive) + '  ' + archive.name + '\n')
    print('RETURN ARCHIVE: ' + str(archive), flush=True)
    return archive
