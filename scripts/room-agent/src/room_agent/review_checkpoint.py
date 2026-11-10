"""Content-bound review receipts. No model, Blender or graph dependency."""
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

SEMANTIC_VERSION = 'all-object-paired-review/1'


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def files(paths):
    return {str(p):file_hash(p) for p in sorted(map(Path,paths))}


def timeout_policy(root,read):
    policy=read(root/'review_policy.json',{})
    if not isinstance(policy,dict) or set(policy)-{'timeout_seconds'}:
        raise ValueError('Unsupported review policy; acceptance policy cannot be overridden')
    seconds=policy.get('timeout_seconds',600)
    if type(seconds) is not int or not 120<=seconds<=1800:
        raise ValueError('Review timeout policy must be an integer in 120..1800 seconds')
    return seconds


def identity(ctx,preview,backend):
    captures=sorted(p for p in ctx.capture_dir.glob('frame_*') if p.suffix in ('.jpg','.json'))
    return {'fingerprint':backend.fingerprint(ctx,preview),
            'build_files':files(p for p in preview.glob('*') if p.name in ('Room.glb','Room.blend','room_layout.json','manifest.json')),
            'capture':files(captures),'semantic_version':SEMANTIC_VERSION,
            'reviewer':backend.review_identity() if hasattr(backend,'review_identity') else {'test_backend':True}}


def valid_report(report,ids,*,complete_rows=False):
    if not isinstance(report,dict):raise ValueError('Invalid review checkpoint report')
    if type(report.get('pass')) is not bool or type(report.get('all_objects_reviewed')) is not bool:
        raise ValueError('Invalid review checkpoint boolean verdict')
    score=report.get('score');rows=report.get('objects');issues=report.get('issues')
    if type(score) not in (float,int) or not math.isfinite(score) or not 0<=score<=10 or not isinstance(rows,list) or not isinstance(issues,list):
        raise ValueError('Invalid review checkpoint score/rows/issues')
    row_ids=[r.get('id') for r in rows if isinstance(r,dict)]
    if len(row_ids)!=len(rows) or len(set(row_ids))!=len(rows) or set(row_ids)-set(ids) or (complete_rows and set(row_ids)!=set(ids)):
        raise ValueError('Invalid review checkpoint object coverage')
    if any(not isinstance(i,dict) or not isinstance(i.get('objects'),list) for i in issues):
        raise ValueError('Invalid review checkpoint issues')
    if report.get('pass') is True and (score<8 or issues or report.get('all_objects_reviewed') is not True or
            {r['id'] for r in rows if r.get('observed') is True}!=set(ids)):
        raise ValueError('Review checkpoint bypassed strict acceptance')
    return report


def legacy_attempt(ctx,preview,root,operation,backend,read):
    """Conservative one-time v0.4.5 migration; no fabricated historical hashes.

    Requires an original matching failed operation, unmodified copied source and
    matching renderer layout. Newly recorded hashes bind subsequent resumes.
    Ambiguity fails closed, preserving all files for inspection.
    """
    candidates=[p for p in (root/'reviews').glob('attempt-*')
                if not (p/'assessment.json').exists() and not (p/'evaluation.json').exists()
                and list(p.glob('views_*/review_*.json'))]
    if not candidates:return None
    # A completed older review is history; only one uncommitted legacy candidate is safe.
    if len(candidates)!=1:raise ValueError('Ambiguous legacy review checkpoints; no reports discarded')
    candidate=candidates[0]
    run_dir=ctx.authoring_root.parents[3]
    provenance=[]
    for p in (run_dir/'work/attempts/author').glob('*/request.json'):
        payload=read(p,{})
        failure=read(p.parent/'stage_failure.json',{})
        if (operation and payload.get('operation_id')==operation and payload.get('stage')=='author'
            and 'room-agent-stage3a-v0.4.5' in Path(payload.get('legacy','')).parts
            and 'visual review incomplete: wall time budget' in failure.get('message','')):
            if hasattr(backend,'review_identity'):
                reviewer=backend.review_identity()
                expected_model=payload.get('request',{}).get('environment',{}).get('LR_CODEX_MODEL')
                if reviewer.get('model')!=expected_model or reviewer.get('provider')!='codex':
                    raise ValueError('Legacy reviewer policy changed; reports not reused')
            provenance.append(str(p))
    if not provenance:raise ValueError('Legacy review has no matching v0.4.5 timeout operation; refusing unverified reuse')
    original=backend.fingerprint(ctx,preview)
    snapshot=backend.fingerprint(SimpleNamespace(authored_room=candidate/'room'),preview)
    if original.get('source')!=snapshot.get('source'):
        raise ValueError('Legacy reviewed source changed; refusing stale reports')
    layout=read(preview/'room_layout.json')
    for folder in candidate.glob('views_*'):
        if not list(folder.glob('review_*.json')):continue
        if read(folder/'compare/renders/room_layout.json')!=layout:
            raise ValueError('Legacy render layout changed; refusing stale reports')
        if not read(candidate/'validation.json',{}).get('pass'):
            raise ValueError('Legacy geometry validation not complete')
    return candidate,provenance


