"""Replay the school's actual nested error; no login, model, or network needed."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

APP = Path(os.environ.get('ROOM_TEST_APP', str(Path(__file__).resolve().parents[1] /
           'vendor/litereality-agent-28ffe1f-room-codex-v0.1.2')))
sys.path.insert(0, str(APP / 'src'))
from litereality_agent.agent.providers.base import SessionSpec, SessionResult
from litereality_agent.agent.providers.codex import CodexHarness

ERROR = {'type': 'item.completed', 'item': {'id': 'item_0', 'type': 'error',
    'message': 'Code Mode is unavailable because failed to spawn code-mode host '
               '/opt/room/tools/codex/codex-code-mode-host: host executable was not found.'}}
DONE = [{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Blocked; no files changed.'}},
        {'type': 'turn.completed', 'usage': {'input_tokens': 12}}]


class RuntimeRegression(unittest.TestCase):
    def replay(self, events, sleep=0):
        real_spawn = asyncio.create_subprocess_exec
        self.argv = None
        async def spawn(*args, **kwargs):
            self.argv = args
            program = 'import json,time\n'
            for event in events:
                program += 'print(' + repr(json.dumps(event)) + ',flush=True)\n'
            program += f'time.sleep({sleep})\n'
            return await real_spawn(sys.executable, '-u', '-c', program, **kwargs)
        async def collect():
            return [x async for x in CodexHarness().run(SessionSpec(
                prompt='TEST FIXTURE', cwd=Path('.'), timeout_seconds=1))]
        with patch('shutil.which', return_value='/fallback/codex'), patch.object(
                asyncio, 'create_subprocess_exec', spawn), patch.dict(os.environ, ROOM_CODEX='/verified/bin/codex'):
            events = asyncio.run(collect())
        return next(x for x in events if isinstance(x, SessionResult))

    def test_nested_runtime_error_is_not_successful_completed_chat(self):
        result = self.replay([ERROR] + DONE)
        self.assertTrue(result.is_error)
        self.assertIn(ERROR, result.raw['stream_errors'])

    def test_runtime_error_stops_immediately(self):
        started = time.monotonic()
        result = self.replay([ERROR], sleep=30)
        self.assertLess(time.monotonic() - started, 0.9)
        self.assertIn('host executable was not found', result.stopped)

    def test_harness_uses_verified_absolute_cli(self):
        self.replay(DONE)
        self.assertEqual(self.argv[0], '/verified/bin/codex')

    def test_successful_turn_remains_supported(self):
        result = self.replay(DONE)
        self.assertFalse(result.is_error)

    def test_incomplete_turn_fails(self):
        result = self.replay(DONE[:1])
        self.assertTrue(result.is_error)


if __name__ == '__main__':
    unittest.main()
