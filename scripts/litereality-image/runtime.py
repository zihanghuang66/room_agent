"""Initialize writable runtime paths, outside the immutable /opt/room installation."""
import os
from pathlib import Path
import shutil


def initialize():
    work = Path(os.environ['LR_REPO_ROOT'])
    for name in ('LR_REPO_ROOT', 'LITEREALITY_WEIGHTS', 'HF_HOME', 'TORCH_HOME',
                 'XDG_CACHE_HOME', 'MPLCONFIGDIR', 'TORCH_EXTENSIONS_DIR', 'TRITON_CACHE_DIR',
                 'LITEREALITY_OUTPUT', 'LITEREALITY_FINAL'):
        Path(os.environ[name]).mkdir(parents=True, exist_ok=True)
    link = work / 'TRELLIS.2'
    target = Path('/opt/room/backends/TRELLIS.2')
    if link.is_symlink():
        if link.resolve() != target:
            raise RuntimeError(f'Existing TRELLIS.2 symlink points elsewhere: {link}')
    elif link.exists():
        raise RuntimeError(f'Will not replace existing directory: {link}')
    else:
        link.symlink_to(target)
    cache = Path(os.environ['FLEX_GEMM_AUTOTUNE_CACHE_PATH'])
    cache.parent.mkdir(parents=True, exist_ok=True)
    seed = Path('/opt/room/backends/flexgemm/autotune_cache.json')
    if not cache.exists() and seed.is_file():
        shutil.copyfile(seed, cache)
    return work


if __name__ == '__main__':
    print('Room runtime directory:', initialize())
