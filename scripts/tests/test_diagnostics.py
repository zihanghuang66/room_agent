import hashlib
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import struct
import tarfile
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'reconstruction-diagnostics' / 'collect.py'

class DiagnosticsTests(unittest.TestCase):
    def module(self):
        self.assertTrue(SCRIPT.exists(), 'Diagnostic collector not implemented')
        spec = importlib.util.spec_from_file_location('diagnostics', SCRIPT)
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        return m

    def fixture(self, root):
        run = root / 'return_output/artifacts/previous'; run.mkdir(parents=True)
        (run/'frames').mkdir(); (run/'frames/frame_000001.jpg').write_bytes(b'original frame')
        (run/'report').mkdir(); (run/'report/result.json').write_text('{"status":"completed"}')
        (run/'sparse/0').mkdir(parents=True); (run/'sparse/0/cameras.bin').write_bytes(b'model')
        (run/'input').mkdir(); (run/'input/source.mov').write_bytes(b'private original video')
        (run/'config.json').write_text('{}')
        with closing(sqlite3.connect(run/'database.db')) as db:
            db.executescript('CREATE TABLE images(image_id INTEGER, name TEXT, camera_id INTEGER); CREATE TABLE keypoints(image_id INTEGER, rows INTEGER); CREATE TABLE matches(pair_id INTEGER, rows INTEGER); CREATE TABLE two_view_geometries(pair_id INTEGER, rows INTEGER, config INTEGER); CREATE TABLE cameras(camera_id INTEGER, model INTEGER, width INTEGER, height INTEGER, params BLOB, prior_focal_length INTEGER);')
            db.executemany('INSERT INTO images VALUES (?,?,1)',[(2,'frame_000001.jpg'),(7,'frame_000002.jpg'),(12,'frame_000003.jpg')])
            db.executemany('INSERT INTO keypoints VALUES (?,?)',[(2,100),(7,100),(12,30)])
            db.execute('INSERT INTO matches VALUES (?,80)',(2*2147483647+7,))
            db.execute('INSERT INTO two_view_geometries VALUES (?,40,2)',(2*2147483647+7,))
            db.execute('INSERT INTO cameras VALUES (1,2,900,1600,?,0)',(struct.pack('<4d',1920,450,800,0),))
            db.commit()
        return run

    def test_readonly_collection_and_graph(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); run=self.fixture(root)
            before={str(p.relative_to(run)):hashlib.sha256(p.read_bytes()).hexdigest() for p in run.rglob('*') if p.is_file()}
            archive=m.collect(root,run,50*1024*1024)
            self.assertEqual(archive.parent,root/'return_output')
            self.assertEqual(struct.unpack('<I',archive.read_bytes()[4:8])[0],0)
            with tarfile.open(archive) as t:
                self.assertTrue(all(x.mtime==0 for x in t))
                names=t.getnames(); self.assertTrue(any(n.endswith('/database.db') for n in names))
                self.assertTrue(any(n.endswith('/frames/frame_000001.jpg') for n in names))
                self.assertFalse(any('source.mov' in n for n in names))
                graph=json.load(t.extractfile(next(n for n in names if n.endswith('/database_summary.json'))))
                self.assertEqual(graph['adjacent_pairs'][0]['inliers'],40)
                self.assertEqual(graph['adjacent_pairs'][1]['inliers'],None)
                self.assertEqual(graph['components_at_15_inliers'],[[2,7],[12]])
                self.assertEqual(graph['cameras'][0]['params'][0],1920)
            after={str(p.relative_to(run)):hashlib.sha256(p.read_bytes()).hexdigest() for p in run.rglob('*') if p.is_file()}
            self.assertEqual(before,after)

    def test_size_limit_and_symlink_rejected(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); run=self.fixture(root)
            with self.assertRaises(ValueError):m.collect(root,run,1)
            (run/'frames/link.jpg').symlink_to(run/'input/source.mov')
            with self.assertRaises(ValueError):m.collect(root,run,50*1024*1024)
            self.assertFalse(list((root/'return_output').glob('room_diagnostics_*.tar.gz')))

if __name__=='__main__':unittest.main()
