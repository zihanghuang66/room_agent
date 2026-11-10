"""One isolated full-scan stage under app Python; graph receipts own scheduling.

No LangGraph dependency, runner invocation, stage-cache predicate or pipeline-state
write belongs here. The pinned algorithm and its additional audits remain intact.
"""
import argparse
from contextlib import contextmanager, nullcontext
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid

OPTIONS = {
    'ingest': dict(use_dino=True),
    'reconstruct': dict(chair_qc=True, concurrency=1),
    'seed': dict(capture_mode='link'),
    'author': dict(provider='codex', profile='open', refine_objects=True, materials=True,
                   quality_pass=True, refine_concurrency=1, refine_budget=8,
                   repair_rounds=2, repair_steps=40, repair_seconds=600),
    'publish': dict(compare_frames=6),
}
STAGES = ('prepare', 'preflight', *OPTIONS, 'export_check', 'author_repair', 'draft_export')


def _save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


@contextmanager
def _environment(env):
    previous = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


@contextmanager
def _working_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _backend(legacy, request):
    # Verify before importing executable backend code. The graph also binds this
    # identity into its request/receipts, independently of these subprocess checks.
    for name, expected in request['backend']['files'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe backend identity path')
        path = legacy / relative
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('Backend identity contains a symlink')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Backend checksum mismatch: ' + name)
    sys.path.insert(0, str(legacy))
    return _module(legacy / 'run.py', '_room_full_scan_backend')


def _prepare(backend, request, folder):
    from capture_paths import link_capture
    archive = Path(request['scan_archive'])
    config = dict(scene=request['upstream_scene'], archive_sha256=request['archive_sha256'],
                  app_sha256=request['backend']['app_sha256'], version=request['backend']['version'],
                  codex_model=request['environment']['LR_CODEX_MODEL'],
                  trellis_resolution=request['environment']['ROOM_TRELLIS_RESOLUTION'])
    input_root = folder / 'input/extracted'
    if not input_root.exists():
        temporary = input_root.with_name('extract_' + uuid.uuid4().hex)
        backend.extract_checked(archive, temporary, request['archive_sha256'], max_bytes=32 * 1024**3)
        temporary.rename(input_root)
    source = backend.find_capture(input_root)
    frames = backend.frame_files(source)
    capture = link_capture(folder, source, request['upstream_scene'])
    detail = dict(config, archive=str(archive), sha256=request['archive_sha256'], capture=str(capture),
                  source_capture=str(source), frames=len(frames), raw_frame_pairs_present=True,
                  human_furniture_counts_verified=False,
                  evidence_selection='All input frame pairs checked; reference and review views selected by pipeline.')
    _save(folder / 'input.json', detail)
    return detail


def _application(legacy, request, folder, capture):
    from litereality_agent.pipeline.context import RunContext
    from litereality_agent.pipeline.stages import STAGES as implementations
    # stage.py imports the historical runner class, but only its audit function
    # is used. Its __main__ block (and all runner scheduling) is never executed.
    audit = _module(legacy / 'stage.py', '_room_full_scan_audit').audit
    context = RunContext(scan=request['upstream_scene'], capture_dir=Path(capture),
                         scene_dir=folder / 'scenes' / request['upstream_scene'], output_root=folder / 'scenes')
    return context, {stage.name: stage for stage in implementations}, audit


def _preflight(backend, legacy, folder, env):
    from runtime import prepare
    prepare(env, folder / 'checks/codex_runtime.json')
    stop = folder / 'imagegen/STOP.json'
    if stop.exists():
        stop.rename(stop.with_name('STOP_previous_' + uuid.uuid4().hex + '.json'))
    commands = [
        ('app_preflight', [sys.executable, legacy / 'preflight.py', 'app'], 180),
        ('mcp_preflight', [sys.executable, legacy / 'preflight.py', 'mcp'], 180),
        ('gpu_preflight', [env['LR_DINO_PYTHON'], legacy / 'preflight.py', 'gpu'], 180),
        ('gpu_kernels', [env['LR_DINO_PYTHON'], legacy / 'check_extensions.py'], 600),
        ('blender_paths', [sys.executable, legacy / 'blender_paths.py'], 90),
        ('blender_cpu', [env['LITEREALITY_BLENDER'], '-b', '-t', '4', '--python-exit-code', '1',
            '--python', legacy / 'blender_smoke.py', '--', '--mode', 'cpu', '--output', folder / 'checks/blender'], 600),
        ('blender_eevee', [env['LITEREALITY_BLENDER'], '-b', '-t', '4', '--python-exit-code', '1',
            '--python', legacy / 'blender_smoke.py', '--', '--mode', 'eevee', '--output', folder / 'checks/blender'], 600),
        ('codex_execution', [sys.executable, legacy / 'execution_smoke.py'], 540),
        ('weights', [env['LR_DINO_PYTHON'], legacy / 'fetch_weights.py'], 21600),
        ('model_load', [env['LR_DINO_PYTHON'], legacy / 'preflight.py', 'models'], 1800),
    ]
    for label, command, seconds in commands:
        backend.command(label, command, Path(env['LR_REPO_ROOT']), env, folder, seconds)
    # Runtime resolution mutates these values; persist them for later isolated stages.
    _save(folder / 'checks/runtime_environment.json', {key: env[key] for key in ('ROOM_CODEX', 'PATH')})
    return {'checks': [label for label, _, _ in commands]}


def _resume_author(context, options):
    """Rebuild the last saved code, then apply the same independent acceptance gate."""
    from litereality_agent.pipeline.realism_authoring.acceptance import finish
    from litereality_agent.pipeline.result import StageResult, StageStatus
    source = context.authored_room / 'Room.py'
    if not source.is_file():
        raise ValueError('Saved Author source is missing; refusing to regenerate from Seed')
    report = finish(context, dict(options))
    accepted = report.get('accepted') is True
    return StageResult('author', StageStatus.COMPLETED if accepted else StageStatus.FAILED,
        artifacts={'room': str(context.authored_room),
                   'acceptance': str(context.authoring_root/'room_preview/author_acceptance.json')},
        error=None if accepted else str(report.get('status','needs_repair'))+': '+str(report.get('error') or 'support/visual acceptance failed; saved source retained'),
        details={'resume_mode':'author_revalidate','acceptance':report})


def execute(payload):
    request, stage = payload['request'], payload['stage']
    folder, attempt, legacy = (Path(payload[key]).absolute() for key in ('folder', 'attempt', 'legacy'))
    attempt.mkdir(parents=True, exist_ok=True)
    folder.mkdir(parents=True, exist_ok=True)
    backend = None
    started = time.monotonic()
    try:
        if stage not in STAGES:
            raise ValueError('Unknown full scan stage: ' + stage)
        backend = _backend(legacy, request)
        if backend.digest(request['scan_archive']) != request['archive_sha256']:
            raise ValueError('Raw archive SHA256 checksum mismatch')
        # Sanitize inherited algorithm settings just as the legacy adapter does.
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('ROOM_', 'LR_', 'LITEREALITY_', 'HARNESS_'))}
        for key in ('CUDA_VISIBLE_DEVICES', 'PYTHONPATH', 'PYTHONHOME'):
            env.pop(key, None)
        env.update(request['environment'])
        outputs = []
        author_review = None
        technical_export_verified = None
        with _environment(env):
            app = backend.ensure_app(request['backend']['app_sha256'])
            if stage == 'prepare':
                detail = _prepare(backend, request, folder)
                capture = detail['capture']
                outputs.append(folder / 'input.json')
            else:
                input_data = json.loads((folder / 'input.json').read_text())
                capture = input_data['capture']
                if (input_data['scene'] != request['upstream_scene'] or
                        input_data['archive_sha256'] != request['archive_sha256']):
                    raise ValueError('Prepared scan identity mismatch')
                env = backend.setup_environment(Path(request['project']), folder, app)
                scene = folder / 'scenes' / request['upstream_scene']
                env.update(LITEREALITY_SCAN=request['upstream_scene'], LR_SCENE=str(scene),
                           LR_AUTHORING=str(scene / 'realism_authoring'))
                env.setdefault('ROOM_MAX_IMAGE_CALLS', '64')
                if stage != 'preflight':
                    resolved = folder / 'checks/runtime_environment.json'
                    if resolved.is_file():
                        env.update(json.loads(resolved.read_text()))
                    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
                # Apply paths before any LiteReality import; modules capture globals.
                sys.path.insert(0, str(app / 'src'))
                with _environment(env), _working_directory(env['LR_REPO_ROOT']):
                    if stage == 'preflight':
                        detail = _preflight(backend, legacy, folder, env)
                        outputs.append(folder / 'checks')
                    elif stage in ('export_check','draft_export'):
                        preview = scene / 'realism_authoring/room_preview'
                        if stage=='draft_export':
                            from litereality_agent.pipeline.realism_authoring.acceptance import fingerprint
                            expected=(payload.get('expected_review') or {}).get('fingerprint')
                            if not expected or fingerprint(scene/'realism_authoring/room',preview)!=expected:
                                raise ValueError('Draft export source/build differs from independent review')
                        backend.command('reopen_exports', [env['LITEREALITY_BLENDER'], '-b', '-t', '4',
                            '--python-exit-code', '1', '--python', legacy / 'verify_export.py', '--', preview],
                            Path(env['LR_REPO_ROOT']), env, folder, 900)
                        exports = [preview / name for name in ('Room.glb', 'Room.portable.blend', 'reopen_verification.json')]
                        if not all(path.is_file() for path in exports):
                            raise RuntimeError('Verified final export files are missing')
                        verification = json.loads(exports[2].read_text())
                        if verification.get('passed') is not True:
                            raise RuntimeError('Final export reopen verification failed')
                        if stage=='draft_export' and fingerprint(scene/'realism_authoring/room',preview)!=expected:
                            raise ValueError('Draft source/build changed during export verification')
                        technical_export_verified = True
                        detail = {'full_run_completed': stage=='export_check', 'human_visual_reviewed': False,
                                  'final_glb': str(exports[0]), 'final_blend': str(exports[1]),
                                  'verification': verification}
                        outputs.extend(exports)
                    else:
                        context, implementations, audit = _application(legacy, request, folder, capture)
                        actual_stage='author' if stage=='author_repair' else stage
                        implementation = implementations[actual_stage]
                        try:
                            from . import author_closeout
                        except ImportError:
                            import author_closeout
                        # Application contexts have authoring_root; lightweight offline
                        # dispatch fixtures deliberately provide no application runtime.
                        adapted=stage in ('author','author_repair','publish') and hasattr(context,'authoring_root')
                        options=dict(OPTIONS[actual_stage])
                        if adapted:options['_operation_id']=payload.get('operation_id')
                        with _environment(context.environment), (author_closeout.install() if adapted else nullcontext()):
                            if stage=='author_repair':
                                from litereality_agent.pipeline.result import StageResult, StageStatus
                                assessment=author_closeout.run(context,options,repair=True)
                                result=StageResult('author',StageStatus.COMPLETED if assessment['accepted'] else StageStatus.FAILED,
                                    error=None if assessment['accepted'] else 'needs_repair',details={'acceptance':assessment})
                            elif stage=='author' and payload.get('resumed_from_saved_workspace') is True and payload.get('resume_mode')=='author_revalidate':
                                result = _resume_author(context, options)
                            elif stage=='publish' and adapted:
                                result = author_closeout.run_publish(context,options,implementation)
                            else:
                                result = implementation.run(context, options)
                            result.duration_seconds = time.monotonic() - started
                            direct = result.to_dict()
                            _save(folder / ('stage_' + stage + '.json'), direct)
                            _save(attempt / 'direct_result.json', direct)
                            candidate=direct.get('details',{}).get('acceptance')
                            if isinstance(candidate,dict) and candidate.get('schema')=='support-first/2' and candidate.get('status') in ('accepted','needs_repair'):
                                author_review=candidate
                            if not result.ok and author_review is None:
                                raise RuntimeError(str(result.error))
                            report = audit(context, actual_stage) if result.ok else {'status':'quality_not_passed','publication_allowed':False}
                        _save(folder / ('audit_' + stage + '.json'), report)
                        detail = {'result': direct, 'audit': report}
                        outputs.extend([folder / ('stage_' + stage + '.json'), folder / ('audit_' + stage + '.json')])
                        if stage == 'ingest':
                            backend.scene_inventory(folder, request['upstream_scene'])
                            outputs.append(folder / 'scene_inventory.json')
                        if stage == 'seed':
                            from capture_paths import repair_capture
                            repair = repair_capture(folder, capture, input_data['frames'], request['upstream_scene'])
                            _save(folder / 'capture_repair.json', repair)
                            backend.command('seed_cameras', [sys.executable, legacy / 'refresh_seed.py'],
                                            Path(env['LR_REPO_ROOT']), env, folder, 1200)
                            outputs.extend([folder / 'capture_repair.json', folder / 'capture_seed_refresh.json'])
        state = {'capture': str(capture), 'scene': str(folder / 'scenes' / request['upstream_scene']),
                 'stage_detail': detail}
        if author_review is not None:state['author_review']=author_review
        if technical_export_verified is not None:state['technical_export_verified']=technical_export_verified
        record = {'state': state, 'outputs': list(map(str, outputs)), 'stage': stage,
                  'duration_seconds': time.monotonic() - started}
        _save(attempt / 'stage_result.json', record)
        print('STAGE_' + stage.upper() + '_PASS', flush=True)
        return record
    except BaseException as exc:
        redact = backend.redact if backend else str
        failure = {'stage': stage, 'type': type(exc).__name__, 'message': redact(str(exc))[-8000:],
                   'duration_seconds': time.monotonic() - started}
        _save(attempt / 'stage_failure.json', failure)
        (attempt / 'error.log').write_text(redact(traceback.format_exc()))
        raise