class Groups:
    def __init__(self,ctx,folder,objects,pairs,binding,backend,save,read,normalize,*,import_legacy=False):
        self.ctx,self.folder,self.objects,self.pairs=ctx,folder,objects,pairs
        self.backend,self.save,self.read,self.normalize=backend,save,read,normalize
        self.binding=binding;self.images=files(pairs);self.path=folder/'groups.json'
        self.progress=ctx.authoring_root/'closeout/review_progress.json'
        self.timeout=getattr(backend,'review_timeout',600)
        self.plan=read(self.path)
        if self.plan is None:
            groups=[];offset=0
            if import_legacy:
                reports=sorted(folder.glob('review_*.json'))
                for n,path in enumerate(reports):
                    if path.name!=f'review_{n:03}.json':raise ValueError('Legacy review groups are not a contiguous prefix')
                    subset=objects[offset:offset+20];ids=[o['id'] for o in subset]
                    report=valid_report(read(path),ids,complete_rows=True)
                    known={Path(p).name for p in pairs}
                    if any(r.get('observed') is True and (not r.get('views') or not set(r['views'])<=known) for r in report['objects']):
                        raise ValueError('Legacy review cited unavailable camera evidence')
                    group={'key':f'group_{n:03}','start':offset,'end':offset+len(subset),'imported':True}
                    self.commit(group,report,legacy_file=str(path))
                    groups.append(group);offset+=len(subset)
            for start in range(offset,len(objects),10):
                groups.append({'key':f'group_{len(groups):03}','start':start,'end':min(start+10,len(objects))})
            self.plan={'schema':1,'binding':binding,'images':self.images,'groups':groups}
            save(self.path,self.plan)
        if self.plan.get('binding')!=binding or self.plan.get('images')!=self.images:
            raise ValueError('Review inputs or rendered images changed; saved groups not reused')
        covered=[i for g in self.plan['groups'] for i in range(g['start'],g['end'])]
        if covered!=list(range(len(objects))):raise ValueError('Invalid review checkpoint partition')

    def key(self,group):
        return digest({'binding':self.binding,'images':self.images,'objects':self.objects[group['start']:group['end']]})

    def commit(self,group,report,**extra):
        self.save(self.folder/(group['key']+'.json'),{'schema':1,'input':self.key(group),'report':report,'report_sha256':digest(report),**extra})

    def cached(self,group):
        receipt=self.read(self.folder/(group['key']+'.json'))
        if receipt is None:return None
        if receipt.get('input')!=self.key(group):raise ValueError('Review checkpoint inputs changed')
        if receipt.get('report_sha256')!=digest(receipt.get('report')):raise ValueError('Review checkpoint report changed')
        return valid_report(receipt.get('report'),[o['id'] for o in self.objects[group['start']:group['end']]])

    def update(self,status,current=None,error=None):
        completed=[g for g in self.plan['groups'] if self.cached(g) is not None]
        value={'status':status,'completed_groups':len(completed),'total_groups':len(self.plan['groups']),
               'completed_objects':sum(g['end']-g['start'] for g in completed),'total_objects':len(self.objects),
               'current_group':current,'error_kind':error,'timeout_seconds':self.timeout,
               'imported_groups':sum(bool(g.get('imported')) for g in completed)}
        self.save(self.progress,value)
        print(f"REVIEW_PROGRESS {status}: {value['completed_groups']}/{value['total_groups']} groups, "
              f"{value['completed_objects']}/{value['total_objects']} objects; imported={value['imported_groups']}",flush=True)

    def run(self):
        index=0;self.update('running')
        while index<len(self.plan['groups']):
            group=self.plan['groups'][index]
            if self.cached(group) is not None:index+=1;continue
            self.update('running',group['key'])
            group['attempts']=group.get('attempts',0)+1;group['state']='running';self.save(self.path,self.plan)
            try:
                if files(self.pairs)!=self.images:raise ValueError('Rendered images changed during review')
                subset=self.objects[group['start']:group['end']]
                report=self.normalize(self.backend.review(self.pairs,subset),[o['id'] for o in subset])
                if files(self.pairs)!=self.images:raise ValueError('Rendered images changed during review')
                valid_report(report,[o['id'] for o in subset])
                self.commit(group,report)
            except Exception as exc:
                timeout=isinstance(exc,TimeoutError) or 'wall time budget' in str(exc).lower() or 'timed out' in str(exc).lower()
                group.update(state='incomplete',error_kind='timeout' if timeout else 'execution_error',error=str(exc))
                if timeout and group['end']-group['start']>=10:
                    mid=(group['start']+group['end'])//2
                    self.plan.setdefault('split_history',[]).append(dict(group))
                    self.plan['groups'][index:index+1]=[
                        {'key':group['key']+'a','start':group['start'],'end':mid},
                        {'key':group['key']+'b','start':mid,'end':group['end']}]
                    self.save(self.path,self.plan);continue
                self.save(self.path,self.plan);self.update('incomplete',group['key'],group['error_kind']);raise
            group['state']='complete';self.save(self.path,self.plan)
            self.update('running');index+=1
        self.update('running')
        return [self.cached(g) for g in self.plan['groups']]
