import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
import numpy as np

PACKAGE=Path(__file__).resolve().parents[1]/'vggt-omega'

def load(name):
    path=PACKAGE/(name+'.py')
    if not path.exists():raise AssertionError('Omega module not implemented: '+name)
    spec=importlib.util.spec_from_file_location('omega_'+name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

class OmegaTests(unittest.TestCase):
    def test_unprojection_camera_translation_and_rotation(self):
        m=load('geometry')
        depth=np.ones((1,2,2),dtype=np.float32)*2
        k=np.eye(3,dtype=np.float32)[None]
        e=np.array([[[0,-1,0,1],[1,0,0,2],[0,0,1,3]]],dtype=np.float32)
        world=m.unproject(depth,e,k)
        # world = R.T @ (camera - t), camera at pixel (1,0) = [2,0,2]
        np.testing.assert_allclose(world[0,0,1],[-2,-1,-1])
        projected=world[0]@e[0,:,:3].T+e[0,:,3]
        np.testing.assert_allclose(projected[...,2],depth[0])

    def test_export_handles_invalid_depth_and_point_limit(self):
        m=load('geometry')
        d=np.ones((2,3,4),np.float32);d[0,0,0]=np.nan;d[0,0,1]=-1
        c=np.ones_like(d);c[0,0,2]=np.inf
        e=np.tile(np.eye(4,dtype=np.float32)[:3],(2,1,1));k=np.tile(np.eye(3,dtype=np.float32),(2,1,1));rgb=np.full((2,3,4,3),120,np.uint8)
        points,colors,scores,stats=m.select_cloud(d,c,e,k,rgb,50,5)
        self.assertEqual(len(points),5);self.assertEqual(stats['valid_pixels'],21)
        self.assertTrue(np.isfinite(points).all())
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'model.ply';m.write_ply(p,points,colors,scores)
            data=p.read_bytes();head,payload=data.split(b'end_header\n',1)
            self.assertIn(b'element vertex 5',head);self.assertEqual(len(payload),5*19)

    def test_manifest_rejects_modified_frame(self):
        m=load('common')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'a.jpg').write_bytes(b'a')
            manifest={'selected_frames':[{'file':'a.jpg','sha256':m.sha256(root/'a.jpg')}]}
            self.assertEqual(m.verify_frames(root,manifest),[root/'a.jpg'])
            (root/'a.jpg').write_bytes(b'b')
            with self.assertRaises(ValueError):m.verify_frames(root,manifest)
            manifest['selected_frames'][0]['file']='../a.jpg'
            with self.assertRaises(ValueError):m.verify_frames(root,manifest)

    def test_missing_checkpoint_returns_failure_archive_without_inference(self):
        load('common')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            r=subprocess.run([sys.executable,'-B',str(PACKAGE/'run.py'),'--project-root',tmp,'--checkpoint',str(root/'missing.pt')],capture_output=True,text=True)
            self.assertEqual(r.returncode,1,r.stdout+r.stderr)
            archive=next((root/'return_output').glob('room_omega_*.tar.gz'))
            self.assertEqual(struct.unpack('<I',archive.read_bytes()[4:8])[0],0)
            with tarfile.open(archive) as t:
                self.assertTrue(all(x.mtime==0 for x in t))
                result=json.load(t.extractfile(next(x for x in t if x.name.endswith('/result.json'))))
            self.assertEqual(result['status'],'failed');self.assertFalse(result['inference_executed']);self.assertIn('checkpoint',result['error'].lower())

if __name__=='__main__':unittest.main()
