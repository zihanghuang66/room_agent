"""Explicit existing-run inputs; no global 'latest' selection or reconstruction."""
import hashlib
import os
from pathlib import Path

from .storage import identifier, read_json, digest

VERSION = 'saved-scene-review/1'
APP_SHA = '8748b08b2ed7790a7b63f389a523a86707b23b8adb6522810b40e401f194fc94'


def legacy_root():
    return Path(__file__).resolve().parents[3] / 'litereality-batch-review-v0.1.0'


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def capture_names(root):
    return {p.name for pattern in ('frame_*.jpg', 'frame_*.json', 'depth_*.png')
            for p in Path(root).glob(pattern)}


def tree_hashes(root, *, exclude_root_names=()):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Expected regular directory: ' + str(root))
    result = {}
    for base, dirs, files in os.walk(root, followlinks=False):
        # Prune before inspecting links; never traverse excluded generated previews.
        excluded = exclude_root_names if Path(base) == root else ()
        dirs[:] = sorted(d for d in dirs if d not in ('__pycache__', '.git', '.codex'))
        dirs[:] = [d for d in dirs if d not in excluded]
        for name in dirs + sorted(files):
            if name in excluded:
                continue
            path = Path(base) / name
            if path.is_symlink():
                raise ValueError('Source symlink requires explicit handling: ' + str(path))
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError('Non-regular source file: ' + str(path))
            result[path.relative_to(root).as_posix()] = file_hash(path)
    return result


def source_tree_hashes(root):
    """Match legacy batch_io.source_files for saved authoring inputs only.

    Top-level previews are rebuilt from Room.py and Object assets. Output receipts
    must continue using strict tree_hashes, including all generated render assets.
    """
    return tree_hashes(root, exclude_root_names=('room_preview', 'repair_preview'))


def request_source_hashes(request, key):
    """New revisions omit known generated previews; old inventories keep their policy."""
    policy = request.get('source_preview_policy')
    if policy is None:
        return source_tree_hashes(request[key])
    if policy != 'generated-preview-v2':
        raise ValueError('Unknown saved-source preview policy')
    return tree_hashes(request[key], exclude_root_names=('room_preview', 'repair_preview', 'revision_preview'))


def engine_hash():
    own = Path(__file__).parent
    files = {f'agent/{p.name}': file_hash(p) for p in own.glob('*.py')}
    from .edit_behavior import SKILL
    files['skill/spatial-edit']=file_hash(SKILL)
    from .review_targets import SKILL as REVIEW_SKILL
    files['skill/spatial-review']=file_hash(REVIEW_SKILL)
    files.update({f'legacy/{k}': v for k, v in tree_hashes(legacy_root()).items()
                  if k.endswith(('.py', '.md', '.toml', '.env', '.sha256')) and not k.startswith('tests/')})
    return digest(files)


def source_info(source_run):
    source = Path(source_run).resolve()
    if source.parent.name != 'artifacts' or source.parent.parent.name != 'return_output':
        raise ValueError('Choose an explicit existing return_output/artifacts run')
    data = read_json(source / 'input.json')
    scene = identifier(data['scene'])
    if data.get('app_sha256') != APP_SHA:
        raise ValueError('Source application version differs; compatibility review required')
    root = source / 'scenes' / scene
    room = root / 'realism_authoring/room'
    if (source / 'latest_candidate.json').exists():
        room = Path(read_json(source / 'latest_candidate.json')['source']).resolve()
    seed = root / 'scene_init/scene_stage/room_init/room'
    if not room.resolve().is_relative_to(source):
        raise ValueError('Candidate source escapes explicit run')
    for directory in (room, seed):
        for name in ('Room.py', 'Room.md', 'manifest.json'):
            if not (directory / name).is_file():
                raise ValueError('Missing saved scene file: ' + str(directory / name))
    capture = Path(data['capture']).resolve()
    cameras = sorted(capture.glob('frame_*.json'))
    if not cameras:
        raise ValueError('Original capture frames/poses are missing; a return tar is insufficient')
    files = set()
    for camera in cameras:
        for p in (camera, camera.with_suffix('.jpg'), capture / ('depth_' + camera.stem[6:] + '.png')):
            if not p.is_file() or p.is_symlink():
                raise ValueError('Missing/linked capture evidence: ' + str(p))
            files.add(p)
    if capture_names(capture) != {p.name for p in files}:
        raise ValueError('Capture contains unpaired RGB/depth/pose files')
    return {'source_run': str(source), 'source': str(room), 'seed': str(seed),
            'capture': str(capture), 'scene': scene, 'project': str(source.parent.parent.parent),
            'source_files': source_tree_hashes(room), 'seed_files': source_tree_hashes(seed),
            'capture_files': {p.name: file_hash(p) for p in sorted(files)}, 'app_sha256': APP_SHA}


def discover_sources(project):
    result = []
    # Discovery is lightweight; full hashes are taken only after explicit selection.
    for p in sorted((Path(project) / 'return_output/artifacts').glob('*/input.json')):
        try:
            data = read_json(p)
            if data.get('app_sha256') != APP_SHA or not data.get('scene'):
                continue
            root = p.parent / 'scenes' / identifier(data['scene'])
            if not (root / 'realism_authoring/room/Room.py').is_file():
                continue
            result.append({'path': str(p.parent.resolve()), 'scene': data['scene'],
                           'label': data['scene'] + ' — ' + p.parent.name})
        except (OSError, ValueError, KeyError):
            continue
    return result
