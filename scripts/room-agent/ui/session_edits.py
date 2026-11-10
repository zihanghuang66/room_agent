"""Explicit confirmed edits inside a stable Workspace session.

Internal runs retain existing executor/checkpoint semantics; the session ledger
is a projection and dispatch receipt, never a replacement workflow checkpoint.
"""
import os
from pathlib import Path
from datetime import datetime, timezone
from room_agent.storage import atomic_json, read_json, digest, identifier, room_lock, room_busy, sync_dir, Journal
from room_agent.revisions import selected_source, VERSION
from room_agent.workflow import SCHEMA_VERSION
from room_agent.saved_scene import engine_hash

from conversation_lock import conversation_lock
from room_agent.edit_behavior import skill_digest
from room_agent.edit_contract import bind_evidence

from session_workflows import RESOLVED, availability, review_request, latest_review, confirm_resume

BUSY = {'running','queued','dispatching'}

def now():return datetime.now(timezone.utc).isoformat()

class SessionEdits:
    def __init__(self, service, worker, status_reader=None):
        self.service,self.worker=service,worker
        self.raw_status=status_reader or self._status
        self.conversation=None

    def _status(self,run):
        status=dict(self.service.show(run));queue=self.worker.status(run) if hasattr(self.worker,'status') else {}
        status.update(queue)
        if queue.get('queue_status') in ('queued','dispatching'):status['execution_status']=queue['queue_status']
        elif queue.get('queue_status') in ('interrupted','failed','running') and status['execution_status']=='created':status['execution_status']=queue['queue_status']
        return status

    def path(self,run):
        identifier(run);req,_=self.service._load(run)
        if req.get('session_id'):raise ValueError('Use the owning Workspace session')
        cursor=self.service.root
        for part in ('runs',run,'conversation','session.json'):
            cursor/=part
            if cursor.is_symlink():raise ValueError('Session symlink denied')
        return cursor

    def _state(self,run):
        path=self.path(run)
        state=read_json(path) if path.exists() else {'schema':1,'run_id':run,'revision':0,'current_run_id':run,'operations':[]}
        if state.get('schema')!=1 or state['run_id']!=run:raise ValueError('Session identity mismatch')
        allowed={run}
        for op in state['operations']:
            request=op['request']
            if (op['run_id']!='se'+digest([run,op['plan_id']])[:30] or request.get('run_id')!=op['run_id'] or
                request.get('session_id')!=run or request.get('confirmed_plan_id')!=op['plan_id'] or
                request.get('parent_run_id')!=op['base_run_id'] or op['base_run_id'] not in allowed):
                raise ValueError('Session operation identity mismatch')
            if op['outcome']=='passed':allowed.add(op['run_id'])
        targets={run,*[o['run_id'] for o in state['operations']]}
        for command in state.get('commands',[]):
            if command.get('operation')!='resume_operation' or command.get('run_id') not in targets or not isinstance(command.get('plan_id'),str):
                raise ValueError('Session command identity mismatch')
        if state['current_run_id'] not in allowed:raise ValueError('Current session result is not accepted')
        return state

    def _save(self,state):atomic_json(self.path(state['run_id']),state)

    def _sync(self,state):
        changed=False
        for op in state['operations']:
            if op.get('outcome') in RESOLVED:continue
            root=self.service.root/'runs'/op['run_id']
            if not root.exists():continue
            status=self.raw_status(op['run_id'])
            if op['request']['operation']=='saved_scene_review' and status['execution_status']=='completed' and not status.get('worker_finalizing'):
                op['outcome']='reviewed';state['revision']+=1;changed=True
                continue
            if (op['request'].get('edit_contract') and status['execution_status']=='completed'
                and status.get('stop_before_build') is True and status.get('completion_reason') in ('needs_clarification','blocked')
                and not status.get('worker_finalizing')):
                op['outcome']='needs_input';state['revision']+=1;changed=True
                continue
            if (op['request'].get('current_source_check') and status['execution_status']=='completed'
                and status.get('no_change_verified') is True and status.get('completion_reason')=='already_satisfied'
                and not status.get('worker_finalizing')):
                op['outcome']='unchanged';state['revision']+=1;changed=True
                continue
            if (status['execution_status']=='completed' and status.get('edit_status')=='passed'
                and status.get('technical_export_verified') is True and not status.get('worker_finalizing')):
                op['outcome']='passed';state['current_run_id']=op['run_id'];state['revision']+=1;changed=True
        if changed:self._save(state)
        return state

    def _public(self,state):
        rows=[]
        review=latest_review(self,state)
        for op in state['operations']:
            item={k:v for k,v in op.items() if k!='request'}
            item['operation']=op['request']['operation']
            if (self.service.root/'runs'/op['run_id']).exists():item['status']=self.raw_status(op['run_id'])
            else:item['status']={'execution_status':'created'}
            if item.get('outcome')=='reviewed':
                item['status']={**item['status'],'completion_reason':'review_completed'}
                if review and review['run_id']==op['run_id'] and review.get('stale'):
                    item['status']={**item['status'],'quality_status':'unreviewed','score':None,'issues':[],'review_stale':True}
            rows.append(item)
        return {**{k:v for k,v in state.items() if k!='operations'},'operations':rows,'active':rows[-1] if rows else None}

    def read(self,run):
        if room_busy(self.service.root,'session-'+run):return self._public(self._state(run))
        with room_lock(self.service.root,'session-'+run):return self._public(self._sync(self._state(run)))

    def project(self,run):
        state=self.read(run);active=state['active']
        target=active['run_id'] if active and active['outcome']!='discarded' else state['current_run_id']
        status=self.raw_status(target) if (self.service.root/'runs'/target).exists() else self.raw_status(run)
        current=dict(self.raw_status(state['current_run_id']))
        current_request,_=self.service._load(state['current_run_id'])
        if current_request.get('parent_review_run_id') and current.get('baseline_quality'):
            current['baseline_quality']={**current['baseline_quality'],'run_id':current_request['parent_review_run_id']}
        review=latest_review(self,self._state(run))
        if review:
            if review.get('stale') and target==review['run_id']:status={**status,'quality_status':'unreviewed','score':None,'issues':[],'review_stale':True}
            current.update(quality_status=review['quality_status'],score=review['score'],issues=review['issues'],
                           whole_room_review='stale' if review.get('stale') else 'completed' if review['visual_review_complete'] else 'geometry_only',
                           review_run_id=review['run_id'],baseline_quality={},review_progress=None)
            if target==state['current_run_id']:
                status={**status,**{k:current[k] for k in ('quality_status','score','issues','whole_room_review','baseline_quality','review_progress','review_run_id')}}
            if active and active['outcome']=='reviewed':status={**status,'completion_reason':'review_completed'}
        return {**status,'run_id':run,'execution_run_id':target,'session':state,'current_result_status':current,'latest_review':review}

    def context(self,run):
        state=self.read(run)
        result={'current_scene_run_id':state['current_run_id'],'session_revision':state['revision'],
                'active_edit':{k:state['active'][k] for k in ('run_id','outcome','change')} if state['active'] else None}
        try:
            source=selected_source(self.service,state['current_run_id'])
            result.update(editable=True,scene_binding=digest(source))
        except (ValueError,KeyError,OSError,RuntimeError) as exc:
            result.update(editable=False,edit_unavailable=str(exc)[:500])
        result['workflow_actions']=availability(self,self._state(run),result['editable'])
        result['latest_review']=latest_review(self,self._state(run))
        return result

    def workspace(self):
        rows=[]
        for row in self.service.list_runs():
            request,_=self.service._load(row['run_id'])
            if not request.get('session_id'):rows.append(self.project(row['run_id']))
        return rows

    def _publish(self,request):
        run=request['run_id'];final=self.service.root/'runs'/run
        if final.is_symlink():raise ValueError('Linked internal operation denied')
        if final.exists():
            existing,_=self.service._load(run)
            if existing!=request:raise RuntimeError('Internal operation identity changed')
            return
        pending=self.service.root/'pending'/run
        if pending.exists():
            if pending.is_symlink() or any((pending/name).is_symlink() for name in ('input.json','input.sha256.json')) or read_json(pending/'input.json')!=request or read_json(pending/'input.sha256.json')!=digest(request):
                raise RuntimeError('Incomplete operation preparation; inspect diagnostics before retry')
            journal=Journal(pending,run)
            if not journal.read():journal.emit('run_created',room_id=request['room_id'])
            final.parent.mkdir(exist_ok=True);pending.rename(final);sync_dir(final.parent)
        else:self.service._publish_request(request)

    def _dispatch(self,state,op):
        if op.get('submitted') or op['outcome']=='discarded':return
        self._publish(op['request'])
        status=self.raw_status(op['run_id'])
        # Recover an accepted enqueue whose receipt was not saved; never restart
        # model work automatically after an execution/interruption.
        if status['execution_status']=='created' and status.get('queue_status') not in ('cancelled','interrupted','failed'):
            self.worker.start(op['run_id'])
        op['submitted']=True;self._save(state)

    def confirm(self,run,data):
        if not self.conversation:raise RuntimeError('Conversation adapter unavailable')
        plan_id=data.get('plan_id')
        if not isinstance(plan_id,str) or type(data.get('revision')) is not int:raise ValueError('Plan and conversation revision required')
        with conversation_lock(self.service.root,'conversation-'+run),room_lock(self.service.root,'session-'+run):
            state=self._sync(self._state(run))
            prior=next((o for o in state['operations'] if o['plan_id']==plan_id),None)
            if prior:
                if prior is state['operations'][-1]:self._dispatch(state,prior)
                return self._public(state)
            previous_command=next((c for c in state.get('commands',[]) if c['plan_id']==plan_id),None)
            if previous_command:return confirm_resume(self,state,{'plan_id':plan_id,'operation_run_id':previous_command['run_id']})
            history=self.conversation.read(run);turn=history['turns'][-1] if history['turns'] else {}
            plan=turn.get('result',{}).get('plan')
            if (history['revision']!=data['revision'] or turn.get('status')!='completed' or not plan or
                plan.get('plan_id')!=plan_id or plan.get('target_run_id')!=run):raise RuntimeError('Plan changed. Read the latest plan before confirming.')
            if plan.get('operation') in ('scene_review','resume_operation'):
                if plan.get('contract_version')!='conversation-workflow/1':raise RuntimeError('Workflow draft is outdated. Ask for a fresh plan.')
                request,_=self.service._load(run)
                if plan['source_binding']!=digest(self.conversation.facts(run,request)):raise RuntimeError('Scene or task status changed. Ask for a fresh plan before confirming.')
                if plan['operation']=='resume_operation':return confirm_resume(self,state,plan)
                if not availability(self,state,True)['scene_review']:raise RuntimeError('Resolve the current operation before starting a review.')
                parent,_=self.service._load(state['current_run_id'])
                with room_lock(self.service.root,parent['room_id']):
                    source=selected_source(self.service,state['current_run_id'])
                    if turn['facts'].get('scene_binding')!=digest(source):raise RuntimeError('Scene source changed; make a fresh plan.')
                    evidence=plan.get('inspection_evidence',[])
                    if evidence:
                        from scene_evidence import verify_bound
                        verify_bound(self.service,state['current_run_id'],evidence)
                    req=review_request(self,state,plan)
                    op={'run_id':req['run_id'],'plan_id':plan_id,'base_run_id':state['current_run_id'],
                        'change':plan['change'],'created_at':now(),'outcome':'pending','submitted':False,'request':req}
                    state['operations'].append(op);state['revision']+=1;self._save(state);self._dispatch(state,op)
                return self._public(state)
            if plan.get('contract_version')!='evidence-edit/1':raise RuntimeError('This earlier draft lacks target/evidence bindings. Send your request again for a fresh plan.')
            if state['operations'] and state['operations'][-1]['outcome'] not in RESOLVED:
                raise RuntimeError('Resolve the current edit before confirming another plan.')
            request,_=self.service._load(run)
            if plan['source_binding']!=digest(self.conversation.facts(run,request)):
                raise RuntimeError('Scene or task status changed. Ask for a fresh plan before confirming.')
            source_id=state['current_run_id'];parent,_=self.service._load(source_id)
            with room_lock(self.service.root,parent['room_id']):
                source=selected_source(self.service,source_id)
                if turn['facts'].get('scene_binding')!=digest(source):raise RuntimeError('Scene source changed; make a fresh plan.')
                evidence=plan.get('inspection_evidence',[])
                if evidence:
                    from scene_evidence import verify_bound
                    verify_bound(self.service,source_id,evidence)
                binding=bind_evidence(plan,turn['facts'])
                contract=dict(version='evidence-edit/1',skill_sha256=skill_digest(),change=plan['change'],preserve=plan['preserve'],**binding)
                if evidence:contract['inspection_evidence']=evidence
                instruction='Requested change: '+plan['change']+'\nObjects (descriptions, verify before editing): '+ '; '.join(plan['objects'])+'\nPreserve: '+'; '.join(plan['preserve'])+'\nInspect the actual scene first. If object identity is ambiguous, do not guess or make changes; report the ambiguity. Keep unrelated content unchanged.'
                if binding['target_ids']:instruction+='\nConfirmed target IDs (verify actual scene): '+', '.join(binding['target_ids'])
                if len(instruction)>4000:raise ValueError('Plan is too long to execute without losing constraints. Ask for a shorter plan.')
                review=latest_review(self,state)
                if review and not review.get('stale'):
                    source.update(parent_quality=review['quality_status'],parent_score=review['score'],parent_issues=review['issues'],parent_review_run_id=review['run_id'])
                elif review and review.get('stale'):
                    source.update(parent_quality='unreviewed',parent_score=None,parent_issues=[])
                elif parent.get('parent_review_run_id'):
                    source['parent_review_run_id']=parent['parent_review_run_id']
                internal='se'+digest([run,plan_id])[:30]
                source.update(schema_version=SCHEMA_VERSION,workflow_version=VERSION,operation='scene_edit',run_id=internal,
                    instruction=instruction,session_id=run,confirmed_plan_id=plan_id,
                    codex_model=os.environ.get('LR_CODEX_MODEL',parent.get('codex_model',parent.get('environment',{}).get('LR_CODEX_MODEL','gpt-6-astra'))),
                    engine_sha256=engine_hash(),repair_seconds=900,edit_review_policy='impact-review/1',current_source_check=True,current_source_check_policy='current-source-check/3',edit_contract=contract,
                    app_python=os.path.abspath(os.environ.get('ROOM_APP_PYTHON','/opt/room/envs/app/bin/python')))
                op={'run_id':internal,'plan_id':plan_id,'base_run_id':source_id,'change':plan['change'],'created_at':now(),
                    'outcome':'pending','submitted':False,'request':source}
                state['operations'].append(op);state['revision']+=1;self._save(state)
                self._dispatch(state,op)
            return self._public(state)

    def action(self,run,data):
        with conversation_lock(self.service.root,'conversation-'+run),room_lock(self.service.root,'session-'+run):
            state=self._sync(self._state(run));op=state['operations'][-1] if state['operations'] else None
            if not op or data.get('operation_id')!=op['run_id']:raise RuntimeError('Operation changed. Reload before acting.')
            action=data.get('action')
            if action=='resume' and not (self.service.root/'runs'/op['run_id']).exists():self._publish(op['request'])
            status=self.raw_status(op['run_id']) if (self.service.root/'runs'/op['run_id']).exists() else {'execution_status':'created'}
            if op['outcome'] in RESOLVED:raise RuntimeError('This edit is already resolved')
            if action=='cancel':self.worker.cancel(op['run_id'])
            elif action=='resume':
                if status['execution_status'] not in ('failed','interrupted','created') or status.get('worker_finalizing') or status.get('queue_status') in BUSY:raise RuntimeError('This edit cannot resume now')
                self.worker.start(op['run_id']);op['submitted']=True
            elif action=='discard':
                if status['execution_status'] in BUSY or status.get('worker_finalizing') or room_busy(self.service.root/'run_activity',op['run_id']):raise RuntimeError('Wait for the edit to stop before keeping the current result')
                op['outcome']='discarded';state['revision']+=1
            else:raise ValueError('Unknown session action')
            self._save(state);return self._public(state)


def session_presentation(manager,run):
    from display import build_presentation
    state=manager.read(run);current=state['current_run_id']
    def projection(target):
        root=manager.service.root/'runs'/target
        if root.is_symlink() or root.parent.is_symlink():raise ValueError('Linked operation denied')
        result=build_presentation(root,manager.raw_status(target))
        def annotate(value):
            if isinstance(value,dict):
                if 'path' in value:value['run_id']=target
                for v in value.values():annotate(v)
            elif isinstance(value,list):
                for v in value:annotate(v)
        annotate(result)
        return result
    result=projection(current);result['source_run_id']=current
    active=state['active']
    if active and active['run_id']!=current and (manager.service.root/'runs'/active['run_id']).exists():
        candidate=projection(active['run_id']);result['diagnostics']=candidate.get('diagnostics',{})
    review=latest_review(manager,manager._state(run))
    if review and not review.get('stale'):
        inspected=projection(review['run_id'])
        if review['quality_status']=='passed':
            result['comparisons']=inspected.get('comparisons',[])
            result['reports']=inspected.get('reports',[])
        if not active or active['run_id']==review['run_id'] or active['outcome'] in RESOLVED:
            result['diagnostics']=inspected.get('diagnostics',{})
    return result
