"""Non-billable preflight before launching reference/authoring sessions."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from common import save

p = argparse.ArgumentParser(); p.add_argument('mode', choices=['app', 'gpu', 'models', 'mcp']); a = p.parse_args()
out = Path(os.environ['ROOM_RUN']) / 'checks'; out.mkdir(exist_ok=True)
if a.mode == 'app':
    import numpy, PIL, open3d, trimesh, mcp, litereality_agent
    from litereality_agent.agent.providers.codex import CodexHarness
    from litereality_agent.pipeline.stages import STAGES
    from litereality_agent.settings import load_settings
    settings = load_settings()
    assert settings.agent_provider == 'codex' and settings.image_provider == 'codex'
    assert settings.dino_backend == 'local' and settings.trellis_python
    # Confirm every subprocess uses the newly packaged code, rather than /opt/room/app.
    assert str(Path(os.environ['ROOM_APP']) / 'src') in litereality_agent.__file__
    cmd = os.environ['ROOM_CODEX']
    version = subprocess.run([cmd, '--version'], capture_output=True, text=True, timeout=40)
    login = subprocess.run([cmd, 'login', 'status'], capture_output=True, text=True, timeout=40)
    features = subprocess.run([cmd, 'features', 'list'], capture_output=True, text=True, timeout=40)
    if version.returncode or login.returncode or 'api key' in (login.stdout + login.stderr).lower():
        raise RuntimeError('Working packaged Codex CLI with existing ChatGPT login is required')
    if features.returncode or not any(line.split()[:1] == ['image_generation'] and line.split()[-1:] == ['true']
                                      for line in features.stdout.splitlines()):
        raise RuntimeError('Codex builtin image_generation feature is not enabled')
    save(out / 'app.json', dict(status='passed', python=sys.executable, source=litereality_agent.__file__,
         codex=version.stdout.strip(), login_verified=True, actual_agent_execution_tested=False,
         stages=[s.name for s in STAGES]))
elif a.mode == 'mcp':
    import asyncio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    async def check():
        params = StdioServerParameters(command=sys.executable, args=['-m',
            'litereality_agent.agent.tools.mcp_server', '--scene', os.environ['ROOM_RUN']], env=dict(os.environ))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                available = await session.list_tools()
                names = sorted(t.name for t in available.tools)
                if not {'render', 'critic', 'fetch_material', 'select_views', 'grid', 'check_collisions'} <= set(names):
                    raise RuntimeError('Required upstream MCP tools missing: ' + repr(names))
                save(out / 'mcp.json', dict(status='passed', tools=names, actual_render_tested=False))
    asyncio.run(asyncio.wait_for(check(), timeout=90))
elif a.mode == 'gpu':
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('No CUDA GPU visible to the container; apply for a GPU before running')
    free, total = torch.cuda.mem_get_info(0)
    if free < 18 * 1024**3:
        raise RuntimeError('Less than 18 GiB free on visible cuda:0; do not start TRELLIS yet')
    save(out / 'gpu.json', dict(status='passed', torch=torch.__version__, cuda=torch.version.cuda,
         name=torch.cuda.get_device_name(0), free_bytes=free, total_bytes=total,
         cuda_visible_devices=os.environ.get('CUDA_VISIBLE_DEVICES'), device='cuda:0'))
    print('GPU ' + torch.cuda.get_device_name(0), flush=True)
else:
    # Load all learned weights before paying for image generation. This is NOT an inference pass.
    import gc
    import torch
    from transformers import AutoModel, AutoProcessor, AutoImageProcessor, AutoModelForZeroShotObjectDetection
    dino = AutoModelForZeroShotObjectDetection.from_pretrained(os.environ['LR_DINO_MODEL'], local_files_only=True).cuda()
    AutoProcessor.from_pretrained(os.environ['LR_DINO_MODEL'], local_files_only=True)
    del dino; gc.collect(); torch.cuda.empty_cache()
    embed = AutoModel.from_pretrained(os.environ['LR_DINO_EMBED_MODEL'], local_files_only=True).cuda()
    AutoImageProcessor.from_pretrained(os.environ['LR_DINO_EMBED_MODEL'], local_files_only=True)
    del embed; gc.collect(); torch.cuda.empty_cache()
    from litereality_agent.models.trellis.inference import load_pipeline
    model = load_pipeline(str(Path(os.environ['LITEREALITY_WEIGHTS']) / 'trellis2_4b_local'))
    torch.cuda.synchronize()
    save(out / 'model_load.json', dict(status='passed', grounding_dino=True, dinov2=True, trellis=True,
         inference_tested=False, note='Full geometry inference is tested by reconstruct, not by loading weights.'))
print('PREFLIGHT_' + a.mode.upper() + '_PASS', flush=True)