def _process_table():
    """Parent and birth identity only; never collect command lines or environments."""
    proc = Path('/proc')
    if proc.is_dir():
        table = {}
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
                if fields[0] != 'Z':
                    table[int(entry.name)] = (int(fields[1]), fields[19])
            except (OSError, IndexError, ValueError):
                continue
        return table
    # Local macOS verification. Production Linux uses kernel start ticks above.
    with subprocess.Popen(['/bin/ps', '-axo', 'pid=,ppid=,stat=,lstart='],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as query:
        try:
            output, _ = query.communicate(timeout=2)
        except BaseException:
            query.kill()
            query.wait()
            raise
        if query.returncode:
            raise RuntimeError('Cannot inspect stage child processes')
    table = {}
    for line in output.splitlines():
        fields = line.split(None, 3)
        if len(fields) == 4 and not fields[2].startswith('Z'):
            table[int(fields[0])] = (int(fields[1]), fields[3])
    table.pop(query.pid, None)
    return table


class _ProcessTree:
    """Retain observed descendants across reparenting, with PID reuse protection."""
    def __init__(self):
        self.root = os.getpid()
        self.children = {}
        self.lock = threading.RLock()
        self.stopped = threading.Event()

    def capture(self):
        table = _process_table()
        with self.lock:
            alive = {pid: born for pid, born in self.children.items()
                     if pid in table and table[pid][1] == born}
            found = {self.root, *alive}
            while True:
                extra = {pid for pid, (parent, _) in table.items() if parent in found} - found
                if not extra:
                    break
                found.update(extra)
            self.children.update({pid: table[pid][1] for pid in found if pid != self.root and pid in table})
            return {pid: self.children[pid] for pid in found if pid != self.root and pid in table}

    def cleanup(self):
        # Finish while the driver still holds its inherited resource leases.
        self.stopped.set()
        for sig, grace in ((signal.SIGTERM, .4), (signal.SIGKILL, 1.0)):
            children = self.capture()
            for pid, born in children.items():
                current = _process_table().get(pid)
                if current is not None and current[1] == born:
                    try:
                        os.kill(pid, sig)
                    except ProcessLookupError:
                        pass
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline:
                if not self.capture():
                    return
                time.sleep(.02)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('payload', type=Path)
    args = parser.parse_args()
    payload = json.loads(args.payload.read_text())
    if payload.get('stage') == 'prepare':
        # Offline extraction is stdlib-only and launches no subprocesses.
        execute(payload)
        return
    parent = os.getppid()
    tree = _ProcessTree()
    tree.capture()  # Refuse to start a model stage without process ownership.
    def watchdog():
        while not tree.stopped.wait(.1):
            tree.capture()
            if os.getppid() != parent:
                tree.cleanup()
                os.kill(os.getpid(), signal.SIGKILL)
    monitor = threading.Thread(target=watchdog, daemon=True)
    monitor.start()
    def stop(sig, frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        # Clean before exception unwinding: a pinned stage may wait for its child
        # in finally, and that child can be in a different process session.
        tree.cleanup()
        raise KeyboardInterrupt('Stage interrupted by signal ' + str(sig))
    signal.signal(signal.SIGTERM, stop)
    try:
        execute(payload)
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        tree.cleanup()
        monitor.join(timeout=2)


if __name__ == '__main__':
    main()
