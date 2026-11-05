"""Run the comprehensive suite with the validated Python environment and return one archive."""
import argparse
import importlib.util
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import subprocess
import tarfile
import time
import uuid

HERE=Path(__file__).resolve().parent


def install_adapter(root):
    path=HERE.parent/'litereality-codex/build_fork.py'
    spec=importlib.util.spec_from_file_location('room_build_fork',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.build(root)


def redact(text):
    for key,value in os.environ.items():
        if len(value)>=8 and any(word in key.upper() for word in ('TOKEN','PASSWORD','SECRET','API_KEY')):text=text.replace(value,'[redacted]')
    text=re.sub(r'(?i)(Bearer\s+)[^\s"\']+',r'\1[redacted]',text)
    text=re.sub(r'\bsk-[A-Za-z0-9_-]{8,}','[redacted]',text)
    text=re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@',r'\1[redacted]@',text)
    text=re.sub(r'(?i)([?&](?:token|key|password|signature)=)[^\s&]+',r'\1[redacted]',text)
    return text


def read_json(path):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return {}


def terminal_print(report,text):
    text=redact(str(text))
    text=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',text)
    text=''.join(c for c in text if ord(c)>=32 or c in '\n\t')
    print(text,flush=True)
    with (report/'terminal_summary.log').open('a') as f:f.write(text+'\n')


def error_details(report,name,data):
    lines=[]
    for key in ('error_type','error','reason','loader_error'):
        if data.get(key):lines.append(key+': '+str(data[key]))
    failed=[k for k,v in data.get('assertions',{}).items() if not v]
    if failed:lines.append('Failed assertions: '+', '.join(failed))
    if data.get('files'):lines.append('File checks: '+json.dumps(data['files'],ensure_ascii=False))
    diag=data.get('diagnostics') or read_json(report/(name+'_session.json'))
    if diag:
        lines.append('CLI returncode: '+str(diag.get('returncode'))+'; stopped: '+str(diag.get('stopped','')))
        if diag.get('stderr'):lines.append('CLI stderr:\n'+redact(str(diag['stderr']))[-1800:])
        if diag.get('stream_errors'):lines.append('Stream errors (recovery status retained): '+json.dumps(diag['stream_errors'],ensure_ascii=False))
        lines.append('completed_turn: '+str(diag.get('completed_turn'))+'; pending_error: '+str(diag.get('pending_error')))
    if data.get('locations'):
        for key,location in data['locations'].items():
            lines.append(key+': '+str(location.get('status'))+'; exit='+str(location.get('returncode'))+'\n'+str(location.get('output_tail',location.get('reason',''))))
    execution=data.get('execution',{})
    if execution:lines.append('Process: '+json.dumps(execution))
    events=report/(name+'_events.jsonl');errors=[]
    if events.is_file():
        with events.open() as f:
            for line in f:
                try:
                    item=json.loads(line)
                    if item.get('kind')=='tool_result' and item.get('is_error'):errors.append(str(item.get('content','')))
                    if item.get('kind')=='raw_event':
                        raw=json.loads(item.get('event','{}'));tool=raw.get('item',{})
                        if tool.get('status')=='failed':errors.append(json.dumps(tool,ensure_ascii=False))
                        if raw.get('type') in ('error','turn.failed'):errors.append(json.dumps(raw,ensure_ascii=False))
                except (ValueError,TypeError):continue
    if errors:lines.append('Tool errors:\n'+'\n'.join(redact(e)[-900:] for e in errors[-3:]))
    log=report/(name+'.log')
    if log.is_file():
        with log.open('rb') as f:
            f.seek(max(0,log.stat().st_size-8192));tail=f.read().decode(errors='replace')
        lines.append('Log tail:\n'+'\n'.join(redact(tail).splitlines()[-12:]))
    if not lines:lines.append('Artifact validation failed; see suite.json and per-check logs.')
    text=redact('\n'.join(lines))
    if len(text)>6500:text=text[:6500]+'\n[truncated; complete diagnostics are in the return archive]'
    return text


def emit_check_updates(report,summary,seen):
    for name,data in summary.get('checks',{}).items():
        status=data.get('status','unknown');key=(name,status)
        if key in seen:continue
        seen.add(key)
        if status in ('failed','blocked'):
            terminal_print(report,'\n=== ERROR BEGIN: '+name+' ===\n'+error_details(report,name,data)+'\n=== ERROR END: '+name+' ===')
        elif status=='software_only':
            renderer=data.get('artifacts',{}).get('details',{}).get('renderer','unknown')
            terminal_print(report,'[WARN] '+name+': software_only; renderer='+str(renderer))
        elif status=='skipped':terminal_print(report,'[SKIP] '+name+': '+data.get('reason',''))
        else:
            terminal_print(report,'['+status.upper()+'] '+name)
            diag=data.get('diagnostics') or {}
            if diag.get('stream_errors'):
                terminal_print(report,'[WARN] '+name+': stream errors were followed by a completed turn; retained in report.\n'+json.dumps(diag['stream_errors'],ensure_ascii=False)[-2000:])


def pack(report,root):
    files=[p for p in sorted(report.iterdir()) if p.is_file() and not p.is_symlink() and (p.suffix in ('.json','.jsonl','.log','.md') or p.name=='activate_graphics.sh') and p.name!='manifest_sha256.json']
    for p in files:
        if p.suffix in ('.json','.jsonl','.log'):p.write_text(redact(p.read_text(errors='replace')))
    for mode in ('host_cpu','host_cuda','host_eevee','host_eevee_repaired','host_cuda_repaired','blender_agent'):
        for name in ('cpu.json','cpu.png','cuda.json','cuda.png','eevee.json','eevee.png','test_cube.glb','test_cube.blend'):
            p=report/'renders'/mode/name
            if p.is_file() and not p.is_symlink() and p.stat().st_size<20*1024**2:files.append(p)
    manifest={p.relative_to(report).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    (report/'manifest_sha256.json').write_text(json.dumps(manifest,indent=2)+'\n')
    output=root/'return_output'/(report.name+'.tar.gz')
    with output.open('wb') as raw,gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as gz:
        with tarfile.open(fileobj=gz,mode='w') as tar:
            for p in files+[report/'manifest_sha256.json']:
                info=tarfile.TarInfo(report.name+'/'+p.relative_to(report).as_posix());info.size=p.stat().st_size;info.mode=0o644;info.mtime=0
                with p.open('rb') as f:tar.addfile(info,f)
    return output


def main():
    p=argparse.ArgumentParser();p.add_argument('--project-root',type=Path,default=Path('/yinxin/projects/room'));p.add_argument('--app-python',type=Path);p.add_argument('--runtime-only',action='store_true');args=p.parse_args()
    root=args.project_root.absolute();name='room_codex_suite_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ_')+uuid.uuid4().hex[:8]
    report=root/'return_output/artifacts'/name;report.mkdir(parents=True)
    result={'status':'failed','version':'0.1.4','stage':'preflight','max_model_sessions':4 if args.runtime_only else 7,'full_reconstruction_tested':False,'runtime_only':args.runtime_only}
    seen=set()
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    try:
        result['stage']='adapter_install'
        fork=install_adapter(root)
        (report/'patch_manifest.json').write_bytes((fork/'room_codex_patch.json').read_bytes())
        python=Path(os.path.abspath(args.app_python or root/'return_output/environments/litereality-agent-28ffe1f/bin/python'))
        if not python.is_file():raise RuntimeError('Validated LiteReality Python environment is required')
        env=dict(os.environ,PYTHONPATH=str(fork/'src'),LR_REPO_ROOT=str(fork),PYTHONDONTWRITEBYTECODE='1',LR_CODEX_MODEL='gpt-6-astra',LR_CODEX_EFFORT='high',
            PATH=str(Path.home()/'.local/bin')+os.pathsep+os.environ.get('PATH',''))
        cli=shutil.which('codex',path=env['PATH'])
        if not cli:raise RuntimeError('Codex CLI not found')
        for label,cmd in [('adapter_offline_tests',[python,'-B',HERE.parent/'litereality-codex/test_adapter.py']),('codex_version',[cli,'--version']),('codex_exec_help',[cli,'exec','--help']),('python_version',[python,'--version'])]:
            result['stage']=label
            with (report/(label+'.log')).open('w') as f:subprocess.run(list(map(str,cmd)),stdout=f,stderr=subprocess.STDOUT,env=env,timeout=120 if label=='adapter_offline_tests' else 20,check=True)
        nvidia=shutil.which('nvidia-smi')
        if nvidia:
            try:
                with (report/'nvidia-smi.log').open('w') as f:
                    probe=subprocess.run([nvidia,'--query-gpu=name,memory.total,driver_version','--format=csv,noheader'],stdout=f,stderr=subprocess.STDOUT,timeout=20)
                result['gpu_probe']={'exit_code':probe.returncode}
            except (subprocess.SubprocessError,OSError) as exc:result['gpu_probe']={'error_type':type(exc).__name__}
        result['stage']='suite'
        terminal_print(report,'Adapter v0.1.2 verified. '+('Runtime-only: up to 4 model sessions, zero if direct sandbox fails.' if args.runtime_only else 'Full suite: up to 7 model sessions.')+' Full log: '+str(report/'suite.log'))
        with (report/'suite.log').open('w') as f:
            with subprocess.Popen([str(python),'-B',str(HERE/'suite.py'),'--report',str(report),'--project-root',str(root)]+(['--runtime-only'] if args.runtime_only else []),cwd=report,env=env,stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT) as proc:
                deadline=time.monotonic()+2400;heartbeat=time.monotonic()
                try:
                    while True:
                        remaining=deadline-time.monotonic()
                        if remaining<=0:raise subprocess.TimeoutExpired(proc.args,2400)
                        try:code=proc.wait(timeout=min(5,remaining));break
                        except subprocess.TimeoutExpired:
                            current=read_json(report/'suite.json');emit_check_updates(report,current,seen)
                            if time.monotonic()-heartbeat>=30:
                                terminal_print(report,'Completed checks: '+str(len(current.get('checks',{})))+'; model sessions started: '+str(current.get('model_sessions','?')))
                                heartbeat=time.monotonic()
                except subprocess.TimeoutExpired:
                    proc.send_signal(signal.SIGINT)
                    try:proc.wait(timeout=20)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait()
                    raise RuntimeError('Suite exceeded 40 minutes; completed checks remain in suite.json')
        result['worker_exit_code']=code
        if (report/'suite.json').is_file():
            summary=json.loads((report/'suite.json').read_text());result['suite']=summary
            result['status']=summary.get('status','failed') if code in (0,1) else 'failed'
            if code!=0 and result['status'] in ('core_checks_passed','runtime_checks_passed'):result['status']='failed'
    except Exception as exc:
        result.update(error_type=type(exc).__name__)
        result['error']=redact(str(exc))
        emit_check_updates(report,{'checks':{result['stage']:{'status':'failed','error_type':type(exc).__name__,'error':redact(str(exc))}}},seen)
    finally:
        if (report/'suite.json').is_file() and 'suite' not in result:result['suite']=read_json(report/'suite.json')
        emit_check_updates(report,result.get('suite',{}),seen)
        if result.get('worker_exit_code') not in (None,0) and not result.get('suite',{}).get('finished'):
            result['status']='failed'
            emit_check_updates(report,{'checks':{'suite':{'status':'failed','error':'Suite worker exited unexpectedly: '+str(result['worker_exit_code'])}}},seen)
        (report/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        if 'suite' in result:
            rows=['# 综合测试结果','', '| 检查 | 状态 |','| --- | --- |']
            rows += ['| '+k+' | '+v.get('status','unknown')+' |' for k,v in result['suite'].get('checks',{}).items()]
            rows += ['', '模型会话数：'+str(result['suite'].get('model_sessions')),'', '此报告不代表完整 LiteReality 重建通过。']
            (report/'结果摘要.md').write_text('\n'.join(rows)+'\n')
        terminal_print(report,'RESULT: '+result['status']+'\nFor a quick diagnosis, copy the ERROR BEGIN / ERROR END block. Full details will also be saved in the archive.')
        archive=pack(report,root);print('RETURN ARCHIVE: '+str(archive),flush=True)
    return 0 if result['status'] in ('core_checks_passed','runtime_checks_passed') else 1

if __name__=='__main__':raise SystemExit(main())
