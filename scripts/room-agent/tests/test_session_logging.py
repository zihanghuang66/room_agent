import asyncio
import importlib
import json


def test_raw_errors_survive_normalization_redacted_and_recoverable(tmp_path, monkeypatch, capsys):
    from room_agent.saved_scene import legacy_root
    root = legacy_root()
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / 'app/src'))
    logger = importlib.import_module('agent_session')
    from litereality_agent.agent import providers as p
    monkeypatch.setenv('TEST_API_TOKEN', 'secret_test_123456')
    class Harness:
        def effective_model(self, spec):
            return 'test'
        async def run(self, spec):
            yield p.AgentMessage(raw={'type': 'error', 'message': 'network retry secret_test_123456',
                                      'code': 'TIMEOUT', 'authorization': 'should_not_be_saved'})
            yield p.SessionResult(result='review completed', raw={'stream_errors': []})
    monkeypatch.setattr(p, 'resolve', lambda *a: Harness())
    folder = tmp_path / 'session'
    result = asyncio.run(logger.recorded_session(p.SessionSpec('test', tmp_path), folder, 'quality'))
    assert result['termination'] == 'completed'
    assert result['stream_error_count'] == 1
    rows = [json.loads(s) for s in (folder / 'events.jsonl').read_text().splitlines()]
    assert rows[0]['error_detail']['message'] == 'network retry [redacted]'
    assert rows[0]['error_detail']['code'] == 'TIMEOUT'
    assert 'authorization' not in rows[0]['error_detail']
    output = capsys.readouterr().out
    assert 'TIMEOUT' in output and 'secret_test_123456' not in output


def test_failed_turn_keeps_nested_error_without_claiming_success(tmp_path, monkeypatch):
    from room_agent.saved_scene import legacy_root
    root = legacy_root()
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root / 'app/src'))
    logger = importlib.import_module('agent_session')
    from litereality_agent.agent import providers as p
    class Harness:
        def effective_model(self, spec):
            return 'test'
        async def run(self, spec):
            yield p.AgentMessage(raw={'type': 'turn.failed', 'error': {'message': 'service unavailable', 'code': 503}})
            yield p.SessionResult(is_error=True, stopped='turn failed')
    monkeypatch.setattr(p, 'resolve', lambda *a: Harness())
    folder = tmp_path / 'session'
    result = asyncio.run(logger.recorded_session(p.SessionSpec('test', tmp_path), folder, 'quality'))
    assert result['termination'] == 'execution_error'
    row = json.loads((folder / 'events.jsonl').read_text().splitlines()[0])
    assert row['error_detail']['error']['code'] == 503
