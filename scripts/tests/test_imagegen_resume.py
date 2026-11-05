"""Offline resume guardrails and chair grouping persistence."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

SOURCE = Path(os.environ.get('ROOM_TEST_PACKAGE', str(Path(__file__).resolve().parents[1]/'litereality-full-v0.2.3')))
sys.path.insert(0,str(SOURCE))


class ResumeTests(unittest.TestCase):
    def fixture(self, root):
        scan=root/'data/raw/room_003/test_03.tar';scan.parent.mkdir(parents=True)
        with tarfile.open(scan,'w') as tar:
            for name,data in {'room.usdz':b'fixture','frame_00000.json':b'{}',
                              'frame_00000.jpg':b'fixture','depth_00000.png':b'fixture'}.items():
                info=tarfile.TarInfo('capture/'+name);info.size=len(data);tar.addfile(info,io.BytesIO(data))
        baseline=SOURCE
        result=subprocess.run([sys.executable,str(baseline/'run.py'),'--project-root',str(root),
                               '--scene','room_003_test_03','--scan-archive',str(scan),'--prepare-only'],text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        folder=next((root/'return_output/artifacts').iterdir())
        state=json.loads((folder/'result.json').read_text());state.update(status='failed',stage='ingest')
        (folder/'result.json').write_text(json.dumps(state))
        return folder

    def invoke(self, root, folder=None):
        args=[sys.executable,str(SOURCE/'run.py'),'--project-root',str(root),'--scene','room_003_test_03','--scan-archive',str(root/'data/raw/room_003/test_03.tar'),'--prepare-only']
        if folder:args+=['--resume-from',str(folder)]
        return subprocess.run(args,text=True,capture_output=True)

    def test_resume_keeps_same_folder_and_existing_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=self.fixture(root)
            evidence=folder/'imagegen/keep.png';evidence.parent.mkdir();evidence.write_bytes(b'keep')
            result=self.invoke(root,folder)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(evidence.read_bytes(),b'keep')
            self.assertEqual(len(list((root/'return_output/artifacts').iterdir())),1)
            self.assertTrue(list(folder.glob('resume_*.json')))

    def test_changed_raw_archive_does_not_touch_previous_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=self.fixture(root)
            before=(folder/'result.json').read_bytes()
            with (root/'data/raw/room_003/test_03.tar').open('ab') as f:f.write(b'changed')
            result=self.invoke(root,folder)
            self.assertEqual(result.returncode,1,result.stdout+result.stderr)
            self.assertIn('Resume mismatch: archive_sha256',result.stdout)
            self.assertEqual((folder/'result.json').read_bytes(),before)

    def test_other_scene_cannot_be_resumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=self.fixture(root)
            data=json.loads((folder/'input.json').read_text());data['scene']='room_002'
            (folder/'input.json').write_text(json.dumps(data))
            before=(folder/'result.json').read_bytes()
            result=self.invoke(root,folder)
            self.assertEqual(result.returncode,1,result.stdout+result.stderr)
            self.assertIn('Resume mismatch: scene',result.stdout)
            self.assertEqual((folder/'result.json').read_bytes(),before)


class GroupingTests(unittest.TestCase):
    def test_empty_grouping_is_checkpointed(self):
        spec=importlib.util.spec_from_file_location('chair_checkpoint',SOURCE/'chair_checkpoint.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            result=module.load_or_build(root,'no_chairs',{},lambda:{'clusters':[]})
            self.assertEqual(result,{'clusters':[]})
            self.assertTrue((root/'no_chairs/chair_grouping_checkpoint.json').is_file())

    def test_grouping_saved_before_generation_reused_and_evidence_invalidated(self):
        spec=importlib.util.spec_from_file_location('chair_checkpoint',SOURCE/'chair_checkpoint.py')
        self.assertTrue(Path(spec.origin).is_file(),'Grouping checkpoint implementation missing')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sheet=root/'scan/input2imagegen/ChairCluster0.jpg';sheet.parent.mkdir(parents=True)
            sheet.write_bytes(b'evidence');calls=[]
            def build():
                calls.append(1)
                return {'clusters':[{'input2imagegen_sheet':'scan/input2imagegen/ChairCluster0.jpg','members':['Chair0']}]}
            first=module.load_or_build(root,'scan',{'key':'a'},build)
            first['clusters'][0]['members']=['MUTATED_MEMORY']
            second=module.load_or_build(root,'scan',{'key':'a'},build)
            self.assertEqual(second['clusters'][0]['members'],['Chair0'])
            self.assertEqual(len(calls),1)
            sheet.write_bytes(b'changed')
            module.load_or_build(root,'scan',{'key':'a'},build)
            self.assertEqual(len(calls),2)
            module.load_or_build(root,'scan',{'key':'b'},build)
            self.assertEqual(len(calls),3)


if __name__=='__main__':unittest.main()
