"""Compare identical source views and pixels, retaining gauge and sampling caveats."""
import argparse
import csv
import importlib.util
import json
import os
from pathlib import Path
import numpy as np


def fit_similarity(source, target):
    x, y = source - source.mean(axis=0), target - target.mean(axis=0)
    covariance = y.T @ x / len(x)
    u, singular, vt = np.linalg.svd(covariance)
    signs = np.ones(3)
    signs[-1] = np.linalg.det(u @ vt)
    rotation = u @ np.diag(signs) @ vt
    scale = float(np.sum(singular * signs) / np.mean(np.sum(x * x, axis=1)))
    translation = target.mean(axis=0) - scale * source.mean(axis=0) @ rotation.T
    return scale, rotation, translation


def shared_depth_stats(old, new):
    keep = np.isfinite(old) & np.isfinite(new)
    a, b = np.abs(old[keep]), np.abs(new[keep])
    result = dict(count=len(a))
    for name, array in [('old', a), ('new', b)]:
        result[name + '_agreement_5pct'] = float(np.mean(array <= 0.05)) if len(a) else None
        result[name + '_agreement_10pct'] = float(np.mean(array <= 0.10)) if len(a) else None
        result[name + '_median_relative_error'] = float(np.median(array)) if len(a) else None
    return result


def region(a, b):
    for lo, hi, label in [(5, 13, 'early_kitchen_study'), (30.5, 35.5, 'bedroom'),
                          (36.5, 40.5, 'bathroom_mirror'), (41.5, 45.5, 'shower')]:
        if lo <= a <= hi and lo <= b <= hi:
            return label
    return 'transitions'


def load_run(path):
    with np.load(path / 'predictions.npz', allow_pickle=False) as data:
        arrays = {name: data[name] for name in data.files}
    arrays['frames'] = json.loads((path / 'input_manifest.json').read_text())['selected_frames']
    arrays['names'] = [f['file'] for f in arrays['frames']]
    arrays['metrics'] = json.loads((path / 'metrics.json').read_text())
    e = arrays['extrinsics']
    arrays['centers'] = -np.einsum('nji,nj->ni', e[:, :, :3], e[:, :, 3])
    return arrays


