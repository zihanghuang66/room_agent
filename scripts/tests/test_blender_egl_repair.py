import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import contextlib
import io
import os
import shlex
import sys
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/'blender-egl-repair/run.py'

class RepairTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(),'EGL repair runner is not implemented')
        spec=importlib.util.spec_from_file_location('egl_repair',SCRIPT)
        self.m=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.m)

    def test_missing_egl_maps_to_userspace_packages_only(self):
        selected=self.m.missing_packages({'libEGL.so.1':False,'libGL.so.1':True,'libOpenGL.so.0':False})
        self.assertEqual(selected,['libegl1','libopengl0'])
        self.assertEqual(self.m.missing_packages({k:True for k in self.m.LIB_PACKAGES}),[])

    def test_install_plan_rejects_driver_changes_and_removal(self):
        for output in ['Inst nvidia-driver-570 (570 repository)\n','Inst libnvidia-gl-570 (570 repository)\n','Remv libgl1 [1.0]\n']:
            with self.assertRaises(ValueError):self.m.validate_plan(output)
        self.m.validate_plan('Inst libglvnd0 (1.3 repository)\nInst libegl1 (1.3 repository)\n')

    def test_globally_passed_requires_png_and_mode_matching_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'eevee.json').write_text(json.dumps({'status':'passed','mode':'eevee'}))
            self.assertFalse(self.m.check_render(root,'eevee',{'exit_code':0})['passed'])
            (root/'eevee.png').write_bytes(b'\x89PNG\r\n\x1a\n'+b'test')
            self.assertTrue(self.m.check_render(root,'eevee',{'exit_code':0})['passed'])
            self.assertFalse(self.m.check_render(root,'eevee',{'exit_code':-6})['passed'])

    def test_software_renderer_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'eevee.json').write_text(json.dumps({'status':'passed','mode':'eevee','renderer':'llvmpipe (LLVM)'}))
            (root/'eevee.png').write_bytes(b'\x89PNG\r\n\x1a\n')
            self.assertTrue(self.m.check_render(root,'eevee',{'exit_code':0})['software_renderer'])

    def test_report_packaging_zero_timestamps_and_hashes(self):
        import tarfile
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);report=root/'report';report.mkdir();(report/'result.json').write_text('{}')
            out=self.m.pack_report(report,root)
            with tarfile.open(out) as t:
                self.assertTrue(all(x.mtime==0 for x in t.getmembers()))
                self.assertIn('report/manifest_sha256.json',t.getnames())
            self.assertEqual(out.read_bytes()[4:8],b'\0'*4)

    def test_full_workflow_installs_and_returns_verified_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);helpers=root/'helpers';helpers.mkdir();marker=helpers/'installed'
            aptworker=helpers/'fake_apt.py'
            aptworker.write_text('import sys\nfrom pathlib import Path\n'
                +"print('Inst libegl1 (1.3 local-test)')\n"
                +'if "-y" in sys.argv:Path('+repr(str(marker))+').touch()\n')
            apt=helpers/'apt-get';apt.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(aptworker))+' "$@"\n');apt.chmod(0o755)
            (helpers/'probe.py').write_text('import json\nfrom pathlib import Path\n'
                +'ready=Path('+repr(str(marker))+').exists()\n'
                +'print(json.dumps({"libraries":{name:ready for name in '+repr(list(self.m.LIB_PACKAGES))+'}}))\n')
            worker=helpers/'fake_blender.py'
            worker.write_text('import sys,json\nfrom pathlib import Path\n'
                +'mode=sys.argv[sys.argv.index("--mode")+1];out=Path(sys.argv[sys.argv.index("--output")+1])\n'
                +'(out/(mode+".png")).write_bytes(b"\\x89PNG\\r\\n\\x1a\\n")\n'
                +'(out/(mode+".json")).write_text(json.dumps({"status":"passed","mode":mode,"renderer":"NVIDIA test fixture"}))\n')
            binary=root/'return_output/tools/blender-5.1.2-linux-x64/blender';binary.parent.mkdir(parents=True)
            binary.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(worker))+' "$@"\n');binary.chmod(0o755)
            with patch.object(self.m,'HERE',helpers),patch.object(self.m.platform,'system',return_value='Linux'),patch.object(self.m.platform,'machine',return_value='x86_64'),patch.object(self.m.os,'geteuid',return_value=0),patch.object(self.m.shutil,'which',side_effect=lambda x:str(apt) if x=='apt-get' else None),patch('sys.argv',['run.py','--project-root',temp]),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.m.main(),0)
            result=json.loads(next((root/'return_output/artifacts').glob('*/result.json')).read_text())
            self.assertTrue(marker.is_file());self.assertEqual(result['status'],'blender_checks_passed')
            self.assertEqual(result['missing_packages_after'],[])
            self.assertEqual(len(list((root/'return_output').glob('room_blender_egl_*.tar.gz'))),1)

if __name__=='__main__':unittest.main()
