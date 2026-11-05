"""Export evaluated Blender meshes at their saved pose as a static MuJoCo scene.

Run with Blender --background --python export_blend.py -- SOURCE.blend OUTPUT.
No source writes; base-color textures/UVs preserved, complex shaders approximated.
"""
import collections
import hashlib
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector, Euler, Matrix


def nums(xs):
    return ' '.join(format(float(x), '.9g') for x in xs)


def main():
    source, target = map(Path, sys.argv[sys.argv.index('--') + 1:])
    target.mkdir(parents=True, exist_ok=True)
    if (target / 'scene.xml').exists():
        raise RuntimeError('Refusing to overwrite existing scene.xml')
    (target / 'meshes').mkdir(exist_ok=True)
    (target / 'textures').mkdir(exist_ok=True)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    bpy.ops.wm.open_mainfile(filepath=str(source))
    scene = bpy.context.scene
    deps = bpy.context.evaluated_depsgraph_get()
    root = ET.Element('mujoco', model='room_003_static_visual_demo')
    ET.SubElement(root, 'compiler', angle='radian', meshdir='meshes', texturedir='textures', inertiafromgeom='false')
    ET.SubElement(root, 'option', gravity='0 0 0', timestep='0.01')
    visual = ET.SubElement(root, 'visual')
    ET.SubElement(visual, 'global', offwidth='1280', offheight='960')
    ET.SubElement(visual, 'quality', shadowsize='2048', offsamples='4')
    ET.SubElement(visual, 'headlight', ambient='.4 .4 .4', diffuse='.6 .6 .6', specular='.2 .2 .2')
    asset = ET.SubElement(root, 'asset')
    world = ET.SubElement(root, 'worldbody')
    ET.SubElement(world, 'light', name='key', directional='true', pos='0 0 8', dir='-.3 .2 -1', diffuse='.6 .6 .6', castshadow='false')
    materials, images, records, approximations, uv_maps = {}, {}, [], [], {}
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)

    def material(mat):
        key = mat.name if mat else '__default__'
        if key in materials:
            return materials[key]
        name = 'mat_' + str(len(materials))
        materials[key] = name
        rgba = list(mat.diffuse_color) if mat else [.65, .65, .65, 1]
        rough, metal, tex = .6, 0., None
        if mat and mat.use_nodes:
            principled = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
            if principled:
                base = principled.inputs['Base Color']
                rgba = list(base.default_value)
                rough = principled.inputs['Roughness'].default_value
                metal = principled.inputs['Metallic'].default_value
                if base.is_linked:
                    node = base.links[0].from_node
                    tint = np.ones(4)
                    while node.type == 'MIX_RGB':
                        if node.blend_type == 'MULTIPLY' and not node.inputs[0].is_linked and node.inputs[1].is_linked and not node.inputs[2].is_linked:
                            fac = node.inputs[0].default_value
                            tint *= (1-fac) + fac*np.array(node.inputs[2].default_value)
                            node = node.inputs[1].links[0].from_node
                        elif node.inputs[2].is_linked:
                            approximations.append({'material': key, 'reason': 'spatial color mask omitted; texture branch retained'})
                            node = node.inputs[2].links[0].from_node
                        else:
                            break
                    if node.type == 'TEX_IMAGE' and node.image:
                        im = node.image
                        if im.name not in images:
                            texname = 'tex_' + str(len(images))
                            filename = texname + '.png'
                            oldpath, oldfmt = im.filepath_raw, im.file_format
                            im.filepath_raw, im.file_format = str(target / 'textures' / filename), 'PNG'
                            im.save()
                            im.filepath_raw, im.file_format = oldpath, oldfmt
                            ET.SubElement(asset, 'texture', name=texname, type='2d', file=filename)
                            images[im.name] = texname
                        tex = images[im.name]
                        rgba = tint.tolist()
                        if node.inputs['Vector'].is_linked:
                            upstream = node.inputs['Vector'].links[0].from_node
                            if upstream.type == 'MAPPING' and upstream.vector_type == 'POINT' and not any(upstream.inputs[n].is_linked for n in ('Location', 'Rotation', 'Scale')):
                                uv_maps[name] = Matrix.LocRotScale(Vector(upstream.inputs['Location'].default_value), Euler(upstream.inputs['Rotation'].default_value).to_quaternion(), Vector(upstream.inputs['Scale'].default_value))
                            elif upstream.type not in ('UVMAP', 'TEX_COORD'):
                                approximations.append({'material': key, 'reason': 'texture vector node approximated by mesh UV'})
                    else:
                        approximations.append({'material': key, 'reason': 'procedural base color approximated'})
                transmission = principled.inputs.get('Transmission Weight')
                if transmission and transmission.default_value > .5:
                    rgba[3] = .25
        attrs = dict(name=name, rgba=nums(rgba), metallic=str(metal), roughness=str(rough),
                     specular=str(.3 + .5 * metal), shininess=str(max(.05, 1 - rough)))
        if tex:
            attrs.update(texture=tex, texuniform='false')
        ET.SubElement(asset, 'material', **attrs)
        return name

    for obj in sorted(scene.objects, key=lambda ob: ob.name):
        if obj.type != 'MESH' or obj.hide_render:
            continue
        evaluated = obj.evaluated_get(deps)
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=deps)
        mesh.calc_loop_triangles()
        if not mesh.loop_triangles:
            evaluated.to_mesh_clear()
            continue
        matrix = evaluated.matrix_world
        coords = np.array([matrix @ v.co for v in mesh.vertices], dtype=np.float64)
        center = (coords.min(axis=0) + coords.max(axis=0)) / 2
        normal_matrix = matrix.to_3x3().inverted_safe().transposed()
        uv = mesh.uv_layers.active.data if mesh.uv_layers.active else None
        groups = collections.defaultdict(list)
        for tri in mesh.loop_triangles:
            groups[tri.material_index].append(tri)
        body = ET.SubElement(world, 'body', name='obj_' + str(len(records)), pos=nums(center))
        group = '2' if 'ceiling' in obj.name.lower() else ('1' if obj.name.lower().startswith('wall') else '0')
        for slot, triangles in sorted(groups.items()):
            mat = mesh.materials[slot] if slot < len(mesh.materials) else None
            mat_name = material(mat)
            vertices, normals, uvs, faces, indices = [], [], [], [], {}
            for tri in triangles:
                face = []
                loops = list(tri.loops)
                if matrix.determinant() < 0:
                    loops.reverse()
                for li in loops:
                    vi = mesh.loops[li].vertex_index
                    normal = (normal_matrix @ mesh.corner_normals[li].vector).normalized()
                    texcoord = tuple(uv[li].uv) if uv else (0., 0.)
                    if mat_name in uv_maps:
                        mapped = uv_maps[mat_name] @ Vector((*texcoord, 0.))
                        texcoord = (mapped.x, mapped.y)
                    key = (vi, *normal, *texcoord)
                    if key not in indices:
                        indices[key] = len(vertices)
                        vertices.append(coords[vi] - center)
                        normals.append(tuple(normal))
                        uvs.append(texcoord)
                    face.append(indices[key])
                faces.append(face)
            if len(vertices) < 4:
                # MuJoCo requires at least four vertices; duplicate one for a single triangle.
                vertices.append(vertices[0]); normals.append(normals[0]); uvs.append(uvs[0])
            idx = sum(len(r['parts']) for r in records) + len(body.findall('geom'))
            name = 'mesh_' + str(idx)
            with (target / 'meshes' / (name + '.msh')).open('wb') as f:
                f.write(np.array([len(vertices), len(normals), len(uvs), len(faces)], dtype='<i4').tobytes())
                for array in (vertices, normals, uvs):
                    f.write(np.array(array, dtype='<f4').tobytes())
                f.write(np.array(faces, dtype='<i4').tobytes())
            ET.SubElement(asset, 'mesh', name=name, file=name + '.msh', inertia='shell')
            ET.SubElement(body, 'geom', name=name, type='mesh', mesh=name, material=mat_name,
                          contype='0', conaffinity='0', mass='0', group=group)
        records.append({'object': obj.name, 'parts': [g.attrib['mesh'] for g in body.findall('geom')], 'group': int(group)})
        lo = np.minimum(lo, coords.min(axis=0)); hi = np.maximum(hi, coords.max(axis=0))
        evaluated.to_mesh_clear()
    cameras = []
    for obj in sorted(scene.objects, key=lambda ob: ob.name):
        if obj.type != 'CAMERA':
            continue
        mat = obj.matrix_world
        name = 'camera_' + str(len(cameras))
        ET.SubElement(world, 'camera', name=name, pos=nums(mat.translation),
                      xyaxes=nums([*mat.col[0][:3], *mat.col[1][:3]]),
                      fovy=str(math.degrees(obj.data.angle_y)))
        cameras.append({'name': name, 'source': obj.name})
    center, extent = (lo + hi) / 2, float(max(hi-lo))
    ET.SubElement(root, 'statistic', center=nums(center), extent=str(extent))
    ET.indent(root)
    ET.ElementTree(root).write(target / 'scene.xml', encoding='utf-8', xml_declaration=True)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    report = dict(source=str(source), source_sha256=before, source_unchanged=True,
                  mode='static_visual_only', collisions=False, actuators=False,
                  objects=records, cameras=cameras, materials=len(materials), textures=len(images),
                  bbox=[lo.tolist(), hi.tolist()], center=center.tolist(), extent=extent,
                  shader_approximations=approximations,
                  limitations=['No calibrated physics, joints, or collisions.', 'Native MuJoCo renderer is not Blender Cycles.'])
    (target / 'export_report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print('EXPORTED', len(records), 'objects', len(images), 'textures', flush=True)


if __name__ == '__main__':
    main()
