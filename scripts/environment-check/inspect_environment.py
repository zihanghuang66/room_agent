#!/usr/bin/env python3
"""Room environment inventory. Python 3.8+, standard library only."""
import argparse
import datetime
import gzip
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile


def probe(name, args, timeout=15):
    executable = shutil.which(args[0])
    if executable is None:
        return {"name": name, "status": "missing", "command": args}
    env = os.environ.copy()
    env.update({"LC_ALL": "C", "QT_QPA_PLATFORM": "offscreen",
                "PYTHONDONTWRITEBYTECODE": "1"})
    try:
        process = subprocess.Popen(
            [executable] + args[1:], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, errors="replace",
            env=env, start_new_session=True,
        )
        try:
            output, _ = process.communicate(timeout=timeout)
            status = "ok" if process.returncode == 0 else "error"
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            output, _ = process.communicate()
            status = "timeout"
        return {"name": name, "status": status, "command": args,
                "returncode": process.returncode, "output": output[:24000],
                "output_truncated": len(output) > 24000}
    except OSError as exc:
        return {"name": name, "status": "error", "command": args,
                "output": str(exc)}


def read_known_file(path):
    try:
        return Path(path).read_text(errors="replace")[:12000]
    except OSError as exc:
        return "unavailable: " + str(exc)


def normalize_tar_info(info):
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.pax_headers = {}
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default="/yinxin/projects/room")
    parser.add_argument("--skip-torch-probe", action="store_true",
                        help="Only inspect installed package versions; do not import PyTorch")
    args = parser.parse_args()
    root = Path(args.project_root).expanduser().resolve()
    if not root.is_dir():
        parser.error("Project directory does not exist: " + str(root))
    return_dir = root / "return_output"
    report_parent = return_dir / "environment_reports"
    try:
        return_dir.mkdir(exist_ok=True)
        report_parent.mkdir(exist_ok=True)
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report_dir = Path(tempfile.mkdtemp(prefix="room_env_" + timestamp + "_",
                                          dir=str(report_parent)))
    except OSError as exc:
        parser.error("Cannot write report directory: " + str(exc))

    data = {
        "schema_version": 1,
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "project_root": str(root),
        "system": {"os": platform.system(), "kernel": platform.release(),
                   "architecture": platform.machine(), "python": sys.version,
                   "python_executable": sys.executable,
                   "logical_cpus_visible": os.cpu_count()},
        "known_files": {}, "packages": {}, "probes": [],
        "unverified": [
            "Platform base image name/tag/digest; obtain from school UI.",
            "Persistence after container release; user reports 1TB persistent storage.",
            "Actual user storage quota (df reports filesystem space, not user quota).",
            "Internet access, download access, listening ports and graphical access.",
            "Permission to install system packages or build images; no sudo/Docker test run.",
            "Reconstruction performance, accuracy, and compatibility on other GPUs.",
        ],
    }
    for path in ["/etc/os-release", "/proc/meminfo",
                 "/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current",
                 "/sys/fs/cgroup/cpu.max", "/sys/fs/cgroup/cpuset.cpus.effective",
                 "/sys/fs/cgroup/memory/memory.limit_in_bytes",
                 "/sys/fs/cgroup/cpu/cpu.cfs_quota_us",
                 "/sys/fs/cgroup/cpu/cpu.cfs_period_us"]:
        data["known_files"][path] = read_known_file(path)
    package_names = ["pip", "numpy", "scipy", "torch", "torchvision", "opencv-python",
                     "opencv-python-headless", "pycolmap", "open3d", "trimesh",
                     "usd-core", "sam2"]
    for name in package_names:
        try:
            data["packages"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            data["packages"][name] = None
    usage = shutil.disk_usage(str(root))
    data["filesystem_bytes"] = dict(zip(["total", "used", "free"], usage))
    commands = [
        ("gpu_inventory", ["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.free", "--format=csv"]),
        ("cuda_compiler", ["nvcc", "--version"]),
        ("ffmpeg", ["ffmpeg", "-version"]),
        ("ffprobe", ["ffprobe", "-version"]),
        ("colmap", ["colmap", "-h"]),
        ("cmake", ["cmake", "--version"]),
        ("cxx_compiler", ["g++", "--version"]),
        ("git", ["git", "--version"]),
        ("project_mount", ["findmnt", "-T", str(root), "-o", "TARGET,FSTYPE"]),
        ("project_disk", ["df", "-h", str(root)]),
    ]
    for name, command in commands:
        print("Checking " + name + " ...", flush=True)
        data["probes"].append(probe(name, command))
    if args.skip_torch_probe or data["packages"]["torch"] is None:
        data["probes"].append({"name": "torch_cuda", "status": "skipped",
                               "output": "Disabled or torch not installed in this Python environment."})
    else:
        print("Checking torch_cuda (timeout 30s) ...", flush=True)
        code = (
            "import json, torch; "
            "print(json.dumps({'torch':torch.__version__, 'build_cuda':torch.version.cuda, "
            "'cuda_available':torch.cuda.is_available(), "
            "'device_count':torch.cuda.device_count()}))"
        )
        data["probes"].append(probe("torch_cuda", [sys.executable, "-B", "-c", code], 30))

    (report_dir / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    lines = ["# Room 环境检查报告", "", "生成时间（UTC）：" + data["generated_at_utc"],
             "", "项目路径：" + str(root), "",
             "本报告仅证明已执行的环境检查；不证明重建、安装权限或持久化已验证。",
             "`ok` 只表示命令退出码为 0，仍需检查输出；CUDA available=false 不算 GPU 验证通过。",
             "未安装工具记为 missing，命令失败记为 error，超时记为 timeout。", "",
             "## 基本环境", "", "```json", json.dumps(data["system"], ensure_ascii=False, indent=2), "```", "",
             "## 当前 Python 环境中的依赖版本", "", "```json",
             json.dumps(data["packages"], indent=2), "```", "",
             "## 命令检查", ""]
    for item in data["probes"]:
        lines.extend(["### " + item["name"] + " — " + item["status"], "", "```text",
                      item.get("output", "Command not found in PATH").replace("```", "'''"), "```", ""])
    lines.extend(["## 系统文件与资源限制", ""])
    for path, value in data["known_files"].items():
        lines.extend(["### " + path, "", "```text", value, "```", ""])
    lines.extend(["## 尚未验证", ""] + ["- " + item for item in data["unverified"]])
    lines.extend(["", "请另提供平台基础镜像名称，并确认该项目路径是否位于所申请的持久化存储。", ""])
    (report_dir / "report.md").write_text("\n".join(lines))
    archive = return_dir / (report_dir.name + ".tar.gz")
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as handle:
                handle.add(report_dir, arcname=report_dir.name, filter=normalize_tar_info)
    print("\nReport: " + str(report_dir / "report.md"))
    print("Return this archive: " + str(archive))
    return 0


if __name__ == "__main__":
    sys.exit(main())
