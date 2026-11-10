"""Manual Summary width must be persistent and never enqueue work."""
from test_dashboard_stage3 import HTML, run_js


def test_resize_direction_bounds_persistence_and_no_work():
    run_js(r'''
globalThis.innerWidth=1600;const saved=new Map();globalThis.localStorage={getItem:k=>saved.get(k)??null,setItem:(k,v)=>saved.set(k,String(v)),removeItem:k=>saved.delete(k)};
setSummaryOpen(true,false);setSidebarOpen(true,false);setSummaryWidth(370);
const handle=$('summaryResize'),before=calls.length;
handle.onpointerdown({button:0,pointerId:1,clientX:1230,preventDefault(){}});
handle.onpointermove({pointerId:1,clientX:1000});assert.equal(summaryWidth,600);
handle.onpointermove({pointerId:2,clientX:800});assert.equal(summaryWidth,600);
handle.onpointerup({pointerId:1});assert.equal(saved.get('spatial-agent.summary-width'),'600');
handle.onkeydown({key:'ArrowRight',preventDefault(){}});assert.equal(summaryWidth,576);
handle.onkeydown({key:'ArrowLeft',preventDefault(){}});assert.equal(summaryWidth,600);
setSummaryWidth(5000);assert.equal(summaryWidth,900);setSummaryWidth(1);assert.equal(summaryWidth,300);
assert.equal(calls.length,before);
''')


def test_saved_width_reclamps_without_forgetting_preference():
    run_js(r'''
const saved=new Map([['spatial-agent.summary-width','700']]);globalThis.localStorage={getItem:k=>saved.get(k)??null,setItem:(k,v)=>saved.set(k,String(v)),removeItem:k=>saved.delete(k)};
globalThis.innerWidth=1600;restoreSummaryWidth();assert.equal(summaryWidth,700);
globalThis.innerWidth=1150;applySummaryWidth();assert.equal(summaryWidth,540);assert.equal(saved.get('spatial-agent.summary-width'),'700');
globalThis.innerWidth=1600;applySummaryWidth();assert.equal(summaryWidth,700);
setSummaryOpen(false,false);assert.equal($('summaryResize').hidden,true);setSummaryOpen(true,false);assert.equal(summaryWidth,700);
$('summaryResize').ondblclick();assert.equal(summaryWidth,370);assert.equal(saved.has('spatial-agent.summary-width'),false);
saved.set('spatial-agent.summary-width','oops');restoreSummaryWidth();assert.equal(summaryWidth,370);
''')


def test_small_viewport_and_pointer_cancel_leave_no_drag_active():
    run_js(r'''
globalThis.innerWidth=1600;setSummaryOpen(true,false);setSummaryWidth(500);
$('summaryResize').onpointerdown({button:0,pointerId:3,clientX:1000,preventDefault(){}});
$('summaryResize').onpointercancel({pointerId:3});assert.equal(summaryDrag,null);
$('summaryResize').onpointerdown({button:0,pointerId:4,clientX:1000,preventDefault(){}});
setSummaryOpen(false,false);assert.equal(summaryDrag,null);
globalThis.innerWidth=900;setSummaryOpen(true,false);assert.equal($('summaryResize').hidden,true);
$('summaryResize').onpointerdown({button:0,pointerId:5,clientX:400,preventDefault(){}});assert.equal(summaryDrag,null);
assert.equal(calls.length,0);
''')


def test_separator_and_logo_have_stable_accessible_markup():
    html=HTML.read_text();body=html.split('</style>')[1].split('<script>')[0]
    assert 'id="summaryResize"' in body and 'role="separator"' in body
    assert 'aria-orientation="vertical"' in body and 'aria-controls="summaryPanel"' in body
    assert '<span class="logo" aria-hidden="true"><svg' in body
    assert '⌂' not in body
    assert '.logo svg{width:20px;height:20px;display:block}' in html
    assert 'touch-action:none' in html and 'cursor:col-resize' in html
