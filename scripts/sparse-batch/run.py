#!/usr/bin/env python3
"""One bounded video-to-sparse run, with a return archive on success or failure."""
import argparse
import datetime
import gzip
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

DEFAULT_PYTHON = "/yinxin/projects/room/.venvs/room_setup_20261030T073737Z_pb2ho14u/bin/python"


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_video(source, run_dir, min_free_gb):
    if source.suffix.lower() != ".zip":
        return source, {"input": str(source), "kind": "video"}
    with zipfile.ZipFile(source) as archive:
        videos = []
        for member in archive.infolist():
            path = PurePosixPath(member.filename.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or (path.parts and ":" in path.parts[0]):
                raise ValueError("Unsafe path in ZIP: " + member.filename)
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError("ZIP symlinks are not supported")
            if member.is_dir() or "__MACOSX" in path.parts or path.name.startswith("._"):
                continue
            if path.suffix.lower() in (".mov", ".mp4", ".m4v", ".mkv", ".avi", ".webm"):
                videos.append(member)
        if len(videos) != 1:
            raise ValueError("ZIP must contain exactly one video; found " + str(len(videos)))
        member = videos[0]
        if member.flag_bits & 1:
            raise ValueError("Encrypted ZIP is not supported")
        if member.file_size <= 0 or member.file_size > 20 * 1024**3:
            raise ValueError("ZIP video must be nonempty and at most 20 GiB")
        if shutil.disk_usage(run_dir).free < member.file_size + min_free_gb * 1024**3:
            raise RuntimeError("Insufficient filesystem space to unpack video and keep reserve")
        folder = run_dir / "input"; folder.mkdir()
        video = folder / ("source" + PurePosixPath(member.filename).suffix.lower())
        with archive.open(member) as incoming, video.open("xb") as output:
            copied = 0
            for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                copied += len(chunk)
                if copied > member.file_size:
                    raise ValueError("ZIP member exceeds declared size")
                output.write(chunk)
        return video, {"input": str(source), "kind": "zip", "input_sha256": sha256(source),
                       "zip_member": member.filename, "extracted_bytes": copied,
                       "extracted_video": str(video)}


def run_step(name, command, log_dir, env, timeout, history):
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / (name + ".log")
    item = {"name": name, "command": command, "status": "running"}
    history.append(item)
    start = time.monotonic()
    print("Running " + name + " | log: " + str(log_path), flush=True)
    child = None
    try:
        with log_path.open("wb") as output:
            child = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                                     env=env, start_new_session=True)
            while True:
                remaining = timeout - (time.monotonic() - start)
                if remaining <= 0:
                    raise TimeoutError(name + " exceeded " + str(timeout) + " seconds")
                try:
                    code = child.wait(timeout=min(30, remaining))
                    break
                except subprocess.TimeoutExpired:
                    print(name + " still running; see " + str(log_path), flush=True)
        item["returncode"] = code
        if code:
            raise RuntimeError(name + " failed (exit " + str(code) + "); see " + str(log_path))
        item["status"] = "ok"
    except BaseException as exc:
        item["status"] = "timeout" if isinstance(exc, TimeoutError) else "failed"
        if child is not None and child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
        raise
    finally:
        item["seconds"] = round(time.monotonic() - start, 3)


def pack_report(report_dir, archive_path):
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path in sorted(report_dir.rglob("*")):
                    if not path.is_file() or path.is_symlink():
                        continue
                    info = tarfile.TarInfo(report_dir.parent.name + "/" + path.relative_to(report_dir).as_posix()
                                           if report_dir.name == "report" and report_dir.parent.name.startswith("room_sparse_")
                                           else report_dir.name + "/" + path.relative_to(report_dir).as_posix())
                    info.size = path.stat().st_size
                    info.mode = 0o644
                    info.mtime = 0
                    with path.open("rb") as content:
                        archive.addfile(info, content)


