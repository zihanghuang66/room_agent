"""Read only selected graphics metadata; never read authentication files."""
import ctypes
import json
import os
from pathlib import Path
import platform


def probe():
    libraries={}
    for name in ('libEGL.so.1','libGL.so.1','libOpenGL.so.0','libEGL_nvidia.so.0','libGLX_nvidia.so.0','libcuda.so.1'):
        try:ctypes.CDLL(name);libraries[name]=True
        except OSError:libraries[name]=False
    vendors=[]
    for folder in ('/usr/share/glvnd/egl_vendor.d','/etc/glvnd/egl_vendor.d'):
        for p in sorted(Path(folder).glob('*.json')):
            try:
                data=json.loads(p.read_text());vendors.append({'file':str(p),'library_path':data.get('ICD',{}).get('library_path')})
            except (OSError,ValueError):vendors.append({'file':str(p),'unreadable':True})
    return {'platform':platform.system(),'architecture':platform.machine(),
        'os_release':Path('/etc/os-release').read_text() if Path('/etc/os-release').is_file() else None,
        'libraries':libraries,'egl_vendors':vendors,
        'container_graphics_env':{k:os.environ.get(k) for k in ('NVIDIA_DRIVER_CAPABILITIES','NVIDIA_VISIBLE_DEVICES','CUDA_VISIBLE_DEVICES')},
        'render_nodes':[str(p) for p in sorted(Path('/dev/dri').glob('renderD*'))]}

if __name__=='__main__':print(json.dumps(probe(),indent=2))
