"""Read-only UI projection, deliberately outside execution fingerprint inputs.

Only renderer-produced camera pairs are comparisons. Never infer scene views
from arbitrary PNG files (which include material maps and object references).
"""
import os
import json
import re
import struct
from collections import defaultdict
from pathlib import Path

STAGES = ['prepare', 'preflight', 'ingest', 'reconstruct', 'seed', 'author', 'publish', 'export_check']
ROOM_FILES = {'room.blend', 'room.portable.blend', 'room.glb'}
REPORTS = {'assessment.json', 'author_acceptance.json', 'visual_review.json', 'validation.json', 'acceptance.json', 'review.json', 'geometry.json'}


def safe(root, path):
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    if root.is_symlink() or '..' in rel.parts:
        return False
    cursor = root
    for part in rel.parts:
        cursor /= part
        if cursor.is_symlink():
            return False
    return path.is_file()


def png_size(path):
    try:
        with path.open('rb') as f:
            data = f.read(24)
        if len(data) == 24 and data[:8] == b'\x89PNG\r\n\x1a\n' and data[12:16] == b'IHDR':
            w, h = struct.unpack('>II', data[16:24])
            if w > 0 and h > 0:
                return w, h
    except OSError:
        pass
    return None


def nearest_assessment(root, path):
    """Both legacy rounds and nested closeout views inherit their own assessment."""
    for parent in path.parents:
        if parent == root or parent.name == 'realism_authoring':
            break
        report = parent / 'assessment.json'
        if safe(root, report):
            return report
    return None


def rank(root, path):
    rel = path.relative_to(root)
    parts = rel.parts
    if parts[0] == 'published' and len(parts) > 1:
        stage = STAGES.index(parts[1]) if parts[1] in STAGES else -1
        base = (2, stage)
    elif parts[:2] == ('backend', 'scenes'):
        base = (3, 0)
    else:
        match = re.search(r'/r(\d+)_(build|render)/', '/' + rel.as_posix())
        base = (2, int(match[1]) * 2 + (match[2] == 'render')) if match else (1, 0)
    assessment = nearest_assessment(root, path)
    attempt = next((p for p in path.parents if p.name.startswith('attempt-')), None)
    revision_time = assessment.stat().st_mtime_ns if assessment else attempt.stat().st_mtime_ns if attempt else 0
    round_match = re.search(r'/round_(\d+)/', '/' + rel.as_posix() + '/')
    views_match = re.search(r'/views_(\d+)/', '/' + rel.as_posix() + '/')
    try:
        modified = path.stat().st_mtime_ns
    except OSError:
        modified = 0
    return (*base, int('realism_authoring' in parts), int(any(part.startswith('publish-review-') for part in parts)), int('closeout' in parts),
            revision_time, int(round_match[1]) if round_match else -1,
            int(views_match[1]) if views_match else -1, modified, rel.as_posix())


