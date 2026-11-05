"""Read-only inspection; never save the loaded Blender file."""
import bpy,json,sys
from pathlib import Path
source,out=sys.argv[sys.argv.index('--')+1:]
bpy.ops.wm.open_mainfile(filepath=source)
images=[dict(name=im.name,source=im.source,packed=bool(im.packed_file or im.packed_files),size=list(im.size),filepath=im.filepath) for im in bpy.data.images]
mats=[]
for m in bpy.data.materials:
    textures=[]
    if m.use_nodes and m.node_tree:
        for n in m.node_tree.nodes:
            if n.type=='TEX_IMAGE':textures.append(dict(node=n.name,image=n.image.name if n.image else None,linked=any(s.is_linked for s in n.outputs)))
    mats.append(dict(name=m.name,users=m.users,textures=textures))
views=[dict(screen=s.name,shading=area.spaces.active.shading.type) for s in bpy.data.screens for area in s.areas if area.type=='VIEW_3D']
report=dict(file=source,images=images,materials=mats,saved_views=views)
Path(out).write_text(json.dumps(report,indent=2))
print(json.dumps(dict(images=len(images),packed_images=sum(i['packed'] for i in images),unpacked_files=[i['name'] for i in images if i['source']=='FILE' and not i['packed']],materials=len(mats),materials_with_image_nodes=sum(bool(m['textures']) for m in mats),saved_views=views),indent=2))
