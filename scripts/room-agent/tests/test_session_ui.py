from test_dashboard_stage3 import run_js,HTML

def test_confirm_targets_stable_session_and_never_creates_sidebar_task():
    run_js(r'''
selectRun('root');currentStatus={run_id:'root',execution_status:'completed',session:{current_run_id:'root',operations:[],active:null}};
const plan={plan_id:'p1',target_run_id:'root',change:'Floor .9',objects:['floor'],preserve:['color'],steps:['build'],expected_outputs:['image']};
paintConversation({run_id:'root',revision:1,turns:[{request_id:'m1',status:'completed',message:'change',facts:{editable:true},result:{reply:'Draft',plan}}]});
assert.equal($('confirmPlan').hidden,false);responses['POST:/api/runs/root/confirm-plan']={active:{run_id:'internal',outcome:'pending'}};
await $('confirmPlan').onclick();
assert.equal(selected,'root');assert.equal(calls.filter(c=>c.body).length,1);
assert.deepEqual(calls.find(c=>c.body).body,{plan_id:'p1',revision:1});
assert.ok(!calls.some(c=>c.path==='/api/edit-runs'));
''')

def test_old_or_busy_plan_is_not_confirmable_and_edit_shortcut_focuses_chat():
    run_js(r'''
selectRun('root');paint({run_id:'root',operation:'scene_edit',execution_status:'completed',session:{active:null}});
await $('editVersion').onclick();assert.equal(document.activeElement,$('chatInput'));assert.ok(!$('newTask').open);assert.equal(calls.length,0);
showPlanSummary({target_run_id:'root',change:'x'},false);assert.equal($('confirmPlan').hidden,true);
currentStatus.session.active={run_id:'internal',outcome:'pending',status:{execution_status:'running'}};
assert.equal(sessionUnresolved(),true);
''')

def test_artifact_paths_are_bound_to_the_source_not_session_root():
    run_js(r'''
selected='root';const viewer=makePreviewViewer([{...pair('x.png'),run_id:'internal'}],'changes',['Captured','Rendered']);
assert.ok(flat(viewer).some(e=>e.attrs.href?.includes('run_id=internal')));
const holder=new Element();outputLinks(holder,[{path:'Room.glb',run_id:'accepted'}]);assert.ok(holder.children[0].href.includes('run_id=accepted'));
''')

def test_chat_send_inflight_blocks_confirmation_and_buttons_bind_operation():
    run_js(r'''
selectRun('root');currentStatus={run_id:'root',session:{active:null,operations:[]}};
const plan={plan_id:'p1',target_run_id:'root',change:'x'};
chatStates.set('root',{revision:1,turns:[{status:'completed',facts:{editable:true},result:{plan}}]});
chatSending.add('root');showPlanSummary(plan,true);assert.equal($('confirmPlan').disabled,true);chatSending.clear();syncConfirmPlan();assert.equal($('confirmPlan').disabled,false);
currentStatus.session={active:{run_id:'op',outcome:'pending',change:'x',status:{execution_status:'failed'}},operations:[]};
paintSessionOperation();assert.equal($('sessionResume').hidden,false);assert.equal($('sessionDiscard').hidden,false);
responses['POST:/api/runs/root/session-action']={active:null,operations:[]};await $('sessionResume').onclick();
const call=calls.find(c=>c.body);assert.deepEqual(call.body,{operation_id:'op',action:'resume'});assert.equal(selected,'root');
''')


def test_message_does_not_race_inflight_confirmation():
    run_js(r'''
selected='root';$('chatInput').value='another request';confirmBusy=true;
await sendConversation();assert.equal(calls.length,0);assert.equal($('chatInput').value,'another request');
assert.match($('chatNotice').textContent,/confirmation/);
confirmBusy=false;sessionActionBusy=true;await sendConversation();assert.equal(calls.length,0);
''')


def test_prerequisite_stop_returns_to_conversation_without_discard_or_resume():
    run_js(r'''
selectRun('root');currentStatus={run_id:'root',session:{active:{run_id:'op',outcome:'needs_input',change:'edit',status:{execution_status:'completed',gate_reason:'Target ambiguous',clarification_questions:['Which cabinet?']}},operations:[]}};
paintSessionOperation();assert.equal(sessionUnresolved(),false);
assert.equal($('sessionResume').hidden,true);assert.equal($('sessionDiscard').hidden,true);
assert.match($('sessionOperationText').textContent,/input is needed/);
assert.match($('sessionOperationChange').textContent,/Which cabinet/);
const nextPlan={plan_id:'new',target_run_id:'root',change:'Named cabinet'};
chatStates.set('root',{revision:2,turns:[{status:'completed',facts:{editable:true},result:{plan:nextPlan}}]});
showPlanSummary(nextPlan,true);
assert.equal($('confirmPlan').hidden,false);
''')


def test_resume_draft_can_confirm_unresolved_operation_without_editable_scene():
    run_js(r'''
selectRun('root');currentStatus={run_id:'root',session:{active:{run_id:'op',outcome:'pending',status:{execution_status:'failed'}},operations:[]}};
const plan={plan_id:'resume1',target_run_id:'root',operation:'resume_operation',operation_run_id:'op',change:'Resume from checkpoint'};
paintConversation({run_id:'root',revision:2,turns:[{status:'completed',message:'Resume',facts:{editable:false},result:{reply:'Confirm to resume',plan}}]});
assert.equal($('confirmPlan').hidden,false);assert.equal($('confirmPlan').disabled,false);
assert.match($('confirmHint').textContent,/checkpoint/);
currentStatus.session.commands=[{plan_id:'resume1',submitted:true}];syncConfirmPlan();assert.equal($('confirmPlan').hidden,true);
''')


def test_completed_review_is_resolved_and_not_called_an_edit():
    run_js(r'''
selectRun('root');currentStatus={run_id:'root',session:{current_run_id:'root',active:{run_id:'review',operation:'saved_scene_review',outcome:'reviewed',change:'Review only',status:{execution_status:'completed',quality_status:'not_passed',score:7}},operations:[]}};
paintSessionOperation();assert.equal(sessionUnresolved(),false);
assert.match($('sessionOperationText').textContent,/Review complete/);
assert.match($('sessionOperationText').textContent,/not passed/);
assert.equal($('sessionResume').hidden,true);assert.equal($('sessionDiscard').hidden,true);
const plan={plan_id:'review2',target_run_id:'root',operation:'scene_review',change:'Review again'};
paintConversation({run_id:'root',revision:2,turns:[{status:'completed',facts:{editable:true},message:'Review',result:{reply:'Confirm',plan}}]});
assert.equal($('confirmPlan').disabled,false);assert.match($('confirmHint').textContent,/No scene edits/);
''')
