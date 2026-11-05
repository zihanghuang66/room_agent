"""Small, standard-library-only packaging and input validation helpers."""
import gzip
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import tarfile

REVISION = '28ffe1fd4a1b03c93f1877a33a8bc9b806f72fe1'
SOURCE_SHA = 'b8f70258dda704a38df8091dd4f96604dd4b7a9ab14e296ef4962c84489773b1'
SCAN_NAME = 'LiteReality-LiteReality_Scanner-Studio_2573-20261030-102546.tar'
SCAN_SHA = '2ffbacbe9900751eeb88dce0cc53da330bc2494328e326266b4a33b8aabcfe24'


def executable_path(path):
    # Resolving a venv's Python symlink selects the base interpreter and loses the venv.
    return Path(os.path.abspath(path))


def find_capture(root):
    root=Path(root)
    candidates=[]
    for p in [root]+sorted(p for p in root.rglob('*') if p.is_dir() and len(p.relative_to(root).parts)<=3):
        if list(p.glob('frame_*.json')) and ((p/'room.usdz').is_file() or (p/'roomplan/room.usdz').is_file()):
            candidates.append(p)
    if len(candidates)!=1:raise ValueError('Expected exactly one scanner capture directory; found '+str(len(candidates)))
    return candidates[0]


def frame_files(scan):
    files=sorted(p for p in Path(scan).glob('frame_*.json') if re.fullmatch(r'frame_\d+\.json',p.name))
    if not files:raise ValueError('No per-frame camera JSON files')
    for p in files:
        number=p.stem.removeprefix('frame_')
        if not (p.parent/(p.stem+'.jpg')).is_file() or not (p.parent/('depth_'+number+'.png')).is_file():
            raise ValueError('Missing RGB/depth pair for '+p.name)
    return files


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def extract_checked(archive, output, expected_sha, strip_first=False):
    if digest(archive)!=expected_sha:raise ValueError('Archive SHA256 mismatch: '+Path(archive).name)
    output=Path(output)
    with tarfile.open(archive) as tar:
        members=tar.getmembers(); seen=set(); selected=[]
        if sum(m.size for m in members)>2*1024**3:raise ValueError('Archive too large')
        for m in members:
            p=PurePosixPath(m.name)
            if p.is_absolute() or '..' in p.parts or not(m.isdir() or m.isfile()):
                raise ValueError('Unsafe archive entry')
            parts=p.parts[1:] if strip_first else p.parts
            if not parts:continue
            name='/'.join(parts)
            if name in seen:raise ValueError('Duplicate archive entry')
            seen.add(name);selected.append((m,parts))
        output.mkdir(parents=True,exist_ok=False)
        for m,parts in selected:
            target=output.joinpath(*parts)
            if m.isdir():target.mkdir(parents=True,exist_ok=True)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                with tar.extractfile(m) as src,target.open('wb') as dst:
                    while True:
                        b=src.read(1024*1024)
                        if not b:break
                        dst.write(b)
                target.chmod(0o755 if m.mode&0o111 else 0o644)


def pack_report(report):
    report=Path(report)
    files=sorted(p for p in report.iterdir() if p.is_file() and p.name!='manifest_sha256.json')
    manifest=report/'manifest_sha256.json';save(manifest,{p.name:digest(p) for p in files})
    output=report.parent.parent/(report.name+'.tar.gz')
    with output.open('wb') as raw,gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0,compresslevel=6) as gz:
        with tarfile.open(fileobj=gz,mode='w',format=tarfile.PAX_FORMAT) as tar:
            for p in files+[manifest]:
                info=tarfile.TarInfo(report.name+'/'+p.name);info.size=p.stat().st_size
                info.mode=0o644;info.mtime=0
                with p.open('rb') as f:tar.addfile(info,f)
    output.with_name(output.name+'.sha256').write_text(digest(output)+'  '+output.name+'\n')
    return output
