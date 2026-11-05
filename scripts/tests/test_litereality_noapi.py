"""Geometry conventions, unsafe inputs, subprocess cleanup, and return-package boundaries."""
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
import numpy as np
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'litereality-noapi'))
from geometry import load_frame,backproject
from common import extract_checked,digest
from run import execute,package_report

class NoApiTests(unittest.TestCase):
    def test_metric_camera_axes_and_confidence(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);f=p/'frame_00000.json'
            pose=np.eye(4);pose[:3,3]=[10,20,30]
            f.write_text(json.dumps(dict(intrinsics=[[2,0,1],[0,2,1],[0,0,1]],cameraPoseARFrame=pose.tolist())))
            Image.new('RGB',(4,4),(255,0,0)).save(f.with_suffix('.jpg'))
            Image.fromarray(np.full((2,2),2000,dtype=np.uint16)).save(p/'depth_00000.png')
            Image.fromarray(np.array([[0,0],[0,2]],dtype=np.uint8)).save(p/'conf_00000.png')
            _,depth,k,c2w,row=load_frame(f)
            pts,v,u=backproject(depth,k,c2w)
            # RGB intrinsics scale by 1/2. Pixel(1,1), z=2 => CV(1,1,2), ARKit(1,-1,-2).
            np.testing.assert_allclose(pts,[[11,19,28]])
            self.assertEqual(row['valid_fraction'],.25)
            f.write_text(json.dumps(dict(intrinsics=[[2,0,1],[0,2,1],[0,0,1]],cameraPoseARFrame=pose.T.tolist())))
            with self.assertRaisesRegex(ValueError,'matrix convention'):load_frame(f)

    def test_depth_8bit_is_not_millimetre_depth(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);f=p/'frame_00000.json'
            f.write_text(json.dumps(dict(intrinsics=np.eye(3).tolist(),cameraPoseARFrame=np.eye(4).tolist())))
            Image.new('RGB',(2,2)).save(f.with_suffix('.jpg'))
            Image.fromarray(np.ones((2,2),dtype=np.uint8)).save(p/'depth_00000.png')
            with self.assertRaisesRegex(ValueError,'integer depth'):load_frame(f)

    def test_tar_traversal_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            for i,kind in enumerate(('traversal','symlink')):
                archive=root/(kind+'.tar')
                with tarfile.open(archive,'w') as tar:
                    info=tarfile.TarInfo('../outside' if i==0 else 'link')
                    if i:info.type=tarfile.SYMTYPE;info.linkname='/etc'
                    tar.addfile(info)
                with self.assertRaisesRegex(ValueError,'Unsafe'):
                    extract_checked(archive,root/('out'+str(i)),digest(archive))
                self.assertFalse((root/('out'+str(i))).exists())

    def test_return_excludes_work_and_preserves_outputs_zero_timestamps(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);folder=root/'return_output/artifacts/test'
            (folder/'work').mkdir(parents=True);(folder/'deliverables').mkdir()
            (folder/'work/secret-looking-data.txt').write_text('not for return')
            (folder/'deliverables/cloud.ply').write_text('example')
            output=package_report(folder,root)
            self.assertEqual(output.read_bytes()[4:8],b'\0'*4)
            with tarfile.open(output) as tar:
                names=tar.getnames();self.assertTrue(any(n.endswith('cloud.ply') for n in names))
                self.assertFalse(any('/work/' in n for n in names))
                self.assertTrue(all(m.mtime==0 for m in tar.getmembers()))

    def test_child_failure_and_timeout_return_errors(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)
            with self.assertRaisesRegex(RuntimeError,'exit code 7'):
                execute([sys.executable,'-c','print("EXPECTED_FAILURE");raise SystemExit(7)'],p/'failure.log',p,{},5)
            self.assertIn('EXPECTED_FAILURE',(p/'failure.log').read_text())
            with self.assertRaises(TimeoutError):
                execute([sys.executable,'-c','import time;time.sleep(30)'],p/'timeout.log',p,{},.2)

if __name__=='__main__':unittest.main()
