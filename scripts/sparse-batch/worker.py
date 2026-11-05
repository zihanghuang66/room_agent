#!/usr/bin/env python3
"""Isolate native COLMAP stages so a crash still leaves the parent able to report."""
import ctypes
import importlib.metadata
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def gpu_identity(index):
    cuda = ctypes.CDLL("libcuda.so.1")
    def checked(name, *args):
        code = getattr(cuda, name)(*args)
        if code:
            raise RuntimeError(name + " failed with CUDA code " + str(code))
    checked("cuInit", 0)
    count = ctypes.c_int()
    checked("cuDeviceGetCount", ctypes.byref(count))
    if not 0 <= index < count.value:
        raise ValueError("GPU index is outside the container's CUDA-visible devices")
    device = ctypes.c_int()
    checked("cuDeviceGet", ctypes.byref(device), index)
    name = ctypes.create_string_buffer(256)
    checked("cuDeviceGetName", name, len(name), device)
    uuid = (ctypes.c_ubyte * 16)()
    function = "cuDeviceGetUuid_v2" if hasattr(cuda, "cuDeviceGetUuid_v2") else "cuDeviceGetUuid"
    checked(function, ctypes.byref(uuid), device)
    raw = bytes(uuid).hex()
    return {"logical_index": index, "name": name.value.decode(errors="replace"),
            "uuid": "GPU-" + "-".join([raw[:8], raw[8:12], raw[12:16], raw[16:20], raw[20:]]),
            "cuda_visible_count": count.value,
            "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES", "not set")}


def options(p, config):
    extraction = p.FeatureExtractionOptions()
    extraction.num_threads = config["threads"]
    extraction.max_image_size = config["max_size"]
    extraction.gpu_index = str(config["gpu_index"])
    extraction.sift.max_num_features = 4096
    matching = p.FeatureMatchingOptions()
    matching.num_threads = config["threads"]
    matching.gpu_index = str(config["gpu_index"])
    pairing = p.SequentialPairingOptions()
    pairing.overlap = 10
    pairing.quadratic_overlap = False
    pairing.loop_detection = False
    mapping = p.IncrementalPipelineOptions()
    mapping.num_threads = config["threads"]
    mapping.ba_use_gpu = False
    mapping.max_num_models = 5
    mapping.min_model_size = 6
    return extraction, matching, pairing, mapping


def database_stats(database):
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        keypoints = [int(row[0]) for row in db.execute("SELECT rows FROM keypoints")]
        pairs = [int(row[0]) for row in db.execute("SELECT rows FROM two_view_geometries")]
        images = int(db.execute("SELECT COUNT(*) FROM images").fetchone()[0])
    return {"images_in_database": images, "images_with_keypoints": sum(n > 0 for n in keypoints),
            "total_keypoints": sum(keypoints), "keypoints_per_image": keypoints,
            "verified_pairs": sum(n > 0 for n in pairs), "inliers_per_pair": pairs}


