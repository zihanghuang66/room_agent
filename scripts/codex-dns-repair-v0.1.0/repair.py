"""Temporary single-host workaround, verified before/after; no auth/config changes."""
import argparse
import datetime
import fcntl
import gzip
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import uuid

HOSTS=Path('/etc/hosts')
RET=Path('/yinxin/projects/room/return_output')
MARKER='# room-codex-dns temporary override'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def addresses(data):
    if data.get('Status')!=0:raise ValueError('HTTPS DNS returned a non-success status')
    found=set()
    for record in data.get('Answer',[]):
        if record.get('type')!=1:continue
        ip=ipaddress.ip_address(record['data'])
        if ip.version==4 and ip.is_global:found.add(str(ip))
    if not found:raise ValueError('No public IPv4 DNS answer; hosts unchanged')
    return sorted(found)


def render_hosts(original,ip):
    addr=ipaddress.ip_address(ip)
    if addr.version!=4 or not addr.is_global:raise ValueError('Requires a public IPv4 address')
    lines=[]
    for line in original.decode('utf-8').splitlines(keepends=True):
        if line.strip()==MARKER:continue
        content,sep,comment=line.rstrip('\r\n').partition('#')
        fields=content.split()
        if len(fields)>1 and 'chatgpt.com' in [s.lower() for s in fields[1:]]:
            aliases=[s for s in fields[1:] if s.lower()!='chatgpt.com']
            if aliases:lines.append(' '.join([fields[0]]+aliases)+(' #'+comment if sep else '')+'\n')
            elif sep:lines.append('#'+comment+'\n')
        else:lines.append(line)
    body=''.join(lines)
    if body and not body.endswith('\n'):body+='\n'
    return (body+MARKER+'\n'+str(addr)+' chatgpt.com\n').encode()


def replace_contents(path,expected,new):
    # /etc/hosts is often a runtime bind mount; rename/atomic-replace may fail with EBUSY.
    with path.open('r+b') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX)
        if stream.read()!=expected:raise RuntimeError('hosts changed concurrently; refusing overwrite')
        stream.seek(0);stream.write(new);stream.truncate();stream.flush();os.fsync(stream.fileno())


def install_override(hosts,run,ip):
    original=hosts.read_bytes();updated=render_hosts(original,ip)
    (run/'hosts.before').write_bytes(original)
    state={'before_sha256':sha(original),'applied_sha256':sha(updated),'address':ip,'hosts':str(hosts)}
    (run/'change.json').write_text(json.dumps(state,indent=2)+'\n')
    replace_contents(hosts,original,updated)
    return state


def restore(hosts,run,state):
    original=(run/'hosts.before').read_bytes();current=hosts.read_bytes()
    if sha(original)!=state['before_sha256']:raise RuntimeError('Backup checksum mismatch')
    if sha(current)!=state['applied_sha256']:raise RuntimeError('hosts changed since repair; refusing rollback overwrite')
    replace_contents(hosts,current,original)


def clean(text):
    for key,value in os.environ.items():
        if len(value)>=8 and any(k in key.upper() for k in ('TOKEN','SECRET','PASSWORD','API_KEY')):
            text=text.replace(value,'[redacted]')
    return re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@',r'\1[redacted]@',text)[-1600:]


def call(args):
    try:
        p=subprocess.run(args,capture_output=True,text=True,timeout=20)
        return p.returncode,p.stdout,clean(p.stderr)
    except subprocess.TimeoutExpired:return 124,'','process timeout'


