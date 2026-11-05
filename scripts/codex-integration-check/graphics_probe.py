"""Read only selected graphics metadata; never read authentication files."""
import ctypes
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess


def command(args):
    try:
        done=subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=10)
        return {'returncode':done.returncode,'output':done.stdout[-16000:]}
    except (OSError,subprocess.TimeoutExpired) as exc:return {'error':str(exc)}


def nvidia_files():
    roots=('/usr/lib/x86_64-linux-gnu','/usr/lib64','/usr/local/nvidia','/usr/lib/nvidia','/lib/x86_64-linux-gnu','/opt/nvidia')
    found={};scanned=0;limited=False
    for root in roots:
        for current,dirs,files in os.walk(root,followlinks=False):
            depth=len(Path(current).relative_to(root).parts)
            if depth>=3:dirs[:]=[]
            for name in files:
                scanned+=1
                if scanned>40000:limited=True;break
                if not name.startswith(('libEGL_nvidia.so','libGLX_nvidia.so','libnvidia-eglcore.so','libnvidia-glcore.so','libnvidia-glsi.so','libnvidia-gpucomp.so')):continue
                p=Path(current)/name;real=p.resolve()
                # Only inspect system-supplied libraries, not user-controlled scan contents.
                if not any(str(real).startswith(prefix) for prefix in ('/usr/','/lib/','/lib64/','/opt/nvidia/')):continue
                found[str(p)]={'path':str(p),'realpath':str(real),'exists':p.is_file()}
            if limited:break
        if limited:break
    return list(found.values()),{'roots':roots,'max_depth':3,'files_examined':scanned,'limited':limited}


def probe():
    libraries={};errors={}
    for name in ('libEGL.so.1','libGL.so.1','libOpenGL.so.0','libEGL_nvidia.so.0','libGLX_nvidia.so.0','libcuda.so.1'):
        try:ctypes.CDLL(name);libraries[name]=True
        except OSError as exc:libraries[name]=False;errors[name]=str(exc)
    vendors=[]
    for folder in ('/usr/share/glvnd/egl_vendor.d','/etc/glvnd/egl_vendor.d'):
        for p in sorted(Path(folder).glob('*.json')):
            try:
                data=json.loads(p.read_text());vendors.append({'file':str(p),'library_path':data.get('ICD',{}).get('library_path')})
            except (OSError,ValueError):vendors.append({'file':str(p),'unreadable':True})
    files,search=nvidia_files();dependencies={}
    if shutil.which('ldd'):
        targets=sorted({f['realpath'] for f in files if f['exists'] and Path(f['realpath']).name.startswith(('libEGL_nvidia.so','libGLX_nvidia.so'))})[:8]
        for path in targets:dependencies[path]=command(['ldd',path])
    driver=command(['nvidia-smi','--query-gpu=driver_version','--format=csv,noheader']) if shutil.which('nvidia-smi') else {}
    versions=set(re.findall(r'^\d+\.\d+(?:\.\d+)?$',driver.get('output',''),re.M))
    return {'platform':platform.system(),'architecture':platform.machine(),
        'os_release':Path('/etc/os-release').read_text() if Path('/etc/os-release').is_file() else None,
        'libraries':libraries,'library_load_errors':errors,'egl_vendors':vendors,
        'nvidia_files':files,'library_search':search,'dependencies':dependencies,
        'driver_query':driver,'driver_version':next(iter(versions)) if len(versions)==1 else None,
        'graphics_overrides':{k:os.environ.get(k) for k in ('LD_LIBRARY_PATH','LIBGL_ALWAYS_SOFTWARE','MESA_LOADER_DRIVER_OVERRIDE','__EGL_VENDOR_LIBRARY_FILENAMES','__EGL_VENDOR_LIBRARY_DIRS')},
        'container_graphics_env':{k:os.environ.get(k) for k in ('NVIDIA_DRIVER_CAPABILITIES','NVIDIA_VISIBLE_DEVICES','CUDA_VISIBLE_DEVICES')},
        'render_nodes':[str(p) for p in sorted(Path('/dev/dri').glob('renderD*'))]}

if __name__=='__main__':print(json.dumps(probe(),indent=2))
