"""Confirmed workflow routing; execution remains owned by the existing graphs."""
import os
from room_agent.storage import digest, read_json
from room_agent.revisions import selected_source
from room_agent.saved_scene import VERSION, engine_hash
from room_agent.workflow import SCHEMA_VERSION

RESOLVED={'passed','discarded','unchanged','needs_input','reviewed'}
BUSY={'running','queued','dispatching'}


def availability(manager,state,editable):
    active=state['operations'][-1] if state['operations'] else None
    unresolved=active and active['outcome'] not in RESOLVED
    target=active['run_id'] if unresolved else state['run_id']
    status=manager.raw_status(target) if (manager.service.root/'runs'/target).exists() else {'execution_status':'created'}
    req=active['request'] if unresolved else manager.service._load(target)[0]
    resumable=(req['operation'] in ('scene_edit','saved_scene_review','full_scan') and
        status.get('execution_status') in ('failed','interrupted','created') and
        not status.get('worker_finalizing') and status.get('queue_status') not in BUSY)
    # A pristine root is a new job, not an interrupted job to resume.
    if not unresolved and status.get('execution_status')=='created' and status.get('queue_status') not in ('failed','interrupted','cancelled'):
        resumable=False
    events=manager.service.events(target) if resumable and (manager.service.root/'runs'/target).exists() else []
    queue_path=manager.service.root/'queue'/(target+'.json')
    queue=read_json(queue_path) if resumable and queue_path.exists() else {}
    binding=digest({'request':req,'event':events[-1] if events else None,'queue_submission':queue.get('submitted_ns')})
    return {'scene_review':bool(editable and not unresolved),
            'resume_operation':{'run_id':target,'operation':req['operation'],'node':status.get('current_node'),
                                'execution_status':status.get('execution_status'),'checkpoint_binding':binding} if resumable else None}


def review_request(manager,state,plan):
    source_id=state['current_run_id'];parent,_=manager.service._load(source_id)
    source=selected_source(manager.service,source_id)
    source.update(schema_version=SCHEMA_VERSION,workflow_version=VERSION,operation='saved_scene_review',
        run_id='se'+digest([state['run_id'],plan['plan_id']])[:30],session_id=state['run_id'],
        confirmed_plan_id=plan['plan_id'],max_batches=0,repair_seconds=900,engine_sha256=engine_hash(),
        codex_model=os.environ.get('LR_CODEX_MODEL',parent.get('codex_model',parent.get('environment',{}).get('LR_CODEX_MODEL','gpt-6-astra'))),
        app_python=os.path.abspath(os.environ.get('ROOM_APP_PYTHON','/opt/room/envs/app/bin/python')))
    return source


def latest_review(manager,state):
    for op in reversed(state['operations']):
        if op['outcome']=='reviewed' and op['base_run_id']==state['current_run_id']:
            status=manager.raw_status(op['run_id'])
            from review_provenance import matches
            valid=matches(manager.service,state['current_run_id'],op['request'])
            if not valid:
                return {'run_id':op['run_id'],'source_run_id':op['base_run_id'],'stale':True,
                        'quality_status':'unreviewed','score':None,'issues':[],'visual_review_complete':False}
            return {'run_id':op['run_id'],'source_run_id':op['base_run_id'],
                    'stale':False,'quality_status':status.get('quality_status','unreviewed'),'score':status.get('score'),
                    'issues':status.get('issues',[]),'visual_review_complete':status.get('score') is not None}
    return None


def confirm_resume(manager,state,plan):
    target=plan['operation_run_id'];commands=state.setdefault('commands',[])
    command=next((c for c in commands if c['plan_id']==plan['plan_id']),None)
    if command and command.get('submitted'):return manager._public(state)
    if command is None:
        available=availability(manager,state,False)['resume_operation']
        if not available or available['run_id']!=target:raise RuntimeError('This operation cannot resume now. Ask for a fresh plan.')
        command={'plan_id':plan['plan_id'],'run_id':target,'operation':'resume_operation','submitted':False,'checkpoint_binding':available['checkpoint_binding']}
        commands.append(command);manager._save(state)
    seen=manager.worker.submitted(target,plan['plan_id'])
    if not seen:
        available=availability(manager,state,False)['resume_operation']
        if not available or available['run_id']!=target or available['checkpoint_binding']!=command['checkpoint_binding']:
            raise RuntimeError('Checkpoint or operation changed before dispatch. Ask for a fresh plan.')
    op=next((o for o in state['operations'] if o['run_id']==target),None)
    if op:manager._publish(op['request'])
    # Queue stores this identity with the enqueue atomically. A lost reply cannot
    # cause the same confirmation to rerun work after a rapid failure or restart.
    manager.worker.start(target,submission_id=plan['plan_id'])
    command['submitted']=True
    if op:op['submitted']=True
    manager._save(state)
    return manager._public(state)
