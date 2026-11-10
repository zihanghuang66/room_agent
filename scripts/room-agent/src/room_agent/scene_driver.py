"""Runs under existing app Python, never imports LangGraph or installs dependencies."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
import traceback
import shutil


def copy_repair_evidence(state, rounddir):
    """Legacy repair is read-scoped to rounddir; put the actual evidence inside it."""
    rounddir = Path(rounddir)
    if state.get('geometry_report'):
        shutil.copyfile(state['geometry_report'], rounddir / 'validation.json')
    if state.get('selection'):
        (rounddir / 'evidence_selection.json').write_text(json.dumps(state['selection'], indent=2))
    for p in map(Path, state.get('pairs', [])):
        target = rounddir / 'compare/pairs' / p.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)


def main():
    parent = os.getppid()
    parser = argparse.ArgumentParser()
    parser.add_argument('payload', type=Path)
    args = parser.parse_args()
    payload = json.loads(args.payload.read_text())
    request, state = payload['request'], payload['state']
    stage, attempt, legacy = payload['stage'], Path(payload['attempt']), Path(payload['legacy'])
    sys.path.insert(0, str(legacy))
    from full_support import setup_environment, command, descendants, signal_descendants
    from batch_io import save, copy_source, source_hashes
    from batch_core import geometry_quality, parse_json, validate_review, review_passes, choose_issues
    env = setup_environment(Path(request['project']), attempt, legacy / 'app')
    env.update(LITEREALITY_SCAN=request['scene'], LR_SCENE=str(attempt / 'scenes' / request['scene']))
    os.environ.update(env)
    sys.path.insert(0, str(legacy / 'app/src'))
    from worker import module, source_build_stamp, Engine
    from review_targets import evidence_selection, review_prompt
    from litereality_agent.agent.providers import SessionSpec
    from agent_session import recorded_session

    # If the orchestrator is killed, clean only this driver and its descendants (including detached helpers).
    def watchdog():
        while True:
            time.sleep(2)
            if os.getppid() != parent:
                children = descendants(os.getpid())
                children.pop(os.getpid(), None)
                signal_descendants(children, signal.SIGKILL)
                os.killpg(os.getpgrp(), signal.SIGKILL)
    threading.Thread(target=watchdog, daemon=True).start()
    def stop(sig, frame):
        raise KeyboardInterrupt('Stage interrupted by signal ' + str(sig))
    signal.signal(signal.SIGTERM, stop)

    capture = Path(request['capture'])
    number = state.get('round', 0)
    result, outputs = {}, []
    if request.get('operation') == 'scene_edit' and stage in ('inspect_current', 'edit', 'evidence', 'review', 'finalize'):
        from edit_driver import run_stage
        result, outputs = run_stage(stage, request, state, attempt)
    elif stage == 'preflight':
        from runtime import prepare
        prepare(env, attempt / 'runtime.json')
        import importlib
        for name in ('numpy', 'PIL', 'trimesh', 'mcp', 'fcl', 'rtree', 'scipy', 'networkx'):
            importlib.import_module(name)
        from trimesh.collision import CollisionManager
        CollisionManager()  # Also validates the native FCL library, before a costly build.
        if not shutil.which('ffmpeg', path=env.get('PATH')):
            raise RuntimeError('ffmpeg is required for paired-render export')
        command('blender_version', [env['LITEREALITY_BLENDER'], '--version'], attempt, env, attempt, 30)
        # Real authenticated execution, not merely `codex --version`.
        spec = SessionSpec(prompt='Reply with exactly ROOM_AGENT_READY. Do not edit files or invoke other models.',
            cwd=attempt, read_only=True, setting_sources=(), file_tools=('Read',), timeout_seconds=90, step_budget=4)
        session = asyncio.run(recorded_session(spec, attempt / 'codex_probe', 'quality'))
        if session['termination'] != 'completed' or 'ROOM_AGENT_READY' not in session['text']:
            raise RuntimeError('Codex authenticated probe failed: ' + session['reason'])
        outputs = [attempt / 'runtime.json', attempt / 'codex_probe/result.json']
    elif stage == 'build':
        rounddir = attempt / 'round'
        room, preview = rounddir / 'room', rounddir / 'room_preview'
        copy_source(Path(state['active_source']), room)
        for path in room.rglob('*.py'):
            compile(path.read_bytes(), str(path), 'exec')
        module('build', 'build_saved', ['--room', room, '--out', preview], attempt, 1600)
        stamp = source_build_stamp(room, preview)
        save(rounddir / 'build_fingerprint.json', stamp)
        result = {'round_dir': str(rounddir), 'room': str(room), 'preview': str(preview), 'build_stamp': stamp}
        # Paired renders load preview/Object/*, so bind those assets as well as exported Room.glb.
        outputs = [room, preview, rounddir / 'build_fingerprint.json']
    elif stage == 'geometry':
        from litereality_agent.pipeline.realism_authoring.acceptance import preserved_instances
        room, preview, rounddir = map(Path, (state['room'], state['preview'], state['round_dir']))
        validation = attempt / 'validation.json'
        try:
            module('geometry', 'litereality_agent.pipeline.room_qc.validate',
                   ['--room', room, '--preview', preview, '--json', validation], attempt, 600)
        except RuntimeError:
            if not validation.is_file():
                raise
        geometry = json.loads(validation.read_text())
        if geometry_quality(geometry) == 'check_failed':
            raise RuntimeError('Geometry check unavailable/incomplete: ' + json.dumps(geometry))
        preserved = preserved_instances(Path(state['seed']), room)
        command('reopen', [env['LITEREALITY_BLENDER'], '-b', '--python-exit-code', '1',
                          '--python', legacy / 'verify_export.py', '--', preview], legacy, env, attempt, 600)
        issues = [{'priority': 'support', 'object_ids': [x['id']] if x.get('id') else [],
                   'defect': json.dumps(x, ensure_ascii=False)} for x in geometry.get('failing', [])]
        if not preserved:
            issues.append({'priority': 'support', 'object_ids': [], 'defect': 'Original manifest identities or source meshes changed.'})
        ok = geometry.get('pass') is True and preserved
        if not ok and not issues:
            raise RuntimeError('Geometry failed without actionable findings')
        result = {'geometry_pass': ok, 'quality_status': 'unreviewed' if ok else 'not_passed', 'issues': issues,
                  'geometry_report': str(validation)}
        save(rounddir / 'assessment.json', result)
        outputs = [validation, preview / 'Room.portable.blend', preview / 'reopen_verification.json']
    elif stage == 'evidence':
        objects = json.loads((Path(state['preview']) / 'room_layout.json').read_text())['objects']
        selection = evidence_selection(capture, attempt, Path(state['room']), objects)
        result = {'selection': selection}
        outputs = [attempt / 'evidence/selection.json', attempt / 'evidence/targets.json']
    elif stage == 'render':
        room, preview = Path(state['room']), Path(state['preview'])
        frames = ','.join(map(str, state['selection']['frames'])) + ','
        compare = attempt / 'compare'
        module('paired_render', 'litereality_agent.room_ops.rendering.room_render.render_vs_capture',
               ['--scan', capture, '--room', room, '--assets', preview, '--out', compare,
                '--frames', frames, '--res-div', '1'], attempt, 3400)
        pairs = sorted((compare / 'pairs').glob('pair_*.png'))
        if {int(p.stem.split('_')[1]) for p in pairs} != set(state['selection']['frames']):
            raise RuntimeError('Selected capture pairs missing')
        if source_build_stamp(room, preview) != state['build_stamp']:
            raise RuntimeError('Rendering changed source/build; cannot review stale revision')
        result = {'pairs': list(map(str, pairs)), 'compare': str(compare)}
        outputs = pairs
    elif stage == 'review':
        room, preview = Path(state['room']), Path(state['preview'])
        objects = json.loads((preview / 'room_layout.json').read_text())['objects']
        objects = list(objects.values()) if isinstance(objects, dict) else objects
        ids = [x['id'] for x in objects]
        prompt = review_prompt(objects, state['selection'])
        spec = SessionSpec(prompt=prompt, cwd=attempt, read_roots=(capture,), image_paths=tuple(map(Path, state['pairs'])),
            read_only=True, setting_sources=(), file_tools=('Read',), timeout_seconds=600, step_budget=60)
        session = asyncio.run(recorded_session(spec, attempt / 'review_session', 'quality'))
        if session['termination'] != 'completed':
            raise RuntimeError('Independent review incomplete: ' + session['termination'] + ': ' + session['reason'])
        review = validate_review(parse_json(session['text']), ids)
        if source_build_stamp(room, preview) != state['build_stamp']:
            raise RuntimeError('Review changed source/build; verdict invalid')
        result = {'quality_status': 'passed' if review_passes(review, ids) else 'not_passed',
                  'score': review['score'], 'issues': review['issues'], 'visual_review': str(attempt / 'visual_review.json'),
                  'review_stream_error_count': session.get('stream_error_count', 0)}
        save(attempt / 'visual_review.json', review)
        save(Path(state['round_dir']) / 'assessment.json', {**result, 'compare': state['compare'], 'geometry_report': state['geometry_report']})
        outputs = [attempt / 'visual_review.json']
    elif stage == 'repair':
        copy_repair_evidence(state, state['round_dir'])
        engine = Engine(attempt, capture, request['scene'], request['repair_seconds'])
        engine.last_round = Path(state['round_dir'])
        result_session = engine.repair(number + 1, choose_issues(state['issues']))
        result = {'active_source': str(engine.active), 'repair_termination': result_session['termination']}
        outputs = [engine.active]
    else:
        raise ValueError('Unknown stage ' + stage)
    save(attempt / 'stage_result.json', {'state': result, 'outputs': list(map(str, outputs))})


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        traceback.print_exc()
        raise
