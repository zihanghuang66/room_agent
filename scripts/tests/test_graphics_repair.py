import importlib.util
from pathlib import Path
import unittest
import tempfile
import subprocess
import os
import json
from unittest.mock import patch

MODULE=Path(__file__).resolve().parents[1]/'codex-integration-check/graphics_repair.py'

class GraphicsRepairTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MODULE.is_file(),'graphics repair implementation missing')
        spec=importlib.util.spec_from_file_location('graphics_repair',MODULE);self.m=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.m)

    def test_only_exact_driver_matched_existing_egl_is_eligible(self):
        data={'driver_version':'570.124.04','nvidia_files':[{'realpath':'/usr/lib/libEGL_nvidia.so.570.124.04','exists':True},{'realpath':'/usr/lib/libEGL_nvidia.so.580.1.1','exists':True}]}
        self.assertEqual(self.m.select_egl(data),'/usr/lib/libEGL_nvidia.so.570.124.04')
        data['driver_version']='550.1.1';self.assertIsNone(self.m.select_egl(data))
        data['driver_version']=None;self.assertIsNone(self.m.select_egl(data))
        data['driver_version']='570.124.04';data['nvidia_files'][0]['exists']=False;self.assertIsNone(self.m.select_egl(data))

    def test_no_success_without_nvidia_renderer_and_artifacts(self):
        self.assertFalse(self.m.nvidia_render_ok({'passed':True,'details':{'renderer':'llvmpipe','vendor':'Mesa'}}))
        self.assertFalse(self.m.nvidia_render_ok({'passed':True,'details':{}}))
        self.assertFalse(self.m.nvidia_render_ok({'passed':False,'details':{'renderer':'NVIDIA RTX 3090'}}))
        self.assertTrue(self.m.nvidia_render_ok({'passed':True,'details':{'renderer':'NVIDIA GeForce RTX 3090','vendor':'NVIDIA Corporation'}}))

    def test_failed_loader_does_not_create_vendor_config_or_activation(self):
        data={'driver_version':'570.124.04','nvidia_files':[{'realpath':'/usr/lib/libEGL_nvidia.so.570.124.04','exists':True}]}
        with tempfile.TemporaryDirectory() as temp,patch.object(self.m.subprocess,'run',return_value=subprocess.CompletedProcess([],1,stdout='libnvidia-eglcore.so: cannot open shared object')):
            report=Path(temp);result,env=self.m.prepare(report,data)
            self.assertEqual(result['status'],'blocked');self.assertIsNone(env)
            self.assertIn('libnvidia-eglcore',result['loader_error'])
            self.assertFalse((report/'nvidia_egl_candidate.json').exists())
            self.assertFalse((report/'activate_graphics.sh').exists())

    def test_candidate_is_process_local_and_not_claimed_as_render_success(self):
        data={'driver_version':'570.124.04','nvidia_files':[{'realpath':'/usr/lib/nvidia/libEGL_nvidia.so.570.124.04','exists':True}]}
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'CUDA_VISIBLE_DEVICES':'assigned-only','LD_LIBRARY_PATH':'/existing'}),patch.object(self.m.subprocess,'run',return_value=subprocess.CompletedProcess([],0,stdout='NVIDIA_EGL_LOAD_OK')):
            before=dict(os.environ);report=Path(temp);result,env=self.m.prepare(report,data)
            self.assertEqual(result['status'],'candidate_ready')
            self.assertEqual(dict(os.environ),before)
            self.assertEqual(env['CUDA_VISIBLE_DEVICES'],'assigned-only')
            self.assertEqual(env['LD_LIBRARY_PATH'],'/usr/lib/nvidia:/existing')
            self.assertEqual(json.loads((report/'nvidia_egl_candidate.json').read_text())['ICD']['library_path'],data['nvidia_files'][0]['realpath'])
            self.assertFalse((report/'activate_graphics.sh').exists())

    def test_explicit_software_override_is_not_silently_cleared(self):
        data={'driver_version':'570.124.04','nvidia_files':[{'realpath':'/usr/lib/libEGL_nvidia.so.570.124.04','exists':True}],'graphics_overrides':{'LIBGL_ALWAYS_SOFTWARE':'1'}}
        with tempfile.TemporaryDirectory() as temp:
            result,env=self.m.prepare(Path(temp),data)
            self.assertEqual(result['status'],'blocked');self.assertIsNone(env)

if __name__=='__main__':unittest.main()
