"""Actual Blender rebuild of returned source; no remote model invocation."""
import os
from pathlib import Path
import subprocess
import sys

project=Path(__file__).resolve().parents[2]
package=project/'scripts/litereality-batch-review-v0.1.0'
root=project/'return_output/environment_reports/batch_review_v010/local_build'
room=root/'scenes/room_004_test_04/realism_authoring/room'
preview=room.parent/'room_preview'
env=dict(os.environ, PYTHONPATH=str(package)+os.pathsep+str(package/'app/src'),
    PYTHONDONTWRITEBYTECODE='1',LR_REPO_ROOT=str(root),LITEREALITY_SCAN='room_004_test_04',
    LITEREALITY_OUTPUT=str(root/'scenes'),LITEREALITY_FINAL=str(root/'scenes'),
    LR_SCANS_DIR=str(root/'input'),LITEREALITY_BLENDER='/Applications/Blender.app/Contents/MacOS/Blender',
    LR_COLOR='0',PYTHONUNBUFFERED='1')
(root/'input').mkdir(exist_ok=True)
with (root/'build.log').open('w') as f:
    p=subprocess.run([sys.executable,'-m','build_saved','--room',str(room),'--out',str(preview)],env=env,stdout=f,stderr=subprocess.STDOUT,timeout=1200)
print('BUILD_RETURN',p.returncode)
print((root/'build.log').read_text()[-3500:])
if p.returncode:sys.exit(p.returncode)
with (root/'reopen.log').open('w') as f:
    p=subprocess.run([env['LITEREALITY_BLENDER'],'-b','--python-exit-code','1','--python',str(package/'verify_export.py'),'--',str(preview)],env=env,stdout=f,stderr=subprocess.STDOUT,timeout=600)
print('REOPEN_RETURN',p.returncode)
print((root/'reopen.log').read_text()[-1600:])
sys.exit(p.returncode)
