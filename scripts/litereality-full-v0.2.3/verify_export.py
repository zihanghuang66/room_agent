"""Reopen both final exports in Blender; reject empty/non-finite geometry."""
import json
import math
from pathlib import Path
import sys
import bpy
from mathutils import Vector

root = Path(sys.argv[sys.argv.index('--') + 1])
checks = []
for suffix in ('blend', 'glb'):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    p = root / ('Room.' + suffix)
    if suffix == 'blend':
        bpy.ops.wm.open_mainfile(filepath=str(p))
        bpy.ops.file.pack_all()
        unpacked = [im.name for im in bpy.data.images if im.source == 'FILE' and im.filepath and not im.packed_file]
        if unpacked:
            raise RuntimeError('Cannot bundle Blender textures: ' + repr(unpacked))
        p = root / 'Room.portable.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(p))
        bpy.ops.wm.open_mainfile(filepath=str(p))
    else:
        bpy.ops.import_scene.gltf(filepath=str(p))
    bpy.context.view_layer.update()
    meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH' and len(o.data.polygons) and not o.hide_get()]
    depsgraph = bpy.context.evaluated_depsgraph_get()
    vertices = [o.matrix_world @ Vector(c) for o in meshes for c in o.evaluated_get(depsgraph).bound_box]
    if not vertices or not all(math.isfinite(x) for v in vertices for x in v):
        raise RuntimeError('Invalid geometry in ' + p.name)
    bounds = [[min(v[i] for v in vertices) for i in range(3)],
              [max(v[i] for v in vertices) for i in range(3)]]
    checks.append(dict(file=p.name, mesh_count=len(meshes), bounds=bounds))
error = max(abs(checks[0]['bounds'][j][i] - checks[1]['bounds'][j][i]) for j in range(2) for i in range(3))
(root / 'reopen_verification.json').write_text(json.dumps(dict(checks=checks, bounds_error_m=error,
    passed=error < .05, visual_fidelity_reviewed=False), indent=2))
if error >= .05:
    raise RuntimeError('BLEND/GLB bounds differ by >= 5 cm; inspect before accepting')
print('FINAL_EXPORTS_REOPEN_PASS', flush=True)
