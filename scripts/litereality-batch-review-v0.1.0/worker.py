"""Rebuild saved source, independently assess, then perform small checkpointed edits."""
import argparse
import asyncio
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

from batch_core import FOCUS, cycle, parse_json, select_frames, validate_review, review_passes, geometry_quality
from batch_io import copy_source, digest, save, source_hashes, timed
from agent_session import recorded_session
from full_support import command

HERE=Path(__file__).resolve().parent


def module(label,name,args,folder,seconds=1200):
    with timed(folder,label):
        command(label,[sys.executable,'-m',name,*map(str,args)],
                Path(os.environ['LR_REPO_ROOT']),dict(os.environ),folder,seconds)


def thumbnails(capture,out):
    from PIL import Image,ImageDraw,ImageFont
    from litereality_agent.room_ops.rendering.room_render.render_vs_capture import _upright_roll
    files=sorted(capture.glob('frame_*.jpg'))
    if not files:raise ValueError('No original RGB capture frames')
    out.mkdir(parents=True)
    ids=[];pages=[]
    for offset in range(0,len(files),12):
        canvas=Image.new('RGB',(960,1160),(28,28,28));draw=ImageDraw.Draw(canvas)
        for j,path in enumerate(files[offset:offset+12]):
            number=int(path.stem.split('_')[1]);ids.append(number)
            if not path.with_suffix('.json').is_file():raise ValueError('Missing camera pose '+str(path))
            with Image.open(path) as opened:
                im=opened.convert('RGB');roll=_upright_roll(capture,path.stem)
                if roll%360:im=im.rotate(roll,expand=True)
                im.thumbnail((310,256))
                x=(j%3)*320;y=(j//3)*290
                canvas.paste(im,(x+(320-im.width)//2,y+25))
                draw.text((x+8,y+5),str(number)+' | '+path.name,fill='white')
        p=out/f'all_capture_{offset//12:03d}.jpg';canvas.save(p,quality=90);pages.append(p)
    return ids,pages


def evidence_selection(capture,folder,room):
    from litereality_agent.agent.providers import SessionSpec
    ids,pages=thumbnails(capture,folder/'evidence'/'all_capture')
    prompt=('Select actual captured photographs for detailed room reconstruction review. '
        'Inspect EVERY supplied contact sheet, including the end of the capture. Numbers label actual frame IDs. '
        'For each target choose up to TWO clear complementary frames, avoiding adjacent duplicate views. '
        'Prefer large, sharp, unobscured subjects; for ground_contact seek bed/cabinet feet and floor junctions. '
        'Do not infer visibility from object names. If not seen, use unobservable; absent only if evidence supports absence. '
        'Return only JSON {"targets": {TARGET: {"frames": [integer IDs], '
        '"visibility": "visible|partial|unobservable|absent", "reason": "specific evidence"}}}. '
        'For absent/unobservable use empty frames. Required targets: '+json.dumps(FOCUS)+'. '
        'This is read-only evidence selection, not model repair. Do not edit files or call another model.')
    before=source_hashes(room)
    spec=SessionSpec(prompt=prompt,cwd=folder,read_roots=(capture,),image_paths=tuple(pages),
        read_only=True,setting_sources=(),file_tools=('Read',),step_budget=50,timeout_seconds=600)
    result=asyncio.run(recorded_session(spec,folder/'sessions'/'selector','quality'))
    if source_hashes(room)!=before:raise RuntimeError('Read-only selector changed room source')
    if result['termination']!='completed':raise RuntimeError('Evidence selection '+result['termination']+': '+result['reason'])
    selection=select_frames(parse_json(result['text']),ids)
    save(folder/'evidence'/'selection.json',selection)
    return selection


def source_build_stamp(room,preview):
    return dict(source=source_hashes(room),build={name:digest(preview/name)
        for name in ('Room.glb','room_layout.json','manifest.json')})


class Engine:
    def __init__(self,folder,capture,scene,seconds):
        self.folder=folder;self.capture=capture;self.scene=scene;self.seconds=seconds
        self.root=folder/'scenes'/scene/'realism_authoring'
        self.seed=folder/'scenes'/scene/'scene_init/scene_stage/room_init/room'
        self.active=self.root/'room';self.selection=None;self.last_round=None

    def assess(self,number):
        from litereality_agent.agent.providers import SessionSpec
        from litereality_agent.pipeline.realism_authoring.acceptance import preserved_instances
        from litereality_agent.agent.tools.shared import config
        rounddir=self.root/'rounds'/f'round_{number:02d}'
        rounddir.mkdir(parents=True,exist_ok=False)
        room=rounddir/'room';preview=rounddir/'room_preview'
        report=dict(quality='check_failed',accepted=False,issues=[],round=number,
                    evidence=str(rounddir),source=str(room),preview=str(preview))
        self.last_round=rounddir
        try:
            copy_source(self.active,room)
            for p in room.rglob('*.py'):
                if '__pycache__' not in p.parts:compile(p.read_bytes(),str(p),'exec')
            config.set_room(room,preview)
            # Fresh preview forces procedural object compilation; retained static/neural meshes
            # are reused. Omitting --regenerate avoids needless network material refetches.
            module(f'r{number:02d}_build','build_saved',
                   ['--room',room,'--out',preview],self.folder)
            report['instances_preserved']=preserved_instances(self.seed,room)
            stamp=source_build_stamp(room,preview)
            save(rounddir/'build_fingerprint.json',stamp)
            try:
                module(f'r{number:02d}_geometry','litereality_agent.pipeline.room_qc.validate',
                    ['--room',room,'--preview',preview,'--json',rounddir/'validation.json'],self.folder)
            except RuntimeError:
                if not (rounddir/'validation.json').is_file():raise
            geometry=json.loads((rounddir/'validation.json').read_text())
            if geometry_quality(geometry)=='check_failed':
                raise RuntimeError('Geometry checker unavailable/incomplete: '+json.dumps(geometry.get('failing',geometry.get('error'))))
            report['support_pass']=geometry.get('pass') is True
            # Export/reopen is a separate technical check, never a visual approval.
            with timed(self.folder,f'r{number:02d}_reopen'):
                command(f'r{number:02d}_reopen',[os.environ['LITEREALITY_BLENDER'],'-b','--python-exit-code','1',
                    '--python',HERE/'verify_export.py','--',preview],HERE,dict(os.environ),self.folder,600)
            report['exports_reopened']=True
            self.update_candidate(rounddir,report)
            if not report['instances_preserved']:
                report.update(quality='not_passed',issues=[dict(priority='support',object_ids=[],
                    defect='Original manifest identities or bundled source GLBs changed; restore them.')])
                return report
            if not report['support_pass']:
                report.update(quality='not_passed',issues=[dict(priority='support',object_ids=[x['id']] if x.get('id') else [],
                    defect=json.dumps(x,ensure_ascii=False)) for x in geometry.get('failing',[])])
                if not report['issues']:raise RuntimeError('Geometry failed without actionable findings')
                return report
            if self.selection is None:
                with timed(self.folder,'evidence_selection'):
                    self.selection=evidence_selection(self.capture,self.folder,room)
            frames=','.join(map(str,self.selection['frames']))+','
            module(f'r{number:02d}_paired_render','litereality_agent.room_ops.rendering.room_render.render_vs_capture',
                ['--scan',self.capture,'--room',room,'--assets',preview,'--out',rounddir/'compare',
                 '--frames',frames,'--res-div','1'],self.folder)
            pairs=sorted((rounddir/'compare'/'pairs').glob('pair_*.png'))
            got={int(p.stem.split('_')[1]) for p in pairs}
            if got!=set(self.selection['frames']):raise RuntimeError('Selected capture pairs missing')
            layout=json.loads((preview/'room_layout.json').read_text())
            objects=layout['objects'];objects=list(objects.values()) if isinstance(objects,dict) else objects
            ids=[x['id'] for x in objects]
            prompt=('Independently judge this saved reconstruction against ALL paired capture photos. '
                'Each pair is RENDER on the left and REAL photograph on the right, at the same captured pose. '
                'This is a new read-only review; do not edit any files, call other models or trust author self-evaluation. '
                'Check every listed object, merged member, opening, support surface, missing item, scale and material. '
                'Prioritize lighting/hotspots and large shapes before small accessories. '
                'Explicitly inspect headboard, bedside cabinet, curtains, bedding and ground contacts in nominated frames. '
                'Separate a visible defect from insufficient evidence: hidden/unresolved items use priority=evidence; '
                'do not invent geometry or call hidden evidence proof of failure in the mesh. '
                'Static support validation does not establish visual similarity or dynamic stability. '
                'A pass requires score >=8, zero issues, all_objects_reviewed=true, and every object ID reviewed. '
                'If any listed object cannot be verified, report an evidence issue and keep all_objects_reviewed=false. '
                'Return only JSON: {"pass":bool,"score":number,"all_objects_reviewed":bool, '
                '"reviewed_object_ids":[verified IDs],"issues":[{"object_ids":[IDs], '
                '"priority":"support|lighting|large_appearance|small_details|evidence", '
                '"defect":"concrete observation with frame IDs"}]}. '
                'Selection rationale: '+json.dumps(self.selection,ensure_ascii=False)+
                '\nObject layout: '+json.dumps(objects,ensure_ascii=False))
            spec=SessionSpec(prompt=prompt,cwd=rounddir,read_roots=(self.capture,),
                image_paths=tuple(pairs),read_only=True,setting_sources=(),file_tools=('Read',),
                timeout_seconds=600,step_budget=60)
            with timed(self.folder,f'r{number:02d}_independent_review'):
                session=asyncio.run(recorded_session(spec,rounddir/'review_session','quality'))
            report['review_termination']=session['termination']
            if session['termination']!='completed':raise RuntimeError('Independent review incomplete: '+session['termination'])
            review=validate_review(parse_json(session['text']),ids)
            save(rounddir/'visual_review.json',review)
            if source_build_stamp(room,preview)!=stamp:raise RuntimeError('Review or rendering changed source/build; verdict invalid')
            passed=review_passes(review,ids)
            report.update(quality='passed' if passed else 'not_passed',accepted=passed,
                          score=review['score'],issues=review['issues'],all_objects_reviewed=review['all_objects_reviewed'])
        except Exception as exc:
            report.update(quality='check_failed',accepted=False,error=f'{type(exc).__name__}: {exc}')
            print(traceback.format_exc(),flush=True)
        finally:
            save(rounddir/'assessment.json',report)
            if report.get('exports_reopened'):self.update_candidate(rounddir,report)
        return report

    def update_candidate(self,rounddir,report):
        save(self.folder/'latest_candidate.json',dict(round=report['round'],accepted=report.get('accepted',False),
            quality=report['quality'],source=str(rounddir/'room'),
            blend=str(rounddir/'room_preview/Room.portable.blend'),glb=str(rounddir/'room_preview/Room.glb'),
            note='Reopen verification is not visual acceptance; review assessment.json.'))

    def repair(self,number,issues):
        from litereality_agent.agent.providers import SessionSpec
        from litereality_agent.agent.tools.shared import config
        # Every prior round remains intact, even when a later repair leaves invalid Python.
        active=self.root/'editing'/f'batch_{number:02d}'/'room'
        copy_source(self.last_round/'room',active)
        self.active=active
        sessiondir=self.root/'editing'/f'batch_{number:02d}'/'session'
        config.set_room(active,active.parent/'room_preview')
        prompt=('Repair ONLY this small batch of visible issues. You are editing a copy of a saved room. '
            'Do not restart ingest, reconstruct, TRELLIS, reference generation or the full author workflow. '
            'Read Room.md and the assessment, photos and validation in '+str(self.last_round)+'. '
            'Work only inside '+str(active)+'. Other rounds, source runs, checks and reports are read-only. '
            'Preserve manifest identities, source neural GLBs, articulated joints and valid finite support. '
            'Do not remove difficult objects, add hidden supports, relax acceptance or fabricate unseen parts. '
            'Edit Room.py/object.py/materials only when justified by the photos. '
            'The supervisor uses saved texture pixels without downloading recipe URLs; if changing a texture, '
            'save the actual image in its existing materials/textures directory. Recipe-only changes do not regenerate pixels. '
            'This batch prioritizes support then lighting and large appearance before small details. '
            'Do not expand scope beyond these issues: '+json.dumps(issues,ensure_ascii=False)+'. '
            f'Hard wall budget {self.seconds}s / 45 tool calls. Save valid source after EACH small change. '
            f'Finish editing by {int(self.seconds*.7)}s; use remaining time to syntax-check and summarize. '
            'This session is source editing only: do not launch Blender, render, mesh checks or full builds. '
            'Use the completed prior round checks and comparison images as evidence. '
            'A separate supervisor will rebuild, render, check collisions/support and independently review '
            'saved changes after you finish, including after a timeout. Do not call another model. '
            'Return changes made, evidence used and unresolved defects; never self-declare acceptance.')
        spec=SessionSpec(prompt=prompt,cwd=active,read_roots=(self.capture,self.last_round),
            capability_scene=active,capability_tools=(),
            setting_sources=(),step_budget=45,timeout_seconds=self.seconds,max_turns=60)
        with timed(self.folder,f'batch_{number:02d}_repair'):
            result=asyncio.run(recorded_session(spec,sessiondir,'author'))
        save(sessiondir/'saved_source_hashes.json',source_hashes(active))
        return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--folder',type=Path,required=True)
    p.add_argument('--max-batches',type=int,default=3);p.add_argument('--repair-seconds',type=int,default=900)
    args=p.parse_args();folder=args.folder.resolve();data=json.loads((folder/'input.json').read_text())
    engine=Engine(folder,Path(data['capture']),data['scene'],args.repair_seconds)
    start=time.monotonic()
    def progress(result):
        result.update(scene=data['scene'],operation='saved_scene_batched_review',
                      elapsed_seconds=time.monotonic()-start,source_run=data['source_run'],
                      ingest_reconstruct_rerun=False)
        save(folder/'result.json',result)
        print('QUALITY:',result['quality'],'REPAIR:',result['repair_termination'],'STATUS:',result['status'],flush=True)
    result=cycle(engine.assess,engine.repair,args.max_batches,progress)
    result['workflow_finished']=True
    progress(result)
    return 0 if result['status']=='accepted' else 2


if __name__=='__main__':sys.exit(main())
