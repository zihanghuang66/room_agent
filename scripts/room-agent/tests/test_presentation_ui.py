"""Read-only presentation index: never confuse textures or objects with room outputs."""
import importlib.util
import struct
from pathlib import Path

SPEC=importlib.util.spec_from_file_location('room_presentation',Path(__file__).resolve().parents[1]/'ui/display.py')

def module():
    m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m);return m

def png(path,w,h):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+struct.pack('>I',13)+b'IHDR'+struct.pack('>II',w,h))

def pair(root,stage='author',frame='00001'):
    p=root/f'published/{stage}/scenes/room/realism_authoring/compare/pairs/pair_{frame}.png'
    png(p,1088,754);png(p.parent.parent/'renders'/f'frame_{frame}.png',540,720)
    return p

def test_pairs_are_labeled_and_not_mixed_with_texture_files(tmp_path):
    m=module();p=pair(tmp_path)
    png(p.parents[2]/'room_preview/Object/wood_texture.png',100,100)
    result=m.build_index(tmp_path,{})
    row=result['comparisons'][0]['pairs'][0]
    assert row['captured']==[548,34,540,720]
    assert row['rendered']==[0,34,540,720]
    assert row['path']==p.relative_to(tmp_path).as_posix()
    assert len(result['comparisons'][0]['pairs'])==1

def test_room_downloads_exclude_nested_objects_and_use_one_revision(tmp_path):
    m=module()
    for stage in ['seed','author']:
        folder=tmp_path/f'published/{stage}/scenes/room/realism_authoring/room_preview';folder.mkdir(parents=True)
        for name in ['Room.glb','Room.blend','Room.portable.blend']:(folder/name).write_bytes(b'fixture')
        (folder/'Object').mkdir();(folder/'Object/Chair.glb').write_bytes(b'fixture')
    result=m.build_index(tmp_path,{})
    assert len(result['files'])==3
    assert all('/author/' in x['path'] for x in result['files'])
    assert not any('Chair' in x['path'] for x in result['files'])
    assert any('Chair' in x['path'] for x in result['other_files'])

def test_initial_scene_without_pairs_has_no_fake_comparison(tmp_path):
    m=module();png(tmp_path/'published/seed/scenes/room/scene_init/room_preview/Object/grain.png',540,720)
    assert m.build_index(tmp_path,{})['comparisons']==[]

def test_unverified_layout_and_symlink_are_not_displayed(tmp_path):
    m=module();p=pair(tmp_path)
    png(p,1400,754)  # not equal-width output for this renderer: don't guess halves
    outside=tmp_path.parent/'outside.png';png(outside,1088,754)
    (p.parent/'pair_00002.png').symlink_to(outside)
    result=m.build_index(tmp_path,{})
    assert result['comparisons']==[]
    assert result['unavailable_pairs']==1

def test_all_paired_views_are_indexed_beyond_old_24_image_cap(tmp_path):
    m=module()
    for i in range(27):pair(tmp_path,frame=f'{i:05d}')
    assert len(m.build_index(tmp_path,{})['comparisons'][0]['pairs'])==27

