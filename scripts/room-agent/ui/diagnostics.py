"""Small, bounded diagnostic archive. No mutation of task or scene files."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from display import build_index, safe

TEXT = {'.json', '.jsonl', '.log', '.txt', '.md', '.py'}
PRIVATE = {'auth.json', 'credentials.json', 'config.toml'}
REPORTS = {'author_acceptance.json', 'assessment.json', 'visual_review.json', 'validation.json', 'stage_author.json', 'direct_result.json', 'request.json', 'input.json', 'input.sha256.json', 'result.json'}


def make_diagnostics(root, destination, status=None, *, scheduler=None, max_bytes=24*1024**2, per_file=512*1024):
    root, destination = Path(root).absolute(), Path(destination)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Expected an existing non-symlink task directory')
    status = status or {}
    if status.get('execution_status') in {'running', 'queued', 'dispatching'}:
        raise ValueError('Wait until the task stops before collecting diagnostics')
    entries, omitted, candidates = [], [], []
    # Traverse once, omit snapshots and duplicate geometry; report omissions.
    for directory, dirs, files in os.walk(root, followlinks=False):
        folder = Path(directory)
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d.lower() not in {'input','capture','captures','textures','materials','object','objects','reconstructed_objs','node_modules','cache','tmp','__pycache__'} and not (folder/d).is_symlink() and not (folder/d).relative_to(root).as_posix().startswith(('published/', 'work/failed/')))
        for name in files:
            path=folder/name;rel=path.relative_to(root).as_posix()
            if not safe(root,path) or name in PRIVATE or name.startswith('.env'):
                continue
            if path.suffix.lower() in TEXT:
                priority=0 if name in REPORTS else 1 if path.suffix=='.log' or name=='events.jsonl' else 2
                candidates.append((priority,-path.stat().st_mtime_ns,rel,path))
            elif len(omitted)<1000:
                omitted.append({'path':rel,'reason':'not diagnostic text/image selection','bytes':path.stat().st_size})
    total=0
    content=[]
    for _,_,rel,path in sorted(candidates)[:1000]:
        size=path.stat().st_size
        limit=min(max_bytes-total,per_file)
        if limit<=0 or size>limit and path.suffix.lower() not in {'.log','.jsonl','.txt'}:
            if len(omitted)<1000:omitted.append({'path':rel,'reason':'size limit','bytes':size})
            continue
        with path.open('rb') as f:
            if size>limit:
                f.seek(size-limit)
            data=f.read(limit)
        truncated=size>limit
        if truncated:
            data=b'[Earlier content omitted; tail follows]\n'+data
            data=data[-limit:]
        name=rel+'.tail.txt' if truncated else rel
        content.append((name,data));total+=len(data)
        entries.append({'source':rel,'path':name,'bytes':len(data),'original_bytes':size,'truncated':truncated})
    # A small selection of real camera comparisons, never textures or all meshes.
    index=build_index(root,status,include_drafts=True)
    for row in (index.get('comparisons') or [{}])[0].get('pairs',[])[:6]:
        path=root/row['path'];size=path.stat().st_size
        if size>2*1024**2 or total+size>max_bytes:
            omitted.append({'path':row['path'],'reason':'image budget','bytes':size});continue
        data=path.read_bytes();content.append((row['path'],data));total+=len(data)
        entries.append({'source':row['path'],'path':row['path'],'bytes':len(data),'truncated':False})
    manifest={'kind':'diagnostics-only','not_a_restorable_scene':True,'content_bytes':total,'content_budget':max_bytes,'entries':entries,'omitted':omitted,'limits':'Up to 1000 text files, 6 paired views. Logs may be tailed; large structured files omitted. Full archive and server task remain unchanged.'}
    if scheduler is not None:
        content.append(('queue_snapshot.json',json.dumps(scheduler,ensure_ascii=False,indent=2).encode()))
    content.extend([('diagnostics_manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2).encode()),('diagnostics_status.json',json.dumps(status,ensure_ascii=False,indent=2).encode())])
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_name(destination.name+'.'+uuid4().hex+'.tmp')
    try:
        with temporary.open('wb') as raw,gzip.GzipFile(fileobj=raw,filename='',mode='wb',mtime=0) as zipped,tarfile.open(fileobj=zipped,mode='w') as archive:
            for name,data in content:
                info=tarfile.TarInfo(root.name+'/'+name);info.size=len(data);info.mode=0o600;info.mtime=0
                archive.addfile(info,io.BytesIO(data))
        os.replace(temporary,destination)
        destination.with_name(destination.name+'.sha256').write_text(hashlib.sha256(destination.read_bytes()).hexdigest()+'  '+destination.name+'\n')
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def collect_for_service(service, run_id, output_dir=None):
    from room_agent.storage import room_lock
    request,root=service._load(run_id)
    project=Path(request.get('project',service.root.parent.parent))
    destination=Path(output_dir) if output_dir is not None else project/'return_output'
    with room_lock(service.root,request['room_id']):
        scheduler={'note':'Persisted scheduler snapshot; a running queue entry can include diagnostic finalization.'}
        for key,path in [('entry',service.root/'queue'/(run_id+'.json')),('error',service.root/'queue_error.json')]:
            if path.is_file() and not path.is_symlink() and not path.parent.is_symlink() and path.stat().st_size<=65536:
                try:scheduler[key]=json.loads(path.read_text())
                except (OSError,ValueError):scheduler[key]={'unreadable':True}
        return make_diagnostics(root,destination/('room_agent_diagnostics_'+run_id+'.tar.gz'),service.show(run_id),scheduler=scheduler)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--state-root',required=True,type=Path)
    parser.add_argument('--output-dir',required=True,type=Path)
    parser.add_argument('run_id')
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.run_id):raise ValueError('Invalid run ID')
    from room_agent.service import Service
    service=Service(args.state_root)
    from session_edits import SessionEdits
    request,_=service._load(args.run_id)
    session=None if request.get('session_id') else SessionEdits(service,object()).read(args.run_id)
    result=collect_session(service,args.run_id,session,args.output_dir)
    print('DIAGNOSTICS_TAR: '+str(result),flush=True)
    print('SIZE_BYTES: '+str(result.stat().st_size),flush=True)



def collect_session(service,run_id,session,output_dir=None):
    """Small session bundle: durable conversation + latest operation diagnostics."""
    parent=collect_for_service(service,run_id,output_dir)
    active=(session or {}).get('active')
    if not active:return parent
    child=collect_for_service(service,active['run_id'],output_dir)
    destination=parent.with_name('spatial_agent_session_diagnostics_'+run_id+'.tar.gz')
    temporary=destination.with_name(destination.name+'.'+uuid4().hex+'.tmp')
    try:
        with temporary.open('wb') as raw,gzip.GzipFile(fileobj=raw,filename='',mode='wb',mtime=0) as zipped,tarfile.open(fileobj=zipped,mode='w') as archive:
            for source in (parent,child):
                with tarfile.open(source) as incoming:
                    for member in incoming.getmembers():
                        if not member.isfile():continue
                        data=incoming.extractfile(member).read()
                        info=tarfile.TarInfo(member.name);info.size=len(data);info.mode=0o600;info.mtime=0
                        archive.addfile(info,io.BytesIO(data))
            data=json.dumps(session,ensure_ascii=False,indent=2).encode()
            info=tarfile.TarInfo(run_id+'/session_summary.json');info.size=len(data);info.mode=0o600;info.mtime=0
            archive.addfile(info,io.BytesIO(data))
        os.replace(temporary,destination)
    finally:temporary.unlink(missing_ok=True)
    return destination


if __name__=='__main__':main()
