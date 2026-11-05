"""Project RoomPlan furniture into RGB frames; select one object, no model calls."""
import argparse
import json
from pathlib import Path
import socket
import zipfile
import numpy as np
from PIL import Image,ImageDraw,ImageOps
from common import save,frame_files


def project_box(points,k,pose,width,height):
    local=(points-pose[:3,3]) @ pose[:3,:3]
    cv=local*np.array([1.,-1.,-1.])
    if np.min(cv[:,2])<=.10:return None
    uv=(cv @ k.T);uv=uv[:,:2]/uv[:,2,None]
    lo=uv.min(0);hi=uv.max(0)
    full=float(np.prod(hi-lo))
    clipped_lo=np.maximum(lo,0);clipped_hi=np.minimum(hi,[width,height])
    if full<=0 or np.any(clipped_hi-clipped_lo<40):return None
    fraction=float(np.prod(clipped_hi-clipped_lo)/(width*height))
    coverage=float(np.prod(clipped_hi-clipped_lo)/full)
    if fraction<.01 or fraction>.85 or coverage<.65:return None
    return clipped_lo,clipped_hi,fraction,coverage,float(cv[:,2].min()),float(cv[:,2].max())


def upright_angle(pose):
    up=(pose[:3,:3].T @ np.array([0.,1.,0.]))*np.array([1.,-1.,-1.])
    if np.linalg.norm(up[:2])<.1:return 0.
    angle=90.-np.degrees(np.arctan2(-up[1],up[0]))
    return float((angle+180)%360-180)


def prepare(scan,out,object_name=None):
    def deny(*a,**k):raise RuntimeError('Preparation is offline')
    socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
    from litereality_agent.pipeline.scene_init.ingest.preprocessing.vendor.litereality.roomplan import get_object_pose,get_wall_and_object_floor_files
    usdz=scan/'roomplan/room.usdz'
    if not usdz.exists():usdz=scan/'room.usdz'
    unpack=out.parent/'work/usdz';unpack.mkdir(parents=True,exist_ok=False)
    with zipfile.ZipFile(usdz) as z:
        if sum(m.file_size for m in z.infolist())>512*1024**2:raise ValueError('USDZ too large')
        for m in z.infolist():
            p=Path(m.filename)
            if p.is_absolute() or '..' in p.parts or '\\' in m.filename or (m.external_attr>>16)&0o170000==0o120000:
                raise ValueError('Unsafe USDZ path')
        z.extractall(unpack)
    groups=get_wall_and_object_floor_files(str(unpack/'room.usda'))
    objects=[]
    for file in groups['objects']:
        path=Path(file).resolve()
        if not path.is_relative_to(unpack.resolve()):raise ValueError('USD reference outside capture')
        if object_name and path.stem!=object_name:continue
        if any(s in path.stem.lower() for s in ('wall','floor','door','window','opening')):continue
        pose=get_object_pose(str(path))
        # USDA uses row-vector transforms; apply the complete local points/transform.
        points=np.c_[pose['points'],np.ones(len(pose['points']))] @ pose['transform']
        if not np.isfinite(points).all():continue
        objects.append((path.stem,points[:,:3]))
    if not objects:raise ValueError('No eligible furniture in RoomPlan'+(': '+object_name if object_name else ''))
    rows=[]
    for index,path in enumerate(frame_files(scan)):
        meta=json.loads(path.read_text());k=np.asarray(meta['intrinsics'],float).reshape(3,3)
        pose=np.asarray(meta['cameraPoseARFrame'],float).reshape(4,4)
        if not np.isfinite(k).all() or not np.isfinite(pose).all() or not np.allclose(pose[3],[0,0,0,1],atol=1e-5):
            raise ValueError('Invalid camera parameters: '+path.name)
        with Image.open(path.with_suffix('.jpg')) as im:width,height=im.size
        with Image.open(path.parent/('depth_'+path.stem.removeprefix('frame_')+'.png')) as im:depth=np.asarray(im).astype(float)/1000
        dh,dw=depth.shape
        for name,points in objects:
            projected=project_box(points,k,pose,width,height)
            if projected is None:continue
            lo,hi,fraction,coverage,zmin,zmax=projected
            low=np.floor(lo*np.array([dw/width,dh/height])).astype(int)
            high=np.minimum(np.ceil(hi*np.array([dw/width,dh/height])).astype(int),[dw,dh])
            region=depth[low[1]:high[1],low[0]:high[0]]
            match=float(np.mean((region>=zmin-.15)&(region<=zmax+.15)))
            if match<.15:continue
            score=fraction**.5*coverage*match
            rows.append(dict(object=name,frame=path.stem,index=index,score=score,
                depth_match_fraction=match,visible_box_fraction=coverage,
                bbox_xyxy=[*lo.tolist(),*hi.tolist()],rgb_size=[width,height],upright_rotation_deg=upright_angle(pose)))
    rows.sort(key=lambda r:(-r['score'],r['object'],r['index']))
    if not rows:raise ValueError('No furniture view passes visibility/depth checks; no model call made')
    name=rows[0]['object'];selected=[]
    for row in rows:
        if row['object']==name and all(abs(row['index']-r['index'])>=2 for r in selected):
            selected.append(row)
            if len(selected)==3:break
    out.mkdir(parents=True,exist_ok=True)
    tiles=[]
    for i,row in enumerate(selected):
        with Image.open(scan/(row['frame']+'.jpg')) as im:
            rgb=im.convert('RGB');w,h=rgb.size
            x0,y0,x1,y1=row['bbox_xyxy'];pad=.08*max(x1-x0,y1-y0)
            crop=rgb.crop((max(0,int(x0-pad)),max(0,int(y0-pad)),min(w,int(x1+pad)),min(h,int(y1+pad))))
            crop=crop.rotate(row['upright_rotation_deg'],expand=True,resample=Image.Resampling.BICUBIC)
            crop=ImageOps.contain(crop,(1000,1000));crop.save(out/f'view_{i+1}.png')
            if i==0:
                ImageDraw.Draw(rgb).rectangle((x0,y0,x1,y1),outline='#ff3030',width=8)
                rgb=ImageOps.contain(rgb.rotate(row['upright_rotation_deg'],expand=True),(1200,1200))
                rgb.save(out/'primary_context.jpg',quality=88)
        tile=Image.new('RGB',(520,560),'#202020')
        thumb=ImageOps.contain(crop,(500,510));tile.paste(thumb,((520-thumb.width)//2,15+(510-thumb.height)//2))
        ImageDraw.Draw(tile).text((12,535),f'{name} | view {i+1} | {row["frame"]}',fill='white');tiles.append(tile)
    sheet=Image.new('RGB',(520*len(tiles),560),'#202020')
    for i,tile in enumerate(tiles):sheet.paste(tile,(520*i,0))
    sheet.save(out/'evidence.jpg',quality=93)
    value=dict(object=name,selected_views=selected,candidate_count=len(rows),
        selection='RoomPlan projection and depth agreement; not semantic segmentation',
        caution='Crops can include occluders or other furniture. Only the selected object is the target.')
    save(out/'selection.json',value);save(out/'candidates.json',rows)
    print('SELECTED_OBJECT '+name,flush=True)
    return value

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--scan',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--object-name')
    args=ap.parse_args();prepare(args.scan,args.output,args.object_name)
