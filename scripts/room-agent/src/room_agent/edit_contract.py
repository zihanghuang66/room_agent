"""Small deterministic boundaries; model judgment is guided by spatial-edit Skill."""
import hashlib
import json
import re
from pathlib import Path


def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def finding_catalog(current):
    groups=[(current.get('run_id'),False,current.get('edit_issues') or current.get('issues') or [])]
    baseline=current.get('baseline_quality') or {}
    groups.append((baseline.get('run_id'),True,baseline.get('issues') or []))
    records=[];total=0;used=0
    for run,historical,issues in groups:
        report=fingerprint(issues)
        for index,issue in enumerate(issues):
            total+=1
            if not isinstance(issue,dict):continue
            detail=issue.get('detail',issue.get('defect',''))
            ids=issue.get('objects',issue.get('object_ids',[]))
            if not isinstance(detail,str) or not isinstance(ids,list) or not all(isinstance(i,str) for i in ids):continue
            row=dict(finding_id='f-'+fingerprint([run,report,index])[:20],source_run_id=run,report_digest=report,
                     historical=historical,object_ids=ids,detail=detail,kind=issue.get('kind'),priority=issue.get('priority'),
                     views=sorted(set(re.findall(r'pair_\d+\.png',detail))))
            size=len(json.dumps(row,ensure_ascii=False))
            # Preserve whole records; disclose omissions instead of splitting prose.
            if len(records)>=60 or used+size>24000:continue
            records.append(row);used+=size
    return {'records':records,'total':total,'omitted':total-len(records)}


def bind_evidence(plan,facts):
    targets=plan.get('target_ids',[]);ids=plan.get('finding_ids',[])
    for values in (targets,ids):
        if not isinstance(values,list) or len(values)>10 or not all(isinstance(v,str) and v.strip() for v in values) or len(set(values))!=len(values):
            raise ValueError('Invalid target/evidence binding')
    catalog={r['finding_id']:r for r in facts.get('finding_catalog',{}).get('records',[])}
    if not set(ids)<=set(catalog):raise ValueError('Plan cites an unavailable finding')
    selected=[catalog[i] for i in ids]
    if selected:
        allowed={o for row in selected for o in row['object_ids']}
        if not targets or not set(targets)<=allowed:raise ValueError('Selected finding does not bind the proposed target')
    return {'target_ids':targets,'finding_ids':ids,'findings':selected}


def stop(reason,questions=None,status='needs_clarification'):
    return dict(gate_status=status,stop_before_build=True,completion_reason=status,edit_status=status,
                gate_reason=reason,clarification_questions=questions or ['Please clarify the missing target, evidence or permitted edit scope.'])


def evidence_valid(rows,source):
    if not isinstance(rows,list) or not rows:return False
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('path'),str) or not isinstance(row.get('quote'),str) or not row['quote'].strip():return False
        rel=Path(row['path']);p=source/rel
        if rel.is_absolute() or '..' in rel.parts or rel.suffix not in ('.py','.json'):return False
        if any(x.is_symlink() for x in [p,*p.parents]) or not p.is_file():return False
        try:
            if row['quote'] not in p.read_text():return False
        except (OSError,UnicodeError):return False
    return True


def readiness(raw,request,source):
    if not isinstance(raw,dict):return stop('The inspection did not establish execution prerequisites.')
    reason=raw.get('reason');reason=reason.strip() if isinstance(reason,str) else ''
    questions=raw.get('questions');questions=questions if isinstance(questions,list) and all(isinstance(q,str) and q.strip() for q in questions) and len(questions)<=3 else []
    if raw.get('status') in ('needs_clarification','blocked'):
        return stop(reason or 'A prerequisite is missing.',questions,raw['status'])
    targets=raw.get('target_ids')
    if not isinstance(targets,list) or not targets or not all(isinstance(t,str) and t for t in targets):
        return stop('The target was not uniquely resolved.',questions)
    bound=request.get('edit_contract',{}).get('target_ids') or []
    if bound and set(bound)!=set(targets):return stop('Inspection target differs from the confirmed plan.',questions)
    if (raw.get('status')!='ready' or not reason or questions or
        any(raw.get(k) is not True for k in ('target_unique','evidence_sufficient','capability_supported')) or
        not evidence_valid(raw.get('evidence'),source)):
        return stop(reason or 'Target, evidence or editing capability is not established.',questions)
    return dict(gate_status='ready',stop_before_build=False,resolved_target_ids=targets)


def delta_outcome(before,after,report,expected_targets=None):
    changed=sorted(k for k in set(before)|set(after) if before.get(k)!=after.get(k))
    report=report if isinstance(report,dict) else {}
    reason=report.get('summary');reason=reason if isinstance(reason,str) and reason.strip() else 'The editor did not provide a usable completion report.'
    questions=report.get('questions');questions=questions if isinstance(questions,list) and len(questions)<=3 and all(isinstance(q,str) and q.strip() for q in questions) else []
    if not changed:
        result=stop(reason,questions,'needs_clarification')
    elif expected_targets and (not isinstance(report.get('target_ids'),list) or
        not all(isinstance(t,str) for t in report['target_ids']) or set(report['target_ids'])!=set(expected_targets)):
        result=stop('Saved candidate target report differs from the confirmed target.',questions,'blocked')
    elif report.get('status')!='changed':
        result=stop('Partial candidate retained. '+reason,questions,'blocked')
    else:
        result=dict(stop_before_build=False,gate_status='changed')
    result['actual_changed_files']=changed
    return result
