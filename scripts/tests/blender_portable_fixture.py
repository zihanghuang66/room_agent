"""Create a textured export, then move the BLEND+GLB to exercise relocation."""
from pathlib import Path
import shutil
import sys
import bpy
root=Path(sys.argv[sys.argv.index('--')+1]).resolve()
origin=root/'origin'; moved=root/'moved';origin.mkdir(parents=True,exist_ok=True);moved.mkdir(exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add()
obj=bpy.context.object
image=bpy.data.images.new('FixtureTexture',width=4,height=4)
image.generated_color=(.4,.2,.1,1)
image.filepath_raw=str(origin/'wood.png');image.file_format='PNG';image.save()
material=bpy.data.materials.new('TextureMaterial');material.use_nodes=True
tex=material.node_tree.nodes.new('ShaderNodeTexImage');tex.image=image
bsdf=material.node_tree.nodes.get('Principled BSDF')
material.node_tree.links.new(tex.outputs['Color'],bsdf.inputs['Base Color'])
obj.data.materials.append(material)
image.filepath='//wood.png'
bpy.ops.wm.save_as_mainfile(filepath=str(origin/'Room.blend'))
bpy.ops.export_scene.gltf(filepath=str(origin/'Room.glb'),export_format='GLB')
for name in ('Room.blend','Room.glb'):shutil.copyfile(origin/name,moved/name)
print('FIXTURE_READY',root)
