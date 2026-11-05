import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'codex-integration-check/suite.py'

class SuiteTests(unittest.TestCase):
    def test_modern_sandbox_supplies_required_profile_and_keeps_managed_rules(self):
        spec=importlib.util.spec_from_file_location('sandbox_probe',SCRIPT.with_name('sandbox_probe.py'));probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)
        help_text='Usage: codex sandbox [OPTIONS] [COMMAND]... [COMMAND]\n--permission-profile <NAME>\n--include-managed-config\n-c --config <key=value>\n--cd <DIR>'
        args=probe.sandbox_command('codex',help_text,Path('/tmp/work'),['python','fixture.py'])
        self.assertIn('--permission-profile',args)
        self.assertEqual(args[args.index('--permission-profile')+1],':workspace')
        self.assertIn('--include-managed-config',args)
        self.assertFalse(any('sandbox_mode=' in a for a in args))

    def test_cli_usage_error_is_not_reported_as_container_denial(self):
        spec=importlib.util.spec_from_file_location('sandbox_probe',SCRIPT.with_name('sandbox_probe.py'));probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)
        self.assertEqual(probe.failure_kind(2,'error: the following required arguments were not provided:\n--permission-profile <NAME>'),'cli_usage_error')
        self.assertEqual(probe.failure_kind(1,'bwrap: Failed to make / slave: Permission denied'),'sandbox_denied')

    def test_sandbox_command_is_bounded_and_unsupported_help_is_not_guessed(self):
        spec=importlib.util.spec_from_file_location('sandbox_probe',SCRIPT.with_name('sandbox_probe.py'));probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)
        args=probe.sandbox_command('/bin/codex','Usage: codex sandbox [COMMAND] --config --cd',Path('/tmp/work'),['python','fixture.py'])
        self.assertIn('sandbox_mode="workspace-write"',args)
        self.assertIn('sandbox_workspace_write.network_access=false',args)
        self.assertEqual(args[-3:],['--','python','fixture.py'])
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',args)
        with self.assertRaises(ValueError):probe.sandbox_command('codex','unknown',Path('/tmp'),['true'])

    def test_patch_has_one_operation_per_target(self):
        patch=self.m.exact_patch('fixed-value')
        self.assertEqual(patch.count('*** Update File: seed.txt'),1)
        self.assertEqual(patch.count('*** Add File: patch_result.txt'),1)
        self.assertNotIn('*** Delete File:',patch)
        self.assertIn('-BEFORE\n+fixed-value',patch)

    def test_mcp_approval_block_is_identified_from_tool_event_not_model_prose(self):
        import json
        raw={'type':'item.completed','item':{'type':'mcp_tool_call','status':'failed','error':{'message':'MCP tool call requires approval, but approval policy is never'}}}
        self.assertTrue(self.m.mcp_approval_blocked([{'kind':'raw_event','event':json.dumps(raw)}]))
        self.assertFalse(self.m.mcp_approval_blocked([{'kind':'text','text':'MCP tool call requires approval, but approval policy is never'}]))

    def test_worker_crash_prints_log_tail_and_still_saves_tar(self):
        import contextlib,io,json,subprocess
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('suite_run',SCRIPT.with_name('run.py'));runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
        class Process:
            def __init__(self,args,**kwargs):
                self.args=args;kwargs['stdout'].write('PermissionError: synthetic worker crash\n');kwargs['stdout'].flush()
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def wait(self,timeout):return 1
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);fork=root/'scripts/vendor/litereality-agent-28ffe1f-room-codex-v0.1.2';fork.mkdir(parents=True)
            (fork/'room_codex_patch.json').write_text('{}');python=root/'python';python.touch();output=io.StringIO()
            with patch('sys.argv',['run.py','--project-root',temp,'--app-python',str(python)]),patch.object(runner.shutil,'which',side_effect=lambda name,**kw:'/mock/codex' if name=='codex' else None),patch.object(runner.subprocess,'run',return_value=subprocess.CompletedProcess([],0)),patch.object(runner.subprocess,'Popen',Process),patch.object(runner.resource,'setrlimit'),patch.object(runner,'install_adapter',return_value=fork),contextlib.redirect_stdout(output):
                self.assertEqual(runner.main(),1)
            self.assertIn('PermissionError: synthetic worker crash',output.getvalue())
            self.assertIn('RETURN ARCHIVE:',output.getvalue())
            self.assertEqual(len(list((root/'return_output').glob('*.tar.gz'))),1)

    def test_terminal_errors_are_printed_once_redacted_and_archived(self):
        import contextlib,io,json,os,tarfile
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('suite_run',SCRIPT.with_name('run.py'));runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);report=root/'return_output/artifacts/report';report.mkdir(parents=True)
            data={'checks':{'patch':{'status':'failed','assertions':{'patch_file':False},'diagnostics':{'returncode':0,'stderr':'Failed to write file result.txt Bearer secret_bearer test_secret_value'}}}}
            output=io.StringIO();seen=set()
            with patch.dict(os.environ,{'TEST_API_KEY':'test_secret_value'}),contextlib.redirect_stdout(output):
                runner.emit_check_updates(report,data,seen);runner.emit_check_updates(report,data,seen)
                archive=runner.pack(report,root)
            text=output.getvalue()
            self.assertEqual(text.count('ERROR BEGIN: patch'),1)
            self.assertIn('Failed to write file result.txt',text);self.assertIn('patch_file',text)
            self.assertNotIn('secret_bearer',text);self.assertNotIn('test_secret_value',text)
            with tarfile.open(archive) as t:self.assertIn('report/terminal_summary.log',t.getnames())

    def setUp(self):
        self.assertTrue(SCRIPT.is_file(),'Integration suite not implemented')
        spec=importlib.util.spec_from_file_location('suite',SCRIPT);self.m=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.m)

    def test_done_cannot_override_failed_files(self):
        self.assertEqual(self.m.verdict(True,{'file_matches':False}),'failed')
        self.assertEqual(self.m.verdict(False,{'file_matches':True}),'failed')
        self.assertEqual(self.m.verdict(True,{'file_matches':True}),'passed')

    def test_partial_failure_remains_visible_in_summary(self):
        checks={name:{'status':'passed'} for name in self.m.CORE_CHECKS}
        checks['patch']['status']='failed'
        self.assertEqual(self.m.overall_status(checks),'checks_incomplete')
        checks['patch']['status']='passed'
        self.assertEqual(self.m.overall_status(checks),'core_checks_passed')

    def test_no_blender_success_without_png_and_glb(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.assertFalse(self.m.render_artifacts(root,'cpu')['passed'])
            (root/'cpu.json').write_text('{"mode":"cpu","status":"passed"}')
            (root/'cpu.png').write_bytes(b'\x89PNG\r\n\x1a\n')
            self.assertFalse(self.m.render_artifacts(root,'cpu')['passed'])
            (root/'test_cube.glb').write_bytes(b'glTF');(root/'test_cube.blend').write_bytes(b'BLENDER')
            self.assertTrue(self.m.render_artifacts(root,'cpu')['passed'])

    def test_return_archive_excludes_work_files_and_redacts_secrets(self):
        import json,os,tarfile
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('suite_run',SCRIPT.with_name('run.py'));runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);report=root/'return_output/artifacts/report';report.mkdir(parents=True)
            (report/'result.json').write_text('{}');(report/'suite.log').write_text('Bearer bearer_secret test_api_secret')
            (report/'work').mkdir();(report/'work/private.txt').write_text('not for return')
            with patch.dict(os.environ,{'TEST_API_KEY':'test_api_secret'}):out=runner.pack(report,root)
            with tarfile.open(out) as tar:
                self.assertTrue(all(x.mtime==0 for x in tar.getmembers()))
                self.assertFalse(any('private.txt' in n for n in tar.getnames()))
                text=tar.extractfile('report/suite.log').read().decode()
                self.assertNotIn('bearer_secret',text);self.assertNotIn('test_api_secret',text)
            self.assertEqual(out.read_bytes()[4:8],b'\0'*4)

    def test_gpu_probe_timeout_does_not_prevent_suite_launch(self):
        import contextlib,io,json,subprocess
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('suite_run',SCRIPT.with_name('run.py'));runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
        class Process:
            def __init__(self,args,**kwargs):
                self.args=args;report=Path(args[args.index('--report')+1])
                (report/'suite.json').write_text(json.dumps({'status':'core_checks_passed','checks':{},'model_sessions':0}))
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def wait(self,timeout):return 0
        def command(args,**kwargs):
            if str(args[0]).endswith('nvidia-smi'):raise subprocess.TimeoutExpired(args,20)
            return subprocess.CompletedProcess(args,0)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);fork=root/'scripts/vendor/litereality-agent-28ffe1f-room-codex-v0.1.2';fork.mkdir(parents=True)
            (fork/'room_codex_patch.json').write_text('{}');python=root/'python';python.touch()
            with patch('sys.argv',['run.py','--project-root',temp,'--app-python',str(python)]),patch.object(runner.shutil,'which',side_effect=lambda name,**kw:'/mock/'+name),patch.object(runner.subprocess,'run',side_effect=command),patch.object(runner.subprocess,'Popen',Process),patch.object(runner.resource,'setrlimit'),patch.object(runner,'install_adapter',return_value=fork),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(),0)
            data=json.loads(next((root/'return_output/artifacts').glob('*/result.json')).read_text())
            self.assertEqual(data['gpu_probe']['error_type'],'TimeoutExpired')

if __name__=='__main__':unittest.main()
