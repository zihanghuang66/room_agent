"""Offline real-capture regression: broken alias fails, repaired alias stitches surfaces."""
import json
import os
from pathlib import Path
import subprocess
import sys

scripts = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(scripts / 'litereality-resume'))
from capture_paths import repair_capture

project = scripts.parent
source = project / 'return_output/input_audits/studio_2573_20261030/raw'
folder = project / 'return_output/artifacts/capture_repair_validation_20261102'
raw = folder / 'input/extracted'; raw.mkdir(parents=True, exist_ok=True)
for path in source.iterdir():
    if path.is_file() and not (raw/path.name).exists():
        (raw/path.name).symlink_to(path.resolve())
scene = folder/'scenes/room_002'; (scene/'capture').mkdir(parents=True,exist_ok=True)
bad = scene/'scene_init/obj_stage/object_init/input/usdz_files';bad.mkdir(parents=True,exist_ok=True)
embedded=scene/'capture/room_002'
if embedded.is_symlink():embedded.unlink()
embedded.symlink_to(bad)
alias=folder/'input/room_002'
if alias.is_symlink():alias.unlink()
(scene/'scene.json').write_text(json.dumps(dict(scan='room_002',paths={'capture':'capture/room_002'},roots={},capture={})))
env=dict(os.environ)
env.update(PYTHONPATH=str(scripts/'vendor/litereality-agent-28ffe1f-room-codex-v0.1.2/src'),
    LR_REPO_ROOT=str(folder/'work'),LR_SCANS_DIR=str(folder/'input'),LITEREALITY_SCAN='room_002',
    LR_SCENE=str(scene),LR_AUTHORING=str(scene/'realism_authoring'),
    LITEREALITY_OUTPUT=str(folder/'scenes'),LITEREALITY_FINAL=str(folder/'scenes'))
cmd=[sys.executable,'-m','litereality_agent.pipeline.realism_authoring.author.evidence','--scene',str(scene)]
before=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=120)
(folder/'before.log').write_text(before.stdout+before.stderr)
assert before.returncode!=0 and 'FileNotFoundError' in before.stderr and 'input/room_002/room.usdz' in before.stderr
repair=repair_capture(folder,raw,len(list(raw.glob('frame_*.json'))))
after=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=180)
(folder/'after.log').write_text(after.stdout+after.stderr)
if after.returncode:raise RuntimeError(after.stderr[-4000:])
manifest=json.loads((scene/'realism_authoring/surface_ref/surface_ref_manifest.json').read_text())
result=dict(status='passed',before_exit=before.returncode,after_exit=after.returncode,
            surfaces=len(manifest['surfaces']),frames=repair['frames'],sample='Studio2573',
            room_002_full_inference_tested=False,model_calls=0)
(folder/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
