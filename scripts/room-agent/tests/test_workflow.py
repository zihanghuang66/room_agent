import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def service(tmp_path):
    from room_agent.service import Service
    return Service(tmp_path / 'return_output' / 'agent_state')


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'Room.py').write_text('value = 1\n')
    (root / 'assets').mkdir()
    (root / 'assets' / 'texture.bin').write_bytes(b'fixture asset')
    return root


def test_new_package_is_available():
    import importlib.util
    assert importlib.util.find_spec('room_agent') is not None


def test_wait_and_resume_across_service_instances(service, source):
    from room_agent.service import Service
    run = service.create('room_004', source)
    waiting = service.execute(run)
    assert waiting['execution_status'] == 'waiting_for_input'
    assert waiting['quality_status'] == 'unreviewed'
    fresh = Service(service.root)
    done = fresh.execute(run, action='finish')
    assert done['execution_status'] == 'completed'
    assert done['operation'] == 'saved_source_audit'
    assert done['quality_status'] == 'unreviewed'
    assert fresh.execute(run) == done
    assert len(fresh.list_runs()) == 1


def test_cancel_keeps_revision_without_quality_acceptance(service, source):
    run = service.create('room_003', source)
    pending = service.execute(run)
    result = service.execute(run, action='cancel')
    assert result['execution_status'] == 'cancelled'
    assert result['revision_id'] == pending['revision_id']
    assert result['quality_status'] == 'unreviewed'
    assert Path(result['artifact_path']).is_dir()


def test_changed_input_before_snapshot_is_rejected(service, source):
    run = service.create('room_004', source)
    (source / 'Room.py').write_text('value = 2\n')
    with pytest.raises(ValueError, match='input changed'):
        service.execute(run)
    assert service.show(run)['execution_status'] == 'failed'


def test_published_revision_survives_source_removal(service, source):
    import shutil
    run = service.create('room_004', source)
    service.execute(run)
    shutil.rmtree(source)
    assert service.execute(run, action='finish')['execution_status'] == 'completed'


@pytest.mark.parametrize('mutation', ['change', 'delete', 'extra'])
def test_tampered_artifact_cannot_resume(service, source, mutation):
    run = service.create('room_004', source)
    result = service.execute(run)
    artifact = Path(result['artifact_path']) / 'source'
    if mutation == 'change':
        (artifact / 'Room.py').write_text('value = 999\n')
    elif mutation == 'delete':
        (artifact / 'Room.py').unlink()
    else:
        (artifact / 'extra.txt').write_text('unexpected')
    with pytest.raises(ValueError, match='artifact integrity'):
        service.execute(run, action='finish')
    assert service.show(run)['execution_status'] == 'failed'


def test_syntax_failure_is_not_quality_failure(service, source):
    (source / 'Room.py').write_text('this is invalid python!')
    run = service.create('room_004', source)
    with pytest.raises(SyntaxError):
        service.execute(run)
    result = service.show(run)
    assert result['execution_status'] == 'failed'
    assert result['quality_status'] == 'unreviewed'
    assert result['error']['node'] == 'audit'
    assert any(e['kind'] == 'node_failed' for e in service.events(run))


def test_reject_symlinks_and_bad_identifiers(service, source):
    with pytest.raises(ValueError):
        service.create('../escape', source)
    (source / 'link').symlink_to(source / 'Room.py')
    with pytest.raises(ValueError, match='symlink'):
        service.create('room_004', source)
    with pytest.raises(ValueError):
        service.show('../escape')


def test_invalid_actions_do_not_destroy_waiting_checkpoint(service, source):
    run = service.create('room_004', source)
    with pytest.raises(ValueError, match='not waiting'):
        service.execute(run, action='finish')
    service.execute(run)
    with pytest.raises(ValueError, match='action'):
        service.execute(run, action='rerun_everything')
    assert service.show(run)['execution_status'] == 'waiting_for_input'


def test_events_have_cursor_and_distinct_run_identity(service, source):
    first = service.create('room_004', source)
    second = service.create('room_003', source)
    a = service.execute(first)
    b = service.execute(second)
    assert a['artifact_path'] != b['artifact_path']
    events = service.events(first)
    assert [e['seq'] for e in events] == list(range(1, len(events) + 1))
    assert service.events(first, after=events[-2]['seq']) == events[-1:]
    assert all(e['run_id'] == first for e in events)


def child(code, *args):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'))
    return subprocess.run([sys.executable, '-c', code, *map(str, args)], env=env,
                          text=True, capture_output=True, timeout=30)


