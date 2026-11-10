"""Full-scan scheduling: LangGraph is the only cross-stage recovery authority."""
import time
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt

VERSION='full-scan-graph/1'
STAGES=('prepare','preflight','ingest','reconstruct','seed','author','publish','export_check')
EXTRA_STAGES=('author_decision','author_repair','draft_export')


def build_scan_graph(saver,executor,journal,request):
    graph=StateGraph(dict)
    def stage(name):
        def call(state):
            started=time.monotonic();journal.emit('node_started',node=name)
            print(f'[{name}] RUNNING',flush=True)
            try:result=executor.step(name,state)
            except BaseException as exc:
                journal.emit('node_failed',node=name,message=str(exc),error_type=type(exc).__name__,seconds=time.monotonic()-started)
                print(f'[{name}] FAILED: {exc}',flush=True)
                raise
            journal.emit('node_completed',node=name,seconds=time.monotonic()-started)
            merged={**state,**result,'execution_status':'running'}
            report=result.get('author_review')
            if report is not None:
                info=report.get('closeout',{})
                merged.update(author_review=report,quality_status='passed' if report.get('accepted') else 'not_passed',
                    round=info.get('batches',state.get('round',0)),score=report.get('visual_review',{}).get('score'),
                    issues=report.get('visual_review',{}).get('issues',[]),repair_termination=info.get('repair_termination',state.get('repair_termination','not_started')),
                    best_candidate=info.get('best_candidate',state.get('best_candidate')),completion_reason=None)
                if report.get('accepted'):merged['last_stage']='author' if name=='author_repair' else name
                if name=='publish' and not report.get('accepted'):merged['completion_reason']='final_publish_not_passed'
                return merged
            if name=='draft_export':
                return {**merged,'execution_status':'completed','quality_status':'not_passed'}
            finished=name=='export_check' or (name=='prepare' and request['prepare_only'])
            return {**merged,'last_stage':name,'execution_status':'completed' if finished else 'running',
                    'quality_status':'human_review_pending' if name=='export_check' else state.get('quality_status','unreviewed'),
                    'completion_reason':('prepared_only' if request['prepare_only'] else 'pipeline_completed_human_review_pending') if finished else None}
        return call
    def decision(state):
        info=state.get('author_review',{}).get('closeout',{})
        reason=info.get('stop_reason')
        if reason:
            journal.emit('decision_finished',node='author_decision',reason=reason,automatic=True)
            return {**state,'next_action':'finish','completion_reason':reason}
        choice=interrupt({'kind':'author_review_ready','actions':['repair','finish'],
                          'round':state.get('round',0),'issues':state.get('issues',[])})
        if choice not in ('repair','finish'):raise ValueError('action must be repair or finish')
        journal.emit('decision_finished',node='author_decision',action=choice,automatic=False)
        return {**state,'next_action':choice,'completion_reason':'user_finished' if choice=='finish' else None}
    for name in (*STAGES,'author_repair','draft_export'):graph.add_node(name,stage(name))
    graph.add_node('author_decision',decision)
    graph.add_edge(START,'prepare')
    graph.add_edge('prepare',END if request['prepare_only'] else 'preflight')
    for a,b in zip(STAGES[1:5],STAGES[2:6]):graph.add_edge(a,b)
    for node in ('author','author_repair'):
        graph.add_conditional_edges(node,lambda s:'author_decision' if s.get('quality_status')=='not_passed' else 'publish')
    graph.add_conditional_edges('author_decision',lambda s:'author_repair' if s['next_action']=='repair' else 'draft_export')
    graph.add_conditional_edges('publish',lambda s:'draft_export' if s.get('quality_status')=='not_passed' else 'export_check')
    graph.add_edge('draft_export',END)
    graph.add_edge('export_check',END)
    return graph.compile(checkpointer=saver)
