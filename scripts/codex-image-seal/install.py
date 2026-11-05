"""Install the already downloaded fixed-version Codex bundle into the image layer."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import traceback
import uuid

from archive_utils import install_archive
from check import TARGET, LINKS, check, image_layer, sha

HERE = Path(__file__).resolve().parent
SHA = '3ec55053109608d5038f7e50750f7d25f768dfd740fef071aa710e2559b63aac'
RUN_NAME = 'room_002_full_v0.1.0_9a5b63b1e097'


def atomic_text(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.seal-' + uuid.uuid4().hex[:8])
    temp.write_text(text); temp.chmod(mode); temp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-root', type=Path, default=Path('/yinxin/projects/room'))
    parser.add_argument('--offline-only', action='store_true', help='Installation/static tests only; not eligible for prepare_save')
    args = parser.parse_args()
    project = args.project_root.resolve()
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '_' + uuid.uuid4().hex[:8]
    report = project / 'return_output/environment_reports' / ('room_codex_seal_' + stamp)
    report.mkdir(parents=True)
    result = dict(status='failed', actual_model_execution_tested=False, report=str(report))
    package = project / 'scripts/room-litereality-full-v0.1.0'
    sys.path.insert(0, str(package))
    spec = importlib.util.spec_from_file_location('seal_original_runner', package / 'run.py')
    runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
    lock = None
    try:
        if os.geteuid() != 0 or platform.system() != 'Linux' or platform.machine() != 'x86_64':
            raise RuntimeError('Run as root in the Linux x86_64 school container')
        if not Path('/opt/room/envs/app/bin/python').is_file():
            raise RuntimeError('Expected Room image environment missing')
        run = project / 'return_output/artifacts' / RUN_NAME
        if run.is_dir():
            lock = (run / '.run.lock').open('a')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('room_002 is still running. Wait until it finishes before sealing Codex.')
        source = project / 'scripts/room-litereality-resume-v0.1.1'
        archive = source / 'codex-runtime.tar.gz'
        if not archive.is_file():
            raise RuntimeError('Extract room-litereality-resume-v0.1.1.tar.gz first; complete offline CLI archive is required')
        install_dir = Path('/opt/room/image/codex-complete')
        env_file = Path('/opt/room/image/environment.sh')
        image_layer([TARGET, install_dir, env_file, *[p.parent for p in LINKS]])
        if not env_file.is_file() or env_file.is_symlink():
            raise RuntimeError('Expected regular Room environment.sh missing')
        for link in LINKS:
            if link.exists() and not (link.is_file() or link.is_symlink()):
                raise RuntimeError('Will not replace a directory: ' + str(link))
        TARGET.parent.mkdir(parents=True, exist_ok=True)
        install_archive(archive, TARGET, SHA)
        # Verify the real executables before switching any global entrypoint.
        for cmd in [[TARGET / 'bin/codex', '--version'], [TARGET / 'bin/codex-code-mode-host', '--help']]:
            subprocess.run(list(map(str, cmd)), check=True, stdout=subprocess.DEVNULL, timeout=30)
        backup = Path('/opt/room/build-info/codex-seal-backups') / stamp
        backup.mkdir(parents=True)
        previous = {}
        for i, link in enumerate(LINKS):
            record = dict(path=str(link), existed=link.exists() or link.is_symlink())
            if link.is_symlink():
                record.update(kind='symlink', target=os.readlink(link))
            elif link.is_file():
                dest = backup / ('entrypoint_' + str(i)); shutil.copy2(link, dest)
                record.update(kind='file', backup=str(dest))
            previous[str(link)] = record
        shutil.copy2(env_file, backup / 'environment.sh')
        (backup / 'entrypoints.json').write_text(json.dumps(previous, indent=2))
        for link in LINKS:
            link.parent.mkdir(parents=True, exist_ok=True)
            temp = link.with_name('.codex-seal-' + uuid.uuid4().hex[:8])
            temp.symlink_to(TARGET / 'bin/codex'); temp.replace(link)
        marker = '# ROOM_CODEX_COMPLETE_V1'
        text = env_file.read_text()
        if marker not in text:
            text += '\n' + marker + '\nexport ROOM_CODEX="' + str(TARGET / 'bin/codex') + '"\n'
            text += 'export PATH="' + str(TARGET / 'bin') + ':$PATH"\n'
            atomic_text(env_file, text)
        files = {p.relative_to(TARGET).as_posix(): sha(p) for p in TARGET.rglob('*') if p.is_file()}
        manifest = dict(version='0.164.0', archive_sha256=SHA, files=files, installed_at=stamp,
                        entrypoint_backup=str(backup))
        atomic_text(Path('/opt/room/build-info/codex-complete-v0.1.0.json'), json.dumps(manifest, indent=2))
        install_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HERE / 'check.py', install_dir / 'check.py')
        atomic_text(Path('/usr/local/bin/room-codex-runtime-check'),
            '#!/bin/sh\nexec /opt/room/envs/app/bin/python /opt/room/image/codex-complete/check.py "$@"\n', 0o755)
        result['runtime'] = check()
        (report / 'runtime.json').write_text(json.dumps(result['runtime'], indent=2))
        # Use the same patched application and execution smoke as the resume run.
        with (source / 'repair_pins.json').open() as f: pins = json.load(f)
        app = install_archive(source / 'app.tar.gz', source / 'app', pins['app_sha256'])
        env = runner.setup_environment(project, report, app)
        env['ROOM_CODEX'] = str(TARGET / 'bin/codex')
        env['PATH'] = str(TARGET / 'bin') + os.pathsep + env['PATH']
        result['backup'] = str(backup)
        if args.offline_only:
            result['status'] = 'installed_offline_checks_only'
        else:
            runner.command('codex_execution', [sys.executable, source / 'execution_smoke.py'],
                           Path(env['LR_REPO_ROOT']), env, report, 540)
            execution = json.loads((report / 'checks/codex_execution.json').read_text())
            if execution['status'] != 'passed' or execution['cli'] != str(TARGET / 'bin/codex'):
                raise RuntimeError('The installed image-layer CLI did not pass execution smoke')
            result.update(status='ready_for_save_preparation', actual_model_execution_tested=True)
        # Login/SSH service are deliberately not modified by installation.
        atomic_text(Path('/opt/room/build-info/codex-seal-last.json'), json.dumps(result, indent=2))
        print('INSTALL_OK ' + result['status'], flush=True)
        return 0
    except BaseException:
        result['error'] = runner.redact(traceback.format_exc())
        print(result['error'], flush=True)
        # Invalidate an earlier success if this verification did not complete.
        if Path('/opt/room/build-info').is_dir():
            atomic_text(Path('/opt/room/build-info/codex-seal-last.json'), json.dumps(result, indent=2))
        return 1
    finally:
        (report / 'result.json').write_text(json.dumps(result, indent=2))
        runner.pack(report, project / 'return_output', 'codex_seal_' + stamp)
        if lock: lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
