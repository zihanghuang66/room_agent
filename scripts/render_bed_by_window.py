"""Create a separate, dimension-preserving bed-placement variant and render it."""
import bpy, sys, math, json
from pathlib import Path
from mathutils import Vector, Matrix

args=sys.argv[sys.argv.index('--')+1:]
src,out=Path(args[0]).resolve(),Path(args[1]).resolve()
preview='--preview' in args
out.mkdir(parents=True,exist_ok=True)
bpy.ops.wm.open_mainfile(filepath=str(src))
scene=bpy.context.scene
a=math.radians(10.67);c,s=math.cos(a),math.sin(a)
def world(x,y,z):return Vector((c*x-s*y,s*x+c*y,z-1.6592))
bed=bpy.data.objects['Bed0']
before=bed.matrix_world.copy()
shift=.1999
bed.matrix_world=Matrix.Translation(Vector((c*shift,s*shift,0)))@before
bpy.context.view_layer.update()
def q(p):return (c*p.x+s*p.y,-s*p.x+c*p.y,p.z+1.6592)
def points(root):
 result=[]
 dg=bpy.context.evaluated_depsgraph_get()
 for obj in [root]+list(root.children_recursive):
  if obj.type!='MESH':continue
  ev=obj.evaluated_get(dg);mesh=ev.to_mesh()
  result.extend(q(ev.matrix_world@v.co) for v in mesh.vertices)
  ev.to_mesh_clear()
 return result
bed_points=points(bed)
window_front=min(p[0] for p in points(bpy.data.objects['WindowBench']))
headboard_gap=window_front-max(p[0] for p in bed_points)
body_gap=window_front-max(p[0] for p in points(bpy.data.objects['geometry_0']) if p[1]<3.9)
assert headboard_gap>=0, 'Bed would penetrate window bench'

data=bpy.data.cameras.new('Bed layout comparison camera')
cam=bpy.data.objects.new('Bed layout comparison camera',data)
scene.collection.objects.link(cam)
cam.location=world(1.90,1.48,1.86)
target=world(3.05,3.20,.80)
cam.rotation_euler=(target-cam.location).to_track_quat('-Z','Y').to_euler()
data.lens=20;data.sensor_width=36;data.clip_start=.03
scene.camera=cam
scene.render.engine='CYCLES'
scene.cycles.samples=32 if preview else 192
scene.cycles.use_denoising=True
scene.cycles.adaptive_threshold=.05 if preview else .015
scene.cycles.max_bounces=10
scene.cycles.diffuse_bounces=6
scene.cycles.glossy_bounces=6
scene.cycles.transmission_bounces=8
scene.cycles.device='CPU'
prefs=bpy.context.preferences.addons['cycles'].preferences
try:
 prefs.compute_device_type='METAL';prefs.get_devices()
 for d in prefs.devices:d.use=d.type=='METAL'
 if any(d.type=='METAL' for d in prefs.devices):scene.cycles.device='GPU'
except Exception as e:print('METAL unavailable:',e,flush=True)
scene.render.resolution_x=800 if preview else 1600
scene.render.resolution_y=600 if preview else 1200
scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG'
scene.view_settings.view_transform='AgX'
scene.view_settings.exposure=.5
scene.render.film_transparent=False
scene.render.use_border=False
scene.use_nodes=False
scene.render.filepath=str(out/('preview.png' if preview else 'bed_by_window.png'))
metadata={'source':str(src),'translation_room_x_m':shift,'bed_body_gap_m':.284999367-shift,'headboard_gap_m':.199999863-shift,'stone_sill_length_m':2.7337075865,'stone_sill_depth_m':.62000033,'window_base_length_m':2.93500022,'engine':scene.render.engine,'device':scene.cycles.device,'samples':scene.cycles.samples,'camera_room':[1.90,1.64,1.86],'target_room':[3.20,3.35,.80],'other_furniture_moved':False}
metadata.update(bed_body_gap_m=body_gap,headboard_gap_m=headboard_gap,window_bench_separation_verified=True)
metadata.update(camera_room=[1.90,1.48,1.86],target_room=[3.05,3.20,.80])
(out/'layout_change.json').write_text(json.dumps(metadata,indent=2))
if not preview:
 bpy.ops.wm.save_as_mainfile(filepath=str(out/'Room.bed_by_window.blend'))
print('RENDER_START',metadata,flush=True)
bpy.ops.render.render(write_still=True)
if not preview:
 bed.matrix_world=before
 bpy.context.view_layer.update()
 scene.render.filepath=str(out/'bed_original.png')
 bpy.ops.render.render(write_still=True)
print('RENDER_COMPLETE',flush=True)
