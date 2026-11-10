"""A killed orchestration worker must not free resources still used by its child."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from room_agent.storage import room_busy


@pytest.mark.skipif(os.name != 'posix', reason='POSIX file locks and process groups required')
def test_backend_keeps_resource_lease_after_worker_sigkill(tmp_path):
    resources = tmp_path / 'resources'
    pidfile = tmp_path / 'backend.pid'
    child_script = (
        'import os, time\nfrom pathlib import Path\n'
        f'Path({str(pidfile)!r}).write_text(str(os.getpid()))\n'
        'time.sleep(60)\n'
    )
    worker_script = (
        'import os\n'
        'from room_agent.storage import room_lock\n'
        'from room_agent.processes import run_process\n'
        f'with room_lock({str(resources)!r}, "scene_execution") as lease:\n'
        f'    run_process({[sys.executable, "-c", child_script]!r}, '
        f'{str(tmp_path / "backend.log")!r}, seconds=120, env=dict(os.environ), '
        'pass_fds=(lease.fileno(),))\n'
    )
    env = dict(os.environ)
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'src')
    worker = subprocess.Popen([sys.executable, '-c', worker_script], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    backend_pid = None
    try:
        deadline = time.monotonic() + 10
        while not pidfile.exists() and worker.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        if not pidfile.exists():
            if worker.poll() is None:
                worker.kill()
            output, _ = worker.communicate(timeout=5)
            pytest.fail('Backend did not start: ' + output.decode(errors='replace'))
        backend_pid = int(pidfile.read_text())
        assert room_busy(resources, 'scene_execution')

        worker.kill()
        worker.wait(timeout=5)
        os.kill(backend_pid, 0)  # The actual backend is still alive after its parent is gone.
        assert room_busy(resources, 'scene_execution'), 'Orphan backend lost its resource lease'

        os.killpg(backend_pid, signal.SIGKILL)
        deadline = time.monotonic() + 5
        while room_busy(resources, 'scene_execution') and time.monotonic() < deadline:
            time.sleep(.02)
        assert not room_busy(resources, 'scene_execution'), 'Lease leaked after backend termination'
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=5)
        if worker.stdout is not None:
            worker.stdout.close()
        if backend_pid is not None:
            try:
                os.killpg(backend_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
