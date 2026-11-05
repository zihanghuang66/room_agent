"""Failure-injection integration; external Blender/model boundaries are simulated here."""
import asyncio
import importlib
import json
import sys
import os
from pathlib import Path
import pytest

DEFAULT=Path(__file__).resolve().parents[1]
PKG=Path(os.environ.get('ROOM_BATCH_PACKAGE',str(DEFAULT if (DEFAULT/'batch_core.py').exists()
    else DEFAULT/'litereality-batch-review-v0.1.0')))
sys.path[:0]=[str(PKG),str(PKG/'app/src')]


@pytest.fixture
def engine(tmp_path,monkeypatch):
    monkeypatch.setenv('LR_REPO_ROOT',str(tmp_path))
    monkeypatch.setenv('LITEREALITY_BLENDER','test-blender')
    import worker
    from batch_io import save
    e=worker.Engine(tmp_path,tmp_path/'capture','test',900)
    for p in (e.active,e.seed):
        p.mkdir(parents=True);(p/'Room.py').write_text('x=1\n')
        (p/'Room.md').write_text('room');save(p/'manifest.json',{'assets':[]})
    e.selection={'frames':[0,199], 'targets':{},'unobservable':[]}
    def module(label,name,args,folder,seconds=1200):
        def val(flag):return Path(args[args.index(flag)+1])
        if name=='build_saved':
            out=val('--out');out.mkdir();(out/'Room.glb').write_bytes(b'glTFtest')
            save(out/'room_layout.json',{'objects':[{'id':'Bed0'},{'id':'Floor0'}]})
            save(out/'manifest.json',{'assets':[]})
        elif name.endswith('.validate'):
            save(val('--json'),{'ok':True,'pass':True,'failing':[]})
        elif name.endswith('.render_vs_capture'):
            out=val('--out')/'pairs';out.mkdir(parents=True)
            for i in [0,199]:(out/f'pair_{i:05d}.png').write_bytes(b'not passed to real model')
        else:raise AssertionError('Unexpected pipeline module: '+name)
    monkeypatch.setattr(worker,'module',module)
    monkeypatch.setattr(worker,'command',lambda *a,**k:None)
    async def session(*a,**k):
        return dict(termination='completed',text=json.dumps({'pass':True,'score':8.1,
            'all_objects_reviewed':True,'reviewed_object_ids':['Bed0','Floor0'],'issues':[]}))
    monkeypatch.setattr(worker,'recorded_session',session)
    return e,worker


def test_assessment_is_bound_to_saved_source(engine):
    e,w=engine;report=e.assess(0)
    assert report['quality']=='passed'
    assert (e.last_round/'build_fingerprint.json').is_file()
    assert (e.last_round/'assessment.json').is_file()


def test_readonly_reviewer_mutation_invalidates_verdict(engine,monkeypatch):
    e,w=engine;original=w.recorded_session
    async def mutate(spec,folder,role):
        (e.last_round/'room/Room.py').write_text('x=999')
        return await original(spec,folder,role)
    monkeypatch.setattr(w,'recorded_session',mutate)
    report=e.assess(0)
    assert report['quality']=='check_failed'
    assert 'changed source/build' in report['error']
    assert (e.active/'Room.py').read_text()=='x=1\n'


def test_invalid_python_is_preserved_and_blocks_review(engine):
    e,w=engine;(e.active/'Room.py').write_text('x=')
    report=e.assess(0)
    assert report['quality']=='check_failed'
    assert (e.last_round/'room/Room.py').read_text()=='x='


def test_review_timeout_is_check_failed_not_quality_score(engine,monkeypatch):
    e,w=engine
    async def timeout(*a,**k):return dict(termination='timeout',text='')
    monkeypatch.setattr(w,'recorded_session',timeout)
    report=e.assess(0)
    assert report['quality']=='check_failed'
    assert report['review_termination']=='timeout'
    assert 'score' not in report


def test_new_repair_uses_copy_and_preserves_previous_round(engine,monkeypatch):
    e,w=engine;e.assess(0)
    async def edit(spec,folder,role):
        (spec.cwd/'Room.py').write_text('x=2\n')
        return dict(termination='timeout',text='',reason='wall time budget 900s reached')
    monkeypatch.setattr(w,'recorded_session',edit)
    result=e.repair(1,[dict(priority='lighting',defect='hotspot')])
    assert result['termination']=='timeout'
    assert (e.active/'Room.py').read_text()=='x=2\n'
    assert (e.last_round/'room/Room.py').read_text()=='x=1\n'


def test_recorded_session_keeps_partial_tool_and_timeout(tmp_path,monkeypatch):
    from litereality_agent.agent import providers as p
    import agent_session
    class Harness:
        def effective_model(self,spec):return 'test'
        async def run(self,spec):
            yield p.AgentMessage([p.ToolUseBlock('a','Bash',{'command':'test'})])
            yield p.SessionResult(stopped='wall time budget 900s reached',is_error=True)
    monkeypatch.setattr(p,'resolve',lambda *a:Harness())
    result=asyncio.run(agent_session.recorded_session(p.SessionSpec('test',tmp_path),tmp_path/'session','author'))
    assert result['termination']=='timeout'
    timing=json.loads((tmp_path/'session/timing.json').read_text())
    assert timing['tools'][0]['completed'] is False
    assert (tmp_path/'session/events.jsonl').is_file()


def test_all_capture_thumbnails_include_last_frame(tmp_path):
    from PIL import Image
    import worker
    capture=tmp_path/'capture';capture.mkdir()
    for i in range(151):
        Image.new('RGB',(12,16),(i,0,0)).save(capture/f'frame_{i:05d}.jpg')
        (capture/f'frame_{i:05d}.json').write_text('{}')
    ids,pages=worker.thumbnails(capture,tmp_path/'sheets')
    assert ids==list(range(151)) and len(pages)==13


def test_launcher_packs_input_failure_with_epoch_timestamps(tmp_path):
    import subprocess,tarfile
    source=tmp_path/'return_output/artifacts/fake';source.mkdir(parents=True)
    p=subprocess.run([sys.executable,str(PKG/'run_batches.py'),str(source)],capture_output=True,text=True)
    assert p.returncode==2 and 'RETURN_TAR:' in p.stdout
    archives=list((tmp_path/'return_output').glob('room_batch_review_*.tar.gz'))
    assert len(archives)==1
    with tarfile.open(archives[0]) as tar:
        members=tar.getmembers();assert all(m.mtime==0 for m in members)
        report=next(m for m in members if m.name.endswith('/result.json'))
        data=json.load(tar.extractfile(report));assert data['status']=='execution_error'
