import importlib.util
import sys
import os
from pathlib import Path
import pytest

DEFAULT=Path(__file__).resolve().parents[1]
ROOT=Path(os.environ.get('ROOM_BATCH_PACKAGE',str(DEFAULT if (DEFAULT/'batch_core.py').exists()
    else DEFAULT/'litereality-batch-review-v0.1.0')))

@pytest.fixture
def core():
    p = ROOT / 'batch_core.py'
    assert p.exists(), 'batched review implementation missing'
    spec = importlib.util.spec_from_file_location('batch_core', p)
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m

def review(**kw):
    d = dict(pass_=True, score=8.5, all_objects_reviewed=True, issues=[],
             reviewed_object_ids=['Bed0', 'Floor0'])
    d.update(kw); d['pass'] = d.pop('pass_')
    return d

def test_score_does_not_override_missing_coverage(core):
    assert not core.review_passes(review(reviewed_object_ids=['Bed0']), ['Bed0','Floor0'])
    assert not core.review_passes(review(score=True), ['Bed0','Floor0'])
    assert not core.review_passes(review(issues=[{'defect':'bad'}]), ['Bed0','Floor0'])
    assert core.review_passes(review(), ['Bed0','Floor0'])

def test_timeout_is_distinct_from_quality_failure(core):
    assert core.status_for('not_passed', 'timeout') == 'repair_timeout'
    assert core.status_for('not_passed', 'completed') == 'quality_not_passed'
    assert core.status_for('check_failed', 'timeout') == 'check_failed'
    assert core.status_for('passed', 'timeout') == 'accepted'

def test_selector_uses_actual_late_frame_and_no_silent_invention(core):
    groups = {k: dict(frames=[199], reason='clear', visibility='visible') for k in core.FOCUS}
    result = core.select_frames({'targets': groups}, list(range(200)))
    assert 199 in result['frames'] and 0 in result['frames']
    groups['bedding']['frames']=[200]
    with pytest.raises(ValueError, match='frame'):core.select_frames({'targets':groups}, list(range(200)))

def test_selector_requires_all_requested_targets(core):
    with pytest.raises(ValueError):core.select_frames({'targets':{}}, [0,10,25])
    groups={k:dict(frames=[],reason='not visible',visibility='unobservable') for k in core.FOCUS}
    result=core.select_frames({'targets':groups}, [0,10,25])
    assert result['unobservable']==list(core.FOCUS)
    assert result['frames']==[0,10,25]

def test_timing_uses_union_not_sum_and_does_not_call_residual_thinking(core):
    t=core.EventTiming()
    t.start('a','Bash',2);t.start('b','render',4);t.end('a',8);t.end('b',10)
    d=t.summary(12)
    assert d['observed_tool_seconds']==8
    assert d['unclassified_non_tool_seconds']==4
    assert 'thinking_seconds' not in d

def test_unfinished_tool_is_recorded_not_lost(core):
    t=core.EventTiming();t.start('a','render',2)
    d=t.summary(10)
    assert d['observed_tool_seconds']==8
    assert d['tools'][0]['completed'] is False

def test_issue_order_prioritizes_large_work(core):
    issues=[dict(priority='small_details',defect='socket'),dict(priority='lighting',defect='hotspot'),
            dict(priority='large_appearance',defect='bedding'),dict(priority='evidence',defect='hidden')]
    chosen=core.choose_issues(issues)
    assert [x['defect'] for x in chosen]==['hotspot','bedding']

def test_saved_partial_edits_are_assessed_before_stopping(core):
    calls=[]
    def assess(i):
        calls.append(('assess',i));return {'quality':'not_passed','issues':[dict(priority='lighting',defect='hot')]}
    def repair(i,issues):calls.append(('repair',i));return {'termination':'timeout'}
    out=core.cycle(assess,repair,3)
    assert calls==[('assess',0),('repair',1),('assess',1)]
    assert out['status']=='repair_timeout'

def test_passed_initial_scene_never_repaired(core):
    def repair(*a):raise AssertionError('repair must not start')
    assert core.cycle(lambda i:{'quality':'passed','issues':[]},repair,3)['status']=='accepted'

def test_check_failure_never_triggers_edits(core):
    def repair(*a):raise AssertionError('repair must not start')
    assert core.cycle(lambda i:{'quality':'check_failed','issues':[]},repair,3)['status']=='check_failed'

