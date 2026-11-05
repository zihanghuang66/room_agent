"""Process-local EGL repair using existing, exactly driver-matched system libraries."""
import json
import os
from pathlib import Path
import subprocess
import sys


def select_egl(data):
    version=data.get('driver_version')
    if not version:return None
    matches=sorted({f['realpath'] for f in data.get('nvidia_files',[]) if f.get('exists') and Path(f['realpath']).name=='libEGL_nvidia.so.'+version})
    return matches[0] if matches else None


def nvidia_render_ok(artifacts):
    data=artifacts.get('details',{})
    renderer=str(data.get('renderer','')).lower();vendor=str(data.get('vendor','')).lower()
    return bool(artifacts.get('passed') and renderer and 'nvidia' in (renderer+' '+vendor)
        and not any(s in renderer for s in ('llvmpipe','softpipe','software')))


def prepare(report,data):
    egl=select_egl(data)
    if not egl:
        return {'status':'blocked','reason':'No existing NVIDIA EGL library with a verified exact host-driver version was found in searched system paths. No driver was installed. See graphics_details.json for paths and loader errors.'},None
    overrides=data.get('graphics_overrides',{})
    if overrides.get('LIBGL_ALWAYS_SOFTWARE') not in (None,'','0','false','False') or overrides.get('MESA_LOADER_DRIVER_OVERRIDE'):
        return {'status':'blocked','reason':'Explicit software-rendering overrides are present; retained for diagnosis, not silently removed.'},None
    version=data['driver_version']
    dirs=sorted({str(Path(f['realpath']).parent) for f in data['nvidia_files'] if f.get('exists') and f['realpath'].endswith('.so.'+version)})
    original=os.environ.get('LD_LIBRARY_PATH','')
    update={'LD_LIBRARY_PATH':os.pathsep.join(dirs+([original] if original else []))}
    env=dict(os.environ,**update)
    code='import ctypes,sys;ctypes.CDLL(sys.argv[1]);print("NVIDIA_EGL_LOAD_OK")'
    try:
        done=subprocess.run([sys.executable,'-c',code,egl],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=15)
        (report/'graphics_library_check.log').write_text(done.stdout)
    except (OSError,subprocess.TimeoutExpired) as exc:
        return {'status':'failed','reason':str(exc)},None
    if done.returncode!=0:
        return {'status':'blocked','reason':'Exact-version library exists but cannot load even with candidate search paths. Inspect graphics_library_check.log; dependencies may be missing.','returncode':done.returncode,'loader_error':done.stdout[-4000:]},None
    vendor=report/'nvidia_egl_candidate.json'
    vendor.write_text(json.dumps({'file_format_version':'1.0.0','ICD':{'library_path':egl}},indent=2)+'\n')
    update['__EGL_VENDOR_LIBRARY_FILENAMES']=str(vendor)
    return {'status':'candidate_ready','library':egl,'driver_version':version,'environment_updates':update,
        'reason':'Library load passed. GPU rendering and CUDA regression must still pass; this is not a repair-success verdict.'},dict(os.environ,**update)
