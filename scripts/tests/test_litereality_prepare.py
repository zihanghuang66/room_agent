import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

PACKAGE=Path(__file__).parents[1]/'litereality-agent-prepare'
sys.path.insert(0,str(PACKAGE))
import common
import probe


class PreparationTests(unittest.TestCase):
    def test_new_scan_nested_directory_and_nonconsecutive_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); capture=root/'Scanner_9952';capture.mkdir()
            (capture/'room.usdz').write_bytes(b'zip')
            for number in (2,7,11):
                for name in (f'frame_{number:05d}.json',f'frame_{number:05d}.jpg',f'depth_{number:05d}.png'):
                    (capture/name).write_bytes(b'test')
            self.assertTrue(hasattr(common,'find_capture'),'Dynamic scan discovery is missing')
            self.assertEqual(common.find_capture(root),capture)
            self.assertEqual([p.stem for p in common.frame_files(capture)],['frame_00002','frame_00007','frame_00011'])
            (capture/'depth_00007.png').unlink()
            with self.assertRaisesRegex(ValueError,'Missing RGB/depth'):common.frame_files(capture)

    def test_venv_python_symlink_is_not_dereferenced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);base=root/'base';base.write_text('interpreter')
            venv=root/'venv/bin';venv.mkdir(parents=True);(venv/'python').symlink_to(base)
            path=common.executable_path(venv/'python')
            self.assertEqual(path.parent,venv)
            self.assertTrue(path.is_symlink())

    def make_tar(self,path,name,kind=None):
        with tarfile.open(path,'w') as t:
            info=tarfile.TarInfo(name)
            if kind:info.type=kind;info.linkname='/tmp/outside'
            else:info.size=4
            t.addfile(info,io.BytesIO(b'test') if not kind else None)

    def test_reject_traversal_and_links_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive=root/'input.tar'
            for name,kind in [('../escape',None),('/absolute',None),('link',tarfile.SYMTYPE)]:
                self.make_tar(archive,name,kind)
                with self.assertRaises(ValueError):common.extract_checked(archive,root/'out',common.digest(archive))
                self.assertFalse((root/'out').exists())

    def test_wrong_hash_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive=root/'input.tar';self.make_tar(archive,'safe')
            with self.assertRaises(ValueError):common.extract_checked(archive,root/'out','0'*64)

    def test_only_presence_no_credentials_in_probe(self):
        with patch.dict('os.environ',{'OPENAI_API_KEY':'SENTINEL_SECRET_DO_NOT_EXPORT'}),patch('shutil.which',return_value=None):
            result=probe.collect(network=False)
            self.assertTrue(result['credential_environment_present']['OPENAI_API_KEY'])
            self.assertNotIn('SENTINEL_SECRET_DO_NOT_EXPORT',str(result))

    def test_return_archive_zero_mtime_and_checksums(self):
        with tempfile.TemporaryDirectory() as tmp:
            report=Path(tmp)/'return_output/artifacts/report';report.mkdir(parents=True)
            common.save(report/'result.json',{'status':'test'})
            archive=common.pack_report(report)
            self.assertEqual(archive.read_bytes()[4:8],b'\0'*4)
            with tarfile.open(archive) as tar:
                self.assertTrue(all(m.mtime==0 for m in tar.getmembers()))
                self.assertEqual(len(tar.getmembers()),2)


if __name__=='__main__':unittest.main()
