#!/usr/bin/env python3
"""Collect existing frames, database and sparse models; no GPU or reconstruction. Stop jobs first."""
import argparse
from collections import Counter
from contextlib import closing
import datetime
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import struct
import tarfile
import tempfile

PAIR_BASE = 2147483647


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()


def database_summary(path):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        images=[{'id':int(i),'name':n,'camera_id':int(c),'keypoints':int(k or 0)}
                for i,n,c,k in db.execute('SELECT i.image_id,i.name,i.camera_id,k.rows FROM images i LEFT JOIN keypoints k ON i.image_id=k.image_id ORDER BY i.name')]
        raw=dict(db.execute('SELECT pair_id,rows FROM matches'))
        pairs=[]
        for pair_id,n,config in db.execute('SELECT pair_id,rows,config FROM two_view_geometries ORDER BY pair_id'):
            a,b=divmod(pair_id,PAIR_BASE)
            pairs.append({'a':a,'b':b,'raw_matches':raw.get(pair_id),'inliers':n,'geometry_config':config})
        cameras=[]
        for i,m,w,h,blob,prior in db.execute('SELECT camera_id,model,width,height,params,prior_focal_length FROM cameras ORDER BY camera_id'):
            cameras.append({'id':i,'model_id':m,'width':w,'height':h,'params':list(struct.unpack('<'+'d'*(len(blob)//8),blob)), 'prior_focal_length':bool(prior)})
    lookup={(x['a'],x['b']):x for x in pairs}
    adjacent=[]
    for left,right in zip(images,images[1:]):
        key=tuple(sorted((left['id'],right['id'])))
        pair=lookup.get(key)
        adjacent.append({'left':left['name'],'right':right['name'],'geometry_record_exists':pair is not None,
                         'inliers':pair['inliers'] if pair else None,
                         'geometry_config':pair['geometry_config'] if pair else None})
    graph={im['id']:set() for im in images}
    for pair in pairs:
        if pair['inliers']>=15 and pair['a'] in graph and pair['b'] in graph:
            graph[pair['a']].add(pair['b']); graph[pair['b']].add(pair['a'])
    unseen=set(graph); components=[]
    while unseen:
        start=min(unseen); unseen.remove(start); group=[start]; queue=[start]
        while queue:
            for n in sorted(graph[queue.pop()] & unseen):
                unseen.remove(n); group.append(n); queue.append(n)
        components.append(sorted(group))
    components.sort(key=lambda x:(-len(x),x))
    return {'images':images,'cameras':cameras,'pairs':pairs,'adjacent_pairs':adjacent,
            'components_at_15_inliers':components,
            'nonzero_pair_geometry_configs':dict(Counter(str(x['geometry_config']) for x in pairs if x['inliers']>0)),
            'note':'Graph connectivity is diagnostic only, not proof of valid 3D geometry. Config codes retained from database; missing pairs are distinct from zero-inlier pairs.'}


def collect(root, source, max_bytes):
    root=Path(root).expanduser().resolve(); source=Path(source).expanduser().resolve()
    if not root.is_dir() or not source.is_dir() or root not in source.parents:
        raise ValueError('Run directory must exist inside the project root')
    files=[]
    for name in ('database.db','config.json','frames','sparse','report'):
        base=source/name
        if not base.exists() or base.is_symlink(): raise ValueError('Missing or symlink source: '+str(base))
        candidates=sorted(base.rglob('*')) if base.is_dir() else [base]
        for p in candidates:
            if p.is_symlink(): raise ValueError('Refusing symlink: '+str(p))
            if p.is_file(): files.append(p)
            elif not p.is_dir(): raise ValueError('Unsupported entry: '+str(p))
    if not list((source/'frames').glob('*.jpg')): raise ValueError('No extracted JPEG frames')
    total=sum(p.stat().st_size for p in files)
    if total>max_bytes: raise ValueError('Input exceeds package limit: '+str(total)+' bytes; use --max-mib explicitly if appropriate')
    if shutil.disk_usage(root).free<3*total+100*1024*1024: raise ValueError('Insufficient free disk space for snapshot and archive')
    returns=root/'return_output'; returns.mkdir(exist_ok=True)
    parent=returns/'diagnostics_reports'; parent.mkdir(exist_ok=True)
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    snapshot=Path(tempfile.mkdtemp(prefix='room_diagnostics_'+stamp+'_',dir=parent))
    print('Collecting existing run: '+str(source),flush=True)
    for p in files:
        target=snapshot/'run'/p.relative_to(source); target.parent.mkdir(parents=True,exist_ok=True)
        if p.name=='database.db' and p.parent==source:
            with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)) as src:
                with closing(sqlite3.connect(target)) as dst:
                    src.backup(dst)
                    if dst.execute('PRAGMA quick_check').fetchone()[0]!='ok': raise ValueError('Database integrity check failed')
        else:
            shutil.copyfile(p,target)
            if digest(p)!=digest(target): raise ValueError('Source changed during copy; stop jobs and retry: '+str(p))
    dump(snapshot/'database_summary.json',database_summary(snapshot/'run/database.db'))
    dump(snapshot/'collection.json',{'source_run':str(source),'scope':'Existing extracted frames, SQLite snapshot, sparse models and reports. No original video, no GPU computation, no new reconstruction.',
         'source_file_count':len(files),'source_bytes':total,'collector_sha256':digest(Path(__file__))})
    (snapshot/'README.md').write_text('# Room diagnostic evidence\n\nNo new reconstruction was run. Models may be invalid or disconnected.\nRead database_summary.json and run/report/. Frames and sparse models are included for local diagnosis.\nThe SQLite database is a consistent backup; source files are retained.\nOld absolute paths in config/report files are historical; local reruns require new paths.\n')
    inventory=[{'path':str(p.relative_to(snapshot)),'bytes':p.stat().st_size,'sha256':digest(p)} for p in sorted(snapshot.rglob('*')) if p.is_file()]
    dump(snapshot/'manifest.json',inventory)
    if sum(x['bytes'] for x in inventory)>max_bytes: raise ValueError('Snapshot exceeds size limit; retained at '+str(snapshot))
    archive=returns/(snapshot.name+'.tar.gz'); temporary=archive.with_suffix(archive.suffix+'.partial')
    try:
        with temporary.open('wb') as raw:
            with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as compressed:
                with tarfile.open(fileobj=compressed,mode='w') as t:
                    for p in sorted(snapshot.rglob('*')):
                        if not p.is_file():continue
                        info=tarfile.TarInfo(snapshot.name+'/'+p.relative_to(snapshot).as_posix())
                        info.size=p.stat().st_size; info.mtime=0; info.mode=0o644
                        with p.open('rb') as f:t.addfile(info,f)
        temporary.replace(archive)
    finally:
        if temporary.exists():temporary.unlink()
    archive.with_name(archive.name+'.sha256').write_text(digest(archive)+'  '+archive.name+'\n')
    print('Archive size: %.1f MiB'%(archive.stat().st_size/1024**2),flush=True)
    print('Return this archive: '+str(archive),flush=True)
    return archive


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root',default='/yinxin/projects/room')
    parser.add_argument('--run-dir',required=True)
    parser.add_argument('--max-mib',type=int,default=512)
    args=parser.parse_args()
    if not 1<=args.max_mib<=4096:parser.error('--max-mib must be 1..4096')
    try:collect(args.project_root,args.run_dir,args.max_mib*1024**2)
    except Exception as exc:parser.exit(1,type(exc).__name__+': '+str(exc)+'\n')
