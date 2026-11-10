import pytest
from test_session_edits import setup, draft, scene
from conversation import validate
from room_agent.storage import digest


def proposal(run,operation,operation_run_id=''):
    return {'kind':'plan','reply':'Review the current scene. Confirm to run.','questions':[],
            'plan':{'target_run_id':run,'change':'Review only','objects':[],
                    'preserve':['Keep scene unchanged'],'steps':['Build','Review'],
                    'expected_outputs':['Review report'],'target_ids':[],'finding_ids':[],
                    'operation':operation,'operation_run_id':operation_run_id}}


def workflow_draft(c,run,operation,target=''):
    c.planner=lambda _:proposal(run,operation,target)
    return draft(c,run,'Review or resume')


def test_review_plan_routes_only_when_available():
    facts={'run_id':'r','workflow_actions':{'scene_review':True,'resume_operation':None}}
    p=validate(proposal('r','scene_review'),facts)['plan']
    assert p['operation']=='scene_review' and p['contract_version']=='conversation-workflow/1'
    with pytest.raises(ValueError):validate(proposal('r','scene_review'),{**facts,'workflow_actions':{}})
    with pytest.raises(ValueError):validate(proposal('r','resume_operation','other'),facts)


def test_review_confirm_creates_one_internal_review_without_replacing_scene(tmp_path,scene):
    s,r,q,m,c=setup(tmp_path,scene)
    data=workflow_draft(c,r,'scene_review');first=m.confirm(r,data);second=m.confirm(r,data)
    child=first['active']['run_id'];req,_=s._load(child)
    assert req['operation']=='saved_scene_review' and req['max_batches']==0
    assert req['parent_run_id']==r and req['session_id']==r
    assert q.calls==[child] and second['active']['run_id']==child
    s.execute(child);q.rows.clear()
    state=m.read(r)
    assert state['current_run_id']==r and state['active']['outcome']=='reviewed'
    assert len(m.workspace())==1
    assert m.context(r)['latest_review']['run_id']==child


def test_resume_plan_binds_existing_operation(tmp_path,scene):
    s,r,q,m,c=setup(tmp_path,scene)
    m.confirm(r,draft(c,r));child=q.calls[0];q.rows[child]={'queue_status':'interrupted'}
    data=workflow_draft(c,r,'resume_operation',child)
    assert c.read(r)['turns'][-1]['result']['plan']['operation_run_id']==child
    assert len(s.list_runs())==2


def real_queue(s,m):
    from room_agent.task_queue import TaskQueue
    from test_task_queue import Worker
    q=TaskQueue(s.root,s,Worker(),autostart=False);m.worker=q
    return q


def test_resume_confirmation_reuses_checkpoint_after_network_failure(tmp_path,scene):
    from room_agent.scene_executor import SceneExecutor
    from room_agent.saved_scene import tree_hashes
    s,r,_,m,c=setup(tmp_path,scene);q=real_queue(s,m);calls=[]
    class Review(SceneExecutor):
        fail=True
        def _external(self,name,state,key):
            calls.append(name)
            if name=='build':return {'room':state['active_source']},[]
            if name=='geometry':return {'geometry_pass':True},[]
            if name=='review':
                if self.fail:raise RuntimeError('network failure')
                return {'quality_status':'not_passed','score':7,'issues':[{'priority':'lighting','defect':'dark'}]},[]
            return {},[]
    try:
        before=tree_hashes(s.root/'runs'/r/'work');s.scene_executor_type=Review
        m.confirm(r,workflow_draft(c,r,'scene_review'));child=m.read(r)['active']['run_id'];q.pump()
        with pytest.raises(RuntimeError,match='network failure'):s.execute(child)
        q.worker.busy=False;q.status(child)
        data=workflow_draft(c,r,'resume_operation',child)
        m.confirm(r,data);m.confirm(r,data);q.pump();Review.fail=False;s.execute(child)
        q.worker.busy=False;q.status(child)
        assert calls.count('build')==1 and calls.count('review')==2
        assert m.read(r)['active']['outcome']=='reviewed'
        assert m.read(r)['current_run_id']==r
        assert m.project(r)['current_result_status']['quality_status']=='not_passed'
        assert m.project(r)['current_result_status']['score']==7
        assert tree_hashes(s.root/'runs'/r/'work')==before
        m.confirm(r,data);assert q.status(child)['queue_status']=='completed'
        assert len(s.list_runs())==2
    finally:q.close()