def read_cloud(path):
    payload = path.read_bytes().split(b'end_header\n', 1)[1]
    dtype = np.dtype([('x', '<f4'), ('y', '<f4'), ('z', '<f4'), ('r', 'u1'), ('g', 'u1'), ('b', 'u1'), ('confidence', '<f4')])
    data = np.frombuffer(payload, dtype=dtype)
    return np.column_stack([data[n] for n in 'xyz']), np.column_stack([data[n] for n in 'rgb'])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--old', required=True)
    ap.add_argument('--new', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()
    old_path, new_path, output = Path(args.old).resolve(), Path(args.new).resolve(), Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.environ['MPLCONFIGDIR'] = str(output / 'matplotlib_cache')
    os.environ['XDG_CACHE_HOME'] = str(output / 'xdg_cache')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    spec = importlib.util.spec_from_file_location('review', Path(__file__).with_name('review_omega.py'))
    review = importlib.util.module_from_spec(spec); spec.loader.exec_module(review)
    old, new = load_run(old_path), load_run(new_path)
    common = sorted(set(old['names']) & set(new['names']))
    old_ids = [old['names'].index(n) for n in common]
    new_ids = [new['names'].index(n) for n in common]
    assert len(common) == 25
    assert np.array_equal(old['rgb'][old_ids], new['rgb'][new_ids])
    for i, j in zip(old_ids, new_ids):
        assert old['frames'][i]['sha256'] == new['frames'][j]['sha256']
    threshold = old['metrics']['cloud']['confidence_threshold']
    times = [old['frames'][i]['resampled_time_seconds'] for i in old_ids]
    pairs, pooled_old, pooled_new, groups = [], [], [], {}
    for i in range(len(common) - 1):
        if times[i + 1] - times[i] > 1.25:
            continue  # do not turn the gap in common views into an adjacent pair
        for a, b in [(i, i + 1), (i + 1, i)]:
            heats = []
            for data, indices in [(old, old_ids), (new, new_ids)]:
                _, heat = review.pair_consistency(data['depth'], data['confidence'], data['extrinsics'],
                      data['intrinsics'], indices[a], indices[b], threshold, stride=8)
                heats.append(heat)
            keep = np.isfinite(heats[0]) & np.isfinite(heats[1])
            values = heats[0][keep], heats[1][keep]
            label = region(times[a], times[b])
            score = shared_depth_stats(*values)
            score.update(source=common[a], target=common[b], region=label)
            score['delta_5pct_percentage_points'] = 100 * (score['new_agreement_5pct'] - score['old_agreement_5pct']) if score['count'] else None
            pairs.append(score)
            pooled_old.append(values[0]); pooled_new.append(values[1])
            group = groups.setdefault(label, [[], []])
            group[0].append(values[0]); group[1].append(values[1])
    aggregate = shared_depth_stats(np.concatenate(pooled_old), np.concatenate(pooled_new))
    per_region = {label: shared_depth_stats(np.concatenate(values[0]), np.concatenate(values[1])) for label, values in groups.items()}
    with (output / 'matched_pair_comparison.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(pairs[0])); writer.writeheader(); writer.writerows(pairs)
    scale, rotation, translation = fit_similarity(new['centers'][new_ids], old['centers'][old_ids])
    mapped = scale * new['centers'] @ rotation.T + translation
    residuals = np.linalg.norm(mapped[new_ids] - old['centers'][old_ids], axis=1)
    display_up = np.median(-old['extrinsics'][:, 1, :3], axis=0); display_up /= np.linalg.norm(display_up)
    forward = old['extrinsics'][0, 2, :3] - np.dot(old['extrinsics'][0, 2, :3], display_up) * display_up
    forward /= np.linalg.norm(forward)
    basis = np.stack((np.cross(forward, display_up), forward, display_up), axis=1)
    clouds = []
    for i, path in enumerate((old_path, new_path)):
        xyz, color = read_cloud(path / 'room_cloud.ply')
        if i: xyz = scale * xyz @ rotation.T + translation
        clouds.append((xyz @ basis, color))
    trajectories = [old['centers'] @ basis, mapped @ basis]
    low = np.min(np.vstack([c[0].min(axis=0) for c in clouds]), axis=0)
    high = np.max(np.vstack([c[0].max(axis=0) for c in clouds]), axis=0)
    fig, axes = plt.subplots(1, 2, figsize=(12, 9), layout='constrained')
    fractions = []
    for ax, count, (xyz, color), trajectory in zip(axes, (50, 100), clouds, trajectories):
        ids = np.random.default_rng(0).choice(len(xyz), 120000, replace=False)
        selected = ids[xyz[ids, 2] <= 0.12]
        fractions.append(float(np.mean(xyz[:, 2] <= 0.12)))
        ax.scatter(xyz[selected, 0], xyz[selected, 1], c=color[selected] / 255, s=0.5, rasterized=True)
        ax.plot(trajectory[:, 0], trajectory[:, 1], color='#c82c26', lw=0.8)
        ax.set(xlim=(low[0] - 0.03, high[0] + 0.03), ylim=(low[1] - 0.03, high[1] + 0.03),
               xlabel='Common display X (arbitrary scale)', ylabel='Common display forward (arbitrary scale)',
               title='%d input frames | exported 500,000 points' % count)
        ax.set_aspect('equal')
    fig.suptitle('Actual point clouds, shared scale and view | 100-frame result aligned using 25 shared camera centers\nUpper surfaces hidden only for display; original PLY files unchanged')
    fig.savefig(output / 'geometry_50_vs_100.png', dpi=150, bbox_inches='tight', pad_inches=0.15); plt.close(fig)
    summary = dict(old_run=old_path.name, new_run=new_path.name, common_frame_count=len(common), common_frames=common,
        common_rgb_identical=True, directed_common_pairs=len(pairs), common_frames_time_ranges=[[5, 13], [30.5, 45.5]],
        comparison_parameters=dict(source_stride_pixels=8, common_confidence_threshold=threshold,
             threshold_source='Old 50-frame run export threshold, applied to both runs',
             mask='Intersection of finite comparisons at identical source pixels in both runs'),
        matched_aggregate=aggregate, matched_regions=per_region,
        greatest_regressions=sorted([x for x in pairs if x['count'] >= 100], key=lambda r: r['delta_5pct_percentage_points'])[:6],
        greatest_improvements=sorted([x for x in pairs if x['count'] >= 100], key=lambda r: r['delta_5pct_percentage_points'], reverse=True)[:6],
        similarity_alignment=dict(new_to_old_scale=scale, rotation=rotation.tolist(), translation=translation.tolist(),
             median_camera_residual_arbitrary_units=float(np.median(residuals)), max_camera_residual_arbitrary_units=float(residuals.max()),
             interpretation='Prediction stability after gauge alignment; neither run is ground truth'),
        old_runtime=old['metrics'], new_runtime=new['metrics'], display_cutaway_retained_fractions=fractions,
        limitations=['Common-view scores do not cover every new frame or the balcony and late revisit views.',
                     'Internal depth consistency is not metric accuracy; shared errors, occlusion and reflections remain possible.',
                     'Each exported PLY uses its own global confidence percentile; images compare exported artifacts, not identical point samples.',
                     'One timed run per condition; no reliable runtime scaling conclusion.'])
    (output / 'comparison.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    print(json.dumps(summary, indent=2, allow_nan=False))


if __name__ == '__main__':main()
