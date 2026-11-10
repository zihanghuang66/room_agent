import json
from pathlib import Path
import pytest
from room_agent import review_targets as rt


def contract(*ids):
    return rt.make_contract([{'id': x} for x in ids])


def response(c, frames=(0,)):
    return {'targets': {k: {'frames': list(frames), 'visibility': 'visible', 'reason': 'Visible in selected photographs'} for k in c['targets']}}


def test_targets_follow_scene_instead_of_bedroom_template():
    c = contract('Desk0', 'Chair0', 'Floor0')
    assert {v['object_id'] for v in c['targets'].values() if v['object_id']} == {'Desk0', 'Chair0', 'Floor0'}
    assert 'headboard' not in json.dumps(c)
    assert 'Bed0' in json.dumps(contract('Bed0', 'Bedside0'))
    with pytest.raises(ValueError): contract('Desk0', 'Desk0')


def test_every_object_requires_visibility_and_actual_camera_ids():
    c = contract('Desk0', 'Chair0')
    data = response(c, (200,))
    result = rt.select_frames(data, [0, 12, 200], c)
    assert 200 in result['frames']
    del data['targets']['object:Chair0']
    with pytest.raises(ValueError): rt.select_frames(data, [0, 12, 200], c)
    with pytest.raises(ValueError): rt.select_frames(response(c, (71,)), [0, 12, 200], c)


def test_absent_model_object_is_not_silently_dropped():
    c = contract('Chair0')
    data = response(c)
    data['targets']['object:Chair0'] = {'frames': [], 'visibility': 'absent', 'reason': 'No matching chair in source photos'}
    result = rt.select_frames(data, [0, 2], c)
    assert 'object:Chair0' in result['unresolved']
    assert result['targets']['object:Chair0']['visibility'] == 'absent'


def test_old_selection_does_not_inject_foreign_targets_into_review():
    c = contract('Desk0')
    old = {'frames': [0, 71], 'targets': {'headboard': {'reason': 'not seen'}}, 'unobservable': ['headboard']}
    cleaned = rt.review_selection(old, c)
    assert cleaned['frames'] == [0, 71]
    assert cleaned['targets'] == {}
    assert cleaned['unresolved'] == ['object:Desk0', 'scene:lighting', 'scene:contacts', 'scene:missing_objects']
    assert 'headboard' not in rt.review_prompt([{'id': 'Desk0'}], old)


def test_contract_and_skill_are_saved_and_loaded_in_real_selector(tmp_path, monkeypatch):
    import sys, types
    c = contract('Desk0')
    seen = []
    class Spec:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    async def session(spec, folder, role):
        seen.append(spec)
        return {'termination': 'completed', 'text': json.dumps(response(c)), 'reason': ''}
    monkeypatch.setitem(sys.modules, 'litereality_agent.agent.providers', types.SimpleNamespace(SessionSpec=Spec))
    monkeypatch.setitem(sys.modules, 'worker', types.SimpleNamespace(thumbnails=lambda *args: ([0, 200], [tmp_path/'page.jpg'])))
    monkeypatch.setitem(sys.modules, 'batch_io', types.SimpleNamespace(source_hashes=lambda p: {'Room.py': 'same'}))
    monkeypatch.setitem(sys.modules, 'agent_session', types.SimpleNamespace(recorded_session=session))
    out = rt.evidence_selection(tmp_path, tmp_path/'attempt', tmp_path, [{'id': 'Desk0'}])
    assert 'object:Desk0' in out['targets']
    assert seen[0].read_only is True
    assert rt.skill_text() in seen[0].prompt
    assert rt.skill_text() in rt.review_prompt([{'id': 'Desk0'}], out)
    assert json.loads((tmp_path/'attempt/evidence/targets.json').read_text()) == c


def test_returned_office_inventory_is_complete_and_not_bedroom_specific():
    import runpy
    ids = runpy.run_path(str(Path(__file__).parent/'fixtures/review_inventory.py'))['OFFICE_OBJECT_IDS']
    c = contract(*ids)
    assert len(ids) == 119
    assert len(c['targets']) == 122
    assert len({x['object_id'] for x in c['targets'].values()} - {None}) == 119
    assert all('headboard' not in x and 'bedside' not in x and 'bedding' not in x for x in c['targets'])
    result = rt.select_frames(response(c, (200,)), range(201), c)
    assert result['frames'][-1] == 200
    assert result['unresolved'] == []
    # This is selector coverage only; the unchanged reviewer must still verify all IDs.


def test_unknown_targets_and_false_visibility_are_rejected():
    c = contract('Desk0')
    data = response(c)
    data['targets']['headboard'] = data['targets']['object:Desk0']
    with pytest.raises(ValueError): rt.select_frames(data, [0], c)
    for frames, visibility in [([True], 'visible'), ([], 'visible'), ([0], 'absent'), ([0], 'unobservable')]:
        data = response(c)
        data['targets']['object:Desk0'].update(frames=frames, visibility=visibility)
        with pytest.raises(ValueError): rt.select_frames(data, [0], c)


def test_read_only_selector_rejects_source_mutation(tmp_path, monkeypatch):
    import sys, types
    class Spec:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    async def session(*args):
        return {'termination': 'completed', 'text': json.dumps(response(contract('Desk0')))}
    hashes = iter([{'Room.py': 'before'}, {'Room.py': 'after'}])
    monkeypatch.setitem(sys.modules, 'litereality_agent.agent.providers', types.SimpleNamespace(SessionSpec=Spec))
    monkeypatch.setitem(sys.modules, 'worker', types.SimpleNamespace(thumbnails=lambda *args: ([0], [])))
    monkeypatch.setitem(sys.modules, 'batch_io', types.SimpleNamespace(source_hashes=lambda p: next(hashes)))
    monkeypatch.setitem(sys.modules, 'agent_session', types.SimpleNamespace(recorded_session=session))
    with pytest.raises(RuntimeError, match='changed room source'):
        rt.evidence_selection(tmp_path, tmp_path/'attempt', tmp_path, [{'id': 'Desk0'}])
