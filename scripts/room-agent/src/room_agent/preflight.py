"""Storage compatibility probe; does not promise safety on untested network filesystems."""
import fcntl
from pathlib import Path
import sqlite3
import subprocess
import sys


def storage_probe(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / 'probe.lock'
    code = '''import fcntl,sys
with open(sys.argv[1], 'a') as f:
    try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: sys.exit(0)
sys.exit(1)
'''
    with lock.open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        proc = subprocess.run([sys.executable, '-c', code, str(lock)], timeout=15, capture_output=True)
        if proc.returncode:
            raise RuntimeError('Persistent directory does not enforce cross-process file locks')
    db = directory / 'probe.sqlite'
    code = '''import sqlite3,sys,os
c=sqlite3.connect(sys.argv[1]); c.execute('PRAGMA journal_mode=WAL')
c.execute('CREATE TABLE IF NOT EXISTS probe (n INTEGER)'); c.execute('DELETE FROM probe')
c.execute('INSERT INTO probe VALUES (42)'); c.commit(); os._exit(0)
'''
    subprocess.run([sys.executable, '-c', code, str(db)], timeout=15, check=True, capture_output=True)
    with sqlite3.connect(db) as conn:
        if conn.execute('SELECT n FROM probe').fetchall() != [(42,)]:
            raise RuntimeError('SQLite process-restart persistence check failed')
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('SQLite integrity check failed')
    return {'lock_exclusion': True, 'sqlite_reopen': True,
            'scope': 'same container, cross-process; not a cross-host or power-loss test'}
