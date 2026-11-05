"""Installer integrity tests; no network or model use."""
import hashlib
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'litereality-resume-v0.1.1'))
from runtime import install_archive


class InstallerRegression(unittest.TestCase):
    def payload(self, root, name='bin/cli', symlink=False):
        archive = root / 'payload.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            m = tarfile.TarInfo(name); m.mode = 0o755
            if symlink:
                m.type = tarfile.SYMTYPE; m.linkname = '/outside'; tar.addfile(m)
            else:
                m.size = 4; tar.addfile(m, io.BytesIO(b'test'))
        return archive, hashlib.sha256(archive.read_bytes()).hexdigest()

    def test_reuse_verifies_contents_and_rejects_changes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); p, sha = self.payload(root); dst = root / 'runtime'
            install_archive(p, dst, sha); install_archive(p, dst, sha)
            (dst / 'bin/cli').write_text('changed')
            with self.assertRaisesRegex(RuntimeError, 'payload changed'):
                install_archive(p, dst, sha)

    def test_bad_digest_does_not_install(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); p, _ = self.payload(root); dst = root / 'runtime'
            with self.assertRaisesRegex(RuntimeError, 'SHA256'):
                install_archive(p, dst, 'wrong')
            self.assertFalse(dst.exists())

    def test_traversal_and_symlink_members_rejected(self):
        for name, symlink in [('../escape', False), ('/absolute', False), ('link', True)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                root = Path(d); p, sha = self.payload(root, name, symlink)
                with self.assertRaisesRegex(RuntimeError, 'Unsafe archive'):
                    install_archive(p, root / 'runtime', sha)


if __name__ == '__main__':
    unittest.main()