def test_resume_lost_reply_and_fast_failure_is_not_resubmitted(tmp_path,scene,monkeypatch):
    s,r,_,m,c=setup(tmp_path,scene);q=real_queue(s,m)
    try:
        m.confirm(r,workflow_draft(c,r,'scene_review'));child=m.read(r)['active']['run_id'];q.cancel(child)
        data=workflow_draft(c,r,'resume_operation',child);original=q.start
        def lose_reply(run,**kw):
            original(run,**kw);q.cancel(run);raise RuntimeError('lost reply')
        monkeypatch.setattr(q,'start',lose_reply)
        with pytest.raises(RuntimeError,match='lost reply'):m.confirm(r,data)
        monkeypatch.setattr(q,'start',original)
        from session_edits import SessionEdits
        again=SessionEdits(s,q);again.conversation=c
        again.confirm(r,data)
        assert q.status(child)['queue_status']=='cancelled'
        assert len(s.list_runs())==2
    finally:q.close()


def test_review_latest_accepted_edit_not_original_root(tmp_path,scene):
    from room_agent.edit_executor import EditExecutor
    s,r,q,m,c=setup(tmp_path,scene)
    class Edit(EditExecutor):
        def _external(self,name,state,key):
            if name=='build':return {'room':state['active_source']},[]
            if name=='review':return {'edit_status':'passed','edit_score':9},[]
            if name=='finalize':return {'technical_export_verified':True},[]
            return {},[]
    s.edit_executor_type=Edit;m.confirm(r,draft(c,r));edited=q.calls[-1];q.rows.clear();s.execute(edited)
    assert m.read(r)['current_run_id']==edited
    m.confirm(r,workflow_draft(c,r,'scene_review'));review=q.calls[-1]
    req,_=s._load(review);assert req['parent_run_id']==edited
    assert str(s.root/'runs'/edited) in req['source']
    s.execute(review);q.rows.clear()
    assert m.read(r)['current_run_id']==edited
    assert m.context(r)['latest_review']['source_run_id']==edited


def test_reserved_resume_with_changed_checkpoint_requires_new_confirmation(tmp_path,scene,monkeypatch):
    from room_agent.storage import Journal
    s,r,_,m,c=setup(tmp_path,scene);q=real_queue(s,m)
    try:
        m.confirm(r,workflow_draft(c,r,'scene_review'));child=m.read(r)['active']['run_id'];q.cancel(child)
        data=workflow_draft(c,r,'resume_operation',child);original=q.start
        monkeypatch.setattr(q,'start',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('before queue')))
        with pytest.raises(RuntimeError,match='before queue'):m.confirm(r,data)
        Journal(s.root/'runs'/child,child).emit('external_execution_changed')
        monkeypatch.setattr(q,'start',original)
        with pytest.raises(RuntimeError,match='changed'):m.confirm(r,data)
        assert q.status(child)['queue_status']=='cancelled'
    finally:q.close()


