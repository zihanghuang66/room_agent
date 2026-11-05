"""Read-only geometric self-consistency review, without ground-truth claims."""
import argparse
import csv
import importlib.util
import json
import os
from pathlib import Path
import numpy as np


def pair_consistency(depth, confidence, extrinsics, intrinsics, source, target, threshold, stride=8):
    h, w = depth.shape[1:]
    v, u = np.mgrid[0:h:stride, 0:w:stride]
    grid_shape = u.shape
    u, v = u.ravel(), v.ravel()
    z = depth[source, v, u]
    valid = np.isfinite(z) & (z > 0) & (confidence[source, v, u] >= threshold)
    source_count = int(valid.sum())
    source_ids = np.flatnonzero(valid)
    u, v, z = u[valid], v[valid], z[valid]
    es, et = extrinsics[source], extrinsics[target]
    ks, kt = intrinsics[source], intrinsics[target]
    camera = np.column_stack(((u - ks[0, 2]) / ks[0, 0] * z, (v - ks[1, 2]) / ks[1, 1] * z, z))
    world = (camera - es[:, 3]) @ es[:, :3]
    target_camera = world @ et[:, :3].T + et[:, 3]
    zt = target_camera[:, 2]
    with np.errstate(divide='ignore', invalid='ignore'):
        x = target_camera[:, 0] / zt * kt[0, 0] + kt[0, 2]
        y = target_camera[:, 1] / zt * kt[1, 1] + kt[1, 2]
    inside = (zt > 0) & np.isfinite(x) & np.isfinite(y) & (x >= 0) & (x < w - 1) & (y >= 0) & (y < h - 1)
    x, y, zt, source_ids = x[inside], y[inside], zt[inside], source_ids[inside]
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    dx, dy = x - x0, y - y0

    def sample(array):
        return (array[y0, x0] * (1 - dx) * (1 - dy) + array[y0, x0 + 1] * dx * (1 - dy)
                + array[y0 + 1, x0] * (1 - dx) * dy + array[y0 + 1, x0 + 1] * dx * dy)

    target_depth = sample(depth[target])
    keep = np.isfinite(target_depth) & (target_depth > 0) & (sample(confidence[target]) >= threshold)
    relative = (zt[keep] - target_depth[keep]) / target_depth[keep]
    error = np.abs(relative)
    heat = np.full(np.prod(grid_shape), np.nan)
    heat[source_ids[keep]] = relative
    count = len(relative)
    result = dict(source_index=source, target_index=target, source_confident_pixels=source_count,
                  in_target_view_pixels=int(inside.sum()), compared_pixels=count,
                  compared_fraction_of_confident_source=count / source_count if source_count else None,
                  median_relative_depth_error=float(np.median(error)) if count else None,
                  depth_agreement_5pct=float(np.mean(error <= 0.05)) if count else None,
                  depth_agreement_10pct=float(np.mean(error <= 0.10)) if count else None,
                  in_front_disagreement_5pct=float(np.mean(relative < -0.05)) if count else None,
                  behind_or_occluded_5pct=float(np.mean(relative > 0.05)) if count else None)
    return result, heat.reshape(grid_shape)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()
    run, output = Path(args.run).resolve(), Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ['MPLCONFIGDIR'] = str(output / 'matplotlib_cache')
    os.environ['XDG_CACHE_HOME'] = str(output / 'xdg_cache')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    with np.load(run / 'predictions.npz', allow_pickle=False) as arrays:
        depth, conf, e, k, rgb = [arrays[n] for n in ('depth', 'confidence', 'extrinsics', 'intrinsics', 'rgb')]
    manifest = json.loads((run / 'input_manifest.json').read_text())['selected_frames']
    names = [x['file'] for x in manifest]
    times = np.array([x['resampled_time_seconds'] for x in manifest])
    metrics = json.loads((run / 'metrics.json').read_text())
    threshold = metrics['cloud']['confidence_threshold']
    frame_count = len(manifest)
    assert depth.shape == conf.shape == (frame_count, 512, 288)
    assert np.isfinite(depth).all() and (depth > 0).all() and np.isfinite(conf).all()
    assert np.isfinite(e).all() and np.isfinite(k).all()
    centers = -np.einsum('nji,nj->ni', e[:, :, :3], e[:, :, 3])
    camera_json = json.loads((run / 'cameras.json').read_text())
    assert camera_json['frames'] == names
    assert np.allclose(centers, camera_json['centers'], atol=1e-6)
    rows = []
    for i in range(frame_count - 1):
        for a, b in [(i, i + 1), (i + 1, i)]:
            score, _ = pair_consistency(depth, conf, e, k, a, b, threshold)
            rows.append(dict(kind='adjacent', source=names[a], target=names[b], **score))
    revisit_names = [(21, 220), (41, 216), (45, 220), (29, 192), (33, 200), (49, 118)]
    revisit_heat = []
    for a, b in revisit_names:
        # Match the planned observation times when the sampling grid changes.
        i, j = int(np.argmin(abs(times - (a - 1) / 4))), int(np.argmin(abs(times - (b - 1) / 4)))
        for s, t in [(i, j), (j, i)]:
            score, heat = pair_consistency(depth, conf, e, k, s, t, threshold)
            rows.append(dict(kind='revisit_candidate', source=names[s], target=names[t], **score))
            if s == i:
                revisit_heat.append((i, j, score, heat))
    with (output / 'pair_consistency.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    adjacent = [x for x in rows if x['kind'] == 'adjacent' and x['compared_pixels']]
    total = sum(x['compared_pixels'] for x in adjacent)
    summary = dict(
        interpretation='Internal prediction consistency only, not accuracy against measurements; occlusion and low overlap affect scores.',
        parameters=dict(stride=8, confidence_min=threshold, depth_error_denominator='target predicted depth',
                        target_sampling='bilinear', agreement_thresholds=[0.05, 0.10]),
        adjacent_compared_pixels=total,
        adjacent_weighted_agreement_5pct=sum(x['depth_agreement_5pct'] * x['compared_pixels'] for x in adjacent) / total,
        adjacent_weighted_agreement_10pct=sum(x['depth_agreement_10pct'] * x['compared_pixels'] for x in adjacent) / total,
        revisit_pairs=[x for x in rows if x['kind'] == 'revisit_candidate'],
        worst_adjacent_pairs_with_at_least_100_samples=sorted([x for x in adjacent if x['compared_pixels'] >= 100], key=lambda r: r['depth_agreement_5pct'])[:12],
        rotation_orthogonality_max_abs=float(np.max(abs(e[:, :, :3] @ e[:, :, :3].transpose(0, 2, 1) - np.eye(3)))),
        determinant_range=[float(x) for x in (np.linalg.det(e[:, :, :3]).min(), np.linalg.det(e[:, :, :3]).max())],
        camera_path_length_arbitrary_units=float(np.linalg.norm(np.diff(centers, axis=0), axis=1).sum()),
        start_end_distance_arbitrary_units=float(np.linalg.norm(centers[-1] - centers[0])),
        metric_scale_verified=False, quality_accepted=False)
    # Recompute the exported cloud to check coordinate/serialization consistency.
    geometry_path = Path(__file__).resolve().parents[1] / 'vggt-omega/geometry.py'
    spec = importlib.util.spec_from_file_location('omega_geometry', geometry_path)
    geometry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(geometry)
    expected_xyz, expected_rgb, expected_scores, _ = geometry.select_cloud(depth, conf, e, k, rgb, 50, 500000)
    raw = (run / 'room_cloud.ply').read_bytes().split(b'end_header\n', 1)[1]
    dtype = np.dtype([('x', '<f4'), ('y', '<f4'), ('z', '<f4'), ('r', 'u1'), ('g', 'u1'), ('b', 'u1'), ('confidence', '<f4')])
    vertices = np.frombuffer(raw, dtype=dtype)
    xyz = np.column_stack([vertices[n] for n in ('x', 'y', 'z')])
    colors = np.column_stack([vertices[n] for n in ('r', 'g', 'b')])
    summary['ply_max_coordinate_difference_from_recomputed'] = float(np.max(abs(xyz - expected_xyz)))
    assert np.allclose(xyz, expected_xyz, atol=1e-6)
    assert np.array_equal(colors, expected_rgb) and np.allclose(vertices['confidence'], expected_scores)
    # Approximate display leveling only: camera-up axes are not a measured gravity reference.
    up = np.median(-e[:, 1, :3], axis=0); up /= np.linalg.norm(up)
    forward = e[0, 2, :3] - np.dot(e[0, 2, :3], up) * up; forward /= np.linalg.norm(forward)
    basis = np.stack((np.cross(forward, up), forward, up), axis=1)
    leveled, trajectory = xyz @ basis, centers @ basis
    summary['display_only'] = dict(up_world=up.tolist(), cutaway_max_height=0.12,
                                   cutaway_retained_fraction=float(np.mean(leveled[:, 2] <= 0.12)),
                                   scale='arbitrary', original_ply_modified=False)
    sample = np.random.default_rng(0).choice(len(xyz), 90000, replace=False)
    fig = plt.figure(figsize=(14, 7), layout='constrained')
    ax = fig.add_subplot(121)
    cut = sample[leveled[sample, 2] <= 0.12]
    ax.scatter(leveled[cut, 0], leveled[cut, 1], c=colors[cut] / 255, s=0.5, rasterized=True)
    ax.plot(trajectory[:, 0], trajectory[:, 1], color='#d52c27', lw=1)
    ax.scatter(trajectory[:, 0], trajectory[:, 1], c=times, cmap='plasma', s=10)
    for target_time in (5, 14.25, 21.25, 28.25, 33.5, 39.5, 45.5, 54.75):
        i = int(np.argmin(abs(times - target_time)))
        ax.annotate('%.2fs' % times[i], trajectory[i, :2], xytext=(4, 4), textcoords='offset points', fontsize=8)
    ax.set_aspect('equal'); ax.set_title('Top view, upper surfaces hidden for inspection')
    ax.set_xlabel('Display X (arbitrary scale)'); ax.set_ylabel('Display forward (arbitrary scale)')
    ax = fig.add_subplot(122, projection='3d')
    ax.scatter(*leveled[cut].T, c=colors[cut] / 255, s=0.5, depthshade=False, rasterized=True)
    ax.plot(*trajectory.T, color='#d52c27', lw=1)
    ax.set_box_aspect(np.maximum(np.ptp(leveled[cut], axis=0), 0.01)); ax.view_init(42, -63)
    ax.set_title('Same cutaway in 3D; original PLY is unchanged')
    ax.set(xlabel='X', ylabel='Forward', zlabel='Approx. up')
    fig.suptitle('Actual VGGT-Omega output | approximate leveling from camera axes, not measured gravity')
    fig.savefig(output / 'geometry_review.png', dpi=150); plt.close(fig)
    selected = [int(np.argmin(abs(times - t))) for t in (5, 14.25, 31.5, 34.5, 37.5, 44.5)]
    fig, axes = plt.subplots(3, 6, figsize=(13, 13), layout='constrained')
    for column, i in enumerate(selected):
        axes[0, column].imshow(rgb[i]); axes[0, column].set_title('%s\n%.2fs' % (names[i][6:12], times[i]))
        lo, hi = np.quantile(depth[i], [0.02, 0.98])
        axes[1, column].imshow(depth[i], vmin=lo, vmax=hi, cmap='magma')
        axes[1, column].set_title('Depth %.3f..%.3f' % (lo, hi), fontsize=9)
        axes[2, column].imshow(conf[i] >= threshold, cmap='gray', vmin=0, vmax=1)
        axes[2, column].set_title('Kept by confidence: %.1f%%' % (100 * np.mean(conf[i] >= threshold)), fontsize=9)
        for row in range(3): axes[row, column].axis('off')
    fig.suptitle('RGB / per-frame depth color range / export confidence mask (white = kept)\nDepth colors use different ranges per column; no metric scale.')
    fig.savefig(output / 'depth_review.png', dpi=120); plt.close(fig)
    fig, axes = plt.subplots(3, 4, figsize=(11, 11), layout='constrained')
    for row, (i, j, score, heat) in enumerate([revisit_heat[x] for x in (1, 4, 5)]):
        axes[row, 0].imshow(rgb[i]); axes[row, 0].set_title('Source %.2fs' % times[i])
        axes[row, 1].imshow(rgb[j]); axes[row, 1].set_title('Revisit %.2fs' % times[j])
        image = axes[row, 2].imshow(heat, cmap='coolwarm', vmin=-0.2, vmax=0.2)
        axes[row, 2].set_title('Relative depth discrepancy')
        axes[row, 3].text(0.03, 0.6, 'Compared pixels: %d\nWithin 5%%: %.1f%%\nWithin 10%%: %.1f%%\nMedian error: %.1f%%\n\nGray/blank: not compared\nPositive: behind target depth\n(occlusion or mismatch)' % (score['compared_pixels'], 100 * (score['depth_agreement_5pct'] or 0), 100 * (score['depth_agreement_10pct'] or 0), 100 * (score['median_relative_depth_error'] or 0)), fontsize=10)
        for col in range(4): axes[row, col].axis('off')
    fig.suptitle('Revisit self-consistency at confident overlapping pixels; not measured reconstruction accuracy')
    fig.colorbar(image, ax=axes[:, 2], shrink=0.6)
    fig.savefig(output / 'revisit_review.png', dpi=130); plt.close(fig)
    frame_rows = [dict(file=names[i], time_seconds=float(times[i]), median_depth=float(np.median(depth[i])),
                       export_confident_fraction=float(np.mean(conf[i] >= threshold)), fx=float(k[i, 0, 0]), fy=float(k[i, 1, 1])) for i in range(frame_count)]
    (output / 'frame_summary.json').write_text(json.dumps(frame_rows, indent=2) + '\n')
    (output / 'review_metrics.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    print(json.dumps(summary, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
