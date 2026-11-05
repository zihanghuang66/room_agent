import ast
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import shlex
import unittest
from unittest.mock import patch

class CodexAdapterTests(unittest.TestCase):
    def test_long_raw_json_remains_parseable_after_redaction(self):
        from litereality_agent.agent.room_codex import redact
        event={'type':'item.completed','item':{'text':'x'*6000+' Bearer sensitive_value'}}
        result=json.loads(redact(json.dumps(event)))
        self.assertEqual(result['type'],'item.completed')
        self.assertNotIn('sensitive_value',result['item']['text'])

    def test_failed_patch_and_mcp_keep_real_errors(self):
        from litereality_agent.agent.providers.codex import _normalise
        from litereality_agent.agent import providers
        event={'type':'item.completed','item':{'type':'file_change','status':'failed','changes':[{'path':'seed.txt'}],'error':{'message':'write denied'}}}
        result=[b for b in _normalise(event,{}) if isinstance(b,providers.ToolResultBlock)][0]
        self.assertTrue(result.is_error)
        self.assertIn('write denied',result.content)
        event['item']={'id':'m','type':'mcp_tool_call','status':'failed','error':{'message':'requires approval'}}
        result=_normalise(event,{})[0]
        self.assertTrue(result.is_error)
        self.assertIn('requires approval',result.content)

    def test_stream_recovery_terminal_failures_and_raw_events(self):
        from litereality_agent.agent import providers
        import sys
        answer={'type':'item.completed','item':{'type':'agent_message','text':'OK'}}
        completed={'type':'turn.completed','usage':{}}
        warning={'type':'error','message':'reconnecting'}
        cases=[([warning,answer,completed],0,False),([warning,answer],0,True),
            ([answer,completed,warning],0,True),([answer,completed],1,True),
            ([{'type':'turn.failed','error':{'message':'failed'}},answer,completed],0,True),
            ([{'type':'item.completed','item':{'type':'reasoning','text':'thinking'}},completed],0,True)]
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);worker=root/'worker.py';cli=root/'codex'
            cli.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(worker))+' "$@"\n');cli.chmod(0o755)
            async def run():
                return [m async for m in providers.resolve().run(providers.SessionSpec(prompt='test',cwd=root,timeout_seconds=10))]
            for events,code,failed in cases:
                worker.write_text('import json,sys\nfor e in '+repr(events)+': print(json.dumps(e))\nsys.exit('+str(code)+')\n')
                with patch.dict(os.environ,{'PATH':str(root)+os.pathsep+os.environ['PATH']}):messages=asyncio.run(run())
                self.assertEqual(messages[-1].is_error,failed,(events,code))
                self.assertEqual([m.raw for m in messages if isinstance(m,providers.AgentMessage)],events)
                self.assertEqual(messages[-1].raw['stream_errors'],[e for e in events if e['type'] in ('error','turn.failed')])

    def test_cli_failure_keeps_redacted_stderr_and_exit_code(self):
        from litereality_agent.agent.room_codex import ask,CodexCallError
        from litereality_agent.agent import providers
        class FailedHarness:
            async def run(self,spec):
                yield providers.SessionResult(result='',is_error=True,raw={'returncode':1,
                    'stderr':'No prompt provided via stdin. Bearer sensitive_bearer_value demo_secret_value'})
        with patch.dict(os.environ,{'TEST_API_KEY':'demo_secret_value'}),patch('litereality_agent.agent.room_codex.providers.resolve',return_value=FailedHarness()):
            with self.assertRaises(CodexCallError) as caught:asyncio.run(ask('test',cwd=Path.cwd()))
        detail=caught.exception.diagnostics
        self.assertEqual(detail['returncode'],1)
        self.assertIn('No prompt provided',detail['stderr'])
        self.assertNotIn('sensitive_bearer_value',detail['stderr'])
        self.assertNotIn('demo_secret_value',detail['stderr'])

    def test_real_stdio_probe_server(self):
        import sys
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        async def check(state):
            params=StdioServerParameters(command=sys.executable,args=[str(Path(__file__).with_name('probe_mcp.py')),str(state)])
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    result=await session.call_tool('get_probe_value',{})
                    self.assertFalse(result.isError)
                    self.assertEqual(result.content[0].text,'room-mcp-test')
        with tempfile.TemporaryDirectory() as temp:
            state=Path(temp)/'state.txt';state.write_text('room-mcp-test')
            asyncio.run(asyncio.wait_for(check(state),timeout=30))

    def test_every_role_is_codex_and_claude_is_rejected(self):
        from litereality_agent.agent import providers
        from litereality_agent.settings import LiteRealitySettings
        with patch.dict(os.environ,{},clear=True):
            for role in ('','author','materials','quality','refine','procedural','classify'):
                self.assertEqual(providers.resolve(role).name,'codex')
            with self.assertRaises(ValueError):providers.resolve(override='claude')
            with self.assertRaises(ValueError):LiteRealitySettings(agent_provider='claude')

    def test_failed_or_missing_session_is_not_success(self):
        from litereality_agent.agent.room_codex import ask
        class FailedHarness:
            async def run(self,spec):
                if False:yield None
        with patch('litereality_agent.agent.room_codex.providers.resolve',return_value=FailedHarness()):
            with self.assertRaises(RuntimeError):asyncio.run(ask('test',cwd=Path.cwd()))

    def test_strict_schema_rejects_incomplete_answer(self):
        from litereality_agent.agent.room_codex import parse_json
        with self.assertRaises(Exception):
            parse_json('{"pass":"yes"}',{'type':'object','required':['pass','missing'],
                'properties':{'pass':{'type':'boolean'}}})

    def test_completeness_failure_is_not_success(self):
        import litereality_agent
        path=litereality_agent.PACKAGE_ROOT/'models/object_generation/articulated-glb-agent/.claude/skills/image-to-articulated-glb/scripts/completeness_check.py'
        spec=importlib.util.spec_from_file_location('completeness_adapter_test',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertFalse(module.check(Path('/missing-reference'),[])['pass'])
        with self.assertRaises(Exception):module._parse('not a JSON verdict')

    def test_cli_receives_images_and_preserves_sandbox(self):
        from litereality_agent.agent.room_codex import ask
        import sys
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);record=root/'arguments.json';image=root/'input.png';image.write_bytes(b'test')
            cli=root/'codex'
            worker=root/'fake_cli.py'
            worker.write_text('import sys,json,argparse\nfrom pathlib import Path\n'
                +'Path('+repr(str(record))+').write_text(json.dumps(sys.argv[1:]))\n'
                +'p=argparse.ArgumentParser();p.add_argument("prompt",nargs="?")\n'
                +'for flag in ("--json","--ephemeral","--ignore-user-config","--skip-git-repo-check"):p.add_argument(flag,action="store_true")\n'
                +'for flag in ("--cd","--sandbox","-c","--add-dir","-m"):p.add_argument(flag,action="append")\n'
                +'p.add_argument("--image",nargs="+",action="append");a=p.parse_args(sys.argv[2:])\n'
                +'if not a.prompt:print("No prompt provided via stdin.",file=sys.stderr);sys.exit(1)\n'
                +'print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"OK"}}))\n'
                +'print(json.dumps({"type":"turn.completed","usage":{}}))\n')
            cli.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(worker))+' \"$@\"\n')
            cli.chmod(0o755)
            with patch.dict(os.environ,{'PATH':str(root)+os.pathsep+os.environ['PATH'],'LR_CODEX_MODEL':'gpt-6-astra'}):
                result=asyncio.run(ask('test',cwd=root,images=[image],timeout=10))
                self.assertEqual(result,'OK')
                args=json.loads(record.read_text())
                self.assertEqual(args[args.index('--image')+1],str(image.resolve()))
                self.assertEqual(args[args.index('--sandbox')+1],'read-only')
                asyncio.run(ask('test',cwd=root,read_only=False,timeout=10))
                args=json.loads(record.read_text())
                self.assertEqual(args[args.index('--sandbox')+1],'workspace-write')
                self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',args)

    def test_classifier_accepts_codex(self):
        from litereality_agent.pipeline.scene_init.reconstruct.classify import classification
        with patch.dict(os.environ,{'LR_CLASSIFY_PROVIDER':'codex'}):
            self.assertEqual(classification.provider(),'codex')

    def test_no_direct_claude_execution_or_sdk_imports(self):
        import litereality_agent
        hits=[]
        for path in litereality_agent.PACKAGE_ROOT.rglob('*.py'):
            tree=ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom) and node.module=='claude_agent_sdk':hits.append(str(path))
                if isinstance(node,ast.Import) and any(a.name=='claude_agent_sdk' for a in node.names):hits.append(str(path))
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in ('run','Popen','create_subprocess_exec') and node.args:
                    arg=node.args[0]
                    if isinstance(arg,ast.List) and arg.elts and isinstance(arg.elts[0],ast.Constant) and arg.elts[0].value=='claude':hits.append(str(path))
        self.assertEqual(hits,[])

if __name__=='__main__':unittest.main()
