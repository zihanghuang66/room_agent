import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
SOURCE=Path(os.environ['ROOM_TEST_PACKAGE'])
class DependencyTests(unittest.TestCase):
    def test_all_missing_modules_reported_together(self):
        self.assertTrue((SOURCE/'dependency_probe.py').is_file())
        spec=importlib.util.spec_from_file_location('dependency_probe',SOURCE/'dependency_probe.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        def fake(name):
            if name in ('fcl','coacd'):raise ImportError('missing '+name)
            return SimpleNamespace(__version__='test')
        with patch.object(m.importlib,'import_module',fake): report=m.probe()
        self.assertEqual(set(report['missing']),{'fcl','coacd'})
        self.assertEqual(report['status'],'failed')
        self.assertIn('mujoco',report['modules'])
if __name__=='__main__':unittest.main()
