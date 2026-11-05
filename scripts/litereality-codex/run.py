"""Install isolated adapter, check it offline, then verify two real Codex requests."""
import argparse
import datetime
import gzip
import json
import os
from pathlib import Path
import subprocess
import tarfile
import uuid
from build_fork import build,sha,HERE


def main():
    p=argparse.ArgumentParser();p.add_argument('--project-root',type=Path,default=Path('/yinxin/projects/room'))
    p.add_argument('--app-python',type=Path);p.add_argument('--offline-only',action='store_true');args=p.parse_args()
    root=args.project_root.absolute();name='room_litereality_codex_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ_')+uuid.uuid4().hex[:8]
    report=root/'return_output/artifacts'/name;report.mkdir(parents=True)
    result={'status':'failed','full_reconstruction_tested':False,'image_generation_api_replaced':False}
    try:
        fork=build(root);result['fork']=str(fork)
        (report/'patch_manifest.json').write_bytes((fork/'room_codex_patch.json').read_bytes())
        python=Path(os.path.abspath(args.app_python or root/'return_output/environments/litereality-agent-28ffe1f/bin/python'))
        if not python.is_file():raise RuntimeError('Validated LiteReality base environment not found')
        env=dict(os.environ,PYTHONPATH=str(fork/'src'),LR_REPO_ROOT=str(fork),PYTHONDONTWRITEBYTECODE='1',
            PATH=str(Path.home()/'.local/bin')+os.pathsep+os.environ['PATH'])
        for role in ('AGENT','AUTHOR','MATERIALS','QUALITY','REFINE','PROCEDURAL','CLASSIFY'):env['LR_'+role+'_PROVIDER']='codex'
        env.update(LR_CODEX_MODEL='gpt-6-astra',HARNESS_VLM='codex',HARNESS_MODEL='gpt-6-astra')
        def step(name,command,timeout):
            result['stage']=name
            print(name+'; log: '+str(report/(name+'.log')),flush=True)
            with (report/(name+'.log')).open('w') as log:
                subprocess.run([str(c) for c in command],cwd=report,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=timeout)
        step('offline_tests',[python,'-B',HERE/'test_adapter.py'],120)
        step('cli_help',[python,'-m','litereality_agent.cli','--help'],60)
        if args.offline_only:result['status']='offline_passed_live_not_run'
        else:
            step('live_probe',[python,'-B',HERE/'live_probe.py','--report',report],450)
            result['live_probe']=json.loads((report/'live_probe.json').read_text())
            result['status']='adapter_smoke_passed_full_reconstruction_not_run'
    except Exception as exc:
        result.update(status='failed',error_type=type(exc).__name__)
        if isinstance(exc,subprocess.CalledProcessError):result['exit_code']=exc.returncode
        if isinstance(exc,RuntimeError):result['error']=str(exc)
    finally:
        if (report/'live_probe.json').is_file():result['live_probe']=json.loads((report/'live_probe.json').read_text())
        (report/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        files=[p for p in sorted(report.iterdir()) if p.is_file()]
        (report/'manifest_sha256.json').write_text(json.dumps({p.name:sha(p) for p in files},indent=2)+'\n')
        output=root/'return_output'/(name+'.tar.gz')
        with output.open('wb') as raw,gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as gz:
            with tarfile.open(fileobj=gz,mode='w') as tar:
                for file in files+[report/'manifest_sha256.json']:
                    info=tarfile.TarInfo(name+'/'+file.name);info.size=file.stat().st_size;info.mode=0o644;info.mtime=0
                    with file.open('rb') as f:tar.addfile(info,f)
        print('RESULT: '+result['status']);print('RETURN ARCHIVE: '+str(output))
    return int(result['status']=='failed')

if __name__=='__main__':raise SystemExit(main())
