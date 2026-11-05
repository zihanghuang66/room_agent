"""Package the verified v012 application with isolated generic scan runner."""
import ast
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

HERE=Path(__file__).resolve().parent
PREFIX='room-litereality-full-v0.2.2'

def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def build():
    pins=json.loads((HERE/'repair_pins.json').read_text())
    for file,key in [('app.tar.gz','app_sha256')]:
        if digest(HERE/file)!=pins[key]:raise RuntimeError('Pinned payload changed: '+file)
    (HERE/'app.sha256').write_text(pins['app_sha256']+'\n')
    names=['app.tar.gz','app.sha256','repair_pins.json','common.py',
           'image_probe.py','pins.json','check_extensions.py','blender_smoke.py','room_image_backend.py',
           'fetch_weights.py','trellis_launcher.py','stage.py','preflight.py','verify_export.py','run.py',
           'runtime.py','blender_paths.py','capture_paths.py','refresh_seed.py','execution_smoke.py',
           'mcp_probe.py','run_test_03.sh','使用说明.md']
    manifest={PREFIX+'/'+n:digest(HERE/n) for n in names}
    (HERE/'package_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    for name in names:
        if name.endswith('.py'):ast.parse((HERE/name).read_text(),filename=name)
    output=HERE.parent/'packages'/f'{PREFIX}.tar.gz'
    with output.open('wb') as raw,gzip.GzipFile(fileobj=raw,filename='',mode='wb',mtime=0) as gz:
        with tarfile.open(fileobj=gz,mode='w') as tar:
            for name in names+['package_manifest.json']:
                p=HERE/name;info=tarfile.TarInfo(PREFIX+'/'+name)
                info.size=p.stat().st_size;info.mtime=0;info.mode=0o755 if name.endswith('.sh') else 0o644
                with p.open('rb') as stream:tar.addfile(info,stream)
    sha=digest(output)
    output.with_name(output.name+'.sha256').write_text(sha+'  '+output.name+'\n')
    print(output,output.stat().st_size,sha)

if __name__=='__main__':build()
