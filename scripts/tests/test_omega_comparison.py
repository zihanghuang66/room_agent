import importlib.util
from pathlib import Path
import unittest
import numpy as np


def load():
    path = Path(__file__).resolve().parents[1] / 'analysis/compare_omega_runs.py'
    spec = importlib.util.spec_from_file_location('comparison', path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


class ComparisonTests(unittest.TestCase):
    def test_similarity_recovers_known_scale_rotation_translation(self):
        m = load()
        source = np.array([[0,0,0],[1,0,0],[0,2,0],[0,0,3],[1,1,1]], dtype=float)
        rotation = np.array([[0,-1,0],[1,0,0],[0,0,1]], dtype=float)
        target = 2.5 * source @ rotation.T + np.array([4,5,6])
        scale, r, t = m.fit_similarity(source, target)
        self.assertAlmostEqual(scale, 2.5)
        np.testing.assert_allclose(scale * source @ r.T + t, target, atol=1e-10)

    def test_comparison_uses_only_identical_valid_source_pixels(self):
        m = load()
        old = np.array([0.01, np.nan, 0.2])
        new = np.array([0.09, 0.01, np.nan])
        summary = m.shared_depth_stats(old, new)
        self.assertEqual(summary['count'], 1)
        self.assertEqual(summary['old_agreement_5pct'], 1)
        self.assertEqual(summary['new_agreement_5pct'], 0)


if __name__ == '__main__':unittest.main()
