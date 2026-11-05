"""Real authenticated model must execute commands and invoke MCP before authoring."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid
from litereality_agent.agent.providers.codex import CodexHarness
from litereality_agent.agent.providers.base import SessionSpec, SessionResult
from run import redact


async def main():
    checks = Path(os.environ['ROOM_RUN']) / 'checks'
    root = checks / ('codex_execution_' + uuid.uuid4().hex[:8]); root.mkdir(parents=True)
    (root / 'challenge.txt').write_text(uuid.uuid4().hex + '\n')
    expected = hashlib.sha256((root / 'challenge.txt').read_bytes()).hexdigest()
    prompt = '''This is a minimal runtime check, not reconstruction. Do only these steps:
1. Use your command execution tool to run Python in the current directory. Read challenge.txt,
   compute SHA256 of its bytes, and write the hexadecimal digest to command_receipt.txt.
2. Invoke the MCP tool verify_runtime_file from the runtime_probe server exactly once.
   It must be invoked through the MCP tool interface, not by running or importing its server script.
3. Use a command tool to read command_receipt.txt and mcp_receipt.json and verify that the digests match.
4. Report the result. If any tool is unavailable, report failure; do not invent receipts or a success.
Do not inspect credentials, edit scene files, generate images, or install software.'''
    spec = SessionSpec(prompt=prompt, cwd=root, timeout_seconds=480,
        stdio_mcp={'runtime_probe': dict(command=sys.executable,
            args=[str(Path(__file__).with_name('mcp_probe.py')), str(root)])})
    result = dict(status='failed', test_dir=str(root), cli=os.environ['ROOM_CODEX'],
                  command_write_verified=False, mcp_call_verified=False)
    try:
        terminal = None
        with (root / 'events.jsonl').open('w') as stream:
            async for event in CodexHarness().run(spec):
                if isinstance(event, SessionResult):
                    terminal = event
                    raw = dict(type='session_result', is_error=event.is_error, stopped=event.stopped,
                               result=event.result, raw=event.raw)
                else:
                    raw = event.raw
                stream.write(redact(json.dumps(raw, default=str)) + '\n'); stream.flush()
        if terminal is None or terminal.is_error or terminal.stopped:
            raise RuntimeError('Codex execution session failed: ' + (terminal.stopped or terminal.result if terminal else 'no terminal result'))
        result['command_write_verified'] = (root / 'command_receipt.txt').read_text().strip() == expected
        result['mcp_call_verified'] = json.loads((root / 'mcp_receipt.json').read_text())['sha256'] == expected
        # Receipts are physical side effects; a completed prose reply alone never passes.
        if not result['command_write_verified'] or not result['mcp_call_verified']:
            raise RuntimeError('Actual command/MCP receipt verification failed')
        result['status'] = 'passed'
        print('CODEX_EXECUTION_PASS: command write/read and MCP receipt verified', flush=True)
    except BaseException as exc:
        result['error'] = redact(str(exc)); raise
    finally:
        (checks / 'codex_execution.json').write_text(json.dumps(result, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
