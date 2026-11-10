"""Edit acceptance is not whole-room certification. Also supports read-only legacy evidence."""
import asyncio
import hashlib
import json
import math
from pathlib import Path

POLICY = 'impact-review/1'


def assess(review, *, changed, known_ids, before_views, after_views, geometry_pass, geometry_issues):
    if not isinstance(review, dict):
        raise RuntimeError('Edit review response must be a JSON object')
    issues = list(geometry_issues)
    def reject(detail):
        issues.append(dict(kind='evidence', objects=[], priority='unknown', detail=detail))
    def text(key):
        value=review.get(key)
        return value.strip() if isinstance(value,str) else ''
    def names(value):
        return isinstance(value,list) and all(isinstance(v,str) and v for v in value) and len(value)==len(set(value))
    score=review.get('score')
    score_ok=type(score) in (int,float) and math.isfinite(score) and 0<=score<=10
    verified=review.get('change_verified') is True and bool(text('change_evidence'))
    if not verified:reject('Requested change lacks independent before/after verification.')
    if review.get('no_unintended_changes') is not True or not text('scope_evidence'):
        reject('Absence of unintended changes has not been verified.')
    files=review.get('changed_files')
    if not changed or not names(files) or set(files)!=set(changed):
        reject('Review must account for every actual changed source file.')
    affected=review.get('affected_objects')
    if not names(affected) or not affected or not set(affected)<=set(known_ids):
        reject('Affected object scope is missing or invalid.')
        affected=[]
    rows=review.get('objects')
    rows=rows if isinstance(rows,list) else []
    before={'before:'+v for v in before_views};after={'after:'+v for v in after_views}
    for identifier in affected:
        matches=[r for r in rows if isinstance(r,dict) and r.get('id')==identifier]
        row=matches[0] if len(matches)==1 else {}
        views=row.get('views')
        if (row.get('observed') is not True or not names(views) or
                not set(views)<=before|after or not set(views)&before or not set(views)&after):
            reject('No verified before/after views cover affected object '+identifier+'.')
    reported=review.get('issues')
    if not isinstance(reported,list):reject('Reviewer did not return an explicit issues list.')
    else:
        issues.extend(r if isinstance(r,dict) else dict(kind='unknown',objects=[],priority='unknown',detail=str(r)) for r in reported)
    if geometry_pass is not True:reject('Geometry / support checks did not pass.')
    if not score_ok or score<8:reject('Edit review requires a finite score of at least 8 / 10.')
    if review.get('pass') is not True:reject('Independent reviewer did not approve this edit.')
    return dict(edit_status='passed' if not issues else 'not_passed',
                edit_score=score if score_ok else None, edit_issues=issues,
                change_verified=verified, change_evidence=text('change_evidence'),
                scope_evidence=text('scope_evidence'), affected_objects=affected)


