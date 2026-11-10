from html.parser import HTMLParser
from test_dashboard_stage3 import HTML,run_js

def test_default_open_panel_and_text_composer_have_no_dispatch_button():
    class P(HTMLParser):
        def __init__(self):super().__init__();self.ids={}
        def handle_starttag(self,tag,attrs):
            a=dict(attrs)
            if 'id' in a:self.ids[a['id']]=(tag,a)
    p=P();p.feed(HTML.read_text())
    assert p.ids['conversationPanel'][0]=='section' and 'hidden' not in p.ids['conversationPanel'][1]
    assert p.ids['chatInput'][0]=='textarea'
    assert 'chatSend' in p.ids and 'chatMic' not in p.ids
    assert 'confirmPlan' in p.ids and 'hidden' in p.ids['confirmPlan'][1]

def test_send_is_planning_only_and_plan_uses_text_nodes():
    run_js(r'''
selectRun('one');
responses['/api/runs/one/conversation']={revision:0,run_id:'one',turns:[]};
await loadConversation();$('chatInput').value='把地板改成灰色';
responses['POST:/api/runs/one/conversation']={revision:1,run_id:'one',turns:[{message:'把地板改成灰色',status:'completed',result:{reply:'<img src=x onerror=alert(1)>',questions:[],plan:{target_run_id:'one',change:'灰色',preserve:['家具'],steps:['渲染'],expected_outputs:['新版本']}}}]};
await sendConversation();
assert.equal(calls.filter(c=>c.init.method==='POST').length,1);
assert.equal(calls.find(c=>c.body?.message).path,'/api/runs/one/conversation');
assert.ok(flat($('chatHistory')).some(e=>e.textContent==='<img src=x onerror=alert(1)>'));
assert.ok(flat($('chatHistory')).some(e=>String(e.textContent).includes('Plan ready')));
assert.ok(flat($('planContent')).some(e=>e.textContent==='灰色'));
assert.equal($('planSummary').hidden,false);
assert.equal($('chatInput').value,'');
''')

def test_switch_keeps_drafts_and_stale_response_cannot_replace_other_room():
    run_js(r'''
selectRun('one');$('chatInput').value='one draft';$('chatInput').oninput();
selectRun('two');assert.equal($('chatInput').value,'');
$('chatInput').value='two draft';$('chatInput').oninput();
selectRun('one');assert.equal($('chatInput').value,'one draft');
paintConversation({run_id:'two',revision:2,turns:[{message:'wrong',status:'completed'}]});
assert.ok(!flat($('chatHistory')).some(e=>e.textContent==='wrong'));
assert.equal($('conversationPanel').hidden,false);
''')

def test_voice_removed_and_assistant_has_icon_without_status_disclosure():
    source=HTML.read_text()
    assert 'SpeechRecognition' not in source and 'startDictation' not in source
    assert 'Dictate' not in source and 'Status when sent' not in source
    run_js(r'''
selectRun('one');const facts={execution_status:'completed',quality_status:'not_passed'};
paintConversation({run_id:'one',revision:1,turns:[{message:'Hi',status:'completed',facts,result:{reply:'Hello'}}]});
const nodes=flat($('chatHistory'));
assert.ok(nodes.some(e=>e.className==='chat-agent-icon'&&e.attrs['aria-hidden']==='true'));
assert.ok(nodes.some(e=>e.tag==='svg'));
assert.ok(nodes.some(e=>e.textContent==='Spatial Agent'));
assert.equal(chatStates.get('one').turns[0].facts.quality_status,'not_passed');
assert.ok(!nodes.some(e=>e.textContent==='Status when sent'));
''')


