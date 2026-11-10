"""Real edit/review checkpoint logic with only external sessions replaced."""
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
import pytest


@pytest.fixture
def runtime(tmp_path,monkeypatch):
    root=Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root/'litereality-batch-review-v0.1.0'))
    monkeypatch.syspath_prepend(str(root/'room-agent/src/room_agent'))
    worker=ModuleType('worker')
    worker.source_build_stamp=lambda *a: {'source':'stable'}
    monkeypatch.setitem(sys.modules,'worker',worker)
    providers=ModuleType('litereality_agent.agent.providers');providers.SessionSpec=lambda **kw:SimpleNamespace(**kw)
    monkeypatch.setitem(sys.modules,providers.__name__,providers)
    sessions=ModuleType('agent_session');sessions.calls=[]
    async def session(spec,path,role):
        sessions.calls.append(spec)
        return {'termination':'completed','text':''}
    sessions.recorded_session=session;monkeypatch.setitem(sys.modules,'agent_session',sessions)
    source=tmp_path/'original';source.mkdir();(source/'Room.py').write_text('x=1')
    baseline=tmp_path/'baseline';baseline.mkdir()
    capture=tmp_path/'capture';capture.mkdir()
    attempt=tmp_path/'attempt';attempt.mkdir()
    req=dict(capture=str(capture),instruction='Move the bed 20 cm.',capture_files={},codex_model='test-model')
    state=dict(active_source=str(source),baseline_round_dir=str(baseline),baseline_room=str(source),baseline_pairs=[],
               room=str(source),preview=str(baseline),build_stamp={'source':'stable'},pairs=[],geometry_pass=True,selection={'frames':[1]},issues=[])
    return req,state,attempt,sessions


def test_interrupted_edit_retains_changes_and_never_recalls_model(runtime):
    from room_agent.edit_driver import run_stage
    req,state,attempt,sessions=runtime
    async def interrupted(spec,path,role):
        sessions.calls.append(spec);(spec.cwd/'Room.py').write_text('x=2')
        raise RuntimeError('connection lost after saved edit')
    sessions.recorded_session=interrupted
    with pytest.raises(RuntimeError):run_stage('edit',req,state,attempt)
    result,_=run_stage('edit',req,state,attempt)
    assert result['edit_termination']=='interrupted' and len(sessions.calls)==1
    assert (Path(result['active_source'])/'Room.py').read_text()=='x=2'
    assert (Path(state['active_source'])/'Room.py').read_text()=='x=1'


def test_edit_recovers_copy_interruption_before_model_starts(runtime, monkeypatch):
    import batch_io
    from room_agent.edit_driver import run_stage
    req,state,attempt,sessions=runtime
    original_copy=batch_io.copy_source
    def partial_copy(src,dst):
        dst.mkdir(parents=True)
        (dst/'Room.py').write_text('x=')
        raise OSError('copy interrupted')
    monkeypatch.setattr(batch_io,'copy_source',partial_copy)
    with pytest.raises(OSError,match='copy interrupted'):
        run_stage('edit',req,state,attempt)
    assert not sessions.calls and not (attempt/'edit_started.json').exists()
    monkeypatch.setattr(batch_io,'copy_source',original_copy)
    result,_=run_stage('edit',req,state,attempt)
    assert len(sessions.calls)==1
    assert (Path(result['active_source'])/'Room.py').read_text()=='x=1'
    assert (Path(state['active_source'])/'Room.py').read_text()=='x=1'


def test_edit_recovers_legacy_partial_copy_without_deleting_it(runtime):
    from room_agent.edit_driver import run_stage
    req,state,attempt,sessions=runtime
    active=attempt/'room';active.mkdir();(active/'Room.py').write_text('x=')
    result,_=run_stage('edit',req,state,attempt)
    assert len(sessions.calls)==1
    assert (Path(result['active_source'])/'Room.py').read_text()=='x=1'
    assert any(p.read_text()=='x=' for p in attempt.glob('room.copy-interrupted-*/Room.py'))


