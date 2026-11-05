"""Check the exact downstream resolver with both binary and directory configuration."""
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch
from litereality_agent.room_ops.paths import find_blender
from litereality_agent.room_ops.rendering.room_render.render_vs_capture import _blender_bin
from litereality_agent.agent.author import _blender_bin as author_blender

binary = Path(find_blender()).resolve()
for value in [str(binary), str(binary.parent)]:
    with patch.dict(os.environ, LITEREALITY_BLENDER=value):
        for resolver in [find_blender, _blender_bin, author_blender]:
            if Path(resolver()).resolve() != binary:
                raise RuntimeError('Inconsistent Blender executable resolver')
p=subprocess.run([str(binary),'--version'],capture_output=True,text=True,timeout=40,check=True)
report=dict(status='passed',binary=str(binary),version=p.stdout.splitlines()[0],
            binary_and_directory_supported=True,scene_render_tested=False)
(Path(os.environ['ROOM_RUN'])/'checks/blender_paths.json').write_text(json.dumps(report,indent=2))
print('BLENDER_PATHS_PASS '+str(binary),flush=True)
