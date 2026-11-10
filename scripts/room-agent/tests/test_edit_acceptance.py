import json
from pathlib import Path

import pytest
from test_edit_driver import runtime
from test_dashboard_stage3 import run_js
from test_revisions import parent_run
from test_scene import scene


def evidence():
    return dict(pass_=True, score=8, change_verified=True,
                change_evidence='Before/after compiled floor material and measured RGB.',
                no_unintended_changes=True, scope_evidence='Floor only; scene objects and lights compared.',
                changed_files=['Room.py'], affected_objects=['Floor0'],
                objects=[dict(id='Floor0', observed=True, views=['before:pair_00001.png', 'after:pair_00001.png'])],
                issues=[])


def normalized(raw=None, **kwargs):
    from room_agent.edit_acceptance import assess
    raw = raw or evidence()
    if 'pass_' in raw:raw['pass'] = raw.pop('pass_')
    return assess(raw, changed=['Room.py'], known_ids=['Floor0', 'Chair0'],
                  before_views=['pair_00001.png'], after_views=['pair_00001.png'],
                  geometry_pass=kwargs.get('geometry_pass', True), geometry_issues=[])


def test_edit_acceptance_is_separate_from_whole_room_quality():
    result = normalized()
    assert result['edit_status'] == 'passed'
    assert result['edit_score'] == 8 and result['change_verified'] is True
    assert 'quality_status' not in result  # edit approval cannot certify the room


@pytest.mark.parametrize('field,value', [
    ('score', 7.99), ('score', True), ('score', float('nan')),
    ('change_verified', False), ('no_unintended_changes', False),
    ('scope_evidence', ''), ('change_evidence', ''), ('changed_files', []),
    ('changed_files', ['Room.py', 'invented.png']), ('affected_objects', []),
    ('affected_objects', ['Unknown']), ('objects', []),
    ('objects', [dict(id='Floor0', observed=True, views=['after:pair_00001.png'])]),
    ('objects', [dict(id='Floor0', observed=True, views=['before:fake.png', 'after:fake.png'])]),
    ('issues', ['unwanted chair change']), ('issues', None), ('pass', False),
])
def test_incomplete_or_out_of_scope_edit_is_not_approved(field, value):
    raw=evidence();raw[field]=value
    if field=='pass':raw.pop('pass_')
    assert normalized(raw)['edit_status']=='not_passed'


def test_geometry_failure_blocks_edit_acceptance():
    assert normalized(geometry_pass=False)['edit_status']=='not_passed'


def test_new_request_pins_policy_and_parent_quality_without_changing_parent(tmp_path,scene):
    service,parent=parent_run(tmp_path,scene)
    before=service.show(parent)
    child=service.create_edit(parent,'Change the floor material to dark grey.')
    request,_=service._load(child)
    assert request['edit_review_policy']=='impact-review/1'
    assert request['parent_quality']=='not_passed'
    assert request['parent_issues']==before['issues']
    assert service.show(parent)==before


