"""Explicit compatible-ingest continuation; never choose a run by newest mtime."""
import json
from pathlib import Path

OLD_APP = 'dc83a122583a8965c2b08e662f8aa45ba9c52f21c877d589a888a7059d658115'


def validate_resume(requested, artifacts, config):
    requested = Path(requested)
    folder = requested.resolve()
    if requested.is_symlink() or folder.parent != Path(artifacts).resolve() or not folder.is_dir():
        raise ValueError('Resume folder must be an existing direct child of return_output/artifacts')
    previous = json.loads((folder / 'input.json').read_text())
    status = json.loads((folder / 'result.json').read_text())
    for key in ('scene', 'archive_sha256', 'codex_model', 'trellis_resolution'):
        if previous.get(key) != config.get(key):
            raise ValueError('Resume mismatch: ' + key)
    if previous.get('app_sha256') != config['app_sha256']:
        if not (previous.get('app_sha256') == OLD_APP and previous.get('version') == '0.2.2'
                and status.get('status') == 'failed' and status.get('stage') == 'ingest'):
            raise ValueError('Resume application is not a compatible failed v0.2.2 ingest')
    if status.get('scene') != config['scene'] or status.get('full_run_completed') is True:
        raise ValueError('Resume requires this scene and an unfinished run')
    return folder, previous
