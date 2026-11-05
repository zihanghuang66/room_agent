"""Blender offline regression with the existing Studio2573 test capture; no model calls."""
import importlib.util
import json
from pathlib import Path
import sys

sys.path.insert(0, '/test')
from capture_paths import repair_capture

folder = Path('/test/full-project/return_output/artifacts/room_002_full_v0.1.0_43315356109e')
info = json.loads((folder/'input.json').read_text()); raw=Path(info['capture'])
scene = folder/'scenes/room_002'; (scene/'capture').mkdir(parents=True, exist_ok=True)
(scene/'scene.json').write_text(json.dumps(dict(scan='room_002', paths={}, roots={}, capture={})))
code=Path('/opt/room/app/src/litereality_agent/room_ops/compile/build_room.py')
spec=importlib.util.spec_from_file_location('test_camera_builder', code)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
alias=folder/'input/room_002'
if alias.is_symlink():alias.unlink()
try:
    module.RoomScene(scan_dir=str(alias))._build_cameras()
except FileNotFoundError:
    before='FileNotFoundError reproduced'
else:
    raise AssertionError('Expected the old path to fail')
repair_capture(folder, raw, info['frames'])
room=module.RoomScene(scan_dir=str(alias)); n=room._build_cameras()
assert n==info['frames']==59
result=dict(status='passed', before=before, after_cameras=n, sample='Studio2573',
            model_calls=0, room_002_camera_refresh_tested=False)
Path('/test/camera_repair_validation.json').write_text(json.dumps(result,indent=2)+'\n')
print('CAPTURE_CAMERAS_PASS',json.dumps(result))
