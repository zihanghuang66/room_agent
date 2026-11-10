"""Durable, single-dispatcher FIFO. Interrupted model work is never auto-resubmitted."""
import threading
import time
from pathlib import Path

from .storage import atomic_json, identifier, read_json, room_busy, room_lock


class TaskQueue:
    def __init__(self, root, service, worker, *, autostart=True):
        self.root = Path(root)
        self.directory = self.root / 'queue'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.service, self.worker = service, worker
        self.mutex = threading.RLock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.active = None
        self.thread = None
        self.ownership = room_lock(self.root / 'queue_scheduler', 'owner')
        self.ownership.__enter__()
        try:
            for row in self._records():
                if row['status'] in ('dispatching', 'running'):
                    row.update(status='interrupted', reason='server_restarted; explicit resume required')
                    self._save(row)
            if autostart:
                self.thread = threading.Thread(target=self._loop, daemon=True)
                self.thread.start()
        except BaseException:
            self.ownership.__exit__(None, None, None)
            raise

    @property
    def busy(self):
        return self.worker.busy

    def _records(self):
        return sorted((read_json(p) for p in self.directory.glob('*.json')),
                      key=lambda x: (x['submitted_ns'], x['run_id']))

    def _path(self, run_id):
        return self.directory / (identifier(run_id) + '.json')

    def _save(self, row):
        atomic_json(self._path(row['run_id']), row)

    def status(self, run_id):
        with self.mutex:
            if not self.worker.busy:
                self._settle_active()
            path = self._path(run_id)
            if not path.exists():
                return {}
            row = read_json(path)
            queued = [r['run_id'] for r in self._records() if r['status'] == 'queued']
            finalizing=(self.active==run_id and self.worker.busy and
                        self.service.show(run_id)['execution_status'] in ('waiting_for_input','completed','failed','interrupted'))
            return {'worker_finalizing':finalizing,'queue_status': row['status'],
                    'queue_position': queued.index(run_id)+1 if run_id in queued else None,
                    'queue_error': row.get('error')}

    def submitted(self, run_id, submission_id):
        identifier(run_id);identifier(submission_id)
        with self.mutex:
            path=self._path(run_id)
            return path.exists() and submission_id in read_json(path).get('submissions',{})

    def start(self, run_id, action=None, *, submission_id=None):
        identifier(run_id)
        if submission_id is not None:identifier(submission_id)
        if action not in (None, 'repair', 'finish'):
            raise ValueError('invalid queue action')
        with self.mutex:
            if self.stop.is_set():
                raise RuntimeError('server is stopping')
            if not self.worker.busy:
                self._settle_active()
            path = self._path(run_id)
            submissions={}
            if path.exists():
                previous = read_json(path)
                submissions=previous.get('submissions',{})
                if submission_id is not None and submission_id in submissions:
                    if submissions[submission_id]!=action:raise ValueError('Submission identity changed action')
                    return {'run_id':run_id,**self.status(run_id)}
                if previous['status'] in ('queued', 'dispatching', 'running'):
                    if submission_id is not None:raise RuntimeError('Another submission is queued or running')
                    if previous.get('action') != action:
                        if self.status(run_id).get('worker_finalizing'):
                            raise RuntimeError('The previous operation is finishing logs and diagnostics. Wait for Finish to become available.')
                        raise RuntimeError('任务已有排队或执行中的操作')
                    return {'run_id': run_id, **self.status(run_id)}
            state = self.service.show(run_id)
            status = state['execution_status']
            if status in ('completed', 'cancelled', 'running'):
                raise ValueError('任务已结束或正在执行，不能重复提交')
            if status == 'waiting_for_input' and action is None:
                raise ValueError('请选择修改一次或结束任务')
            if action is not None and status != 'waiting_for_input':
                raise ValueError('任务尚未等待选择')
            if action is not None and 'allowed_actions' in state and action not in state['allowed_actions']:
                raise ValueError('当前任务不允许这个操作')
            if submission_id is not None:submissions={**submissions,submission_id:action}
            self._save({'run_id': run_id, 'action': action, 'status': 'queued',
                        'submitted_ns': time.time_ns(),'submissions':submissions})
            self.wake.set()
            return {'run_id': run_id, **self.status(run_id)}

    def cancel(self, run_id):
        with self.mutex:
            row = read_json(self._path(run_id))
            if row['status'] != 'queued':
                raise RuntimeError('只能取消尚未开始的排队操作')
            row.update(status='cancelled', reason='user_cancelled_queue')
            self._save(row)
            return {'run_id': run_id, **self.status(run_id)}

    def _settle_active(self):
        if self.active:
            row = read_json(self._path(self.active))
            try:
                state = self.service.show(self.active)
                status = state['execution_status']
                row['status'] = status if status in ('completed','waiting_for_input','failed','interrupted') else 'interrupted'
                if state.get('error'):
                    row['error'] = state['error'].get('message', str(state['error']))
            except Exception as exc:
                row.update(status='failed', error=str(exc))
            self._save(row)
            self.active = None

    def pump(self):
        with self.mutex:
            if self.stop.is_set() or self.worker.busy:
                return
            self._settle_active()
            # Also wait for a CLI invocation/orphan worker that owns the shared resource.
            if room_busy(self.root / 'resources', 'scene_execution'):
                return
            rows = [r for r in self._records() if r['status'] == 'queued']
            if not rows:
                return
            row = rows[0]
            # CLI may have finished a task after this command was queued.
            state=self.service.show(row['run_id'])
            execution=state['execution_status'];action=row.get('action')
            if execution in ('completed','cancelled'):
                row.update(status=execution,reason='task_already_finished');self._save(row);return
            if execution=='running':return
            if ((execution=='waiting_for_input' and action is None) or
                (action is not None and (execution!='waiting_for_input' or
                 'allowed_actions' in state and action not in state['allowed_actions']))):
                row.update(status='cancelled',reason='task_state_changed; explicit new choice required');self._save(row);return
            row['status'] = 'dispatching'
            self._save(row)
            try:
                reply = self.worker.start(row['run_id'], row.get('action'))
                row.update(status='running', pid=reply.get('pid'))
                self.active = row['run_id']
            except Exception as exc:
                row.update(status='failed', error=str(exc))
            self._save(row)

    def _loop(self):
        while not self.stop.is_set():
            try:
                self.pump()
            except Exception as exc:
                # A broken queue must not silently dispatch potentially duplicated work.
                atomic_json(self.root / 'queue_error.json', {'error': str(exc)})
                print('QUEUE_ERROR: ' + str(exc), flush=True)
                self.stop.set()
                break
            self.wake.wait(.2)
            self.wake.clear()

    def close(self):
        if self.ownership is None:
            return
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=5)
        try:
            self.worker.close()
        finally:
            with self.mutex:
                try:
                    if self.active:
                        row = read_json(self._path(self.active))
                        try:
                            status = self.service.show(self.active)['execution_status']
                            row['status'] = status if status in ('completed','waiting_for_input','failed') else 'interrupted'
                        except Exception as exc:
                            row.update(status='interrupted', error=str(exc))
                        self._save(row)
                finally:
                    self.ownership.__exit__(None, None, None)
                    self.ownership = None
