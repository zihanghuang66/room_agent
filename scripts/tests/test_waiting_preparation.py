import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parents[1]


def module(path):
    spec = importlib.util.spec_from_file_location('preparation_test_module', path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class PreparationTests(unittest.TestCase):
    def test_preflight_recognizes_explicit_checkpoint_without_loading_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = root / 'model with spaces' / 'vggt_omega_1b_512.pt'
            checkpoint.parent.mkdir()
            checkpoint.write_bytes(b'not a real model; presence check only')
            p = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'vggt-omega/preflight.py'),
                                '--project-root', tmp, '--checkpoint', str(checkpoint)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
            archive = next((root / 'return_output').glob('room_omega_preflight_*.tar.gz'))
            with tarfile.open(archive) as tar:
                result = json.load(tar.extractfile(next(m for m in tar if m.name.endswith('/result.json'))))
            self.assertEqual(result['checks']['checkpoint']['status'], 'present_not_loaded')
            self.assertEqual(result['checks']['checkpoint']['path'], str(checkpoint.resolve()))
            self.assertFalse(result['inference_executed'])

    def test_cpu_only_environment_cannot_pass_gpu_preflight(self):
        package = SCRIPTS / 'vggt-omega'
        with mock.patch.object(sys, 'path', [str(package), *sys.path]):
            m = module(package / 'preflight_worker.py')
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'environment_reference.json').write_text('{"packages": {}}')
            fake_modules = {'torch': SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)),
                            'torchvision': SimpleNamespace(__version__='test'),
                            'vggt_omega.models': SimpleNamespace(VGGTOmega=object)}
            with mock.patch.dict(sys.modules, fake_modules), mock.patch.object(m, 'PACKAGE', folder):
                with mock.patch('importlib.metadata.distributions', return_value=[]):
                    with self.assertRaisesRegex(RuntimeError, 'No usable CUDA'):
                        m.check_environment(folder)
            self.assertFalse(json.loads((folder / 'runtime.json').read_text())['cuda_available'])

    def test_missing_inputs_and_environment_return_both_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'vggt-omega/preflight.py'),
                                '--project-root', tmp], capture_output=True, text=True)
            self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
            files = list((Path(tmp) / 'return_output').glob('room_omega_preflight_*.tar.gz'))
            self.assertEqual(len(files), 1)
            with tarfile.open(files[0]) as tar:
                result = json.load(tar.extractfile(next(m for m in tar if m.name.endswith('/result.json'))))
            self.assertFalse(result['inference_executed'])
            self.assertEqual(result['status'], 'needs_attention')
            self.assertEqual(result['checks']['inputs']['status'], 'failed')
            self.assertEqual(result['checks']['environment']['status'], 'failed')
            self.assertEqual(result['checks']['checkpoint']['status'], 'waiting_for_authorized_weights')

    def test_input_quality_metrics_distinguish_flat_and_textured_images(self):
        from PIL import Image
        import numpy as np
        m = module(SCRIPTS / 'analysis/prepare_comparison.py')
        flat = Image.fromarray(np.full((128, 128, 3), 100, np.uint8))
        y, x = np.indices((128, 128))
        textured = Image.fromarray(np.repeat((((x + y) % 2) * 255).astype(np.uint8)[..., None], 3, axis=-1))
        a, b = m.frame_metrics(flat), m.frame_metrics(textured)
        self.assertEqual(a['laplacian_variance_gray_max640'], 0)
        self.assertGreater(b['laplacian_variance_gray_max640'], a['laplacian_variance_gray_max640'])
        self.assertEqual(a['near_black_fraction'], 0)
        self.assertEqual(b['near_black_fraction'], 0.5)
        self.assertEqual(b['near_white_fraction'], 0.5)


if __name__ == '__main__':
    unittest.main()
