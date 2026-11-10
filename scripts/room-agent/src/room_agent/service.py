"""Application interface shared by CLI and future clients."""
from contextlib import contextmanager, nullcontext
from pathlib import Path
from uuid import uuid4
import os

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .artifacts import ArtifactStore, inventory
from .storage import (Journal, atomic_json, digest, identifier, read_json,
                      room_busy, room_lock, sync_dir)
from .workflow import SCHEMA_VERSION, WORKFLOW_VERSION, build_graph


class Service:
    def __init__(self, root, *, artifact_type=ArtifactStore, scene_executor_type=None, full_process_runner=None):
        self.root = Path(root).resolve()
        self.artifact_type = artifact_type
        self.scene_executor_type = scene_executor_type
        self.full_process_runner = full_process_runner

    def create_full(self, scan_archive, scene, project, prepare_only=False, *, workflow="graph"):
        from .full_scan import capture_input, VERSION
        run_id = uuid4().hex
        request = capture_input(scan_archive, scene, project, run_id, prepare_only)
        if workflow == 'graph':
            from .scan_graph import VERSION
            from .scan_executor import engine_hash
            request['engine_sha256'] = engine_hash()
        elif workflow != 'legacy':
            raise ValueError('Unknown full scan workflow')
        request.update(schema_version=SCHEMA_VERSION, workflow_version=VERSION,
                       operation='full_scan', room_id=scene, run_id=run_id)
        return self._publish_request(request)

    def _publish_request(self, request):
        run_id = request['run_id']
        directory = self.root / 'pending' / run_id
        directory.mkdir(parents=True, exist_ok=False)
        atomic_json(directory / 'input.json', request)
        atomic_json(directory / 'input.sha256.json', digest(request))
        Journal(directory, run_id).emit('run_created', room_id=request['room_id'])
        final = self.root / 'runs' / run_id
        final.parent.mkdir(exist_ok=True)
        directory.rename(final)
        sync_dir(final.parent)
        return run_id

    def create_review(self, source_run, *, max_batches=1, repair_seconds=900):
        from .saved_scene import source_info, VERSION, engine_hash
        if type(max_batches) is not int or not 0 <= max_batches <= 3 or type(repair_seconds) is not int or not 120 <= repair_seconds <= 1800:
            raise ValueError('max_batches 0..3 and repair_seconds 120..1800 required')
        request = source_info(source_run)
        run_id = uuid4().hex
        request.update(schema_version=SCHEMA_VERSION, workflow_version=VERSION,
                       operation='saved_scene_review', room_id=request['scene'], run_id=run_id,
                       max_batches=max_batches, repair_seconds=repair_seconds,
                       engine_sha256=engine_hash(),
                       app_python=os.path.abspath(os.environ.get('ROOM_APP_PYTHON', '/opt/room/envs/app/bin/python')))
        return self._publish_request(request)

    def versions(self):
        from .revisions import versions
        return versions(self)

    def create_edit(self, parent_run_id, instruction):
        from .revisions import selected_source, VERSION
        from .saved_scene import engine_hash
        if not isinstance(instruction, str) or not 3 <= len(instruction.strip()) <= 4000:
            raise ValueError('A specific change instruction of 3..4000 characters is required')
        parent, _ = self._load(parent_run_id)
        with room_lock(self.root, parent['room_id']):
            request = selected_source(self, parent_run_id)
            request.update(schema_version=SCHEMA_VERSION, workflow_version=VERSION,
                operation='scene_edit', run_id=uuid4().hex, instruction=instruction.strip(),
                codex_model=os.environ.get('LR_CODEX_MODEL', parent.get('codex_model', parent.get('environment', {}).get('LR_CODEX_MODEL', 'gpt-6-astra'))),
                engine_sha256=engine_hash(), repair_seconds=900, edit_review_policy='impact-review/1',
                app_python=os.path.abspath(os.environ.get('ROOM_APP_PYTHON', '/opt/room/envs/app/bin/python')))
            return self._publish_request(request)

    def create(self, room_id, source):
        identifier(room_id)
        source = Path(source).absolute()
        if source.is_symlink():
            raise ValueError('symlink source is not supported')
        source = source.resolve()
        if source == self.root or source in self.root.parents or self.root in source.parents:
            raise ValueError('source and state root must not contain one another')
        files = inventory(source)
        run_id = uuid4().hex
        request = {'schema_version': SCHEMA_VERSION, 'workflow_version': WORKFLOW_VERSION,
                   'operation': 'saved_source_audit', 'room_id': room_id, 'run_id': run_id,
                   'source': str(source), 'files': files}
        return self._publish_request(request)

    def _load(self, run_id):
        directory = self.root / 'runs' / identifier(run_id)
        request = read_json(directory / 'input.json')
        from .saved_scene import VERSION
        expected = VERSION if request.get('operation') == 'saved_scene_review' else WORKFLOW_VERSION
        if request.get('operation') == 'scene_edit':
            from .revisions import VERSION as expected
        if request.get('operation') == 'full_scan':
            from .full_scan import VERSION as legacy_version
            from .scan_graph import VERSION as graph_version
            expected = legacy_version if request.get('workflow_version') == legacy_version else graph_version
        if (request.get('schema_version') != SCHEMA_VERSION or
                request.get('workflow_version') != expected):
            raise ValueError('incompatible workflow/schema version; migration required')
        if request.get('run_id') != run_id or digest(request) != read_json(directory / 'input.sha256.json'):
            raise ValueError('immutable input checksum mismatch')
        identifier(request['room_id'])
        return request, directory

    @contextmanager
    def _graph(self, request, directory):
        journal = Journal(directory, request['run_id'])
        with SqliteSaver.from_conn_string(str(directory / 'checkpoints.sqlite')) as saver:
            if request['operation'] == 'full_scan':
                from .scan_executor import ScanExecutor
                from .scan_graph import build_scan_graph
                artifacts = ScanExecutor(request, directory, journal, self.full_process_runner)
                graph = build_scan_graph(saver, artifacts, journal, request)
            elif request['operation'] == 'scene_edit':
                from .edit_executor import EditExecutor
                from .edit_graph import build_edit_graph
                artifacts = (getattr(self, 'edit_executor_type', None) or EditExecutor)(request, directory, journal)
                graph = build_edit_graph(saver, artifacts, journal, request)
            elif request['operation'] == 'saved_scene_review':
                from .scene_executor import SceneExecutor
                from .scene_graph import build_scene_graph
                artifacts = (self.scene_executor_type or SceneExecutor)(request, directory, journal)
                graph = build_scene_graph(saver, artifacts, journal, request)
            else:
                artifacts = self.artifact_type(self.root, request, journal.emit)
                graph = build_graph(saver, artifacts, journal)
            yield graph, artifacts, journal, {'configurable': {'thread_id': request['run_id']}, 'recursion_limit': 100}

    def _summary(self, request, graph, config, journal, *, locked=False):
        snapshot = graph.get_state(config)
        state = dict(snapshot.values)
        status = state.get('execution_status', 'created')
        events = journal.read()
        terminal = next((e for e in reversed(events) if e['kind'] in
                         ('run_started', 'operation_failed', 'operation_finished')), None)
        error = None
        if terminal and terminal['kind'] == 'operation_failed':
            status = 'interrupted' if terminal['error']['type'] in ('KeyboardInterrupt', 'InterruptedError') else 'failed'
            error = terminal['error']
        elif terminal and terminal['kind'] == 'run_started':
            active = locked or room_busy(self.root / 'run_activity', request['run_id'])
            status = 'running' if active else 'interrupted'
        elif any(t.interrupts for t in snapshot.tasks):
            status = 'waiting_for_input'
        result = {'run_id': request['run_id'], 'room_id': request['room_id'],
                'operation': request['operation'], 'execution_status': status,
                'quality_status': state.get('quality_status', 'unreviewed'), 'revision_id': state.get('revision_id'),
                'artifact_path': state.get('artifact_path'), 'audit': state.get('audit'),
                'next_nodes': list(snapshot.next), 'error': error}
        if request['operation'] == 'saved_scene_review':
            from .scene_graph import STAGES, can_repair
            number = state.get('round', 0)
            stages = []
            for node in STAGES:
                selected = [e for e in events if e.get('node') == node and
                            (e.get('round') == number or node in ('snapshot', 'preflight', 'repair'))]
                event = next((e for e in reversed(selected) if e['kind'] in ('node_started', 'node_completed', 'node_failed')), {})
                stages.append({'node': node, 'status': {'node_started': 'running', 'node_completed': 'completed',
                    'node_failed': 'failed'}.get(event.get('kind'), 'pending'), 'seconds': event.get('seconds')})
            if status == 'waiting_for_input':
                stages[STAGES.index('decision')]['status'] = 'waiting_for_input'
            if status == 'completed':
                stages[STAGES.index('decision')]['status'] = 'completed'
                for item in stages:
                    if item['status'] == 'pending':
                        item['status'] = 'skipped'
            if status in ('failed', 'interrupted'):
                for item in stages:
                    if item['status'] == 'running':
                        item['status'] = status
            directory = self.root / 'runs' / request['run_id']
            previews = [{'path': str(p.relative_to(directory)), 'label': p.name} for p in
                        sorted((directory / 'work/attempts').glob('r*_render/*/compare/pairs/pair_*.png'))][-48:]
            exports = [{'path': str(p.relative_to(directory)), 'label': p.name} for p in
                       sorted((directory / 'work/attempts').glob('r*_build/*/round/room_preview/Room.*'))
                       if p.suffix in ('.blend', '.glb')]
            active_node = next((e.get('node') for e in reversed(events) if e['kind'] == 'node_started'), None)
            result.update(round=number, score=state.get('score'), issues=state.get('issues', []),
                          review_stream_error_count=state.get('review_stream_error_count', 0),
                          repair_termination=state.get('repair_termination', 'not_started'),
                          current_node=('finished' if status == 'completed' else
                                        'decision' if status == 'waiting_for_input' else
                                        error['node'] if error else active_node),
                          completion_reason=state.get('completion_reason',
                              'legacy_unspecified' if status == 'completed' else None),
                          stages=stages, previews=previews, exports=exports,
                          allowed_actions=['repair', 'finish'] if status == 'waiting_for_input' and can_repair(state, request) else [],
                          ingest_reconstruct_rerun=False, source_run=request['source_run'])
        if request['operation'] == 'full_scan':
            from .scan_executor import ScanExecutor
            directory = self.root / 'runs' / request['run_id']
            result.update(ScanExecutor(request, directory, journal).project(state, events, status))
            if error:
                result['current_node'] = error['node']
        if request['operation'] == 'scene_edit':
            from .edit_graph import project
            result.update(project(request, state, events, status))
            if status=='completed' and not state.get('edit_status'):
                from .edit_acceptance import legacy_verification
                result.update(legacy_verification(self.root/'runs'/request['run_id'],request,state))
        return result

    def execute(self, run_id, *, action=None, action_origin='api'):
        request, directory = self._load(run_id)
        if action_origin not in ('api', 'cli', 'web'):
            raise ValueError('unknown action origin')
        if request['operation'] == 'full_scan' and request['workflow_version'] == 'legacy-full-scan/1':
            from .full_scan import execute
            return execute(self, request, directory, action=action)
        scene = request['operation'] in ('saved_scene_review', 'scene_edit')
        full = request['operation'] == 'full_scan'
        external = scene or full
        actions = ('repair', 'finish') if scene or full else ('finish', 'cancel')
        if action is not None and action not in actions:
            raise ValueError('action must be one of ' + repr(actions))
        with ((room_lock(self.root / 'resources', 'scene_execution') if external else nullcontext()) as resource_lease,
              room_lock(self.root, request['room_id']) as room_lease,
              room_lock(self.root / 'run_activity', run_id) as activity_lease,
              self._graph(request, directory) as resources):
            graph, artifacts, journal, config = resources
            if external:
                artifacts.lock_fds = tuple(handle.fileno() for handle in (resource_lease, room_lease, activity_lease))
            snapshot = graph.get_state(config)
            waiting = any(t.interrupts for t in snapshot.tasks)
            if action is not None and not waiting:
                raise ValueError('run is not waiting for input')
            if snapshot.values and snapshot.next and action is None:
                journal.emit('resume_requested', origin=action_origin, nodes=list(snapshot.next))
            journal.emit('run_started')
            try:
                # Validate published files even if graph has already completed or stopped before a checkpoint.
                if full:
                    artifacts.verify(snapshot.values)
                elif scene or artifacts.path.exists() or snapshot.values.get('artifact_path'):
                    artifacts.verify()
                if action is not None:
                    journal.emit('action_received', action=action, origin=action_origin,
                                 round=snapshot.values.get('round', 0))
                if snapshot.values and not snapshot.next:
                    pass
                elif waiting and action is None:
                    pass
                else:
                    initial = {'run_id': run_id, 'room_id': request['room_id'],
                               'operation': request['operation'], 'quality_status': 'unreviewed'}
                    if scene:
                        initial.update(round=0, repair_termination='not_started')
                    payload = Command(resume=action) if action else (None if snapshot.values else initial)
                    graph.invoke(payload, config, durability='sync')
                if full:
                    artifacts.verify(graph.get_state(config).values)
                elif scene:
                    artifacts.verify()
                journal.emit('operation_finished')
            except BaseException as exc:
                failures = [e for e in journal.read() if e['kind'] == 'node_failed']
                failed_node = failures[-1]['node'] if failures and failures[-1]['message'] == str(exc) else 'integrity'
                journal.emit('operation_failed', error={
                    'type': type(exc).__name__, 'message': str(exc), 'node': failed_node,
                })
                failed = self._summary(request, graph, config, journal, locked=True)
                atomic_json(directory / 'result.json', failed)
                if full:
                    atomic_json(directory / 'pipeline.json', {'authority': 'projection_only', 'stages': {x['node']: x for x in failed['stages']}})
                raise
            result = self._summary(request, graph, config, journal, locked=True)
            atomic_json(directory / 'result.json', result)
            if full:
                atomic_json(directory / 'pipeline.json', {'authority': 'projection_only', 'stages': {x['node']: x for x in result['stages']}})
            return result

    def show(self, run_id):
        request, directory = self._load(run_id)
        if request['operation'] == 'full_scan' and request['workflow_version'] == 'legacy-full-scan/1':
            from .full_scan import summary
            return summary(self.root, request, directory)
        with self._graph(request, directory) as resources:
            graph, _, journal, config = resources
            return self._summary(request, graph, config, journal)

    def list_runs(self):
        return [self.show(p.parent.name) for p in sorted((self.root / 'runs').glob('*/input.json'))]

    def events(self, run_id, *, after=0):
        _, directory = self._load(run_id)
        return Journal(directory, run_id).read(after)

    def package_return(self, run_id):
        request, directory = self._load(run_id)
        if request['operation'] == 'full_scan' and request['workflow_version'] == 'legacy-full-scan/1':
            from .full_scan import package_return
            return package_return(self, request, directory)
        from .reports import package_return
        return package_return(self, run_id)
