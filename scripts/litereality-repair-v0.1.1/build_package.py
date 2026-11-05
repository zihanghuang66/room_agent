"""Local release builder: deterministic tar timestamps, only code/docs, no runtimes."""
from pathlib import Path
import gzip
import hashlib
import json
import tarfile

root=Path(__file__).resolve().parent
name='room-litereality-repair-v0.1.1'
files=sorted(p for p in root.rglob('*') if p.is_file() and not p.is_symlink()
    and not any(x in ('__pycache__','.git','.codex') for x in p.relative_to(root).parts)
    and p.suffix not in ('.pyc','.tar','.gz') and p.name!='package_manifest.json')
manifest={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
(root/'package_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');files.append(root/'package_manifest.json')
output=root.parent/'packages'/(name+'.tar.gz');output.parent.mkdir(exist_ok=True)
with output.open('wb') as raw,gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as gz:
    with tarfile.open(fileobj=gz,mode='w') as tar:
        for p in sorted(files):
            info=tarfile.TarInfo(name+'/'+p.relative_to(root).as_posix());info.mtime=0
            info.mode=0o755 if p.suffix=='.sh' else 0o644;info.size=p.stat().st_size
            with p.open('rb') as f:tar.addfile(info,f)
sha=hashlib.sha256(output.read_bytes()).hexdigest()
output.with_name(output.name+'.sha256').write_text(sha+'  '+output.name+'\n')
print(output);print('BYTES',output.stat().st_size);print('SHA256',sha)
