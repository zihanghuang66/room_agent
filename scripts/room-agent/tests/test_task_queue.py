from pathlib import Path
import pytest


class Service:
    def __init__(self):
        self.states={}
    def show(self, run):
        return {'execution_status':self.states.get(run,'created')}


class Worker:
    busy=False
    calls=[]
    def __init__(self):
        self.calls=[];self.busy=False
    def start(self, run, action=None):
        assert not self.busy
        self.calls.append((run,action));self.busy=True
        return {'pid':123,'run_id':run}
    def close(self):
        self.busy=False


def queue(tmp_path):
    from room_agent.task_queue import TaskQueue
    service=Service();worker=Worker()
    return TaskQueue(tmp_path,service,worker,autostart=False),service,worker


def test_fifo_duplicate_cancel_and_completion(tmp_path):
    q,s,w=queue(tmp_path)
    try:
        assert q.start('one')['queue_position']==1
        assert q.start('one')['queue_position']==1
        assert q.start('two')['queue_position']==2
        q.start('three');q.cancel('three')
        q.pump();assert w.calls==[('one',None)]
        assert q.status('two')['queue_position']==1
        with pytest.raises(RuntimeError):q.cancel('one')
        s.states['one']='completed';w.busy=False
        q.pump();assert w.calls==[('one',None),('two',None)]
        assert q.status('one')['queue_status']=='completed'
        assert q.status('three')['queue_status']=='cancelled'
    finally:q.close()


def test_restart_keeps_queue_but_requires_explicit_resume_of_interrupted(tmp_path):
    q,s,w=queue(tmp_path)
    q.start('one');q.start('two');q.pump();q.close()
    q,s,w=queue(tmp_path)
    try:
        assert q.status('one')['queue_status']=='interrupted'
        q.pump();assert w.calls==[('two',None)]
    finally:q.close()


def test_only_one_scheduler_and_resource_lock_waits(tmp_path):
    from room_agent.storage import room_lock
    q,s,w=queue(tmp_path)
    try:
        with pytest.raises(RuntimeError):queue(tmp_path)
        q.start('one')
        with room_lock(tmp_path/'resources','scene_execution'):
            q.pump();assert w.calls==[]
        q.pump();assert w.calls==[('one',None)]
    finally:q.close()


def test_no_repair_to_closed_task_and_waiting_requires_action(tmp_path):
    q,s,w=queue(tmp_path)
    try:
        s.states['done']='completed'
        with pytest.raises(ValueError):q.start('done','repair')
        s.states['waiting']='waiting_for_input'
        with pytest.raises(ValueError):q.start('waiting')
        assert q.start('waiting','finish')['queue_status']=='queued'
    finally:q.close()


def test_confirmed_submission_replay_after_failure_and_restart_is_not_queued(tmp_path):
    q,s,w=queue(tmp_path)
    q.start('one',submission_id='confirm1');q.pump()
    s.states['one']='failed';w.busy=False;q.status('one');q.close()
    q,s,w=queue(tmp_path)
    try:
        s.states['one']='failed'
        q.start('one',submission_id='confirm1')
        assert q.status('one')['queue_status']=='failed'
        q.start('one',submission_id='confirm2');q.pump()
        assert w.calls==[('one',None)]
        s.states['one']='failed';w.busy=False;q.status('one')
        q.start('one',submission_id='confirm1')
        assert q.status('one')['queue_status']=='failed'
        q.start('one',submission_id='confirm2')
        assert q.status('one')['queue_status']=='failed'
    finally:q.close()
