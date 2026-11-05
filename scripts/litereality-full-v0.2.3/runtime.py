"""Use the container's existing Codex; never download, install, or change login."""
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess


def prepare(env, report):
    report = Path(report)
    report.parent.mkdir(parents=True, exist_ok=True)
    result = dict(status='failed', source='existing_container_environment',
                  installed_or_downloaded=False, actual_agent_execution_tested=False)
    try:
        if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
            raise RuntimeError('This reconstruction environment requires Linux x86_64')
        requested = env.get('ROOM_CODEX') or 'codex'
        found = shutil.which(requested, path=env.get('PATH'))
        if not found:
            raise RuntimeError('Existing Codex executable not found: ' + requested)
        # Keep a wrapper/symlink's spelling, so its own package discovery remains intact.
        cli = Path(os.path.abspath(found))
        env['ROOM_CODEX'] = str(cli)
        env['PATH'] = str(cli.parent) + os.pathsep + env.get('PATH', '')
        result['cli'] = str(cli)
        proc = subprocess.run([str(cli), '--version'], env=env, capture_output=True,
                              text=True, timeout=30)
        result['version'] = dict(returncode=proc.returncode, stdout=proc.stdout[-2000:],
                                 stderr=proc.stderr[-2000:])
        if proc.returncode:
            raise RuntimeError('Existing Codex version check failed: exit=' + str(proc.returncode))
        result.update(status='passed',
                      note='Version check only; actual command and MCP execution are tested next.')
        print('CODEX_EXISTING_RUNTIME_PASS ' + str(cli), flush=True)
        return cli
    except Exception as exc:
        result['error'] = str(exc)
        raise
    finally:
        report.write_text(json.dumps(result, indent=2) + '\n')
