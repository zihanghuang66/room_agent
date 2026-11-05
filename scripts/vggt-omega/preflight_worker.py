"""Small independent checks, executed inside the previously installed venv."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import sys
from common import PACKAGE, save_json


def check_environment(report):
    import torch
    import torchvision
    from vggt_omega.models import VGGTOmega
    expected = json.loads((PACKAGE / 'environment_reference.json').read_text())
    actual = {d.metadata['Name'].lower().replace('_', '-'): d.version for d in importlib.metadata.distributions()}
    differences = {name: dict(expected=version, actual=actual.get(name)) for name, version in expected['packages'].items()
                   if actual.get(name) != version}
    data = dict(python=sys.version, platform=platform.platform(), packages=actual, reference_differences=differences,
                disk_free_bytes=shutil.disk_usage(report).free, visible_devices=os.environ.get('CUDA_VISIBLE_DEVICES'),
                model_imported=VGGTOmega.__name__, torchvision=torchvision.__version__, cuda_available=torch.cuda.is_available())
    save_json(report / 'runtime.json', data)
    critical = {'torch', 'torchvision', 'numpy', 'vggt-omega'} & set(differences)
    if critical:
        raise RuntimeError('Core package version changed: ' + ', '.join(sorted(critical)))
    if not torch.cuda.is_available():
        raise RuntimeError('No usable CUDA device in this container')
    free, total = torch.cuda.mem_get_info(0)
    x = torch.ones((32, 32), device='cuda:0')
    y = x @ x
    torch.cuda.synchronize(0)
    if float(y[0, 0]) != 32:
        raise RuntimeError('CUDA arithmetic check failed')
    data.update(gpu=torch.cuda.get_device_name(0), total_memory_bytes=total, free_memory_bytes=free,
                cuda_runtime=torch.version.cuda, bf16_supported=torch.cuda.is_bf16_supported(),
                small_cuda_operation_passed=True, model_forward_executed=False)
    save_json(report / 'runtime.json', data)
    print('Imports, core versions and small CUDA operation passed; no weights or model forward.')


def check_images(report):
    import torch
    from vggt_omega.utils.load_fn import load_and_preprocess_images
    from torchvision.utils import make_grid
    from torchvision.transforms.functional import to_pil_image
    config = json.loads((report / 'input_config.json').read_text())
    images = load_and_preprocess_images(config['frames'], mode='max_size', image_resolution=512)
    data = dict(shape=list(images.shape), min=float(images.min()), max=float(images.max()),
                all_finite=bool(torch.isfinite(images).all()), expected_shape=[len(config['frames']), 3, 512, 288],
                device=str(images.device), model_forward_executed=False)
    save_json(report / 'preprocessing.json', data)
    if list(images.shape) != data['expected_shape'] or not data['all_finite'] or data['min'] < 0 or data['max'] > 1:
        raise ValueError('Official preprocessing produced an unexpected tensor')
    # Return only a small contact sheet; this is real input, not predicted geometry.
    thumbnails = torch.nn.functional.interpolate(images, size=(256, 144), mode='bilinear', align_corners=False)
    to_pil_image(make_grid(thumbnails, nrow=10, padding=3)).save(report / 'preprocessed_inputs.jpg', quality=90)
    print('Verified official preprocessing of all %d real frames on CPU: %s' % (len(config['frames']), list(images.shape)))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mode', choices=['environment', 'images'], required=True)
    ap.add_argument('--report', required=True)
    args = ap.parse_args()
    report = Path(args.report)
    (check_environment if args.mode == 'environment' else check_images)(report)


if __name__ == '__main__':
    main()
