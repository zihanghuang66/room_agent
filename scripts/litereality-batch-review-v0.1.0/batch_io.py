"""Isolated source checkpoints and timing; original runs are never rewritten."""
import hashlib
import json
import shutil
import time
from contextlib import contextmanager
from pathlib import Path


def save(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.writing')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    tmp.replace(path)


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def source_files(root):
    root=Path(root)
    for p in sorted(root.rglob('*')):
        rel=p.relative_to(root)
        if any(x in ('__pycache__','.git','.codex') for x in rel.parts):continue
        if rel.parts[0] in ('room_preview','repair_preview'):continue
        if p.is_symlink():raise ValueError('Source symlink requires explicit handling: '+str(p))
        if p.is_file():yield p


def source_hashes(root):
    root=Path(root)
    return {str(p.relative_to(root)):digest(p) for p in source_files(root)}


def copy_source(src,dst):
    src,dst=Path(src),Path(dst)
    files=list(source_files(src))
    needed=sum(p.stat().st_size for p in files)
    parent=dst.parent
    while not parent.exists():parent=parent.parent
    if shutil.disk_usage(parent).free < needed+512*1024**2:
        raise RuntimeError('Insufficient disk space for source checkpoint')
    dst.mkdir(parents=True,exist_ok=False)
    for p in files:
        target=dst/p.relative_to(src);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(p,target)
    if source_hashes(src)!=source_hashes(dst):raise RuntimeError('Source checkpoint hash mismatch')


@contextmanager
def timed(folder,label):
    start=time.monotonic();status='completed'
    print('['+label+'] RUNNING',flush=True)
    try:yield
    except BaseException:
        status='failed';raise
    finally:
        row=dict(label=label,seconds=time.monotonic()-start,status=status)
        with (Path(folder)/'timings.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
        print('['+label+'] '+status.upper()+' '+str(round(row['seconds'],1))+'s',flush=True)
