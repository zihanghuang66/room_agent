"""Runs in the existing scene Python. Source edits, matched views, strict grouped review."""
import asyncio
import json
from pathlib import Path
from uuid import uuid4


def selection(available, count=24):
    values=sorted(set(available))
    if not values:raise ValueError('No capture cameras')
    count=min(count,len(values))
    return [values[round(i*(len(values)-1)/max(1,count-1))] for i in range(count)]


def run_stage(stage,request,state,attempt):
    from batch_io import copy_source, source_hashes, digest
    from author_closeout import save, normalize_review
    from batch_core import parse_json
    from worker import source_build_stamp
    from agent_session import recorded_session
    from litereality_agent.agent.providers import SessionSpec
    capture=Path(request['capture'])
    if stage=='inspect_current':
        try:
            from .unchanged_edit import inspect_current
        except ImportError:
            from unchanged_edit import inspect_current
        return inspect_current(request,state,attempt)
    if stage=='evidence':
        # Same trajectory-wide camera set for baseline and child. Never scene-name heuristics.
        frames=selection(int(p.stem[6:]) for p in capture.glob('frame_*.json'))
        result={'frames':frames,'method':'evenly spaced across full capture; identical before/after cameras',
                'coverage':'Visual review must flag unobservable objects; sampling is not proof of coverage.'}
        save(attempt/'selection.json',result)
        return {'selection':result},[attempt/'selection.json']
    if stage=='edit':
        active=attempt/'room';marker=attempt/'edit_started.json'
        if not marker.exists():
            source=Path(state['active_source'])
            # Before the edit marker exists, only a source copy has been attempted.
            # Retain incomplete copies from older releases; never replace edited work.
            if active.exists() and source_hashes(active)!=source_hashes(source):
                active.rename(attempt/('room.copy-interrupted-'+uuid4().hex))
            if not active.exists():
                staging=attempt/('room.copying-'+uuid4().hex)
                copy_source(source,staging)
                if source_hashes(staging)!=source_hashes(source):
                    raise RuntimeError('Unstarted edit source differs from snapshot')
                staging.rename(active)
            # Durable at-most-once guard is saved before model invocation.
            save(marker,{'instruction':request['instruction'],'source':source_hashes(active)})
            prompt=('Apply ONLY the explicit user change below to this saved room copy. '
                'Read Room.md, Room.py, Object sources and manifest. Preserve object IDs, source neural meshes, '
                'articulated joints and all unrelated geometry/materials. Save valid Python after each small edit. '
                'Work only inside '+str(active)+'. Do not modify capture, parent, baseline, reports or checks. '
                'Do not run ingest, reconstruct, TRELLIS, image generation, Blender or full builds. '
                'Do not call another model. Render/build/collision/independent review run afterward. '
                'Use actual source coordinates and dimensions; never guess a direction or size from names. '
                'If the requested object/direction is ambiguous, leave it unchanged and explain why. '
                'Do not silently fix unrelated pre-existing quality defects. '
                'If modifying textures, save actual pixels; do not rely on recipe downloads. '
                'Budget: 900 seconds / 45 tool calls. Finish edits by 630 seconds. '
                'Return changes made, measurements and unresolved requirements; do not self-approve. '
                '\nUSER CHANGE:\n'+request['instruction'])
            if request.get('edit_contract'):
                try:
                    from .edit_behavior import skill_text
                except ImportError:
                    from edit_behavior import skill_text
                prompt+='\nBEHAVIOR SKILL\n'+skill_text(request)+'\nCONFIRMED CONTRACT\n'+json.dumps(request['edit_contract'],ensure_ascii=False)
                prompt+=('\nReturn ONLY JSON {"status":"changed|needs_clarification|blocked|unchanged",'
                         '"summary":string,"target_ids":[actual IDs],"questions":[up to 3 concise questions in the user language]}. '
                         'Use changed only for saved complete edits in the confirmed scope. Report partial edits as blocked. '
                         'When identity/evidence/capability is missing, stop and ask specifically; do not silently continue. '
                         'Matched images and bound findings are evidence; inspect them before claiming target ambiguity.')
            save(attempt/'edit_prompt.json',{'prompt':prompt})
            spec=SessionSpec(prompt=prompt,model=request['codex_model'],cwd=active,read_roots=(capture,Path(state['baseline_round_dir'])),
                image_paths=tuple(map(Path,state['baseline_pairs'])),capability_scene=active,capability_tools=(),
                setting_sources=(),step_budget=45,timeout_seconds=900,max_turns=60)
            session=asyncio.run(recorded_session(spec,attempt/'session','author'))
            save(attempt/'edit_session.json',session)
        session=json.loads((attempt/'edit_session.json').read_text()) if (attempt/'edit_session.json').exists() else {'termination':'interrupted'}
        if not (active/'Room.py').is_file():raise RuntimeError('Saved edit is missing Room.py')
        save(attempt/'saved_source.json',source_hashes(active))
        result={'active_source':str(active),'edit_termination':session['termination']}
        outputs=[active,marker,attempt/'saved_source.json']
        if request.get('edit_contract'):
            try:
                from .edit_contract import delta_outcome
            except ImportError:
                from edit_contract import delta_outcome
            try:
                report=parse_json(session.get('text',''))
            except (ValueError,TypeError):
                report={}
            if session['termination']!='completed':
                report={'status':'blocked','summary':'Editor interrupted; saved candidate retained.','questions':['Please review the interruption before retrying.']}
            result.update(delta_outcome(json.loads(marker.read_text())['source'],source_hashes(active),report,request['edit_contract'].get('target_ids') or state.get('resolved_target_ids')))
            save(attempt/'edit_outcome.json',result);outputs.append(attempt/'edit_outcome.json')
        return result,outputs
    if stage=='review':
        if request.get('edit_review_policy'):
            try:
                from .edit_acceptance import POLICY, run_review
            except ImportError:
                from edit_acceptance import POLICY, run_review
            if request['edit_review_policy']!=POLICY:raise RuntimeError('Unknown edit review policy')
            return run_review(request,state,attempt)
        room,preview=Path(state['room']),Path(state['preview'])
        if source_build_stamp(room,preview)!=state['build_stamp']:raise RuntimeError('Review source/build identity changed')
        objects=json.loads((preview/'room_layout.json').read_text())['objects']
        objects=list(objects.values()) if isinstance(objects,dict) else objects
        if not objects:raise RuntimeError('No objects to review')
        ids=[x['id'] for x in objects]
        if len(ids)!=len(set(ids)):raise RuntimeError('Duplicate object IDs')
        binding={'source':state['build_stamp'],'capture_files':request['capture_files'],
                 'instruction':request['instruction'],'selection':state['selection'],
                 'images':{str(p):digest(Path(p)) for p in state['pairs']},'model':request['codex_model'],'policy':'edit-review/1:score8-all-cited-views-zero-issues'}
        binding_path=attempt/'binding.json'
        if binding_path.exists() and json.loads(binding_path.read_text())!=binding:raise RuntimeError('Review checkpoint identity changed')
        save(binding_path,binding)
        ledger_path=attempt/'group_receipts.json'
        ledger=json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
        for name,sha in ledger.items():
            if digest(attempt/name)!=sha:raise RuntimeError('Saved review group integrity changed')
        def checked(review,group_ids):
            valid={Path(p).name for p in state['pairs']}
            for row in review.get('objects',[]):
                if isinstance(row,dict):
                    views=row.get('views')
                    if not isinstance(views,list) or not views or not all(v in valid for v in views):row['observed']=False
            result=normalize_review(review,group_ids)
            verified=review.get('change_verified') is True and isinstance(review.get('change_evidence'),str) and bool(review['change_evidence'].strip())
            result.update(change_verified=bool(verified),change_evidence=review.get('change_evidence',''))
            if not verified:
                result['pass']=False
                result['issues'].append({'kind':'evidence','objects':group_ids,'priority':'unknown','detail':'Requested change was not independently verified from before/after source and views.'})
            return result
        reports=[];outputs=[binding_path]
        for offset in range(0,len(objects),8):
            group=objects[offset:offset+8];group_ids=[x['id'] for x in group]
            path=attempt/f'group_{offset//8:03d}.json'
            if path.exists() and path.name in ledger:
                stored=json.loads(path.read_text())
                if stored['ids']!=group_ids:raise RuntimeError('Review group identity changed')
                review=checked(stored['review'],group_ids)
            else:
                prompt=('Independently review this edited scene. Each pair shows RENDER left, original CAPTURE right '
                    'at the same camera. Read-only: do not edit or call other models. '
                    'The explicit user change intentionally overrides the corresponding original appearance/placement; '
                    'verify that change from source coordinates and renders. All other content must remain faithful to capture. '
                    'Check every listed object and every merged member, articulation, scale, material, missing parts and support. '
                    'Do not trust author summaries. Hidden/unobservable objects require evidence issues; never waive them. '
                    'Score >=8, zero issues, all_objects_reviewed=true and every listed ID verified are ALL required. '
                    'Flag an incomplete or ambiguous user change as an issue even if the scene looks good. '
                    'Explicitly verify the requested change against before/after coordinates, dimensions and materials; explain measurements. '
                    'Return only JSON {"change_verified":bool,"change_evidence":string,"pass":bool,"score":number,"all_objects_reviewed":bool,'
                    '"objects":[{"id":ID,"observed":bool,"views":[pair filename],"reason":string}],"issues":[{"objects":[IDs],"kind":"geometry|appearance|evidence",'
                    '"priority":"support|lighting|large_appearance|small_details","detail":"specific evidence and frame IDs"}]}. '
                    '\nUser change: '+request['instruction']+'\nObjects: '+json.dumps(group,ensure_ascii=False)+
                    '\nChild source: '+str(room)+'\nBefore source: '+str(state['baseline_room']))
                spec=SessionSpec(prompt=prompt,model=request['codex_model'],cwd=attempt,read_roots=(room,Path(state['baseline_room']),capture),
                    image_paths=tuple(map(Path,state['pairs'])),read_only=True,setting_sources=(),file_tools=('Read',),
                    timeout_seconds=600,step_budget=60)
                session=asyncio.run(recorded_session(spec,attempt/f'session_{offset//8:03d}','quality'))
                if session['termination']!='completed':raise RuntimeError('Review group incomplete: '+session['termination']+'; completed groups retained')
                raw_review=parse_json(session['text'])
                review=checked(raw_review,group_ids)
                if source_build_stamp(room,preview)!=state['build_stamp']:raise RuntimeError('Review changed source/build')
                save(path,{'ids':group_ids,'review':raw_review})
                ledger[path.name]=digest(path);save(ledger_path,ledger)
            reports.append(review);outputs.append(path)
            print(f'REVIEW_GROUPS {len(reports)}/{(len(objects)+7)//8}',flush=True)
        issues=list(state.get('issues',[]))+[x for r in reports for x in r['issues']]
        passed=state.get('geometry_pass') is True and all(r['pass'] is True for r in reports)
        result=dict(quality_status='passed' if passed else 'not_passed',score=min(r['score'] for r in reports),issues=issues)
        save(attempt/'visual_review.json',result);outputs.extend([attempt/'visual_review.json',ledger_path])
        return result,outputs
    if stage=='finalize':
        from struct import unpack
        from litereality_agent.room_ops.rendering.room_render.render_vs_capture import _upright_roll
        before={int(Path(p).stem[5:]):Path(p) for p in state['baseline_pairs']}
        after={int(Path(p).stem[5:]):Path(p) for p in state['pairs']}
        if set(before)!=set(after) or set(after)!=set(state['selection']['frames']):raise RuntimeError('Before/after camera IDs differ')
        def size(path):
            with path.open('rb') as stream:header=stream.read(24)
            if len(header)!=24 or header[:8]!=b'\x89PNG\r\n\x1a\n':raise RuntimeError('Invalid render PNG')
            return unpack('>II',header[16:24])
        rows=[]
        for camera in sorted(after):
            a,b=after[camera],before[camera]
            raw=a.parent.parent/'renders'/('frame_'+a.stem[5:]+'.png')
            raw_before=b.parent.parent/'renders'/raw.name
            rw,rh=size(raw)
            if size(raw_before)!=(rw,rh) or size(a)!=size(b):raise RuntimeError('Before/after render dimensions differ')
            if _upright_roll(capture,raw.stem)%180:rw,rh=rh,rw
            w=round(720*rw/rh);width,height=size(a)
            if height!=754 or width<w+8:raise RuntimeError('Unexpected renderer pair format')
            rows.append(dict(path=str(a),before_path=str(b),camera_id=camera,width=width,height=height,
                             captured=[0,34,w,720],rendered=[0,34,w,720]))
        result=dict(change_views=rows,technical_export_verified=True,
                    completion_reason='quality_passed' if state['quality_status']=='passed' else 'edit_review_not_passed')
        if request.get('edit_review_policy'):
            result['completion_reason']='edit_accepted' if state.get('edit_status')=='passed' else 'edit_not_accepted'
        save(attempt/'comparison.json',result)
        return result,[attempt/'comparison.json']
    raise ValueError('Unknown edit stage '+stage)
