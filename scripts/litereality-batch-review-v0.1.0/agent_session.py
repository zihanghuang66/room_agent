"""Record separate Codex sessions without pretending network/think time is observable."""
import asyncio
import dataclasses
import json
import time
from datetime import datetime,timezone

from batch_core import EventTiming
from batch_io import save
from full_support import redact


def error_detail(raw):
    """Retain diagnostic fields without copying arbitrary headers or credential objects."""
    fields = {'type', 'message', 'code', 'error', 'item', 'status', 'status_code',
              'http_status', 'retry_after', 'attempt', 'request_id'}
    def clean(value, depth=0):
        if depth > 5:
            return '[truncated]'
        if isinstance(value, dict):
            return {k: clean(v, depth+1) for k, v in value.items() if k in fields}
        if isinstance(value, list):
            return [clean(v, depth+1) for v in value[:10]]
        if isinstance(value, str):
            return redact(value)[:6000]
        return value if value is None or isinstance(value, (int, float, bool)) else redact(str(value))[:6000]
    return clean(raw)


async def recorded_session(spec,folder,role):
    from litereality_agent.agent import providers as p
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'prompt.txt').write_text(spec.prompt)
    harness=p.resolve(role,'codex');clock=time.monotonic;start=clock()
    timing=EventTiming();terminal=None;error='';seq=0;stream_errors=[]
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
                    if isinstance(event.raw,dict):
                        row['raw_event_type']=event.raw.get('type')
                        item=event.raw.get('item')
                        if (event.raw.get('type') in ('error','turn.failed') or
                                isinstance(item,dict) and item.get('type') == 'error'):
                            detail=error_detail(event.raw)
                            row['error_detail']=detail
                            stream_errors.append(dict(sequence=seq,elapsed_seconds=elapsed,detail=detail))
                            print('['+role+'] CODEX_STREAM_ERROR '+json.dumps(detail,ensure_ascii=False),flush=True)
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
        elapsed_seconds=clock()-start,stream_error_count=len(stream_errors),
        stream_errors=stream_errors,completed_after_stream_errors=termination=='completed' and bool(stream_errors))
    save(folder/'result.json',result)
    return result
