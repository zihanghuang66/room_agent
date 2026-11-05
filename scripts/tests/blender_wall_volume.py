"""Compare actual Blender wall vertices with the layout solid; intersect real meshes."""
import importlib.util,json,math,sys
from pathlib import Path
import bpy,bmesh
from mathutils import Vector
args=sys.argv[sys.argv.index('--')+1:];app=Path(args[0]).resolve();out=Path(args[1]).resolve()
sys.path.insert(0,str(app/'src'))
from litereality_agent.room_ops.wall_geometry import collision_walls
path=app/'src/litereality_agent/room_ops/compile/build_room.py'
# Exercise the same standalone source embedding used by export_room, without app imports.
portable=out/'StandaloneRoom.py'
portable.write_text(path.read_text().replace('from litereality_agent.room_ops.wall_geometry import outward_sign',
    (app/'src/litereality_agent/room_ops/wall_geometry.py').read_text()))
sys.path.remove(str(app/'src'))
spec=importlib.util.spec_from_file_location('builder_fixture',portable);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
unit=m._unit_cube_mesh('unit_check');bm=bmesh.new();bm.from_mesh(unit);assert abs(bm.calc_volume(signed=True)-1)<1e-8;bm.free()
shell=json.loads((out/'shell.json').read_text())
bpy.ops.wm.read_factory_settings(use_empty=True)
objects,_=m.RoomScene(build_cameras=False)._build_shell_from_compact(shell)
walls=[o for o in objects if o.name in shell['walls']];floors=[o for o in objects if o.name.startswith('Floor')]
center=sum((o.matrix_world.translation for o in walls),Vector())/len(walls)
m.thicken_walls(walls,center,floor_tris=m._floor_triangles(floors))
bpy.context.view_layer.update();expected=collision_walls(shell);reports=[]
for obj in walls:
 w=expected[obj.name];a=Vector(w['start']);b=Vector(w['end']);along=(b-a).normalized();normal=Vector((-along.y,along.x))
 distances=[((obj.matrix_world@v.co).xy-a).dot(normal) for v in obj.data.vertices]
 actual=[min(distances),max(distances)];err=max(abs(a-b) for a,b in zip(actual,w['solid_interval']))
 assert err<1e-5,(obj.name,actual,w['solid_interval'])
 reports.append({'wall':obj.name,'actual_interval':actual,'expected_interval':w['solid_interval'],'max_error_m':err})
wall=bpy.data.objects['Wall1'];cabinet=bpy.data.objects['Storage0'];w=shell['walls']['Wall1']
a=Vector(w['start']);b=Vector(w['end']);d=(b-a).normalized();n=Vector((-d.y,d.x,0))
def intersection_volume(offset):
 probe=cabinet.copy();probe.data=cabinet.data.copy();bpy.context.scene.collection.objects.link(probe)
 probe.location+=n*offset
 modifier=probe.modifiers.new('measured_intersection','BOOLEAN');modifier.operation='INTERSECT';modifier.solver='EXACT';modifier.object=wall
 bpy.context.view_layer.objects.active=probe
 bpy.ops.object.modifier_apply(modifier=modifier.name)
 bm=bmesh.new();bm.from_mesh(probe.data);bm.transform(probe.matrix_world);volume=abs(bm.calc_volume(signed=True));print('VOLUME',offset,len(bm.faces),volume,flush=True);bm.free()
 bpy.data.objects.remove(probe,do_unlink=True)
 return volume
# Wall1's normal points out of this captured room, so +3cm is intentionally into the wall.
print('WALL_INTERVALS',reports,flush=True)
base_volume=intersection_volume(0);bad_volume=intersection_volume(.03)
face_area=shell['objects']['Storage0']['size'][0]*shell['objects']['Storage0']['size'][2]
assert base_volume<=face_area*.005,(base_volume,face_area)
assert bad_volume>face_area*.02,(bad_volume,face_area)
report={'passed':True,'blender':bpy.app.version_string,'walls':reports,'facade_overlap_m3':base_volume,
        'deliberate_3cm_penetration_m3':bad_volume,'no_collision_exemptions':True}
(out/'blender_wall_verification.json').write_text(json.dumps(report,indent=2)+'\n')
bpy.ops.wm.save_as_mainfile(filepath=str(out/'wall_geometry_fixture.blend'))
print('BLENDER_WALL_VOLUME_PASS',json.dumps(report))
