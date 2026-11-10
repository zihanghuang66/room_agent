"""Real graph/checkpoint tests; replace only the expensive application subprocess."""
import io
import json
from pathlib import Path
import tarfile
import pytest
from room_agent.service import Service
from room_agent.storage import atomic_json, read_json


class Stages:
    def __init__(self):
        self.calls=[];self.fail=None;self.hook=None
    def __call__(self, command, log, **kwargs):
        p=read_json(command[-1]);name=p['stage'];self.calls.append(name)
        folder=Path(p['folder']);scene=folder/'scenes'/p['request']['upstream_scene']
        scene.mkdir(parents=True,exist_ok=True)
        if self.hook:self.hook(p)
        (scene/'data.txt').write_text(name)
        if name=='author':
            room=scene/'realism_authoring/room';room.mkdir(parents=True,exist_ok=True)
            (room/'Room.py').write_text('# saved author edits')
            preview=scene/'realism_authoring/room_preview';preview.mkdir(exist_ok=True)
            atomic_json(preview/'author_acceptance.json',{'accepted':False,'status':'needs_repair'})
        if self.fail==name:raise RuntimeError(name+' network failed')
        if name in ('ingest','seed','author','publish'):
            preview=scene/({'ingest':'scene_init/obj_stage/object_init/object_refs','seed':'scene_init/scene_stage/room_init/room_preview'}.get(name,'realism_authoring/room_preview'));preview.mkdir(exist_ok=True,parents=True)
            (preview/(name+'.png')).write_bytes(b'preview')
        if name=='prepare':
            (folder/'input').mkdir(exist_ok=True)
            (folder/'input'/'capture.txt').write_text('raw immutable')
            atomic_json(folder/'input.json',{'scan':'fixture'})
        if name=='preflight':atomic_json(folder/'checks/runtime_environment.json',{'ROOM_CODEX':'fixture'})
        Path(log).parent.mkdir(exist_ok=True,parents=True);Path(log).write_text(name+' output\n')
        atomic_json(Path(p['attempt'])/'stage_result.json',{'state':{'stage_detail':{'status':name}}})


@pytest.fixture
def setup(tmp_path):
    raw=tmp_path/'data/raw/test.tar';raw.parent.mkdir(parents=True)
    with tarfile.open(raw,'w') as t:
        v=b'raw';i=tarfile.TarInfo('capture/frame.jpg');i.size=len(v);t.addfile(i,io.BytesIO(v))
    runner=Stages();svc=Service(tmp_path/'return_output/state',full_process_runner=runner)
    return svc,raw,runner,tmp_path


def test_new_full_scan_is_real_graph_and_resume_retries_only_failed_stage(setup):
    svc,raw,runner,project=setup
    run=svc.create_full(raw,'room',project);runner.fail='author'
    with pytest.raises(RuntimeError,match='author network'):svc.execute(run)
    first=svc.show(run)
    assert first['legacy_adapter'] is False
    assert first['next_nodes']==['author']
    assert first['current_node']=='author'
    assert any(p['stage']=='seed' for p in first['previews'])
    request,directory=svc._load(run)
    assert (directory/'checkpoints.sqlite').is_file()
    # A fake legacy pipeline must never be a recovery authority.
    atomic_json(directory/'pipeline.json',{'stages':{'author':{'status':'completed'}}})
    runner.fail=None
    svc=Service(svc.root,full_process_runner=runner)
    final=svc.execute(run)
    assert runner.calls==['prepare','preflight','ingest','reconstruct','seed','author','author','publish','export_check']
    assert final['execution_status']=='completed'
    assert final['quality_status']=='human_review_pending'
    assert final['full_run_completed'] is True
    assert svc.execute(run)['execution_status']=='completed'
    assert len(runner.calls)==9


def test_prepare_only_has_no_model_nodes(setup):
    svc,raw,runner,project=setup
    run=svc.create_full(raw,'room',project,prepare_only=True)
    result=svc.execute(run)
    assert runner.calls==['prepare']
    assert result['completion_reason']=='prepared_only'
    assert result['quality_status']=='unreviewed'
    assert result['legacy_adapter'] is False