def build_index(root, status=None, *, include_drafts=False):
    root = Path(root).absolute()
    status = status or {}
    groups = defaultdict(list)
    rooms = defaultdict(list)
    other = []
    reports = []
    unavailable = 0
    # Published snapshots contain uncapped outputs. Saved-scene reviews retain
    # render attempts. Do not traverse the input capture or credentials.
    roots = [root / 'published', root / 'work' / 'attempts']
    if include_drafts and status.get('operation') == 'full_scan' and status.get('execution_status') in {'failed', 'interrupted', 'running', 'waiting_for_input', 'completed'}:
        roots.append(root / 'backend' / 'scenes')
    for base in roots:
        if not base.is_dir() or base.is_symlink() or any(p.is_symlink() for p in base.parents if p != root.parent):
            continue
        for directory, dirs, files in os.walk(base, followlinks=False):
            folder = Path(directory)
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d.lower() not in {'display', 'textures', 'materials', 'capture', 'input', 'cache', '__pycache__'} and not (folder / d).is_symlink())
            for name in sorted(files):
                p = folder / name
                if not safe(root, p):
                    continue
                rel = p.relative_to(root)
                draft = rel.parts[:2] == ('backend', 'scenes')
                # A stage snapshot is not proof of Author acceptance. Legacy or
                # interrupted snapshots can retain unapproved room files.
                if status.get('operation') == 'full_scan' and rel.parts[:2] == ('published', 'author'):
                    author = next((parent for parent in p.parents if parent.name == 'realism_authoring'), None)
                    report = author / 'room_preview/author_acceptance.json' if author else None
                    try:
                        draft = not (report and safe(root, report) and json.loads(report.read_text()).get('accepted') is True)
                    except (OSError, ValueError):
                        draft = True
                if status.get('operation') == 'full_scan' and rel.parts[0] == 'published' and ('acceptance' in rel.parts or 'closeout' in rel.parts and 'reviews' in rel.parts or any(part.startswith('publish-review-') for part in rel.parts)):
                    assessment = nearest_assessment(root, p)
                    try:
                        draft = draft or not (assessment and safe(root, assessment) and json.loads(assessment.read_text()).get('accepted') is True)
                    except (OSError, ValueError):
                        draft = True
                if draft and not include_drafts:
                    continue
                row = {'path': rel.as_posix(), 'draft': draft}
                match = re.fullmatch(r'pair_(\d+)\.png', name)
                if match and folder.name == 'pairs' and not any(x.lower() in {'object', 'objects'} for x in p.relative_to(root).parts):
                    if not include_drafts and 'acceptance' in p.relative_to(root).parts:
                        report=nearest_assessment(root,p)
                        try:
                            if not report or not safe(root,report) or json.loads(report.read_text()).get('accepted') is not True:continue
                        except (OSError,ValueError):continue
                    raw = folder.parent / 'renders' / ('frame_' + match[1] + '.png')
                    size = png_size(p)
                    raw_size = png_size(raw) if safe(root, raw) else None
                    # Contract of the bundled render_vs_capture._make_pairs:
                    # 34px label, 720px image, 8px gutter, rendered then captured.
                    widths = {round(720 * raw_size[0] / raw_size[1]), round(720 * raw_size[1] / raw_size[0])} if raw_size else set()
                    w = (size[0] - 8) // 2 if size else -1
                    if size and size[1] == 754 and size[0] == 2*w+8 and w in widths:
                        row.update(width=size[0], height=size[1], captured=[w+8,34,w,720], rendered=[0,34,w,720])
                        groups[folder].append(row)
                    else:
                        unavailable += 1
                elif p.suffix.lower() in {'.glb', '.blend'}:
                    if name.lower() in ROOM_FILES and (folder.name in {'room_preview', 'repair_preview'} or folder.name.startswith('attempt-') and folder.parent.name == 'reviews' and 'closeout' in rel.parts or folder.name == 'renders' and folder.parent.name == 'compare') and not any(x.lower() in {'object','objects'} for x in p.relative_to(root).parts):
                        rooms[folder].append(row)
                    else:
                        other.append(row)
                elif name in REPORTS:
                    reports.append(row)
    comparisons = [{'key': folder.relative_to(root).as_posix(), 'draft': rows[0]['draft'], 'pairs': rows} for folder, rows in sorted(groups.items(), key=lambda x: rank(root, x[0]), reverse=True)]
    room_groups = sorted(rooms, key=lambda p: rank(root, p), reverse=True)
    # Retained comparison builds keep previews/downloads on one revision. A
    # completed draft with fingerprint-backed export verification instead uses
    # the exact room_preview files that the final export checks examined.
    preferred = None
    if comparisons:
        latest = root / comparisons[0]['key']
        if comparisons[0]['draft'] or 'closeout' in latest.parts:
            matching = latest.parent / 'renders'
            assessment = nearest_assessment(root, latest)
            retained = assessment.parent if assessment and 'closeout' in latest.parts else None
            preferred = retained if retained in room_groups else matching
    if (include_drafts and status.get('operation') == 'full_scan'
            and status.get('execution_status') == 'completed'
            and status.get('quality_status') == 'not_passed'
            and status.get('technical_export_verified') is True):
        checked = [p for p in room_groups if p.relative_to(root).parts[:2] == ('backend', 'scenes')
                   and p.name == 'room_preview' and p.parent.name == 'realism_authoring']
        if checked:
            preferred = checked[0]
    if (status.get('operation') == 'full_scan'
            and status.get('execution_status') == 'completed'
            and status.get('full_run_completed') is True):
        final_exports = [p for p in room_groups if p.relative_to(root).parts[:2] == ('published', 'export_check')
                         and p.name == 'room_preview' and p.parent.name == 'realism_authoring']
        if final_exports:
            preferred = final_exports[0]
    # Saved-scene renders make disposable Room.blend/GLB copies. Downloads
    # belong to the exact build passed to that render, where export validation
    # produced Room.portable.blend. Never search a different run or old build.
    if comparisons and status.get('operation') in {'scene_edit', 'saved_scene_review'}:
        pairs_folder = root / comparisons[0]['key']
        payload_path = pairs_folder.parent.parent / 'request.json'
        if safe(root, payload_path):
            try:
                payload = json.loads(payload_path.read_text())
                linked = Path(payload['state']['preview'])
                if payload.get('stage') == 'render' and linked in room_groups:
                    preferred = linked
            except (OSError, ValueError, KeyError, TypeError):
                pass
    if preferred in room_groups:
        room_groups.remove(preferred)
        room_groups.insert(0, preferred)
    primary = rooms[room_groups[0]] if room_groups else []
    for p in room_groups[1:]:
        other.extend(rooms[p])
    reports.sort(key=lambda r: rank(root, root / r['path']), reverse=True)
    # Latest copy of each report type only; full audit trail stays in the return tar.
    latest_reports = {}
    for row in reports:
        latest_reports.setdefault(Path(row['path']).name, row)
    return {'comparisons': comparisons, 'files': primary, 'other_files': sorted(other, key=lambda r: r['path']), 'reports': list(latest_reports.values()), 'unavailable_pairs': unavailable}


