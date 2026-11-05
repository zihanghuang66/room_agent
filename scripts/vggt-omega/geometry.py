"""OpenCV world-to-camera depth unprojection and explicit display filtering."""
from pathlib import Path
import numpy as np


def unproject(depth, extrinsics, intrinsics):
    n, h, w = depth.shape
    u, v = np.meshgrid(np.arange(w), np.arange(h))
    camera = np.stack(((u[None] - intrinsics[:, None, None, 0, 2]) / intrinsics[:, None, None, 0, 0] * depth,
                       (v[None] - intrinsics[:, None, None, 1, 2]) / intrinsics[:, None, None, 1, 1] * depth,
                       depth), axis=-1).astype(np.float32)
    # Row-vector form of world = R.T @ (camera - t).
    return np.einsum('nhwj,njk->nhwk', camera - extrinsics[:, None, None, :, 3], extrinsics[:, :, :3])


def select_cloud(depth, confidence, extrinsics, intrinsics, rgb, percentile, max_points):
    if not 0 <= percentile <= 100 or max_points < 1:
        raise ValueError('Invalid point filtering parameters')
    world = unproject(depth, extrinsics, intrinsics)
    valid = np.isfinite(depth) & (depth > 0) & np.isfinite(confidence) & np.isfinite(world).all(axis=-1)
    count = int(valid.sum())
    if not count:
        raise ValueError('No finite positive-depth points')
    threshold = float(np.percentile(confidence[valid], percentile))
    indices = np.flatnonzero(valid & (confidence >= threshold))
    retained = len(indices)
    if retained > max_points:
        # Seeded random sampling avoids a periodic pixel-grid alias.
        indices = np.sort(np.random.default_rng(0).choice(indices, max_points, replace=False))
    stats = dict(valid_pixels=count, confidence_threshold=threshold, confidence_percentile=percentile,
                 points_before_sampling=retained, exported_points=len(indices), sampling_seed=0)
    return world.reshape(-1, 3)[indices], rgb.reshape(-1, 3)[indices], confidence.ravel()[indices], stats


def write_ply(path, points, colors, scores):
    dtype = np.dtype([('x', '<f4'), ('y', '<f4'), ('z', '<f4'),
                      ('red', 'u1'), ('green', 'u1'), ('blue', 'u1'), ('confidence', '<f4')])
    data = np.empty(len(points), dtype=dtype)
    for i, axis in enumerate(('x', 'y', 'z')):
        data[axis] = points[:, i]
    for i, channel in enumerate(('red', 'green', 'blue')):
        data[channel] = colors[:, i]
    data['confidence'] = scores
    header = ('ply\nformat binary_little_endian 1.0\ncomment scale is not calibrated in meters\n'
              'element vertex ' + str(len(points)) + '\nproperty float x\nproperty float y\nproperty float z\n'
              'property uchar red\nproperty uchar green\nproperty uchar blue\nproperty float confidence\nend_header\n')
    with Path(path).open('wb') as stream:
        stream.write(header.encode('ascii'))
        stream.write(data.tobytes())
