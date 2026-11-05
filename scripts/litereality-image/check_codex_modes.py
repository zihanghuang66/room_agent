"""Compare Codex execution modes in a disposable container, without model calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback

sys.path.insert(0, '/opt/room/image')
from reports import create, save, archive
from sandbox_probe import metadata

folder = create('room_codex_modes')
result = {'model_calls': 0, 'scope': 'command_executor_only', 'cases': [],
          'metadata': metadata(), 'status': 'running'}
fixture = '''import json,os,subprocess
from pathlib import Path
p=Path("written-by-test.txt")
p.write_text("ROOM_CODEX_MODE_OK\\n")
assert p.read_text()=="ROOM_CODEX_MODE_OK\\n"
assert subprocess.check_output(["/bin/sh","-c","printf CHILD_PROCESS_OK"],text=True)=="CHILD_PROCESS_OK"
print("ROOM_MODE_WRITE_AND_CHILD_OK",flush=True)
'''

def run_case(name, location, profile=None, approval='on-request', extra=()):
    work = location / name
    work.mkdir()
    cmd = [sys.executable, '-c', fixture]
    if profile:
        cmd = ['codex', 'sandbox', '--permission-profile', profile,
               '--include-managed-config', '-c', 'approval_policy='+json.dumps(approval),
               *extra, '-C', str(work), '--', *cmd]
    try:
        done = subprocess.run(cmd, cwd=work, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=30)
        output = done.stdout + done.stderr
        code = done.returncode
    except subprocess.TimeoutExpired:
        output, code = 'TIMEOUT', 124
    target = work / 'written-by-test.txt'
    matches = target.is_file() and target.read_text() == 'ROOM_CODEX_MODE_OK\n'
    passed = code == 0 and matches and 'ROOM_MODE_WRITE_AND_CHILD_OK' in output
    entry = {'name': name, 'path': str(work), 'command': cmd, 'returncode': code,
             'passed': passed, 'file_matches': matches, 'output': output}
    result['cases'].append(entry)
    (folder / (location.name+'_'+name+'.log')).write_text(output)
    print(location.name, name, 'PASS' if passed else 'FAIL', flush=True)
    if not passed: print(output[-2000:], flush=True)

try:
    result['codex_version'] = subprocess.check_output(['codex', '--version'], text=True).strip()
    project = folder / 'project'
    project.mkdir()
    with tempfile.TemporaryDirectory(prefix='room-codex-modes-') as tmp:
        for location in (project, Path(tmp)):
            run_case('ordinary_process', location)
            run_case('workspace_on_request', location, ':workspace')
            run_case('workspace_never', location, ':workspace', 'never')
            run_case('workspace_network_enabled', location, 'room_test_network', extra=(
                '-c', 'permissions.room_test_network.extends=":workspace"',
                '-c', 'permissions.room_test_network.network.enabled=true'))
            run_case('container_boundary_on_request', location, ':danger-full-access')
            run_case('container_boundary_never', location, ':danger-full-access', 'never')
    result['status'] = 'comparison_complete'
except Exception:
    result['status'] = 'test_error'
    result['error'] = traceback.format_exc()
    print(result['error'], flush=True)
finally:
    save(folder / 'result.json', result)
    archive(folder)
print('RESULT_DIR', folder, flush=True)
raise SystemExit(0 if result['status'] == 'comparison_complete' else 1)
