"""Launcher compatibility tests, including the Python version used by the school image."""
import os
from pathlib import Path
import subprocess


def delivery_dir():
    root = Path(__file__).resolve().parents[1]
    return root / 'delivery' if (root / 'delivery').is_dir() else root.parent


def fake_python(tmp_path, version, status):
    path = tmp_path / ('python' + version)
    path.write_text(f'#!/bin/sh\nprintf "%s\\n" "{version}"\nexit {status}\n')
    path.chmod(0o755)
    return path


def select(env):
    return subprocess.run(['bash', '-c', 'source "$1"; room_select_python',
                           'bootstrap-test', str(delivery_dir() / 'select_python.sh')],
                          env=env, capture_output=True, text=True, timeout=15)


def test_selector_exists():
    assert (delivery_dir() / 'select_python.sh').is_file()


def test_explicit_python310_is_supported_and_reported(tmp_path):
    executable = fake_python(tmp_path, '3.10.19', 0)
    result = select(dict(os.environ, ROOM_ORCH_BOOTSTRAP_PYTHON=str(executable)))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(executable)
    assert '3.10.19' in result.stderr and 'SUPPORTED' in result.stderr


def test_explicit_old_python_fails_with_actual_version_not_silent_fallback(tmp_path):
    executable = fake_python(tmp_path, '3.9.6', 3)
    result = select(dict(os.environ, ROOM_ORCH_BOOTSTRAP_PYTHON=str(executable)))
    assert result.returncode != 0
    assert not result.stdout.strip()
    assert '3.9.6' in result.stderr and '3.10' in result.stderr


def test_existing_app_python_is_considered_before_system_python(tmp_path):
    executable = fake_python(tmp_path, '3.10.19', 0)
    env = dict(os.environ, ROOM_APP_PYTHON=str(executable))
    env.pop('ROOM_ORCH_BOOTSTRAP_PYTHON', None)
    result = select(env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(executable)
