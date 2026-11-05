"""Export all confident samples and fuse observed depths; no metric-scale claim."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import time

import numpy as np

SOURCE_SHA256 = 'f8cdc95b765683e8e8b6c742c1936e63dfc349572cd79158f7796463715a2f85'


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def world_points(depth, e, k):
    v, u = np.indices(depth.shape)
    camera = np.stack(((u-k[0, 2])*depth/k[0, 0],
                       (v-k[1, 2])*depth/k[1, 1], depth), axis=-1)
    return (camera-e[:, 3]) @ e[:, :3]


def support_mask(i, depth, conf, e, k, threshold, tolerance=.05):
    """Require one nearby view to agree; unsupported surfaces remain holes."""
    z = depth[i]
    valid = np.isfinite(z) & (z > 0) & (conf[i] >= threshold)
    world = world_points(z, e[i], k[i])
    supported = np.zeros(z.shape, bool)
    h, w = z.shape
    for j in range(max(0, i-2), min(len(depth), i+3)):
        if i == j:
            continue
        cam = world @ e[j, :, :3].T + e[j, :, 3]
        zz = cam[..., 2]
        with np.errstate(invalid='ignore', divide='ignore'):
            u = cam[..., 0]/zz*k[j, 0, 0]+k[j, 0, 2]
            v = cam[..., 1]/zz*k[j, 1, 1]+k[j, 1, 2]
        inside = valid & (zz > 0) & np.isfinite(u) & np.isfinite(v)
        inside &= (u >= 0) & (u <= w-1) & (v >= 0) & (v <= h-1)
        rows, cols = np.nonzero(inside)
        x, y = np.rint(u[inside]).astype(int), np.rint(v[inside]).astype(int)
        target = depth[j, y, x]
        agree = (target > 0) & np.isfinite(target) & (conf[j, y, x] >= threshold)
        agree &= np.abs(zz[inside]-target) <= tolerance*target
        supported[rows[agree], cols[agree]] = True
    # Avoid extending triangles across strong depth discontinuities.
    edge = np.zeros(z.shape, bool)
    for axis in (0, 1):
        a, b = [slice(None)]*2, [slice(None)]*2
        a[axis], b[axis] = slice(None, -1), slice(1, None)
        a, b = tuple(a), tuple(b)
        jump = np.abs(z[a]-z[b]) > tolerance*np.minimum(z[a], z[b])
        edge[a] |= jump
        edge[b] |= jump
    return valid & supported & ~edge


def export_dense(path, depth, conf, e, k, rgb, threshold):
    masks = np.isfinite(depth) & (depth > 0) & np.isfinite(conf) & (conf >= threshold)
    count = int(masks.sum())
    dtype = np.dtype([('x', '<f4'), ('y', '<f4'), ('z', '<f4'),
                      ('red', 'u1'), ('green', 'u1'), ('blue', 'u1'), ('confidence', '<f4')])
    header = ('ply\nformat binary_little_endian 1.0\ncomment scale is NOT calibrated in meters\n'
              f'element vertex {count}\nproperty float x\nproperty float y\nproperty float z\n'
              'property uchar red\nproperty uchar green\nproperty uchar blue\n'
              'property float confidence\nend_header\n')
    with path.open('wb') as f:
        f.write(header.encode())
        for i, mask in enumerate(masks):
            points = world_points(depth[i], e[i], k[i])[mask]
            if not np.isfinite(points).all():
                raise ValueError('Nonfinite world coordinates')
            data = np.empty(len(points), dtype=dtype)
            for axis, name in enumerate(('x', 'y', 'z')):
                data[name] = points[:, axis]
            for channel, name in enumerate(('red', 'green', 'blue')):
                data[name] = rgb[i][mask][:, channel]
            data['confidence'] = conf[i][mask]
            f.write(data.tobytes())
    return count


def write_glb(path, mesh):
    """Write actual binary glTF (Open3D 0.19's Assimp exporter failed round-trip)."""
    arrays = [np.asarray(mesh.vertices, dtype='<f4'), np.asarray(mesh.vertex_normals, dtype='<f4'),
              np.rint(np.clip(np.asarray(mesh.vertex_colors), 0, 1)*255).astype('u1'),
              np.asarray(mesh.triangles, dtype='<u4').ravel()]
    views, accessors, chunks, offset = [], [], [], 0
    for i, a in enumerate(arrays):
        raw = a.tobytes()
        views.append(dict(buffer=0, byteOffset=offset, byteLength=len(raw), target=34963 if i==3 else 34962))
        accessor = dict(bufferView=i, componentType=[5126, 5126, 5121, 5125][i],
                        count=len(a), type='SCALAR' if i==3 else 'VEC3')
        if i == 0:
            accessor.update(min=a.min(axis=0).tolist(), max=a.max(axis=0).tolist())
        if i == 2:
            accessor['normalized'] = True
        accessors.append(accessor)
        padded = raw+b'\0'*((-len(raw))%4)
        chunks.append(padded)
        offset += len(padded)
    document = dict(asset=dict(version='2.0', generator='Room mesh postprocess v0.1.0',
                              extras=dict(scale='uncalibrated model units; NOT measured meters')),
        scene=0, scenes=[dict(nodes=[0])], nodes=[dict(mesh=0)],
        meshes=[dict(primitives=[dict(attributes=dict(POSITION=0, NORMAL=1, COLOR_0=2), indices=3, material=0)])],
        materials=[dict(doubleSided=True, pbrMetallicRoughness=dict(metallicFactor=0, roughnessFactor=1))],
        buffers=[dict(byteLength=offset)], bufferViews=views, accessors=accessors)
    encoded = json.dumps(document, separators=(',', ':')).encode()
    encoded += b' '*((-len(encoded))%4)
    with path.open('wb') as f:
        f.write(struct.pack('<III', 0x46546c67, 2, 12+8+len(encoded)+8+offset))
        f.write(struct.pack('<II', len(encoded), 0x4e4f534a)); f.write(encoded)
        f.write(struct.pack('<II', offset, 0x004e4942))
        for chunk in chunks:
            f.write(chunk)


def preview(mesh, e, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    vertices, triangles = np.asarray(mesh.vertices), np.asarray(mesh.triangles)
    colors = np.asarray(mesh.vertex_colors)
    up = np.median(-e[:, 1, :3], axis=0)
    up /= np.linalg.norm(up)
    axis = np.eye(3)[np.argmin(abs(up))]
    right = np.cross(axis, up)
    right /= np.linalg.norm(right)
    forward = np.cross(up, right)
    p = vertices @ np.stack((right, forward, up), axis=1)
    # Display a cutaway, but preserve every face in exported files.
    height = np.quantile(p[:, 2], .70)
    eligible = np.flatnonzero((p[triangles, 2] <= height).all(axis=1))
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    fig = plt.figure(figsize=(14, 7))
    lo, hi = np.quantile(p, [.01, .99], axis=0)
    for panel, azim in enumerate((-65, 35), 1):
        ax = fig.add_subplot(1, 2, panel, projection='3d')
        poly = Poly3DCollection(p[triangles[eligible]], linewidths=0,
                                facecolors=colors[triangles[eligible]].mean(axis=1))
        ax.add_collection3d(poly)
        ax.set(xlim=(lo[0], hi[0]), ylim=(lo[1], hi[1]), zlim=(lo[2], hi[2]))
        ax.set_box_aspect(np.maximum(hi-lo, .001))
        ax.view_init(elev=50, azim=azim)
        ax.set_xlabel('relative X'); ax.set_ylabel('relative Y'); ax.set_zlabel('relative height')
    fig.suptitle('Initial TSDF mesh | display cutaway only | uncalibrated scale')
    fig.savefig(output / 'mesh_preview.png', dpi=160, bbox_inches='tight')
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--voxel-ratio', type=float, default=.005)
    args = ap.parse_args()
    if not .001 <= args.voxel_ratio <= .05:
        raise ValueError('voxel-ratio must be between .001 and .05')
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ['MPLCONFIGDIR'] = str(args.output / 'matplotlib_cache')
    import open3d as o3d
    started = time.monotonic()
    if sha256(args.source / 'predictions.npz') != SOURCE_SHA256:
        raise ValueError('This package requires the verified 100-frame predictions.npz')
    with np.load(args.source / 'predictions.npz', allow_pickle=False) as data:
        depth, conf, e, k, rgb = [data[n] for n in ('depth', 'confidence', 'extrinsics', 'intrinsics', 'rgb')]
    if depth.shape != (100, 512, 288) or conf.shape != depth.shape or rgb.shape != (*depth.shape, 3):
        raise ValueError('Unexpected array shapes')
    if not all(np.isfinite(a).all() for a in (depth, conf, e, k)) or not (depth > 0).all():
        raise ValueError('Invalid source geometry')
    threshold = float(np.percentile(conf, 50))
    print('Exporting every confident point; no random point cap.', flush=True)
    count = export_dense(args.output / 'room_dense.ply', depth, conf, e, k, rgb, threshold)
    # Internal normalization controls resolution, not physical units. Undo on output.
    reference_depth = float(np.median(depth[conf >= threshold]))
    voxel = args.voxel_ratio
    normalized_max = float(depth[conf >= threshold].max()/reference_depth + 1)
    volume = o3d.pipelines.integration.ScalableTSDFVolume(
        voxel_length=voxel, sdf_trunc=4*voxel,
        color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8,
        depth_sampling_stride=2)
    counts = []
    for i in range(len(depth)):
        mask = support_mask(i, depth, conf, e, k, threshold)
        counts.append(int(mask.sum()))
        filtered = np.where(mask, depth[i]/reference_depth, 0).astype(np.float32)
        rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(
            o3d.geometry.Image(np.ascontiguousarray(rgb[i])), o3d.geometry.Image(filtered),
            depth_scale=1., depth_trunc=normalized_max, convert_rgb_to_intensity=False)
        intrinsic = o3d.camera.PinholeCameraIntrinsic(
            depth.shape[2], depth.shape[1], k[i, 0, 0], k[i, 1, 1], k[i, 0, 2], k[i, 1, 2])
        extrinsic = np.eye(4)
        extrinsic[:3] = e[i]
        extrinsic[:3, 3] /= reference_depth
        volume.integrate(rgbd, intrinsic, extrinsic)
        if (i+1) % 10 == 0:
            print(f'Integrated {i+1}/100 frames; supported pixels={sum(counts)}', flush=True)
    mesh = volume.extract_triangle_mesh()
    del volume
    mesh.scale(reference_depth, center=(0, 0, 0))
    mesh.remove_degenerate_triangles().remove_duplicated_triangles().remove_duplicated_vertices()
    raw_vertices, raw_faces = len(mesh.vertices), len(mesh.triangles)
    if not raw_faces:
        raise ValueError('Empty TSDF mesh')
    labels, sizes, _ = mesh.cluster_connected_triangles()
    labels, sizes = np.asarray(labels), np.asarray(sizes)
    # Keep all substantial components, not just the largest room.
    mesh.remove_triangles_by_mask(sizes[labels] < 100)
    mesh.remove_unreferenced_vertices()
    mesh.compute_vertex_normals()
    if not len(mesh.triangles):
        raise ValueError('No component survives small-island filtering')
    if not o3d.io.write_triangle_mesh(str(args.output / 'room_mesh.ply'), mesh):
        raise RuntimeError('PLY write failed')
    lightweight = mesh.simplify_vertex_clustering(voxel_size=4*voxel*reference_depth)
    lightweight.remove_degenerate_triangles().remove_duplicated_triangles().remove_unreferenced_vertices()
    lightweight.compute_vertex_normals()
    write_glb(args.output / 'room_mesh_preview.glb', lightweight)
    for filename, expected in (('room_mesh.ply', mesh), ('room_mesh_preview.glb', lightweight)):
        check = o3d.io.read_triangle_mesh(str(args.output / filename))
        if len(check.triangles) != len(expected.triangles) or not np.isfinite(np.asarray(check.vertices)).all():
            raise ValueError('Mesh round-trip validation failed: '+filename)
        if not check.has_vertex_colors():
            raise ValueError('Missing mesh colors: '+filename)
    boundary_or_nonmanifold = len(mesh.get_non_manifold_edges(allow_boundary_edges=False))
    metrics = dict(source_sha256=SOURCE_SHA256, source_frames=len(depth),
        scale='uncalibrated; same coordinates as input point cloud',
        confidence_threshold=threshold, dense_points=count,
        reference_depth_model_units=reference_depth, voxel_size_model_units=voxel*reference_depth,
        sdf_trunc_model_units=4*voxel*reference_depth,
        support='at least one of +/-2 adjacent frames agrees within 5%; nearest-pixel projection',
        support_pixels_by_frame=counts, support_pixels_total=sum(counts),
        raw_mesh_vertices=raw_vertices, raw_mesh_triangles=raw_faces,
        removed_small_components=int((sizes < 100).sum()), minimum_component_triangles=100,
        mesh_vertices=len(mesh.vertices), mesh_triangles=len(mesh.triangles),
        remaining_components=int((sizes >= 100).sum()),
        preview_mesh_vertices=len(lightweight.vertices), preview_mesh_triangles=len(lightweight.triangles),
        preview_vertex_clustering_size_model_units=4*voxel*reference_depth,
        boundary_or_nonmanifold_edges=boundary_or_nonmanifold,
        watertight=False if boundary_or_nonmanifold else bool(mesh.is_watertight()),
        elapsed_seconds=time.monotonic()-started, numpy=np.__version__, open3d=o3d.__version__,
        limitations=['Not ground-truth geometry or metric scale.',
                     'No hole filling, furniture separation or texture atlas.',
                     'Support filtering may remove real but singly observed surfaces.',
                     'TSDF may retain ghosts where depth or poses disagree.'])
    (args.output / 'metrics.json').write_text(json.dumps(metrics, indent=2)+'\n')
    preview(lightweight, e, args.output)
    print(json.dumps({key:metrics[key] for key in ('dense_points', 'mesh_vertices', 'mesh_triangles', 'remaining_components', 'elapsed_seconds')}), flush=True)


if __name__ == '__main__':
    main()
