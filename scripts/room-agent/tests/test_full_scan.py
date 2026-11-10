import io
import json
from pathlib import Path
import tarfile

import pytest

from room_agent.service import Service
from room_agent.storage import atomic_json, read_json, room_lock


@pytest.fixture
def raw_scan(tmp_path):
    archive = tmp_path / 'data/raw/scan.tar'
    archive.parent.mkdir(parents=True)
    with tarfile.open(archive, 'w') as tar:
        for name in ('capture/frame_000.json', 'capture/frame_000.jpg', 'capture/depth_000.png', 'capture/room.usdz'):
            data = b'{}'
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return archive


class FakeLegacy:
    def __init__(self):
        self.calls = []
        self.fail = False

    def __call__(self, command, log, *, seconds, env):
        from room_agent.full_scan import upstream_folder, backend_config
        self.calls.append(command)
        request = read_json(Path(log).parent.parent / 'input.json')
        complete = not self.fail and not request['prepare_only']
        folder = upstream_folder(request)
        folder.mkdir(parents=True, exist_ok=True)
        atomic_json(folder / 'input.json', backend_config(request))
        atomic_json(folder / 'result.json', {
            'scene': request['upstream_scene'], 'stage': 'author' if self.fail else 'reopen_exports' if complete else 'offline_input_validation',
            'status': 'failed' if self.fail else 'pipeline_completed_human_review_pending' if complete else 'prepared_only',
            'full_run_completed': complete,
            'error': 'author failed' if self.fail else None})
        atomic_json(folder / 'scenes' / request['upstream_scene'] / '.litereality/pipeline.json', {
            'stages': {name: {'status': 'completed' if complete or name == 'ingest' else 'failed' if name == 'author' and self.fail else 'pending'}
                       for name in ('ingest', 'reconstruct', 'seed', 'author', 'publish')}})
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        Path(log).write_text('legacy output\n')
        if self.fail:
            preview = folder / 'candidate_preview'
            preview.mkdir(exist_ok=True)
            (preview / 'Room.glb').write_bytes(b'candidate')
            raise RuntimeError('author failed')


class LegacyService(Service):
    def create_full(self, *args, **kwargs):
        return super().create_full(*args, **kwargs, workflow='legacy')


def make_service(tmp_path, runner):
    return LegacyService(tmp_path / 'return_output/agent_state', full_process_runner=runner)


def test_full_prepare_only_executes_backend_and_is_not_quality_pass(tmp_path, raw_scan):
    runner = FakeLegacy()
    service = make_service(tmp_path, runner)
    run = service.create_full(raw_scan, 'room', tmp_path, prepare_only=True)
    result = service.execute(run)
    assert result['execution_status'] == 'completed'
    assert result['quality_status'] == 'unreviewed'
    assert result['completion_reason'] == 'prepared_only'
    assert '--prepare-only' in runner.calls[0]
    assert '--expected-sha' in runner.calls[0]
    assert service.execute(run)['completion_reason'] == 'prepared_only'
    assert len(runner.calls) == 1
    assert service.events(run)[-1]['kind'] == 'operation_finished'


def test_same_scan_creates_isolated_legacy_folders(tmp_path, raw_scan):
    from room_agent.full_scan import upstream_folder
    service = make_service(tmp_path, FakeLegacy())
    runs = [service.create_full(raw_scan, 'r' * 80, tmp_path) for _ in range(2)]
    requests = [service._load(run)[0] for run in runs]
    assert len({upstream_folder(request) for request in requests}) == 2
    assert all(len(request['upstream_scene']) <= 80 for request in requests)
    assert all(request['scene'] == 'r' * 80 for request in requests)


