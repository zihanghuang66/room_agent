from pathlib import Path
import pytest
from room_agent.service import Service
from room_agent.storage import atomic_json, Journal
from test_scene import scene


def parent_run(tmp_path, scene):
    service = Service(tmp_path / 'return_output/state')
    run = service.create_review(scene, max_batches=0)
    request, directory = service._load(run)
    # Use a completed saved-scene graph with real immutable snapshot receipt.
    from room_agent.scene_executor import SceneExecutor
    class Parent(SceneExecutor):
        def _external(self, name, state, key):
            if name == 'build':
                return {'room': state['active_source']}, []
            if name == 'geometry':
                return {'geometry_pass': False, 'quality_status': 'not_passed', 'issues': []}, []
            return {}, []
    service.scene_executor_type = Parent
    service.execute(run)
    return service, run


def test_revision_is_explicit_child_and_parent_is_unchanged(tmp_path, scene):
    service, parent = parent_run(tmp_path, scene)
    before = service.show(parent)
    versions = service.versions()
    assert versions[0]['run_id'] == parent
    child = service.create_edit(parent, 'Move the bed 20 cm toward the window.')
    request, _ = service._load(child)
    assert request['operation'] == 'scene_edit'
    assert request['parent_run_id'] == parent and child != parent
    assert request['room_id'] == before['room_id']
    assert service.show(parent) == before
    assert service.show(child)['next_nodes'] == []
    assert not any(e['kind'] == 'run_started' for e in service.events(child))


def test_unfinished_parent_or_empty_instruction_rejected(tmp_path, scene):
    s = Service(tmp_path / 'state')
    run = s.create_review(scene)
    with pytest.raises(ValueError, match='completed'):
        s.create_edit(run, 'Move bed')
    s, run = parent_run(tmp_path, scene)
    with pytest.raises(ValueError, match='instruction'):
        s.create_edit(run, '   ')


def test_edit_snapshot_rejects_changed_parent(tmp_path, scene):
    s, parent = parent_run(tmp_path, scene)
    child = s.create_edit(parent, 'Change the bed legs to black.')
    request, directory = s._load(child)
    from room_agent.edit_executor import EditExecutor
    executor = EditExecutor(request, directory, Journal(directory, child))
    Path(request['source'], 'Room.py').write_text('changed = 1')
    with pytest.raises(ValueError, match='changed|integrity'):
        executor.step('snapshot', {})


def test_full_scan_output_bridges_to_revision_without_reconstruction(tmp_path, scene):
    import shutil
    from test_scan_graph import setup
    # Publish a realistic completed graph state without invoking a backend.
    from room_agent.full_scan import capture_input
    from room_agent.scan_executor import engine_hash
    from room_agent.workflow import SCHEMA_VERSION
    from room_agent.scan_graph import VERSION
    raw=tmp_path/'data/raw/scan.tar';raw.parent.mkdir(parents=True)
    import tarfile, io
    with tarfile.open(raw,'w') as archive:
        info=tarfile.TarInfo('capture/frame.jpg');info.size=3;archive.addfile(info,io.BytesIO(b'raw'))
    s=Service(tmp_path/'return_output/state');run='a'*32
    req=capture_input(raw,'bedroom',tmp_path,run,False)
    req.update(run_id=run,room_id='bedroom',operation='full_scan',schema_version=SCHEMA_VERSION,workflow_version=VERSION,engine_sha256=engine_hash())
    s._publish_request(req);_,directory=s._load(run)
    old=scene/'scenes/room_004_test_04'
    shutil.copytree(old,directory/'backend/scenes'/req['upstream_scene'])
    capture=directory/'backend/input/extracted/capture';shutil.copytree(scene/'input/extracted',capture)
    atomic_json(directory/'backend/input.json',{'scene':req['upstream_scene'],'capture':str(capture)})
    with s._graph(req,directory) as (graph,_,_,config):
        graph.update_state(config,{'execution_status':'completed','technical_export_verified':True,'quality_status':'not_passed'},as_node='draft_export')
    child=s.create_edit(run,'Change bedside legs to black.')
    request,_=s._load(child)
    assert request['scene']==req['upstream_scene'] and request['room_id']=='bedroom'
    assert Path(request['capture'])==capture
    assert [x['run_id'] for x in s.versions()]==[run]
    assert not any(e['kind']=='run_started' for e in s.events(run))


