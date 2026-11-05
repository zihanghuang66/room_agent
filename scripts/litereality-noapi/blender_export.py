"""Blender 5.x worker: original USDZ layout and observed TSDF mesh, kept separate."""
import argparse
import json
import math
from pathlib import Path
import sys
import bpy
from mathutils import Vector, Matrix


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')


def bounds(objects):
    corners = [obj.matrix_world @ Vector(c) for obj in objects if obj.type == 'MESH' for c in obj.bound_box]
    if not corners: raise RuntimeError('Imported scene has no mesh geometry')
    lo = Vector(tuple(min(c[i] for c in corners) for i in range(3)))
    hi = Vector(tuple(max(c[i] for c in corners) for i in range(3)))
    if not all(math.isfinite(v) for v in (*lo, *hi)): raise RuntimeError('Invalid scene bounds')
    return lo, hi


def material(name, color):
    mat = bpy.data.materials.new(name); mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    mat.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value = (*color, 1)
    mat.node_tree.nodes.get('Principled BSDF').inputs['Roughness'].default_value = .8
    return mat


def camera(scene, center, span, direction):
    data = bpy.data.cameras.new('PreviewCamera')
    obj = bpy.data.objects.new('PreviewCamera', data); scene.collection.objects.link(obj)
    obj.location = center + Vector(direction).normalized()*span*2
    obj.rotation_euler = (center-obj.location).to_track_quat('-Z','Y').to_euler()
    data.type = 'ORTHO'; data.ortho_scale = span*1.45; data.clip_end = max(100., span*10)
    scene.camera = obj
    return obj


def export_scene(kind, source, out):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = 'METRIC'; scene.unit_settings.scale_length = 1.
    if kind == 'layout':
        bpy.ops.wm.usd_import(filepath=str(source))
        # Native USD importer handles source stage units/up-axis and hierarchy.
        meshes = [o for o in scene.objects if o.type == 'MESH']
        palette = [(0.24,.53,.83),(.83,.48,.20),(.35,.67,.42),(.68,.40,.75)]
        for i, obj in enumerate(meshes):
            obj['geometry_source'] = 'original RoomPlan parametric proxy; not detailed reconstructed furniture'
            obj.data.materials.clear()
            color = (.65,.68,.72) if 'wall' in obj.name.lower() else palette[i%len(palette)]
            obj.data.materials.append(material('RoomPlan_'+str(i), color))
    else:
        bpy.ops.wm.ply_import(filepath=str(source))
        meshes = [o for o in scene.objects if o.type == 'MESH']
        for obj in meshes:
            obj.name = 'Observed_RGBD_TSDF_Mesh'
            # PLY has no up-axis metadata: ARKit (x,y,z) -> Blender (x,-z,y).
            obj.matrix_world = Matrix(((1,0,0,0),(0,0,-1,0),(0,1,0,0),(0,0,0,1))) @ obj.matrix_world
            obj['geometry_source'] = 'measured RGB-D TSDF; unsegmented; holes may remain'
            mat = material('MeasuredColor', (.65,.7,.75))
            if obj.data.color_attributes:
                node = mat.node_tree.nodes.new('ShaderNodeVertexColor')
                node.layer_name = obj.data.color_attributes[0].name
                mat.node_tree.links.new(node.outputs['Color'], mat.node_tree.nodes.get('Principled BSDF').inputs['Base Color'])
            obj.data.materials.clear(); obj.data.materials.append(mat)
    bpy.context.view_layer.update()
    lo, hi = bounds(meshes); center = (lo+hi)/2; span = max((hi-lo).length, 1.)
    objects = [dict(name=o.name, vertices=len(o.data.vertices), polygons=len(o.data.polygons),
        matrix_world=[list(row) for row in o.matrix_world], geometry_source=o['geometry_source']) for o in meshes]
    if not any(o['polygons'] for o in objects): raise RuntimeError('Imported scene contains no faces')
    scene.world = bpy.data.worlds.new('PreviewWorld'); scene.world.use_nodes = True
    scene.world.node_tree.nodes.get('Background').inputs[0].default_value = (.7,.7,.7,1)
    scene.world.node_tree.nodes.get('Background').inputs[1].default_value = .7
    light_data = bpy.data.lights.new('PreviewArea','AREA'); light_data.energy = 1500
    light_data.shape='DISK'; light_data.size=span
    light = bpy.data.objects.new('PreviewArea', light_data); scene.collection.objects.link(light)
    light.location = center+Vector((0,0,span))
    scene.render.engine='CYCLES'; scene.cycles.device='CPU'; scene.cycles.samples=16
    scene.render.resolution_x=1000; scene.render.resolution_y=800; scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG'; scene.render.film_transparent=False
    cam = camera(scene, center, span, (1,-1,1.1))
    # Export meshes only. Viewer camera/lights are Blender preview aids.
    bpy.ops.object.select_all(action='DESELECT')
    for obj in meshes: obj.select_set(True)
    bpy.ops.export_scene.gltf(filepath=str(out/(kind+'.glb')), export_format='GLB', use_selection=True,
                              export_extras=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(out/(kind+'.blend')))
    for label, direction in [('overview',(1,-1,1.1)), ('top',(0,0,1))]:
        cam.location=center+Vector(direction).normalized()*span*2
        cam.rotation_euler=(center-cam.location).to_track_quat('-Z','Y').to_euler()
        scene.render.filepath=str(out/(kind+'_'+label+'.png'))
        bpy.ops.render.render(write_still=True)
    result = dict(status='exported', kind=kind, blender_version=bpy.app.version_string,
        mesh_objects=objects, mesh_count=len(meshes), bounds_blender_z_up_m=[list(lo),list(hi)],
        render_engine='Cycles CPU', source=str(source), independently_scale_calibrated=False)
    save(out/(kind+'_export.json'), result)
    print('EXPORT_OK '+kind, flush=True)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--kind', choices=['layout','observed'], required=True)
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args=ap.parse_args(sys.argv[sys.argv.index('--')+1:])
    args.output.mkdir(parents=True, exist_ok=True)
    export_scene(args.kind, args.source, args.output)

if __name__=='__main__': main()
