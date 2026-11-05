import importlib.util,json,os,tempfile,unittest,io,subprocess,sys,tarfile
from pathlib import Path
P=Path(os.environ['ROOM_TEST_PACKAGE'])
spec=importlib.util.spec_from_file_location('wall_resume',P/'resume_guard.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
OLD='6d28c5e24cc49cd462b16746aba237993bb83f4a05619a9ee226bed0b86abae8'
class Resume(unittest.TestCase):
 def fixture(self,root):
  folder=root/'job';folder.mkdir()
  new=dict(scene='arbitrary_scene',archive_sha256='raw',codex_model='model',trellis_resolution='512',version='0.3.1',app_sha256='new')
  old=dict(new,version='0.3.0',app_sha256=OLD)
  (folder/'input.json').write_text(json.dumps(old));(folder/'result.json').write_text(json.dumps(dict(scene=new['scene'],status='failed',stage='ingest',full_run_completed=False)))
  state=folder/'scenes'/new['scene']/'.litereality/pipeline.json';state.parent.mkdir(parents=True)
  state.write_text(json.dumps({'stages':{'ingest':{'status':'failed'}}}))
  data=folder/'scenes'/new['scene']/'scene_init/obj_stage/object_init/input/scene_data'/new['scene'];data.mkdir(parents=True)
  for n in ('walls.pkl','objects.pkl','objects.extracted.pkl','floor.pkl','wall_holes.pkl'):(data/n).write_bytes(b'original fixture')
  (data/'layout_validation.json').write_text(json.dumps({'passed':False,'violations':[{'kind':'wall_clash'}]}))
  return folder,new,data,state
 def test_exact_old_failed_ingest_can_resume(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);folder,new,data,state=self.fixture(root)
   self.assertEqual(m.validate_resume(folder,root,new)[0],folder.resolve())
 def test_unknown_app_or_downstream_work_refused(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);folder,new,data,state=self.fixture(root)
   state.write_text(json.dumps({'stages':{'ingest':{'status':'failed'},'reconstruct':{'status':'completed'}}}))
   with self.assertRaises(ValueError):m.validate_resume(folder,root,new)
   state.write_text(json.dumps({'stages':{'ingest':{'status':'failed'}}}))
   old=json.loads((folder/'input.json').read_text());old['app_sha256']='other'
   (folder/'input.json').write_text(json.dumps(old))
   with self.assertRaises(ValueError):m.validate_resume(folder,root,new)
 def test_backup_preserves_prior_geometry_and_metadata(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);folder,new,data,state=self.fixture(root)
   old=json.loads((folder/'input.json').read_text())
   backup=m.backup_migration(folder,old,new,'fixture')
   self.assertEqual((data/'objects.pkl').read_bytes(),b'original fixture')
   self.assertEqual((backup/'scene_data/objects.pkl').read_bytes(),b'original fixture')
   self.assertEqual(json.loads((backup/'input.json').read_text())['version'],'0.3.0')
 def test_changed_raw_and_scene_are_refused(self):
  for key in ('scene','archive_sha256','codex_model','trellis_resolution'):
   with tempfile.TemporaryDirectory() as t:
    root=Path(t);folder,new,data,state=self.fixture(root);new[key]='wrong'
    with self.assertRaises(ValueError):m.validate_resume(folder,root,new)
 def test_launcher_migrates_existing_run_without_reextracting(self):
  oldpkg=Path(__file__).resolve().parents[1]/'litereality-full-v0.3.0'
  with tempfile.TemporaryDirectory() as t:
   project=Path(t)/'project';project.mkdir();archive=project/'scan.tar'
   with tarfile.open(archive,'w') as tar:
    for n in ('room.usdz','frame_00000.json','frame_00000.jpg','depth_00000.png'):
     data=b'fixture';info=tarfile.TarInfo(n);info.size=len(data);tar.addfile(info,io.BytesIO(data))
   first=subprocess.run([sys.executable,str(oldpkg/'run.py'),'--project-root',str(project),'--scene','neutral_scan','--scan-archive',str(archive),'--prepare-only'],capture_output=True,text=True)
   self.assertEqual(first.returncode,0,first.stdout+first.stderr)
   folder=next((project/'return_output/artifacts').iterdir())
   result=json.loads((folder/'result.json').read_text());result.update(status='failed',stage='ingest');(folder/'result.json').write_text(json.dumps(result))
   state=folder/'scenes/neutral_scan/.litereality/pipeline.json';state.parent.mkdir(parents=True);state.write_text(json.dumps({'stages':{'ingest':{'status':'failed'}}}))
   d=folder/'scenes/neutral_scan/scene_init/obj_stage/object_init/input/scene_data/neutral_scan';d.mkdir(parents=True)
   for n in ('walls.pkl','objects.pkl','objects.extracted.pkl','floor.pkl','wall_holes.pkl'):(d/n).write_bytes(b'preserved')
   (d/'layout_validation.json').write_text(json.dumps({'passed':False,'violations':[{'kind':'wall_clash'}]}))
   marker=folder/'input/extracted/keep.txt';marker.write_text('do not re-extract')
   env=dict(os.environ,ROOM_APP_PYTHON=sys.executable)
   second=subprocess.run(['bash',str(P/'resume_existing.sh'),str(folder),'--prepare-only'],capture_output=True,text=True,env=env)
   self.assertEqual(second.returncode,0,second.stdout+second.stderr)
   self.assertIn('MIGRATION_BACKUP:',second.stdout)
   self.assertEqual(json.loads((folder/'input.json').read_text())['version'],'0.3.1')
   self.assertEqual(marker.read_text(),'do not re-extract')
   self.assertEqual((d/'objects.pkl').read_bytes(),b'preserved')
   self.assertEqual(len(list((project/'return_output/artifacts').iterdir())),1)

if __name__=='__main__':unittest.main()
