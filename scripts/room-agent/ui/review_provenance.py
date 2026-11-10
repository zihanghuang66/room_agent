"""Check whether a saved review still describes the retained source/capture.

Cache file content hashes only while inode, size, mtime and ctime are unchanged.
Inventories and metadata are checked on every projection; execution still uses
its existing full integrity checks independently.
"""
from functools import lru_cache
from pathlib import Path
import os
from room_agent.saved_scene import file_hash, capture_names
from room_agent.revisions import selected_source


def identity(path):
    if path.is_symlink() or not path.is_file():raise ValueError('Review source file missing or linked')
    st=path.stat()
    return st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns


@lru_cache(maxsize=4096)
def content_hash(path,metadata):
    value=file_hash(path)
    if identity(Path(path))!=metadata:raise ValueError('Review source changed while checking')
    return value


def inventory(root):
    root=Path(root)
    if root.is_symlink() or not root.is_dir():raise ValueError('Review source directory missing or linked')
    result={}
    for base,dirs,files in os.walk(root,followlinks=False):
        dirs[:]=[n for n in dirs if n not in ('__pycache__','.git','.codex') and
                 not (Path(base)==root and n in ('room_preview','repair_preview','revision_preview'))]
        for name in dirs+files:
            if Path(base)==root and name in ('room_preview','repair_preview','revision_preview'):continue
            p=Path(base)/name
            if p.is_symlink():raise ValueError('Review source link changed')
            if p.is_file():result[p.relative_to(root).as_posix()]=p
            elif not p.is_dir():raise ValueError('Non-regular review source')
    return result


def matches(service,source_id,request):
    try:
        current=selected_source(service,source_id,hashes=False)
        for key in ('source','seed','capture'):
            if Path(current[key]).absolute()!=Path(request[key]).absolute():return False
            root=Path(current[key])
            paths={n:root/n for n in capture_names(root)} if key=='capture' else inventory(root)
            expected=request[key+'_files']
            if set(paths)!=set(expected):return False
            for name,path in paths.items():
                metadata=identity(path)
                if content_hash(str(path),metadata)!=expected[name] or identity(path)!=metadata:return False
        return True
    except (ValueError,OSError,KeyError,RuntimeError):return False