def test_read_only_index_keeps_published_snapshots_unchanged(tmp_path):
    m=module();pair(tmp_path)
    before={p.relative_to(tmp_path):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    m.build_index(tmp_path,{})
    assert before=={p.relative_to(tmp_path):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}

def test_saved_review_rounds_choose_latest_render_and_room(tmp_path):
    m=module()
    for round_no in (0, 1):
        compare=tmp_path/f'work/attempts/r{round_no:02d}_render/id/compare'
        png(compare/'pairs/pair_00001.png',1088,754);png(compare/'renders/frame_00001.png',540,720)
        folder=tmp_path/f'work/attempts/r{round_no:02d}_build/id/round/room_preview'
        folder.mkdir(parents=True);(folder/'Room.glb').write_bytes(b'fixture')
    result=m.build_index(tmp_path,{})
    assert '/r01_render/' in result['comparisons'][0]['key']
    assert '/r01_build/' in result['files'][0]['path']

def test_presentation_endpoint_uses_existing_host_guard_and_never_launches_worker(tmp_path,monkeypatch):
    import http.client,json,threading
    from test_web import FakeService,FakeWorker
    from room_agent import web
    spec=importlib.util.spec_from_file_location('room_ui_server',Path(__file__).resolve().parents[1]/'ui/server.py')
    server_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(server_module)
    monkeypatch.setattr(web,'Handler',server_module.PresentationHandler)
    root=tmp_path/'state';pair(root/'runs/run1')
    worker=FakeWorker();service=FakeService(root)
    service._load=lambda run: ({'run_id':run,'room_id':'room_004'},root/'runs'/run)
    server=web.make_server(root,tmp_path,port=0,service=service,worker=worker)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
        conn.request('GET','/api/runs/run1/presentation');response=conn.getresponse()
        assert response.status==200
        data=json.loads(response.read());assert data['comparisons']==[];assert len(data['diagnostics']['comparisons'])==1
        conn.request('GET','/api/runs/run1/presentation',headers={'Host':'evil.example'})
        response=conn.getresponse();assert response.status==403;response.read()
        conn.request('GET','/api/runs/unknown/presentation');response=conn.getresponse()
        assert response.status==400;response.read();conn.close()
        import diagnostics
        monkeypatch.setattr(diagnostics,'collect_session',lambda service,run_id,session,output_dir=None: (tmp_path/'room_agent_diagnostics_run1.tar.gz'))
        (tmp_path/'room_agent_diagnostics_run1.tar.gz').write_bytes(b'fixture archive')
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
        conn.request('GET','/diagnostics?run_id=run1');response=conn.getresponse()
        assert response.status==200 and 'room_agent_diagnostics_run1.tar.gz' in response.getheader('Content-Disposition')
        assert response.read()==b'fixture archive';conn.close()
        assert not worker.calls
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)

def test_failed_author_draft_is_visible_with_reports_and_matching_exports(tmp_path):
    import json
    m=module()
    c=tmp_path/'backend/scenes/scene/realism_authoring/acceptance/attempt-a/round_01/compare'
    png(c/'pairs/pair_00000.png',1088,754);png(c/'renders/frame_00000.png',540,720)
    (c/'renders/room.glb').write_bytes(b'draft glb');(c/'renders/Room.blend').write_bytes(b'draft blend')
    (c.parent/'visual_review.json').write_text(json.dumps({'pass':False,'issues':['wrong chair']}))
    result=m.build_index(tmp_path,{'operation':'full_scan','execution_status':'failed','current_node':'author'},include_drafts=True)
    assert len(result['comparisons'])==1
    assert result['comparisons'][0]['draft'] is True
    assert all('/round_01/compare/renders/' in row['path'] for row in result['files'])
    assert len(result['files'])==2
    assert any(row['path'].endswith('visual_review.json') for row in result['reports'])

def test_completed_run_does_not_prefer_mutable_backend_over_published(tmp_path):
    m=module();pair(tmp_path,stage='publish')
    c=tmp_path/'backend/scenes/scene/realism_authoring/acceptance/new/round_99/compare'
    png(c/'pairs/pair_00001.png',1088,754);png(c/'renders/frame_00001.png',540,720)
    r=m.build_index(tmp_path,{'operation':'full_scan','execution_status':'completed'})
    assert r['comparisons'][0]['key'].startswith('published/')


def test_failed_draft_only_appears_in_diagnostics_tab(tmp_path):
    m=module();c=tmp_path/'backend/scenes/scene/realism_authoring/acceptance/attempt-a/round_01/compare'
    png(c/'pairs/pair_00000.png',1088,754);png(c/'renders/frame_00000.png',540,720)
    (c/'renders/room.glb').write_bytes(b'candidate')
    result=m.build_presentation(tmp_path,{'operation':'full_scan','execution_status':'failed'})
    assert result['comparisons']==[] and result['files']==[]
    assert len(result['diagnostics']['comparisons'])==1
    assert len(result['diagnostics']['files'])==1


