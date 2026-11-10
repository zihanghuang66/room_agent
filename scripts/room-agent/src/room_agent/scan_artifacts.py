"""Immutable stage snapshots and bounded presentation files for full-scan graphs."""
import os
import shutil
from pathlib import Path
from uuid import uuid4
from .storage import atomic_json, read_json
from .saved_scene import file_hash


def inventory(root, workspace):
    """Hash files and link text; never follow capture/asset links while copying scenes."""
    root,workspace=Path(root),Path(workspace)
    result={}
    if not root.is_dir() or root.is_symlink():
        raise ValueError('Scene snapshot integrity: missing directory')
    for base,dirs,files in os.walk(root,followlinks=False):
        for name in sorted(dirs+files):
            p=Path(base)/name;rel=p.relative_to(root).as_posix()
            if p.is_symlink():
                target=os.readlink(p)
                logical=workspace/'scenes'/rel
                resolved=(logical.parent/target).resolve()
                if not any(resolved.is_relative_to((workspace/k).resolve()) for k in ('scenes','input')):
                    raise ValueError('Scene link escapes workspace: '+rel)
                result[rel]={'link':target}
            elif p.is_file():result[rel]={'sha256':file_hash(p)}
            elif p.is_dir():result[rel]={'directory':True}
            else:raise ValueError('Unsupported scene artifact: '+rel)
    return result


def copy_scene(source,target,workspace):
    before=inventory(source,workspace)
    required=sum(p.stat().st_size for p in Path(source).rglob('*') if p.is_file() and not p.is_symlink())
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(target.parent).free<required+512*1024**2:
        raise RuntimeError('Insufficient disk space for stage snapshot')
    shutil.copytree(source,target,symlinks=True)
    if inventory(target,workspace)!=before:raise ValueError('Scene copy integrity mismatch')
    return before


def restore_scene(snapshot,workspace,history):
    scene=workspace/'scenes'
    if scene.exists() and snapshot is not None and inventory(scene,workspace)==inventory(snapshot,workspace):
        return
    if scene.exists():
        history.mkdir(parents=True,exist_ok=True)
        scene.rename(history/('scenes-'+uuid4().hex))
    if snapshot is None:scene.mkdir(parents=True,exist_ok=True)
    else:
        temporary=workspace/('scenes.restore-'+uuid4().hex)
        copy_scene(snapshot,temporary,workspace)
        temporary.rename(scene)


def presentation(source,target,stage):
    """Publish only this stage's existing evidence, never relabel old references as new renders."""
    rows=[];source=Path(source);target=Path(target)
    images={'.png','.jpg','.jpeg','.webp'}
    candidates=[]
    for base,dirs,files in os.walk(source,followlinks=False):
        dirs[:]=sorted(d for d in dirs if d not in ('input','capture','captures','tmp','cache','textures','.git','.codex','__pycache__') and not (Path(base)/d).is_symlink())
        for name in sorted(files):
            p=Path(base)/name;rel=p.relative_to(source);parts=rel.parts
            if p.is_symlink() or p.suffix.lower() not in images|{'.glb','.blend'}:continue
            include=(stage=='ingest' and any(x in parts for x in ('object_refs','opening_refs','chair_clusters','nano_banana')) or
                     stage=='reconstruct' and 'reconstructed_objs' in parts or
                     stage=='seed' and 'room_init' in parts and 'room_preview' in parts or
                     stage in ('author','publish','export_check') and 'realism_authoring' in parts and
                     any(x in parts for x in ('room_preview','compare','pairs','surface_ref')))
            if not include:continue
            candidates.append(p)
    # Prefer canonical reference images over auxiliary crops when applying the display cap.
    candidates.sort(key=lambda p:(0 if 'reference' in p.name.lower() else 1,p.as_posix()))
    for p in candidates:
        rel=p.relative_to(source);kind='image' if p.suffix.lower() in images else 'file'
        if sum(r['kind']==kind for r in rows)>=(24 if kind=='image' else 12):continue
        dest=target/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
        rows.append({'relative':rel.as_posix(),'label':p.name,'stage':stage,'kind':kind,'candidate':True})
    return rows
