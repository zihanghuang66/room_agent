import json
from pathlib import Path
import pytest


@pytest.fixture
def scene(tmp_path):
    run = tmp_path / 'return_output/artifacts/existing'
    root = run / 'scenes/room_004_test_04'
    for relative in ('realism_authoring/room', 'scene_init/scene_stage/room_init/room'):
        p = root / relative
        p.mkdir(parents=True)
        (p / 'Room.py').write_text('value = 1\n')
        (p / 'Room.md').write_text('fixture')
        (p / 'manifest.json').write_text('{}')
    capture = run / 'input/extracted'
    capture.mkdir(parents=True)
    for name in ('frame_000.json', 'frame_000.jpg', 'depth_000.png', 'room.usdz'):
        (capture / name).write_text('{}')
    (run / 'input.json').write_text(json.dumps({
        'scene': 'room_004_test_04', 'capture': str(capture),
        'app_sha256': '8748b08b2ed7790a7b63f389a523a86707b23b8adb6522810b40e401f194fc94'}))
    return run


class RecordingExecutor:
    calls = []
    fail = None
    quality = 'not_passed'
    termination = 'completed'

    def __init__(self, request, directory, journal):
        self.request, self.directory, self.journal = request, directory, journal

    def verify(self):
        pass

    def step(self, name, state):
        self.calls.append((name, state.get('round', 0)))
        if name == self.fail:
            raise RuntimeError('stage failed')
        if name == 'snapshot':
            return {'artifact_path': str(self.directory / 'work'), 'revision_id': 'fixture'}
        if name == 'geometry':
            return {'geometry_pass': True}
        if name == 'review':
            return {'quality_status': self.quality, 'score': 5, 'issues': [
                {'priority': 'lighting', 'defect': 'dark', 'object_ids': []}]}
        if name == 'repair':
            return {'repair_termination': self.termination}
        return {}


@pytest.fixture
def service(tmp_path):
    from room_agent.service import Service
    RecordingExecutor.calls = []
    RecordingExecutor.fail = None
    RecordingExecutor.quality = 'not_passed'
    RecordingExecutor.termination = 'completed'
    return Service(tmp_path / 'return_output/agent_state', scene_executor_type=RecordingExecutor)


def test_scene_feature_exists():
    from room_agent.service import Service
    assert hasattr(Service, 'create_review')


def test_real_graph_stops_at_review_and_preserves_quality_failure(service, scene):
    run = service.create_review(scene)
    result = service.execute(run)
    assert result['execution_status'] == 'waiting_for_input'
    assert result['quality_status'] == 'not_passed'
    assert [x[0] for x in RecordingExecutor.calls] == [
        'snapshot', 'preflight', 'build', 'geometry', 'evidence', 'render', 'review']
    assert result['operation'] == 'saved_scene_review'
    assert service.execute(run, action='finish')['execution_status'] == 'completed'


def test_failed_node_resumes_without_rebuilding_completed_stage(service, scene):
    run = service.create_review(scene)
    RecordingExecutor.fail = 'render'
    with pytest.raises(RuntimeError):
        service.execute(run)
    assert service.show(run)['error']['node'] == 'render'
    RecordingExecutor.fail = None
    from room_agent.service import Service
    restarted = Service(service.root, scene_executor_type=RecordingExecutor)
    assert restarted.execute(run)['execution_status'] == 'waiting_for_input'
    names = [x[0] for x in RecordingExecutor.calls]
    assert names.count('build') == 1
    assert names.count('render') == 2


def test_repair_timeout_still_rebuilds_and_reviews(service, scene):
    run = service.create_review(scene, max_batches=1)
    service.execute(run)
    RecordingExecutor.termination = 'timeout'
    result = service.execute(run, action='repair')
    assert result['execution_status'] == 'completed'
    assert result['repair_termination'] == 'timeout'
    assert result['completion_reason'] == 'repair_timeout'
    assert result['quality_status'] == 'not_passed'
    assert ('build', 1) in RecordingExecutor.calls and ('review', 1) in RecordingExecutor.calls
    assert sum(name == 'snapshot' for name, _ in RecordingExecutor.calls) == 1


