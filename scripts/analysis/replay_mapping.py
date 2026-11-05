"""Controlled local replay from a copied database; preserve server evidence."""
import argparse,hashlib,json,shutil,time
from pathlib import Path
import pycolmap as p
import numpy as np
ap=argparse.ArgumentParser();ap.add_argument('--source',required=True);ap.add_argument('--output',required=True);ap.add_argument('--focal',type=float);ap.add_argument('--database-source');ap.add_argument('--min-frame',type=int,default=1);ap.add_argument('--no-fallback',action='store_true');ap.add_argument('--exhaustive',action='store_true');ap.add_argument('--max-models',type=int,default=5);ap.add_argument('--input-manifest');args=ap.parse_args()
src=Path(args.source).resolve();out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=False)
dbpath=out/'database.db';shutil.copyfile(Path(args.database_source) if args.database_source else src/'database.db',dbpath)
if args.focal:
 db=p.Database.open(dbpath)
 for c in db.read_all_cameras():
  c.params[0]=args.focal;db.update_camera(c)
 db.close()
matching_seconds=0
if args.exhaustive:
 match=p.FeatureMatchingOptions();match.num_threads=4
 pair=p.ExhaustivePairingOptions();pair.block_size=20
 t=time.monotonic();p.match_exhaustive(dbpath,matching_options=match,pairing_options=pair,device=p.Device.cpu);matching_seconds=time.monotonic()-t
opt=p.IncrementalPipelineOptions();opt.num_threads=4;opt.random_seed=0;opt.mapper.random_seed=0;opt.triangulation.random_seed=0;opt.max_num_models=args.max_models;opt.min_model_size=6;opt.ba_use_gpu=False;opt.max_runtime_seconds=180
opt.structure_less_registration_fallback=not args.no_fallback
if args.min_frame>1:
 opt.image_names=[f.name for f in sorted((src/'frames').glob('frame_*.jpg')) if int(f.stem.split('_')[-1])>=args.min_frame]
if args.input_manifest:
 manifest=json.loads(Path(args.input_manifest).read_text());names=[]
 for item in manifest['selected_frames']:
  name=item['file']
  if Path(name).name!=name or name in names:raise ValueError('Unsafe or duplicate manifest filename')
  if hashlib.sha256((src/'frames'/name).read_bytes()).hexdigest()!=item['sha256']:raise ValueError('Input hash mismatch: '+name)
  names.append(name)
 opt.image_names=names
 (out/'input_manifest.json').write_text(json.dumps(manifest,indent=2))
 db=p.Database.open(dbpath);cache_options=p.DatabaseCacheOptions();cache_options.image_names=set(names);cache_options.min_num_matches=opt.min_num_matches
 cache=p.DatabaseCache.create(db,cache_options);db.close()
 (out/'database_cache_scope.json').write_text(json.dumps({'requested_images':names,'cached_image_count':cache.num_images(),'reconstruction_uses_image_names_filter':True,'source_database_sha256':hashlib.sha256(dbpath.read_bytes()).hexdigest()},indent=2))
 assert cache.num_images()<=len(names)
 del cache
(out/'config.json').write_text(json.dumps({'args':vars(args),'options':opt.todict(),'version':p.__version__},default=str,indent=2))
start=time.monotonic();models=p.incremental_mapping(dbpath,src/'frames',out/'sparse',options=opt)
summary=[]
for mid,m in models.items():
 d=out/'sparse'/str(mid);d.mkdir(exist_ok=True);m.write(d);m.export_PLY(d/'sparse.ply')
 registered=[m.images[i] for i in m.reg_image_ids()]
 if args.input_manifest:assert all(i.name in names for i in registered)
 summary.append({'model':int(mid),'registered':len(registered),'points':m.num_points3D(),'mean_reprojection_px':m.compute_mean_reprojection_error() if m.num_points3D() else None,'images':sorted(i.name for i in registered),'cameras':[{ 'id':int(c.camera_id),'params':c.params.tolist()} for c in m.cameras.values()]})
(out/'result.json').write_text(json.dumps({'seconds':time.monotonic()-start,'matching_seconds':matching_seconds,'models':summary},indent=2));print('RESULT',json.dumps(summary))
