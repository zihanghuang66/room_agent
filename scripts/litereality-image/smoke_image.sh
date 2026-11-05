#!/usr/bin/env bash
# Offline CPU checks in the final image. Does not log in or call paid APIs.
set -Eeuo pipefail
source /opt/room/image/environment.sh
python /opt/room/image/runtime.py
mkdir -p /yinxin/projects/room/return_output/build-info
cp -a /opt/room/build-info/. /yinxin/projects/room/return_output/build-info/
dpkg-query -W > /yinxin/projects/room/return_output/build-info/system-packages.tsv
python -c 'import litereality_agent, open3d, fcl, coacd, mujoco; print("APP_IMPORTS_OK")'
python -m litereality_agent.cli --help
/opt/room/bootstrap/bin/uv pip check --python /opt/room/envs/app/bin/python
/opt/room/bootstrap/bin/uv pip check --python /opt/room/envs/gpu/bin/python
/opt/room/envs/gpu/bin/python -c 'import torch, torchvision; from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor; assert torch.__version__=="2.6.0+cu124"; print("GPU_ENV_CPU_IMPORTS_OK",torch.__version__)'
/opt/room/envs/gpu/bin/python /opt/room/image/check_extensions.py --files-only
printf '%s\n' '{"op":"ping","id":"room-image-smoke"}' | \
    /opt/room/envs/gpu/bin/python -m litereality_agent.models.grounding_dino.worker \
    > /yinxin/projects/room/return_output/dino-worker.json
python -c 'import json; p="/yinxin/projects/room/return_output/dino-worker.json"; d=json.load(open(p)); assert d["ok"] and d["id"]=="room-image-smoke"; print("DINO_WORKER_PROTOCOL_OK",d)'
codex --version
bwrap --version
node --version
npm --version
ssh -V
tmux -V
infocmp -x xterm-ghostty >/dev/null
infocmp tmux-256color >/dev/null
blender --background --factory-startup --python-exit-code 1 \
    --python /opt/room/image/blender_smoke.py -- \
    --mode cpu --output /yinxin/projects/room/return_output/blender-cpu
test -s /yinxin/projects/room/return_output/blender-cpu/cpu.png
test -s /yinxin/projects/room/return_output/blender-cpu/test_cube.glb
printf 'CPU_SMOKE_PASSED; target GPU kernels, model inference and authenticated agents remain untested\n'
