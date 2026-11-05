import bpy,math,json,sys
from pathlib import Path
from mathutils import Vector
args=sys.argv[sys.argv.index('--')+1:]
bpy.ops.wm.open_mainfile(filepath=args[0])
angle=math.radians(10.67);c=math.cos(angle);s=math.sin(angle);floor=-1.6592
def q(p):return (c*p.x+s*p.y,-s*p.x+c*p.y,p.z-floor)
def verts(name,recursive=True):
    parent=bpy.data.objects[name];objects=[parent]+(list(parent.children_recursive) if recursive else [])
    out=[];dg=bpy.context.evaluated_depsgraph_get()
    for obj in objects:
        if obj.type!='MESH':continue
        ev=obj.evaluated_get(dg);me=ev.to_mesh()
        out.extend(q(ev.matrix_world@v.co) for v in me.vertices);ev.to_mesh_clear()
    return out
def bounds(points):
    lo=[min(v[i] for v in points) for i in range(3)];hi=[max(v[i] for v in points) for i in range(3)]
    return dict(min=lo,max=hi,size=[b-a for a,b in zip(lo,hi)])
result={'unit_settings':dict(system=bpy.context.scene.unit_settings.system,scale=bpy.context.scene.unit_settings.scale_length),'frame':bpy.context.scene.frame_current,'room_rotation_degrees':10.67}
names=['Bed0','Storage1','WindowBench','HeadboardUpholstery','HeadboardSilverTrim']
for n in names:result[n]=bounds(verts(n))
bed=verts('Bed0');result['Bed_with_authored_headboard']=bounds(bed+verts('HeadboardUpholstery')+verts('HeadboardSilverTrim'))
for a,b in [(0,.30),(.30,.64),(.50,.64)]:
    pts=[v for v in bed if a<=v[2]<=b]
    result[f'bed_height_band_{a}_{b}']=bounds(pts)
    result[f'gap_to_bench_band_{a}_{b}']=result['WindowBench']['min'][0]-max(v[0] for v in pts)
# Probe actual bed surface from its right side at representative floor heights / lengths.
from mathutils.bvhtree import BVHTree
def world(q):return Vector((c*q[0]-s*q[1],s*q[0]+c*q[1],floor+q[2]))
meshes=[o for o in [bpy.data.objects['Bed0']]+list(bpy.data.objects['Bed0'].children_recursive) if o.type=='MESH']
trees=[]
for obj in meshes:
    ev=obj.evaluated_get(bpy.context.evaluated_depsgraph_get());me=ev.to_mesh()
    trees.append(BVHTree.FromPolygons([ev.matrix_world@v.co for v in me.vertices],[tuple(p.vertices) for p in me.polygons]));ev.to_mesh_clear()
probes=[]
for h in [.2,.4,.55,.60]:
 for y in [2.2,2.7,3.2,3.7]:
    hit=[]
    for tree in trees:
        p,*_=tree.ray_cast(world((3.965,y,h)),Vector((-c,-s,0)),2)
        if p is not None:hit.append(q(p)[0])
    if hit:probes.append(dict(height=h,along_bed=y,bed_right_x=max(hit),horizontal_gap=3.965-max(hit)))
result['side_gap_probes']=probes
details=[]
for obj in [bpy.data.objects['Storage1']]+list(bpy.data.objects['Storage1'].children_recursive):
    if obj.type=='MESH':details.append(dict(name=obj.name,bounds=bounds(verts(obj.name,False))))
result['cabinet_meshes']=details
result['cabinet_without_accessories']=bounds([v for n in ['Storage1_Carcass','Drawer_Lower','Drawer_Upper'] for v in verts(n,False)])
result['bed_source_only']=bounds(verts('geometry_0'))
result['bed_body_excluding_rear_headboard']=bounds([v for v in verts('geometry_0') if v[1]<3.9])
Path(args[1]).write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
