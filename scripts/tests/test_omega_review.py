import importlib.util
from pathlib import Path
import unittest
import numpy as np


class OmegaReviewTests(unittest.TestCase):
    def load(self):
        path = Path(__file__).resolve().parents[1] / 'analysis/review_omega.py'
        spec = importlib.util.spec_from_file_location('omega_review', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_identical_plane_and_camera_are_consistent(self):
        m = self.load()
        depth = np.ones((2, 8, 8), np.float32)
        e = np.tile(np.eye(4, dtype=np.float32)[:3], (2, 1, 1))
        k = np.tile(np.eye(3, dtype=np.float32), (2, 1, 1))
        score, _ = m.pair_consistency(depth, np.ones_like(depth), e, k, 0, 1, 0, stride=1)
        self.assertEqual(score['depth_agreement_5pct'], 1)
        self.assertEqual(score['median_relative_depth_error'], 0)

    def test_wrong_target_depth_is_detected(self):
        m = self.load()
        depth = np.ones((2, 8, 8), np.float32); depth[1] *= 2
        e = np.tile(np.eye(4, dtype=np.float32)[:3], (2, 1, 1))
        k = np.tile(np.eye(3, dtype=np.float32), (2, 1, 1))
        score, _ = m.pair_consistency(depth, np.ones_like(depth), e, k, 0, 1, 0, stride=1)
        self.assertEqual(score['median_relative_depth_error'], 0.5)
        self.assertEqual(score['depth_agreement_5pct'], 0)
        self.assertEqual(score['in_front_disagreement_5pct'], 1)

    def test_points_behind_target_camera_are_excluded(self):
        m = self.load()
        depth = np.ones((2, 8, 8), np.float32)
        e = np.tile(np.eye(4, dtype=np.float32)[:3], (2, 1, 1)); e[1, 2, 3] = -2
        k = np.tile(np.eye(3, dtype=np.float32), (2, 1, 1))
        score, _ = m.pair_consistency(depth, np.ones_like(depth), e, k, 0, 1, 0, stride=1)
        self.assertEqual(score['compared_pixels'], 0)
        self.assertIsNone(score['depth_agreement_5pct'])


if __name__ == '__main__':
    unittest.main()
