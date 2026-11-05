"""Invoke the upstream stage runner with bounded serial workers and extra result checks."""
import json
import os
from pathlib import Path
import sys

from common import save
from litereality_agent.pipeline.context import RunContext
from litereality_agent.pipeline.runner import PipelineRunner
from litereality_agent.pipeline.scene_init import paths


def audit(context, stage):
    paths.set_scan(context.scan)
    if stage == 'ingest':
        root = context.object_root / 'object_init'
        records = []
        failures = []
        covered = set()
        for p in root.rglob('reference_meta.json'):
            data = json.loads(p.read_text())
            records.append(dict(file=str(p), status=data.get('status')))
            if data.get('object'):
                covered.add(data['object'])
            if data.get('status') != 'ok':
                failures.append(str(p))
        for p in root.rglob('chair_clusters.json'):
            for cluster in json.loads(p.read_text()).get('clusters', []):
                covered.update(cluster.get('members', []))
                if cluster.get('reference_status') != 'ok':
                    failures.append(str(p) + ':' + str(cluster.get('cluster_id')))
        from litereality_agent.pipeline.scene_init.ingest.references.object_references import load_scene_objects
        expected = set(load_scene_objects(context.scan))
        missing = sorted(expected - covered)
        failures.extend('no furniture reference: ' + name for name in missing)
        for p in root.rglob('opening_references.json'):
            for opening in json.loads(p.read_text()).get('openings', []):
                if opening.get('status') != 'ok':
                    failures.append(str(opening.get('opening')) + ': ' + str(opening.get('status')))
        if not records or failures:
            raise RuntimeError('Reference quality gate failed: ' + repr(failures or ['no reference metadata']))
        return dict(reference_records=records, expected_furniture=sorted(expected), covered_furniture=sorted(covered),
                    missing_furniture=missing, image_fidelity_reviewed=False)
    if stage == 'reconstruct':
        from litereality_agent.pipeline.scene_init.flow import _expected_assets, _missing_assets
        names = _expected_assets(context.scan)
        missing = _missing_assets(context.scan, names)
        if not names or missing:
            raise RuntimeError('Incomplete reconstruction: ' + repr(missing or ['empty expected asset list']))
        import numpy as np
        import trimesh
        checked = []
        for p in (context.object_root / 'reconstructed_objs').rglob('*.glb'):
            scene = trimesh.load(p, force='scene')
            if not scene.geometry or not np.isfinite(scene.bounds).all():
                raise RuntimeError('Invalid object GLB: ' + str(p))
            checked.append(str(p))
        return dict(expected_assets=names, missing=missing, parsed_glbs=checked)
    if stage == 'seed':
        if not (context.seed_room / 'Room.py').is_file():
            raise RuntimeError('Seed source Room.py missing')
    if stage == 'publish':
        report = json.loads((context.preview_dir / 'acceptance.json').read_text())
        if report.get('accepted') is not True:
            raise RuntimeError('Final acceptance was not passed')
        for name in ('Room.glb', 'Room.blend'):
            if not (context.preview_dir / name).is_file():
                raise RuntimeError('Missing final export: ' + name)
        compare = context.authoring_root / 'compare'
        images = sorted(str(p) for p in compare.rglob('*') if p.suffix.lower() in ('.png', '.jpg', '.jpeg'))
        if not images:
            raise RuntimeError('Final real-vs-render comparison images are missing')
        return dict(acceptance=report, comparison_images=images)
    return dict(status='checked')


if __name__ == '__main__':
    name, capture, output = sys.argv[1:]
    # Construct explicitly so the archive's Studio name does not replace room_002.
    context = RunContext(scan='room_002', capture_dir=Path(capture), scene_dir=Path(output) / 'room_002',
                         output_root=Path(output))
    options = {
        'ingest': dict(use_dino=True),
        'reconstruct': dict(chair_qc=True, concurrency=1),
        'seed': dict(capture_mode='link'),
        'author': dict(provider='codex', profile='open', refine_objects=True, materials=True,
                       quality_pass=True, refine_concurrency=1, refine_budget=8,
                       repair_rounds=2, repair_steps=40, repair_seconds=600),
        'publish': dict(compare_frames=6),
    }
    result = PipelineRunner().run_stage(context, name, strict=True, options=options[name])
    save(Path(os.environ['ROOM_RUN']) / ('stage_' + name + '.json'), result.to_dict())
    if not result.ok:
        raise RuntimeError(str(result.error))
    try:
        report = audit(context, name)
    except Exception:
        # Do not let upstream's weaker completion predicate cache a rejected stage as successful.
        state = json.loads(context.state_path.read_text())
        state['stages'][name]['status'] = 'failed'
        save(context.state_path, state)
        raise
    save(Path(os.environ['ROOM_RUN']) / ('audit_' + name + '.json'), report)
    print('STAGE_' + name.upper() + '_PASS', flush=True)
