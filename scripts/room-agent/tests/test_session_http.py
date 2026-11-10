import json,threading,sys,tarfile,io
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ui'))
from test_scene import scene
from test_session_edits import setup,draft
from test_web import request


def test_http_session_dispatch_visibility_diagnostics_and_guards(tmp_path,scene,monkeypatch):
    from room_agent import web
    from server import PresentationHandler
    s,r,q,m,c=setup(tmp_path,scene);q.close=lambda:None
    monkeypatch.setattr(web,'Handler',PresentationHandler)
    server=web.make_server(s.root,tmp_path,port=0,service=s,worker=q);server.conversation=c
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();endpoint=(server,s,q)
    headers={'Origin':'http://127.0.0.1:'+str(server.server_port),'X-Room-Token':server.token}
    try:
        assert request(endpoint,'/api/edit-runs','POST',{'parent_run_id':r,'instruction':'Move bed'},headers)[0]==409
        data=draft(c,r);path='/api/runs/'+r+'/confirm-plan'
        assert request(endpoint,path,'POST',data)[0]==403
        result=request(endpoint,path,'POST',data,headers);assert result[0]==200,result
        active=json.loads(result[2])['active']['run_id']
        assert len(json.loads(request(endpoint,'/api/runs')[2]))==1
        status=json.loads(request(endpoint,'/api/runs/'+r)[2]);assert status['run_id']==r and status['execution_run_id']==active
        assert request(endpoint,path,'POST',data,headers)[0]==200 and len(q.calls)==1
        bad=request(endpoint,'/api/runs/'+r+'/session-action','POST',{'operation_id':'wrong','action':'resume'},headers);assert bad[0]==409
        assert request(endpoint,'/diagnostics?run_id='+r)[0]==409
        request(endpoint,'/api/runs/'+r+'/session-action','POST',{'operation_id':active,'action':'cancel'},headers)
        result=request(endpoint,'/diagnostics?run_id='+r);assert result[0]==200,result
        with tarfile.open(fileobj=io.BytesIO(result[2])) as tar:
            assert r+'/conversation/history.json' in tar.getnames()
            assert active+'/input.json' in tar.getnames()
            assert all(x.mtime==0 for x in tar.getmembers())
        presentation=json.loads(request(endpoint,'/api/runs/'+r+'/presentation')[2]);assert presentation['source_run_id']==r
    finally:server.shutdown();server.server_close();thread.join(3)


def test_http_review_and_resume_keep_one_workspace_and_one_run(tmp_path,scene,monkeypatch):
    from room_agent import web
    from server import PresentationHandler
    from test_conversation_workflows import real_queue,proposal
    s,r,_,m,c=setup(tmp_path,scene);q=real_queue(s,m)
    c.planner=lambda _:proposal(r,'scene_review')
    monkeypatch.setattr(web,'Handler',PresentationHandler)
    server=web.make_server(s.root,tmp_path,port=0,service=s,worker=q);server.conversation=c
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();endpoint=(server,s,q)
    headers={'Origin':'http://127.0.0.1:'+str(server.server_port),'X-Room-Token':server.token}
    try:
        code,_,body=request(endpoint,'/api/runs/'+r+'/conversation','POST',{'request_id':'review','revision':0,'message':'Review current scene only'},headers)
        assert code==200
        turn=json.loads(body)['turns'][-1];assert turn['status']=='completed',turn
        assert len(s.list_runs())==1
        data={'revision':1,'plan_id':turn['result']['plan']['plan_id']};path='/api/runs/'+r+'/confirm-plan'
        code,_,body=request(endpoint,path,'POST',data,headers);assert code==200,body
        child=json.loads(body)['active']['run_id'];q.cancel(child)
        c.planner=lambda _:proposal(r,'resume_operation',child)
        code,_,body=request(endpoint,'/api/runs/'+r+'/conversation','POST',{'request_id':'resume','revision':1,'message':'Continue interrupted operation'},headers)
        turn=json.loads(body)['turns'][-1];assert turn['status']=='completed',turn
        data={'revision':2,'plan_id':turn['result']['plan']['plan_id']}
        assert request(endpoint,path,'POST',data)[0]==403
        assert request(endpoint,path,'POST',data,headers)[0]==200
        q.cancel(child)
        assert request(endpoint,path,'POST',data,headers)[0]==200
        assert q.status(child)['queue_status']=='cancelled'
        assert len(s.list_runs())==2 and len(json.loads(request(endpoint,'/api/runs')[2]))==1
    finally:server.shutdown();server.server_close();thread.join(3);q.close()
