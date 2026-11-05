#!/usr/bin/env python3
"""Build upload archives without inheriting machine clocks or file timestamps."""
import gzip
import hashlib
from pathlib import Path
import tarfile
import tempfile


PACKAGES = {
    "litereality-resume": ("capture_paths.py", "refresh_seed.py", "resume.py", "run_room_002.sh", "使用说明.md"),
    "litereality-full": (),  # Its builder assembles the isolated pinned application payload.
    "codex-imagegen-check": ("common.py", "prepare.py", "probe.py", "run.py", "run_room_002.sh", "使用说明.md"),
    "litereality-noapi": ("common.py", "extract_worker.py", "geometry.py", "blender_export.py", "verify_blender.py", "run.py", "run_room_002.sh", "使用说明.md"),
    "codex-integration-check": ("run.py", "suite.py", "helpers.py", "probe_mcp.py", "sandbox_probe.py", "graphics_probe.py", "graphics_repair.py", "blender_smoke.py", "run_room.sh", "run_runtime.sh", "使用说明.md", "容器要求.md"),
    "codex-write-check": ("run.py", "worker.py", "probe_mcp.py", "run_room.sh", "使用说明.md"),
    "blender-egl-repair": ("run.py", "probe.py", "smoke.py", "run_room.sh", "使用说明.md"),
    "litereality-codex": ("build_fork.py", "bridge.py", "classifier.py", "test_adapter.py", "probe_mcp.py", "live_probe.py", "activate_room.sh", "run_room.sh", "run.py", "使用说明.md"),
    "blender-runtime-setup": ("run.py", "smoke.py", "run_room.sh", "activate_room.sh", "使用说明.md"),
    "codex-check": ("check.py", "run_room.sh", "使用说明.md"),
    "litereality-agent-prepare": ("common.py", "probe.py", "run.py", "extract_worker.py", "run_room.sh", "run_room_002.sh", "linux-compatibility.txt", "source.json", "upstream-source.tar.gz", "使用说明.md"),
    "omega-mesh": ("mesh.py", "run.py", "run_room.sh", "requirements.txt", "使用说明.md"),
    "vggt-omega": ("common.py", "geometry.py", "setup.py", "run.py", "worker.py", "preflight.py", "preflight_worker.py", "run_room.sh", "environment_reference.json", "requirements.txt", "input_manifest.json", "使用说明.md"),
    "reconstruction-diagnostics": ("collect.py", "使用说明.md"),
    "environment-check": ("run_check.sh", "inspect_environment.py", "使用说明.md"),
    "reconstruction-setup": ("setup.py", "requirements.txt", "使用说明.md"),
    "sparse-batch": ("run.py", "worker.py", "organize_outputs.py", "使用说明.md"),
}

# Increment the version when changing an already distributed package.
PACKAGE_VERSIONS = {"codex-imagegen-check": "v0.1.0", "litereality-noapi": "v0.1.0", "codex-integration-check": "v0.1.4", "codex-write-check": "v0.1.0", "blender-egl-repair": "v0.1.0", "litereality-codex": "v0.1.2", "blender-runtime-setup": "v0.1.1", "codex-check": "v0.1.0", "litereality-agent-prepare": "v0.2.1", "omega-mesh": "v0.1.0", "vggt-omega": "v0.3.0", "reconstruction-diagnostics": "v0.1.0", "environment-check": "v0.1.2", "reconstruction-setup": "v0.1.2",
                    "sparse-batch": "v0.1.2"}


PACKAGE_VERSIONS['litereality-full'] = 'v0.1.0'
PACKAGE_VERSIONS['litereality-resume'] = 'v0.1.0'


def build_package(source, output, members):
    source, output = Path(source), Path(output)
    paths = [source / name for name in sorted(members)]
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise ValueError("Expected a regular source file: " + str(path))
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as raw:
            temp_path = Path(raw.name)
            # Both gzip and tar use the Unix epoch, independent of the sender clock.
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                    for path in paths:
                        info = tarfile.TarInfo(source.name + "/" + path.relative_to(source).as_posix())
                        info.size = path.stat().st_size
                        info.mtime = 0
                        info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        info.mode = 0o755 if path.suffix == ".sh" else 0o644
                        # Fresh TarInfo avoids inherited PAX mtime/atime/ctime fields.
                        with path.open("rb") as content:
                            archive.addfile(info, content)
        temp_path.replace(output)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_name(output.name + ".sha256").write_text(digest + "  " + output.name + "\n")
    return digest


def build_codex_suite_bundle(scripts,output):
    # One upload installs both the new suite and its exact adapter builder.
    folders=('codex-integration-check','litereality-codex')
    members=[folder+'/'+name for folder in folders for name in PACKAGES[folder]]
    return build_package(scripts,output,members)


def main():
    scripts = Path(__file__).resolve().parent
    for folder, members in PACKAGES.items():
        if folder == 'litereality-full':
            import importlib.util
            spec = importlib.util.spec_from_file_location('room_full_builder', scripts / folder / 'build.py')
            builder = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(builder)
            builder.build()
            continue
        version = PACKAGE_VERSIONS.get(folder)
        suffix = "-" + version if version else ""
        output = scripts / "packages" / ("room-" + folder + suffix + ".tar.gz")
        digest = build_codex_suite_bundle(scripts,output) if folder=='codex-integration-check' else build_package(scripts / folder, output, members)
        print(str(output) + "\nSHA256: " + digest)


if __name__ == "__main__":
    main()
