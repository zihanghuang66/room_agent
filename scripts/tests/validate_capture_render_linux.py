"""Exercise the real Blender capture renderer on a synthetic cube + existing test capture."""
import json
import os
from pathlib import Path
import subprocess
import sys

package, out = map(Path, sys.argv[1:])
sys.path.insert(0, str(package))
from runtime import prepare_app
app = prepare_app()
scan = Path('/test/full-validation-final-20261102/full-project/return_output/artifacts/room_002_full_v0.1.0_43315356109e/input/extracted')
assert len(list(scan.glob('frame_*.jpg'))) == 59
room = out / 'room'; room.mkdir(parents=True, exist_ok=True)
preview = out / 'room_preview'; preview.mkdir(exist_ok=True)
(preview / 'manifest.json').write_text('{}')
(room / 'Room.py').write_text('''import bpy,sys
from litereality_agent.room_ops.compile.build_room import RoomScene
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=1,location=(0,0,0))
args=sys.argv[sys.argv.index('--')+1:]
ROOM=RoomScene(scan_dir=args[0],assets_dir=args[1]);ROOM._build_cameras()
''')
env=dict(os.environ, PYTHONPATH=str(app/'src'), LITEREALITY_BLENDER='/opt/room/tools/blender/blender',
         LIBGL_ALWAYS_SOFTWARE='1', EGL_PLATFORM='surfaceless', PYTHONDONTWRITEBYTECODE='1')
cmd=[sys.executable,'-m','litereality_agent.room_ops.rendering.room_render.render_vs_capture',
     '--scan',str(scan),'--room',str(room),'--out',str(out/'compare'),'--frames','3','--res-div','16']
p=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=360)
(out/'render.log').write_text(p.stdout+p.stderr)
if p.returncode: raise RuntimeError(p.stdout[-1600:]+p.stderr[-2200:])
pairs=list((out/'compare/pairs').glob('pair_*.png')); assert len(pairs)==3
assert (out/'compare/contact_sheet.png').is_file() and (out/'compare/vs_capture.mp4').stat().st_size>0
# A Python failure inside Blender must be surfaced to the caller, not a false-success exit 0.
(room/'Room.py').write_text('raise RuntimeError("EXPECTED TEST FAILURE")\n')
bad=cmd.copy(); bad[bad.index('--out')+1]=str(out/'expected_failure')
p=subprocess.run(bad,env=env,capture_output=True,text=True,timeout=90)
(out/'expected_failure.log').write_text(p.stdout+p.stderr)
assert p.returncode != 0 and 'EXPECTED TEST FAILURE' in p.stdout+p.stderr
result=dict(status='passed', real_blender_binary=True, real_capture_cameras=59,
            rendered_pairs=3, contact_sheet=True, video=True, blender_python_failure_nonzero=True,
            geometry='synthetic cube', capture='Studio2573 test capture',
            room_002_fidelity_tested=False, model_calls=0)
(out/'validation.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
