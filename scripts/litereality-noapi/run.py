"""Room scanner offline baseline. No agent, image API, model download, or install."""
import argparse
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import traceback
import uuid
from common import digest, extract_checked, find_capture, save

HERE=Path(__file__).resolve().parent
VERSION='v0.1.0'
SCAN='LiteReality-LiteReality_Scanner-Studio_9952-20261031-090455.tar'


def bundle_digest():
    h=hashlib.sha256()
    for p in sorted(HERE.glob('*.py')):
        h.update(p.name.encode()); h.update(p.read_bytes())
    return h.hexdigest()


def child_env(folder):
    env=dict(os.environ)
    for k in list(env):
        if k.startswith(('OPENAI_', 'ANTHROPIC_', 'GEMINI_', 'GOOGLE_', 'MODAL_', 'CLAUDE_', 'CODEX_', 'LR_', 'LITEREALITY_')) or k.endswith(('_TOKEN','_API_KEY')):
            env.pop(k,None)
    env.pop('PYTHONPATH',None)
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', OMP_NUM_THREADS='4',
        OPENBLAS_NUM_THREADS='4', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        HF_HUB_DISABLE_TELEMETRY='1', MPLCONFIGDIR=str(folder/'work/matplotlib'),
        XDG_CACHE_HOME=str(folder/'work/cache'), LR_REPO_ROOT=str(folder/'work/runtime'),
        LITEREALITY_OUTPUT=str(folder/'work/output'), LITEREALITY_FINAL=str(folder/'work/output'))
    source=Path(os.environ.get('ROOM_LITEREALITY_SOURCE','/opt/room/app'))
    if (source/'src/litereality_agent').is_dir(): env['PYTHONPATH']=str(source/'src')
    for k in ('MPLCONFIGDIR','XDG_CACHE_HOME','LR_REPO_ROOT'):
        Path(env[k]).mkdir(parents=True, exist_ok=True)
    return env


def execute(command, log, cwd, env, timeout):
    started=time.monotonic()
    with log.open('wb') as stream:
        p=subprocess.Popen([str(c) for c in command],cwd=cwd,env=env,stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,start_new_session=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(p.stdout,selectors.EVENT_READ)
                while selector.get_map():
                    if time.monotonic()-started>timeout: raise TimeoutError('Stage timeout after '+str(timeout)+' seconds')
                    for key,_ in selector.select(timeout=1):
                        chunk=os.read(key.fileobj.fileno(),65536)
                        if not chunk: selector.unregister(key.fileobj); continue
                        stream.write(chunk); stream.flush()
                        # Only concise progress; complete output remains in log.
                        for line in chunk.decode('utf-8','replace').splitlines():
                            if line.startswith(('FUSION ','EXPORT_OK ','REOPEN_ALL_PASS')): print(line,flush=True)
            rc=p.wait(timeout=10)
            if rc: raise RuntimeError('Child exit code '+str(rc))
        except BaseException:
            if p.poll() is None:
                os.killpg(p.pid,signal.SIGTERM)
                try: p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid,signal.SIGKILL);p.wait()
            raise
        finally:
            p.stdout.close()


def report_files(folder):
    selected=[]
    for p in sorted(folder.rglob('*')):
        rel=p.relative_to(folder)
        if rel.parts[0] in ('work','offline_extraction','receipts') or p.is_symlink() or not p.is_file(): continue
        if p.name in ('manifest_sha256.json','run.lock'): continue
        selected.append(p)
    return selected


