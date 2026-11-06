"""Application interface shared by CLI and future clients."""
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .artifacts import ArtifactStore, inventory
from .storage import (Journal, atomic_json, digest, identifier, read_json,
                      room_busy, room_lock)
from .workflow import SCHEMA_VERSION, WORKFLOW_VERSION, build_graph


class Service:
    def __init__(self, root, *, artifact_type=ArtifactStore):
        self.root = Path(root).resolve()
        self.artifact_type = artifact_type

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
        directory = self.root / 'runs' / run_id
        directory.mkdir(parents=True, exist_ok=False)
        atomic_json(directory / 'input.json', request)
        atomic_json(directory / 'input.sha256.json', digest(request))
        Journal(directory, run_id).emit('run_created', room_id=room_id)
        return run_id

    def _load(self, run_id):
        directory = self.root / 'runs' / identifier(run_id)
        request = read_json(directory / 'input.json')
        if (request.get('schema_version') != SCHEMA_VERSION or
                request.get('workflow_version') != WORKFLOW_VERSION):
            raise ValueError('incompatible workflow/schema version; migration required')
        if request.get('run_id') != run_id or digest(request) != read_json(directory / 'input.sha256.json'):
            raise ValueError('immutable input checksum mismatch')
        identifier(request['room_id'])
        return request, directory

    @contextmanager
    def _graph(self, request, directory):
        journal = Journal(directory, request['run_id'])
        artifacts = self.artifact_type(self.root, request, journal.emit)
        with SqliteSaver.from_conn_string(str(directory / 'checkpoints.sqlite')) as saver:
            graph = build_graph(saver, artifacts, journal)
            yield graph, artifacts, journal, {'configurable': {'thread_id': request['run_id']}}

    def _summary(self, request, graph, config, journal, *, locked=False):
        snapshot = graph.get_state(config)
        state = dict(snapshot.values)
        status = state.get('execution_status', 'created')
        events = journal.read()
        terminal = next((e for e in reversed(events) if e['kind'] in
                         ('run_started', 'operation_failed', 'operation_finished')), None)
        error = None
        if terminal and terminal['kind'] == 'operation_failed':
            status = 'failed'
            error = terminal['error']
        elif terminal and terminal['kind'] == 'run_started':
            active = locked or room_busy(self.root / 'run_activity', request['run_id'])
            status = 'running' if active else 'interrupted'
        elif any(t.interrupts for t in snapshot.tasks):
            status = 'waiting_for_input'
        return {'run_id': request['run_id'], 'room_id': request['room_id'],
                'operation': request['operation'], 'execution_status': status,
                'quality_status': 'unreviewed', 'revision_id': state.get('revision_id'),
                'artifact_path': state.get('artifact_path'), 'audit': state.get('audit'),
                'next_nodes': list(snapshot.next), 'error': error}

    def execute(self, run_id, *, action=None):
        request, directory = self._load(run_id)
        if action is not None and action not in ('finish', 'cancel'):
            raise ValueError('action must be finish or cancel')
        with (room_lock(self.root, request['room_id']),
              room_lock(self.root / 'run_activity', run_id),
              self._graph(request, directory) as resources):
            graph, artifacts, journal, config = resources
            snapshot = graph.get_state(config)
            waiting = any(t.interrupts for t in snapshot.tasks)
            if action is not None and not waiting:
                raise ValueError('run is not waiting for input')
            journal.emit('run_started')
            try:
                # Validate published files even if graph has already completed or stopped before a checkpoint.
                if artifacts.path.exists() or snapshot.values.get('artifact_path'):
                    artifacts.verify()
                if snapshot.values and not snapshot.next:
                    pass
                elif waiting and action is None:
                    pass
                else:
                    initial = {'run_id': run_id, 'room_id': request['room_id'],
                               'operation': request['operation'], 'quality_status': 'unreviewed'}
                    payload = Command(resume=action) if action else (None if snapshot.values else initial)
                    graph.invoke(payload, config, durability='sync')
                journal.emit('operation_finished')
            except Exception as exc:
                failures = [e for e in journal.read() if e['kind'] == 'node_failed']
                failed_node = failures[-1]['node'] if failures and failures[-1]['message'] == str(exc) else 'integrity'
                journal.emit('operation_failed', error={
                    'type': type(exc).__name__, 'message': str(exc), 'node': failed_node,
                })
                atomic_json(directory / 'result.json', self._summary(request, graph, config, journal, locked=True))
                raise
            result = self._summary(request, graph, config, journal, locked=True)
            atomic_json(directory / 'result.json', result)
            return result

    def show(self, run_id):
        request, directory = self._load(run_id)
        with self._graph(request, directory) as resources:
            graph, _, journal, config = resources
            return self._summary(request, graph, config, journal)

    def list_runs(self):
        return [self.show(p.parent.name) for p in sorted((self.root / 'runs').glob('*/input.json'))]

    def events(self, run_id, *, after=0):
        _, directory = self._load(run_id)
        return Journal(directory, run_id).read(after)
