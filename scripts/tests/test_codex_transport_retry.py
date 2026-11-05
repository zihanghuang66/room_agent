"""Transport replay: never replay a session after a tool/action event."""
import asyncio
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(os.environ['ROOM_TEST_APP'])/'src'))
from litereality_agent.agent.providers.base import SessionSpec, SessionResult, AgentMessage, ToolUseBlock
from litereality_agent.agent.providers.codex import CodexHarness

class TransportTests(unittest.TestCase):
    def replay(self, failure, messages=(), always=False):
        self.calls=0
        async def once(harness,spec):
            self.calls+=1
            if self.calls==1 or always:
                for m in messages: yield m
                yield SessionResult(is_error=True,stopped=failure,raw={})
            else: yield SessionResult(result='done',raw={})
        async def collect():
            return [x async for x in CodexHarness().run(SessionSpec(prompt='test',cwd=Path('.'),timeout_seconds=30,log=lambda s:None))]
        async def sleep(_): pass
        with patch.object(CodexHarness,'_run_once',once),patch('asyncio.sleep',sleep):
            return asyncio.run(collect())
    def test_retry_network_failure_before_any_tool(self):
        events=self.replay('workspace routing discovery timed out')
        self.assertEqual(self.calls,2)
        finals=[x for x in events if isinstance(x,SessionResult)]
        self.assertEqual(len(finals),1);self.assertFalse(finals[0].is_error)
    def test_bounded_attempts(self):
        events=self.replay('connection reset by peer',always=True)
        self.assertEqual(self.calls,3);self.assertTrue(events[-1].is_error)
    def test_no_retry_after_tool(self):
        self.replay('connection reset by peer',[AgentMessage(content=[ToolUseBlock('1','shell',{})])])
        self.assertEqual(self.calls,1)
    def test_no_retry_after_unknown_event(self):
        self.replay('connection reset by peer',[AgentMessage(raw={'type':'new.action'})])
        self.assertEqual(self.calls,1)
    def test_missing_runtime_and_auth_are_not_network_retries(self):
        for error in ['host executable was not found','401 Unauthorized','step budget 40 reached']:
            with self.subTest(error=error):
                self.replay(error);self.assertEqual(self.calls,1)
if __name__=='__main__':unittest.main()
