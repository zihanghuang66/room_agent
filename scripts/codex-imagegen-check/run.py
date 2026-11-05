"""Prepare one room_002 furniture and probe Codex built-in image generation."""
import argparse
import datetime
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import traceback
import uuid
from common import digest,extract_checked,find_capture,save
from probe import probe,redact,clean_env

HERE=Path(__file__).resolve().parent
SCAN='LiteReality-LiteReality_Scanner-Studio_9952-20261031-090455.tar'


def choose_codex(explicit=None):
    if explicit:return explicit
    packaged=Path('/opt/room/tools/codex-packaged/bin/codex')
    if packaged.is_file():return str(packaged)
    return shutil.which('codex') or '/opt/room/bin/codex'


def pack(folder,root):
    # Exact allowlist. Never include raw capture, Codex home, arbitrary agent-created files or links.
    names=['result.json','selection_summary.json','probe_result.json','prompt.txt','source_sha256.json','使用说明.md']
    names += ['inputs/'+n for n in ('evidence.jpg','primary_context.jpg','view_1.png','view_2.png','view_3.png','selection.json','candidates.json')]
    names += ['outputs/'+n for n in ('reference_raw.png','reference_1024.png','comparison.jpg','generation_receipt.json')]
    names += ['logs/'+n for n in ('prepare.log','events.jsonl','stderr.log','version.txt','features.txt','login.json','command.json','last_message.txt','tool_evidence.json','error.log')]
    paths=[folder/n for n in names if (folder/n).is_file() and not (folder/n).is_symlink() and (folder/n).resolve().is_relative_to(folder.resolve())]
    # Re-sanitize textual diagnostics, including error paths and agent-written text.
    for p in paths:
        if p.suffix in ('.json','.jsonl','.txt','.log','.md'):p.write_text(redact(p.read_text(errors='replace')))
    manifest=folder/'manifest_sha256.json';save(manifest,{str(p.relative_to(folder)):digest(p) for p in paths})
    output=root/'return_output'/(folder.name+'.tar.gz')
    with output.open('wb') as raw,gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as gz:
        with tarfile.open(fileobj=gz,mode='w') as t:
            for p in paths+[manifest]:
                info=tarfile.TarInfo(folder.name+'/'+str(p.relative_to(folder)));info.size=p.stat().st_size
                info.mtime=0;info.mode=0o644
                with p.open('rb') as f:t.addfile(info,f)
    output.with_name(output.name+'.sha256').write_text(digest(output)+'  '+output.name+'\n')
    return output


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--project-root',type=Path,default=Path(os.environ.get('ROOM_PROJECT_ROOT','/yinxin/projects/room')))
    ap.add_argument('--scan-archive',type=Path)
    ap.add_argument('--object-name',help='Optional exact RoomPlan object ID, e.g. Chair0; default best visible furniture')
    ap.add_argument('--codex',default=os.environ.get('ROOM_CODEX_BIN'))
    ap.add_argument('--model',default=os.environ.get('ROOM_CODEX_MODEL','gpt-6-astra'))
    ap.add_argument('--timeout',type=int,default=900)
    ap.add_argument('--prepare-only',action='store_true',help='Offline evidence selection only; no Codex call')
    args=ap.parse_args()
    if not 30<=args.timeout<=3600:ap.error('timeout must be 30..3600 seconds')
    root=args.project_root.resolve()
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    folder=root/'return_output/artifacts'/('room_codex_imagegen_'+stamp+'_'+uuid.uuid4().hex[:8])
    for name in ('work','inputs','outputs','logs'):(folder/name).mkdir(parents=True,exist_ok=True)
    result=dict(status='running',version='v0.1.0',codex_sessions_started=0,api_fallback_enabled=False,
        full_pipeline=False,requested_outputs=1,automatic_retries=0,model=args.model,
        output_directory=str(folder),source_scan=str(args.scan_archive or root/'data/raw/room_002'/SCAN))
    try:
        scan_archive=(args.scan_archive or root/'data/raw/room_002'/SCAN).resolve()
        result['input_sha256']=digest(scan_archive)
        print('[prepare] Extracting capture copy; no model calls',flush=True)
        extract_checked(scan_archive,folder/'work/capture',result['input_sha256'])
        scan=find_capture(folder/'work/capture')
        env=clean_env();env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1',
            XDG_CACHE_HOME=str(folder/'work/cache'),MPLCONFIGDIR=str(folder/'work/matplotlib'),
            OMP_NUM_THREADS='4',OPENBLAS_NUM_THREADS='4',HF_HUB_OFFLINE='1')
        source=Path(os.environ.get('ROOM_LITEREALITY_SOURCE','/opt/room/app'))
        if (source/'src/litereality_agent').is_dir():env['PYTHONPATH']=str(source/'src')
        cmd=[sys.executable,str(HERE/'prepare.py'),'--scan',str(scan),'--output',str(folder/'inputs')]
        if args.object_name:cmd+=['--object-name',args.object_name]
        with (folder/'logs/prepare.log').open('w') as f:
            done=subprocess.run(cmd,cwd=folder/'work',env=env,stdout=f,stderr=subprocess.STDOUT,timeout=300)
        if done.returncode:raise RuntimeError('Furniture preparation failed; see prepare.log')
        selection=json.loads((folder/'inputs/selection.json').read_text())
        save(folder/'selection_summary.json',selection)
        result['selected_object']=selection['object']
        print('[prepare] PASS object='+selection['object']+' views='+str(len(selection['selected_views'])),flush=True)
        print('EVIDENCE '+str(folder/'inputs/evidence.jpg'),flush=True)
        if args.prepare_only:
            result['status']='prepared_only_no_model_call'
        else:
            codex=choose_codex(args.codex)
            if not Path(codex).is_file():raise FileNotFoundError('Codex executable missing: '+codex)
            result['codex_executable']=codex
            # Preflight failures can occur before the session actually starts; refine using command receipt below.
            print('[codex] Checking login and requesting one built-in image generation',flush=True)
            value=probe(codex,folder,selection,args.model,args.timeout)
            result['status']=value['status']
            result['probe']=value
    except (Exception,KeyboardInterrupt) as e:
        result['status']='failed';result['error']=redact(str(e))
        (folder/'logs/error.log').write_text(redact(traceback.format_exc()))
        print('FAIL: '+redact(str(e)),flush=True)
    finally:
        result['codex_sessions_started']=int((folder/'logs/tool_evidence.json').exists())
        save(folder/'result.json',result)
        save(folder/'source_sha256.json',{p.name:digest(p) for p in HERE.iterdir() if p.is_file()})
        shutil.copyfile(HERE/'使用说明.md',folder/'使用说明.md')
        if result['status'] not in ('prepared_only_no_model_call','imagegen_transport_passed_quality_pending'):
            for name in ('prepare.log','stderr.log','last_message.txt'):
                p=folder/'logs'/name
                if p.is_file() and not p.is_symlink():print('['+name+']\n'+redact(p.read_text(errors='replace')[-2500:]),flush=True)
        print('RESULT '+result['status'],flush=True)
        print('RETURN_TAR '+str(pack(folder,root)),flush=True)
    return 0 if result['status'] in ('prepared_only_no_model_call','imagegen_transport_passed_quality_pending') else 1

if __name__=='__main__':sys.exit(main())