@pytest.mark.parametrize("new_contract", [False, True])
def test_scoped_review_one_session_preserves_parent_quality_and_reuses_result(runtime,new_contract):
    from room_agent.edit_driver import run_stage
    req,state,attempt,sessions=runtime
    req.update(edit_review_policy='impact-review/1',parent_quality='not_passed',parent_score=5.5,
               parent_issues=[{'detail':'Inherited chair mismatch'}])
    if new_contract:
        from room_agent.edit_behavior import skill_digest
        req['edit_contract']={'version':'evidence-edit/1','target_ids':['Floor0'],'skill_sha256':skill_digest()}
    child=attempt/'child';child.mkdir();(child/'Room.py').write_text('x=2')
    state['room']=str(child)
    for directory in [Path(state['preview'])]:
        (directory/'room_layout.json').write_text(json.dumps({'objects':[{'id':'Floor0'},{'id':'Chair0'}]}))
    for field,folder in [('pairs','after'),('baseline_pairs','before')]:
        path=attempt/folder/'pair_00001.png';path.parent.mkdir();path.write_bytes(b'image'+folder.encode())
        state[field]=[str(path)]
    async def reviewer(spec,path,role):
        sessions.calls.append(spec)
        if new_contract:assert 'Evidence-guided scene editing' in spec.prompt and 'Confirmed contract' in spec.prompt
        raw=evidence();raw['pass']=raw.pop('pass_')
        return {'termination':'completed','text':json.dumps(raw)}
    sessions.recorded_session=reviewer
    result,_=run_stage('review',req,state,attempt)
    assert result['edit_status']=='passed' and result['quality_status']=='not_passed'
    assert result['whole_room_review']=='not_repeated' and result['score'] is None
    assert result['baseline_quality']['score']==5.5
    assert result['baseline_quality']['issues']==req['parent_issues']
    assert len(sessions.calls)==1
    raw_path=attempt/'impact_review.json';original=raw_path.read_bytes()
    raw_path.write_text('{}')
    with pytest.raises(RuntimeError,match='integrity'):
        run_stage('review',req,state,attempt)
    raw_path.write_bytes(original)
    assert set(map(str,sessions.calls[0].image_paths))==set(state['pairs']+state['baseline_pairs'])
    assert run_stage('review',req,state,attempt)[0]==result
    assert len(sessions.calls)==1
    # A changed baseline invalidates the comparison checkpoint.
    (Path(state['baseline_room'])/'Room.py').write_text('x=9')
    with pytest.raises(RuntimeError,match='identity'):
        run_stage('review',req,state,attempt)


def test_scoped_timeout_remains_execution_failure(runtime):
    from room_agent.edit_driver import run_stage
    req,state,attempt,sessions=runtime
    req.update(edit_review_policy='impact-review/1',parent_quality='passed')
    child=attempt/'child';child.mkdir();(child/'Room.py').write_text('x=2');state['room']=str(child)
    (Path(state['preview'])/'room_layout.json').write_text(json.dumps({'objects':[{'id':'Floor0'}]}))
    async def reviewer(*args):return {'termination':'timeout','text':''}
    sessions.recorded_session=reviewer
    with pytest.raises(RuntimeError,match='incomplete'):
        run_stage('review',req,state,attempt)
    assert not (attempt/'visual_review.json').exists()


def test_no_saved_change_cannot_be_approved_or_certify_passed_parent(runtime):
    from room_agent.edit_driver import run_stage
    req,state,attempt,sessions=runtime
    req.update(edit_review_policy='impact-review/1',parent_quality='passed')
    (Path(state['preview'])/'room_layout.json').write_text(json.dumps({'objects':[{'id':'Floor0'}]}))
    result,_=run_stage('review',req,state,attempt)
    assert result['edit_status']=='not_passed' and result['change_verified'] is False
    assert result['quality_status']!='passed' and not sessions.calls


def test_status_projection_preserves_new_edit_fields():
    from room_agent.edit_graph import project
    state=dict(edit_status='passed',edit_score=8,edit_issues=[],change_verified=True,
               baseline_quality={'status':'not_passed'},whole_room_review='not_repeated')
    out=project({'parent_run_id':'parent','instruction':'Grey floor'},state,[],'completed')
    assert all(out[k]==v for k,v in state.items())


def test_unknown_policy_does_not_silently_run_full_review(runtime):
    from room_agent.edit_driver import run_stage
    req,state,attempt,sessions=runtime
    req['edit_review_policy']='unknown'
    with pytest.raises(RuntimeError,match='Unknown edit review policy'):
        run_stage('review',req,state,attempt)
    assert not sessions.calls


