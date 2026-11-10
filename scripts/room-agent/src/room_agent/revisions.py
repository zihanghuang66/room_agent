"""Bind an explicit completed run to a child revision. Never select filesystem 'latest'."""
from pathlib import Path
from .saved_scene import request_source_hashes, capture_names, file_hash, APP_SHA
from .storage import read_json, room_busy

VERSION = 'scene-edit/1'


def selected_source(service, run_id, *, hashes=True):
    request, directory = service._load(run_id)
    status = service.show(run_id)
    if status['execution_status'] != 'completed' or room_busy(service.root / 'run_activity', run_id):
        raise ValueError('Select a completed scene version before creating an edit')
    with service._graph(request, directory) as (graph, _, _, config):
        state = dict(graph.get_state(config).values)
    operation = request['operation']
    if operation == 'full_scan':
        if request['workflow_version'] != 'full-scan-graph/1' or not state.get('technical_export_verified'):
            raise ValueError('Full scan has no verified saved export yet')
        scene = request['upstream_scene']
        root = directory / 'backend/scenes' / scene
        source = root / 'realism_authoring/room'
        seed = root / 'scene_init/scene_stage/room_init/room'
        data = read_json(directory / 'backend/input.json')
        if data.get('scene') != scene:
            raise ValueError('Capture scene identity mismatch')
        capture = Path(data['capture']).resolve()
        if not capture.is_relative_to((directory / 'backend/input').resolve()):
            raise ValueError('Capture escapes parent input')
    elif operation in ('saved_scene_review', 'scene_edit'):
        source = Path(state.get('room') or state.get('active_source', ''))
        seed = Path(state.get('seed', ''))
        capture, scene = Path(request['capture']).resolve(), request['scene']
    else:
        raise ValueError('This task has no editable scene')
    for folder in (source, seed):
        if not folder.resolve().is_relative_to(directory.resolve()) or folder.is_symlink():
            raise ValueError('Scene source escapes parent run')
        for name in ('Room.py', 'Room.md', 'manifest.json'):
            if not (folder / name).is_file():
                raise ValueError('Missing editable source: ' + str(folder / name))
    result = dict(parent_run_id=run_id, room_id=request['room_id'], source_run=str(directory),
                  source=str(source), seed=str(seed), capture=str(capture), scene=scene,
                  project=request['project'], app_sha256=APP_SHA,
                  parent_quality=status['quality_status'], parent_score=status.get('score'),
                  parent_issues=status.get('issues',[]))
    if status.get('whole_room_review')=='not_repeated':
        # Carry the historical findings across multiple child revisions; do not lose
        # them merely because the immediately preceding edit had no new defects.
        baseline=status.get('baseline_quality',{})
        result['parent_issues']=[*baseline.get('issues',[]),*status.get('issues',[])]
        result['parent_score']=baseline.get('score')
    if hashes:
        result['source_preview_policy'] = 'generated-preview-v2'
        result.update(source_files=request_source_hashes(result, 'source'), seed_files=request_source_hashes(result, 'seed'))
        names = capture_names(capture)
        cameras = {x for x in names if x.startswith('frame_') and x.endswith('.json')}
        expected = {n for c in cameras for n in (c, c[:-5]+'.jpg', 'depth_'+c[6:-5]+'.png')}
        if not cameras or names != expected:
            raise ValueError('Capture requires paired RGB, pose and depth files')
        result['capture_files'] = {}
        for name in sorted(names):
            p = capture / name
            if p.is_symlink() or not p.is_file():
                raise ValueError('Missing or linked capture file: '+name)
            result['capture_files'][name] = file_hash(p)
    return result


def versions(service):
    rows=[]
    for path in (service.root / 'runs').glob('*/input.json'):
        try:
            info=selected_source(service,path.parent.name,hashes=False)
            request,_=service._load(path.parent.name)
            events=service.events(path.parent.name)
            created=next((e['time'] for e in events if e['kind']=='run_created'), '')
            rows.append(dict(run_id=path.parent.name,room_id=info['room_id'],quality_status=info['parent_quality'],
                             parent_run_id=request.get('parent_run_id'),instruction=request.get('instruction',''),created=created))
        except (ValueError,KeyError,OSError):
            continue
    return sorted(rows,key=lambda x:(x['created'],x['run_id']),reverse=True)
