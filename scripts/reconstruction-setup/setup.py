#!/usr/bin/env python3
"""Install the Room reconstruction environment without changing system packages."""
import argparse
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tarfile
import tempfile


def run_step(name, command, report_dir, env, timeout=1200):
    print("Running: " + name, flush=True)
    log_path = report_dir / (name + ".log")
    result = {"name": name, "command": command, "status": "error"}
    with log_path.open("w") as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                 env=env, start_new_session=True)
        try:
            child.wait(timeout=timeout)
            result.update(status="ok" if child.returncode == 0 else "error",
                          returncode=child.returncode)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
            result.update(status="timeout", returncode=child.returncode)
    print("  " + result["status"] + ": " + str(log_path), flush=True)
    return result


def normalize_tar_info(info):
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.pax_headers = {}
    return info


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default="/yinxin/projects/room")
    parser.add_argument("--plan", action="store_true", help="Print plan; no writes or downloads")
    args = parser.parse_args(argv)
    root = Path(args.project_root).expanduser().resolve()
    requirements = Path(__file__).resolve().with_name("requirements.txt")
    if args.plan:
        print("Project:", root)
        print("Create a fresh isolated venv; download binary wheels; install; check imports.")
        print(requirements.read_text())
        print("No changes to system Python, drivers, PyTorch, or GPU workloads.")
        return 0
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        parser.error("This package targets the reported Linux x86_64 server only.")
    if sys.version_info[:2] != (3, 10):
        parser.error("Use the reported /usr/bin/python3 (Python 3.10); other versions are not verified.")
    if not root.is_dir():
        parser.error("Project root does not exist: " + str(root))
    return_dir = root / "return_output"
    return_dir.mkdir(exist_ok=True)
    report_parent = return_dir / "setup_reports"
    report_parent.mkdir(exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_dir = Path(tempfile.mkdtemp(prefix="room_setup_" + stamp + "_", dir=report_parent))
    env_parent = root / ".venvs"
    env_parent.mkdir(exist_ok=True)
    venv_path = env_parent / report_dir.name
    wheels = return_dir / "wheelhouse" / report_dir.name
    wheels.mkdir(parents=True)
    python = str(venv_path / "bin/python")
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("PIP_") or key in ("PYTHONPATH", "PYTHONHOME"):
            env.pop(key)
    env.update(PIP_CONFIG_FILE=os.devnull, PYTHONNOUSERSITE="1",
               PYTHONDONTWRITEBYTECODE="1", CUDA_VISIBLE_DEVICES="",
               OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    data = {"schema_version": 1, "status": "failed", "venv": str(venv_path),
            "wheelhouse": str(wheels), "steps": [],
            "scope": "Install and import only; no GPU computation or reconstruction tested."}
    (report_dir / "requirements.txt").write_bytes(requirements.read_bytes())
    pip = [sys.executable, "-m", "pip", "--isolated", "--disable-pip-version-check",
           "--python", python]
    verify_code = (
        "import importlib.metadata as m,json,sys,pycolmap,numpy; "
        "assert sys.prefix != sys.base_prefix; "
        "required=['extract_features','match_sequential','incremental_mapping']; "
        "assert all(callable(getattr(pycolmap,n,None)) for n in required); "
        "print(json.dumps({'python':sys.executable,'pycolmap':m.version('pycolmap-cuda12'),"
        "'numpy':numpy.__version__,'has_cuda':str(getattr(pycolmap,'has_cuda','unknown')),"
        "'api_available':required,'gpu_workload_tested':False},indent=2))"
    )
    steps = [
        ("create_venv", [sys.executable, "-m", "venv", "--without-pip", str(venv_path)], 60),
        ("download_wheels", pip + ["download", "--index-url", "https://pypi.org/simple",
          "--no-cache-dir", "--only-binary=:all:", "--retries", "1", "--timeout", "30",
          "--dest", str(wheels), "-r", str(requirements)], 1200),
        ("install", pip + ["install", "--no-index", "--find-links", str(wheels),
          "--only-binary=:all:", "--report", str(report_dir / "pip-install-report.json"),
          "-r", str(requirements)], 600),
        ("pip_check", pip + ["check"], 60),
        ("resolved_versions", pip + ["freeze", "--all"], 60),
        ("verify_imports", [python, "-I", "-B", "-c", verify_code], 60),
    ]
    try:
        for name, command, timeout in steps:
            result = run_step(name, command, report_dir, env, timeout)
            data["steps"].append(result)
            if result["status"] != "ok":
                break
        else:
            data["status"] = "installed_import_verified"
            (report_dir / "activate.txt").write_text("source " + str(venv_path / "bin/activate") + "\n")
    except Exception as exc:
        data["error"] = type(exc).__name__ + ": " + str(exc)
    finally:
        hashes = []
        for wheel in sorted(wheels.glob("*.whl")):
            digest = hashlib.sha256()
            with wheel.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            hashes.append(digest.hexdigest() + "  " + wheel.name)
        (report_dir / "wheels.sha256").write_text("\n".join(hashes) + "\n")
        (report_dir / "result.json").write_text(json.dumps(data, indent=2) + "\n")
        archive = return_dir / (report_dir.name + ".tar.gz")
        with archive.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as handle:
                    handle.add(report_dir, arcname=report_dir.name, filter=normalize_tar_info)
        print("\nStatus: " + data["status"])
        print("Return this archive: " + str(archive))
        print("Environment retained at: " + str(venv_path))
    return 0 if data["status"] == "installed_import_verified" else 1


if __name__ == "__main__":
    sys.exit(main())
