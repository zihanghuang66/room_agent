"""Approved conversation-first layout; no task execution on presentation changes."""
from html.parser import HTMLParser
from test_dashboard_stage3 import HTML,run_js

class Structure(HTMLParser):
    def __init__(self):super().__init__();self.stack=[];self.ids={}
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if a.get('id'):
            assert a['id'] not in self.ids
            self.ids[a['id']]=(tag,a,tuple(self.stack))
        if tag not in ('input','meta','br','img','hr','link','path','rect','line','circle'):self.stack.append(a.get('id',tag))
    def handle_endtag(self,tag):
        if tag not in ('path','rect','line','circle') and self.stack:self.stack.pop()

def test_three_panels_keep_conversation_open_and_results_in_summary():
    p=Structure();p.feed(HTML.read_text().split('<script>')[0])
    assert p.ids['conversationPanel'][0]=='section'
    assert p.ids['summaryPanel'][0]=='aside'
    assert 'summaryPanel' not in p.ids['chatInput'][2]
    for id in ('resultCard','planSummary','progressDetails','reviewDetails','runMenu','activityDetails'):
        assert 'summaryPanel' in p.ids[id][2]
    assert p.ids['summaryToggle'][1]['aria-controls']=='summaryPanel'
    for id in ('sidebarToggle','summaryToggle'):
        assert 'workspaceSidebar' not in p.ids[id][2] and 'summaryPanel' not in p.ids[id][2]
    assert p.ids['progressDetails'][0]=='details'
    assert 'open' not in p.ids['progressDetails'][1]
    assert 'runMenu' in p.ids['resultDownloads'][2]

def test_independent_panels_persist_and_never_submit_tasks():
    run_js(r'''
const saved=new Map();globalThis.localStorage={getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,String(v))};
setSidebarOpen(true,false);setSummaryOpen(true,false);const before=calls.length;
$('summaryToggle').onclick();assert.equal($('summaryPanel').hidden,true);assert.equal($('workspaceSidebar').hidden,false);
$('sidebarToggle').onclick();assert.equal($('workspaceSidebar').hidden,true);assert.equal($('summaryPanel').hidden,true);
assert.equal($('workspaceShell').className,'shell sidebar-collapsed summary-collapsed');
$('summaryToggle').onclick();assert.equal($('summaryPanel').hidden,false);assert.equal($('workspaceSidebar').hidden,true);
restorePanels();assert.equal($('workspaceSidebar').hidden,true);assert.equal($('summaryPanel').hidden,false);
selectRun('next');assert.equal($('workspaceSidebar').hidden,true);assert.equal($('summaryPanel').hidden,false);
assert.equal(calls.length,before);
''')

def test_latest_plan_only_and_later_message_cannot_leave_old_plan_current():
    run_js(r'''
selectRun('one');const plan={target_run_id:'one',change:'Floor gray',preserve:['Furniture'],steps:['Build','Review'],expected_outputs:['New version']};
paintConversation({run_id:'one',revision:1,turns:[{request_id:'m1',message:'gray',status:'completed',result:{reply:'Plan ready',plan}}]});
assert.equal($('planSummary').hidden,false);
assert.ok(flat($('planContent')).some(e=>e.textContent==='Floor gray'));
assert.ok(!flat($('chatHistory')).some(e=>e.textContent==='Floor gray'));
paintConversation({run_id:'one',revision:2,turns:[{request_id:'m1',message:'gray',status:'completed',result:{reply:'Plan ready',plan}},{request_id:'m2',message:'Actually make it warmer',status:'completed',result:{reply:'Which tone?',questions:['Which tone?']}}]});
assert.equal($('planSummary').hidden,true);
const link=flat($('chatHistory')).find(e=>e.textContent==='View earlier draft');assert.ok(link);link.onclick();
assert.equal($('planSummary').hidden,false);assert.equal($('planLabel').textContent,'Earlier draft · Not current');
selectRun('two');assert.equal($('planSummary').hidden,true);
''')

def test_hidden_summary_still_exposes_task_state_and_action_shortcut():
    run_js(r'''
selectRun('r');setSummaryOpen(false,false);
paint({run_id:'r',room_id:'Room',operation:'full_scan',execution_status:'waiting_for_input',quality_status:'not_passed',can_repair:true,allowed_actions:['repair','finish']});
assert.equal($('summaryPanel').hidden,true);assert.match($('conversationTaskState').textContent,/decision/i);
assert.equal($('showTaskActions').hidden,false);$('showTaskActions').onclick();
assert.equal($('summaryPanel').hidden,false);assert.equal($('finish').disabled,false);assert.equal(calls.length,0);
''')

def test_layout_bounds_scrolling_and_small_screens():
    css=HTML.read_text().split('</style>')[0]
    assert 'height:100dvh' in css
    assert '#chatHistory{flex:1;min-height:0;max-height:none;' in css
    assert '#summaryPanel{min-width:0;min-height:0;overflow-y:auto;' in css
    assert '.chat-composer-dock{flex:none;' in css
    assert '@media(max-width:1100px)' in css and '@media(max-width:760px)' in css
    assert '.shell.summary-collapsed{--summary-width:0px}' in css

def test_snapshot_expansion_uses_accepted_views_only_and_closes_on_switch():
    run_js(r'''
selectRun('one');presentations.set('one',{comparisons:[{pairs:[pair('approved.png')]}],files:[],diagnostics:{comparisons:[{pairs:[pair('draft.png')]}]}});
currentStatus={run_id:'one'};previewList([],{});assert.equal($('expandSnapshot').hidden,false);$('expandSnapshot').onclick();
assert.equal($('snapshotDialog').open,true);
assert.ok(flat($('snapshotLarge')).some(e=>e.attrs.href?.includes('approved.png')));
assert.ok(!flat($('snapshotLarge')).some(e=>e.attrs.href?.includes('draft.png')));
selectRun('two');assert.equal($('snapshotDialog').open,false);
presentations.set('two',{comparisons:[],files:[],diagnostics:{comparisons:[{pairs:[pair('draft.png')]}]}});
previewList([],{});assert.equal($('expandSnapshot').hidden,true);
''')

def test_pending_or_failed_planning_clears_current_draft_and_stale_links_stay_historical():
    run_js(r'''
selectRun('one');const plan={target_run_id:'one',change:'Floor gray',preserve:['Furniture'],steps:['Build'],expected_outputs:['Version']};
paintConversation({run_id:'one',revision:1,turns:[{request_id:'m1',message:'gray',status:'completed',result:{reply:'Plan ready',plan}}]});
const oldLink=flat($('chatHistory')).find(e=>e.textContent==='Plan ready · View summary');
paintConversation({run_id:'one',revision:2,turns:[{request_id:'m1',message:'gray',status:'completed',result:{reply:'Plan ready',plan}},{request_id:'m2',message:'red instead',status:'planning'}]});
assert.equal($('planSummary').hidden,true);oldLink.onclick();assert.equal($('planLabel').textContent,'Earlier draft · Not current');
''')

def test_literal_dom_references_exist_in_real_document():
    import re
    p=Structure();p.feed(HTML.read_text().split('<script>')[0])
    script=HTML.read_text().split('<script>',1)[1].split('</script>',1)[0]
    referenced=set(re.findall(r'''\$\(['"]([^'"]+)['"]\)''',script))
    assert referenced<=set(p.ids), sorted(referenced-set(p.ids))
