import json,sys
from pathlib import Path
import pytest
from test_scene import scene
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ui'))

@pytest.fixture
def evidence(tmp_path):
    from scene_evidence import EvidenceReader
    root=tmp_path/'run';source=root/'room';source.mkdir(parents=True)
    (source/'Room.py').write_text("# evidence only\nobj='CabinetA'\nroughness=0.8\n")
    (source/'manifest.json').write_text('{"objects":["CabinetA"]}')
    (source/'auth.json').write_text('secret')
    image=root/'pair_00001.png';image.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    return EvidenceReader(root,source,'current',[image])

def test_catalog_only_allows_scene_source_and_supplied_images(evidence):
    cat=evidence.catalog();assert 'auth.json' not in json.dumps(cat)
    records,images=evidence.read({'files':[],'queries':['CabinetA'],'images':[]})
    assert records and any('CabinetA' in r['excerpt'] for r in records)
    assert all(r['run_id']=='current' and r['sha256'] for r in records) and not images
    evidence.verify(records)

@pytest.mark.parametrize('query',[{'files':[{'id':'../../auth.json','start':1,'lines':20}],'queries':[],'images':[]}, {'files':[],'queries':[],'images':['other-run/image']}, {'files':[],'queries':['x']*9,'images':[]}])
def test_unknown_id_or_unbounded_requests_fail_closed(evidence,query):
    with pytest.raises(ValueError):evidence.read(query)

def test_stale_and_symlink_evidence_rejected(evidence,tmp_path):
    records,_=evidence.read({'files':[],'queries':['CabinetA'],'images':[]})
    p=evidence.root/records[0]['path'];p.write_text('changed')
    with pytest.raises(ValueError,match='changed'):evidence.verify(records)
    p.unlink();p.symlink_to(tmp_path/'outside')
    with pytest.raises(ValueError):evidence.verify(records)

def test_image_delivery_is_bound_and_source_is_unchanged(evidence):
    before={p:p.read_bytes() for p in evidence.root.rglob('*') if p.is_file()}
    image=evidence.catalog()['images'][0]
    records,images=evidence.read({'files':[],'queries':[],'images':[image['id']]})
    assert records[0]['kind']=='image' and len(images)==1
    assert all(p.read_bytes()==b for p,b in before.items())


def test_graph_inspects_then_answers_with_real_bound_evidence(evidence):
    from conversation import planning_graph
    calls=[];progress=[]
    def planner(prompt,images=()):
        calls.append((prompt,images))
        if len(calls)==1:return {'kind':'inspect','reply':'Inspecting','questions':[],'plan':None,'inspection':{'files':[],'queries':['CabinetA'],'images':[]},'citations':[]}
        import re
        eid=re.search(r'"evidence_id": "(e[0-9a-f]+)"',prompt).group(1)
        assert 'roughness=0.8' in prompt
        return {'kind':'answer','reply':'Source sets roughness to 0.8; compiled result unverified.','questions':[],'plan':None,'inspection':None,'citations':[eid]}
    result=planning_graph(planner,evidence,lambda **kw:progress.append(kw)).invoke({'facts':{'run_id':'root'},'history':[],'message':'Inspect cabinet'})
    assert len(calls)==2 and result['result']['evidence'][0]['run_id']=='current'
    assert progress and result['inspection_rounds']==1


def test_graph_bounds_inspection_and_rejects_fake_citation(evidence):
    from conversation import planning_graph
    def loop(prompt):return {'kind':'inspect','reply':'Inspect','questions':[],'plan':None,'inspection':{'files':[],'queries':['CabinetA'],'images':[]},'citations':[]}
    with pytest.raises(ValueError,match='limit'):planning_graph(loop,evidence).invoke({'facts':{'run_id':'root'},'history':[],'message':'Inspect'})
    from conversation import validate
    with pytest.raises(ValueError,match='citation'):
        validate({'kind':'answer','reply':'Seen','questions':[],'plan':None,'inspection':None,'citations':['invented']},{'run_id':'root'},[])


