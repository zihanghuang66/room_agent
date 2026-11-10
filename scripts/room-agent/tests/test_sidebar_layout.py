"""UI-only sidebar and always-visible progress; no task actions on layout changes."""
from html.parser import HTMLParser
from test_dashboard_stage3 import HTML, run_js


def test_progress_is_fixed_open_and_hides_internal_identifiers():
    class Tags(HTMLParser):
        def __init__(self):super().__init__();self.ids={}
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if attrs.get('id'):self.ids[attrs['id']]=(tag,attrs)
    p=Tags();p.feed(HTML.read_text().split('<script>')[0])
    assert p.ids['stepDetails'][0]=='div'
    assert 'runId' not in p.ids and 'parentVersion' not in p.ids
    assert p.ids['sidebarToggle'][1]['aria-controls']=='workspaceSidebar'
    assert p.ids['workspaceSidebar'][0]=='aside'


def test_compact_sidebar_uses_screen_edge_instead_of_centered_max_width():
    css=HTML.read_text().split('</style>')[0]
    assert '.shell{max-width:none;width:100%;margin:0;' in css
    assert '#workspaceSidebar{padding:22px 12px 12px;' in css
    assert '.shell.sidebar-collapsed{--workspace-width:0px;' in css


def test_toggle_keeps_task_and_can_always_reopen_without_request():
    run_js(r'''
selected='current-task';
assert.equal($('workspaceSidebar').hidden,false);
assert.equal($('sidebarToggle').attrs['aria-expanded'],'true');
const before=calls.length;
$('sidebarToggle').onclick();
assert.equal($('workspaceSidebar').hidden,true);
assert.equal($('workspaceShell').className,'shell sidebar-collapsed');
assert.equal($('sidebarToggle').hidden,false);
assert.equal($('sidebarToggle').attrs['aria-expanded'],'false');
paint({run_id:selected,operation:'scene_edit',execution_status:'running',stages:[{node:'build',status:'running'}]});
assert.equal($('workspaceSidebar').hidden,true);
assert.equal($('stages').children.length,1);
$('sidebarToggle').onclick();
assert.equal($('workspaceSidebar').hidden,false);
assert.equal($('workspaceShell').className,'shell');
assert.equal(selected,'current-task');assert.equal(calls.length,before);
''')


def test_sidebar_preference_restores_and_storage_denial_is_safe():
    run_js(r'''
const saved=new Map();
globalThis.localStorage={getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,String(v))};
setSidebarOpen(false);
assert.equal(saved.get('spatial-agent.sidebar-hidden'),'true');
setSidebarOpen(true,false);restoreSidebar();
assert.equal($('workspaceSidebar').hidden,true);
selectRun('next-task');assert.equal($('workspaceSidebar').hidden,true);
globalThis.localStorage={getItem(){throw Error('blocked')},setItem(){throw Error('blocked')}};
restoreSidebar();assert.equal($('workspaceSidebar').hidden,false);
setSidebarOpen(false);assert.equal($('workspaceSidebar').hidden,true);
''')
