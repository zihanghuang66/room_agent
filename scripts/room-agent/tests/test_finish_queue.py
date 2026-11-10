"""Web Finish handoff; no models/GPU or mutation of existing user tasks."""
import io,json,os,signal,subprocess,sys,threading
from pathlib import Path
import pytest
from test_task_queue import queue


def test_finished_graph_keeps_busy_worker_explicit_until_finalize_done(tmp_path):
    q,s,w=queue(tmp_path)
    try:
        s.states['one']='waiting_for_input';q.start('one','repair');q.pump()
        status=q.status('one')
        assert status['worker_finalizing'] is True
        with pytest.raises(RuntimeError,match='finishing'):
            q.start('one','finish')
        assert w.calls==[('one','repair')]
        w.busy=False
        # Status poll itself settles the prior action, even before scheduler tick.
        assert q.status('one')['queue_status']=='waiting_for_input'
        assert q.start('one','finish')['queue_status']=='queued'
        q.start('one','finish');q.pump()
        assert w.calls==[('one','repair'),('one','finish')]
    finally:q.close()


def test_stale_queued_action_cannot_rerun_cli_completed_task(tmp_path):
    q,s,w=queue(tmp_path)
    try:
        s.states['one']='waiting_for_input';q.start('one','repair')
        s.states['one']='completed';q.pump()
        assert w.calls==[]
        assert q.status('one')['queue_status']=='completed'
    finally:q.close()


@pytest.mark.parametrize('error',[False,True])
def test_web_cli_does_not_automatically_compress_full_scene(tmp_path,monkeypatch,error):
    from room_agent import cli
    calls=[]
    class Service:
        def __init__(self,root):pass
        def execute(self,*args,**kw):
            if error:raise RuntimeError('fixture failure')
            return {'operation':'full_scan','execution_status':'waiting_for_input'}
        def package_return(self,run):calls.append('full_bundle');return 'large.tar.gz'
    monkeypatch.setattr(cli,'Service',Service)
    monkeypatch.setattr(sys,'argv',['room_agent','--state-root',str(tmp_path),'resume','one','--origin','web','--action','finish'])
    if error:
        with pytest.raises(SystemExit):cli.main()
    else:cli.main()
    assert calls==[]


