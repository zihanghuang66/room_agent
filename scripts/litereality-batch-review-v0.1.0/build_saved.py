"""Compile saved sources with saved texture pixels, never refetch recipe URLs."""
import json
import shutil
import sys
from pathlib import Path


def cached_textures(obj_dir,texdir):
    recipe=obj_dir/'textures.json';bundled=obj_dir/'textures'
    required=json.loads(recipe.read_text()) if recipe.exists() else {}
    for name in required:
        path=Path(name)
        if path.is_absolute() or '..' in path.parts:raise ValueError('Unsafe texture filename')
        if not (bundled/path).is_file():raise RuntimeError('Missing saved texture: '+str(bundled/path))
    texdir.mkdir(parents=True,exist_ok=True)
    count=0
    if bundled.is_dir():
        for p in bundled.rglob('*'):
            if not p.is_file():continue
            target=texdir/p.relative_to(bundled);target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists() and p.samefile(target):continue
            shutil.copy2(p,target);count+=1
    return count


def saved_room_materials(recipe,out_dir):
    missing=[str(out_dir/name) for name in recipe if not (out_dir/name).is_file()]
    if missing:raise RuntimeError('Missing saved room textures; no network replacement: '+repr(missing))
    return list(recipe)


def main():
    from litereality_agent.room_ops.compile import build_from_room
    if '--regenerate' in sys.argv:
        raise ValueError('Use a fresh preview; --regenerate would refetch room textures')
    room=Path(sys.argv[sys.argv.index('--room')+1])
    recipe=room/'textures.json'
    if recipe.exists():saved_room_materials(json.loads(recipe.read_text()),room/'materials')
    build_from_room.materialize_textures=cached_textures
    return build_from_room.main()


if __name__=='__main__':raise SystemExit(main())
