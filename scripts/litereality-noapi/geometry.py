"""Offline all-frame RGB-D fusion in the scanner's ARKit Y-up, metre coordinates."""
import argparse
import json
from pathlib import Path
import socket
import numpy as np
from PIL import Image, ImageDraw
from common import frame_files, save


def deny_network(*a, **k):
    raise RuntimeError('This worker is offline; network access is disabled')


def load_frame(path, min_confidence=1, max_depth=6.):
    meta = json.loads(path.read_text())
    k = np.asarray(meta['intrinsics'], dtype=float).reshape(3, 3)
    pose = np.asarray(meta['cameraPoseARFrame'], dtype=float).reshape(4, 4)
    if not (np.isfinite(k).all() and np.isfinite(pose).all()):
        raise ValueError('Nonfinite camera: ' + path.name)
    if not np.allclose(pose[3], [0, 0, 0, 1], atol=1e-5):
        raise ValueError('Unexpected camera matrix convention: ' + path.name)
    if not np.allclose(pose[:3, :3].T @ pose[:3, :3], np.eye(3), atol=2e-3):
        raise ValueError('Camera rotation is not orthonormal: ' + path.name)
    if abs(np.linalg.det(pose[:3, :3])-1) > 2e-3:
        raise ValueError('Camera transform is not a proper rotation')
    if k[0, 0] <= 0 or k[1, 1] <= 0 or not np.allclose(k[2], [0, 0, 1]):
        raise ValueError('Invalid camera intrinsics')
    if abs(k[0, 1]) > 1e-5 or abs(k[1, 0]) > 1e-5:
        raise ValueError('Skewed intrinsics are unsupported')
    with Image.open(path.with_suffix('.jpg')) as im:
        width, height = im.size
        image = im.convert('RGB')
    suffix = path.stem.removeprefix('frame_')
    with Image.open(path.parent / ('depth_'+suffix+'.png')) as im:
        raw = np.asarray(im)
    if raw.ndim != 2 or raw.dtype.kind not in 'ui' or raw.dtype.itemsize < 2 or raw.min() < 0 or raw.max() > 65535:
        raise ValueError('Expected a millimetre integer depth PNG')
    dh, dw = raw.shape
    kd = k.copy(); kd[0] *= dw/width; kd[1] *= dh/height
    depth = raw.astype(np.float32)/1000.
    valid = (depth > .15) & (depth < max_depth)
    conf_path = path.parent / ('conf_'+suffix+'.png')
    if conf_path.exists():
        with Image.open(conf_path) as im:
            conf = np.asarray(im)
        if conf.shape != raw.shape or not np.isin(conf, [0, 1, 2]).all():
            raise ValueError('Unexpected confidence map: '+conf_path.name)
        valid &= conf >= min_confidence
    depth[~valid] = 0
    rgb = np.asarray(image.resize((dw, dh), Image.Resampling.BILINEAR)).copy()
    # Open3D camera: +x right, +y down, +z forward; ARKit: +y up, -z forward.
    camera_to_world = pose @ np.diag([1., -1., -1., 1.])
    return rgb, depth, kd, camera_to_world, dict(frame=path.stem,
        valid_fraction=float(valid.mean()), confidence_present=conf_path.exists(),
        camera_position_m=pose[:3, 3].tolist())


def backproject(depth, k, camera_to_world):
    v, u = np.nonzero(depth > 0)
    z = depth[v, u]
    local = np.column_stack(((u-k[0, 2])*z/k[0, 0], (v-k[1, 2])*z/k[1, 1], z))
    return local @ camera_to_world[:3, :3].T + camera_to_world[:3, 3], v, u


