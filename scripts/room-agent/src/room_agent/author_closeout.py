"""Finite Author acceptance adapter. Backend validation thresholds stay unchanged.

The durable ledger consumes a batch BEFORE a model call. Resume reviews saved edits,
never repeats an interrupted repair. Reviewed candidates are immutable evidence;
a best-candidate pointer does not silently roll back the working source.
"""
import asyncio
from contextlib import contextmanager
import json
import math
import inspect
import os
import re
from pathlib import Path
import shutil
import uuid

try:
    from . import review_checkpoint as checkpoints
except ImportError:  # standalone school app interpreter
    import review_checkpoint as checkpoints

MAX_BATCHES = 3
MAX_STALLED = 2
PRIORITIES = ('support', 'lighting', 'large_appearance', 'small_details')


def save(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    with tmp.open('w') as stream:
        stream.write(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
        stream.flush();os.fsync(stream.fileno())
    tmp.replace(path)
    directory=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)


def read(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).is_file() else default


def select_frames(available, count, previous=()):
    values = sorted(set(available))
    chosen = set(previous) & set(values)
    count = min(count, len(values))
    if count and not chosen:
        chosen.update(values[round(i*(len(values)-1)/max(1,count-1))] for i in range(count))
    # Fill the largest gaps in the existing whole-trajectory set deterministically.
    while len(chosen) < count:
        chosen.add(max((v for v in values if v not in chosen), key=lambda v:(min(abs(v-c) for c in chosen), -v)))
    return sorted(chosen)


def normalize_review(review, ids):
    if not isinstance(review, dict):raise RuntimeError('Review response is not JSON object')
    score = review.get('score')
    valid_score = type(score) in (int,float) and math.isfinite(score) and 0 <= score <= 10
    rows = review.get('objects', [])
    if not isinstance(rows,list) or not isinstance(review.get('issues'),list):
        raise RuntimeError('Review response is missing objects/issues lists')
    observed = {r.get('id') for r in rows if isinstance(r,dict) and r.get('observed') is True}
    missing = set(ids)-observed
    issues=[]
    for issue in review['issues']:
        if not isinstance(issue,dict):
            issues.append({'kind':'unknown','objects':[],'detail':str(issue),'priority':'unknown'});continue
        kind=issue.get('kind') if issue.get('kind') in ('geometry','appearance','evidence') else 'unknown'
        objects=issue.get('objects',[])
        if not isinstance(objects,list):objects=[]
        if not objects or any(not isinstance(x,str) or x not in ids for x in objects):kind='unknown'
        issues.append({'kind':kind,'objects':[str(x) for x in objects],
                       'detail':str(issue.get('detail','Unspecified issue')),
                       'priority':issue.get('priority') if issue.get('priority') in PRIORITIES else 'unknown'})
    for name in sorted(missing):
        issues.append({'kind':'evidence','objects':[name],'priority':'unknown','detail':'No verified view covers this object and its contacts.'})
    if not valid_score:
        raise RuntimeError('Review score must be a finite number in 0..10')
    passed=(review.get('pass') is True and score>=8 and review.get('all_objects_reviewed') is True and not issues and not missing)
    if not passed and not issues:
        issues.append({'kind':'unknown','objects':list(ids),'priority':'unknown','detail':'Reviewer did not approve; no concrete repair target was supplied.'})
    return {'pass':passed,'score':score,'all_objects_reviewed':review.get('all_objects_reviewed') is True and not missing,
            'issues':issues,'objects':rows}


def repair_targets(issues):
    usable=[x for x in issues if x.get('kind') in ('geometry','appearance') and x.get('objects') and x.get('priority') in PRIORITIES]
    return sorted(usable,key=lambda x:(x['kind']!='geometry',PRIORITIES.index(x['priority'])))[:3]


def improved(before, after):
    if after.get('accepted'):return True
    if not before.get('support_pass') and after.get('support_pass'):return True
    if before.get('support_pass') and not after.get('support_pass'):return False
    a,b=before.get('visual_review',{}),after.get('visual_review',{})
    if a.get('frames')!=b.get('frames'):return False
    def defects(r):return {(x.get('kind'),tuple(sorted(x.get('objects',[])))) for x in r.get('issues',[])}
    # Rewording or fewer prose bullets alone is never counted as progress.
    return (b.get('score',0)>=a.get('score',0)+.25 and defects(b)<=defects(a)) or (defects(b)<defects(a) and b.get('score',0)>=a.get('score',0))


class Backend:
    def __init__(self):
        from litereality_agent.pipeline.realism_authoring import acceptance
        from litereality_agent.pipeline.support import run_module
        self.acceptance=acceptance;self.module=run_module
    def fingerprint(self,ctx,preview):return self.acceptance.fingerprint(ctx.authored_room,preview)
    def preserved(self,ctx):return self.acceptance.preserved_instances(ctx.seed_room,ctx.authored_room)
    def build(self,ctx,preview):
        rc,log=self.module(ctx,'litereality_agent.room_ops.compile.build_from_room',
                          ['--room',ctx.authored_room,'--out',preview,'--regenerate'],log_name='closeout_build')
        if rc:raise RuntimeError('Saved source rebuild failed: '+str(log))
    def geometry(self,ctx,preview):return self.acceptance.validate(ctx.authored_room,preview)
    def render(self,ctx,evidence,frames):
        rc,log=self.module(ctx,'litereality_agent.room_ops.rendering.room_render.render_vs_capture',
            ['--scan',ctx.capture_dir,'--room',ctx.authored_room,'--out',evidence/'compare',
             '--frames',','.join(map(str,frames))],log_name='closeout_render')
        pairs=sorted((evidence/'compare/pairs').glob('pair_*.png'))
        actual={int(p.stem.split('_')[-1]) for p in pairs}
        if rc or actual!=set(frames) or len(pairs)<3:raise RuntimeError('Capture-pair rendering incomplete: '+str(log))
        return list(map(str,pairs))
    def review_identity(self):
        from litereality_agent.agent.tools.shared import config
        from litereality_agent.agent import providers
        return {'model':config.MODEL,'provider':providers.provider_name('quality'),
                'semantic_version':checkpoints.SEMANTIC_VERSION,
                'prompt_code':checkpoints.digest(inspect.getsource(Backend.review))}
    def review(self,images,objects):
        prompt=('Independently compare the rendered room against these paired capture photos. '
                'Check EVERY listed object, every merged member, support/contact, opening, scale, shape, material and excessive lighting. '
                'Never infer unseen geometry as correct. Distinguish actual visible defects from missing evidence. '
                'Return JSON: pass:boolean, score:number 0..10, all_objects_reviewed:boolean, '
                'objects:[{id, observed:boolean, views:[pair filename], reason}], '
                'issues:[{kind:geometry|appearance|evidence,objects:[exact IDs], '
                'priority:support|lighting|large_appearance|small_details,detail}]. '
                'Set observed true only if cited views actually show identity, shape AND relevant contacts. '
                'Pass requires score>=8, zero issues and every listed object reviewed. '
                'Include scene-wide lighting/material defects even in this object subset. Layout: '+json.dumps(objects))
        response=asyncio.run(review_vision(images,prompt,timeout_seconds=getattr(self,'review_timeout',600)))
        if not isinstance(response,dict):raise RuntimeError('Visual review returned invalid JSON')
        valid={Path(p).name for p in images}
        for row in response.get('objects',[]):
            if isinstance(row,dict):
                views=row.get('views')
                if not isinstance(views,list) or not views or not all(v in valid for v in views):row['observed']=False
        return response
    def repair(self,ctx,path,options):
        from litereality_agent.agent import providers
        harness=providers.resolve('author',options.get('provider','codex'))
        spec=providers.SessionSpec(cwd=ctx.authored_room,read_roots=(ctx.capture_dir,path.parent),
            capability_scene=ctx.authored_room,capability_tools=('render','select_views','check_collisions'),
            model='gpt-6-astra',step_budget=40,timeout_seconds=600,max_turns=50,
            prompt=f'''Read {path}. Work ONLY on its selected_targets (at most three concrete defects).
Use its assessment and original capture evidence. Geometry/support first, then lighting and large appearance.
Missing evidence is NOT an instruction to invent or edit an object. Do not broaden this batch.
Preserve manifest identities, merged members, neural meshes, articulation, and unrelated objects.
Do not weaken checks, delete objects, alter reports, add invisible supports or launch another model.
Save valid Room.py/object.py edits frequently. Stop early when these targets are addressed.
A separate supervisor rebuilds and independently reviews all objects. Budget 40 tools / 600 seconds.
Report corrected targets and remaining uncertainty.''')
        async def perform():
            result=None
            async for event in harness.run(spec):
                if isinstance(event,providers.SessionResult):result=event
            if result is None or result.is_error or result.stopped:
                raise RuntimeError('repair incomplete: '+str(getattr(result,'stopped','') or getattr(result,'result','no result')))
        asyncio.run(perform())


async def review_vision(images,prompt,*,timeout_seconds=600):
    """Same read-only quality role and image contract; explicit per-group budget."""
    from litereality_agent.agent import providers
    from litereality_agent.agent.tools.shared import config
    request='First Read every image, then answer.\n'+'\n'.join(
        f'- IMAGE {i+1}: {path}' for i,path in enumerate(images))+'\n\n'+prompt
    request+='\n\nReply with only JSON.\nRead-only review: do not edit files or call another model.'
    spec=providers.SessionSpec(prompt=request,cwd=Path(config.ROOT),
        read_roots=tuple(Path(p).resolve().parent for p in images),file_tools=('Read',),setting_sources=(),
        model=config.MODEL,read_only=True,max_turns=max(8,len(images)+4),
        step_budget=max(14,len(images)+4),timeout_seconds=timeout_seconds)
    output=None
    async for message in providers.resolve('quality').run(spec):
        if isinstance(message,providers.SessionResult):
            if message.is_error or message.stopped:
                raise RuntimeError('visual review incomplete: '+str(message.stopped or message.result))
            output=message.result
    if not output or not output.strip():raise RuntimeError('visual review returned no completed result')
    try:return json.loads(output)
    except json.JSONDecodeError:
        match=re.search(r'\{.*\}|\[.*\]',output,re.S)
        if not match:raise ValueError('visual review did not return JSON')
        return json.loads(match.group(0))


def assess(ctx,preview,evidence,backend,previous_frames=()):
    evidence.mkdir(parents=True,exist_ok=True)
    fp=backend.fingerprint(ctx,preview)
    evaluation=read(evidence/'evaluation.json')
    binding=checkpoints.identity(ctx,preview,backend)
    if evaluation and evaluation['identity']!=binding:
        raise ValueError('Review source, build, captures or policy changed; checkpoint retained')
    if not evaluation:
        evaluation={'schema':1,'identity':binding};save(evidence/'evaluation.json',evaluation)
    report={'schema':'support-first/2','accepted':False,'status':'needs_repair','fingerprint':fp,
            'instances_preserved':backend.preserved(ctx)}
    geometry=backend.geometry(ctx,preview);save(evidence/'validation.json',geometry)
    report.update(support_pass=geometry.get('pass') is True,validation=str(evidence/'validation.json'))
    if not report['instances_preserved']:
        raise RuntimeError('Author changed immutable instance identities or source neural meshes')
    if geometry.get('ok') is False or any(x.get('kind') in ('strict_check_unavailable','mesh_check_unavailable','mesh_check_failed','articulated_unchecked') for x in geometry.get('failing',[]) if isinstance(x,dict)):
        raise RuntimeError('Required geometry check unavailable: '+str(evidence/'validation.json'))
    if not report['support_pass']:
        # Geometry check data is retained verbatim. Only structured concrete object
        # findings can become automatic edit targets; ambiguous failures need inspection.
        issues=[]
        for row in geometry.get('failing',geometry.get('issues',[])):
            if not isinstance(row,dict):continue
            names=row.get('objects') or [row[k] for k in ('id','a','b','object') if isinstance(row.get(k),str)]
            if names:
                issues.append({'kind':'geometry','objects':names,'priority':'support','detail':str(row)})
        report['visual_review']={'score':0,'pass':False,'all_objects_reviewed':False,'issues':issues or [
            {'kind':'unknown','objects':[],'priority':'support','detail':'Geometry validation failed; inspect validation.json.'}],'frames':[]}
        return report
    objects=read(preview/'room_layout.json')['objects']
    if not isinstance(objects,list) or not objects or any(not isinstance(o,dict) or not isinstance(o.get('id'),str) for o in objects):
        raise RuntimeError('Review layout contains no complete object ID list')
    ids=[o['id'] for o in objects]
    if len(set(ids))!=len(ids):raise RuntimeError('Duplicate layout object IDs')
    available=[int(p.stem.split('_')[-1]) for p in ctx.capture_dir.glob('frame_*.jpg') if p.with_suffix('.json').is_file()]
    if len(available)<3:raise RuntimeError('At least three paired capture cameras are required')
    frames=sorted(set(previous_frames)&set(available))
    review=None
    for cap in (6,12,24):
        if len(frames)>cap:continue
        frames=select_frames(available,cap,frames)
        folder=evidence/('views_'+str(len(frames)))
        rendered=read(folder/'render_receipt.json')
        if rendered:
            pairs=rendered['pairs']
            if rendered['binding']!=binding or rendered['frames']!=frames or rendered['hashes']!=checkpoints.files(pairs):
                raise ValueError('Rendered images or review inputs changed; checkpoint retained')
        else:
            if evaluation.get('legacy_import') and list(folder.glob('review_*.json')):
                pairs=list(map(str,sorted((folder/'compare/pairs').glob('pair_*.png'))))
                if {int(Path(p).stem.split('_')[-1]) for p in pairs}!=set(frames):
                    raise ValueError('Legacy rendered camera pairs incomplete; no completed reports discarded')
            else:pairs=backend.render(ctx,folder,frames)
            save(folder/'render_receipt.json',{'binding':binding,'frames':frames,'pairs':pairs,'hashes':checkpoints.files(pairs)})
        chunks=checkpoints.Groups(ctx,folder,objects,pairs,binding,backend,save,read,normalize_review,
                                  import_legacy=bool(evaluation.get('legacy_import'))).run()
        review={'pass':all(r['pass'] is True and r['score']>=8 and r['all_objects_reviewed'] is True and not r['issues'] for r in chunks),'score':min(r['score'] for r in chunks),
                'all_objects_reviewed':all(r['all_objects_reviewed'] for r in chunks),
                'issues':[i for r in chunks for i in r['issues']],
                'frames':frames,'object_count':len(objects),'review_chunks':len(chunks)}
        save(folder/'visual_review.json',review)
        if not any(i['kind']=='evidence' for i in review['issues']) or len(frames)==min(24,len(available)):break
    if review is None:raise RuntimeError('Stored camera selection exceeds policy limits')
    save(evidence/'visual_review.json',review)
    report.update(visual_review=review,review=str(evidence/'visual_review.json'),visual_pass=review['pass'])
    if checkpoints.identity(ctx,preview,backend)!=binding:raise RuntimeError('Scene changed during independent review')
    report.update(accepted=review['pass'],status='accepted' if review['pass'] else 'needs_repair')
    progress_path=ctx.authoring_root/'closeout/review_progress.json'
    progress=read(progress_path,{})
    progress.update(status='complete',current_group=None,error_kind=None);save(progress_path,progress)
    return report


def run(ctx,options,*,repair=False,backend=None):
    backend=backend or Backend()
    root=ctx.authoring_root/'closeout';root.mkdir(parents=True,exist_ok=True)
    backend.review_timeout=checkpoints.timeout_policy(root,read)
    ledger_path=root/'control.json'
    ledger=read(ledger_path,{'schema':1,'batches':0,'stalled':0,'frames':[],'history':[],'pending':None})
    preview=ctx.authoring_root/'room_preview'
    # Durable phase-entry marker even if the first build or model review fails.
    if not ledger_path.exists():save(ledger_path,ledger)
    operation=options.get('_operation_id')
    committed=ledger.get('operations',{}).get(operation) if operation else None
    if committed:
        cached=read(committed)
        if cached.get('fingerprint')!=backend.fingerprint(ctx,preview):
            raise ValueError('Completed Author operation source/build changed; refusing replay')
        evaluation=read(Path(committed).parent/'evaluation.json')
        if evaluation and evaluation.get('identity')!=checkpoints.identity(ctx,preview,backend):
            raise ValueError('Completed Author review inputs changed; refusing stale acceptance')
        return cached
    previous=read(ledger.get('last_report',''),{}) if ledger.get('last_report') else {}
    pending=ledger.get('pending')
    if repair and not pending:
        if ledger.get('stop_reason') or not previous or not repair_targets(previous.get('visual_review',{}).get('issues',[])):
            raise ValueError('Repair is not allowed: no actionable targets or closeout has ended')
        ledger.pop('active_review',None)
        ledger['batches']+=1
        pending={'batch':ledger['batches'],'termination':'interrupted','started':True}
        ledger['pending']=pending;save(ledger_path,ledger)
        plan=root/f'batch_{ledger["batches"]:02d}.json'
        save(plan,{'assessment':ledger['last_report'],'selected_targets':repair_targets(previous['visual_review']['issues']),
                   'limits':{'tools':40,'seconds':600},'evidence_only':[i for i in previous['visual_review']['issues'] if i['kind']=='evidence']})
        try:backend.repair(ctx,plan,options);pending['termination']='completed'
        except Exception as exc:
            message=str(exc).lower()
            if 'step budget' in message:pending['termination']='step_budget'
            elif 'wall time budget' in message or isinstance(exc,TimeoutError):pending['termination']='timeout'
            else:
                pending.update(termination='execution_error',error=str(exc));save(ledger_path,ledger);raise
        save(ledger_path,ledger)
    # A restart while pending never calls the repair model again; it rebuilds the
    # edits that actually survived. Failed reviews keep pending for another retry.
    active=ledger.get('active_review')
    if not active:
        orphans=[]
        for path in (root/'reviews').glob('attempt-*/evaluation.json'):
            candidate=read(path,{})
            if 'operation' in candidate and candidate['operation']==operation and str(path.parent/'assessment.json') not in ledger['history']:
                orphans.append(path.parent)
        if len(orphans)>1:raise ValueError('Ambiguous orphan review checkpoints')
        if orphans:
            active={'operation':operation,'evidence':str(orphans[0])}
            ledger['active_review']=active;save(ledger_path,ledger)
    if active:
        if active.get('operation')!=operation:raise ValueError('Unfinished review belongs to a different operation')
        evidence=Path(active['evidence'])
        if evidence.parent.resolve()!=(root/'reviews').resolve():raise ValueError('Invalid active review path')
        if read(evidence/'evaluation.json',{}).get('identity')!=checkpoints.identity(ctx,preview,backend):
            raise ValueError('Review source, build, captures or policy changed; saved reports retained')
    else:
        migration=checkpoints.legacy_attempt(ctx,preview,root,operation,backend,read)
        if migration:
            evidence,provenance=migration
            save(evidence/'evaluation.json',{'schema':1,'operation':operation,'identity':checkpoints.identity(ctx,preview,backend),
                 'legacy_import':{'version':'0.4.5','provenance':provenance,
                                  'verification':'Copied sources and renderer layout matched; hashes bound at migration.'}})
        else:
            backend.build(ctx,preview)
            evidence=root/'reviews'/('attempt-'+uuid.uuid4().hex);evidence.mkdir(parents=True)
            shutil.copytree(ctx.authored_room,evidence/'room',symlinks=True)
            save(evidence/'evaluation.json',{'schema':1,'operation':operation,'identity':checkpoints.identity(ctx,preview,backend)})
        ledger['active_review']={'operation':operation,'evidence':str(evidence)};save(ledger_path,ledger)
    print('REVIEW_CHECKPOINT: '+str(evidence),flush=True)
    try:
        report=assess(ctx,preview,evidence,backend,ledger['frames'])
    except Exception as exc:
        progress_path=root/'review_progress.json'
        progress=read(progress_path,{})
        progress.update(status='incomplete',error_kind='timeout' if isinstance(exc,TimeoutError) or 'wall time budget' in str(exc).lower() else 'execution_error')
        save(progress_path,progress)
        raise
    for name in ('Room.glb','Room.blend','room_layout.json','manifest.json'):
        source=preview/name
        if source.is_file():shutil.copy2(source,evidence/name)
    report_path=evidence/'assessment.json'
    if pending:
        comparable=previous.get('visual_review',{}).get('frames')==report.get('visual_review',{}).get('frames')
        ledger['stalled']=0 if improved(previous,report) or not comparable else ledger['stalled']+1
    if not ledger.get('best_candidate') or improved(read(ledger['best_report']),report):
        ledger.update(best_candidate=str(evidence),best_report=str(report_path))
    review=report.get('visual_review',{})
    ledger['frames']=review.get('frames') or ledger['frames']
    reason=('quality_passed' if report['accepted'] else
            'repair_budget_exhausted' if ledger['batches']>=MAX_BATCHES else
            'no_progress' if ledger['stalled']>=MAX_STALLED else
            ('evidence_insufficient' if any(i['kind']=='evidence' for i in review.get('issues',[])) else 'no_actionable_issues') if not repair_targets(review.get('issues',[])) else None)
    ledger.update(last_report=str(report_path),stop_reason=reason,
                  repair_termination=pending['termination'] if pending else ledger.get('repair_termination','not_started'),pending=None)
    ledger['history'].append(str(report_path))
    report['closeout']={k:ledger[k] for k in ('batches','stalled','stop_reason','repair_termination','best_candidate','frames')}
    save(report_path,report);save(preview/'author_acceptance.json',report)
    if operation:ledger.setdefault('operations',{})[operation]=str(report_path)
    ledger.pop('active_review',None)
    save(ledger_path,ledger)
    return report


def run_publish(ctx,options,implementation):
    """Journal upstream publish phases without changing its checks or bake logic.

    Called in the isolated stage subprocess. The upstream function is still the
    implementation; only successful expensive boundaries and its review directory
    are made durable. Every re-entry verifies the latest source/build/capture.
    """
    from types import SimpleNamespace
    from litereality_agent.pipeline.room_qc import publish
    from litereality_agent.room_ops import api
    backend=Backend();root=ctx.authoring_root/'closeout';root.mkdir(parents=True,exist_ok=True)
    backend.review_timeout=checkpoints.timeout_policy(root,read)
    preview=ctx.authoring_root/'room_preview';path=root/'publish_control.json'
    state=read(path);operation=options.get('_operation_id')
    current=checkpoints.identity(ctx,preview,backend)
    if state:
        if state.get('operation')!=operation or state.get('identity')!=current:
            raise ValueError('Final publication source/build/capture or operation changed; saved checkpoints retained')
    else:
        evidence=ctx.authoring_root/('publish-review-'+uuid.uuid4().hex);evidence.mkdir()
        state={'schema':1,'operation':operation,'identity':current,'evidence':str(evidence),'calls':{}}
        save(path,state)
    evidence=Path(state['evidence'])
    if evidence.parent.resolve()!=ctx.authoring_root.resolve():raise ValueError('Invalid publish review checkpoint path')
    original_module,original_bake,original_temp=publish.run_module,api.bake_room,publish.tempfile
    def boundary(key,arguments,call,record_failure=False):
        signature=checkpoints.digest(arguments)
        stored=state['calls'].get(key)
        if stored:
            if stored['signature']!=signature:raise ValueError('Publish phase arguments changed: '+key)
            return stored['result']
        result=call()
        rc=result[0] if isinstance(result,(tuple,list)) else result
        if rc==0 or record_failure:
            serial_result=[str(v) if isinstance(v,Path) else v for v in result] if isinstance(result,(tuple,list)) else result
            state['calls'][key]={'signature':signature,'result':serial_result}
            state['identity']=checkpoints.identity(ctx,preview,backend);save(path,state)
        return result
    def module(context,name,args,*,log_name):
        return boundary(log_name,[name,list(map(str,args))],
            lambda:original_module(context,name,args,log_name=log_name),
            record_failure=log_name in ('publish_collision','publish_quality'))
    def bake(blend,glb):
        return boundary('bake',[str(blend),str(glb)],lambda:original_bake(blend,glb))
    publish.run_module=module;api.bake_room=bake
    publish.tempfile=SimpleNamespace(mkdtemp=lambda **kwargs:str(evidence))
    try:
        return implementation.run(ctx,options)
    except Exception as exc:
        progress=read(root/'review_progress.json',{})
        progress.update(status='incomplete',error_kind='timeout' if isinstance(exc,TimeoutError) or 'wall time budget' in str(exc).lower() else 'execution_error')
        save(root/'review_progress.json',progress)
        raise
    finally:
        publish.run_module,api.bake_room,publish.tempfile=original_module,original_bake,original_temp


@contextmanager
def install():
    """Scoped adapter, never edits/repackages pinned backend files."""
    from litereality_agent.pipeline.realism_authoring import acceptance
    original_finish,original_assess=acceptance.finish,acceptance.assess
    acceptance.finish=lambda ctx,options:run(ctx,options)
    # Final publish gets the same strict, coverage-aware review; no repair here.
    def published_assess(ctx,preview,evidence):
        ledger=read(ctx.authoring_root/'closeout/control.json',{})
        backend=Backend()
        backend.review_timeout=checkpoints.timeout_policy(ctx.authoring_root/'closeout',read)
        report=assess(ctx,preview,evidence,backend,ledger.get('frames',[]))
        save(evidence/'assessment.json',report)
        return report
    acceptance.assess=published_assess
    try:yield
    finally:acceptance.finish,acceptance.assess=original_finish,original_assess