def probe(ip=None):
    args=['curl','-q','-4','--silent','--show-error','--connect-timeout','6','--max-time','12']
    if ip:args+=['--resolve','chatgpt.com:443:'+ip]
    rc,out,err=call(args+['--output','/dev/null','--write-out','%{http_code} %{remote_ip} %{time_appconnect}',
                        'https://chatgpt.com/'])
    fields=out.split();code=fields[0] if fields else '000'
    remote=fields[1] if len(fields)>1 else ''
    return dict(exit=rc,http=code,remote=remote,error=err,
                transport_ok=rc==0 and code.isdigit() and int(code)>=100)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--rollback',type=Path,help='Restore a specific backup directory printed by repair')
    args=parser.parse_args()
    name='room_codex_dns_fix_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8]
    run=RET/'environment_reports'/name;run.mkdir(parents=True,exist_ok=True)
    report={'status':'failed','codex_authenticated_request_tested':False,'run':str(run)}
    state=None
    try:
        if os.geteuid()!=0:raise RuntimeError('Run in the school container as root; /etc/hosts needs write access')
        if args.rollback:
            backup=args.rollback.resolve()
            if backup.parent!=(RET/'environment_reports').resolve() or not backup.name.startswith('room_codex_dns_fix_'):
                raise ValueError('Rollback must reference a repair directory under return_output/environment_reports')
            saved=json.loads((backup/'change.json').read_text())
            restore(HOSTS,backup,saved);report['status']='rolled_back'
            print('ROLLBACK_OK',flush=True);return 0
        proxies=[k for k in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy') if os.environ.get(k)]
        if proxies:raise RuntimeError('Proxy environment changed since diagnosis; refusing a direct-DNS fix. Set variables: '+','.join(proxies))
        print('Fetching current chatgpt.com A records over certificate-verified HTTPS DNS...',flush=True)
        body=run/'dns-answer.json'
        rc,out,err=call(['curl','-q','-4','--silent','--show-error','--connect-timeout','6','--max-time','12',
            '--resolve','dns.google:443:8.8.8.8','--max-filesize','65536','--output',str(body),
            '--write-out','%{http_code}','https://dns.google/resolve?name=chatgpt.com&type=A'])
        if rc or out!='200':raise RuntimeError('HTTPS DNS query failed; hosts unchanged: '+str(rc)+' '+err)
        data=json.loads(body.read_text());ips=addresses(data)
        report['dns_answers']=ips
        report['dns_ttl_seconds']=[r.get('TTL') for r in data.get('Answer',[]) if r.get('type')==1]
        report['probes']=[];selected=None
        for ip in ips[:4]:
            check=probe(ip);report['probes'].append({'address':ip,**check})
            print('CANDIDATE '+ip+' '+json.dumps(check),flush=True)
            if check['transport_ok'] and check['remote']==ip:selected=ip;break
        if not selected:raise RuntimeError('No HTTPS-verified reachable candidate; hosts unchanged')
        state=install_override(HOSTS,run,selected)
        check=probe();report['after']=check
        if not check['transport_ok'] or check['remote']!=selected:
            raise RuntimeError('Normal resolver verification failed after hosts update')
        report.update(status='dns_override_applied_transport_verified',change=state,
                      application_access_unverified=True)
        print('DNS_TRANSPORT_OK HTTP='+check['http']+' IP='+selected,flush=True)
        print('HTTP 403 is not an authenticated Codex success. Start a fresh Codex process next.',flush=True)
        print('BACKUP_DIR: '+str(run),flush=True)
        print('ROLLBACK: bash '+str(Path(__file__).parent/'repair.sh')+' --rollback '+str(run),flush=True)
        return 0
    except Exception as exc:
        report['error']=clean(str(exc));print('FAILED: '+report['error'],flush=True)
        if state:
            try:restore(HOSTS,run,state);report['auto_rollback']='ok';print('AUTO_ROLLBACK_OK',flush=True)
            except Exception as rollback:report['auto_rollback']=clean(str(rollback));print('AUTO_ROLLBACK_FAILED: '+report['auto_rollback'],flush=True)
        return 1
    finally:
        payload=(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode()
        (run/'report.json').write_bytes(payload)
        archive=RET/(name+'.tar.gz')
        with archive.open('wb') as raw,gzip.GzipFile(fileobj=raw,filename='',mode='wb',mtime=0) as gz:
            with tarfile.open(fileobj=gz,mode='w') as tar:
                info=tarfile.TarInfo(name+'/report.json');info.size=len(payload);info.mtime=0;info.mode=0o600
                tar.addfile(info,io.BytesIO(payload))
        print('RETURN_TAR: '+str(archive),flush=True)


if __name__=='__main__':raise SystemExit(main())
