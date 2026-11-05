"""Exact-configuration resume, plus one audited v0.3.0 failed-ingest migration."""
import json
from pathlib import Path
import shutil

PRIOR_APP='6d28c5e24cc49cd462b16746aba237993bb83f4a05619a9ee226bed0b86abae8'

def scene_data(folder, scene):
    return Path(folder)/'scenes'/scene/'scene_init/obj_stage/object_init/input/scene_data'/scene

def validate_resume(requested, artifacts, config):
    requested=Path(requested);folder=requested.resolve()
    if requested.is_symlink() or folder.parent!=Path(artifacts).resolve() or not folder.is_dir():
        raise ValueError('Resume folder must be an existing direct child of return_output/artifacts')
    previous=json.loads((folder/'input.json').read_text());status=json.loads((folder/'result.json').read_text())
    for key in ('scene','archive_sha256','codex_model','trellis_resolution'):
        if previous.get(key)!=config.get(key):raise ValueError('Resume mismatch: '+key)
    if status.get('scene')!=config['scene'] or status.get('full_run_completed') is True:
        raise ValueError('Resume requires this scene and an unfinished run')
    same=all(previous.get(k)==config.get(k) for k in ('app_sha256','version'))
    if not same:
        allowed=(previous.get('version')=='0.3.0' and previous.get('app_sha256')==PRIOR_APP
                 and config.get('version')=='0.3.1' and status.get('stage')=='ingest'
                 and status.get('status')=='failed')
        if not allowed:raise ValueError('Resume application/version is not compatible with this fix')
        state=json.loads((folder/'scenes'/config['scene']/'.litereality/pipeline.json').read_text())
        stages=state.get('stages',{})
        if set(stages)!={'ingest'} or stages['ingest'].get('status')!='failed':
            raise ValueError('Cross-version migration requires failed ingest with no downstream work')
        data=scene_data(folder,config['scene'])
        if not data.resolve().is_relative_to(folder):raise ValueError('Scene data escapes run directory')
        for name in ('walls.pkl','objects.pkl','objects.extracted.pkl','floor.pkl','wall_holes.pkl','layout_validation.json'):
            if not (data/name).is_file() or (data/name).is_symlink():raise ValueError('Missing/unsafe preserved layout input: '+name)
        check=json.loads((data/'layout_validation.json').read_text())
        if check.get('passed') is not False or not any(v.get('kind')=='wall_clash' for v in check.get('violations',[])):
            raise ValueError('Migration requires a recorded wall-clash layout failure')
    return folder,previous

def backup_migration(folder, previous, config, invocation):
    """Called under the run lock, before any layout or run metadata mutation."""
    folder=Path(folder)
    # Repeat the guard under lock; an earlier read may have raced with another run.
    _,current=validate_resume(folder,folder.parent,config)
    if current!=previous:raise ValueError('Previous input changed while acquiring the run lock')
    if previous['version']==config['version'] and previous['app_sha256']==config['app_sha256']:return None
    out=folder/'migration_backups'/invocation;out.mkdir(parents=True,exist_ok=False)
    for name in ('input.json','result.json'):shutil.copyfile(folder/name,out/name)
    state=folder/'scenes'/config['scene']/'.litereality/pipeline.json'
    shutil.copyfile(state,out/'pipeline.json')
    target=out/'scene_data';target.mkdir()
    for path in scene_data(folder,config['scene']).iterdir():
        if path.is_file() and not path.is_symlink() and path.suffix in ('.json','.pkl'):
            shutil.copyfile(path,target/path.name)
    (out/'migration.json').write_text(json.dumps({'from':previous,'to':config,
        'policy':'preserve extraction and object placement; rerun failed layout and remaining pipeline'},indent=2))
    return out
