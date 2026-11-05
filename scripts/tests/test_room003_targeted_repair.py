"""Regression: edited assets must not reuse stale geometry; acceptance binds exact files."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

PKG=Path(__file__).resolve().parents[1]/os.environ.get('ROOM_REPAIR_TEST_PACKAGE','litereality-repair-v0.1.0')
sys.path[:0]=[str(PKG),str(PKG/'app/src')]
# Exercise the real build functions without requiring GPU/server dependency imports.
import ast
src=PKG/'app/src/litereality_agent/room_ops/compile/build_from_room.py'
tree=ast.parse(src.read_text())
keep=[n for n in tree.body if isinstance(n,(ast.Import,ast.FunctionDef)) or (isinstance(n,ast.ImportFrom) and n.module=='__future__')]
ns={'Path':Path,'console':SimpleNamespace(row=lambda *a:' '.join(str(v) for v in a),colour=lambda *a:'',human_bytes=str)}
exec(compile(ast.Module(body=keep,type_ignores=[]),str(src),'exec'),ns)
run_object_py=ns['run_object_py']

class CacheTests(unittest.TestCase):
    def test_edit_or_corruption_invalidates_cached_glb(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);obj=root/'obj';obj.mkdir();tex=root/'textures';tex.mkdir()
            src=obj/'object.py';src.write_text('old geometry');out=root/'out.glb'
            calls=[]
            def blender(cmd,**kw):
                calls.append(cmd);Path(cmd[-1]).write_bytes(b'glTF'+src.read_bytes()+b' '*40)
                return SimpleNamespace(returncode=0,stderr=b'')
            with patch('subprocess.run',side_effect=blender):
                self.assertTrue(run_object_py(obj,'Table',True,out,tex,'blender',False))
                self.assertTrue(run_object_py(obj,'Table',True,out,tex,'blender',False))
                self.assertEqual(len(calls),1)
                src.write_text('new geometry')
                self.assertTrue(run_object_py(obj,'Table',True,out,tex,'blender',False))
                self.assertIn(b'new geometry',out.read_bytes())
                out.write_bytes(b'glTF CORRUPTED CACHE'+b' '*40)
                self.assertTrue(run_object_py(obj,'Table',True,out,tex,'blender',False))
                self.assertEqual(len(calls),3)
                (tex/'wood.png').write_bytes(b'changed texture')
                run_object_py(obj,'Table',True,out,tex,'blender',False)
                self.assertEqual(len(calls),4)

class OfflineTextureTests(unittest.TestCase):
    def test_bundled_texture_reuse_never_downloads(self):
        self.assertIn('ROOM_REPAIR_OFFLINE_TEXTURES',src.read_text())
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);obj=r/'obj';(obj/'textures').mkdir(parents=True)
            (obj/'textures/wood.jpg').write_bytes(b'existing texture')
            (obj/'textures.json').write_text(json.dumps({'wood.jpg':{'from':'polyhaven','id':'unused'}}))
            with patch.dict('os.environ',{'ROOM_REPAIR_OFFLINE_TEXTURES':'1'}):
                count=ns['materialize_textures'](obj,r/'out')
            self.assertEqual((r/'out/wood.jpg').read_bytes(),b'existing texture')
            self.assertEqual(count,1)

class GateTests(unittest.TestCase):
    def core(self):
        self.assertTrue((PKG/'repair_core.py').is_file(),'targeted continuation guards are missing')
        import repair_core
        return repair_core
    def test_source_change_invalidates_step_but_does_not_erase_other_state(self):
        c=self.core()
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);(r/'Room.py').write_text('x=1');mark=c.tree_hashes(r)
            state={'steps':{'chairs':{'source_after':mark,'complete':True}}}
            self.assertTrue(c.step_done(state,'chairs',r))
            (r/'Room.py').write_text('x=2')
            self.assertFalse(c.step_done(state,'chairs',r))
    def test_review_rejects_missing_targets_bool_score_and_unresolved_issues(self):
        c=self.core();good={'pass':True,'score':8,'issues':[], 'targets':{k:True for k in c.TARGETS}}
        self.assertTrue(c.review_pass(good))
        for bad in [dict(good,score=True),dict(good,issues=['wrong shelf']),dict(good,targets={}),dict(good,score=7.9)]:
            self.assertFalse(c.review_pass(bad))
    def test_snapshot_detects_mutation_and_static_instances_cannot_disappear(self):
        c=self.core()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);room=p/'room';room.mkdir();pv=p/'preview';pv.mkdir()
            (room/'Room.py').write_text('ok')
            for n in ('Room.glb','Room.blend','Room.portable.blend','room_layout.json','manifest.json'):(pv/n).write_text(n)
            snap=c.build_fingerprint(room,pv)
            (pv/'Room.glb').write_text('changed')
            self.assertNotEqual(snap,c.build_fingerprint(room,pv))
    def test_network_retry_is_bounded_and_does_not_retry_quality_failure(self):
        c=self.core()
        self.assertTrue(c.transient_network('workspace routing discovery timed out'))
        self.assertFalse(c.transient_network('visual score 6; geometry failed'))
    def test_copy_rebases_internal_links_without_mutating_original(self):
        c=self.core()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);old=p/'old';old.mkdir();src=old/'scenes';src.mkdir()
            (src/'Room.py').write_text(str(old)+'/scenes')
            (src/'alias').symlink_to(src/'Room.py');new=p/'new'
            c.clone_tree(src,new/'scenes',old,new)
            self.assertEqual((src/'Room.py').read_text(),str(old)+'/scenes')
            self.assertEqual((new/'scenes/alias').read_text(),str(new)+'/scenes')
            self.assertFalse((new/'scenes/alias').is_symlink())


class SupervisorTests(unittest.TestCase):
    def test_failed_edit_resumes_without_rerunning_completed_chairs_and_returns_tar(self):
        self.exercise_resume('furniture')

    def test_failed_reopen_reuses_completed_build_and_all_edits(self):
        self.exercise_resume('reopen', assert_build_once=True)

    def exercise_resume(self, fail_step, assert_build_once=False):
        import repair
        import runtime
        import tarfile
        import contextlib,io
        from repair_core import save,digest,TARGETS
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'return_output/artifacts/source';scene=source/'scenes'/repair.SCENE
            room=scene/'realism_authoring/room';room.mkdir(parents=True)
            (room/'Room.py').write_text('# initial room\n')
            save(room/'manifest.json',{'assets':[{'object':'ChairCluster0','source':'TRELLIS','maps_to_prim':'Chair0'}]})
            static=room/'Objects/Static/ChairCluster0';static.mkdir(parents=True);(static/'ChairCluster0.glb').write_bytes(b'original')
            (scene/'realism_authoring/evidence').mkdir();(scene/'scene_init/obj_stage/object_init').mkdir(parents=True)
            cap=source/'input'/repair.SCENE;cap.mkdir(parents=True);(cap/'room.usdz').write_bytes(b'raw scan')
            for n in range(72):
                (cap/f'frame_{n:05d}.json').write_text('{}');(cap/f'frame_{n:05d}.jpg').write_bytes(b'raw image')
            blender=root/'blender';blender.write_text('fixture')
            calls=[];fail_once=[True]
            def environment(project,folder,app):
                (folder/'checks').mkdir(exist_ok=True)
                return {'LITEREALITY_BLENDER':str(blender),'ROOM_CODEX':'fixture'}
            def fake_command(label,cmd,cwd,env,folder,seconds):
                calls.append(label);cmd=list(map(str,cmd))
                if label.startswith(fail_step) and fail_once[0]:fail_once[0]=False;raise RuntimeError('controlled local failure')
                if cmd[1].endswith('repair_worker.py'):
                    job=json.loads(Path(cmd[-1]).read_text())
                    if cmd[2]=='edit':
                        target=Path(job['room'])/'Room.py';target.write_text(target.read_text()+'# '+label+'\n')
                        save(job['report'],{'session_ok':True,'tool_calls':1})
                    else:save(job['report'],{'review':{'pass':True,'score':8,'issues':[], 'targets':{k:True for k in TARGETS}}})
                elif label.startswith('build_'):
                    pv=Path(cmd[-1]);pv.mkdir(parents=True,exist_ok=True);(pv/'Object').mkdir(exist_ok=True)
                    for n in ('Room.glb','Room.blend','room_layout.json','manifest.json'):(pv/n).write_text(n)
                elif label.startswith('reopen_'):
                    pv=Path(cmd[cmd.index('--')+1]);(pv/'Room.portable.blend').write_bytes((pv/'Room.blend').read_bytes())
                elif label.startswith('geometry_'):
                    job=json.loads(Path(cmd[-1]).read_text());save(job['report'],{'pass':True})
                elif label.startswith('render_'):
                    job=json.loads(Path(cmd[-1]).read_text());Path(job['out']).mkdir(parents=True,exist_ok=True)
                elif label.startswith('pairs_'):
                    job=json.loads(Path(cmd[-1]).read_text());out=Path(job['out']);pair=out/'pairs/pair_00018.png';pair.parent.mkdir();pair.write_bytes(b'fixture pair');save(out/'pairs.json',[str(pair)])
            argv=['repair.py','--project',str(root),'--source',str(source)]
            original=(room/'Room.py').read_bytes()
            captured=io.StringIO()
            with patch.object(repair,'EXPECTED_ROOM_SHA',digest(room/'Room.py')),patch.object(repair,'setup_environment',side_effect=environment),patch.object(repair,'command',side_effect=fake_command),patch.object(runtime,'prepare'),patch.object(sys,'argv',argv),contextlib.redirect_stdout(captured):
                self.assertEqual(repair.main(),1)
                self.assertEqual(repair.main(),0,captured.getvalue())
            self.assertEqual(sum(x.startswith('chairs_') for x in calls),1)
            if assert_build_once:
                self.assertEqual(sum(x.startswith('build_') for x in calls),1,'reopen failure must not rebuild completed geometry')
                self.assertEqual(sum(x.startswith('furniture_') for x in calls),1)
                self.assertEqual(sum(x.startswith('materials_layout_') for x in calls),1)
            self.assertFalse(any(x.startswith(('ingest','reconstruct','seed')) for x in calls))
            self.assertEqual((room/'Room.py').read_bytes(),original)
            archives=list((root/'return_output').glob('room_003_repair*.tar.gz'));self.assertEqual(len(archives),2)
            for archive in archives:
                with tarfile.open(archive) as t:
                    self.assertTrue(all(m.mtime==0 for m in t.getmembers()))
                    self.assertFalse(any('/input/' in m.name for m in t.getmembers()))
            completed=list((root/'return_output/artifacts').glob('room_003_targeted*/acceptance.json'))
            self.assertTrue(json.loads(completed[0].read_text())['accepted'])

class AdditionalGuardTests(unittest.TestCase):
    def test_missing_source_still_produces_diagnostic_tar(self):
        import repair,contextlib,io
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with patch.object(sys,'argv',['repair.py','--project',str(root)]),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(repair.main(),1)
            self.assertEqual(len(list((root/'return_output').glob('room_003_repair*.tar.gz'))),1)
    def test_deleting_a_furniture_identity_or_changing_neural_source_is_rejected(self):
        from repair_core import identities,retained_assets,digest,save
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);save(r/'manifest.json',{'assets':[{'object':'Chair0','source':'TRELLIS'}]})
            (r/'source.glb').write_bytes(b'original')
            before={'identities':identities(r),'neural_sources':{'source.glb':digest(r/'source.glb')}}
            self.assertTrue(retained_assets(before,r))
            (r/'source.glb').write_bytes(b'changed');self.assertFalse(retained_assets(before,r))
            (r/'source.glb').write_bytes(b'original');save(r/'manifest.json',{'assets':[]})
            self.assertFalse(retained_assets(before,r))

if __name__=='__main__':unittest.main()
