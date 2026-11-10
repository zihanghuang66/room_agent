import json
from pathlib import Path
import pytest
from test_edit_driver import runtime
from test_scene import scene
from test_session_edits import setup, draft
from test_dashboard_stage3 import run_js


def response():
    return {'already_satisfied':True,'source_verifiable':True,'unique_target':True,
            'requirements_complete':True,'reason':'The final assignment already sets the requested value.',
            'targets':['Floor0'],'requirements':[{'requirement':'roughness 0.90',
            'evidence':[{'path':'Room.py','quote':'x=1'}]}], 'issues':[]}


def test_independent_inspection_reuses_bound_report_without_editing(runtime):
    from room_agent.unchanged_edit import inspect_current
    req,state,attempt,sessions=runtime
    async def review(spec,path,role):
        assert spec.read_only and role=='quality'
        sessions.calls.append(spec)
        return {'termination':'completed','text':json.dumps(response())}
    sessions.recorded_session=review
    result,paths=inspect_current(req,state,attempt)
    assert result['no_change_verified'] is True
    assert result['completion_reason']=='already_satisfied'
    assert result['edit_status']=='unchanged' and 'edit_score' not in result
    assert inspect_current(req,state,attempt)[0]==result and len(sessions.calls)==1
    (Path(state['active_source'])/'Room.py').write_text('x=2')
    with pytest.raises(RuntimeError,match='identity'):inspect_current(req,state,attempt)


@pytest.mark.parametrize('field,value', [('already_satisfied',False),('unique_target',False),
    ('source_verifiable',False),('requirements_complete',False),('issues',['ambiguous']),
    ('targets',[]),('requirements',[]),('reason','')])
def test_unproven_no_change_does_not_pass(runtime,field,value):
    from room_agent.unchanged_edit import validate
    req,state,attempt,sessions=runtime
    raw=response();raw[field]=value
    assert validate(raw,Path(state['active_source'])) is False


@pytest.mark.parametrize('path,quote',[('../outside.py','x=1'),('Room.py','x=9'),('/etc/passwd','root')])
def test_invented_or_unsafe_evidence_rejected(runtime,path,quote):
    from room_agent.unchanged_edit import validate
    _,state,_,_=runtime
    raw=response();raw['requirements'][0]['evidence']=[dict(path=path,quote=quote)]
    assert not validate(raw,Path(state['active_source']))


def test_timeout_can_resume_without_edit_or_build(runtime):
    from room_agent.unchanged_edit import inspect_current
    req,state,attempt,sessions=runtime
    async def timeout(*args):return {'termination':'timeout','text':''}
    sessions.recorded_session=timeout
    with pytest.raises(RuntimeError,match='incomplete'):inspect_current(req,state,attempt)
    assert not (attempt/'unchanged_receipt.json').exists()


def test_real_graph_no_change_stops_before_build_and_keeps_current(tmp_path,scene):
    from room_agent.edit_executor import EditExecutor
    from room_agent.saved_scene import tree_hashes
    s,r,q,m,c=setup(tmp_path,scene);before=tree_hashes(s.root/'runs'/r/'work');calls=[]
    class Adapter(EditExecutor):
        def _external(self,name,state,key):
            calls.append(name)
            if name=='inspect_current':return {'no_change_verified':True,'completion_reason':'already_satisfied','edit_status':'unchanged'},[]
            if name!='preflight':raise AssertionError('Unexpected expensive stage '+name)
            return {},[]
    s.edit_executor_type=Adapter;m.confirm(r,draft(c,r));child=q.calls[-1];q.rows.clear()
    s.execute(child)
    status=s.show(child)
    assert status['execution_status']=='completed' and status['no_change_verified']
    assert calls==['preflight','inspect_current']
    assert m.read(r)['active']['outcome']=='unchanged'
    assert m.read(r)['current_run_id']==r and len(m.workspace())==1
    assert tree_hashes(s.root/'runs'/r/'work')==before
    m.confirm(r,draft(c,r,'Now use 0.95'))
    assert s._load(q.calls[-1])[0]['parent_run_id']==r


def test_no_change_ui_has_simple_message_and_no_decision():
    run_js(r'''
selected='r';currentStatus={run_id:'r',session:{active:{run_id:'child',outcome:'unchanged',change:'roughness',status:{execution_status:'completed'}},operations:[]}};
paintSessionOperation();assert.equal($('sessionOperationText').textContent,'Already matches your request. No changes needed.');
assert.equal($('sessionDiscard').hidden,true);assert.equal($('sessionResume').hidden,true);assert.equal(sessionUnresolved(),false);
''')


