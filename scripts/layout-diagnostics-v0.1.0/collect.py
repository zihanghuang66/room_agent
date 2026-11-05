"""Read-only collection of omitted layout evidence. Never execute/unpickle returned data."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import tarfile
import time
import uuid

RUN='room_001_test_01_full_v0.2.1_482bd3e5c832'
SCENE='room_001_test_01'
SHA='a5f3da0c7e33f98939db2cdaa61191e1ecaccb56f225c37fa28933384743361a'

def collect(project):
    ret=Path(project)/'return_output';run=ret/'artifacts'/RUN
    identity=json.loads((run/'input.json').read_text())
    if identity.get('sha256')!=SHA or identity.get('scene')!=SCENE:
        raise ValueError('This collector targets the reported test_01 run only')
    source=run/'scenes'/SCENE/'scene_init/obj_stage/object_init'
    scene_data=source/'input/scene_data'/SCENE
    stamp=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'_'+uuid.uuid4().hex[:8]
    report=ret/'environment_reports'/('room_layout_diagnostics_'+stamp)
    report.mkdir(parents=True,exist_ok=False)
    candidates=[run/'input.json',run/'result.json',run/'stage_ingest.json']
    candidates.extend(scene_data.rglob('*'))
    candidates.extend((source/'object_refs'/SCENE).rglob('members.json'))
    # JSON contains exact box geometry/decisions; photographs show whether boxes share a real object.
    allowed={'.json','.jsonl','.html','.txt','.log','.jpg','.jpeg','.png','.pkl'}
    selected=[];skipped=[];total=0
    for p in sorted(set(candidates)):
        if not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(run.resolve()):continue
        if p.suffix.lower() not in allowed or p.name in {'auth.json','credentials.json','config.toml'}:continue
        if p.stat().st_size>10*1024**2:
            skipped.append(str(p.relative_to(run)));continue
        total+=p.stat().st_size
        if total>200*1024**2:raise ValueError('Diagnostic evidence exceeds 200 MiB; no large archive created')
        selected.append(p)
    required=['layout_validation.json','layout_report.json','layout_baseline.json','layout_shell.json','merge_review.json']
    summary={'read_only':True,'models_called':False,'files':len(selected),'bytes':total,
             'missing_reports':[n for n in required if not (scene_data/n).is_file()],
             'reference_images':sum(p.suffix.lower() in {'.jpg','.jpeg','.png'} for p in selected),
             'skipped_large_files':skipped,'note':'Pickle files are copied as bytes, never loaded or executed.'}
    (report/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    records={'evidence/'+str(p.relative_to(run)):p for p in selected}
    records['summary.json']=report/'summary.json'
    hashes={n:hashlib.sha256(p.read_bytes()).hexdigest() for n,p in records.items()}
    (report/'manifest_sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')
    records['manifest_sha256.json']=report/'manifest_sha256.json'
    out=ret/(report.name+'.tar.gz')
    with out.open('wb') as stream,gzip.GzipFile(fileobj=stream,filename='',mode='wb',mtime=0) as gz:
        with tarfile.open(fileobj=gz,mode='w') as t:
            for name,p in sorted(records.items()):
                info=tarfile.TarInfo(report.name+'/'+name);info.size=p.stat().st_size;info.mtime=0;info.mode=0o644
                with p.open('rb') as f:t.addfile(info,f)
    print(json.dumps(summary,indent=2),flush=True)
    print('RETURN_TAR: '+str(out),flush=True)
    return out

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--project-root',type=Path,default=Path('/yinxin/projects/room'))
    collect(parser.parse_args().project_root)
