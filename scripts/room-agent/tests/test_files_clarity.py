from test_dashboard_stage3 import HTML,run_js

def test_only_portable_blender_is_offered():
    run_js(r'''
const holder=new Element();selected='session';
outputLinks(holder,[{path:'preview/Room.blend',run_id:'ordinary'},{path:'preview/Room.portable.blend',run_id:'portable'},{path:'preview/Room.glb',run_id:'portable'}]);
assert.deepEqual(holder.children.map(e=>e.textContent),['Blender','GLB']);
assert.equal(holder.children[0].download,'Room.portable.blend');assert.match(holder.children[0].href,/run_id=portable/);
assert.equal(holder.children[0].title,'Room.portable.blend');
const old=new Element();outputLinks(old,[{path:'Room.blend'}]);assert.equal(old.children.length,0);
selected='old';presentations.set('old',{files:[{path:'Room.blend'}],comparisons:[],diagnostics:{files:[{path:'candidate/Room.blend'}]}});
previewList([],{});assert.equal($('resultDownloads').hidden,true);assert.equal($('candidateDetails').hidden,true);
''')


def test_diagnostics_always_visible_but_busy_click_cannot_download():
    run_js(r'''
selected='r';
for(const status of [{execution_status:'queued'},{execution_status:'running'},{execution_status:'completed',worker_finalizing:true}]){
paint({run_id:'r',operation:'scene_edit',...status});
assert.equal($('diagnostics').hidden,false);assert.equal($('diagnostics').attrs['aria-disabled'],'true');
assert.equal($('diagnosticsHint').hidden,false);assert.equal($('runMenu').hidden,false);
let blocked=false;$('diagnostics').onclick({preventDefault(){blocked=true}});assert.equal(blocked,true);
}
paint({run_id:'r',execution_status:'failed',operation:'scene_edit'});
assert.equal($('diagnostics').attrs['aria-disabled'],'false');assert.equal($('diagnosticsHint').hidden,true);
assert.equal($('diagnostics').href,'/diagnostics?run_id=r');let blocked=false;$('diagnostics').onclick({preventDefault(){blocked=true}});assert.equal(blocked,false);
''')

def test_other_files_and_reports_not_rendered_main_or_candidate():
    html=HTML.read_text();assert 'id="otherFiles"' not in html and 'id="otherFileLinks"' not in html
    run_js(r'''
selected='r';presentations.set('r',{files:[{path:'Room.glb'}],comparisons:[],reports:[{path:'assessment.json'}],other_files:[{path:'Object/chair.glb'}],diagnostics:{reports:[{path:'candidate/report.json'}],other_files:[{path:'old/Room.blend'}]}});
previewList([],{});
assert.deepEqual(flat($('resultDownloadLinks')).filter(e=>e.className==='button').map(e=>e.textContent),['GLB']);
assert.equal($('candidateDetails').hidden,true);
''')


def test_files_share_one_horizontal_button_row():
    from html.parser import HTMLParser
    class Parents(HTMLParser):
        def __init__(self):super().__init__();self.stack=[];self.parents={}
        def handle_starttag(self,tag,attrs):
            d=dict(attrs)
            if 'id' in d:self.parents[d['id']]=list(self.stack)
            if tag not in ('input','img','br','meta','link','hr'):self.stack.append(d.get('id',tag))
        def handle_endtag(self,tag):
            if self.stack:self.stack.pop()
    parser=Parents();parser.feed(HTML.read_text())
    assert 'fileActions' in parser.parents['resultDownloadLinks']
    assert 'fileActions' in parser.parents['diagnostics']
    assert '#fileActions{display:flex;flex-direction:row;flex-wrap:nowrap;' in HTML.read_text()
    assert '#fileActions a.button{display:inline-flex;' in HTML.read_text()
    run_js(r'''
const h=new Element();outputLinks(h,[{path:'Room.portable.blend'},{path:'Room.glb'}]);
assert.deepEqual(h.children.map(x=>x.textContent),['Blender','GLB']);
assert.equal(h.children[0].download,'Room.portable.blend');
''')
