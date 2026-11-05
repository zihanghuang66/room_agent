"""Reuse installed CLI without fetching, installing or replacing its package."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE=Path(os.environ.get('ROOM_TEST_PACKAGE', str(Path(__file__).resolve().parents[1]/'litereality-full-v0.2.1')))/'runtime.py'
spec=importlib.util.spec_from_file_location('existing_runtime',SOURCE)
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)

class ExistingRuntimeTests(unittest.TestCase):
    def test_installed_cli_reused_without_package_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cli=root/'codex';cli.write_text('#!/bin/sh\necho "codex-cli fixture-only"\n');cli.chmod(0o755)
            env=dict(os.environ,ROOM_CODEX=str(cli));report=root/'runtime.json'
            with patch.object(runtime.platform,'system',return_value='Linux'), patch.object(runtime.platform,'machine',return_value='x86_64'):
                found=runtime.prepare(env,report)
            self.assertEqual(str(found),str(cli))
            data=json.loads(report.read_text())
            self.assertFalse(data['installed_or_downloaded'])
            self.assertFalse(data['actual_agent_execution_tested'])
            self.assertEqual(env['ROOM_CODEX'],str(cli))
            self.assertEqual(sorted(p.name for p in root.iterdir()),['codex','runtime.json'])

    def test_missing_explicit_cli_does_not_silently_install_or_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);report=root/'runtime.json'
            with patch.object(runtime.platform,'system',return_value='Linux'), patch.object(runtime.platform,'machine',return_value='x86_64'):
                with self.assertRaisesRegex(RuntimeError,'not found'):
                    runtime.prepare(dict(os.environ,ROOM_CODEX=str(root/'missing')),report)
            self.assertEqual(json.loads(report.read_text())['status'],'failed')

    def test_nonzero_cli_version_is_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cli=root/'codex';cli.write_text('#!/bin/sh\nexit 9\n');cli.chmod(0o755)
            with patch.object(runtime.platform,'system',return_value='Linux'), patch.object(runtime.platform,'machine',return_value='x86_64'):
                with self.assertRaisesRegex(RuntimeError,'version check failed'):
                    runtime.prepare(dict(os.environ,ROOM_CODEX=str(cli)),root/'report.json')

if __name__=='__main__':unittest.main()
