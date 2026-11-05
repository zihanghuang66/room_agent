import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, os.environ.get('ROOM_TEST_PACKAGE', str(Path(__file__).resolve().parents[1] / 'litereality-resume')))
from capture_paths import repair_capture


class CaptureRepairTests(unittest.TestCase):
    def fixture(self, root):
        raw = root / 'input/extracted'; raw.mkdir(parents=True)
        for n in ('room.usdz', 'frame_00000.json', 'frame_00000.jpg', 'depth_00000.png'):
            (raw / n).write_text('{}')
        scene = root / 'scenes/room_002'; (scene / 'capture').mkdir(parents=True)
        bad = scene / 'scene_init/obj_stage/object_init/input/usdz_files'; bad.mkdir(parents=True)
        (bad / 'room_002.usdz').write_text('staged only')
        (scene / 'capture/room_002').symlink_to(bad)
        (scene / 'scene.json').write_text(json.dumps(dict(scan='room_002', paths={'capture':'capture/room_002'},
            roots={'scans':str(root/'input')}, capture={'source':str(bad),'frames':0,'has_usdz':False},
            providers={'image':'openai'}, models={'image':'gpt-image-2'})))
        return raw, scene

    def test_repairs_both_consumers_and_manifest_then_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); raw, scene = self.fixture(root)
            repair_capture(root, raw, 1, "room_002")
            self.assertTrue((root/'input/room_002/room.usdz').is_file())
            self.assertEqual((scene/'capture/room_002').resolve(), raw.resolve())
            doc=json.loads((scene/'scene.json').read_text())
            self.assertEqual(doc['capture']['frames'], 1)
            self.assertTrue(doc['capture']['has_usdz'])
            self.assertEqual(doc['providers']['image'], 'codex_builtin')
            before=(scene/'scene.json').read_bytes()
            repair_capture(root, raw, 1, "room_002")
            self.assertEqual((scene/'scene.json').read_bytes(), before)

    def test_refuses_to_replace_real_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,scene=self.fixture(root)
            existing=root/'input/room_002';existing.mkdir();(existing/'keep.txt').write_text('keep')
            with self.assertRaises(ValueError):repair_capture(root,raw,1,"room_002")
            self.assertEqual((existing/'keep.txt').read_text(),'keep')

    def test_missing_raw_frames_does_not_change_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,scene=self.fixture(root)
            before=(scene/'scene.json').read_bytes()
            (raw/'frame_00000.jpg').unlink()
            with self.assertRaises(ValueError):repair_capture(root,raw,1,"room_002")
            self.assertEqual((scene/'scene.json').read_bytes(),before)


if __name__=='__main__':unittest.main()
