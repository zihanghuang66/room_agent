"""One bounded Codex editing or independent review session; no full pipeline calls."""
import asyncio
import json
import os
from pathlib import Path
import sys
from dataclasses import asdict
from repair_core import save
from runtime_support import redact

async def main():
    from litereality_agent.agent import providers
    from litereality_agent.agent.room_codex import parse_json,session_diagnostics
    mode,job_path=sys.argv[1:];job=json.loads(Path(job_path).read_text())
    room=Path(job['room']);report=Path(job['report']);report.parent.mkdir(parents=True,exist_ok=True)
    prompt=Path(job['prompt']).read_text()
    readonly=mode=='review'
    spec=providers.SessionSpec(prompt=prompt,cwd=report.parent if readonly else room,
        capability_scene=room,capability_tools=() if readonly else ('render','select_views','check_collisions'),
        read_roots=(Path(job['capture']),Path(job['evidence'])),
        image_paths=tuple(Path(p) for p in job.get('images',[])),read_only=readonly,
        setting_sources=(),timeout_seconds=job.get('seconds',2400),
        step_budget=job.get('steps',140),max_turns=160)
    terminal=None;uses=[]
    with report.with_suffix('.events.jsonl').open('w') as stream:
        async for event in providers.resolve(override='codex').run(spec):
            raw=event.raw if getattr(event,'raw',None) is not None else asdict(event)
            stream.write(redact(json.dumps(raw,default=str))+'\n');stream.flush()
            if isinstance(event,providers.SessionResult):terminal=event
            for block in getattr(event,'content',[]):
                if isinstance(block,providers.ToolUseBlock):
                    uses.append(block.name);print('TOOL '+block.name,flush=True)
                elif isinstance(block,providers.TextBlock):print(redact(block.text)[:1200],flush=True)
    result={'session_ok':terminal is not None and not terminal.is_error and not terminal.stopped,
        'diagnostics':session_diagnostics(terminal),'tool_calls':len(uses),'tools':uses}
    save(report,result)
    if not result['session_ok']:raise RuntimeError(json.dumps(result['diagnostics']))
    if readonly:
        result['review']=parse_json(terminal.result)
        if not isinstance(result['review'],dict):raise ValueError('Expected review JSON object')
    elif not uses:raise RuntimeError('No tool calls; repair cannot pass on a prose-only response')
    save(report,result)
    print('SESSION_COMPLETED '+mode,flush=True)

if __name__=='__main__':asyncio.run(main())
