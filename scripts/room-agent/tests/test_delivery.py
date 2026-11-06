import json
import tarfile
from pathlib import Path
import pytest


def test_diagnostic_return_excludes_secrets_and_raw_inputs(tmp_path):
    from room_agent.reports import make_archive
    run = tmp_path / 'run'
    (run / 'input').mkdir(parents=True)
    (run / '.codex').mkdir()
    (run / 'input/frame_0.jpg').write_bytes(b'raw')
    (run / '.codex/auth.json').write_text('secret')
    (run / 'auth.json').write_text('secret')
    (run / 'result.json').write_text('{}')
    (run / 'outside').symlink_to(tmp_path)
    dest = tmp_path / 'result.tar.gz'
    make_archive(run, dest)
    with tarfile.open(dest) as tf:
        names = [m.name for m in tf.getmembers()]
        assert all(m.mtime == 0 for m in tf.getmembers())
        assert names == ['run/result.json']


def test_stage_timeout_kills_child_and_preserves_attempt(tmp_path):
    # Test the process boundary itself, without making a fake reconstruction claim.
    from room_agent.processes import run_process
    import sys
    log = tmp_path / 'stage.log'
    with pytest.raises(TimeoutError):
        run_process([sys.executable, '-u', '-c', 'import time;print("started");time.sleep(20)'],
                    log, seconds=.2, env=None)
    assert 'started' in log.read_text()


def test_storage_probe_uses_real_processes(tmp_path):
    from room_agent.preflight import storage_probe
    result = storage_probe(tmp_path / 'probe')
    assert result['lock_exclusion'] is True
    assert result['sqlite_reopen'] is True
