"""Publication keeps upstream steps but journals completed work before review."""
import json,sys
from types import ModuleType,SimpleNamespace
from pathlib import Path
import pytest
from room_agent import author_closeout as ac
from test_review_checkpoints import Many,scene


def runtime(monkeypatch,ctx,b):
    calls=[]
    publish=ModuleType('litereality_agent.pipeline.room_qc.publish')
    api=ModuleType('litereality_agent.room_ops.api')
    def module(ctx,name,args,log_name):calls.append(log_name);return 0,ctx.authoring_root/'logs/ok.log'
    publish.run_module=module
    publish.tempfile=SimpleNamespace(mkdtemp=lambda **kw:None)
    def bake(*args):calls.append('bake');return 0
    api.bake_room=bake
    def upstream(ctx,options):
        for label in ('collision','quality','compile'):
            publish.run_module(ctx,label,[],log_name=label)
        api.bake_room(ctx.authoring_root/'room_preview/Room.blend',ctx.authoring_root/'room_preview/Room.glb')
        evidence=Path(publish.tempfile.mkdtemp(prefix='publish-review-',dir=ctx.authoring_root))
        ac.assess(ctx,ctx.authoring_root/'room_preview',evidence,b)
        publish.run_module(ctx,'compare',[],log_name='compare')
        return 'success'
    qc=ModuleType('litereality_agent.pipeline.room_qc');qc.publish=publish
    ops=ModuleType('litereality_agent.room_ops');ops.api=api
    for mod in (publish,api,qc,ops):monkeypatch.setitem(sys.modules,mod.__name__,mod)
    return SimpleNamespace(run=upstream),calls,publish,api


def test_publish_timeout_resume_skips_compile_bake_and_completed_review_groups(scene,monkeypatch):
    ctx,_=scene;b=Many(ctx.authored_room);b.build(ctx,ctx.authoring_root/'room_preview');b.calls=[]
    impl,calls,publish,api=runtime(monkeypatch,ctx,b)
    original=(publish.run_module,publish.tempfile,api.bake_room)
    monkeypatch.setattr(ac,'Backend',lambda:b)
    b.fail={'O030','O035'}
    with pytest.raises(TimeoutError):ac.run_publish(ctx,{'_operation_id':'publish'},impl)
    assert calls==['collision','quality','compile','bake']
    assert (publish.run_module,publish.tempfile,api.bake_room)==original
    b.fail=set();b.reviewed=[]
    assert ac.run_publish(ctx,{'_operation_id':'publish'},impl)=='success'
    assert calls==['collision','quality','compile','bake','compare']
    assert [x[0] for x in b.reviewed]==['O030','O035']
    assert len(b.calls)==1  # first render only


def test_publish_resume_rejects_changed_baked_export(scene,monkeypatch):
    ctx,_=scene;b=Many(ctx.authored_room);b.build(ctx,ctx.authoring_root/'room_preview')
    impl,calls,_,_=runtime(monkeypatch,ctx,b);monkeypatch.setattr(ac,'Backend',lambda:b)
    b.fail={'O030','O035'}
    with pytest.raises(TimeoutError):ac.run_publish(ctx,{'_operation_id':'publish'},impl)
    (ctx.authoring_root/'room_preview/Room.glb').write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'):ac.run_publish(ctx,{'_operation_id':'publish'},impl)
    assert calls==['collision','quality','compile','bake']


def test_real_pinned_publish_function_resumes_final_review(scene,monkeypatch):
    """Execute shipped upstream publish source; replace only model/GPU boundaries."""
    import tarfile
    ctx,_=scene;b=Many(ctx.authored_room);b.build(ctx,ctx.authoring_root/'room_preview');b.calls=[]
    ctx.preview_dir=ctx.authoring_root/'room_preview';ctx.scene_dir=ctx.authoring_root.parent;ctx.scan='fixture'
    impl,calls,publish,api=runtime(monkeypatch,ctx,b)
    boundary=publish.run_module
    context_module=ModuleType('litereality_agent.pipeline.context');context_module.RunContext=SimpleNamespace
    result_module=ModuleType('litereality_agent.pipeline.result')
    result_module.StageStatus=SimpleNamespace(FAILED='failed',COMPLETED='completed')
    class Result:
        def __init__(self,name,status,**kw):self.status=status;self.__dict__.update(kw)
    result_module.StageResult=Result
    support=ModuleType('litereality_agent.pipeline.support');support.run_module=boundary
    acceptance=ModuleType('litereality_agent.pipeline.realism_authoring.acceptance')
    acceptance.assess=lambda c,p,e:ac.assess(c,p,e,b)
    for mod in (context_module,result_module,support,acceptance):monkeypatch.setitem(sys.modules,mod.__name__,mod)
    archive=Path(__file__).resolve().parents[2]/'litereality-full-v0.3.1/app.tar.gz'
    with tarfile.open(archive) as t:
        member=next(m for m in t.getmembers() if m.name.endswith('/pipeline/room_qc/publish/__init__.py'))
        source=t.extractfile(member).read()
    exec(compile(source,str(archive)+'!publish/__init__.py','exec'),publish.__dict__)
    monkeypatch.setattr(ac,'Backend',lambda:b)
    b.fail={'O030','O035'}
    with pytest.raises(TimeoutError):ac.run_publish(ctx,{'_operation_id':'pub','compare_frames':6},publish)
    assert calls==['publish_collision','publish_quality','publish_compile','bake']
    b.fail=set();b.reviewed=[]
    result=ac.run_publish(ctx,{'_operation_id':'pub','compare_frames':6},publish)
    assert result.status=='completed'
    assert calls==['publish_collision','publish_quality','publish_compile','bake','publish_compare']
    assert [g[0] for g in b.reviewed]==['O030','O035']
    assert json.loads((ctx.preview_dir/'acceptance.json').read_text())['accepted'] is True