def build_presentation(root,status):
    published=build_index(root,status)
    if status.get('operation') == 'scene_edit':
        return edit_presentation(Path(root), status, published)
    all_outputs=build_index(root,status,include_drafts=True)
    if status.get('operation')=='saved_scene_review' and status.get('quality_status')!='passed':
        diagnostics={k:published[k] for k in ('comparisons','files','reports','other_files','unavailable_pairs')}
        published.update(comparisons=[],files=[],reports=[],other_files=[],diagnostics=diagnostics)
        return published
    published['diagnostics']={
        'comparisons':[g for g in all_outputs['comparisons'] if g['draft']],
        'files':[r for r in all_outputs['files'] if r['draft']],
        'reports':[r for r in all_outputs['reports'] if r['draft']],
        'other_files':[r for r in all_outputs['other_files'] if r['draft']],
        'unavailable_pairs':all_outputs['unavailable_pairs'],
    }
    return published


def edit_presentation(root, status, index):
    # Baseline assets are comparison evidence, never child downloads.
    def baseline(row):
        return '/r00_baseline_' in '/' + row['path']
    index['comparisons'] = [g for g in index['comparisons'] if '/r00_baseline_' not in '/'+g['key']]
    index['files'] = [r for r in index['files'] if not baseline(r)]
    index['other_files'] = [r for r in index['other_files'] if not baseline(r)]
    views=[]
    path=root/'work/attempts/r00_finalize/stable/comparison.json'
    if safe(root,path):
        data=json.loads(path.read_text())
        for row in data.get('change_views',[]):
            p=Path(row['path'])
            b=Path(row['before_path'])
            if safe(root,p) and safe(root,b) and png_size(p)==png_size(b)==(row['width'],row['height']):
                views.append({**row,'path':p.relative_to(root).as_posix(),'before_path':b.relative_to(root).as_posix()})
    index['changes']=views
    empty={k:[] for k in ('comparisons','files','other_files','reports','changes')}
    empty['unavailable_pairs']=0
    accepted_edit=status.get('edit_status')=='passed' and status.get('technical_export_verified') is True
    if (status.get('quality_status') != 'passed' and not accepted_edit) or status.get('execution_status') != 'completed':
        return {**empty,'diagnostics':index}
    return {**index,'diagnostics':empty}
