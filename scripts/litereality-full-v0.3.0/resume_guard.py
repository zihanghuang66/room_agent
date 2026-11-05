"""Explicit compatible-ingest continuation; never choose a run by newest mtime."""
import json
from pathlib import Path

def validate_resume(requested, artifacts, config):
    requested = Path(requested)
    folder = requested.resolve()
    if requested.is_symlink() or folder.parent != Path(artifacts).resolve() or not folder.is_dir():
        raise ValueError('Resume folder must be an existing direct child of return_output/artifacts')
    previous = json.loads((folder / 'input.json').read_text())
    status = json.loads((folder / 'result.json').read_text())
    for key in ('scene', 'archive_sha256', 'codex_model', 'trellis_resolution', 'app_sha256', 'version'):
        if previous.get(key) != config.get(key):
            raise ValueError('Resume mismatch: ' + key)
    if status.get('scene') != config['scene'] or status.get('full_run_completed') is True:
        raise ValueError('Resume requires this scene and an unfinished run')
    return folder, previous
