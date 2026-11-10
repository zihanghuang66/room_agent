"""The compact workspace must retain recovery actions and version boundaries."""
from html.parser import HTMLParser
from test_dashboard_stage3 import HTML, run_js


def test_static_disclosures_and_unique_bundle_entries():
    class Structure(HTMLParser):
        def __init__(self):
            super().__init__();self.stack=[];self.ids={}
        def handle_starttag(self, tag, attrs):
            attrs=dict(attrs)
            if attrs.get('id'):
                assert attrs['id'] not in self.ids
                self.ids[attrs['id']]=(attrs,tuple(self.stack))
            if tag not in ('input','meta','link','br','img','hr'):
                self.stack.append(attrs.get('id',tag))
        def handle_endtag(self, tag):
            if self.stack:self.stack.pop()
    p=Structure();p.feed(HTML.read_text().split('<script>')[0])
    for name in ('activityDetails','reviewDetails','runMenu','candidateDetails'):
        assert name in p.ids
        assert 'open' not in p.ids[name][0]
    assert 'runMenu' in p.ids['diagnostics'][1]
    assert 'activityDetails' in p.ids['download'][1]
    assert 'activityDetails' in p.ids['candidatePreviews'][1]
    assert 'stepDetails' in p.ids['stages'][1]
    assert 'activityDetails' not in p.ids['continue'][1]
    assert 'stepDetails' not in p.ids['finish'][1]
    assert 'open' not in p.ids['stepDetails'][0]  # fixed div, not a disclosure
    assert 'activityDetails' not in p.ids['reviewDetails'][1]
    assert 'reviewDetails' in p.ids['reviewCard'][1]
    assert 'runMenu' in p.ids['resultDownloads'][1]
    assert '<details id="resultDownloads"' not in HTML.read_text()
    assert 'class="file-links"' in HTML.read_text()
    assert 'Spatial Agent' in HTML.read_text() and '>Room Agent<' not in HTML.read_text()
    # File controls participate in normal flow. The old absolute-positioned
    # .menu-links container was clipped by the surrounding overflow:hidden card.
    assert 'class="menu-links"' not in HTML.read_text().split('<body>')[1].split('<script>')[0]
    assert '.file-links{position:static;display:flex;flex-wrap:wrap;' in HTML.read_text()


def test_edit_action_moves_to_the_selected_approved_or_candidate_version():
    run_js(r'''
selected='r';const s={run_id:'r',operation:'scene_edit',execution_status:'completed',quality_status:'passed'};
paint(s);assert.equal($('editVersion').parentNode,$('resultActions'));
assert.equal($('editVersion').hidden,false);
paint({...s,quality_status:'not_passed'});
assert.equal($('editVersion').parentNode,$('resultActions'));
assert.equal($('editVersion').hidden,false);
paint({...s,execution_status:'running'});assert.equal($('editVersion').hidden,true);
''')


def test_candidates_stay_out_of_main_results_and_downloads():
    run_js(r'''
selected='r';presentations.set('r',{comparisons:[],files:[],diagnostics:{comparisons:[{pairs:[pair('draft.png')]}],files:[{path:'draft/Room.glb'}]}});
previewList([],{});
assert.equal(flat($('previews')).filter(e=>e.tag==='image').length,0);
assert.equal(flat($('candidatePreviews')).filter(e=>e.tag==='image').length,2);
assert.equal($('resultDownloads').hidden,true);
assert(!flat($('previews')).some(e=>e.textContent==='Diagnostics'||e.textContent==='Results'));
presentations.set('r',{comparisons:[{pairs:[pair('approved.png')]}],files:[{path:'approved/Room.glb'}],diagnostics:{}});
previewList([],{});
assert.equal($('resultDownloads').hidden,false);
assert(flat($('resultDownloadLinks')).some(e=>e.href?.includes('approved')));
assert(!flat($('resultDownloadLinks')).some(e=>e.href?.includes('draft')));
''')


def test_recovery_and_decision_actions_remain_visible_outside_details():
    run_js(r'''
const s={run_id:'r',operation:'full_scan',quality_status:'not_passed'};
paint({...s,execution_status:'failed',current_node:'author',error:{message:'network failed'}});
assert.equal($('actionPanel').hidden,false);assert.equal($('continue').hidden,false);assert.equal($('continue').disabled,false);
paint({...s,execution_status:'waiting_for_input',can_repair:true,allowed_actions:['repair','finish']});
assert.equal($('actionPanel').hidden,false);assert.equal($('finish').disabled,false);assert.equal($('repair').disabled,false);
paint({...s,execution_status:'running',current_node:'review'});
assert.equal($('actionPanel').hidden,true);assert.match($('stepSummary').textContent,/review/i);
paint({...s,execution_status:'completed'});assert.equal($('actionPanel').hidden,true);
''')


def test_menu_escape_restores_focus_and_task_switch_closes_details():
    run_js(r'''
$('runMenu').open=true;let prevented=false;
$('runMenu').onkeydown({key:'Escape',preventDefault(){prevented=true}});
assert.equal($('runMenu').open,false);assert.equal(document.activeElement,$('moreActions'));assert(prevented);
$('activityDetails').open=true;$('runMenu').open=true;
selectRun('next');assert.equal($('activityDetails').open,false);assert.equal($('runMenu').open,false);
''')


def test_task_switch_cannot_edit_stale_preview():
    run_js(r'''
selected='old';paint({run_id:'old',operation:'scene_edit',execution_status:'completed',quality_status:'passed'});
selectRun('new');assert.equal($('workspace').hidden,true);
await $('editVersion').onclick();assert.equal(calls.length,0);assert(!$('newTask').open);
''')


def test_direct_edit_needs_no_version_picker_and_binds_original_parent():
    run_js(r'''
selected='old';paint({run_id:'old',room_id:'Bedroom',operation:'scene_edit',execution_status:'completed',quality_status:'not_passed'});
await $('editVersion').onclick();
assert.equal($('newTask').open,true);assert.equal($('taskPicker').hidden,true);assert.equal($('versionPicker').hidden,true);
assert.match($('editingVersion').textContent,/Bedroom.*old/);assert.equal(calls.length,0);
$('version').value='wrong';$('editInstruction').value='Change the floor to dark grey.';
responses['/api/config']={token:'test'};responses['POST:/api/edit-runs']={run_id:'child'};
await $('createEdit').onclick();
assert.equal(calls.find(c=>c.path==='/api/edit-runs').body.parent_run_id,'old');
calls.length=0;$('newTaskButton').onclick();
assert.equal($('taskPicker').hidden,false);assert.equal($('versionPicker').hidden,false);
$('version').value='another';$('editInstruction').value='Change floor colour';responses['POST:/api/edit-runs']={run_id:'child2'};
await $('createEdit').onclick();assert.equal(calls.find(c=>c.path==='/api/edit-runs').body.parent_run_id,'another');
''')


def test_review_summary_reports_issue_count_without_opening_details():
    run_js(r'''
const s={run_id:'r',operation:'scene_edit',execution_status:'completed',quality_status:'not_passed',issues:[{detail:'First'},{detail:'Second'}],score:7};
paint(s);assert.match($('reviewBadge').textContent,/2 findings/);
$('reviewDetails').open=true;
paint(s);assert.equal($('reviewDetails').open,true);
paint({...s,issues:[],quality_status:'passed'});assert.match($('reviewBadge').textContent,/Passed/);
paint({...s,operation:'full_scan',review_progress:{status:'running',completed_groups:1,total_groups:3}});
assert.equal($('reviewBadge').textContent,'Review in progress');
''')
