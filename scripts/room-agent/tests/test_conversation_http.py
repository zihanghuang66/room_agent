import sys
import json
from pathlib import Path
import threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ui'))
from test_web import FakeWorker,request
from test_conversation import Service,reply
import pytest

@pytest.fixture
def endpoint(tmp_path,monkeypatch):
    import server as ui
    from room_agent import web
    from conversation import Conversation
    monkeypatch.setattr(web,'Handler',ui.PresentationHandler)
    fake=Service(tmp_path);worker=FakeWorker()
    http=web.make_server(tmp_path,tmp_path,port=0,service=fake,worker=worker)
    http.conversation=Conversation(fake,planner=lambda p:reply(kind='plan'))
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    yield http,fake,worker
    http.shutdown();http.server_close();thread.join(3)

def test_guard_and_no_execution(endpoint):
    server,_,worker=endpoint
    path='/api/runs/one/conversation'
    assert request(endpoint,path)[0]==200
    body={'request_id':'m1','revision':0,'message':'地板改成灰色'}
    assert request(endpoint,path,'POST',body)[0]==403
    headers={'Origin':'http://127.0.0.1:'+str(server.server_port),'X-Room-Token':server.token}
    status,_,raw=request(endpoint,path,'POST',body,headers)
    out=json.loads(raw)
    assert status==200 and out['turns'][0]['status']=='completed'
    assert json.loads(request(endpoint,path)[2])==out
    assert not worker.calls
    assert json.loads(request(endpoint,'/api/runs/two/conversation')[2])['turns']==[]
    assert request(endpoint,'/api/runs/one/conversation','POST',{'request_id':'m2','revision':0,'message':'stale'},headers)[0]==409