def test_crash_after_publication_reuses_snapshot(service, source):
    run = service.create('room_004', source)
    code = '''
import os, sys
from room_agent.service import Service
from room_agent.artifacts import ArtifactStore
class DieAfterPublish(ArtifactStore):
    def snapshot(self, *args):
        result = super().snapshot(*args)
        os._exit(91)
Service(sys.argv[1], artifact_type=DieAfterPublish).execute(sys.argv[2])
'''
    result = child(code, service.root, run)
    assert result.returncode == 91, result.stderr
    assert service.show(run)['execution_status'] == 'interrupted'
    resumed = service.execute(run)
    assert resumed['execution_status'] == 'waiting_for_input'
    assert any(e['kind'] == 'artifact_reused' for e in service.events(run))
    revisions = list((service.root / 'rooms' / 'room_004' / 'revisions').iterdir())
    assert len(revisions) == 1


def test_room_lock_rejects_concurrent_process(service, source):
    from room_agent.storage import room_lock
    run = service.create('room_004', source)
    code = '''
import sys
from room_agent.service import Service
Service(sys.argv[1]).execute(sys.argv[2])
'''
    with room_lock(service.root, 'room_004'):
        result = child(code, service.root, run)
    assert result.returncode != 0
    assert 'room is busy' in result.stderr
    assert service.show(run)['execution_status'] == 'created'


def test_cli_separate_processes_resume_real_checkpoint(tmp_path, source):
    code = 'from room_agent.cli import main; main()'
    args = ['--state-root', tmp_path / 'state']
    created = child(code, *args, 'create', '--room', 'room_004', '--source', source)
    assert created.returncode == 0, created.stderr
    run = json.loads(created.stdout)['run_id']
    start = child(code, *args, 'run', run)
    assert start.returncode == 0, start.stderr
    assert json.loads(start.stdout)['execution_status'] == 'waiting_for_input'
    resume = child(code, *args, 'resume', run, '--action', 'finish')
    assert resume.returncode == 0, resume.stderr
    assert json.loads(resume.stdout)['execution_status'] == 'completed'


def test_version_mismatch_blocks_resume(service, source):
    run = service.create('room_004', source)
    service.execute(run)
    path = service.root / 'runs' / run / 'input.json'
    data = json.loads(path.read_text())
    data['workflow_version'] = 'unknown'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='version'):
        service.execute(run, action='finish')


def test_completed_run_with_missing_revision_cannot_report_success(service, source):
    import shutil
    run = service.create('room_004', source)
    service.execute(run)
    done = service.execute(run, action='finish')
    shutil.rmtree(done['artifact_path'])
    with pytest.raises(ValueError, match='artifact integrity'):
        service.execute(run)
    assert service.show(run)['execution_status'] == 'failed'


def test_failure_retry_does_not_repeat_successful_snapshot(service, source):
    run = service.create('room_004', source)
    # Fail only the first audit check, in a process that then exits normally.
    code = '''
import sys
from room_agent.service import Service
from room_agent.artifacts import ArtifactStore
class FailAudit(ArtifactStore):
    def verify(self):
        raise OSError('temporary audit read failure')
try:
    Service(sys.argv[1], artifact_type=FailAudit).execute(sys.argv[2])
except OSError:
    pass
'''
    result = child(code, service.root, run)
    assert result.returncode == 0, result.stderr
    assert service.show(run)['error']['node'] == 'audit'
    (source / 'Room.py').unlink()
    assert service.execute(run)['execution_status'] == 'waiting_for_input'
    starts = [e['node'] for e in service.events(run) if e['kind'] == 'node_started']
    assert starts.count('snapshot') == 1
    assert starts.count('audit') == 2


def test_result_report_is_not_execution_authority(service, source):
    run = service.create('room_004', source)
    service.execute(run)
    path = service.root / 'runs' / run / 'result.json'
    path.write_text('{"execution_status": "completed", "quality_status": "passed"}')
    assert service.show(run)['execution_status'] == 'waiting_for_input'
    assert service.show(run)['quality_status'] == 'unreviewed'


def test_same_room_lock_does_not_block_another_room(service, source):
    from room_agent.storage import room_lock
    run = service.create('room_003', source)
    with room_lock(service.root, 'room_004'):
        assert service.execute(run)['execution_status'] == 'waiting_for_input'


def test_input_and_output_trees_cannot_overlap(service, source):
    with pytest.raises(ValueError, match='contain'):
        service.create('room_004', service.root.parent)


def test_mutated_request_rejected_even_with_compatible_version(service, source):
    run = service.create('room_004', source)
    path = service.root / 'runs' / run / 'input.json'
    data = json.loads(path.read_text())
    data['source'] = '/some/other/source'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='checksum'):
        service.execute(run)


def test_other_run_in_same_room_does_not_make_crashed_run_look_running(service, source):
    from room_agent.storage import room_lock, Journal
    run = service.create('room_004', source)
    Journal(service.root / 'runs' / run, run).emit('run_started')
    with room_lock(service.root, 'room_004'):
        assert service.show(run)['execution_status'] == 'interrupted'
