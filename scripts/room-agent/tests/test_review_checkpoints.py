"""Durable review at real filesystem boundary; GPU/model work is a fake."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from room_agent import author_closeout as ac
from test_author_closeout import scene, Backend

class Many(Backend):
    def __init__(self,room):
        super().__init__(room);self.fail=set();self.reviewed=[];self.n=40
    def build(self,ctx,preview):
        super().build(ctx,preview)
        ac.save(preview/'room_layout.json',{'objects':[{'id':f'O{i:03}'} for i in range(self.n)]})
    def render(self,ctx,evidence,frames):
        self.calls.append(('render',list(frames)));p=evidence/'compare/pairs';p.mkdir(parents=True,exist_ok=True)
        for i in frames:(p/f'pair_{i:05}.png').write_bytes(b'png')
        return list(map(str,sorted(p.glob('*.png'))))
    def review(self,images,objects):
        ids=[o['id'] for o in objects];self.reviewed.append(ids)
        if ids[0] in self.fail:raise TimeoutError('wall time budget 600s reached')
        return dict(pass_=True,**{'pass':True,'score':8,'all_objects_reviewed':True,'issues':[],
            'objects':[{'id':i,'observed':True,'views':[Path(images[0]).name]} for i in ids]})


def test_timeout_restart_keeps_groups_build_and_renders(scene):
    ctx,_=scene;b=Many(ctx.authored_room);b.fail={'O030','O035'}
    with pytest.raises(TimeoutError):ac.run(ctx,{'_operation_id':'op'},backend=b)
    progress=ac.read(ctx.authoring_root/'closeout/review_progress.json')
    assert progress['status']=='incomplete' and progress['completed_objects']==30
    assert progress['error_kind']=='timeout'
    # New process / backend instance, existing ledger only.
    b2=Many(ctx.authored_room)
    r=ac.run(ctx,{'_operation_id':'op'},backend=b2)
    assert r['accepted'] and not b2.calls
    assert [x[0] for x in b2.reviewed]==['O030','O035']
    assert sum(len(x) for x in b2.reviewed)==10
    assert ac.read(ctx.authoring_root/'closeout/review_progress.json')['status']=='complete'


@pytest.mark.parametrize('change',['source','capture','layout','render','policy'])
def test_changed_inputs_cannot_reuse_approved_chunks(scene,change):
    ctx,_=scene;b=Many(ctx.authored_room);b.fail={'O030','O035'}
    with pytest.raises(TimeoutError):ac.run(ctx,{'_operation_id':'op'},backend=b)
    root=ctx.authoring_root/'closeout'
    if change=='source':(ctx.authored_room/'Room.py').write_text('# modified')
    if change=='capture':(ctx.capture_dir/'frame_00000.jpg').write_bytes(b'changed')
    if change=='layout':(ctx.authoring_root/'room_preview/room_layout.json').write_text('{}')
    if change=='render':next(root.glob('reviews/*/views_6/compare/pairs/*.png')).write_bytes(b'changed')
    if change=='policy':ac.save(root/'review_policy.json',{'semantic_version':'tampered'})
    with pytest.raises((ValueError,RuntimeError),match='changed|policy'):
        ac.run(ctx,{'_operation_id':'op'},backend=Many(ctx.authored_room))


def test_finished_group_receipt_not_replayed_after_ledger_crash(scene,monkeypatch):
    ctx,_=scene;b=Many(ctx.authored_room);b.n=20
    original=ac.save
    def crashing(path,value):
        if Path(path).name=='review_progress.json' and value.get('completed_objects')==10:
            raise OSError('simulated process loss after group commit')
        return original(path,value)
    monkeypatch.setattr(ac,'save',crashing)
    with pytest.raises(OSError):ac.run(ctx,{'_operation_id':'op'},backend=b)
    monkeypatch.setattr(ac,'save',original)
    b2=Many(ctx.authored_room)
    ac.run(ctx,{'_operation_id':'op'},backend=b2)
    assert [r[0] for r in b2.reviewed]==['O010']
    assert not b2.calls


def test_timeout_bounded_and_successful_split_sibling_reused(scene):
    ctx,_=scene;b=Many(ctx.authored_room);b.n=10;b.fail={'O005'}
    original=b.review
    def parent_timeout(images,objects):
        if len(objects)==10:
            b.reviewed.append([x['id'] for x in objects]);raise TimeoutError('wall time budget')
        return original(images,objects)
    b.review=parent_timeout
    with pytest.raises(TimeoutError):ac.run(ctx,{'_operation_id':'op'},backend=b)
    assert [len(x) for x in b.reviewed]==[10,5,5]
    b2=Many(ctx.authored_room);ac.run(ctx,{'_operation_id':'op'},backend=b2)
    assert [x[0] for x in b2.reviewed]==['O005']


def test_no_partial_overall_score_or_acceptance(scene):
    ctx,_=scene;b=Many(ctx.authored_room);b.fail={'O010','O015'}
    with pytest.raises(TimeoutError):ac.run(ctx,{'_operation_id':'op'},backend=b)
    assert not (ctx.authoring_root/'room_preview/author_acceptance.json').exists()
    progress=ac.read(ctx.authoring_root/'closeout/review_progress.json')
    assert 'score' not in progress and progress['completed_objects']==10


def legacy_scene(tmp_path):
    import shutil
    root=tmp_path/'run';auth=root/'backend/scenes/scene/realism_authoring'
    room=auth/'room';room.mkdir(parents=True);(room/'Room.py').write_text('# original')
    capture=root/'capture';capture.mkdir()
    for i in range(72):
        (capture/f'frame_{i:05}.jpg').write_bytes(b'jpg')
        (capture/f'frame_{i:05}.json').write_text('{}')
    ctx=SimpleNamespace(authored_room=room,seed_room=room,authoring_root=auth,capture_dir=capture)
    b=Many(room);b.n=119
    b.fingerprint=lambda c,p:{'source':(c.authored_room/'Room.py').read_text()}
    preview=auth/'room_preview';b.build(ctx,preview)
    attempt=auth/'closeout/reviews/attempt-legacy';attempt.mkdir(parents=True)
    shutil.copytree(room,attempt/'room')
    ac.save(attempt/'validation.json',{'ok':True,'pass':True,'failing':[]})
    folder=attempt/'views_6';pairs=b.render(ctx,folder,[0,14,28,43,57,71])
    ac.save(folder/'compare/renders/room_layout.json',ac.read(preview/'room_layout.json'))
    objects=ac.read(preview/'room_layout.json')['objects']
    for i in range(3):ac.save(folder/f'review_{i:03}.json',ac.normalize_review(b.review(pairs,objects[i*20:(i+1)*20]),[o['id'] for o in objects[i*20:(i+1)*20]]))
    proof=root/'work/attempts/author/old'
    ac.save(proof/'request.json',{'operation_id':'op','stage':'author','legacy':'/scripts/room-agent-stage3a-v0.4.5/litereality-full-v0.3.1'})
    ac.save(proof/'stage_failure.json',{'message':'visual review incomplete: wall time budget 240s reached'})
    b.calls=[];b.reviewed=[]
    return ctx,b,attempt


def test_v045_three_existing_groups_imported_without_rebuild(tmp_path):
    ctx,b,attempt=legacy_scene(tmp_path)
    original={p.name:p.read_bytes() for p in (attempt/'views_6').glob('review_*.json')}
    r=ac.run(ctx,{'_operation_id':'op'},backend=b)
    assert r['accepted'] and not b.calls
    assert [len(x) for x in b.reviewed]==[10,10,10,10,10,9]
    assert b.reviewed[0][0]=='O060'
    assert original=={p.name:p.read_bytes() for p in (attempt/'views_6').glob('review_*.json')}
    progress=ac.read(ctx.authoring_root/'closeout/review_progress.json')
    assert progress['imported_groups']==3 and progress['completed_objects']==119


@pytest.mark.parametrize('corrupt',['source','layout','coverage','provenance','pairs','ambiguous'])
def test_legacy_migration_fails_closed_on_unverifiable_artifacts(tmp_path,corrupt):
    import shutil
    ctx,b,attempt=legacy_scene(tmp_path)
    if corrupt=='source':(ctx.authored_room/'Room.py').write_text('# changed')
    if corrupt=='layout':ac.save(attempt/'views_6/compare/renders/room_layout.json',{})
    if corrupt=='coverage':
        p=attempt/'views_6/review_001.json';r=ac.read(p);r['objects'].pop();ac.save(p,r)
    if corrupt=='provenance':
        next((tmp_path/'run/work').rglob('request.json')).unlink()
    if corrupt=='pairs':next((attempt/'views_6/compare/pairs').glob('*.png')).unlink()
    if corrupt=='ambiguous':shutil.copytree(attempt,attempt.with_name('attempt-second'))
    with pytest.raises(ValueError):ac.run(ctx,{'_operation_id':'op'},backend=b)
    assert not b.calls and not b.reviewed


def test_reviewer_timeout_actually_reaches_session_and_readonly_contract(monkeypatch,tmp_path):
    import sys,asyncio
    from types import ModuleType
    seen=[]
    class Result:
        is_error=False;stopped=None;result='{"score":8}'
    class Harness:
        async def run(self,spec):seen.append(spec);yield Result()
    providers=SimpleNamespace(SessionSpec=lambda **kw:kw,SessionResult=Result,resolve=lambda role:Harness())
    agent=ModuleType('litereality_agent.agent');agent.providers=providers
    shared=ModuleType('litereality_agent.agent.tools.shared');shared.config=SimpleNamespace(ROOT=tmp_path,MODEL='test-model')
    monkeypatch.setitem(sys.modules,'litereality_agent.agent',agent)
    monkeypatch.setitem(sys.modules,'litereality_agent.agent.tools.shared',shared)
    for seconds in (600,900):
        assert asyncio.run(ac.review_vision(['pair.png'],'strict review',timeout_seconds=seconds))=={'score':8}
        assert seen[-1]['timeout_seconds']==seconds
        assert seen[-1]['read_only'] and seen[-1]['file_tools']==('Read',)
        assert seen[-1]['model']=='test-model'


def test_timeout_configuration_does_not_discard_completed_semantic_reviews(scene):
    ctx,_=scene;b=Many(ctx.authored_room);b.fail={'O030','O035'}
    with pytest.raises(TimeoutError):ac.run(ctx,{'_operation_id':'op'},backend=b)
    ac.save(ctx.authoring_root/'closeout/review_policy.json',{'timeout_seconds':900})
    b2=Many(ctx.authored_room);ac.run(ctx,{'_operation_id':'op'},backend=b2)
    assert b2.review_timeout==900 and sum(map(len,b2.reviewed))==10


def test_cached_report_tampering_detected(scene):
    ctx,_=scene;b=Many(ctx.authored_room);b.fail={'O030','O035'}
    with pytest.raises(TimeoutError):ac.run(ctx,{'_operation_id':'op'},backend=b)
    receipt=next((ctx.authoring_root/'closeout').glob('reviews/*/views_6/group_000.json'))
    value=ac.read(receipt);value['report']['score']=9;ac.save(receipt,value)
    with pytest.raises(ValueError,match='report changed'):ac.run(ctx,{'_operation_id':'op'},backend=Many(ctx.authored_room))


def test_saved_rejection_is_reused_without_renormalizing_or_lowering_gate(tmp_path):
    ctx,b,attempt=legacy_scene(tmp_path)
    path=attempt/'views_6/review_000.json';report=ac.read(path)
    report.update(score=4.5,**{'pass':False})
    report['issues']=[{'kind':'appearance','objects':['O000'],'priority':'large_appearance','detail':'wrong shape'}]
    ac.save(path,report)
    result=ac.run(ctx,{'_operation_id':'op'},backend=b)
    assert not result['accepted'] and result['visual_review']['score']==4.5
    assert result['visual_review']['issues']==report['issues']
    assert not b.calls and b.reviewed[0][0]=='O060'


@pytest.mark.parametrize('legacy',[False,True])
def test_crash_after_evaluation_before_activation_recovers_orphan(tmp_path,scene,monkeypatch,legacy):
    if legacy:ctx,b,_=legacy_scene(tmp_path/'legacy')
    else:ctx,_=scene;b=Many(ctx.authored_room)
    original=ac.save
    def crash(path,value):
        if Path(path).name=='control.json' and value.get('active_review'):raise OSError('activation crash')
        return original(path,value)
    monkeypatch.setattr(ac,'save',crash)
    with pytest.raises(OSError):ac.run(ctx,{'_operation_id':'op'},backend=b)
    build_count=b.calls.count('build');render_count=len(b.calls)-build_count
    monkeypatch.setattr(ac,'save',original)
    result=ac.run(ctx,{'_operation_id':'op'},backend=b)
    assert result['accepted'] and b.calls.count('build')==build_count
    if legacy:
        assert not b.calls and b.reviewed[0][0]=='O060'
    else:assert len(b.calls)-build_count==render_count+1


@pytest.mark.parametrize('key,value',[('pass','false'),('pass',1),('all_objects_reviewed','true')])
def test_malformed_boolean_in_legacy_report_rejected(tmp_path,key,value):
    ctx,b,attempt=legacy_scene(tmp_path)
    path=attempt/'views_6/review_000.json';report=ac.read(path);report[key]=value;ac.save(path,report)
    with pytest.raises(ValueError,match='boolean'):ac.run(ctx,{'_operation_id':'op'},backend=b)
    assert not b.reviewed


def test_changed_reviewer_cannot_reuse_completed_groups(scene):
    ctx,_=scene;b=Many(ctx.authored_room);b.review_identity=lambda:{'model':'original'};b.fail={'O030','O035'}
    with pytest.raises(TimeoutError):ac.run(ctx,{'_operation_id':'op'},backend=b)
    b2=Many(ctx.authored_room);b2.review_identity=lambda:{'model':'changed'}
    with pytest.raises(ValueError,match='changed'):ac.run(ctx,{'_operation_id':'op'},backend=b2)
    assert not b2.reviewed


@pytest.mark.parametrize('value',[True,0,119,1801,'900'])
def test_invalid_timeout_configuration_rejected_before_model(scene,value):
    ctx,_=scene;b=Many(ctx.authored_room)
    ac.save(ctx.authoring_root/'closeout/review_policy.json',{'timeout_seconds':value})
    with pytest.raises(ValueError,match='timeout policy'):ac.run(ctx,{},backend=b)
    assert not b.calls and not b.reviewed
