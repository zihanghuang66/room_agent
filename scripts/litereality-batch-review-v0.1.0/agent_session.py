"""Record separate Codex sessions without pretending network/think time is observable."""
import asyncio
import dataclasses
import json
import time
from datetime import datetime,timezone

from batch_core import EventTiming
from batch_io import save
from full_support import redact


async def recorded_session(spec,folder,role):
    from litereality_agent.agent import providers as p
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'prompt.txt').write_text(spec.prompt)
    harness=p.resolve(role,'codex');clock=time.monotonic;start=clock()
    timing=EventTiming();terminal=None;error='';seq=0
    async def heartbeat():
        while True:
            await asyncio.sleep(30)
            print('['+role+'] waiting/running '+str(round(clock()-start))+'s',flush=True)
    task=asyncio.create_task(heartbeat())
    try:
        with (folder/'events.jsonl').open('w') as f:
            async for event in harness.run(spec):
                elapsed=clock()-start;seq+=1
                row=dict(sequence=seq,elapsed_seconds=elapsed,event=type(event).__name__,
                         utc=datetime.now(timezone.utc).isoformat())
                if isinstance(event,p.SessionResult):
                    terminal=event;row['result']=dataclasses.asdict(event)
                else:
                    blocks=[]
                    for block in event.content:
                        if isinstance(block,p.ToolUseBlock):
                            timing.start(block.id,block.name,elapsed)
                            print('['+role+'] tool '+block.name,flush=True)
                        elif isinstance(block,p.ToolResultBlock):timing.end(block.tool_use_id,elapsed)
                        if dataclasses.is_dataclass(block):blocks.append(dataclasses.asdict(block))
                    row['blocks']=blocks
                    if isinstance(event.raw,dict):row['raw_event_type']=event.raw.get('type')
                encoded=redact(json.dumps(row,default=str,ensure_ascii=False))
                # No credentials/rollout files are copied. Preserve event boundaries even for huge outputs.
                if len(encoded)>100000:
                    encoded=json.dumps(dict(sequence=seq,elapsed_seconds=elapsed,event=row['event'],
                        truncated_payload=encoded[:99000]),ensure_ascii=False)
                f.write(encoded+'\n');f.flush()
    except Exception as exc:
        error=f'{type(exc).__name__}: {exc}'
    finally:
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass
        save(folder/'timing.json',timing.summary(clock()-start))
    reason=(terminal.stopped if terminal else '') or error
    termination='completed'
    if 'wall time budget' in reason or 'TimeoutError' in reason:termination='timeout'
    elif 'step budget' in reason:termination='step_limit'
    elif terminal is None or terminal.is_error or terminal.stopped or error:termination='execution_error'
    result=dict(termination=termination,reason=redact(reason),
        text=redact(terminal.result if terminal else ''),model=harness.effective_model(spec),
        elapsed_seconds=clock()-start)
    save(folder/'result.json',result)
    return result