def test_fresh_review_evidence_is_bound_to_review_and_current_source(tmp_path,scene):
    from pathlib import Path
    from room_agent.scene_executor import SceneExecutor
    from room_agent.storage import atomic_json
    from scene_evidence import verify_bound
    s,r,q,m,c=setup(tmp_path,scene)
    class Review(SceneExecutor):
        def _external(self,name,state,key):
            if name=='build':return {'room':state['active_source']},[]
            if name=='geometry':return {'geometry_pass':True},[]
            if name=='render':
                img=self.work/'pair_00015.png';img.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
                return {'pairs':[str(img)]},[img]
            if name=='review':return {'quality_status':'not_passed','score':7,'issues':[{'priority':'lighting','object_ids':['Desk'],'defect':'dark in pair_00015.png'}]},[]
            return {},[]
    s.scene_executor_type=Review;m.confirm(r,workflow_draft(c,r,'scene_review'));child=q.calls[-1];q.rows.clear();s.execute(child)
    facts=c.facts(r,s._load(r)[0]);assert facts['finding_catalog']['records'][0]['source_run_id']==child
    reader=c._reader(facts);assert reader.run_id==child
    row=reader.catalog()['images'][0]
    records,_=reader.read({'files':[],'queries':[],'images':[row['id']]})
    verify_bound(s,r,records)
    from room_agent.revisions import selected_source
    Path(selected_source(s,r)['source'],'Room.py').write_text('changed')
    with pytest.raises(ValueError,match='changed'):verify_bound(s,r,records)


def test_interrupted_root_without_editable_scene_can_resume_same_graph(tmp_path,scene):
    from room_agent.scene_executor import SceneExecutor
    s,_,_,m,c=setup(tmp_path,scene);r=s.create_review(scene,max_batches=0);calls=[]
    class Review(SceneExecutor):
        fail=True
        def _external(self,name,state,key):
            calls.append(name)
            if name=='preflight' and self.fail:raise RuntimeError('probe failed')
            if name=='build':return {'room':state['active_source']},[]
            if name=='geometry':return {'geometry_pass':False,'quality_status':'not_passed','issues':[]},[]
            return {},[]
    s.scene_executor_type=Review
    with pytest.raises(RuntimeError,match='probe failed'):s.execute(r)
    q=real_queue(s,m)
    try:
        facts=c.facts(r,s._load(r)[0]);assert facts['editable'] is False
        assert facts['workflow_actions']['resume_operation']['run_id']==r
        before=len(s.list_runs());data=workflow_draft(c,r,'resume_operation',r)
        m.confirm(r,data);q.pump();Review.fail=False;s.execute(r);q.worker.busy=False;q.status(r)
        assert len(s.list_runs())==before and m.read(r)['current_run_id']==r
        assert len([e for e in s.events(r) if e['kind']=='node_completed' and e.get('node')=='snapshot'])==1
        assert m.context(r)['workflow_actions']['resume_operation'] is None
    finally:q.close()


def test_workflow_draft_rejected_after_source_change_or_operation_resolved(tmp_path,scene):
    from pathlib import Path
    from room_agent.revisions import selected_source
    s,r,q,m,c=setup(tmp_path,scene);data=workflow_draft(c,r,'scene_review')
    source=Path(selected_source(s,r)['source'],'Room.py');original=source.read_text();source.write_text(original+'\n# changed')
    with pytest.raises(RuntimeError,match='changed'):m.confirm(r,data)
    assert not q.calls
    source.write_text(original);m.confirm(r,workflow_draft(c,r,'scene_review'));child=q.calls[-1];q.rows.clear()
    data=workflow_draft(c,r,'resume_operation',child);s.execute(child)
    with pytest.raises(RuntimeError,match='changed'):m.confirm(r,data)


def reviewed_fixture(tmp_path,scene):
    from room_agent.scene_executor import SceneExecutor
    s,r,q,m,c=setup(tmp_path,scene)
    class Review(SceneExecutor):
        def _external(self,name,state,key):
            if name=='build':return {'room':state['active_source']},[]
            if name=='geometry':return {'geometry_pass':True},[]
            if name=='review':return {'quality_status':'not_passed','score':7,'issues':[{'priority':'lighting','object_ids':['Desk'],'defect':'fresh report'}]},[]
            return {},[]
    s.scene_executor_type=Review;m.confirm(r,workflow_draft(c,r,'scene_review'));review=q.calls[-1];q.rows.clear();s.execute(review);m.read(r)
    return s,r,q,m,c,review


