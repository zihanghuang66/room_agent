import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

spec=importlib.util.spec_from_file_location('blender_setup',Path(__file__).resolve().parents[1]/'blender-runtime-setup/run.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class BlenderDownloadTests(unittest.TestCase):
    def archive(self,root):
        p=root/'upload.tar.xz'
        with tarfile.open(p,'w:xz') as t:
            data=b'fake executable';info=tarfile.TarInfo(m.FOLDER+'/blender');info.size=len(data);t.addfile(info,io.BytesIO(data))
        return p

    def test_http_failure_records_status_without_sensitive_error_text(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch.object(m.platform,'system',return_value='Linux'),patch.object(m.platform,'machine',return_value='x86_64'),patch.object(m.shutil,'which',return_value=None),patch.object(m.subprocess,'run',return_value=m.subprocess.CompletedProcess([],0)),patch.object(m.urllib.request,'urlopen',side_effect=urllib.error.HTTPError(m.URL,403,'SECRET_PROXY_PASSWORD',{},None)),patch('sys.argv',['run.py','--project-root',temp]),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(m.main(),1)
            result=json.loads(next((root/'return_output/artifacts').glob('*/result.json')).read_text())
            self.assertEqual(result.get('stage'),'download')
            self.assertEqual(result.get('download',{}).get('attempts',[{}])[0].get('http_status'),403)
            self.assertNotIn('SECRET_PROXY_PASSWORD',json.dumps(result))

    def test_local_archive_install_does_not_use_network(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=self.archive(root)
            with patch.object(m,'SHA256',m.digest(p)),patch.object(m.urllib.request,'urlopen',side_effect=AssertionError('network forbidden')):
                binary=m.install(root,archive_path=p,download_report={})
                self.assertEqual(binary.read_bytes(),b'fake executable')

    def test_invalid_local_archive_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=self.archive(root)
            with patch.object(m.urllib.request,'urlopen',side_effect=AssertionError('network forbidden')):
                with self.assertRaisesRegex(ValueError,'checksum'):m.install(root,archive_path=p,download_report={})
            self.assertFalse((root/'return_output/tools'/m.FOLDER).exists())

    def test_temporary_http_error_retries_then_verifies_download(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=self.archive(root);data=p.read_bytes();report={}
            with patch.object(m,'SHA256',m.digest(p)),patch.object(m.time,'sleep'),patch.object(m.urllib.request,'urlopen',side_effect=[urllib.error.HTTPError(m.URL,503,'unavailable',{},None),io.BytesIO(data)]):
                binary=m.install(root,download_report=report)
            self.assertTrue(binary.is_file());self.assertEqual(report['attempts'][0]['http_status'],503)
            self.assertEqual(report['attempts'][1]['status'],'verified')

if __name__=='__main__':unittest.main()
