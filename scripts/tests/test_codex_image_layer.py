"""Verify that the installer distinguishes image files from separately mounted data."""
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'codex-image-seal'))
from check import image_layer


class ImageLayerTests(unittest.TestCase):
    def check_mounts(self, target, extra):
        with tempfile.TemporaryDirectory() as d:
            m = Path(d) / 'mountinfo'
            m.write_text('1 0 0:1 / / rw - overlay overlay rw\n' + extra)
            image_layer([target], m)

    def test_root_layer_allowed(self):
        self.check_mounts('/opt/room/tools/codex-complete-0.164.0', '')

    def test_separate_opt_mount_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'separate mount'):
            self.check_mounts('/opt/room/tools/codex-complete-0.164.0',
                              '2 1 0:2 / /opt/room rw - ext4 disk rw\n')

    def test_unrelated_mount_does_not_reject(self):
        self.check_mounts('/opt/room/tools/codex-complete-0.164.0',
                          '2 1 0:2 / /yinxin rw - ext4 disk rw\n')


if __name__ == '__main__': unittest.main()
