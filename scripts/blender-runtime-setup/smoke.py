"""Execute inside Blender: a synthetic cube, not reconstruction of the user's scan."""
import argparse
import json
from pathlib import Path
import sys
import bpy

p=argparse.ArgumentParser()
p.add_argument('--mode',choices=['cpu','cuda','eevee'],required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);a.output.mkdir(parents=True,exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
scene=bpy.context.scene
bpy.ops.mesh.primitive_cube_add(size=2)
cube=bpy.context.object;cube.name='Connectivity_Test_Cube'
mat=bpy.data.materials.new('TestMaterial');mat.diffuse_color=(0.1,0.5,0.8,1)
cube.data.materials.append(mat)
bpy.ops.object.camera_add(location=(4,-5,3))
camera=bpy.context.object;camera.rotation_euler=(-camera.location).to_track_quat('-Z','Y').to_euler()
scene.camera=camera
bpy.ops.object.light_add(type='AREA',location=(3,-4,5))
light=bpy.context.object;light.data.energy=800;light.data.shape='DISK';light.data.size=5
light.rotation_euler=(-light.location).to_track_quat('-Z','Y').to_euler()
scene.world=bpy.data.worlds.new('World');scene.world.color=(0.15,0.15,0.15)
scene.render.resolution_x=128;scene.render.resolution_y=128;scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG';scene.render.filepath=str(a.output/(a.mode+'.png'))
result={'blender_version':bpy.app.version_string,'mode':a.mode,'synthetic_test_only':True}
if a.mode=='eevee':
    for name in ('BLENDER_EEVEE','BLENDER_EEVEE_NEXT'):
        try:scene.render.engine=name;break
        except TypeError:continue
    else:raise RuntimeError('No supported EEVEE engine')
else:
    scene.render.engine='CYCLES';scene.cycles.samples=8;scene.cycles.use_denoising=False
    scene.cycles.device='CPU'
    if a.mode=='cuda':
        preferences=bpy.context.preferences.addons['cycles'].preferences
        preferences.compute_device_type='CUDA';preferences.get_devices()
        devices=[]
        for device in preferences.devices:
            device.use=device.type=='CUDA'
            if device.use:devices.append(device.name)
        if not devices:raise RuntimeError('No CUDA device available to Blender')
        result['cuda_devices']=devices;scene.cycles.device='GPU'
bpy.ops.render.render(write_still=True)
if a.mode=='cpu':
    bpy.ops.wm.save_as_mainfile(filepath=str(a.output/'test_cube.blend'))
    bpy.ops.export_scene.gltf(filepath=str(a.output/'test_cube.glb'),export_format='GLB')
    if (a.output/'test_cube.glb').read_bytes()[:4]!=b'glTF':raise RuntimeError('Invalid GLB header')
result['status']='passed'
(a.output/(a.mode+'.json')).write_text(json.dumps(result,indent=2)+'\n')