def test_fresh_review_is_preserved_as_next_edits_historical_baseline(tmp_path,scene):
    from test_conversation import reply
    s,r,q,m,c,review=reviewed_fixture(tmp_path,scene);c.planner=lambda _:reply(r,'plan')
    m.confirm(r,draft(c,r));req,_=s._load(q.calls[-1])
    assert req['parent_score']==7 and req['parent_issues'][0]['defect']=='fresh report'
    assert req['parent_review_run_id']==review


def test_modified_source_or_capture_invalidates_fresh_review(tmp_path,scene):
    from pathlib import Path
    s,r,q,m,c,review=reviewed_fixture(tmp_path,scene)
    assert m.project(r)['current_result_status']['score']==7
    req,_=s._load(review);file=Path(req['capture'])/next(iter(req['capture_files']))
    file.write_bytes(b'changed')
    assert m.context(r)['latest_review']['stale'] is True
    assert m.project(r)['current_result_status']['quality_status']=='unreviewed'
    assert m.project(r)['score'] is None


def test_passed_review_projects_new_comparisons_but_keeps_scene_downloads(tmp_path,scene,monkeypatch):
    from session_edits import session_presentation
    import display
    s,r,q,m,c,review=reviewed_fixture(tmp_path,scene)
    orig=m.raw_status;m.raw_status=lambda run:{**orig(run),'quality_status':'passed'} if run==review else orig(run)
    monkeypatch.setattr(display,'build_presentation',lambda root,status:{'comparisons':[{'pairs':[{'path':root.name+'.png'}]}],
        'reports':[{'path':root.name+'.json'}],'files':[{'path':root.name+'.blend'}],'diagnostics':{}})
    result=session_presentation(m,r)
    assert result['comparisons'][0]['pairs'][0]['run_id']==review
    assert result['reports'][0]['run_id']==review
    assert result['files'][0]['run_id']==r


from test_scan_graph import setup as scan_setup


def test_conversational_resume_of_old_full_scan_keeps_completed_reconstruction(scan_setup,monkeypatch):
    import json
    from pathlib import Path
    from room_agent import scan_executor
    from session_edits import SessionEdits
    from conversation import Conversation
    s,raw,runner,project=scan_setup
    policy=json.loads((Path(__file__).resolve().parents[1]/'engine_compatibility.json').read_text())
    current=scan_executor.engine_hash
    monkeypatch.setattr(scan_executor,'engine_hash',lambda:policy['engines']['full_scan']['previous'][0])
    r=s.create_full(raw,'room',project)
    monkeypatch.setattr(scan_executor,'engine_hash',current)
    runner.fail='author'
    with pytest.raises(RuntimeError):s.execute(r)
    m=SessionEdits(s,None);q=real_queue(s,m)
    c=Conversation(s,status_reader=m.project,context_reader=m.context);m.conversation=c
    try:
        data=workflow_draft(c,r,'resume_operation',r);m.confirm(r,data);q.pump()
        runner.fail=None;s.execute(r);q.worker.busy=False;q.status(r)
        assert runner.calls.count('ingest')==1 and runner.calls.count('reconstruct')==1
        assert runner.calls.count('author')==2 and len(s.list_runs())==1
        assert s.show(r)['execution_status']=='completed'
        m.confirm(r,data);assert q.status(r)['queue_status']=='completed'
    finally:q.close()


def test_discarded_edit_still_shows_retained_versions_latest_review(tmp_path,scene):
    from test_conversation import reply
    s,r,q,m,c,review=reviewed_fixture(tmp_path,scene);c.planner=lambda _:reply(r,'plan')
    m.confirm(r,draft(c,r));edit=q.calls[-1];q.cancel(edit)
    m.action(r,{'operation_id':edit,'action':'discard'})
    status=m.project(r)
    assert status['score']==7 and status['issues'][0]['defect']=='fresh report'
    assert status['execution_run_id']==r
