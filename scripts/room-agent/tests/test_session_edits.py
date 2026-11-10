import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ui'))
from test_scene import scene
from test_revisions import parent_run
from test_conversation import reply
from conversation import Conversation
from session_edits import SessionEdits
from room_agent.storage import read_json, atomic_json, digest

class Queue:
    def __init__(self):self.calls=[];self.rows={}
    def status(self,run):return self.rows.get(run,{})
    def start(self,run,action=None):
        if self.rows.get(run,{}).get('queue_status')!='queued':self.calls.append(run)
        self.rows[run]={'queue_status':'queued'}
        return {'run_id':run,**self.rows[run]}
    def cancel(self,run):self.rows[run]={'queue_status':'cancelled'}

def setup(tmp_path,scene):
    s,root=parent_run(tmp_path,scene);q=Queue();m=SessionEdits(s,q)
    c=Conversation(s,planner=lambda p:reply(root,'plan'),status_reader=m.project,context_reader=m.context)
    m.conversation=c
    return s,root,q,m,c

def draft(c,root,msg='Make floor roughness 0.9'):
    state=c.read(root);turn=c.send(root,{'request_id':'m'+str(state['revision']+1),'revision':state['revision'],'message':msg})['turns'][-1]
    assert turn['status']=='completed',turn
    return {'revision':c.read(root)['revision'],'plan_id':turn['result']['plan']['plan_id']}

def test_confirm_once_same_session_parent_untouched(tmp_path,scene):
    s,r,q,m,c=setup(tmp_path,scene);before=s.show(r);data=draft(c,r)
    first=m.confirm(r,data);second=m.confirm(r,data)
    assert first['active']['run_id']==second['active']['run_id']
    assert len(q.calls)==1 and s.show(r)==before
    req,_=s._load(q.calls[0]);assert req['session_id']==r and req['parent_run_id']==r
    assert '颜色' in req['instruction'] and '主地板' in req['instruction']
    assert m.project(r)['run_id']==r and m.project(r)['execution_run_id']==q.calls[0]
    assert [row['run_id'] for row in m.workspace()]==[r]
    assert m.read(r)['current_run_id']==r

def test_stale_plan_and_edited_files_rejected(tmp_path,scene):
    s,r,q,m,c=setup(tmp_path,scene);data=draft(c,r);draft(c,r,'Actually different')
    with pytest.raises(RuntimeError):m.confirm(r,data)
    data=draft(c,r)
    from room_agent.revisions import selected_source
    src=Path(selected_source(s,r)['source'])/'Room.py';src.write_text(src.read_text()+'\n# changed\n')
    with pytest.raises(RuntimeError,match='changed'):m.confirm(r,data)
    assert not q.calls

def test_restart_after_publish_reuses_reserved_id(tmp_path,scene,monkeypatch):
    s,r,q,m,c=setup(tmp_path,scene);data=draft(c,r);original=q.start
    monkeypatch.setattr(q,'start',lambda *a:(_ for _ in ()).throw(RuntimeError('offline')))
    with pytest.raises(RuntimeError,match='offline'):m.confirm(r,data)
    child=m.read(r)['active']['run_id'];assert s._load(child)
    monkeypatch.setattr(q,'start',original)
    again=SessionEdits(s,q);again.conversation=c
    assert again.confirm(r,data)['active']['run_id']==child
    assert len(s.list_runs())==2 and q.calls==[child]

def test_only_verified_pass_promotes_and_new_plan_uses_latest_source(tmp_path,scene):
    s,r,q,m,c=setup(tmp_path,scene);m.confirm(r,draft(c,r));child=q.calls[0];q.rows.clear()
    original=m.raw_status
    m.raw_status=lambda rid: ({'run_id':child,'execution_status':'completed','edit_status':'not_passed','technical_export_verified':True,'quality_status':'not_passed'} if rid==child else original(rid))
    assert m.read(r)['current_run_id']==r
    with pytest.raises(RuntimeError):m.confirm(r,draft(c,r))
    m.raw_status=lambda rid: ({'run_id':child,'execution_status':'completed','edit_status':'passed','technical_export_verified':True,'quality_status':'not_passed'} if rid==child else original(rid))
    assert m.read(r)['current_run_id']==child

