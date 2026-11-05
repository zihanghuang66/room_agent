"""OFFLINE TEST ONLY, run in existing r4. Not shipped to the school as a real pipeline."""
import json
import os
from pathlib import Path
import subprocess
import sys

package = Path('/test/room-litereality-full-v0.1.0')
sys.path.insert(0, str(package))
from run import setup_environment

project = Path('/test/full-validation')
folder = project / 'return_output/artifacts/offline_runtime'
folder.mkdir(parents=True, exist_ok=True)
env = setup_environment(project, folder, package / 'app')
code = '''
import numpy, PIL, open3d, trimesh, mcp
import litereality_agent
from litereality_agent.pipeline.stages import STAGES
from litereality_agent.agent.providers.codex import CodexHarness
from litereality_agent.settings import load_settings
from litereality_agent.pipeline.scene_init.ingest.references.image_gen import generate_reference
from litereality_agent.agent.tools import build_default_registry
import room_image_backend
import stage
s=load_settings()
assert s.agent_provider=='codex' and s.image_provider=='codex'
assert s.dino_backend=='local'
assert litereality_agent.__file__.startswith('/test/room-litereality-full-v0.1.0/app/src/')
print('APP_IMPORTS_AND_PATCHED_SOURCE_PASS', [s.name for s in STAGES])
'''
subprocess.run([sys.executable, '-c', code], env=env, check=True)
subprocess.run([sys.executable, package / 'preflight.py', 'mcp'], env=env, check=True)
subprocess.run(['/opt/room/envs/gpu/bin/python', package / 'check_extensions.py', '--files-only'], env=env, check=True)
blender = '/opt/room/tools/blender/blender'
preview = folder / 'cube'
subprocess.run([blender, '-b', '-t', '4', '--python-exit-code', '1', '--python', package / 'blender_smoke.py',
                '--', '--mode', 'cpu', '--output', preview], env=env, check=True)
(preview / 'test_cube.glb').rename(preview / 'Room.glb')
(preview / 'test_cube.blend').rename(preview / 'Room.blend')
subprocess.run([blender, '-b', '-t', '4', '--python-exit-code', '1', '--python', package / 'verify_export.py',
                '--', preview], env=env, check=True)
(folder / 'offline_validation.json').write_text(json.dumps(dict(status='passed',
    network_enabled=False, synthetic_export_only=True, room_002_reconstruction_tested=False,
    codex_imagegen_tested=False, gpu_inference_tested=False), indent=2))
print('OFFLINE_LINUX_VALIDATION_PASS')
