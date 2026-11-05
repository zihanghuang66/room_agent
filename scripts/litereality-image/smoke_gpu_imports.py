"""Validate CUDA wiring and imports without downloading models or running TRELLIS.

This build host has a Pascal GPU. Target extension kernels are compiled for
A100/3090/4090, so they must be tested separately on those cards.
"""
import importlib
import json
import os
from pathlib import Path
import traceback

import torch

result = {'target_extension_kernels_tested': False, 'model_inference_tested': False,
          'imports': {}, 'status': 'failed', 'ld_library_path': os.environ.get('LD_LIBRARY_PATH', '')}
out = Path('/yinxin/projects/room/return_output/gpu-imports.json')
out.parent.mkdir(parents=True, exist_ok=True)
try:
    assert torch.cuda.is_available(), 'No CUDA device visible'
    result['gpu'] = torch.cuda.get_device_name(0)
    result['compute_capability'] = torch.cuda.get_device_capability(0)
    values = torch.arange(1024, device='cuda', dtype=torch.float32)
    assert torch.equal((values + 1).cpu(), torch.arange(1024, dtype=torch.float32) + 1)
    torch.cuda.synchronize()
    result['pytorch_cuda_kernel'] = 'passed'
    result['loaded_cuda_driver_libraries'] = sorted({line.split()[-1] for line in Path('/proc/self/maps').read_text().splitlines() if 'libcuda.so' in line or 'libnvidia-ptxjitcompiler.so' in line})
    for name in ('cumesh', 'flex_gemm', 'o_voxel', 'nvdiffrast.torch',
                 'nvdiffrec_render.renderutils', 'xformers.ops',
                 'cumesh._C', 'cumesh._cubvh', 'cumesh._cumesh_xatlas',
                 'flex_gemm.kernels.cuda', 'o_voxel._C', '_nvdiffrast_c',
                 'nvdiffrec_render.renderutils._C'):
        try:
            importlib.import_module(name)
            result['imports'][name] = 'passed'
        except Exception:
            result['imports'][name] = traceback.format_exc()
    from litereality_agent.models.runtime_env import configure_trellis_runtime
    configure_trellis_runtime()
    from trellis2.pipelines import Trellis2ImageTo3DPipeline
    result['trellis_pipeline_import'] = 'passed'
    assert all(v == 'passed' for v in result['imports'].values()), 'Extension import failure'
    result['status'] = 'passed'
except Exception:
    result['error'] = traceback.format_exc()
finally:
    out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
raise SystemExit(0 if result['status'] == 'passed' else 1)
