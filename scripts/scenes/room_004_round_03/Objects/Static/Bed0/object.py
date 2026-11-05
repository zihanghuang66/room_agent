"""Bed0 — a static object (mesh source, in the uniform object.py format).

STATIC: the geometry is a neurally generated or scanned mesh, `Bed0.glb` — there is no
procedural modelling code. It is still wrapped in the same object.py interface a procedural
object exposes, so the assembler can treat every object alike.

Run:
  blender -b --python object.py                       # reads ./Bed0.glb, writes ./Bed0_built.glb
  blender -b --python object.py -- <texdir> <out.glb>
"""
import bpy, os, sys

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
SRC = os.path.join(HERE, "Bed0.glb")               # the source mesh (materials included)
OUT = _argv[1] if len(_argv) > 1 else os.path.join(HERE, "Bed0_built.glb")


def build(out_glb=OUT):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=SRC)
    # Placeholder for the uniform format: materials-as-code and the orientation fix (canonical
    # alignment) belong here.
    bpy.ops.object.select_all(action="SELECT")
    os.makedirs(os.path.dirname(out_glb) or ".", exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=out_glb, export_format="GLB",
                              use_selection=True, export_apply=True)
    print(f"[static] Bed0 -> {out_glb}")
    return out_glb


if __name__ == "__main__":
    build()
