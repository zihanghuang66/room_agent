"""CPU-only postprocessing launcher with isolated dependencies and return archive."""
import argparse
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import traceback
import uuid

PACKAGE = Path(__file__).resolve().parent
SOURCE_NAME = 'room_omega_20261030T134941Z_xtkkor0g'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def pack(report):
    files = sorted(p for p in report.iterdir() if p.is_file() and p.name != 'manifest_sha256.json')
    manifest = report / 'manifest_sha256.json'
    manifest.write_text(json.dumps({p.name:digest(p) for p in files}, indent=2)+'\n')
    archive = report.parent.parent / (report.name+'.tar.gz')
    with archive.open('wb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0, compresslevel=6) as gz:
        with tarfile.open(fileobj=gz, mode='w', format=tarfile.PAX_FORMAT) as tar:
            for path in files+[manifest]:
                info = tarfile.TarInfo(report.name+'/'+path.name)
                info.size = path.stat().st_size
                info.mode = 0o644
                info.mtime = 0
                with path.open('rb') as f:
                    tar.addfile(info, f)
    archive.with_suffix(archive.suffix+'.sha256').write_text(digest(archive)+'  '+archive.name+'\n')
    return archive


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project-root', type=Path, default=Path('/yinxin/projects/room'))
    ap.add_argument('--source', type=Path)
    ap.add_argument('--python', type=Path, help='Use an already prepared Python; skip installation')
    ap.add_argument('--voxel-ratio', type=float, default=.005)
    args = ap.parse_args()
    root = args.project_root.resolve()
    name = 'room_mesh_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8]
    report = root / 'return_output/artifacts' / name
    report.mkdir(parents=True)
    result = dict(status='failed', source=str(args.source), gpu_required=False, model_inference=False)
    env = dict(os.environ, PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
               OMP_NUM_THREADS='4', OPENBLAS_NUM_THREADS='4', MKL_NUM_THREADS='4',
               MPLCONFIGDIR=str(report/'matplotlib_cache'), XDG_CACHE_HOME=str(root/'return_output/cache/omega-mesh'))
    env.pop('PYTHONPATH', None)
    def step(label, command, timeout=1800):
        print(label+'; log: '+str(report/(label+'.log')), flush=True)
        with (report/(label+'.log')).open('w') as log:
            subprocess.run([str(c) for c in command], env=env, stdout=log, stderr=subprocess.STDOUT,
                           check=True, timeout=timeout, cwd=root)
    try:
        source = args.source or root/'return_output/artifacts'/SOURCE_NAME
        if not (source/'predictions.npz').is_file():
            raise FileNotFoundError('Missing '+str(source/'predictions.npz')+'; pass --source to the extracted 100-frame report directory.')
        result['source'] = str(source.resolve())
        python = args.python
        if python is None:
            # Kept under return_output; the original model environment is untouched.
            venv = root/'return_output/environments/omega-mesh-v0.1.0'
            python = venv/'bin/python'
            if not python.is_file():
                step('create_environment', [sys.executable, '-m', 'venv', '--without-pip', venv])
            pip = [sys.executable, '-m', 'pip', '--isolated', '--disable-pip-version-check', '--python', python]
            step('install_dependencies', pip+['install', '--only-binary=:all:', '--index-url', 'https://pypi.org/simple',
                 '--cache-dir', root/'return_output/cache/omega-mesh/pip', '-r', PACKAGE/'requirements.txt'])
            step('pip_check', pip+['check'])
            step('pip_freeze', pip+['freeze', '--all'])
        else:
            step('environment_versions', [python, '-c', 'import numpy,open3d,matplotlib; print(numpy.__version__,open3d.__version__,matplotlib.__version__)'])
        step('mesh', [python, '-B', PACKAGE/'mesh.py', '--source', source,
             '--output', report, '--voxel-ratio', args.voxel_ratio], timeout=7200)
        result.update(status='completed_needs_visual_review', metrics=json.loads((report/'metrics.json').read_text()))
    except Exception as exc:
        result.update(error=str(exc), traceback=traceback.format_exc())
        print('FAILED: '+str(exc), flush=True)
    finally:
        (report/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        (report/'scripts_sha256.json').write_text(json.dumps({p.name:digest(p) for p in PACKAGE.iterdir() if p.is_file()}, indent=2)+'\n')
        shutil.copyfile(PACKAGE/'使用说明.md', report/'使用说明.md')
        print('RETURN ARCHIVE: '+str(pack(report)), flush=True)
    return 0 if result['status']=='completed_needs_visual_review' else 1


if __name__ == '__main__':
    sys.exit(main())
