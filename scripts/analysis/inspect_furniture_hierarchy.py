import bpy,json,sys
from pathlib import Path
src,out=sys.argv[sys.argv.index('--')+1:]
bpy.ops.wm.open_mainfile(filepath=src)
def node(o):
 return {'name':o.name,'type':o.type,'room_id':o.get('room_id'),'vertices':len(o.data.vertices) if o.type=='MESH' else None,'children':[node(c) for c in o.children]}
roots=[o for o in bpy.data.objects if o.name=='Table2' or o.get('room_id')=='Table2']
r={'matches':[node(o) for o in roots], 'furniture_collections':{c.name:[o.name for o in c.objects] for c in bpy.data.collections if 'furn' in c.name.lower()},'matching_names':[o.name for o in bpy.data.objects if any(k in o.name for k in ['Table2','Tapered','Case_Floor','Tabletop','Drawer_Left'])]}
Path(out).write_text(json.dumps(r,ensure_ascii=False,indent=2));print(json.dumps(r,ensure_ascii=False,indent=2))
