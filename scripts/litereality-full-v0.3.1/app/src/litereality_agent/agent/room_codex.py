"""Codex-only calls for previously direct Claude call sites. No provider fallback."""
import asyncio
import json
import os
import re
from pathlib import Path
import subprocess

from litereality_agent.agent import providers


def redact(text):
    text=str(text)
    for key,value in os.environ.items():
        if len(value)>=8 and any(word in key.upper() for word in ('TOKEN','PASSWORD','SECRET','API_KEY')):
            text=text.replace(value,'[redacted]')
    text=re.sub(r'(?i)(Bearer\s+)[^\s"\']+',r'\1[redacted]',text)
    text=re.sub(r'\bsk-[A-Za-z0-9_-]{8,}','[redacted]',text)
    text=re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@',r'\1[redacted]@',text)
    text=re.sub(r'(?i)([?&](?:token|key|password|signature)=)[^\s&]+',r'\1[redacted]',text)
    return text


def session_diagnostics(terminal):
    if terminal is None:return {'terminal_received':False}
    raw=terminal.raw if isinstance(terminal.raw,dict) else {}
    return {'terminal_received':True,'returncode':raw.get('returncode'),
        'is_error':terminal.is_error,'stopped':redact(terminal.stopped),
        'stderr':redact(raw.get('stderr','')),'last_message':redact(terminal.result),
        'completed_turn':raw.get('completed_turn'),'pending_error':raw.get('pending_error'),
        'stream_errors':json.loads(redact(json.dumps(raw.get('stream_errors',[]))))}


class CodexCallError(RuntimeError):
    def __init__(self,terminal):
        super().__init__('Codex session incomplete; inspect diagnostics, no Claude fallback')
        self.diagnostics=session_diagnostics(terminal)


def parse_json(text, schema=None):
    text=text.strip()
    if text.startswith('```'):
        lines=text.splitlines();text='\n'.join(lines[1:-1]).strip()
    value=json.loads(text)
    if schema is not None:
        from jsonschema import validate
        validate(value,schema)
    return value


async def ask(prompt, *, cwd, images=(), read_only=True, timeout=180, step_budget=24):
    paths=tuple(Path(p).resolve() for p in images)
    if any(not p.is_file() for p in paths):raise FileNotFoundError('Required vision image is missing')
    prompt='Use attached images as visual evidence. For additional image paths, use your image-viewing tool; legacy instructions naming Read refer to viewing the image, not merely reading its bytes.\n\n'+prompt
    spec=providers.SessionSpec(prompt=prompt,cwd=Path(cwd).resolve(),read_only=read_only,
        image_paths=paths,read_roots=tuple({p.parent for p in paths}),setting_sources=(),
        timeout_seconds=timeout,step_budget=step_budget)
    harness=providers.resolve(override='codex')
    terminal=None
    async for message in harness.run(spec):
        if isinstance(message,providers.SessionResult):terminal=message
    if terminal is None or terminal.is_error or terminal.stopped or not terminal.result.strip():
        raise CodexCallError(terminal)
    return terminal.result


def run_text(prompt, *, cwd, images=(), read_only=True, timeout=180):
    text=asyncio.run(ask(prompt,cwd=cwd,images=images,read_only=read_only,timeout=timeout))
    return subprocess.CompletedProcess(['codex','exec'],0,stdout=text,stderr='')
