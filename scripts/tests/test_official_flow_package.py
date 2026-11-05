"""Offline regressions for the official-flow container adapter, not GPU/model tests."""
import ast
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(os.environ.get('ROOM_TEST_PACKAGE', str(Path(__file__).resolve().parents[1] / 'litereality-full-v0.2.3')))
UPSTREAM = Path(__file__).resolve().parents[1] / 'vendor/litereality-28ffe1fd/source'


class ContainerAdapterTests(unittest.TestCase):
    def test_gpu_rejection_records_actual_measurement_before_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            free = int(17.971 * 1024**3)
            cuda = SimpleNamespace(is_available=lambda: True, mem_get_info=lambda n: (free, 24*1024**3),
                                   get_device_name=lambda n: 'TEST GPU')
            fake = SimpleNamespace(cuda=cuda, __version__='TEST', version=SimpleNamespace(cuda='TEST'))
            output = io.StringIO()
            with patch.dict(os.environ, ROOM_RUN=tmp), patch.dict(sys.modules, torch=fake), \
                 patch.object(sys, 'argv', ['preflight.py', 'gpu']), \
                 patch.object(sys, 'path', [str(SOURCE), *sys.path]), contextlib.redirect_stdout(output):
                with self.assertRaises(RuntimeError):
                    runpy.run_path(str(SOURCE/'preflight.py'), run_name='__main__')
            report = Path(tmp)/'checks/gpu.json'
            self.assertTrue(report.is_file(), 'Failing preflight must retain its actual measurement')
            data = json.loads(report.read_text())
            self.assertEqual(data['free_bytes'], free)
            self.assertEqual(data['status'], 'insufficient_memory')
            self.assertIn('17.971', output.getvalue())

    def test_generic_runner_requires_explicit_input_and_scene(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = subprocess.run([sys.executable, str(SOURCE/'run.py'), '--project-root', tmp, '--prepare-only'],
                               text=True, capture_output=True)
        self.assertEqual(p.returncode, 2, p.stdout+p.stderr)
        self.assertIn('--scan-archive', p.stderr)
        self.assertIn('--scene', p.stderr)
        self.assertNotIn('RETURN_TAR:', p.stdout)

    def test_export_copy_can_resolve_original_texture_without_basename_guessing(self):
        module = SOURCE/'texture_rebase.py'
        self.assertTrue(module.is_file(), 'Full pipeline must also carry the verified texture relocation fix')
        spec = importlib.util.spec_from_file_location('texture_probe', module)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); texture = root/'room/materials/wood.png'
            texture.parent.mkdir(parents=True); texture.write_bytes(b'correct texture')
            self.assertEqual(mod.resolve_image_path('//../room/materials/wood.png', root/'candidate',
                                                   root/'room_preview'), texture.resolve())
            with self.assertRaises(FileNotFoundError):
                mod.resolve_image_path('//unrelated/wood.png', root/'candidate', root/'room_preview')

    def test_official_prompt_literals_remain_unchanged(self):
        modules = ['agent/author.py', 'pipeline/realism_authoring/author/quality.py',
                   'pipeline/realism_authoring/author/refine_objects.py',
                   'pipeline/realism_authoring/author/materials.py', 'agent/tools/critic/tool.py']
        def prompts(path):
            tree = ast.parse(path.read_text()); result = {}
            for node in tree.body:
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and any(k in target.id for k in ('PROMPT', 'BRIEF', 'CLAUSE')):
                            result[target.id] = node.value.value
            return result
        for name in modules:
            rel = Path('src/litereality_agent')/name
            with self.subTest(module=name):
                self.assertEqual(prompts(SOURCE/'app'/rel), prompts(UPSTREAM/rel))

    def test_official_frame_selection_covers_long_scan(self):
        path = SOURCE/'app/src/litereality_agent/room_ops/rendering/room_render/render_room_cameras.py'
        tree = ast.parse(path.read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'select_frames')
        scope = {}; exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
        self.assertEqual(scope['select_frames'](list(range(150)), '6'), [0,30,60,89,119,149])
        self.assertEqual(scope['select_frames']([4,30,99,170,201,350,500], '6')[-1], 500)

    def test_acceptance_predicate_and_view_selection_match_upstream(self):
        rel=Path('src/litereality_agent/pipeline/realism_authoring/acceptance.py')
        for name in ('visual_accepted','fingerprint','preserved_instances','is_accepted','repair'):
            def function(root):
                tree=ast.parse((root/rel).read_text())
                node=next(n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
                for item in ast.walk(node):
                    if isinstance(item,ast.keyword) and item.arg=='model': item.value=ast.Constant(value='BACKEND_MODEL')
                return ast.dump(node)
            self.assertEqual(function(SOURCE/'app'),function(UPSTREAM),name)
        rel=Path('src/litereality_agent/agent/tools/select_views')
        for p in (UPSTREAM/rel).rglob('*.py'):
            self.assertEqual(p.read_bytes(),(SOURCE/'app'/rel/p.relative_to(UPSTREAM/rel)).read_bytes())

    def test_failed_visual_review_cannot_be_accepted(self):
        import math
        path=SOURCE/'app/src/litereality_agent/pipeline/realism_authoring/acceptance.py'
        node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='visual_accepted')
        scope={'math':math};exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),scope)
        accept=scope['visual_accepted'];good={'pass':True,'score':8,'issues':[],'all_objects_reviewed':True}
        self.assertTrue(accept(good))
        for changes in ({'score':7.9},{'score':float('nan')},{'score':True},{'issues':['missing chair']},{'all_objects_reviewed':False},{'pass':False}):
            self.assertFalse(accept(dict(good,**changes)))
        self.assertFalse(accept({'status':'check_failed'}))


if __name__ == '__main__':
    unittest.main()
