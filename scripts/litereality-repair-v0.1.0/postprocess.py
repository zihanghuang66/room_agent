"""Geometry gate and photo pairs, run with the prepared app Python."""
import json
from pathlib import Path
import sys
from repair_core import save
mode,job_path=sys.argv[1:];job=json.loads(Path(job_path).read_text())
if mode=='geometry':
    from litereality_agent.pipeline.room_qc.validate import validate
    result=validate(Path(job['room']),Path(job['preview']))
    save(job['report'],result)
    if result.get('pass') is not True:print('GEOMETRY_NEEDS_REPAIR',flush=True)
    else:print('GEOMETRY_PASS',flush=True)
elif mode=='pairs':
    from litereality_agent.room_ops.rendering.room_render.render_vs_capture import _make_pairs,_contact_sheet
    out=Path(job['out']);pairs=_make_pairs(Path(job['capture']),out/'renders',out/'pairs',height=600)
    if len(pairs)!=len(job['frames']):raise RuntimeError('Missing comparison pairs')
    _contact_sheet(pairs,out/'contact_sheet.png',n=len(pairs))
    save(out/'pairs.json',[str(p) for p in pairs])
else:raise ValueError(mode)
