#!/usr/bin/env python3
"""Install a separate VGGT-Omega environment, without needing model weights."""
import argparse
import json
from pathlib import Path
import platform
import shutil
import sys
import tarfile
import traceback
import urllib.request
from common import PACKAGE, REVISION, SOURCE_SHA256, clean_env, new_report, pack_report, run_step, save_json, sha256


def fetch_source(root):
    cache = root / 'return_output' / 'source_cache'
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / ('vggt-omega-' + REVISION + '.tar.gz')
    if not archive.exists():
        url = 'https://codeload.github.com/facebookresearch/vggt-omega/tar.gz/' + REVISION
        temp = archive.with_suffix('.download')
        print('Downloading pinned official source: ' + url, flush=True)
        with urllib.request.urlopen(url, timeout=120) as source, temp.open('wb') as output:
            shutil.copyfileobj(source, output)
        if sha256(temp) != SOURCE_SHA256:
            raise ValueError('Source download SHA256 mismatch')
        temp.replace(archive)
    if sha256(archive) != SOURCE_SHA256:
        raise ValueError('Cached source SHA256 mismatch: ' + str(archive))
    vendor = root / 'scripts' / 'vendor' / ('vggt-omega-' + REVISION)
    vendor.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        for member in tar:
            parts = Path(member.name).parts
            if not parts or parts[0] != 'vggt-omega-' + REVISION or '..' in parts:
                raise ValueError('Unsafe source archive path')
            if len(parts) < 2 or (parts[1] != 'vggt_omega' and parts[1:] not in [('LICENSE',), ('README.md',), ('pyproject.toml',)]):
                continue
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError('Non-regular source member')
            target = vendor.joinpath(*parts[1:])
            if any(p.is_symlink() for p in [target, *target.parents]):
                raise ValueError('Symlink in source destination')
            data = tar.extractfile(member).read()
            if target.exists() and target.read_bytes() != data:
                raise ValueError('Existing vendor source modified; refusing overwrite: ' + str(target))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return vendor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', default='/yinxin/projects/room')
    args = parser.parse_args()
    root = Path(args.project_root).expanduser().resolve()
    report = new_report(root, 'setup_reports', 'room_omega_setup_')
    result = dict(status='failed', source_revision=REVISION, source_sha256=SOURCE_SHA256,
                  scope='dependency installation and small CUDA operation only; no weights or reconstruction')
    try:
        if platform.system() != 'Linux' or platform.machine() != 'x86_64' or sys.version_info[:2] != (3, 10):
            raise RuntimeError('Use the school container /usr/bin/python3: Linux x86_64 Python 3.10 required')
        env = clean_env(root)
        source = fetch_source(root)
        result['disk_free_bytes_before_install'] = shutil.disk_usage(root).free
        venv = root / '.venvs' / report.name
        venv.parent.mkdir(exist_ok=True)
        python = venv / 'bin' / 'python'
        result.update(python=str(python), source=str(source))
        run_step('create_venv', [sys.executable, '-m', 'venv', '--without-pip', venv], report, env)
        # The school's system pip supports --python; this bootstraps the isolated venv.
        pip = [sys.executable, '-m', 'pip', '--isolated', '--disable-pip-version-check', '--python', python]
        cache_args = ['--cache-dir', root / 'return_output' / 'cache' / 'vggt-omega' / 'pip']
        run_step('install_torch', pip + ['install', *cache_args, '--only-binary=:all:', 'torch==2.6.0',
                 'torchvision==0.21.0', '--index-url', 'https://download.pytorch.org/whl/cu124'], report, env)
        run_step('install_dependencies', pip + ['install', *cache_args, '--only-binary=:all:',
                 '--index-url', 'https://pypi.org/simple', '-r', PACKAGE / 'requirements.txt'], report, env)
        build_source = root / 'return_output' / 'build_cache' / report.name
        shutil.copytree(source, build_source)
        run_step('install_model_source', pip + ['install', '--no-deps', '--no-build-isolation', build_source], report, env)
        run_step('pip_check', pip + ['check'], report, env)
        run_step('pip_freeze', pip + ['freeze', '--all'], report, env)
        # Logical device zero respects the scheduler's visibility restriction.
        probe = ('import json,torch,torchvision,numpy,matplotlib; from vggt_omega.models import VGGTOmega; '
                 'assert torch.cuda.is_available(), "No usable CUDA device"; '
                 'x=torch.ones((32,32),device="cuda:0"); y=x@x; torch.cuda.synchronize(); '
                 'assert float(y[0,0])==32; '
                 'print(json.dumps({"torch":torch.__version__,"cuda":torch.version.cuda,'
                 '"gpu":torch.cuda.get_device_name(0),"total_memory":torch.cuda.get_device_properties(0).total_memory,'
                 '"visible_devices":__import__("os").environ.get("CUDA_VISIBLE_DEVICES")}))')
        run_step('cuda_probe', [python, '-B', '-c', probe], report, env, timeout=120)
        result['status'] = 'ready_without_weights'
        pointer = root / 'return_output' / 'environments'
        pointer.mkdir(exist_ok=True)
        temporary = pointer / (report.name + '.json')
        save_json(temporary, result)
        temporary.replace(pointer / 'omega-current.json')
    except Exception as exc:
        result.update(error=str(exc), traceback=traceback.format_exc())
        print('SETUP FAILED: ' + str(exc), flush=True)
    finally:
        shutil.copy2(PACKAGE / 'requirements.txt', report / 'requirements.txt')
        save_json(report / 'scripts_sha256.json', {p.name: sha256(p) for p in PACKAGE.iterdir() if p.is_file()})
        save_json(report / 'result.json', result)
        pack_report(report, root / 'return_output')
    return 0 if result['status'] == 'ready_without_weights' else 1


if __name__ == '__main__':
    sys.exit(main())
