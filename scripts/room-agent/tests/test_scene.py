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
    assert service.execute(run)['execution_status'] == 'waiting_for_input'
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