def test_same_session_reads_retained_scene_and_does_not_dispatch(tmp_path,scene,monkeypatch):
    from test_session_edits import setup
    from room_agent.revisions import selected_source
    from test_conversation import reply
    s,r,q,m,c=setup(tmp_path,scene)
    src=Path(selected_source(s,r)['source'])/'Room.py';src.write_text("obj='CabinetA'\nroughness=0.8\n")
    calls=[]
    def planner(prompt):
        calls.append(prompt)
        if len(calls)==1:return {'kind':'inspect','reply':'Read','questions':[],'plan':None,'inspection':{'files':[],'queries':['CabinetA'],'images':[]},'citations':[]}
        import re
        out=reply(r,'plan');out.update(inspection=None,citations=[re.search(r'"evidence_id": "(e[0-9a-f]+)"',prompt).group(1)])
        out['plan']['target_ids']=['CabinetA'];return out
    c.planner=planner
    before={p:p.read_bytes() for p in (s.root/'runs'/r/'work').rglob('*') if p.is_file()}
    result=c.send(r,{'request_id':'inspect1','revision':0,'message':'Check cabinet first'})
    turn=result['turns'][-1]
    assert turn['status']=='completed' and not q.calls
    assert all(p.read_bytes()==data for p,data in before.items())
    assert turn['result']['plan']['inspection_evidence']
    assert turn['evidence'][0]['run_id']==r
    src.write_text("obj='CabinetA'\nroughness=0.7\n")
    with pytest.raises(RuntimeError,match='changed'):
        m.confirm(r,{'revision':result['revision'],'plan_id':turn['result']['plan']['plan_id']})
    assert not q.calls


def test_images_are_attached_to_followup_not_just_named(evidence):
    from conversation import planning_graph
    image=evidence.catalog()['images'][0]['id'];calls=[]
    def planner(prompt,images=()):
        calls.append(images)
        if len(calls)==1:return {'kind':'inspect','reply':'Read','questions':[],'plan':None,'inspection':{'files':[],'queries':[],'images':[image]},'citations':[]}
        import re
        return {'kind':'answer','reply':'Only this saved view was checked.','questions':[],'plan':None,'inspection':None,'citations':[re.search(r'"evidence_id": "(e[0-9a-f]+)"',prompt).group(1)]}
    planning_graph(planner,evidence).invoke({'facts':{'run_id':'root'},'history':[],'message':'Look at saved image'})
    assert not calls[0] and len(calls[1])==1 and Path(calls[1][0]).is_file()


def test_source_changed_during_model_response_is_not_returned(evidence):
    from conversation import planning_graph
    calls=[]
    def planner(prompt):
        calls.append(prompt)
        if len(calls)==1:return {'kind':'inspect','reply':'Read','questions':[],'plan':None,'inspection':{'files':[],'queries':['CabinetA'],'images':[]},'citations':[]}
        (evidence.source/'Room.py').write_text('changed')
        return {'kind':'answer','reply':'Incorrect stale answer','questions':[],'plan':None}
    with pytest.raises(ValueError,match='changed'):planning_graph(planner,evidence).invoke({'facts':{'run_id':'root'},'history':[],'message':'Check'})


def test_prompt_injection_cannot_dispatch_or_run_command(evidence):
    from conversation import planning_graph
    bad={'kind':'inspect','reply':'Read','questions':[],'plan':None,'inspection':{'command':'touch /tmp/forbidden'},'citations':[]}
    with pytest.raises(ValueError,match='Invalid inspection'):planning_graph(lambda p:bad,evidence).invoke({'facts':{'run_id':'root'},'history':[],'message':'Read'})


def test_current_catalog_ignores_pending_candidate(tmp_path,scene):
    from test_session_edits import setup,draft
    s,r,q,m,c=setup(tmp_path,scene)
    m.confirm(r,draft(c,r));child=q.calls[-1]
    facts=c.facts(r,s._load(r)[0])
    assert facts['evidence_catalog']['run_id']==r and facts['evidence_catalog']['run_id']!=child


def test_inspection_interruption_keeps_audit_and_never_replays(tmp_path,scene):
    from test_session_edits import setup
    from room_agent.revisions import selected_source
    from room_agent.storage import read_json
    s,r,q,m,c=setup(tmp_path,scene)
    (Path(selected_source(s,r)['source'])/'Room.py').write_text("obj='CabinetA'\n")
    calls=[]
    def planner(prompt):
        calls.append(prompt)
        if len(calls)==1:return {'kind':'inspect','reply':'Read','questions':[],'plan':None,'inspection':{'files':[],'queries':['CabinetA'],'images':[]},'citations':[]}
        raise RuntimeError('network timeout')
    c.planner=planner;data={'request_id':'interrupted','revision':0,'message':'Inspect'}
    result=c.send(r,data)
    assert result['turns'][-1]['status']=='failed' and result['turns'][-1]['evidence']
    assert read_json(s.root/'runs'/r/'conversation/turns/interrupted.json')['inspection_requests']
    assert c.send(r,data)==result and len(calls)==2 and not q.calls


def test_audit_symlink_does_not_write_outside_conversation(tmp_path):
    from test_conversation import make
    c=make(tmp_path);folder=tmp_path/'runs/one/conversation';folder.mkdir();outside=tmp_path/'outside';outside.mkdir();(folder/'turns').symlink_to(outside)
    with pytest.raises(ValueError,match='symlink'):c.send('one',{'request_id':'x','revision':0,'message':'hi'})
    assert not list(outside.iterdir())
