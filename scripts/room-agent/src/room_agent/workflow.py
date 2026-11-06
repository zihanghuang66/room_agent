"""Stage-1 graph: saved-source audit only, no reconstruction or visual evaluation."""
import ast
from pathlib import Path
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt

SCHEMA_VERSION = 1
WORKFLOW_VERSION = 'saved-source-audit/1'


class State(TypedDict, total=False):
    run_id: str
    room_id: str
    operation: str
    revision_id: str
    artifact_path: str
    execution_status: str
    quality_status: str
    audit: dict


def build_graph(saver, artifacts, journal):
    def tracked(name, action):
        def node(state):
            journal.emit('node_started', node=name)
            try:
                result = action(state)
            except Exception as exc:
                journal.emit('node_failed', node=name, error_type=type(exc).__name__, message=str(exc))
                raise
            journal.emit('node_completed', node=name)
            return result
        return node

    def snapshot(state):
        return {'artifact_path': artifacts.snapshot(), 'revision_id': state['run_id'],
                'execution_status': 'running'}

    def audit(state):
        artifacts.verify()
        source = Path(state['artifact_path']) / 'source'
        python_files = [p for p in artifacts.request['files'] if p.endswith('.py')]
        for relative in python_files:
            path = source / relative
            ast.parse(path.read_bytes(), filename=relative)
        return {'audit': {'python_files': len(python_files),
                          'file_count': len(artifacts.request['files']),
                          'bytes': sum(f['size'] for f in artifacts.request['files'].values()),
                          'syntax': 'passed', 'build': 'not_run', 'render': 'not_run'},
                'execution_status': 'waiting_for_input'}

    def decision(state):
        choice = interrupt({'kind': 'source_audit_ready', 'run_id': state['run_id'],
                            'actions': ['finish', 'cancel'], 'audit': state['audit'],
                            'quality_status': 'unreviewed'})
        if choice not in ('finish', 'cancel'):
            raise ValueError('invalid action')
        artifacts.verify()
        return {'execution_status': 'cancelled' if choice == 'cancel' else 'completed'}

    graph = StateGraph(State)
    graph.add_node('snapshot', tracked('snapshot', snapshot))
    graph.add_node('audit', tracked('audit', audit))
    graph.add_node('decision', tracked('decision', decision))
    graph.add_edge(START, 'snapshot')
    graph.add_edge('snapshot', 'audit')
    graph.add_edge('audit', 'decision')
    graph.add_edge('decision', END)
    return graph.compile(checkpointer=saver)
