"""Describe the 50-frame baseline without treating nominal registrations as success."""
import json
from pathlib import Path
import sqlite3

root = Path(__file__).resolve().parents[2]
out = root / 'return_output/local_experiments/omega_matched_50'
manifest = json.loads((out / 'input_manifest.json').read_text())
names = {i['file'] for i in manifest['selected_frames']}
with sqlite3.connect((out / 'database.db').as_uri() + '?mode=ro', uri=True) as db:
    images = {i: name for i, name in db.execute('SELECT image_id,name FROM images') if name in names}
    adjacency = {i: set() for i in images}
    edges = []
    for pair_id, count in db.execute('SELECT pair_id,rows FROM two_view_geometries'):
        a, b = divmod(pair_id, 2147483647)
        if a in images and b in images and count >= 15:
            adjacency[a].add(b)
            adjacency[b].add(a)
            edges.append(dict(a=images[a], b=images[b], inliers=count))
remaining, components = set(images), []
while remaining:
    todo, component = [remaining.pop()], []
    while todo:
        i = todo.pop()
        component.append(images[i])
        fresh = adjacency[i] & remaining
        remaining -= fresh
        todo.extend(fresh)
    components.append(sorted(component))
components.sort(key=len, reverse=True)
results = {}
for name in ('omega_matched_50', 'omega_matched_50_no_fallback'):
    result = json.loads((out.parent / name / 'result.json').read_text())
    results[name] = result
    for model in result['models']:
        assert set(model['images']) <= names
summary = dict(input_count=len(names), matching_edges_at_least_15_inliers=len(edges),
               component_sizes=[len(c) for c in components], components=components,
               baseline_results=results, geometry_accepted=False,
               comparison_caveats=['Same image identities, not same resolution: COLMAP uses 900x1600 while Omega will use 288x512.',
                                   'COLMAP reuses cached two-view matches and focal initialization 700 chosen in earlier diagnosis; this is not a blind benchmark.',
                                   'Mapping runtime excludes feature extraction and matching, and cannot be compared to Omega end-to-end runtime.',
                                   'One 3D point cannot establish room geometry; nominal registrations and its reprojection error are not success metrics.'])
(out / 'comparison_summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary, indent=2))
