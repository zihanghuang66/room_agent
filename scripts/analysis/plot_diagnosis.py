"""Render observed frame correspondences, graph and reconstructed sparse models."""
import os
from pathlib import Path
root=Path(__file__).resolve().parents[2]
out=root/'return_output/local_experiments'
os.environ['MPLCONFIGDIR']=str(out/'matplotlib_cache')
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pycolmap as p
from PIL import Image
source=root/'return_output/diagnostics_reports/room_diagnostics_20261030T082944Z_4exhuia1'
s=json.loads((source/'database_summary.json').read_text())
fig,axs=plt.subplots(2,1,figsize=(12,8),gridspec_kw={'height_ratios':[2,1]},layout='constrained')
mat=np.zeros((220,220))
for pair in s['pairs']:
 a,b=pair['a']-1,pair['b']-1;mat[a,b]=mat[b,a]=pair['inliers']
im=axs[0].imshow(np.log1p(mat),origin='lower',extent=[.5,220.5,.5,220.5],cmap='viridis',aspect='auto')
axs[0].axvline(145.5,color='red',ls='--');axs[0].axhline(145.5,color='red',ls='--');axs[0].set(title='Original verified matching graph (220 frames)',xlabel='Frame ID',ylabel='Frame ID');fig.colorbar(im,ax=axs[0],label='log(1 + inliers)')
a=s['adjacent_pairs'];axs[1].plot(np.arange(1,len(a)+1)/4,[x['inliers'] or 0 for x in a],lw=1);axs[1].axvline(36.25,color='red',ls='--');axs[1].set(title='Inliers between consecutive frames',xlabel='Boundary time (seconds, resampled)',ylabel='Inlier matches');fig.savefig(out/'matching_graph.png',dpi=150);plt.close(fig)
old=p.Reconstruction(source/'run/sparse/1')
new=p.Reconstruction(out/'focal700_allpairs_static/sparse/2')
fig=plt.figure(figsize=(14,6),layout='constrained')
ax=fig.add_subplot(131);frame=old.images[19];ax.imshow(Image.open(source/'run/frames'/frame.name));xy=np.array([x.xy for x in frame.points2D if x.has_point3D()]);ax.scatter(xy[:,0],xy[:,1],s=1,c='#00ffbb',alpha=.6);ax.set_title('Old model: observations at 4.5 s\nGreen = features supporting 3D points');ax.axis('off')
for n,model,title in [(2,old,'Server result: 20 / 220 frames\n6,570 points; entrance-door segment'),(3,new,'Final diagnostic variant: 101 / 200 frames\n6,728 / 6,935 points displayed (view crop only)')]:
 ax=fig.add_subplot(1,3,n,projection='3d');xyz=np.array([x.xyz for x in model.points3D.values()]);rgb=np.array([x.color for x in model.points3D.values()])/255
 if n==3:
  lo,hi=np.quantile(xyz,[.005,.995],axis=0);keep=np.all((xyz>=lo)&(xyz<=hi),axis=1);xyz=xyz[keep];rgb=rgb[keep]
  print('Zoom displays',len(xyz),'of',model.num_points3D(),'points; exported PLY remains unfiltered')
 ax.scatter(xyz[:,0],xyz[:,1],xyz[:,2],c=rgb,s=1,depthshade=False)
 registered=sorted((model.images[i] for i in model.reg_image_ids()),key=lambda im:im.name)
 centers=np.array([im.projection_center() for im in registered]);numbers=np.array([int(im.name.split('_')[-1].split('.')[0]) for im in registered])
 ax.scatter(centers[:,0],centers[:,1],centers[:,2],color='#e34234',s=3)
 for indices in np.split(np.arange(len(centers)),np.flatnonzero(np.diff(numbers)>1)+1):
  segment=centers[indices];ax.plot(segment[:,0],segment[:,1],segment[:,2],color='#e34234',lw=1)
 allxyz=np.vstack([xyz,centers]);span=np.maximum(np.ptp(allxyz,axis=0),1e-5);ax.set_box_aspect(span);ax.view_init(20,-65);ax.set_title(title,fontsize=11);ax.set(xlabel='X',ylabel='Y',zlabel='Z')
fig.suptitle('Actual sparse geometry — arbitrary scale; panels use independent coordinates',fontsize=14)
fig.savefig(out/'actual_sparse_comparison.png',dpi=170);plt.close(fig)
# Readable annotated samples at the door and verified graph disconnect.
ids=[17,20,23,142,145,148]
fig,axs=plt.subplots(2,3,figsize=(8,10),layout='constrained')
for ax,i in zip(axs.flat,ids):
 ax.imshow(Image.open(source/'run/frames'/('frame_%06d.jpg'%i)));ax.set_title('Frame %d | %.2f s'%(i,(i-1)/4));ax.axis('off')
fig.suptitle('Observed transition frames (not a reconstructed room)')
fig.savefig(out/'transition_frames.png',dpi=120);plt.close(fig)
print('Created three diagnostic figures and original matching graph.')
