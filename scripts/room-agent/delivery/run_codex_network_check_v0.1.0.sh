#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
"""Read-only DNS/TLS comparison. No credentials, model calls or config changes."""
import datetime, gzip, io, ipaddress, json, os, re, shutil, subprocess, tarfile, uuid
from pathlib import Path

ret=Path('/yinxin/projects/room/return_output')
name='room_codex_network_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8]
out=ret/'environment_reports'/name
out.mkdir(parents=True,exist_ok=True)
report={'checks':{},'proxy_variables_set':[k for k in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy') if os.environ.get(k)]}

def clean(text):
    for key,value in os.environ.items():
        if len(value)>=8 and any(t in key.upper() for t in ('TOKEN','SECRET','PASSWORD','API_KEY')):
            text=text.replace(value,'[redacted]')
    text=re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@',r'\1[redacted]@',text)
    return text[-1500:]

def call(args,seconds=18):
    try:
        p=subprocess.run(args,capture_output=True,text=True,timeout=seconds)
        return p.returncode,p.stdout,p.stderr
    except subprocess.TimeoutExpired:return 124,'','timeout'
    except OSError as e:return 127,'',type(e).__name__

def record(label,data):
    report['checks'][label]=data
    print(label+': '+json.dumps(data,ensure_ascii=False),flush=True)

try:
    lines=Path('/etc/hosts').read_text().splitlines()
    report['target_hosts_entries']=[line.split('#',1)[0].strip() for line in lines
        if any(host in line.split('#',1)[0].split()[1:] for host in ('chatgpt.com','auth.openai.com'))]
    report['nameservers']=[line.split()[1] for line in Path('/etc/resolv.conf').read_text().splitlines()
        if len(line.split())>=2 and line.split()[0]=='nameserver']
    rc,stdout,stderr=call(['getent','ahostsv4','chatgpt.com'],10)
    record('system_dns',{'exit':rc,'addresses':sorted(set(line.split()[0] for line in stdout.splitlines() if line.split()))})
    if not shutil.which('curl'):raise RuntimeError('curl unavailable; no packages installed')
    base=['curl','-q','-4','--silent','--show-error','--connect-timeout','6','--max-time','12']
    metrics='http=%{http_code} remote=%{remote_ip} connect=%{time_connect} tls=%{time_appconnect} total=%{time_total}'
    rc,stdout,stderr=call(base+['--output','/dev/null','--write-out',metrics,'https://chatgpt.com/'])
    record('normal_https',{'exit':rc,'metrics':stdout,'error':clean(stderr)})
    # Two independent HTTPS DNS sources. Bootstrap only the diagnostic request;
    # certificate verification and the original HTTPS hostname remain enabled.
    addresses=set()
    for label,host,ip,url in [
        ('google','dns.google','8.8.8.8','https://dns.google/resolve?name=chatgpt.com&type=A'),
        ('cloudflare','cloudflare-dns.com','1.1.1.1','https://cloudflare-dns.com/dns-query?name=chatgpt.com&type=A')]:
        body=out/(label+'_dns_body.tmp')
        rc,stdout,stderr=call(base+['--resolve',host+':443:'+ip,'--header','Accept: application/dns-json',
            '--max-filesize','65536','--output',str(body),'--write-out','%{http_code}',url])
        data={'exit':rc,'http':stdout,'error':clean(stderr),'addresses':[]}
        if rc==0 and stdout=='200':
            try:
                answer=json.loads(body.read_text());data['dns_status']=answer.get('Status')
                for item in answer.get('Answer',[]):
                    if item.get('type')!=1:continue
                    addr=ipaddress.ip_address(item.get('data',''))
                    if addr.version==4 and addr.is_global:data['addresses'].append(str(addr))
                if data['dns_status']==0:addresses.update(data['addresses'])
            except (ValueError,OSError,TypeError):data['parse_error']='No valid DNS JSON answer'
        record(label+'_dns_https',data)
    for ip in sorted(addresses)[:4]:
        rc,stdout,stderr=call(base+['--resolve','chatgpt.com:443:'+ip,'--output','/dev/null',
            '--write-out',metrics,'https://chatgpt.com/'])
        record('https_resolve_'+ip,{'exit':rc,'metrics':stdout,'error':clean(stderr)})
    report['note']='An HTTP response proves transport reachability only, not workspace routing, login or model access. Different CDN DNS answers alone do not prove DNS poisoning. --resolve is request-local; with an explicit proxy, destination resolution may happen at that proxy.'
except Exception as exc:
    report['error']=clean(str(exc))
finally:
    payload=(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode()
    (out/'report.json').write_bytes(payload)
    archive=ret/(name+'.tar.gz')
    with archive.open('wb') as raw,gzip.GzipFile(fileobj=raw,filename='',mode='wb',mtime=0) as gz:
        with tarfile.open(fileobj=gz,mode='w') as tar:
            info=tarfile.TarInfo(name+'/report.json');info.size=len(payload);info.mtime=0;info.mode=0o600
            tar.addfile(info,io.BytesIO(payload))
    print('RETURN_TAR:',archive,flush=True)
PY