def test_stage_preview_published_before_next_stage_runs(setup):
    svc,raw,runner,project=setup
    run=svc.create_full(raw,'room',project)
    def hook(payload):
        if payload['stage']=='reconstruct':
            live=svc.show(run)
            assert live['execution_status']=='running'
            assert any(p['stage']=='ingest' for p in live['previews'])
    runner.hook=hook;svc.execute(run)


def test_resume_rejects_snapshot_and_raw_tampering(setup):
    svc,raw,runner,project=setup
    run=svc.create_full(raw,'room',project);runner.fail='reconstruct'
    with pytest.raises(RuntimeError):svc.execute(run)
    _,d=svc._load(run)
    snapshot=next((d/'published'/'ingest'/'scenes').rglob('data.txt'))
    snapshot.write_text('corrupt')
    runner.fail=None
    with pytest.raises(ValueError,match='integrity'):svc.execute(run)
    assert runner.calls.count('ingest')==1


def test_receipt_publication_before_checkpoint_is_reused(setup,monkeypatch):
    from room_agent.scan_executor import ScanExecutor
    svc,raw,runner,project=setup
    run=svc.create_full(raw,'room',project)
    original=ScanExecutor.step;once=[]
    def killed(self,name,state):
        result=original(self,name,state)
        if name=='ingest' and not once:
            once.append(True);raise RuntimeError('worker lost after publication')
        return result
    monkeypatch.setattr(ScanExecutor,'step',killed)
    with pytest.raises(RuntimeError,match='worker lost'):svc.execute(run)
    svc.execute(run)
    assert runner.calls.count('ingest')==1
    assert any(e['kind']=='artifact_reused' and e['node']=='ingest' for e in svc.events(run))


def test_resume_preserves_failed_author_workspace_and_checkpoints_before_retry(setup):
    svc,raw,runner,project=setup
    run=svc.create_full(raw,'room',project);runner.fail='author'
    with pytest.raises(RuntimeError):svc.execute(run)
    def hook(payload):
        if payload['stage']=='author':
            content=(Path(payload['folder'])/'scenes'/payload['request']['upstream_scene']/'data.txt').read_text()
            assert content=='author'
            assert payload['resume_mode']=='author_revalidate'
            assert payload['resumed_from_saved_workspace'] is True
    runner.fail=None;runner.hook=hook
    svc.execute(run)
    _,d=svc._load(run)
    assert any(p.read_text()=='author' for p in (d/'work/failed/author').rglob('data.txt'))


def test_raw_capture_change_rejected_before_resume(setup):
    svc,raw,runner,project=setup
    run=svc.create_full(raw,'room',project);runner.fail='author'
    with pytest.raises(RuntimeError):svc.execute(run)
    _,d=svc._load(run)
    (d/'backend/input/capture.txt').write_text('modified')
    with pytest.raises(ValueError,match='capture integrity'):svc.execute(run)


def test_return_package_contains_previews_not_all_recovery_snapshots(setup):
    svc,raw,runner,project=setup
    run=svc.create_full(raw,'room',project);svc.execute(run)
    archive=svc.package_return(run)
    with tarfile.open(archive) as tar:
        names=tar.getnames()
    assert any('/published/seed/display/' in p for p in names)
    assert not any('/published/seed/scenes/' in p for p in names)
    assert any(p.endswith('/pipeline.json') for p in names)


def test_input_metadata_and_resolved_runtime_are_bound(setup):
    svc,raw,runner,project=setup
    run=svc.create_full(raw,'room',project);runner.fail='ingest'
    with pytest.raises(RuntimeError):svc.execute(run)
    _,d=svc._load(run)
    atomic_json(d/'backend/checks/runtime_environment.json',{'ROOM_CODEX':'changed'})
    with pytest.raises(ValueError,match='dependency integrity'):svc.execute(run)


