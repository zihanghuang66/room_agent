"""A chair-only room must not fail merely because generic-object metadata is absent."""
import ast
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch
SOURCE=Path(os.environ['ROOM_TEST_PACKAGE'])
class ReferenceAudit(unittest.TestCase):
    def audit(self, members, status='ok'):
        path=SOURCE/'stage.py'
        fn=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='audit')
        scope={'json':json,'paths':SimpleNamespace(set_scan=lambda s:None)}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),str(path),'exec'),scope)
        name='litereality_agent.pipeline.scene_init.ingest.references.object_references'
        module=ModuleType(name);module.load_scene_objects=lambda scan:['Chair0']
        with tempfile.TemporaryDirectory() as tmp,patch.dict(sys.modules,{name:module}):
            root=Path(tmp);(root/'object_init').mkdir()
            (root/'object_init/chair_clusters.json').write_text(json.dumps({'clusters':[{'members':members,'reference_status':status}]}))
            return scope['audit'](SimpleNamespace(scan='generic',object_root=root),'ingest')
    def test_chair_only_references_count_as_coverage(self):
        self.assertEqual(self.audit(['Chair0'])['missing_furniture'],[])
    def test_missing_or_failed_chair_reference_still_fails(self):
        for members,status in [([], 'ok'),(['Chair0'],'error')]:
            with self.assertRaises(RuntimeError):self.audit(members,status)
if __name__=='__main__':unittest.main()
