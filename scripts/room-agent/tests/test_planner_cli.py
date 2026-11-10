import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ui'))

def test_command_is_explicit_no_tool_ephemeral_readonly(tmp_path):
    import planner_cli as p
    args=p.command('/codex',tmp_path,tmp_path/'catalog.json')
    assert '--ignore-user-config' in args and '--ignore-rules' in args and '--strict-config' in args
    assert args[args.index('--sandbox')+1]=='read-only'
    assert 'approval_policy="never"' in args
    assert '--dangerously-bypass-approvals-and-sandbox' not in args
    assert 'shell_tool' in args and 'code_mode_host' in args
    assert 'web_search="disabled"' in args

def test_parse_only_completed_structured_agent_output():
    import planner_cli as p
    def event(kind,**kw):return json.dumps({'type':kind,**kw})
    output=event('item.completed',item={'type':'agent_message','text':'{"kind":"answer"}'})+'\n'+event('turn.completed')
    assert p.parse_events(output)=={'kind':'answer'}
    with pytest.raises(RuntimeError):p.parse_events(output.replace('turn.completed','turn.failed'))
    with pytest.raises(RuntimeError):p.parse_events(event('item.started',item={'type':'command_execution'})+'\n'+output)
    with pytest.raises(RuntimeError):p.parse_events(output.split('\n')[0])
    with pytest.raises(RuntimeError):p.parse_events(event('item.completed',item={'type':'agent_message','text':'not json'})+'\n'+event('turn.completed'))

def test_runtime_timeout_stops_own_process(tmp_path):
    import planner_cli as p
    with pytest.raises(RuntimeError,match='timed out'):
        p.run([sys.executable,'-c','import time; time.sleep(20)'],timeout=.1)

def test_no_automatic_model_retry(monkeypatch,tmp_path):
    import planner_cli as p
    calls=[]
    monkeypatch.setattr(p,'ensure_runtime',lambda:('/codex','{}'))
    def failed(*a,**k):calls.append(a);raise RuntimeError('network failed')
    monkeypatch.setattr(p,'run',failed)
    with pytest.raises(RuntimeError):p.plan('hello')
    assert len(calls)==1

def test_success_after_cli_transport_reconnect_is_accepted():
    import planner_cli as p
    events=[{'type':'error','message':'Reconnecting... 1/5 (stream disconnected)'},
            {'type':'item.completed','item':{'type':'agent_message','text':'{"kind":"answer"}'}},
            {'type':'turn.completed'}]
    assert p.parse_events('\n'.join(json.dumps(e) for e in events))=={'kind':'answer'}


def test_transport_schema_requires_target_and_evidence_bindings():
    import planner_cli as p
    schema=next(s for s in p.OUTPUT_SCHEMA['properties']['plan']['anyOf'] if s['type']=='object')
    assert {'target_ids','finding_ids'} <= set(schema['required'])
    assert {'target_ids','finding_ids'} <= set(schema['properties'])
    assert schema['additionalProperties'] is False


def test_schema_constrained_cli_plan_reaches_conversation_validator(monkeypatch):
    import planner_cli as p
    from conversation import validate
    from room_agent.edit_contract import finding_catalog
    from test_conversation import reply
    desired=reply('one','plan')
    catalog=finding_catalog({'run_id':'one','issues':[{'objects':['OrganiserA'],'detail':'Brown woven edge in pair_00015.png'}]})
    desired['plan'].update(target_ids=['OrganiserA'],finding_ids=[catalog['records'][0]['finding_id']])
    monkeypatch.setattr(p,'ensure_runtime',lambda:('/codex','{}'))
    # Model transport obeys the actual --output-schema supplied by plan().
    def schema_constrained(args,**kw):
        schema=json.loads(Path(args[args.index('--output-schema')+1]).read_text())
        fields=next(s for s in schema['properties']['plan']['anyOf'] if s['type']=='object')['properties']
        result={**desired,'plan':{k:v for k,v in desired['plan'].items() if k in fields}}
        return '\n'.join(json.dumps(e) for e in [
            {'type':'item.completed','item':{'type':'agent_message','text':json.dumps(result)}},
            {'type':'turn.completed'}]),''
    monkeypatch.setattr(p,'run',schema_constrained)
    result=validate(p.plan('Use the bound report target'),{'run_id':'one','behavior_sha256':'current','finding_catalog':catalog})
    assert result['plan']['target_ids']==['OrganiserA']
    assert result['plan']['findings']==catalog['records']