def test_ingest_reference_and_stage_specific_previews(tmp_path):
    from room_agent.scan_artifacts import presentation
    source=tmp_path/'scenes'
    ref=source/'room/scene_init/obj_stage/object_init/object_refs/room/Bed0/reference_1024.png'
    ref.parent.mkdir(parents=True);ref.write_bytes(b'ref')
    for n in range(30):(ref.parent/(str(n)+'.png')).write_bytes(b'ref')
    seed=source/'room/scene_init/scene_stage/room_init/room_preview/Room.glb'
    seed.parent.mkdir(parents=True);seed.write_bytes(b'glb')
    ingest=presentation(source,tmp_path/'ingest','ingest')
    assert any(r['label']=='reference_1024.png' for r in ingest)
    rows=presentation(source,tmp_path/'seed','seed')
    assert [r['label'] for r in rows]==['Room.glb']


def test_deleted_entire_publication_is_not_reported_completed(setup):
    import shutil
    svc,raw,runner,project=setup
    run=svc.create_full(raw,'room',project,prepare_only=True);svc.execute(run)
    _,d=svc._load(run);shutil.rmtree(d/'published')
    with pytest.raises(ValueError,match='integrity'):svc.execute(run)


def test_uncommitted_preparation_is_reextracted_from_archive(setup):
    svc,raw,runner,project=setup
    run=svc.create_full(raw,'room',project,prepare_only=True)
    calls=[]
    def process(command,log,**kwargs):
        payload=read_json(command[-1]);folder=Path(payload['folder']);calls.append(1)
        if len(calls)>1:
            assert not (folder/'input').exists(), 'uncommitted input must not be reused'
        runner(command,log,**kwargs)
        if len(calls)==1:raise RuntimeError('lost before receipt')
    svc.full_process_runner=process
    with pytest.raises(RuntimeError,match='lost'):svc.execute(run)
    _,d=svc._load(run);(d/'backend/input/capture.txt').write_text('altered')
    assert svc.execute(run)['execution_status']=='completed'
    assert any(p.read_text()=='altered' for p in (d/'work/failed/prepare').rglob('capture.txt'))


def test_missing_author_source_does_not_silently_roll_back_to_seed(setup):
    svc,raw,runner,project=setup
    run=svc.create_full(raw,'room',project);runner.fail='author'
    with pytest.raises(RuntimeError):svc.execute(run)
    req,d=svc._load(run)
    (d/'backend/scenes'/req['upstream_scene']/'realism_authoring/room/Room.py').unlink()
    runner.fail=None
    with pytest.raises(ValueError,match='Saved Author source is missing'):svc.execute(run)
    assert runner.calls.count('author')==1


def test_second_interrupted_author_resume_keeps_latest_changes(setup):
    svc,raw,runner,project=setup
    run=svc.create_full(raw,'room',project);runner.fail='author'
    with pytest.raises(RuntimeError):svc.execute(run)
    req,d=svc._load(run);saved=d/'backend/scenes'/req['upstream_scene']/'data.txt'
    saved.write_text('latest user edit')
    def hook(payload):
        if payload['stage']=='author':assert saved.read_text()=='latest user edit'
    runner.hook=hook
    with pytest.raises(RuntimeError):svc.execute(run)
    assert any(p.read_text()=='latest user edit' for p in (d/'work/failed/author').rglob('data.txt'))

def test_v043_request_and_receipts_resume_without_rewriting_input(setup,monkeypatch):
    from room_agent import scan_executor
    svc,raw,runner,project=setup
    current=scan_executor.engine_hash
    monkeypatch.setattr(scan_executor,'engine_hash',lambda:'d0fe7df19101bf488a562402c431271de88c7b94964442de2356a5adb5d95559')
    run=svc.create_full(raw,'room',project)
    monkeypatch.setattr(scan_executor,'engine_hash',current)
    runner.fail='author'
    with pytest.raises(RuntimeError):svc.execute(run)
    request,d=svc._load(run);before=(d/'input.json').read_bytes()
    for path in (d/'work/attempts/author').glob('*/request.json'):
        data=read_json(path);data.pop('resume_mode',None);data.pop('resumed_from_saved_workspace',None);atomic_json(path,data)
    saved=d/'backend/scenes'/request['upstream_scene']/'realism_authoring/room/Room.py'
    def hook(payload):
        if payload['stage']=='author':
            assert saved.read_text()=='# saved author edits'
            assert payload['resume_mode']=='author_revalidate'
    runner.fail=None;runner.hook=hook
    assert svc.execute(run)['execution_status']=='completed'
    assert (d/'input.json').read_bytes()==before
    assert runner.calls.count('ingest')==runner.calls.count('reconstruct')==1