def main():
    stage, filename = sys.argv[1:]
    config = json.loads(Path(filename).read_text())
    run = Path(config["run_directory"])
    report = run / "report"
    import numpy as np
    import pycolmap as p
    version = importlib.metadata.version("pycolmap-cuda12") if config["device"] == "cuda" else p.__version__
    if version != "4.2.1":
        raise RuntimeError("This batch targets PyCOLMAP 4.2.1; found " + version)
    extraction, matching, pairing, mapping = options(p, config)
    device = p.Device.cuda if config["device"] == "cuda" else p.Device.cpu
    database = run / "database.db"
    frames = run / "frames"
    if stage == "preflight":
        info = {"python": sys.executable, "pycolmap": version, "numpy": np.__version__,
                "has_cuda": bool(p.has_cuda), "requested_device": config["device"],
                "extraction_options": extraction.todict(), "matching_options": matching.todict(),
                "pairing_options": pairing.todict(), "mapping_options": mapping.todict()}
        # Native option dictionaries include enums/paths; store their readable form separately.
        for key in ["extraction_options", "matching_options", "pairing_options", "mapping_options"]:
            info[key] = json.loads(json.dumps(info[key], default=str))
        if config["device"] == "cuda":
            if not p.has_cuda:
                raise RuntimeError("Installed PyCOLMAP has no CUDA support")
            info["selected_gpu"] = gpu_identity(config["gpu_index"])
            try:
                inventory = subprocess.run(["nvidia-smi", "--query-gpu=uuid,name,driver_version,memory.total,memory.free",
                                            "--format=csv"], capture_output=True, text=True, timeout=20)
                info["gpu_snapshot"] = inventory.stdout
                info["nvidia_smi_returncode"] = inventory.returncode
            except (OSError, subprocess.TimeoutExpired) as exc:
                info["gpu_snapshot_error"] = str(exc)
        write_json(report / "runtime.json", info)
    elif stage == "features":
        reader = p.ImageReaderOptions()
        reader.camera_model = "SIMPLE_RADIAL"
        mode = p.CameraMode.SINGLE if config["camera_mode"] == "single" else p.CameraMode.PER_IMAGE
        p.extract_features(database, frames, camera_mode=mode, reader_options=reader,
                           extraction_options=extraction, device=device)
        stats = database_stats(database)
        write_json(report / "features.json", stats)
        if stats["total_keypoints"] == 0:
            raise RuntimeError("No features were extracted; inspect input frames and native logs")
    elif stage == "matching":
        p.match_sequential(database, matching_options=matching, pairing_options=pairing, device=device)
        stats = database_stats(database)
        write_json(report / "matches.json", stats)
        if stats["verified_pairs"] == 0:
            raise RuntimeError("No geometrically verified image pairs; mapping would have no usable input")
    elif stage == "mapping":
        sparse = run / "sparse"; sparse.mkdir()
        models = p.incremental_mapping(database, frames, sparse, options=mapping)
        count = len(list(frames.glob("frame_*.jpg")))
        summaries = []
        all_registered = set()
        for model_id, model in models.items():
            model_dir = sparse / str(model_id); model_dir.mkdir(exist_ok=True)
            model.write(model_dir)
            model.write_text(model_dir)
            model.export_PLY(model_dir / "sparse.ply")
            registered = list(model.reg_image_ids())
            all_registered.update(model.images[i].name for i in registered)
            errors = [float(point.error) for point in model.points3D.values()
                      if math.isfinite(float(point.error)) and float(point.error) >= 0]
            summaries.append({"model_id": int(model_id), "registered_images": len(registered),
                              "registration_fraction": len(registered) / count,
                              "points3D": model.num_points3D(),
                              "mean_reprojection_error_px": float(np.mean(errors)) if errors else None,
                              "median_reprojection_error_px": float(np.median(errors)) if errors else None,
                              "p95_reprojection_error_px": float(np.percentile(errors, 95)) if errors else None})
            poses = [{"image": model.images[i].name, "camera_id": int(model.images[i].camera_id),
                      "center": model.images[i].projection_center().tolist(),
                      "world_to_camera": model.images[i].cam_from_world().matrix().tolist()}
                     for i in sorted(registered, key=lambda i: model.images[i].name)]
            write_json(report / ("model_" + str(model_id) + "_cameras.json"),
                       {"scale": "arbitrary; not meters", "poses": poses,
                        "intrinsics": [{"id": int(cid), "model": camera.model_name,
                                        "width": camera.width, "height": camera.height,
                                        "params": camera.params.tolist()}
                                       for cid, camera in model.cameras.items()]})
        summary = {"frame_count": count, "model_count": len(models), "models": summaries,
                   "unregistered_images": sorted({p.name for p in frames.glob("*.jpg")} - all_registered),
                   "quality_accepted": False, "scale": "arbitrary",
                   "note": "Models are separately estimated and may overlap or be disconnected; do not assume a shared coordinate system."}
        write_json(report / "sparse_metrics.json", summary)
        if not summaries or not any(x["points3D"] > 0 for x in summaries):
            raise RuntimeError("No nonempty sparse model reconstructed")
        best = max(summaries, key=lambda x: (x["registered_images"], x["points3D"]))
        summary["largest_model_id"] = best["model_id"]
        best_registered = {models[best["model_id"]].images[i].name
                           for i in models[best["model_id"]].reg_image_ids()}
        summary["largest_model_unregistered_images"] = sorted({p.name for p in frames.glob("*.jpg")} - best_registered)
        write_json(report / "sparse_metrics.json", summary)
        model = models[best["model_id"]]
        ids = sorted(model.points3D)
        ids = ids[::max(1, math.ceil(len(ids) / 20000))]
        with (report / "largest_model_preview.ply").open("w") as out:
            out.write("ply\nformat ascii 1.0\ncomment arbitrary scale, sampled sparse points\nelement vertex " + str(len(ids))
                      + "\nproperty float x\nproperty float y\nproperty float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
            for i in ids:
                point = model.points3D[i]
                out.write(" ".join(str(float(x)) for x in point.xyz) + " "
                          + " ".join(str(int(x)) for x in point.color) + "\n")
    else:
        raise ValueError("Unknown stage: " + stage)


if __name__ == "__main__":
    main()