def preview(points, colors, path):
    # Diagnostic orthographic views, not a photorealistic rendering.
    canvas = Image.new('RGB', (1800, 650), '#20252c')
    draw = ImageDraw.Draw(canvas)
    for idx, (axes, label) in enumerate([((0, 2), 'TOP: X / Z'), ((0, 1), 'FRONT: X / Y'), ((2, 1), 'SIDE: Z / Y')]):
        xy = points[:, axes]
        lo, hi = xy.min(axis=0), xy.max(axis=0)
        scale = 540 / max(float((hi-lo).max()), .01)
        pixel = np.rint((xy-(lo+hi)/2)*scale).astype(int)
        pixel[:, 0] += idx*600+300; pixel[:, 1] = 340-pixel[:, 1]
        keep = (pixel[:, 0]>=idx*600) & (pixel[:, 0]<(idx+1)*600) & (pixel[:, 1]>=50) & (pixel[:, 1]<640)
        # Draw all fused points; display dimensions naturally collapse overlapping pixels.
        array = np.asarray(canvas).copy()
        array[pixel[keep, 1], pixel[keep, 0]] = np.rint(colors[keep]*255).clip(0,255).astype(np.uint8)
        canvas = Image.fromarray(array); draw = ImageDraw.Draw(canvas)
        draw.text((idx*600+20, 18), label+' (metres)', fill='white')
    canvas.save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scan', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--voxel', type=float, default=.02)
    ap.add_argument('--min-confidence', type=int, default=1, choices=(0, 1, 2))
    args = ap.parse_args()
    if not .005 <= args.voxel <= .10: ap.error('voxel must be 0.005 to 0.10 metres')
    socket.socket.connect = deny_network; socket.socket.connect_ex = deny_network
    socket.create_connection = deny_network
    import open3d as o3d
    args.output.mkdir(parents=True, exist_ok=True)
    volume = o3d.pipelines.integration.ScalableTSDFVolume(voxel_length=args.voxel,
        sdf_trunc=args.voxel*4, color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8)
    cloud = o3d.geometry.PointCloud()
    rows = []; total = 0; used = 0
    paths = frame_files(args.scan)
    for i, path in enumerate(paths):
        rgb, depth, k, c2w, row = load_frame(path, args.min_confidence)
        xyz, v, u = backproject(depth, k, c2w)
        row['accepted_points'] = len(xyz); rows.append(row); total += len(xyz)
        if len(xyz):
            used += 1
            frame = o3d.geometry.PointCloud()
            frame.points = o3d.utility.Vector3dVector(xyz)
            frame.colors = o3d.utility.Vector3dVector(rgb[v, u].astype(float)/255.)
            cloud += frame
            # Bound memory by spatial resolution, never by truncating the time sequence.
            if (i+1) % 10 == 0:
                cloud = cloud.voxel_down_sample(args.voxel/2)
            rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(
                o3d.geometry.Image(rgb), o3d.geometry.Image(depth),
                depth_scale=1., depth_trunc=6., convert_rgb_to_intensity=False)
            h, w = depth.shape
            intrinsic = o3d.camera.PinholeCameraIntrinsic(w, h, k[0,0], k[1,1], k[0,2], k[1,2])
            volume.integrate(rgbd, intrinsic, np.linalg.inv(c2w))
        print(f'FUSION {i+1}/{len(paths)} accepted_points={len(xyz)}', flush=True)
    save(args.output/'fusion_frames.json', rows)
    if not len(cloud.points): raise ValueError('No accepted depth points; inspect confidence/depth input')
    cloud = cloud.voxel_down_sample(args.voxel/2)
    if not o3d.io.write_point_cloud(str(args.output/'observed_cloud.ply'), cloud):
        raise RuntimeError('Point cloud export failed')
    preview(np.asarray(cloud.points), np.asarray(cloud.colors), args.output/'cloud_views.png')
    mesh = volume.extract_triangle_mesh()
    mesh.remove_degenerate_triangles(); mesh.remove_duplicated_triangles(); mesh.remove_unreferenced_vertices()
    if not len(mesh.triangles): raise ValueError('TSDF produced no triangles; point cloud retained')
    mesh.compute_vertex_normals()
    if not o3d.io.write_triangle_mesh(str(args.output/'observed_mesh.ply'), mesh):
        raise RuntimeError('Mesh export failed')
    summary = dict(status='geometry_passed', frames_total=len(paths), frames_integrated=used,
        accepted_depth_samples=total, cloud_points=len(cloud.points), mesh_vertices=len(mesh.vertices),
        mesh_triangles=len(mesh.triangles), mesh_voxel_m=args.voxel, cloud_voxel_m=args.voxel/2,
        min_confidence=args.min_confidence, min_depth_m=.15, max_depth_m=6., frame_stride=1,
        hard_point_cap=None, coordinate_system='ARKit world, Y up, metres',
        bounds_m=[cloud.get_min_bound().tolist(), cloud.get_max_bound().tolist()],
        independently_scale_calibrated=False, image_api_calls=0, model_calls=0,
        limitations=['TSDF fuses measured depth; holes and drift may remain.',
                    'RGB is resized into the depth grid using scanner calibration convention.',
                    'RoomPlan proxy objects are separate from this observed, unsegmented mesh.'])
    save(args.output/'geometry_summary.json', summary)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__': main()
