import importlib.util
from pathlib import Path
import tempfile
import unittest
import json
import struct
from types import SimpleNamespace
import numpy as np

spec = importlib.util.spec_from_file_location('room_mesh', Path(__file__).parents[1]/'omega-mesh/mesh.py')
mesh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mesh)


class MeshGeometryTests(unittest.TestCase):
    def setUp(self):
        self.depth = np.ones((3, 8, 8), np.float32)
        self.conf = np.ones_like(self.depth)*10
        self.e = np.tile(np.eye(4, dtype=np.float32)[:3], (3, 1, 1))
        self.k = np.tile(np.array([[4., 0, 3.], [0, 4., 3.], [0, 0, 1.]]), (3, 1, 1))

    def test_plane_support_and_contradiction(self):
        self.assertTrue(mesh.support_mask(1, self.depth, self.conf, self.e, self.k, 9).all())
        self.depth[[0, 2]] *= 2
        self.assertFalse(mesh.support_mask(1, self.depth, self.conf, self.e, self.k, 9).any())

    def test_world_to_camera_translation(self):
        self.e[0, 0, 3] = 2
        p = mesh.world_points(self.depth[0], self.e[0], self.k[0])
        np.testing.assert_allclose(p[3, 3], [-2, 0, 1])

    def test_dense_export_has_no_cap(self):
        rgb = np.full((*self.depth.shape, 3), 128, np.uint8)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'dense.ply'
            count = mesh.export_dense(path, self.depth, self.conf, self.e, self.k, rgb, 9)
            header, body = path.read_bytes().split(b'end_header\n')
            self.assertEqual(count, 192)
            self.assertIn(b'element vertex 192', header)
            self.assertEqual(len(body), count*19)

    def test_glb_has_bounded_binary_buffer(self):
        triangle = SimpleNamespace(vertices=np.array([[0,0,0], [1,0,0], [0,1,0]]),
            triangles=np.array([[0,1,2]]), vertex_normals=np.tile([0,0,1], (3,1)),
            vertex_colors=np.eye(3))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'test.glb'
            mesh.write_glb(path, triangle)
            raw = path.read_bytes()
            magic, version, size = struct.unpack_from('<III', raw)
            self.assertEqual((magic, version, size), (0x46546c67, 2, len(raw)))
            length, kind = struct.unpack_from('<II', raw, 12)
            self.assertEqual(kind, 0x4e4f534a)
            doc = json.loads(raw[20:20+length])
            bin_length, bin_kind = struct.unpack_from('<II', raw, 20+length)
            self.assertEqual(bin_kind, 0x004e4942)
            self.assertEqual(bin_length, doc['buffers'][0]['byteLength'])
            self.assertNotIn('uri', doc['buffers'][0])
            for view in doc['bufferViews']:
                self.assertLessEqual(view['byteOffset']+view['byteLength'], bin_length)
                self.assertEqual(view['byteOffset']%4, 0)


if __name__ == '__main__':
    unittest.main()
