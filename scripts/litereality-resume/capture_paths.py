"""Repair the raw-capture path contract without regenerating assets."""
import hashlib
import json
from pathlib import Path
import re


def repair_capture(folder, capture, expected_frames):
    folder, capture = Path(folder).resolve(), Path(capture).resolve()
    if not capture.is_relative_to(folder / 'input'):
        raise ValueError('Raw capture must remain in this run input directory')
    frames = sorted(p for p in capture.glob('frame_*.json') if re.fullmatch(r'frame_\d+\.json', p.name))
    if len(frames) != expected_frames or not (capture / 'room.usdz').is_file():
        raise ValueError('Missing raw room.usdz or wrong camera-frame count')
    for pose in frames:
        if not pose.with_suffix('.jpg').is_file() or not pose.with_name(pose.name.replace('frame_', 'depth_').replace('.json', '.png')).is_file():
            raise ValueError('Missing RGB/depth frame pair: ' + pose.name)
    scene = folder / 'scenes/room_002'; manifest = scene / 'scene.json'
    before = manifest.read_bytes(); data = json.loads(before)
    if data.get('scan') != 'room_002':
        raise ValueError('This repair only applies to room_002')
    staged = scene / 'scene_init/obj_stage/object_init/input/usdz_files'
    links = [folder / 'input/room_002', scene / 'capture/room_002']
    # Validate every destination before changing any path. Only replace the known broken link.
    for link in links:
        if link.is_symlink():
            if link.resolve() not in (capture, staged.resolve()):
                raise ValueError('Unexpected capture link: ' + str(link))
        elif link.exists():
            raise ValueError('Refusing to replace a real file/directory: ' + str(link))
    backup = folder / 'capture_repair_backups'; backup.mkdir(exist_ok=True)
    backup_file = backup / ('scene_' + hashlib.sha256(before).hexdigest()[:12] + '.json')
    if not backup_file.exists():
        backup_file.write_bytes(before)
    for link in links:
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() and link.resolve() == capture:
            continue
        if link.is_symlink():
            link.unlink()
        link.symlink_to(capture, target_is_directory=True)
    data.setdefault('paths', {})['capture'] = 'capture/room_002'
    data.setdefault('roots', {})['scans'] = str(folder / 'input')
    data['capture'] = dict(mode='link', source=str(capture), exists=True, frames=len(frames),
                           poses=len(frames), has_usdz=True, has_depth=True)
    data.setdefault('providers', {})['image'] = 'codex_builtin'
    data.setdefault('models', {})['image'] = 'builtin_image_model_not_recorded'
    audit = folder / 'audit_reconstruct.json'
    if audit.is_file():
        assets = json.loads(audit.read_text())['expected_assets']
        data.setdefault('init', {}).update(objects=assets, n_objects=len(assets))
    encoded = (json.dumps(data, indent=2, ensure_ascii=False) + '\n').encode()
    if before != encoded:
        temporary = manifest.with_suffix('.repair.tmp'); temporary.write_bytes(encoded); temporary.replace(manifest)
    return dict(status='repaired', capture=str(capture), aliases=[str(p) for p in links],
                frames=len(frames), manifest=str(manifest), backup=str(backup_file))
