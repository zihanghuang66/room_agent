"""Integrity and failure-path regression checks; no paid models or internet."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'litereality-full'
sys.path.insert(0, str(SOURCE))
from room_image_backend import native_provenance, generate_reference
from run import pack, command


class FullRoomChecks(unittest.TestCase):
    def test_image_transport_cache_and_circuit_breaker(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'input.png'
            Image.new('RGB', (512, 512), 'white').save(source)
            env = dict(ROOM_IMAGE_LOGS=str(root / 'imagegen'), ROOM_CODEX='TEST_DOUBLE_NOT_CODEX',
                       LR_CODEX_MODEL='TEST_ONLY', CODEX_HOME=str(root / 'codex'), ROOM_MAX_IMAGE_CALLS='3')
            def fake_run(command, job, environ, logs, timeout):
                # Explicitly synthetic fixture, not a model call or a reconstruction.
                original = root / 'codex/generated_images/TEST_THREAD/output.png'
                original.parent.mkdir(parents=True, exist_ok=True)
                im = Image.new('RGB', (512, 512), 'black')
                im.paste('red', (100, 100, 400, 400)); im.save(original)
                output = job / 'outputs/reference_raw.png'; output.write_bytes(original.read_bytes())
                (job / 'outputs/generation_receipt.json').write_text(json.dumps(dict(original_image_path=str(original))))
                return 0, [dict(type='thread.started', thread_id='TEST_THREAD'), dict(type='turn.completed')], []
            with patch.dict(os.environ, env), patch('room_image_backend.run_codex', side_effect=fake_run) as mocked:
                output = root / 'reference.png'
                first = generate_reference(source, 'TEST_ONLY', output)
                second = generate_reference(source, 'TEST_ONLY', output)
                self.assertEqual(mocked.call_count, 1)
                self.assertEqual(first, second)
                self.assertFalse(first['provenance']['tool_call_strictly_proven'])
            with patch.dict(os.environ, env), patch('room_image_backend.run_codex', return_value=(1, [], [])) as mocked:
                with self.assertRaises(RuntimeError):
                    generate_reference(source, 'TEST_FAILURE', root / 'bad.png')
                with self.assertRaisesRegex(RuntimeError, 'earlier image call failed'):
                    generate_reference(source, 'TEST_FAILURE_NEXT', root / 'bad2.png')
                self.assertEqual(mocked.call_count, 1)

    def test_native_image_requires_same_thread_freshness_and_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); native = root / 'generated_images/current/image.png'
            native.parent.mkdir(parents=True); native.write_bytes(b'new-image')
            output = root / 'result.png'; output.write_bytes(b'new-image')
            events = [dict(type='thread.started', thread_id='current')]
            receipt = dict(original_image_path=str(native))
            good = native_provenance(events, receipt, output, time.time(), root)
            self.assertFalse(good['tool_call_strictly_proven'])
            with self.assertRaises(ValueError):
                native_provenance([dict(type='thread.started', thread_id='other')], receipt, output, time.time(), root)
            os.utime(native, (1, 1))
            with self.assertRaises(ValueError):
                native_provenance(events, receipt, output, time.time(), root)
            os.utime(native, None); output.write_bytes(b'fake')
            with self.assertRaises(ValueError):
                native_provenance(events, receipt, output, time.time(), root)

    def test_return_archive_excludes_inputs_secrets_links_and_clock(self):
        with tempfile.TemporaryDirectory() as temp:
            ret = Path(temp); folder = ret / 'artifacts/run'; folder.mkdir(parents=True)
            for name in ('input/frame.jpg', 'work/auth.json', 'result.json', 'scenes/Room.glb', 'scenes/Room.py', 'scenes/auth.json'):
                path = folder / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('{}')
            (folder / 'scenes/secret.txt').symlink_to(folder / 'work/auth.json')
            out = pack(folder, ret, 'test')
            self.assertEqual(out.read_bytes()[4:8], bytes(4))
            with tarfile.open(out) as tar:
                names = tar.getnames()
                self.assertIn('run/scenes/Room.glb', names)
                self.assertIn('run/scenes/Room.py', names)
                self.assertTrue(all(not m.issym() and m.mtime == 0 for m in tar.getmembers()))
                self.assertFalse(any('auth.json' in n or '/input/' in n or 'secret.txt' in n for n in names))
                manifest = json.load(tar.extractfile('run/manifest_sha256.json'))
                for name, value in manifest.items():
                    self.assertEqual(hashlib.sha256(tar.extractfile('run/' + name).read()).hexdigest(), value)

    def test_child_failure_and_timeout_are_not_success(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            with self.assertRaisesRegex(RuntimeError, 'exit=7'):
                command('bad', [sys.executable, '-c', 'print("useful error");raise SystemExit(7)'], folder, dict(os.environ), folder, 5)
            self.assertIn('useful error', (folder / 'logs/bad.log').read_text())
            with self.assertRaises(TimeoutError):
                command('timeout', [sys.executable, '-c', 'import time;time.sleep(30)'], folder, dict(os.environ), folder, .2)


if __name__ == '__main__':
    unittest.main()
