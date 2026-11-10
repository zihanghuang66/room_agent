import importlib.util,json,tarfile
from pathlib import Path

def module():
    spec=importlib.util.spec_from_file_location('room_diagnostics',Path(__file__).resolve().parents[1]/'ui/diagnostics.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_small_bundle_keeps_assessments_and_logs_not_models_or_credentials(tmp_path):
    m=module();root=tmp_path/'run1';root.mkdir()
    (root/'console.log').write_text('start\n'+'x'*20000+'\nstep budget 40 reached\n')
    p=root/'backend/scenes/scene/realism_authoring/acceptance/attempt-a/round_01';p.mkdir(parents=True)
    (p/'visual_review.json').write_text('{"pass":false,"issues":["wrong chair"]}')
    (p/'assessment.json').write_text('{"accepted":false}')
    (p/'Room.glb').write_bytes(b'x'*5000)
    (p/'auth.json').write_text('{"token":"secret"}')
    (root/'outside.log').symlink_to(p/'auth.json')
    before={f:f.read_bytes() for f in root.rglob('*') if f.is_file() and not f.is_symlink()}
    out=m.make_diagnostics(root,tmp_path/'small.tar.gz',{'execution_status':'failed'},max_bytes=8000,per_file=2000)
    with tarfile.open(out) as t:
        names=t.getnames();assert not any(n.endswith(('Room.glb','auth.json','outside.log')) for n in names)
        assert any(n.endswith('visual_review.json') for n in names)
        logs=[t.extractfile(x).read() for x in t if 'console.log' in x.name]
        assert b'step budget 40 reached' in b''.join(logs)
        manifest=json.load(t.extractfile('run1/diagnostics_manifest.json'))
        assert manifest['content_bytes']<=8000
        assert manifest['omitted']
        assert all(x.mtime==0 and x.isfile() for x in t.getmembers())
    assert before=={f:f.read_bytes() for f in before}

def test_diagnostics_never_traverses_input_credentials_or_symlink_tree(tmp_path):
    m=module();root=tmp_path/'run1';root.mkdir()
    for sub in ['input','.codex','backend/input','published/seed/scenes']:
        p=root/sub;p.mkdir(parents=True,exist_ok=True);(p/'secret.log').write_text('not for diagnostics')
    outside=tmp_path/'external';outside.mkdir();(outside/'external.log').write_text('outside')
    (root/'linked').symlink_to(outside,target_is_directory=True)
    out=m.make_diagnostics(root,tmp_path/'small.tar.gz',{})
    with tarfile.open(out) as t:assert not any('secret.log' in n or 'external.log' in n for n in t.getnames())

def test_active_run_is_not_packaged(tmp_path):
    import pytest
    m=module();root=tmp_path/'run';root.mkdir()
    with pytest.raises(ValueError,match='stops'):
        m.make_diagnostics(root,tmp_path/'small.tar.gz',{'execution_status':'running'})
    assert not (tmp_path/'small.tar.gz').exists()

def test_web_worker_collects_small_archive_after_exit(tmp_path,monkeypatch):
    import io
    spec=importlib.util.spec_from_file_location('room_ui_worker',Path(__file__).resolve().parents[1]/'ui/server.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    calls=[]
    monkeypatch.setattr(m,'collect_for_service',lambda service,run_id: calls.append(run_id) or tmp_path/'small.tar.gz')
    class Process:
        stdout=io.BytesIO(b'completed\n')
        def wait(self,timeout):calls.append('exit');return 1
    worker=m.DiagnosticWorker(tmp_path)
    worker._tee(Process(),io.BytesIO(),'run1')
    assert calls==['exit','run1']


def test_diagnostics_include_persisted_queue_snapshot_without_mutating_task(tmp_path):
    from types import SimpleNamespace
    from room_agent.storage import atomic_json
    m=module();root=tmp_path/'state';run=root/'runs/run1';run.mkdir(parents=True)
    (run/'Room.py').write_text('# retained')
    atomic_json(root/'queue/run1.json',{'status':'running','action':'repair','run_id':'run1','pid':123})
    service=SimpleNamespace(root=root,_load=lambda r:({'project':str(tmp_path),'room_id':'room'},run),
        show=lambda r:{'run_id':r,'execution_status':'waiting_for_input'})
    before=(root/'queue/run1.json').read_bytes()
    path=m.collect_for_service(service,'run1')
    with tarfile.open(path) as t:
        snapshot=json.load(t.extractfile('run1/queue_snapshot.json'))
        assert snapshot['entry']['status']=='running' and snapshot['entry']['action']=='repair'
    assert (root/'queue/run1.json').read_bytes()==before and (run/'Room.py').read_text()=='# retained'
