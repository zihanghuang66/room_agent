"""Exercise the image's SSH service using disposable containers and keys."""
import datetime
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile
import tempfile
import time
import traceback
import uuid

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
OUT=ROOT/'return_output'
STAMP=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8]
REPORT=OUT/'ssh_checks'/STAMP
REPORT.mkdir(parents=True)
IMAGE=os.environ.get('ROOM_BUILD_IMAGE','room-litereality-agent:0.1.3-cuda124-r3')
result={'image':IMAGE,'status':'running','checks':{},'containers_cleaned':False}
containers=[]
log=(REPORT/'commands.log').open('w')

def run(args,input=None,check=True,timeout=40,env=None):
    log.write('$ '+json.dumps([str(x) for x in args])+'\n');log.flush()
    p=subprocess.run([str(x) for x in args],input=input,text=True,stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT,timeout=timeout,env=env)
    log.write(p.stdout+'\nEXIT '+str(p.returncode)+'\n');log.flush()
    if check and p.returncode:
        raise RuntimeError(f'Command failed ({p.returncode}): {args[0]}\n{p.stdout[-4000:]}')
    return p

def passed(name,detail=True):
    result['checks'][name]=detail
    print('PASS',name,flush=True)

def wait_ready(name,port):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        if run(['docker','inspect','-f','{{.State.Running}}',name]).stdout.strip()!='true':
            raise RuntimeError(run(['docker','logs',name],check=False).stdout)
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=1) as s:
                s.settimeout(2)
                if s.recv(256).startswith(b'SSH-2.0-'):return
        except (OSError,TimeoutError):pass
        time.sleep(.3)
    raise RuntimeError('SSH banner not ready')

