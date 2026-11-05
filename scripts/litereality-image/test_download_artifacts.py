"""Regression checks for the observed 403 and file integrity across retries."""
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import download_artifacts as module


class Downloads(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / 'blender.tar.xz'
        self.good = b'known artifact bytes'
        self.item = {'url': 'https://first.example/artifact', 'fallback_urls': ['https://second.example/artifact'],
                     'sha256': hashlib.sha256(self.good).hexdigest()}

    def runner(self, responses):
        def call(args, **kwargs):
            code, status, body = responses.pop(0)
            Path(args[args.index('--output') + 1]).write_bytes(body)
            self.assertNotIn('--insecure', args)
            self.assertNotIn('-k', args)
            self.assertEqual(args[args.index('--proto-redir') + 1], '=https')
            return subprocess.CompletedProcess(args, code, status, 'HTTP download failed' if code else '')
        return call

    def test_403_then_valid_mirror(self):
        with patch.object(module.subprocess, 'run', side_effect=self.runner([(22, '403', b'blocked html'), (0, '200', self.good)])) as run:
            module.download(self.item, self.target)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(self.target.read_bytes(), self.good)
        self.assertFalse(self.target.with_name(self.target.name + '.part').exists())

    def test_corrupt_200_stops_before_extracting_or_another_source(self):
        with patch.object(module.subprocess, 'run', side_effect=self.runner([(0, '200', b'corrupt')])) as run:
            with self.assertRaisesRegex(RuntimeError, 'Checksum mismatch'):
                module.download(self.item, self.target)
        self.assertEqual(run.call_count, 1)
        self.assertFalse(self.target.exists())

    def test_all_sources_fail_with_statuses(self):
        with patch.object(module.subprocess, 'run', side_effect=self.runner([(22, '403', b'bad'), (22, '404', b'bad')])):
            with self.assertRaisesRegex(RuntimeError, 'All configured artifact sources failed') as ctx:
                module.download(self.item, self.target)
        self.assertIn('403', str(ctx.exception))
        self.assertIn('404', str(ctx.exception))
        self.assertFalse(self.target.exists())

    def test_existing_valid_file_needs_no_network(self):
        self.target.write_bytes(self.good)
        with patch.object(module.subprocess, 'run') as run:
            module.download(self.item, self.target)
        run.assert_not_called()

    def test_http_or_credentials_rejected(self):
        for url in ('http://host.example/file', 'https://user:password@host.example/file'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                module.download(dict(self.item, url=url), self.target)

    def test_timeout_then_good_source(self):
        real_fake = self.runner([(0, '200', self.good)])
        count = 0
        def call(args, **kw):
            nonlocal count
            count += 1
            if count == 1:
                Path(args[args.index('--output') + 1]).write_bytes(b'partial')
                raise subprocess.TimeoutExpired(args, 1900)
            return real_fake(args, **kw)
        with patch.object(module.subprocess, 'run', side_effect=call):
            module.download(self.item, self.target)
        self.assertEqual(self.target.read_bytes(), self.good)


if __name__ == '__main__':
    unittest.main()
