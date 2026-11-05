"""School-container full room run, checkpointed outputs and automatic return tar."""
import argparse
from collections import deque
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
import traceback
import uuid

from common import digest, extract_checked, find_capture, frame_files, save

HERE = Path(__file__).resolve().parent
SCAN = 'LiteReality-LiteReality_Scanner-Studio_9952-20261031-090455.tar'
SCAN_SHA = '914f5381f0c7a3a513c20df1c27b67adbcf93ad89e252056ff6bedc9d0ed6574'
STAGES = ('ingest', 'reconstruct', 'seed', 'author', 'publish')


def redact(text):
    for key, value in os.environ.items():
        if len(value) >= 8 and any(k in key.upper() for k in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY')):
            text = text.replace(value, '[redacted]')
    text = re.sub(r'\bsk-[A-Za-z0-9_-]{8,}', '[redacted]', text)
    text = re.sub(r'(?i)(Bearer\s+)[^\s"\']+', r'\1[redacted]', text)
    return text


def descendants(root):
    """Track this job only, including helpers that create independent process groups."""
    proc = Path('/proc')
    if not proc.is_dir():
        return {}
    table = {}
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            table[int(entry.name)] = (int(fields[1]), fields[19])
        except (OSError, IndexError, ValueError):
            pass
    found = {root}
    while True:
        extra = {pid for pid, (parent, _) in table.items() if parent in found} - found
        if not extra:
            return {pid: table[pid][1] for pid in found if pid in table}
        found.update(extra)


def signal_descendants(processes, sig):
    for pid, started in processes.items():
        try:
            current = Path('/proc') / str(pid) / 'stat'
            if current.read_text().rsplit(')', 1)[1].split()[19] == started:
                os.kill(pid, sig)
        except (OSError, IndexError):
            pass


def command(label, cmd, cwd, env, folder, seconds):
    """Bounded subprocess tree; terminal output, log, heartbeat, and useful failure tail."""
    print('[' + label + '] RUNNING', flush=True)
    logs = folder / 'logs'; logs.mkdir(exist_ok=True)
    p = subprocess.Popen([str(x) for x in cmd], cwd=cwd, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, errors='replace',
                         stdin=subprocess.DEVNULL, start_new_session=True)
    q = queue.Queue(); tail = deque(maxlen=45)
    def reader():
        try:
            for line in p.stdout:
                q.put(line)
        finally:
            q.put(None); p.stdout.close()
    thread = threading.Thread(target=reader, daemon=True); thread.start()
    start = time.monotonic(); heartbeat = start + 30
    try:
        with (logs / (label + '.log')).open('a') as log:
            while True:
                now = time.monotonic()
                if now - start > seconds:
                    raise TimeoutError(label + ' exceeded ' + str(seconds) + ' seconds')
                if now >= heartbeat:
                    print('[' + label + '] still running ' + str(int(now - start)) + 's', flush=True)
                    heartbeat = now + 30
                try:
                    line = q.get(timeout=.5)
                except queue.Empty:
                    continue
                if line is None:
                    break
                line = redact(line); tail.append(line); log.write(line); log.flush()
                print(line, end='', flush=True)
        rc = p.wait(timeout=10)
        if rc:
            raise RuntimeError(label + ' exit=' + str(rc) + '\n' + ''.join(tail)[-6000:])
    finally:
        if p.poll() is None:
            children = descendants(p.pid)
            signal_descendants(children, signal.SIGTERM)
            try:
                os.killpg(p.pid, signal.SIGTERM); p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL); p.wait()
            except ProcessLookupError:
                pass
            signal_descendants(children, signal.SIGKILL)
        thread.join(timeout=3)
    print('[' + label + '] PASS', flush=True)