def test_author_retry_before_first_room_creation_retains_workspace(setup):
    svc,raw,runner,project=setup
    run=svc.create_full(raw,'room',project)
    def fail_before_room(payload):
        if payload['stage']=='author':raise RuntimeError('evidence network unavailable')
    runner.hook=fail_before_room
    with pytest.raises(RuntimeError):svc.execute(run)
    def resumed(payload):
        if payload['stage']=='author':
            assert payload['resumed_from_saved_workspace'] is True
            assert payload['resume_mode']=='stage_retry'
    runner.hook=resumed
    assert svc.execute(run)['execution_status']=='completed'
    assert runner.calls.count('seed')==1 and runner.calls.count('author')==2


def test_quality_stop_is_decision_not_error_and_repair_is_finite(setup):
    svc,raw,runner,project=setup
    original=runner.__call__
    class QualityStages:
        def __call__(self,command,log,**kwargs):
            original(command,log,**kwargs)
            p=read_json(command[-1]);name=p['stage']
            if name in ('author','author_repair'):
                batches=0 if name=='author' else 1
                atomic_json(Path(p['attempt'])/'stage_result.json',{'state':{'stage_detail':{},
                    'author_review':{'accepted':False,'support_pass':True,'visual_review':{'score':5,'issues':[
                        {'kind':'appearance','objects':['Bed'],'priority':'large_appearance','detail':'wrong shape'}]},
                        'closeout':{'batches':batches,'repair_termination':'step_budget' if batches else 'not_started',
                                    'stop_reason':'no_progress' if batches else None}}}})
    svc.full_process_runner=QualityStages()
    run=svc.create_full(raw,'room',project)
    s=svc.execute(run)
    assert s['execution_status']=='waiting_for_input' and s['quality_status']=='not_passed'
    assert s['allowed_actions']==['repair','finish'] and s['current_node']=='author_decision'
    _,d=svc._load(run)
    assert not (d/'published/author').exists()
    before=list(runner.calls)
    svc.execute(run) # Resume cannot silently authorize another batch
    assert runner.calls==before
    s=svc.execute(run,action='repair')
    assert s['execution_status']=='completed' and s['completion_reason']=='no_progress'
    assert s['quality_status']=='not_passed' and not s['full_run_completed']
    assert runner.calls==['prepare','preflight','ingest','reconstruct','seed','author','author_repair','draft_export']
    svc.execute(run)
    assert runner.calls.count('author_repair')==1


def test_finish_with_issues_only_checks_draft_exports(setup):
    svc,raw,runner,project=setup
    original=runner.__call__
    class QualityStages:
        def __call__(self,command,log,**kwargs):
            original(command,log,**kwargs)
            p=read_json(command[-1])
            if p['stage']=='author':
                atomic_json(Path(p['attempt'])/'stage_result.json',{'state':{'author_review':{
                    'accepted':False,'visual_review':{'score':4,'issues':[]},'closeout':{'batches':0}}}})
    svc.full_process_runner=QualityStages();run=svc.create_full(raw,'room',project)
    assert svc.execute(run)['execution_status']=='waiting_for_input'
    s=svc.execute(run,action='finish')
    assert s['execution_status']=='completed' and s['quality_status']=='not_passed'
    assert 'publish' not in runner.calls and 'export_check' not in runner.calls
    assert runner.calls[-1]=='draft_export'


