"""Blender: render the already-saved portable BLEND. Never execute Room.py here."""
import json
from pathlib import Path
import sys
import bpy

job=json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text())
preview=Path(job['preview']);capture=Path(job['capture']);out=Path(job['out']);out.mkdir(parents=True,exist_ok=True)
bpy.ops.wm.open_mainfile(filepath=str(preview/'Room.portable.blend'))
sc=bpy.context.scene
# Use the saved assembly pose and authored lighting, rather than resetting animation to an arbitrary frame.
samples=int(job.get('samples',32));res_div=int(job.get('res_div',2))
sc.render.engine='CYCLES';sc.cycles.samples=samples;sc.cycles.use_denoising=True
sc.cycles.device='CPU';device='CPU'
try:
    prefs=bpy.context.preferences.addons['cycles'].preferences
    prefs.compute_device_type='CUDA';prefs.get_devices()
    cuda=[d for d in prefs.devices if d.type=='CUDA']
    if cuda:
        for d in prefs.devices:d.use=(d.type=='CUDA')
        sc.cycles.device='GPU';device='CUDA'
except Exception as exc:print('CUDA not available; using Cycles CPU: '+str(exc),flush=True)
sc.render.image_settings.file_format='PNG';sc.render.resolution_percentage=100
frames=job['frames'];meta=[]
for fi in frames:
    cam=bpy.data.objects.get(f'cam_{fi:05d}')
    if cam is None or cam.type!='CAMERA':raise RuntimeError('Missing captured camera '+str(fi))
    photo=capture/f'frame_{fi:05d}.jpg'
    im=bpy.data.images.load(str(photo),check_existing=False)
    width,height=im.size[:];bpy.data.images.remove(im)
    sc.camera=cam;sc.render.resolution_x=max(1,width//res_div);sc.render.resolution_y=max(1,height//res_div)
    sc.render.filepath=str(out/f'frame_{fi:05d}.png')
    try:bpy.ops.render.render(write_still=True)
    except RuntimeError:
        if device!='CUDA':raise
        print('CUDA render failed; retrying this frame on CPU',flush=True)
        sc.cycles.device='CPU';device='CPU';bpy.ops.render.render(write_still=True)
    meta.append({'frame':fi,'device':device,'width':width//res_div,'height':height//res_div})
    print('FROZEN_RENDER_FRAME '+str(fi),flush=True)
(out/'render_info.json').write_text(json.dumps({'source':'Room.portable.blend','scene_frame':sc.frame_current,
    'engine':'CYCLES','samples':samples,'authored_lighting':True,'frames':meta},indent=2))
# Deliberately do not save/re-export the scene after rendering.