def test_waiting_and_completed_with_issues_retain_backend_diagnostics(tmp_path):
    m=module();c=tmp_path/'backend/scenes/scene/realism_authoring/acceptance/attempt-a/round_02/compare'
    png(c/'pairs/pair_00000.png',1088,754);png(c/'renders/frame_00000.png',540,720)
    (c/'renders/room.glb').write_bytes(b'candidate')
    for execution in ('waiting_for_input','completed'):
        result=m.build_presentation(tmp_path,{'operation':'full_scan','execution_status':execution,'quality_status':'not_passed'})
        assert result['comparisons']==[] and result['files']==[]
        assert len(result['diagnostics']['comparisons'])==1
        assert len(result['diagnostics']['files'])==1


def test_unapproved_author_snapshot_is_diagnostic_even_when_execution_completes(tmp_path):
    import json
    m=module();p=pair(tmp_path)
    room=p.parents[2]/'room_preview';room.mkdir()
    (room/'Room.glb').write_bytes(b'candidate')
    report=room/'author_acceptance.json'
    for accepted in (None,False,True):
        if accepted is not None:report.write_text(json.dumps({'accepted':accepted}))
        result=m.build_presentation(tmp_path,{'operation':'full_scan','execution_status':'completed','quality_status':'not_passed'})
        section=result if accepted else result['diagnostics']
        assert len(section['comparisons'])==1 and len(section['files'])==1
        if not accepted:assert not result['comparisons'] and not result['files']


def test_rejected_acceptance_revision_is_not_an_accepted_room_download(tmp_path):
    import json
    m=module();p=pair(tmp_path)
    author=p.parents[2];room=author/'room_preview';room.mkdir()
    (room/'Room.glb').write_bytes(b'accepted')
    (room/'author_acceptance.json').write_text(json.dumps({'accepted':True}))
    compare=author/'acceptance/attempt-a/round_00/compare'
    png(compare/'pairs/pair_00000.png',1088,754);png(compare/'renders/frame_00000.png',540,720)
    (compare/'renders/room.glb').write_bytes(b'rejected')
    (compare.parent/'assessment.json').write_text(json.dumps({'accepted':False}))
    result=m.build_presentation(tmp_path,{'operation':'full_scan','execution_status':'completed','quality_status':'passed'})
    assert result['files'][0]['path'].endswith('room_preview/Room.glb')
    assert not any('/acceptance/' in row['path'] for row in result['files']+result['other_files'])
    assert any('/acceptance/' in row['path'] for row in result['diagnostics']['files']+result['diagnostics']['other_files'])


def test_closeout_revisions_require_nearest_assessment_and_prefer_complete_approved_views(tmp_path):
    import json,os
    m=module();p=pair(tmp_path);author=p.parents[2]
    room=author/'room_preview';room.mkdir();(room/'Room.glb').write_bytes(b'current')
    (room/'author_acceptance.json').write_text(json.dumps({'accepted':True}))
    for attempt,accepted in [('attempt-old',False),('attempt-current',True)]:
        base=author/'closeout/reviews'/attempt
        for views in (6,24):
            compare=base/f'views_{views}/compare'
            for frame in range(views):
                png(compare/f'pairs/pair_{frame:05d}.png',1088,754)
                png(compare/f'renders/frame_{frame:05d}.png',540,720)
            (compare/'renders/room.glb').write_bytes(b'renderer')
        (base/'assessment.json').write_text(json.dumps({'accepted':accepted}))
        (base/'Room.glb').write_bytes(b'retained')
    # Copy/inspection timestamps cannot promote the old legacy comparison.
    os.utime(p.parent,(2100000000,2100000000))
    result=m.build_presentation(tmp_path,{'operation':'full_scan','execution_status':'completed','quality_status':'passed'})
    assert '/attempt-current/views_24/' in result['comparisons'][0]['key']
    assert len(result['comparisons'][0]['pairs'])==24
    assert result['files'][0]['path'].endswith('attempt-current/Room.glb')
    assert not any('/attempt-old/' in x['path'] for x in result['files']+result['other_files']+result['reports'])
    assert any('/attempt-old/' in x['key'] for x in result['diagnostics']['comparisons'])