try:
    image=json.loads(run(['docker','image','inspect',IMAGE]).stdout)[0]
    result.update(image_id=image['Id'],image_default_cmd=image['Config'].get('Cmd'),
                  image_entrypoint=image['Config'].get('Entrypoint'))
    assert image['Config']['Cmd']==['/usr/local/bin/room-sshd'],image['Config']['Cmd']
    passed('default_command_is_room_sshd')
    run(['docker','run','--rm','--network=none','--entrypoint','bash',IMAGE,'-c',
         'test -z "$(find /etc/ssh -maxdepth 1 -name \'ssh_host_*_key\' -print)"'])
    passed('no_host_private_keys_baked_into_image')
    with tempfile.TemporaryDirectory(prefix='.ssh-check-private-',dir=OUT) as private:
        private=Path(private)
        key=private/'client'; wrong=private/'wrong';known=private/'known_hosts'
        for path in (key,wrong):run(['ssh-keygen','-q','-t','ed25519','-N','','-f',path])
        known.write_text('')
        options=['-F','/dev/null','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
                 '-o','ConnectTimeout=5','-o','StrictHostKeyChecking=yes',
                 '-o',f'UserKnownHostsFile={known}','-o','GlobalKnownHostsFile=/dev/null']
        def ssh(port,command,keyfile=key,pty=False):
            return run(['ssh',*options,'-i',keyfile,'-p',port,*(['-tt'] if pty else []),
                        'root@127.0.0.1',command],env=dict(os.environ,TERM='xterm-ghostty'))
        def start(port):
            name='room-ssh-check-'+uuid.uuid4().hex[:10]
            containers.append(name)
            run(['docker','run','-d','--name',name,'-p',f'127.0.0.1::{port}',
                 '-e',f'ROOM_SSH_PORT={port}',IMAGE])
            binding=run(['docker','port',name,f'{port}/tcp']).stdout.strip()
            assert binding.startswith('127.0.0.1:'),binding
            hostport=int(binding.rsplit(':',1)[1])
            wait_ready(name,hostport)
            public=run(['docker','exec',name,'cat','/etc/ssh/ssh_host_ed25519_key.pub']).stdout.strip()
            with known.open('a') as f:f.write(f'[127.0.0.1]:{hostport} '+' '.join(public.split()[:2])+'\n')
            run(['docker','exec','-i',name,'sh','-c',
                 'umask 077; mkdir -p /root/.ssh; cat > /root/.ssh/authorized_keys'],input=key.with_suffix('.pub').read_text())
            return name,hostport,public
        first,port,hostkey=start(22)
        passed('default_startup_serves_ssh_without_command_override')
        effective=run(['docker','exec',first,'/usr/sbin/sshd','-T','-f','/opt/room/image/sshd_config','-p','22']).stdout
        (REPORT/'sshd-effective.txt').write_text(effective)
        settings=dict(line.split(' ',1) for line in effective.splitlines() if ' ' in line)
        assert settings['passwordauthentication']=='no'
        assert settings['kbdinteractiveauthentication']=='no'
        assert settings['pubkeyauthentication']=='yes'
        assert settings['permitrootlogin'] in ('prohibit-password','without-password')
        passed('effective_config', {k:settings[k] for k in ['passwordauthentication','pubkeyauthentication','permitrootlogin','port']})
        assert 'SSH_COMMAND_OK' in ssh(port,'test "$(id -u)" = 0 && printf SSH_COMMAND_OK').stdout
        passed('public_key_login_and_command')
        bad=run(['ssh',*options,'-i',wrong,'-p',port,'root@127.0.0.1','true'],check=False)
        assert bad.returncode==255 and 'Permission denied' in bad.stdout,bad.stdout
        passed('unrecognized_key_rejected')
        shell="bash -lc 'test -t 0 && test \"$TERM\" = xterm-ghostty && infocmp -x xterm-ghostty >/dev/null && command -v codex && command -v blender && tmux -L room-ssh-check new-session -d -s smoke && tmux -L room-ssh-check has-session -t smoke && tmux -L room-ssh-check kill-server && printf PTY_TMUX_ENV_OK'"
        assert 'PTY_TMUX_ENV_OK' in ssh(port,shell,pty=True).stdout
        passed('pty_ghostty_tmux_and_login_environment')
        assert 'usage:' in ssh(port,'/usr/local/bin/room-litereality --help').stdout.lower()
        passed('litereality_cli_over_ssh')
        payload=('room-ssh-file-transfer-'+uuid.uuid4().hex+'\n').encode()
        upload=private/'upload.txt';download=private/'download.txt';batch=private/'sftp.batch'
        upload.write_bytes(payload)
        ssh(port,'mkdir -p /tmp/room-ssh-file-check')
        batch.write_text(f'put {upload} /tmp/room-ssh-file-check/upload.txt\nget /tmp/room-ssh-file-check/upload.txt {download}\n')
        run(['sftp',*options,'-i',key,'-P',port,'-b',batch,'root@127.0.0.1'])
        assert download.read_bytes()==payload
        digest=hashlib.sha256(payload).hexdigest()
        assert ssh(port,'sha256sum /tmp/room-ssh-file-check/upload.txt').stdout.split()[0]==digest
        passed('sftp_upload_download_sha256',digest)
        old_port=port
        run(['docker','restart',first])
        binding=run(['docker','port',first,'22/tcp']).stdout.strip()
        assert binding.startswith('127.0.0.1:'),binding
        port=int(binding.rsplit(':',1)[1])
        if port!=old_port:
            with known.open('a') as f:f.write(f'[127.0.0.1]:{port} '+' '.join(hostkey.split()[:2])+'\n')
        result['restart_host_ports']={'before':old_port,'after':port}
        wait_ready(first,port)
        assert run(['docker','exec',first,'cat','/etc/ssh/ssh_host_ed25519_key.pub']).stdout.strip()==hostkey
        assert 'RESTART_LOGIN_OK' in ssh(port,'printf RESTART_LOGIN_OK').stdout
        passed('restart_preserves_host_key_and_login')
        second,port2,hostkey2=start(2222)
        assert hostkey.split()[1]!=hostkey2.split()[1]
        assert 'CUSTOM_PORT_OK' in ssh(port2,'printf CUSTOM_PORT_OK').stdout
        passed('custom_container_port_2222')
        passed('new_container_gets_distinct_host_key')
    passed('temporary_client_private_keys_removed')
    result['status']='passed'
except Exception:
    result.update(status='failed',error=traceback.format_exc())
    print(result['error'],flush=True)
finally:
    for name in containers:
        p=run(['docker','logs',name],check=False)
        (REPORT/(name+'.log')).write_text(p.stdout)
    cleanup=[]
    for name in containers:cleanup.append(run(['docker','rm','-f',name],check=False).returncode==0)
    result['containers_cleaned']=all(cleanup)
    if not result['containers_cleaned']:result['status']='failed'
    (REPORT/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    log.close()
    target=OUT/('room_image_ssh_'+STAMP+'.tar.gz')
    part=target.with_name(target.name+'.part')
    with part.open('wb') as raw,gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as gz:
        with tarfile.open(fileobj=gz,mode='w') as tar:
            for p in sorted(REPORT.iterdir()):
                if p.is_file() and not p.is_symlink():
                    data=p.read_bytes();info=tarfile.TarInfo(target.name[:-7]+'/'+p.name)
                    info.size=len(data);info.mode=0o644;info.mtime=info.uid=info.gid=0
                    tar.addfile(info,io.BytesIO(data))
    part.replace(target)
    print('RESULT',result['status'],flush=True)
    print('RETURN_TAR',target,flush=True)
raise SystemExit(0 if result['status']=='passed' else 1)
