"""Persist the grouping before any fallible image-generation call."""
import json
from pathlib import Path

from common import digest, save


def load_or_build(output_root, scan, key, build):
    root = Path(output_root)
    path = root / scan / 'chair_grouping_checkpoint.json'
    if path.is_file():
        old = json.loads(path.read_text())
        if old.get('schema') == 1 and old.get('key') == key:
            sheets = old.get('sheets', {})
            if all((root / name).is_file() and digest(root / name) == sha for name, sha in sheets.items()):
                print('[chair-cluster] REUSED evidence-bound grouping checkpoint', flush=True)
                return old['result']
    result = build()
    sheets = {}
    for cluster in result.get('clusters', []):
        name = cluster.get('input2imagegen_sheet') or cluster['cluster_sheet']
        sheets[name] = digest(root / name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.pending.json')
    save(temporary, dict(schema=1, key=key, sheets=sheets, result=result))
    temporary.replace(path)
    return result