def test_no_repair_after_limit_or_quality_pass(service, scene):
    run = service.create_review(scene, max_batches=0)
    assert service.execute(run)['execution_status'] == 'completed'
    with pytest.raises(ValueError):
        service.execute(run, action='repair')
    RecordingExecutor.quality = 'passed'
    other = service.create_review(scene)
    assert service.execute(other)['execution_status'] == 'completed'


def test_source_candidate_cannot_escape_run(service, scene, tmp_path):
    (scene / 'latest_candidate.json').write_text(json.dumps({'source': str(tmp_path)}))
    with pytest.raises(ValueError, match='escape'):
        service.create_review(scene)


def test_discover_does_not_choose_latest_implicitly(scene):
    from room_agent.saved_scene import discover_sources
    candidates = discover_sources(scene.parent.parent.parent)
    assert [c['path'] for c in candidates] == [str(scene)]


def test_real_snapshot_receipt_reused_and_tamper_rejected(tmp_path, scene):
    from room_agent.service import Service
    from room_agent.scene_executor import SceneExecutor
    from room_agent.storage import Journal
    s = Service(tmp_path / 'return_output/agent_state')
    run = s.create_review(scene)
    request, directory = s._load(run)
    executor = SceneExecutor(request, directory, Journal(directory, run))
    state = {'round': 0}
    first = executor.step('snapshot', state)
    assert executor.step('snapshot', state) == first
    original = scene / 'scenes/room_004_test_04/realism_authoring/room/Room.py'
    assert original.read_text() == 'value = 1\n'
    target = Path(first['artifact_path']) / 'initial/room/Room.py'
    target.write_text('changed')
    with pytest.raises(ValueError, match='integrity'):
        executor.step('snapshot', state)


def test_repair_evidence_is_inside_readable_round(tmp_path):
    from room_agent.scene_driver import copy_repair_evidence
    external = tmp_path / 'other_attempt'
    external.mkdir()
    geometry = external / 'validation.json'
    geometry.write_text('{}')
    pair = external / 'pair_001.png'
    pair.write_bytes(b'pair')
    rounddir = tmp_path / 'round'
    rounddir.mkdir()
    copy_repair_evidence({'geometry_report': str(geometry), 'pairs': [str(pair)],
                          'selection': {'frames': [1]}}, rounddir)
    assert (rounddir / 'validation.json').read_text() == '{}'
    assert (rounddir / 'compare/pairs/pair_001.png').read_bytes() == b'pair'


def test_completed_build_receipt_covers_render_assets(tmp_path, scene):
    from room_agent.service import Service
    from room_agent.scene_executor import SceneExecutor
    from room_agent.storage import Journal
    service = Service(tmp_path / 'return_output/state')
    run = service.create_review(scene)
    request, directory = service._load(run)
    class Build( SceneExecutor ):
        def _external(self, name, state, key):
            preview = self.work / 'preview'
            (preview / 'Object').mkdir(parents=True)
            (preview / 'Object/chair.glb').write_bytes(b'glTF-test')
            return {'preview': str(preview)}, [preview]
    executor = Build(request, directory, Journal(directory, run))
    executor.step('build', {'round': 0})
    (executor.work / 'preview/Object/chair.glb').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='integrity'):
        executor.verify()


def test_create_is_not_visible_until_request_is_complete(tmp_path, scene, monkeypatch):
    import room_agent.service as module
    service = module.Service(tmp_path / 'return_output/state')
    original = module.atomic_json
    def inspect_partial(path, data):
        original(path, data)
        if Path(path).name == 'input.json':
            assert service.list_runs() == []
    monkeypatch.setattr(module, 'atomic_json', inspect_partial)
    run = service.create_review(scene)
    assert service.show(run)['execution_status'] == 'created'


def test_appended_capture_frames_invalidate_request(tmp_path, scene):
    from room_agent.service import Service
    from room_agent.scene_executor import SceneExecutor
    from room_agent.storage import Journal
    service = Service(tmp_path / 'return_output/state')
    run = service.create_review(scene)
    request, directory = service._load(run)
    for name in ('frame_001.jpg', 'frame_001.json', 'depth_001.png'):
        (Path(request['capture']) / name).write_text('new input')
    with pytest.raises(ValueError, match='capture integrity'):
        SceneExecutor(request, directory, Journal(directory, run)).verify()


