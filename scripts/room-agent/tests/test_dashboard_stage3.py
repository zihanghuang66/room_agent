"""Browser-contract checks for clear manual review and experimental scan controls."""
from pathlib import Path
import shutil
import subprocess

import pytest


HTML = Path(__file__).resolve().parents[1] / 'src/room_agent/dashboard.html'
HARNESS = r'''
const assert = require('node:assert/strict');
class Element {
  constructor() { this.value=''; this.textContent=''; this.children=[]; this.style={setProperty(k,v){this[k]=v}}; this.checked=true; this.disabled=false; this.hidden=false; this.scrollHeight=0; this.scrollTop=0; this.clientHeight=0; this.attrs={}; }
  setAttribute(k,v) { this.attrs[k]=String(v); }
  showModal() { this.open=true; }
  close() { this.open=false; }
  appendChild(e) { if(e.parentNode)e.parentNode.children=e.parentNode.children.filter(x=>x!==e);e.parentNode=this;this.children.push(e); return e; }
  append(...es) { es.forEach(e=>this.appendChild(e)); }
  replaceChildren(...es) { this.children.forEach(e=>e.parentNode=null);this.children=[];this.append(...es);this.value=''; }
  focus() { document.activeElement=this; }
  contains(e) { return this===e||this.children.some(x=>x.contains(e)); }
}
const elements = new Map();
const document={addEventListener(){},getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id)},createElement(){return new Element()},createElementNS(ns,tag){const e=new Element();e.tag=tag;return e}};
const history={replaceState(){}};
const location={hash:''};
const calls=[];
function flat(e){return [e,...e.children.flatMap(flat)]}
function pair(path){return {path,width:1088,height:754,captured:[548,34,540,720],rendered:[0,34,540,720]}}
let responses={};
async function fetch(path, init) {
 calls.push({path,init,body:init?.body?JSON.parse(init.body):undefined});
 let data=responses[(init?.method||'GET')+':'+path]??responses[path];
 if(data===undefined) data=path==='/api/runs'?[]:path.includes('/events?after=')?[]:path.endsWith('/log')?'':path.startsWith('/api/runs/')?{run_id:'new',operation:'saved_scene_review',execution_status:'created'}:{};
 return {ok:true,json:async()=>data,text:async()=>typeof data==='string'?data:JSON.stringify(data)};
}
'''


