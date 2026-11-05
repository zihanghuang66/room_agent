"""Offline new-capture isolation and failure semantics; no model/GPU claims."""
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

SOURCE = Path(os.environ.get('ROOM_TEST_PACKAGE', str(Path(__file__).resolve().parents[1] / 'litereality-full-v0.2.2')))
sys.path.insert(0, str(SOURCE))
import run


class NewScanTest(unittest.TestCase):
    def test_internal_layout_evidence_is_collected_without_raw_rgbd(self):
        with tempfile.TemporaryDirectory() as tmp:
            ret=Path(tmp);folder=ret/'artifacts/run';folder.mkdir(parents=True)
            (folder/'result.json').write_text(json.dumps({'scene':'room_003_test_03'}))
            base='scenes/room_003_test_03/scene_init/obj_stage/object_init/input/'
            kept=[base+'scene_data/room_003_test_03/layout_validation.json',
                  base+'scene_data/room_003_test_03/merge_review.json',
                  base+'scene_data/room_003_test_03/objects.extracted.pkl',
                  base+'scene_data/room_003_test_03/references/Storage0/rank0.jpg']
            excluded=['input/extracted/frame_00000.jpg',base+'rgbd/room_003_test_03/image/frame_00000.jpg',
                      base+'scene_data/room_003_test_03/auth.json']
            for name in kept+excluded:
                p=folder/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'fixture')
            link=folder/(base+'scene_data/room_003_test_03/references/linked.jpg')
            link.symlink_to(folder/'input/extracted/frame_00000.jpg')
            out=run.pack(folder,ret,'fixture')
            with tarfile.open(out) as t:
                names=t.getnames()
                for n in kept:self.assertIn('run/'+n,names)
                for n in excluded:self.assertNotIn('run/'+n,names)
                self.assertNotIn('run/'+link.relative_to(folder).as_posix(),names)

    def scan(self, root, extra=b''):
        dest = root / 'data/raw/room_003/test_03.tar'
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(dest, 'w') as t:
            for name, data in {'room.usdz': b'fixture'+extra, 'frame_00000.json': b'{}',
                               'frame_00000.jpg': b'fixture', 'depth_00000.png': b'fixture'}.items():
                info = tarfile.TarInfo('ScannerDifferentName/'+name); info.size=len(data)
                t.addfile(info, io.BytesIO(data))
        return dest

    def prepare(self, root):
        return subprocess.run([sys.executable, str(SOURCE/'run.py'), '--project-root', str(root),
                               '--scene', 'room_003_test_03', '--scan-archive', str(root/'data/raw/room_003/test_03.tar'), '--prepare-only'], text=True, capture_output=True)

    def test_new_scan_and_changed_archive_use_independent_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); self.scan(root)
            first=self.prepare(root)
            self.assertEqual(first.returncode, 0, first.stdout+first.stderr)
            inputs=list((root/'return_output/artifacts').glob('*/input.json'))
            self.assertEqual(len(inputs),1)
            data=json.loads(inputs[0].read_text())
            self.assertEqual(data['scene'], 'room_003_test_03')
            self.assertEqual(data['frames'],1)
            self.assertTrue(Path(data['capture']).joinpath('frame_00000.json').is_file())
            self.assertEqual(Path(data['capture']).name,'room_003_test_03')
            self.assertFalse(json.loads(inputs[0].with_name('result.json').read_text())['full_run_completed'])
            second=self.prepare(root)
            self.assertEqual(second.returncode,0,second.stdout+second.stderr)
            self.assertEqual(len(list((root/'return_output/artifacts').glob('*/input.json'))),1)
            self.scan(root, b'changed')
            third=self.prepare(root)
            self.assertEqual(third.returncode,0,third.stdout+third.stderr)
            self.assertEqual(len(list((root/'return_output/artifacts').glob('*/input.json'))),2)

    def test_missing_scan_returns_diagnostic_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); p=self.prepare(root)
            self.assertNotEqual(p.returncode,0)
            self.assertIn('RETURN_TAR:',p.stdout)
            self.assertTrue(list((root/'return_output').glob('room_003_test_03_full_*.tar.gz')))

    def test_alias_conflict_does_not_replace_user_directory(self):
        import capture_paths
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); capture=root/'input/extracted';capture.mkdir(parents=True)
            alias=root/'input/room_003_test_03';alias.mkdir();(alias/'keep.txt').write_text('keep')
            with self.assertRaises(ValueError):
                capture_paths.link_capture(root,capture,'room_003_test_03')
            self.assertEqual((alias/'keep.txt').read_text(),'keep')

    def test_return_tar_uses_new_scene_and_excludes_raw_and_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'artifacts/new';folder.mkdir(parents=True)
            (folder/'result.json').write_text(json.dumps({'scene':'room_003_test_03'}))
            (folder/'input').mkdir();(folder/'input/raw.jpg').write_bytes(b'raw')
            (folder/'scenes').mkdir();(folder/'scenes/auth.json').write_text('{}')
            (folder/'scenes/Room.glb').write_bytes(b'fixture')
            archive=run.pack(folder,root,'test')
            self.assertTrue(archive.name.startswith('room_003_test_03_full_'))
            with tarfile.open(archive) as t:
                names=t.getnames()
                self.assertTrue(any(n.endswith('Room.glb') for n in names))
                self.assertFalse(any('/input/' in n or 'auth.json' in n for n in names))
                self.assertTrue(all(m.mtime==0 for m in t.getmembers()))

if __name__=='__main__':unittest.main()
