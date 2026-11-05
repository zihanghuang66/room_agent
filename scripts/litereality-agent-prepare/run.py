"""Prepare isolated upstream app and test capture extraction, not paid Agent stages."""
import argparse
import datetime
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import traceback
import uuid

from common import REVISION,SOURCE_SHA,SCAN_NAME,SCAN_SHA,digest,extract_checked,save,pack_report,executable_path,find_capture,frame_files
from probe import collect

PACKAGE=Path(__file__).resolve().parent


def ensure_extract(archive,target,expected,strip_first=False):
    if not target.exists():
        extract_checked(archive,target,expected,strip_first)
        return
    if digest(archive)!=expected:raise ValueError('Archive SHA256 mismatch')
    # Reuse only if all files still match the verified archive; no silent overwrites.
    import hashlib
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if not member.isfile():continue
            parts=Path(member.name).parts[1:] if strip_first else Path(member.name).parts
            path=target.joinpath(*parts)
            if not path.is_file() or path.is_symlink() or digest(path)!=hashlib.sha256(tar.extractfile(member).read()).hexdigest():
                raise ValueError('Existing extracted input/source differs: '+str(path))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project-root',type=Path,default=Path('/yinxin/projects/room'))
    ap.add_argument('--scan-archive',type=Path)
    ap.add_argument('--scene-name',default='studio_2573')
    ap.add_argument('--expected-scan-sha256',help='Optional independently supplied SHA256; otherwise record the server file hash')
    ap.add_argument('--app-python',type=Path,help='Already installed pinned app environment; skip install')
    ap.add_argument('--probe-only',action='store_true')
    ap.add_argument('--skip-network-probe',action='store_true')
    args=ap.parse_args();root=args.project_root.resolve()
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.scene_name):ap.error('Invalid scene name')
    name='room_litereality_prepare_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8]
    report=root/'return_output/artifacts'/name;report.mkdir(parents=True)
    result=dict(status='failed',upstream_revision=REVISION,full_agent_run=False,paid_model_calls=False)
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1',OMP_NUM_THREADS='4',OPENBLAS_NUM_THREADS='4')
    env.pop('PYTHONPATH',None)
    for key in list(env):
        if key.startswith(('OPENAI_','ANTHROPIC_','GEMINI_','GOOGLE_','MODAL_','CLAUDE_','CODEX_','LR_','UV_INDEX','UV_EXTRA_INDEX')):
            env.pop(key,None)
    env.update(UV_CACHE_DIR=str(root/'return_output/cache/litereality-agent/uv'),
        UV_PYTHON_DOWNLOADS='never',UV_NO_CONFIG='1',UV_HTTP_TIMEOUT='120',
        XDG_CACHE_HOME=str(root/'return_output/cache/litereality-agent/xdg'),
        HF_HOME=str(root/'return_output/cache/litereality-agent/huggingface'),
        MPLCONFIGDIR=str(report/'matplotlib_cache'))
    def step(name,command,cwd=root,timeout=1800):
        print(name+'; log: '+str(report/(name+'.log')),flush=True)
        with (report/(name+'.log')).open('w') as log:
            subprocess.run([str(v) for v in command],cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=timeout)
    try:
        print('Checking capabilities and public HTTPS endpoints. No credentials are sent.',flush=True)
        save(report/'capabilities.json',collect(not args.skip_network_probe))
        result['free_disk_bytes']=shutil.disk_usage(root).free
        if args.probe_only:
            result['status']='probe_completed_no_install'
        else:
            source=root/'scripts/vendor'/('litereality-agent-'+REVISION)
            ensure_extract(PACKAGE/'upstream-source.tar.gz',source,SOURCE_SHA,True)
            env['LR_REPO_ROOT']=str(source)
            python=executable_path(args.app_python) if args.app_python else None
            if python is None:
                if not (3,10)<=sys.version_info[:2]<(3,13):raise RuntimeError('Use Python 3.10, 3.11 or 3.12 via ROOM_PYTHON')
                if result['free_disk_bytes']<4*1024**3:raise RuntimeError('At least 4 GiB free disk requested for dependencies/cache')
                bootstrap=root/'return_output/environments/litereality-bootstrap-v0.1.0'
                bootstrap_python=bootstrap/'bin/python'
                if not bootstrap_python.is_file():
                    step('create_bootstrap',[sys.executable,'-m','venv','--without-pip',bootstrap])
                step('install_uv',[sys.executable,'-m','pip','--isolated','--disable-pip-version-check',
                    '--python',bootstrap_python,'install','--index-url','https://pypi.org/simple',
                    '--cache-dir',root/'return_output/cache/litereality-agent/pip','uv==0.9.26'])
                uv=bootstrap/'bin/uv'
                venv=root/'return_output/environments'/('litereality-agent-'+REVISION[:7])
                env['UV_PROJECT_ENVIRONMENT']=str(venv)
                step('install_app',[uv,'sync','--frozen','--no-dev','--python',sys.executable,
                    '--default-index','https://pypi.org/simple','--project',source],timeout=3600)
                python=venv/'bin/python'
                if sys.platform.startswith('linux'):
                    # The upstream lock omits ipywidgets required by the Linux Open3D wheel.
                    # Keep upstream untouched, add a hash-pinned overlay, constrain every
                    # already installed distribution so the repair cannot upgrade the base.
                    constraints=report/'baseline_constraints.txt'
                    step('capture_baseline',[python,'-c',
                        'from importlib.metadata import distributions; from pathlib import Path; import sys; '
                        'Path(sys.argv[1]).write_text("\\n".join(sorted(d.metadata["Name"]+"=="+d.version for d in distributions()))+"\\n")',
                        constraints])
                    step('install_linux_compatibility',[uv,'pip','install','--python',python,
                        '--index-url','https://pypi.org/simple','--only-binary',':all:',
                        '--require-hashes','--constraint',constraints,
                        '--requirements',PACKAGE/'linux-compatibility.txt'])
                    result['linux_compatibility_overlay_sha256']=digest(PACKAGE/'linux-compatibility.txt')
                step('pip_check',[uv,'pip','check','--python',python])
                step('environment_freeze',[uv,'pip','freeze','--python',python])
            step('cli_help',[python,'-m','litereality_agent.cli','--help'])
            scan_archive=args.scan_archive
            if scan_archive is None:
                candidates=[root/'data/raw/studio_2573'/SCAN_NAME,root/'scripts'/SCAN_NAME]
                scan_archive=next((p for p in candidates if p.is_file()),None)
            if scan_archive is None:
                result.update(status='environment_ready_scan_missing',scan_expected=str(root/'data/raw/studio_2573'/SCAN_NAME))
            else:
                actual_sha=digest(scan_archive)
                expected=args.expected_scan_sha256 or (SCAN_SHA if scan_archive.name==SCAN_NAME else actual_sha)
                result.update(scene_name=args.scene_name,scan_archive=str(scan_archive),scan_sha256=actual_sha,
                    independently_verified_scan_hash=bool(args.expected_scan_sha256 or scan_archive.name==SCAN_NAME))
                expanded=root/'data/raw'/args.scene_name/('capture_'+actual_sha[:16])
                ensure_extract(scan_archive.resolve(),expanded,expected)
                scan=find_capture(expanded)
                result['input_frame_count']=len(frame_files(scan))
                result['capture_directory']=str(scan)
                step('offline_extract',[python,'-B',PACKAGE/'extract_worker.py','--scan',scan,'--report',report,
                    '--scene-name',args.scene_name],timeout=1800)
                result.update(status='offline_extraction_passed_full_agent_not_run',
                    extraction=json.loads((report/'extraction_summary.json').read_text()))
            (root/'return_output/environments').mkdir(parents=True,exist_ok=True)
            save(root/'return_output/environments/litereality-current.json',dict(
                python=str(python),source=str(source),revision=REVISION,stage='base_app_only'))
        result['outstanding']=['This package does not test Agent authentication, image-generation API or model access.',
            'Blender 5.x plus separate TRELLIS.2 and GroundingDINO GPU environments are not installed by this package.',
            'Public ingest also invokes models; this offline extraction is NOT a completed ingest stage.']
    except Exception as exc:
        # Commands contain only local paths and public package names; never include credentials.
        result.update(status='failed',error=str(exc),traceback=traceback.format_exc())
        print('FAILED: '+str(exc),flush=True)
    finally:
        save(report/'result.json',result)
        save(report/'scripts_sha256.json',{p.name:digest(p) for p in PACKAGE.iterdir() if p.is_file()})
        shutil.copyfile(PACKAGE/'使用说明.md',report/'使用说明.md')
        print('RETURN ARCHIVE: '+str(pack_report(report)),flush=True)
    return 1 if result['status']=='failed' else 0


if __name__=='__main__':sys.exit(main())