def test_failed_author_keeps_quality_unreviewed_and_resumes_same_folder(tmp_path, raw_scan):
    runner = FakeLegacy()
    runner.fail = True
    service = make_service(tmp_path, runner)
    run = service.create_full(raw_scan, 'room', tmp_path)
    with pytest.raises(RuntimeError, match='author failed'):
        service.execute(run)
    failed = service.show(run)
    assert failed['execution_status'] == 'failed'
    assert failed['quality_status'] != 'passed'
    assert failed['error']['node'] == 'author'
    assert failed['exports'][0]['candidate'] is True
    assert failed['stages'][0]['status'] == 'completed'
    runner.fail = False
    restarted = make_service(tmp_path, runner)
    completed = restarted.execute(run)
    assert completed['execution_status'] == 'completed'
    assert completed['quality_status'] == 'human_review_pending'
    assert '--resume-from' in runner.calls[-1]
    assert runner.calls[-1][runner.calls[-1].index('--resume-from') + 1] == failed['source_run']


def test_input_mutation_blocks_even_completed_run(tmp_path, raw_scan):
    runner = FakeLegacy()
    service = make_service(tmp_path, runner)
    run = service.create_full(raw_scan, 'room', tmp_path, prepare_only=True)
    service.execute(run)
    raw_scan.write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum|archive'):
        service.execute(run)
    assert len(runner.calls) == 1


def test_scan_path_and_tar_validation(tmp_path, raw_scan):
    service = make_service(tmp_path, FakeLegacy())
    outside = tmp_path / 'outside.tar'
    outside.write_bytes(raw_scan.read_bytes())
    with pytest.raises(ValueError, match='data/raw'):
        service.create_full(outside, 'room', tmp_path)
    link = raw_scan.with_name('link.tar')
    link.symlink_to(raw_scan)
    with pytest.raises(ValueError, match='symlink'):
        service.create_full(link, 'room', tmp_path)
    raw_scan.write_bytes(b'not a tar')
    with pytest.raises(ValueError, match='tar'):
        service.create_full(raw_scan, 'room', tmp_path)


def test_backend_mutation_blocks_launch(tmp_path, raw_scan, monkeypatch):
    import room_agent.full_scan as adapter
    runner = FakeLegacy()
    service = make_service(tmp_path, runner)
    run = service.create_full(raw_scan, 'room', tmp_path)
    monkeypatch.setattr(adapter, 'backend_identity', lambda: {'changed': True})
    with pytest.raises(ValueError, match='backend'):
        service.execute(run)
    assert not runner.calls


def test_full_scan_shares_scene_resource_lock(tmp_path, raw_scan):
    runner = FakeLegacy()
    service = make_service(tmp_path, runner)
    run = service.create_full(raw_scan, 'room', tmp_path)
    with room_lock(service.root / 'resources', 'scene_execution'):
        with pytest.raises(RuntimeError, match='busy'):
            service.execute(run)
    assert not runner.calls


def test_return_package_only_contains_local_safe_outputs(tmp_path, raw_scan):
    runner = FakeLegacy()
    runner.fail = True
    service = make_service(tmp_path, runner)
    run = service.create_full(raw_scan, 'room', tmp_path)
    with pytest.raises(RuntimeError):
        service.execute(run)
    package = service.package_return(run)
    with tarfile.open(package) as tar:
        names = tar.getnames()
        assert any(name.endswith('/Room.glb') for name in names)
        assert any(name.endswith('/logs/full_scan.log') for name in names)
        assert not any('/extracted/' in name or name.endswith('scan.tar') for name in names)


def test_running_preflight_is_observable(tmp_path, raw_scan):
    runner = FakeLegacy()
    service = make_service(tmp_path, runner)
    run = service.create_full(raw_scan, 'room', tmp_path)
    observed = []
    def process(*args, **kwargs):
        runner(*args, **kwargs)
        from room_agent.full_scan import upstream_folder
        request, _ = service._load(run)
        folder = upstream_folder(request)
        result = read_json(folder / 'result.json')
        result.update(stage='model_load', status='failed', full_run_completed=False)
        atomic_json(folder / 'result.json', result)
        observed.append(service.show(run))
        raise KeyboardInterrupt('interrupted fixture')
    service.full_process_runner = process
    with pytest.raises(KeyboardInterrupt):
        service.execute(run)
    assert observed[0]['execution_status'] == 'running'
    assert observed[0]['current_node'] == 'model_load'
    assert service.show(run)['execution_status'] == 'interrupted'