@pytest.mark.parametrize('release', ['new','0.5.1','0.5.3'])
def test_child_graph_retry_does_not_repeat_edit_or_parent_execution(tmp_path,scene,release):
    from room_agent.edit_executor import EditExecutor
    from room_agent.saved_scene import tree_hashes
    import shutil
    s,parent=parent_run(tmp_path,scene)
    parent_dir=s.root/'runs'/parent
    parent_before=tree_hashes(parent_dir/'work')
    calls=[]
    class Fake(EditExecutor):
        fail=True
        def _external(self,name,state,key):
            calls.append(name)
            if name=='edit':
                active=self.work/'edited';shutil.copytree(state['active_source'],active)
                (active/'Room.py').write_text('value = 2\n')
                return {'active_source':str(active),'edit_termination':'completed'},[active]
            if name=='build':return {'room':state['active_source']},[]
            if name=='geometry':return {'geometry_pass':False,'quality_status':'not_passed','issues':[{'priority':'support','defect':'collision'}]},[]
            if name=='review':
                if self.fail:raise RuntimeError('network timeout')
                return {'score':9,'quality_status':'not_passed'},[]
            if name=='finalize':return {'completion_reason':'edit_review_not_passed'},[]
            return {},[]
    s.edit_executor_type=Fake
    child=s.create_edit(parent,'Move cabinet 20 cm.')
    if release!='new':
        # Construct an old-release request before any work, then resume its saved
        # graph with the new executor. Never mutate an in-flight production input.
        from room_agent.storage import digest
        request,directory=s._load(child)
        request.pop('edit_review_policy')
        request['engine_sha256']={'0.5.1':'11b34877c80c9029ce5b4f88b5fe7dd1ef823b52c4e146cdecbe90f1d23ff219',
                                  '0.5.3':'158985c6ca9ba7ceab819f5474e5a9875a8b2909b0128b13ef974df2a5b40183'}[release]
        atomic_json(directory/'input.json',request)
        atomic_json(directory/'input.sha256.json',digest(request))
    with pytest.raises(RuntimeError,match='timeout'):s.execute(child)
    assert s.show(child)['next_nodes']==['review']
    Fake.fail=False
    restarted=Service(s.root);restarted.edit_executor_type=Fake
    result=restarted.execute(child)
    assert result['execution_status']=='completed' and result['quality_status']=='not_passed'
    assert calls.count('edit')==1 and calls.count('build')==1 and calls.count('review')==2
    assert set(calls).isdisjoint({'ingest','reconstruct','author','repair','seed'})
    assert tree_hashes(parent_dir/'work')==parent_before
    next_child=restarted.create_edit(child,'Move cabinet another 5 cm.')
    req,_=restarted._load(next_child)
    assert req['parent_run_id']==child and req['room_id']==s.show(parent)['room_id']
    assert Path(req['source'],'Room.py').read_text()=='value = 2\n'


def test_revision_preview_links_are_not_child_source_and_parent_is_untouched(tmp_path,scene):
    from room_agent.edit_executor import EditExecutor
    from room_agent.saved_scene import file_hash
    s,parent=parent_run(tmp_path,scene)
    parent_request,parent_dir=s._load(parent)
    source=parent_dir/'work/initial/room'
    preview=source/'revision_preview/Object'
    preview.mkdir(parents=True)
    asset=source/'ChairCluster0.glb';asset.write_bytes(b'original neural asset')
    link=preview/'ChairCluster0.glb';link.symlink_to('../../ChairCluster0.glb')
    before=(link.readlink(),file_hash(asset),(source/'Room.py').read_bytes(),s.show(parent))
    child=s.create_edit(parent,'Change floor to dark grey.')
    request,directory=s._load(child)
    executor=EditExecutor(request,directory,Journal(directory,child))
    state=executor.step('snapshot',{})
    copied=Path(state['active_source'])
    assert not (copied/'revision_preview').exists()
    assert (copied/'ChairCluster0.glb').read_bytes()==asset.read_bytes()
    assert (link.readlink(),file_hash(asset),(source/'Room.py').read_bytes(),s.show(parent))==before
    # Generated preview changes do not invalidate source. Required assets still do.
    (preview/'unused.txt').write_text('new preview')
    executor.verify()
    asset.write_bytes(b'changed real asset')
    with pytest.raises(ValueError,match='integrity'):executor.verify()


@pytest.mark.parametrize('name',['Object/revision_preview/asset.glb','other_preview/asset.glb','Object/asset.glb'])
def test_new_preview_policy_does_not_allow_links_in_real_assets(tmp_path,scene,name):
    s,parent=parent_run(tmp_path,scene)
    source=s.root/'runs'/parent/'work/initial/room'
    path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.symlink_to(tmp_path/'missing')
    with pytest.raises(ValueError,match='symlink'):s.create_edit(parent,'Change floor colour.')


def test_preexisting_requests_keep_their_exact_source_inventory(tmp_path,scene):
    from room_agent.scene_executor import SceneExecutor
    source=scene/'scenes/room_004_test_04/realism_authoring/room'
    preview=source/'revision_preview';preview.mkdir();(preview/'old.txt').write_text('old regular output')
    s=Service(tmp_path/'return_output/state');run=s.create_review(scene,max_batches=0)
    req,directory=s._load(run)
    assert 'source_preview_policy' not in req
    assert 'revision_preview/old.txt' in req['source_files']
    executor=SceneExecutor(req,directory,Journal(directory,run))
    state=executor.step('snapshot',{})
    assert Path(state['active_source'],'revision_preview/old.txt').read_text()=='old regular output'
    executor.verify()


def test_edit_checkpoint_survives_package_directory_change(tmp_path,scene,monkeypatch):
    from room_agent.edit_executor import EditExecutor
    from room_agent.storage import digest,read_json
    import room_agent.edit_executor as module
    s,parent=parent_run(tmp_path,scene);child=s.create_edit(parent,'Change floor colour.')
    req,directory=s._load(child);executor=EditExecutor(req,directory,Journal(directory,child))
    attempt=executor.work/'attempts/r00_edit/stable';attempt.mkdir(parents=True)
    state={'active_source':'saved-copy'}
    old={'request':req,'state':state,'stage':'edit','attempt':str(attempt),'legacy':'/old-release/litereality-batch-review-v0.1.0'}
    atomic_json(attempt/'request.json',old)
    atomic_json(attempt/'stage_result.json',{'state':{'edit_termination':'completed'},'outputs':[]})
    atomic_json(attempt/'completed.json',dict(input_sha256=digest(old),outputs=executor._outputs([attempt/'stage_result.json']),trees={}))
    def forbidden(*a,**k):raise AssertionError('completed edit must not be executed again')
    monkeypatch.setattr(module,'run_process',forbidden)
    assert executor._external('edit',state,'r00_edit')[0]['edit_termination']=='completed'
    with pytest.raises(ValueError,match='identity'):
        executor._external('edit',{'active_source':'another-copy'},'r00_edit')