def test_verified_draft_downloads_use_checked_exports_and_interrupted_use_saved_comparison(tmp_path):
    m=module();author=tmp_path/'backend/scenes/scene/realism_authoring'
    compare=author/'closeout/reviews/attempt-current/views_6/compare'
    png(compare/'pairs/pair_00000.png',1088,754);png(compare/'renders/frame_00000.png',540,720)
    (compare/'renders/room.glb').write_bytes(b'renderer')
    room=author/'room_preview';room.mkdir()
    for name in ['Room.glb','Room.portable.blend']:(room/name).write_bytes(b'verified')
    status={'operation':'full_scan','execution_status':'completed','quality_status':'not_passed','technical_export_verified':True,'current_node':'draft_export'}
    result=m.build_presentation(tmp_path,status)
    assert {x['path'].split('/')[-1] for x in result['diagnostics']['files']}=={'Room.glb','Room.portable.blend'}
    assert all('/room_preview/' in x['path'] for x in result['diagnostics']['files'])
    result=m.build_presentation(tmp_path,{**status,'execution_status':'interrupted','technical_export_verified':False})
    assert result['diagnostics']['files'][0]['path'].endswith('/compare/renders/room.glb')


def test_final_publish_review_and_verified_exports_replace_author_closeout(tmp_path):
    import json
    m=module();author=tmp_path/'published/export_check/scenes/room/realism_authoring'
    room=author/'room_preview';room.mkdir(parents=True)
    for name in ('Room.glb','Room.portable.blend'):(room/name).write_bytes(b'final export')
    (room/'author_acceptance.json').write_text(json.dumps({'accepted':True}))
    for name,accepted in [('closeout/reviews/attempt-author',True),('publish-review-final',True),('publish-review-rejected',False)]:
        attempt=author/name;compare=attempt/'views_6/compare'
        png(compare/'pairs/pair_00000.png',1088,754);png(compare/'renders/frame_00000.png',540,720)
        (compare/'renders/room.glb').write_bytes(b'renderer export')
        (attempt/'assessment.json').write_text(json.dumps({'accepted':accepted}))
        (attempt/'Room.glb').write_bytes(b'older saved export')
    result=m.build_presentation(tmp_path,{'operation':'full_scan','execution_status':'completed','quality_status':'passed','full_run_completed':True,'technical_export_verified':True})
    assert '/publish-review-final/' in result['comparisons'][0]['key']
    assert {x['path'].split('/')[-1] for x in result['files']}=={'Room.glb','Room.portable.blend'}
    assert all('/room_preview/' in x['path'] for x in result['files'])
    assert not any('/publish-review-rejected/' in x['path'] for x in result['files']+result['other_files']+result['reports'])
    assert any('/publish-review-rejected/' in x['key'] for x in result['diagnostics']['comparisons'])


def test_edit_downloads_use_build_bound_to_latest_render_not_temporary_render_exports(tmp_path):
    import json
    m=module();build=tmp_path/'work/attempts/r00_build/b/round/room_preview';build.mkdir(parents=True)
    for n in ['Room.glb','Room.blend','Room.portable.blend']:(build/n).write_bytes(b'current')
    attempt=tmp_path/'work/attempts/r00_render/r';pairs=attempt/'compare/pairs'
    png(pairs/'pair_00001.png',1088,754);png(attempt/'compare/renders/frame_00001.png',540,720)
    for n in ['Room.glb','Room.blend']:(attempt/'compare/renders'/n).write_bytes(b'temporary')
    (attempt/'request.json').write_text(json.dumps({'stage':'render','state':{'preview':str(build)}}))
    result=m.build_index(tmp_path,{'operation':'scene_edit'})
    assert {Path(x['path']).name for x in result['files']}=={'Room.glb','Room.blend','Room.portable.blend'}
    assert all('/r00_build/b/' in x['path'] for x in result['files'])
    # No cross-run or arbitrary-folder fallback.
    (attempt/'request.json').write_text(json.dumps({'stage':'render','state':{'preview':str(tmp_path.parent/'another-run/room_preview')}}))
    result=m.build_index(tmp_path,{'operation':'scene_edit'})
    assert all('/r00_render/r/' in x['path'] for x in result['files'])