def package_report(folder, root):
    files=report_files(folder)
    manifest=folder/'manifest_sha256.json'
    save(manifest,{str(p.relative_to(folder)):digest(p) for p in files})
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output=root/'return_output'/('room_002_noapi_'+stamp+'_'+uuid.uuid4().hex[:8]+'.tar.gz')
    with output.open('wb') as raw,gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as gz:
        with tarfile.open(fileobj=gz,mode='w') as tar:
            for p in files+[manifest]:
                info=tarfile.TarInfo(folder.name+'/'+str(p.relative_to(folder)))
                info.size=p.stat().st_size;info.mtime=0;info.mode=0o644
                with p.open('rb') as f: tar.addfile(info,f)
    output.with_name(output.name+'.sha256').write_text(digest(output)+'  '+output.name+'\n')
    return output


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--project-root',type=Path,default=Path(os.environ.get('ROOM_PROJECT_ROOT','/yinxin/projects/room')))
    ap.add_argument('--scan-archive',type=Path)
    ap.add_argument('--scene-name',default='room_002')
    ap.add_argument('--app-python',default=os.environ.get('ROOM_PYTHON','/opt/room/envs/app/bin/python'))
    ap.add_argument('--blender',default=os.environ.get('ROOM_BLENDER','/opt/room/bin/blender'))
    ap.add_argument('--voxel',type=float,default=.02)
    ap.add_argument('--min-confidence',type=int,default=1,choices=(0,1,2))
    ap.add_argument('--fresh',action='store_true',help='Use a fresh run directory instead of verified cached stages')
    ap.add_argument('--skip-blender',action='store_true',help='Geometry-only diagnostic; not full baseline acceptance')
    args=ap.parse_args()
    if not re.fullmatch('[A-Za-z0-9_-]+',args.scene_name): ap.error('Invalid scene name')
    if not .005<=args.voxel<=.10: ap.error('voxel must be 0.005 to 0.10 metres')
    root=args.project_root.resolve(); (root/'return_output/artifacts').mkdir(parents=True,exist_ok=True)
    archive=(args.scan_archive or root/'data/raw/room_002'/SCAN).absolute()
    # Missing inputs still generate a failure report.
    scan_sha=digest(archive) if archive.is_file() else 'missing'
    signature=dict(version=VERSION, bundle_sha256=bundle_digest(),scan_sha256=scan_sha,
        scene_name=args.scene_name,voxel_m=args.voxel,min_confidence=args.min_confidence,
        app_python=os.path.abspath(args.app_python),blender=os.path.abspath(args.blender))
    sig=hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()
    suffix=('_'+uuid.uuid4().hex[:8]) if args.fresh else ''
    folder=root/'return_output/artifacts'/('room_002_noapi_'+VERSION+'_'+sig[:16]+suffix)
    folder.mkdir(exist_ok=True)
    import fcntl
    run_lock=(folder/'run.lock').open('a')
    try: fcntl.flock(run_lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('ERROR: this run is already active: '+str(folder),flush=True)
        return 1
    for d in ('logs','receipts','work','deliverables'): (folder/d).mkdir(exist_ok=True)
    result=dict(status='running',version=VERSION,signature=signature,stages={},model_calls=0,
        image_api_calls=0,full_litereality_pipeline=False,stage_scope='offline extraction + measured RGBD fusion + original RoomPlan export',
        scan_archive=str(archive),output_directory=str(folder),
        limitations=['No ingest/reconstruct/seed/author stage completion is claimed.',
        'No furniture generation, segmentation, layout-agent correction or texture synthesis.',
        'RoomPlan furniture proxies and observed unsegmented Mesh are separate deliverables.'])
    env=child_env(folder)
    save(folder/'result.json',result)
    print('OUTPUT_DIR '+str(folder),flush=True)
    def stage(name,action,outputs):
        receipt=folder/'receipts'/(name+'.json')
        try:
            if receipt.is_file():
                prior=json.loads(receipt.read_text())
                if prior.get('signature')==sig and prior.get('files') and all((folder/p).is_file() and digest(folder/p)==sha for p,sha in prior['files'].items()):
                    result['stages'][name]='REUSED';print('['+name+'] REUSED (hash verified)',flush=True);return True
            print('['+name+'] RUNNING',flush=True)
            action()
            paths=outputs()
            if not paths or any(not p.is_file() or p.stat().st_size==0 for p in paths): raise RuntimeError('Required stage output missing/empty')
            save(receipt,dict(signature=sig,files={str(p.relative_to(folder)):digest(p) for p in paths}))
            result['stages'][name]='PASS';print('['+name+'] PASS',flush=True);return True
        except Exception as e:
            result['stages'][name]='FAIL';save(folder/'logs'/(name+'_error.json'),dict(error=str(e),traceback=traceback.format_exc()))
            print('['+name+'] FAIL: '+str(e),flush=True)
            log=folder/'logs'/(name+'.log')
            if log.exists(): print(log.read_text(errors='replace')[-5000:],flush=True)
            return False
        finally: save(folder/'result.json',result)
    def child(name,cmd,timeout=1800):
        execute(cmd,folder/'logs'/(name+'.log'),folder/'work',env,timeout)
    try:
        if not archive.is_file(): raise FileNotFoundError('Scan archive not found: '+str(archive))
        if not Path(args.app_python).is_file(): raise FileNotFoundError('App Python missing: '+args.app_python)
        raw=folder/'work/raw'
        def unpack():
            # Only this run's disposable copy is replaced on incomplete extraction.
            if raw.exists(): shutil.rmtree(raw)
            extract_checked(archive,raw,scan_sha)
            capture=find_capture(raw)
            save(folder/'input.json',dict(archive_sha256=scan_sha,capture=str(capture)))
        if not stage('unpack',unpack,lambda:[folder/'input.json']+sorted(p for p in raw.rglob('*') if p.is_file())):
            raise RuntimeError('Input extraction failed')
        capture=find_capture(raw)
        original_usdz=capture/'roomplan/room.usdz'
        if not original_usdz.exists(): original_usdz=capture/'room.usdz'
        # Validate nested archive before either parser/importer sees it.
        import zipfile
        with zipfile.ZipFile(original_usdz) as z:
            for m in z.infolist():
                p=Path(m.filename)
                if p.is_absolute() or '..' in p.parts or '\\' in m.filename or (m.external_attr>>16)&0o170000==0o120000:
                    raise ValueError('Unsafe USDZ member')
            if sum(m.file_size for m in z.infolist())>2*1024**3: raise ValueError('USDZ exceeds 2 GiB limit')
            if z.testzip(): raise ValueError('Corrupt USDZ')
        def extract():
            offline=folder/'offline_extraction'
            if offline.exists(): shutil.rmtree(offline)
            child('extract',[args.app_python,'-u',HERE/'extract_worker.py','--scan',capture,'--report',folder,'--scene-name',args.scene_name])
        stage('extract',extract,lambda:[folder/'extraction_summary.json',folder/'frame_audit.json',folder/'rgb_contact_sheet.jpg']+
              sorted(p for p in (folder/'offline_extraction').rglob('*') if p.is_file()))
        deliver=folder/'deliverables'
        stage('geometry',lambda:child('geometry',[args.app_python,'-u',HERE/'geometry.py','--scan',capture,
            '--output',deliver,'--voxel',str(args.voxel),'--min-confidence',str(args.min_confidence)],3600),
            lambda:[deliver/p for p in ('observed_cloud.ply','observed_mesh.ply','cloud_views.png','fusion_frames.json','geometry_summary.json')])
        if args.skip_blender:
            result['stages']['blender']='SKIPPED_BY_REQUEST'
        else:
            for kind,source in (('layout',original_usdz),('observed',deliver/'observed_mesh.ply')):
                if kind=='observed' and result['stages']['geometry']=='FAIL':
                    result['stages']['export_observed']='SKIPPED_DEPENDENCY';continue
                stage('export_'+kind,lambda kind=kind,source=source:child('export_'+kind,
                    [args.blender,'--background','--factory-startup','--python-exit-code','1','--python',HERE/'blender_export.py',
                    '--','--kind',kind,'--source',source,'--output',deliver],1800),
                    lambda kind=kind:[deliver/(kind+s) for s in ('.glb','.blend','_overview.png','_top.png','_export.json')])
            if all(result['stages'].get('export_'+k) in ('PASS','REUSED') for k in ('layout','observed')):
                stage('reopen',lambda:child('reopen',[args.blender,'--background','--factory-startup','--python-exit-code','1',
                    '--python',HERE/'verify_blender.py','--',deliver]),lambda:[deliver/'reopen_verification.json'])
            else: result['stages']['reopen']='SKIPPED_DEPENDENCY'
        if any(v=='FAIL' or v=='SKIPPED_DEPENDENCY' for v in result['stages'].values()): result['status']='failed_with_partial_outputs'
        elif args.skip_blender: result['status']='geometry_only_blender_not_validated'
        else: result['status']='offline_baseline_passed'
    except (Exception,KeyboardInterrupt) as e:
        result['status']='failed_with_partial_outputs';result['error']=str(e)
        (folder/'logs/fatal.log').write_text(traceback.format_exc())
        print('FAILED: '+str(e),flush=True)
    finally:
        save(folder/'result.json',result)
        shutil.copyfile(HERE/'使用说明.md',folder/'使用说明.md')
        save(folder/'source_sha256.json',{p.name:digest(p) for p in HERE.iterdir() if p.is_file()})
        print('RESULT '+result['status'],flush=True)
        print('RETURN_TAR '+str(package_report(folder,root)),flush=True)
    return 0 if result['status']=='offline_baseline_passed' else 1

if __name__=='__main__': sys.exit(main())
