"""Diagnostic/export archives, never account files, raw captures or model caches."""
import gzip
import os
from pathlib import Path
import tarfile
from uuid import uuid4

from .storage import atomic_json, room_lock
from .saved_scene import file_hash


def make_archive(directory, destination, *, skip_trees=()):
    directory, destination = Path(directory).resolve(), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + '.' + uuid4().hex + '.tmp')
    allowed = {'.json', '.jsonl', '.log', '.txt', '.md', '.py', '.png', '.jpg', '.jpeg',
               '.webp', '.glb', '.blend', '.exr', '.hdr', '.bin', '.gltf'}
    excluded = {'input', '.codex', '.git', '__pycache__', 'tmp', 'cache', 'initial', 'node_modules'}
    try:
        with temp.open('wb') as raw, gzip.GzipFile(fileobj=raw, filename='', mode='wb', mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode='w') as archive:
                for base, dirs, files in os.walk(directory, followlinks=False):
                    dirs[:] = sorted(d for d in dirs if (Path(base)/d).relative_to(directory).as_posix() not in skip_trees and d not in excluded and not (Path(base)/d).is_symlink()
                                     and not (d == 'work' and Path(base) != directory))
                    for name in sorted(files):
                        path = Path(base) / name
                        if (path.is_symlink() or path.suffix.lower() not in allowed or
                            name in ('auth.json', 'credentials.json', 'config.toml') or name.startswith('.env')):
                            continue
                        info = tarfile.TarInfo(directory.name + '/' + path.relative_to(directory).as_posix())
                        info.size, info.mode, info.mtime = path.stat().st_size, 0o644, 0
                        with path.open('rb') as f:
                            archive.addfile(info, f)
        os.replace(temp, destination)
        destination.with_name(destination.name + '.sha256').write_text(file_hash(destination) + '  ' + destination.name + '\n')
        return destination
    finally:
        temp.unlink(missing_ok=True)


def package_return(service, run_id):
    request, directory = service._load(run_id)
    # Exclusion uses the same lock as writers, including CLI workers outside the web server.
    with room_lock(service.root, request['room_id']):
        atomic_json(directory / 'result.json', service.show(run_id))
        project = Path(request.get('project', service.root.parent.parent))
        scan = request.get('workflow_version') == 'full-scan-graph/1'
        prefix = 'room_agent_stage4a' if request['operation'] == 'scene_edit' else 'room_agent_stage3a' if scan else 'room_agent_stage2'
        dest = project / 'return_output' / f'{prefix}_{run_id}.tar.gz'
        skip = ['work/failed'] + [str(p.relative_to(directory)) for p in (directory/'published').glob('*/scenes')] if scan else []
        return make_archive(directory, dest, skip_trees=skip)