def setup_environment(project, folder, app):
    env = dict(os.environ)
    # Keep the existing ChatGPT login; no direct image API or hosted Modal fallback.
    for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'ANTHROPIC_API_KEY',
                'MODAL_TOKEN_ID', 'MODAL_TOKEN_SECRET', 'MODAL_PROFILE'):
        env.pop(key, None)
    ret = project / 'return_output'; cache = ret / 'cache'; work = folder / 'work'
    for name, path in {
        'ROOM_RETURN_OUTPUT': ret, 'ROOM_RUN': folder, 'ROOM_APP': app,
        'ROOM_IMAGE_LOGS': folder / 'imagegen', 'LR_REPO_ROOT': work,
        'LR_SCANS_DIR': folder / 'input', 'LITEREALITY_OUTPUT': folder / 'scenes',
        'LITEREALITY_FINAL': folder / 'scenes', 'LITEREALITY_WEIGHTS': cache / 'litereality-models',
        'HF_HOME': cache / 'litereality-models', 'HUGGINGFACE_HUB_CACHE': cache / 'litereality-models/hub',
        'LITEREALITY_DINOV3': cache / 'litereality-models/dinov3',
        'LR_DINO_MODEL': cache / 'litereality-models/grounding-dino',
        'LR_DINO_EMBED_MODEL': cache / 'litereality-models/dinov2',
        'TORCH_HOME': cache / 'torch', 'XDG_CACHE_HOME': cache / 'xdg',
        'MPLCONFIGDIR': cache / 'matplotlib', 'TORCH_EXTENSIONS_DIR': cache / 'torch-extensions',
        'TRITON_CACHE_DIR': cache / 'triton', 'TMPDIR': folder / 'tmp',
    }.items():
        env[name] = str(path)
        if name not in ('LITEREALITY_DINOV3', 'LR_DINO_MODEL', 'LR_DINO_EMBED_MODEL'):
            path.mkdir(parents=True, exist_ok=True)
    env.update(PYTHONPATH=str(HERE) + os.pathsep + str(app / 'src'), PYTHONUNBUFFERED='1',
        PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', LR_IMAGE_PROVIDER='codex',
        LR_DINO_BACKEND='local', LR_IMAGE_WORKERS='1', LR_SEQUENTIAL='1', LR_COLOR='0',
        LR_CODEX_MODEL=env.get('LR_CODEX_MODEL', 'gpt-6-astra'), LR_CODEX_EFFORT='high',
        LR_CODEX_SESSION_SECONDS=env.get('ROOM_AGENT_SECONDS', '3600'),
        LITEREALITY_TRELLIS_LAUNCHER=str(HERE / 'trellis_launcher.py'),
        LITEREALITY_PROCEDURAL_PYTHON=sys.executable,
        ATTN_BACKEND='sdpa', SPARSE_ATTN_BACKEND='xformers',
        PYTORCH_CUDA_ALLOC_CONF='expandable_segments:True', OPENCV_IO_ENABLE_OPENEXR='1',
        FLEX_GEMM_AUTOTUNE_CACHE_PATH=str(cache / 'flex-gemm/autotune_cache.json'),
        CUDA_HOME=env.get('CUDA_HOME', '/usr/local/cuda-12.4'))
    for role in ('AGENT', 'AUTHOR', 'MATERIALS', 'QUALITY', 'REFINE', 'PROCEDURAL', 'CLASSIFY'):
        env['LR_' + role + '_PROVIDER'] = 'codex'
    env['HARNESS_VLM'] = 'codex'
    for role in ('HARNESS_MODEL', 'HARNESS_CRITIC_MODEL', 'LR_CHAIR_JUDGE_MODEL', 'LR_PROCEDURAL_MODEL', 'LR_COMPLETENESS_MODEL'):
        env[role] = env['LR_CODEX_MODEL']
    env['LR_DINO_PYTHON'] = env.get('ROOM_GPU_PYTHON', '/opt/room/envs/gpu/bin/python')
    env['LITEREALITY_TRELLIS_PYTHON'] = env['LR_DINO_PYTHON']
    env['LITEREALITY_BLENDER'] = env.get('ROOM_BLENDER', '/opt/room/tools/blender/blender')
    env['PATH'] = os.pathsep.join(['/opt/room/tools/codex-packaged/bin', '/opt/room/tools/node/bin',
        str(Path(sys.executable).parent), '/opt/room/bin', '/usr/local/cuda-12.4/bin', env.get('PATH', '')])
    env['ROOM_CODEX'] = env.get('ROOM_CODEX') or shutil.which('codex', path=env['PATH']) or 'codex'
    # Honor the scheduler's CUDA_VISIBLE_DEVICES. All GPU jobs use visible cuda:0; no nvidia-smi index guessing.
    backend = Path(env.get('ROOM_TRELLIS_SOURCE', '/opt/room/backends/TRELLIS.2'))
    link = work / 'TRELLIS.2'
    if link.is_symlink():
        if link.resolve() != backend.resolve():
            raise RuntimeError('TRELLIS source link differs')
    elif not link.exists():
        link.symlink_to(backend)
    cache_file = Path(env['FLEX_GEMM_AUTOTUNE_CACHE_PATH']); cache_file.parent.mkdir(parents=True, exist_ok=True)
    seed = Path('/opt/room/backends/flexgemm/autotune_cache.json')
    if not cache_file.exists() and seed.is_file():
        shutil.copyfile(seed, cache_file)
    return env


