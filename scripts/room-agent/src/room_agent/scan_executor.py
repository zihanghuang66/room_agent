"""Isolated scan stage execution, immutable publication and replay verification."""
import os
import shutil
from pathlib import Path
from uuid import uuid4
from . import full_scan
from .storage import atomic_json,read_json,digest
from .saved_scene import file_hash
from .scan_artifacts import inventory,copy_scene,restore_scene,presentation


def engine_hash():
    root=Path(__file__).parent
    return digest({p.name:file_hash(p) for p in sorted(root.glob('*.py'))})


class ScanExecutor:
    def __init__(self,request,directory,journal,runner=None):
        self.request=request;self.directory=Path(directory);self.journal=journal
        self.runner=runner or full_scan.run_backend
        self.folder=self.directory/'backend'
        self.published=self.directory/'published'

    def verify_stage(self,path):
        m=read_json(path/'manifest.json')
        if inventory(path/'scenes',self.folder)!=m['scene_files']:raise ValueError('Stage snapshot integrity failed: '+path.name)
        for rel,h in m.get('dependencies',{}).items():
            p=self.folder/rel
            if p.is_symlink() or not p.is_file() or file_hash(p)!=h:raise ValueError('Stage dependency integrity failed: '+rel)
        for rel,h in m['files'].items():
            p=path/rel
            if p.is_symlink() or not p.is_file() or file_hash(p)!=h:raise ValueError('Published artifact integrity failed: '+rel)
        return read_json(path/'receipt.json')

    def verify(self, state=None):
        from .scan_graph import STAGES
        if state and state.get('last_stage'):
            for name in STAGES[:STAGES.index(state['last_stage'])+1]:
                if not (self.published/name/'receipt.json').is_file():
                    raise ValueError('Stage snapshot integrity: missing '+name)
        full_scan.verify(self.request)
        from .engine_compatibility import verify_engine
        verify_engine(self.request['engine_sha256'],engine_hash(),'full_scan')
        for path in self.published.glob('*'):
            if path.name.startswith('.'):continue
            receipt=self.verify_stage(path)
            if receipt['request_sha256']!=digest(self.request):raise ValueError('Stage request integrity mismatch')
        prepare=self.published/'prepare'/'input_manifest.json'
        if prepare.exists():
            if inventory(self.folder/'input',self.folder)!=read_json(prepare):raise ValueError('Prepared capture integrity changed')

    def step(self,name,state):
        self.verify(state);target=self.published/('author' if name=='author_repair' else name)
        fingerprint=digest({'request':self.request,'stage':name,'state':state})
        quality_receipt=self.directory/'work/quality_receipts'/fingerprint/'receipt.json'
        if quality_receipt.is_file():
            cached=read_json(quality_receipt)
            if cached['workspace']!=inventory(self.folder/'scenes',self.folder):raise ValueError('Quality receipt workspace integrity changed')
            self.journal.emit('quality_receipt_reused',node=name)
            return cached['result']
        if target.exists():
            receipt=self.verify_stage(target)
            if receipt['input_sha256']!=fingerprint:raise ValueError('Stage input fingerprint differs: '+name)
            self.journal.emit('artifact_reused',node=name)
            return receipt['result']
        self.folder.mkdir(parents=True,exist_ok=True)
        if name == 'prepare' and (self.folder/'input').exists():
            history=self.directory/'work/failed/prepare'
            history.mkdir(parents=True,exist_ok=True)
            (self.folder/'input').rename(history/('input-'+uuid4().hex))
        previous=self.published/state['last_stage']/'scenes' if state.get('last_stage') else None
        # An attempted stage owns saved edits. Only first execution starts from the
        # previous publication; retry preserves and checkpoints the current workspace.
        prior=[]
        for path in (self.directory/'work/attempts'/name).glob('*/request.json'):
            if path.is_symlink() or path.parent.is_symlink():
                raise ValueError('Previous stage attempt must not be a symlink')
            data=read_json(path)
            if data.get('request')!=self.request or data.get('stage')!=name or Path(data.get('folder','')).absolute()!=self.folder.absolute():
                raise ValueError('Previous stage attempt identity mismatch: '+name)
            prior.append(path)
        retained=(bool(prior) and name!='prepare') or name in ('author_repair','draft_export')
        resume_mode='stage_retry'
        if retained:
            scene=self.folder/'scenes'
            if not scene.is_dir() or scene.is_symlink():
                raise ValueError('Saved stage workspace is missing; refusing automatic rollback: '+name)
            author_root=scene/self.request['upstream_scene']/'realism_authoring'
            if name in ('author','author_repair'):
                room=author_root/'room/Room.py'
                has_saved_room=room.parent.exists() or (author_root/'room_preview/author_acceptance.json').exists()
                if has_saved_room and (not room.is_file() or room.is_symlink() or room.parent.is_symlink()):
                    raise ValueError('Saved Author source is missing; refusing automatic rollback to Seed')
                # If the first attempt failed before creating a room (e.g. evidence
                # service unavailable), re-enter Author on the retained workspace.
                # Once acceptance was reached, resume at rebuild/review, not the
                # initial authoring, object-refinement and materials passes.
                if (author_root/'room_preview/author_acceptance.json').is_file() or (author_root/'closeout/control.json').is_file():
                    resume_mode='author_revalidate'
            checkpoint=self.directory/'work/failed'/name/('before-resume-'+uuid4().hex)/'scenes'
            files=copy_scene(scene,checkpoint,self.folder)
            atomic_json(checkpoint.parent/'checkpoint.json',{'request_sha256':digest(self.request),'stage':name,'scene_files':files,'resume_mode':resume_mode})
            self.journal.emit('saved_workspace_retained',node=name,resume_mode=resume_mode,checkpoint=str(checkpoint.relative_to(self.directory)))
        else:
            restore_scene(previous,self.folder,self.directory/'work/failed'/name)
        attempt=self.directory/'work/attempts'/name/uuid4().hex;attempt.mkdir(parents=True)
        payload={'request':self.request,'stage':name,'folder':str(self.folder),'attempt':str(attempt),'legacy':str(full_scan.full_root()),
                 'resumed_from_saved_workspace':retained,'resume_mode':resume_mode,'operation_id':fingerprint,'expected_review':state.get('author_review')}
        payload_path=attempt/'request.json';atomic_json(payload_path,payload)
        env={k:v for k,v in os.environ.items() if not k.startswith(('ROOM_','LR_','LITEREALITY_','HARNESS_'))}
        for k in ('CUDA_VISIBLE_DEVICES','PYTHONPATH','PYTHONHOME'):env.pop(k,None)
        env.update(self.request['environment'],PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1')
        log=self.directory/'logs'/(name+'.log');log.parent.mkdir(exist_ok=True)
        command=[self.request['environment']['ROOM_APP_PYTHON'],'-u',str(Path(__file__).with_name('scan_driver.py')),str(payload_path)]
        seconds={'prepare':1800,'preflight':28800,'export_check':1200}.get(name,43200)
        self.runner(command,log,seconds=seconds,env=env,pass_fds=getattr(self,'lock_fds',()))
        output=read_json(attempt/'stage_result.json')
        self.verify()
        update=output.get('state',{})
        review=update.get('author_review')
        if name=='draft_export' or review is not None and review.get('accepted') is not True:
            result={k:update[k] for k in ('stage_detail','author_review','technical_export_verified') if k in update}
            atomic_json(quality_receipt,{'result':result,'workspace':inventory(self.folder/'scenes',self.folder)})
            self.journal.emit('quality_review_saved',node=name,accepted=False)
            return result
        temporary=self.published/('.'+name+'-'+uuid4().hex);temporary.mkdir(parents=True)
        scene_files=copy_scene(self.folder/'scenes',temporary/'scenes',self.folder)
        rows=presentation(self.folder/'scenes',temporary/'display',name)
        result={'stage_detail':output.get('state',{}).get('stage_detail',{}),'artifact_path':str(target),'revision_id':self.request['run_id']}
        if review is not None:result['author_review']=review
        if 'technical_export_verified' in update:result['technical_export_verified']=update['technical_export_verified']
        atomic_json(temporary/'summary.json',output)
        atomic_json(temporary/'previews.json',rows)
        if name=='prepare':atomic_json(temporary/'input_manifest.json',inventory(self.folder/'input',self.folder))
        receipt={'request_sha256':digest(self.request),'input_sha256':fingerprint,'result':result}
        atomic_json(temporary/'receipt.json',receipt)
        files={p.relative_to(temporary).as_posix():file_hash(p) for p in temporary.rglob('*') if p.is_file() and not p.is_symlink() and 'scenes' not in p.relative_to(temporary).parts}
        dependencies = {'prepare': ['input.json'], 'preflight': ['checks/runtime_environment.json']}.get(name, [])
        atomic_json(temporary/'manifest.json',{'scene_files':scene_files,'files':files,
                    'dependencies':{rel:file_hash(self.folder/rel) for rel in dependencies}})
        temporary.rename(target)
        self.journal.emit('stage_artifacts_published',node=name,preview_count=len(rows))
        return result

    def project(self,state,events,status):
        from .scan_graph import STAGES, EXTRA_STAGES
        stages=[];previews=[];exports=[]
        displayed=list(STAGES)
        if any(e.get('node') in EXTRA_STAGES for e in events) or state.get('author_review'):
            displayed[6:6]=['author_decision','author_repair']
            if any(e.get('node')=='draft_export' for e in events):displayed.append('draft_export')
        for name in displayed:
            event=next((e for e in reversed(events) if e.get('node')==name and e['kind'] in ('node_started','node_completed','node_failed')), {})
            value={'node_started':'running','node_completed':'completed','node_failed':'failed'}.get(event.get('kind'),'pending')
            if name=='author_decision' and status=='waiting_for_input':value='waiting_for_input'
            if status=='completed' and value=='pending':value='skipped'
            if status in ('failed','interrupted') and value=='running':value=status
            stages.append({'node':name,'status':value,'seconds':event.get('seconds')})
            path=self.published/name/'previews.json'
            if path.exists():
                for row in read_json(path):
                    entry={**row,'path':str((path.parent/'display'/row['relative']).relative_to(self.directory))}
                    (previews if row['kind']=='image' else exports).append(entry)
        node=next((e.get('node') for e in reversed(events) if e['kind']=='node_started'),None)
        progress={}
        progress_path=self.folder/'scenes'/self.request['upstream_scene']/'realism_authoring/closeout/review_progress.json'
        if progress_path.is_file() and not progress_path.is_symlink():
            try:
                candidate=read_json(progress_path)
                if isinstance(candidate,dict) and candidate.get('status') in ('running','incomplete','complete'):
                    progress=dict(candidate)
                    if status in ('failed','interrupted') and progress['status']=='running':
                        progress.update(status='incomplete',error_kind='execution_error')
            except (OSError,ValueError):pass
        return {'review_progress':progress,'stages':stages,'previews':previews,'exports':exports,'current_node':'finished' if status=='completed' else 'author_decision' if status=='waiting_for_input' else node,
                'legacy_adapter':False,'orchestrator':'langgraph','prepare_only':self.request['prepare_only'],
                'completion_reason':state.get('completion_reason'),'full_run_completed':status=='completed' and state.get('last_stage')=='export_check',
                'human_visual_reviewed':False,'source_run':str(self.folder),
                'allowed_actions':['repair','finish'] if status=='waiting_for_input' else [],
                **{k:state.get(k) for k in ('round','score','issues','repair_termination','best_candidate','technical_export_verified')},
                'logs':[{'path':'logs/'+n+'.log','label':n} for n in (*STAGES,*EXTRA_STAGES) if (self.directory/'logs'/(n+'.log')).exists()]}
