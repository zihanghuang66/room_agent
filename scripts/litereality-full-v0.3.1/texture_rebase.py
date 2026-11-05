"""Resolve a relocated BLEND image by its full original path, never by basename."""
from pathlib import Path


def resolve_image_path(filepath, relocated_dir, original_dir=None):
    path=str(filepath)
    if path.startswith('//'):
        relative=path[2:]
        candidates=[]
        if original_dir is not None:candidates.append(Path(original_dir)/relative)
        candidates.append(Path(relocated_dir)/relative)
    else:
        p=Path(path)
        candidates=[p if p.is_absolute() else Path(original_dir or relocated_dir)/p]
    for candidate in candidates:
        candidate=candidate.resolve()
        if candidate.is_file():return candidate
    raise FileNotFoundError('Missing image '+repr(path)+'; checked '+', '.join(str(p) for p in candidates))
