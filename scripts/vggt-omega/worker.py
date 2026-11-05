#!/usr/bin/env python3
"""GPU worker. Parent run.py captures errors and builds the return archive."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import time
import numpy as np
from common import save_json, sha256
from geometry import select_cloud, write_ply


def preview(report, points, colors, centers, rgb, depth):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    sample = np.linspace(0, len(points) - 1, min(40000, len(points)), dtype=int)
    p, c = points[sample], colors[sample] / 255.0
    fig = plt.figure(figsize=(14, 6))
    for index, axes in enumerate(((0, 2), (0, 1))):
        ax = fig.add_subplot(1, 2, index + 1)
        ax.scatter(p[:, axes[0]], p[:, axes[1]], c=c, s=0.4, rasterized=True)
        ax.plot(centers[:, axes[0]], centers[:, axes[1]], color='red', linewidth=0.8)
        ax.scatter(centers[0, axes[0]], centers[0, axes[1]], c='lime', s=30, label='first camera')
        ax.set_aspect('equal', adjustable='datalim')
        ax.set_xlabel('XYZ'[axes[0]] + ' (arbitrary scale)')
        ax.set_ylabel('XYZ'[axes[1]] + ' (arbitrary scale)')
        ax.set_title('Uncropped cloud and predicted camera path')
        ax.legend()
    fig.tight_layout()
    fig.savefig(report / 'preview.png', dpi=160)
    plt.close(fig)
    chosen = np.linspace(0, len(rgb) - 1, 6, dtype=int)
    fig, axes = plt.subplots(2, 6, figsize=(14, 8))
    valid = depth[np.isfinite(depth) & (depth > 0)]
    low, high = np.percentile(valid, [2, 98])
    for column, i in enumerate(chosen):
        axes[0, column].imshow(rgb[i])
        axes[0, column].set_title('Input #%d' % (i + 1))
        axes[1, column].imshow(np.ma.masked_invalid(depth[i]), vmin=low, vmax=high, cmap='magma')
        for row in (0, 1):
            axes[row, column].axis('off')
    fig.suptitle('RGB and predicted depth; shared color range clipped at 2 / 98 percentiles')
    fig.tight_layout()
    fig.savefig(report / 'depth_preview.png', dpi=130)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    report = Path(config['report'])
    expected_count = config.get('input_frame_count', len(config['frames']))
    if expected_count != len(config['frames']) or expected_count < 1:
        raise ValueError('Input config frame count mismatch')
    import torch
    from vggt_omega.models import VGGTOmega
    from vggt_omega.utils.load_fn import load_and_preprocess_images
    from vggt_omega.utils.pose_enc import encoding_to_camera
    if not torch.cuda.is_available():
        raise RuntimeError('No CUDA device visible inside this container')
    torch.cuda.set_device(0)
    torch.manual_seed(0)
    torch.cuda.manual_seed_all(0)
    free, total = torch.cuda.mem_get_info(0)
    device = torch.cuda.get_device_properties(0)
    hardware = dict(gpu=device.name, free_memory_bytes=free, total_memory_bytes=total,
                    cuda_visible_devices=os.environ.get('CUDA_VISIBLE_DEVICES'), logical_device=0,
                    torch=torch.__version__, cuda_runtime=torch.version.cuda,
                    bf16_supported=torch.cuda.is_bf16_supported(), packages={
                        d.metadata['Name']: d.version for d in importlib.metadata.distributions()})
    save_json(report / 'hardware.json', hardware)
    print('GPU: ' + device.name + '; free bytes: ' + str(free), flush=True)
    checkpoint = Path(config['checkpoint'])
    checkpoint_hash = sha256(checkpoint)
    save_json(report / 'checkpoint.json', dict(filename=checkpoint.name, sha256=checkpoint_hash,
              bytes=checkpoint.stat().st_size, note='Recorded hash; not compared to an independently published weight hash'))
    images = load_and_preprocess_images(config['frames'], mode=config['mode'],
                                        image_resolution=config['image_resolution'])
    save_json(report / 'preprocessing.json', dict(shape=list(images.shape), mode=config['mode'],
              resolution=config['image_resolution'], image_min=float(images.min()), image_max=float(images.max())))
    if images.shape[0] != expected_count or not torch.isfinite(images).all():
        raise ValueError('Unexpected input tensor')
    print('Loading official 1B model and checkpoint on CPU', flush=True)
    model = VGGTOmega().eval()
    state = torch.load(checkpoint, map_location='cpu', weights_only=True)
    model.load_state_dict(state, strict=True)
    del state
    model = model.to('cuda:0')
    images = images.to('cuda:0')
    torch.cuda.reset_peak_memory_stats(0)
    save_json(report / 'forward_started.json', dict(time_unix=time.time()))
    print('Starting one forward pass over all %d frames' % expected_count, flush=True)
    torch.cuda.synchronize()
    started = time.monotonic()
    with torch.inference_mode():
        pred = model(images)
        extrinsics, intrinsics = encoding_to_camera(pred['pose_enc'], pred['images'].shape[-2:])
    torch.cuda.synchronize()
    seconds = time.monotonic() - started
    peak = torch.cuda.max_memory_allocated(0)
    reserved = torch.cuda.max_memory_reserved(0)
    depth = pred['depth'][0, ..., 0].float().cpu().numpy()
    confidence = pred['depth_conf'][0].float().cpu().numpy()
    e, k = extrinsics[0].float().cpu().numpy(), intrinsics[0].float().cpu().numpy()
    rgb = (pred['images'][0].permute(0, 2, 3, 1).float().clamp(0, 1) * 255).round().byte().cpu().numpy()
    del pred, images, model, extrinsics, intrinsics
    torch.cuda.empty_cache()
    # Preserve raw arrays before filtering, including nonfinite values for diagnosis.
    np.savez_compressed(report / 'predictions.npz', depth=depth, confidence=confidence,
                        extrinsics=e, intrinsics=k, rgb=rgb)
    if depth.shape != confidence.shape or rgb.shape != (*depth.shape, 3) or len(depth) != expected_count:
        raise ValueError('Unexpected model output shapes; see predictions.npz')
    if not np.isfinite(e).all() or not np.isfinite(k).all() or np.any(k[:, (0, 1), (0, 1)] <= 0):
        raise ValueError('Invalid predicted camera parameters; see predictions.npz')
    centers = -np.einsum('nji,nj->ni', e[:, :, :3], e[:, :, 3])
    save_json(report / 'cameras.json', dict(convention='OpenCV world-to-camera: camera = R @ world + t',
              scale='not calibrated in meters', frames=[Path(p).name for p in config['frames']],
              extrinsics=e.tolist(), intrinsics=k.tolist(), centers=centers.tolist()))
    points, colors, scores, stats = select_cloud(depth, confidence, e, k, rgb,
                                                config['confidence_percentile'], config['max_points'])
    write_ply(report / 'room_cloud.ply', points, colors, scores)
    preview(report, points, colors, centers, rgb, depth)
    save_json(report / 'metrics.json', dict(predicted_pose_count=len(e), forward_seconds=seconds,
              peak_allocated_bytes=peak, peak_reserved_bytes=reserved,
              finite_positive_depth_fraction=float(np.mean(np.isfinite(depth) & (depth > 0))),
              cloud=stats, quality_accepted=False,
              limitations=['Predicted pose count is not registration rate or evidence of correct geometry.',
                           'Confidence is an uncalibrated model score, not a probability.',
                           'No measured metric scale, mesh or editable furniture is produced.',
                           'GPU seed is recorded but bitwise determinism is not guaranteed.']))
    print('Export finished; geometry still requires review.', flush=True)


if __name__ == '__main__':
    main()
