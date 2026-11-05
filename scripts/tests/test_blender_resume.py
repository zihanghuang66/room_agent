"""Regression for binary-vs-directory Blender config and infrastructure repair loops."""
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(os.environ['ROOM_TEST_APP']) / 'src'))
from litereality_agent.room_ops.rendering.room_render import render_vs_capture as rvc
from litereality_agent.agent import author
from litereality_agent.pipeline.realism_authoring import acceptance
from litereality_agent.pipeline.scene_init.reconstruct.flow import _blender_env


class BlenderResumeTests(unittest.TestCase):
    def test_binary_path_is_not_appended_twice(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / 'blender'; exe.write_text('#!/bin/sh\n'); exe.chmod(0o755)
            with patch.dict(os.environ, LITEREALITY_BLENDER=str(exe)):
                self.assertEqual(rvc._blender_bin(), str(exe))
                self.assertEqual(author._blender_bin(), str(exe))

    def test_install_directory_remains_supported(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / 'blender'; exe.write_text('#!/bin/sh\n'); exe.chmod(0o755)
            with patch.dict(os.environ, LITEREALITY_BLENDER=d):
                self.assertTrue(Path(rvc._blender_bin()).samefile(exe))
                self.assertTrue(Path(author._blender_bin()).samefile(exe))

    def test_path_contains_directory_not_binary(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / 'blender'; exe.write_text('test'); exe.chmod(0o755)
            self.assertEqual(_blender_env(str(exe))['PATH'].split(os.pathsep)[0], d)

    def test_check_execution_error_is_distinct_from_quality_failure(self):
        with tempfile.TemporaryDirectory() as d, patch.object(acceptance, 'fingerprint', side_effect=FileNotFoundError('test')):
            context = SimpleNamespace(authored_room=Path(d))
            result = acceptance.assess(context, Path(d), Path(d))
            self.assertEqual(result['status'], 'check_failed')
            self.assertFalse(result['accepted'])

    def test_no_model_repair_for_infrastructure_error(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); room = root / 'room'; room.mkdir(); (room / 'Room.py').write_text('# test')
            context = SimpleNamespace(authoring_root=root, authored_room=room, capture_dir=root)
            with patch.object(acceptance, 'run_module', return_value=(0, 'test.log')), \
                 patch.object(acceptance, 'assess', return_value=dict(accepted=False, status='check_failed', error='Blender unavailable')), \
                 patch.object(acceptance, 'repair', new_callable=AsyncMock) as repair:
                result = acceptance.finish(context, dict(repair_rounds=2))
                self.assertEqual(result['round'], 0)
                repair.assert_not_called()


if __name__ == '__main__': unittest.main()