def ensure_app(expected):
    app = HERE / 'app'
    if not app.exists():
        temporary = HERE / ('app_extract_' + uuid.uuid4().hex[:8])
        extract_checked(HERE / 'app.tar.gz', temporary, expected)
        temporary.rename(app)
    with tarfile.open(HERE / 'app.tar.gz') as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            target = app / member.name
            if target.is_symlink() or not target.is_file() or digest(target) != hashlib.sha256(tar.extractfile(member).read()).hexdigest():
                raise RuntimeError('Unpacked application changed; use a fresh versioned package directory: ' + member.name)
    return app


def pack(folder, ret, invocation):
    """Allowlist generated review files; never traverse raw scans, model caches, auth or symlinks."""
    allowed = {'.json', '.jsonl', '.log', '.txt', '.md', '.png', '.jpg', '.jpeg', '.glb', '.blend',
               '.py', '.mtl', '.obj', '.webp', '.hdr', '.exr', '.bin', '.gltf', '.mp4', '.webm', '.html'}
    members = []
    for path in folder.rglob('*'):
        rel = path.relative_to(folder)
        if any(p in ('input', 'tmp', 'work', '__pycache__', '.codex', '.git') for p in rel.parts):
            continue
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in allowed:
            continue
        if path.name in ('auth.json', 'credentials.json', 'config.toml', 'manifest_sha256.json'):
            continue
        members.append(path)
    manifest = {p.relative_to(folder).as_posix(): digest(p) for p in sorted(members)}
    mf = folder / 'manifest_sha256.json'; save(mf, manifest); members.append(mf)
    target = ret / ('room_002_full_' + invocation + '.tar.gz')
    with target.open('wb') as raw, gzip.GzipFile(fileobj=raw, filename='', mode='wb', mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w') as tar:
            for p in sorted(members):
                info = tarfile.TarInfo(folder.name + '/' + p.relative_to(folder).as_posix())
                info.size = p.stat().st_size; info.mode = 0o644; info.mtime = 0
                with p.open('rb') as f:
                    tar.addfile(info, f)
    target.with_name(target.name + '.sha256').write_text(digest(target) + '  ' + target.name + '\n')
    print('RETURN_TAR: ' + str(target), flush=True)
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-root', type=Path, default=Path('/yinxin/projects/room'))
    parser.add_argument('--scan-archive', type=Path)
    parser.add_argument('--expected-sha', default=SCAN_SHA)
    parser.add_argument('--prepare-only', action='store_true', help='Offline input/package checks only; not a reconstruction pass')
    parser.add_argument('--collect-only', action='store_true', help='Package existing outputs without model calls')
    args = parser.parse_args()
    project = args.project_root.resolve(); ret = project / 'return_output'; ret.mkdir(parents=True, exist_ok=True)
    invocation = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '_' + uuid.uuid4().hex[:8]
    identity = hashlib.sha256((args.expected_sha + (HERE / 'app.sha256').read_text().strip() +
        os.environ.get('LR_CODEX_MODEL', 'gpt-6-astra') + os.environ.get('ROOM_TRELLIS_RESOLUTION', '512')).encode()).hexdigest()[:12]
    folder = ret / 'artifacts' / ('room_002_full_v0.1.0_' + identity); folder.mkdir(parents=True, exist_ok=True)
    lock = (folder / '.run.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('A full-room run already holds this output directory. Do not start a second copy.', flush=True)
        return 2
    result = dict(status='failed', invocation=invocation, stage='initialize', folder=str(folder),
                  image_provider='codex_builtin', image_tool_provenance_may_be_indirect=True,
                  visual_fidelity_reviewed=False, robot_simulation_requested=False)
    app = HERE / 'app'
    try:
        if args.collect_only:
            print('Collecting existing results; no stages run.', flush=True)
            return 0
        archive = args.scan_archive or project / 'data/raw/room_002' / SCAN
        if digest(archive) != args.expected_sha:
            raise RuntimeError('Raw scan SHA256 mismatch; wrong or changed input')
        expected_app = (HERE / 'app.sha256').read_text().strip()
        if digest(HERE / 'app.tar.gz') != expected_app:
            raise RuntimeError('Application payload checksum mismatch')
        # Validate all package files before use; unpacked app is isolated from the image installation.
        manifest_path = HERE / 'package_manifest.json'
        if manifest_path.exists():
            for name, expected in json.loads(manifest_path.read_text()).items():
                if digest(HERE / Path(name).relative_to(Path(name).parts[0])) != expected:
                    raise RuntimeError('Package file changed: ' + name)
        app = ensure_app(expected_app)
        env = setup_environment(project, folder, app)
        input_root = folder / 'input/extracted'
        if not input_root.exists():
            temporary = input_root.with_name('extract_' + invocation)
            extract_checked(archive, temporary, args.expected_sha)
            temporary.rename(input_root)
        capture = find_capture(input_root)
        frames = frame_files(capture)
        save(folder / 'input.json', dict(archive=str(archive), sha256=args.expected_sha,
            capture=str(capture), frames=len(frames), app_sha256=expected_app,
            codex_model=env['LR_CODEX_MODEL'], trellis_resolution=env.get('ROOM_TRELLIS_RESOLUTION', '512'),
            raw_frame_pairs_validated=True,
            evidence_selection='upstream view ranking; depth fallback uses frame stride 4 and pixel stride 3'))
        if args.prepare_only:
            result.update(status='prepared_only', stage='offline_input_validation', full_run_completed=False)
            print('PREPARE_ONLY_PASS; models and reconstruction were NOT run.', flush=True)
            return 0
        stop = folder / 'imagegen/STOP.json'
        if stop.exists():
            stop.rename(stop.with_name('STOP_previous_' + invocation + '.json'))
        for label, cmd, timeout in [
            ('app_preflight', [sys.executable, HERE / 'preflight.py', 'app'], 180),
            ('mcp_preflight', [sys.executable, HERE / 'preflight.py', 'mcp'], 180),
            ('gpu_preflight', [env['LR_DINO_PYTHON'], HERE / 'preflight.py', 'gpu'], 180),
            ('gpu_kernels', [env['LR_DINO_PYTHON'], HERE / 'check_extensions.py'], 600),
            ('blender_cpu', [env['LITEREALITY_BLENDER'], '-b', '-t', '4', '--python-exit-code', '1', '--python', HERE / 'blender_smoke.py', '--', '--mode', 'cpu', '--output', folder / 'checks/blender'], 600),
            ('blender_eevee', [env['LITEREALITY_BLENDER'], '-b', '-t', '4', '--python-exit-code', '1', '--python', HERE / 'blender_smoke.py', '--', '--mode', 'eevee', '--output', folder / 'checks/blender'], 600),
            ('weights', [env['LR_DINO_PYTHON'], HERE / 'fetch_weights.py'], 21600),
            ('model_load', [env['LR_DINO_PYTHON'], HERE / 'preflight.py', 'models'], 1800),
        ]:
            result['stage'] = label; save(folder / 'result.json', result)
            command(label, cmd, Path(env['LR_REPO_ROOT']), env, folder, timeout)
        # No HF network or unpinned model resolution after the fixed snapshots have loaded.
        env['HF_HUB_OFFLINE'] = '1'; env['TRANSFORMERS_OFFLINE'] = '1'
        for stage in STAGES:
            result['stage'] = stage; save(folder / 'result.json', result)
            command(stage, [sys.executable, HERE / 'stage.py', stage, capture, folder / 'scenes'],
                    Path(env['LR_REPO_ROOT']), env, folder, 43200)
        result['stage'] = 'reopen_exports'
        preview = folder / 'scenes/room_002/realism_authoring/room_preview'
        command('reopen_exports', [env['LITEREALITY_BLENDER'], '-b', '-t', '4', '--python-exit-code', '1',
            '--python', HERE / 'verify_export.py', '--', preview], Path(env['LR_REPO_ROOT']), env, folder, 900)
        result.update(status='pipeline_completed_quality_review_pending', full_run_completed=True,
                      final_glb=str(preview / 'Room.glb'), final_blend=str(preview / 'Room.portable.blend'))
        return 0
    except BaseException as exc:
        error = redact(traceback.format_exc()); (folder / ('error_' + invocation + '.log')).write_text(error)
        result.update(error=str(exc), full_run_completed=False)
        print('\nFAILED at ' + result['stage'] + '\n' + error[-6500:], flush=True)
        return 1
    finally:
        if not args.collect_only:
            save(folder / 'result.json', result)
        try:
            pack(folder, ret, invocation)
        finally:
            lock.close()
        print('RESULT_DIR: ' + str(folder), flush=True)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt('SIGTERM received')))
    raise SystemExit(main())
