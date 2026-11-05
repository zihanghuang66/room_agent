"""Package only this build's diagnostics; exclude image archives and credentials."""
import datetime
import gzip
import json
from pathlib import Path
import tarfile

root = Path(__file__).resolve().parent.parent
out = root / 'return_output'
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
prefix = 'room_image_build_' + stamp
target = out / (prefix + '.tar.gz')
selected = []
for name in ('build-r1-missing-bwrap.log', 'build-r1.status', 'codex-sandbox-missing-bwrap.log', 'codex-sandbox-smoke.log', 'gpu-smoke-default-failed.log', 'gpu-default-failed.json', 'gpu-smoke.log', 'export-verification.json', 'prepare.status', 'prepare-and-build.log', 'build.log', 'build.status', 'build-original-ubuntu.log', 'build-gpu-download-retries.log',
             'build-final-download-slow.log', 'remaining-wheels.log', 'remaining-wheels-retry.log',
             'gpu-wheelhouse/remaining-receipt.json', 'build-python-cdn-slow.log', 'opencv-ranges.log', 'extra-wheels.log',
             'gpu-wheelhouse/extra-receipt.json', 'build-gpu-cdn-slow.log', 'cudnn-ranges.log', 'gpu-wheelhouse-download.log',
             'gpu-wheelhouse/receipt.json', 'base-manifest.json', 'base-image.txt', 'dockerfile.sha256', 'image-inspect.json'):
    p = out / name
    if p.is_file() and not p.is_symlink():
        selected.append((name, p))
for p in sorted(out.glob('*.docker.tar.gz.sha256')):
    if not p.is_symlink():
        selected.append((p.name, p))
# Whitelist diagnostics; runtime caches can contain root-only thumbnails and
# are neither necessary nor appropriate for a return report.
for name in ('cpu-smoke/return_output/build-info', 'cpu-smoke/return_output/blender-cpu',
             'codex-sandbox-smoke/return_output/sandbox',
             'codex-sandbox-missing-bwrap/return_output/sandbox'):
    for p in sorted((out / name).rglob('*')):
        if p.is_file() and not p.is_symlink() and not any(x.is_symlink() for x in p.parents):
            selected.append((str(p.relative_to(out)), p))
for name in ('cpu-smoke/return_output/dino-worker.json',
             'gpu-smoke/return_output/gpu-imports.json'):
    p = out / name
    if p.is_file() and not p.is_symlink():
        selected.append((name, p))
# Include SSH diagnostics only for the image in the current inspection report.
inspection = out / 'image-inspect.json'
if inspection.is_file():
    current_id = json.loads(inspection.read_text())[0]['Id']
    for result_file in sorted((out / 'ssh_checks').glob('*/result.json')):
        if json.loads(result_file.read_text()).get('image_id') == current_id:
            for p in sorted(result_file.parent.iterdir()):
                if p.is_file() and not p.is_symlink():
                    selected.append((str(p.relative_to(out)), p))
provenance = root / 'scripts/provenance.json'
if provenance.is_file():
    selected.append(('provenance.json', provenance))
part = target.with_name(target.name + '.part')
with part.open('wb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as gz:
    with tarfile.open(fileobj=gz, mode='w') as tar:
        for name, p in selected:
            info = tarfile.TarInfo(prefix + '/' + name)
            info.size = p.stat().st_size
            info.mode = 0o644
            info.uid = info.gid = info.mtime = 0
            with p.open('rb') as f:
                tar.addfile(info, f)
part.replace(target)
print('RETURN TAR:', target)
