"""Foreground school launcher, explicit input run, immutable original and return tar."""
import argparse
import fcntl
import json
import os
import re
import signal
import sys
import time
import traceback
import uuid
from pathlib import Path

from batch_io import copy_source, save, source_hashes
from common import frame_files
from full_support import command, ensure_app, pack, setup_environment
from runtime import prepare

HERE=Path(__file__).resolve().parent
VERSION='0.1.0'
APP_SHA='8748b08b2ed7790a7b63f389a523a86707b23b8adb6522810b40e401f194fc94'


def source_info(source):
    source=source.resolve()
    if source.parent.name!='artifacts' or source.parent.parent.name!='return_output':
        raise ValueError('Provide an explicit existing run under <project>/return_output/artifacts')
    data=json.loads((source/'input.json').read_text())
    scene=data['scene']
    if not re.fullmatch(r'[A-Za-z0-9_-]+',scene):raise ValueError('Invalid scene name')
    if data.get('app_sha256')!=APP_SHA:raise ValueError('Source application revision differs; review compatibility first')
    scene_root=source/'scenes'/scene
    room=scene_root/'realism_authoring/room'
    latest=source/'latest_candidate.json'
    if latest.exists():room=Path(json.loads(latest.read_text())['source']).resolve()
    if not room.resolve().is_relative_to(source):raise ValueError('Candidate source escapes explicit run')
    seed=scene_root/'scene_init/scene_stage/room_init/room'
    for root in (room,seed):
        for name in ('Room.py','Room.md','manifest.json'):
            if not (root/name).is_file():raise ValueError('Missing saved source: '+str(root/name))
    capture=Path(data['capture']).resolve()
    frame_files(capture)
    return data,room,seed,capture


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source_run',type=Path)
    p.add_argument('--max-batches',type=int,default=3,help='0 = rebuild/review only, 1..3 = bounded repairs')
    p.add_argument('--repair-seconds',type=int,default=900)
    a=p.parse_args()
    if not 0<=a.max_batches<=3 or not 120<=a.repair_seconds<=1800:p.error('batches 0..3; repair seconds 120..1800')
    source=a.source_run.resolve()
    if source.parent.name!='artifacts' or source.parent.parent.name!='return_output':p.error('Expected return_output/artifacts/<explicit-run>')
    ret=source.parent.parent;project=ret.parent
    invocation=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'_'+uuid.uuid4().hex[:8]
    folder=ret/'artifacts'/('room_batch_review_v010_'+invocation)
    folder.mkdir(parents=True,exist_ok=False)
    result=dict(scene='room',status='execution_error',operation='saved_scene_batched_review',
                source_run=str(source),ingest_reconstruct_rerun=False,invocation=invocation)
    save(folder/'result.json',result)
    locks=[];before=None;room=None;start=time.monotonic();exitcode=2
    def stop(sig,frame):raise KeyboardInterrupt('Signal '+str(sig))
    signal.signal(signal.SIGTERM,stop)
    try:
        if not source.is_dir():raise ValueError('Source run does not exist: '+str(source))
        for lock_path in (source/'.run.lock',folder/'.run.lock'):
            lock=lock_path.open('a');locks.append(lock)
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        data,room,seed,capture=source_info(source)
        result['scene']=data['scene'];save(folder/'result.json',result)
        before=source_hashes(room)
        save(folder/'original_source_hashes.json',before)
        target=folder/'scenes'/data['scene']
        copy_source(room,target/'realism_authoring/room')
        copy_source(seed,target/'scene_init/scene_stage/room_init/room')
        save(folder/'input.json',dict(scene=data['scene'],source_run=str(source),source_room=str(room),
            capture=str(capture),app_sha256=APP_SHA,version=VERSION,archive=data.get('archive'),
            archive_sha256=data.get('archive_sha256'),max_batches=a.max_batches,repair_seconds=a.repair_seconds))
        app=ensure_app(APP_SHA)
        env=setup_environment(project,folder,app)
        env.update(LITEREALITY_SCAN=data['scene'],LR_SCENE=str(target),
                   LR_AUTHORING=str(target/'realism_authoring'))
        # The input link is an existing scan, not extraction or ingestion. Packing excludes it.
        (folder/'input'/data['scene']).symlink_to(capture,target_is_directory=True)
        prepare(env,folder/'checks/runtime.json')
        try:
            command('saved_scene_batches',[sys.executable,'-u',HERE/'worker.py','--folder',folder,
                '--max-batches',a.max_batches,'--repair-seconds',a.repair_seconds],
                HERE,env,folder,7200*(a.max_batches+1)+a.max_batches*a.repair_seconds)
        except RuntimeError:
            current=json.loads((folder/'result.json').read_text())
            if current.get('operation')!='saved_scene_batched_review' or current.get('workflow_finished') is not True:raise
        result=json.loads((folder/'result.json').read_text())
        exitcode=0 if result['status']=='accepted' else 2
    except BaseException as exc:
        result=json.loads((folder/'result.json').read_text())
        result.update(status='interrupted' if isinstance(exc,KeyboardInterrupt) else 'execution_error',error=str(exc))
        (folder/'error.log').write_text(traceback.format_exc())
        print(traceback.format_exc(),flush=True)
    finally:
        if before is not None:
            try:
                unchanged=source_hashes(room)==before
            except Exception:unchanged=False
            result['original_source_unchanged']=unchanged
            if not unchanged:
                result.update(status='execution_error',error='Original source changed while batch job ran; inspect before reuse')
                exitcode=2
        result.update(invocation=invocation,total_wall_seconds=time.monotonic()-start)
        save(folder/'result.json',result)
        print('FINAL_STATUS:',result['status'],flush=True)
        print('RESULT_DIR:',folder,flush=True)
        try:
            archive=pack(folder,ret,invocation)
            print('RETURN_TAR:',archive,flush=True)
        finally:
            for lock in reversed(locks):lock.close()
    return exitcode


if __name__=='__main__':sys.exit(main())