def run_review(request,state,attempt):
    # Standalone school interpreter uses the same module through scene_driver.
    from batch_io import source_hashes, digest
    from author_closeout import save
    from batch_core import parse_json
    from worker import source_build_stamp
    from agent_session import recorded_session
    from litereality_agent.agent.providers import SessionSpec
    room,preview=Path(state['room']),Path(state['preview'])
    baseline=Path(state['baseline_room'])
    baseline_preview=Path(state.get('baseline_preview',state['preview']))
    baseline_stamp=source_build_stamp(baseline,baseline_preview)
    if state.get('baseline_build_stamp',baseline_stamp)!=baseline_stamp:
        raise RuntimeError('Baseline source/build identity changed')
    if source_build_stamp(room,preview)!=state['build_stamp']:
        raise RuntimeError('Review source/build identity changed')
    before,after=source_hashes(baseline),source_hashes(room)
    changed=sorted(k for k in set(before)|set(after) if before.get(k)!=after.get(k))
    if request.get('edit_contract') and not changed:
        raise RuntimeError('No saved source delta at review; inspect the edit checkpoint. No quality score was assigned.')
    objects=json.loads((preview/'room_layout.json').read_text())['objects']
    objects=list(objects.values()) if isinstance(objects,dict) else objects
    ids=[o['id'] for o in objects]
    if not ids or len(ids)!=len(set(ids)):raise RuntimeError('Missing or duplicate object IDs')
    images=[*state['baseline_pairs'],*state['pairs']]
    binding=dict(policy=POLICY,source=state['build_stamp'],baseline_build=baseline_stamp,baseline_files=before,child_files=after,
                 capture_files=request['capture_files'],instruction=request['instruction'],selection=state['selection'],
                 images={str(p):digest(Path(p)) for p in images},model=request['codex_model'])
    if request.get('edit_contract'):binding['edit_contract']=request['edit_contract']
    binding_path=attempt/'impact_binding.json'
    if binding_path.exists() and json.loads(binding_path.read_text())!=binding:
        raise RuntimeError('Edit review checkpoint identity changed')
    save(binding_path,binding)
    delta=attempt/'source_delta.json'
    save(delta,dict(changed_files=changed,before=before,after=after))
    raw_path=attempt/'impact_review.json';receipt=attempt/'impact_receipt.json'
    if raw_path.exists() and receipt.exists():
        saved=json.loads(receipt.read_text())
        if saved!={'binding':digest(binding_path),'review':digest(raw_path)}:
            raise RuntimeError('Saved edit review integrity changed')
        raw=json.loads(raw_path.read_text())
    elif not changed:
        raw=dict(score=0,issues=[dict(kind='evidence',objects=[],priority='unknown',detail='No source change was saved; instruction completion requires inspection.')])
        save(raw_path,raw);save(receipt,dict(binding=digest(binding_path),review=digest(raw_path)))
    else:
        prompt=('Independently assess ONLY this explicit edit and its effects, not overall capture fidelity. Read-only; do not edit, '
                'run another model, reconstruct, or repair. Parent quality findings are historical and do not automatically reject '
                'an otherwise correct edit. They also do not become fixed or approved. '
                'Inspect source_delta.json and the complete changed source/asset content, before and after compiled scenes, '
                'and matched renders. Do not trust author descriptions. Identify every affected object including shared materials, '
                'light/visibility/contact effects on neighbours; include those IDs in affected_objects. '
                'For material changes compare geometry, transforms, scene object inventory, unrelated material/texture content, '
                'lights and camera settings; for placement changes inspect supports, collisions and resulting occlusion. '
                'Account for EVERY changed file, not just the named target. Explain measurements supporting the request and '
                'absence of out-of-scope changes. If effects cannot be bounded or affected objects are hidden, return evidence issues; '
                'never approve on appearance alone. Unchanged baseline defects are not new edit defects. '
                'Images are before and after renderer pairs (RENDER left, original CAPTURE right); compare the LEFT panels for edits. '
                'Each affected object needs valid before:pair_XXXXX.png AND after:pair_XXXXX.png citations. '
                'Score >=8, zero edit issues, verified change, verified scope and complete affected-object coverage are all required. '
                'Return ONLY JSON {"pass":bool,"score":number,"change_verified":bool,"change_evidence":string,'
                '"no_unintended_changes":bool,"scope_evidence":string,"changed_files":[relative paths],'
                '"affected_objects":[IDs],"objects":[{"id":ID,"observed":bool,"views":[citations]}],'
                '"issues":[{"kind":"geometry|appearance|evidence","objects":[IDs],"priority":"support|lighting|large_appearance|small_details","detail":string}]}. '
                '\nUser change: '+request['instruction']+'\nSource delta: '+str(delta)+
                '\nBefore scene: '+str(baseline)+'\nBefore compiled exports: '+str(baseline_preview)+
                '\nAfter scene: '+str(room)+'\nAfter compiled exports: '+str(preview)+
                '\nObjects: '+json.dumps(objects,ensure_ascii=False)+
                '\nBefore images: '+json.dumps(state['baseline_pairs'])+'\nAfter images: '+json.dumps(state['pairs']))
        if request.get('edit_contract'):
            try:
                from .edit_behavior import skill_text
            except ImportError:
                from edit_behavior import skill_text
            prompt=skill_text(request)+'\nROLE: independent reviewer of an actual saved change. Use the following review schema and unchanged acceptance criteria.\n'+prompt+'\nConfirmed contract: '+json.dumps(request['edit_contract'],ensure_ascii=False)
        save(attempt/'impact_prompt.json',{'prompt':prompt})
        spec=SessionSpec(prompt=prompt,model=request['codex_model'],cwd=attempt,
                         read_roots=(room,baseline,preview,baseline_preview,Path(request['capture'])),image_paths=tuple(map(Path,images)),
                         read_only=True,setting_sources=(),file_tools=('Read',),timeout_seconds=900,step_budget=60)
        session=asyncio.run(recorded_session(spec,attempt/'impact_session','quality'))
        if session['termination']!='completed':
            raise RuntimeError('Edit review incomplete: '+session['termination']+'; saved edit retained')
        raw=parse_json(session['text'])
        if (source_build_stamp(room,preview)!=state['build_stamp'] or source_build_stamp(baseline,baseline_preview)!=baseline_stamp
                or source_hashes(baseline)!=before or source_hashes(room)!=after):
            raise RuntimeError('Review changed source/build')
        save(raw_path,raw);save(receipt,dict(binding=digest(binding_path),review=digest(raw_path)))
    result=assess(raw,changed=changed,known_ids=ids,
                  before_views=[Path(p).name for p in state['baseline_pairs']],
                  after_views=[Path(p).name for p in state['pairs']],
                  geometry_pass=state.get('geometry_pass'),geometry_issues=state.get('issues',[]))
    baseline_quality=dict(status=request.get('parent_quality','unreviewed'),score=request.get('parent_score'),
                          issues=request.get('parent_issues',[]),run_id=request['parent_run_id'] if 'parent_run_id' in request else None)
    # There is no fresh whole-room score. Retain negative evidence, never promote a local pass.
    result.update(quality_status='not_passed' if baseline_quality['status']=='not_passed' or result['edit_status']!='passed' else 'unreviewed',
                  score=None,issues=result['edit_issues'],baseline_quality=baseline_quality,whole_room_review='not_repeated')
    save(attempt/'visual_review.json',result)
    return result,[binding_path,delta,raw_path,receipt,attempt/'visual_review.json']


