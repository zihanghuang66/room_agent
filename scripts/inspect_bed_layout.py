import bpy, sys, math, json
from pathlib import Path
from mathutils import Vector
src, out = sys.argv[sys.argv.index('--')+1:]
bpy.ops.wm.open_mainfile(filepath=src)
c,s=math.cos(math.radians(10.67)),math.sin(math.radians(10.67))
def q(p):return [c*p.x+s*p.y,-s*p.x+c*p.y,p.z+1.6592]
def geom(o):
 e=o.evaluated_get(bpy.context.evaluated_depsgraph_get());m=e.to_mesh()
 v=[q(e.matrix_world@a.co) for a in m.vertices];e.to_mesh_clear();return v
def bounds(v):return [[min(p[i] for p in v) for i in range(3)],[max(p[i] for p in v) for i in range(3)]]
d={'engine':bpy.context.scene.render.engine,'exposure':bpy.context.scene.view_settings.exposure,'view':bpy.context.scene.view_settings.view_transform,'objects':[],'cameras':[],'lights':[]}
for o in bpy.data.objects:
 if o.type=='CAMERA':
  d['cameras'].append({'name':o.name,'position':q(o.matrix_world.translation),'lens':o.data.lens})
 elif o.type=='LIGHT':d['lights'].append({'name':o.name,'energy':o.data.energy,'position':q(o.matrix_world.translation)})
 elif o.type=='MESH' and (any(t in o.name.lower() for t in ['headboard','geometry_0','window','curtain']) or not o.parent):
  v=geom(o)
  item={'name':o.name,'parent':o.parent.name if o.parent else None,'bounds':bounds(v)}
  lower=[p for p in v if p[2]<.66 and p[1]>1.2245 and p[1]<4.1595]
  if lower:item['below_sill_max_x']=max(p[0] for p in lower)
  d['objects'].append(item)
for n in ['Bed0','SageThrow','CreamDuvet','BlueClothes','HeadboardUpholstery','HeadboardSilverTrim']:
 o=bpy.data.objects.get(n)
 if o:d[n]={'parent':o.parent.name if o.parent else None,'children':[a.name for a in o.children_recursive]}
win=bpy.data.objects['Window_Static_Frame_Sill_Rail_Curtains']
d['window_materials']=[m.name for m in win.data.materials]
for i,m in enumerate(win.data.materials):
 if 'marble' in m.name.lower() or 'stone' in m.name.lower():
  ids=set(j for p in win.data.polygons if p.material_index==i for j in p.vertices)
  d['sill_bounds']=bounds([q(win.matrix_world@win.data.vertices[j].co) for j in ids])
Path(out).write_text(json.dumps(d,indent=2))
