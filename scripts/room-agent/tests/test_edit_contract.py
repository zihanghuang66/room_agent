import json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ui'))
from test_conversation import reply
from test_edit_driver import runtime
from test_scene import scene
from test_session_edits import setup,draft


def test_catalog_keeps_complete_findings_ids_and_history():
    from room_agent.edit_contract import finding_catalog,bind_evidence
    current={'run_id':'current','baseline_quality':{'run_id':'prior','issues':[{'objects':['DeskA'],'detail':'Surface too thick in pair_00003.png','priority':'large_appearance'}]}}
    catalog=finding_catalog(current)
    row=catalog['records'][0]
    assert row['object_ids']==['DeskA'] and row['historical'] and row['source_run_id']=='prior'
    assert row['views']==['pair_00003.png']
    bound=bind_evidence({'target_ids':['DeskA'],'finding_ids':[row['finding_id']]},{'finding_catalog':catalog})
    assert bound['findings']==[row]
    with pytest.raises(ValueError):bind_evidence({'target_ids':['Other'],'finding_ids':[row['finding_id']]},{'finding_catalog':catalog})
    with pytest.raises(ValueError):bind_evidence({'target_ids':['DeskA'],'finding_ids':['fake']},{'finding_catalog':catalog})


def test_ready_gate_requires_targets_evidence_and_capability(runtime):
    from room_agent.edit_contract import readiness
    _,state,_,_=runtime
    source=Path(state['active_source']);(source/'Room.py').write_text("target='CabinetA'\n")
    request={'edit_contract':{'target_ids':['CabinetA']}}
    raw={'status':'ready','target_ids':['CabinetA'],'target_unique':True,'evidence_sufficient':True,'capability_supported':True,
         'reason':'Editable panel source located','questions':[], 'evidence':[{'path':'Room.py','quote':"target='CabinetA'"}]}
    assert readiness(raw,request,source)['gate_status']=='ready'
    for key in ['target_unique','evidence_sufficient','capability_supported']:
        assert readiness({**raw,key:False},request,source)['stop_before_build']
    assert readiness({**raw,'target_ids':['Other']},request,source)['stop_before_build']


def test_planner_binding_is_preserved_in_confirmed_request(tmp_path,scene):
    from room_agent.edit_contract import finding_catalog
    s,r,q,m,c=setup(tmp_path,scene)
    old=c.facts
    catalog=finding_catalog({'run_id':r,'issues':[{'objects':['WindowA'],'detail':'Frame mismatch','priority':'large_appearance'}]})
    def facts(*a):return {**old(*a),'finding_catalog':catalog}
    c.facts=facts
    def planner(prompt):
        out=reply(r,'plan');out['plan'].update(target_ids=['WindowA'],finding_ids=[catalog['records'][0]['finding_id']]);return out
    c.planner=planner;m.confirm(r,draft(c,r,'Fix window'))
    req=s._load(q.calls[0])[0]
    assert req['edit_contract']['target_ids']==['WindowA']
    assert req['edit_contract']['findings'][0]['detail']=='Frame mismatch'
    assert 'WindowA' in req['instruction']
    assert req['current_source_check_policy']=='current-source-check/3'


def test_no_change_and_partial_block_are_not_quality_zero():
    from room_agent.edit_contract import delta_outcome
    report={'status':'needs_clarification','summary':'Two matching objects','questions':['Which object?']}
    result=delta_outcome({'a':'1'},{'a':'1'},report)
    assert result['stop_before_build'] and result['completion_reason']=='needs_clarification'
    assert 'edit_score' not in result and result['actual_changed_files']==[]
    result=delta_outcome({'a':'1'},{'a':'2'},report)
    assert result['stop_before_build'] and result['completion_reason']=='blocked'
    assert result['actual_changed_files']==['a']
    assert not delta_outcome({'a':'1'},{'a':'2'},{'status':'changed'})['stop_before_build']


@pytest.mark.parametrize('stop_node',['inspect_current','edit'])
def test_real_graph_stops_for_input_and_preserves_parent(tmp_path,scene,stop_node):
    from room_agent.edit_executor import EditExecutor
    s,r,q,m,c=setup(tmp_path,scene);calls=[]
    class Adapter(EditExecutor):
        def _external(self,name,state,key):
            calls.append(name)
            if name==stop_node:return {'stop_before_build':True,'completion_reason':'needs_clarification','edit_status':'needs_clarification','gate_reason':'Target ambiguous','clarification_questions':['Which object?']},[]
            if name=='inspect_current':return {'no_change_verified':False,'gate_status':'ready'},[]
            if name in ('build','geometry','render','review','finalize'):raise AssertionError('Expensive downstream stage '+name)
            return {},[]
    s.edit_executor_type=Adapter;m.confirm(r,draft(c,r));child=q.calls[-1];q.rows.clear();s.execute(child)
    status=s.show(child)
    assert status['completion_reason']=='needs_clarification' and status['error'] is None
    assert status['clarification_questions']==['Which object?']
    assert m.read(r)['current_run_id']==r and m.read(r)['active']['outcome']=='needs_input'
    m.confirm(r,draft(c,r,'The explicitly named target'))
    assert len(m.workspace())==1
    assert s._load(q.calls[-1])[0]['parent_run_id']==r


def test_inspector_ambiguity_returns_question_instead_of_render(runtime):
    from room_agent.unchanged_edit import inspect_current
    from room_agent.edit_behavior import skill_digest
    req,state,attempt,sessions=runtime
    req.update(current_source_check_policy='current-source-check/3',edit_contract={'version':'evidence-edit/1','target_ids':[],'skill_sha256':skill_digest()})
    async def inspect(spec,path,role):
        assert 'Evidence-guided scene editing' in spec.prompt
        return {'termination':'completed','text':json.dumps({'already_satisfied':False,'readiness':{'status':'needs_clarification','reason':'Two matching targets','questions':['Which target?']}})}
    sessions.recorded_session=inspect
    result,_=inspect_current(req,state,attempt)
    assert result['stop_before_build'] and result['clarification_questions']==['Which target?']


