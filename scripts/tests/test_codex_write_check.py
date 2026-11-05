import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'codex-write-check/worker.py'

class WriteCheckTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(),'Write diagnostics not implemented')
        spec=importlib.util.spec_from_file_location('write_worker',SCRIPT)
        self.m=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.m)

    def test_missing_wrong_or_symlink_file_never_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=root/'result.txt'
            self.assertFalse(self.m.inspect_file(p,'expected')['content_matches'])
            p.write_text('wrong');self.assertFalse(self.m.inspect_file(p,'expected')['content_matches'])
            p.unlink();source=root/'state';source.write_text('expected');p.symlink_to(source)
            self.assertFalse(self.m.inspect_file(p,'expected')['content_matches'])

    def test_actual_file_is_verified_independently(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'result.txt';p.write_text('expected\n')
            self.assertTrue(self.m.inspect_file(p,'expected')['content_matches'])

    def test_tool_trace_preserves_errors_and_redacts_credentials(self):
        from types import SimpleNamespace
        class Text:pass
        class Use:
            def __init__(self):self.id='t1';self.name='Bash';self.input={'command':'python test.py'}
        class Result:
            def __init__(self):self.tool_use_id='t1';self.is_error=True;self.content='Permission denied Bearer secret123'
        provider=SimpleNamespace(ToolUseBlock=Use,ToolResultBlock=Result,TextBlock=Text)
        message=SimpleNamespace(content=[Use(),Result()],raw={'type':'item.completed'})
        records=self.m.trace_message(message,provider,lambda s:s.replace('secret123','[redacted]'))
        result=next(r for r in records if r['kind']=='tool_result')
        self.assertTrue(result['is_error']);self.assertIn('Permission denied',result['content'])
        self.assertNotIn('secret123',json.dumps(records))

if __name__=='__main__':unittest.main()
