import bpy,sys,math,json
from pathlib import Path
from mathutils import Vector
src,out=sys.argv[sys.argv.index('--')+1:]
bpy.ops.wm.open_mainfile(filepath=src)
c,s=math.cos(math.radians(10.67)),math.sin(math.radians(10.67))
def q(p):return (c*p.x+s*p.y,-s*p.x+c*p.y,p.z+1.6592)
def summary(pts):
 lo=[min(p[i] for p in pts) for i in range(3)]
 hi=[max(p[i] for p in pts) for i in range(3)]
 return {'min':lo,'max':hi,'size':[hi[i]-lo[i] for i in range(3)]}
obj=bpy.data.objects['Window_Static_Frame_Sill_Rail_Curtains']
ev=obj.evaluated_get(bpy.context.evaluated_depsgraph_get());mesh=ev.to_mesh()
points=[q(ev.matrix_world@v.co) for v in mesh.vertices]
result={'source':src,'units':bpy.context.scene.unit_settings.system,'unit_scale':bpy.context.scene.unit_settings.scale_length,'room_rotation_degrees':10.67,'materials':{}}
for i,m in enumerate(mesh.materials):
 ids=set(j for p in mesh.polygons if p.material_index==i for j in p.vertices)
 if ids:result['materials'][m.name]=summary([points[j] for j in ids])
 # Independently measure transverse planar face edges in world metres.
 if m.name=='window_marble':
  edges=set(tuple(sorted((a,b))) for p in mesh.polygons if p.material_index==i for a,b in zip(p.vertices,list(p.vertices[1:])+[p.vertices[0]]))
  transverse=[]
  for i1,i2 in edges:
   p1,p2=points[i1],points[i2]
   if abs(p1[1]-p2[1])<1e-5 and abs(p1[2]-p2[2])<1e-5 and abs(p1[0]-p2[0])>.5:
    transverse.append({'length_m':((ev.matrix_world@mesh.vertices[i1].co)-(ev.matrix_world@mesh.vertices[i2].co)).length,'endpoint_room_m':[p1,p2]})
  result['transverse_mesh_edges']=transverse
ev.to_mesh_clear()
base=bpy.data.objects['Window bench plastered masonry']
ev=base.evaluated_get(bpy.context.evaluated_depsgraph_get());mesh=ev.to_mesh()
result['base']=summary([q(ev.matrix_world@v.co) for v in mesh.vertices]);ev.to_mesh_clear()
Path(out).write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
