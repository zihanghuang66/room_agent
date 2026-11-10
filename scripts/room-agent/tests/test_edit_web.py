import http.client
import json
import threading
import time
from pathlib import Path
from test_scene import scene
from test_revisions import parent_run
from test_dashboard_stage3 import run_js


def test_ui_creates_child_and_open_version_never_runs_parent():
    run_js(r'''
responses['/api/versions']=[{run_id:'parent',room_id:'bedroom',quality_status:'not_passed'}];
await loadVersions('parent');assert.equal($('version').value,'parent');
$('editInstruction').value='Move bed 20 cm';responses['POST:/api/edit-runs']={run_id:'child'};
await $('createEdit').onclick();
assert.deepEqual(calls.find(c=>c.path==='/api/edit-runs').body,{parent_run_id:'parent',instruction:'Move bed 20 cm'});
assert(calls.some(c=>c.path==='/api/runs/child/start'));
assert(!calls.some(c=>c.path==='/api/runs/parent/start'||c.path==='/api/runs/parent/resume'));
calls.length=0;$('version').value='parent';await $('openVersion').onclick();
assert.equal(selected,'parent');assert(!calls.some(c=>c.body));
''')


def test_ui_before_after_uses_two_distinct_files_and_retains_arrows():
    run_js(r'''
const row={...pair('after.png'),before_path:'before.png'};
const viewer=makePreviewViewer([row,{...row,path:'after2.png',before_path:'before2.png'}],'changes',['Before','After']);
const parts=flat(viewer);
assert(parts.some(e=>e.textContent==='Before'));assert(parts.some(e=>e.textContent==='After'));
assert(parts.some(e=>e.attrs.href?.includes('before.png')));assert(parts.some(e=>e.attrs.href?.includes('after.png')));
parts.find(e=>e.className==='preview-next').onclick();
assert(flat(viewer).some(e=>e.attrs.href?.includes('before2.png')));
paint({run_id:'child',parent_run_id:'parent',operation:'scene_edit',execution_status:'completed',quality_status:'not_passed',allowed_actions:[]});
assert.equal($('editVersion').hidden,false);assert.equal($('continue').hidden,true);
assert.equal($('finish').disabled,true);assert.equal($('execution').textContent,'Completed with issues');
''')


def test_http_queue_new_child_does_not_reopen_completed_parent(tmp_path,scene):
    from room_agent.web import make_server
    from room_agent.task_queue import TaskQueue
    from room_agent.edit_executor import EditExecutor
    s,parent=parent_run(tmp_path,scene)
    calls=[]
    class Fake(EditExecutor):
        def _external(self,name,state,key):
            calls.append(name)
            if name=='build':return {'room':state['active_source']},[]
            if name=='review':return {'quality_status':'not_passed','score':7,'issues':[]},[]
            return {},[]
    s.edit_executor_type=Fake
    class Worker:
        thread=None
        @property
        def busy(self):return self.thread is not None and self.thread.is_alive()
        def start(self,run_id,action=None):
            self.thread=threading.Thread(target=lambda:s.execute(run_id,action=action,action_origin='web'));self.thread.start()
            return {'run_id':run_id,'pid':0}
        def close(self):
            if self.thread:self.thread.join(timeout=5)
    queue=TaskQueue(s.root,s,Worker());server=make_server(s.root,tmp_path,port=0,service=s,worker=queue)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def req(path,body=None):
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
        conn.request('GET' if body is None else 'POST',path,body=json.dumps(body) if body is not None else None,
                     headers={'Origin':f'http://127.0.0.1:{server.server_port}','X-Room-Token':server.token,'Content-Type':'application/json'})
        reply=conn.getresponse();data=json.loads(reply.read());conn.close();assert reply.status<300,data;return data
    try:
        before=s.show(parent)
        assert req('/api/versions')[0]['run_id']==parent
        child=req('/api/edit-runs',{'parent_run_id':parent,'instruction':'Change leg colour to black.'})['run_id']
        req('/api/runs/'+child+'/start',{})
        end=time.monotonic()+10
        while time.monotonic()<end:
            status=req('/api/runs/'+child)
            if status['execution_status']=='completed':break
            time.sleep(.05)
        assert status['execution_status']=='completed'
        assert s.show(parent)==before
        assert 'edit' in calls and not set(calls)&{'ingest','reconstruct','repair'}
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)


def test_unapproved_edit_views_are_only_in_diagnostics(tmp_path):
    import sys,struct
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ui'))
    from display import build_presentation
    from room_agent.storage import atomic_json
    root=tmp_path/'run';rows=[]
    for stage in ('baseline_render','render'):
        p=root/'work/attempts'/('r00_'+stage)/'a/compare/pairs/pair_00001.png'
        p.parent.mkdir(parents=True);p.write_bytes(b'\x89PNG\r\n\x1a\n'+b'\0\0\0\rIHDR'+struct.pack('>II',1448,754));rows.append(p)
        raw=p.parent.parent/'renders/frame_00001.png';raw.parent.mkdir();raw.write_bytes(b'\x89PNG\r\n\x1a\n'+b'\0\0\0\rIHDR'+struct.pack('>II',100,100))
    export=root/'work/attempts/r00_build/a/round/room_preview/Room.glb';export.parent.mkdir(parents=True);export.write_bytes(b'glb')
    atomic_json(root/'work/attempts/r00_finalize/stable/comparison.json',{'change_views':[dict(path=str(rows[1]),before_path=str(rows[0]),width=1448,height=754,captured=[0,34,720,720],rendered=[0,34,720,720])]})
    status=dict(operation='scene_edit',execution_status='completed',quality_status='not_passed')
    data=build_presentation(root,status)
    assert data['files']==data['changes']==data['comparisons']==[]
    assert len(data['diagnostics']['changes'])==1 and len(data['diagnostics']['files'])==1
    assert len(data['diagnostics']['comparisons'])==1
    status['quality_status']='passed';data=build_presentation(root,status)
    assert len(data['changes'])==1 and len(data['files'])==1
    assert not data['diagnostics']['files']
    status.update(quality_status='not_passed',edit_status='passed',technical_export_verified=True)
    data=build_presentation(root,status)
    assert len(data['changes'])==1 and len(data['files'])==1
    status['edit_status']='change_verified'
    assert not build_presentation(root,status)['files']
    status.update(edit_status='passed',technical_export_verified=False)
    assert not build_presentation(root,status)['files']
