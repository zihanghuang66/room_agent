"""Distinguish compiled artifacts, runtime imports, and CUDA execution."""
import argparse
import importlib
import json
from pathlib import Path
import sysconfig

parser = argparse.ArgumentParser()
parser.add_argument('--files-only', action='store_true')
args = parser.parse_args()
site = Path(sysconfig.get_paths()['platlib'])
patterns = ['cumesh/_C*.so', 'cumesh/_cubvh*.so', 'cumesh/_cumesh_xatlas*.so',
            'flex_gemm/kernels/cuda*.so', 'o_voxel/_C*.so', '_nvdiffrast_c*.so',
            'nvdiffrec_render/renderutils/_C*.so']
files = {p: [str(f.relative_to(site)) for f in site.glob(p)] for p in patterns}
missing = [p for p, matches in files.items() if not matches]
print(json.dumps({'compiled_files': files, 'missing': missing}))
if missing:
    raise SystemExit('Missing compiled CUDA extensions')
if not args.files_only:
    import torch
    assert torch.cuda.is_available(), 'PyTorch cannot access an assigned CUDA GPU'
    for name in ['cumesh', 'flex_gemm', 'o_voxel', 'nvdiffrast.torch', 'nvdiffrec_render.renderutils']:
        importlib.import_module(name)
    from litereality_agent.models.runtime_env import configure_trellis_runtime
    configure_trellis_runtime()
    from trellis2.pipelines import Trellis2ImageTo3DPipeline
    # Exercise one compiled CUDA rasterizer, not just an import.
    import nvdiffrast.torch as dr
    v = torch.tensor([[[-.5, -.5, 0., 1.], [.5, -.5, 0., 1.], [0., .5, 0., 1.]]], device='cuda')
    f = torch.tensor([[0, 1, 2]], dtype=torch.int32, device='cuda')
    pixels, _ = dr.rasterize(dr.RasterizeCudaContext(), v, f, resolution=[32, 32])
    torch.cuda.synchronize()
    assert (pixels[..., 3] > 0).any().item(), 'CUDA rasterizer produced no triangle'
    print('GPU_IMPORTS_AND_RASTER_KERNEL_OK')
