"""Saved-scene nodes with explicit quality/technical outcomes and human repair choice."""
import time
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt

STAGES = ('snapshot', 'preflight', 'build', 'geometry', 'evidence', 'render', 'review', 'decision', 'repair')


def actionable(state):
    return [x for x in state.get('issues', []) if x.get('priority') in
            ('support', 'lighting', 'large_appearance', 'small_details')]


def can_repair(state, request):
    return (state.get('round', 0) < request['max_batches'] and
            state.get('quality_status') == 'not_passed' and bool(actionable(state)) and
            state.get('repair_termination', 'not_started') in ('not_started', 'completed'))


def build_scene_graph(saver, executor, journal, request):
    def stage(name):
        def call(state):
            start = time.monotonic()
            number = state.get('round', 0)
            journal.emit('node_started', node=name, round=number)
            print(f'[{name}] RUNNING round={number}', flush=True)
            try:
                update = executor.step(name, state)
            except BaseException as exc:
                journal.emit('node_failed', node=name, round=number, message=str(exc),
                             error_type=type(exc).__name__, seconds=time.monotonic()-start)
                print(f'[{name}] FAILED\n{type(exc).__name__}: {exc}', flush=True)
                raise
            journal.emit('node_completed', node=name, round=number, seconds=time.monotonic()-start)
            print(f'[{name}] COMPLETED round={number}', flush=True)
            result = {**state, **update, 'execution_status': 'running'}
            if name == 'repair':
                for field in ('room', 'preview', 'round_dir', 'build_stamp', 'selection', 'pairs',
                              'compare', 'geometry_report', 'geometry_pass', 'visual_review'):
                    result.pop(field, None)
                result.update(round=number+1, quality_status='unreviewed', score=None, issues=[])
            return result
        return call

    def decision(state):
        if not can_repair(state, request):
            return {**state, 'execution_status': 'completed', 'next_action': 'finish'}
        choice = interrupt({'kind': 'review_ready', 'quality_status': state['quality_status'],
                            'round': state.get('round', 0), 'actions': ['repair', 'finish'],
                            'issues': state.get('issues', [])})
        if choice not in ('repair', 'finish'):
            raise ValueError('action must be repair or finish')
        return {**state, 'next_action': choice,
                'execution_status': 'completed' if choice == 'finish' else 'running'}

    graph = StateGraph(dict)
    for name in STAGES:
        graph.add_node(name, decision if name == 'decision' else stage(name))
    graph.add_edge(START, 'snapshot')
    for left, right in [('snapshot','preflight'), ('preflight','build'), ('build','geometry'),
                        ('evidence','render'), ('render','review'), ('review','decision'), ('repair','build')]:
        graph.add_edge(left, right)
    graph.add_conditional_edges('geometry', lambda s: 'evidence' if s.get('geometry_pass') else 'decision')
    graph.add_conditional_edges('decision', lambda s: 'repair' if s['next_action'] == 'repair' else END)
    return graph.compile(checkpointer=saver)