def test_added_snapshot_code_is_not_an_accepted_input(tmp_path, scene):
    from room_agent.service import Service
    from room_agent.scene_executor import SceneExecutor
    from room_agent.storage import Journal
    service = Service(tmp_path / 'return_output/state')
    run = service.create_review(scene)
    request, directory = service._load(run)
    executor = SceneExecutor(request, directory, Journal(directory, run))
    saved = executor.step('snapshot', {'round': 0})
    (Path(saved['artifact_path']) / 'initial/room/unregistered.py').write_text('unexpected=True')
    with pytest.raises(ValueError, match='integrity'):
        executor.verify()


def test_existing_preview_links_are_excluded_from_source_snapshot(tmp_path, scene):
    from room_agent.service import Service
    from room_agent.scene_executor import SceneExecutor
    from room_agent.storage import Journal
    root = scene / 'scenes/room_004_test_04'
    for relative in ('realism_authoring/room', 'scene_init/scene_stage/room_init/room'):
        room = root / relative
        (room / 'Object').mkdir()
        (room / 'Object/bed.glb').write_bytes(b'required asset')
        for name in ('repair_preview', 'room_preview'):
            preview = room / name
            preview.mkdir()
            (preview / 'Object').symlink_to('../Object', target_is_directory=True)
            (preview / 'old.blend').write_bytes(b'rebuildable preview')
    service = Service(tmp_path / 'return_output/state')
    run = service.create_review(scene)
    request, directory = service._load(run)
    for key in ('source_files', 'seed_files'):
        assert 'Object/bed.glb' in request[key]
        assert not any(p.startswith(('repair_preview/', 'room_preview/')) for p in request[key])
    # Changing old previews after selection must not invalidate the saved source.
    (root / 'realism_authoring/room/repair_preview/old.blend').write_bytes(b'new preview')
    executor = SceneExecutor(request, directory, Journal(directory, run))
    result = executor.step('snapshot', {'round': 0})
    for leaf in ('room', 'seed'):
        copied = Path(result['artifact_path']) / 'initial' / leaf
        assert (copied / 'Object/bed.glb').read_bytes() == b'required asset'
        assert not (copied / 'repair_preview').exists()
        assert not (copied / 'room_preview').exists()
    executor.verify()
    assert (root / 'realism_authoring/room/repair_preview/Object').is_symlink()


@pytest.mark.parametrize('linked', ['Object/bed.glb', 'Object/repair_preview', 'Room.py'])
def test_source_asset_links_still_fail_outside_top_level_previews(tmp_path, scene, linked):
    from room_agent.saved_scene import source_info
    room = scene / 'scenes/room_004_test_04/realism_authoring/room'
    external = tmp_path / 'external'
    external.write_bytes(b'outside')
    path = room / linked
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        path.unlink()
    path.symlink_to(external)
    with pytest.raises(ValueError, match='symlink'):
        source_info(scene)


def test_artifact_tree_checks_do_not_exclude_preview_assets(tmp_path):
    from room_agent.saved_scene import tree_hashes
    root = tmp_path / 'artifact'
    asset = root / 'repair_preview/Object/bed.glb'
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b'asset')
    before = tree_hashes(root)
    assert 'repair_preview/Object/bed.glb' in before
    asset.write_bytes(b'changed')
    assert tree_hashes(root) != before
    asset.unlink()
    asset.symlink_to(tmp_path / 'outside')
    with pytest.raises(ValueError, match='symlink'):
        tree_hashes(root)


def test_finish_is_auditable_and_terminal_display_has_no_pending_work(service, scene):
    run = service.create_review(scene)
    assert service.execute(run)['execution_status'] == 'waiting_for_input'
    result = service.execute(run, action='finish', action_origin='web')
    assert result['current_node'] == 'finished'
    assert result['completion_reason'] == 'user_finished'
    assert result['quality_status'] == 'not_passed'
    assert next(s for s in result['stages'] if s['node'] == 'repair')['status'] == 'skipped'
    assert not any(s['status'] == 'pending' for s in result['stages'])
    events = service.events(run)
    assert len([e for e in events if e['kind'] == 'decision_waiting']) == 1
    action = next(e for e in events if e['kind'] == 'action_received')
    assert action['action'] == 'finish' and action['origin'] == 'web'
    done = next(e for e in events if e['kind'] == 'decision_finished')
    assert done['action'] == 'finish' and done['reason'] == 'user_finished'


