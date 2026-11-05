import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

HERE = Path(__file__).resolve().parent
files = {p.name: p.read_bytes() for p in HERE.iterdir()
         if p.is_file() and p.suffix in ('.py', '.sh', '.md') and p.name != 'build.py'}
files['package_manifest.json'] = json.dumps({n: hashlib.sha256(b).hexdigest()
    for n, b in files.items()}, indent=2).encode()
target = HERE.parent / 'packages/room-codex-image-seal-v0.1.0.tar.gz'
with target.open('wb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as gz:
    with tarfile.open(fileobj=gz, mode='w') as t:
        for n, b in sorted(files.items()):
            m = tarfile.TarInfo('room-codex-image-seal-v0.1.0/' + n)
            m.size = len(b); m.mode = 0o755 if n.endswith('.sh') else 0o644; m.mtime = 0
            t.addfile(m, io.BytesIO(b))
digest = hashlib.sha256(target.read_bytes()).hexdigest()
target.with_name(target.name + '.sha256').write_text(digest + '  ' + target.name + '\n')
print(target, target.stat().st_size, digest)
