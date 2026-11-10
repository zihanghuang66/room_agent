"""An explicit edit produces one child version; no hidden quality repair loop."""
import time
from langgraph.graph import StateGraph, START, END
from .revisions import VERSION

STAGES = ('snapshot','preflight','baseline_build','evidence','baseline_render','edit','build','geometry','render','review','finalize')


def build_edit_graph(saver,executor,journal,request):
    def stage(name):
        def call(state):
            start=time.monotonic();journal.emit('node_started',node=name)
            print('['+name+'] RUNNING',flush=True)
            try:
                update=executor.step(name,state)
            except BaseException as exc:
                journal.emit('node_failed',node=name,message=str(exc),error_type=type(exc).__name__,seconds=time.monotonic()-start)
                raise
            journal.emit('node_completed',node=name,seconds=time.monotonic()-start)
            print('['+name+'] COMPLETED',flush=True)
            return {**state,**update,'execution_status':'completed' if name=='finalize' or update.get('no_change_verified') is True or (request.get('edit_contract') and update.get('stop_before_build') is True) else 'running'}
        return call
    graph=StateGraph(dict)
    for name in STAGES:graph.add_node(name,stage(name))
    if request.get('current_source_check'):
        graph.add_node('inspect_current',stage('inspect_current'))
    graph.add_edge(START,STAGES[0])
    for left,right in zip(STAGES,STAGES[1:]):
        if left=='preflight' and request.get('current_source_check'):
            graph.add_edge(left,'inspect_current')
            graph.add_conditional_edges('inspect_current',lambda s: 'done' if s.get('no_change_verified') is True or (request.get('edit_contract') and s.get('stop_before_build') is True) else 'continue',{'done':END,'continue':right})
        elif left=='edit' and request.get('edit_contract'):
            graph.add_conditional_edges(left,lambda s:'done' if s.get('stop_before_build') is True else 'continue',{'done':END,'continue':right})
        else:graph.add_edge(left,right)
    graph.add_edge(STAGES[-1],END)
    return graph.compile(checkpointer=saver)


def project(request,state,events,status):
    stages=[]
    nodes=list(STAGES)
    if request.get('current_source_check'):nodes.insert(2,'inspect_current')
    for node in nodes:
        event=next((e for e in reversed(events) if e.get('node')==node and e['kind'] in ('node_started','node_completed','node_failed')), {})
        value={'node_started':'running','node_completed':'completed','node_failed':'failed'}.get(event.get('kind'),'pending')
        if (state.get('no_change_verified') is True or state.get('stop_before_build') is True) and value=='pending':value='skipped'
        if value=='running' and status in ('failed','interrupted'):value=status
        stages.append(dict(node=node,status=value,seconds=event.get('seconds')))
    result=dict(stages=stages,parent_run_id=request['parent_run_id'],instruction=request['instruction'],
                current_node='finished' if status=='completed' else next((e.get('node') for e in reversed(events) if e['kind']=='node_started'),None),
                score=state.get('score'),issues=state.get('issues',[]),allowed_actions=[],
                edit_termination=state.get('edit_termination','not_started'),
                completion_reason=state.get('completion_reason'),ingest_reconstruct_rerun=False,
                technical_export_verified=state.get('technical_export_verified',False))
    result.update({key:state[key] for key in ('edit_status','edit_score','edit_issues','change_verified','change_evidence',
                   'stop_before_build','gate_status','gate_reason','clarification_questions','actual_changed_files','resolved_target_ids','no_change_verified','no_change_evidence','scope_evidence','affected_objects','baseline_quality','whole_room_review') if key in state})
    return result
