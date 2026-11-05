"""Install a pinned portable Blender and test rendering without any paid API calls."""
import argparse
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request
import urllib.error
import uuid

FOLDER='blender-5.1.2-linux-x64'
SHA256='aaccb355f50183979b698bcce7467103a76261b5fa59f4972295842662a285fb'
URL='https://download.blender.org/release/Blender5.1/'+FOLDER+'.tar.xz'
PACKAGE=Path(__file__).resolve().parent


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def save(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def validate_members(members):
    for m in members:
        p=PurePosixPath(m.name)
        if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0]!=FOLDER:
            raise ValueError('Unexpected Blender archive path')
        if not (m.isfile() or m.isdir() or m.issym() or m.islnk()):raise ValueError('Unexpected special file')
        if m.issym() or m.islnk():
            if PurePosixPath(m.linkname).is_absolute():raise ValueError('Absolute archive link')
            base=posixpath.dirname(m.name) if m.issym() else ''
            target=PurePosixPath(posixpath.normpath(posixpath.join(base,m.linkname)))
            if not target.parts or target.parts[0]!=FOLDER:raise ValueError('Escaping archive link')


def install(root,archive_path=None,download_report=None):
    download_report=download_report if download_report is not None else {}
    download_report['stage']='install_check'
    tools=root/'return_output/tools';tools.mkdir(parents=True,exist_ok=True)
    target=tools/FOLDER;marker=target/'.room_install_sha256'
    if target.exists():
        if not marker.is_file() or marker.read_text().strip()!=SHA256:
            raise RuntimeError('Existing Blender directory is not this verified installation: '+str(target))
        return target/'blender'
    if shutil.disk_usage(tools).free<5*1024**3:raise RuntimeError('At least 5 GiB free space required')
    cache=root/'return_output/cache/blender';cache.mkdir(parents=True,exist_ok=True)
    archive=Path(archive_path).absolute() if archive_path else cache/(FOLDER+'.tar.xz')
    download_report['source']='uploaded_archive' if archive_path else 'cache_or_official_download'
    if archive_path and not archive.is_file():raise RuntimeError('Uploaded Blender archive not found: '+str(archive))
    if not archive.exists():
        download_report['stage']='download'
        download_report['url']=URL
        download_report['attempts']=[]
        print('Downloading official Blender 5.1.2; first download may take several minutes.',flush=True)
        for attempt in range(3):
            part=cache/(FOLDER+'.'+uuid.uuid4().hex[:8]+'.part')
            started=time.monotonic();last=started;total=0
            item={'attempt':attempt+1};download_report['attempts'].append(item)
            try:
                request=urllib.request.Request(URL,headers={'User-Agent':'Mozilla/5.0 Room-Blender-Setup/0.1.1'})
                with urllib.request.urlopen(request,timeout=60) as response,part.open('wb') as f:
                    while True:
                        chunk=response.read(1024*1024)
                        if not chunk:break
                        f.write(chunk);total+=len(chunk)
                        if time.monotonic()-last>20:
                            print('Downloaded %.1f MiB'%(total/1024**2),flush=True);last=time.monotonic()
                        if time.monotonic()-started>1800:raise TimeoutError('Blender download exceeded 30 minutes')
                if digest(part)!=SHA256:raise ValueError('Official Blender checksum mismatch')
                part.replace(archive);item.update(status='verified',bytes=total);break
            except urllib.error.HTTPError as exc:
                item.update(status='failed',error_type='HTTPError',http_status=exc.code)
                if exc.code not in (408,429,500,502,503,504) or attempt==2:raise
                time.sleep(2*(attempt+1))
            except (urllib.error.URLError,TimeoutError) as exc:
                item.update(status='failed',error_type=type(exc).__name__)
                if attempt==2:raise
                time.sleep(2*(attempt+1))
            finally:
                if part.exists():part.unlink()
    download_report['stage']='checksum'
    if digest(archive)!=SHA256:raise ValueError('Blender archive checksum mismatch')
    download_report['sha256']=SHA256
    download_report['stage']='extract'
    print('Verified official archive; extracting portable installation.',flush=True)
    with tempfile.TemporaryDirectory(prefix='blender-install-',dir=tools) as temp:
        with tarfile.open(archive) as tar:
            validate_members(tar.getmembers())
            # Fixed official hash, paths and links checked; supports school Python 3.10.
            tar.extractall(temp)
        (Path(temp)/FOLDER).rename(target)
    marker.write_text(SHA256+'\n')
    download_report['stage']='installed'
    return target/'blender'


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--project-root',type=Path,default=Path('/yinxin/projects/room'))
    ap.add_argument('--archive',type=Path,help='Use an uploaded official Blender tar.xz without downloading')
    ap.add_argument('--probe-only',action='store_true');args=ap.parse_args();root=args.project_root.absolute()
    name='room_blender_setup_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ_')+uuid.uuid4().hex[:8]
    report=root/'return_output/artifacts'/name;report.mkdir(parents=True)
    result=dict(status='started',full_agent_run=False,paid_model_calls=False,synthetic_geometry_only=True)
    env=dict(os.environ)
    for key in list(env):
        if key.startswith(('OPENAI_','ANTHROPIC_','GEMINI_','GOOGLE_','MODAL_','CLAUDE_','CODEX_')):env.pop(key,None)
    def step(label,cmd,timeout=120):
        print(label+'; log: '+str(report/(label+'.log')),flush=True)
        try:
            with (report/(label+'.log')).open('w') as f:
                proc=subprocess.run([str(v) for v in cmd],stdout=f,stderr=subprocess.STDOUT,env=env,
                    cwd=report,timeout=timeout)
            return {'exit_code':proc.returncode}
        except subprocess.TimeoutExpired:return {'error':'timeout'}
        except OSError as exc:return {'error_type':type(exc).__name__}
    try:
        if platform.system()!='Linux' or platform.machine() not in ('x86_64','AMD64'):
            raise RuntimeError('This package targets the school Linux x86_64 container')
        found={n:shutil.which(n) for n in ('codex','claude','nvcc','gcc','g++','nvidia-smi')}
        local_codex=Path.home()/'.local/bin/codex'
        if not found['codex'] and local_codex.is_file():found['codex']=str(local_codex)
        nvcc_candidates=sorted(Path('/usr/local').glob('cuda*/bin/nvcc'))
        if not found['nvcc'] and nvcc_candidates:found['nvcc']=str(nvcc_candidates[-1])
        result['tools']=found
        result['credential_env_present']={k:bool(os.environ.get(k)) for k in ('OPENAI_API_KEY','ANTHROPIC_API_KEY','GEMINI_API_KEY')}
        result['credential_note']='Presence only; no login files read and no model/API requests made.'
        for name_,cmd in [('codex',['--version']),('nvcc',['--version']),
                          ('nvidia-smi',['--query-gpu=name,memory.total,driver_version','--format=csv,noheader'])]:
            if found[name_]:result[name_]=step(name_,[found[name_]]+cmd)
        result['gpu_backend_interpreters']={k:bool(os.environ.get(k) and Path(os.environ[k]).is_file())
            for k in ('TRELLIS_PYTHON','GROUNDING_DINO_PYTHON')}
        result['gpu_backend_note']='Only configured interpreter existence checked; weights and inference not tested.'
        if args.probe_only:result['status']='probe_complete'
        else:
            uploaded=args.archive
            if uploaded is None and (root/'scripts'/(FOLDER+'.tar.xz')).is_file():uploaded=root/'scripts'/(FOLDER+'.tar.xz')
            result['download']={}
            blender=install(root,archive_path=uploaded,download_report=result['download']);result['blender_path']=str(blender)
            result['stage']='blender_start'
            result['blender_archive_sha256']=SHA256
            result['blender_version']=step('blender_version',[blender,'--version'])
            if result['blender_version'].get('exit_code')!=0:raise RuntimeError('Blender cannot start; see blender_version.log for missing system libraries')
            checks={}
            result['stage']='render_checks'
            for mode in ('cpu','cuda','eevee'):
                checks[mode]=step('render_'+mode,[blender,'--background','--factory-startup','--python-exit-code','1',
                    '--python',PACKAGE/'smoke.py','--','--mode',mode,'--output',report],timeout=600)
                checks[mode]['passed']=checks[mode].get('exit_code')==0 and (report/(mode+'.json')).is_file()
            result['render_checks']=checks
            result['status']='blender_checks_passed' if all(v['passed'] for v in checks.values()) else 'blender_checks_incomplete'
            save(root/'return_output/tools/blender-current.json',{'binary':str(blender),'install_directory':str(blender.parent),
                'version':'5.1.2','archive_sha256':SHA256,'checks':checks})
        result['remaining']=['Codex adaptation and image-generation service access are verified separately from Blender.',
            'TRELLIS.2 and detection model environments still need installation and actual inference tests.',
            'CUDA runtime availability is distinct from the nvcc compiler required for native extensions.']
    except Exception as exc:
        result.update(status='failed',error_type=type(exc).__name__)
        if 'stage' not in result:result['stage']=result.get('download',{}).get('stage','environment_probe')
        if isinstance(exc,urllib.error.HTTPError):result['http_status']=exc.code
        # Avoid including request/proxy error strings in the returned archive.
        if isinstance(exc,(RuntimeError,ValueError)):result['error']=str(exc)
        print('Setup failed: '+type(exc).__name__,flush=True)
    finally:
        save(report/'result.json',result)
        files=[p for p in sorted(report.iterdir()) if p.is_file()]
        save(report/'manifest_sha256.json',{p.name:digest(p) for p in files})
        archive=root/'return_output'/(name+'.tar.gz')
        with archive.open('wb') as raw,gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as gz:
            with tarfile.open(fileobj=gz,mode='w') as tar:
                for p in files+[report/'manifest_sha256.json']:
                    info=tarfile.TarInfo(name+'/'+p.name);info.size=p.stat().st_size;info.mode=0o644;info.mtime=0
                    with p.open('rb') as f:tar.addfile(info,f)
        print('RESULT: '+result['status']);print('RETURN ARCHIVE: '+str(archive))
    return 0 if result['status'] in ('blender_checks_passed','probe_complete') else 1


if __name__=='__main__':raise SystemExit(main())
