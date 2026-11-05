#!/usr/bin/env python3
"""Validate the fixed input manifest, run the experiment and package its results."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import traceback
from common import PACKAGE, REVISION, clean_env, new_report, pack_report, run_step, save_json, sha256, verify_frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', default='/yinxin/projects/room')
    parser.add_argument('--frames-dir', help='Override the directory; all manifest file hashes must match')
    parser.add_argument('--checkpoint', help='Default: return_output/model_cache/vggt_omega/vggt_omega_1b_512.pt')
    parser.add_argument('--prepare-only', action='store_true', help='Check input hashes; no install, weights or GPU needed')
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args()
    root = Path(args.project_root).expanduser().resolve()
    report = new_report(root, 'artifacts', 'room_omega_')
    result = dict(status='failed', inference_executed=False, quality_accepted=False, source_revision=REVISION)
    try:
        checkpoint = Path(args.checkpoint).expanduser().resolve() if args.checkpoint else root / 'return_output/model_cache/vggt_omega/vggt_omega_1b_512.pt'
        if not args.prepare_only and not checkpoint.is_file():
            raise FileNotFoundError('Missing checkpoint: ' + str(checkpoint) + '; wait for authorized model access')
        manifest = json.loads((PACKAGE / 'input_manifest.json').read_text())
        frames_dir = Path(args.frames_dir).expanduser().resolve() if args.frames_dir else root / manifest['server_source_relative']
        frames = verify_frames(frames_dir, manifest)
        save_json(report / 'input_manifest.json', manifest)
        config = dict(frames=[str(p) for p in frames], checkpoint=str(checkpoint), report=str(report),
                      input_frame_count=len(frames), experiment_id=manifest.get('experiment_id', 'legacy_manifest'),
                      image_resolution=512, mode='max_size', confidence_percentile=50, max_points=500000,
                      logical_device=0, source_revision=REVISION)
        save_json(report / 'config.json', config)
        result['input_frame_count'] = len(frames)
        result['experiment_id'] = config['experiment_id']
        print('Verified %d input frames for %s' % (len(frames), config['experiment_id']), flush=True)
        if args.prepare_only:
            result['status'] = 'inputs_verified_only'
        else:
            pointer = root / 'return_output/environments/omega-current.json'
            if not pointer.is_file():
                raise FileNotFoundError('Run setup.py successfully first; missing ' + str(pointer))
            environment = json.loads(pointer.read_text())
            if environment['source_revision'] != REVISION or environment['status'] != 'ready_without_weights':
                raise ValueError('Installed environment revision/status mismatch; rerun setup.py')
            python = Path(environment['python'])
            if not python.is_file():
                raise FileNotFoundError('Saved environment missing; rerun setup.py in this container')
            shutil.copy2(pointer, report / 'environment.json')
            env = clean_env(root)
            result['worker_started'] = True
            run_step('inference', [python, '-B', PACKAGE / 'worker.py', '--config', report / 'config.json'],
                     report, env, timeout=args.timeout)
            worker_result = json.loads((report / 'metrics.json').read_text())
            result.update(status='inference_completed_needs_review', inference_executed=True, metrics=worker_result)
    except Exception as exc:
        result.update(error=str(exc), traceback=traceback.format_exc())
        # This marker is created immediately before forward; failures are still returned.
        result['inference_executed'] = (report / 'forward_started.json').exists()
        print('RUN FAILED: ' + str(exc), flush=True)
    finally:
        save_json(report / 'scripts_sha256.json', {p.name: sha256(p) for p in PACKAGE.iterdir() if p.is_file()})
        save_json(report / 'result.json', result)
        pack_report(report, root / 'return_output')
    print('STATUS: ' + result['status'], flush=True)
    return 1 if result['status'] == 'failed' else 0


if __name__ == '__main__':
    sys.exit(main())
