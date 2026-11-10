"""Scene-derived review policy; shared by the read-only selector and reviewer."""
import asyncio
import hashlib
import json
from pathlib import Path

SKILL = Path(__file__).parent / 'skills/spatial-review/SKILL.md'


def skill_text():
    return SKILL.read_text(encoding='utf-8')


def make_contract(objects):
    if isinstance(objects, dict):
        objects = list(objects.values())
    ids = [x['id'] for x in objects]
    if not ids or any(not isinstance(x, str) or not x.strip() for x in ids) or len(ids) != len(set(ids)):
        raise ValueError('Review needs a nonempty, unique current object inventory')
    targets = {'object:' + x: {'object_id': x, 'purpose': 'identity, appearance, placement and support'} for x in ids}
    targets.update({
        'scene:lighting': {'object_id': None, 'purpose': 'illumination, exposure, reflections and major surfaces'},
        'scene:contacts': {'object_id': None, 'purpose': 'support surfaces, openings and ground contacts'},
        'scene:missing_objects': {'object_id': None, 'purpose': 'capture-visible objects missing from the layout; no invented identities'},
    })
    return {'schema': 1, 'skill_sha256': hashlib.sha256(SKILL.read_bytes()).hexdigest(), 'targets': targets}


def select_frames(selection, actual_ids, contract):
    ids = sorted(set(actual_ids))
    targets = selection.get('targets', {})
    if not ids or not isinstance(targets, dict) or set(targets) != set(contract['targets']):
        raise ValueError('Selection must cover exactly the current scene targets')
    chosen = {ids[round(i * (len(ids)-1) / 5)] for i in range(6)}
    unresolved = []
    for name, item in targets.items():
        if not isinstance(item, dict):
            raise ValueError('Invalid target visibility record: ' + name)
        frames, visibility = item.get('frames'), item.get('visibility')
        if (not isinstance(frames, list) or len(frames) > 2
                or any(type(f) is not int or f not in ids for f in frames)
                or visibility not in ('visible', 'partial', 'unobservable', 'absent')
                or not isinstance(item.get('reason'), str) or not item['reason'].strip()):
            raise ValueError('Invalid target evidence: ' + name)
        if visibility in ('visible', 'partial') and not frames:
            raise ValueError('Visible target needs actual frame evidence: ' + name)
        if visibility in ('unobservable', 'absent') and frames:
            raise ValueError('Unobservable/absent target must have empty frames: ' + name)
        if visibility != 'visible':
            unresolved.append(name)
        chosen.update(frames)
    return {'frames': sorted(chosen), 'targets': targets, 'unresolved': unresolved,
            'actual_frame_count': len(ids), 'contract': contract,
            'method': 'current scene inventory + full capture inspection + six trajectory anchors',
            'limitation': 'View selection is not proof of complete visibility; independent review must check coverage.'}


def review_selection(selection, contract):
    # Old receipts remain immutable. Do not repeat scene-specific target claims
    # from older releases in a new review prompt; keep their valid rendered frames.
    targets = {k: v for k, v in selection.get('targets', {}).items() if k in contract['targets']}
    return {'frames': selection['frames'], 'targets': targets,
            'unresolved': [k for k in contract['targets'] if targets.get(k, {}).get('visibility') != 'visible']}


def review_prompt(objects, selection):
    contract = make_contract(objects)
    return (skill_text() + '\nIndependently judge this saved reconstruction against ALL paired capture photos. '
        'Each pair is RENDER on the left and REAL photograph on the right at the same camera pose. '
        'Read-only: do not edit files, call other models or trust author self-evaluation. '
        'Passing requires score>=8, zero issues, all_objects_reviewed=true and every object ID verified. '
        'If a listed object cannot be verified, report an evidence issue and all_objects_reviewed=false. '
        'Return only JSON {"pass":bool,"score":number,"all_objects_reviewed":bool,'
        '"reviewed_object_ids":[IDs],"issues":[{"object_ids":[IDs],'
        '"priority":"support|lighting|large_appearance|small_details|evidence","defect":"observation with frame IDs"}]}. '
        '\nCurrent target contract: ' + json.dumps(contract, ensure_ascii=False) +
        '\nSelection: ' + json.dumps(review_selection(selection, contract), ensure_ascii=False) +
        '\nCurrent layout: ' + json.dumps(objects, ensure_ascii=False))


def evidence_selection(capture, folder, room, objects):
    from worker import thumbnails
    from batch_io import source_hashes
    from agent_session import recorded_session
    from litereality_agent.agent.providers import SessionSpec
    contract = make_contract(objects)
    output = folder / 'evidence'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'targets.json').write_text(json.dumps(contract, ensure_ascii=False, indent=2))
    ids, pages = thumbnails(capture, output / 'all_capture')
    prompt = (skill_text() + '\nSelect actual captured photographs for independent review. '
        'Inspect EVERY supplied contact sheet, including the end; numbers are actual frame IDs. '
        'For EACH contract target choose up to TWO clear complementary frames. Share frames when useful. '
        'Return only JSON {"targets": {TARGET_KEY: {"frames": [integer IDs], '
        '"visibility": "visible|partial|unobservable|absent", "reason": "specific evidence"}}}. '
        'For absent/unobservable use empty frames. Include every contract target key exactly once. '
        '\nContract: ' + json.dumps(contract, ensure_ascii=False) +
        '\nCurrent layout: ' + json.dumps(objects, ensure_ascii=False))
    before = source_hashes(room)
    spec = SessionSpec(prompt=prompt, cwd=folder, read_roots=(capture,), image_paths=tuple(pages),
        read_only=True, setting_sources=(), file_tools=('Read',), step_budget=50, timeout_seconds=600)
    session = asyncio.run(recorded_session(spec, folder / 'sessions/selector', 'quality'))
    if source_hashes(room) != before:
        raise RuntimeError('Read-only selector changed room source')
    if session['termination'] != 'completed':
        raise RuntimeError('Evidence selection incomplete: ' + session['termination'] + ': ' + session['reason'])
    text = session['text'].strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    selection = select_frames(json.loads(text), ids, contract)
    (output / 'selection.json').write_text(json.dumps(selection, ensure_ascii=False, indent=2))
    return selection