def test_busy_discard_and_target_guard(tmp_path,scene):
    s,r,q,m,c=setup(tmp_path,scene);m.confirm(r,draft(c,r));child=q.calls[0]
    with pytest.raises(RuntimeError):m.action(r,{'operation_id':'wrong','action':'resume'})
    with pytest.raises(RuntimeError):m.action(r,{'operation_id':child,'action':'discard'})
    m.action(r,{'operation_id':child,'action':'cancel'})
    m.action(r,{'operation_id':child,'action':'discard'})
    assert m.read(r)['active']['outcome']=='discarded' and m.read(r)['current_run_id']==r
    m.confirm(r,draft(c,r,'new request'))
    assert len(s.list_runs())==3 and len(m.workspace())==1

def test_real_graph_failure_resume_reuses_edit_and_parent_then_next_edit(tmp_path,scene):
    from room_agent.edit_executor import EditExecutor
    from room_agent.saved_scene import tree_hashes
    s,r,q,m,c=setup(tmp_path,scene);before=tree_hashes(s.root/'runs'/r/'work');calls=[]
    class Adapter(EditExecutor):
        fail=True
        def _external(self,name,state,key):
            calls.append(name)
            if name=='build':return {'room':state['active_source']},[]
            if name=='review':
                if self.fail:raise RuntimeError('simulated network timeout')
                return {'edit_status':'passed','edit_score':9,'quality_status':'not_passed'},[]
            if name=='finalize':return {'technical_export_verified':True,'completion_reason':'edit_accepted'},[]
            return {},[]
    s.edit_executor_type=Adapter;m.confirm(r,draft(c,r));child=q.calls[-1];q.rows.clear()
    with pytest.raises(RuntimeError,match='network timeout'):s.execute(child)
    assert m.read(r)['current_run_id']==r
    m.action(r,{'operation_id':child,'action':'resume'});q.rows.clear();Adapter.fail=False;s.execute(child)
    assert calls.count('edit')==1 and calls.count('review')==2
    assert m.read(r)['current_run_id']==child
    assert tree_hashes(s.root/'runs'/r/'work')==before
    data=draft(c,r,'Change the same floor again');m.confirm(r,data)
    assert s._load(q.calls[-1])[0]['parent_run_id']==child
    assert len(m.workspace())==1 and not {'ingest','reconstruct'}&set(calls)


def test_pending_approval_no_promotion_and_finalization_wait(tmp_path,scene):
    s,r,q,m,c=setup(tmp_path,scene);m.confirm(r,draft(c,r));child=q.calls[-1];q.rows.clear();orig=m.raw_status
    for fields in [{'edit_status':'passed','technical_export_verified':False}, {'edit_status':'not_passed','technical_export_verified':True},{'edit_status':'passed','technical_export_verified':True,'worker_finalizing':True}]:
        m.raw_status=lambda rid: ({'run_id':child,'execution_status':'completed',**fields} if rid==child else orig(rid))
        assert m.read(r)['current_run_id']==r


def test_confirm_lock_and_changed_plan_during_model_call(tmp_path,scene):
    from room_agent.storage import room_lock
    s,r,q,m,c=setup(tmp_path,scene);data=draft(c,r)
    with room_lock(s.root,'conversation-'+r):
        with pytest.raises(RuntimeError,match='still processing'):m.confirm(r,data)
    assert q.calls==[]


def test_no_confirm_for_old_pre_session_draft(tmp_path,scene):
    s,r,q,m,c=setup(tmp_path,scene)
    old=Conversation(s,planner=lambda _:reply(r,'plan'))
    data=draft(old,r)
    with pytest.raises(RuntimeError,match='changed'):m.confirm(r,data)
    assert not q.calls

