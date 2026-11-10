"""Finite acceptance controller: real persistence, fake only GPU/model boundaries."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from room_agent import author_closeout as ac


def test_frame_selection_covers_whole_trajectory_and_keeps_previous():
    frames=list(range(180))
    first=ac.select_frames(frames,6)
    expanded=ac.select_frames(frames,24,first)
    assert first[0]==0 and first[-1]==179
    assert len(expanded)==24 and set(first)<=set(expanded)


def test_strict_review_requires_every_object_and_no_missing_evidence():
    good={'pass':True,'score':8,'all_objects_reviewed':True,'issues':[],
          'objects':[{'id':'Bed','observed':True},{'id':'Table','observed':True}]}
    assert ac.normalize_review(good,['Bed','Table'])['pass']
    good['objects'].pop()
    assert not ac.normalize_review(good,['Bed','Table'])['pass']
    assert ac.normalize_review(good,['Bed','Table'])['issues'][0]['kind']=='evidence'
    for score in (True,float('nan')):
        good['score']=score
        with pytest.raises(RuntimeError):ac.normalize_review(good,['Bed'])
    good['score']=7.9
    assert not ac.normalize_review(good,['Bed'])['pass']


def test_target_plan_excludes_unknown_and_evidence_and_bounds_batch():
    issues=[{'kind':kind,'objects':['O'+str(i)],'detail':'issue','priority':priority}
            for i,(kind,priority) in enumerate([('evidence','support'),('appearance','small_details'),
              ('appearance','lighting'),('geometry','support'),('appearance','large_appearance'),('unknown','support')])]
    chosen=ac.repair_targets(issues)
    assert len(chosen)==3
    assert [x['priority'] for x in chosen]==['support','lighting','large_appearance']


class Backend:
    def __init__(self,room):self.room=room;self.calls=[];self.score=5;self.evidence=False;self.stop=None;self.error=False
    def fingerprint(self,ctx,preview):return {'source':(self.room/'Room.py').read_text()}
    def preserved(self,ctx):return True
    def build(self,ctx,preview):
        self.calls.append('build');preview.mkdir(exist_ok=True)
        for n in ('Room.glb','Room.blend'): (preview/n).write_text('build')
        (preview/'room_layout.json').write_text(json.dumps({'objects':[{'id':'Bed'}]}))
    def geometry(self,ctx,preview):return {'pass':True}
    def render(self,ctx,evidence,frames):
        self.calls.append(('render',list(frames)))
        evidence.mkdir(parents=True,exist_ok=True)
        paths=[evidence/('pair_'+str(f)+'.png') for f in frames]
        for path in paths:path.write_bytes(b'png')
        return list(map(str,paths))
    def review(self,images,objects):
        self.calls.append('review')
        return {'pass':self.score>=8 and not self.evidence,'score':self.score,'all_objects_reviewed':not self.evidence,
                'objects':[{'id':'Bed','observed':not self.evidence}],
                'issues':[] if self.score>=8 else [{'kind':'appearance','objects':['Bed'],'priority':'large_appearance','detail':'wrong shape'}]}
    def repair(self,ctx,path,options):
        self.calls.append('repair');(self.room/'Room.py').write_text((self.room/'Room.py').read_text()+'\n# saved')
        if self.stop:raise RuntimeError(self.stop)
        if self.error:raise RuntimeError('network disconnected')


@pytest.fixture
def scene(tmp_path):
    room=tmp_path/'room';room.mkdir();(room/'Room.py').write_text('# original')
    capture=tmp_path/'capture';capture.mkdir()
    for i in range(32):
        (capture/f'frame_{i:05d}.jpg').write_bytes(b'')
        (capture/f'frame_{i:05d}.json').write_text('{}')
    return SimpleNamespace(authored_room=room,seed_room=room,authoring_root=tmp_path,capture_dir=capture),Backend(room)


def test_cap_rebuilds_saved_edits_then_stops_without_claiming_pass(scene):
    ctx,b=scene
    r=ac.run(ctx,{},backend=b)
    assert r['closeout']['batches']==0 and not r['accepted']
    b.stop='repair incomplete: step budget 40 reached'
    r=ac.run(ctx,{},repair=True,backend=b)
    assert r['closeout']['batches']==1
    assert r['closeout']['repair_termination']=='step_budget'
    assert b.calls[-1]=='review' and b.calls.count('repair')==1
    assert r['fingerprint']['source'].endswith('# saved')
    assert not r['accepted']
    r=ac.run(ctx,{},repair=True,backend=b)
    assert r['closeout']['stop_reason']=='no_progress'
    with pytest.raises(ValueError,match='not allowed'):ac.run(ctx,{},repair=True,backend=b)
    assert b.calls.count('repair')==2


def test_missing_evidence_expands_before_stopping_and_never_edits(scene):
    ctx,b=scene;b.evidence=True;b.score=8
    r=ac.run(ctx,{},backend=b)
    renders=[x[1] for x in b.calls if isinstance(x,tuple)]
    assert [len(x) for x in renders]==[6,12,24]
    assert r['closeout']['stop_reason']=='evidence_insufficient'
    assert not r['accepted'] and 'repair' not in b.calls


def test_network_failure_is_error_and_resume_reviews_without_repair_again(scene):
    ctx,b=scene;ac.run(ctx,{},backend=b);b.error=True
    with pytest.raises(RuntimeError,match='network'):ac.run(ctx,{},repair=True,backend=b)
    b.error=False
    r=ac.run(ctx,{},repair=True,backend=b)
    assert b.calls.count('repair')==1 and r['closeout']['batches']==1
    assert r['fingerprint']['source'].endswith('# saved')


def test_accepted_result_keeps_original_gate_and_best_snapshot(scene):
    ctx,b=scene;ac.run(ctx,{},backend=b);b.score=8
    r=ac.run(ctx,{},repair=True,backend=b)
    assert r['accepted'] and r['schema']=='support-first/2'
    assert Path(r['closeout']['best_candidate']).is_dir()
    assert r['closeout']['stop_reason']=='quality_passed'


def test_crash_after_report_before_graph_receipt_does_not_repeat_repair(scene):
    ctx,b=scene
    ac.run(ctx,{'_operation_id':'initial'},backend=b)
    r=ac.run(ctx,{'_operation_id':'approved-batch-1'},repair=True,backend=b)
    # The process died after durable controller commit but before graph receipt.
    retry=ac.run(ctx,{'_operation_id':'approved-batch-1'},repair=True,backend=b)
    assert retry==r and b.calls.count('repair')==1
    assert retry['closeout']['batches']==1


def test_review_cannot_target_invented_object_ids():
    raw={'pass':False,'score':5,'all_objects_reviewed':True,'objects':[{'id':'Bed','observed':True}],
         'issues':[{'kind':'appearance','objects':['ImaginedDesk'],'priority':'large_appearance','detail':'invented'}]}
    r=ac.normalize_review(raw,['Bed'])
    assert not ac.repair_targets(r['issues'])


def test_total_batches_survive_restarts_even_if_score_keeps_improving(scene):
    ctx,b=scene;ac.run(ctx,{'_operation_id':'initial'},backend=b)
    for index in range(1,4):
        b.score+=.5
        report=ac.run(ctx,{'_operation_id':'batch-'+str(index)},repair=True,backend=b)
        assert report['closeout']['batches']==index
    assert report['closeout']['stop_reason']=='repair_budget_exhausted'
    with pytest.raises(ValueError,match='not allowed'):ac.run(ctx,{'_operation_id':'batch-4'},repair=True,backend=b)
    assert b.calls.count('repair')==3


def test_timeout_still_reviews_saved_edits(scene):
    ctx,b=scene;ac.run(ctx,{},backend=b);b.stop='repair incomplete: wall time budget 600s reached'
    report=ac.run(ctx,{},repair=True,backend=b)
    assert report['closeout']['repair_termination']=='timeout'
    assert report['fingerprint']['source'].endswith('# saved')
    assert b.calls[-1]=='review'


def test_source_change_during_review_and_unavailable_check_are_errors(scene):
    ctx,b=scene;review=b.review
    def mutating(images,objects):
        (ctx.authored_room/'Room.py').write_text('# unexpected change')
        return review(images,objects)
    b.review=mutating
    with pytest.raises(RuntimeError,match='changed during'):ac.run(ctx,{},backend=b)
    assert (ctx.authoring_root/'closeout/control.json').exists()
    assert not (ctx.authoring_root/'room_preview/author_acceptance.json').exists()
    (ctx.authored_room/'Room.py').write_text('# original')
    b.review=review;b.geometry=lambda *args:{'pass':False,'failing':[{'kind':'mesh_check_unavailable'}]}
    with pytest.raises(RuntimeError,match='check unavailable'):ac.run(ctx,{},backend=b)


def test_geometry_failure_targets_only_blocking_findings(scene):
    ctx,b=scene
    b.geometry=lambda *args:{'ok':True,'pass':False,'failing':[{'kind':'wall_clash','id':'Bed','detail':'penetrates wall'}],
                            'overlap':[{'id':'NotBlocking','kind':'aabb_overlap'}]}
    r=ac.run(ctx,{},backend=b)
    assert not r['accepted'] and not r['support_pass']
    assert ac.repair_targets(r['visual_review']['issues'])[0]['objects']==['Bed']
    assert not any(isinstance(c,tuple) for c in b.calls)


def test_cached_operation_rejects_changed_source_instead_of_repairing_again(scene):
    ctx,b=scene;ac.run(ctx,{'_operation_id':'initial'},backend=b)
    ac.run(ctx,{'_operation_id':'batch'},repair=True,backend=b)
    (ctx.authored_room/'Room.py').write_text('# external edit')
    with pytest.raises(ValueError,match='source/build changed'):ac.run(ctx,{'_operation_id':'batch'},repair=True,backend=b)
    assert b.calls.count('repair')==1


def test_publish_adapter_persists_assessment_for_exact_revision(scene,monkeypatch):
    import sys
    from types import ModuleType
    ctx,b=scene
    acceptance=ModuleType('litereality_agent.pipeline.realism_authoring.acceptance')
    acceptance.finish=lambda *args:None;acceptance.assess=lambda *args:None
    monkeypatch.setitem(sys.modules,acceptance.__name__,acceptance)
    # Supply parent for lazy import without GPU dependencies.
    parent=ModuleType('litereality_agent.pipeline.realism_authoring');parent.acceptance=acceptance
    monkeypatch.setitem(sys.modules,parent.__name__,parent)
    monkeypatch.setattr(ac,'Backend',lambda:b)
    report={'schema':'support-first/2','accepted':False,'status':'needs_repair'}
    monkeypatch.setattr(ac,'assess',lambda *args:report)
    evidence=ctx.authoring_root/'publish-review-new'
    old_assess=acceptance.assess
    with ac.install():assert acceptance.assess(ctx,ctx.authoring_root/'room_preview',evidence)==report
    assert json.loads((evidence/'assessment.json').read_text())==report
    assert acceptance.assess is old_assess
