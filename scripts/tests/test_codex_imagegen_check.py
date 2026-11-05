import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'codex-imagegen-check'))
from prepare import project_box,upright_angle
from probe import image_tool_evidence,validate_image,compact,run_codex
from run import pack

class ImagegenTests(unittest.TestCase):
    def test_projection_and_behind_camera(self):
        points=np.array([[x,y,z] for x in (-.5,.5) for y in (-.5,.5) for z in (-3,-2)])
        k=np.array([[100,0,100],[0,100,100],[0,0,1]])
        value=project_box(points,k,np.eye(4),200,200)
        np.testing.assert_allclose(value[0],[75,75]);np.testing.assert_allclose(value[1],[125,125])
        self.assertIsNone(project_box(-points,k,np.eye(4),200,200))
        self.assertEqual(upright_angle(np.eye(4)),0)

    def test_text_is_not_tool_proof(self):
        self.assertEqual(image_tool_evidence({'type':'item.completed','item':{'type':'agent_message','text':'I used image_gen and succeeded'}}),[])
        self.assertEqual(image_tool_evidence({'type':'item.completed','item':{'type':'command_execution','aggregated_output':'{"name":"imagegen","status":"completed"}'}}),[])
        proof=image_tool_evidence({'type':'item.completed','item':{'type':'image_generation','status':'completed','id':'img1'}})
        self.assertEqual(proof[0]['id'],'img1')
        self.assertTrue(image_tool_evidence({'type':'item.completed','item':{'type':'mcp_tool_call','server':'image_gen','tool':'imagegen','status':'completed'}}))

    def test_input_copy_and_blank_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);pixels=np.indices((256,256)).sum(0).astype(np.uint8)
            rgb=np.stack([pixels,pixels//2,pixels//3],axis=2)
            Image.fromarray(rgb).save(root/'input.png');Image.fromarray(rgb).save(root/'out.png')
            with self.assertRaisesRegex(ValueError,'copy'):validate_image(root/'out.png',[root/'input.png'],root)
            Image.new('RGB',(256,256),'black').save(root/'out.png')
            with self.assertRaisesRegex(ValueError,'constant'):validate_image(root/'out.png',[root/'input.png'],root)
            Image.fromarray(np.flip(rgb,0).copy()).save(root/'out.png')
            self.assertTrue(validate_image(root/'out.png',[root/'input.png'],root)['png_valid'])
            self.assertTrue((root/'comparison.jpg').is_file())

    def test_secret_and_image_data_redaction(self):
        with patch.dict(os.environ,{'TEST_API_KEY':'sensitive-key-123456'}):
            value=compact({'type':'error','message':'sensitive-key-123456','b64_json':'imagebytes','url':'data:image/png;base64,AAAA'})
        self.assertNotIn('sensitive-key',json.dumps(value));self.assertNotIn('imagebytes',json.dumps(value))
        self.assertEqual(value['url'],'[image data omitted]')

    def test_mock_stream_and_timeout(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            code='import json;print(json.dumps({"type":"item.completed","item":{"type":"image_generation","status":"completed"}}));print(json.dumps({"type":"turn.completed"}))'
            rc,events,proof=run_codex([sys.executable,'-c',code],root,dict(os.environ),root,10)
            self.assertEqual(rc,0);self.assertEqual(len(proof),1)
            with self.assertRaises(TimeoutError):run_codex([sys.executable,'-c','import time;time.sleep(30)'],root,dict(os.environ),root,.1)
            self.assertTrue(json.loads((root/'tool_evidence.json').read_text())['timed_out'])

    def test_return_allowlist(self):
        import tarfile
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);folder=root/'return_output/artifacts/probe';(folder/'outputs').mkdir(parents=True)
            (folder/'logs').mkdir();(folder/'result.json').write_text('{}')
            (folder/'outputs/arbitrary_auth.json').write_text('NEVER_RETURN')
            (folder/'logs/stderr.log').write_text('test')
            out=pack(folder,root)
            with tarfile.open(out) as tar:
                self.assertEqual(len(tar.getmembers()),3)
                self.assertTrue(all(m.mtime==0 for m in tar.getmembers()))
                self.assertFalse(any('auth' in m.name for m in tar.getmembers()))
            self.assertEqual(out.read_bytes()[4:8],b'\0'*4)

if __name__=='__main__':unittest.main()