def test_legacy_projection_reads_verified_receipt_without_mutating_files(tmp_path):
    from room_agent.edit_acceptance import legacy_verification
    from room_agent.saved_scene import file_hash
    root=tmp_path;attempt=root/'work/attempts/r00_review/stable';attempt.mkdir(parents=True)
    binding=attempt/'binding.json';binding.write_text(json.dumps({'source':{'a':'b'},'instruction':'Floor grey'}))
    group=attempt/'group_000.json';group.write_text(json.dumps({'ids':['Floor0'],'review':{'change_verified':True,'change_evidence':'Measured floor RGB'}}))
    ledger=attempt/'group_receipts.json';ledger.write_text(json.dumps({group.name:file_hash(group)}))
    outputs={p.relative_to(root).as_posix():file_hash(p) for p in (binding,group,ledger)}
    receipt=root/'receipts/r00_review.json';receipt.parent.mkdir();receipt.write_text(json.dumps({'outputs':outputs}))
    req={'instruction':'Floor grey'};state={'build_stamp':{'a':'b'}}
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    result=legacy_verification(root,req,state)
    assert result['edit_status']=='change_verified'  # never invent scope acceptance
    assert all(p.read_bytes()==data for p,data in before.items())
    group.write_text('{}')
    assert legacy_verification(root,req,state)['edit_status']=='unreviewed'


def test_finished_legacy_task_projection_never_executes_a_node(tmp_path,scene):
    from room_agent.edit_executor import EditExecutor
    from room_agent.saved_scene import file_hash
    from room_agent.storage import atomic_json, digest
    service,parent=parent_run(tmp_path,scene)
    child=service.create_edit(parent,'Floor grey')
    req,root=service._load(child)
    req.pop('edit_review_policy')  # fixture represents immutable pre-v0.5.4 request
    atomic_json(root/'input.json',req);atomic_json(root/'input.sha256.json',digest(req))
    class NeverExecute(EditExecutor):
        def step(self,*args):raise AssertionError('Viewing a completed task must not run anything')
    service.edit_executor_type=NeverExecute
    with service._graph(req,root) as (graph,_,_,config):
        graph.update_state(config,dict(execution_status='completed',quality_status='not_passed',
                          build_stamp={'a':'b'},technical_export_verified=True),as_node='finalize')
    attempt=root/'work/attempts/r00_review/stable';attempt.mkdir(parents=True)
    atomic_json(attempt/'binding.json',{'source':{'a':'b'},'instruction':'Floor grey'})
    atomic_json(attempt/'group_000.json',{'review':{'change_verified':True,'change_evidence':'Measured floor'}})
    atomic_json(attempt/'group_receipts.json',{'group_000.json':file_hash(attempt/'group_000.json')})
    atomic_json(root/'receipts/r00_review.json',{'outputs':{p.relative_to(root).as_posix():file_hash(p) for p in attempt.iterdir()}})
    checkpoints=(root/'checkpoints.sqlite').read_bytes()
    old_request=(root/'input.json').read_bytes()
    status=service.show(child)
    assert status['edit_status']=='change_verified' and status['quality_status']=='not_passed'
    assert (root/'input.json').read_bytes()==old_request
    assert (root/'checkpoints.sqlite').read_bytes()==checkpoints


def test_ui_separates_edit_result_from_scene_findings():
    run_js(r'''
const s={run_id:'edit',operation:'scene_edit',execution_status:'completed',quality_status:'not_passed',edit_status:'passed',edit_score:8,whole_room_review:'not_repeated',baseline_quality:{status:'not_passed',score:5.5,issues:[{detail:'Old chair'}]},issues:[],edit_issues:[]};
paint(s);
assert.match($('editOutcome').textContent,/Edit passed/);
assert.match($('quality').textContent,/Not passed/i);
assert.match($('guidance').textContent,/not re-reviewed/i);
paint({...s,edit_status:'change_verified'});
assert.match($('editOutcome').textContent,/Change verified/);
assert(!/Edit passed/.test($('editOutcome').textContent));
paint({...s,operation:'full_scan'});assert.equal($('editOutcome').hidden,true);
''')