def test_review_started_marker_resumes_review_when_first_review_has_no_report(setup):
    svc,raw,runner,project=setup
    run=svc.create_full(raw,'room',project);runner.fail='author'
    with pytest.raises(RuntimeError):svc.execute(run)
    _,d=svc._load(run)
    root=d/'backend/scenes'/read_json(d/'input.json')['upstream_scene']/'realism_authoring'
    (root/'room_preview/author_acceptance.json').unlink()
    atomic_json(root/'closeout/control.json',{'schema':1,'batches':0})
    def hook(payload):
        if payload['stage']=='author':assert payload['resume_mode']=='author_revalidate'
    runner.hook=hook;runner.fail=None
    svc.execute(run)


def test_final_publish_quality_rejection_retains_best_and_ends_without_loop(setup):
    svc,raw,runner,project=setup
    original=runner.__call__
    class PublishStages:
        def __call__(self,command,log,**kwargs):
            original(command,log,**kwargs);p=read_json(command[-1]);name=p['stage']
            if name in ('author','publish'):
                report={'accepted':name=='author','visual_review':{'score':8 if name=='author' else 7,'issues':[]}}
                if name=='author':report['closeout']={'batches':2,'best_candidate':'saved-reviewed-source','repair_termination':'step_budget'}
                atomic_json(Path(p['attempt'])/'stage_result.json',{'state':{'author_review':report}})
    svc.full_process_runner=PublishStages();run=svc.create_full(raw,'room',project)
    s=svc.execute(run)
    assert s['execution_status']=='completed' and s['quality_status']=='not_passed'
    assert s['completion_reason']=='final_publish_not_passed'
    assert s['best_candidate']=='saved-reviewed-source' and s['repair_termination']=='step_budget'
    assert runner.calls[-1]=='draft_export' and 'author_repair' not in runner.calls


def test_quality_receipt_survives_crash_before_graph_checkpoint(setup,monkeypatch):
    from room_agent.scan_executor import ScanExecutor
    svc,raw,runner,project=setup
    original=runner.__call__
    class QualityStages:
        def __call__(self,command,log,**kwargs):
            original(command,log,**kwargs);p=read_json(command[-1])
            if p['stage']=='author':
                atomic_json(Path(p['attempt'])/'stage_result.json',{'state':{'author_review':{
                    'accepted':False,'visual_review':{'score':4,'issues':[]},'closeout':{'batches':0}}}})
    svc.full_process_runner=QualityStages();run=svc.create_full(raw,'room',project)
    step=ScanExecutor.step;once=[]
    def killed(self,name,state):
        result=step(self,name,state)
        if name=='author' and not once:once.append(True);raise RuntimeError('crash after quality receipt')
        return result
    monkeypatch.setattr(ScanExecutor,'step',killed)
    with pytest.raises(RuntimeError,match='crash after quality'):svc.execute(run)
    s=svc.execute(run)
    assert s['execution_status']=='waiting_for_input'
    assert runner.calls.count('author')==1


def test_live_group_progress_survives_failed_author_resume(setup):
    svc,raw,runner,project=setup
    run=svc.create_full(raw,'room',project);runner.fail='author'
    def progress(payload):
        if payload['stage']=='author':
            path=Path(payload['folder'])/'scenes'/payload['request']['upstream_scene']/'realism_authoring/closeout/review_progress.json'
            atomic_json(path,{'status':'incomplete','completed_groups':3,'total_groups':9,
                              'completed_objects':60,'total_objects':119,'error_kind':'timeout'})
    runner.hook=progress
    with pytest.raises(RuntimeError):svc.execute(run)
    result=svc.show(run)
    assert result['review_progress']['completed_objects']==60
    assert result['review_progress']['status']=='incomplete'
    assert result['next_nodes']==['author']
    # Restart service; checkpoints and display progress remain distinct persisted facts.
    restarted=Service(svc.root,full_process_runner=runner)
    assert restarted.show(run)['review_progress']==result['review_progress']
    runner.fail=None;runner.hook=None
    restarted.execute(run)
    assert runner.calls.count('ingest')==1 and runner.calls.count('reconstruct')==1
    assert runner.calls.count('author')==2