def run_js(checks):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required for dashboard behavior checks')
    script = HTML.read_text().split('<script>', 1)[1].split('</script>', 1)[0]
    script = script.split('(async()=>{try{token=', 1)[0]
    result = subprocess.run([node, '-e', HARNESS + script + '\n(async()=>{' + checks + '})().catch(e=>{console.error(e);process.exit(1)});'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_review_checkbox_maps_to_api_without_automatic_repair():
    run_js(r'''
$('source').value='/saved'; $('seconds').value='900'; $('maxChanges').value='2';
responses['POST:/api/runs']={run_id:'new'};
$('allowRepair').checked=false; await $('create').onclick();
assert.equal(calls.find(c=>c.path==='/api/runs'&&c.body).body.max_batches,0);
assert.equal(calls.filter(c=>c.path.endsWith('/resume')).length,0);
calls.length=0; $('allowRepair').checked=true; await $('create').onclick();
assert.equal(calls.find(c=>c.path==='/api/runs'&&c.body).body.max_batches,2);
''')


def test_review_guidance_and_buttons_respect_run_state():
    run_js(r'''
const s={run_id:'r',operation:'saved_scene_review',quality_status:'not_passed',can_repair:true,allowed_actions:['repair','finish']};
paint({...s,execution_status:'running'});
assert.equal($('repair').disabled,true); assert.match($('guidance').textContent,/Wait for.*review/);
paint({...s,execution_status:'waiting_for_input'});
assert.equal($('repair').disabled,false); assert.equal($('finish').disabled,false); assert.match($('guidance').textContent,/Repair & review/);
paint({...s,execution_status:'completed',completion_reason:'user_finished'});
assert.equal($('repair').disabled,true); assert.equal($('continue').disabled,true); assert.match($('guidance').textContent,/new review/); assert.match($('guidance').textContent,/Quality not passed/);
paint({...s,execution_status:'completed',quality_status:'passed'}); assert.match($('guidance').textContent,/Quality passed/);
paint({...s,execution_status:'waiting_for_input',can_repair:false}); assert.equal($('repair').disabled,true); assert.match($('guidance').textContent,/Repair is unavailable/);
''')


def test_queue_cancellation_is_only_for_queued_tasks():
    run_js(r'''
selected='r'; const s={run_id:'r',operation:'saved_scene_review',execution_status:'created'};
paint({...s,queue_status:'queued'});
assert.equal($('execution').textContent,'Queued'); assert.equal($('continue').disabled,true); assert.equal($('cancelQueue').disabled,false);
await $('cancelQueue').onclick(); assert.ok(calls.find(c=>c.path==='/api/runs/r/cancel-queue'&&c.init.method==='POST'));
paint({...s,queue_status:'dispatching'}); assert.equal($('cancelQueue').disabled,true); assert.equal($('continue').disabled,true);
paint({...s,execution_status:'running',queue_status:'running'}); assert.equal($('cancelQueue').disabled,true);
''')


def test_full_scan_creation_and_explicit_closeout_actions():
    run_js(r'''
$('scan').value='/project/data/raw/test.tar'; $('scene').value='test'; responses['/api/full-runs']={run_id:'full'};
await $('rebuildScan').onclick();
assert.ok(calls.find(c=>c.path==='/api/runs/full/start'));
assert.equal(calls.find(c=>c.path==='/api/full-runs').body.prepare_only,false);
paint({run_id:'full',operation:'full_scan',execution_status:'waiting_for_input',can_repair:true,allowed_actions:['repair','finish']});
assert.equal($('repair').hidden,false); assert.equal($('finish').hidden,false); assert.equal($('repair').disabled,false);
assert.equal($('repair').textContent,'Repair one batch'); assert.equal($('finish').textContent,'Finish with issues');
assert.equal($('continue').hidden,true); assert.equal($('reviewCard').hidden,false);
''')


def test_scan_refresh_preserves_selection_and_user_scene():
    run_js(r'''
responses['/api/scans']=[{path:'/a.tar',label:'<script>bad</script>',scene:'suggested'}];
await scans(); $('scan').value='/a.tar'; $('scan').onchange(); assert.equal($('scene').value,'suggested');
$('scene').value='custom'; await scans(); assert.equal($('scan').value,'/a.tar'); assert.equal($('scene').value,'custom');
assert.equal($('scan').children[1].textContent,'<script>bad</script>');
''')


def test_main_controls_use_plain_language_and_advanced_limits():
    html = HTML.read_text()
    assert 'id="batches"' not in html
    assert 'id="allowRepair"' in html
    assert 'id="maxChanges"' in html
    assert 'An upper limit, not an automatic loop.' in html
    assert 'Repair &amp; review' in html
    assert 'GPU' in html and 'automatic refinement' in html and 'experimental' in html


def test_invalid_limits_do_not_create_tasks_and_busy_disables_controls():
    run_js(r'''
$('source').value='/saved'; $('allowRepair').checked=true; $('seconds').value='900'; $('maxChanges').value='4';
await $('create').onclick(); assert.equal(calls.length,0); assert.match($('notice').textContent,/1–3/);
paint({run_id:'r',operation:'saved_scene_review',execution_status:'waiting_for_input',allowed_actions:['repair','finish']});
setBusy(true); for(const id of ['create','repair','finish','rebuildScan'])assert.equal($(id).disabled,true);
setBusy(false); assert.equal($('repair').disabled,false);
''')


def test_full_run_outcome_labels_do_not_claim_quality_passed():
    run_js(r'''
paint({run_id:'f',operation:'full_scan',execution_status:'completed',quality_status:'human_review_pending',completion_reason:'pipeline_completed'});
assert.equal($('quality').textContent,'Human review needed'); assert.match($('completionState').textContent,/Pipeline completed/); assert.match($('guidance').textContent,/Quality has not been confirmed/);
paint({run_id:'f',operation:'full_scan',execution_status:'completed',completion_reason:'prepared_only'});
assert.match($('completionState').textContent,/Scan validated/);
paint({run_id:'q',operation:'full_scan',execution_status:'created',queue_status:'queued',queue_position:3});
assert.match($('guidance').textContent,/3/);
''')


def test_actual_full_completion_and_queue_cancellation_dispatch_priority():
    run_js(r'''
paint({run_id:'f',operation:'full_scan',execution_status:'completed',quality_status:'human_review_pending',completion_reason:'pipeline_completed_human_review_pending'});
assert.match($('completionState').textContent,/Pipeline completed.*Human quality review/);
paint({run_id:'q',operation:'saved_scene_review',execution_status:'created',queue_status:'cancelled'});
assert.match($('guidance').textContent,/Queue entry cancelled/); assert.equal($('continue').disabled,false); assert.equal($('cancelQueue').disabled,true);
paint({run_id:'q',operation:'saved_scene_review',execution_status:'queued',queue_status:'dispatching'});
assert.equal($('cancelQueue').disabled,true); assert.equal($('continue').disabled,true);
paint({run_id:'q',operation:'saved_scene_review',execution_status:'failed',queue_status:'failed',error:{node:'queue',message:'worker could not start'}});
assert.match($('runError').textContent,/queue.*worker could not start/); assert.equal($('continue').disabled,false);
''')


def test_english_light_layout_has_real_task_dialog_and_no_demo_content():
    import re
    html = HTML.read_text()
    assert '<html lang="en">' in html
    assert 'color-scheme:light' in html
    assert '<dialog id="newTask"' in html
    assert 'id="newTaskButton"' in html
    assert 'id="reviewForm"' in html and 'id="scanForm"' in html
    assert 'Repair &amp; review' in html
    assert 'DESIGN PREVIEW' not in html and '__ROOM_IMAGE__' not in html
    assert not re.search(r'[\u4e00-\u9fff]', html)


def test_task_dialog_routes_forms_and_keeps_validation_errors_visible():
    run_js(r'''
$('newTaskButton').onclick(); assert.equal($('newTask').open,true);
$('taskType').value='scan'; $('taskType').onchange();
assert.equal($('reviewForm').hidden,true); assert.equal($('scanForm').hidden,false);
$('scan').value=''; $('scene').value=''; await $('rebuildScan').onclick();
assert.equal(calls.length,0); assert.equal($('newTask').open,true);
assert.match($('formNotice').textContent,/Choose a scan/);
$('closeTask').onclick(); assert.equal($('newTask').open,false);
$('emptyNewTask').onclick(); $('taskType').value='review'; $('taskType').onchange();
assert.equal($('reviewForm').hidden,false); assert.equal($('scanForm').hidden,true);
$('source').value='/saved'; $('maxChanges').value='1'; $('seconds').value='900';
responses['POST:/api/runs']={run_id:'new'}; await $('create').onclick();
assert.equal($('newTask').open,false); assert.ok(calls.find(c=>c.path==='/api/runs/new/start'));
''')


def test_other_outputs_do_not_leak_into_room_downloads():
    run_js(r'''
selected='r';presentations.set('r',{comparisons:[],files:[{path:'Room.glb'}],other_files:[{path:'Object/<script>bad</script>.glb'}]});previewList([]);
assert.equal(flat($('resultDownloadLinks')).filter(e=>e.className==='button').length,1);
assert.ok(!flat($('resultDownloadLinks')).some(e=>e.textContent.includes('<script>')));
''')


def test_prepare_only_guidance_does_not_claim_reconstruction():
    run_js(r'''
const s={run_id:'f',operation:'full_scan',prepare_only:true};
paint({...s,execution_status:'running'}); assert.match($('guidance').textContent,/Checking.*scan/); assert.doesNotMatch($('guidance').textContent,/Reconstruction is running/);
paint({...s,execution_status:'completed',completion_reason:'prepared_only'}); assert.match($('guidance').textContent,/No reconstruction/);
''')


def test_output_versions_are_grouped_without_mixing_rounds():
    run_js(r'''
const rows=[
{path:'work/attempts/r00_build/a/round/room_preview/Room.glb'},
{path:'work/attempts/r00_build/a/round/room_preview/Room.portable.blend'},
{path:'work/attempts/r01_build/b/round/room_preview/Room.glb'},
{path:'work/attempts/r01_build/b/round/room_preview/Room.portable.blend'},
{path:'work/attempts/r01_build/b/round/room_preview/Room.glb'}];
const versions=groupOutputs(rows);
assert.deepEqual(versions.map(v=>v.round),[1,0]);
assert.equal(versions[0].rows.length,2);assert.equal(versions[1].rows.length,2);
assert.match(outputAction(rows[2]),/^GLB$/);
assert.match(outputAction(rows[3]),/^Blender$/);
assert.match(outputAction({path:'report.json'}),/Open report/);
''')


def test_resume_shows_request_feedback_and_posts_null_action():
    run_js(r'''
selected='failed';responses['/api/config']={token:'fresh'};
responses['/api/runs/failed']={run_id:'failed',operation:'saved_scene_review',execution_status:'failed',round:1,current_node:'evidence'};
responses['POST:/api/runs/failed/resume']={run_id:'failed',queue_status:'queued',queue_position:1};
paint(responses['/api/runs/failed']);await $('continue').onclick();
assert.ok(calls.find(c=>c.path==='/api/runs/failed/resume'&&c.body.action===null));
assert.match($('actionFeedback').textContent,/accepted|queued/i);
assert.equal(calls.find(c=>c.path.endsWith('/resume')).init.headers['X-Room-Token'],'fresh');
''')


def test_network_failure_has_visible_recovery_guidance_and_never_auto_retries():
    run_js(r'''
selected='r';paint({run_id:'r',operation:'saved_scene_review',execution_status:'failed'});
const originalFetch=fetch;let attempts=0;
fetch=async()=>{attempts++;throw new TypeError('Failed to fetch')};
await $('continue').onclick();
assert.equal($('connectionNotice').hidden,false);
assert.match($('connectionNotice').textContent,/server.*SSH tunnel/);
assert.match($('actionFeedback').textContent,/Cannot reach/);
assert.equal(calls.filter(c=>c.init?.method==='POST').length,0);
assert.equal($('continue').disabled,true);
fetch=originalFetch;await api('/api/config');
assert.equal($('connectionNotice').hidden,true);assert.equal($('continue').disabled,false);
''')


def test_task_status_updates_even_when_log_fetch_fails():
    run_js(r'''
selected='r';const originalFetch=fetch;
responses['/api/runs/r']={run_id:'r',operation:'saved_scene_review',execution_status:'running',round:1};
fetch=async(path,init)=>{if(path.endsWith('/log'))throw Error('Failed to fetch');return originalFetch(path,init)};
await refresh();assert.equal($('execution').textContent,'Running');
assert.match($('log').textContent,/Log unavailable/);
''')


def test_full_scan_outputs_group_by_stage_and_label_reference_images():
    run_js(r'''
selected='scan';presentations.set('scan',{comparisons:[],files:[]});
previewList([{path:'wood.png',stage:'seed'}],{operation:'full_scan'});
const all=flat($('previews')),text=all.map(x=>x.textContent).join(' ');
assert.match(text,/after their stage passes/);assert.doesNotMatch(text,/Seed|Ingest|Stage previews/);
assert.equal(all.filter(x=>x.tag==='image').length,0);assert.equal(nodeNames.seed,'Initial scene');
''')


def test_scan_dialog_has_one_reconstruction_button():
    html=HTML.read_text()
    assert 'id="checkScan"' not in html
    assert '>Start Reconstruction</button>' in html
    assert 'Scan check queued.' not in html


def test_workspace_sorts_by_recorded_time_and_resume_not_id():
    run_js(r"""
responses['/api/runs']=[
{run_id:'zz-old',room_id:'Old',execution_status:'failed'},
{run_id:'aa-new',room_id:'New',execution_status:'completed'}];
responses['/api/runs/zz-old/events?after=0']=[{seq:1,kind:'run_created',time:'2026-11-01T10:00:00Z'},{seq:2,kind:'run_started',time:'2026-11-01T11:00:00Z'}];
responses['/api/runs/aa-new/events?after=0']=[{seq:1,kind:'run_created',time:'2026-11-02T10:00:00Z'}];
await refresh();
assert.match($('runs').children[0].textContent,/New/);assert.equal(selected,'aa-new');
responses['/api/runs'][0].execution_status='running';
responses['/api/runs/zz-old/events?after=2']=[{seq:3,kind:'run_started',time:'2026-11-03T10:00:00Z'}];
await refresh();assert.match($('runs').children[0].textContent,/Old/);
assert.equal(selected,'aa-new'); // sorting never changes the scene being viewed
responses['/api/runs/aa-new/events?after=1']=[{seq:2,kind:'node_completed',time:'2026-11-04T10:00:00Z'}];
responses['/api/runs'][1].execution_status='running';
await refresh();assert.match($('runs').children[0].textContent,/Old/); // stage updates don't reorder tasks
""")


def test_reconstruction_queue_notice_clears_when_work_starts():
    run_js(r"""
$('scan').value='/project/data/raw/test.tar';$('scene').value='test';
responses['/api/full-runs']={run_id:'full'};
responses['/api/runs/full']={run_id:'full',operation:'full_scan',execution_status:'queued',queue_status:'queued'};
await $('rebuildScan').onclick();assert.match($('notice').textContent,/queued/i);
paint({run_id:'full',operation:'full_scan',execution_status:'running',current_node:'prepare'});
assert.equal($('notice').textContent,'');
""")


def test_numbered_preview_has_one_image_and_no_inferred_names():
    run_js(r'''
selected='run';const rows=[pair('a.png'),pair('b.png'),pair('c.png')];
const viewer=makePreviewViewer(rows,'run:views'),all=flat(viewer),prev=all.find(e=>e.className==='preview-prev'),next=all.find(e=>e.className==='preview-next'),count=all.find(e=>e.className==='preview-count');
const images=all.filter(e=>e.tag==='image'),svgs=all.filter(e=>e.tag==='svg');
assert.equal(images.length,2);assert.deepEqual(svgs.map(e=>e.attrs.viewBox),['548 34 540 720','0 34 540 720']);
assert.deepEqual(all.filter(e=>e.className==='preview-label').map(e=>e.textContent),['Captured','Rendered']);
assert.equal(count.textContent,'1 / 3');assert.equal(prev.disabled,true);
next.onclick();assert.equal(count.textContent,'2 / 3');assert(images.every(e=>e.attrs.href.includes('b.png')));
next.onclick();assert.equal(next.disabled,true);prev.onclick();assert.equal(count.textContent,'2 / 3');
assert.doesNotMatch(all.map(e=>e.textContent).join(' '),/Bay window|Entrance|More/);
''')


def test_preview_preserves_selected_file_when_outputs_refresh():
    run_js(r'''
selected='run';const a=pair('a.png'),b=pair('b.png');
let viewer=makePreviewViewer([a,b],'run:views');flat(viewer).find(e=>e.className==='preview-next').onclick();
viewer=makePreviewViewer([pair('new.png'),a,b],'run:views');
assert.equal(flat(viewer).find(e=>e.className==='preview-count').textContent,'3 / 3');
viewer=makePreviewViewer([a,b],'another:views');assert.equal(flat(viewer).find(e=>e.className==='preview-count').textContent,'1 / 2');
viewer=makePreviewViewer([a],'run:views');assert(flat(viewer).filter(e=>e.className==='preview-next'||e.className==='preview-prev').every(e=>e.disabled));
''')


def test_preview_image_failure_can_advance_to_next_image():
    run_js(r'''
selected='run';const all=flat(makePreviewViewer([pair('missing.png'),pair('pair_001.png')],'run:views'));
const img=all.find(e=>e.tag==='image'),svg=all.find(e=>e.tag==='svg'),error=all.find(e=>e.className==='preview-error');
img.onerror();assert.equal(error.hidden,false);assert.equal(svg.hidden,true);
all.find(e=>e.className==='preview-next').onclick();assert.equal(error.hidden,true);assert.equal(svg.hidden,false);assert.match(img.attrs.href,/pair_001.png/);
''')


def test_stage_preview_uses_carousel_and_keeps_files_separate():
    run_js(r'''
selected='scan';presentations.set('scan',{comparisons:[{key:'latest',pairs:[pair('a.png'),pair('b.png')]}],files:[{path:'Room.glb'}],other_files:[{path:'Object/chair.glb'}]});
previewList([{path:'wood.png',stage:'seed'}],{operation:'full_scan'});const all=flat($('previews'));
assert.equal(all.filter(e=>e.tag==='image').length,2);assert.equal(all.find(e=>e.className==='preview-count').textContent,'1 / 2');
const links=flat($('resultDownloadLinks'));assert.equal(links.find(e=>e.className==='button').textContent,'GLB');
assert.equal(links.filter(e=>e.className==='button').length,1);
''')


def test_failed_author_shows_draft_and_small_download_without_changing_controls():
    run_js(r'''
selected='r';const s={run_id:'r',operation:'full_scan',execution_status:'failed',error:{message:'needs_repair: support/visual acceptance failed'}};
paint(s);assert.equal($('execution').textContent,'Needs repair');
assert.match($('guidance').textContent,/Resume keeps saved edits/);
assert.equal($('diagnostics').hidden,false);assert.equal($('diagnostics').href,'/diagnostics?run_id=r');
assert.equal($('download').href,'/return?run_id=r');
presentations.set('r',{comparisons:[],files:[],diagnostics:{comparisons:[{draft:true,key:'backend/scenes/x',pairs:[pair('a.png')]}],files:[{path:'Room.glb',draft:true}]}});previewList([],s);
assert.equal(flat($('previews')).filter(x=>x.tag==='image').length,0);
assert.equal($('candidateDetails').hidden,false);
assert.equal(flat($('candidatePreviews')).filter(x=>x.tag==='image').length,2);
assert.match(flat($('candidatePreviews')).map(x=>x.textContent).join(' '),/Saved attempt files/);
assert.equal(flat($('previews')).filter(x=>x.tag==='image').length,0);
paint({...s,execution_status:'running'});assert.equal($('diagnostics').hidden,false);assert.equal($('diagnostics').attrs['aria-disabled'],'true');
''')


def test_full_scan_closeout_explains_bounds_and_uses_allowed_actions():
    run_js(r'''
const s={run_id:'full',operation:'full_scan',quality_status:'not_passed',current_node:'author_decision',round:2,allowed_actions:['repair','finish']};
paint({...s,execution_status:'waiting_for_input',score:6.2,repair_termination:'step_budget',issues:[{kind:'geometry',priority:'high',objects:['chair'],detail:'Chair intersects wall'}]});
assert.equal($('reviewScore').textContent,'6.2 / 10');
assert.match(flat($('reviewIssues')).map(e=>e.textContent).join(' '),/Geometry.*high.*chair.*Chair intersects wall/);
assert.match($('current').textContent,/Round 2.*Your decision/);
assert.match($('repairState').textContent,/step limit.*saved/i);
assert.match($('guidance').textContent,/Repair one batch/);
paint({...s,execution_status:'waiting_for_input',allowed_actions:['finish']});
assert.equal($('repair').disabled,true); assert.equal($('finish').disabled,false);
paint({...s,execution_status:'waiting_for_input',allowed_actions:undefined});
assert.equal($('repair').disabled,true); assert.equal($('finish').disabled,true);
for(const [reason,pattern] of [['no_progress',/no measurable improvement/i],['repair_budget_exhausted',/repair budget.*exhausted/i],['evidence_insufficient',/evidence.*insufficient/i],['user_finished',/your choice/i]]){
paint({...s,execution_status:'completed',completion_reason:reason,technical_export_verified:true});
assert.equal($('execution').textContent,'Completed with issues');assert.equal($('quality').textContent,'Quality not passed');
assert.equal($('continue').hidden,true);assert.equal($('repair').disabled,true);assert.equal($('finish').disabled,true);
assert.match($('completionState').textContent,pattern);assert.match($('guidance').textContent,/Diagnostics/);
assert.equal($('runError').textContent,'');
}
paint({...s,execution_status:'completed',quality_status:'passed',completion_reason:'quality_passed'});
assert.notEqual($('execution').textContent,'Completed with issues'); assert.equal($('quality').textContent,'Quality passed');
paint({...s,execution_status:'running',current_node:'author_repair'});assert.equal($('repairState').textContent,'Repair in progress');
''')
    assert 'v0.10.2' in HTML.read_text()
    assert 'does not use manual repair approvals' not in HTML.read_text()


def test_full_scan_final_publish_closeout_labels_do_not_offer_another_repair():
    run_js(r'''
paint({run_id:'r',operation:'full_scan',execution_status:'completed',quality_status:'not_passed',current_node:'draft_export',completion_reason:'final_publish_not_passed',allowed_actions:[],stages:[{node:'author_decision',status:'completed'},{node:'author_repair',status:'completed'},{node:'draft_export',status:'completed'}]});
assert.match($('current').textContent,/Export check/);
assert.match($('completionState').textContent,/Final.*quality.*not pass/i);
assert.deepEqual($('stages').children.map(x=>x.children[0].textContent),['Your decision','Repair & review','Export check']);
assert.equal($('repair').disabled,true);assert.equal($('continue').hidden,true);
''')


def test_full_scan_incomplete_review_retains_progress_without_quality_rejection():
    run_js(r'''
selected='r';
const progress={status:'incomplete',completed_groups:2,total_groups:4,completed_objects:60,total_objects:119,current_group:'group_003',error_kind:'timeout',timeout_seconds:600,imported_groups:2};
const s={run_id:'r',operation:'full_scan',execution_status:'failed',quality_status:'not_passed',score:7.5,review_progress:progress,error:{message:'Review timed out'}};
paint(s);
assert.equal($('execution').textContent,'Review incomplete');
assert.equal($('quality').textContent,'Review incomplete');
assert.equal($('reviewScore').textContent,'—');
assert.equal($('reviewCard').hidden,false);
assert.match($('reviewProgress').textContent,/2 \/ 4 groups.*60 \/ 119 objects/);
assert.match($('reviewProgress').textContent,/group_003/);
assert.match($('reviewSummary').textContent,/not a quality rejection/i);
assert.match($('guidance').textContent,/saved groups.*retained/i);
assert.match($('guidance').textContent,/Resume.*unfinished/i);
assert.equal($('continue').disabled,false);assert.equal($('continue').hidden,false);
assert.equal($('repair').disabled,true);assert.equal($('finish').disabled,true);
assert.equal($('diagnostics').hidden,false);assert.equal($('diagnostics').href,'/diagnostics?run_id=r');
assert.equal(s.execution_status,'failed');
paint({...s,error:{message:'needs_repair: old failure'}});assert.equal($('execution').textContent,'Review incomplete');
paint({...s,execution_status:'queued',queue_status:'queued'});
assert.equal($('execution').textContent,'Queued');assert.match($('guidance').textContent,/Queued/);assert.equal($('continue').disabled,true);
''')


def test_full_scan_review_progress_shows_before_author_review_and_clears_between_runs():
    run_js(r'''
const progress={status:'running',completed_groups:2,total_groups:4,completed_objects:60,total_objects:119,current_group:'<group_003>',error_kind:null,timeout_seconds:600,imported_groups:2};
paint({run_id:'r',operation:'full_scan',execution_status:'running',score:8,review_progress:progress});
assert.equal($('reviewCard').hidden,false);assert.equal($('reviewProgress').hidden,false);
assert.match($('reviewProgress').textContent,/2 \/ 4 groups.*60 \/ 119 objects.*<group_003>/);
assert.equal($('reviewScore').textContent,'—');assert.equal($('continue').disabled,true);
paint({run_id:'r',operation:'full_scan',execution_status:'waiting_for_input',score:8,review_progress:{...progress,status:'complete',completed_groups:4,completed_objects:119,current_group:null},allowed_actions:['repair','finish']});
assert.match($('reviewProgress').textContent,/4 \/ 4 groups.*119 \/ 119 objects/);
assert.doesNotMatch($('reviewProgress').textContent,/group_003/);
assert.equal($('reviewScore').textContent,'8.0 / 10');assert.equal($('repair').disabled,false);
paint({run_id:'saved',operation:'saved_scene_review',execution_status:'waiting_for_input',score:7.5});
assert.equal($('reviewProgress').hidden,true);assert.equal($('reviewProgress').textContent,'');
assert.equal($('reviewScore').textContent,'7.5 / 10');assert.equal($('repair').textContent,'Repair & review');
''')


def test_finish_and_download_wait_for_queue_finalization_then_enable():
    run_js(r'''
selected='r';connected=true;
const s={run_id:'r',operation:'full_scan',execution_status:'waiting_for_input',quality_status:'not_passed',allowed_actions:['repair','finish'],queue_status:'running',worker_finalizing:true};
paint(s);
assert.equal($('finish').disabled,true);assert.equal($('repair').disabled,true);
assert.match($('guidance').textContent,/finishing|Finishing/);
assert.equal($('diagnostics').hidden,false);assert.equal($('diagnostics').attrs['aria-disabled'],'true');assert.equal($('download').hidden,true);
paint({...s,queue_status:'waiting_for_input',worker_finalizing:false});
assert.equal($('finish').disabled,false);assert.equal($('repair').disabled,false);
assert.equal($('diagnostics').hidden,false);
''')
