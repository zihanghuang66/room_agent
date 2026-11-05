"""Test the actual Codex sandbox without a model call or permission escalation."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def metadata():
    result={'uid':os.getuid(),'gid':os.getgid()}
    path=Path('/proc/self/status')
    if path.is_file():
        wanted={'NoNewPrivs','Seccomp','Seccomp_filters','CapEff','CapBnd'}
        result['process']={k:v.strip() for line in path.read_text().splitlines() if ':' in line for k,v in [line.split(':',1)] if k in wanted}
    for path in ('/proc/self/uid_map','/proc/self/attr/current','/proc/sys/user/max_user_namespaces','/proc/sys/kernel/unprivileged_userns_clone'):
        try:result[path]=Path(path).read_text().strip()[:1000]
        except OSError:pass
    return result


def sandbox_command(cli,help_text,work,command):
    # Detect CLI shape from this installation, not its version number.
    if not ('--config' in help_text or '-c ' in help_text):
        raise ValueError('Unsupported sandbox help: no config option; no sandbox settings were changed')
    args=[cli,'sandbox']
    if re.search(r'^\s+linux\s',help_text,re.M):args.append('linux')
    elif '[COMMAND]' not in help_text:raise ValueError('Unsupported sandbox command shape')
    if '--permission-profile' in help_text:
        if '--include-managed-config' not in help_text:
            raise ValueError('Cannot explicitly preserve managed policy with this sandbox CLI')
        args+=['--permission-profile',':workspace','--include-managed-config','-c','approval_policy="never"']
    else:
        args+=['-c','sandbox_mode="workspace-write"','-c','approval_policy="never"',
               '-c','sandbox_workspace_write.network_access=false',
               '-c','sandbox_workspace_write.writable_roots='+json.dumps([str(work)])]
    if '--cd' in help_text:args+=['--cd',str(work)]
    return args+['--']+list(map(str,command))


def failure_kind(code,output):
    if code==2 and any(s in output for s in ('Usage:','required arguments','unexpected argument')):return 'cli_usage_error'
    if 'bwrap:' in output and any(s in output.lower() for s in ('permission denied','operation not permitted')):return 'sandbox_denied'
    if any(s in output.lower() for s in ('not allowed','requirements','unknown permission profile')):return 'policy_or_config_error'
    return 'execution_failed'


def probe(report,work):
    result={'metadata':metadata(),'model_calls':0,'sandbox_bypassed':False,'locations':{}}
    cli=shutil.which('codex')
    if not cli:return dict(result,status='blocked',error='Codex CLI missing')
    def execute(args,cwd,log):
        try:
            done=subprocess.run(args,cwd=cwd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=25)
            log.write_text(done.stdout);return done.returncode,done.stdout
        except subprocess.TimeoutExpired as exc:
            output=exc.stdout or b''
            if isinstance(output,bytes):output=output.decode(errors='replace')
            log.write_text(output+'\nTIMEOUT after 25 seconds\n');return None,output+'\nTIMEOUT'
    code,help_text=execute([cli,'sandbox','--help'],work,report/'sandbox_help.log')
    if code!=0:return dict(result,status='blocked',error='Cannot inspect Codex sandbox CLI; see sandbox_help.log')
    def one(name,location):
        script=location/'sandbox_fixture.py';target=location/'sandbox_result.txt'
        script.write_text('import os\nfrom pathlib import Path\nprint("ROOM_FIXTURE_STARTED",flush=True)\np=Path(__file__).with_name("sandbox_result.txt")\n'
            'with p.open("w") as f: f.write("ROOM_SANDBOX_OK"); f.flush(); os.fsync(f.fileno())\n'
            'assert p.read_text()=="ROOM_SANDBOX_OK"\nprint("ROOM_SANDBOX_OK")\n')
        try:cmd=sandbox_command(cli,help_text,location,[sys.executable,script])
        except ValueError as exc:return {'status':'blocked','reason':str(exc)}
        code,output=execute(cmd,location,report/('sandbox_'+name+'.log'))
        matches=target.is_file() and target.read_text()=='ROOM_SANDBOX_OK'
        kind=None if code==0 and matches else failure_kind(code,output)
        return {'status':'passed' if kind is None else 'blocked' if kind=='sandbox_denied' else 'failed','returncode':code,
            'failure_kind':kind,'fixture_started':'ROOM_FIXTURE_STARTED' in output,
            'file_matches':matches,'path':str(location),'command':cmd,'output_tail':output[-6000:]}
    project=work/'sandbox_project';project.mkdir()
    result['locations']['project']=one('project',project)
    with tempfile.TemporaryDirectory(prefix='room-sandbox-probe-',dir='/tmp') as tmp:
        result['locations']['tmp']=one('tmp',Path(tmp))
    result['status']=result['locations']['project']['status']
    if result['status']!='passed':result['reason']='Direct sandbox check did not pass. Inspect failure_kind: CLI usage/config errors do not prove a container denial. Dependent model writes are skipped. /tmp is comparison only.'
    return result
