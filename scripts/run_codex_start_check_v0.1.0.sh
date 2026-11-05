#!/usr/bin/env bash
set -euo pipefail
# --start is an isolated interactive A/B test; it does not kill the existing daemon.
if [[ "${1:-}" == "--start" ]]; then
  exec "${ROOM_CODEX:-codex}" --no-daemon
fi
python3 - <<'PY'
import datetime, gzip, io, json, os, re, shutil, subprocess, tarfile, uuid
from pathlib import Path

ret=Path('/yinxin/projects/room/return_output')
name='room_codex_start_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8]
out=ret/'environment_reports'/name
out.mkdir(parents=True,exist_ok=True)
cli=os.environ.get('ROOM_CODEX') or shutil.which('codex')
report={'cli':cli,'proxy_variables_set':[k for k in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy') if os.environ.get(k)],'checks':{}}

def call(args,seconds=25):
    try:
        p=subprocess.run(args,capture_output=True,text=True,timeout=seconds)
        return p.returncode,p.stdout,p.stderr
    except subprocess.TimeoutExpired:return 124,'','timeout'
    except OSError as e:return 127,'',type(e).__name__

def clean(text):
    for key,value in os.environ.items():
        if len(value)>=8 and any(t in key.upper() for t in ('TOKEN','SECRET','PASSWORD','API_KEY')):
            text=text.replace(value,'[redacted]')
    text=re.sub(r'(?i)(Bearer\s+)\S+',r'\1[redacted]',text)
    text=re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@',r'\1[redacted]@',text)
    return text[-1500:]

if cli:
    rc,stdout,stderr=call([cli,'--version'])
    report['checks']['version']={'exit':rc,'stdout':clean(stdout),'stderr':clean(stderr)}
    rc,stdout,stderr=call([cli,'login','status'])
    # No raw auth/status output or credential files are collected.
    mode='api_key' if 'api key' in (stdout+stderr).lower() else 'chatgpt' if 'chatgpt' in (stdout+stderr).lower() else 'unknown'
    report['checks']['login']={'exit':rc,'mode':mode,'credentials_present':rc==0,'remote_route_verified':False}
    rc,stdout,stderr=call([cli,'--help'])
    report['checks']['no_daemon_option']={'help_exit':rc,'listed':'--no-daemon' in stdout}

for host in ('chatgpt.com','auth.openai.com'):
    if shutil.which('getent'):
        rc,stdout,stderr=call(['getent','ahostsv4',host],10)
        report['checks']['dns_'+host]={'exit':rc,'ipv4_addresses':sorted(set(line.split()[0] for line in stdout.splitlines() if line.split()))}
    if shutil.which('curl'):
        rc,stdout,stderr=call(['curl','-q','--silent','--show-error','--connect-timeout','8','--max-time','15',
            '--output','/dev/null','--write-out','http=%{http_code} dns=%{time_namelookup} connect=%{time_connect} tls=%{time_appconnect} total=%{time_total}',
            'https://'+host+'/'],20)
        report['checks']['https_'+host]={'exit':rc,'metrics':stdout,'error':clean(stderr)}
report['note']='Read-only startup diagnostics. HTTP responses alone do not verify authenticated workspace routing or imagegen. Login status only indicates stored credentials. No model call or config/login changes.'
payload=(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode()
(out/'report.json').write_bytes(payload)
print(payload.decode(),flush=True)
archive=ret/(name+'.tar.gz')
with archive.open('wb') as raw,gzip.GzipFile(fileobj=raw,filename='',mode='wb',mtime=0) as gz:
    with tarfile.open(fileobj=gz,mode='w') as tar:
        info=tarfile.TarInfo(name+'/report.json');info.size=len(payload);info.mtime=0;info.mode=0o600
        tar.addfile(info,io.BytesIO(payload))
print('RETURN_TAR:',archive)
PY
