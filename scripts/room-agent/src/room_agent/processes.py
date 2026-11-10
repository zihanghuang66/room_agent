"""Bounded foreground worker process group; only processes created by this job are stopped."""
import os
from pathlib import Path
import signal
import subprocess
import time


def tail(path, limit=6000):
    with Path(path).open('rb') as f:
        f.seek(max(0, Path(path).stat().st_size - limit))
        return f.read(limit).decode(errors='replace')


def run_process(command, log, *, seconds, env, pass_fds=()):
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('a') as stream:
        proc = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
            stdout=stream, stderr=subprocess.STDOUT, start_new_session=True, pass_fds=pass_fds)
        started = time.monotonic()
        heartbeat = started + 30
        try:
            while proc.poll() is None:
                now = time.monotonic()
                if now - started > seconds:
                    raise TimeoutError(f'Stage exceeded {seconds}s; saved attempts retained. Log: {log}')
                if now >= heartbeat:
                    print(f'[{log.stem}] still running {int(now-started)}s; log={log}', flush=True)
                    heartbeat = now + 30
                time.sleep(.1)
            if proc.returncode:
                raise RuntimeError(f'{log.stem} exit={proc.returncode}\n{tail(log)}')
        finally:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                except ProcessLookupError:
                    proc.wait()
