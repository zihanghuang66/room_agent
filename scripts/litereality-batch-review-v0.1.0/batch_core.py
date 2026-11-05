"""Pure policy for saved-scene review; no scene-specific IDs or frame numbers."""
import json
import math

FOCUS = ('headboard', 'bedside_cabinet', 'curtains', 'bedding', 'ground_contact')
PRIORITY = ('support', 'lighting', 'large_appearance', 'small_details')


def parse_json(text):
    text = text.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


def review_passes(review, object_ids):
    score = review.get('score')
    return (review.get('pass') is True and type(score) in (int, float)
            and math.isfinite(score) and 8 <= score <= 10
            and review.get('all_objects_reviewed') is True
            and review.get('issues') == []
            and isinstance(review.get('reviewed_object_ids'), list)
            and set(object_ids) <= set(review['reviewed_object_ids']))


def validate_review(d, object_ids):
    if (type(d.get('pass')) is not bool or type(d.get('all_objects_reviewed')) is not bool
            or type(d.get('score')) not in (int,float) or not math.isfinite(d['score'])
            or not 0 <= d['score'] <= 10 or not isinstance(d.get('issues'),list)
            or not isinstance(d.get('reviewed_object_ids'),list)
            or any(not isinstance(x,str) for x in d['reviewed_object_ids'])):
        raise ValueError('Invalid independent review schema')
    if set(d['reviewed_object_ids']) - set(object_ids):
        raise ValueError('Review names an unknown object')
    for x in d['issues']:
        if (not isinstance(x,dict) or x.get('priority') not in (*PRIORITY,'evidence')
                or not isinstance(x.get('object_ids'),list)
                or any(not isinstance(v,str) for v in x['object_ids'])
                or not isinstance(x.get('defect'),str) or not x['defect'].strip()):
            raise ValueError('Invalid review issue')
        if set(x['object_ids']) - set(object_ids):
            raise ValueError('Issue names an unknown object')
    return d


def select_frames(selection, actual_ids):
    ids=sorted(set(actual_ids))
    if not ids:raise ValueError('No capture frames')
    targets=selection.get('targets',{})
    if set(targets)!=set(FOCUS):raise ValueError('Missing requested evidence targets')
    chosen={ids[round(i*(len(ids)-1)/5)] for i in range(6)}
    missing=[]
    for name in FOCUS:
        item=targets[name]; frames=item.get('frames')
        if (not isinstance(frames,list) or len(frames)>2
                or any(type(f) is not int or f not in ids for f in frames)):
            raise ValueError('Invalid or nonexistent capture frame for '+name)
        if item.get('visibility') not in ('visible','partial','unobservable','absent'):
            raise ValueError('Missing visibility state for '+name)
        if not isinstance(item.get('reason'),str) or not item['reason'].strip():
            raise ValueError('Missing evidence-selection reason')
        if item['visibility'] in ('visible','partial') and not frames:
            raise ValueError('Visible target needs frame evidence')
        if item['visibility'] in ('unobservable','absent') and frames:
            raise ValueError('Unobservable/absent target must not assert selected views')
        if item['visibility']=='unobservable':missing.append(name)
        chosen.update(frames)
    return dict(frames=sorted(chosen),targets=targets,unobservable=missing,
                actual_frame_count=len(ids),method='all-capture thumbnail inspection + six global anchors',
                limitation='Model selection is evidence-guided, not an optimal-view guarantee; reviewer must confirm visibility.')


def choose_issues(issues, limit=3):
    actionable=[x for x in issues if x.get('priority') in PRIORITY]
    if any(x['priority']=='support' for x in actionable):
        actionable=[x for x in actionable if x['priority']=='support']
    elif any(x['priority'] in ('lighting','large_appearance') for x in actionable):
        actionable=[x for x in actionable if x['priority'] in ('lighting','large_appearance')]
    return sorted(actionable,key=lambda x:PRIORITY.index(x['priority']))[:limit]


def geometry_quality(report):
    if report.get('ok') is not True or report.get('finite_support',{}).get('error'):
        return 'check_failed'
    technical=('unavailable','unchecked','check_failed')
    if any(any(word in str(x.get('kind','')) for word in technical) for x in report.get('failing',[])):
        return 'check_failed'
    return 'passed' if report.get('pass') is True else 'not_passed'


def status_for(quality, termination='completed'):
    if quality=='check_failed':return 'check_failed'
    if quality=='passed':return 'accepted'
    return {'timeout':'repair_timeout','step_limit':'repair_step_limit',
            'execution_error':'execution_error'}.get(termination,'quality_not_passed')


def cycle(assess, repair, max_batches, progress=lambda value:None):
    history=[]; session={'termination':'not_started'}
    for i in range(max_batches+1):
        assessment=assess(i)
        history.append(dict(round=i,assessment=assessment,repair_session=session))
        result=dict(status=status_for(assessment['quality'],session['termination']),
                    quality=assessment['quality'],repair_termination=session['termination'],
                    history=history,final_round=i)
        progress(result)
        if assessment['quality'] in ('passed','check_failed') or i==max_batches:
            return result
        if session['termination'] in ('timeout','step_limit','execution_error'):
            return result
        selected=choose_issues(assessment.get('issues',[]))
        if not selected:
            result['note']='Evidence insufficient or no actionable defect; do not invent hidden geometry.'
            progress(result);return result
        session=repair(i+1,selected)
    raise AssertionError('unreachable')


class EventTiming:
    def __init__(self):
        self.open={};self.rows=[];self.unmatched_results=0

    def start(self,id,name,t):
        if id not in self.open:self.open[id]=(name,t)

    def end(self,id,t):
        if id not in self.open:
            self.unmatched_results+=1;return
        name,start=self.open.pop(id)
        self.rows.append(dict(id=id,name=name,start=start,end=t,completed=True))

    def summary(self,total):
        rows=self.rows+[dict(id=k,name=n,start=s,end=total,completed=False) for k,(n,s) in self.open.items()]
        intervals=sorted((max(0,r['start']),min(total,r['end'])) for r in rows)
        end=0.;union=0.
        for a,b in intervals:
            union+=max(0,b-max(a,end));end=max(end,b)
        return dict(wall_seconds=total,observed_tool_seconds=union,
                    unclassified_non_tool_seconds=max(0,total-union),tools=rows,
                    unmatched_results=self.unmatched_results,
                    limitation='Intervals are observed stream times. Residual combines model/network/queue/CLI overhead; it is not pure model thinking time.')
