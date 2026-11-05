import bpy, json, sys
from pathlib import Path
from mathutils import Vector
source, output = sys.argv[sys.argv.index('--')+1:]
bpy.ops.wm.open_mainfile(filepath=source)
records={}
for obj in bpy.context.scene.objects:
    if obj.type not in ('MESH','EMPTY'): continue
    ancestors=[]; parent=obj.parent
    while parent:
        ancestors.append(parent.name); parent=parent.parent
    record={'type':obj.type,'parent':obj.parent.name if obj.parent else None,'ancestors':ancestors,
            'matrix': [list(r) for r in obj.matrix_world], 'properties': {k:obj[k].to_list() if hasattr(obj[k],'to_list') else str(obj[k]) for k in obj.keys() if k!='_RNA_UI'}}
    if obj.type=='MESH':
        corners=[obj.matrix_world@Vector(v) for v in obj.bound_box]
        record['world_bbox']=[[min(p[i] for p in corners) for i in range(3)],[max(p[i] for p in corners) for i in range(3)]]
        record['local_bbox']=[[min(v[i] for v in obj.bound_box) for i in range(3)],[max(v[i] for v in obj.bound_box) for i in range(3)]]
    records[obj.name]=record
Path(output).write_text(json.dumps(records,indent=2))
for name,r in records.items():
    if name in ['Drawer_Left_Upper','Drawer_Left_Lower','Table2_Frame','Floor0'] or r.get('parent') in ['Table0','Table1','Chair0','Chair1']:
        print(name,json.dumps(r))
