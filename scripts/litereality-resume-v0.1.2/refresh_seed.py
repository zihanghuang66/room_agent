"""Recompile the existing seed with real ARKit cameras; never regenerate furniture."""
import json
import os
from pathlib import Path
from litereality_agent.room_ops import compile_room

root = Path(os.environ['ROOM_RUN'])
base = root / 'scenes/room_002/scene_init/scene_stage/room_init'
layout = base / 'room_preview/room_layout.json'
frames = int(json.loads((root / 'input.json').read_text())['frames'])
old = json.loads(layout.read_text()) if layout.is_file() else {}
if len(old.get('cameras', [])) != frames:
    output = compile_room(base / 'room', bake=True, regenerate=False)
    if output is None:
        raise RuntimeError('Seed recompile failed; original furniture assets are retained')
    old = json.loads(layout.read_text())
count = len(old.get('cameras', []))
if count != frames:
    raise RuntimeError(f'Seed has {count} cameras; expected {frames}')
report = dict(status='passed', cameras=count, objects=len(old.get('objects', [])),
              furniture_regenerated=False, preview=str(base / 'room_preview/Room.glb'))
(root / 'capture_seed_refresh.json').write_text(json.dumps(report, indent=2) + '\n')
print('SEED_CAMERAS_PASS', count, flush=True)