def test_reserve_before_publish_and_explicit_resume_recovers(tmp_path,scene,monkeypatch):
    s,r,q,m,c=setup(tmp_path,scene);data=draft(c,r);original=m._publish
    monkeypatch.setattr(m,'_publish',lambda *args:(_ for _ in ()).throw(RuntimeError('power loss')))
    with pytest.raises(RuntimeError,match='power loss'):m.confirm(r,data)
    child=m.read(r)['active']['run_id'];assert not (s.root/'runs'/child).exists()
    monkeypatch.setattr(m,'_publish',original)
    m.action(r,{'operation_id':child,'action':'resume'})
    assert q.calls==[child] and len(m.workspace())==1


def test_queued_receipt_recovery_no_second_enqueue(tmp_path,scene,monkeypatch):
    s,r,q,m,c=setup(tmp_path,scene);data=draft(c,r);original=q.start
    def accepted_then_lost(run):original(run);raise RuntimeError('reply lost')
    monkeypatch.setattr(q,'start',accepted_then_lost)
    with pytest.raises(RuntimeError,match='reply lost'):m.confirm(r,data)
    monkeypatch.setattr(q,'start',original)
    m.confirm(r,data);assert len(q.calls)==1
    assert m.read(r)['active']['submitted'] is True


def test_atomic_promotion_restores_on_new_manager(tmp_path,scene):
    s,r,q,m,c=setup(tmp_path,scene);m.confirm(r,draft(c,r));child=q.calls[-1];q.rows.clear();original=m.raw_status
    m.raw_status=lambda x: ({'run_id':child,'execution_status':'completed','edit_status':'passed','technical_export_verified':True} if x==child else original(x))
    assert m.read(r)['current_run_id']==child
    other=SessionEdits(s,q);assert other.read(r)['current_run_id']==child


def test_ambiguous_non_plan_cannot_confirm(tmp_path,scene):
    s,r,q,m,c=setup(tmp_path,scene);c.planner=lambda _:reply(r,'clarify')
    c.send(r,{'request_id':'m1','revision':0,'message':'Move that'})
    with pytest.raises(RuntimeError):m.confirm(r,{'revision':1,'plan_id':'made-up'})
    assert not q.calls and len(s.list_runs())==1

def test_current_and_candidate_presentation_keep_explicit_ownership(tmp_path,scene,monkeypatch):
    import display
    from session_edits import session_presentation
    s,r,q,m,c=setup(tmp_path,scene);m.confirm(r,draft(c,r));child=q.calls[-1]
    def render(root,status):
        if root.name==r:return {'comparisons':[{'pairs':[{'path':'old.png'}]}],'files':[{'path':'Room.blend'}],'diagnostics':{}}
        return {'comparisons':[],'files':[],'diagnostics':{'comparisons':[{'pairs':[{'path':'new.png'}]}],'files':[{'path':'Candidate.blend'}]}}
    monkeypatch.setattr(display,'build_presentation',render)
    result=session_presentation(m,r)
    assert result['files'][0]['run_id']==r
    assert result['comparisons'][0]['pairs'][0]['run_id']==r
    assert result['diagnostics']['files'][0]['run_id']==child
    assert result['diagnostics']['comparisons'][0]['pairs'][0]['run_id']==child

def test_reserved_but_unpublished_edit_can_be_abandoned(tmp_path,scene,monkeypatch):
    s,r,q,m,c=setup(tmp_path,scene);data=draft(c,r)
    monkeypatch.setattr(m,'_publish',lambda *args:(_ for _ in ()).throw(RuntimeError('power loss')))
    with pytest.raises(RuntimeError):m.confirm(r,data)
    op=m.read(r)['active'];m.action(r,{'operation_id':op['run_id'],'action':'discard'})
    assert m.read(r)['active']['outcome']=='discarded' and q.calls==[]


def test_session_identity_tamper_is_rejected(tmp_path,scene):
    s,r,q,m,c=setup(tmp_path,scene);m.confirm(r,draft(c,r));path=m.path(r);state=read_json(path)
    state['operations'][0]['request']['session_id']='another';atomic_json(path,state)
    with pytest.raises(ValueError,match='identity'):m.read(r)