def legacy_verification(root,request,state):
    """Project old evidence without changing any receipt, state, or acceptance policy."""
    unknown=dict(edit_status='unreviewed',change_verified=False)
    root=Path(root)
    def read(rel,expected=None):
        rel=Path(rel)
        if rel.is_absolute() or '..' in rel.parts:raise ValueError('Unsafe receipt path')
        path=root/rel
        if any(p.is_symlink() for p in [path,*path.parents]) or not path.is_file():raise ValueError('Unavailable evidence')
        data=path.read_bytes()
        if expected and hashlib.sha256(data).hexdigest()!=expected:raise ValueError('Evidence changed')
        return json.loads(data)
    try:
        outputs=read('receipts/r00_review.json')['outputs']
        prefix='work/attempts/r00_review/stable/'
        binding=read(prefix+'binding.json',outputs[prefix+'binding.json'])
        if binding['source']!=state['build_stamp'] or binding['instruction']!=request['instruction']:return unknown
        ledger=read(prefix+'group_receipts.json',outputs[prefix+'group_receipts.json'])
        expected={Path(k).name for k in outputs if k.startswith(prefix+'group_') and k.endswith('.json') and not k.endswith('group_receipts.json')}
        if not ledger or set(ledger)!=expected:return unknown
        evidence=[]
        for name,sha in ledger.items():
            if Path(name).name!=name or outputs[prefix+name]!=sha:return unknown
            review=read(prefix+name,sha)['review']
            if review.get('change_verified') is not True or not isinstance(review.get('change_evidence'),str) or not review['change_evidence'].strip():return unknown
            evidence.append(review['change_evidence'])
        return dict(edit_status='change_verified',change_verified=True,change_evidence='\n\n'.join(evidence),
                    edit_verification_source='saved_legacy_review',verified_review_groups=len(evidence))
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        return unknown
