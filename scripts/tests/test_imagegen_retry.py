"""Offline reproduction of room_003's exit-zero / no-image transport failure.

Only the remote Codex process is simulated. Image validation, provenance, caching,
error recording and retry limits run as production code; no model/GPU claims.
"""
import ast
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

SOURCE = Path(os.environ.get('ROOM_TEST_PACKAGE', str(Path(__file__).resolve().parents[1] / 'litereality-full-v0.2.3')))
sys.path.insert(0, str(SOURCE))
import room_image_backend as backend


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'evidence.png'
        Image.new('RGB', (512, 512), 'white').save(self.source)
        self.output = self.root / 'reference.png'
        self.env = dict(ROOM_IMAGE_LOGS=str(self.root / 'imagegen'), ROOM_CODEX='TEST_ONLY',
                        LR_CODEX_MODEL='TEST_ONLY', CODEX_HOME=str(self.root / 'codex'),
                        ROOM_MAX_IMAGE_CALLS='64', ROOM_IMAGE_ATTEMPTS='3')
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, self.env).start()
        self.sleep = patch.object(backend.time, 'sleep').start()

    def success(self, cmd, job, env, logs, timeout):
        native = self.root / 'codex/generated_images/TEST_THREAD/output.png'
        native.parent.mkdir(parents=True, exist_ok=True)
        im = Image.new('RGB', (512, 512), 'black')
        im.paste('red', (100, 100, 400, 400)); im.save(native)
        (job / 'outputs/reference_raw.png').write_bytes(native.read_bytes())
        (job / 'outputs/generation_receipt.json').write_text(json.dumps({'original_image_path': str(native)}))
        return 0, [{'type': 'thread.started', 'thread_id': 'TEST_THREAD'}, {'type': 'turn.completed'}], []

    def failed(self, message, stderr='', rc=0):
        def run(cmd, job, env, logs, timeout):
            (logs / 'stderr.log').write_text(stderr)
            (logs / 'last_message.txt').write_text(message)
            return rc, [{'type': 'thread.started', 'thread_id': 'TEST_THREAD'},
                        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': message}},
                        {'type': 'turn.completed'}], []
        return run

    def invoke(self):
        return backend.generate_reference(self.source, 'TEST_FURNITURE', self.output)

    def test_exit_zero_network_failure_retries_then_caches_success(self):
        fail = self.failed('Image generation failed with a connection error. No PNG was returned.',
                           'ERROR codex_core::tools::router: error=image generation failed: connection failed: error sending request')
        attempts = [fail, self.success]
        with patch.object(backend, 'run_codex', side_effect=lambda *a: attempts.pop(0)(*a)) as runner:
            result = self.invoke()
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(runner.call_count, 2)
            self.assertEqual(self.invoke(), result)
            self.assertEqual(runner.call_count, 2)
        self.assertFalse((self.root/'imagegen/STOP.json').exists())
        failures = list((self.root/'imagegen').glob('*/failure.json'))
        self.assertEqual(json.loads(failures[0].read_text())['category'], 'network')

    def test_persistent_connection_error_is_bounded_and_prints_real_cause(self):
        with patch.object(backend, 'run_codex', side_effect=self.failed('Image generation failed: connection failed')) as runner:
            with self.assertRaisesRegex(RuntimeError, 'network.*connection failed'):
                self.invoke()
            self.assertEqual(runner.call_count, 3)
            with self.assertRaisesRegex(RuntimeError, 'earlier image call failed'):
                backend.generate_reference(self.source, 'NEXT', self.root/'next.png')
            self.assertEqual(runner.call_count, 3)

    def test_permanent_failures_are_not_retried_even_with_earlier_timeout(self):
        for message, category in [('IMAGEGEN_UNAVAILABLE', 'unavailable'),
                                  ('Authentication failed: 401 Unauthorized', 'authentication'),
                                  ('Image generation quota exceeded', 'quota')]:
            with self.subTest(category=category):
                logs = self.root / category
                with patch.dict(os.environ, ROOM_IMAGE_LOGS=str(logs)), patch.object(backend, 'run_codex',
                        side_effect=self.failed(message, 'request timed out')) as runner:
                    with self.assertRaisesRegex(RuntimeError, category): self.invoke()
                    self.assertEqual(runner.call_count, 1)

    def test_output_validation_failure_is_not_retried(self):
        def invalid(*args):
            result = self.success(*args)
            (args[1]/'outputs/reference_raw.png').write_bytes(b'invalid png')
            (args[3]/'stderr.log').write_text('Reconnecting: request timed out')
            return result
        with patch.object(backend, 'run_codex', side_effect=invalid) as runner:
            with self.assertRaisesRegex(RuntimeError, 'validation'): self.invoke()
            self.assertEqual(runner.call_count, 1)

    def test_no_image_without_network_evidence_is_not_retried(self):
        with patch.object(backend, 'run_codex', side_effect=self.failed('Done.')) as runner:
            with self.assertRaisesRegex(RuntimeError, 'missing_output'): self.invoke()
            self.assertEqual(runner.call_count, 1)

    def test_native_image_without_delivery_is_not_regenerated(self):
        def orphan(*args):
            native = self.root / 'codex/generated_images/TEST_THREAD/output.png'
            native.parent.mkdir(parents=True);native.write_bytes(b'possible native result')
            return self.failed('connection failed')(*args)
        with patch.object(backend, 'run_codex', side_effect=orphan) as runner:
            with self.assertRaisesRegex(RuntimeError, 'delivery_incomplete'): self.invoke()
            self.assertEqual(runner.call_count, 1)

    def test_session_limit_includes_retry_attempts(self):
        with patch.dict(os.environ, ROOM_MAX_IMAGE_CALLS='1'), patch.object(backend, 'run_codex',
                side_effect=self.failed('connection failed')) as runner:
            with self.assertRaisesRegex(RuntimeError, 'limit'): self.invoke()
            self.assertEqual(runner.call_count, 1)


class ChairResumeTests(unittest.TestCase):
    def test_existing_chair_goes_through_backend_and_sets_success_status(self):
        # Execute the actual function without importing CUDA/CV dependencies.
        src = SOURCE/'app/src/litereality_agent/pipeline/scene_init/ingest/references/chair_clusters.py'
        tree = ast.parse(src.read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'generate_for_cluster')
        from types import SimpleNamespace
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); png = root/'scan/generated/ChairCluster0.png'
            png.parent.mkdir(parents=True);Image.new('RGB',(512,512),'white').save(png)
            sheet = root/'sheet.jpg';Image.new('RGB',(512,512),'black').save(sheet)
            calls=[]
            model=SimpleNamespace(generate_reference=lambda *a,**k: calls.append(a),
                                  normalize_reference=lambda a,b: (b.parent.mkdir(parents=True,exist_ok=True),shutil.copyfile(a,b)))
            namespace=dict(Path=Path,json=json,image_model=model,np=SimpleNamespace(mean=lambda x:sum(x)/len(x)))
            exec(compile(ast.Module(body=[fn],type_ignores=[]),str(src),'exec'),namespace)
            cluster=dict(cluster_id='ChairCluster0',cluster_sheet='sheet.jpg',member_colors={})
            namespace['generate_for_cluster']('scan',cluster,root,'TEST',False)
            self.assertEqual(len(calls),1,'Existing image must have evidence/prompt/hash checked by backend')
            self.assertEqual(cluster.get('reference_status'),'ok')


if __name__ == '__main__': unittest.main()
