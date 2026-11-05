import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1] / 'vggt-omega'


class SamplingTests(unittest.TestCase):
    def test_prepare_and_preflight_accept_manifest_with_100_frames(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            package = root / 'scripts/vggt-omega'
            shutil.copytree(PACKAGE, package)
            frames = root / 'frames'
            frames.mkdir()
            selected = []
            for i in range(100):
                path = frames / ('frame_%06d.jpg' % (21 + 2 * i))
                data = str(i).encode()
                path.write_bytes(data)
                selected.append(dict(file=path.name, sha256=hashlib.sha256(data).hexdigest(), resampled_time_seconds=5 + i / 2))
            manifest = dict(expected_frame_count=100, experiment_id='2fps_test', server_source_relative='frames', selected_frames=selected)
            (package / 'input_manifest.json').write_text(json.dumps(manifest))
            proc = subprocess.run([sys.executable, '-B', str(package / 'run.py'), '--project-root', str(root), '--prepare-only'], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            archive = next((root / 'return_output').glob('room_omega_*.tar.gz'))
            with tarfile.open(archive) as tar:
                config = json.load(tar.extractfile(next(x for x in tar if x.name.endswith('/config.json'))))
            self.assertEqual(config['input_frame_count'], 100)
            self.assertEqual(len(config['frames']), 100)
            proc = subprocess.run([sys.executable, '-B', str(package / 'preflight.py'), '--project-root', str(root)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)  # no installed GPU environment in this fixture
            archive = next((root / 'return_output').glob('room_omega_preflight_*.tar.gz'))
            with tarfile.open(archive) as tar:
                result = json.load(tar.extractfile(next(x for x in tar if x.name.endswith('/result.json'))))
                config = json.load(tar.extractfile(next(x for x in tar if x.name.endswith('/input_config.json'))))
            self.assertEqual(result['checks']['inputs']['status'], 'passed')
            self.assertEqual(config['input_frame_count'], 100)

    def test_manifest_count_mismatch_is_rejected(self):
        spec = importlib.util.spec_from_file_location('omega_common', PACKAGE / 'common.py')
        common = importlib.util.module_from_spec(spec); spec.loader.exec_module(common)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / 'a.jpg').write_bytes(b'a')
            manifest = dict(expected_frame_count=100, selected_frames=[dict(file='a.jpg', sha256=hashlib.sha256(b'a').hexdigest())])
            with self.assertRaisesRegex(ValueError, 'count'):
                common.verify_frames(root, manifest)


if __name__ == '__main__':
    unittest.main()
