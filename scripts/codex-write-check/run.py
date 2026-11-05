"""Collect Codex write diagnostics using the existing v0.1.1 adapter and Python env."""
import argparse
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tarfile
import uuid

HERE=Path(__file__).resolve().parent


def main():
    p=argparse.ArgumentParser();p.add_argument('--project-root',type=Path,default=Path('/yinxin/projects/room'))
    p.add_argument('--app-python',type=Path);args=p.parse_args();root=args.project_root.absolute()
    name='room_codex_write_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ_')+uuid.uuid4().hex[:8]
    report=root/'return_output/artifacts'/name;report.mkdir(parents=True)
    result={'status':'failed','version':'0.1.0','full_reconstruction_tested':False,'vision_test_repeated':False}
    try:
        fork=root/'scripts/vendor/litereality-agent-28ffe1f-room-codex-v0.1.1'
        python=Path(os.path.abspath(args.app_python or root/'return_output/environments/litereality-agent-28ffe1f/bin/python'))
        if not (fork/'room_codex_patch.json').is_file() or not python.is_file():raise RuntimeError('Existing v0.1.1 adapter / Python environment is missing')
        env=dict(os.environ,PYTHONPATH=str(fork/'src'),LR_REPO_ROOT=str(fork),LR_CODEX_MODEL='gpt-6-astra',LR_CODEX_EFFORT='high',
            PYTHONDONTWRITEBYTECODE='1',PATH=str(Path.home()/'.local/bin')+os.pathsep+os.environ.get('PATH',''))
        cli=shutil.which('codex',path=env['PATH'])
        if not cli:raise RuntimeError('Codex CLI not found')
        with (report/'codex_version.log').open('w') as f:subprocess.run([cli,'--version'],stdout=f,stderr=subprocess.STDOUT,env=env,timeout=20,check=True)
        print('Testing shell and patch writes; log: '+str(report/'write_probe.log'),flush=True)
        with (report/'write_probe.log').open('w') as log:
            with subprocess.Popen([str(python),'-B',str(HERE/'worker.py'),'--report',str(report)],cwd=report,env=env,
                stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT) as proc:
                try:code=proc.wait(timeout=300)
                except subprocess.TimeoutExpired:
                    # Let the harness cancellation handler terminate its Codex process tree.
                    proc.send_signal(signal.SIGINT)
                    try:proc.wait(timeout=15)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait()
                    raise RuntimeError('Write diagnostic worker timed out')
        result['exit_code']=code
        if (report/'write_probe.json').is_file():result['write_probe']=json.loads((report/'write_probe.json').read_text())
        result['status']='write_checks_passed' if code==0 and result.get('write_probe',{}).get('status')=='passed' else 'write_checks_incomplete'
    except Exception as exc:
        result.update(error_type=type(exc).__name__)
        if isinstance(exc,RuntimeError):result['error']=str(exc)
    finally:
        (report/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        # The synthetic nonce is not needed for review; test files stay outside the return archive.
        files=[p for p in sorted(report.iterdir()) if p.is_file() and p.name!='probe_nonce.txt']
        (report/'manifest_sha256.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files},indent=2)+'\n')
        out=root/'return_output'/(name+'.tar.gz')
        with out.open('wb') as raw,gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as gz:
            with tarfile.open(fileobj=gz,mode='w') as t:
                for file in files+[report/'manifest_sha256.json']:
                    info=tarfile.TarInfo(name+'/'+file.name);info.size=file.stat().st_size;info.mode=0o644;info.mtime=0
                    with file.open('rb') as f:t.addfile(info,f)
        print('RESULT: '+result['status']);print('RETURN ARCHIVE: '+str(out))
    return 0 if result['status']=='write_checks_passed' else 1

if __name__=='__main__':raise SystemExit(main())