@pytest.mark.parametrize('saved,report_status,expected',[(False,'changed','needs_clarification'),(True,'blocked','blocked'),(True,'changed','changed')])
def test_actual_editor_delta_gates_downstream_and_keeps_original(runtime,saved,report_status,expected):
    from room_agent.edit_driver import run_stage
    from room_agent.edit_behavior import skill_digest
    req,state,attempt,sessions=runtime
    req['edit_contract']={'version':'evidence-edit/1','target_ids':['CabinetA'],'skill_sha256':skill_digest(),
                          'findings':[{'finding_id':'f-example','object_ids':['CabinetA'],'views':['pair_00005.png'],'detail':'Panel mismatch'}]}
    async def edit(spec,path,role):
        sessions.calls.append(spec)
        assert 'Evidence-guided scene editing' in spec.prompt and 'f-example' in spec.prompt and 'pair_00005.png' in spec.prompt
        if saved:(spec.cwd/'Room.py').write_text('x=2')
        return {'termination':'completed','text':json.dumps({'status':report_status,'summary':'Observed result','target_ids':['CabinetA'],'questions':[]})}
    sessions.recorded_session=edit
    result,_=run_stage('edit',req,state,attempt)
    assert result['gate_status']==expected
    assert result['stop_before_build']==(expected!='changed')
    assert 'edit_score' not in result
    assert (Path(state['active_source'])/'Room.py').read_text()=='x=1'
    assert run_stage('edit',req,state,attempt)[0]==result and len(sessions.calls)==1


def test_new_contract_interrupted_editor_keeps_partial_checkpoint_without_recall(runtime):
    from room_agent.edit_driver import run_stage
    from room_agent.edit_behavior import skill_digest
    req,state,attempt,sessions=runtime
    req['edit_contract']={'version':'evidence-edit/1','target_ids':['DeskB'],'skill_sha256':skill_digest()}
    async def edit(spec,path,role):
        sessions.calls.append(spec);(spec.cwd/'Room.py').write_text('x=2')
        raise RuntimeError('network interrupted')
    sessions.recorded_session=edit
    with pytest.raises(RuntimeError,match='network interrupted'):run_stage('edit',req,state,attempt)
    result,_=run_stage('edit',req,state,attempt)
    assert result['stop_before_build'] and result['gate_status']=='blocked' and result['edit_termination']=='interrupted'
    assert len(sessions.calls)==1 and (Path(result['active_source'])/'Room.py').read_text()=='x=2'
    assert (Path(state['active_source'])/'Room.py').read_text()=='x=1'


def test_malformed_target_report_stops_instead_of_throwing():
    from room_agent.edit_contract import delta_outcome
    for value in ([{}],None,'CabinetA'):
        assert delta_outcome({'x':'1'},{'x':'2'},{'status':'changed','target_ids':value},['CabinetA'])['stop_before_build']


def test_skill_hash_prevents_silent_behavior_change():
    from room_agent.edit_behavior import skill_text,skill_digest
    assert 'Evidence-guided' in skill_text({'edit_contract':{'skill_sha256':skill_digest()}})
    with pytest.raises(RuntimeError):skill_text({'edit_contract':{'skill_sha256':'changed'}})


def test_followup_preserves_finding_and_changed_report_requires_new_plan(tmp_path,scene):
    from room_agent.edit_contract import finding_catalog
    s,r,q,m,c=setup(tmp_path,scene);original=c.facts
    catalog=finding_catalog({'run_id':r,'issues':[{'objects':['ShelfB'],'detail':'Shelf heights in pair_00002.png'}]})
    c.facts=lambda *a:{**original(*a),'finding_catalog':catalog}
    def planner(prompt):
        assert 'ShelfB' in prompt and 'pair_00002.png' in prompt
        out=reply(r,'plan');out['plan'].update(target_ids=['ShelfB'],finding_ids=[catalog['records'][0]['finding_id']]);return out
    c.planner=planner
    draft(c,r,'Address this report');data=draft(c,r,'Only the upper shelf')
    catalog['records'][0]['detail']='Updated finding; requires another draft'
    with pytest.raises(RuntimeError,match='changed'):m.confirm(r,data)
    assert not q.calls


def test_inspector_malformed_noop_target_cannot_bypass_prerequisites(runtime):
    from room_agent.unchanged_edit import inspect_current
    from room_agent.edit_behavior import skill_digest
    req,state,attempt,sessions=runtime
    req.update(current_source_check_policy='current-source-check/3',edit_contract={'version':'evidence-edit/1','target_ids':['CabinetA'],'skill_sha256':skill_digest()})
    async def inspect(*args):return {'termination':'completed','text':json.dumps({'already_satisfied':True,'targets':[{}]})}
    sessions.recorded_session=inspect
    result,_=inspect_current(req,state,attempt)
    assert result['stop_before_build'] and not result['no_change_verified']


def test_review_cannot_invent_zero_score_if_new_contract_loses_delta(runtime):
    from room_agent.edit_acceptance import run_review
    from room_agent.edit_behavior import skill_digest
    req,state,attempt,_=runtime
    req['edit_contract']={'version':'evidence-edit/1','skill_sha256':skill_digest()}
    with pytest.raises(RuntimeError,match='No quality score'):run_review(req,state,attempt)
    assert not (attempt/'impact_review.json').exists()