def test_group_review_retry_reuses_completed_groups_and_strict_geometry(runtime):
    from room_agent.edit_driver import run_stage
    req,state,attempt,sessions=runtime
    objects=[{'id':f'o{i}'} for i in range(9)]
    (Path(state['preview'])/'room_layout.json').write_text(json.dumps({'objects':objects}))
    pair=attempt/'pair_00001.png';pair.write_bytes(b'test');state['pairs']=[str(pair)]
    fail=True
    async def review(spec,path,role):
        sessions.calls.append(str(path));index=int(path.name.rsplit('_',1)[1])
        if index==1 and fail:raise RuntimeError('review timeout')
        ids=[x['id'] for x in objects[index*8:index*8+8]]
        return {'termination':'completed','text':json.dumps({'change_verified':True,'change_evidence':'source comparison','pass':True,'score':8,'all_objects_reviewed':True,'objects':[dict(id=id,observed=True,views=['pair_00001.png'],reason='visible') for id in ids],'issues':[]})}
    sessions.recorded_session=review
    with pytest.raises(RuntimeError):run_stage('review',req,state,attempt)
    fail=False;state['geometry_pass']=False
    result,_=run_stage('review',req,state,attempt)
    assert result['quality_status']=='not_passed'
    state['geometry_pass']=True
    assert run_stage('review',req,state,attempt)[0]['quality_status']=='passed'
    assert sum(x.endswith('000') for x in sessions.calls)==1
    assert sum(x.endswith('001') for x in sessions.calls)==2
    (attempt/'group_000.json').write_text('{}')
    with pytest.raises(RuntimeError,match='integrity'):run_stage('review',req,state,attempt)


def test_review_cannot_pass_with_insufficient_coverage(runtime):
    from room_agent.edit_driver import run_stage
    req,state,attempt,sessions=runtime
    (Path(state['preview'])/'room_layout.json').write_text(json.dumps({'objects':[{'id':'bed'}]}))
    async def review(*args):
        return {'termination':'completed','text':json.dumps({'pass':True,'score':10,'all_objects_reviewed':True,'objects':[],'issues':[]})}
    sessions.recorded_session=review
    result,_=run_stage('review',req,state,attempt)
    assert result['quality_status']=='not_passed'


def test_selection_covers_whole_trajectory():
    from room_agent.edit_driver import selection
    frames=selection(range(301))
    assert len(frames)==24 and frames[0]==0 and frames[-1]==300
    assert selection([2,12])==[2,12]


def test_comparison_matches_camera_ids_and_correct_render_panel(runtime,monkeypatch):
    import struct
    from room_agent.edit_driver import run_stage
    req,state,attempt,sessions=runtime
    module=ModuleType('litereality_agent.room_ops.rendering.room_render.render_vs_capture');module._upright_roll=lambda *a:0
    monkeypatch.setitem(sys.modules,module.__name__,module)
    def png(path,w,h):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'\x89PNG\r\n\x1a\n'+b'\0\0\0\rIHDR'+struct.pack('>II',w,h))
    pairs=[]
    for side in ('before','after'):
        pair=attempt/side/'pairs/pair_00001.png';png(pair,1508,754) # photo has different aspect ratio
        png(pair.parent.parent/'renders/frame_00001.png',100,100)
        pairs.append(str(pair))
    state.update(baseline_pairs=[pairs[0]],pairs=[pairs[1]],quality_status='not_passed')
    result,_=run_stage('finalize',req,state,attempt)
    row=result['change_views'][0]
    assert row['captured']==row['rendered']==[0,34,720,720]
    assert row['before_path']==pairs[0] and row['path']==pairs[1]
    state['selection']['frames']=[2]
    with pytest.raises(RuntimeError,match='camera IDs'):run_stage('finalize',req,state,attempt)


def test_fake_view_citation_cannot_pass_quality(runtime):
    from room_agent.edit_driver import run_stage
    req,state,attempt,sessions=runtime
    (Path(state['preview'])/'room_layout.json').write_text(json.dumps({'objects':[{'id':'bed'}]}))
    async def review(*args):
        return {'termination':'completed','text':json.dumps({'pass':True,'score':10,'all_objects_reviewed':True,
                'objects':[{'id':'bed','observed':True,'views':['nonexistent.png']}],'issues':[]})}
    sessions.recorded_session=review
    result,_=run_stage('review',req,state,attempt)
    assert result['quality_status']=='not_passed' and result['issues'][0]['kind']=='evidence'


def test_quality_requires_verified_user_change(runtime):
    from room_agent.edit_driver import run_stage
    req,state,attempt,sessions=runtime
    pair=attempt/'pair.png';pair.write_bytes(b'image');state['pairs']=[str(pair)]
    (Path(state['preview'])/'room_layout.json').write_text(json.dumps({'objects':[{'id':'bed'}]}))
    async def review(*args):
        return {'termination':'completed','text':json.dumps({'pass':True,'score':10,'all_objects_reviewed':True,
                'objects':[{'id':'bed','observed':True,'views':['pair.png']}],'issues':[]})}
    sessions.recorded_session=review
    result,_=run_stage('review',req,state,attempt)
    assert result['quality_status']=='not_passed'
    assert any('Requested change' in r['detail'] for r in result['issues'])
