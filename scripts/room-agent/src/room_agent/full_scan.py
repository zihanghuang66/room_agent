"""Adapter for the unchanged full-scan runner; its pipeline.json owns stage recovery.

The agent owns admission, immutable configuration, locking and presentation only.
Raw pipeline nodes are deliberately not a second LangGraph workflow.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import threading

from .processes import run_process
from .saved_scene import file_hash
from .storage import Journal, atomic_json, identifier, read_json, room_busy, room_lock

VERSION = 'legacy-full-scan/1'
LEGACY_VERSION = '0.3.1'
STAGES = ('ingest', 'reconstruct', 'seed', 'author', 'publish')
ENV_DEFAULTS = {
    'LR_CODEX_MODEL': 'gpt-6-astra', 'ROOM_TRELLIS_RESOLUTION': '512',
    'ROOM_APP_PYTHON': '/opt/room/envs/app/bin/python',
    'ROOM_GPU_PYTHON': '/opt/room/envs/gpu/bin/python',
    'ROOM_BLENDER': '/opt/room/tools/blender/blender',
    'ROOM_TRELLIS_SOURCE': '/opt/room/backends/TRELLIS.2',
    'ROOM_MAX_IMAGE_CALLS': '64', 'ROOM_AGENT_SECONDS': '3600',
}


def run_backend(command, log, *, seconds, env, pass_fds=()):
    """Keep the durable subprocess log and stream new output to CLI/web console."""
    log = Path(log)
    offset = log.stat().st_size if log.exists() else 0
    stopped = threading.Event()
    def forward():
        position = offset
        while True:
            block = b''
            if log.is_file():
                with log.open('rb') as stream:
                    stream.seek(position)
                    block = stream.read(65536)
                    position += len(block)
            if block:
                try:
                    print(block.decode('utf-8', errors='replace'), end='', flush=True)
                except (BrokenPipeError, OSError, ValueError):
                    return
            if stopped.is_set() and not block:
                return
            stopped.wait(.2)
    reader = threading.Thread(target=forward, daemon=True)
    reader.start()
    try:
        return run_process(command, log, seconds=seconds, env=env, pass_fds=pass_fds)
    finally:
        stopped.set()
        reader.join(timeout=3)


def full_root():
    return Path(__file__).resolve().parents[3] / 'litereality-full-v0.3.1'


def backend_identity():
    root = full_root()
    if not (root / 'run.py').is_file():
        raise ValueError('Full scan backend is missing')
    manifest_path = root / 'package_manifest.json'
    manifest = read_json(manifest_path)
    files = {'package_manifest.json': file_hash(manifest_path)}
    # Use the shipped file set so a source checkout and its delivery have identical identity.
    # The generated unpacked app is checked by the backend against app.tar.gz.
    for name, expected_hash in sorted(manifest.items()):
        relative = Path(*PurePosixPath(name).parts[1:])
        if not relative.parts or '..' in relative.parts:
            raise ValueError('Unsafe full scan backend manifest')
        path = no_links(root / relative, root)
        actual = file_hash(path)
        if actual != expected_hash:
            raise ValueError('Full scan backend package checksum mismatch: ' + str(relative))
        files[relative.as_posix()] = actual
    expected = (root / 'app.sha256').read_text().strip()
    if files.get('app.tar.gz') != expected:
        raise ValueError('Full scan backend application checksum mismatch')
    return {'files': files, 'app_sha256': expected, 'version': LEGACY_VERSION}


def no_links(path, root):
    """Constrain a path and each component before following filesystem links."""
    if '..' in Path(path).parts or '..' in Path(root).parts:
        raise ValueError('Parent traversal is not allowed in a confined path')
    path, root = Path(path).absolute(), Path(root).absolute()
    if not path.is_relative_to(root):
        raise ValueError('Path escapes permitted directory: ' + str(root))
    for item in (root, *root.parents, path, *path.parents):
        if item.is_symlink():
            raise ValueError('symlink path is not supported: ' + str(item))
    return path


def scan_path(scan_archive, project):
    project = Path(project).resolve()
    raw = project / 'data/raw'
    archive = Path(scan_archive).absolute()
    if not archive.is_relative_to(raw):
        raise ValueError('Scan archive must be under project data/raw')
    no_links(archive, raw)
    if not archive.is_file():
        raise ValueError('Scan archive must be a regular file under data/raw')
    return archive


def validate_tar(path):
    try:
        with tarfile.open(path) as archive:
            seen = set()
            total = 0
            for member in archive:
                name = PurePosixPath(member.name)
                if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                    raise ValueError('Unsafe tar archive entry')
                key = name.as_posix()
                if key in seen:
                    raise ValueError('Duplicate tar archive entry')
                seen.add(key)
                total += member.size
                if total > 32 * 1024**3:
                    raise ValueError('Scan tar exceeds 32 GiB extraction limit')
            if not seen:
                raise ValueError('Scan tar archive is empty')
    except (tarfile.TarError, OSError) as exc:
        raise ValueError('Invalid scan tar archive: ' + str(exc)) from exc


def discover_scans(project):
    project = Path(project).resolve()
    raw = project / 'data/raw'
    result = []
    if not raw.is_dir() or raw.is_symlink():
        return result
    for base, dirs, files in os.walk(raw, followlinks=False):
        dirs[:] = sorted(name for name in dirs if not (Path(base) / name).is_symlink())
        for name in sorted(files):
            if not name.endswith(('.tar', '.tar.gz', '.tgz')):
                continue
            try:
                path = scan_path(Path(base) / name, project)
                if not tarfile.is_tarfile(path):
                    continue
                stem = name.removesuffix('.gz').removesuffix('.tar').removesuffix('.tgz')
                scene = re.sub('[^A-Za-z0-9_-]', '_', stem).lstrip('_-')[:80] or 'scan'
                result.append({'path': str(path), 'label': str(path.relative_to(raw)), 'scene': scene})
            except (OSError, ValueError):
                continue
    return result


def capture_input(scan_archive, scene, project, run_id, prepare_only):
    identifier(scene)
    if type(prepare_only) is not bool:
        raise ValueError('prepare_only must be boolean')
    project = Path(project).resolve()
    archive = scan_path(scan_archive, project)
    validate_tar(archive)
    env = {key: os.environ.get(key, default) for key, default in ENV_DEFAULTS.items()}
    # Capture overrides without including credentials or account configuration.
    for key in ('ROOM_CODEX', 'CUDA_VISIBLE_DEVICES'):
        if key in os.environ:
            env[key] = os.environ[key]
    env['ROOM_APP_PYTHON'] = os.path.abspath(env['ROOM_APP_PYTHON'])
    return {'scene': scene, 'upstream_scene': scene[:47] + '_' + run_id,
            'scan_archive': str(archive), 'archive_sha256': file_hash(archive),
            'project': str(project), 'prepare_only': prepare_only,
            'backend': backend_identity(), 'environment': env}


def backend_config(request):
    return {'scene': request['upstream_scene'], 'archive_sha256': request['archive_sha256'],
            'app_sha256': request['backend']['app_sha256'], 'version': request['backend']['version'],
            'codex_model': request['environment']['LR_CODEX_MODEL'],
            'trellis_resolution': request['environment']['ROOM_TRELLIS_RESOLUTION']}


def upstream_folder(request):
    config = backend_config(request)
    identity = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]
    return Path(request['project']) / 'return_output/artifacts' / (
        request['upstream_scene'] + '_full_v' + config['version'] + '_' + identity)


def checked_json(root, relative):
    path = no_links(Path(root) / relative, root)
    if not path.exists():
        return {}
    try:
        return read_json(path)
    except json.JSONDecodeError:
        # Legacy progress writes are not atomic; the next poll observes the completed write.
        return {}


def verify(request):
    archive = scan_path(request['scan_archive'], request['project'])
    if file_hash(archive) != request['archive_sha256']:
        raise ValueError('Scan archive checksum mismatch; create a new request for changed input')
    if backend_identity() != request['backend']:
        raise ValueError('Full scan backend version/checksum changed; create a new request after compatibility review')
    validate_tar(archive)
    no_links(upstream_folder(request), Path(request['project']) / 'return_output/artifacts')


def summary(root, request, directory, *, locked=False):
    folder = upstream_folder(request)
    legacy = checked_json(folder, 'result.json')
    pipeline = checked_json(folder, 'scenes/' + request['upstream_scene'] + '/.litereality/pipeline.json')
    journal = Journal(directory, request['run_id'])
    terminal = next((event for event in reversed(journal.read()) if event['kind'] in
                     ('run_started', 'operation_failed', 'operation_finished')), {})
    status, error = 'created', None
    if terminal.get('kind') == 'run_started':
        status = 'running' if locked or room_busy(root / 'run_activity', request['run_id']) else 'interrupted'
    elif terminal.get('kind') == 'operation_failed':
        error = terminal['error']
        status = 'interrupted' if error['type'] in ('KeyboardInterrupt', 'InterruptedError') else 'failed'
    elif terminal.get('kind') == 'operation_finished':
        status = 'completed'
    stage = legacy.get('stage', 'input_validation')
    stages = []
    for name in STAGES:
        item = pipeline.get('stages', {}).get(name, {})
        state = item.get('status', 'pending')
        state = 'completed' if state == 'reused' else state
        if name == stage and status in ('running', 'failed', 'interrupted'):
            state = status
        if status == 'completed' and legacy.get('status') == 'prepared_only':
            state = 'skipped'
        stages.append({'node': name, 'status': state, 'seconds': item.get('duration_seconds'),
                       'error': item.get('error')})
    index = read_json(directory / 'artifacts.json') if (directory / 'artifacts.json').exists() else {}
    complete = status == 'completed' and legacy.get('full_run_completed') is True
    return {'run_id': request['run_id'], 'room_id': request['room_id'], 'scene': request['scene'],
            'upstream_scene': request['upstream_scene'], 'operation': 'full_scan',
            'execution_status': status, 'quality_status': 'human_review_pending' if complete else 'unreviewed',
            'current_node': 'finished' if status == 'completed' else (error['node'] if error else stage),
            'completion_reason': legacy.get('status') if status == 'completed' else None,
            'error': error, 'stages': stages, 'allowed_actions': [], 'next_nodes': [],
            'source_run': str(folder), 'artifact_path': str(directory / 'legacy'),
            'previews': index.get('previews', []), 'exports': index.get('exports', []),
            'logs': [{'path': 'logs/full_scan.log', 'label': '完整扫描日志'}]
                    if (directory / 'logs/full_scan.log').is_file() else [],
            'prepare_only': request['prepare_only'], 'legacy_adapter': True,
            'upstream_status': legacy.get('status'), 'upstream_stage': stage,
            'full_run_completed': complete, 'human_visual_reviewed': False}


def collect(request, directory):
    """Copy only presentation/diagnostics; never expose a symlink into the legacy tree."""
    folder = upstream_folder(request)
    files = []
    published = False
    if folder.exists():
        no_links(folder, folder.parent)
        for base, dirs, names in os.walk(folder, followlinks=False):
            rel = Path(base).relative_to(folder)
            dirs[:] = sorted(name for name in dirs if name not in
                ('input', 'work', 'tmp', 'cache', '.codex', '.git', '__pycache__')
                and not (Path(base) / name).is_symlink())
            for name in sorted(names):
                path = Path(base) / name
                if path.is_symlink() or not path.is_file():
                    continue
                # Detailed evidence stays in the upstream folder; carry safe diagnostics and viewable outputs.
                display = any(part in rel.parts for part in ('candidate_preview', 'room_preview', 'pairs', 'compare'))
                diagnostic = path.suffix in ('.json', '.jsonl', '.log', '.txt', '.md')
                if (not (diagnostic or display) or
                    path.suffix.lower() not in ('.json', '.jsonl', '.log', '.txt', '.md', '.png', '.jpg', '.jpeg', '.webp', '.glb', '.blend') or
                    name in ('auth.json', 'credentials.json', 'config.toml') or name.startswith('.env')):
                    continue
                target = directory / 'legacy' / path.relative_to(folder)
                no_links(target, directory)
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name('.' + target.name + '.copy')
                no_links(temporary, directory)
                shutil.copyfile(path, temporary)
                os.replace(temporary, target)
                files.append(target)
        result = checked_json(folder, 'result.json')
        if result.get('full_run_completed') is True:
            try:
                validate_completion(request, folder, result)
                published = True
            except (ValueError, RuntimeError):
                pass
    def entry(path):
        return {'path': path.relative_to(directory).as_posix(), 'label': path.name,
                'candidate': not published or 'candidate_preview' in path.parts}
    previews = [entry(p) for p in files if p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp')][-48:]
    exports = [entry(p) for p in files if p.suffix.lower() in ('.glb', '.blend')]
    atomic_json(directory / 'artifacts.json', {'previews': previews, 'exports': exports})


def validate_completion(request, folder, result):
    if result.get('scene') != request['upstream_scene']:
        raise RuntimeError('Legacy runner completion scene mismatch')
    if request['prepare_only'] and result.get('status') == 'prepared_only':
        return
    pipeline = checked_json(folder, 'scenes/' + request['upstream_scene'] + '/.litereality/pipeline.json')
    if (result.get('status') != 'pipeline_completed_human_review_pending' or
            result.get('full_run_completed') is not True or
            any(pipeline.get('stages', {}).get(name, {}).get('status') not in ('completed', 'reused', 'skipped')
                for name in STAGES)):
        raise RuntimeError('Legacy runner did not produce a valid completion receipt and successful pipeline stages')


def execute(service, request, directory, *, action=None):
    if action is not None:
        raise ValueError('Full scan recovery takes no repair/finish action; resume this run explicitly')
    with (room_lock(service.root / 'resources', 'scene_execution') as resource_lease,
          room_lock(service.root, request['room_id']) as room_lease,
          room_lock(service.root / 'run_activity', request['run_id']) as activity_lease):
        journal = Journal(directory, request['run_id'])
        try:
            verify(request)
            prior = summary(service.root, request, directory, locked=True)
            if prior['execution_status'] == 'completed':
                return prior
            folder = upstream_folder(request)
            command = [request['environment']['ROOM_APP_PYTHON'], '-u', str(full_root() / 'run.py'),
                       '--project-root', request['project'], '--scan-archive', request['scan_archive'],
                       '--scene', request['upstream_scene'], '--expected-sha', request['archive_sha256']]
            if folder.exists():
                previous = checked_json(folder, 'input.json')
                result = checked_json(folder, 'result.json')
                if not previous or not result:
                    raise ValueError('Incomplete legacy input/result metadata; inspect preserved folder before recovery')
                for key, value in backend_config(request).items():
                    if previous.get(key) != value:
                        raise ValueError('Legacy resume configuration mismatch: ' + key)
                if result.get('scene') != request['upstream_scene']:
                    raise ValueError('Legacy resume scene mismatch')
                if result.get('full_run_completed') is True:
                    # A worker can die after legacy success but before its own completion receipt.
                    validate_completion(request, folder, result)
                    collect(request, directory)
                    journal.emit('operation_finished', recovered_completion=True)
                    final = summary(service.root, request, directory, locked=True)
                    atomic_json(directory / 'result.json', final)
                    return final
                command += ['--resume-from', str(folder)]
            if request['prepare_only']:
                command.append('--prepare-only')
            journal.emit('run_started', adapter='legacy_full_scan', source_run=str(folder))
            # Inherited application knobs can redirect artifacts or change resumed work.
            # Admit only the supported configuration captured in the immutable request.
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(('ROOM_', 'LR_', 'LITEREALITY_', 'HARNESS_'))}
            for key in ('CUDA_VISIBLE_DEVICES', 'PYTHONPATH', 'PYTHONHOME'):
                env.pop(key, None)
            env.update(request['environment'], PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')
            runner = service.full_process_runner or run_backend
            leases = {} if service.full_process_runner else {
                'pass_fds': tuple(handle.fileno() for handle in (resource_lease, room_lease, activity_lease))}
            runner(command, directory / 'logs/full_scan.log', seconds=72 * 3600, env=env, **leases)
            verify(request)
            result = checked_json(folder, 'result.json')
            validate_completion(request, folder, result)
            collect(request, directory)
            journal.emit('operation_finished')
        except BaseException as exc:
            try:
                collect(request, directory)
            except Exception as copy_error:
                journal.emit('artifact_collection_failed', message=str(copy_error))
            legacy = checked_json(upstream_folder(request), 'result.json')
            journal.emit('operation_failed', error={'type': type(exc).__name__, 'message': str(exc)[-8000:],
                         'node': legacy.get('stage', 'input_validation')})
            atomic_json(directory / 'result.json', summary(service.root, request, directory, locked=True))
            raise
        final = summary(service.root, request, directory, locked=True)
        atomic_json(directory / 'result.json', final)
        return final


def package_return(service, request, directory):
    from .reports import make_archive
    with room_lock(service.root, request['room_id']):
        collect(request, directory)
        atomic_json(directory / 'result.json', summary(service.root, request, directory))
        destination = Path(request['project']) / 'return_output' / (
            'room_agent_stage3_' + request['run_id'] + '.tar.gz')
        return make_archive(directory, destination)
