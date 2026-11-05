"""Make a separate Blender material demo; leave the source and geometry unchanged."""
import bpy,sys,json,hashlib,math
from pathlib import Path
from mathutils import Vector
source,out_dir=map(Path,sys.argv[sys.argv.index('--')+1:])
out_dir.mkdir(parents=True,exist_ok=True)
output=out_dir/'Room.dark_metal_legs.blend'
if output.exists():raise RuntimeError('Output already exists; refusing overwrite')
source_sha=hashlib.sha256(source.read_bytes()).hexdigest()
bpy.ops.wm.open_mainfile(filepath=str(source))
names=['Front_Left_Tapered_Leg','Front_Right_Tapered_Leg','Rear_Left_Tapered_Leg','Rear_Right_Tapered_Leg']
legs=[bpy.data.objects.get(n) for n in names]
assert all(o is not None and o.type=='MESH' for o in legs)
def geom_state(o):
 h=hashlib.sha256()
 for v in o.data.vertices:h.update(str(tuple(v.co)).encode())
 for p in o.data.polygons:h.update(str(tuple(p.vertices)).encode())
 return {'geometry':h.hexdigest(),'matrix':list(sum((list(row) for row in o.matrix_world),[])),'materials':[m.name if m else None for m in o.data.materials]}
before={o.name:geom_state(o) for o in bpy.data.objects if o.type=='MESH'}
mat=bpy.data.materials.new('Room_Demo_Dark_Metal');mat.use_nodes=True
shader=mat.node_tree.nodes.get('Principled BSDF')
shader.inputs['Base Color'].default_value=(.065,.075,.085,1)
shader.inputs['Metallic'].default_value=1.0
shader.inputs['Roughness'].default_value=.3
mat.diffuse_color=(.065,.075,.085,1)
for ob in legs:
 if ob.data.users>1:ob.data=ob.data.copy()
 ob.data.materials.clear();ob.data.materials.append(mat)
 for face in ob.data.polygons:face.material_index=0
# A new presentation camera, without moving the original capture cameras.
frame=bpy.data.objects['Table2_Frame']
points=[o.matrix_world@Vector(c) for o in [frame,*legs] for c in o.bound_box]
lo=Vector([min(p[i] for p in points) for i in range(3)])
hi=Vector([max(p[i] for p in points) for i in range(3)])
center=(lo+hi)/2
camera_data=bpy.data.cameras.new('Demo_Vanity_Camera');camera=bpy.data.objects.new('Demo_Vanity_Camera',camera_data)
bpy.context.scene.collection.objects.link(camera)
camera.location=center+Vector((.25,-2.15,1.05))
target=center+Vector((0,0,.18))
camera.rotation_euler=(target-camera.location).to_track_quat('-Z','Y').to_euler()
camera_data.lens=43;camera_data.clip_start=.05;camera_data.clip_end=100
scene=bpy.context.scene;scene.camera=camera
scene.render.resolution_x=960;scene.render.resolution_y=960;scene.render.resolution_percentage=100
scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=24;scene.cycles.use_denoising=True
scene.render.image_settings.file_format='PNG';scene.render.filepath=str(out_dir/'preview.png')
for screen in bpy.data.screens:
 for area in screen.areas:
  if area.type=='VIEW_3D':
   area.spaces.active.shading.type='MATERIAL'
   area.spaces.active.region_3d.view_perspective='CAMERA'
bpy.ops.file.pack_all()
bpy.context.view_layer.update()
after={o.name:geom_state(o) for o in bpy.data.objects if o.type=='MESH'}
assert before.keys()==after.keys()
for n in before:
 assert before[n]['geometry']==after[n]['geometry'],n
 assert before[n]['matrix']==after[n]['matrix'],n
 if n not in names:assert before[n]['materials']==after[n]['materials'],n
bpy.ops.wm.save_as_mainfile(filepath=str(output))
bpy.ops.wm.open_mainfile(filepath=str(output))
for n in names:
 m=bpy.data.objects[n].data.materials[0];p=m.node_tree.nodes.get('Principled BSDF')
 assert abs(p.inputs['Metallic'].default_value-1)<1e-6
 assert abs(p.inputs['Roughness'].default_value-.3)<1e-6
assert hashlib.sha256(source.read_bytes()).hexdigest()==source_sha
report={'source':str(source),'source_sha256':source_sha,'output':str(output),'edited_objects':names,
 'geometry_and_transforms_unchanged':True,'other_objects_material_assignments_unchanged':True,
 'reopen_material_check_passed':True,'material':{'base_color_linear':[.065,.075,.085,1],'metallic':1,'roughness':.3}}
(out_dir/'changes.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
bpy.ops.render.render(write_still=True)
print('MATERIAL_DEMO_SAVED_AND_VERIFIED '+str(output),flush=True)
