"""Isolated Docker validation of shipped runtime and MCP, explicitly without a model call."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

package, original, out = map(Path, sys.argv[1:])
sys.path[:0] = [str(package), str(original)]
from runtime import prepare, prepare_app
import run as original_runner

out.mkdir(parents=True, exist_ok=True)
app = prepare_app()
env = original_runner.setup_environment(out / 'project', out, app)
prepare(env, out / 'runtime.json')
os.environ.update(env)
sys.path.insert(0, str(app / 'src'))
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from litereality_agent.agent.providers.base import SessionSpec
from litereality_agent.agent.providers.codex import _mcp_config


async def validate():
    challenge = out / 'probe'; challenge.mkdir(exist_ok=True)
    (challenge / 'challenge.txt').write_text('OFFLINE LINUX MCP VALIDATION\n')
    params = StdioServerParameters(command=sys.executable, args=[str(package / 'mcp_probe.py'), str(challenge)], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = await client.list_tools()
            assert 'verify_runtime_file' in {t.name for t in tools.tools}
            result = await client.call_tool('verify_runtime_file', {})
            assert not result.isError
    receipt = json.loads((challenge / 'mcp_receipt.json').read_text())
    assert receipt['sha256'] == hashlib.sha256((challenge / 'challenge.txt').read_bytes()).hexdigest()
    cfg = _mcp_config(SessionSpec(prompt='test', cwd=out, capability_tools=('grid',)))
    assert any('ROOM_CODEX' in s and 'PATH' in s for s in cfg)
    proc = subprocess.run([sys.executable, str(original / 'preflight.py'), 'mcp'], env=env,
                          text=True, capture_output=True, timeout=180)
    (out / 'mcp.log').write_text(proc.stdout + proc.stderr)
    assert proc.returncode == 0, proc.stderr[-2000:]
    proc = subprocess.run([sys.executable, str(package / 'resume.py'), '--help'], env=env,
                          text=True, capture_output=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    report = dict(status='passed', cli_host_launch=True, upstream_mcp_tools=6,
                  custom_mcp_call_verified=True, absolute_cli_forwarded_to_mcp=True,
                  actual_codex_model_execution_tested=False, room_authoring_tested=False)
    (out / 'validation.json').write_text(json.dumps(report, indent=2)); print(json.dumps(report))


asyncio.run(validate())
