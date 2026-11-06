"""Content-verified source snapshots; never execute submitted scene Python."""
import hashlib
import os
from pathlib import Path
import shutil
import stat
from uuid import uuid4

from .storage import atomic_json, read_json, sync_dir

MAX_FILES = 10000
MAX_BYTES = 1024 ** 3


def inventory(root):
    root = Path(root)
    if root.is_symlink():
        raise ValueError('symlink source is not supported')
    if not root.is_dir():
        raise ValueError('source directory does not exist')
    files = {}
    total = 0
    for base, dirs, names in os.walk(root, followlinks=False):
        for name in sorted(dirs + names):
            path = Path(base) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ValueError('symlink is not supported: ' + str(path))
            if stat.S_ISDIR(info.st_mode):
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('non-regular source file: ' + str(path))
            total += info.st_size
            if total > MAX_BYTES or len(files) >= MAX_FILES:
                raise ValueError('source exceeds 1 GiB or 10000 files')
            h = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    h.update(chunk)
            files[path.relative_to(root).as_posix()] = {'size': info.st_size, 'sha256': h.hexdigest()}
    if 'Room.py' not in files:
        raise ValueError('source must contain Room.py at its root')
    return dict(sorted(files.items()))


class ArtifactStore:
    def __init__(self, root, request, emit):
        self.request = request
        self.path = Path(root) / 'rooms' / request['room_id'] / 'revisions' / request['run_id']
        self.emit = emit

    def verify(self):
        try:
            if self.path.is_symlink():
                raise ValueError('symlink revision')
            receipt = read_json(self.path / 'manifest.json')
            expected = {'run_id': self.request['run_id'], 'files': self.request['files']}
            if receipt != expected or inventory(self.path / 'source') != self.request['files']:
                raise ValueError('manifest or files differ')
        except (ValueError, OSError) as exc:
            raise ValueError('artifact integrity check failed: ' + str(exc)) from exc

    def snapshot(self):
        if self.path.exists():
            self.verify()
            self.emit('artifact_reused', revision_id=self.request['run_id'])
            return str(self.path)
        source = Path(self.request['source'])
        if inventory(source) != self.request['files']:
            raise ValueError('input changed since request creation; create a new run')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Interrupted temporary directories are never interpreted as published artifacts.
        for stale in self.path.parent.glob('.' + self.path.name + '.tmp-*'):
            if stale.is_dir() and not stale.is_symlink():
                shutil.rmtree(stale)
        temporary = self.path.with_name('.' + self.path.name + '.tmp-' + uuid4().hex)
        try:
            for relative in self.request['files']:
                target = temporary / 'source' / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / relative, target)
                with target.open('rb') as stream:
                    os.fsync(stream.fileno())
            if inventory(temporary / 'source') != self.request['files']:
                raise ValueError('input changed during snapshot')
            atomic_json(temporary / 'manifest.json', {
                'run_id': self.request['run_id'], 'files': self.request['files'],
            })
            for base, _, _ in os.walk(temporary, topdown=False):
                sync_dir(base)
            os.replace(temporary, self.path)
            sync_dir(self.path.parent)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        self.emit('artifact_published', revision_id=self.request['run_id'])
        return str(self.path)