def test_planning_guidance_assigns_source_capability_checks_to_inspector():
    from conversation import SYSTEM
    from room_agent.edit_behavior import skill_text
    assert 'User consent is not technical evidence' in SYSTEM
    assert 'conditional plan' in SYSTEM
    assert 'Use the role and tools supplied by the caller' in skill_text()


@pytest.mark.parametrize('kind',['answer','clarify','unsupported'])
def test_nonplan_response_has_no_binding_requirement(kind):
    from conversation import validate
    response={'kind':kind,'reply':'Clarify scope' if kind=='clarify' else 'Status only','questions':['Which object?'] if kind=='clarify' else [],'plan':None}
    assert validate(response,{'run_id':'one','behavior_sha256':'current'})['plan'] is None


def test_missing_binding_is_rejected_without_inventing_evidence():
    from conversation import validate
    from test_conversation import reply
    response=reply('one','plan');response['plan'].pop('target_ids');response['plan'].pop('finding_ids')
    with pytest.raises(ValueError,match='preserve target and evidence'):
        validate(response,{'run_id':'one','behavior_sha256':'current'})


def test_inspection_wire_schema_and_image_arguments_match_host_protocol(monkeypatch,tmp_path):
    import planner_cli as p
    seen=[];image=tmp_path/'existing.png';image.write_bytes(b'fixture')
    monkeypatch.setattr(p,'ensure_runtime',lambda:('/codex','{}'))
    def run(args,**kw):
        schema=json.loads(Path(args[args.index('--output-schema')+1]).read_text())
        assert 'inspect' in schema['properties']['kind']['enum']
        assert {'inspection','citations'}<=set(schema['required'])
        assert args[args.index('--image')+1]==str(image)
        seen.append(args)
        response={'kind':'answer','reply':'Seen saved view','questions':[],'plan':None,'inspection':None,'citations':['e1']}
        return '\n'.join(json.dumps(e) for e in [{'type':'item.completed','item':{'type':'agent_message','text':json.dumps(response)}},{'type':'turn.completed'}]),''
    monkeypatch.setattr(p,'run',run)
    assert p.plan('Inspect',images=[image])['citations']==['e1'] and len(seen)==1


def test_real_cli_schema_binds_session_not_retained_child(monkeypatch):
    import planner_cli as p
    from conversation import planning_graph
    from test_conversation_workflows import proposal
    facts={'run_id':'session-root','current_scene_run_id':'retained-child',
           'workflow_actions':{'scene_review':True,'resume_operation':None}}
    monkeypatch.setattr(p,'ensure_runtime',lambda:('/codex','{}'))
    seen=[]
    def model(args,**kw):
        schema=json.loads(Path(args[args.index('--output-schema')+1]).read_text())
        fields=next(s for s in schema['properties']['plan']['anyOf'] if s['type']=='object')['properties']
        assert fields['target_run_id']['enum']==['session-root']
        assert fields['operation_run_id']['enum']==['']
        assert 'scene_review' in fields['operation']['enum']
        assert 'resume_operation' not in fields['operation']['enum']
        seen.append(kw['prompt'])
        out=proposal(fields['target_run_id']['enum'][0],'scene_review');out.update(inspection=None,citations=[])
        return '\n'.join(json.dumps(e) for e in [{'type':'item.completed','item':{'type':'agent_message','text':json.dumps(out)}},{'type':'turn.completed'}]),''
    monkeypatch.setattr(p,'run',model)
    output=planning_graph(p.Planner()).invoke({'facts':facts,'history':[],'message':'Review the retained scene'})
    assert output['result']['plan']['target_run_id']=='session-root'
    assert 'target_run_id must be FACTS.run_id' in seen[0]
    # A turn-specific restriction must not leak into another request or global schema.
    fields=next(s for s in p.OUTPUT_SCHEMA['properties']['plan']['anyOf'] if s['type']=='object')['properties']
    assert 'enum' not in fields['target_run_id']


def test_bound_schemas_are_isolated_and_resume_uses_exact_available_run():
    from planner_contract import bound_schema
    first=bound_schema({'run_id':'one','workflow_actions':{'scene_review':True,'resume_operation':None}})
    second=bound_schema({'run_id':'two','workflow_actions':{'scene_review':False,'resume_operation':{'run_id':'failed-child'}}})
    props=lambda s:next(r for r in s['properties']['plan']['anyOf'] if r['type']=='object')['properties']
    assert props(first)['target_run_id']['enum']==['one']
    assert props(second)['target_run_id']['enum']==['two']
    assert props(second)['operation_run_id']['enum']==['','failed-child']
    assert props(second)['operation']['enum']==['scene_edit','resume_operation']
