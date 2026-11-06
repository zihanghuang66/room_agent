"""Filesystem persistence and single-writer coordination (local POSIX filesystems)."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from datetime import datetime, timezone
from uuid import uuid4


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', value):
        raise ValueError('invalid identifier')
    return value


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex)
    try:
        with temporary.open('x') as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    return json.loads(Path(path).read_text())


@contextmanager
def room_lock(root, room):
    directory = Path(root) / 'locks'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / (identifier(room) + '.lock')).open('a+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('room is busy: ' + room) from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def room_busy(root, room):
    try:
        with room_lock(root, room):
            return False
    except RuntimeError:
        return True


class Journal:
    """One atomic event file per sequence; no partial JSONL tail after process death."""
    def __init__(self, directory, run_id):
        self.directory = Path(directory) / 'events'
        self.run_id = run_id

    def read(self, after=0):
        return [read_json(p) for p in sorted(self.directory.glob('*.json'))
                if int(p.stem) > after]

    def emit(self, kind, **fields):
        # Caller holds the room lock (or owns a newly created run).
        events = self.read()
        sequence = events[-1]['seq'] + 1 if events else 1
        atomic_json(self.directory / f'{sequence:08d}.json', {
            'seq': sequence, 'run_id': self.run_id, 'kind': kind,
            'time': datetime.now(timezone.utc).isoformat(), **fields,
        })
