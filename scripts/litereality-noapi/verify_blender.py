"""Reopen both deliverable formats in fresh scenes; compare geometry bounds/counts."""
import json
from pathlib import Path
import sys
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_export import bounds

out=Path(sys.argv[sys.argv.index('--')+1])
checks=[]
for kind in ('layout','observed'):
    expected=json.loads((out/(kind+'_export.json')).read_text())
    for ext in ('blend','glb'):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        path=out/(kind+'.'+ext)
        if ext=='blend': bpy.ops.wm.open_mainfile(filepath=str(path))
        else: bpy.ops.import_scene.gltf(filepath=str(path))
        meshes=[o for o in bpy.context.scene.objects if o.type=='MESH']
        bpy.context.view_layer.update()
        lo,hi=bounds(meshes)
        max_error=max(abs(v-e) for row, ref in zip((lo,hi),expected['bounds_blender_z_up_m']) for v,e in zip(row,ref))
        count_match=len(meshes)==expected['mesh_count']
        passed=count_match and max_error<.002 and all(len(o.data.polygons)>0 for o in meshes)
        checks.append(dict(file=path.name, passed=passed, mesh_count=len(meshes), bounds_error_m=max_error))
        if not passed: raise RuntimeError('Reopen validation failed: '+str(checks[-1]))
(out/'reopen_verification.json').write_text(json.dumps(checks,indent=2)+'\n')
print('REOPEN_ALL_PASS',flush=True)
