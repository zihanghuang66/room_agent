"""Repair and resume the exact completed school run, keeping imagegen and models cached."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time
import traceback
import uuid

from capture_paths import repair_capture

HERE = Path(__file__).resolve().parent
RUN_NAME = 'room_002_full_v0.1.0_9a5b63b1e097'
APP_SHA = '29d3b7ccbf7b3f673b327b35883cb2fd70350bcf4480975b9a8005587b776aa5'
SCAN_SHA = '914f5381f0c7a3a513c20df1c27b67adbcf93ad89e252056ff6bedc9d0ed6574'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--project-root', type=Path, default=Path('/yinxin/projects/room'))
    p.add_argument('--repair-only', action='store_true', help='Repair paths and test evidence/cameras, without starting author')
    a = p.parse_args()
    project = a.project_root.resolve()
    package = project / 'scripts/room-litereality-full-v0.1.0'
    folder = project / 'return_output/artifacts' / RUN_NAME
    # The completed run belongs to this exact package; do not start a new hashed output directory.
    sys.path.insert(0, str(package))
    spec = importlib.util.spec_from_file_location('room_original_runner', package / 'run.py')
    runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
    lock = (folder / '.run.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('The original run is still active. Do not start another copy.', flush=True); return 2
    stamp = 'resume_' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '_' + uuid.uuid4().hex[:8]
    result = dict(status='failed', stage='resume_validation', invocation=stamp,
                  folder=str(folder), full_run_completed=False, visual_fidelity_reviewed=False,
                  image_provider='codex_builtin', image_tool_provenance_may_be_indirect=True)
    try:
        prior = folder / 'result.json'
        if prior.exists():
            backup = folder / ('result_before_' + stamp + '.json'); backup.write_bytes(prior.read_bytes())
        inputs = json.loads((folder / 'input.json').read_text())
        if inputs['sha256'] != SCAN_SHA or inputs['app_sha256'] != APP_SHA or inputs['frames'] != 42:
            raise ValueError('Input/package identity differs from the audited school run')
        app = runner.ensure_app(APP_SHA)
        state = json.loads((folder / 'scenes/room_002/.litereality/pipeline.json').read_text())
        for stage in ('ingest', 'reconstruct', 'seed'):
            if state['stages'].get(stage, {}).get('status') not in ('completed', 'reused'):
                raise RuntimeError('Cannot skip incomplete prerequisite: ' + stage)
        # Only input.json's recorded in-run capture is allowed, never a guessed folder.
        capture = Path(inputs['capture'])
        if runner.digest(Path(inputs['archive'])) != SCAN_SHA:
            raise ValueError('Raw archive changed since the original run')
        result['stage'] = 'capture_paths'
        repair = repair_capture(folder, capture, inputs['frames'])
        runner.save(folder / 'capture_repair.json', repair)
        env = runner.setup_environment(project, folder, app)
        env.update(LITEREALITY_SCAN='room_002', LR_SCENE=str(folder / 'scenes/room_002'),
                   LR_AUTHORING=str(folder / 'scenes/room_002/realism_authoring'),
                   HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        print('REUSING: 11 reference images, 11 object assets, downloaded weights. No ingest/reconstruct rerun.', flush=True)
        commands = [
            ('resume_app', [sys.executable, package / 'preflight.py', 'app'], 180),
            ('resume_mcp', [sys.executable, package / 'preflight.py', 'mcp'], 180),
            ('resume_eevee', [env['LITEREALITY_BLENDER'], '-b', '-t', '4', '--python-exit-code', '1',
                '--python', package / 'blender_smoke.py', '--', '--mode', 'eevee', '--output', folder / 'checks/blender'], 600),
            ('capture_evidence', [sys.executable, '-m', 'litereality_agent.pipeline.realism_authoring.author.evidence',
                '--scene', folder / 'scenes/room_002'], 1800),
            ('seed_cameras', [sys.executable, HERE / 'refresh_seed.py'], 1200),
        ]
        for label, cmd, timeout in commands:
            result['stage'] = label
            runner.command(label, cmd, Path(env['LR_REPO_ROOT']), env, folder, timeout)
        if a.repair_only:
            result.update(status='capture_repaired_author_not_resumed', full_run_completed=False)
            return 0
        for stage in ('author', 'publish'):
            result['stage'] = stage; runner.save(folder / 'result.json', result)
            runner.command(stage, [sys.executable, package / 'stage.py', stage, folder / 'input/room_002', folder / 'scenes'],
                           Path(env['LR_REPO_ROOT']), env, folder, 43200)
        result['stage'] = 'reopen_exports'
        preview = folder / 'scenes/room_002/realism_authoring/room_preview'
        runner.command('reopen_exports', [env['LITEREALITY_BLENDER'], '-b', '-t', '4', '--python-exit-code', '1',
                       '--python', package / 'verify_export.py', '--', preview], Path(env['LR_REPO_ROOT']), env, folder, 900)
        result.update(status='pipeline_completed_quality_review_pending', full_run_completed=True,
                      final_glb=str(preview / 'Room.glb'), final_blend=str(preview / 'Room.portable.blend'))
        return 0
    except BaseException:
        error = runner.redact(traceback.format_exc())
        (folder / ('error_' + stamp + '.log')).write_text(error)
        result['error'] = error[-7000:]
        print('FAILED at ' + result['stage'] + '\n' + error[-7000:], flush=True)
        return 1
    finally:
        runner.save(folder / 'result.json', result)
        try:
            runner.pack(folder, project / 'return_output', stamp)
        finally:
            lock.close()


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt('SIGTERM')))
    raise SystemExit(main())
