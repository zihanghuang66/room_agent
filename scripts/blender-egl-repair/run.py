"""Repair missing GLVND user-space libraries and retest installed Blender."""
import argparse
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import resource
import shutil
import subprocess
import sys
import tarfile
import uuid

HERE=Path(__file__).resolve().parent
LIB_PACKAGES={'libEGL.so.1':'libegl1','libGL.so.1':'libgl1','libOpenGL.so.0':'libopengl0'}


def save(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def missing_packages(libraries):return [p for lib,p in LIB_PACKAGES.items() if not libraries.get(lib)]


def validate_plan(text):
    for line in text.splitlines():
        fields=line.split()
        if not fields:continue
        if fields[0]=='Remv':raise ValueError('APT plan removes packages; refusing automatic install')
        if fields[0] in ('Inst','Conf') and len(fields)>1:
            name=fields[1].lower()
            if any(x in name for x in ('nvidia','cuda','linux-image','linux-headers','dkms')):
                raise ValueError('APT plan touches driver or kernel packages; refusing automatic install')


def check_render(report,mode,execution):
    result=dict(execution,passed=False)
    try:
        data=json.loads((report/(mode+'.json')).read_text())
        with (report/(mode+'.png')).open('rb') as image:valid=image.read(8)==b'\x89PNG\r\n\x1a\n'
        result.update(passed=execution.get('exit_code')==0 and data.get('status')=='passed' and data.get('mode')==mode and valid,details=data)
        renderer=str(data.get('renderer','')).lower()
        result['software_renderer']=any(k in renderer for k in ('llvmpipe','softpipe','software rasterizer','swrast'))
    except (OSError,ValueError):pass
    return result


def pack_report(report,output):
    files=[p for p in sorted(report.iterdir()) if p.is_file() and p.name!='manifest_sha256.json']
    save(report/'manifest_sha256.json',{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
    archive=output/(report.name+'.tar.gz')
    with archive.open('wb') as raw,gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as gz:
        with tarfile.open(fileobj=gz,mode='w') as tar:
            for p in files+[report/'manifest_sha256.json']:
                info=tarfile.TarInfo(report.name+'/'+p.name);info.size=p.stat().st_size;info.mode=0o644;info.mtime=0
                with p.open('rb') as f:tar.addfile(info,f)
    return archive


def no_core_dump():resource.setrlimit(resource.RLIMIT_CORE,(0,0))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--project-root',type=Path,default=Path('/yinxin/projects/room'))
    ap.add_argument('--check-only',action='store_true',help='Collect diagnostics and retest without installing packages')
    args=ap.parse_args();root=args.project_root.absolute()
    name='room_blender_egl_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ_')+uuid.uuid4().hex[:8]
    report=root/'return_output/artifacts'/name;report.mkdir(parents=True)
    result={'status':'failed','version':'0.1.0','full_reconstruction_run':False,'paid_model_calls':False,'driver_packages_installed':False}
    env={k:v for k,v in os.environ.items() if not k.startswith(('OPENAI_','ANTHROPIC_','GEMINI_','GOOGLE_','MODAL_','CLAUDE_','CODEX_'))}
    env.update(LC_ALL='C',LANG='C',DEBIAN_FRONTEND='noninteractive',PYTHONDONTWRITEBYTECODE='1')
    def step(label,command,timeout=120):
        logfile=report/(label+'.log');print(label+'; log: '+str(logfile),flush=True)
        try:
            with logfile.open('w') as f:
                proc=subprocess.run([str(c) for c in command],cwd=report,env=env,stdin=subprocess.DEVNULL,
                    stdout=f,stderr=subprocess.STDOUT,timeout=timeout,start_new_session=True,preexec_fn=no_core_dump)
            return {'exit_code':proc.returncode}
        except subprocess.TimeoutExpired:return {'error':'timeout'}
        except OSError as exc:return {'error_type':type(exc).__name__}
        finally:
            if logfile.is_file():
                text=logfile.read_text(errors='replace')
                text=re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@',r'\1[redacted]@',text)
                text=re.sub(r'([?&](?:token|key|password|signature)=)[^\s&]+',r'\1[redacted]',text,flags=re.I)
                logfile.write_text(text)
    def graphics_probe(label):
        execution=step(label,[sys.executable,'-B',HERE/'probe.py'])
        if execution.get('exit_code')!=0:raise RuntimeError('Graphics probe failed: '+label)
        data=json.loads((report/(label+'.log')).read_text());save(report/(label+'.json'),data);return data
    try:
        if platform.system()!='Linux' or platform.machine() not in ('x86_64','AMD64'):raise RuntimeError('Use the school Linux x86_64 container')
        blender=root/'return_output/tools/blender-5.1.2-linux-x64/blender'
        if not blender.is_file():raise RuntimeError('Installed Blender 5.1.2 was not found; run the existing setup first')
        result['stage']='graphics_probe';before=graphics_probe('graphics_before')
        result['blender']=str(blender);needed=missing_packages(before['libraries']);result['missing_packages_before']=needed
        if needed and not args.check_only:
            result['stage']='install_userspace_libraries'
            apt=shutil.which('apt-get')
            if not apt:result['repair_error']='apt_get_unavailable'
            else:
                prefix=[]
                if os.geteuid()!=0:
                    sudo=shutil.which('sudo')
                    if sudo and step('sudo_check',[sudo,'-n','true']).get('exit_code')==0:prefix=[sudo,'-n']
                    else:result['repair_error']='root_or_passwordless_sudo_required'
                if 'repair_error' not in result:
                    opts=['-o','Acquire::Retries=2','-o','Acquire::http::Timeout=30','-o','Acquire::https::Timeout=30']
                    result['apt_update']=step('apt_update',prefix+[apt]+opts+['update'],600)
                    if result['apt_update'].get('exit_code')!=0:result['repair_error']='apt_update_failed'
                    else:
                        command=prefix+[apt]+opts+['install','--no-install-recommends','--no-upgrade','--no-remove']+needed
                        result['apt_plan']=step('apt_plan',command+['--simulate'],180)
                        if result['apt_plan'].get('exit_code')!=0:result['repair_error']='apt_plan_failed'
                        else:
                            validate_plan((report/'apt_plan.log').read_text())
                            result['apt_install']=step('apt_install',command+['-y'],600)
                            if result['apt_install'].get('exit_code')!=0:result['repair_error']='apt_install_failed'
        elif needed:result['repair_error']='check_only_install_skipped'
        result['stage']='retest';after=graphics_probe('graphics_after');result['missing_packages_after']=missing_packages(after['libraries'])
        nvidia=shutil.which('nvidia-smi')
        if nvidia:result['gpu']=step('nvidia-smi',[nvidia,'--query-gpu=name,memory.total,driver_version','--format=csv,noheader'])
        checks={}
        for mode in ('cpu','cuda','eevee'):
            checks[mode]=check_render(report,mode,step('render_'+mode,[blender,'--background','--factory-startup',
                '--python-exit-code','1','--python',HERE/'smoke.py','--','--mode',mode,'--output',report],600))
        result['render_checks']=checks
        if all(c['passed'] for c in checks.values()):
            result['status']='eevee_software_only' if checks['eevee'].get('software_renderer') else 'blender_checks_passed'
        else:result['status']='blender_checks_incomplete'
        if result['missing_packages_after']:result['next_action']='Resolve APT/permissions errors; EGL/OpenGL loader libraries are still unavailable.'
        elif not checks['eevee']['passed']:
            result['next_action']='Inspect render_eevee.log and graphics_after.json. Missing NVIDIA EGL libraries/vendor metadata may require graphics capability at container creation; this is not proven solely by a render failure.'
        elif result['status']=='eevee_software_only':result['next_action']='EEVEE rendered in software; verify NVIDIA graphics driver mounts before claiming GPU EEVEE support.'
        else:result['next_action']='Synthetic Blender checks passed; full LiteReality reconstruction remains untested.'
        result['container_note']='If NVIDIA graphics driver mounts are missing, request compute,utility,graphics at container creation. Exporting this variable inside an already running container does not add driver mounts. Do not replace the host NVIDIA driver.'
    except Exception as exc:
        result.update(status='failed',error_type=type(exc).__name__)
        if isinstance(exc,(RuntimeError,ValueError)):result['error']=str(exc)
    finally:
        save(report/'result.json',result);archive=pack_report(report,root/'return_output')
        print('RESULT: '+result['status']);print('RETURN ARCHIVE: '+str(archive))
    return 0 if result['status']=='blender_checks_passed' else 1

if __name__=='__main__':raise SystemExit(main())
