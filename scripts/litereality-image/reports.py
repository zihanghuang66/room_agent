"""Own-output-only reports. Never archive the project, cache or credential directories."""
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tarfile


def redact(text):
    for key, value in os.environ.items():
        if any(x in key.upper() for x in ('TOKEN', 'API_KEY', 'PASSWORD', 'SECRET')) and len(value) >= 8:
            text = text.replace(value, '[REDACTED]')
    return re.sub(r'\b(?:sk-|hf_)[A-Za-z0-9_-]{12,}', '[REDACTED]', text)


def create(prefix):
    root = Path(os.environ.get('ROOM_RETURN_OUTPUT', '/yinxin/projects/room/return_output'))
    name = prefix + '_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + secrets.token_hex(4)
    folder = root / 'environment_reports' / name
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def save(path, data):
    path.write_text(redact(json.dumps(data, ensure_ascii=False, indent=2, default=str)) + '\n')


def archive(folder):
    # Only files created by this run, never following symlinks.
    files = sorted(p for p in folder.rglob('*') if p.is_file() and not p.is_symlink())
    manifest = {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    save(folder / 'manifest.json', manifest)
    files.append(folder / 'manifest.json')
    target = folder.parent.parent / (folder.name + '.tar.gz')
    with target.open('xb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w', format=tarfile.PAX_FORMAT) as tar:
            for p in files:
                info = tar.gettarinfo(str(p), arcname=str(Path(folder.name) / p.relative_to(folder)))
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ''
                info.pax_headers = {}
                with p.open('rb') as f:
                    tar.addfile(info, f)
    print('RETURN TAR:', target, flush=True)
    return target
