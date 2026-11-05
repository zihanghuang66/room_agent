"""Reproducible delivery archive; no binaries, model weights or scene assets."""
import gzip
import hashlib
import json
import shutil
import tarfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent
SRC=ROOT/'litereality-batch-review-v0.1.0'
NAME='room-litereality-batch-review-v0.1.0'
FILES=('batch_core.py','batch_io.py','agent_session.py','worker.py','run_batches.py','build_saved.py',
       'run.sh','run_room_004.sh','full_support.py','common.py','runtime.py','verify_export.py',
       'texture_rebase.py','app.tar.gz','app.sha256','使用说明.md')

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    (SRC/'tests').mkdir(exist_ok=True)
    for n in ('test_batch_review.py','test_batch_integration.py'):
        shutil.copy2(ROOT/'tests'/n,SRC/'tests'/n)
    files=[SRC/n for n in FILES]+sorted((SRC/'tests').glob('test_*.py'))
    manifest=dict(version='0.1.0',upstream_revision='28ffe1fd4a1b03c93f1877a33a8bc9b806f72fe1',
        app_sha256=sha(SRC/'app.tar.gz'),files={p.relative_to(SRC).as_posix():sha(p) for p in files})
    mp=SRC/'package_manifest.json';mp.write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n')
    files.append(mp)
    dest=ROOT/'packages'/(NAME+'.tar.gz');dest.parent.mkdir(exist_ok=True)
    with dest.open('wb') as raw,gzip.GzipFile(fileobj=raw,filename='',mode='wb',mtime=0) as gz:
        with tarfile.open(fileobj=gz,mode='w') as tar:
            for p in sorted(files):
                info=tarfile.TarInfo(NAME+'/'+p.relative_to(SRC).as_posix())
                info.size=p.stat().st_size;info.mode=0o755 if p.suffix=='.sh' else 0o644;info.mtime=0
                with p.open('rb') as f:tar.addfile(info,f)
    dest.with_name(dest.name+'.sha256').write_text(sha(dest)+'  '+dest.name+'\n')
    print(dest);print('BYTES',dest.stat().st_size);print('SHA256',sha(dest))

if __name__=='__main__':main()