def prepare_video(args, video, run_dir, report, logs, env, history):
    # FFprobe 4.4 names the parent stream_side_data_list, but its child is
    # only named side_data. Selecting the parent preserves display rotation
    # without relying on the newer stream_side_data unique section name.
    entries = ("stream=index,codec_name,width,height,pix_fmt,avg_frame_rate,r_frame_rate,"
               "duration,sample_aspect_ratio,color_space,color_transfer,color_primaries:"
               "stream_tags=rotate:stream_side_data_list:format=duration,size,format_name")
    run_step("video_probe", ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", entries, "-of", "json", str(video)], logs, env, 60, history)
    metadata = json.loads((logs / "video_probe.log").read_text())
    if not metadata.get("streams"):
        raise ValueError("No video stream found")
    stream = metadata["streams"][0]
    durations = [stream.get("duration"), metadata.get("format", {}).get("duration")]
    duration = next((float(x) for x in durations if x not in (None, "N/A", "") and math.isfinite(float(x))), 0)
    if duration <= args.start:
        raise ValueError("Video duration is unavailable or start lies beyond its end")
    clip = min(args.seconds, duration - args.start)
    rate = min(args.fps, args.frames / clip)
    write_json(report / "video.json", {"probe": metadata, "input_sha256": sha256(video),
               "input_bytes": video.stat().st_size, "clip_start_seconds": args.start,
               "clip_duration_seconds": clip, "resampled_fps": rate,
               "note": "Frame times below are resampled output times, not original capture timestamps."})
    frames = run_dir / "frames"
    frames.mkdir()
    filters = ("fps=" + format(rate, ".12g") + ",scale=w='min(" + str(args.max_size)
               + ",iw)':h='min(" + str(args.max_size)
               + ",ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,showinfo")
    run_step("extract_frames", ["ffmpeg", "-hide_banner", "-nostdin", "-n", "-ss", str(args.start),
             "-threads", str(args.threads), "-i", str(video), "-t", str(clip), "-map", "0:v:0",
             "-an", "-sn", "-dn", "-vf", filters, "-filter_threads", str(args.threads),
             "-frames:v", str(args.frames), "-vsync", "vfr", "-q:v", "2", "-pix_fmt", "yuvj420p",
             str(frames / "frame_%06d.jpg")], logs, env, args.step_timeout, history)
    files = sorted(frames.glob("frame_*.jpg"))
    if len(files) < 6:
        raise ValueError("Fewer than 6 sampled frames; use a longer segment or higher fps")
    log = (logs / "extract_frames.log").read_text(errors="replace")
    pts = {int(n): float(t) for n, t in re.findall(r"\bn:\s*(\d+).*?\bpts_time:([\d.eE+-]+)", log)}
    manifest = [{"file": p.name, "sha256": sha256(p),
                 "resampled_time_seconds": args.start + pts[i] if i in pts else None}
                for i, p in enumerate(files)]
    unique = len({x["sha256"] for x in manifest})
    write_json(report / "frames.json", {"count": len(files), "unique_file_hashes": unique, "frames": manifest})
    stride = max(1, math.ceil(len(files) / 12))
    count = math.ceil(len(files) / stride)
    sheet_filter = "select='not(mod(n," + str(stride) + "))',scale=320:-2,tile=4x" + str(math.ceil(count / 4)) + ":nb_frames=" + str(count)
    run_step("contact_sheet", ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
             "-framerate", "1", "-i", str(frames / "frame_%06d.jpg"), "-vf", sheet_filter,
             "-frames:v", "1", "-update", "1", str(report / "contact_sheet.jpg")], logs, env, 120, history)
    warnings = ["Fixed intrinsics assume a single lens and no zoom; image stabilization can violate this.",
                "Uniform sampling is not an automatic blur/coverage quality filter."]
    if unique < len(files):
        warnings.append("Repeated JPEG hashes detected; motion or sampling may be insufficient.")
    if stream.get("color_transfer") in ("smpte2084", "arib-std-b67"):
        warnings.append("HDR source detected; current extraction does not apply a calibrated HDR tone map.")
    return {"frame_count": len(files), "clip_seconds": clip, "warnings": warnings}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, help="Video file or ZIP containing one video")
    parser.add_argument("--project-root", default="/yinxin/projects/room")
    parser.add_argument("--python", default=DEFAULT_PYTHON, help="Verified reconstruction environment interpreter")
    parser.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    parser.add_argument("--gpu-index", type=int, default=0, help="One logical CUDA device in the assigned container")
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--fps", type=float, default=2)
    parser.add_argument("--max-size", type=int, default=1600)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--step-timeout", type=int, default=1800)
    parser.add_argument("--min-free-gb", type=float, default=5)
    parser.add_argument("--camera-mode", choices=["single", "per-image"], default="single")
    parser.add_argument("--prepare-only", action="store_true", help="Probe and sample video without reconstruction")
    args = parser.parse_args(argv)
    if (not all(math.isfinite(x) for x in [args.start, args.seconds, args.fps, args.min_free_gb])
            or args.start < 0 or not 0 < args.seconds <= 600 or not 6 <= args.frames <= 300
            or not 0 < args.fps <= 10 or not 320 <= args.max_size <= 2400
            or not 1 <= args.threads <= 32 or not 1 <= args.step_timeout <= 7200
            or args.gpu_index < 0 or args.min_free_gb < 0):
        parser.error("Invalid limits; see --help (frames 6..300, seconds <=600, max-size 320..2400)")
    root = Path(args.project_root).expanduser().resolve()
    if not root.is_dir():
        parser.error("Project root does not exist: " + str(root))
    returns = root / "return_output"; returns.mkdir(exist_ok=True)
    artifacts = returns / "artifacts"; artifacts.mkdir(exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(tempfile.mkdtemp(prefix="room_sparse_" + stamp + "_", dir=artifacts))
    report = run_dir / "report"; report.mkdir()
    logs = run_dir / "logs"; logs.mkdir()
    data = {"status": "failed", "reconstruction_executed": False, "steps": [],
            "run_directory": str(run_dir), "started_utc": stamp,
            "scope": "Sparse reconstruction only; arbitrary scale; no mesh, object separation, USD or UE5."}
    env = os.environ.copy()
    env.pop("PYTHONPATH", None); env.pop("PYTHONHOME", None)
    env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen",
               OMP_NUM_THREADS=str(args.threads), OPENBLAS_NUM_THREADS=str(args.threads))
    if args.device == "cpu":
        env["CUDA_VISIBLE_DEVICES"] = ""
    worker = Path(__file__).resolve().with_name("worker.py")
    config = dict(vars(args), run_directory=str(run_dir))
    write_json(run_dir / "config.json", config)
    write_json(report / "config.json", config)
    try:
        video = Path(args.video).expanduser().resolve()
        if not video.is_file():
            raise FileNotFoundError("Video does not exist: " + str(video))
        if shutil.disk_usage(root).free < args.min_free_gb * 1024**3:
            raise RuntimeError("Insufficient filesystem free space for configured minimum")
        video, input_info = resolve_video(video, run_dir, args.min_free_gb)
        write_json(report / "input.json", input_info)
        if not args.prepare_only:
            if not Path(args.python).is_file():
                raise FileNotFoundError("Verified Python environment unavailable; supply --python for this container")
            run_step("preflight", [args.python, "-B", str(worker), "preflight", str(run_dir / "config.json")],
                     logs, env, 120, data["steps"])
        data.update(prepare_video(args, video, run_dir, report, logs, env, data["steps"]))
        if args.prepare_only:
            data["status"] = "prepared_only"
        else:
            data["reconstruction_executed"] = True
            for step in ["features", "matching", "mapping"]:
                run_step(step, [args.python, "-B", str(worker), step, str(run_dir / "config.json")],
                         logs, env, args.step_timeout, data["steps"])
            data["status"] = "sparse_completed_review_required"
            data["sparse_metrics"] = json.loads((report / "sparse_metrics.json").read_text())
    except (Exception, KeyboardInterrupt) as exc:
        data["error"] = type(exc).__name__ + ": " + str(exc)
        print(data["error"], flush=True)
    finally:
        write_json(report / "result.json", data)
        source_hashes = {p.name: sha256(p) for p in [Path(__file__).resolve(), worker] if p.is_file()}
        write_json(report / "script_hashes.json", source_hashes)
        log_copy = report / "logs"; log_copy.mkdir(exist_ok=True)
        for log in logs.glob("*.log"):
            with log.open("rb") as stream:
                size = log.stat().st_size
                stream.seek(max(0, size - 262144))
                tail = stream.read()
            (log_copy / log.name).write_bytes((b"[Only final 256KiB included; full log on server]\n" if size > 262144 else b"") + tail)
        (report / "README.md").write_text(
            "# Room batch result\n\nStatus: " + data["status"]
            + "\n\nRead result.json, video.json, frames.json, logs/, contact_sheet.jpg and sparse_metrics.json if present."
            + "\nOriginal video, all extracted images, database and full models stay on the server."
            + "\nSparse completion is not quality acceptance; inspect camera coverage and geometry before dense reconstruction.\n")
        archive = returns / (run_dir.name + ".tar.gz")
        pack_report(report, archive)
        archive.with_name(archive.name + ".sha256").write_text(sha256(archive) + "  " + archive.name + "\n")
        print("\nStatus: " + data["status"])
        print("Artifacts: " + str(run_dir))
        print("Return this archive: " + str(archive))
    return 0 if data["status"] in ("prepared_only", "sparse_completed_review_required") else 1


if __name__ == "__main__":
    sys.exit(main())