def test_real_graph_inspection_timeout_resume_reuses_snapshot_preflight(tmp_path,scene):
    from room_agent.edit_executor import EditExecutor
    s,r,q,m,c=setup(tmp_path,scene);calls=[]
    class Adapter(EditExecutor):
        fail=True
        def _external(self,name,state,key):
            calls.append(name)
            if name=='inspect_current':
                if self.fail:raise RuntimeError('inspection timeout')
                return {'no_change_verified':True,'completion_reason':'already_satisfied','edit_status':'unchanged'},[]
            if name!='preflight':raise AssertionError(name)
            return {},[]
    s.edit_executor_type=Adapter;m.confirm(r,draft(c,r));child=q.calls[-1];q.rows.clear()
    with pytest.raises(RuntimeError,match='inspection timeout'):s.execute(child)
    assert m.read(r)['active']['outcome']=='pending'
    Adapter.fail=False;s.execute(child)
    assert calls==['preflight','inspect_current','inspect_current']
    assert m.read(r)['current_run_id']==r
    assert m.read(r)['active']['outcome']=='unchanged'


def test_inspection_detects_mutation_and_tampered_receipt(runtime):
    from room_agent.unchanged_edit import inspect_current
    req,state,attempt,sessions=runtime
    async def bad(spec,path,role):
        (Path(state['active_source'])/'Room.py').write_text('x=2')
        return {'termination':'completed','text':json.dumps(response())}
    sessions.recorded_session=bad
    with pytest.raises(RuntimeError,match='changed source'):inspect_current(req,state,attempt)
    assert not (attempt/'unchanged_receipt.json').exists()
    (Path(state['active_source'])/'Room.py').write_text('x=1')
    async def good(*args):return {'termination':'completed','text':json.dumps(response())}
    sessions.recorded_session=good;inspect_current(req,state,attempt)
    (attempt/'unchanged_review.json').write_text('{}')
    with pytest.raises(RuntimeError,match='integrity'):inspect_current(req,state,attempt)


def test_real_graph_unverified_check_keeps_full_edit_acceptance(tmp_path,scene):
    from room_agent.edit_executor import EditExecutor
    s,r,q,m,c=setup(tmp_path,scene);calls=[]
    class Adapter(EditExecutor):
        def _external(self,name,state,key):
            calls.append(name)
            if name=='inspect_current':return {'no_change_verified':False},[]
            if name=='review':return {'edit_status':'not_passed','quality_status':'not_passed'},[]
            if name=='finalize':return {'technical_export_verified':True,'completion_reason':'edit_not_accepted'},[]
            return {},[]
    s.edit_executor_type=Adapter;m.confirm(r,draft(c,r));child=q.calls[-1];q.rows.clear();s.execute(child)
    assert calls==['preflight','inspect_current','baseline_build','evidence','baseline_render','edit','build','geometry','render','review','finalize']
    assert m.read(r)['current_run_id']==r and m.read(r)['active']['outcome']=='pending'


def test_preservation_is_proved_by_unchanged_files_not_recertifying_room(runtime):
    from room_agent.unchanged_edit import inspect_current
    req,state,attempt,sessions=runtime
    req['current_source_check_policy']='current-source-check/2'
    async def reviewer(spec,path,role):
        assert 'Preservation is an equality constraint' in spec.prompt
        assert 'Do not demand new before/after renders' in spec.prompt
        assert 'positive requested changes' in spec.prompt
        sessions.calls.append(spec)
        return {'termination':'completed','text':json.dumps(response())}
    sessions.recorded_session=reviewer
    result,_=inspect_current(req,state,attempt)
    assert result['no_change_verified']
    assert json.loads((attempt/'unchanged_binding.json').read_text())['policy']=='current-source-check/2'


def test_legacy_inspection_retains_policy_and_cached_verdict(runtime):
    from room_agent.unchanged_edit import inspect_current
    req,state,attempt,sessions=runtime
    async def old(spec,path,role):
        raw=response();raw['already_satisfied']=False
        return {'termination':'completed','text':json.dumps(raw)}
    sessions.recorded_session=old
    assert not inspect_current(req,state,attempt)[0]['no_change_verified']
    assert json.loads((attempt/'unchanged_binding.json').read_text())['policy']=='current-source-check/1'
    async def never(*args):pytest.fail('old saved review must not be repeated or reclassified')
    sessions.recorded_session=never
    assert not inspect_current(req,state,attempt)[0]['no_change_verified']
