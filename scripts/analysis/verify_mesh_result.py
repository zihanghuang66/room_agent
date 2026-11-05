"""Independently compare dense PLY samples to source NPZ and inspect final mesh."""
import argparse
import importlib.util
import json
from pathlib import Path
import numpy as np
import open3d as o3d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--result', type=Path, required=True)
    args = ap.parse_args()
    spec = importlib.util.spec_from_file_location('mesh_worker', Path(__file__).parents[1]/'omega-mesh/mesh.py')
    worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    with np.load(args.source/'predictions.npz') as data:
        depth, conf, e, k, rgb = [data[n] for n in ('depth','confidence','extrinsics','intrinsics','rgb')]
    threshold = float(np.percentile(conf, 50))
    dtype = np.dtype([('x','<f4'),('y','<f4'),('z','<f4'),('red','u1'),('green','u1'),('blue','u1'),('confidence','<f4')])
    total, max_error = 0, 0.
    with (args.result/'room_dense.ply').open('rb') as f:
        header = []
        while True:
            line = f.readline()
            if not line:
                raise ValueError('Truncated PLY header')
            header.append(line)
            if line == b'end_header\n':
                break
        claimed = int(next(line.split()[2] for line in header if line.startswith(b'element vertex ')))
        for i in range(len(depth)):
            mask = conf[i] >= threshold
            n = int(mask.sum())
            records = np.frombuffer(f.read(n*19), dtype=dtype)
            assert len(records) == n
            xyz = np.column_stack([records[n] for n in ('x','y','z')])
            v, u = np.indices(depth[i].shape)
            rays = np.stack((u, v, np.ones_like(u)), axis=-1) @ np.linalg.inv(k[i]).T
            camera = rays * depth[i][..., None]
            world = (camera-e[i,:,3]) @ e[i,:,:3]
            error = float(np.max(np.abs(xyz-world[mask]))) if n else 0.
            max_error = max(max_error, error)
            assert error < 1e-5
            np.testing.assert_array_equal(np.column_stack([records[n] for n in ('red','green','blue')]),rgb[i][mask])
            np.testing.assert_array_equal(records['confidence'],conf[i][mask])
            total += n
        assert total == claimed and not f.read(1)
    statistics = dict(dense_points=total, maximum_xyz_error=max_error, rgb_and_confidence_exact=True)
    for name in ('room_mesh.ply', 'room_mesh_preview.glb'):
        mesh = o3d.io.read_triangle_mesh(str(args.result/name))
        xyz, faces, colors = np.asarray(mesh.vertices), np.asarray(mesh.triangles), np.asarray(mesh.vertex_colors)
        assert len(xyz)>0 and len(faces)>0 and np.isfinite(xyz).all() and np.isfinite(colors).all()
        assert faces.min()>=0 and faces.max()<len(xyz) and len(colors)==len(xyz)
        statistics[name] = dict(vertices=len(xyz), triangles=len(faces), bytes=(args.result/name).stat().st_size,
                               finite_vertices=True, valid_face_indices=True, vertex_colors=True)
    statistics['source_sha256'] = worker.sha256(args.source/'predictions.npz')
    assert statistics['source_sha256']==worker.SOURCE_SHA256
    (args.result/'verification.json').write_text(json.dumps(statistics,indent=2)+'\n')
    print(json.dumps(statistics,indent=2))


if __name__ == '__main__':
    main()