def test_automatic_end_explains_no_repair_budget(service, scene):
    run = service.create_review(scene, max_batches=0)
    result = service.execute(run)
    assert result['completion_reason'] == 'repair_budget_exhausted'
    assert not any(e['kind'] == 'action_received' for e in service.events(run))


def test_restart_then_repair_runs_new_round_without_repeating_snapshot(service, scene):
    from room_agent.service import Service
    run = service.create_review(scene)
    service.execute(run)
    reopened = Service(service.root, scene_executor_type=RecordingExecutor)
    result = reopened.execute(run, action='repair', action_origin='cli')
    assert result['round'] == 1 and result['execution_status'] == 'completed'
    assert result['completion_reason'] == 'repair_budget_exhausted'
    assert RecordingExecutor.calls.count(('snapshot', 0)) == 1
    assert next(s for s in result['stages'] if s['node'] == 'repair')['status'] == 'completed'
    assert RecordingExecutor.calls.count(('preflight', 0)) == 1
    assert ('build', 1) in RecordingExecutor.calls and ('review', 1) in RecordingExecutor.calls
    assert any(e['kind'] == 'decision_finished' and e['action'] == 'repair' for e in reopened.events(run))


def test_resume_after_repair_evidence_failure_only_retries_evidence(service, scene):
    run = service.create_review(scene, max_batches=1)
    assert service.execute(run)['execution_status'] == 'waiting_for_input'
    RecordingExecutor.fail = 'evidence'
    with pytest.raises(RuntimeError, match='stage failed'):
        service.execute(run, action='repair')
    failed = service.show(run)
    assert failed['round'] == 1 and failed['next_nodes'] == ['evidence']
    before = list(RecordingExecutor.calls)
    RecordingExecutor.fail = None
    result = service.execute(run)
    assert result['execution_status'] == 'completed'
    assert RecordingExecutor.calls[len(before):] == [('evidence', 1), ('render', 1), ('review', 1)]
    assert RecordingExecutor.calls.count(('repair', 0)) == 1
    assert RecordingExecutor.calls.count(('build', 1)) == 1


def test_http_queue_resume_after_repair_evidence_failure(service, scene):
    import http.client
    import threading
    import time
    from room_agent.task_queue import TaskQueue
    from room_agent.web import make_server

    class ThreadWorker:
        def __init__(self):
            self.thread = None
        @property
        def busy(self):
            return self.thread is not None and self.thread.is_alive()
        def start(self, run_id, action=None):
            def execute():
                try:
                    service.execute(run_id, action=action, action_origin='web')
                except RuntimeError:
                    pass
            self.thread=threading.Thread(target=execute)
            self.thread.start()
            return {'pid':0,'run_id':run_id}
        def close(self):
            if self.thread:self.thread.join(timeout=5)

    queue=TaskQueue(service.root,service,ThreadWorker())
    server=make_server(service.root,scene.parents[2],port=0,service=service,worker=queue)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(path,body=None):
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        headers={'Origin':f'http://127.0.0.1:{server.server_port}','X-Room-Token':server.token,'Content-Type':'application/json'}
        conn.request('GET' if body is None else 'POST',path,body=None if body is None else json.dumps(body),headers=headers)
        response=conn.getresponse();data=json.loads(response.read());conn.close()
        assert response.status<300,data
        return data
    def wait(run,status):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            value=request('/api/runs/'+run)
            if value['execution_status']==status and value.get('queue_status') not in ('queued','dispatching','running'):
                return value
            time.sleep(.05)
        raise AssertionError(value)
    try:
        run=service.create_review(scene,max_batches=1)
        request('/api/runs/'+run+'/start',{})
        wait(run,'waiting_for_input')
        RecordingExecutor.fail='evidence'
        request('/api/runs/'+run+'/resume',{'action':'repair'})
        failed=wait(run,'failed');assert failed['round']==1
        before=list(RecordingExecutor.calls);RecordingExecutor.fail=None
        reply=request('/api/runs/'+run+'/resume',{'action':None})
        assert reply['queue_status']=='queued'
        wait(run,'completed')
        assert RecordingExecutor.calls[len(before):]==[('evidence',1),('render',1),('review',1)]
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