def test_enter_sends_shift_enter_and_ime_never_send():
    run_js(r'''
selectRun('one');responses['/api/runs/one/conversation']={revision:0,run_id:'one',turns:[]};await loadConversation();
$('chatInput').value='Hello';let prevented=0;
const event=extra=>({key:'Enter',preventDefault(){prevented++},...extra});
await $('chatInput').onkeydown(event({shiftKey:true}));
await $('chatInput').onkeydown(event({isComposing:true}));
await $('chatInput').onkeydown(event({keyCode:229}));
await $('chatInput').onkeydown(event({repeat:true}));
$('chatInput').oncompositionstart();await $('chatInput').onkeydown(event({}));$('chatInput').oncompositionend();
assert.equal(prevented,0);assert.equal(calls.filter(c=>c.init.method==='POST').length,0);
responses['POST:/api/runs/one/conversation']={revision:1,run_id:'one',turns:[{request_id:'m1',message:'Hello',status:'completed',result:{reply:'Hi'}}]};
await $('chatInput').onkeydown(event({}));
assert.equal(prevented,1);assert.equal(calls.filter(c=>c.init.method==='POST').length,1);
assert.equal($('chatInput').value,'');
await $('chatInput').onkeydown(event({}));assert.equal(calls.filter(c=>c.init.method==='POST').length,1);
''')


def test_lost_response_recovery_clears_only_matching_sent_draft():
    run_js(r'''
selectRun('one');$('chatInput').value='sent text';chatDrafts.set('one','sent text');
chatUnconfirmed.set('one',{request_id:'m1',message:'sent text'});
paintConversation({run_id:'one',revision:1,turns:[{request_id:'m1',message:'sent text',status:'completed',result:{reply:'Done planning',questions:[]}}]});
assert.equal(chatUnconfirmed.has('one'),false);assert.equal($('chatInput').value,'');
assert.equal(chatDrafts.get('one'),'');
chatUnconfirmed.set('one',{request_id:'m2',message:'old'});$('chatInput').value='new unsent text';
paintConversation({run_id:'one',revision:2,turns:[{request_id:'m2',message:'old',status:'completed',result:{reply:'Plan',questions:[]}}]});
assert.equal($('chatInput').value,'new unsent text');
''')


def test_evidence_shows_two_thumbnails_without_source_and_opens_zoom():
    run_js(r'''
selected='root';paintConversation({run_id:'root',revision:1,turns:[{request_id:'q',status:'completed',message:'Inspect',result:{kind:'answer',reply:'Checked',evidence:[{kind:'image',name:'pair_00015.png',run_id:'accepted',path:'work/pairs/pair_00015.png'},{kind:'image',name:'pair_00019.png',run_id:'accepted',path:'work/pairs/pair_00019.png'},{kind:'source',name:'Room.py',excerpt:'SECRET SOURCE'}]}}]});
const nodes=flat($('chatHistory')),gallery=nodes.find(x=>x.className==='evidence-grid');
assert.ok(gallery);assert.equal(gallery.children.length,2);
assert.ok(!nodes.some(x=>x.textContent==='SECRET SOURCE'||String(x.textContent).includes('Room.py')));
const thumbs=gallery.children;assert.ok(thumbs.every(x=>x.className==='evidence-thumb'));
assert.ok(thumbs[0].children[0].src.includes('run_id=accepted'));
thumbs[0].onclick();assert.equal($('evidenceDialog').open,true);
assert.equal($('evidenceImage').src,thumbs[0].children[0].src);
$('closeEvidence').onclick();assert.equal($('evidenceDialog').open,false);
assert.equal(calls.filter(c=>c.body).length,0);
''')
    source=HTML.read_text()
    assert '.evidence-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))' in source
    assert 'object-fit:contain' in source


def test_source_only_evidence_does_not_show_empty_gallery():
    run_js(r'''
selected='root';paintConversation({run_id:'root',revision:1,turns:[{status:'completed',message:'Inspect source',result:{reply:'Material checked',evidence:[{kind:'source',name:'Room.py',excerpt:'PRIVATE SOURCE'}]}}]});
assert.ok(!flat($('chatHistory')).some(x=>x.className==='chat-evidence'||x.textContent==='PRIVATE SOURCE'));
assert.equal(chatStates.get('root').turns[0].result.evidence[0].excerpt,'PRIVATE SOURCE');
''')
