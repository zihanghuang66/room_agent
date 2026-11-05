"""Run with: python3 -B -m unittest discover -s scripts/tests -v"""
import gzip
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / "sparse-batch" / "run.py"


class SparseBatchTests(unittest.TestCase):
    def load_module(self):
        self.assertTrue(SCRIPT.is_file(), "Batch runner is not implemented yet")
        spec = importlib.util.spec_from_file_location("sparse_batch", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_missing_video_still_returns_failure_archive(self):
        self.load_module()
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([sys.executable, str(SCRIPT), "--project-root", temp,
                                     "--video", str(Path(temp) / "absent.mov")],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            archives = list((Path(temp) / "return_output").glob("*.tar.gz"))
            self.assertEqual(len(archives), 1)
            with tarfile.open(archives[0]) as t:
                member = next(m for m in t if m.name.endswith("/result.json"))
                report = json.load(t.extractfile(member))
            self.assertEqual(report["status"], "failed")
            self.assertIn("video", report["error"].lower())

    def test_subprocess_failure_and_timeout_are_not_success(self):
        m = self.load_module()
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(RuntimeError):
                m.run_step("failed", [sys.executable, "-c", "raise SystemExit(9)"],
                           Path(temp), os.environ.copy(), 10, [])
            with self.assertRaises(TimeoutError):
                m.run_step("timeout", [sys.executable, "-c", "import time;time.sleep(20)"],
                           Path(temp), os.environ.copy(), .1, [])

    def test_zip_accepts_single_video_and_ignores_mac_metadata(self):
        m = self.load_module()
        self.assertTrue(hasattr(m, "resolve_video"), "ZIP support not implemented")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); archive = root / "IMG.MOV.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("IMG.MOV", b"video bytes")
                z.writestr("__MACOSX/._IMG.MOV", b"metadata")
            output = root / "run"; output.mkdir()
            video, info = m.resolve_video(archive, output, 0)
            self.assertEqual(video.read_bytes(), b"video bytes")
            self.assertEqual(info["zip_member"], "IMG.MOV")
            self.assertTrue(archive.is_file())

    def test_zip_rejects_unsafe_paths_and_ambiguous_videos(self):
        m = self.load_module()
        self.assertTrue(hasattr(m, "resolve_video"), "ZIP support not implemented")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for i, names in enumerate([["../escape.MOV"], ["one.MOV", "two.mp4"]]):
                archive = root / (str(i) + ".zip")
                with zipfile.ZipFile(archive, "w") as z:
                    for name in names: z.writestr(name, b"video")
                output = root / ("run" + str(i)); output.mkdir()
                with self.assertRaises(ValueError):
                    m.resolve_video(archive, output, 0)
            self.assertFalse((root / "escape.MOV").exists())

    def test_return_archive_excludes_video_and_normalizes_timestamps(self):
        m = self.load_module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / "report"; report.mkdir()
            (root / "private-video.mov").write_bytes(b"not to be returned")
            (report / "说明.md").write_text("summary")
            os.utime(report / "说明.md", (4102444800, 4102444800))
            archive = root / "result.tar.gz"
            m.pack_report(report, archive)
            self.assertEqual(struct.unpack("<I", archive.read_bytes()[4:8])[0], 0)
            with tarfile.open(archive) as t:
                self.assertEqual(t.getnames(), ["report/说明.md"])
                self.assertTrue(all(x.mtime == 0 for x in t.getmembers()))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_legacy_probe_sections_preserve_rotation_and_allow_preparation(self):
        self.load_module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = root / "base.mp4"; video = root / "rotated.mov"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                            "-i", "testsrc2=size=320x240:rate=12", "-t", "4", "-c:v", "mpeg4",
                            str(base)], check=True)
            rotated = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error",
                                      "-display_rotation", "90", "-i", str(base),
                                      "-c", "copy", str(video)], capture_output=True, text=True)
            if rotated.returncode:
                subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(base),
                                "-c", "copy", "-metadata:s:v:0", "rotate=90", str(video)], check=True)
            fixture = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams",
                                                          "-of", "json", str(video)]))
            self.assertTrue(any(abs(float(x.get("rotation", 0))) == 90
                                for x in fixture["streams"][0].get("side_data_list", [])),
                            "The rotation fixture was not created correctly")
            # Model the exact 4.4.2 section-name failure, then delegate actual probing
            # to local FFprobe. This is a compatibility shim, not a 4.4.2 binary test.
            bin_dir = root / "bin"; bin_dir.mkdir()
            shim = bin_dir / "ffprobe"
            shim.write_text("#!" + sys.executable + "\nimport os,sys\n"
                            "if '-show_entries' in sys.argv:\n"
                            " entries=sys.argv[sys.argv.index('-show_entries')+1]\n"
                            " if 'stream_side_data' in [x.split('=')[0] for x in entries.split(':')]:\n"
                            "  print(\"No match for section 'stream_side_data'\",file=sys.stderr);sys.exit(1)\n"
                            "os.execv(" + repr(shutil.which("ffprobe")) + ", ['ffprobe']+sys.argv[1:])\n")
            shim.chmod(0o755)
            env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"])
            result = subprocess.run([sys.executable, str(SCRIPT), "--project-root", temp,
                                     "--video", str(video), "--prepare-only", "--frames", "6",
                                     "--seconds", "4", "--min-free-gb", "0"],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            run = next((root / "return_output" / "artifacts").glob("room_sparse_*"))
            metadata = json.loads((run / "report/video.json").read_text())["probe"]["streams"][0]
            rotations = [x.get("rotation") for x in metadata.get("side_data_list", [])]
            self.assertTrue(any(abs(float(x)) == 90 for x in rotations if x is not None), metadata)
            self.assertEqual(len(list((run / "frames").glob("*.jpg"))), 6)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_real_ffmpeg_preparation_caps_frames_and_preserves_input(self):
        self.load_module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); video = root / "video with spaces.mp4"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                            "-i", "testsrc2=size=320x240:rate=12", "-t", "4", "-c:v", "mpeg4",
                            str(video)], check=True)
            original = video.read_bytes()
            for _ in range(2):
                result = subprocess.run([sys.executable, str(SCRIPT), "--project-root", temp,
                                         "--video", str(video), "--prepare-only", "--frames", "6",
                                         "--seconds", "4", "--min-free-gb", "0"],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(video.read_bytes(), original)
            runs = sorted((root / "return_output" / "artifacts").glob("room_sparse_*"))
            self.assertEqual(len(runs), 2)
            self.assertFalse((root / "artifacts").exists())
            for run in runs:
                self.assertEqual(len(list((run / "frames").glob("*.jpg"))), 6)
                summary = json.loads((run / "report" / "result.json").read_text())
                self.assertEqual(summary["status"], "prepared_only")
                self.assertFalse(summary["reconstruction_executed"])
                self.assertTrue((run / "report" / "contact_sheet.jpg").is_file())
                self.assertTrue((run / "report" / "frames.json").is_file())
            self.assertEqual(len(list((root / "return_output").glob("*.tar.gz"))), 2)


if __name__ == "__main__":
    unittest.main()
