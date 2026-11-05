"""Read-only scan audit; no reconstruction or changes to source archive."""
import argparse
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import tarfile
import zipfile

import numpy as np
from PIL import Image, ImageDraw, ImageOps


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--archive', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    raw = args.output/'raw'; raw.mkdir()
    inventory = {}
    with tarfile.open(args.archive) as tar:
        members = tar.getmembers()
        if sum(m.size for m in members)>1024**3:
            raise ValueError('Unexpected size')
        names = set()
        for m in members:
            name = PurePosixPath(m.name)
            if name.is_absolute() or '..' in name.parts or m.name in names or not (m.isdir() or m.isfile()):
                raise ValueError('Unsafe or duplicate archive member: '+m.name)
            names.add(m.name)
            if m.isfile():
                content = tar.extractfile(m).read()
                target = raw.joinpath(*name.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                inventory[m.name] = dict(bytes=len(content), sha256=sha(content))
    meta = json.loads((raw/'metadata.json').read_text())
    frames = meta['frames']; count = len(frames)
    records, depths, confidences, poses = [], [], [], []
    k_error, pose_error, sample_time_error, inverse_error = [], [], [], []
    sizes = Counter()
    for i, record in enumerate(frames):
        base = f'{i:05d}'
        info = json.loads((raw/f'frame_{base}.json').read_text())
        assert info['frame_index'] == i
        assert inventory[f'frame_{base}.jpg']['sha256']==inventory[f'images/frame_{base}.jpg']['sha256']
        camera = record['camera']
        image = Image.open(raw/f'frame_{base}.jpg'); image.load()
        d = np.array(Image.open(raw/f'depth_{base}.png'))
        c = np.array(Image.open(raw/f'conf_{base}.png'))
        sizes[str((image.size, d.shape, str(d.dtype), c.shape, str(c.dtype)))] += 1
        assert image.size==(camera['capturedImageWidth'],camera['capturedImageHeight'])
        assert d.shape==c.shape==(camera['depthHeight'],camera['depthWidth'])
        assert set(np.unique(c)) <= {0,1,2}
        K=np.array(camera['cameraIntrinsics']).reshape(3,3,order='F')
        Kd=np.array(camera['depthCameraIntrinsics']).reshape(3,3,order='F')
        k_error.append(float(np.max(abs(K-np.array(info['intrinsics']).reshape(3,3)))))
        np.testing.assert_allclose(Kd[:2],K[:2]*d.shape[1]/image.width,atol=1e-4)
        T=np.array(camera['worldFromCameraTransform']).reshape(4,4,order='F')
        inv=np.array(camera['cameraFromWorldTransform']).reshape(4,4,order='F')
        inverse_error.append(float(np.max(abs(T@inv-np.eye(4)))))
        pose_error.append(float(np.max(abs(T-np.array(info['cameraPoseARFrame']).reshape(4,4)))))
        sample=meta['cameraSamples'][record['cameraSampleIndex']]
        sample_time_error.append(abs(sample['camera']['timestamp']-info['time']))
        assert abs(camera['timestamp']-info['time'])<1e-6
        records.append(dict(index=i,time=info['time'],tracking=camera['trackingState'],
            valid_depth_fraction=float(np.mean(d>0)),high_confidence_fraction=float(np.mean(c==2)),
            depth_raw_quantiles=np.quantile(d[d>0],[0,.01,.5,.99,1]).tolist(),
            confidence_counts=np.bincount(c.ravel(),minlength=3).tolist(),motion_quality=info['motionQuality']))
        depths.append(d); confidences.append(c); poses.append(T)
    depths, confidences, poses = np.array(depths),np.array(confidences),np.array(poses)
    times=np.array([r['time'] for r in records]); intervals=np.diff(times)
    assert np.all(intervals>0)
    positions=poses[:,:3,3]
    samples=meta['cameraSamples']
    sample_times=np.array([s['camera']['timestamp'] for s in samples])
    with zipfile.ZipFile(raw/'room.usdz') as z:
        assert z.testzip() is None
        usd_assets=z.namelist()
        usd_root=z.read('room.usda').decode()
        (args.output/'room_layout_root.usda').write_text(usd_root)
        objects=Counter()
        for n in usd_assets:
            stem=PurePosixPath(n).stem
            if n!='room.usda':
                category=''.join(ch for ch in stem if not ch.isdigit())
                objects[category]+=1
    assert inventory['room.usdz']['sha256']==inventory['roomplan/room.usdz']['sha256']
    import open3d as o3d
    pcd=o3d.io.read_point_cloud(str(raw/'pointcloud.pcd'))
    points=np.asarray(pcd.points)
    assert np.isfinite(points).all() and len(points)==meta['pointCount']
    # Compare sampled backprojections against the supplied cloud, not a new reconstruction.
    from scipy.spatial import cKDTree
    tree=cKDTree(points)
    comparison={}
    for scale in (.001, .01, 1.):
        distances=[]
        for i in (5,15,30,45,58):
            camera=frames[i]['camera']
            Kd=np.array(camera['depthCameraIntrinsics']).reshape(3,3,order='F')
            v,u=np.mgrid[0:depths.shape[1]:4,0:depths.shape[2]:4]
            z=depths[i,v,u]*scale; keep=(z>0)&(confidences[i,v,u]==2)
            # ARKit camera convention: right, up, looking along negative Z.
            xyz=np.stack(((u-Kd[0,2])*z/Kd[0,0],-(v-Kd[1,2])*z/Kd[1,1],-z),axis=-1)[keep]
            world=xyz@poses[i,:3,:3].T+poses[i,:3,3]
            dist,_=tree.query(world)
            distances.extend(dist.tolist())
        comparison[str(scale)]=dict(samples=len(distances),median_distance_m=float(np.median(distances)),
            p90_distance_m=float(np.quantile(distances,.9)))
    result=dict(archive=str(args.archive),archive_sha256=sha(args.archive.read_bytes()),
        archive_file_count=len(inventory),unique_rgb_frames=count,
        duplicate_rgb_copies_verified=count,room_usdz_copy_identical=True,
        sizes=dict(sizes),selected_frame_duration_seconds=float(times[-1]-times[0]),
        selected_frame_intervals_seconds=dict(min=float(intervals.min()),median=float(np.median(intervals)),max=float(intervals.max())),
        selected_tracking_states=dict(Counter(r['tracking'] for r in records)),
        all_camera_samples=len(samples),camera_sample_duration_seconds=float(sample_times[-1]-sample_times[0]),
        all_camera_tracking_states=dict(Counter(s['camera']['trackingState'] for s in samples)),
        valid_depth_fraction=float(np.mean(depths>0)),
        depth_raw_quantiles=np.quantile(depths[depths>0],[0,.01,.5,.99,1]).tolist(),
        confidence_counts=np.bincount(confidences.ravel(),minlength=3).tolist(),
        high_confidence_fraction=float(np.mean(confidences==2)),
        per_frame_high_confidence_min=float(min(r['high_confidence_fraction'] for r in records)),
        max_intrinsics_serialization_difference=max(k_error),max_pose_serialization_difference=max(pose_error),
        max_world_camera_inverse_error=max(inverse_error),max_camera_sample_timestamp_error=max(sample_time_error),
        estimated_selected_camera_path_m=float(np.linalg.norm(np.diff(positions,axis=0),axis=1).sum()),
        camera_end_displacement_m=float(np.linalg.norm(positions[-1]-positions[0])),
        pcd_points=len(points),pcd_has_colors=pcd.has_colors(),
        pcd_bounds_min=points.min(axis=0).tolist(),pcd_bounds_max=points.max(axis=0).tolist(),
        pcd_bbox_size=(points.max(axis=0)-points.min(axis=0)).tolist(),
        roomplan_assets=dict(objects),usdz_asset_count=len(usd_assets),
        usdz_declares_meters_per_unit_1='metersPerUnit = 1' in usd_root,
        depth_scale_vs_existing_pcd=comparison,
        caveats=['Pointcloud bounds are not measured room dimensions.',
                 'Depth scale 0.001 is inferred from numeric range and agreement with supplied pointcloud, not explicit PNG metadata.',
                 'Tracking normal and plausible metric units do not establish ground-truth accuracy.',
                 'CameraSamples contains poses, not 3035 saved RGB-D frames.'])
    (args.output/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
    (args.output/'frames.json').write_text(json.dumps(records,indent=2)+'\n')
    (args.output/'inventory_sha256.json').write_text(json.dumps(inventory,indent=2)+'\n')
    # Contact sheet rotates only the display for readability, never source images.
    sheet=Image.new('RGB',(4*300,3*430),'white');draw=ImageDraw.Draw(sheet)
    ids=np.linspace(0,count-1,12).round().astype(int)
    for panel,i in enumerate(ids):
        im=Image.open(raw/f'frame_{i:05d}.jpg').rotate(-90,expand=True)
        im.thumbnail((290,390))
        x,y=(panel%4)*300,(panel//4)*430
        sheet.paste(im,(x+(300-im.width)//2,y+25))
        draw.text((x+5,y+5),f'Frame {i:02d} | {times[i]-times[0]:.1f}s',fill='black')
    sheet.save(args.output/'rgb_contact_sheet.jpg',quality=90)
    os.environ['MPLCONFIGDIR']=str(args.output/'matplotlib_cache')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,3,figsize=(12,7))
    for col,i in enumerate((5,30,55)):
        axes[0,col].imshow(depths[i]/1000,vmin=0,vmax=5,cmap='viridis');axes[0,col].set_title(f'Depth {i} (assumed mm / 1000)')
        axes[1,col].imshow(confidences[i],vmin=0,vmax=2,cmap='gray');axes[1,col].set_title('Confidence 0 / 1 / 2')
        for row in range(2):axes[row,col].axis('off')
    fig.tight_layout();fig.savefig(args.output/'depth_confidence.png',dpi=130);plt.close(fig)
    fig,ax=plt.subplots(figsize=(10,7))
    ax.scatter(points[::3,0],points[::3,2],c=points[::3,1],s=.15,cmap='viridis',rasterized=True)
    ax.plot(positions[:,0],positions[:,2],'-r',lw=1,label='Saved camera path')
    ax.scatter(positions[0,0],positions[0,2],c='red',s=30,label='Start')
    ax.set_aspect('equal');ax.set_xlabel('ARKit X (m)');ax.set_ylabel('ARKit Z (m)')
    ax.set_title('Existing scanner point cloud; height-colored, not RGB');ax.legend()
    fig.tight_layout();fig.savefig(args.output/'existing_cloud_top.png',dpi=150);plt.close(fig)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
