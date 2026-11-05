import json
from pathlib import Path
import sys
import tempfile
import unittest

PKG=Path(__file__).resolve().parents[1]/'litereality-repair-v0.1.1'
sys.path.insert(0,str(PKG))
from texture_rebase import resolve_image_path
from repair_core import pending_export_fingerprint,legacy_export_reusable


class TextureRelocationTests(unittest.TestCase):
    def test_origin_anchor_does_not_have_to_be_in_return_archive(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);image=p/'room/materials/wood.png';image.parent.mkdir(parents=True);image.write_bytes(b'right image')
            self.assertEqual(resolve_image_path('//../room/materials/wood.png',p/'final',p/'room_preview'),image.resolve())

    def test_full_path_wins_over_same_basename(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);origin=p/'origin';final=p/'final'
            for base,data in ((origin,b'original'),(final,b'other')):
                (base/'textures').mkdir(parents=True);(base/'textures/wood.png').write_bytes(data)
            self.assertEqual(resolve_image_path('//textures/wood.png',final,origin).read_bytes(),b'original')
            self.assertEqual(resolve_image_path(str(origin/'textures/wood.png'),final),(origin/'textures/wood.png').resolve())

    def test_missing_texture_is_not_replaced_by_unrelated_same_basename(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'wood.png').write_bytes(b'wrong')
            with self.assertRaises(FileNotFoundError):resolve_image_path('//missing/wood.png',p,p/'old')


class LegacyRecoveryTests(unittest.TestCase):
    def test_legacy_recovery_requires_matching_all_exports_and_object_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);room=p/'room';room.mkdir();(room/'Room.py').write_text('source')
            preview=p/'preview';final=p/'final'
            for path in (preview,final):
                (path/'Object').mkdir(parents=True)
                for n in ('Room.blend','Room.glb','manifest.json','room_layout.json'):(path/n).write_bytes(n.encode())
                (path/'Object/chair.glb').write_bytes(b'chair')
            previous={'version':'0.1.0','stage':'reopen_0','status':'failed'}
            self.assertTrue(legacy_export_reusable(previous,room,preview,final,0))
            self.assertFalse(legacy_export_reusable(previous,room,preview,final,1))
            mark=pending_export_fingerprint(room,final,preview)
            (final/'Object/chair.glb').write_bytes(b'changed')
            self.assertFalse(legacy_export_reusable(previous,room,preview,final,0))
            self.assertNotEqual(mark,pending_export_fingerprint(room,final,preview))


if __name__=='__main__':unittest.main()