def test_exited_worker_with_inherited_stdout_cannot_hold_queue_forever(tmp_path):
    from room_agent.web import WorkerManager
    code="import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)']); print('worker finished',flush=True)"
    p=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
    worker=WorkerManager(tmp_path);worker.process=p
    log=tmp_path/'console.log';worker.reader=threading.Thread(target=worker._tee,args=(p,log.open('ab',buffering=0),'one'),daemon=True)
    worker.reader.start()
    try:
        p.wait(timeout=3);worker.reader.join(timeout=3)
        assert not worker.busy
        assert b'worker finished' in log.read_bytes()
    finally:
        try:os.killpg(p.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        worker.reader.join(timeout=3)


def test_live_worker_and_resource_lock_are_not_bypassed(tmp_path):
    from room_agent.storage import room_lock
    q,s,w=queue(tmp_path)
    try:
        q.start('one');q.pump();s.states['one']='running'
        assert q.status('one')['worker_finalizing'] is False
        with pytest.raises(RuntimeError):q.start('one','finish')
        s.states['one']='waiting_for_input';w.busy=False
        with room_lock(tmp_path/'resources','scene_execution'):
            q.start('one','finish');q.pump();assert w.calls==[('one',None)]
        q.pump();assert w.calls[-1]==('one','finish')
    finally:q.close()


def test_restart_retains_completed_task_and_discards_no_scene(tmp_path):
    from room_agent.task_queue import TaskQueue
    from test_task_queue import Service,Worker
    q,s,w=queue(tmp_path);s.states['one']='waiting_for_input';q.start('one','finish');q.pump()
    s.states['one']='completed';q.close()
    marker=tmp_path/'runs/one/Room.py';marker.parent.mkdir(parents=True);marker.write_text('retained')
    w2=Worker();q=TaskQueue(tmp_path,s,w2,autostart=False)
    try:
        q.pump();assert w2.calls==[]
        with pytest.raises(ValueError):q.start('one','finish')
        assert marker.read_text()=='retained'
    finally:q.close()


def test_real_http_full_graph_finish_after_slow_finalization(tmp_path,monkeypatch):
    """Real HTTP, queue, SQLite graph; only reconstruction/model boundary is fake."""
    import tarfile,time
    from room_agent.service import Service
    from room_agent.storage import atomic_json,read_json
    from room_agent.task_queue import TaskQueue
    from room_agent.web import make_server
    from test_web import request,auth
    from test_scan_graph import Stages
    raw=tmp_path/'data/raw/scan.tar';raw.parent.mkdir(parents=True)
    with tarfile.open(raw,'w') as t:
        member=tarfile.TarInfo('capture/frame.jpg');member.size=3;t.addfile(member,io.BytesIO(b'raw'))
    stages=Stages()
    class QualityStages:
        def __call__(self,command,log,**kwargs):
            stages(command,log,**kwargs);p=read_json(command[-1])
            if p['stage'] in ('author','author_repair'):
                atomic_json(Path(p['attempt'])/'stage_result.json',{'state':{'author_review':{
                    'accepted':False,'visual_review':{'score':4.5,'issues':[]},
                    'closeout':{'batches':0 if p['stage']=='author' else 1}}}})
    svc=Service(tmp_path/'state',full_process_runner=QualityStages())
    run=svc.create_full(raw,'isolated_web_test',tmp_path)
    svc.execute(run);_,folder=svc._load(run)
    original=(folder/'input.json').read_bytes()
    class Worker:
        busy=False
        def start(self,r,action=None):
            self.busy=True
            svc.execute(r,action=action,action_origin='web')
            return {'run_id':r,'pid':os.getpid()}
        def close(self):self.busy=False
    worker=Worker();q=TaskQueue(svc.root,svc,worker,autostart=False)
    server=make_server(svc.root,tmp_path,port=0,service=svc,worker=q,source_discovery=lambda p:[])
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();web=(server,svc,q)
    try:
        headers=auth(web);endpoint='/api/runs/'+run
        assert request(web,endpoint+'/resume','POST',{'action':'repair'},headers)[0]==202
        q.pump() # graph has returned, simulate diagnostics still collecting
        response=json.loads(request(web,endpoint)[2])
        assert response['execution_status']=='waiting_for_input' and response['worker_finalizing']
        rejected=request(web,endpoint+'/resume','POST',{'action':'finish'},headers)
        assert rejected[0]==409 and b'finishing' in rejected[2]
        worker.busy=False
        assert json.loads(request(web,endpoint)[2])['queue_status']=='waiting_for_input'
        before=list(stages.calls)
        assert request(web,endpoint+'/resume','POST',{'action':'finish'},headers)[0]==202
        assert request(web,endpoint+'/resume','POST',{'action':'finish'},headers)[0]==202
        q.pump();worker.busy=False
        final=json.loads(request(web,endpoint)[2])
        assert final['execution_status']=='completed' and final['quality_status']=='not_passed'
        assert final['completion_reason']=='user_finished' and final['next_nodes']==[]
        assert stages.calls==before+['draft_export']
        assert (folder/'input.json').read_bytes()==original
        assert stages.calls.count('ingest')==stages.calls.count('reconstruct')==1
        assert request(web,endpoint+'/resume','POST',{'action':'finish'},headers)[0]==400
        print('WEB_FINISH_ACCEPTANCE_PASS: one Finish, only draft_export, input unchanged, no reconstruction replay')
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)


def test_cli_origin_keeps_explicit_full_return_behavior(tmp_path,monkeypatch):
    from room_agent import cli
    calls=[]
    class Service:
        def __init__(self,root):pass
        def execute(self,*args,**kw):return {'operation':'full_scan','execution_status':'completed'}
        def package_return(self,run):calls.append(run);return 'full.tar.gz'
    monkeypatch.setattr(cli,'Service',Service)
    monkeypatch.setattr(sys,'argv',['room_agent','--state-root',str(tmp_path),'resume','one','--action','finish'])
    cli.main();assert calls==['one']
