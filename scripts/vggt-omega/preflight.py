#!/usr/bin/env python3
"""Batch checks for the installed Omega environment; never runs model forward."""
import argparse
import json
from pathlib import Path
import sys
import traceback
from common import PACKAGE, REVISION, clean_env, new_report, pack_report, run_step, save_json, sha256, verify_frames


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project-root', default='/yinxin/projects/room')
    ap.add_argument('--frames-dir')
    ap.add_argument('--checkpoint', help='Check this weight path for presence, without loading it')
    args = ap.parse_args()
    root = Path(args.project_root).expanduser().resolve()
    report = new_report(root, 'preflight_reports', 'room_omega_preflight_')
    checks = {}
    result = dict(status='needs_attention', inference_executed=False, source_revision=REVISION, checks=checks)

    def check(name, action):
        try:
            value = action()
            checks[name] = dict(status='passed')
            return value
        except Exception as exc:
            checks[name] = dict(status='failed', error=str(exc), traceback=traceback.format_exc())
            print(name + ' FAILED: ' + str(exc), flush=True)
            return None

    try:
        env = clean_env(root)
        manifest = json.loads((PACKAGE / 'input_manifest.json').read_text())
        frames_root = Path(args.frames_dir).expanduser().resolve() if args.frames_dir else root / manifest['server_source_relative']

        def inputs():
            frames = verify_frames(frames_root, manifest)
            save_json(report / 'input_manifest.json', manifest)
            save_json(report / 'input_config.json', dict(frames=[str(p) for p in frames], report=str(report), input_frame_count=len(frames)))
            return frames

        frames = check('inputs', inputs)

        def environment():
            pointer = root / 'return_output/environments/omega-current.json'
            installed = json.loads(pointer.read_text())
            if installed['source_revision'] != REVISION or installed['status'] != 'ready_without_weights':
                raise ValueError('Saved environment does not match this experiment')
            python = Path(installed['python'])
            if not python.is_file():
                raise FileNotFoundError('Environment Python missing: ' + str(python))
            save_json(report / 'environment.json', installed)
            return python

        python = check('environment', environment)
        if python is not None:
            check('dependencies', lambda: run_step('dependencies', [sys.executable, '-m', 'pip', '--isolated',
                  '--disable-pip-version-check', '--python', python, 'check'], report, env, timeout=120))
            check('gpu_and_versions', lambda: run_step('gpu_and_versions', [python, '-B', PACKAGE / 'preflight_worker.py',
                  '--mode', 'environment', '--report', report], report, env, timeout=180))
            if frames is not None:
                check('official_preprocessing', lambda: run_step('official_preprocessing', [python, '-B',
                      PACKAGE / 'preflight_worker.py', '--mode', 'images', '--report', report], report, env, timeout=180))
            else:
                checks['official_preprocessing'] = dict(status='skipped', reason='Input hashes not verified')
        else:
            for name in ('dependencies', 'gpu_and_versions', 'official_preprocessing'):
                checks[name] = dict(status='skipped', reason='Installed environment unavailable')
        checkpoint = Path(args.checkpoint).expanduser().resolve() if args.checkpoint else root / 'return_output/model_cache/vggt_omega/vggt_omega_1b_512.pt'
        checks['checkpoint'] = dict(status='present_not_loaded' if checkpoint.is_file() else 'waiting_for_authorized_weights',
                                    path=str(checkpoint))
        result['status'] = ('ready_for_weight_loading_test' if checkpoint.is_file() else 'ready_without_weights') if all(
            checks[n]['status'] == 'passed' for n in ('inputs', 'environment', 'dependencies', 'gpu_and_versions', 'official_preprocessing')) else 'needs_attention'
    except Exception as exc:
        result.update(error=str(exc), traceback=traceback.format_exc())
    finally:
        save_json(report / 'scripts_sha256.json', {p.name: sha256(p) for p in PACKAGE.iterdir() if p.is_file()})
        save_json(report / 'result.json', result)
        pack_report(report, root / 'return_output')
    print('STATUS: ' + result['status'], flush=True)
    return 1 if result['status'] == 'needs_attention' else 0


if __name__ == '__main__':
    sys.exit(main())