def test_malformed_review_is_technical_failure(core):
    with pytest.raises(ValueError):core.validate_review({'pass':False},['Bed0'])
    with pytest.raises(ValueError):core.validate_review(review(score=float('nan')),['Bed0','Floor0'])
    with pytest.raises(ValueError):core.validate_review(review(reviewed_object_ids=['Unknown']),['Bed0'])

def test_evidence_only_does_not_trigger_geometry_invention(core):
    def repair(*a):raise AssertionError('Hidden evidence is not an actionable mesh defect')
    d=core.cycle(lambda i:dict(quality='not_passed',issues=[dict(priority='evidence',defect='hidden')]),repair,3)
    assert d['status']=='quality_not_passed'

def test_steps_are_not_wall_timeout(core):
    assert core.status_for('not_passed','step_limit')=='repair_step_limit'
    assert core.status_for('not_passed','execution_error')=='execution_error'

def test_all_three_batches_have_post_edit_assessment(core):
    seq=[]
    def assess(i):seq.append('a'+str(i));return dict(quality='not_passed',issues=[dict(priority='lighting',defect='bad')])
    def repair(i,issues):seq.append('r'+str(i));return dict(termination='completed')
    out=core.cycle(assess,repair,3)
    assert seq==['a0','r1','a1','r2','a2','r3','a3']
    assert out['status']=='quality_not_passed'

@pytest.fixture
def io(core):
    sys.path.insert(0,str(ROOT))
    import batch_io
    return batch_io

def test_copy_is_independent_and_excludes_generated_previews(io,tmp_path):
    src=tmp_path/'source';src.mkdir();(src/'Room.py').write_text('x=1')
    (src/'materials').mkdir();(src/'materials/x.jpg').write_bytes(b'image')
    (src/'repair_preview').mkdir();(src/'repair_preview/old.blend').write_bytes(b'old')
    dst=tmp_path/'copy';io.copy_source(src,dst)
    assert not (dst/'repair_preview').exists()
    (dst/'Room.py').write_text('x=2')
    assert (src/'Room.py').read_text()=='x=1'
    assert (src/'Room.py').stat().st_ino!=(dst/'Room.py').stat().st_ino

def test_symlinked_source_is_rejected_not_silently_dereferenced(io,tmp_path):
    src=tmp_path/'source';src.mkdir();(src/'Room.py').symlink_to('/etc/passwd')
    with pytest.raises(ValueError,match='symlink'):io.copy_source(src,tmp_path/'copy')

def test_failed_copy_never_overwrites_prior_checkpoint(io,tmp_path):
    src=tmp_path/'source';src.mkdir();(src/'Room.py').write_text('new')
    dst=tmp_path/'copy';dst.mkdir();(dst/'Room.py').write_text('old')
    with pytest.raises(FileExistsError):io.copy_source(src,dst)
    assert (dst/'Room.py').read_text()=='old'

def test_saved_build_uses_actual_bundled_textures_without_fetch(core,tmp_path):
    import importlib
    sys.path.insert(0,str(ROOT))
    assert (ROOT/'build_saved.py').exists(), 'saved-material build adapter missing'
    b=importlib.import_module('build_saved')
    obj=tmp_path/'obj';(obj/'textures').mkdir(parents=True)
    (obj/'textures.json').write_text('{"wood.jpg":{"from":"polyhaven","id":"x"}}')
    (obj/'textures/wood.jpg').write_bytes(b'actual edited pixels')
    assert b.cached_textures(obj,tmp_path/'out')==1
    assert (tmp_path/'out/wood.jpg').read_bytes()==b'actual edited pixels'
    (obj/'textures/wood.jpg').unlink()
    with pytest.raises(RuntimeError,match='Missing saved texture'):b.cached_textures(obj,tmp_path/'fresh')

def test_missing_checker_is_not_a_geometry_defect(core):
    assert core.geometry_quality({'ok':True,'pass':False,'failing':[{'kind':'mesh_check_unavailable'}]})=='check_failed'
    assert core.geometry_quality({'ok':True,'pass':False,'failing':[{'kind':'source_animation_unchecked'}]})=='check_failed'
    assert core.geometry_quality({'ok':False,'pass':False})=='check_failed'
    assert core.geometry_quality({'ok':True,'pass':False,'failing':[{'kind':'floating'}]})=='not_passed'
    assert core.geometry_quality({'ok':True,'pass':True,'failing':[]})=='passed'