def test_completed_legacy_receipt_after_worker_interruption_does_not_repeat_models(tmp_path, raw_scan):
    runner = FakeLegacy()
    def interrupted(*args, **kwargs):
        runner(*args, **kwargs)
        raise KeyboardInterrupt('worker interrupted after upstream success')
    service = make_service(tmp_path, interrupted)
    run = service.create_full(raw_scan, 'room', tmp_path)
    with pytest.raises(KeyboardInterrupt):
        service.execute(run)
    restarted = make_service(tmp_path, runner)
    assert restarted.execute(run)['execution_status'] == 'completed'
    assert len(runner.calls) == 1


def test_incomplete_or_mismatched_legacy_metadata_refuses_recovery(tmp_path, raw_scan):
    from room_agent.full_scan import upstream_folder
    runner = FakeLegacy()
    runner.fail = True
    service = make_service(tmp_path, runner)
    run = service.create_full(raw_scan, 'room', tmp_path)
    with pytest.raises(RuntimeError):
        service.execute(run)
    folder = upstream_folder(service._load(run)[0])
    previous = read_json(folder / 'input.json')
    previous['codex_model'] = 'different-model'
    atomic_json(folder / 'input.json', previous)
    with pytest.raises(ValueError, match='configuration mismatch'):
        service.execute(run)
    (folder / 'input.json').unlink()
    with pytest.raises(ValueError, match='Incomplete legacy'):
        service.execute(run)
    assert len(runner.calls) == 1


def test_export_collection_does_not_follow_symlinks(tmp_path, raw_scan):
    from room_agent.full_scan import collect, upstream_folder
    service = make_service(tmp_path, FakeLegacy())
    run = service.create_full(raw_scan, 'room', tmp_path)
    request, directory = service._load(run)
    folder = upstream_folder(request)
    folder.mkdir(parents=True)
    secret = tmp_path / 'secret'
    secret.mkdir()
    (secret / 'Room.glb').write_bytes(b'secret')
    (secret / 'token.json').write_text('{}')
    (folder / 'candidate_preview').symlink_to(secret, target_is_directory=True)
    (folder / 'leak.json').symlink_to(secret / 'token.json')
    collect(request, directory)
    assert read_json(directory / 'artifacts.json') == {'previews': [], 'exports': []}
    assert not (directory / 'legacy/leak.json').exists()


def test_unsafe_tar_entries_rejected_before_publication(tmp_path, raw_scan):
    service = make_service(tmp_path, FakeLegacy())
    for name, kind in (('../outside', tarfile.REGTYPE), ('link', tarfile.SYMTYPE)):
        with tarfile.open(raw_scan, 'w') as archive:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.linkname = '/etc/passwd' if kind == tarfile.SYMTYPE else ''
            archive.addfile(member)
        with pytest.raises(ValueError, match='Unsafe tar'):
            service.create_full(raw_scan, 'room', tmp_path)
    assert service.list_runs() == []


def test_scan_discovery_excludes_bad_tar_and_links(tmp_path, raw_scan):
    from room_agent.full_scan import discover_scans
    raw_scan.with_name('bad.tar.gz').write_text('not tar')
    raw_scan.with_name('linked.tar').symlink_to(raw_scan)
    found = discover_scans(tmp_path)
    assert found == [{'path': str(raw_scan), 'label': 'scan.tar', 'scene': 'scan'}]


def test_real_process_log_is_also_streamed_without_replaying_previous_attempt(tmp_path, capsys):
    import os
    import sys
    from room_agent.full_scan import run_backend
    log = tmp_path / 'full.log'
    log.write_text('previous attempt\n')
    run_backend([sys.executable, '-u', '-c', 'print("visible backend output")'], log, seconds=10, env=dict(os.environ))
    output = capsys.readouterr().out
    assert 'visible backend output' in output
    assert 'previous attempt' not in output
    assert log.read_text() == 'previous attempt\nvisible backend output\n'


