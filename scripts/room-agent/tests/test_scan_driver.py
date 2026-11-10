"""Offline driver coverage; GPU/model boundaries use explicit fake stage implementations."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

from room_agent import full_scan


@pytest.fixture
def payload(tmp_path):
    archive = tmp_path / 'data/raw/scan.tar'
    archive.parent.mkdir(parents=True)
    with tarfile.open(archive, 'w') as tar:
        for name in ('frame_000.json', 'frame_000.jpg', 'depth_000.png', 'room.usdz'):
            info = tarfile.TarInfo('capture/' + name)
            info.size = 2
            tar.addfile(info, io.BytesIO(b'{}'))
    request = full_scan.capture_input(archive, 'room', tmp_path, 'test123', True)
    return {'request': request, 'stage': 'prepare', 'folder': str(tmp_path / 'backend'),
            'attempt': str(tmp_path / 'attempt'), 'legacy': str(full_scan.full_root())}


def test_real_prepare_cli_validates_frames_without_importing_model_runtime(payload, tmp_path):
    driver = Path(full_scan.__file__).with_name('scan_driver.py')
    assert driver.is_file(), 'isolated full scan driver is missing'
    input_file = tmp_path / 'payload.json'
    input_file.write_text(json.dumps(payload))
    result = subprocess.run([sys.executable, str(driver), str(input_file)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads((Path(payload['attempt']) / 'stage_result.json').read_text())['state']
    assert state['stage_detail']['frames'] == 1
    assert Path(state['capture']).is_symlink()
    assert Path(state['capture']).resolve().is_relative_to(Path(payload['folder']) / 'input/extracted')
    assert not (Path(payload['folder']) / 'checks').exists()
    assert not list(Path(payload['folder']).rglob('pipeline.json'))


def test_prepare_rejects_changed_raw_archive_and_preserves_error(payload):
    from room_agent import scan_driver
    Path(payload['request']['scan_archive']).write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum|SHA256'):
        scan_driver.execute(payload)
    failure = json.loads((Path(payload['attempt']) / 'stage_failure.json').read_text())
    assert failure['stage'] == 'prepare'
    assert not (Path(payload['attempt']) / 'stage_result.json').exists()


def fake_stage(monkeypatch, payload, *, ok=True, audit_error=False):
    from room_agent import scan_driver
    scan_driver.execute(payload)
    payload['stage'] = 'author'
    calls = []
    class Context:
        @property
        def environment(self):
            return {**os.environ, 'DRIVER_CONTEXT_BOUND': 'yes'}
    context = Context()
    def run(actual_context, options):
        assert actual_context is context
        assert os.environ['DRIVER_CONTEXT_BOUND'] == 'yes'
        assert os.environ['HF_HUB_OFFLINE'] == '1'
        assert Path.cwd() == Path(payload['folder']) / 'work'
        calls.append(options)
        return SimpleNamespace(ok=ok, error=None if ok else 'quality rejection', duration_seconds=0,
            to_dict=lambda: {'stage': 'author', 'status': 'completed' if ok else 'failed',
                             'details': {'quality_preserved': True}})
    def audit(actual_context, name):
        assert actual_context is context
        assert name == 'author'
        if audit_error:
            raise RuntimeError('audit rejected output')
        return {'status': 'checked'}
    monkeypatch.setattr(scan_driver, '_application', lambda *args: (context, {'author': SimpleNamespace(run=run)}, audit))
    # Every attempt gets its own immutable result location.
    payload['attempt'] = str(Path(payload['attempt']).with_name('author_attempt'))
    return scan_driver, calls


def test_direct_stage_dispatch_preserves_author_options_and_never_writes_pipeline(payload, monkeypatch):
    driver, calls = fake_stage(monkeypatch, payload)
    result = driver.execute(payload)
    assert len(calls) == 1
    assert calls[0] == dict(provider='codex', profile='open', refine_objects=True, materials=True,
        quality_pass=True, refine_concurrency=1, refine_budget=8, repair_rounds=2, repair_steps=40, repair_seconds=600)
    assert result['state']['stage_detail']['audit'] == {'status': 'checked'}
    assert not list(Path(payload['folder']).rglob('pipeline.json'))
    assert 'DRIVER_CONTEXT_BOUND' not in os.environ


@pytest.mark.parametrize('ok,audit_error,message', [(False, False, 'quality rejection'), (True, True, 'audit rejected')])
def test_failed_direct_result_or_audit_never_becomes_success(payload, monkeypatch, ok, audit_error, message):
    driver, calls = fake_stage(monkeypatch, payload, ok=ok, audit_error=audit_error)
    with pytest.raises(RuntimeError, match=message):
        driver.execute(payload)
    attempt = Path(payload['attempt'])
    assert (attempt / 'stage_failure.json').is_file()
    assert (attempt / 'error.log').is_file()
    assert not (attempt / 'stage_result.json').exists()
    assert (Path(payload['folder']) / 'stage_author.json').is_file()
    assert len(calls) == 1


def test_preflight_keeps_all_legacy_checks_and_runtime_environment(payload, monkeypatch):
    from room_agent import scan_driver
    scan_driver.execute(payload)
    payload.update(stage='preflight', attempt=str(Path(payload['attempt']).with_name('preflight')))
    backend = scan_driver._backend(Path(payload['legacy']), payload['request'])
    commands = []
    monkeypatch.setattr(backend, 'command', lambda label, cmd, cwd, env, folder, seconds:
                        commands.append((label, cmd, dict(env), seconds)))
    monkeypatch.setattr(scan_driver, '_backend', lambda *args: backend)
    import runtime
    monkeypatch.setattr(runtime, 'prepare', lambda env, report: env.update(ROOM_CODEX='/verified/codex'))
    result = scan_driver.execute(payload)
    assert [call[0] for call in commands] == ['app_preflight', 'mcp_preflight', 'gpu_preflight',
        'gpu_kernels', 'blender_paths', 'blender_cpu', 'blender_eevee', 'codex_execution', 'weights', 'model_load']
    assert all(call[2]['ROOM_CODEX'] == '/verified/codex' for call in commands)
    assert result['state']['stage_detail']['checks'] == [call[0] for call in commands]


def test_export_check_requires_verified_files(payload, monkeypatch):
    from room_agent import scan_driver
    scan_driver.execute(payload)
    payload.update(stage='export_check', attempt=str(Path(payload['attempt']).with_name('export_check')))
    backend = scan_driver._backend(Path(payload['legacy']), payload['request'])
    monkeypatch.setattr(backend, 'command', lambda *args: None)
    monkeypatch.setattr(scan_driver, '_backend', lambda *args: backend)
    with pytest.raises(RuntimeError, match='export'):
        scan_driver.execute(payload)


def test_seed_repairs_capture_and_refreshes_cameras_after_direct_stage(payload, monkeypatch):
    driver, calls = fake_stage(monkeypatch, payload)
    payload['stage'] = 'seed'
    backend = driver._backend(Path(payload['legacy']), payload['request'])
    commands = []
    monkeypatch.setattr(backend, 'command', lambda *args: commands.append(args))
    monkeypatch.setattr(driver, '_backend', lambda *args: backend)
    import capture_paths
    repaired = []
    monkeypatch.setattr(capture_paths, 'repair_capture', lambda *args: repaired.append(args) or {'status': 'repaired'})
    # The fake boundary permits checking seed orchestration without GPU imports.
    monkeypatch.setattr(driver, '_application', lambda *args: (
        SimpleNamespace(environment=dict(os.environ)),
        {'seed': SimpleNamespace(run=lambda context, options: SimpleNamespace(ok=True, duration_seconds=0,
            to_dict=lambda: {'stage': 'seed', 'status': 'completed'}))},
        lambda context, stage: {'status': 'checked'}))
    driver.execute(payload)
    assert repaired[0][2] == 1
    assert repaired[0][3] == payload['request']['upstream_scene']
    assert commands[0][0] == 'seed_cameras'
    assert Path(commands[0][1][1]).name == 'refresh_seed.py'


def test_export_check_rejects_failed_reopen_report(payload, monkeypatch):
    from room_agent import scan_driver
    scan_driver.execute(payload)
    payload.update(stage='export_check', attempt=str(Path(payload['attempt']).with_name('export_failed')))
    preview = Path(payload['folder']) / 'scenes' / payload['request']['upstream_scene'] / 'realism_authoring/room_preview'
    preview.mkdir(parents=True)
    for name in ('Room.glb', 'Room.portable.blend'):
        (preview / name).write_bytes(b'export')
    (preview / 'reopen_verification.json').write_text(json.dumps({'passed': False}))
    backend = scan_driver._backend(Path(payload['legacy']), payload['request'])
    monkeypatch.setattr(backend, 'command', lambda *args: None)
    monkeypatch.setattr(scan_driver, '_backend', lambda *args: backend)
    with pytest.raises(RuntimeError, match='verification'):
        scan_driver.execute(payload)


def test_actual_application_loader_calls_pinned_audit_without_constructing_runner(payload, monkeypatch):
    from room_agent import scan_driver
    from types import ModuleType
    def module(name, **values):
        result = ModuleType(name)
        result.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, result)
    class ForbiddenRunner:
        def __init__(self, *args, **kwargs):
            raise AssertionError('PipelineRunner must not be constructed')
    class Context:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
        @property
        def environment(self):
            return dict(os.environ)
    calls = []
    stage = SimpleNamespace(name='author', run=lambda context, options:
        calls.append((context.scan, options)) or SimpleNamespace(ok=True, duration_seconds=0,
            to_dict=lambda: {'stage': 'author', 'status': 'completed'}))
    module('litereality_agent.pipeline.context', RunContext=Context)
    module('litereality_agent.pipeline.stages', STAGES=[stage])
    module('litereality_agent.pipeline.runner', PipelineRunner=ForbiddenRunner)
    module('litereality_agent.pipeline.scene_init', paths=SimpleNamespace(set_scan=lambda scan: None))
    scan_driver.execute(payload)
    payload.update(stage='author', attempt=str(Path(payload['attempt']).with_name('real_loader')))
    result = scan_driver.execute(payload)
    assert calls[0][0] == payload['request']['upstream_scene']
    assert result['state']['stage_detail']['audit'] == {'status': 'checked'}
    assert not list(Path(payload['folder']).rglob('pipeline.json'))


def _running(pid):
    probe = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True)
    return probe.returncode == 0 and bool(probe.stdout.strip()) and not probe.stdout.strip().startswith('Z')


def _detached_driver(tmp_path, mode):
    payload_path = tmp_path / 'empty_payload.json'
    payload_path.write_text('{}')
    pidfile = tmp_path / 'detached.pid'
    child_script = ('import os,signal,time,pathlib\n'
        'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
        f'pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()))\n'
        'time.sleep(60)\n')
    script = (
        'import pathlib,subprocess,sys,time\nfrom room_agent import scan_driver\n'
        'def execute(payload):\n'
        f'    child=subprocess.Popen([sys.executable,"-c",{child_script!r}],start_new_session=True)\n'
        f'    while not pathlib.Path({str(pidfile)!r}).exists(): time.sleep(.01)\n'
        + ('    time.sleep(60)\n' if mode in ('timeout', 'parent_death') else
           '    raise RuntimeError("stage failed")\n' if mode == 'failure' else '    return {}\n') +
        'scan_driver.execute=execute\n'
        f'sys.argv=["scan_driver",{str(payload_path)!r}]\n'
        'scan_driver.main()\n')
    env = dict(os.environ, PYTHONPATH=str(Path(full_scan.__file__).parents[1]))
    return [sys.executable, '-c', script], env, pidfile


@pytest.mark.skipif(os.name != 'posix', reason='POSIX detached process sessions required')
@pytest.mark.parametrize('mode', ['normal', 'failure', 'timeout'])
def test_driver_cleans_detached_children_on_completion_failure_and_actual_timeout(tmp_path, mode):
    import signal
    import time
    from room_agent.processes import run_process
    command, env, pidfile = _detached_driver(tmp_path, mode)
    pid = None
    try:
        if mode == 'normal':
            run_process(command, tmp_path / 'driver.log', seconds=5, env=env)
        else:
            with pytest.raises(TimeoutError if mode == 'timeout' else RuntimeError):
                run_process(command, tmp_path / 'driver.log', seconds=.6 if mode == 'timeout' else 5, env=env)
        assert pidfile.exists(), (tmp_path / 'driver.log').read_text()
        pid = int(pidfile.read_text())
        deadline = time.monotonic() + 2
        while _running(pid) and time.monotonic() < deadline:
            time.sleep(.02)
        assert not _running(pid), 'Detached stage child survived driver ' + mode
    finally:
        if pid is not None and _running(pid):
            os.kill(pid, signal.SIGKILL)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX parent death required')
def test_driver_cleans_detached_children_after_orchestrator_sigkill(tmp_path):
    import signal
    import time
    command, env, pidfile = _detached_driver(tmp_path, 'parent_death')
    from room_agent.storage import room_busy
    resources = tmp_path / 'resources'
    worker_script = ('from room_agent.processes import run_process\nfrom room_agent.storage import room_lock\nimport os\n'
        f'with room_lock({str(resources)!r}, "scene_execution") as lease:\n'
        f'    run_process({command!r},{str(tmp_path / "driver.log")!r},seconds=60,env=dict(os.environ),pass_fds=(lease.fileno(),))\n')
    worker = subprocess.Popen([sys.executable, '-c', worker_script], env=env)
    pid = None
    try:
        deadline = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert pidfile.exists(), 'Driver child failed to start'
        pid = int(pidfile.read_text())
        worker.kill()
        worker.wait(timeout=3)
        assert room_busy(resources, 'scene_execution'), 'Driver released lease before cleanup'
        deadline = time.monotonic() + 5
        while _running(pid) and time.monotonic() < deadline:
            time.sleep(.02)
        assert not _running(pid), 'Detached child survived orchestrator SIGKILL'
        while room_busy(resources, 'scene_execution') and time.monotonic() < deadline:
            time.sleep(.02)
        assert not room_busy(resources, 'scene_execution'), 'Driver leaked resource lease after cleanup'
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=3)
        if pid is not None and _running(pid):
            os.kill(pid, signal.SIGKILL)


def test_main_checks_process_tracking_before_starting_stage(tmp_path, monkeypatch):
    from room_agent import scan_driver
    payload_path = tmp_path / 'tracking.json'
    payload_path.write_text(json.dumps({'stage': 'author'}))
    monkeypatch.setattr(sys, 'argv', ['scan_driver', str(payload_path)])
    calls = []
    monkeypatch.setattr(scan_driver, 'execute', lambda payload: calls.append(payload))
    monkeypatch.setattr(scan_driver, '_process_table', lambda: (_ for _ in ()).throw(RuntimeError('tracking unavailable')))
    previous = __import__('signal').getsignal(__import__('signal').SIGTERM)
    try:
        with pytest.raises(RuntimeError, match='tracking unavailable'):
            scan_driver.main()
        assert not calls, 'Stage started without cancellation ownership'
    finally:
        __import__('signal').signal(__import__('signal').SIGTERM, previous)


def test_author_resume_only_rebuilds_and_checks_saved_scene(monkeypatch,tmp_path):
    from room_agent import scan_driver
    from types import ModuleType
    app=Path(full_scan.full_root())/'app/src'
    monkeypatch.syspath_prepend(str(app))
    acceptance=ModuleType('litereality_agent.pipeline.realism_authoring.acceptance')
    calls=[]
    acceptance.finish=lambda context,options: calls.append((context,options)) or {'accepted':False,'status':'needs_repair','error':'repair incomplete: step budget 40 reached'}
    monkeypatch.setitem(sys.modules,acceptance.__name__,acceptance)
    room=tmp_path/'room';room.mkdir();(room/'Room.py').write_text('# latest edits')
    import importlib.util
    spec=importlib.util.spec_from_file_location('litereality_agent.pipeline.result',app/'litereality_agent/pipeline/result.py')
    result_module=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,spec.name,result_module);spec.loader.exec_module(result_module)
    context=SimpleNamespace(authored_room=room,authoring_root=tmp_path)
    result=scan_driver._resume_author(context,scan_driver.OPTIONS['author'])
    assert len(calls)==1 and calls[0][0] is context
    assert calls[0][1]['repair_steps']==40
    assert not result.ok and 'step budget 40' in result.error
    assert (room/'Room.py').read_text()=='# latest edits'
    acceptance.finish=lambda *args:{'accepted':True,'status':'accepted'}
    assert scan_driver._resume_author(context,scan_driver.OPTIONS['author']).ok


def test_resume_payload_dispatches_acceptance_instead_of_original_author(payload,monkeypatch):
    driver,calls=fake_stage(monkeypatch,payload)
    seen=[]
    def resume(context,options):
        seen.append(options)
        return SimpleNamespace(ok=True,duration_seconds=0,to_dict=lambda:{'stage':'author','status':'completed','details':{'resume_mode':'author_revalidate'}})
    monkeypatch.setattr(driver,'_resume_author',resume)
    payload.update(resumed_from_saved_workspace=True,resume_mode='author_revalidate')
    driver.execute(payload)
    assert len(seen)==1 and calls==[]


def test_draft_export_rejects_source_changes_after_review(payload,monkeypatch):
    from room_agent import scan_driver
    from types import ModuleType
    scan_driver.execute(payload)
    payload.update(stage='draft_export',attempt=str(Path(payload['attempt']).with_name('draft_export')),
                   expected_review={'fingerprint':{'source':'reviewed'}})
    acceptance=ModuleType('litereality_agent.pipeline.realism_authoring.acceptance')
    acceptance.fingerprint=lambda *args:{'source':'changed'}
    monkeypatch.setitem(sys.modules,acceptance.__name__,acceptance)
    backend=scan_driver._backend(Path(payload['legacy']),payload['request'])
    calls=[]
    monkeypatch.setattr(backend,'command',lambda *args:calls.append(args))
    monkeypatch.setattr(scan_driver,'_backend',lambda *args:backend)
    with pytest.raises(ValueError,match='differs from independent review'):scan_driver.execute(payload)
    assert not calls


def test_driver_quality_failure_is_durable_review_not_process_failure(payload,monkeypatch):
    driver,calls=fake_stage(monkeypatch,payload)
    context,stages,audit=driver._application(None,None,None,None)
    stages['author'].run=lambda *args: SimpleNamespace(ok=False,duration_seconds=0,error='needs_repair',
        to_dict=lambda:{'stage':'author','status':'failed','details':{'acceptance':{
            'schema':'support-first/2','accepted':False,'status':'needs_repair','closeout':{'batches':0}}}})
    monkeypatch.setattr(driver,'_application',lambda *args:(context,stages,audit))
    record=driver.execute(payload)
    assert record['state']['author_review']['accepted'] is False
    assert record['state']['stage_detail']['audit']['publication_allowed'] is False
    assert not (Path(payload['attempt'])/'stage_failure.json').exists()