def test_backend_uses_captured_model_config_and_drops_orchestrator_python_path(tmp_path, raw_scan, monkeypatch):
    monkeypatch.setenv('PYTHONPATH', '/orchestrator-only')
    monkeypatch.setenv('PYTHONHOME', '/orchestrator-home')
    monkeypatch.setenv('LR_CODEX_MODEL', 'selected-model')
    monkeypatch.setenv('ROOM_TRELLIS_RESOLUTION', '1024')
    monkeypatch.setenv('ROOM_GPU_PYTHON', '/fixture/gpu/python')
    monkeypatch.setenv('ROOM_BLENDER', '/fixture/blender')
    unsupported = {
        'LR_HARNESS': '/another-run/harness', 'LR_SURFACE_REF': '/another-run/reference',
        'LITEREALITY_ROOM_DIR': '/another-run/room', 'LITEREALITY_ROOM_PREVIEW': '/another-run/preview',
        'HARNESS_ROUNDS': '999', 'HARNESS_BUDGET': '999', 'ROOM_UNSUPPORTED_OVERRIDE': 'changed',
    }
    for key, value in unsupported.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv('CUDA_VISIBLE_DEVICES', raising=False)
    monkeypatch.setenv('HTTPS_PROXY', 'http://fixture-proxy.invalid')
    captured = []
    runner = FakeLegacy()
    def process(*args, **kwargs):
        captured.append(kwargs['env'])
        return runner(*args, **kwargs)
    service = make_service(tmp_path, process)
    run = service.create_full(raw_scan, 'room', tmp_path, prepare_only=True)
    monkeypatch.setenv('LR_CODEX_MODEL', 'later-model')
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', 'later-device')
    service.execute(run)
    assert 'PYTHONPATH' not in captured[0] and 'PYTHONHOME' not in captured[0]
    assert captured[0]['LR_CODEX_MODEL'] == 'selected-model'
    assert captured[0]['ROOM_TRELLIS_RESOLUTION'] == '1024'
    assert captured[0]['ROOM_GPU_PYTHON'] == '/fixture/gpu/python'
    assert captured[0]['ROOM_BLENDER'] == '/fixture/blender'
    assert not any(key in captured[0] for key in unsupported)
    assert 'CUDA_VISIBLE_DEVICES' not in captured[0]
    assert captured[0]['HTTPS_PROXY'] == 'http://fixture-proxy.invalid'
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '0')
    pinned_run = service.create_full(raw_scan, 'room', tmp_path, prepare_only=True)
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '1')
    service.execute(pinned_run)
    assert captured[1]['CUDA_VISIBLE_DEVICES'] == '0'


def test_scan_parent_traversal_cannot_escape_raw_directory(tmp_path, raw_scan):
    from room_agent.full_scan import discover_scans, no_links, scan_path
    outside = tmp_path / 'outside.tar'
    outside.write_bytes(raw_scan.read_bytes())
    traversal = raw_scan.parent / '../../outside.tar'
    service = make_service(tmp_path, FakeLegacy())
    for call in (lambda: service.create_full(traversal, 'room', tmp_path),
                 lambda: scan_path(traversal, tmp_path),
                 lambda: no_links(traversal, raw_scan.parent)):
        with pytest.raises(ValueError, match='traversal'):
            call()
    assert service.list_runs() == []
    assert [item['path'] for item in discover_scans(tmp_path)] == [str(raw_scan)]


def test_failed_author_room_preview_exports_are_also_candidates(tmp_path, raw_scan):
    from room_agent.full_scan import collect, upstream_folder
    runner = FakeLegacy()
    runner.fail = True
    service = make_service(tmp_path, runner)
    run = service.create_full(raw_scan, 'room', tmp_path)
    with pytest.raises(RuntimeError):
        service.execute(run)
    request, directory = service._load(run)
    preview = upstream_folder(request) / 'scenes' / request['upstream_scene'] / 'realism_authoring/room_preview'
    preview.mkdir(parents=True)
    (preview / 'Room.glb').write_bytes(b'unaccepted room preview')
    collect(request, directory)
    exports = service.show(run)['exports']
    assert len(exports) == 2
    assert all(item['candidate'] is True for item in exports)
